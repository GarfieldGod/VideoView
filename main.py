"""
视频收藏集管理器 - 类似Steam风格
"""
import sys
import os
import threading
import json
import hashlib
from queue import Queue
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QListView, QLabel, QScrollArea, QFileDialog,
    QPushButton, QFrame, QGraphicsDropShadowEffect, QProgressDialog, QGridLayout,
    QTabWidget, QStackedWidget
)
from PyQt5.QtCore import Qt, QRect, QSize, QPoint, pyqtSignal, QThread, QTimer, QByteArray, QBuffer
from PyQt5.QtGui import QPixmap, QImage, QColor, QPalette, QBrush, QIcon, QPainter
from PyQt5.QtWidgets import QLayout


def path_hash(path):
    """计算路径的确定性哈希值（使用MD5）"""
    return hashlib.md5(path.encode('utf-8')).hexdigest()[:16]


# 缓存管理器 - 管理 JSON 记录和缩略图文件
class CacheManager:
    """基于 JSON 的缓存管理器"""
    def __init__(self, cache_dir, root_folder):
        self.cache_dir = cache_dir
        self.root_folder = root_folder.replace('\\', '/')
        self.manifest_path = os.path.join(cache_dir, 'cache_manifest.json')
        self.favorites_path = os.path.join(cache_dir, 'favorites.json')
        self.manifest = self._load_manifest()
        self.favorites = self._load_favorites()

    def _load_manifest(self):
        """加载缓存清单"""
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data
            except Exception as e:
                print(f"[CacheManager] 加载缓存清单失败: {e}")
        return {}

    def _save_manifest(self):
        """保存缓存清单"""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self.manifest_path, 'w', encoding='utf-8') as f:
                json.dump(self.manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CacheManager] 保存缓存清单失败: {e}")

    def _load_favorites(self):
        """加载收藏列表"""
        if os.path.exists(self.favorites_path):
            try:
                with open(self.favorites_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 数据结构: {"collections": {"默认收藏夹": ["rel_path1", "rel_path2"]}}
                    collections = data.get('collections', {})
                    if '默认收藏夹' not in collections:
                        collections['默认收藏夹'] = []
                    data['collections'] = collections
                    print(f"[CacheManager] 加载收藏列表: {len(collections.get('默认收藏夹', []))} 个视频")
                    return data
            except Exception as e:
                print(f"[CacheManager] 加载收藏列表失败: {e}")
        return {'collections': {'默认收藏夹': []}}

    def save_favorites(self):
        """保存收藏列表"""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self.favorites_path, 'w', encoding='utf-8') as f:
                json.dump(self.favorites, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CacheManager] 保存收藏列表失败: {e}")

    def get_cache_path(self, video_relative_path):
        """根据视频相对路径获取缓存文件路径"""
        normalized = video_relative_path.replace('\\', '/')
        cache_key = path_hash(normalized)
        return os.path.join(self.cache_dir, f"{cache_key}.jpg")

    def cache_exists(self, video_relative_path):
        """检查缓存是否存在"""
        cache_path = self.get_cache_path(video_relative_path)
        return os.path.exists(cache_path), cache_path

    def add_cache(self, video_relative_path, cache_path):
        """添加缓存记录"""
        cache_key = path_hash(video_relative_path)
        self.manifest[video_relative_path] = cache_key
        self._save_manifest()

    def is_favorite(self, video_relative_path):
        """检查视频是否在默认收藏夹中"""
        normalized = video_relative_path.replace('\\', '/')
        fav_list = self.favorites.get('collections', {}).get('默认收藏夹', [])
        return normalized in fav_list

    def toggle_favorite(self, video_relative_path):
        """切换收藏状态，返回新状态 (True=已收藏)"""
        normalized = video_relative_path.replace('\\', '/')
        fav_list = self.favorites.get('collections', {}).setdefault('默认收藏夹', [])
        if normalized in fav_list:
            fav_list.remove(normalized)
            self.save_favorites()
            return False
        else:
            fav_list.append(normalized)
            self.save_favorites()
            return True

    def get_favorite_videos(self):
        """获取默认收藏夹中所有视频的绝对路径列表"""
        fav_list = self.favorites.get('collections', {}).get('默认收藏夹', [])
        result = []
        for rel_path in fav_list:
            abs_path = os.path.join(self.root_folder, rel_path)
            if os.path.exists(abs_path):
                result.append(abs_path)
        return result

    def get_favorite_count(self):
        """获取默认收藏夹视频数量"""
        fav_list = self.favorites.get('collections', {}).get('默认收藏夹', [])
        # 只统计实际存在的视频
        return len([f for f in fav_list if os.path.exists(os.path.join(self.root_folder, f))])


