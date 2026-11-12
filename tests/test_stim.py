"""The stimulator: shapes, safety, the NI output task, and the menu."""
import sys
import time
import types

import numpy as np
import pytest

from neusci_daq import config, session, trialfile
from neusci_daq import stim as stimmod
from pyqtgraph.Qt import QtWidgets
from neusci_daq.stim import Stimulus, check_limit, load_wave_file

from test_channels import app, make_cfg, pump, win  # noqa: F401  (fixtures)

RATE = 5000.0


# ---------------------------------------------------------------- shapes
def test_single_pulse():
    w = Stimulus(shape="pulse", amplitude_v=2, width_ms=1, delay_ms=2).waveform(RATE)
    assert len(w) == 10 + 5 + 1                       # 2 ms delay, 1 ms pulse, final 0 V
    assert np.all(w[:10] == 0) and np.all(w[10:15] == 2) and w[-1] == 0


def test_train_and_biphasic():
    w = Stimulus(shape="pulse", amplitude_v=1, width_ms=1, count=3, interval_ms=4).waveform(RATE)
    starts = np.flatnonzero(np.diff(np.r_[0, w]) > 0)
    assert list(starts) == [0, 20, 40] and w.sum() == 15
    b = Stimulus(shape="pulse", amplitude_v=1.5, width_ms=1, biphasic=True).waveform(RATE)
    assert np.all(b[:5] == 1.5) and np.all(b[5:10] == -1.5) and b[-1] == 0


def test_step_ramp_sine():
    s = Stimulus(shape="step", amplitude_v=-0.5, duration_ms=10).waveform(RATE)
    assert np.all(s[:-1] == -0.5) and len(s) == 51
    r = Stimulus(shape="ramp", start_v=0, end_v=4, duration_ms=10).waveform(RATE)
    assert r[0] == 0 and r[-2] == pytest.approx(4) and np.all(np.diff(r[:-1]) > 0)
    n = Stimulus(shape="sine", amplitude_v=1, freq_hz=100, duration_ms=10, offset_v=0.5).waveform(RATE)
    assert n[:-1].max() == pytest.approx(1.5, abs=0.01) and n[:-1].min() == pytest.approx(-0.5, abs=0.01)


def test_custom_segments_make_any_shape():
    segs = [{"duration_ms": 2, "start_v": 0, "end_v": 2},    # ramp up
            {"duration_ms": 2, "start_v": 2, "end_v": 2},    # hold
            {"duration_ms": 1, "start_v": -1, "end_v": -1}]  # jump negative
    w = Stimulus(shape="custom", segments=segs).waveform(RATE)
    assert len(w) == 10 + 10 + 5 + 1
    assert w[0] == 0 and np.all(w[10:20] == 2) and np.all(w[20:25] == -1) and w[-1] == 0


def test_waveform_files(tmp_path):
    one = tmp_path / "one.csv"
    one.write_text("volts\n0\n1\n2\n1\n")
    w = Stimulus(shape="file", file=str(one), file_rate=1000).waveform(RATE)
    assert w.max() == pytest.approx(2) and len(w) == 3 * 5 + 1 + 1    # 3 ms resampled, + 0 V
    two = tmp_path / "two.txt"
    two.write_text("# time volts\n0 0\n0.002 3\n")
    w2 = load_wave_file(two, RATE, RATE)
    assert len(w2) == 11 and w2[-1] == pytest.approx(3) and w2[5] == pytest.approx(1.5)
    bad = tmp_path / "bad.csv"
    bad.write_text("0,1\n1\n")
    with pytest.raises(ValueError, match="same 1 or 2 columns"):
        load_wave_file(bad, RATE, RATE)


