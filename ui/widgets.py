"""UI 组件：FlowLayout / CollectionItem / VideoItem / 列表 / 网格"""

import os

from PyQt5.QtCore import Qt, QSize, QRect, QPoint, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QListWidget, QListWidgetItem, QListView, QScrollArea, QStackedWidget,
    QFileDialog, QProgressDialog, QApplication, QMenu, QAction, QLayout,
)
from PyQt5.QtGui import QPixmap, QPainter, QColor, QDesktopServices
from PyQt5.QtCore import QUrl

from core.thumbnail import THUMBNAIL_SIZE_COLLECTION, THUMBNAIL_SIZE_VIDEO


# 右键菜单统一样式：文字白色，悬浮黄色
MENU_QSS = (
    "QMenu {background-color:#2a2a44; border:1px solid #4a4a6a;"
    "border-radius:6px; padding:4px;}"
    "QMenu::item {color:white; padding:8px 24px; border-radius:4px;}"
    "QMenu::item:selected {background-color:#4a4a6a; color:#ffcc00;}"
    "QMenu::separator {height:1px; background:#4a4a6a; margin:4px 8px;}"
)


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
                 is_marked=False, parent=None, on_mark_changed=None,
                 cache_manager=None, video_relative_path=None,
                 on_thumbnail_ready=None, on_batch_rotate=None,
                 on_batch_favorite=None,
                 on_batch_unfavorite=None,
                 cache_manager_resolver=None):
        super().__init__(parent)
        self.name = name
        self.thumbnail_path = thumbnail_path
        self.video_count = video_count
        self.is_marked = is_marked
        self.on_mark_changed = on_mark_changed
        self.cache_manager = cache_manager
        self.video_relative_path = video_relative_path
        self.on_thumbnail_ready = on_thumbnail_ready
        self.on_batch_rotate = on_batch_rotate
        self.on_batch_favorite = on_batch_favorite
        self.on_batch_unfavorite = on_batch_unfavorite
        # 缓存管理器解析器：返回给定 rel_path 应使用的 CacheManager（支持子目录独立配置）
        self.cache_manager_resolver = cache_manager_resolver
        self._thumbnail_loaded = False
        self.setup_ui()
        # 不在初始化时加载缩略图：避免一次性为所有文件夹生成缩略图造成高负载
        # 仅在用户点击时调用 load_thumbnail() 才开始加载

    def load_thumbnail(self):
        """加载缩略图：优先从缓存加载；否则触发异步生成（按当前旋转角度生成到同一缓存路径）"""
        if self._thumbnail_loaded:
            return
        self._thumbnail_loaded = True
        cm = self._resolve_cache_manager()
        if not cm or not self.video_relative_path:
            return

        # 统一从 hash.jpg 读取
        exists, cache_path = cm.cache_exists(self.video_relative_path)
        if exists:
            self._set_thumbnail(cache_path)
            return

        # 获取当前旋转角度用于生成新缩略图
        rotation = 0
        try:
            rotation = int(cm.get_rotation(self.video_relative_path)) % 360
        except Exception:
            pass

        # 触发异步生成（按当前旋转角度生成到 hash.jpg）
        if self.on_thumbnail_ready:
            self.on_thumbnail_ready(self.video_relative_path, rotation)

    def _resolve_cache_manager(self):
        """获取正确的 CacheManager（支持子目录独立配置）"""
        if self.cache_manager_resolver:
            return self.cache_manager_resolver(self.video_relative_path)
        return self.cache_manager

    def release_thumbnail(self):
        """释放缩略图（滑出视口时调用，节省内存）"""
        if not self._thumbnail_loaded:
            return
        self._thumbnail_loaded = False
        # 清空 label 上的 pixmap：setText("📁") 并释放内存
        try:
            self.thumbnail_label.clear()
            self.thumbnail_label.setText("📁")
        except Exception:
            pass

    def is_thumbnail_loaded(self):
        return self._thumbnail_loaded

    def update_from_cache(self):
        """从缓存刷新缩略图（缩略图生成完成后调用，或旋转后调用）"""
        cm = self._resolve_cache_manager()
        if not cm or not self.video_relative_path:
            return

        exists, cache_path = cm.cache_exists(self.video_relative_path)
        if exists:
            self._thumbnail_loaded = True
            self._set_thumbnail(cache_path)
        else:
            # 没有缓存 — 释放当前缩略图并触发异步生成
            try:
                self.thumbnail_label.clear()
                self.thumbnail_label.setText("📁")
            except Exception:
                pass
            self._thumbnail_loaded = False
            rotation = 0
            try:
                rotation = int(cm.get_rotation(self.video_relative_path)) % 360
            except Exception:
                pass
            if self.on_thumbnail_ready:
                self.on_thumbnail_ready(self.video_relative_path, rotation)
                self._thumbnail_loaded = True

    def _set_thumbnail(self, cache_path):
        """设置缩略图到 label"""
        pixmap = QPixmap(cache_path)
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

        # 初始显示占位符
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

        layout.addWidget(thumb_container, alignment=Qt.AlignCenter)
        layout.addWidget(self.name_label, alignment=Qt.AlignCenter)
        layout.addWidget(self.count_label, alignment=Qt.AlignCenter)

        # 右键菜单支持（批量旋转整个收藏夹的视频 + 批量收藏）
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def set_selected_state(self, is_selected):
        """根据选中状态调整文字颜色：选中 → 深色文字（白色背景），未选中 → 浅色文字（深色背景）"""
        if is_selected:
            self.name_label.setStyleSheet(
                "QLabel {color: #222; font-size: 20px; font-weight: bold;}"
            )
            self.count_label.setStyleSheet(
                "QLabel {color: #555; font-size: 16px;}"
            )
        else:
            self.name_label.setStyleSheet(
                "QLabel {color: #fff; font-size: 20px; font-weight: bold;}"
            )
            self.count_label.setStyleSheet(
                "QLabel {color: #aaa; font-size: 16px;}"
            )

    def _update_mark_button_style(self):
        if self.is_marked:
            self.mark_btn.setStyleSheet(
                "QPushButton {background-color: rgba(60, 160, 90, 0.92); "
                "border: 2px solid rgba(90, 200, 120, 0.9); border-radius: 20px; "
                "color: white; font-size: 22px;}"
                "padding: 0px;"
                "QPushButton:hover {background-color: rgba(80, 180, 110, 0.95);}"
            )
        else:
            self.mark_btn.setStyleSheet(
                "QPushButton {background-color: rgba(40, 40, 60, 0.6); "
                "border: 2px solid rgba(100, 100, 120, 0.5); border-radius: 20px; "
                "color: rgba(160, 160, 180, 0.55); font-size: 22px;}"
                "padding: 0px;"
                "QPushButton:hover {background-color: rgba(60, 60, 80, 0.75); "
                "border: 2px solid rgba(150, 150, 170, 0.7); "
                "color: rgba(200, 200, 220, 0.85);}"
            )

    def _on_mark_clicked(self):
        self.is_marked = not self.is_marked
        self._update_mark_button_style()
        if self.on_mark_changed:
            self.on_mark_changed(self.name, self.is_marked)

    def _on_context_menu(self, pos):
        """右键菜单：批量收藏 / 批量取消收藏 / 批量旋转（左旋90°/右旋90°）

        - on_batch_favorite → 点击"批量收藏到最爱
        - on_batch_unfavorite → 批量取消该收藏夹
        - on_batch_rotate → 批量旋转该收藏夹
        """
        try:
            if not self.on_batch_rotate and not self.on_batch_favorite and not self.on_batch_unfavorite:
                return
            menu = QMenu(self)
            menu.setStyleSheet(MENU_QSS)
            has_items = False
            if self.on_batch_unfavorite:
                action_unfav = QAction(f"✕ 批量取消收藏（{self.video_count} 个视频）", self)
                action_unfav.triggered.connect(lambda: self.on_batch_unfavorite(self.name))
                menu.addAction(action_unfav)
                has_items = True
            if self.on_batch_favorite:
                if has_items:
                    menu.addSeparator()
                action_fav = QAction(f"⭐ 批量收藏到最爱（{self.video_count} 个视频）", self)
                action_fav.triggered.connect(lambda: self.on_batch_favorite(self.name))
                menu.addAction(action_fav)
                has_items = True
            if self.on_batch_rotate:
                if has_items:
                    menu.addSeparator()
                action_left = QAction(f"↺ 左旋 90°（{self.video_count} 个视频）", self)
                action_left.triggered.connect(lambda: self.on_batch_rotate(self.name, -90))
                menu.addAction(action_left)
                action_right = QAction(f"↻ 右旋 90°（{self.video_count} 个视频）", self)
                action_right.triggered.connect(lambda: self.on_batch_rotate(self.name, 90))
                menu.addAction(action_right)
                has_items = True

            if has_items:
                menu.exec_(self.mapToGlobal(pos))
        except Exception as e:
            print(f"[CollectionItem] 右键菜单异常: {e}")

