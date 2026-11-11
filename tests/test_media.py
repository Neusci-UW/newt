"""USB audio (input, listening, playback) and the camera panel, with a stand-in for the
sound hardware so the tests run anywhere (and never ask macOS for the microphone)."""
import json
import threading
import time

import numpy as np
import pytest

from neusci_daq import audio, config, video

from test_channels import app, make_cfg, pump, win  # noqa: F401  (fixtures)


class FakeSD:
    """Enough of sounddevice: two devices, an input stream that plays a sine per input."""
    def __init__(self):
        self.devices = [
            {"name": "Fake Interface", "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 48000.0},
            {"name": "Fake Speakers", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 44100.0},
        ]
        self.default = type("D", (), {"device": [0, 1]})()
        self.played = []
        self.outputs = []

    def query_devices(self):
        return self.devices

    def check_input_settings(self, device, channels, samplerate, dtype):
        if samplerate not in (44100, 48000):
            raise ValueError("bad rate")

    def InputStream(self, device, channels, samplerate, blocksize, dtype, callback):
        return _FakeIn(channels, samplerate, blocksize, callback)

    def OutputStream(self, device, channels, samplerate, dtype, callback, latency):
        s = _FakeOut(callback)
        self.outputs.append(s)
        return s

    def play(self, data, rate, device):
        self.played.append((np.asarray(data), rate))

    def stop(self):
        pass


class _FakeIn:
    def __init__(self, channels, rate, block, callback):
        self.channels, self.samplerate, self.block, self.cb = channels, rate, block, callback
        self._stop = threading.Event()

    def start(self):
        def run():
            i = 0
            while not self._stop.is_set():
                t = (i + np.arange(self.block)) / self.samplerate
                data = np.column_stack([np.sin(2 * np.pi * (100 + 100 * k) * t) * (k + 1) * 0.1
                                        for k in range(self.channels)]).astype(np.float32)
                self.cb(data, self.block, None, None)
                i += self.block
                time.sleep(self.block / self.samplerate)
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def close(self):
        pass


class _FakeOut:
    def __init__(self, callback):
        self.cb = callback

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def pull(self, frames):
        out = np.zeros((frames, 1), np.float32)
        self.cb(out, frames, None, None)
        return out[:, 0]


@pytest.fixture
def sd(monkeypatch):
    fake = FakeSD()
    monkeypatch.setattr(audio, "_sd", fake)
    monkeypatch.setattr(audio, "_WHY", "")
    return fake


# ---------------------------------------------------------------- audio
def test_resample_carries_on_across_chunks():
    t = np.arange(4000) / 20000
    y = np.sin(2 * np.pi * 300 * t)
    whole, _ = audio.resample(y, 20000, 44100)
    parts, state = [], None
    for i in range(0, len(y), 500):
        out, state = audio.resample(y[i:i + 500], 20000, 44100, state)
        parts.append(out)
    joined = np.concatenate(parts)
    n = min(len(joined), len(whole)) - 50
    assert abs(len(joined) - len(whole)) <= 8 and np.allclose(joined[:n], whole[:n], atol=0.02)


def test_an_audio_device_is_an_input_with_channels_in1_in2(sd):
    cfg = config.Config(backend="audio", audio_device="Fake", channels=[config.Channel("ai0", "Ch1")])
    audio.use_audio_channels(cfg)
    config.validate(cfg)
    assert [c.physical for c in cfg.channels] == ["in1", "in2"] and cfg.sample_rate == 48000
    assert config.valid_inputs(cfg) == ["in1", "in2"]
    from neusci_daq.backends import make_backend
    be = make_backend(cfg)
    q = be.subscribe()
    be.start()
    time.sleep(0.3)
    be.stop()
    chunks = [q.get_nowait() for _ in range(q.qsize())]
    d = np.concatenate([c.data for c in chunks], axis=1)
    assert d.shape[0] == 2 and d.shape[1] > 4000 and be.actual_rate == 48000
    assert np.abs(d[0]).max() == pytest.approx(0.1, abs=0.01) and np.abs(d[1]).max() == pytest.approx(0.2, abs=0.01)
    assert np.allclose(np.diff(chunks[1].t)[:3], 1 / 48000)
    bad = config.Config(backend="audio", audio_device="Fake", sample_rate=20000,
                        channels=[config.Channel("in1", "A")])
    with pytest.raises(RuntimeError, match="can't record at 20,000 Hz"):
        make_backend(bad).start()


def test_monitor_plays_the_channel_scaled_to_be_heard(sd):
    m = audio.Monitor()
    m.start()
    fs = 20000
    t = np.arange(2000) / fs
    m.feed(0.001 * np.sin(2 * np.pi * 500 * t) + 5.0, fs)            # tiny, on an offset
    out = sd.outputs[-1].pull(6000)                                   # all of it (0.1 s at 44.1 kHz)
    assert 0.3 < np.abs(out).max() <= 0.5 + 1e-6 and abs(np.mean(out)) < 0.05   # centred and audible
    assert np.all(sd.outputs[-1].pull(100) == 0)                      # nothing queued: silence
    m.stop()


def test_player_says_how_far_it_has_got(sd):
    p = audio.Player()
    p.play(np.sin(np.linspace(0, 100, 20000)), 20000)                 # 1 s
    data, rate = sd.played[-1]
    assert rate == 44100 and len(data) == pytest.approx(44100, abs=3)
    assert 0 <= p.position() < 0.2
    p.stop()
    assert p.position() is None


# ---------------------------------------------------------------- the window
def test_listen_live_and_play_back_in_review(app, win, sd):
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    assert win.set_listen("Ch1")
    pump(app, 0.3)
    assert len(win._monitor._buf) > 0                                 # live samples go to the speakers
    win.btn_rec.setChecked(True)
    pump(app, 0.5)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    win.open_session(win.session_dir)
    pump(app, 0.1)
    assert win._monitor is None                                       # review: Play plays it instead
    assert win.play_shown() and win._play_timer.isActive()
    assert sd.played and sd.played[-1][1] == 44100
    win._play_tick()
    assert win._cursor_lines[0].isVisible()                           # a cursor moves with the sound
    win.toggle_playback()
    assert not win._play_timer.isActive()
    win.set_cursor(0.25)
    assert all(ln.value() == pytest.approx(0.25) for ln in win._cursor_lines)


def test_switching_to_an_audio_input_and_back(app, win, sd):
    from neusci_daq import gui
    assert win.switch_input("audio", "Fake Interface")
    new = gui._WINDOWS[-1]
    try:
        assert new.cfg.backend == "audio" and [c.physical for c in new.cfg.channels] == ["in1", "in2"]
        assert type(new.backend).__name__ == "AudioBackend"
        assert new.switch_input("simulated")
        back = gui._WINDOWS[-1]
        assert back.cfg.backend == "simulated" and [c.name for c in back.cfg.channels] == ["Ch1", "Ch2"]
        back.close()
    finally:
        new.close()


# ---------------------------------------------------------------- video
def test_video_files_line_up_with_their_block(tmp_path):
    block = tmp_path / "block_001.h5"
    block.write_bytes(b"")
    assert video.video_of(block) is None
    (tmp_path / "block_001.mp4").write_bytes(b"x")
    video.sidecar(block).write_text(json.dumps({"file": "block_001.mp4", "offset_s": 0.12, "nudge_s": 0}))
    assert video.video_of(block) == (tmp_path / "block_001.mp4", pytest.approx(0.12))
    video.save_nudge(block, -0.02)
    assert video.video_of(block)[1] == pytest.approx(0.10)
    assert video.video_ms(1.0, 0.10) == 900 and video.video_ms(0.05, 0.10) == 0


def test_camera_panel_follows_review_and_records_only_with_a_camera(app, win, tmp_path):
    panel = win.camera_panel
    assert not panel.start_block(tmp_path / "block_001.h5", time.monotonic())   # no camera: no video
    win.show_camera(True)
    assert not panel.isHidden() and win.act_camera.isChecked()
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)
    pump(app, 0.3)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    block = win.blocks[-1]
    (block.with_suffix(".mp4")).write_bytes(b"x")                     # as if a video was recorded
    video.sidecar(block).write_text(json.dumps({"file": block.with_suffix(".mp4").name, "offset_s": 0.0}))
    win.open_session(win.session_dir)
    pump(app, 0.1)
    assert not panel.live and "Click a plot" in panel.message.text()
    panel.nudge.setValue(0.05)                                        # saved with the block
    assert json.loads(video.sidecar(block).read_text())["nudge_s"] == pytest.approx(0.05)