def test_problems_are_explained():
    with pytest.raises(ValueError, match="shorter than one output sample"):
        Stimulus(shape="pulse", width_ms=0.1).waveform(RATE)
    with pytest.raises(ValueError, match="Interval"):
        Stimulus(shape="pulse", width_ms=2, count=2, interval_ms=1).waveform(RATE)
    with pytest.raises(ValueError, match="half the output rate"):
        Stimulus(shape="sine", freq_hz=3000).waveform(RATE)
    with pytest.raises(ValueError, match="limit is 60"):
        Stimulus(shape="step", duration_ms=61000).waveform(RATE)
    with pytest.raises(ValueError, match="5 V limit"):
        check_limit(Stimulus(amplitude_v=6).waveform(RATE), 5.0)
    assert Stimulus(width_ms=0.3).rounding_notes(RATE) == \
        ["Width 0.3 ms rounds to 0.4 ms (output steps are 0.2 ms)."]
    assert len(Stimulus(width_ms=0.3).waveform(RATE)) == 2 + 1     # notes match what's sent
    assert len(Stimulus(width_ms=0.5).waveform(RATE)) == 3 + 1     # halves round up, not to even


def test_save_load_and_describe(tmp_path):
    s = Stimulus(shape="pulse", amplitude_v=2, width_ms=1, count=5, interval_ms=100)
    assert s.describe() == "Pulse 2 V, 1 ms ×5 every 100 ms"
    back = Stimulus.load(s.save(tmp_path / "s.json"))
    assert back == s
    (tmp_path / "junk.json").write_text("not json")
    with pytest.raises(ValueError, match="isn't a saved stimulus"):
        Stimulus.load(tmp_path / "junk.json")


