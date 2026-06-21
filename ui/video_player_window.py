"""应用内播放器

优先使用 VLC（流畅 + 有声音），VLC 不可用时降级到 OpenCV 逐帧播放。

播放控制：上一个/下一个/暂停/进度拖动/左旋/右旋/收藏。
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
        self._slider_seeking = False
        self._muted = False
        self._closed = False  # 窗口关闭标志，防止定时器在析构后触发

        # OpenCV 后端
        self._cv_cap = None
        self._cv_timer = None
        self._cv_fps = 30.0

        # VLC 后端
        self._vlc_inst = None
        self._vlc_player = None
        self._vlc_timer = None
        self._vlc_widget = None

        # 必须先检测后端，再构建 UI（setup_ui 依赖 self._backend）
        self._detect_backend()
        # 音量从 app_config.json 读取（持久化），无 config 时默认 80
        from core import get_volume
        self._volume = get_volume()
        self.setup_ui()

        # 延迟加载放到 showEvent 中，此时窗口已渲染完毕，winId() 有效
        self._pending_load = (start_index,)
        self.show()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, '_pending_load') and self._pending_load:
            idx = self._pending_load[0]
            del self._pending_load
            QTimer.singleShot(50, lambda: self.load_video(idx))

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
        self.resize(1280, 820)

        central = QWidget()
        central.setStyleSheet("background-color:#000000;")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 视频显示区：一个容器 widget，内嵌 QStackedLayout，VLC widget 与 QLabel 同级切换
        self._video_container = QWidget()
        self._video_container.setStyleSheet("background-color:#000000;")
        self._video_container.setMinimumSize(640, 360)
        stack = QStackedLayout(self._video_container)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setStackingMode(QStackedLayout.StackOne)
        self._video_stack = stack

        self._display_label = QLabel()
        self._display_label.setAlignment(Qt.AlignCenter)
        self._display_label.setStyleSheet(
            "background-color:#000000; color:#888; font-size:18px;"
        )
        self._display_label.setText("加载中...")
        stack.addWidget(self._display_label)

        if self._backend == 'vlc':
            self._vlc_widget = QWidget(self._video_container)
            self._vlc_widget.setAttribute(Qt.WA_NativeWindow, True)
            self._vlc_widget.setStyleSheet("background-color:#000000;")
            stack.addWidget(self._vlc_widget)
        else:
            self._vlc_widget = None

        main_layout.addWidget(self._video_container, 1)

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
        # 初始范围用 0~1，避免 total=0 时 setRange(0,0) 导致异常
        self.progress_slider.setRange(0, 1)
        self.progress_slider.setValue(0)
        self.progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self.progress_slider.sliderReleased.connect(self._on_slider_released)
        self.progress_slider.sliderMoved.connect(self._on_slider_moved)
        # 安装事件过滤器：点击进度条任意位置直接跳转
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
        """事件过滤器：让进度条支持点击任意位置跳转"""
        if obj is self.progress_slider:
            etype = event.type()
            if etype == QEvent.MouseButtonPress:
                # 根据鼠标点击位置计算目标毫秒值，并立即跳转
                btn = event.button()
                if btn == Qt.LeftButton:
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
                            return True  # 消费事件，阻止默认 pageStep 行为
                    except Exception:
                        pass
            elif etype == QEvent.MouseButtonRelease:
                self._slider_seeking = False
                return True
        return super().eventFilter(obj, event)

    def _seek_to(self, target_ms):
        """统一跳转入口：同时更新 VLC 或 OpenCV 播放位置"""
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
        """把 self._volume 应用到 VLC 播放器并持久化"""
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
        # 持久化到 app_config.json
        from core import save_volume
        save_volume(vol if not self._muted else self._volume)

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

    # ------------------------------------------------------------
    # VLC 后端
    # ------------------------------------------------------------
    def _init_vlc(self, inst):
        """复用 core.utils 已验证过的 VLC Instance，避免重复创建"""
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

    def _bind_vlc_window(self):
        """把 VLC 视频输出绑定到 _vlc_widget 的 HWND"""
        if self._vlc_widget is None or self._vlc_player is None:
            return
        try:
            self._vlc_widget.winId()
            self._vlc_player.set_hwnd(self._vlc_widget.winId())
        except Exception as e:
            print(f"[VideoPlayerWindow] set_hwnd 失败: {e}")

    def _vlc_build_media(self, abs_path, rotation_deg):
        """构造 VLC Media，并注入旋转滤镜（必须在 set_media 之前设置）"""
        media = self._vlc_inst.media_new(abs_path)
        if media is None:
            return None

        deg = int(rotation_deg) % 360
        if deg > 0:
            # transform 滤镜参数：type=0(90°顺时针) / 1(180°) / 2(90°逆时针) / 3(90°逆时针)
            # 注意：VLC transform type: 0=90°RL, 1=180°, 2=270°RL (即 90°逆时针)
            # 我们需要：90° -> type=0 (clockwise/right), 180° -> type=1, 270° -> type=2 (counter-clockwise/left)
            ttype_map = {90: 0, 180: 1, 270: 2}
            ttype = ttype_map.get(deg, -1)
            if ttype >= 0:
                try:
                    media.add_option(f":video-filter=transform{{type={ttype}}}")
                except Exception as e:
                    print(f"[VideoPlayerWindow] add_option 旋转滤镜失败: {e}")
        return media

    def _on_vlc_tick(self):
        """每 250ms 更新 VLC 进度条；检测播放错误并降级 OpenCV"""
        if self._vlc_player is None or self._closed:
            return
        try:
            state = self._vlc_player.get_state()
            # VLC 状态：0=NothingSpecial, 1=Opening, 2=Buffering, 3=Playing, 4=Paused, 5=Stopped, 6=Ended, 7=Error
            if state == 7:  # Error — 降级到 OpenCV
                if self._closed:
                    return
                print("[VideoPlayerWindow] VLC 播放错误，降级到 OpenCV")
                self._vlc_timer.stop()
                try:
                    self._vlc_player.stop()
                except Exception:
                    pass
                self._backend = 'opencv'
                abs_path = self.video_paths[self.current_index][0] if 0 <= self.current_index < len(self.video_paths) else None
                if abs_path:
                    self._load_cv(os.path.abspath(abs_path))
                return

            if state in (3, 4):  # Playing or Paused
                if self._closed:
                    return
                total = self._vlc_player.get_length()
                cur = self._vlc_player.get_time()
                self._total_ms = max(1, total)
                self._current_ms = max(0, cur)
                if total > 0:
                    try:
                        self.progress_slider.setRange(0, max(1, total))
                    except Exception:
                        pass
                    if not self._slider_seeking:  # 用户操作时不被定时器覆盖
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
        # 进度条先用合理范围，视频加载后由 _on_vlc_tick 更新为真实时长
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
        """VLC 加载：关键时序——原生窗口 → 绑定 HWND → 创建媒体(含滤镜) → set_media → play()"""
        if self._closed:
            return
        try:
            if self._vlc_player is None:
                self._load_cv(abs_path)
                return

            # 1. 切换到 VLC widget 并触发原生窗口创建
            if self._vlc_widget:
                try:
                    self._video_stack.setCurrentWidget(self._vlc_widget)
                except Exception:
                    pass
                self._vlc_widget.show()
                self._vlc_widget.winId()   # 强制创建原生句柄

            # 2. 先绑定窗口（play() 之前必须完成）
            self._bind_vlc_window()

            # 3. 停止旧媒体
            try:
                self._vlc_player.stop()
            except Exception:
                pass

            # 4. 创建新媒体（含旋转滤镜，必须在 set_media 前完成）
            media = self._vlc_build_media(abs_path, self._rotation)
            if media is None:
                raise RuntimeError("VLC 无法创建媒体")

            self._vlc_player.set_media(media)

            # 5. 播放
            ret = self._vlc_player.play()
            print(f"[VideoPlayerWindow] VLC play() = {ret}")
            self._is_playing = True
            self.btn_play.setText("暂停")
            # 应用音量（VLC 需要在 play() 之后设置音量才生效）
            QTimer.singleShot(150, self._apply_volume)

        except Exception as e:
            print(f"[VideoPlayerWindow] VLC 加载失败，回退到 OpenCV: {e}")
            self._backend = 'opencv'
            self._load_cv(abs_path)

    def _load_cv(self, abs_path):
        """OpenCV 逐帧播放（VLC 不可用时的回退）"""
        if self._closed:
            return
        try:
            if self._vlc_timer:
                self._vlc_timer.stop()
            if self._cv_timer:
                self._cv_timer.stop()
            if self._cv_cap:
                try:
                    self._cv_cap.release()
                except Exception:
                    pass
                self._cv_cap = None

            try:
                self._video_stack.setCurrentWidget(self._display_label)
            except Exception:
                pass
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
        if self._cv_cap is None or not self._is_playing or self._closed:
            return
        try:
            ret, frame = self._cv_cap.read()
            if not ret or frame is None or frame.size == 0:
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
        try:
            # 确保容器尺寸有效（窗口刚创建时可能为 0）
            cw = self._video_container.width()
            ch = self._video_container.height()
            lw = max(1, cw if cw > 0 else 960)
            lh = max(1, ch if ch > 0 else 540)

            fh, fw = frame.shape[:2]
            scale = min(lw / fw, lh / fh)
            nw = max(1, int(fw * scale))
            nh = max(1, int(fh * scale))

            # 放大也处理（小视频铺满窗口）
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
            h, w = rgb.shape[:2]
            # 深拷贝避免 QImage 引用已释放的 numpy 数组数据
            qimg = QImage(rgb.copy(), w, h, w * 3, QImage.Format_RGB888)
            if qimg.isNull():
                return
            self._display_label.setPixmap(QPixmap.fromImage(qimg))
        except Exception as e:
            print(f"[VideoPlayerWindow] _show_cv_frame 异常: {e}")

    # ------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------
    def toggle_play(self):
        if self._backend == 'vlc':
            if self._vlc_player is None:
                return
            state = self._vlc_player.get_state()
            # VLC 状态：0=NothingSpecial, 1=Opening, 2=Buffering, 3=Playing, 4=Paused, 5=Stopped, 6=Ended, 7=Error
            if state == 3:  # Playing
                self._vlc_player.pause()
                self._is_playing = False
                self.btn_play.setText("播放")
            else:
                if state in (5, 6, 7):  # Stopped/Ended/Error → 重新播放
                    self._vlc_player.play()
                else:
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
        self._rotation = (int(self._rotation) + int(delta_deg)) % 360
        if self.cache_manager and 0 <= self.current_index < len(self.video_paths):
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
        self._closed = True
        # 先停定时器（阻止所有后续 tick）
        if self._vlc_timer:
            self._vlc_timer.stop()
        if self._cv_timer:
            self._cv_timer.stop()
        # 再停止播放
        if self._vlc_player:
            try:
                self._vlc_player.stop()
            except Exception:
                pass
        if self._cv_cap:
            try:
                self._cv_cap.release()
            except Exception:
                pass
        event.accept()
