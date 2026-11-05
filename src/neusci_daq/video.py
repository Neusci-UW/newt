"""USB camera: watch the preparation (or the animal) while recording, save a video with
each block, and play it back in step with the traces.

The Camera panel (Acquisition tab, Audio/Video ▾ › Camera) shows a USB camera live.
While Record is on, it also records block_NNN.mp4 next to block_NNN.h5. Its start is
measured against the block's first sample and saved in block_NNN.video.json:
time in the block = time in the video + offset_s. It's close (within about one read
of data, ~50 ms); Video offset nudges it, and the nudge is saved too.

In review, the panel plays the block's video: clicking a plot (or playing the sound,
Audio/Video ▾ › Play) shows the frame at that time.

Qt Multimedia does the work (it comes with PyQt6). It sees the cameras the operating
system lists as cameras (on Windows, the ones the Camera app can open), and the list
updates when one is plugged in or out. AmScope microscope cameras that Windows doesn't
list (the MU series) come through AmScope's own library instead (amscope.py): their
frames are handed to the same recorder, so they're saved and lined up the same way.
macOS asks once for permission to use the camera.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import amscope

try:
    from PyQt6.QtMultimedia import (QCamera, QMediaCaptureSession, QMediaDevices, QMediaPlayer,
                                    QMediaRecorder)
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    _WHY = ""
except Exception as e:                       # Qt built without multimedia
    QCamera = QMediaCaptureSession = QMediaDevices = QMediaPlayer = QMediaRecorder = QVideoWidget = None
    _WHY = f"Video needs Qt Multimedia: {e}"
try:                                         # Qt 6.8+: frames from elsewhere (AmScope cameras)
    from PyQt6.QtMultimedia import QVideoFrame, QVideoFrameInput
except Exception:
    QVideoFrame = QVideoFrameInput = None

QT, AMSCOPE = "qt:", "amscope:"              # what kind of camera a list entry is
LIVE_WIDTH = 2000                            # AmScope: start at the biggest size up to this wide


def available() -> str:
    return _WHY


def cameras() -> list[str]:
    if QMediaDevices is None:
        return []
    return [d.description() for d in QMediaDevices.videoInputs()]


def _camera_list() -> list[tuple[str, str]]:
    """[(name shown, "qt:…" or "amscope:…" id)]: the cameras Qt sees, then AmScope ones
    (only with Qt 6.8+, which can record their frames). Two cameras with the same name
    get "(2)" and so on."""
    found = []
    if QMediaDevices is not None:
        found += [(d.description(), QT + bytes(d.id()).hex()) for d in QMediaDevices.videoInputs()]
    if QVideoFrameInput is not None:
        found += [(f"{name} (AmScope)", AMSCOPE + dev_id) for name, dev_id in amscope.cameras()]
    out, seen = [], {}
    for name, dev_id in found:
        seen[name] = seen.get(name, 0) + 1
        out.append((name if seen[name] == 1 else f"{name} ({seen[name]})", dev_id))
    return out


NONE_FOUND = ("No camera found. Plug it in and it appears here. If it's plugged in: does the "
              "Windows Camera app show it? Some microscope cameras (many AmScope models) only work "
              "with their own software and aren't listed as cameras. Close that software if it's "
              "open: only one program can use a camera at a time.")


def sidecar(block: Path) -> Path:
    return Path(block).with_suffix(".video.json")


def video_of(block: Path) -> tuple[Path, float] | None:
    """(video file, offset_s) recorded with this block, or None."""
    meta = sidecar(block)
    if not meta.exists():
        return None
    try:
        d = json.loads(meta.read_text())
        f = Path(block).parent / d["file"]
        return (f, float(d.get("offset_s", 0.0)) + float(d.get("nudge_s", 0.0))) if f.exists() else None
    except (ValueError, KeyError, OSError):
        return None


def save_nudge(block: Path, nudge_s: float) -> None:
    meta = sidecar(block)
    d = json.loads(meta.read_text())
    d["nudge_s"] = float(nudge_s)
    meta.write_text(json.dumps(d, indent=1))


def video_ms(t_block: float, offset_s: float) -> int:
    """Where in the video (ms) a time in the block (s) is."""
    return max(0, int(round((t_block - offset_s) * 1000)))


class CameraPanel(QtWidgets.QFrame):
    """The camera, live or played back. The main window calls start_block/stop_block
    around each block, show_block in review, and seek(t) to show a time."""

    closed = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("commentsPanel")
        self.setMinimumWidth(320)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        top = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel("<b>Camera</b>")
        close = QtWidgets.QToolButton(text="✕", autoRaise=True)
        close.clicked.connect(self.closed.emit)
        top.addWidget(self.title, 1)
        top.addWidget(close)
        lay.addLayout(top)
        self.camera_box = QtWidgets.QComboBox()
        self.camera_box.setToolTip("A USB camera (or the built-in one) to watch and record")
        self.btn_refresh = QtWidgets.QToolButton(text="↻")
        self.btn_refresh.setToolTip("Look for cameras again")
        self.btn_refresh.setAccessibleName("Look for cameras again")
        self.record_video = QtWidgets.QCheckBox("Record video with each block")
        self.record_video.setChecked(True)
        pick = QtWidgets.QHBoxLayout()
        pick.addWidget(self.camera_box, 1)
        pick.addWidget(self.btn_refresh)
        lay.addLayout(pick)
        # AmScope cameras: their sizes. Smaller ones give more frames per second.
        self.size_row = QtWidgets.QWidget()
        sr = QtWidgets.QHBoxLayout(self.size_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(QtWidgets.QLabel("Size"))
        self.size_box = QtWidgets.QComboBox()
        self.size_box.setToolTip("The camera's resolution. Smaller sizes give smoother video.")
        sr.addWidget(self.size_box, 1)
        self.size_row.hide()
        lay.addWidget(self.size_row)
        lay.addWidget(self.record_video)
        self.view = QVideoWidget() if QVideoWidget is not None else QtWidgets.QLabel(_WHY)
        self.view.setMinimumHeight(200)
        lay.addWidget(self.view, 1)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Video offset"))
        self.nudge = QtWidgets.QDoubleSpinBox(minimum=-10, maximum=10, decimals=3, singleStep=0.01, suffix=" s")
        self.nudge.setToolTip("Shift the video against the traces if they're out of step (saved with the block)")
        row.addWidget(self.nudge)
        lay.addLayout(row)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: gray")
        lay.addWidget(self.message)

        self.camera = None                       # a Qt camera…
        self.amcam = None                        # …or an AmScope one (amscope.Camera)
        self.frame_input = QVideoFrameInput() if QVideoFrameInput is not None else None
        self._pull = QtCore.QTimer(self)         # AmScope: fetch its newest frame
        self._pull.setInterval(15)
        self._pull.timeout.connect(self._amcam_frame)
        self._frames_t0 = None                   # monotonic time of frame 0 (timestamps)
        self.session = QMediaCaptureSession() if QMediaCaptureSession is not None else None
        self.recorder = QMediaRecorder() if QMediaRecorder is not None else None
        self.player = QMediaPlayer() if QMediaPlayer is not None else None
        if self.session is not None:
            self.session.setRecorder(self.recorder)
            self.recorder.durationChanged.connect(self._first_frames)
            self.recorder.errorChanged.connect(lambda: self.recorder.errorString() and
                                               self.message.setText(f"Video: {self.recorder.errorString()}"))
        if self.player is not None and QVideoWidget is not None:
            self.player.setVideoOutput(self.view)
        self._block: Path | None = None          # recording (live) or shown (review)
        self._block_t0 = None                    # monotonic time of the block's first sample
        self._video_t0 = None
        self._offset = 0.0
        self._devices = QMediaDevices() if QMediaDevices is not None else None
        self.fill_cameras()
        if self._devices is not None:          # a camera plugged in or out while Argus is open
            self._devices.videoInputsChanged.connect(self.fill_cameras)
        self.btn_refresh.clicked.connect(self.fill_cameras)
        self.camera_box.currentIndexChanged.connect(lambda *_: self.set_camera(self.camera_box.currentData()))
        self.size_box.activated.connect(self._size_picked)
        self.nudge.valueChanged.connect(self._nudged)
        self.set_live(True)

    # -- live --------------------------------------------------------------------------------
    def set_live(self, live: bool) -> None:
        """Live: the camera; review: the block's video."""
        self.live = live
        self.camera_box.setVisible(live)
        self.record_video.setVisible(live)
        self.nudge.setEnabled(not live)
        if self.session is None:
            return
        if live:
            if self.player is not None:
                self.player.stop()
            self.session.setVideoOutput(self.view)
        else:
            self.session.setVideoOutput(None)
            if self.player is not None:
                self.player.setVideoOutput(self.view)
        self.title.setText("<b>Camera</b>" if live else "<b>Video</b>")

    def fill_cameras(self) -> None:
        """List the cameras connected now, keeping the one in use if it's still there."""
        found = _camera_list()
        current = self.camera_box.currentData() or ""
        live = getattr(self, "live", True)
        self.camera_box.blockSignals(True)
        self.camera_box.clear()
        self.camera_box.addItem("Off", "")
        for name, dev_id in found:
            self.camera_box.addItem(name, dev_id)
        keep = self.camera_box.findData(current) if current else 0
        self.camera_box.setCurrentIndex(max(0, keep))
        self.camera_box.blockSignals(False)
        if current and keep < 0:                 # the camera in use was unplugged
            self.set_camera("")
            self.message.setText("The camera was unplugged.")
        elif not found and live:
            self.message.setText(_WHY or NONE_FOUND + (f" ({amscope.why_not()})" if amscope.why_not() else ""))
        elif not current and live:
            self.message.setText(f"{len(found)} camera{'s' if len(found) != 1 else ''} found: pick one above.")

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.fill_cameras()                      # Windows doesn't announce AmScope cameras

    def _stop_camera(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.camera = None
        if self.amcam is not None:
            self._pull.stop()
            try:
                self.amcam.close()
            except OSError:
                pass
            self.amcam = None
        self.size_row.hide()
        if self.session is not None:
            self.session.setCamera(None)
            if self.frame_input is not None:
                self.session.setVideoFrameInput(None)

    def set_camera(self, dev_id: str) -> None:
        """Use the camera with this id (from the list: "qt:…" or "amscope:…"), or none for ""."""
        if self.session is None:
            self.message.setText(_WHY)
            return
        self._stop_camera()
        if not dev_id:
            self.message.setText("No camera.")
            return
        name = self.camera_box.currentText()
        if dev_id.startswith(AMSCOPE):
            self._open_amscope(dev_id[len(AMSCOPE):], name)
            return
        qt_id = dev_id[len(QT):] if dev_id.startswith(QT) else dev_id
        dev = next((d for d in QMediaDevices.videoInputs() if bytes(d.id()).hex() == qt_id), None)
        if dev is None:
            self.message.setText(f"{name} isn't connected.")
            return
        if not self._allowed(name):
            return
        self.camera = QCamera(dev)
        self.camera.errorChanged.connect(lambda: self.camera is not None and self.camera.errorString() and
                                         self.message.setText(f"Camera: {self.camera.errorString()}"))
        self.session.setCamera(self.camera)
        self.session.setVideoOutput(self.view)
        self.camera.start()
        self._say_live(name)

    def _say_live(self, name: str) -> None:
        self.message.setText(f"{name}: live. Recorded with each block." if self.record_video.isChecked()
                             else f"{name}: live (not recorded).")

    # -- AmScope cameras ---------------------------------------------------------------------
    def _open_amscope(self, dev_id: str, name: str) -> None:
        try:
            cam = amscope.Camera(dev_id)
            sizes = cam.resolutions()
            fits = [i for i, (w, _) in enumerate(sizes) if w <= LIVE_WIDTH]
            pick = max(fits, key=lambda i: sizes[i][0]) if fits else (len(sizes) - 1 if sizes else 0)
            if sizes:
                cam.set_resolution(pick)
            cam.start()
        except OSError as e:
            self.message.setText(f"{name}: {e}")
            return
        self.amcam = cam
        self.size_box.clear()
        for w, h in sizes:
            self.size_box.addItem(f"{w} × {h}")
        self.size_box.setCurrentIndex(pick)
        self.size_row.setVisible(bool(sizes))
        self.session.setVideoFrameInput(self.frame_input)
        self.session.setVideoOutput(self.view)
        self._frames_t0 = None
        self._pull.start()
        self._say_live(name)

    def _size_picked(self, index: int) -> None:
        if self.amcam is None or index < 0:
            return
        if self._block is not None and self.live:
            self.message.setText("Change the size between blocks, not while recording one.")
            self.size_box.setCurrentIndex(self.amcam.resolution())
            return
        try:
            self.amcam.set_resolution(index)
        except OSError as e:
            self.message.setText(str(e))

    def _amcam_frame(self) -> None:
        """Hand the camera's newest frame to the preview and, while recording, the video."""
        cam = self.amcam
        if cam is None:
            return
        if cam.lost:
            self._stop_camera()
            self.fill_cameras()
            self.message.setText("The AmScope camera stopped: was it unplugged?")
            return
        try:
            rgb = cam.latest()
        except OSError:
            rgb = None
        if rgb is None:
            return
        now = time.monotonic()
        if self._frames_t0 is None:
            self._frames_t0 = now
        if self._block is not None and self.live and self._video_t0 is None:
            self._video_t0 = now                 # the recording's first frame: lines it up
            self._frames_t0 = now                # and its timestamps start at 0
        h, w, _ = rgb.shape
        img = QtGui.QImage(rgb.data, w, h, 3 * w, QtGui.QImage.Format.Format_RGB888).copy()
        frame = QVideoFrame(img)
        us = int((now - self._frames_t0) * 1e6)
        frame.setStartTime(us)
        frame.setEndTime(us + 1)
        self.frame_input.sendVideoFrame(frame)   # False if the recorder is behind: skip it

    def _allowed(self, name: str) -> bool:
        """macOS asks once to allow the camera; until it's answered, try again after."""
        app = QtCore.QCoreApplication.instance()
        perm = getattr(QtCore, "QCameraPermission", None)
        if perm is None or not hasattr(app, "checkPermission"):
            return True
        status = app.checkPermission(perm())
        if status == QtCore.Qt.PermissionStatus.Granted:
            return True
        if status == QtCore.Qt.PermissionStatus.Denied:
            self.message.setText("The camera isn't allowed: turn it on in System Settings › Privacy & "
                                 "Security › Camera, for the app running this.")
            return False
        self.message.setText("Allow the camera when asked…")
        app.requestPermission(perm(), self, lambda *_: self.set_camera(self.camera_box.currentData() or ""))
        return False

    @property
    def recording_on(self) -> bool:
        return ((self.camera is not None or self.amcam is not None)
                and self.record_video.isChecked() and self.recorder is not None)

    def start_block(self, block: Path, block_t0: float) -> bool:
        """Record video with this block (block_t0: monotonic time of its first sample)."""
        if not self.recording_on:
            return False
        self._block, self._block_t0, self._video_t0 = Path(block), block_t0, None
        self.recorder.setOutputLocation(QtCore.QUrl.fromLocalFile(str(Path(block).with_suffix(".mp4"))))
        self.recorder.record()
        return True

    def _first_frames(self, ms: int) -> None:
        if self._video_t0 is None and ms > 0 and self._block_t0 is not None:
            self._video_t0 = time.monotonic() - ms / 1000      # when the video's first frame was

    def stop_block(self) -> Path | None:
        """Stop the block's video and note how it lines up. Returns the video file."""
        if self._block is None or self.recorder is None:
            return None
        self.recorder.stop()
        out = Path(self.recorder.actualLocation().toLocalFile() or self._block.with_suffix(".mp4"))
        offset = (self._video_t0 - self._block_t0) if self._video_t0 is not None else 0.0
        sidecar(self._block).write_text(json.dumps({"file": out.name, "offset_s": offset, "nudge_s": 0.0},
                                                   indent=1))
        self._block = None
        return out

    # -- review ------------------------------------------------------------------------------
    def show_block(self, block: Path | None) -> bool:
        """Play back this block's video (if it has one)."""
        self._block = Path(block) if block else None
        found = video_of(block) if block else None
        if found is None:
            self.message.setText("No video was recorded with this block.")
            if self.player is not None:
                self.player.setSource(QtCore.QUrl())
            return False
        path, self._offset = found
        d = json.loads(sidecar(block).read_text())
        self.nudge.blockSignals(True)
        self.nudge.setValue(float(d.get("nudge_s", 0.0)))
        self.nudge.blockSignals(False)
        if self.player is not None:
            self.player.setSource(QtCore.QUrl.fromLocalFile(str(path)))
            self.player.pause()
        self.message.setText(f"{path.name}. Click a plot to see that moment.")
        return True

    def seek(self, t_block: float) -> None:
        if self.player is not None and self._block is not None and not self.live:
            self.player.setPosition(video_ms(t_block, self._offset))

    def play_from(self, t_block: float) -> None:
        if self.player is not None and self._block is not None and not self.live:
            self.seek(t_block)
            self.player.play()

    def pause(self) -> None:
        if self.player is not None:
            self.player.pause()

    def _nudged(self, v: float) -> None:
        if self._block is None or self.live or video_of(self._block) is None:
            return
        save_nudge(self._block, v)
        self._offset = video_of(self._block)[1]

    def shutdown(self) -> None:
        self._stop_camera()
        if self.player is not None:
            self.player.stop()