def test_config_stimulator_section(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.stim_output, cfg.stim_max_v, cfg.stim_monitor = "ao1", 8.0, "ai3"
    back = config.load(config.save(cfg, tmp_path / "c.toml"))
    assert (back.stim_output, back.stim_rate, back.stim_max_v, back.stim_monitor) == ("ao1", 5000, 8.0, "ai3")
    cfg.stim_rate = 10000
    with pytest.raises(ValueError, match="5,000"):
        config.validate(cfg)


# ---------------------------------------------------------------- NI output task
class FakeTask:
    log = []

    def __init__(self):
        self.done_at = None
        FakeTask.log.append(("task",))
        self.ao_channels = types.SimpleNamespace(add_ao_voltage_chan=self._add)
        self.timing = types.SimpleNamespace(cfg_samp_clk_timing=self._timing)

    def _add(self, name, min_val, max_val):
        FakeTask.log.append(("chan", name, min_val, max_val))

    def _timing(self, rate, sample_mode, samps_per_chan):
        FakeTask.log.append(("timing", rate, sample_mode, samps_per_chan))
        self.duration = samps_per_chan / rate

    def write(self, data, auto_start):
        FakeTask.log.append(("write", np.atleast_1d(np.asarray(data, float)).copy(), auto_start))

    def start(self):
        self.done_at = time.monotonic() + self.duration
        FakeTask.log.append(("start",))

    def is_task_done(self):
        return time.monotonic() >= self.done_at

    def stop(self):
        FakeTask.log.append(("stop",))

    def close(self):
        FakeTask.log.append(("close",))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture
def fake_nidaqmx(monkeypatch):
    mod = types.ModuleType("nidaqmx")
    mod.Task = FakeTask
    consts = types.ModuleType("nidaqmx.constants")
    consts.AcquisitionType = types.SimpleNamespace(FINITE="FINITE", CONTINUOUS="CONTINUOUS")
    monkeypatch.setitem(sys.modules, "nidaqmx", mod)
    monkeypatch.setitem(sys.modules, "nidaqmx.constants", consts)
    FakeTask.log = []
    return FakeTask.log


def test_nidaqmx_output_sends_a_finite_hardware_timed_task(fake_nidaqmx, tmp_path):
    from neusci_daq.stimout import NidaqmxOutput
    cfg = make_cfg(tmp_path)
    cfg.device, cfg.stim_output = "Dev1", "ao1"
    out = NidaqmxOutput(cfg)
    wave = Stimulus(amplitude_v=2, width_ms=1).waveform(RATE)
    out.fire(wave, RATE)
    deadline = time.time() + 2
    while out.busy and time.time() < deadline:
        time.sleep(0.01)
    assert out.error is None
    kinds = [e[0] for e in fake_nidaqmx]
    assert kinds == ["task", "chan", "timing", "write", "start", "stop", "close"]
    assert fake_nidaqmx[1] == ("chan", "Dev1/ao1", -10.0, 10.0)
    assert fake_nidaqmx[2][1:] == (RATE, "FINITE", len(wave))
    np.testing.assert_array_equal(fake_nidaqmx[3][1], wave)
    assert fake_nidaqmx[3][2] is False


def test_nidaqmx_stop_cuts_it_short_and_sets_zero(fake_nidaqmx, tmp_path):
    from neusci_daq.stimout import NidaqmxOutput
    out = NidaqmxOutput(make_cfg(tmp_path))
    out.fire(Stimulus(shape="step", amplitude_v=1, duration_ms=5000).waveform(RATE), RATE)
    time.sleep(0.1)
    t0 = time.time()
    out.stop()
    assert time.time() - t0 < 1 and not out.busy
    writes = [e for e in fake_nidaqmx if e[0] == "write"]
    assert writes[-1][1].tolist() == [0.0, 0.0] and writes[-1][2] is True  # both outputs left at 0 V


def test_nidaqmx_error_is_reported_and_output_zeroed(fake_nidaqmx, tmp_path, monkeypatch):
    from neusci_daq.stimout import NidaqmxOutput
    monkeypatch.setattr(FakeTask, "start", lambda self: (_ for _ in ()).throw(RuntimeError("DAQmx -200xxx")))
    out = NidaqmxOutput(make_cfg(tmp_path))
    out.fire(Stimulus().waveform(RATE), RATE)
    out._thread.join(2)
    assert "200xxx" in str(out.error)
    assert [e for e in fake_nidaqmx if e[0] == "write"][-1][1].tolist() == [0.0, 0.0]


# ---------------------------------------------------------------- the menu, simulated
def test_fire_needs_acquisition_running(win):
    assert not win.fire_stimulus()
    assert "Press Start" in win.statusBar().currentMessage()


def test_record_stimulus_on_adds_and_removes_a_stim_channel(app, win):
    assert win.set_stim_monitor("ai3")
    assert [c.name for c in win.cfg.channels] == ["Ch1", "Ch2", "Stim"]
    assert win.cfg.channels[2].physical == "ai3" and win.cfg.stim_monitor == "ai3"
    assert win.set_stim_monitor("ai4")                      # move it
    assert [c.physical for c in win.cfg.channels] == ["ai0", "ai1", "ai4"]
    assert win.set_stim_monitor("")
    assert [c.name for c in win.cfg.channels] == ["Ch1", "Ch2"] and win.cfg.stim_monitor == ""


def test_an_existing_channel_can_record_the_stimulus_from_acquisition(app, win):
    from neusci_daq.channels import ChannelStrip
    strips = [s for s in win.strips if isinstance(s, ChannelStrip)]
    assert [s.stim.isVisible() or not s.stim.isHidden() for s in strips] == [True, True]
    assert not any(s.stim.isChecked() for s in strips)
    strips[1].stim.setChecked(True)                         # Ch2 records the stimulus
    pump(app, 0.05)
    assert win.cfg.stim_monitor == "ai1" and [c.name for c in win.cfg.channels] == ["Ch1", "Ch2"]
    assert win.stim_page.monitor.currentData() == "ai1"     # the Stimulator tab agrees
    strips = [s for s in win.strips if isinstance(s, ChannelStrip)]
    strips[0].stim.setChecked(True)                         # only one at a time
    pump(app, 0.05)
    assert win.cfg.stim_monitor == "ai0"
    assert [s.stim.isChecked() for s in win.strips if isinstance(s, ChannelStrip)] == [True, False]
    assert win.set_stim_monitor("ai3")                      # a new Stim channel, then back to Ch1
    assert win.set_stim_monitor("ai0")
    assert [c.name for c in win.cfg.channels] == ["Ch1", "Ch2"]   # the added Stim channel goes
    strips = [s for s in win.strips if isinstance(s, ChannelStrip)]
    strips[0].stim.setChecked(False)
    pump(app, 0.05)
    assert win.cfg.stim_monitor == "" and len(win.cfg.channels) == 2
    strips = [s for s in win.strips if isinstance(s, ChannelStrip)]
    strips[1].stim.setChecked(True)
    pump(app, 0.05)
    win._channel_removed([s for s in win.strips if isinstance(s, ChannelStrip)][1])   # remove it: unmarked
    pump(app, 0.05)
    assert win.cfg.stim_monitor == ""


def test_fired_stimulus_is_recorded_on_the_stim_channel_and_logged(app, win):
    win.set_stim_monitor("ai3")
    win.set_stimulus(Stimulus(shape="step", amplitude_v=3, duration_ms=100, delay_ms=20))
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)
    pump(app, 0.2)
    assert win.fire_stimulus()
    pump(app, 0.4)
    win.btn_rec.setChecked(False)
    win.btn_run.setChecked(False)
    blk = trialfile.read_block(win.blocks[-1])
    stim = blk["data"][2]
    assert stim.max() == pytest.approx(3, abs=0.02)
    assert (stim > 1.5).sum() == pytest.approx(0.1 * win.cfg.sample_rate, abs=3)   # 100 ms at the AI rate
    assert any(text.startswith("Stim:") and "Step 3 V" in text for _, text in blk["comments"])
    ch1 = blk["data"][0]                                    # cell 1, intracellular at gain 10
    assert ch1.max() - np.median(ch1) > 0.1                 # depolarized past threshold: > 10 mV


