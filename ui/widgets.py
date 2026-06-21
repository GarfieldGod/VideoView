"""UI 组件：FlowLayout / CollectionItem / VideoItem / 列表 / 网格"""

import os

from PyQt5.QtCore import Qt, QSize, QRect, QPoint, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QListWidget, QListWidgetItem, QListView, QScrollArea, QStackedWidget,
    QFileDialog, QProgressDialog, QApplication, QMenu, QAction, QLayout,
)
from PyQt5.QtGui import QPixmap, QPainter, QColor, QDesktopServices
from PyQt5.QtCore import QUrl

from core.thumbnail import THUMBNAIL_SIZE_COLLECTION, THUMBNAIL_SIZE_VIDEO


# ============================================================
# FlowLayout：流式自动换行布局
# ============================================================
class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=10, spacing=10):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.margin = margin
        self.spacing = spacing
        self.item_list = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.item_list.append(item)

    def count(self):
        return len(self.item_list)

    def itemAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Vertical | Qt.Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        size += QSize(2 * self.margin, 2 * self.margin)
        return size

    def _do_layout(self, rect, test_only=False):
        x = rect.x()
        y = rect.y()
        line_height = 0

        for item in self.item_list:
            wid = item.widget()
            space_x = self.spacing
            space_y = self.spacing

            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y()


