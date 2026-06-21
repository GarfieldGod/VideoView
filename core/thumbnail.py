"""缩略图生成（使用 cv2）"""

import os
import cv2
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt


# 缩略图大小
THUMBNAIL_SIZE_COLLECTION = (200, int(200 * 16 / 9))   # 收藏夹列表
THUMBNAIL_SIZE_VIDEO = (560, int(560 * 16 / 9))         # 视频预览网格


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

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    cv2.imwrite(cache_path, frame)


def generate_video_thumbnail(video_path, target_width, target_height=None,
                              cache_path=None, rotation_deg=0):
    """生成视频缩略图，按 9:16 比例从视频中间裁剪；支持旋转"""
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
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        cv2.imwrite(cache_path, frame)

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = QImage(frame_rgb.data, target_width, target_height,
                   target_width * 3, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image)