class ThumbnailManager(QThread):
    """缩略图生成线程池管理器"""
    finished = pyqtSignal(str)  # video_relative_path - 生成完成后通知

    def __init__(self, cache_manager, max_workers=2):
        super().__init__()
        self.cache_manager = cache_manager
        self.max_workers = max_workers
        self.queue = Queue()
        self.active_count = 0
        self.running = True
        self.lock = threading.Lock()
        self.pending = set()

    def enqueue(self, video_path, video_relative_path):
        """入队请求"""
        with self.lock:
            if video_path not in self.pending:
                self.pending.add(video_path)
                self.queue.put((video_path, video_relative_path))

    def process_pending(self):
        """处理队列中的请求 - 在线程中执行"""
        while self.running:
            with self.lock:
                if self.active_count >= self.max_workers or self.queue.empty():
                    return
                video_path, video_relative_path = self.queue.get()
                self.active_count += 1

            try:
                target_width = 560
                target_height = int(target_width * 16 / 9)
                cache_path = self.cache_manager.get_cache_path(video_relative_path)

                if not os.path.exists(cache_path):
                    generate_video_thumbnail_file(video_path, target_width, target_height, cache_path)
                    self.cache_manager.add_cache(video_relative_path, cache_path)

                self.finished.emit(video_relative_path)
            except Exception as e:
                print(f"生成缩略图失败 {video_path}: {e}")
            finally:
                with self.lock:
                    self.active_count -= 1
                    if video_path in self.pending:
                        self.pending.discard(video_path)

    def run(self):
        """线程主循环"""
        while self.running:
            self.process_pending()
            threading.Event().wait(0.05)

    def stop(self):
        self.running = False


def pixmap_to_bytes(pixmap):
    """将QPixmap转换为字节数据"""
    if pixmap.isNull():
        return b''
    img = pixmap.toImage()
    ba = QByteArray()
    buffer = QBuffer(ba)
    buffer.open(QBuffer.WriteOnly)
    img.save(buffer, 'JPG', 85)
    return ba.data()


def bytes_to_pixmap(data):
    """将字节数据转换为QPixmap"""
    if not data:
        return QPixmap()
    img = QImage.fromData(data)
    if img.isNull():
        return QPixmap()
    return QPixmap.fromImage(img)


class FlowLayout(QLayout):
    """流式布局，从左到右排列，自动换行"""
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
        height = self._do_layout(QRect(0, 0, width, 0), test_only=True)
        return height

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


# 视频文件扩展名
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg'}

# 缩略图大小 (收藏夹列表不变, 视频预览2倍)
THUMBNAIL_SIZE_COLLECTION = (200, int(200 * 16 / 9))  # 收藏夹列表缩略图 (9:16)
THUMBNAIL_SIZE_VIDEO = (560, int(560 * 16 / 9))  # 视频预览缩略图 (9:16) - 2倍大小


def get_video_extensions():
    """获取支持的视频扩展名集合"""
    return VIDEO_EXTENSIONS.copy()


def is_video_file(filename):
    """检查文件是否是视频文件"""
    _, ext = os.path.splitext(filename.lower())
    return ext in VIDEO_EXTENSIONS


def scan_folder_for_videos(folder_path):
    """扫描文件夹，返回所有视频文件路径"""
    videos = []
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if is_video_file(file):
                videos.append(os.path.join(root, file))
    return videos


