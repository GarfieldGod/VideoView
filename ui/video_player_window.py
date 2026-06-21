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
    # 最近播放位置发生变更：发射 rel_path（相对 root_folder 的路径）
    last_played_changed = pyqtSignal(str)

    def __init__(self, video_paths, start_index=0, cache_manager=None,
                 parent=None, root_folder=None, on_video_rotated=None,
                 collection_name=None,
                 on_get_next_collection_videos=None,
                 on_get_prev_collection_videos=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self.video_paths = video_paths
        self.current_index = start_index
        self.cache_manager = cache_manager
        self.root_folder = root_folder.replace('\\', '/') if root_folder else None
        self._on_video_rotated_cb = on_video_rotated  # 旋转后通知主窗口刷新缩略图
        self._collection_name = collection_name  # 当前播放的收藏夹名
        # 跨收藏集导航回调：签名 on_get_next_collection_videos() -> (video_list, collection_name) 或 None
        self._on_get_next_collection_videos = on_get_next_collection_videos
        self._on_get_prev_collection_videos = on_get_prev_collection_videos

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
        self._cv_attrs_saved = None  # 记录视频文件原始 Windows 属性（打开隐藏文件时保存）
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
        self._restore_window_state()

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

    def _restore_window_state(self):
        """从 app_config.json 恢复播放器窗口的状态"""
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QDesktopWidget
        from core import get_window_state
        state = get_window_state('player')
        if state is None:
            # 首次打开播放器：默认最大化
            self.showMaximized()
            return
        try:
            w = max(600, state['width'])
            h = max(400, state['height'])
            self.resize(w, h)
            try:
                screen = QDesktopWidget().availableGeometry()
                x = max(screen.left(), min(state['x'], screen.right() - 200))
                y = max(screen.top(), min(state['y'], screen.bottom() - 100))
                self.move(x, y)
            except Exception:
                pass
            if state.get('maximized'):
                self.showMaximized()
            elif state.get('minimized'):
                self.showMinimized()
            else:
                self.show()
        except Exception as e:
            print(f"恢复播放器窗口状态失败: {e}")
            self.showMaximized()

    # ------------------------------------------------------------
    # 后端检测
    # ------------------------------------------------------------
    def _detect_backend(self):
        """在 setup_ui() 之前调用，设置 self._backend。
        不预先创建共享 Instance，改为在 _load_vlc 中根据视频的旋转角度
        动态创建独立的 Instance（Instance 级 --video-filter=transform 才真正生效）。
        """
        from core import has_vlc
        if has_vlc():
            self._backend = 'vlc'
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
        ctrl.setFixedHeight(160)
        ctrl.setStyleSheet("background-color:#1a1a2e;")
        ctrl_layout = QVBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(20, 10, 20, 10)
        ctrl_layout.setSpacing(8)

        time_row = QHBoxLayout()
        self.time_current = QLabel("00:00")
        self.time_current.setStyleSheet("color:#bbb; font-size:26px;")
        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, 1)
        self.progress_slider.setValue(0)
        self.progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self.progress_slider.sliderReleased.connect(self._on_slider_released)
        self.progress_slider.sliderMoved.connect(self._on_slider_moved)
        self.progress_slider.installEventFilter(self)
        self.time_total = QLabel("00:00")
        self.time_total.setStyleSheet("color:#bbb; font-size:26px;")
        time_row.addWidget(self.time_current)
        time_row.addWidget(self.progress_slider, 1)
        time_row.addWidget(self.time_total)

        # 按钮行：从左到右 —— [文件名] [左旋/右旋] [上一个/暂停/下一个（中心）] [收藏] [音量]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        # 最左边：视频文件名
        self.filename_label = QLabel("")
        self.filename_label.setStyleSheet("color:#ddd; font-size:45px; font-weight:bold;")
        self.filename_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.filename_label.setMinimumWidth(400)
        self.filename_label.setMaximumWidth(560)
        btn_row.addWidget(self.filename_label)

        # 左侧弹性空间：把中间的播放控制推向中心
        btn_row.addStretch(1)

        # 中间偏左：旋转按钮
        self.btn_rot_left = self._make_ctrl_btn("↺ 左旋")
        self.btn_rot_left.clicked.connect(lambda: self.rotate_video(-90))
        self.btn_rot_right = self._make_ctrl_btn("↻ 右旋")
        self.btn_rot_right.clicked.connect(lambda: self.rotate_video(90))
        btn_row.addWidget(self.btn_rot_left)
        btn_row.addWidget(self.btn_rot_right)

        # 旋转与播放控制之间留空隙
        btn_row.addSpacing(40)

        # 正中央：上一个 / 暂停 / 下一个
        self.btn_prev = self._make_ctrl_btn("上一个")
        self.btn_prev.clicked.connect(self.play_previous)
        self.btn_play = self._make_ctrl_btn("暂停")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_next = self._make_ctrl_btn("下一个")
        self.btn_next.clicked.connect(self.play_next)
        btn_row.addWidget(self.btn_prev)
        btn_row.addWidget(self.btn_play)
        btn_row.addWidget(self.btn_next)

        # 播放控制与收藏之间留空隙
        btn_row.addSpacing(40)

        # 中间偏右：收藏
        self.btn_fav = self._make_ctrl_btn("♥ 收藏")
        self.btn_fav.clicked.connect(self.toggle_favorite)
        btn_row.addWidget(self.btn_fav)

        # 右侧弹性空间：把音量推到最右边
        btn_row.addStretch(1)

        # 最右边：音量控制（顺序：静音 → 减 → 滑条 → 加 → 数字）
        self.volume_label = QLabel(str(self._volume))
        self.volume_label.setStyleSheet("color:#bbb; font-size:28px; min-width:60px;")
        self.volume_label.setAlignment(Qt.AlignCenter)
        self.btn_mute = self._make_ctrl_btn("🔊", small=True)
        self.btn_mute.clicked.connect(self._toggle_mute)
        self.btn_vol_down = self._make_ctrl_btn("🔉", small=True)
        self.btn_vol_down.clicked.connect(lambda: self._change_volume(-10))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self._volume)
        self.volume_slider.setFixedWidth(140)
        self.volume_slider.setStyleSheet(
            "QSlider::groove:horizontal{background:#2a2a3e;height:8px;border-radius:4px;}"
            "QSlider::handle:horizontal{background:#7a7a9e;width:18px;margin:-6px 0;border-radius:9px;}"
            "QSlider::handle:horizontal:hover{background:#aaaac0;}"
        )
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.btn_vol_up = self._make_ctrl_btn("🔊", small=True)
        self.btn_vol_up.clicked.connect(lambda: self._change_volume(10))
        btn_row.addWidget(self.btn_mute)
        btn_row.addWidget(self.btn_vol_down)
        btn_row.addWidget(self.volume_slider)
        btn_row.addWidget(self.btn_vol_up)
        btn_row.addWidget(self.volume_label)

        ctrl_layout.addLayout(time_row)
        ctrl_layout.addLayout(btn_row)
        main_layout.addWidget(ctrl)

    def _make_ctrl_btn(self, text, small=False):
        """生成底部控制栏按钮，默认两倍大小；small=True 用于小图标按钮。"""
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        if small:
            btn.setFixedSize(60, 70)
            btn.setStyleSheet(
                "QPushButton{background-color:#3a3a5a;color:white;border:none;"
                "border-radius:10px;font-size:22px;font-weight:bold;}"
                "QPushButton:hover{background-color:#5a5a7a;}"
                "QPushButton:pressed{background-color:#2a2a4a;}"
            )
        else:
            btn.setFixedSize(140, 70)
            btn.setStyleSheet(
                "QPushButton{background-color:#3a3a5a;color:white;border:none;"
                "border-radius:10px;font-size:24px;font-weight:bold;}"
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
    def _vlc_create_instance(self, rotation_deg):
        """创建一个全新的 VLC Instance，把旋转角度写进 Instance 级参数。

        这是唯一被广泛验证有效的 VLC 旋转方式：
          --video-filter=transform --transform-type=90 (或 180 / 270)
        """
        try:
            import vlc as _vlc_mod
        except Exception as e:
            print(f"[VideoPlayerWindow] 无法导入 vlc 模块: {e}")
            return None

        deg = int(rotation_deg) % 360
        base = "--no-xlib --quiet --no-video-title-show --no-osd -q"
        if deg == 90:
            args = base + " --video-filter=transform --transform-type=90"
        elif deg == 180:
            args = base + " --video-filter=transform --transform-type=180"
        elif deg == 270:
            args = base + " --video-filter=transform --transform-type=270"
        else:
            args = base

        try:
            inst = _vlc_mod.Instance(args)
        except Exception as e:
            print(f"[VideoPlayerWindow] _vlc_create_instance 异常: {e}")
            inst = None

        if inst is None:
            try:
                inst = _vlc_mod.Instance("--no-xlib --quiet -q")
            except Exception:
                inst = None
            print(f"[VideoPlayerWindow] 回退：创建无旋转参数的 VLC Instance（rotation={deg}° args 不生效）")
        else:
            print(f"[VideoPlayerWindow] VLC Instance 创建成功，rotation={deg}°（args: {args}）")
        return inst

    def _vlc_build_media(self, abs_path, rotation_deg=0):
        """创建 VLC Media —— 旋转参数已经在 Instance 级别设置，这里不需要额外处理。"""
        if self._vlc_inst is None:
            return None
        media = self._vlc_inst.media_new(abs_path)
        return media

    def _on_vlc_tick(self):
        """每 250ms 更新 VLC 进度条"""
        if self._closing or self._vlc_player is None or self._closed:
            return
        try:
            state = self._vlc_player.get_state()
            if state == 7:  # Error
                if self._vlc_timer is not None:
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
        self._update_title()

        if self.cache_manager is not None:
            try:
                raw = self.cache_manager.get_rotation(rel_path)
                self._rotation = int(raw) % 360
                if self._rotation != 0:
                    print(f"[VideoPlayerWindow] 从缓存读取旋转: rel='{rel_path}' raw={raw} deg={self._rotation}")
                else:
                    print(f"[VideoPlayerWindow] 从缓存读取旋转: rel='{rel_path}' raw={raw} (无旋转)")
            except Exception as e:
                print(f"[VideoPlayerWindow] 读取旋转缓存失败: {e}")
                self._rotation = 0
        else:
            print(f"[VideoPlayerWindow] cache_manager 为 None，无法读取旋转缓存")

        # 记录为最近一次播放的视频（写入根目录 .videoview/config.json）
        self._save_last_played(rel_path, abs_path)

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

    def _save_last_played(self, rel_path, abs_path):
        """记录最近播放的视频信息到根目录 .videoview/config.json

        同时发射 last_played_changed 信号，让主窗口的视频预览网格实时刷新高亮。
        """
        if self.cache_manager is None and not hasattr(self, 'root_folder'):
            return
        try:
            if self.cache_manager is not None:
                # 通过 cache_manager 的便捷接口写入
                self.cache_manager.set_last_played(
                    rel_path, abs_path, self._collection_name
                )
        except Exception as e:
            try:
                print(f"[VideoPlayerWindow] 记录最近播放位置失败: {e}")
            except Exception:
                pass
        # 发射信号，让主窗口的视频网格刷新高亮（即使 cache_manager 写失败也尝试）
        try:
            if rel_path:
                self.last_played_changed.emit(str(rel_path))
        except Exception as e:
            try:
                print(f"[VideoPlayerWindow] 发射 last_played_changed 异常: {e}")
            except Exception:
                pass

    def _load_vlc(self, abs_path):
        """VLC 渲染：创建带旋转滤镜的 Media → set_media → set_hwnd → play"""
        if self._closing or self._closed:
            return
        try:
            if self._vlc_timer is not None:
                self._vlc_timer.stop()
            else:
                self._vlc_timer = QTimer(self)
                self._vlc_timer.timeout.connect(self._on_vlc_tick)
            try:
                if self._vlc_player is not None:
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

            # 2a) 销毁旧的 instance/player（释放资源），然后创建带旋转参数的新 VLC Instance
            try:
                if self._vlc_player is not None:
                    try:
                        self._vlc_player.stop()
                    except Exception:
                        pass
                    try:
                        self._vlc_player.release()
                    except Exception:
                        pass
                    self._vlc_player = None
            except Exception:
                pass
            try:
                if self._vlc_inst is not None:
                    try:
                        self._vlc_inst.release()
                    except Exception:
                        pass
                    self._vlc_inst = None
            except Exception:
                pass

            self._vlc_inst = self._vlc_create_instance(self._rotation)
            if self._vlc_inst is None:
                raise RuntimeError("VLC instance 创建失败")
            self._vlc_player = self._vlc_inst.media_player_new()
            if self._vlc_player is None:
                raise RuntimeError("VLC media_player 创建失败")

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
            if self._cv_attrs_saved:
                try:
                    from core.utils import _restore_file_attributes
                    _restore_file_attributes(self._cv_attrs_saved)
                except Exception:
                    pass
                self._cv_attrs_saved = None

            self._video_stack.setCurrentWidget(self._display_label)
            self._display_label.clear()
            self._display_label.setText("加载中...")

            # ---- 兼容 Windows 隐藏/系统属性的视频文件 + 中文/特殊字符路径 ----
            from core.utils import (
                _is_file_entry_readable,
                _restore_file_attributes,
                _as_cv2_safe_path,
            )
            safe_path = _as_cv2_safe_path(abs_path)

            cap = cv2.VideoCapture(safe_path)
            saved_attrs = None
            if cap is None or not cap.isOpened():
                # 常规打开失败，尝试临时去除隐藏/系统属性后重试
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                saved_attrs = _is_file_entry_readable(abs_path)
                cap = cv2.VideoCapture(_as_cv2_safe_path(abs_path))

            if cap is None or not cap.isOpened():
                try:
                    if cap is not None:
                        cap.release()
                except Exception:
                    pass
                # 恢复属性（如果有修改）
                _restore_file_attributes(saved_attrs)
                self._display_label.setText("无法打开视频文件：\n" + os.path.basename(abs_path))
                return
            self._cv_cap = cap
            self._cv_attrs_saved = saved_attrs

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

    def _update_title(self):
        """根据当前视频与收藏夹名更新窗口标题"""
        try:
            if 0 <= self.current_index < len(self.video_paths):
                abs_path = self.video_paths[self.current_index][0]
                file_name = os.path.basename(abs_path)
                parts = []
                if self._collection_name:
                    parts.append(f"[{self._collection_name}]")
                parts.append(f"{self.current_index + 1}/{len(self.video_paths)} {file_name}")
                self.setWindowTitle("  ".join(parts))
                return
        except Exception:
            pass
        self.setWindowTitle("视频播放器")

    def play_previous(self):
        if len(self.video_paths) == 0:
            return
        # 若当前已是第一个视频，则尝试切换到上一个收藏夹的最后一个视频
        if self.current_index == 0 and self._on_get_prev_collection_videos is not None:
            try:
                result = self._on_get_prev_collection_videos()
                if result and len(result) >= 1:
                    prev_videos = result[0]
                    if prev_videos and len(prev_videos) > 0:
                        new_coll_name = result[1] if len(result) >= 2 else None
                        self.video_paths = prev_videos
                        self._collection_name = new_coll_name
                        self.load_video(len(prev_videos) - 1)
                        return
            except Exception as e:
                print(f"[VideoPlayerWindow] 获取上一个收藏集视频列表失败: {e}")
        # 正常：在当前收藏集内循环
        self.load_video((self.current_index - 1) % len(self.video_paths))

    def play_next(self):
        if len(self.video_paths) == 0:
            return
        # 若当前已是最后一个视频，则尝试切换到下一个收藏夹的第一个视频
        if self.current_index == len(self.video_paths) - 1 and self._on_get_next_collection_videos is not None:
            try:
                result = self._on_get_next_collection_videos()
                if result and len(result) >= 1:
                    next_videos = result[0]
                    if next_videos and len(next_videos) > 0:
                        new_coll_name = result[1] if len(result) >= 2 else None
                        self.video_paths = next_videos
                        self._collection_name = new_coll_name
                        self.load_video(0)
                        return
            except Exception as e:
                print(f"[VideoPlayerWindow] 获取下一个收藏集视频列表失败: {e}")
        # 正常：在当前收藏集内循环
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
            self.btn_fav.setText("♥ 已收藏")
            self.btn_fav.setStyleSheet(
                "QPushButton{background-color:#c84040;color:white;border:none;"
                "border-radius:10px;font-size:24px;font-weight:bold;}"
                "QPushButton:hover{background-color:#e85050;}"
                "QPushButton:pressed{background-color:#a03030;}"
            )
        else:
            self.btn_fav.setText("♥ 收藏")
            self.btn_fav.setStyleSheet(
                "QPushButton{background-color:#3a3a5a;color:white;border:none;"
                "border-radius:10px;font-size:24px;font-weight:bold;}"
                "QPushButton:hover{background-color:#5a5a7a;}"
                "QPushButton:pressed{background-color:#2a2a4a;}"
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
            self.volume_slider.setValue(self._volume)
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
        # 第一步：先保存窗口状态到 app_config.json，然后隐藏窗口并异步释放资源
        if not getattr(self, '_closing', False):
            try:
                from core import save_window_state
                save_window_state(self, 'player')
            except Exception as e:
                print(f"保存播放器窗口状态失败: {e}")

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
        # 恢复文件原始 Windows 属性（若是隐藏/系统属性的视频）
        if getattr(self, '_cv_attrs_saved', None):
            try:
                from core.utils import _restore_file_attributes
                _restore_file_attributes(self._cv_attrs_saved)
            except Exception:
                pass
            self._cv_attrs_saved = None
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
