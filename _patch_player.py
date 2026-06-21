# 1. 定义新版 VideoDecodeThread + VideoPlayerWindow 的完整代码（作为 Python 文本）
NEW_CODE = r"""
class VideoDecodeThread(QThread):
    """独立解码线程：按真实时钟驱动，落后时自动跳帧；预缩放+颜色转换+旋转。"""

    frame_ready = pyqtSignal(object, int)
    duration_changed = pyqtSignal(int)
    stream_ended = pyqtSignal()

    def __init__(self, video_path, target_size_func, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.target_size_func = target_size_func
        self._running = True
        self._paused = False
        self._seek_ms = -1
        self._rotation = 0
        self._pause_lock = threading.Lock()

    def stop(self):
        self._running = False

    def pause(self):
        with self._pause_lock:
            self._paused = True

    def resume(self):
        with self._pause_lock:
            self._paused = False

    def seek(self, ms):
        self._seek_ms = int(ms)

    def set_rotation(self, deg):
        try:
            self._rotation = int(deg) % 360
        except Exception:
            self._rotation = 0

    def run(self):
        try:
            cap = cv2.VideoCapture(self.video_path)
        except Exception as e:
            print(f"[VideoDecodeThread] 打开视频失败: {e}")
            self.stream_ended.emit()
            return
        if not cap.isOpened():
            cap.release()
            self.stream_ended.emit()
            return

        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if fps <= 1 or fps > 240:
                fps = 30.0
            frame_ms = 1000.0 / fps
            total_ms = int(total_frames * frame_ms) if total_frames > 0 else 0
            self.duration_changed.emit(max(1, total_ms))

            elapsed_timer = QElapsedTimer()
            elapsed_timer.start()
            current_frame_idx = 0

            if self._seek_ms >= 0:
                target_frame = int(self._seek_ms / frame_ms) if frame_ms > 0 else 0
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                current_frame_idx = target_frame
                elapsed_timer.restart()
                self._seek_ms = -1

            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                self.stream_ended.emit()
                return
            self._emit_frame(frame, int(current_frame_idx * frame_ms))
            current_frame_idx += 1

            while self._running:
                with self._pause_lock:
                    paused_local = self._paused
                if paused_local:
                    self.msleep(50)
                    elapsed_timer.restart()
                    continue

                if self._seek_ms >= 0:
                    target_ms = self._seek_ms
                    target_frame = int(target_ms / frame_ms) if frame_ms > 0 else 0
                    target_frame = max(0, min(max(0, total_frames - 1), target_frame))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    current_frame_idx = target_frame
                    self._seek_ms = -1
                    elapsed_timer.restart()
                    ret, frame = cap.read()
                    if ret and frame is not None and frame.size > 0:
                        self._emit_frame(frame, int(current_frame_idx * frame_ms))
                        current_frame_idx += 1
                    else:
                        self.stream_ended.emit()
                        break
                    continue

                # 按"真实时钟"决定目标帧索引
                elapsed_ms = elapsed_timer.elapsed()
                target_frame_idx = int(elapsed_ms / frame_ms) if frame_ms > 0 else current_frame_idx

                # 落后太多：grab 跳过解码以追赶
                catches = 0
                while current_frame_idx < target_frame_idx and catches < 600:
                    grabbed = cap.grab()
                    if not grabbed:
                        self.stream_ended.emit()
                        return
                    current_frame_idx += 1
                    catches += 1

                # 解码太快（时钟还没到目标帧）：sleep 避免阻塞 CPU
                if current_frame_idx > target_frame_idx:
                    sleep_ms = int((current_frame_idx - target_frame_idx) * frame_ms) - 1
                    if sleep_ms > 1:
                        self.msleep(min(500, sleep_ms))
                    continue

                # 读取一帧
                ret, frame = cap.read()
                if not ret or frame is None or frame.size == 0:
                    tries = 0
                    while tries < 3 and self._running:
                        ret, frame = cap.read()
                        if ret and frame is not None and frame.size > 0:
                            break
                        tries += 1
                        self.msleep(20)
                    if not ret or frame is None or frame.size == 0:
                        self.stream_ended.emit()
                        return

                self._emit_frame(frame, int(current_frame_idx * frame_ms))
                current_frame_idx += 1
        except Exception as e:
            print(f"[VideoDecodeThread] 解码异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            try:
                cap.release()
            except Exception:
                pass

    def _emit_frame(self, frame, pos_ms):
        try:
            try:
                target_w, target_h = self.target_size_func()
            except Exception:
                target_w, target_h = 960, 540
            if target_w < 320:
                target_w = 320
            if target_h < 200:
                target_h = 200

            rotation = self._rotation % 360

            fh, fw = frame.shape[:2]
            if fw == 0 or fh == 0:
                return

            if rotation in (90, 270):
                display_w, display_h = target_h, target_w
            else:
                display_w, display_h = target_w, target_h

            scale = min(display_w / fw, display_h / fh)
            new_w = max(1, int(fw * scale))
            new_h = max(1, int(fh * scale))

            if new_w < fw or new_h < fh:
                small = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
            else:
                small = frame

            if rotation == 90:
                small = cv2.rotate(small, cv2.ROTATE_90_CLOCKWISE)
            elif rotation == 180:
                small = cv2.rotate(small, cv2.ROTATE_180)
            elif rotation == 270:
                small = cv2.rotate(small, cv2.ROTATE_90_COUNTERCLOCKWISE)

            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            h_rgb, w_rgb = rgb.shape[0], rgb.shape[1]
            bytes_per_line = w_rgb * 3
            qimg = QImage(rgb.data, w_rgb, h_rgb, bytes_per_line, QImage.Format_RGB888).copy()
            self.frame_ready.emit(qimg, pos_ms)
        except Exception as e:
            print(f"[VideoDecodeThread] 帧处理异常: {e}")


class VideoPlayerWindow(QMainWindow):
    """应用内播放器：子线程解码+预缩放+旋转，主线程仅显示。"""

    favorite_changed = pyqtSignal(str, bool)

    _sig_stop = pyqtSignal()
    _sig_seek = pyqtSignal(int)
    _sig_pause = pyqtSignal()
    _sig_resume = pyqtSignal()
    _sig_rotation = pyqtSignal(int)

    def __init__(self, video_paths, start_index=0, cache_manager=None, parent=None, root_folder=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.video_paths = video_paths
        self.current_index = start_index
        self.cache_manager = cache_manager
        self.root_folder = root_folder.replace('\\', '/') if root_folder else None

        self._decode_thread = None
        self._total_ms = 1
        self._current_ms = 0
        self._is_playing = False
        self._dragging = False

        self.setup_ui()
        QTimer.singleShot(80, lambda: self.load_video(self.current_index))

    def setup_ui(self):
        self.setWindowTitle("视频播放器（应用内播放）")
        self.resize(1280, 820)

        central = QWidget()
        central.setStyleSheet("background-color:#000000;")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color:#000000; color:#888; font-size:22px;")
        self.video_label.setMinimumSize(640, 360)
        self.video_label.setText("加载中...")
        main_layout.addWidget(self.video_label, 1)

        control_bar = QWidget()
        control_bar.setFixedHeight(110)
        control_bar.setStyleSheet("background-color:#1a1a2e;")
        bar_layout = QVBoxLayout(control_bar)
        bar_layout.setContentsMargins(16, 8, 16, 8)
        bar_layout.setSpacing(4)

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

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_prev = self._make_button("⏮ 上一个")
        self.btn_prev.clicked.connect(self.play_previous)

        self.btn_play = self._make_button("⏸ 暂停")
        self.btn_play.clicked.connect(self.toggle_play)

        self.btn_next = self._make_button("下一个 ⏭")
        self.btn_next.clicked.connect(self.play_next)

        self.btn_rot_left = self._make_button("⟲ 左旋 90°")
        self.btn_rot_left.clicked.connect(lambda: self.rotate_frame(-90))

        self.btn_rot_right = self._make_button("⟳ 右旋 90°")
        self.btn_rot_right.clicked.connect(lambda: self.rotate_frame(90))

        self.btn_fav = self._make_button("♡ 收藏")
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

        bar_layout.addLayout(time_row)
        bar_layout.addLayout(btn_row)
        main_layout.addWidget(control_bar)

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

    def _target_size(self):
        sz = self.video_label.size()
        return max(1, sz.width()), max(1, sz.height())

    def load_video(self, index):
        if not (0 <= index < len(self.video_paths)):
            return
        self.current_index = index
        abs_path, rel_path = self.video_paths[index]
        self.filename_label.setText(os.path.basename(abs_path))
        self._update_fav_button()

        self._stop_thread()

        self.progress_slider.setValue(0)
        self.progress_slider.setRange(0, 1000)
        self.time_current.setText("00:00")
        self.time_total.setText("00:00")
        self.video_label.setText("加载中...")
        self.video_label.setStyleSheet("background-color:#000000; color:#888; font-size:22px;")
        self._is_playing = True
        self.btn_play.setText("⏸ 暂停")

        thread = VideoDecodeThread(abs_path, self._target_size, self)
        thread.frame_ready.connect(self._on_frame_ready)
        thread.duration_changed.connect(self._on_duration_changed)
        thread.stream_ended.connect(self._on_stream_ended)

        self._sig_stop.connect(thread.stop)
        self._sig_seek.connect(thread.seek)
        self._sig_pause.connect(thread.pause)
        self._sig_resume.connect(thread.resume)
        self._sig_rotation.connect(thread.set_rotation)

        if self.cache_manager is not None:
            rot = self.cache_manager.get_rotation(rel_path)
            self._sig_rotation.emit(rot)

        self._decode_thread = thread
        thread.start()

    def _stop_thread(self):
        t = self._decode_thread
        if t is not None:
            try:
                self._sig_stop.emit()
            except Exception:
                pass
            try:
                t.wait(600)
            except Exception:
                pass
            try:
                t.deleteLater()
            except Exception:
                pass
        self._decode_thread = None

    @pyqtSlot(object, int)
    def _on_frame_ready(self, qimg, pos_ms):
        try:
            pixmap = QPixmap.fromImage(qimg)
            self.video_label.setPixmap(pixmap)
            self.video_label.setStyleSheet("background-color:#000000;")
            self._current_ms = pos_ms
            if not self._dragging:
                self.progress_slider.setValue(pos_ms)
                self.time_current.setText(self._format_ms(pos_ms))
        except Exception as e:
            print(f"[VideoPlayerWindow] 渲染异常: {e}")

    @pyqtSlot(int)
    def _on_duration_changed(self, total_ms):
        self._total_ms = max(1, total_ms)
        self.progress_slider.setRange(0, self._total_ms)
        self.time_total.setText(self._format_ms(self._total_ms))

    @pyqtSlot()
    def _on_stream_ended(self):
        QTimer.singleShot(200, self.play_next)

    def toggle_play(self):
        if self._decode_thread is None:
            return
        if self._is_playing:
            self._is_playing = False
            self.btn_play.setText("▶ 播放")
            self._sig_pause.emit()
        else:
            self._is_playing = True
            self.btn_play.setText("⏸ 暂停")
            self._sig_resume.emit()

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
                "QPushButton{background-color:#c84040;color:white;border:2px solid #e86060;"
                "border-radius:5px;font-size:13px;padding:4px 10px;font-weight:bold;}"
                "QPushButton:hover{background-color:#e85050;}"
            )
        else:
            self.btn_fav.setText("♡ 收藏")
            self.btn_fav.setStyleSheet(
                "QPushButton{background-color:#3a3a5a;color:white;border:2px solid #5a5a7a;"
                "border-radius:5px;font-size:13px;padding:4px 10px;font-weight:bold;}"
                "QPushButton:hover{background-color:#5a5a7a;}"
            )

    def rotate_frame(self, delta_deg):
        if not self.cache_manager or not (0 <= self.current_index < len(self.video_paths)):
            return
        _, rel_path = self.video_paths[self.current_index]
        if delta_deg < 0:
            new_deg = self.cache_manager.rotate_left(rel_path)
        else:
            new_deg = self.cache_manager.rotate_right(rel_path)
        self._sig_rotation.emit(new_deg)

    def _on_slider_pressed(self):
        self._dragging = True

    def _on_slider_released(self):
        self._dragging = False
        target_ms = int(self.progress_slider.value())
        self._sig_seek.emit(target_ms)

    def _on_slider_moved(self, position):
        self.time_current.setText(self._format_ms(position))

    def _format_ms(self, ms):
        ms = max(0, int(ms))
        total_seconds = ms // 1000
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def keyPressEvent(self, event):
        try:
            key = event.key()
            if key == Qt.Key_Escape:
                self.close()
            elif key == Qt.Key_Space:
                self.toggle_play()
            elif key == Qt.Key_Left:
                self._sig_seek.emit(max(0, self._current_ms - 5000))
            elif key == Qt.Key_Right:
                self._sig_seek.emit(min(self._total_ms, self._current_ms + 5000))
            elif key == Qt.Key_N:
                self.play_next()
            elif key == Qt.Key_P:
                self.play_previous()
            elif key == Qt.Key_F:
                self.toggle_favorite()
            elif key == Qt.Key_Q:
                self.rotate_frame(-90)
            elif key == Qt.Key_E:
                self.rotate_frame(90)
            else:
                super().keyPressEvent(event)
        except Exception as e:
            print(f"[VideoPlayerWindow] 键盘事件异常: {e}")

    def closeEvent(self, event):
        self._stop_thread()
        event.accept()
"""

