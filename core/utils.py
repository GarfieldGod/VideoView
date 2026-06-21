"""缩略图生成线程池管理器 + 通用工具函数"""

import os
import threading
from queue import Queue

from PyQt5.QtCore import QThread, pyqtSignal, QByteArray, QBuffer
from PyQt5.QtGui import QImage, QPixmap


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

    def enqueue(self, video_path, video_relative_path):
        with self.lock:
            if video_path not in self.pending:
                self.pending.add(video_path)
                self.queue.put((video_path, video_relative_path))

    def process_pending(self):
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
                    # 延迟导入避免循环依赖
                    from core.thumbnail import generate_video_thumbnail_file
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
