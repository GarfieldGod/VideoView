"""VLC 依赖检测 / 缩略图线程池 / 通用工具"""

import os
import sys
import threading
from queue import Queue

from PyQt5.QtCore import QThread, pyqtSignal, QByteArray, QBuffer
from PyQt5.QtGui import QImage, QPixmap


# ============================================================
# VLC 依赖检测 —— 优先使用项目内嵌的 vlc/ 目录
# ============================================================
# 优先级：1. 项目根目录 vlc/  2. 系统标准安装路径  3. PATH
# 注：已移除 PotPlayer / C:\Application\VLC 等 32-bit 或无效路径，避免位数不匹配
_VLC_CANDIDATES = [
    # 1. 项目内嵌（用户自行复制 VLC 便携版到这里）
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vlc'),
]
# 2. 系统标准 VLC 安装路径（64-bit）
for _p in [
    r"C:\Program Files\VideoLAN\VLC",
    r"C:\Program Files (x86)\VideoLAN\VLC",
]:
    _VLC_CANDIDATES.append(_p)
# 3. PATH 环境变量
for _p in os.environ.get("PATH", "").split(os.pathsep):
    if _p and _p not in _VLC_CANDIDATES:
        _VLC_CANDIDATES.append(_p)


def _check_vlc_dll(dll_path):
    """校验 libvlc.dll 是否可用（基础函数存在则认为是有效 VLC 库）"""
    try:
        import ctypes
        dll = ctypes.CDLL(dll_path)
        if not hasattr(dll, 'libvlc_media_player_stop'):
            # 某些 stub / 4.x 版本缺失基础函数
            return False
        return True
    except Exception:
        return False


_VLC_DIR = None
for _p in _VLC_CANDIDATES:
    if _p and os.path.isfile(os.path.join(_p, "libvlc.dll")):
        if _check_vlc_dll(os.path.join(_p, "libvlc.dll")):
            _VLC_DIR = _p
            break

# Python 3.8+：ctypes.LoadLibrary 依赖 add_dll_directory 才能找到 DLL
if _VLC_DIR and hasattr(os, "add_dll_directory"):
    try:
        os.add_dll_directory(_VLC_DIR)
    except Exception:
        pass
if _VLC_DIR:
    os.environ["PATH"] = _VLC_DIR + os.pathsep + os.environ.get("PATH", "")

# 设置 VLC_PLUGIN_PATH（VLC 运行时要靠它找到解码器插件）
if _VLC_DIR:
    _plugin_dir = os.path.join(_VLC_DIR, "plugins")
    if os.path.isdir(_plugin_dir):
        os.environ["VLC_PLUGIN_PATH"] = _plugin_dir
        print(f"[VLC] bundled dir={_VLC_DIR}, plugins={_plugin_dir}")
    else:
        for _name in os.listdir(_VLC_DIR) if os.path.isdir(_VLC_DIR) else []:
            if _name.lower().startswith("plugin"):
                os.environ["VLC_PLUGIN_PATH"] = os.path.join(_VLC_DIR, _name)
                print(f"[VLC] bundled dir={_VLC_DIR}, plugins={os.path.join(_VLC_DIR, _name)}")
                break

# 尝试 import python-vlc
try:
    import vlc as _vlc_module
    _test_inst = _vlc_module.Instance("--no-xlib --quiet --no-video-title-show --no-osd -q")
    if _test_inst is not None:
        _test_player = _test_inst.media_player_new()
        if _test_player is not None:
            _vlc_module_found = True
            print(f"[VLC] python-vlc 已就绪（libvlc.dll 来自：{_VLC_DIR or '系统 PATH'}）")
        else:
            _vlc_module_found = False
            print("[VLC] VLC player 创建失败（位数不匹配？）")
    else:
        _vlc_module_found = False
        print("[VLC] VLC Instance 创建失败")
except Exception as _e:
    _vlc_module_found = False
    _vlc_module = None
    print(f"[VLC] python-vlc 不可用：{_e}")


def get_vlc_module():
    return _vlc_module if _vlc_module_found else None


def get_vlc_instance():
    """复用 utils 模块导入时已创建并验证过的 VLC Instance，避免重复创建导致 NoneType 错误"""
    return _test_inst if _vlc_module_found else None


def has_vlc():
    return _vlc_module_found


def get_vlc_dir():
    return _VLC_DIR


# ============================================================
# ThumbnailManager：在后台线程里生成缩略图，通过 signal 通知完成
# ============================================================
class ThumbnailManager(QThread):
    """缩略图生成线程池管理器"""
    finished = pyqtSignal(str)  # video_relative_path

    def __init__(self, cache_manager, max_workers=2):
        super().__init__()
        self.cache_manager = cache_manager
        self.max_workers = max_workers
        self.queue = Queue()
        self.active_count = 0
        self.running = True
        self.lock = threading.Lock()
        self.pending = set()

    def enqueue(self, video_path, video_relative_path, rotation_deg=0):
        deg = int(rotation_deg) % 360
        # 使用 (相对路径, 旋转角度) 作为去重 key，确保同视频不同旋转都会被处理
        dedup_key = (video_relative_path.replace('\\', '/'), deg)
        with self.lock:
            if dedup_key not in self.pending:
                self.pending.add(dedup_key)
                self.queue.put((video_path, video_relative_path, deg))

    def process_pending(self):
        while self.running:
            with self.lock:
                if self.active_count >= self.max_workers or self.queue.empty():
                    return
                video_path, video_relative_path, deg = self.queue.get()
                self.active_count += 1

            try:
                target_width = 560
                target_height = int(target_width * 16 / 9)
                # 使用旋转角度生成不同的缓存文件
                cache_path = self.cache_manager.get_cache_path(video_relative_path, deg)

                if not os.path.exists(cache_path):
                    # 延迟导入避免循环依赖
                    from core.thumbnail import generate_video_thumbnail_file
                    generate_video_thumbnail_file(video_path, target_width, target_height, cache_path, deg)
                    self.cache_manager.add_cache(video_relative_path, cache_path, deg)

                self.finished.emit(video_relative_path.replace('\\', '/'))
            except Exception as e:
                print(f"生成缩略图失败 {video_path} (rot={deg}°): {e}")
            finally:
                with self.lock:
                    self.active_count -= 1
                    pending_key = (video_relative_path.replace('\\', '/'), int(deg))
                    self.pending.discard(pending_key)

    def run(self):
        while self.running:
            self.process_pending()
            threading.Event().wait(0.05)

    def stop(self):
        self.running = False


# ============================================================
# 视频文件扫描 / 扩展名 / 图片工具函数
# ============================================================
VIDEO_EXTENSIONS = {
    '.mp4', '.avi', '.mkv', '.mov', '.wmv',
    '.flv', '.webm', '.m4v', '.mpg', '.mpeg',
}


def is_video_file(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in VIDEO_EXTENSIONS


def scan_folder_for_videos(folder_path):
    videos = []
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if is_video_file(file):
                videos.append(os.path.join(root, file))
    return videos


def path_hash(path):
    import hashlib
    return hashlib.md5(path.encode('utf-8')).hexdigest()[:16]


def pixmap_to_bytes(pixmap):
    if pixmap.isNull():
        return b''
    img = pixmap.toImage()
    ba = QByteArray()
    buffer = QBuffer(ba)
    buffer.open(QBuffer.WriteOnly)
    img.save(buffer, 'JPG', 85)
    return ba.data()


def bytes_to_pixmap(data):
    if not data:
        return QPixmap()
    img = QImage.fromData(data)
    if img.isNull():
        return QPixmap()
    return QPixmap.fromImage(img)
