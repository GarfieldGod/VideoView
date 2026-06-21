"""
视频收藏集管理器 - 类似Steam风格
"""
import sys
import os
import threading
import json
import hashlib
from queue import Queue
import cv2
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QListView, QLabel, QScrollArea, QFileDialog,
    QPushButton, QFrame, QGraphicsDropShadowEffect, QProgressDialog, QGridLayout,
    QTabWidget, QStackedWidget, QSlider, QStyle, QSizePolicy, QMenu, QAction
)
from PyQt5.QtCore import Qt, QRect, QSize, QPoint, pyqtSignal, pyqtSlot, QThread, QTimer, QElapsedTimer, QByteArray, QBuffer, QUrl
from PyQt5.QtGui import QPixmap, QImage, QColor, QPalette, QBrush, QIcon, QPainter, QDesktopServices
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
        self.marked_collections = self._load_marked_collections()
        self.rotations = self._load_rotations()

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

    # ===== 收藏夹标记功能 =====
    def _load_marked_collections(self):
        """加载已标记的收藏夹列表"""
        marked_path = os.path.join(os.path.dirname(self.cache_dir), 'marked_collections.json')
        self.marked_path = marked_path
        if os.path.exists(marked_path):
            try:
                with open(marked_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return set(data.get('marked', []))
            except Exception as e:
                print(f"[CacheManager] 加载标记列表失败: {e}")
        return set()

    def save_marked_collections(self):
        """保存已标记的收藏夹列表"""
        try:
            os.makedirs(os.path.dirname(self.marked_path), exist_ok=True)
            with open(self.marked_path, 'w', encoding='utf-8') as f:
                json.dump({'marked': sorted(list(self.marked_collections))}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CacheManager] 保存标记列表失败: {e}")

    def is_marked(self, collection_name):
        """检查收藏夹是否被标记"""
        return collection_name in self.marked_collections

    def toggle_mark(self, collection_name):
        """切换收藏夹标记状态，返回新状态"""
        if collection_name in self.marked_collections:
            self.marked_collections.discard(collection_name)
            self.save_marked_collections()
            return False
        else:
            self.marked_collections.add(collection_name)
            self.save_marked_collections()
            return True

    def set_mark(self, collection_name, is_marked):
        """直接设置收藏夹标记状态"""
        if is_marked:
            self.marked_collections.add(collection_name)
        else:
            self.marked_collections.discard(collection_name)
        self.save_marked_collections()

    # ===== 视频旋转功能 =====
    def _load_rotations(self):
        """加载视频旋转记录：{video_relative_path: rotation_deg}，rotation_deg 为 0/90/180/270"""
        rotation_path = os.path.join(os.path.dirname(self.cache_dir), 'rotations.json')
        self.rotation_path = rotation_path
        if os.path.exists(rotation_path):
            try:
                with open(rotation_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    rotations = data.get('rotations', {})
                    if isinstance(rotations, dict):
                        # 统一清理：确保值为 0/90/180/270
                        return {str(k).replace('\\', '/'): int(v) % 360 for k, v in rotations.items() if int(v) % 360 in (0, 90, 180, 270)}
                    return {}
            except Exception as e:
                print(f"[CacheManager] 加载旋转列表失败: {e}")
        return {}

    def save_rotations(self):
        """保存视频旋转记录"""
        try:
            os.makedirs(os.path.dirname(self.rotation_path), exist_ok=True)
            with open(self.rotation_path, 'w', encoding='utf-8') as f:
                json.dump({'rotations': self.rotations}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CacheManager] 保存旋转列表失败: {e}")

    def get_rotation(self, video_relative_path):
        """获取视频的旋转角度（0/90/180/270）"""
        normalized = video_relative_path.replace('\\', '/')
        return int(self.rotations.get(normalized, 0))

    def set_rotation(self, video_relative_path, rotation_deg):
        """设置视频旋转角度（自动规范化到 0-270，步长 90）"""
        normalized = video_relative_path.replace('\\', '/')
        deg = int(rotation_deg) % 360
        if deg not in (0, 90, 180, 270):
            deg = (deg // 90) * 90 % 360
        if deg == 0:
            if normalized in self.rotations:
                del self.rotations[normalized]
                self.save_rotations()
        else:
            self.rotations[normalized] = deg
            self.save_rotations()

    def rotate_left(self, video_relative_path):
        """视频画面左旋 90 度，返回新角度"""
        current = self.get_rotation(video_relative_path)
        new_deg = (current - 90) % 360
        self.set_rotation(video_relative_path, new_deg)
        return new_deg

    def rotate_right(self, video_relative_path):
        """视频画面右旋 90 度，返回新角度"""
        current = self.get_rotation(video_relative_path)
        new_deg = (current + 90) % 360
        self.set_rotation(video_relative_path, new_deg)
        return new_deg


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

# 应用配置（保存上次打开的根目录）
APP_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app_config.json')


def get_app_config():
    """读取应用配置"""
    if os.path.exists(APP_CONFIG_PATH):
        try:
            with open(APP_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"读取应用配置失败: {e}")
    return {}


def save_app_config(config):
    """保存应用配置"""
    try:
        with open(APP_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存应用配置失败: {e}")


def get_last_root_folder():
    """获取上次打开的根目录"""
    return get_app_config().get('last_root_folder', '')


def set_last_root_folder(folder):
    """保存上次打开的根目录"""
    config = get_app_config()
    config['last_root_folder'] = folder
    save_app_config(config)

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
    """收藏夹项组件 - 带右上角标记按钮"""
    def __init__(self, name, thumbnail_path, video_count, is_marked=False, parent=None, on_mark_changed=None):
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

        # 缩略图容器（用于放置标记按钮）
        thumb_container = QWidget()
        thumb_container.setFixedSize(*THUMBNAIL_SIZE_COLLECTION)
        thumb_layout = QVBoxLayout(thumb_container)
        thumb_layout.setContentsMargins(0, 0, 0, 0)

        # 缩略图标签
        self.thumbnail_label = QLabel(thumb_container)
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

        # 标记按钮（右上角）——仅在"显示标记"模式下使用；此处总是渲染
        self.mark_btn = QPushButton(thumb_container)
        self.mark_btn.setFixedSize(40, 40)
        self.mark_btn.move(THUMBNAIL_SIZE_COLLECTION[0] - 46, 6)
        self.mark_btn.setCursor(Qt.PointingHandCursor)
        self.mark_btn.setFlat(True)
        self.mark_btn.clicked.connect(self._on_mark_clicked)
        self._update_mark_button_style()

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

        layout.addWidget(thumb_container)
        layout.addWidget(self.name_label)
        layout.addWidget(self.count_label)

        self.setFixedWidth(280)

    def _update_mark_button_style(self):
        """更新标记按钮样式：未点击为透明灰，点击后为 finished（绿/✓）"""
        if self.is_marked:
            self.mark_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(60, 160, 90, 0.92);
                    border: 2px solid rgba(90, 200, 120, 0.9);
                    border-radius: 20px;
                    color: white;
                    font-size: 22px;
                }
                QPushButton:hover {
                    background-color: rgba(80, 180, 110, 0.95);
                }
                QPushButton:pressed {
                    background-color: rgba(50, 140, 80, 0.95);
                }
            """)
            self.mark_btn.setText("✓")
        else:
            self.mark_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(40, 40, 60, 0.6);
                    border: 2px solid rgba(100, 100, 120, 0.5);
                    border-radius: 20px;
                    color: rgba(160, 160, 180, 0.55);
                    font-size: 22px;
                }
                QPushButton:hover {
                    background-color: rgba(60, 60, 80, 0.75);
                    border: 2px solid rgba(150, 150, 170, 0.7);
                    color: rgba(200, 200, 220, 0.85);
                }
                QPushButton:pressed {
                    background-color: rgba(60, 160, 90, 0.92);
                    border: 2px solid rgba(90, 200, 120, 0.9);
                    color: white;
                }
            """)
            self.mark_btn.setText("○")

    def _on_mark_clicked(self):
        """点击标记按钮"""
        self.is_marked = not self.is_marked
        self._update_mark_button_style()
        if self.on_mark_changed:
            self.on_mark_changed(self.name, self.is_marked)


class VideoItem(QWidget):
    """视频项组件 - 异步加载缩略图 + 收藏按钮 + 双击播放 + 右键菜单"""
    double_clicked = pyqtSignal(str)  # 发出 video_relative_path（供应用内播放器使用）

    def __init__(self, video_path, video_relative_path, cache_manager, parent=None,
                 on_thumbnail_ready=None, on_favorite_changed=None, on_double_clicked=None):
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

    def mouseDoubleClickEvent(self, event):
        """双击 VideoItem —— 应用内播放（保留上一个/下一个/旋转/收藏）"""
        event.accept()
        try:
            if self.on_double_clicked:
                self.on_double_clicked(self.video_path, self.video_relative_path)
        except Exception as e:
            print(f"[VideoItem] 双击事件出错: {e}")
            import traceback
            traceback.print_exc()

    def _open_with_system_player(self):
        """优先用 PotPlayer 打开（系统若安装了）；否则回退系统默认关联程序"""
        abs_path = os.path.abspath(self.video_path)

        # 常见的 PotPlayer / 第三方播放器安装路径
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
                # 用第三方播放器直接打开（最可靠）
                import subprocess
                print(f"[VideoItem] 用外部播放器打开: {chosen}")
                subprocess.Popen([chosen, abs_path], close_fds=True)
                return
            # 没有检测到，就用系统默认关联程序
            from PyQt5.QtCore import QUrl
            from PyQt5.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(abs_path))
        except Exception as e:
            print(f"[VideoItem] 外部播放器打开失败: {e}")
            try:
                if sys.platform.startswith('win'):
                    os.startfile(abs_path)
                elif sys.platform == 'darwin':
                    os.system(f'open "{abs_path}"')
                else:
                    os.system(f'xdg-open "{abs_path}" &')
            except Exception as e2:
                print(f"[VideoItem] 系统打开也失败: {e2}")

    def _on_context_menu(self, pos):
        """右键菜单：应用内播放（带控制/收藏/旋转）/ 以本地播放器打开"""
        try:
            menu = QMenu(self)

            action_internal = QAction("在应用内播放（上一个/下一个/收藏/旋转）", self)
            action_internal.triggered.connect(lambda: self.on_double_clicked(self.video_path, self.video_relative_path))
            menu.addAction(action_internal)

            action_external = QAction("以本地播放器打开（流畅 + 有声音）", self)
            action_external.triggered.connect(lambda: self._open_with_system_player())
            menu.addAction(action_external)

            menu.exec_(self.mapToGlobal(pos))
        except Exception as e:
            print(f"[VideoItem] 右键菜单异常: {e}")

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
        self.on_double_clicked_callback = None
        self.setup_ui()

    def set_on_favorite_changed(self, callback):
        """设置收藏状态变更回调"""
        self.on_favorite_changed_callback = callback

    def set_on_double_clicked(self, callback):
        """设置视频双击播放回调"""
        self.on_double_clicked_callback = callback

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
            on_favorite_changed=self.on_favorite_changed_callback,
            on_double_clicked=self.on_double_clicked_callback
        )
        # 不再额外连接 double_clicked signal，避免重复触发
        self.video_items[video_relative_path] = video_item
        self.flow_layout.addWidget(video_item)