def test_limit_and_busy_are_refused_with_a_message(app, win):
    win.cfg.stim_max_v = 5                                  # an instructor's lower limit
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.set_stimulus(Stimulus(amplitude_v=9))
    assert not win.fire_stimulus() and "limit" in win.statusBar().currentMessage()
    win.set_stimulus(Stimulus(shape="step", amplitude_v=1, duration_ms=1000))
    assert win.fire_stimulus()
    assert not win.fire_stimulus() and "still running" in win.statusBar().currentMessage()
    win.stop_stimulus()
    assert not win.stim_out.busy
    win.btn_run.setChecked(False)


def test_repeat_fires_until_stopped(app, win):
    win.stim_repeat_s = 0.15
    win.set_stimulus(Stimulus(amplitude_v=1, width_ms=1))
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.stim_page.btn_repeat.click()                       # the Repeat button on the tab
    pump(app, 0.55)
    n = win.stim_out.fired
    assert n >= 3
    win.btn_run.setChecked(False)                          # Stop also stops repeating
    assert not win.repeating and not win.stim_page.btn_repeat.isChecked()
    pump(app, 0.3)
    assert win.stim_out.fired == n


def test_output_choice_and_stimulus_are_remembered(win, tmp_path):
    from neusci_daq.gui import MainWindow
    win.set_stim_output("ao1")
    assert win.cfg.stim_output == "ao1" and win.stim_page.output.currentText() == "ao1"
    win.set_stimulus(Stimulus(shape="ramp", end_v=2))
    w2 = MainWindow(make_cfg(tmp_path), settings=win.settings)
    assert w2.stimulus.shape == "ramp" and w2.stimulus.end_v == 2
    w2.close()


# ---------------------------------------------------------------- the window
def test_window_fields_build_the_stimulus_and_preview(app, win):
    sw = win.open_stim_window()
    sw.shape.setCurrentIndex(sw.shape.findData("pulse"))
    sw.p_amp.setValue(2.5)
    sw.p_width.setValue(2)
    sw.p_count.setValue(3)
    sw.p_interval.setValue(10)
    s = win.stimulus
    assert (s.shape, s.amplitude_v, s.width_ms, s.count, s.interval_ms) == ("pulse", 2.5, 2, 3, 10)
    x, y = sw.curve.getData()
    assert y.max() == pytest.approx(2.5) and "Peak 2.5 V" in sw.message.text()


