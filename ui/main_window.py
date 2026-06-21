"""主窗口：左侧面板（收藏夹列表 + 标签页）+ 右侧视频网格"""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QFrame, QPushButton, QListWidget, QListWidgetItem,
    QFileDialog, QProgressDialog, QStackedWidget, QLabel,
)
from PyQt5.QtGui import QPixmap, QPainter, QColor

from core.cache_manager import CacheManager
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
        self.left_panel.setFixedWidth(320)
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

    def on_thumbnail_ready(self, video_relative_path):
        self.right_panel.on_thumbnail_generated(video_relative_path)

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
        self.collections.clear()
        self.collection_list.clear()

        try:
            subfolders = sorted(
                d for d in os.listdir(folder_path)
                if os.path.isdir(os.path.join(folder_path, d))
            )
        except Exception as e:
            print(f"扫描文件夹失败: {e}")
            return

        # 进度对话框
        progress = QProgressDialog("正在扫描文件夹...", "取消", 0, len(subfolders), self)
        progress.setWindowTitle("扫描中")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        for i, subfolder in enumerate(subfolders):
            if progress.wasCanceled():
                break
            subfolder_path = os.path.join(folder_path, subfolder)
            videos = scan_folder_for_videos(subfolder_path)
            if videos:
                self.collections.append({
                    'name': subfolder,
                    'path': subfolder_path,
                    'videos': videos,
                })
            progress.setValue(i + 1)
            progress.setLabelText(f"扫描: {subfolder}")

        progress.close()

        self.populate_collection_list()
        self.populate_fav_collection_list()

        # 默认选中第一个
        if self.collections:
            self.collection_list.setCurrentRow(0)
            self.on_collection_clicked(self.collection_list.item(0))

    # ------------------------------------------------------------
    # 填充列表
    # ------------------------------------------------------------
    def populate_collection_list(self):
        self.collection_list.clear()

        # 按标记状态排序：未标记在前
        if self.cache_manager:
            unmarked = [c for c in self.collections
                        if not self.cache_manager.is_marked(c['name'])]
            marked = [c for c in self.collections
                      if self.cache_manager.is_marked(c['name'])]
            ordered = unmarked + marked
        else:
            ordered = self.collections

        for collection in ordered:
            thumbnail = None
            if collection['videos'] and self.cache_manager:
                try:
                    video_path = collection['videos'][0]
                    video_rel = os.path.relpath(video_path, self.root_folder).replace('\\', '/')
                    exists, cache_path = self.cache_manager.cache_exists(video_rel)
                    if exists:
                        thumbnail = cache_path
                except Exception:
                    pass

            item = QListWidgetItem(self.collection_list)
            item.setSizeHint(__import__('PyQt5.QtCore', fromlist=['QSize']).QSize(280, 420))

            is_marked = False
            if self.cache_manager:
                is_marked = self.cache_manager.is_marked(collection['name'])

            widget = CollectionItem(
                collection['name'],
                thumbnail,
                len(collection['videos']),
                is_marked=is_marked,
                on_mark_changed=self.on_collection_mark_changed,
            )
            self.collection_list.setItemWidget(item, widget)

    def refresh_fav_collection_list(self):
        current_row = self.fav_collection_list.currentRow()
        self.populate_fav_collection_list()
        if self.fav_collection_list.count() > current_row >= 0:
            self.fav_collection_list.setCurrentRow(current_row)

    def populate_fav_collection_list(self):
        self.fav_collection_list.clear()
        if not self.cache_manager:
            return

        # 默认收藏夹：使用收藏的第一个视频作为缩略图
        fav_videos = self.cache_manager.get_favorite_videos()
        thumbnail = None
        if fav_videos:
            try:
                first_video = fav_videos[0]
                rel_path = os.path.relpath(first_video, self.root_folder).replace('\\', '/')
                exists, cache_path = self.cache_manager.cache_exists(rel_path)
                if exists:
                    thumbnail = cache_path
            except Exception:
                pass

        from PyQt5.QtCore import QSize
        item = QListWidgetItem(self.fav_collection_list)
        item.setSizeHint(QSize(280, 420))
        widget = CollectionItem(
            "默认收藏夹",
            thumbnail,
            self.cache_manager.get_favorite_count(),
            is_marked=False,
        )
        self.fav_collection_list.setItemWidget(item, widget)

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

        if mode == 0 and self.collections:
            if self.collection_list.count() > 0:
                self.collection_list.setCurrentRow(0)
                self.on_collection_clicked(self.collection_list.item(0))
        elif mode == 1:
            if self.fav_collection_list.count() > 0:
                self.fav_collection_list.setCurrentRow(0)
                self.on_fav_collection_clicked(self.fav_collection_list.item(0))

    def on_collection_clicked(self, item):
        self.current_tab = 0
        widget = self.collection_list.itemWidget(item)
        target_name = widget.name if widget else None
        if target_name is not None:
            for c in self.collections:
                if c['name'] == target_name:
                    self.show_videos(c)
                    return

    def on_fav_collection_clicked(self, item):
        self.current_tab = 1
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
                video_path, video_relative_path, self.cache_manager
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
                video_path, video_relative_path, self.cache_manager
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
