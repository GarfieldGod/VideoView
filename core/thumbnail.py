"""缩略图生成（使用 cv2）

注意：Windows 上设置了「隐藏」或「系统」属性的视频文件，cv2.VideoCapture
有时会打开失败。我们在打开视频前通过 utils 的辅助函数临时去除
隐藏/系统属性，读取完成后再恢复。
"""

import os
import cv2
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt

from core.config import makedirs_hidden, ensure_hidden
from core.utils import _is_file_entry_readable, _restore_file_attributes


# 缩略图大小
THUMBNAIL_SIZE_COLLECTION = (200, int(200 * 16 / 9))   # 收藏夹列表
THUMBNAIL_SIZE_VIDEO = (530, int(530 * 16 / 9))         # 视频预览网格


def _open_video_capture(video_path):
    """打开视频捕获 —— 兼容：
       1) Windows 下中文/特殊字符路径（通过 GetShortPathNameW 转短路径）
       2) Windows 隐藏/系统属性的视频文件（临时去隐藏属性后重试）
    """
    from core.utils import _as_cv2_safe_path
    safe_path = _as_cv2_safe_path(video_path)
    cap = cv2.VideoCapture(safe_path)
    if cap is not None and cap.isOpened():
        return cap, None

    if cap is not None:
        try:
            cap.release()
        except Exception:
            pass

    # 常规打开失败（可能是隐藏/系统属性或中文路径导致），再尝试去除隐藏属性后重试
    saved = _is_file_entry_readable(video_path)
    cap2 = cv2.VideoCapture(_as_cv2_safe_path(video_path))
    if cap2 is not None and cap2.isOpened():
        return cap2, saved

    # 仍然失败
    try:
        if cap2 is not None:
            cap2.release()
    except Exception:
        pass
    _restore_file_attributes(saved)
    return None, None


def _close_video_capture(cap, saved_attrs):
    """释放 cv2 VideoCapture，并在需要时恢复文件属性"""
    try:
        if cap is not None:
            cap.release()
    except Exception:
        pass
    _restore_file_attributes(saved_attrs)


def generate_video_thumbnail_file(video_path, target_width, target_height,
                                   cache_path, rotation_deg=0):
    """生成视频缩略图并保存到缓存文件（旋转后覆盖原有缓存）。

    按当前 rotation_deg 应用旋转后裁剪缩放；每次都会先删除旧缓存文件再写入新的。
    """
    try:
        if os.path.exists(cache_path):
            os.remove(cache_path)
    except Exception:
        pass

    cap, saved_attrs = _open_video_capture(video_path)
    if cap is None or not cap.isOpened():
        _close_video_capture(cap, saved_attrs)
        raise Exception("无法打开视频")

    try:
        video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            raise Exception("视频帧数无效")

        frame_idx = total_frames // 2
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

        ret, frame = cap.read()
        if not ret or frame is None:
            raise Exception("无法读取视频帧")

        # 应用旋转（影响后续宽高比计算）
        deg = int(rotation_deg) % 360
        if deg == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif deg == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif deg == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        # 旋转后重新计算宽高（90/270 会交换宽高）
        video_width = frame.shape[1]
        video_height = frame.shape[0]

        video_aspect = video_width / video_height
        target_aspect = target_width / target_height

        if abs(video_aspect - target_aspect) < 0.01:
            frame = cv2.resize(frame, (target_width, target_height))
        elif video_aspect > target_aspect:
            crop_width = int(video_height * target_aspect)
            crop_x = (video_width - crop_width) // 2
            frame = frame[:, crop_x:crop_x + crop_width]
            frame = cv2.resize(frame, (target_width, target_height))
        else:
            crop_height = int(video_width / target_aspect)
            crop_y = (video_height - crop_height) // 2
            frame = frame[crop_y:crop_y + crop_height, :]
            frame = cv2.resize(frame, (target_width, target_height))

        makedirs_hidden(os.path.dirname(cache_path))
        # 若之前的缓存文件被错误地标记了隐藏属性，去除它
        try:
            _unset_windows_hidden_on_files(cache_path)
        except Exception:
            pass
        # 注意：cv2.imwrite 在 Windows 下对中文路径会静默失败
        # （OpenCV 的 C API 用 ANSI 编码解析路径）。
        # 用 cv2.imencode + Python 原生 open 写入来绕过这个问题。
        try:
            ret, buf = cv2.imencode('.jpg', frame)
            if ret:
                with open(cache_path, 'wb') as f:
                    f.write(buf.tobytes())
            else:
                # 兜底：退化到 cv2.imwrite（若路径不含中文可能成功）
                cv2.imwrite(cache_path, frame)
        except Exception:
            cv2.imwrite(cache_path, frame)
    finally:
        _close_video_capture(cap, saved_attrs)


def generate_video_thumbnail(video_path, target_width, target_height=None,
                              cache_path=None, rotation_deg=0):
    """生成视频缩略图，按 9:16 比例从视频中间裁剪；支持旋转

    兼容 Windows 隐藏/系统属性的视频文件。
    """
    cap, saved_attrs = _open_video_capture(video_path)
    if cap is None or not cap.isOpened():
        _close_video_capture(cap, saved_attrs)
        raise Exception("无法打开视频")

    try:
        video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            raise Exception("视频帧数无效")

        frame_idx = total_frames // 2
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

        ret, frame = cap.read()
        if not ret or frame is None:
            raise Exception("无法读取视频帧")

        # 应用旋转
        deg = int(rotation_deg) % 360
        if deg == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif deg == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif deg == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        if target_height is None:
            target_height = int(target_width * 16 / 9)

        fw, fh = frame.shape[1], frame.shape[0]
        video_aspect = fw / fh
        target_aspect = 9 / 16

        if abs(video_aspect - target_aspect) < 0.01:
            frame = cv2.resize(frame, (target_width, target_height))
        else:
            if video_aspect > target_aspect:
                crop_width = int(fh * 9 / 16)
                crop_x = (fw - crop_width) // 2
                frame = frame[:, crop_x:crop_x + crop_width]
            else:
                crop_height = int(fw * 16 / 9)
                crop_y = (fh - crop_height) // 2
                frame = frame[crop_y:crop_y + crop_height, :]

            frame = cv2.resize(frame, (target_width, target_height))

        if cache_path:
            makedirs_hidden(os.path.dirname(cache_path))
            try:
                _unset_windows_hidden_on_files(cache_path)
            except Exception:
                pass
            # 同样用 cv2.imencode + 原生 open 写文件，避免 Windows 下中文路径
            # 被 OpenCV 的 C API 以 ANSI 解析导致写入失败。
            try:
                ret, buf = cv2.imencode('.jpg', frame)
                if ret:
                    with open(cache_path, 'wb') as f:
                        f.write(buf.tobytes())
                else:
                    cv2.imwrite(cache_path, frame)
            except Exception:
                cv2.imwrite(cache_path, frame)

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(frame_rgb.data, target_width, target_height,
                       target_width * 3, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(image)
    finally:
        _close_video_capture(cap, saved_attrs)
