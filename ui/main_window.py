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
from core.config import set_last_root_folder, get_last_root_folder
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
        例如：D:/Videos/A/B -> 收藏集 "A/B"（只包含 B 的直接视频，不包含更深层子目录的视频）"""
        self.collections.clear()
        self.collection_list.clear()

        # 第一步：用 os.walk 收集所有子目录的相对路径
        all_subfolders = []
        try:
            for dirpath, dirnames, filenames in os.walk(folder_path):
                # 跳过 .videoview 配置目录
                dirnames[:] = [d for d in dirnames if d != '.videoview']
                rel_path = os.path.relpath(dirpath, folder_path).replace('\\', '/')
                # 排除根目录本身（rel_path == '.'）和 .videoview
                if rel_path != '.' and '.videoview' not in rel_path.split('/'):
                    all_subfolders.append((rel_path, dirpath))
        except Exception as e:
            print(f"扫描文件夹失败: {e}")
            return

        # 按相对路径字母序排序
        all_subfolders.sort(key=lambda x: x[0])

        # 进度对话框
        progress = QProgressDialog("正在扫描文件夹...", "取消", 0, len(all_subfolders), self)
        progress.setWindowTitle("扫描中")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setMaximumWidth(500)  # 限制进度条窗口宽度，避免过长
        progress.setValue(0)

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
            progress.setValue(i + 1)
            progress.setLabelText(f"扫描: {rel_path}")

        progress.close()

        self.populate_collection_list()
        self.populate_fav_collection_list()

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

        # 默认收藏夹：使用收藏的第一个视频作为缩略图（延迟加载）
        fav_videos = self.cache_manager.get_favorite_videos()
        video_rel = None
        if fav_videos:
            try:
                first_video = fav_videos[0]
                video_rel = os.path.relpath(
                    first_video, self.root_folder
                ).replace('\\', '/')
            except Exception:
                pass

        from PyQt5.QtCore import QSize
        item = QListWidgetItem(self.fav_collection_list)
        item.setSizeHint(QSize(280, 420))

        # 构造缩略图请求回调（支持旋转角度）
        def make_fav_thumb_callback():
            if video_rel and fav_videos:
                def _cb(rel_path, rotation_deg=0):
                    if self.thumbnail_manager:
                        self.thumbnail_manager.enqueue(
                            fav_videos[0], rel_path, rotation_deg
                        )
                return _cb
            return None

        widget = CollectionItem(
            "默认收藏夹",
            None,
            self.cache_manager.get_favorite_count(),
            is_marked=False,
            cache_manager=self.cache_manager,
            video_relative_path=video_rel,
            on_thumbnail_ready=make_fav_thumb_callback(),
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
        # 不自动选中第一个收藏夹，避免为所有视频触发缩略图加载

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
        self.show_default_favorites()

    def show_videos(self, collection):
        self.current_tab = 0
        self.right_panel.clear_videos()
        self.right_panel.set_title(
            f"📁 {collection['name']} ({len(collection['videos'])} 个视频)"
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

    def show_default_favorites(self):
        self.current_tab = 1
        self.right_panel.clear_videos()

        if not self.cache_manager:
            self.right_panel.set_title("⭐ 默认收藏夹")
            return

        fav_videos = self.cache_manager.get_favorite_videos()
        self.right_panel.set_title(
            f"⭐ 默认收藏夹 ({len(fav_videos)} 个视频)"
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
        if self.thumbnail_manager:
            self.thumbnail_manager.stop()
            self.thumbnail_manager.wait()
        event.accept()