# ---------------------------------------------------------------- finding cameras
def test_no_camera_says_what_to_check(app, win, monkeypatch):
    monkeypatch.setattr(video, "_camera_list", lambda: [])
    panel = win.camera_panel
    panel.fill_cameras()
    assert [panel.camera_box.itemText(i) for i in range(panel.camera_box.count())] == ["Off"]
    if video.available():                      # Qt without multimedia says that instead
        return
    assert "No camera found" in panel.message.text() and "Camera app" in panel.message.text()


def test_cameras_plugged_in_later_appear_and_two_alike_are_told_apart(app, win, monkeypatch):
    panel = win.camera_panel
    monkeypatch.setattr(video, "_camera_list", lambda: [])
    panel.fill_cameras()
    plugged = [("AmScope MU500", "aa01"), ("AmScope MU500 (2)", "aa02")]
    monkeypatch.setattr(video, "_camera_list", lambda: plugged)
    panel.btn_refresh.click()                  # also runs by itself when Qt sees a change
    texts = [panel.camera_box.itemText(i) for i in range(panel.camera_box.count())]
    assert texts == ["Off", "AmScope MU500", "AmScope MU500 (2)"]
    assert panel.camera_box.itemData(2) == "aa02"
    assert "2 cameras found" in panel.message.text()


def test_the_list_keeps_the_camera_picked_and_notices_it_unplugged(app, win, monkeypatch):
    panel = win.camera_panel
    monkeypatch.setattr(video, "_camera_list", lambda: [("Cam A", "01"), ("Cam B", "02")])
    panel.fill_cameras()
    panel.camera_box.blockSignals(True)        # no real camera to open here
    panel.camera_box.setCurrentIndex(2)
    panel.camera_box.blockSignals(False)
    monkeypatch.setattr(video, "_camera_list", lambda: [("Cam C", "03"), ("Cam B", "02")])
    panel.fill_cameras()
    assert panel.camera_box.currentData() == "02"          # still Cam B, though it moved
    monkeypatch.setattr(video, "_camera_list", lambda: [("Cam C", "03")])
    panel.fill_cameras()
    assert panel.camera_box.currentData() == "" and "unplugged" in panel.message.text()


def test_window_title_says_newt(win):
    assert "Newt" in win.windowTitle() and "neusci-daq-visualizer" not in win.windowTitle()