import threading


import threading




class VideoPlayerWindow(QMainWindow):
    """应用内播放器：OpenCV 逐帧播放（保证能显示画面）。
    右键菜单提供"以本地播放器打开"选项（流畅+有声音）。"""
    favorite_changed = pyqtSignal(str, bool)

    def __init__(self, video_paths, start_index=0, cache_manager=None, parent=None, root_folder=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.video_paths = video_paths
        self.current_index = start_index
        self.cache_manager = cache_manager
        self.root_folder = root_folder.replace('\\', '/') if root_folder else None

        # 状态
        self._total_ms = 1
        self._current_ms = 0
        self._rotation = 0
        self._is_playing = False
        self._dragging = False

        # OpenCV 后端
        self._cv_cap = None
        self._cv_timer = None
        self._cv_label = None
        self._cv_frame_ms = 33
        self._cv_fps = 30.0
        self._cv_total_frames = 0

        self.setup_ui()
        QTimer.singleShot(100, lambda: self.load_video(self.current_index))
        self.show()

    def setup_ui(self):
        self.setWindowTitle("视频播放器")
        self.resize(1280, 820)

        central = QWidget()
        central.setStyleSheet("background-color:#000000;")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 视频显示区
        self._cv_label = QLabel()
        self._cv_label.setAlignment(Qt.AlignCenter)
        self._cv_label.setStyleSheet("background-color:#000000; color:#888; font-size:18px;")
        self._cv_label.setText("加载中...")
        self._cv_label.setMinimumSize(640, 360)
        main_layout.addWidget(self._cv_label, 1)

        # 控制栏
        ctrl = QWidget()
        ctrl.setFixedHeight(110)
        ctrl.setStyleSheet("background-color:#1a1a2e;")
        ctrl_layout = QVBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(16, 8, 16, 8)
        ctrl_layout.setSpacing(4)

        # 进度条 + 时间
        time_row = QHBoxLayout()
        self.time_current = QLabel("00:00")
        self.time_current.setStyleSheet("color:#bbb; font-size:13px;")
        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.setValue(0)
        self.progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self.progress_slider.sliderReleased.connect(self._on_slider_released)
        self.progress_slider.sliderMoved.connect(self._on_slider_moved)
        self.time_total = QLabel("00:00")
        self.time_total.setStyleSheet("color:#bbb; font-size:13px;")
        time_row.addWidget(self.time_current)
        time_row.addWidget(self.progress_slider, 1)
        time_row.addWidget(self.time_total)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_prev = self._make_button("上一个")
        self.btn_prev.clicked.connect(self.play_previous)
        self.btn_play = self._make_button("暂停")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_next = self._make_button("下一个")
        self.btn_next.clicked.connect(self.play_next)
        self.btn_rot_left = self._make_button("左旋 90°")
        self.btn_rot_left.clicked.connect(lambda: self.rotate_video(-90))
        self.btn_rot_right = self._make_button("右旋 90°")
        self.btn_rot_right.clicked.connect(lambda: self.rotate_video(90))
        self.btn_fav = self._make_button("收藏")
        self.btn_fav.clicked.connect(self.toggle_favorite)
        self.filename_label = QLabel("")
        self.filename_label.setStyleSheet("color:#ddd; font-size:13px; padding:0 8px;")
        self.filename_label.setAlignment(Qt.AlignCenter)
        btn_row.addWidget(self.btn_prev)
        btn_row.addWidget(self.btn_play)
        btn_row.addWidget(self.btn_next)
        btn_row.addWidget(self.btn_rot_left)
        btn_row.addWidget(self.btn_rot_right)
        btn_row.addWidget(self.btn_fav)
        btn_row.addWidget(self.filename_label, 1)

        ctrl_layout.addLayout(time_row)
        ctrl_layout.addLayout(btn_row)
        main_layout.addWidget(ctrl)

    def _make_button(self, text):
        btn = QPushButton(text)
        btn.setFixedHeight(38)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton{background-color:#3a3a5a;color:white;border:none;"
            "border-radius:5px;font-size:13px;padding:4px 10px;font-weight:bold;}"
            "QPushButton:hover{background-color:#5a5a7a;}"
            "QPushButton:pressed{background-color:#2a2a4a;}"
        )
        return btn

    def load_video(self, index):
        if not (0 <= index < len(self.video_paths)):
            return
        self.current_index = index
        abs_path, rel_path = self.video_paths[index]
        self.filename_label.setText(os.path.basename(abs_path))
        self._update_fav_button()

        # 读取持久化的旋转角度
        if self.cache_manager is not None:
            try:
                self._rotation = int(self.cache_manager.get_rotation(rel_path)) % 360
            except Exception:
                self._rotation = 0

        self.progress_slider.setValue(0)
        self.progress_slider.setRange(0, 1000)
        self.time_current.setText("00:00")
        self.time_total.setText("00:00")

        abs_path = os.path.abspath(abs_path)
        self._load_video_cv(abs_path)

    def _load_video_cv(self, abs_path):
        try:
            # 释放旧的
            if self._cv_timer is not None:
                self._cv_timer.stop()
            if self._cv_cap is not None:
                try:
                    self._cv_cap.release()
                except Exception:
                    pass
                self._cv_cap = None

            cap = cv2.VideoCapture(abs_path)
            if not cap.isOpened():
                cap.release()
                self._cv_label.setText("无法打开视频文件：\n" + os.path.basename(abs_path))
                return
            self._cv_cap = cap
            try:
                fps = float(cap.get(cv2.CAP_PROP_FPS))
                if not fps or fps <= 1 or fps > 240:
                    fps = 30.0
            except Exception:
                fps = 30.0
            try:
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            except Exception:
                total_frames = 0
            self._cv_fps = fps
            self._cv_total_frames = total_frames
            self._cv_frame_ms = int(max(15, 1000.0 / fps))
            total_ms = 0
            if fps > 0 and total_frames > 0:
                total_ms = int(total_frames * 1000.0 / fps)
                self._total_ms = max(1, total_ms)
                try:
                    self.progress_slider.setRange(0, self._total_ms)
                except Exception:
                    pass
                self.time_total.setText(self._format_ms(self._total_ms))

            # 先读一帧显示
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                self._show_cv_frame(frame)
                self._current_ms = 0
            self._is_playing = True
            self.btn_play.setText("暂停")
            self._cv_timer = QTimer(self)
            self._cv_timer.timeout.connect(self._on_cv_tick)
            self._cv_timer.start(self._cv_frame_ms)
        except Exception as e:
            print(f"[VideoPlayerWindow] OpenCV 加载失败：{e}")
            self._cv_label.setText("加载失败")

    def _on_cv_tick(self):
        if self._cv_cap is None or not self._is_playing:
            return
        try:
            ret, frame = self._cv_cap.read()
            if not ret or frame is None or frame.size == 0:
                # 到结尾了
                try:
                    cur = int(self._cv_cap.get(cv2.CAP_PROP_POS_FRAMES))
                    total = int(self._cv_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if total > 10 and cur >= total - 2:
                        self._cv_timer.stop()
                        QTimer.singleShot(300, self.play_next)
                        return
                except Exception:
                    pass
                for _ in range(3):
                    ret, frame = self._cv_cap.read()
                    if ret and frame is not None and frame.size > 0:
                        break
                if not ret or frame is None or frame.size == 0:
                    return
            self._show_cv_frame(frame)
            try:
                pos_ms = int(self._cv_cap.get(cv2.CAP_PROP_POS_MSEC))
                if pos_ms < 0:
                    pos_ms = 0
                self._current_ms = pos_ms
                if not self._dragging:
                    try:
                        self.progress_slider.setValue(pos_ms)
                    except Exception:
                        pass
                self.time_current.setText(self._format_ms(pos_ms))
            except Exception:
                pass
        except Exception as e:
            print(f"[VideoPlayerWindow] cv tick 异常：{e}")

    def _show_cv_frame(self, frame):
        try:
            lw = max(1, self._cv_label.width())
            lh = max(1, self._cv_label.height())
            fh, fw = frame.shape[:2]
            scale = min(lw / fw, lh / fh)
            nw = max(1, int(fw * scale))
            nh = max(1, int(fh * scale))
            if nw < fw or nh < fh:
                frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_NEAREST)
            deg = int(self._rotation) % 360
            if deg == 90:
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            elif deg == 180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            elif deg == 270:
                frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[0], rgb.shape[1]
            qimg = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888).copy()
            self._cv_label.setPixmap(QPixmap.fromImage(qimg))
        except Exception as e:
            print(f"[VideoPlayerWindow] _show_cv_frame 异常：{e}")

    def toggle_play(self):
        if self._is_playing:
            self._is_playing = False
            if self._cv_timer is not None:
                self._cv_timer.stop()
            self.btn_play.setText("播放")
        else:
            self._is_playing = True
            if self._cv_timer is not None:
                self._cv_timer.start(self._cv_frame_ms)
            self.btn_play.setText("暂停")

    def play_previous(self):
        if len(self.video_paths) == 0:
            return
        next_index = (self.current_index - 1) % len(self.video_paths)
        self.load_video(next_index)

    def play_next(self):
        if len(self.video_paths) == 0:
            return
        next_index = (self.current_index + 1) % len(self.video_paths)
        self.load_video(next_index)

    def rotate_video(self, delta_deg):
        self._rotation = (int(self._rotation) + int(delta_deg)) % 360
        if self.cache_manager is not None and 0 <= self.current_index < len(self.video_paths):
            _, rel_path = self.video_paths[self.current_index]
            try:
                if delta_deg < 0:
                    self.cache_manager.rotate_left(rel_path)
                else:
                    self.cache_manager.rotate_right(rel_path)
            except Exception:
                pass

    def toggle_favorite(self):
        if not self.cache_manager or not (0 <= self.current_index < len(self.video_paths)):
            return
        _, rel_path = self.video_paths[self.current_index]
        new_state = self.cache_manager.toggle_favorite(rel_path)
        self._update_fav_button()
        try:
            self.favorite_changed.emit(rel_path, new_state)
        except Exception:
            pass

    def _update_fav_button(self):
        is_fav = False
        if self.cache_manager and 0 <= self.current_index < len(self.video_paths):
            _, rel_path = self.video_paths[self.current_index]
            is_fav = self.cache_manager.is_favorite(rel_path)
        if is_fav:
            self.btn_fav.setText("已收藏")
            self.btn_fav.setStyleSheet(
                "QPushButton{background-color:#c84040;color:white;border:2px solid #e86060;"
                "border-radius:5px;font-size:13px;padding:4px 10px;font-weight:bold;}"
                "QPushButton:hover{background-color:#e85050;}"
            )
        else:
            self.btn_fav.setText("收藏")
            self.btn_fav.setStyleSheet(
                "QPushButton{background-color:#3a3a5a;color:white;border:2px solid #5a5a7a;"
                "border-radius:5px;font-size:13px;padding:4px 10px;font-weight:bold;}"
                "QPushButton:hover{background-color:#5a5a7a;}"
            )

    def _on_slider_pressed(self):
        self._dragging = True

    def _on_slider_released(self):
        self._dragging = False
        target_ms = int(self.progress_slider.value())
        if self._cv_cap is not None:
            try:
                self._cv_cap.set(cv2.CAP_PROP_POS_MSEC, float(max(0, target_ms)))
            except Exception:
                pass

    def _on_slider_moved(self, position):
        self.time_current.setText(self._format_ms(position))

    def _format_ms(self, ms):
        ms = max(0, int(ms))
        total_seconds = ms // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return "%02d:%02d:%02d" % (hours, minutes, seconds)
        return "%02d:%02d" % (minutes, seconds)

    def keyPressEvent(self, event):
        try:
            key = event.key()
            if key == Qt.Key_Escape:
                self.close()
            elif key == Qt.Key_Space:
                self.toggle_play()
            elif key == Qt.Key_Left:
                self._seek_relative(-5000)
            elif key == Qt.Key_Right:
                self._seek_relative(5000)
            elif key == Qt.Key_N:
                self.play_next()
            elif key == Qt.Key_P:
                self.play_previous()
            elif key == Qt.Key_F:
                self.toggle_favorite()
            elif key == Qt.Key_Q:
                self.rotate_video(-90)
            elif key == Qt.Key_E:
                self.rotate_video(90)
            else:
                super().keyPressEvent(event)
        except Exception as e:
            print(f"[VideoPlayerWindow] 键盘事件异常：{e}")

    def _seek_relative(self, delta_ms):
        target = max(0, min(self._total_ms, self._current_ms + delta_ms))
        if self._cv_cap is not None:
            try:
                self._cv_cap.set(cv2.CAP_PROP_POS_MSEC, float(target))
            except Exception:
                pass

    def closeEvent(self, event):
        try:
            if self._cv_timer is not None:
                try:
                    self._cv_timer.stop()
                except Exception:
                    pass
            if self._cv_cap is not None:
                try:
                    self._cv_cap.release()
                except Exception:
                    pass
        except Exception:
            pass
        event.accept()