def test_window_custom_table_and_errors(app, win):
    sw = win.open_stim_window()
    sw.shape.setCurrentIndex(sw.shape.findData("custom"))
    sw._add_segment()
    assert win.stimulus.shape == "custom" and len(win.stimulus.segments) == 4
    sw.table.item(0, 0).setText("abc")
    assert "must be a number" in sw.message.text()
    sw.table.item(0, 0).setText("5")
    sw.shape.setCurrentIndex(sw.shape.findData("pulse"))
    win.cfg.stim_max_v = 5
    sw.p_amp.setValue(8)                                   # over a 5 V limit
    assert "limit" in sw.message.text()


def test_loading_a_stimulus_updates_preview_and_what_fire_sends(app, win, tmp_path):
    sw = win.open_stim_window()
    custom = Stimulus(shape="custom", segments=[{"duration_ms": 10, "start_v": 0, "end_v": 3},
                                                {"duration_ms": 10, "start_v": -1, "end_v": -1}])
    sw.set_stimulus(custom)
    assert win.stimulus.shape == "custom" and win.stimulus.segments == custom.segments
    x, y = sw.curve.getData()
    assert y.min() == pytest.approx(-1) and y.max() < 3


# ---------------------------------------------------------------- units
def test_describe_in_units():
    s = Stimulus(shape="pulse", amplitude_v=2, width_ms=1)
    assert s.describe() == "Pulse 2 V, 1 ms"
    assert s.describe("µA", 100) == "Pulse 200 µA, 1 ms"
    r = Stimulus(shape="ramp", start_v=0, end_v=0.5, duration_ms=10)
    assert r.describe("mA", 2) == "Ramp 0→1 mA over 10 ms"


def test_units_setting_is_saved_and_checked(tmp_path):
    c = config.Config(backend="simulated", channels=[config.Channel("ai0", "A")],
                      stim_units="µA", stim_scale=100)
    back = config.load(config.save(c, tmp_path / "u.toml"))
    assert (back.stim_units, back.stim_scale) == ("µA", 100)
    plain = config.Config(backend="simulated", channels=[config.Channel("ai0", "A")])
    stim_section = config.dumps(plain).split("[stimulator]")[1].split("[[channels]]")[0]
    assert "units" not in stim_section                     # unchanged files for volts
    with pytest.raises(ValueError, match="scale"):
        config.validate(config.Config(backend="simulated", channels=[config.Channel("ai0", "A")],
                                      stim_scale=0))


def test_stimulator_fields_show_units_but_stimulus_stays_in_volts(win):
    win.cfg.stim_units, win.cfg.stim_scale = "µA", 100
    win.cfg.stim_max_v = 5
    page = win.stim_page
    page.sync()                                            # picks up the new units
    win.set_stimulus(Stimulus(shape="pulse", amplitude_v=2, width_ms=1))
    assert page.p_amp.value() == pytest.approx(200) and page.p_amp.text() == "200 µA"
    assert page.p_amp.maximum() == pytest.approx(1000)     # ±10 V at the output
    page.p_amp.setValue(150)
    assert win.stimulus.amplitude_v == pytest.approx(1.5)  # sent in volts
    assert "150 µA" in page.message.text() and "(500 µA)" in page.info.text()
    win.set_stimulus(Stimulus(shape="custom", segments=[{"duration_ms": 5, "start_v": 0, "end_v": 0.5}]))
    assert page.table.item(0, 2).text() == "50"
    assert page.table.horizontalHeaderItem(1).text() == "Start (µA)"
    page.table.item(0, 2).setText("80")
    assert win.stimulus.segments[0]["end_v"] == pytest.approx(0.8)


# ---------------------------------------------------------------- typed units
def test_parse_quantity_and_unit_factor():
    from neusci_daq.units import _same_kind, parse_quantity, unit_factor
    assert unit_factor("s", "ms") == 1000 and unit_factor("us", "ms") == pytest.approx(0.001)
    assert unit_factor("mV", "V") == pytest.approx(0.001) and unit_factor("mA", "µA") == pytest.approx(1000)
    assert unit_factor("V", "ms") is None
    ms = _same_kind("ms")
    assert parse_quantity("0.5 s", ms) == (500, "s") and parse_quantity("250", ms) == (250, "")
    assert parse_quantity("300µs", ms)[0] == pytest.approx(0.3) and parse_quantity("1,500 ms", ms)[0] == 1500
    assert parse_quantity("2", ms, default_unit="s") == (2000, "s")       # bare number: the unit shown
    with pytest.raises(ValueError):
        parse_quantity("5 V", ms)


