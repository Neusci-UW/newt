"""USB camera: watch the preparation (or the animal) while recording, save a video with
each block, and play it back in step with the traces.

The Camera panel (Acquisition tab, Audio/Video ▾ › Camera) shows a USB camera live.
While Record is on, it also records block_NNN.mp4 next to block_NNN.h5. Its start is
measured against the block's first sample and saved in block_NNN.video.json:
time in the block = time in the video + offset_s. It's close (within about one read
of data, ~50 ms); Video offset nudges it, and the nudge is saved too.

In review, the panel plays the block's video: clicking a plot (or playing the sound,
Audio/Video ▾ › Play) shows the frame at that time.

Qt Multimedia does the work (it comes with PyQt6). macOS asks once for permission to
use the camera.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from pyqtgraph.Qt import QtCore, QtWidgets

try:
    from PyQt6.QtMultimedia import (QCamera, QMediaCaptureSession, QMediaDevices, QMediaPlayer,
                                    QMediaRecorder)
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    _WHY = ""
except Exception as e:                       # Qt built without multimedia
    QCamera = QMediaCaptureSession = QMediaDevices = QMediaPlayer = QMediaRecorder = QVideoWidget = None
    _WHY = f"Video needs Qt Multimedia: {e}"


def available() -> str:
    return _WHY


def cameras() -> list[str]:
    if QMediaDevices is None:
        return []
    return [d.description() for d in QMediaDevices.videoInputs()]


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
        self.camera_box.addItem("Off", "")
        for name in cameras():
            self.camera_box.addItem(name, name)
        self.camera_box.setToolTip("A USB camera (or the built-in one) to watch and record")
        self.record_video = QtWidgets.QCheckBox("Record video with each block")
        self.record_video.setChecked(True)
        lay.addWidget(self.camera_box)
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

        self.camera = None
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
        self.camera_box.currentIndexChanged.connect(lambda *_: self.set_camera(self.camera_box.currentData()))
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

    def set_camera(self, name: str) -> None:
        if self.session is None:
            self.message.setText(_WHY)
            return
        if self.camera is not None:
            self.camera.stop()
            self.camera = None
        self.session.setCamera(None)
        if not name:
            self.message.setText("No camera.")
            return
        dev = next((d for d in QMediaDevices.videoInputs() if d.description() == name), None)
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
        self.message.setText(f"{name}: live. Recorded with each block." if self.record_video.isChecked()
                             else f"{name}: live (not recorded).")

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
        app.requestPermission(perm(), self, lambda *_: self.set_camera(name))
        return False

    @property
    def recording_on(self) -> bool:
        return self.camera is not None and self.record_video.isChecked() and self.recorder is not None

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
        if self.camera is not None:
            self.camera.stop()
        if self.player is not None:
            self.player.stop()
