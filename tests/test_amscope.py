"""AmScope cameras through their own library: the ctypes wrapper (against a stand-in
library compiled from fakeamcam.c, where there's a C compiler) and the camera panel
(with a stand-in camera)."""
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import pytest

from neusci_daq import amscope, video

from test_channels import app, make_cfg, pump, win  # noqa: F401  (fixtures)

HERE = os.path.dirname(__file__)


# ---------------------------------------------------------------- the wrapper, against a C stand-in
@pytest.fixture
def fake_sdk(tmp_path, monkeypatch):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if sys.platform == "win32" or cc is None:
        pytest.skip("needs a C compiler (and the stand-in uses char strings, not Windows' wchar_t)")
    lib = tmp_path / ("libamcam.dylib" if sys.platform == "darwin" else "libamcam.so")
    subprocess.run([cc, "-shared", "-fPIC", "-O2", "-o", str(lib), os.path.join(HERE, "fakeamcam.c"),
                    "-lpthread"], check=True)
    monkeypatch.setenv("NEWT_CAMERA_SDK", str(lib))
    amscope.reset()
    yield lib
    amscope.reset()


def test_lists_opens_and_pulls_frames(fake_sdk):
    assert amscope.cameras() == [("MU1000", "tp-1-2-1234")] and amscope.why_not() == ""
    cam = amscope.Camera("tp-1-2-1234")
    try:
        assert cam.resolutions() == [(3584, 2748), (1792, 1374), (896, 684)]
        cam.set_resolution(2)
        cam.start()
        deadline = time.time() + 2
        frame = None
        while frame is None and time.time() < deadline:
            frame = cam.latest()
            time.sleep(0.005)
        assert frame is not None and frame.shape == (684, 896, 3) and frame.dtype == np.uint8
        assert frame[5, 7, 0] == 5 and frame[5, 7, 1] == 7            # the right pixel, unpadded
        first = frame[0, 0, 2]
        time.sleep(0.1)
        later = cam.latest()
        assert later[0, 0, 2] != first and frame[0, 0, 2] == first   # a copy, not overwritten
        cam.set_resolution(1)                                         # restarts the stream
        time.sleep(0.1)
        assert cam.latest().shape == (1374, 1792, 3)
    finally:
        cam.close()
    assert not cam.running


def test_a_wrong_id_or_no_library_says_why(fake_sdk, monkeypatch):
    with pytest.raises(OSError, match="didn't open"):
        amscope.Camera("not-a-camera")
    monkeypatch.setenv("NEWT_CAMERA_SDK", str(fake_sdk.parent / "missing.dll"))
    monkeypatch.setattr(amscope, "_candidates", lambda: [(fake_sdk.parent / "missing.dll", "Amcam_")])
    amscope.reset()
    assert amscope.cameras() == [] and "wasn't found" in amscope.why_not()


# ---------------------------------------------------------------- the panel, with a stand-in camera
class FakeCamera:
    """What CameraPanel uses of amscope.Camera."""
    opened: list = []

    def __init__(self, dev_id):
        if dev_id != "tp-1":
            raise OSError("The camera didn't open: is AmScope's own software using it?")
        self.sizes = [(3584, 2748), (1792, 1374), (896, 684)]
        self.size = 0
        self.running = self.closed = self.lost = False
        self.n = 0
        FakeCamera.opened.append(self)

    def resolutions(self):
        return self.sizes

    def resolution(self):
        return self.size

    def set_resolution(self, i):
        self.size = i

    def start(self):
        self.running = True

    def latest(self):
        if not self.running:
            return None
        self.n += 1
        w, h = self.sizes[self.size]
        img = np.zeros((h // 8, w // 8, 3), np.uint8)               # small, to keep the test quick
        img[..., 0] = self.n % 255
        return img

    def stop(self):
        self.running = False

    def close(self):
        self.stop()
        self.closed = True


@pytest.fixture
def amscope_cam(monkeypatch):
    if video.QVideoFrameInput is None:
        pytest.skip("AmScope cameras need Qt 6.8 or later")
    FakeCamera.opened = []
    monkeypatch.setattr(amscope, "cameras", lambda: [("MU1000", "tp-1")])
    monkeypatch.setattr(amscope, "Camera", FakeCamera)
    yield FakeCamera


def pick(panel, text):
    i = panel.camera_box.findText(text)
    assert i > 0, [panel.camera_box.itemText(k) for k in range(panel.camera_box.count())]
    panel.camera_box.setCurrentIndex(i)


def test_amscope_camera_is_listed_and_starts_at_a_live_size(app, win, amscope_cam):
    panel = win.camera_panel
    panel.fill_cameras()
    pick(panel, "MU1000 (AmScope)")
    cam = amscope_cam.opened[-1]
    assert cam.running and cam.size == 1                            # 1792 wide: the biggest ≤ 2000
    assert not panel.size_row.isHidden() and panel.size_box.currentText() == "1792 × 1374"
    assert "live" in panel.message.text() and panel.recording_on
    panel.size_box.setCurrentIndex(2)
    panel.size_box.activated.emit(2)
    assert cam.size == 2
    panel.camera_box.setCurrentIndex(0)                              # Off
    assert cam.closed and panel.size_row.isHidden() and not panel.recording_on


def test_amscope_frames_reach_the_preview(app, win, amscope_cam):
    from PyQt6.QtMultimedia import QVideoSink
    panel = win.camera_panel
    sink = QVideoSink()
    got = []
    sink.videoFrameChanged.connect(lambda f: got.append((f.width(), f.height())))
    panel.fill_cameras()
    pick(panel, "MU1000 (AmScope)")
    panel.session.setVideoOutput(sink)
    pump(app, 0.3)
    assert got and got[-1] == (1792 // 8, 1374 // 8)
    panel.set_camera("")


def test_an_unplugged_amscope_camera_is_closed_and_said(app, win, amscope_cam, monkeypatch):
    panel = win.camera_panel
    panel.fill_cameras()
    pick(panel, "MU1000 (AmScope)")
    cam = amscope_cam.opened[-1]
    monkeypatch.setattr(amscope, "cameras", lambda: [])
    cam.lost = True
    pump(app, 0.1)
    assert cam.closed and panel.amcam is None and "unplugged" in panel.message.text()


def test_amscope_video_is_recorded_with_the_block_and_lined_up(app, win, amscope_cam, tmp_path):
    panel = win.camera_panel
    panel.fill_cameras()
    pick(panel, "MU1000 (AmScope)")
    pump(app, 0.2)                                                   # previewing first
    block = tmp_path / "block_001.h5"
    t0 = time.monotonic()
    assert panel.start_block(block, t0)
    pump(app, 1.2)
    out = panel.stop_block()
    pump(app, 1.5)                                                   # the file is finished off
    meta = json.loads(video.sidecar(block).read_text())
    assert meta["file"] == "block_001.mp4"
    assert 0 <= meta["offset_s"] < 0.5                               # first frame soon after t0
    assert out.exists() and out.stat().st_size > 1000
    panel.set_camera("")