def type_into(box, text):
    box.lineEdit().setText(text)
    box.interpretText()


def test_stimulator_fields_take_typed_units(win):
    page = win.stim_page
    win.set_stimulus(Stimulus(shape="pulse", amplitude_v=1, width_ms=1))
    type_into(page.p_amp, "500 mV")
    type_into(page.p_width, "0.002 s")
    type_into(page.p_interval, "50ms")
    assert page.p_amp.text() == "500 mV" and page.p_width.text() == "2 ms"   # best prefix
    s = win.stimulus
    assert (s.amplitude_v, s.width_ms, s.interval_ms) == pytest.approx((0.5, 2, 50))   # kept in V, ms
    type_into(page.p_amp, "3 kg")                            # not a unit here: unchanged
    assert win.stimulus.amplitude_v == pytest.approx(0.5)
    type_into(page.repeat, "500 ms")
    assert win.stim_repeat_s == pytest.approx(0.5) and page.repeat.text() == "500 ms"
    win.set_stimulus(Stimulus(shape="sine", freq_hz=10))
    type_into(page.n_freq, "1.5 kHz")
    assert win.stimulus.freq_hz == pytest.approx(1500) and page.n_freq.text() == "1.5 kHz"


def test_fields_keep_the_typed_unit(win):
    page = win.stim_page
    win.set_stimulus(Stimulus(shape="step", amplitude_v=1, duration_ms=100))
    type_into(page.s_dur, "1s")
    assert page.s_dur.text() == "1 s" and win.stimulus.duration_ms == pytest.approx(1000)
    type_into(page.s_dur, "2")                               # a bare number is in the unit shown
    assert page.s_dur.text() == "2 s" and win.stimulus.duration_ms == pytest.approx(2000)
    page.s_dur.stepBy(1)                                     # arrows step in that unit: 10 ms -> 10 s
    assert win.stimulus.duration_ms == pytest.approx(12000)
    win.set_stimulus(Stimulus(shape="step", amplitude_v=0.25, duration_ms=50))
    assert page.s_dur.text() == "50 ms" and page.s_amp.text() == "250 mV"   # loaded values too
    type_into(page.s_amp, "300 mv")                          # tidied: mv -> mV
    assert page.s_amp.text() == "300 mV"
    type_into(page.s_dur, "250 us")
    assert page.s_dur.text() == "250 µs" and win.stimulus.duration_ms == pytest.approx(0.25)


def test_typed_units_with_converted_stimulator_units(win):
    win.cfg.stim_units, win.cfg.stim_scale = "µA", 100       # 100 µA per V
    page = win.stim_page
    page.sync()
    win.set_stimulus(Stimulus(shape="step", amplitude_v=1, duration_ms=10))
    type_into(page.s_amp, "0.05 mA")                         # 50 µA
    assert page.s_amp.text() == "50 µA" and win.stimulus.amplitude_v == pytest.approx(0.5)
    type_into(page.s_amp, "2 V")                             # volts at the output: 200 µA
    assert win.stimulus.amplitude_v == pytest.approx(2)
    win.set_stimulus(Stimulus(shape="custom", segments=[{"duration_ms": 5, "start_v": 0, "end_v": 0.5}]))
    page.table.item(0, 0).setText("0.01 s")
    page.table.item(0, 2).setText("300 mV")                  # 0.3 V
    seg = win.stimulus.segments[0]
    assert seg["duration_ms"] == pytest.approx(10) and seg["end_v"] == pytest.approx(0.3)
    page._add_segment()                                      # copies "300 mV" as 30 µA
    assert win.stimulus.segments[1]["start_v"] == pytest.approx(0.3)