def generate_video_thumbnail_file(video_path, target_width, target_height, cache_path):
    """生成视频缩略图并保存到缓存文件 - 仅使用cv2

    按9:16比例从视频中间裁剪
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception("无法打开视频")

    video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        raise Exception("视频帧数无效")

    frame_idx = total_frames // 2
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise Exception("无法读取视频帧")

    # 9:16 比例裁剪
    video_aspect = video_width / video_height
    target_aspect = 9 / 16

    if abs(video_aspect - target_aspect) < 0.01:
        frame = cv2.resize(frame, (target_width, target_height))
    elif video_aspect > target_aspect:
        crop_width = int(video_height * 9 / 16)
        crop_x = (video_width - crop_width) // 2
        frame = frame[:, crop_x:crop_x + crop_width]
        frame = cv2.resize(frame, (target_width, target_height))
    else:
        crop_height = int(video_width * 16 / 9)
        crop_y = (video_height - crop_height) // 2
        frame = frame[crop_y:crop_y + crop_height, :]
        frame = cv2.resize(frame, (target_width, target_height))

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    cv2.imwrite(cache_path, frame)


def generate_video_thumbnail(video_path, target_width, target_height=None, cache_path=None):
    """生成视频缩略图，按9:16比例从视频中间裁剪"""
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise Exception("无法打开视频")

    video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        raise Exception("视频帧数无效")

    frame_idx = total_frames // 2
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise Exception("无法读取视频帧")

    if target_height is None:
        target_height = int(target_width * 16 / 9)

    if abs(video_width / video_height - 9 / 16) < 0.01:
        frame = cv2.resize(frame, (target_width, target_height))
    else:
        video_aspect = video_width / video_height
        target_aspect = 9 / 16

        if video_aspect > target_aspect:
            crop_width = int(video_height * 9 / 16)
            crop_x = (video_width - crop_width) // 2
            frame = frame[:, crop_x:crop_x + crop_width]
        else:
            crop_height = int(video_width * 16 / 9)
            crop_y = (video_height - crop_height) // 2
            frame = frame[crop_y:crop_y + crop_height, :]

        frame = cv2.resize(frame, (target_width, target_height))

    if cache_path:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        cv2.imwrite(cache_path, frame)

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = QImage(frame_rgb.data, target_width, target_height, target_width * 3, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image)


class CollectionItem(QWidget):
    """收藏夹项组件"""
    def __init__(self, name, thumbnail_path, video_count, parent=None):
        super().__init__(parent)
        self.name = name
        self.thumbnail_path = thumbnail_path
        self.video_count = video_count

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # 缩略图标签
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(*THUMBNAIL_SIZE_COLLECTION)
        self.thumbnail_label.setStyleSheet("""
            QLabel {
                background-color: #2a2a44;
                border-radius: 6px;
                border: 1px solid #4a4a6a;
                font-size: 36px;
                color: #888;
            }
        """)
        self.thumbnail_label.setAlignment(Qt.AlignCenter)

        # 加载缩略图
        if self.thumbnail_path and os.path.exists(self.thumbnail_path):
            pixmap = QPixmap(self.thumbnail_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    THUMBNAIL_SIZE_COLLECTION[0], THUMBNAIL_SIZE_COLLECTION[1],
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                result = QPixmap(THUMBNAIL_SIZE_COLLECTION[0], THUMBNAIL_SIZE_COLLECTION[1])
                result.fill(QColor('#2a2a44'))
                painter = QPainter(result)
                x = (THUMBNAIL_SIZE_COLLECTION[0] - scaled.width()) // 2
                y = (THUMBNAIL_SIZE_COLLECTION[1] - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
                painter.end()
                self.thumbnail_label.setPixmap(result)
        else:
            self.thumbnail_label.setText("📁")

        # 名称标签
        self.name_label = QLabel(self.name)
        self.name_label.setStyleSheet("""
            QLabel {
                color: #fff;
                font-size: 20px;
                font-weight: bold;
            }
        """)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setFixedHeight(26)

        # 视频数量标签
        self.count_label = QLabel(f"{self.video_count} 个视频")
        self.count_label.setStyleSheet("""
            QLabel {
                color: #aaa;
                font-size: 16px;
            }
        """)
        self.count_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.thumbnail_label)
        layout.addWidget(self.name_label)
        layout.addWidget(self.count_label)

        self.setFixedWidth(280)


class VideoItem(QWidget):
    """视频项组件 - 异步加载缩略图 + 收藏按钮"""
    def __init__(self, video_path, video_relative_path, cache_manager, parent=None, on_thumbnail_ready=None, on_favorite_changed=None):
        super().__init__(parent)
        self.video_path = video_path
        self.video_relative_path = video_relative_path.replace('\\', '/')
        self.cache_manager = cache_manager
        self.on_thumbnail_ready = on_thumbnail_ready
        self.on_favorite_changed = on_favorite_changed

        self.thumbnail_width = THUMBNAIL_SIZE_VIDEO[0]
        self.thumbnail_height = THUMBNAIL_SIZE_VIDEO[1]

        self.setup_ui()
        self.load_thumbnail()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # 缩略图容器（用于放置红心按钮）
        thumb_container = QWidget()
        thumb_container.setFixedSize(self.thumbnail_width, self.thumbnail_height)
        thumb_layout = QVBoxLayout(thumb_container)
        thumb_layout.setContentsMargins(0, 0, 0, 0)

        self.thumbnail_label = QLabel(thumb_container)
        self.thumbnail_label.setFixedSize(self.thumbnail_width, self.thumbnail_height)
        self.thumbnail_label.setStyleSheet("""
            QLabel {
                background-color: #2a2a44;
                border-radius: 8px;
                border: 1px solid #4a4a6a;
                color: #888;
                font-size: 24px;
            }
        """)
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setText("🎬")

        # 收藏按钮（右上角）
        self.fav_btn = QPushButton(thumb_container)
        self.fav_btn.setFixedSize(52, 52)
        self.fav_btn.move(self.thumbnail_width - 58, 6)
        self.fav_btn.setCursor(Qt.PointingHandCursor)
        self.fav_btn.setFlat(True)
        self.fav_btn.clicked.connect(self._on_fav_clicked)
        self._update_fav_button_style()

        # 名称标签
        self.name_label = QLabel(os.path.splitext(os.path.basename(self.video_path))[0])
        self.name_label.setStyleSheet("""
            QLabel {
                color: #fff;
                font-size: 18px;
            }
        """)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setFixedHeight(28)
        self.name_label.setWordWrap(True)

        layout.addWidget(thumb_container)
        layout.addWidget(self.name_label)

        self.setFixedWidth(THUMBNAIL_SIZE_VIDEO[0] + 20)

    def _update_fav_button_style(self):
        """更新收藏按钮样式"""
        is_fav = False
        if self.cache_manager:
            is_fav = self.cache_manager.is_favorite(self.video_relative_path)

        if is_fav:
            self.fav_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(230, 50, 50, 0.92);
                    border: 2px solid rgba(255, 80, 80, 0.9);
                    border-radius: 26px;
                    color: white;
                    font-size: 28px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 70, 70, 0.95);
                }
                QPushButton:pressed {
                    background-color: rgba(200, 40, 40, 0.95);
                }
            """)
            self.fav_btn.setText("♥")
        else:
            self.fav_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(40, 40, 60, 0.7);
                    border: 2px solid rgba(100, 100, 120, 0.6);
                    border-radius: 26px;
                    color: rgba(180, 180, 200, 0.6);
                    font-size: 28px;
                }
                QPushButton:hover {
                    background-color: rgba(60, 60, 80, 0.8);
                    border: 2px solid rgba(150, 150, 170, 0.8);
                    color: rgba(220, 220, 240, 0.9);
                }
                QPushButton:pressed {
                    background-color: rgba(230, 50, 50, 0.92);
                    border: 2px solid rgba(255, 80, 80, 0.9);
                    color: white;
                }
            """)
            self.fav_btn.setText("♥")

    def _on_fav_clicked(self):
        """点击收藏按钮"""
        if not self.cache_manager:
            return
        new_state = self.cache_manager.toggle_favorite(self.video_relative_path)
        self._update_fav_button_style()
        if self.on_favorite_changed:
            self.on_favorite_changed(self.video_relative_path, new_state)

    def sizeHint(self):
        return QSize(THUMBNAIL_SIZE_VIDEO[0] + 20, self.thumbnail_height + 50)

    def load_thumbnail(self):
        """加载缩略图 - 有缓存就显示，没有就请求后台生成"""
        if not self.cache_manager:
            return

        exists, cache_path = self.cache_manager.cache_exists(self.video_relative_path)
        if exists:
            pixmap = QPixmap(cache_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.thumbnail_width, self.thumbnail_height,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.thumbnail_label.setPixmap(scaled)
                return

        if self.on_thumbnail_ready:
            self.on_thumbnail_ready(self.video_path, self.video_relative_path, self)

    def update_from_cache(self):
        """从缓存文件重新加载缩略图 - 后台生成完成后调用"""
        if not self.cache_manager:
            return
        exists, cache_path = self.cache_manager.cache_exists(self.video_relative_path)
        if exists:
            pixmap = QPixmap(cache_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.thumbnail_width, self.thumbnail_height,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.thumbnail_label.setPixmap(scaled)


class CollectionListWidget(QListWidget):
    """收藏夹列表组件"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        self.setStyleSheet("""
            QListWidget {
                background-color: #1a1a2e;
                border: none;
                padding: 10px;
            }
            QListWidget::item {
                background-color: transparent;
                border-radius: 8px;
                padding: 8px;
            }
            QListWidget::item:selected {
                background-color: #2d2d44;
                border: 1px solid #4a4a6a;
            }
            QListWidget::item:hover {
                background-color: #252540;
            }
        """)
        self.setSpacing(8)
        self.setFlow(QListView.TopToBottom)
        self.setResizeMode(QListWidget.Fixed)


class VideoGridWidget(QWidget):
    """视频网格组件 - 流式布局"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_items = {}
        self.on_favorite_changed_callback = None
        self.setup_ui()

    def set_on_favorite_changed(self, callback):
        """设置收藏状态变更回调"""
        self.on_favorite_changed_callback = callback

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(16)

        self.title_label = QLabel("全部视频")
        self.title_label.setStyleSheet("""
            QLabel {
                color: #fff;
                font-size: 34px;
                font-weight: bold;
                padding: 10px 0;
            }
        """)

        self.scroll_area = QScrollArea()
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #16162a;
                border: none;
            }
            QScrollBar:vertical {
                background-color: #2a2a4a;
                width: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background-color: #4a4a6a;
                border-radius: 6px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #5a5a7a;
            }
        """)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background-color: #16162a;")
        self.flow_layout = FlowLayout(self.content_widget, 10, 16)

        self.scroll_area.setWidget(self.content_widget)
        self.main_layout.addWidget(self.title_label)
        self.main_layout.addWidget(self.scroll_area)

    def set_title(self, title):
        """设置标题"""
        self.title_label.setText(title)

    def clear_videos(self):
        """清空视频列表"""
        self.video_items.clear()
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def find_main_window(self):
        """向上查找主窗口"""
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, QMainWindow):
                return parent
            parent = parent.parent()
        return None

    def on_thumbnail_requested(self, video_path, video_relative_path, video_item):
        """请求生成缩略图 - 查找主窗口的 thumbnail_manager"""
        main_window = self.find_main_window()
        if main_window and hasattr(main_window, 'thumbnail_manager') and main_window.thumbnail_manager:
            main_window.thumbnail_manager.enqueue(video_path, video_relative_path)

    def on_thumbnail_generated(self, video_relative_path):
        """缩略图生成完成 - 从缓存加载"""
        if video_relative_path in self.video_items:
            self.video_items[video_relative_path].update_from_cache()

    def add_video(self, video_path, video_relative_path, cache_manager):
        """添加视频项"""
        video_item = VideoItem(
            video_path, video_relative_path, cache_manager,
            on_thumbnail_ready=self.on_thumbnail_requested,
            on_favorite_changed=self.on_favorite_changed_callback
        )
        self.video_items[video_relative_path] = video_item
        self.flow_layout.addWidget(video_item)


class MainWindow(QMainWindow):
    """主窗口"""
    def __init__(self):
        super().__init__()
        self.root_folder = None
        self.collections = []  # 根目录子文件夹收藏夹列表
        self.thumbnail_manager = None
        self.cache_manager = None
        self.current_tab = 0  # 0 = 根目录, 1 = 收藏夹

        self.setup_ui()
        self.apply_styles()

    def setup_ui(self):
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

        # 左侧面板 - 标签页
        self.left_panel = QFrame()
        self.left_panel.setFixedWidth(320)
        self.left_panel.setFont(font)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)

        # 顶部按钮
        self.open_btn = QPushButton("📂 打开文件夹")
        self.open_btn.setFixedHeight(40)
        self.open_btn.setFont(font)
        self.open_btn.clicked.connect(self.open_folder)

        # 两个并排切换按钮：根目录 / 最爱
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

        # 列表切换容器
        self.list_stack = QStackedWidget()
        self.list_stack.setFont(font)
        self.list_stack.setStyleSheet("background-color: transparent;")

        # 根目录列表
        self.collection_list = CollectionListWidget()
        self.collection_list.itemClicked.connect(self.on_collection_clicked)

        # 最爱列表 - 虚拟收藏夹
        self.fav_collection_list = CollectionListWidget()
        self.fav_collection_list.itemClicked.connect(self.on_fav_collection_clicked)

        self.list_stack.addWidget(self.collection_list)
        self.list_stack.addWidget(self.fav_collection_list)

        left_layout.addWidget(self.open_btn)
        left_layout.addLayout(switch_row)
        left_layout.addWidget(self.list_stack)

        # 右侧面板 - 视频预览
        self.right_panel = VideoGridWidget(self)
        self.right_panel.setFont(font)
        self.right_panel.set_on_favorite_changed(self.on_favorite_changed)

        main_layout.addWidget(self.left_panel)
        main_layout.addWidget(self.right_panel)

    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1a1a2e;
            }
            QPushButton {
                background-color: #4a4a6a;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 22px;
                font-weight: bold;
                padding: 10px 20px;
            }
            QPushButton:hover {
                background-color: #5a5a7a;
            }
            QPushButton:pressed {
                background-color: #3a3a5a;
            }
        """)

        self.left_panel.setStyleSheet("""
            QFrame {
                background-color: #1a1a2e;
                border-right: 1px solid #2a2a4a;
            }
        """)

    def init_thumbnail_manager(self):
        """初始化缩略图管理器"""
        if self.thumbnail_manager:
            self.thumbnail_manager.stop()
            self.thumbnail_manager.wait()

        cache_dir = os.path.join(self.root_folder, '.videoview', 'cache')
        self.cache_manager = CacheManager(cache_dir, self.root_folder)

        self.thumbnail_manager = ThumbnailManager(self.cache_manager, max_workers=2)
        self.thumbnail_manager.finished.connect(self.on_thumbnail_ready)
        self.thumbnail_manager.start()

    def on_thumbnail_ready(self, video_relative_path):
        """缩略图生成完成 - 通知右侧面板加载"""
        self.right_panel.on_thumbnail_generated(video_relative_path)

    def on_favorite_changed(self, video_relative_path, new_state):
        """收藏状态变更 - 更新左侧收藏夹列表显示"""
        # 刷新左侧虚拟收藏夹列表的显示数量
        if self.cache_manager:
            self.refresh_fav_collection_list()
        # 如果当前在收藏夹标签页且显示的是默认收藏夹，刷新右侧显示
        if self.current_tab == 1:
            # 重新显示默认收藏夹内容
            self.show_default_favorites()

    def open_folder(self):
        """打开文件夹对话框"""
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择视频文件夹",
            "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )

        if folder:
            self.root_folder = folder.replace('\\', '/')
            self.init_thumbnail_manager()
            self.scan_collections(folder)

    def scan_collections(self, folder_path):
        """扫描收藏夹"""
        self.collections.clear()
        self.collection_list.clear()

        try:
            subfolders = sorted([d for d in os.listdir(folder_path)
                                if os.path.isdir(os.path.join(folder_path, d))])
        except Exception as e:
            print(f"扫描文件夹失败: {e}")
            return

        # 创建进度对话框
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
                    'videos': videos
                })

            progress.setValue(i + 1)
            progress.setLabelText(f"扫描: {subfolder}")

        progress.close()

        # 填充根目录标签列表
        self.populate_collection_list()

        # 填充收藏夹标签列表
        self.populate_fav_collection_list()

        # 默认选中第一个
        if self.collections:
            self.collection_list.setCurrentRow(0)
            self.on_collection_clicked(self.collection_list.item(0))

    def populate_collection_list(self):
        """填充根目录收藏夹列表"""
        self.collection_list.clear()

        for collection in self.collections:
            thumbnail = None
            if collection['videos'] and self.cache_manager:
                try:
                    video_path = collection['videos'][0]
                    video_rel_path = os.path.relpath(video_path, self.root_folder).replace('\\', '/')
                    exists, cache_path = self.cache_manager.cache_exists(video_rel_path)
                    if exists:
                        thumbnail = cache_path
                except:
                    pass

            item = QListWidgetItem(self.collection_list)
            item.setSizeHint(QSize(280, 420))

            widget = CollectionItem(
                collection['name'],
                thumbnail,
                len(collection['videos'])
            )

            self.collection_list.setItemWidget(item, widget)

    def refresh_fav_collection_list(self):
        """刷新收藏夹标签列表（保持当前选择）"""
        current_row = self.fav_collection_list.currentRow()
        self.populate_fav_collection_list()
        if self.fav_collection_list.count() > current_row >= 0:
            self.fav_collection_list.setCurrentRow(current_row)

    def populate_fav_collection_list(self):
        """填充虚拟收藏夹列表 - 默认收藏夹"""
        self.fav_collection_list.clear()

        if not self.cache_manager:
            return

        # 默认收藏夹 - 使用收藏的第一个视频作为缩略图
        fav_videos = self.cache_manager.get_favorite_videos()
        thumbnail = None
        if fav_videos:
            try:
                first_video = fav_videos[0]
                rel_path = os.path.relpath(first_video, self.root_folder).replace('\\', '/')
                exists, cache_path = self.cache_manager.cache_exists(rel_path)
                if exists:
                    thumbnail = cache_path
            except:
                pass

        item = QListWidgetItem(self.fav_collection_list)
        item.setSizeHint(QSize(280, 420))

        widget = CollectionItem(
            "默认收藏夹",
            thumbnail,
            self.cache_manager.get_favorite_count()
        )

        self.fav_collection_list.setItemWidget(item, widget)

    def _update_switch_buttons(self, mode):
        """更新两个切换按钮的样式
        mode: 0 = 根目录, 1 = 最爱
        """
        active_style = """
            QPushButton {
                background-color: #3a3a5a;
                color: #fff;
                border: 2px solid #5a5a7a;
                border-radius: 8px;
                font-size: 18px;
                font-weight: bold;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #4a4a6a;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: #252540;
                color: #aaa;
                border: 2px solid #2d2d44;
                border-radius: 8px;
                font-size: 18px;
                font-weight: bold;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #2d2d44;
                color: #ccc;
            }
        """
        if mode == 0:
            self.btn_root.setStyleSheet(active_style)
            self.btn_fav.setStyleSheet(inactive_style)
            self.btn_root.setChecked(True)
            self.btn_fav.setChecked(False)
        else:
            self.btn_root.setStyleSheet(inactive_style)
            self.btn_fav.setStyleSheet(active_style)
            self.btn_root.setChecked(False)
            self.btn_fav.setChecked(True)

    def switch_list_mode(self, mode):
        """切换列表模式：0=根目录，1=最爱"""
        self.current_tab = mode
        self._update_switch_buttons(mode)
        self.list_stack.setCurrentIndex(mode)

        # 切换时自动选中第一个项并显示
        if mode == 0 and self.collections:
            if self.collection_list.count() > 0:
                self.collection_list.setCurrentRow(0)
                self.on_collection_clicked(self.collection_list.item(0))
        elif mode == 1:
            if self.fav_collection_list.count() > 0:
                self.fav_collection_list.setCurrentRow(0)
                self.on_fav_collection_clicked(self.fav_collection_list.item(0))

    def on_collection_clicked(self, item):
        """根目录收藏夹点击事件"""
        self.current_tab = 0
        index = self.collection_list.row(item)
        if 0 <= index < len(self.collections):
            collection = self.collections[index]
            self.show_videos(collection)

    def on_fav_collection_clicked(self, item):
        """收藏夹标签点击事件"""
        self.current_tab = 1
        index = self.fav_collection_list.row(item)
        if index == 0:
            # 默认收藏夹
            self.show_default_favorites()

    def show_videos(self, collection):
        """显示普通收藏夹的视频列表"""
        self.current_tab = 0
        self.right_panel.clear_videos()
        self.right_panel.set_title(f"📁 {collection['name']} ({len(collection['videos'])} 个视频)")

        for video_path in collection['videos']:
            video_relative_path = os.path.relpath(video_path, self.root_folder).replace('\\', '/')
            self.right_panel.add_video(video_path, video_relative_path, self.cache_manager)

    def show_default_favorites(self):
        """显示默认收藏夹的视频列表"""
        self.current_tab = 1
        self.right_panel.clear_videos()

        if not self.cache_manager:
            self.right_panel.set_title("⭐ 默认收藏夹")
            return

        fav_videos = self.cache_manager.get_favorite_videos()
        self.right_panel.set_title(f"⭐ 默认收藏夹 ({len(fav_videos)} 个视频)")

        for video_path in fav_videos:
            video_relative_path = os.path.relpath(video_path, self.root_folder).replace('\\', '/')
            self.right_panel.add_video(video_path, video_relative_path, self.cache_manager)

    def closeEvent(self, event):
        """关闭窗口时停止线程"""
        if self.thumbnail_manager:
            self.thumbnail_manager.stop()
            self.thumbnail_manager.wait()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("视频收藏集管理器")

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
