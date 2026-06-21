"""主窗口：左侧面板（收藏夹列表 + 标签页）+ 右侧视频网格"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QFrame, QPushButton, QListWidget, QListWidgetItem,
    QFileDialog, QProgressDialog, QStackedWidget, QLabel,
)
from PyQt5.QtGui import QPixmap, QPainter, QColor

from core.cache_manager import CacheManager, path_hash
from core.utils import ThumbnailManager, scan_folder_for_videos
from core.config import set_last_root_folder, get_last_root_folder, get_window_state, save_window_state
from core.thumbnail import THUMBNAIL_SIZE_COLLECTION

from ui.widgets import (
    CollectionItem,
    CollectionListWidget,
    VideoGridWidget,
)
from ui.video_player_window import VideoPlayerWindow


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.root_folder = None
        self.collections = []
        self.thumbnail_manager = None
        self.cache_manager = None
        self.current_tab = 0
        self.current_video_list = []
        self.active_player = None

        self.setup_ui()
        self.apply_styles()
        self._restore_window_state()

        # 启动时加载上次目录（延迟一点，等待窗口先出来）
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(50, self._auto_load_last_folder)

    # ------------------------------------------------------------
    # UI
    # ------------------------------------------------------------
    def setup_ui(self):
        from PyQt5.QtWidgets import QApplication
        self.setWindowTitle("视频收藏集管理器")
        self.setMinimumSize(1400, 900)

        font = QApplication.font()
        font.setPointSize(18)
        QApplication.setFont(font)
        self.setFont(font)

        central_widget = QWidget()
        central_widget.setFont(font)
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 左侧面板
        self.left_panel = QFrame()
        self.left_panel.setFixedWidth(400)
        self.left_panel.setFont(font)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)

        # 打开文件夹按钮
        self.open_btn = QPushButton("📂 打开文件夹")
        self.open_btn.setFixedHeight(40)
        self.open_btn.setFont(font)
        self.open_btn.clicked.connect(self.open_folder)

        # 两个切换按钮
        switch_row = QHBoxLayout()
        switch_row.setSpacing(8)
        self.btn_root = QPushButton("📁 根目录")
        self.btn_root.setFixedHeight(44)
        self.btn_root.setFont(font)
        self.btn_root.setCheckable(True)
        self.btn_root.clicked.connect(lambda: self.switch_list_mode(0))

        self.btn_fav = QPushButton("⭐ 最爱")
        self.btn_fav.setFixedHeight(44)
        self.btn_fav.setFont(font)
        self.btn_fav.setCheckable(True)
        self.btn_fav.clicked.connect(lambda: self.switch_list_mode(1))

        switch_row.addWidget(self.btn_root)
        switch_row.addWidget(self.btn_fav)

        # 初始选中根目录
        self._update_switch_buttons(0)

        # 列表容器：QStackedWidget，两层：根目录 / 最爱
        self.list_stack = QStackedWidget()
        self.list_stack.setFont(font)
        self.list_stack.setStyleSheet("background-color: transparent;")

        self.collection_list = CollectionListWidget()
        self.collection_list.itemClicked.connect(self.on_collection_clicked)

        self.fav_collection_list = CollectionListWidget()
        self.fav_collection_list.itemClicked.connect(self.on_fav_collection_clicked)

        self.list_stack.addWidget(self.collection_list)
        self.list_stack.addWidget(self.fav_collection_list)

        left_layout.addWidget(self.open_btn)
        left_layout.addLayout(switch_row)
        left_layout.addWidget(self.list_stack)

        # 右侧视频网格
        self.right_panel = VideoGridWidget(self)
        self.right_panel.setFont(font)
        self.right_panel.set_on_favorite_changed(self.on_favorite_changed)
        self.right_panel.set_on_double_clicked(self.on_video_double_clicked)
        self.right_panel.on_prev_collection = self.on_prev_collection
        self.right_panel.on_next_collection = self.on_next_collection

        main_layout.addWidget(self.left_panel)
        main_layout.addWidget(self.right_panel)

    def apply_styles(self):
        self.setStyleSheet(
            "QMainWindow {background-color: #1a1a2e;}"
            "QPushButton {background-color:#4a4a6a;color:white;border:none;"
            "border-radius:8px;font-size:22px;font-weight:bold;padding:10px 20px;}"
            "QPushButton:hover {background-color:#5a5a7a;}"
            "QPushButton:pressed {background-color:#3a3a5a;}"
            "QMenu {background-color:#2a2a44; border:1px solid #4a4a6a; border-radius:6px; padding:4px;}"
            "QMenu::item {color:white; padding:8px 24px; border-radius:4px;}"
            "QMenu::item:selected {background-color:#4a4a6a; color:#ffcc00;}"
            "QMenu::separator {height:1px; background:#4a4a6a; margin:4px 8px;}"
        )
        self.left_panel.setStyleSheet(
            "QFrame {background-color:#1a1a2e; border-right:1px solid #2a2a4a;}"
        )

    def _restore_window_state(self):
        """从 app_config.json 恢复上次的窗口状态与尺寸位置"""
        from PyQt5.QtWidgets import QDesktopWidget
        from PyQt5.QtCore import Qt
        state = get_window_state('main')
        if state is None:
            # 首次启动：默认最大化
            self.resize(1600, 1000)
            self.setAttribute(Qt.WA_DontShowOnScreen, False)
            self.setWindowState(Qt.WindowMaximized)
            return
        try:
            w = max(400, state['width'])
            h = max(300, state['height'])
            self.resize(w, h)
            try:
                screen = QDesktopWidget().availableGeometry()
                x = max(screen.left(), min(state['x'], screen.right() - 200))
                y = max(screen.top(), min(state['y'], screen.bottom() - 100))
                self.move(x, y)
            except Exception:
                pass
            if state.get('maximized'):
                self.setWindowState(Qt.WindowMaximized)
            elif state.get('minimized'):
                self.setWindowState(Qt.WindowMinimized)
        except Exception as e:
            print(f"恢复主窗口状态失败: {e}")

    # ------------------------------------------------------------
    # 初始化：缩略图 / 扫描
    # ------------------------------------------------------------
    def init_thumbnail_manager(self):
        if self.thumbnail_manager:
            self.thumbnail_manager.stop()
            self.thumbnail_manager.wait()

        cache_dir = os.path.join(self.root_folder, '.videoview', 'cache')
        self.cache_manager = CacheManager(cache_dir, self.root_folder)

        self.thumbnail_manager = ThumbnailManager(self.cache_manager, max_workers=2)
        self.thumbnail_manager.finished.connect(self.on_thumbnail_ready)
        self.thumbnail_manager.start()

        # 追踪 CollectionItem（按其缩略图对应的 video_relative_path）
        self._collection_thumbnail_items = {}

    def on_thumbnail_ready(self, video_relative_path):
        # 更新右侧视频网格中的 VideoItem
        self.right_panel.on_thumbnail_generated(video_relative_path)
        # 更新左侧收藏夹列表中对应的 CollectionItem（如果有）
        if video_relative_path in self._collection_thumbnail_items:
            widget = self._collection_thumbnail_items[video_relative_path]
            if widget:
                widget.update_from_cache()

    def on_video_rotated(self, video_abs_path, video_relative_path):
        """视频旋转后回调：删除旧缓存 → 按新角度重新生成缩略图 → 刷新UI"""
        rel_path = video_relative_path.replace('\\', '/')
        cm = self.cache_manager

        # 获取新的旋转角度
        rotation = 0
        try:
            rotation = int(cm.get_rotation(rel_path)) % 360
        except Exception:
            pass

        # 删除旧缓存
        cm.clear_cache_for(rel_path)

        # 触发异步重新生成（按新角度写入同一缓存路径）
        if self.thumbnail_manager:
            self.thumbnail_manager.enqueue(video_abs_path, rel_path, rotation)

        # 刷新 UI 中的 item
        if hasattr(self, 'right_panel'):
            if video_relative_path in self.right_panel.video_items:
                video_item = self.right_panel.video_items[video_relative_path]
                if hasattr(video_item, '_thumbnail_loaded'):
                    video_item._thumbnail_loaded = False
                if hasattr(video_item, 'update_from_cache'):
                    video_item.update_from_cache()

        if video_relative_path in getattr(self, '_collection_thumbnail_items', {}):
            widget = self._collection_thumbnail_items[video_relative_path]
            if widget:
                if hasattr(widget, '_thumbnail_loaded'):
                    widget._thumbnail_loaded = False
                widget.update_from_cache()

    def on_batch_rotate_collection(self, collection_name, delta_deg):
        """批量旋转整个收藏夹的视频：更新对应 .videoview 的 rotations.json + 重新生成缩略图"""
        if not self.cache_manager:
            return

        # 找到对应 collection 的所有视频
        target_collection = None
        for c in self.collections:
            if c['name'] == collection_name:
                target_collection = c
                break

        if target_collection is None or not target_collection['videos']:
            return

        abs_rel_pairs = []
        for video_abs in target_collection['videos']:
            try:
                rel = os.path.relpath(video_abs, self.root_folder).replace('\\', '/')
                abs_rel_pairs.append((video_abs, rel))
            except Exception:
                continue

        if not abs_rel_pairs:
            return

        # 1. 更新根目录 .videoview 中 rotations.json 的旋转角度
        for abs_path, rel in abs_rel_pairs:
            # cm = self._get_cache_manager_for_rel(rel)  # 映射子目录.videoview的代码已注释
            cm = self.cache_manager  # 统一使用根目录的 cache_manager
            if delta_deg < 0:
                cm.rotate_left(rel)
            else:
                cm.rotate_right(rel)

        # 2. 删除各自的缓存文件
        for abs_path, rel in abs_rel_pairs:
            # cm = self._get_cache_manager_for_rel(rel)  # 映射子目录.videoview的代码已注释
            cm = self.cache_manager  # 统一使用根目录的 cache_manager
            cm.clear_cache_for(rel)

        # 3. 触发重新生成缩略图（异步）
        if self.thumbnail_manager:
            for abs_path, rel in abs_rel_pairs:
                # cm = self._get_cache_manager_for_rel(rel)  # 映射子目录.videoview的代码已注释
                cm = self.cache_manager  # 统一使用根目录的 cache_manager
                rotation = 0
                try:
                    rotation = int(cm.get_rotation(rel)) % 360
                except Exception:
                    pass
                self.thumbnail_manager.enqueue(abs_path, rel, rotation)

        # 4. 刷新 UI：左侧收藏夹的缩略图 + 右侧视频网格
        # 刷新左侧收藏夹中该收藏夹的代表视频缩略图
        if target_collection['videos']:
            first_rel = os.path.relpath(target_collection['videos'][0], self.root_folder).replace('\\', '/')
            if first_rel in getattr(self, '_collection_thumbnail_items', {}):
                widget = self._collection_thumbnail_items[first_rel]
                if widget:
                    if hasattr(widget, '_thumbnail_loaded'):
                        widget._thumbnail_loaded = False
                    if hasattr(widget, 'update_from_cache'):
                        widget.update_from_cache()

        # 刷新右侧视频网格：如果当前显示的是这个 collection，刷新其所有 item
        if hasattr(self, 'right_panel') and hasattr(self.right_panel, 'video_items'):
            for rel, item in self.right_panel.video_items.items():
                if item is not None:
                    if hasattr(item, '_thumbnail_loaded'):
                        item._thumbnail_loaded = False
                    if hasattr(item, 'update_from_cache'):
                        item.update_from_cache()

        print(f"[批量旋转] 收藏夹 '{collection_name}': 旋转了 {len(abs_rel_pairs)} 个视频（{'左旋' if delta_deg < 0 else '右旋'} 90°）")

    def on_batch_favorite_collection(self, collection_name):
        """批量收藏：将收藏夹中的所有视频作为新的命名收藏夹出现在"最爱"列表中"""
        if not self.cache_manager:
            return

        target_collection = None
        for c in self.collections:
            if c['name'] == collection_name:
                target_collection = c
                break

        if target_collection is None or not target_collection['videos']:
            return

        rel_paths = []
        for video_abs in target_collection['videos']:
            try:
                rel = os.path.relpath(video_abs, self.root_folder).replace('\\', '/')
                rel_paths.append(rel)
            except Exception:
                continue

        if not rel_paths:
            return

        # 以"原收藏夹名"作为新的收藏夹名（若已存在，则覆盖）
        # 命名为 "⭐ 收藏夹名"
        self.cache_manager.create_named_collection(collection_name, rel_paths)
        print(f"[批量收藏] 已将收藏夹 '{collection_name}'（{len(rel_paths)} 个视频）添加到最爱列表")
        self.refresh_fav_collection_list()

    def on_batch_unfavorite_collection(self, collection_name):
        """批量取消收藏：从"最爱"列表中删除该命名收藏夹（"默认收藏夹"不可取消）"""
        if not self.cache_manager:
            return
        if collection_name == '默认收藏夹':
            return
        try:
            self.cache_manager.remove_named_collection(collection_name)
            print(f"[批量取消收藏] 已从最爱列表移除收藏夹 '{collection_name}'")
            self.refresh_fav_collection_list()
        except Exception as e:
            print(f"[批量取消收藏] 异常: {e}")

    def on_favorite_changed(self, video_relative_path, new_state):
        if self.cache_manager:
            self.refresh_fav_collection_list()
        if self.current_tab == 1:
            self.show_default_favorites()

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择视频文件夹", "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder_path):
        self.root_folder = folder_path.replace('\\', '/')
        set_last_root_folder(self.root_folder)
        self.init_thumbnail_manager()
        self.scan_collections(folder_path)

    def _auto_load_last_folder(self):
        last = get_last_root_folder()
        if last and os.path.exists(last) and os.path.isdir(last):
            try:
                self._load_folder(last)
            except Exception as e:
                print(f"自动加载上次目录失败: {e}")

    def scan_collections(self, folder_path):
        """递归扫描所有层级的子目录，每个包含直接视频的子目录都作为一个独立的收藏集。
        例如：D:/Videos/A/B -> 收藏集 "A/B"（只包含 B 的直接视频，不包含更深层子目录的视频）

        同时扫描根目录本身的直接视频：
          - 若根目录有直接视频文件，则以根目录的 basename 作为收藏夹名，插入到列表最前。

        关键点：Windows 上使用 os.scandir 替代 os.walk，确保带隐藏/系统属性的
        目录与视频文件也能被扫描到（避免 Windows 对 HIDDEN+SYSTEM 属性目录的隐式过滤）。
        """
        self.collections.clear()
        self.collection_list.clear()

        # 第一步：扫描根目录自身的直接视频文件（若存在，则作为「根目录」收藏夹）
        root_videos = scan_folder_for_videos(folder_path, recursive=False)
        root_collection = None
        if root_videos:
            root_name = os.path.basename(os.path.normpath(folder_path))
            root_collection = {
                'name': root_name,
                'path': folder_path,
                'videos': root_videos,
            }

        # 第二步：递归枚举所有子目录
        all_subfolders = []
        try:
            stack = [(folder_path, "")]
            while stack:
                cur_path, cur_rel = stack.pop()
                try:
                    with os.scandir(cur_path) as it:
                        for entry in it:
                            name = entry.name
                            # 跳过应用自己的元数据目录与 Windows 系统保留目录
                            if name == '.videoview' or name.startswith('$'):
                                continue
                            try:
                                if not entry.is_dir(follow_symlinks=False):
                                    continue
                            except Exception:
                                continue
                            # 构造相对路径（如 "A/B"），用于子目录命名
                            new_rel = (cur_rel + '/' + name) if cur_rel else name
                            all_subfolders.append((new_rel, entry.path))
                            stack.append((entry.path, new_rel))
                except Exception:
                    continue
        except Exception as e:
            print(f"扫描文件夹失败: {e}")
            # 即便子目录扫描失败，根目录若有视频仍可显示
            if root_collection is None:
                return

        # 按相对路径字母序排序
        all_subfolders.sort(key=lambda x: x[0])

        # 进度对话框：总数 = 子目录数 +（有根目录视频时 +1）
        total_steps = len(all_subfolders)
        if root_collection is not None:
            total_steps += 1
        progress = QProgressDialog("正在扫描文件夹...", "取消", 0, max(1, total_steps), self)
        progress.setWindowTitle("扫描中")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setMaximumWidth(500)  # 限制进度条窗口宽度，避免过长
        progress.setValue(0)

        step_index = 0

        # 先处理根目录收藏夹（若存在），以保证它出现在列表最前
        if root_collection is not None:
            self.collections.append(root_collection)
            step_index += 1
            progress.setValue(step_index)
            progress.setLabelText(f"扫描: {root_collection['name']}（根目录）")

        for i, (rel_path, abs_path) in enumerate(all_subfolders):
            if progress.wasCanceled():
                break
            # 只收集该目录的直接视频，不包含更深层子目录的视频
            videos = scan_folder_for_videos(abs_path, recursive=False)
            if videos:
                # 用相对路径作为 collection name（如 "A/B"），确保唯一性且体现层级
                name = rel_path
                self.collections.append({
                    'name': name,
                    'path': abs_path,
                    'videos': videos,
                })
            step_index += 1
            progress.setValue(step_index)
            progress.setLabelText(f"扫描: {rel_path}")

        progress.close()

        self.populate_collection_list()
        self.populate_fav_collection_list()
        # 扫描完成后，默认打开列表中的第一个收藏夹（与用户点击一致的体验）
        self._auto_open_first_collection(0)

    def populate_collection_list(self):
        self.collection_list.clear()
        self._collection_thumbnail_items.clear()

        # 按标记状态排序：统一使用根目录 cache_manager
        if self.cache_manager:
            unmarked = [c for c in self.collections if not self.cache_manager.is_marked(c['name'])]
            marked = [c for c in self.collections if self.cache_manager.is_marked(c['name'])]
            ordered = unmarked + marked
        else:
            ordered = self.collections

        for collection in ordered:
            video_rel = None
            if collection['videos'] and self.cache_manager:
                try:
                    video_path = collection['videos'][0]
                    video_rel = os.path.relpath(video_path, self.root_folder).replace('\\', '/')
                except Exception:
                    pass

            item = QListWidgetItem(self.collection_list)
            item.setSizeHint(__import__('PyQt5.QtCore', fromlist=['QSize']).QSize(280, 420))

            is_marked = False
            if self.cache_manager:
                is_marked = self.cache_manager.is_marked(collection['name'])

            # 构造缩略图请求回调（支持旋转角度）
            def make_thumb_callback(vp, vr):
                def _cb(rel_path, rotation_deg=0):
                    if self.thumbnail_manager:
                        self.thumbnail_manager.enqueue(vp, rel_path, rotation_deg)
                return _cb

            thumb_callback = None
            if collection['videos'] and self.cache_manager and video_rel:
                try:
                    video_path = collection['videos'][0]
                    thumb_callback = make_thumb_callback(video_path, video_rel)
                except Exception:
                    pass

            widget = CollectionItem(
                collection['name'],
                None,  # thumbnail_path 由 load_thumbnail 从缓存加载
                len(collection['videos']),
                is_marked=is_marked,
                on_mark_changed=self.on_collection_mark_changed,
                cache_manager=self.cache_manager,
                video_relative_path=video_rel,
                on_thumbnail_ready=thumb_callback,
                on_batch_rotate=self.on_batch_rotate_collection,
                on_batch_favorite=self.on_batch_favorite_collection,
            )
            self.collection_list.setItemWidget(item, widget)

            # 注册到映射中，以便缩略图生成完成后能定位到正确的 CollectionItem
            if video_rel:
                self._collection_thumbnail_items[video_rel] = widget

        # 所有项已添加 → 触发一次视口刷新，加载当前可见项的缩略图
        if hasattr(self.collection_list, '_pending_refresh_timer'):
            self.collection_list._pending_refresh_timer.start()

    def refresh_fav_collection_list(self):
        current_row = self.fav_collection_list.currentRow()
        self.populate_fav_collection_list()
        if self.fav_collection_list.count() > current_row >= 0:
            self.fav_collection_list.setCurrentRow(current_row)

    def populate_fav_collection_list(self):
        self.fav_collection_list.clear()
        if not self.cache_manager:
            return

        # 获取所有命名收藏夹名（含"默认收藏夹"）
        all_names = self.cache_manager.get_collection_names()

        from PyQt5.QtCore import QSize
        for name in all_names:
            if name == '默认收藏夹':
                fav_videos = self.cache_manager.get_favorite_videos()
                count = self.cache_manager.get_favorite_count()
            else:
                fav_videos = self.cache_manager.get_named_collection_videos(name)
                count = self.cache_manager.get_named_collection_count(name)

            video_rel = None
            if fav_videos:
                try:
                    first_video = fav_videos[0]
                    video_rel = os.path.relpath(
                        first_video, self.root_folder
                    ).replace('\\', '/')
                except Exception:
                    pass

            item = QListWidgetItem(self.fav_collection_list)
            item.setSizeHint(QSize(280, 420))

            # 构造缩略图请求回调（支持旋转角度）
            def make_fav_thumb_callback(vids, vr):
                if vr and vids:
                    def _cb(rel_path, rotation_deg=0):
                        if self.thumbnail_manager:
                            self.thumbnail_manager.enqueue(
                                vids[0], rel_path, rotation_deg
                            )
                    return _cb
                return None

            widget = CollectionItem(
                name,
                None,
                count,
                is_marked=False,
                cache_manager=self.cache_manager,
                video_relative_path=video_rel,
                on_thumbnail_ready=make_fav_thumb_callback(fav_videos, video_rel),
                # 非"默认收藏夹"才支持批量取消收藏（默认收藏夹始终保留）
                on_batch_unfavorite=(
                    self.on_batch_unfavorite_collection if name != '默认收藏夹' else None
                ),
            )
            self.fav_collection_list.setItemWidget(item, widget)
            if video_rel:
                self._collection_thumbnail_items[video_rel] = widget

        if hasattr(self.fav_collection_list, '_pending_refresh_timer'):
            self.fav_collection_list._pending_refresh_timer.start()

    # ------------------------------------------------------------
    # 切换
    # ------------------------------------------------------------
    def _update_switch_buttons(self, mode):
        active = (
            "QPushButton {background-color:#3a3a5a;color:#fff;"
            "border:2px solid #5a5a7a;border-radius:8px;font-size:18px;"
            "font-weight:bold;padding:6px 12px;}"
            "QPushButton:hover {background-color:#4a4a6a;}"
        )
        inactive = (
            "QPushButton {background-color:#252540;color:#aaa;"
            "border:2px solid #2d2d44;border-radius:8px;font-size:18px;"
            "font-weight:bold;padding:6px 12px;}"
            "QPushButton:hover {background-color:#2d2d44;color:#ccc;}"
        )
        if mode == 0:
            self.btn_root.setStyleSheet(active)
            self.btn_fav.setStyleSheet(inactive)
            self.btn_root.setChecked(True)
            self.btn_fav.setChecked(False)
        else:
            self.btn_root.setStyleSheet(inactive)
            self.btn_fav.setStyleSheet(active)
            self.btn_root.setChecked(False)
            self.btn_fav.setChecked(True)

    def switch_list_mode(self, mode):
        self.current_tab = mode
        self._update_switch_buttons(mode)
        self.list_stack.setCurrentIndex(mode)
        # 切换到对应列表时，自动选中并打开第一个收藏夹
        self._auto_open_first_collection(mode)

    def _auto_open_first_collection(self, mode):
        """在指定列表中选中并打开第一个收藏夹项。
        mode=0 -> 根目录的收藏夹列表；mode=1 -> 最爱列表"""
        try:
            # 同步切换左侧列表到对应 tab（保证与用户手动点击的视觉一致）
            self.list_stack.setCurrentIndex(mode)
            self._update_switch_buttons(mode)
            self.current_tab = mode
            if mode == 0:
                if self.collection_list.count() > 0:
                    first_item = self.collection_list.item(0)
                    if first_item is not None:
                        self.collection_list.setCurrentItem(first_item)
                        widget = self.collection_list.itemWidget(first_item)
                        if widget is not None and hasattr(widget, 'load_thumbnail'):
                            widget.load_thumbnail()
                        # 用 widget.name 定位到对应的 collection dict（与点击逻辑一致）
                        target_name = widget.name if widget else None
                        if target_name is not None:
                            for c in self.collections:
                                if c['name'] == target_name:
                                    self.show_videos(c)
                                    return
            elif mode == 1:
                if self.fav_collection_list.count() > 0:
                    first_item = self.fav_collection_list.item(0)
                    if first_item is not None:
                        self.fav_collection_list.setCurrentItem(first_item)
                        widget = self.fav_collection_list.itemWidget(first_item)
                        if widget is not None and hasattr(widget, 'load_thumbnail'):
                            widget.load_thumbnail()
                        self.show_default_favorites()
        except Exception as e:
            print(f"自动打开第一个收藏夹失败: {e}")

    def on_collection_clicked(self, item):
        self.current_tab = 0
        widget = self.collection_list.itemWidget(item)
        target_name = widget.name if widget else None
        if target_name is not None:
            # 点击时才开始加载该收藏夹的缩略图（延迟加载，避免高负载）
            if widget is not None:
                widget.load_thumbnail()
            for c in self.collections:
                if c['name'] == target_name:
                    self.show_videos(c)
                    return

    def on_fav_collection_clicked(self, item):
        self.current_tab = 1
        widget = self.fav_collection_list.itemWidget(item)
        if widget is not None:
            widget.load_thumbnail()
        target_name = widget.name if widget else None
        if target_name is None:
            return
        self.show_named_collection(target_name)

    def show_videos(self, collection):
        self.current_tab = 0
        self.right_panel.clear_videos()
        self._current_collection_name = collection['name']

        # 在 collections 列表中找到当前收藏夹的索引
        current_idx = None
        for i, c in enumerate(self.collections):
            if c['name'] == collection['name']:
                current_idx = i
                break
        self._current_root_index = current_idx
        total = len(self.collections)
        can_prev = current_idx is not None and current_idx > 0
        can_next = current_idx is not None and current_idx < total - 1
        self.right_panel.set_title_header(
            "📁", self.root_folder, collection['name'],
            len(collection['videos']), can_prev=can_prev, can_next=can_next,
        )

        self.current_video_list = []
        for video_path in collection['videos']:
            video_relative_path = os.path.relpath(
                video_path, self.root_folder
            ).replace('\\', '/')
            self.current_video_list.append((video_path, video_relative_path))
            self.right_panel.add_video(
                video_path, video_relative_path, self.cache_manager,
            )

    def _get_fav_names_ordered(self):
        """返回当前最爱列表中显示的收藏夹名有序列表（与左侧列表一致）"""
        if not self.cache_manager:
            return []
        return self.cache_manager.get_collection_names()

    def show_default_favorites(self):
        self.current_tab = 1
        self.right_panel.clear_videos()

        fav_names = self._get_fav_names_ordered()
        try:
            idx = fav_names.index('默认收藏夹')
        except ValueError:
            idx = 0
        self._current_fav_index = idx
        can_prev = idx > 0
        can_next = idx < len(fav_names) - 1

        if not self.cache_manager:
            self.right_panel.set_title_header(
                "⭐", self.root_folder, "默认收藏夹", 0,
                can_prev=can_prev, can_next=can_next,
            )
            return

        fav_videos = self.cache_manager.get_favorite_videos()
        self.right_panel.set_title_header(
            "⭐", self.root_folder, "默认收藏夹", len(fav_videos),
            can_prev=can_prev, can_next=can_next,
        )

        self.current_video_list = []
        for video_path in fav_videos:
            video_relative_path = os.path.relpath(
                video_path, self.root_folder
            ).replace('\\', '/')
            self.current_video_list.append((video_path, video_relative_path))
            self.right_panel.add_video(
                video_path, video_relative_path, self.cache_manager,
            )

    def show_named_collection(self, name):
        """在最爱列表中显示某个命名收藏夹（支持"默认收藏夹"或批量收藏的命名收藏夹）"""
        self.current_tab = 1
        self.right_panel.clear_videos()

        fav_names = self._get_fav_names_ordered()
        try:
            idx = fav_names.index(name)
        except ValueError:
            idx = 0
        self._current_fav_index = idx
        self._current_named_collection = name
        can_prev = idx > 0
        can_next = idx < len(fav_names) - 1

        if not self.cache_manager:
            self.right_panel.set_title_header(
                "⭐", self.root_folder, name, 0,
                can_prev=can_prev, can_next=can_next,
            )
            return

        if name == '默认收藏夹':
            fav_videos = self.cache_manager.get_favorite_videos()
        else:
            fav_videos = self.cache_manager.get_named_collection_videos(name)

        self.right_panel.set_title_header(
            "⭐", self.root_folder, name, len(fav_videos),
            can_prev=can_prev, can_next=can_next,
        )

        self.current_video_list = []
        for video_path in fav_videos:
            try:
                video_relative_path = os.path.relpath(
                    video_path, self.root_folder
                ).replace('\\', '/')
            except Exception:
                continue
            self.current_video_list.append((video_path, video_relative_path))
            self.right_panel.add_video(
                video_path, video_relative_path, self.cache_manager,
            )

    # ------------------------------------------------------------
    # 上下收藏夹切换（标题栏 ◀ ▶ 按钮）
    # ------------------------------------------------------------
    def on_prev_collection(self):
        """切换到上一个收藏夹（根据当前所在的 tab 选择对应的列表）"""
        if self.current_tab == 0:
            idx = getattr(self, '_current_root_index', None)
            if idx is None or idx <= 0 or not self.collections:
                return
            target_idx = idx - 1
            target = self.collections[target_idx]
            # 同时同步左侧选中项
            try:
                if 0 <= target_idx < self.collection_list.count():
                    item = self.collection_list.item(target_idx)
                    if item:
                        self.collection_list.setCurrentItem(item)
            except Exception:
                pass
            self.show_videos(target)
        else:
            fav_names = self._get_fav_names_ordered()
            idx = getattr(self, '_current_fav_index', None)
            if idx is None or idx <= 0 or not fav_names:
                return
            target_idx = idx - 1
            target_name = fav_names[target_idx]
            try:
                if 0 <= target_idx < self.fav_collection_list.count():
                    item = self.fav_collection_list.item(target_idx)
                    if item:
                        self.fav_collection_list.setCurrentItem(item)
            except Exception:
                pass
            if target_name == '默认收藏夹':
                self.show_default_favorites()
            else:
                self.show_named_collection(target_name)

    def on_next_collection(self):
        """切换到下一个收藏夹（根据当前所在的 tab 选择对应的列表）"""
        if self.current_tab == 0:
            idx = getattr(self, '_current_root_index', None)
            if idx is None or not self.collections:
                return
            total = len(self.collections)
            if idx >= total - 1:
                return
            target_idx = idx + 1
            target = self.collections[target_idx]
            try:
                if 0 <= target_idx < self.collection_list.count():
                    item = self.collection_list.item(target_idx)
                    if item:
                        self.collection_list.setCurrentItem(item)
            except Exception:
                pass
            self.show_videos(target)
        else:
            fav_names = self._get_fav_names_ordered()
            idx = getattr(self, '_current_fav_index', None)
            if idx is None or not fav_names:
                return
            total = len(fav_names)
            if idx >= total - 1:
                return
            target_idx = idx + 1
            target_name = fav_names[target_idx]
            try:
                if 0 <= target_idx < self.fav_collection_list.count():
                    item = self.fav_collection_list.item(target_idx)
                    if item:
                        self.fav_collection_list.setCurrentItem(item)
            except Exception:
                pass
            if target_name == '默认收藏夹':
                self.show_default_favorites()
            else:
                self.show_named_collection(target_name)

    # ------------------------------------------------------------
    # 其他事件
    # ------------------------------------------------------------
    def on_collection_mark_changed(self, collection_name, new_state):
        if self.cache_manager:
            self.cache_manager.set_mark(collection_name, new_state)
        self.populate_collection_list()

    def on_video_double_clicked(self, video_path, video_relative_path):
        if not self.current_video_list:
            video_list = [(video_path, video_relative_path)]
            start_index = 0
        else:
            video_list = list(self.current_video_list)
            start_index = 0
            for i, (vp, rp) in enumerate(video_list):
                if rp == video_relative_path or vp == video_path:
                    start_index = i
                    break

        try:
            player = VideoPlayerWindow(
                video_list, start_index, self.cache_manager,
                parent=self, root_folder=self.root_folder,
                on_video_rotated=self.on_video_rotated,
            )
            player.favorite_changed.connect(self._on_player_favorite_changed)
            self.active_player = player
            player.show()
        except Exception as e:
            print(f"打开播放器失败: {e}")

    def _on_player_favorite_changed(self, video_relative_path, new_state):
        if self.cache_manager:
            self.refresh_fav_collection_list()
        if self.current_tab == 1:
            self.show_default_favorites()

    def closeEvent(self, event):
        # 保存当前窗口状态（最大化/尺寸/位置）到 app_config.json
        try:
            save_window_state(self, 'main')
        except Exception as e:
            print(f"保存主窗口状态失败: {e}")
        if self.thumbnail_manager:
            self.thumbnail_manager.stop()
            self.thumbnail_manager.wait()
        event.accept()