def test_stimulus_can_start_with_acquisition(app, win, tmp_path):
    from neusci_daq.gui import MainWindow
    win.stim_repeat_s = 0.15
    win.set_stimulus(Stimulus(amplitude_v=1, width_ms=1))
    page = win.stim_page
    assert page.on_start.currentData() == ""                 # off by default
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    assert win.stim_out.fired == 0
    win.btn_run.setChecked(False)

    page.on_start.setCurrentIndex(page.on_start.findData("once"))
    win.btn_run.setChecked(True)
    pump(app, 0.4)
    assert win.stim_out.fired == 1 and not win.repeating
    win.btn_run.setChecked(False)

    page.on_start.setCurrentIndex(page.on_start.findData("repeat"))
    fired = win.stim_out.fired
    win.btn_run.setChecked(True)
    pump(app, 0.4)
    assert win.repeating and page.btn_repeat.isChecked() and win.stim_out.fired >= fired + 2
    win.btn_run.setChecked(False)                            # Stop stops it, as before
    assert not win.repeating
    w2 = MainWindow(make_cfg(tmp_path), settings=win.settings)   # remembered
    assert w2.stim_on_start == "repeat" and w2.stim_page.on_start.currentData() == "repeat"
    w2.close()


def test_fields_show_numbers_under_1000_with_a_prefix(win):
    page = win.stim_page
    win.set_stimulus(Stimulus(shape="step", amplitude_v=0.0004, duration_ms=10000))
    assert page.s_dur.text() == "10 s" and page.s_amp.text() == "400 µV"
    type_into(page.s_dur, "2500 ms")
    assert page.s_dur.text() == "2.5 s"
    type_into(page.s_dur, "0.0003 s")
    assert page.s_dur.text() == "300 µs" and win.stimulus.duration_ms == pytest.approx(0.3)
    type_into(page.s_amp, "0.75 V")
    assert page.s_amp.text() == "750 mV"
    win.set_stimulus(Stimulus(shape="sine", freq_hz=2000))
    assert page.n_freq.text() == "2 kHz"
    type_into(page.repeat, "3600 s")                         # no bigger prefix than s: stays in s
    assert page.repeat.text() == "3600 s"


def test_the_preview_fits_a_stimulus_that_changes_its_scale(app, win):
    from neusci_daq.stim import Stimulus
    page = win.stim_page
    vb = page.preview.getPlotItem().vb
    win.set_stimulus(Stimulus(shape="pulse", amplitude_v=1, width_ms=1))
    page._update_preview()
    vb.setXRange(0, 0.0005, padding=0)                               # zoomed in by hand
    win.set_stimulus(Stimulus(shape="pulse", amplitude_v=1, width_ms=1, biphasic=True))
    page._update_preview()
    vb.updateAutoRange()
    end = page.curve.getData()[0][-1]
    assert vb.viewRange()[0][1] >= end * 0.99


def test_a_block_saves_the_stimulus_sent(app, win):
    s = Stimulus(shape="sine", amplitude_v=1, freq_hz=20, duration_ms=200)
    win.set_stimulus(s)
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.btn_rec.setChecked(True)
    pump(app, 0.2)
    assert win.fire_stimulus()
    pump(app, 0.3)
    win.btn_rec.setChecked(False)
    pump(app, 0.1)
    win.btn_run.setChecked(False)
    block = trialfile.read_block(session.list_blocks(win.session_dir)[-1])
    saved = block["stimuli"][0]
    assert saved["rate"] == win.cfg.stim_rate and 0 <= saved["t"] < block["duration_s"] + 0.1
    assert Stimulus.from_dict(saved["settings"]) == s
    assert s.repeat() == (0.05, 0.0, 4)
    assert Stimulus(shape="pulse", count=3, interval_ms=100, delay_ms=20).repeat() == (0.1, 0.02, 3)
    assert Stimulus(shape="step").repeat() == (0.0, 0.0, 0)