# ============================================================
# CollectionItem：左侧面板中的单个收藏夹项
# ============================================================
class CollectionItem(QWidget):
    """收藏夹项组件 - 带右上角标记按钮"""

    def __init__(self, name, thumbnail_path, video_count,
                 is_marked=False, parent=None, on_mark_changed=None):
        super().__init__(parent)
        self.name = name
        self.thumbnail_path = thumbnail_path
        self.video_count = video_count
        self.is_marked = is_marked
        self.on_mark_changed = on_mark_changed
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        thumb_container = QWidget()
        thumb_container.setFixedSize(*THUMBNAIL_SIZE_COLLECTION)
        thumb_layout = QVBoxLayout(thumb_container)
        thumb_layout.setContentsMargins(0, 0, 0, 0)

        self.thumbnail_label = QLabel(thumb_container)
        self.thumbnail_label.setFixedSize(*THUMBNAIL_SIZE_COLLECTION)
        self.thumbnail_label.setStyleSheet(
            "QLabel {background-color: #2a2a44; border-radius: 6px; "
            "border: 1px solid #4a4a6a; font-size: 36px; color: #888;}"
        )
        self.thumbnail_label.setAlignment(Qt.AlignCenter)

        if self.thumbnail_path and os.path.exists(self.thumbnail_path):
            pixmap = QPixmap(self.thumbnail_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    THUMBNAIL_SIZE_COLLECTION[0],
                    THUMBNAIL_SIZE_COLLECTION[1],
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
                result = QPixmap(
                    THUMBNAIL_SIZE_COLLECTION[0],
                    THUMBNAIL_SIZE_COLLECTION[1],
                )
                result.fill(QColor('#2a2a44'))
                painter = QPainter(result)
                x = (THUMBNAIL_SIZE_COLLECTION[0] - scaled.width()) // 2
                y = (THUMBNAIL_SIZE_COLLECTION[1] - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
                painter.end()
                self.thumbnail_label.setPixmap(result)
        else:
            self.thumbnail_label.setText("📁")

        self.mark_btn = QPushButton(thumb_container)
        self.mark_btn.setFixedSize(40, 40)
        self.mark_btn.move(THUMBNAIL_SIZE_COLLECTION[0] - 46, 6)
        self.mark_btn.setCursor(Qt.PointingHandCursor)
        self.mark_btn.setFlat(True)
        self.mark_btn.clicked.connect(self._on_mark_clicked)
        self._update_mark_button_style()

        self.name_label = QLabel(self.name)
        self.name_label.setStyleSheet(
            "QLabel {color: #fff; font-size: 20px; font-weight: bold;}"
        )
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setFixedHeight(26)

        self.count_label = QLabel(f"{self.video_count} 个视频")
        self.count_label.setStyleSheet("QLabel {color: #aaa; font-size: 16px;}")
        self.count_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(thumb_container)
        layout.addWidget(self.name_label)
        layout.addWidget(self.count_label)

        self.setFixedWidth(280)

    def _update_mark_button_style(self):
        if self.is_marked:
            self.mark_btn.setStyleSheet(
                "QPushButton {background-color: rgba(60, 160, 90, 0.92); "
                "border: 2px solid rgba(90, 200, 120, 0.9); border-radius: 20px; "
                "color: white; font-size: 22px;}"
                "QPushButton:hover {background-color: rgba(80, 180, 110, 0.95);}"
            )
            self.mark_btn.setText("✓")
        else:
            self.mark_btn.setStyleSheet(
                "QPushButton {background-color: rgba(40, 40, 60, 0.6); "
                "border: 2px solid rgba(100, 100, 120, 0.5); border-radius: 20px; "
                "color: rgba(160, 160, 180, 0.55); font-size: 22px;}"
                "QPushButton:hover {background-color: rgba(60, 60, 80, 0.75); "
                "border: 2px solid rgba(150, 150, 170, 0.7); "
                "color: rgba(200, 200, 220, 0.85);}"
            )
            self.mark_btn.setText("○")

    def _on_mark_clicked(self):
        self.is_marked = not self.is_marked
        self._update_mark_button_style()
        if self.on_mark_changed:
            self.on_mark_changed(self.name, self.is_marked)


# ============================================================
# VideoItem：右侧网格中的单个视频卡片
# ============================================================
class VideoItem(QWidget):
    """视频项组件：异步加载缩略图 + 收藏按钮 + 右键菜单"""
    double_clicked = pyqtSignal(str)

    def __init__(self, video_path, video_relative_path, cache_manager,
                 parent=None, on_thumbnail_ready=None,
                 on_favorite_changed=None, on_double_clicked=None):
        super().__init__(parent)
        self.video_path = video_path
        self.video_relative_path = video_relative_path.replace('\\', '/')
        self.cache_manager = cache_manager
        self.on_thumbnail_ready = on_thumbnail_ready
        self.on_favorite_changed = on_favorite_changed
        self.on_double_clicked = on_double_clicked

        self.thumbnail_width = THUMBNAIL_SIZE_VIDEO[0]
        self.thumbnail_height = THUMBNAIL_SIZE_VIDEO[1]

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self.setup_ui()
        self.load_thumbnail()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        thumb_container = QWidget()
        thumb_container.setFixedSize(self.thumbnail_width, self.thumbnail_height)
        thumb_layout = QVBoxLayout(thumb_container)
        thumb_layout.setContentsMargins(0, 0, 0, 0)

        self.thumbnail_label = QLabel(thumb_container)
        self.thumbnail_label.setFixedSize(self.thumbnail_width, self.thumbnail_height)
        self.thumbnail_label.setStyleSheet(
            "QLabel {background-color: #2a2a44; border-radius: 8px; "
            "border: 1px solid #4a4a6a; color: #888; font-size: 24px;}"
        )
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setText("🎬")

        self.fav_btn = QPushButton(thumb_container)
        self.fav_btn.setFixedSize(52, 52)
        self.fav_btn.move(self.thumbnail_width - 58, 6)
        self.fav_btn.setCursor(Qt.PointingHandCursor)
        self.fav_btn.setFlat(True)
        self.fav_btn.clicked.connect(self._on_fav_clicked)
        self._update_fav_button_style()

        self.name_label = QLabel(os.path.splitext(os.path.basename(self.video_path))[0])
        self.name_label.setStyleSheet("QLabel {color: #fff; font-size: 18px;}")
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setFixedHeight(28)
        self.name_label.setWordWrap(True)

        layout.addWidget(thumb_container)
        layout.addWidget(self.name_label)
        self.setFixedWidth(THUMBNAIL_SIZE_VIDEO[0] + 20)

    def _update_fav_button_style(self):
        is_fav = False
        if self.cache_manager:
            is_fav = self.cache_manager.is_favorite(self.video_relative_path)

        if is_fav:
            self.fav_btn.setStyleSheet(
                "QPushButton {background-color: rgba(230, 50, 50, 0.92); "
                "border: 2px solid rgba(255, 80, 80, 0.9); border-radius: 26px; "
                "color: white; font-size: 28px;}"
                "QPushButton:hover {background-color: rgba(255, 70, 70, 0.95);}"
            )
        else:
            self.fav_btn.setStyleSheet(
                "QPushButton {background-color: rgba(40, 40, 60, 0.7); "
                "border: 2px solid rgba(100, 100, 120, 0.6); border-radius: 26px; "
                "color: rgba(180, 180, 200, 0.6); font-size: 28px;}"
                "QPushButton:hover {background-color: rgba(60, 60, 80, 0.8); "
                "border: 2px solid rgba(150, 150, 170, 0.8); "
                "color: rgba(220, 220, 240, 0.9);}"
            )
        self.fav_btn.setText("♥")

    def _on_fav_clicked(self):
        if not self.cache_manager:
            return
        new_state = self.cache_manager.toggle_favorite(self.video_relative_path)
        self._update_fav_button_style()
        if self.on_favorite_changed:
            self.on_favorite_changed(self.video_relative_path, new_state)

    def mouseDoubleClickEvent(self, event):
        """双击 VideoItem — 应用内播放"""
        event.accept()
        try:
            if self.on_double_clicked:
                self.on_double_clicked(self.video_path, self.video_relative_path)
        except Exception as e:
            print(f"[VideoItem] 双击事件出错: {e}")

    def _on_context_menu(self, pos):
        """右键菜单：应用内播放 / 以本地播放器打开"""
        try:
            menu = QMenu(self)

            action_internal = QAction("播放", self)
            action_internal.triggered.connect(
                lambda: self.on_double_clicked(self.video_path, self.video_relative_path)
            )
            menu.addAction(action_internal)

            action_external = QAction("以本地播放器打开", self)
            action_external.triggered.connect(lambda: self._open_with_system_player())
            menu.addAction(action_external)

            menu.addSeparator()

            action_explorer = QAction("在资源管理器中浏览", self)
            action_explorer.triggered.connect(lambda: self._open_in_explorer())
            menu.addAction(action_explorer)

            menu.exec_(self.mapToGlobal(pos))
        except Exception as e:
            print(f"[VideoItem] 右键菜单异常: {e}")

    def _open_with_system_player(self):
        """优先用第三方播放器；否则回退系统默认关联程序"""
        abs_path = os.path.abspath(self.video_path)
        external_players = [
            r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
            r"C:\Program Files\PotPlayer\PotPlayerMini64.exe",
            r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini.exe",
            r"C:\Program Files (x86)\PotPlayer\PotPlayerMini.exe",
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        ]

        chosen = None
        for exe in external_players:
            if os.path.isfile(exe):
                chosen = exe
                break

        try:
            if chosen is not None:
                import subprocess
                subprocess.Popen([chosen, abs_path], close_fds=True)
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(abs_path))
        except Exception as e:
            print(f"[VideoItem] 外部播放器打开失败: {e}")
            try:
                if os.name == 'nt':
                    os.startfile(abs_path)
            except Exception as e2:
                print(f"[VideoItem] 系统打开也失败: {e2}")

    def _open_in_explorer(self):
        """在资源管理器中打开所在目录并选中该文件"""
        try:
            abs_path = os.path.abspath(self.video_path)
            folder = os.path.dirname(abs_path)
            if os.name == 'nt':
                import subprocess
                subprocess.Popen(f'explorer /select,"{abs_path}"', close_fds=True)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        except Exception as e:
            print(f"[VideoItem] 资源管理器打开失败: {e}")

    def sizeHint(self):
        return QSize(THUMBNAIL_SIZE_VIDEO[0] + 20, self.thumbnail_height + 50)

    def load_thumbnail(self):
        if not self.cache_manager:
            return

        exists, cache_path = self.cache_manager.cache_exists(self.video_relative_path)
        if exists:
            pixmap = QPixmap(cache_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.thumbnail_width, self.thumbnail_height,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
                self.thumbnail_label.setPixmap(scaled)
                return

        if self.on_thumbnail_ready:
            self.on_thumbnail_ready(self.video_path, self.video_relative_path, self)

    def update_from_cache(self):
        if not self.cache_manager:
            return
        exists, cache_path = self.cache_manager.cache_exists(self.video_relative_path)
        if exists:
            pixmap = QPixmap(cache_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.thumbnail_width, self.thumbnail_height,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
                self.thumbnail_label.setPixmap(scaled)


# ============================================================
# 左侧收藏夹列表控件
# ============================================================
class CollectionListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QListWidget {background-color: #1a1a2e; border: none; padding: 10px;}"
            "QListWidget::item {background-color: transparent; border-radius: 8px; padding: 8px;}"
            "QListWidget::item:selected {background-color: #2d2d44; border: 1px solid #4a4a6a;}"
            "QListWidget::item:hover {background-color: #252540;}"
        )
        self.setSpacing(8)
        self.setFlow(QListView.TopToBottom)
        self.setResizeMode(QListWidget.Fixed)


# ============================================================
# 右侧视频网格控件
# ============================================================
class VideoGridWidget(QWidget):
    """视频网格组件 - 流式布局"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_items = {}
        self.on_favorite_changed_callback = None
        self.on_double_clicked_callback = None
        self.setup_ui()

    def set_on_favorite_changed(self, callback):
        self.on_favorite_changed_callback = callback

    def set_on_double_clicked(self, callback):
        self.on_double_clicked_callback = callback

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(16)

        self.title_label = QLabel("全部视频")
        self.title_label.setStyleSheet(
            "QLabel {color: #fff; font-size: 34px; font-weight: bold; padding: 10px 0;}"
        )

        self.scroll_area = QScrollArea()
        self.scroll_area.setStyleSheet(
            "QScrollArea {background-color: #16162a; border: none;}"
            "QScrollBar:vertical {background-color: #2a2a4a; width: 12px; border-radius: 6px;}"
            "QScrollBar::handle:vertical {background-color: #4a4a6a; border-radius: 6px; min-height: 30px;}"
            "QScrollBar::handle:vertical:hover {background-color: #5a5a7a;}"
        )
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background-color: #16162a;")
        self.flow_layout = FlowLayout(self.content_widget, 10, 16)

        self.scroll_area.setWidget(self.content_widget)
        self.main_layout.addWidget(self.title_label)
        self.main_layout.addWidget(self.scroll_area)

    def set_title(self, title):
        self.title_label.setText(title)

    def clear_videos(self):
        self.video_items.clear()
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def find_main_window(self):
        from PyQt5.QtWidgets import QMainWindow
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, QMainWindow):
                return parent
            parent = parent.parent()
        return None

    def on_thumbnail_requested(self, video_path, video_relative_path, video_item):
        """请求生成缩略图 — 查找主窗口的 thumbnail_manager 并入队"""
        main_window = self.find_main_window()
        if main_window and hasattr(main_window, 'thumbnail_manager') \
                and main_window.thumbnail_manager:
            main_window.thumbnail_manager.enqueue(video_path, video_relative_path)

    def on_thumbnail_generated(self, video_relative_path):
        if video_relative_path in self.video_items:
            self.video_items[video_relative_path].update_from_cache()

    def add_video(self, video_path, video_relative_path, cache_manager):
        video_item = VideoItem(
            video_path, video_relative_path, cache_manager,
            on_thumbnail_ready=self.on_thumbnail_requested,
            on_favorite_changed=self.on_favorite_changed_callback,
            on_double_clicked=self.on_double_clicked_callback,
        )
        self.video_items[video_relative_path] = video_item
        self.flow_layout.addWidget(video_item)