class MainWindow(QMainWindow):
    """主窗口"""
    def __init__(self):
        super().__init__()
        self.root_folder = None
        self.collections = []  # 根目录子文件夹收藏夹列表
        self.thumbnail_manager = None
        self.cache_manager = None
        self.current_tab = 0  # 0 = 根目录, 1 = 收藏夹
        # 当前显示中的视频顺序列表 [(abs_path, rel_path), ...]
        self.current_video_list = []
        self.active_player = None

        self.setup_ui()
        self.apply_styles()

        # 启动时加载上次目录
        QTimer.singleShot(50, self._auto_load_last_folder)

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
        self.right_panel.set_on_double_clicked(self.on_video_double_clicked)

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
            self._load_folder(folder)

    def _load_folder(self, folder_path):
        """实际加载一个文件夹"""
        self.root_folder = folder_path.replace('\\', '/')
        set_last_root_folder(self.root_folder)
        self.init_thumbnail_manager()
        self.scan_collections(folder_path)

    def _auto_load_last_folder(self):
        """启动时自动加载上次打开的根目录"""
        last = get_last_root_folder()
        if last and os.path.exists(last) and os.path.isdir(last):
            try:
                self._load_folder(last)
            except Exception as e:
                print(f"自动加载上次目录失败: {e}")

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
        """填充根目录收藏夹列表（按标记状态排序，标记放最后）"""
        self.collection_list.clear()

        # 按标记状态排序：未标记的在前（保持原顺序），已标记的在后
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
                    video_rel_path = os.path.relpath(video_path, self.root_folder).replace('\\', '/')
                    exists, cache_path = self.cache_manager.cache_exists(video_rel_path)
                    if exists:
                        thumbnail = cache_path
                except:
                    pass

            item = QListWidgetItem(self.collection_list)
            item.setSizeHint(QSize(280, 420))

            is_marked = False
            if self.cache_manager:
                is_marked = self.cache_manager.is_marked(collection['name'])

            widget = CollectionItem(
                collection['name'],
                thumbnail,
                len(collection['videos']),
                is_marked=is_marked,
                on_mark_changed=self.on_collection_mark_changed
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
            self.cache_manager.get_favorite_count(),
            is_marked=False
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
            # 注意：这里的 collections 顺序可能与 populate_collection_list 显示顺序可能不同，
            # 但因为 populate_collection_list 显示的是当前 row 对应的索引，
            # 这里查找同名项显示
            widget = self.collection_list.itemWidget(item)
            target_name = widget.name if widget else None
            if target_name is not None:
                for c in self.collections:
                    if c['name'] == target_name:
                        self.show_videos(c)
                        return

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

        # 保存当前视频顺序列表（按收藏夹顺序）
        self.current_video_list = []
        for video_path in collection['videos']:
            video_relative_path = os.path.relpath(video_path, self.root_folder).replace('\\', '/')
            self.current_video_list.append((video_path, video_relative_path))
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

        # 保存当前视频顺序列表（按收藏夹顺序）
        self.current_video_list = []
        for video_path in fav_videos:
            video_relative_path = os.path.relpath(video_path, self.root_folder).replace('\\', '/')
            self.current_video_list.append((video_path, video_relative_path))
            self.right_panel.add_video(video_path, video_relative_path, self.cache_manager)

    def on_collection_mark_changed(self, collection_name, new_state):
        """收藏夹标记状态改变 - 更新 marked_collections 并重绘列表"""
        if self.cache_manager:
            self.cache_manager.set_mark(collection_name, new_state)
        # 重新填充（保持标记状态持久化在 marked_collections.json）
        self.populate_collection_list()

    def on_video_double_clicked(self, video_path, video_relative_path):
        """视频项被双击 - 打开播放器，按当前视频顺序播放"""
        if not self.current_video_list:
            # 回退：仅播放当前视频
            video_list = [(video_path, video_relative_path)]
            start_index = 0
        else:
            video_list = list(self.current_video_list)
            # 找到被双击的视频索引
            start_index = 0
            for i, (vp, rp) in enumerate(video_list):
                if rp == video_relative_path or vp == video_path:
                    start_index = i
                    break

        try:
            player = VideoPlayerWindow(
                video_list, start_index, self.cache_manager, parent=self, root_folder=self.root_folder
            )
            player.favorite_changed.connect(self._on_player_favorite_changed)
            self.active_player = player
            player.show()
        except Exception as e:
            print(f"打开播放器失败: {e}")

    def _on_player_favorite_changed(self, video_relative_path, new_state):
        """播放器中的收藏变更 - 同步到主界面"""
        # 刷新左侧收藏夹数量显示
        if self.cache_manager:
            self.refresh_fav_collection_list()
        # 如果当前显示的是默认收藏夹，刷新右侧视频显示（可能改变顺序或新增一个未收藏的视频会被从列表移除）
        if self.current_tab == 1:
            # 重新加载默认收藏夹 - 确保视频显示当前顺序信息
            # 为避免在播放时大量重建引起性能问题，这里只在当前面板更新
            self.show_default_favorites()

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