# ---------------------------------------------------------------- a second output
def test_both_outputs_go_in_one_task_from_one_clock(fake_nidaqmx, tmp_path):
    from neusci_daq.stimout import NidaqmxOutput
    out = NidaqmxOutput(make_cfg(tmp_path))
    chans, wave = stimmod.combine({"ao1": Stimulus(shape="step", amplitude_v=-2, duration_ms=4).waveform(RATE),
                                   "ao0": Stimulus(amplitude_v=3, width_ms=1).waveform(RATE)})
    assert chans == ["ao0", "ao1"] and wave.shape[0] == 2 and wave[:, -1].tolist() == [0, 0]
    out.fire(wave, RATE, chans)
    out._thread.join(2)
    assert out.error is None
    assert [e[1] for e in fake_nidaqmx if e[0] == "chan"][:2] == ["Dev1/ao0", "Dev1/ao1"]
    assert sum(1 for e in fake_nidaqmx if e[0] == "task") == 1                  # one task: one clock
    sent = next(e for e in fake_nidaqmx if e[0] == "write")[1]
    assert sent.shape == wave.shape and sent[0].max() == 3 and sent[1].min() == -2
    with pytest.raises(ValueError, match="One row"):
        out.fire(wave, RATE, ["ao0"])


def test_default_limit_is_the_outputs_ten_volts(tmp_path):
    assert config.Config().stim_max_v == 10
    c = config.Config(backend="simulated", channels=[config.Channel("ai0", "A")], stim_max_v=5)
    assert config.load(config.save(c, tmp_path / "c.toml")).stim_max_v == 5   # a saved limit stays


def test_add_a_second_output_edit_it_fire_both_and_remove_it(app, win):
    page = win.open_stim_window()
    assert page.out_tabs.count() == 1 and page.btn_add_out.isVisibleTo(page)
    assert page.btn_add_out.text() == "+ Add second output (ao1)"
    win.set_stimulus(Stimulus(shape="pulse", amplitude_v=4, width_ms=2))
    page.btn_add_out.click()
    assert win.stimulus2 is not None and page.editing == 1 and page.out_tabs.count() == 2
    assert [page.out_tabs.tabText(i) for i in range(2)] == ["ao0", "ao1"] and page.btn_fire.text() == "Fire both"
    page.shape.setCurrentIndex(page.shape.findData("step"))
    page.s_amp.setValue(-3)
    assert win.stimulus2.shape == "step" and win.stimulus2.amplitude_v == -3
    assert win.stimulus.shape == "pulse" and win.stimulus.amplitude_v == 4    # ao0 untouched
    page.out_tabs.setCurrentIndex(0)
    assert page.shape.currentData() == "pulse" and page.p_amp.value() == 4     # the fields follow the tab
    assert "ao0:" in page.message.text() and "ao1:" in page.message.text()
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    assert win.fire_stimulus() and win.stim_out.last_channels == ["ao0", "ao1"]
    win.stop_stimulus()
    win.btn_run.setChecked(False)
    page.btn_remove_out.click()
    pump(app, 0.05)                                         # removed once the click has finished
    assert win.stimulus2 is None and page.out_tabs.count() == 1 and page.btn_fire.text() == "Fire"


def test_second_output_follows_the_first_and_both_are_saved(app, win, tmp_path, monkeypatch):
    page = win.open_stim_window()
    win.set_stimulus2(Stimulus(shape="step", amplitude_v=2))
    win.set_stim_output("ao1")                               # the first moves: the second takes ao0
    assert win.second_output == "ao0" and page.out_tabs.tabText(1) == "ao0"
    path = tmp_path / "pair.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    page._save()
    win.set_stimulus2(None)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    page._load()
    assert win.stimulus2 is not None and win.stimulus2.shape == "step" and page.out_tabs.count() == 2
    win.set_stimulus2(None)
    win.set_stim_output("ao0")


def test_a_second_output_over_the_limit_is_refused_with_its_name(app, win):
    win.cfg.stim_max_v = 5
    win.set_stimulus2(Stimulus(shape="step", amplitude_v=8))
    assert "ao1:" in win.stim_page.message.text() and "limit" in win.stim_page.message.text()
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    assert not win.fire_stimulus() and "limit" in win.statusBar().currentMessage()
    win.btn_run.setChecked(False)
    win.set_stimulus2(None)
