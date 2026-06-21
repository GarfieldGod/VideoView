"""应用内播放器：OpenCV 逐帧播放

使用 cv2.VideoCapture 按视频 fps 逐帧读取，每帧缩放、旋转、BGR→RGB 后显示到 QLabel。
- 因为是 Python 层逐帧处理，性能不如原生播放器，但不依赖额外库，保证总能播放。
- 上一个/下一个/收藏/旋转/进度条/键盘快捷键一应俱全。
"""

import os
import cv2

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap, QColor
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QApplication,
)


from PyQt5.QtCore import pyqtSignal


class VideoPlayerWindow(QMainWindow):
    """应用内播放器：OpenCV 逐帧播放"""

    favorite_changed = pyqtSignal(str, bool)

    def __init__(self, video_paths, start_index=0, cache_manager=None,
                 parent=None, root_folder=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self.video_paths = video_paths
        self.current_index = start_index
        self.cache_manager = cache_manager
        self.root_folder = root_folder.replace('\\', '/') if root_folder else None

        # 播放状态
        self._total_ms = 1
        self._current_ms = 0
        self._rotation = 0
        self._is_playing = False

        # OpenCV 后端内部状态
        self._cv_cap = None
        self._cv_timer = None
        self._cv_fps = 30.0

        self.setup_ui()
        QTimer.singleShot(100, lambda: self.load_video(self.current_index))
        self.show()

    # ------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------
    def setup_ui(self):
        self.setWindowTitle("视频播放器")
        self.resize(1280, 820)

        central = QWidget()
        central.setStyleSheet("background-color:#000000;")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._cv_label = QLabel()
        self._cv_label.setAlignment(Qt.AlignCenter)
        self._cv_label.setStyleSheet(
            "background-color:#000000; color:#888; font-size:18px;"
        )
        self._cv_label.setText("加载中...")
        self._cv_label.setMinimumSize(640, 360)
        main_layout.addWidget(self._cv_label, 1)

        # 底部控制栏
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
        self.filename_label.setStyleSheet(
            "color:#ddd; font-size:13px; padding:0 8px;"
        )
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

    # ------------------------------------------------------------
    # 视频加载
    # ------------------------------------------------------------
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
            interval_ms = int(max(15, 1000.0 / fps))

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
            self._cv_timer.start(interval_ms)

        except Exception as e:
            print(f"[VideoPlayerWindow] OpenCV 加载失败：{e}")
            self._cv_label.setText("加载失败")

    def _on_cv_tick(self):
        if self._cv_cap is None or not self._is_playing:
            return
        try:
            ret, frame = self._cv_cap.read()
            if not ret or frame is None or frame.size == 0:
                # 到结尾，自动播放下一个
                try:
                    cur = int(self._cv_cap.get(cv2.CAP_PROP_POS_FRAMES))
                    total = int(self._cv_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if total > 10 and cur >= total - 2:
                        if self._cv_timer is not None:
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

    # ------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------
    def toggle_play(self):
        if self._is_playing:
            self._is_playing = False
            if self._cv_timer is not None:
                self._cv_timer.stop()
            self.btn_play.setText("播放")
        else:
            self._is_playing = True
            if self._cv_timer is not None:
                interval = int(max(15, 1000.0 / max(1.0, self._cv_fps)))
                self._cv_timer.start(interval)
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
        # 拖动中：暂停定时器？不暂停，只是禁止 setValue
        pass

    def _on_slider_released(self):
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
