"""应用内播放器

纯 VLC 渲染（硬件加速 + 有声音）：
- VLC 旋转通过 Media 级别的 :video-filter=transform{type=N} 参数实现
- type 映射：0 → 90°顺时针, 1 → 180°, 2 → 270°顺时针（逆时针 90°）
- 播放中切换旋转：重建 Media 并 seek 到当前进度
- VLC 不可用时回退到 OpenCV 逐帧播放

播放控制：上一个/下一个/暂停/进度拖动/左旋/右旋/收藏/音量。
旋转角度自动持久化到 rotations.json。
"""

import os
import cv2

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QEvent
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedLayout,
    QLabel, QPushButton, QSlider, QApplication,
)



class VideoPlayerWindow(QMainWindow):
    """应用内播放器"""

    favorite_changed = pyqtSignal(str, bool)

    def __init__(self, video_paths, start_index=0, cache_manager=None,
                 parent=None, root_folder=None, on_video_rotated=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self.video_paths = video_paths
        self.current_index = start_index
        self.cache_manager = cache_manager
        self.root_folder = root_folder.replace('\\', '/') if root_folder else None
        self._on_video_rotated_cb = on_video_rotated  # 旋转后通知主窗口刷新缩略图

        # 播放状态
        self._total_ms = 1
        self._current_ms = 0
        self._rotation = 0
        self._is_playing = False
        self._slider_seeking = False
        self._muted = False
        self._closed = False
        self._closing = False  # 窗口正在关闭，阻止所有定时器回调

        # OpenCV 回退
        self._cv_cap = None
        self._cv_timer = None
        self._cv_fps = 30.0

        # 追踪 pending single-shot timers，用于关闭时取消
        self._pending_timers = []

        # VLC 后端
        self._vlc_inst = None
        self._vlc_player = None
        self._vlc_timer = None

        # 必须先检测后端，再构建 UI
        self._detect_backend()
        # 音量从 app_config.json 读取
        from core import get_volume
        self._volume = get_volume()
        self.setup_ui()

        # 延迟加载放到 showEvent 中
        self._pending_load = (start_index,)
        self.showMaximized()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, '_pending_load') and self._pending_load:
            idx = self._pending_load[0]
            del self._pending_load
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self.load_video(idx))
            self._pending_timers.append(timer)
            timer.start(50)

    # ------------------------------------------------------------
    # 后端检测
    # ------------------------------------------------------------
    def _detect_backend(self):
        """在 setup_ui() 之前调用，设置 self._backend"""
        from core import has_vlc, get_vlc_instance
        inst = get_vlc_instance()
        if inst is not None:
            self._backend = 'vlc'
            self._init_vlc(inst)
            print("[VideoPlayerWindow] 使用 VLC 后端（流畅 + 有声音）")
        else:
            self._backend = 'opencv'
            print("[VideoPlayerWindow] VLC 不可用，使用 OpenCV 后端")

    # ------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------
    def setup_ui(self):
        self.setWindowTitle("视频播放器")

        central = QWidget()
        central.setStyleSheet("background-color:#000000;")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 视频显示区
        self._video_container = QWidget()
        self._video_container.setStyleSheet("background-color:#000000;")
        self._video_container.setMinimumSize(640, 360)
        main_layout.addWidget(self._video_container, 1)

        self._video_stack = QStackedLayout(self._video_container)
        self._video_stack.setContentsMargins(0, 0, 0, 0)
        self._video_stack.setStackingMode(QStackedLayout.StackOne)

        # QLabel：OpenCV 回退或加载提示
        self._display_label = QLabel()
        self._display_label.setAlignment(Qt.AlignCenter)
        self._display_label.setStyleSheet(
            "background-color:#000000; color:#888; font-size:18px;"
        )
        self._display_label.setText("加载中...")
        self._video_stack.addWidget(self._display_label)

        # VLC widget：VLC 直连渲染（绑定原生窗口）
        if self._backend == 'vlc':
            self._vlc_widget = QWidget(self._video_container)
            self._vlc_widget.setAttribute(Qt.WA_NativeWindow, True)
            self._vlc_widget.setStyleSheet("background-color:#000000;")
            self._video_stack.addWidget(self._vlc_widget)
        else:
            self._vlc_widget = None

        # 底部控制栏
        ctrl = QWidget()
        ctrl.setFixedHeight(110)
        ctrl.setStyleSheet("background-color:#1a1a2e;")
        ctrl_layout = QVBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(16, 8, 16, 8)
        ctrl_layout.setSpacing(4)

        time_row = QHBoxLayout()
        self.time_current = QLabel("00:00")
        self.time_current.setStyleSheet("color:#bbb; font-size:13px;")
        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, 1)
        self.progress_slider.setValue(0)
        self.progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self.progress_slider.sliderReleased.connect(self._on_slider_released)
        self.progress_slider.sliderMoved.connect(self._on_slider_moved)
        self.progress_slider.installEventFilter(self)
        self.time_total = QLabel("00:00")
        self.time_total.setStyleSheet("color:#bbb; font-size:13px;")
        time_row.addWidget(self.time_current)
        time_row.addWidget(self.progress_slider, 1)
        time_row.addWidget(self.time_total)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_prev = self._make_btn("上一个")
        self.btn_prev.clicked.connect(self.play_previous)
        self.btn_play = self._make_btn("暂停")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_next = self._make_btn("下一个")
        self.btn_next.clicked.connect(self.play_next)
        self.btn_rot_left = self._make_btn("左旋 90°")
        self.btn_rot_left.clicked.connect(lambda: self.rotate_video(-90))
        self.btn_rot_right = self._make_btn("右旋 90°")
        self.btn_rot_right.clicked.connect(lambda: self.rotate_video(90))
        self.btn_fav = self._make_btn("收藏")
        self.btn_fav.clicked.connect(self.toggle_favorite)

        # 音量控制
        self.btn_vol_down = self._make_btn("🔉")
        self.btn_vol_down.setFixedWidth(38)
        self.btn_vol_down.clicked.connect(lambda: self._change_volume(-10))
        self.btn_mute = self._make_btn("🔊")
        self.btn_mute.setFixedWidth(38)
        self.btn_mute.clicked.connect(self._toggle_mute)
        self.btn_vol_up = self._make_btn("🔊+")
        self.btn_vol_up.setFixedWidth(38)
        self.btn_vol_up.clicked.connect(lambda: self._change_volume(10))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self._volume)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.setStyleSheet(
            "QSlider::groove:horizontal{background:#2a2a3e;height:6px;border-radius:3px;}"
            "QSlider::handle:horizontal{background:#7a7a9e;width:14px;margin:-5px 0;border-radius:7px;}"
            "QSlider::handle:horizontal:hover{background:#aaaac0;}"
        )
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_label = QLabel(str(self._volume))
        self.volume_label.setStyleSheet("color:#bbb;font-size:13px;min-width:28px;")

        self.filename_label = QLabel("")
        self.filename_label.setStyleSheet("color:#ddd; font-size:13px; padding:0 8px;")
        self.filename_label.setAlignment(Qt.AlignCenter)
        btn_row.addWidget(self.btn_prev)
        btn_row.addWidget(self.btn_play)
        btn_row.addWidget(self.btn_next)
        btn_row.addWidget(self.btn_rot_left)
        btn_row.addWidget(self.btn_rot_right)
        btn_row.addWidget(self.btn_fav)
        btn_row.addWidget(self.btn_vol_down)
        btn_row.addWidget(self.btn_mute)
        btn_row.addWidget(self.btn_vol_up)
        btn_row.addWidget(self.volume_slider, 0)
        btn_row.addWidget(self.volume_label)
        btn_row.addWidget(self.filename_label, 1)

        ctrl_layout.addLayout(time_row)
        ctrl_layout.addLayout(btn_row)
        main_layout.addWidget(ctrl)

    def _make_btn(self, text):
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

    def eventFilter(self, obj, event):
        """进度条点击任意位置跳转"""
        if obj is self.progress_slider:
            etype = event.type()
            if etype == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    try:
                        pos_x = event.pos().x()
                        w = self.progress_slider.width()
                        if w > 0:
                            mn = self.progress_slider.minimum()
                            mx = self.progress_slider.maximum()
                            ratio = max(0.0, min(1.0, pos_x / w))
                            target = int(mn + (mx - mn) * ratio)
                            self.progress_slider.setValue(target)
                            self._slider_seeking = True
                            self._seek_to(target)
                            return True
                    except Exception:
                        pass
            elif etype == QEvent.MouseButtonRelease:
                self._slider_seeking = False
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------
    # VLC 后端
    # ------------------------------------------------------------
    def _init_vlc(self, inst):
        """复用 core.utils 已验证过的 VLC Instance"""
        try:
            self._vlc_inst = inst
            self._vlc_player = self._vlc_inst.media_player_new()
            if self._vlc_player is None:
                self._backend = 'opencv'
                return
            self._vlc_timer = QTimer(self)
            self._vlc_timer.timeout.connect(self._on_vlc_tick)
            self._vlc_timer.start(250)
        except Exception as e:
            print(f"[VideoPlayerWindow] VLC 初始化失败: {e}")
            self._backend = 'opencv'

    def _vlc_build_media(self, abs_path, rotation_deg=0):
        """创建 VLC Media，并在需要旋转时注入 transform 滤镜

        type 映射：0 → 90°顺时针, 1 → 180°, 2 → 270°顺时针
        rotation_deg=0 时不注入任何滤镜（保持原始方向）
        """
        media = self._vlc_inst.media_new(abs_path)
        if media is None:
            return None
        deg = int(rotation_deg) % 360
        ttype_map = {90: 0, 180: 1, 270: 2}
        ttype = ttype_map.get(deg, -1)
        if ttype >= 0:
            try:
                media.add_option(f":video-filter=transform{{type={ttype}}}")
                print(f"[VideoPlayerWindow] VLC 创建媒体（旋转 {deg}°）")
            except Exception as e:
                print(f"[VideoPlayerWindow] add_option 旋转滤镜失败: {e}")
        return media

    def _on_vlc_tick(self):
        """每 250ms 更新 VLC 进度条"""
        if self._closing or self._vlc_player is None or self._closed:
            return
        try:
            state = self._vlc_player.get_state()
            if state == 7:  # Error
                self._vlc_timer.stop()
                self._backend = 'opencv'
                abs_path = self.video_paths[self.current_index][0] if 0 <= self.current_index < len(self.video_paths) else None
                if abs_path and not self._closed:
                    self._load_cv(os.path.abspath(abs_path))
                return
            if state in (3, 4):  # Playing or Paused
                total = self._vlc_player.get_length()
                cur = self._vlc_player.get_time()
                self._total_ms = max(1, total)
                self._current_ms = max(0, cur)
                if total > 0:
                    try:
                        self.progress_slider.setRange(0, max(1, total))
                    except Exception:
                        pass
                    if not self._slider_seeking:
                        try:
                            self.progress_slider.setValue(max(0, int(cur)))
                        except Exception:
                            pass
                    self.time_current.setText(self._format_ms(cur))
                    self.time_total.setText(self._format_ms(total))
        except Exception:
            pass

    # ------------------------------------------------------------
    # 视频加载
    # ------------------------------------------------------------
    def load_video(self, index):
        if self._closing:
            return
        if not (0 <= index < len(self.video_paths)):
            return
        self.current_index = index
        abs_path, rel_path = self.video_paths[index]
        self.filename_label.setText(os.path.basename(abs_path))
        self._update_fav_button()

        if self.cache_manager is not None:
            try:
                self._rotation = int(self.cache_manager.get_rotation(rel_path)) % 360
            except Exception:
                self._rotation = 0

        self._current_ms = 0
        self.progress_slider.setRange(0, 1)
        self.progress_slider.setValue(0)
        self.time_current.setText("00:00")
        self.time_total.setText("00:00")

        abs_path = os.path.abspath(abs_path)
        if self._backend == 'vlc':
            self._load_vlc(abs_path)
        else:
            self._load_cv(abs_path)

    def _load_vlc(self, abs_path):
        """VLC 渲染：创建带旋转滤镜的 Media → set_media → set_hwnd → play"""
        if self._closing or self._closed:
            return
        try:
            if self._vlc_player is None:
                self._load_cv(abs_path)
                return

            self._vlc_timer.stop()
            try:
                self._vlc_player.stop()
            except Exception:
                pass

            # 1) 切换到 VLC widget，强制创建原生窗口
            if self._vlc_widget:
                try:
                    self._video_stack.setCurrentWidget(self._vlc_widget)
                except Exception:
                    pass
                self._vlc_widget.winId()
                self._vlc_widget.show()

            # 2) 创建带旋转滤镜的 Media，set_media
            media = self._vlc_build_media(abs_path, self._rotation)
            if media is None:
                raise RuntimeError("VLC 无法创建媒体")
            self._vlc_player.set_media(media)

            # 3) 绑定窗口句柄
            try:
                self._vlc_player.set_hwnd(int(self._vlc_widget.winId()))
            except Exception as e:
                print(f"[VideoPlayerWindow] set_hwnd 失败: {e}")

            # 4) 播放
            ret = self._vlc_player.play()
            print(f"[VideoPlayerWindow] VLC play() = {ret} (rotation={self._rotation}°)")
            self._is_playing = True
            self.btn_play.setText("暂停")
            self._vlc_timer.start(250)
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._apply_volume)
            self._pending_timers.append(timer)
            timer.start(150)

        except Exception as e:
            print(f"[VideoPlayerWindow] VLC 加载失败，回退到 OpenCV: {e}")
            self._backend = 'opencv'
            self._load_cv(abs_path)

    def _load_cv(self, abs_path):
        """OpenCV 逐帧播放（VLC 不可用时）"""
        if self._closing or self._closed:
            return
        try:
            if self._cv_timer:
                self._cv_timer.stop()
            if self._cv_cap:
                try:
                    self._cv_cap.release()
                except Exception:
                    pass
                self._cv_cap = None

            self._video_stack.setCurrentWidget(self._display_label)
            self._display_label.clear()
            self._display_label.setText("加载中...")

            cap = cv2.VideoCapture(abs_path)
            if not cap.isOpened():
                cap.release()
                self._display_label.setText("无法打开视频文件：\n" + os.path.basename(abs_path))
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

            if fps > 0 and total_frames > 0:
                total_ms = int(total_frames * 1000.0 / fps)
                self._total_ms = max(1, total_ms)
                try:
                    self.progress_slider.setRange(0, max(1, self._total_ms))
                except Exception:
                    pass
                self.time_total.setText(self._format_ms(self._total_ms))

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
            print(f"[VideoPlayerWindow] OpenCV 加载失败: {e}")
            self._display_label.setText("加载失败")

    def _on_cv_tick(self):
        if self._closing or self._cv_cap is None or not self._is_playing or self._closed:
            return
        try:
            ret, frame = self._cv_cap.read()
            if not ret or frame is None or frame.size == 0:
                try:
                    cur = int(self._cv_cap.get(cv2.CAP_PROP_POS_FRAMES))
                    total = int(self._cv_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if total > 10 and cur >= total - 2:
                        self._cv_timer.stop()
                        timer = QTimer(self)
                        timer.setSingleShot(True)
                        timer.timeout.connect(self.play_next)
                        self._pending_timers.append(timer)
                        timer.start(300)
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
                if not self._slider_seeking:
                    try:
                        self.progress_slider.setValue(pos_ms)
                    except Exception:
                        pass
                self.time_current.setText(self._format_ms(pos_ms))
            except Exception:
                pass
        except Exception as e:
            print(f"[VideoPlayerWindow] cv tick 异常: {e}")

    def _show_cv_frame(self, frame):
        """OpenCV 帧 → 旋转 → 缩放 → QLabel"""
        try:
            cw = self._video_container.width()
            ch = self._video_container.height()
            lw = max(1, cw if cw > 0 else 960)
            lh = max(1, ch if ch > 0 else 540)

            fh, fw = frame.shape[:2]
            scale = min(lw / fw, lh / fh)
            nw = max(1, int(fw * scale))
            nh = max(1, int(fh * scale))
            if nw != fw or nh != fh:
                frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)

            deg = int(self._rotation) % 360
            if deg == 90:
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            elif deg == 180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            elif deg == 270:
                frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h_, w_ = rgb.shape[:2]
            qimg = QImage(rgb.copy(), w_, h_, w_ * 3, QImage.Format_RGB888)
            if qimg.isNull():
                return
            self._display_label.setPixmap(QPixmap.fromImage(qimg))
        except Exception as e:
            print(f"[VideoPlayerWindow] _show_cv_frame 异常: {e}")

    def _refresh_cv_frame(self):
        """强制刷新 OpenCV 当前帧（读取并重新显示当前帧，带当前 rotation）"""
        if self._closing or self._cv_cap is None:
            return
        try:
            cur_ms = self._current_ms
            if cur_ms > 0:
                self._cv_cap.set(cv2.CAP_PROP_POS_MSEC, cur_ms)
            ret, frame = self._cv_cap.read()
            if ret and frame is not None and frame.size > 0:
                self._show_cv_frame(frame)
        except Exception:
            pass

    # ------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------
    def toggle_play(self):
        if self._backend == 'vlc' and self._vlc_player:
            state = self._vlc_player.get_state()
            if state == 3:  # Playing
                self._vlc_player.pause()
                self._is_playing = False
                self.btn_play.setText("播放")
            else:
                if state in (5, 6, 7):  # Stopped/Ended/Error
                    self._vlc_player.play()
                else:
                    # Paused(4) 或其它状态 -> 切换为播放
                    self._vlc_player.pause()
                self._is_playing = True
                self.btn_play.setText("暂停")
        else:
            if self._is_playing:
                self._is_playing = False
                if self._cv_timer:
                    self._cv_timer.stop()
                self.btn_play.setText("播放")
            else:
                self._is_playing = True
                if self._cv_timer:
                    interval = int(max(15, 1000.0 / max(1.0, self._cv_fps)))
                    self._cv_timer.start(interval)
                self.btn_play.setText("暂停")

    def play_previous(self):
        if len(self.video_paths) == 0:
            return
        self.load_video((self.current_index - 1) % len(self.video_paths))

    def play_next(self):
        if len(self.video_paths) == 0:
            return
        self.load_video((self.current_index + 1) % len(self.video_paths))

    def rotate_video(self, delta_deg):
        """旋转：更新角度并持久化，然后重建 Media 并 seek 到当前进度，通知主窗口刷新缩略图"""
        if self._closing:
            return
        old_rot = self._rotation
        self._rotation = (int(self._rotation) + int(delta_deg)) % 360
        if self.cache_manager and 0 <= self.current_index < len(self.video_paths):
            abs_path, rel_path = self.video_paths[self.current_index]
            try:
                if delta_deg < 0:
                    self.cache_manager.rotate_left(rel_path)
                else:
                    self.cache_manager.rotate_right(rel_path)
            except Exception:
                pass
            # 通知主窗口：旋转角度改变 — 需要重新生成缩略图并刷新UI
            if self._on_video_rotated_cb:
                try:
                    self._on_video_rotated_cb(abs_path, rel_path)
                except Exception as e:
                    print(f"[VideoPlayerWindow] on_video_rotated 回调异常: {e}")

        if self._backend == 'vlc':
            # VLC：重建 Media + seek 回到当前进度
            self._reload_current()
        elif self._backend == 'opencv' and self._cv_cap is not None:
            # OpenCV：_show_cv_frame 每次都读取 self._rotation，强制刷新当前帧
            self._refresh_cv_frame()

        print(f"[VideoPlayerWindow] 旋转：{old_rot}° → {self._rotation}°（后端：{self._backend}）")

    def _reload_current(self):
        """重建当前 Media（保持播放进度和播放状态）——用于旋转角度改变"""
        if self._closing or self._closed or not (0 <= self.current_index < len(self.video_paths)):
            return
        was_playing = self._is_playing
        cur_ms = max(0, int(self._current_ms))
        abs_path = os.path.abspath(self.video_paths[self.current_index][0])

        if self._backend == 'vlc':
            self._load_vlc(abs_path)
            if cur_ms > 0:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda: self._seek_to(cur_ms))
                self._pending_timers.append(timer)
                timer.start(400)
        else:
            self._load_cv(abs_path)
            if cur_ms > 0:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda: self._seek_to(cur_ms))
                self._pending_timers.append(timer)
                timer.start(200)

        if was_playing:
            self._is_playing = True

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

    def _seek_to(self, target_ms):
        """统一跳转入口"""
        if self._closing or self._closed:
            return
        target_ms = max(0, int(target_ms))
        if self._backend == 'vlc' and self._vlc_player:
            try:
                self._vlc_player.set_time(target_ms)
            except Exception:
                pass
        elif self._cv_cap is not None:
            try:
                self._cv_cap.set(cv2.CAP_PROP_POS_MSEC, float(target_ms))
            except Exception:
                pass
        try:
            self._current_ms = target_ms
            self.time_current.setText(self._format_ms(target_ms))
        except Exception:
            pass

    def _apply_volume(self):
        """应用音量到 VLC 并持久化到 app_config.json"""
        if self._closing:
            return
        vol = 0 if self._muted else max(0, min(100, int(self._volume)))
        try:
            self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(vol if not self._muted else 0)
        except Exception:
            pass
        finally:
            try:
                self.volume_slider.blockSignals(False)
            except Exception:
                pass
        try:
            self.volume_label.setText(str(vol))
        except Exception:
            pass
        if self._backend == 'vlc' and self._vlc_player is not None:
            try:
                self._vlc_player.audio_set_volume(vol)
            except Exception:
                pass
        from core import save_volume
        save_volume(self._volume if not self._muted else self._volume)

    def _on_volume_changed(self, value):
        self._volume = max(0, min(100, int(value)))
        self._muted = False
        try:
            self.btn_mute.setText("🔊")
        except Exception:
            pass
        self._apply_volume()

    def _change_volume(self, delta):
        self._muted = False
        self._volume = max(0, min(100, int(self._volume) + int(delta)))
        try:
            self.btn_mute.setText("🔊")
        except Exception:
            pass
        self._apply_volume()

    def _toggle_mute(self):
        self._muted = not self._muted
        try:
            self.btn_mute.setText("🔇" if self._muted else "🔊")
        except Exception:
            pass
        self._apply_volume()

    def _on_slider_pressed(self):
        self._slider_seeking = True

    def _on_slider_released(self):
        self._slider_seeking = False
        target_ms = int(self.progress_slider.value())
        self._seek_to(target_ms)

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
            elif key == Qt.Key_Up:
                self._change_volume(5)
                event.accept()
                return
            elif key == Qt.Key_Down:
                self._change_volume(-5)
                event.accept()
                return
            elif key == Qt.Key_M:
                self._toggle_mute()
                event.accept()
                return
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
            print(f"[VideoPlayerWindow] 键盘事件异常: {e}")

    def _seek_relative(self, delta_ms):
        target = max(0, min(self._total_ms, self._current_ms + delta_ms))
        try:
            self.progress_slider.setValue(int(target))
        except Exception:
            pass
        self._seek_to(target)

    def closeEvent(self, event):
        # 第一步：只设置标志、停止所有定时器、隐藏窗口，然后立即返回
        # 避免在主线程执行可能阻塞的资源释放操作
        if self._closing:
            # 已经在异步清理中，接受关闭
            event.accept()
            return
        self._closing = True

        # 取消所有 pending timers（Qt 定时器 stop() 是安全的，不会阻塞）
        for timer in self._pending_timers:
            try:
                timer.stop()
            except Exception:
                pass
        self._pending_timers.clear()

        self._closed = True
        if self._vlc_timer:
            try:
                self._vlc_timer.stop()
            except Exception:
                pass
        if self._cv_timer:
            try:
                self._cv_timer.stop()
            except Exception:
                pass

        # 隐藏窗口，让用户觉得窗口已经关闭
        try:
            self.hide()
        except Exception:
            pass

        # 忽略当前 closeEvent，用异步定时器在后台释放资源
        event.ignore()
        cleanup_timer = QTimer(self)
        cleanup_timer.setSingleShot(True)
        cleanup_timer.timeout.connect(self._async_cleanup)
        cleanup_timer.start(30)

    def _async_cleanup(self):
        """异步清理资源：分步骤通过 QTimer 调度，每步之间让出事件循环，避免死锁。
        步骤：解绑窗口 → 停止 VLC → 释放 OpenCV → 清空显示 → 销毁窗口"""
        # 步骤 1：解绑 VLC 渲染窗口句柄（关键：防止 VLC 回调已销毁的窗口）
        if self._vlc_player is not None:
            try:
                import ctypes
                self._vlc_player.set_hwnd(ctypes.c_void_p(0))
            except Exception:
                pass

        # 步骤 2：通过 QTimer 下一步（让出事件循环）
        t2 = QTimer(self)
        t2.setSingleShot(True)
        t2.timeout.connect(self._async_cleanup_step2)
        t2.start(10)

    def _async_cleanup_step2(self):
        # 停止 VLC 播放
        if self._vlc_player is not None:
            try:
                self._vlc_player.stop()
            except Exception:
                pass
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(self._async_cleanup_step3)
        t.start(10)

    def _async_cleanup_step3(self):
        # 释放 OpenCV 捕获对象
        if self._cv_cap is not None:
            try:
                self._cv_cap.release()
            except Exception:
                pass
        self._cv_cap = None
        # 清理显示引用
        try:
            if hasattr(self, '_display_label') and self._display_label:
                self._display_label.clear()
        except Exception:
            pass
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(self._async_cleanup_step4)
        t.start(10)

    def _async_cleanup_step4(self):
        # 最后：销毁窗口（deleteLater 比 destroy/close 更安全）
        try:
            self.deleteLater()
        except Exception:
            try:
                self.destroy()
            except Exception:
                pass