import sys
import os

main_path = r'f:\MyProject\God\Project\AIProject\MyVideo\main.py'

with open(main_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start = None
end = None
for i, line in enumerate(lines):
    if start is None and line.startswith('class VideoDecodeThread('):
        start = i
        continue
    if start is not None and i > start:
        stripped = line.lstrip()
        if stripped.startswith('class ') and not stripped.startswith('class  '):
            end = i
            break

if start is None:
    print("ERROR: 找不到 VideoDecodeThread")
    sys.exit(1)
if end is None:
    end = len(lines)

print(f"替换行 {start+1} - {end}")
print(f"  首行: {lines[start].rstrip()}")
print(f"  末行: {lines[end-1].rstrip()}")
print(f"  保留行: {lines[end].rstrip() if end < len(lines) else '(EOF)'}")

# NEW_CODE 是整段 Python 文本，前面可能有一个空行
# 我们把它切成行列表，确保前导空行被剥离
new_lines = NEW_CODE.split('\n')
# 去掉开头的空行
while new_lines and new_lines[0].strip() == '':
    new_lines.pop(0)
# 每行末尾加上换行符（除了最后一行），保持与原文件一致
# 为了安全：让拼接后保持行尾换行符
new_lines = [ln + '\n' if not ln.endswith('\n') else ln for ln in new_lines]

# 合并结果：lines[:start] + new_lines + lines[end:]
result = lines[:start] + new_lines + lines[end:]

with open(main_path, 'w', encoding='utf-8') as f:
    f.writelines(result)

print(f"完成：写入 {len(result)} 行")

