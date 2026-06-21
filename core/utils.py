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
        """入队生成缩略图 — 视频路径为去重 key，rotation_deg 仅用于生成时的旋转参数"""
        dedup_key = video_relative_path.replace('\\', '/')
        with self.lock:
            if dedup_key not in self.pending:
                self.pending.add(dedup_key)
                self.queue.put((video_path, video_relative_path, rotation_deg))

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
                # 获取缓存路径（统一 hash.jpg，不区分角度；旋转后需先调用 clear_cache_for）
                cache_path = self.cache_manager.get_cache_path(video_relative_path)

                if not os.path.exists(cache_path):
                    # 生成（函数内部会先删除旧文件再写入新的）
                    from core.thumbnail import generate_video_thumbnail_file
                    generate_video_thumbnail_file(video_path, target_width, target_height, cache_path, deg)
                    self.cache_manager.add_cache(video_relative_path, cache_path, deg)

                self.finished.emit(video_relative_path.replace('\\', '/'))
            except Exception as e:
                print(f"生成缩略图失败 {video_path} (rot={deg}°): {e}")
            finally:
                with self.lock:
                    self.active_count -= 1
                    pending_key = video_relative_path.replace('\\', '/')
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


# Windows 文件属性常量（用于兼容 os.scandir 的 DirEntry.stat().st_file_attributes）
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4
_FILE_ATTRIBUTE_DIRECTORY = 0x10


def _as_cv2_safe_path(path):
    """返回可以被 cv2.VideoCapture / cv2.imwrite 等 API 安全使用的路径。

    Windows 下 OpenCV 的 C API 用 ANSI 编码解析路径，遇到中文路径会失败。
    这里尝试用 kernel32!GetShortPathNameW 获取 DOS 8.3 短路径（纯 ASCII）。
    失败或非 Windows 平台返回原路径。
    """
    if not path:
        return path
    if os.name != 'nt':
        return path
    try:
        import ctypes
        GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        abs_path = os.path.abspath(str(path))
        # 第一次调用：获取需要的缓冲区大小（含结尾空字符）
        needed = GetShortPathNameW(abs_path, None, 0)
        if needed <= 0:
            return path  # 获取失败，退还原路径
        buf = ctypes.create_unicode_buffer(needed)
        real_size = GetShortPathNameW(abs_path, buf, needed)
        if real_size <= 0:
            return path
        short_path = buf.value
        # 如果获取到的短路径有效，返回它
        if short_path and os.path.exists(short_path):
            return short_path
        return path
    except Exception:
        return path


def _is_windows_path(path):
    """判断是否为 Windows 路径（基于系统或路径含有盘符前缀）"""
    if os.name == 'nt':
        return True
    # 在非 Windows 系统上，若用户传入 Windows 风格路径（很少见），仍然保守返回 False
    return False


def _is_file_entry_readable(path):
    """兼容 Windows 隐藏/系统属性：确保文件可读

    在 Windows 上，cv2.VideoCapture 对带 FILE_ATTRIBUTE_HIDDEN 的文件
    有时会打开失败。这里尝试临时去除隐藏/系统属性（失败时静默忽略），
    调用方使用完后调用 _restore_file_attributes 恢复。

    返回一个 dict 描述原始属性（非 Windows 或不支持时返回 None）。
    """
    if not _is_windows_path(path):
        return None
    try:
        import ctypes
        GetFileAttributesW = ctypes.windll.kernel32.GetFileAttributesW
        SetFileAttributesW = ctypes.windll.kernel32.SetFileAttributesW
        INVALID_FILE_ATTRIBUTES = -1

        attrs = GetFileAttributesW(str(path))
        if attrs == INVALID_FILE_ATTRIBUTES:
            return None
        # 如果有隐藏/系统属性，先临时去掉（以便 cv2/系统打开读取）
        if attrs & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM):
            new_attrs = attrs & ~(_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM)
            if SetFileAttributesW(str(path), new_attrs) != 0:
                return {"path": path, "attrs": attrs}
        return None
    except Exception:
        return None


def _restore_file_attributes(saved):
    """恢复文件原始隐藏/系统属性"""
    if not saved:
        return
    try:
        import ctypes
        SetFileAttributesW = ctypes.windll.kernel32.SetFileAttributesW
        SetFileAttributesW(str(saved["path"]), saved["attrs"])
    except Exception:
        pass


def scan_folder_for_videos(folder_path, recursive=False):
    """扫描文件夹下的视频文件（Windows 下会包含带隐藏属性的视频）

    Args:
        folder_path: 要扫描的文件夹路径
        recursive: 是否递归扫描子文件夹（默认 False，只扫描直接文件）

    实现要点：
    - 使用 os.scandir 替代 os.listdir/os.walk，获得 DirEntry
      （在 Windows 上包含 st_file_attributes，能准确识别隐藏/系统属性）
    - 不主动跳过隐藏/系统属性的文件或目录——用户可能需要扫描它们
    - 仍然跳过 .videoview 等应用元数据目录
    """
    videos = []
    try:
        if recursive:
            # 递归扫描：os.walk 在某些情况下会跳过「隐藏+系统」属性的目录，
            # 这里手动实现基于 scandir 的递归确保覆盖。
            stack = [folder_path]
            while stack:
                cur = stack.pop()
                try:
                    with os.scandir(cur) as it:
                        for entry in it:
                            name = entry.name
                            # 跳过应用自己的元数据目录
                            if name == '.videoview' or name.startswith('$'):
                                continue
                            try:
                                if entry.is_file(follow_symlinks=False):
                                    if is_video_file(name):
                                        videos.append(entry.path)
                                elif entry.is_dir(follow_symlinks=False):
                                    stack.append(entry.path)
                            except Exception:
                                continue
                except Exception:
                    continue
        else:
            # 只扫描直接文件，不递归进入子目录
            with os.scandir(folder_path) as it:
                for entry in it:
                    name = entry.name
                    if name == '.videoview':
                        continue
                    try:
                        if entry.is_file(follow_symlinks=False) and is_video_file(name):
                            videos.append(entry.path)
                    except Exception:
                        continue
    except Exception:
        # scandir 失败时退化到 os.listdir，但仍然收集所有文件（不依赖隐藏属性）
        try:
            for file in os.listdir(folder_path):
                if file == '.videoview':
                    continue
                file_path = os.path.join(folder_path, file)
                try:
                    if os.path.isfile(file_path) and is_video_file(file):
                        videos.append(file_path)
                except Exception:
                    continue
        except Exception:
            pass
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