# ============================================================
# VideoItem：右侧网格中的单个视频卡片
# ============================================================
class VideoItem(QWidget):
    """视频项组件：异步加载缩略图 + 收藏按钮 + 右键菜单"""
    double_clicked = pyqtSignal(str)

    def __init__(self, video_path, video_relative_path, cache_manager,
                 parent=None, on_thumbnail_ready=None,
                 on_favorite_changed=None, on_double_clicked=None,
                 cache_manager_resolver=None):
        super().__init__(parent)
        self.video_path = video_path
        self.video_relative_path = video_relative_path.replace('\\', '/')
        self.cache_manager = cache_manager
        self.on_thumbnail_ready = on_thumbnail_ready
        self.on_favorite_changed = on_favorite_changed
        self.on_double_clicked = on_double_clicked
        # 缓存管理器解析器：返回给定 rel_path 应使用的 CacheManager（支持子目录独立配置）
        self.cache_manager_resolver = cache_manager_resolver

        self.thumbnail_width = THUMBNAIL_SIZE_VIDEO[0]
        self.thumbnail_height = THUMBNAIL_SIZE_VIDEO[1]
        self._thumbnail_loaded = False
        self._is_last_played = False

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self.setup_ui()
        # 不自动加载缩略图：交由 VideoGridWidget._update_viewport_items 管理

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

        # 「上次播放位置」标签（高亮时显示，默认隐藏）
        # 大尺寸绿色底白字，醒目提示用户这是最近播放的位置
        self.last_played_label = QLabel("上次播放位置", thumb_container)
        self.last_played_label.setFixedHeight(68)  # 约为原来2.4倍
        self.last_played_label.setFixedWidth(232)
        self.last_played_label.setStyleSheet(
            "QLabel {background-color: #22aa33; "  # 绿色背景
            "color: #ffffff; font-size: 34px; font-weight: bold; "  # 白字+放大约2.4倍
            "padding: 0px 0px; border-radius: 15px;}"
        )
        self.last_played_label.setAlignment(Qt.AlignCenter)
        self.last_played_label.hide()

        # 将「上次播放位置」标签放在缩略图底部
        self.last_played_label.move(
            20, self.thumbnail_height - self.last_played_label.height() - 20
        )

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

    def set_last_played(self, is_last):
        """设置为 / 取消 最近一次播放的视频卡片"""
        if getattr(self, '_is_last_played', False) == bool(is_last):
            return
        self._is_last_played = bool(is_last)
        try:
            if self._is_last_played:
                # 绿色高亮边框（约为原来的 2-3 倍粗）+ 显示绿色大字标签
                self.thumbnail_label.setStyleSheet(
                    "QLabel {background-color: #2a2a44; border-radius: 15px; "
                    "border: 10px solid #22aa33; color: #888; font-size: 24px;}"
                )
                self.last_played_label.show()
                self.setStyleSheet(
                    "QWidget {background-color: transparent;}"
                )
            else:
                # 恢复默认样式
                self.thumbnail_label.setStyleSheet(
                    "QLabel {background-color: #2a2a44; border-radius: 8px; "
                    "border: 1px solid #4a4a6a; color: #888; font-size: 24px;}"
                )
                self.last_played_label.hide()
        except Exception as e:
            print(f"[VideoItem] set_last_played 异常: {e}")

    def _update_fav_button_style(self):
        is_fav = False
        cm = self._resolve_cache_manager()
        if cm:
            is_fav = cm.is_favorite(self.video_relative_path)

        if is_fav:
            self.fav_btn.setStyleSheet(
                "QPushButton {background-color: rgba(230, 50, 50, 0.92); "
                "border: 2px solid rgba(255, 80, 80, 0.9); border-radius: 26px; "
                "padding: 0px;"
                "color: white; font-size: 42px;}"
                "QPushButton:hover {background-color: rgba(255, 70, 70, 0.95);}"
            )
        else:
            self.fav_btn.setStyleSheet(
                "QPushButton {background-color: rgba(40, 40, 60, 0.7); "
                "border: 2px solid rgba(100, 100, 120, 0.6); border-radius: 26px; "
                "padding: 0px;"
                "color: rgba(180, 180, 200, 0.6); font-size: 42px;}"
                "QPushButton:hover {background-color: rgba(60, 60, 80, 0.8); "
                "border: 2px solid rgba(150, 150, 170, 0.8); "
                "color: rgba(220, 220, 240, 0.9);}"
            )
        self.fav_btn.setText("♥")

    def _on_fav_clicked(self):
        cm = self._resolve_cache_manager()
        if not cm:
            return
        new_state = cm.toggle_favorite(self.video_relative_path)
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
        """右键菜单：播放 / 以本地播放器打开 / 左旋90° / 右旋90° / 在资源管理器中浏览"""
        try:
            menu = QMenu(self)
            menu.setStyleSheet(MENU_QSS)

            action_internal = QAction("播放", self)
            action_internal.triggered.connect(
                lambda: self.on_double_clicked(self.video_path, self.video_relative_path)
            )
            menu.addAction(action_internal)

            action_external = QAction("以本地播放器打开", self)
            action_external.triggered.connect(lambda: self._open_with_system_player())
            menu.addAction(action_external)

            menu.addSeparator()

            action_left = QAction("↺ 左旋 90°", self)
            action_left.triggered.connect(lambda: self._rotate_video(-90))
            menu.addAction(action_left)

            action_right = QAction("↻ 右旋 90°", self)
            action_right.triggered.connect(lambda: self._rotate_video(90))
            menu.addAction(action_right)

            menu.addSeparator()

            action_explorer = QAction("在资源管理器中浏览", self)
            action_explorer.triggered.connect(lambda: self._open_in_explorer())
            menu.addAction(action_explorer)

            menu.exec_(self.mapToGlobal(pos))
        except Exception as e:
            print(f"[VideoItem] 右键菜单异常: {e}")

    def _rotate_video(self, delta_deg):
        """旋转单个视频：更新缓存旋转角度 + 清除缓存 + 触发重新生成缩略图"""
        try:
            cm = self._resolve_cache_manager()
            if not cm:
                return
            if delta_deg < 0:
                cm.rotate_left(self.video_relative_path)
            else:
                cm.rotate_right(self.video_relative_path)
            cm.clear_cache_for(self.video_relative_path)
            # 重置缩略图加载状态，触发重新生成
            self._thumbnail_loaded = False
            try:
                self.thumbnail_label.clear()
                self.thumbnail_label.setText("🎬")
            except Exception:
                pass
            rotation = 0
            try:
                rotation = int(cm.get_rotation(self.video_relative_path)) % 360
            except Exception:
                pass
            if self.on_thumbnail_ready:
                self.on_thumbnail_ready(self.video_path, self.video_relative_path, self, rotation)
        except Exception as e:
            print(f"[VideoItem] 旋转异常: {e}")

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
        """加载缩略图：优先从缓存加载；否则触发异步生成（按当前旋转角度）"""
        if self._thumbnail_loaded:
            return
        self._thumbnail_loaded = True
        cm = self._resolve_cache_manager()
        if not cm:
            return

        exists, cache_path = cm.cache_exists(self.video_relative_path)
        if exists:
            pixmap = QPixmap(cache_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.thumbnail_width, self.thumbnail_height,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
                self.thumbnail_label.setPixmap(scaled)
                return

        # 无缓存：获取当前旋转角度并触发生成
        rotation = 0
        try:
            rotation = int(cm.get_rotation(self.video_relative_path)) % 360
        except Exception:
            pass
        if self.on_thumbnail_ready:
            self.on_thumbnail_ready(self.video_path, self.video_relative_path, self, rotation)

    def release_thumbnail(self):
        """滑出视口时释放缩略图内存"""
        if not self._thumbnail_loaded:
            return
        self._thumbnail_loaded = False
        try:
            self.thumbnail_label.clear()
            self.thumbnail_label.setText("🎬")
        except Exception:
            pass

    def is_thumbnail_loaded(self):
        return self._thumbnail_loaded

    def _resolve_cache_manager(self):
        """获取正确的 CacheManager（支持子目录独立配置）"""
        if self.cache_manager_resolver:
            return self.cache_manager_resolver(self.video_relative_path)
        return self.cache_manager

    def update_from_cache(self):
        """缩略图异步生成完成后回调 — 从缓存读取最新缩略图"""
        cm = self._resolve_cache_manager()
        if not cm:
            return

        exists, cache_path = cm.cache_exists(self.video_relative_path)
        if exists:
            self._thumbnail_loaded = True
            pixmap = QPixmap(cache_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.thumbnail_width, self.thumbnail_height,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
                self.thumbnail_label.setPixmap(scaled)
        else:
            # 无缓存 — 重置并触发生成（旋转后若还在排队中会走这里）
            self._thumbnail_loaded = False
            try:
                self.thumbnail_label.clear()
                self.thumbnail_label.setText("🎬")
            except Exception:
                pass
            rotation = 0
            try:
                rotation = int(cm.get_rotation(self.video_relative_path)) % 360
            except Exception:
                pass
            if self.on_thumbnail_ready:
                self.on_thumbnail_ready(self.video_path, self.video_relative_path, self, rotation)
                self._thumbnail_loaded = True


# ============================================================
# 左侧收藏夹列表控件：视口范围内加载、范围外释放
# ============================================================
class CollectionListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QListWidget {background-color: #1a1a2e; border: none; padding: 10px;}"
            "QListWidget::item {background-color: transparent; border-radius: 8px; padding: 8px;}"
            "QListWidget::item:selected {background-color: #ffffff; "
            "border: 2px solid #4a4a6a; border-radius: 8px; padding: 8px;}"
            "QListWidget::item:hover {background-color: #252540;}"
            "QListWidget::item:selected:hover {background-color: #ffffff;}"
        )
        self.setSpacing(8)
        self.setFlow(QListView.TopToBottom)
        self.setResizeMode(QListWidget.Fixed)
        self.itemSelectionChanged.connect(self._on_selection_changed)

        # 用 QTimer 去抖：滚动/调整大小后只做一次批量刷新
        self._pending_refresh_timer = QTimer(self)
        self._pending_refresh_timer.setSingleShot(True)
        self._pending_refresh_timer.setInterval(80)
        self._pending_refresh_timer.timeout.connect(self._update_viewport_items)

        # 垂直滚动条、水平滚动条、尺寸变化都触发视口刷新
        try:
            self.verticalScrollBar().valueChanged.connect(
                lambda _=None: self._pending_refresh_timer.start()
            )
            self.horizontalScrollBar().valueChanged.connect(
                lambda _=None: self._pending_refresh_timer.start()
            )
        except Exception:
            pass

    def _on_selection_changed(self):
        """选中项变化时：让选中项显示深色文字（白色背景），未选中项显示浅色文字"""
        for i in range(self.count()):
            item = self.item(i)
            if item is None:
                continue
            widget = self.itemWidget(item)
            if widget is None or not hasattr(widget, 'set_selected_state'):
                continue
            widget.set_selected_state(item.isSelected())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._pending_refresh_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._pending_refresh_timer.start()

    def _update_viewport_items(self):
        """仅加载视口内 + 周围 margin 范围内的缩略图，其余释放"""
        if self.count() == 0:
            return
        try:
            viewport_rect = self.viewport().rect()
            # margin：上下各扩展一个 CollectionItem 大致高度，提前预加载
            item_height = self.visualItemRect(self.item(0)).height() if self.count() > 0 else 400
            margin = max(item_height * 1, 300)
            expanded = viewport_rect.adjusted(0, -margin, 0, margin)

            for i in range(self.count()):
                item = self.item(i)
                if item is None:
                    continue
                widget = self.itemWidget(item)
                if widget is None or not hasattr(widget, 'load_thumbnail'):
                    continue
                item_rect = self.visualItemRect(item)
                if item_rect.intersects(expanded):
                    # 在视口范围内 → 加载（幂等）
                    widget.load_thumbnail()
                else:
                    # 滑出视口（且超出 margin）→ 释放
                    if hasattr(widget, 'release_thumbnail'):
                        widget.release_thumbnail()
        except Exception as e:
            print(f"[CollectionListWidget] _update_viewport_items 异常: {e}")


# ============================================================
# 右侧视频网格控件：视口范围内加载、范围外释放
# ============================================================
class VideoGridWidget(QWidget):
    """视频网格组件 - 流式布局 + 视口范围内按需加载缩略图"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_items = {}
        self.on_favorite_changed_callback = None
        self.on_double_clicked_callback = None
        # 上下切换收藏夹回调：on_prev_collection() / on_next_collection()
        # 由主窗口绑定，用于标题栏左右三角形按钮
        self.on_prev_collection = None
        self.on_next_collection = None
        self.setup_ui()

        # 视口刷新：滚动/尺寸变化后延迟合并刷新，避免高频率计算
        self._pending_refresh_timer = QTimer(self)
        self._pending_refresh_timer.setSingleShot(True)
        self._pending_refresh_timer.setInterval(80)
        self._pending_refresh_timer.timeout.connect(self._update_viewport_items)

        # 连接滚动条信号
        try:
            self.scroll_area.verticalScrollBar().valueChanged.connect(
                lambda _=None: self._pending_refresh_timer.start()
            )
            self.scroll_area.horizontalScrollBar().valueChanged.connect(
                lambda _=None: self._pending_refresh_timer.start()
            )
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._pending_refresh_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._pending_refresh_timer.start()

    def _update_viewport_items(self):
        """根据当前视口加载可见项的缩略图，释放滑出视口的项"""
        if not self.video_items:
            return
        try:
            vp = self.scroll_area.viewport()
            if vp is None:
                return

            # 以 content_widget 的坐标系构建可见 rect
            x_offset = self.scroll_area.horizontalScrollBar().value()
            y_offset = self.scroll_area.verticalScrollBar().value()
            visible_rect = QRect(
                int(x_offset),
                int(y_offset),
                vp.width(),
                vp.height(),
            )
            # margin：提前预加载视口上下各约一个 VideoItem 高度的范围
            margin_y = 500  # 约 ~560px 宽 * 16/9 的高度 + 标签
            margin_x = 400
            expanded = visible_rect.adjusted(-margin_x, -margin_y, margin_x, margin_y)

            for key, item in list(self.video_items.items()):
                if item is None or not hasattr(item, 'load_thumbnail'):
                    continue
                try:
                    geom = item.geometry()
                    if geom.width() == 0 and geom.height() == 0:
                        # 还没布局完成 → 先不处理
                        continue
                    if geom.intersects(expanded):
                        item.load_thumbnail()
                    elif hasattr(item, 'release_thumbnail'):
                        item.release_thumbnail()
                except Exception:
                    continue

        except Exception as e:
            print(f"[VideoGridWidget] _update_viewport_items 异常: {e}")

    def set_on_favorite_changed(self, callback):
        self.on_favorite_changed_callback = callback

    def set_on_double_clicked(self, callback):
        self.on_double_clicked_callback = callback

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(16)

        # ========== 标题栏：◀ 📁 根目录名 : 收藏夹名 (XX 个视频) ▶ ==========
        self.header_widget = QWidget()
        self.header_widget.setStyleSheet("background-color: transparent;")
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 10, 0, 10)
        header_layout.setSpacing(12)

        # ◀ 上一个收藏夹按钮
        self.prev_btn = QPushButton("◀")
        self.prev_btn.setFixedSize(56, 56)
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.setStyleSheet(
            "QPushButton {background-color: #2a2a44; color: #fff; border: 2px solid #4a4a6a;"
            "border-radius: 28px; font-size: 22px; font-weight: bold;}"
            "QPushButton:hover {background-color: #3a3a5a; color: #ffcc00;}"
            "QPushButton:disabled {background-color: #1a1a2e; color: #555; border-color: #2a2a4a;}"
        )
        self.prev_btn.clicked.connect(self._on_prev_clicked)

        # 图标
        self.icon_label = QLabel("📁")
        self.icon_label.setStyleSheet(
            "QLabel {color: #fff; font-size: 30px;}"
        )
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setFixedWidth(44)

        # 主标题：根目录名 : 收藏夹名 (XX 个视频)
        self.title_label = QLabel("全部视频")
        self.title_label.setStyleSheet(
            "QLabel {color: #fff; font-size: 28px; font-weight: bold; padding: 0px;}"
        )
        self.title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # ▶ 下一个收藏夹按钮
        self.next_btn = QPushButton("▶")
        self.next_btn.setFixedSize(56, 56)
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.setStyleSheet(
            "QPushButton {background-color: #2a2a44; color: #fff; border: 2px solid #4a4a6a;"
            "border-radius: 28px; font-size: 22px; font-weight: bold;}"
            "QPushButton:hover {background-color: #3a3a5a; color: #ffcc00;}"
            "QPushButton:disabled {background-color: #1a1a2e; color: #555; border-color: #2a2a4a;}"
        )
        self.next_btn.clicked.connect(self._on_next_clicked)

        header_layout.addWidget(self.prev_btn, 0, Qt.AlignVCenter)
        header_layout.addWidget(self.icon_label, 0, Qt.AlignVCenter)
        header_layout.addWidget(self.title_label, 1, Qt.AlignVCenter)
        header_layout.addWidget(self.next_btn, 0, Qt.AlignVCenter)

        # ========== 滚动区域 ==========
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
        self.main_layout.addWidget(self.header_widget)
        self.main_layout.addWidget(self.scroll_area)

    def _on_prev_clicked(self):
        if self.on_prev_collection:
            self.on_prev_collection()

    def _on_next_clicked(self):
        if self.on_next_collection:
            self.on_next_collection()

    def set_title(self, title):
        """兼容旧接口：直接设置文字（不包含图标/根目录信息时使用）"""
        self.title_label.setText(title)

    def set_title_header(self, icon, root_name, collection_name, video_count,
                          can_prev=True, can_next=True):
        """设置完整标题栏：◀ 图标 根目录名 : 收藏夹名 (XX 个视频) ▶"""
        try:
            self.icon_label.setText(icon if icon else "📁")
            if root_name and collection_name:
                # 只显示根目录的最后一级名，避免过长
                display_root = os.path.basename(root_name.rstrip('/')) or root_name
                self.title_label.setText(f"{display_root} : {collection_name} ({video_count} 个视频)")
            elif collection_name:
                self.title_label.setText(f"{collection_name} ({video_count} 个视频)")
            else:
                self.title_label.setText(f"全部视频 ({video_count} 个视频)")
        except Exception:
            self.title_label.setText(f"{collection_name or ''} ({video_count} 个视频)")
        # 根据 can_prev/can_next 控制按钮可用性
        try:
            self.prev_btn.setEnabled(bool(can_prev))
            self.next_btn.setEnabled(bool(can_next))
        except Exception:
            pass

    def clear_videos(self):
        """清空视频网格并将滚动条重置到最上方（切换收藏夹时调用）"""
        self.video_items.clear()
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # 重置滚动条到最顶部，避免切换收藏夹后保持在上一位置
        try:
            if hasattr(self, 'scroll_area') and self.scroll_area is not None:
                vsb = self.scroll_area.verticalScrollBar()
                if vsb is not None:
                    vsb.setValue(vsb.minimum() if hasattr(vsb, 'minimum') else 0)
                hsb = self.scroll_area.horizontalScrollBar()
                if hsb is not None:
                    hsb.setValue(hsb.minimum() if hasattr(hsb, 'minimum') else 0)
        except Exception:
            pass

    def highlight_last_played(self, video_relative_path):
        """将指定视频标记为「上次播放位置」，其他视频取消该状态。

        返回 True 表示在当前网格中找到了对应视频。
        """
        if not video_relative_path:
            for key, item in self.video_items.items():
                try:
                    if item is not None and hasattr(item, 'set_last_played'):
                        item.set_last_played(False)
                except Exception:
                    continue
            return False

        target_rel = video_relative_path.replace('\\', '/')
        found = False
        for key, item in self.video_items.items():
            try:
                if item is None or not hasattr(item, 'set_last_played'):
                    continue
                item_rel = getattr(item, 'video_relative_path', None)
                if item_rel and item_rel.replace('\\', '/') == target_rel:
                    item.set_last_played(True)
                    found = True
                else:
                    item.set_last_played(False)
            except Exception:
                continue
        return found

    def scroll_to_video(self, video_relative_path):
        """将视图滚动到指定视频所在位置。

        用于打开根目录后定位到最近播放的视频。
        """
        if not video_relative_path:
            return
        target_rel = video_relative_path.replace('\\', '/')
        try:
            item = self.video_items.get(target_rel)
            if item is None:
                # 做兜底搜索：遍历所有项匹配相对路径
                for key, it in self.video_items.items():
                    try:
                        if it is None:
                            continue
                        item_rel = getattr(it, 'video_relative_path', None)
                        if item_rel and item_rel.replace('\\', '/') == target_rel:
                            item = it
                            break
                    except Exception:
                        continue
            if item is None:
                return
            # 定位到该 item：使用 geometry() 相对坐标
            vp = self.scroll_area.viewport()
            if vp is None:
                return
            parent_widget = item.parentWidget()
            # 在 scroll_area 的内容坐标系中计算目标位置
            try:
                # item 在 content_widget 中的相对位置
                pos_in_content = item.mapTo(self.content_widget, item.rect().topLeft())
            except Exception:
                pos_in_content = item.pos()
            # 滚动到视频项，让其出现在视口中央
            self.scroll_area.verticalScrollBar().setValue(
                max(0, pos_in_content.y() - vp.height() // 3)
            )
            self.scroll_area.horizontalScrollBar().setValue(
                max(0, pos_in_content.x() - vp.width() // 3)
            )
            # 延迟刷新缩略图
            try:
                self._pending_refresh_timer.start()
            except Exception:
                pass
        except Exception as e:
            print(f"[VideoGridWidget] scroll_to_video 异常: {e}")

    def find_main_window(self):
        from PyQt5.QtWidgets import QMainWindow
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, QMainWindow):
                return parent
            parent = parent.parent()
        return None

    def on_thumbnail_requested(self, video_path, video_relative_path, video_item, rotation_deg=0):
        """请求生成缩略图 — 查找主窗口的 thumbnail_manager 并入队（含旋转角度）"""
        main_window = self.find_main_window()
        if main_window and hasattr(main_window, 'thumbnail_manager') \
                and main_window.thumbnail_manager:
            main_window.thumbnail_manager.enqueue(video_path, video_relative_path, rotation_deg)

    def on_thumbnail_generated(self, video_relative_path):
        if video_relative_path in self.video_items:
            self.video_items[video_relative_path].update_from_cache()

    def add_video(self, video_path, video_relative_path, cache_manager,
                  cache_manager_resolver=None):
        video_item = VideoItem(
            video_path, video_relative_path, cache_manager,
            on_thumbnail_ready=self.on_thumbnail_requested,
            on_favorite_changed=self.on_favorite_changed_callback,
            on_double_clicked=self.on_double_clicked_callback,
            cache_manager_resolver=cache_manager_resolver,
        )
        self.video_items[video_relative_path] = video_item
        self.flow_layout.addWidget(video_item)
        # 新增项后延迟刷新视口（布局完成后再决定是否加载缩略图）
        self._pending_refresh_timer.start()
