"""Analysis: block lists, event detection, spike sorting, and the Analysis tab."""
import numpy as np
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets
import pytest

from neusci_daq import analysis as A
from neusci_daq import pages, colormaps as cm, pages, session, simulation as sim
from neusci_daq.channels import ColorButton
from neusci_daq.gui import REVIEW
from neusci_daq.canvas import KINDS
from neusci_daq.helpview import HelpDialog, TOPICS

from test_channels import app, make_cfg, pump, win  # noqa: F401  (fixtures)

FS = 20000


def fake_blocks(n=20, per_cell=4, noise=0.02, seed=0):
    """1 s blocks with spikes from the three default simulated cells (0.4, 0.9, 1.6 V)."""
    rng = np.random.default_rng(seed)
    cells = sim.default_network().cells
    out, truth = [], []
    for b in range(n):
        y = noise * rng.standard_normal(FS)
        # distinct times at least 10 ms apart, so no two spikes overlap
        times = rng.choice(np.arange(200, FS - 200, 200), per_cell * len(cells), replace=False)
        for k, t in enumerate(times):
            u = k % len(cells)
            w, tr = sim.spike_shape(cells[u], FS)
            y[t - tr:t - tr + len(w)] += w
            truth.append((b, t, u))
        out.append((b, y, float(FS)))
    return out, truth


def test_parse_blocks():
    assert A.parse_blocks("", 5) == [0, 1, 2, 3, 4] and A.parse_blocks("all", 3) == [0, 1, 2]
    assert A.parse_blocks("1-3, 5", 10) == [0, 1, 2, 4]
    assert A.parse_blocks("8-", 10) == [7, 8, 9] and A.parse_blocks("-2", 10) == [0, 1]
    assert A.parse_blocks("4-40", 6) == [3, 4, 5]
    for bad in ("x", "5-2", "0"):
        with pytest.raises(ValueError):
            A.parse_blocks(bad, 10)


def test_detect_finds_each_spike_once_at_its_peak():
    blocks, truth = fake_blocks(n=1)
    y = blocks[0][1]
    idx = A.detect(y, FS, 5 * A.noise_sd(y), "negative", dead_ms=1.0)
    true = sorted(t for _, t, _ in truth)
    assert len(idx) == len(true)
    assert np.all(np.abs(np.array(sorted(idx)) - true) <= 1)        # at the trough
    assert A.noise_sd(y) == pytest.approx(0.02, rel=0.1)            # spikes barely change it


def test_sorting_finds_the_three_cells():
    blocks, truth = fake_blocks()
    ev = A.find_events(blocks, threshold=5, fixed=False)
    assert ev.n_units == 3
    s = ev.summary()
    assert [round(x["amplitude"], 1) for x in s] == [-0.4, -0.9, -1.6]   # unit 1 is the smallest
    assert [x["count"] for x in s] == [80, 80, 80]
    assert [round(x["width_ms"], 1) for x in s] == [0.3, 0.6, 0.8]
    assert s[0]["rate"] == pytest.approx(4.0)                        # 80 in 20 s
    true = {(b, t): u for b, t, u in truth}
    got = [true.get((b, int(round(t * FS)))) for b, t in zip(ev.block, ev.t)]
    assert all(g == u for g, u in zip(got, ev.unit) if g is not None)


def test_a_set_number_of_units_and_a_fixed_threshold():
    blocks, _ = fake_blocks()
    assert A.find_events(blocks, threshold=5, fixed=False, n_units=1).n_units == 1
    ev = A.find_events(blocks, threshold=1.2, fixed=True)            # only the biggest cell
    assert len(ev.t) == 80 and ev.n_units == 1 and ev.level == 1.2


def test_positive_events_and_problems():
    blocks, _ = fake_blocks(n=2)
    flipped = [(b, -y, fs) for b, y, fs in blocks]
    assert len(A.find_events(flipped, threshold=5, fixed=False, polarity="positive").t) == 24
    with pytest.raises(ValueError, match="sample rates"):
        A.find_events([(0, blocks[0][1], 20000.0), (1, blocks[1][1], 10000.0)], threshold=5, fixed=False)
    with pytest.raises(ValueError):
        A.find_events([], threshold=5, fixed=False)


# ---------------------------------------------------------------- the tab and pages
def record(app, win, n):
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    for _ in range(n):
        win.btn_rec.setChecked(True)
        pump(app, 0.4)
        win.btn_rec.setChecked(False)
        pump(app, 0.05)
    win.btn_run.setChecked(False)


def test_up_down_step_through_the_blocks_panel(app, win):
    record(app, win, 3)
    win.show()
    assert win.mode == "live"
    win.step_block(1)                                                # ↓ from Live: the first block
    assert win.mode == REVIEW and win.block_combo.currentIndex() == 0
    win.step_block(1)
    win.step_block(1)
    assert win.block_combo.currentIndex() == 2
    win.step_block(1)                                                # past the end: stays
    assert win.block_combo.currentIndex() == 2
    win.step_block(-1)
    assert win.block_combo.currentIndex() == 1
    win.step_block(-1)
    win.step_block(-1)                                               # ↑ past the first: Back to Live
    assert win.mode == "live" and len(win.blocks) == 3


def test_analysis_tab_overlays_blocks_and_finds_events(app, win, tmp_path):
    from neusci_daq.simpage import SimPage                           # noqa: F401
    net = sim.default_network()
    net.recordings = [sim.Recording("ai0", 1, "extracellular", noise_v=0.02),
                      sim.Recording("ai1", 2, "extracellular", noise_v=0.02)]
    win.cfg.sim_network = net
    win.apply_setup(sample_rate=20000)
    record(app, win, 3)
    win.resize(1400, 900)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    page = win.analysis_page.raw
    assert page.channel.count() == 2 and win.analysis_page.current.maximum() == 3
    assert len(C(page, "Trials").group_avgs) == 1 and not C(page, "Trials").trial_items        # nothing picked: the page's average
    win.analysis_page.select_blocks([1, 2])
    assert trials_shown(page) == 2 and not C(page, "Trials").group_avgs           # blocks picked: their trials
    win.analysis_page.select_blocks([])
    ev = page.find()
    assert ev is not None and len(ev.t) > 0 and page.table.rowCount() == ev.n_units
    assert len(C(page, "Raster").raster_dots.data) == len(ev.t)
    assert "event" in page.message.text()
    out = page.export(tmp_path / "events.csv")
    assert out.read_text().splitlines()[0] == "trial,time_s,unit,amplitude,channel"
    assert len(out.read_text().splitlines()) == len(ev.t) + 1


def test_dragging_the_threshold_sets_a_fixed_level(app, win):
    record(app, win, 1)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    page = win.analysis_page.raw
    base = page.baseline()
    C(page, "Trials").thr_line.setValue(base - 0.3)
    C(page, "Trials")._thr_dragged()
    assert page.method.currentData() is False and page.threshold.text() == "300 mV"
    assert page._threshold() == ("level", pytest.approx(0.3))
    assert page.polarity.currentData() == "negative"


# ---------------------------------------------------------------- thresholds with units, bimodal, biphasic
def test_parse_threshold():
    assert A.parse_threshold("50 mV", "V") == ("level", pytest.approx(0.05))
    assert A.parse_threshold("0.05 V", "mV") == ("level", pytest.approx(50))
    assert A.parse_threshold("200 uV", "V") == ("level", pytest.approx(0.0002))
    assert A.parse_threshold("5", "V") == ("sd", 5) and A.parse_threshold("4.5 SD", "V") == ("sd", 4.5)
    assert A.parse_threshold("3σ", "V") == ("sd", 3)
    for bad in ("lots", "5 kg", "50 ms"):
        with pytest.raises(ValueError, match="noise SDs"):
            A.parse_threshold(bad, "V")


def test_bimodal_threshold_sits_between_noise_and_spikes():
    blocks, truth = fake_blocks()
    level, note = A.bimodal_level([y for _, y, _ in blocks], FS)
    assert 0.06 < level < 0.4                                        # above the noise, below the smallest spike
    ev = A.find_events(blocks, bimodal=True)
    assert len(ev.t) == len(truth) and ev.n_units == 3 and "Bimodal" in ev.notes[0]
    flat = [(0, 0.02 * np.random.default_rng(1).standard_normal(FS * 5), float(FS))]
    level, note = A.bimodal_level([flat[0][1]], FS)
    assert level is None and "two clear groups" in note
    ev = A.find_events(flat, bimodal=True, threshold=6)              # falls back to 6 SD
    assert "instead" in ev.notes[0] and ev.level == pytest.approx(6 * A.noise_sd(flat[0][1]))


def test_biphasic_keeps_spikes_and_drops_one_sided_artifacts():
    blocks, truth = fake_blocks(n=5)
    y = blocks[0][1].copy()
    for t in (1100, 7100, 15100):                                    # monophasic dips, 1 ms wide, between spikes
        y[t:t + 20] -= 1.0
    data = [(0, y, float(FS))]
    plain = A.find_events(data)
    strict = A.find_events(data, biphasic=0.3)
    assert len(plain.t) == 12 + 3 and len(strict.t) == 12             # the three dips are gone


def test_threshold_field_takes_a_level_with_units(app, win):
    net = sim.default_network()
    for c in net.cells:
        c.drive_mv = 14                                              # fire often: plenty of spikes
    net.recordings = [sim.Recording("ai0", 1, "extracellular", noise_v=0.02),
                      sim.Recording("ai1", 2, "extracellular", noise_v=0.02)]
    win.cfg.sim_network = net
    win.apply_setup(sample_rate=20000)
    record(app, win, 2)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    page = win.analysis_page.raw
    page.threshold.setText("500 mV")
    page.threshold.editingFinished.emit()
    ev = page.find()
    assert ev.level == pytest.approx(0.5) and "500 mV" in page.message.text()
    assert all(abs(a) >= 0.5 for a in ev.amplitude)
    page.threshold.setText("lots")
    page.threshold.editingFinished.emit()
    assert "noise SDs" in page.message.text()
    page.threshold.setText("5 SD")
    page.method.setCurrentIndex(1)                                   # bimodal
    page.biphasic.setChecked(True)
    ev = page.find()
    assert ev is not None and ev.notes and len(ev.t) > 0


# ---------------------------------------------------------------- sub-tabs, stepping, comparison
def key(widget, k):
    from pyqtgraph.Qt import QtCore, QtGui, QtWidgets
    for t in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease):
        QtWidgets.QApplication.sendEvent(widget, QtGui.QKeyEvent(t, k, QtCore.Qt.KeyboardModifier.NoModifier))


def test_arrow_keys_and_slider_step_the_analysis_block(app, win):
    from pyqtgraph.Qt import QtCore
    record(app, win, 4)
    win.open_session(win.session_dir)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    assert [ap.canvas.layouts.itemText(i) for i in range(ap.canvas.layouts.count())] == \
        ["Spike Sorting", "Response", "Waveforms"]
    C(ap.raw, "Trials").plot.setFocus()
    key(C(ap.raw, "Trials").plot, QtCore.Qt.Key.Key_Right)
    pump(app, 0.05)
    assert ap.current.value() == 2 and win.block_combo.currentIndex() == 0   # Analysis, not review
    ap.slider.setValue(4)
    assert ap.current.value() == 4
    ap.step(1)                                                       # past the end: stays
    assert ap.current.value() == 4 and ap.slider.value() == 4
    win.tabs.pick(0, "acq")
    pump(app, 0.05)
    win.step_block(1)                                                # ↓ on Acquisition: the review block
    assert win.block_combo.currentIndex() == 1


def stim_session(app, win, n=3):
    """A simulated recording: Ch2 (ai1) records cell 1 extracellularly, and a Stim
    channel (ai2) records a 1 V pulse every 0.25 s."""
    from neusci_daq.stim import Stimulus
    net = sim.default_network()
    net.recordings = [sim.Recording("ai1", 0, "extracellular", noise_v=0.02)]
    net.cells[0].stimulus = 40                                       # each pulse makes cell 1 fire
    win.cfg.sim_network = net
    win.apply_setup(sample_rate=20000)
    assert win.set_stim_monitor("ai2")                               # adds a channel named Stim
    win.set_stimulus(Stimulus(shape="pulse", amplitude_v=1, width_ms=1))
    win.stim_repeat_s = 0.25
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.set_repeat(True)
    for _ in range(n):
        win.btn_rec.setChecked(True)
        pump(app, 1.0)
        win.btn_rec.setChecked(False)
    win.set_repeat(False)
    win.btn_run.setChecked(False)


def test_raw_view_shows_rate_histogram_and_waveforms(app, win):
    stim_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    raw = win.analysis_page.raw
    raw.channel.setCurrentText("Ch2")
    ev = raw.find()
    assert ev is not None and len(ev.t) > 5
    assert C(raw, "Rate").plot.getPlotItem().listDataItems()                # spikes/s during a block
    assert C(raw, "AP sizes").plot.getPlotItem().listDataItems()                     # AP sizes
    assert C(raw, "Waveforms").plot.getPlotItem().listDataItems()


def test_comparison_traces_derivatives_and_triggered_averages(app, win):
    stim_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.raw.channel.setCurrentText("Ch2")
    ap.raw.find()
    ap.show_layout("Response")
    pump(app, 0.2)
    assert len(ap.chart("Traces")._plots) == 3                                      # Ch1, Ch2 and Stim V
    ap.chart("Traces").series.item(1, 1).setCheckState(QtCore.Qt.CheckState.Checked)   # + Ch2 dV/dt
    assert len(ap.chart("Traces")._plots) == 4
    ap.chart("Traces").layout_mode.setCurrentIndex(1)                               # overlaid
    assert len(ap.chart("Traces")._plots) == 1
    assert ap.chart("Stimulus-triggered rate").trigger.currentData() == "Stim"                       # picked as the stimulus channel
    pp = ap.chart("Stimulus-triggered rate").plot.getPlotItem().listDataItems()[0]
    edges, rate = pp.getData()                                       # a stepped plot: bin edges
    n_stim = int(ap.chart("Stimulus-triggered rate").plot.getPlotItem().titleLabel.text.split("(")[1].split()[0])
    assert n_stim >= 6
    peak_bin = edges[np.argmax(rate)]                                # its left edge
    assert 0 <= peak_bin < 30                                        # fires just after each stimulus
    ap.chart("Spike-triggered average").sta_channel.setCurrentIndex(ap.chart("Spike-triggered average").sta_channel.findData("Stim"))
    t, mean = ap.chart("Spike-triggered average").plot.getPlotItem().listDataItems()[-1].getData()
    assert mean[t < 0].max() > 0.5                                   # the stimulus comes before the spike


def test_cycles_of_a_sine_line_up_by_phase():
    t = np.arange(FS) / FS
    y = 2 * np.sin(2 * np.pi * 10 * t) + 0.02 * np.random.default_rng(1).normal(size=FS)
    bounds = A.cycle_bounds(y)
    assert len(bounds) in (8, 9)                                     # noise can't start a cycle
    assert np.diff(bounds, axis=1) == pytest.approx(FS / 10, rel=0.02)
    rows = A.cycle_rows(y, bounds)
    assert rows.shape == (len(bounds), A.CYCLE_POINTS)
    assert rows.mean(0)[90] == pytest.approx(2, abs=0.05)            # the top of the sine at 90°
    k, deg = A.spike_phases(bounds[3, 0] / FS + np.array([0.025]), bounds, FS)
    assert k[0] == 3 and deg[0] == pytest.approx(90, abs=2)
    assert A.spike_phases(np.array([bounds[-1, 1] / FS + 0.001]), bounds, FS)[0].size == 0


def test_cycles_leave_out_gaps_and_noise():
    t = np.arange(FS) / FS
    y = np.sin(2 * np.pi * 10 * t)
    y[(t > 0.4) & (t < 0.6)] = 0                                     # a pause between two bursts
    bounds = A.cycle_bounds(y)
    assert len(bounds) == 6 and np.diff(bounds, axis=1).max() < FS / 10 * 1.1
    assert len(A.cycle_bounds(np.random.default_rng(2).normal(size=FS))) == 0
    assert len(A.cycle_bounds(np.zeros(FS))) == 0


def _custom_shape(n_repeats=8, rest=0.04, noise=0.02):
    """A shape that rises twice per repeat, with a pause after it."""
    s = np.arange(int(0.08 * FS)) / FS
    one = np.exp(-((s - 0.015) / 0.004) ** 2) + 0.7 * np.exp(-((s - 0.05) / 0.008) ** 2)
    one = np.r_[one, np.zeros(int(rest * FS))]
    y = np.r_[np.zeros(int(0.03 * FS)), np.tile(one, n_repeats), np.zeros(int(0.05 * FS))]
    return y + noise * np.random.default_rng(3).normal(size=len(y)), len(one)


def test_repeats_of_a_custom_shape_are_found_by_shape():
    y, n = _custom_shape()
    r = A.find_repeats(y, FS)
    assert r.method == "shape" and len(r.bounds) == 8
    assert np.diff(r.bounds, axis=1) == pytest.approx(n, abs=n * 0.02)  # not one per rise
    assert len(A.cycle_bounds(y)) != 8                               # which crossings would get wrong


def test_repeats_by_hand_fall_back_and_one_offs_have_none():
    y, n = _custom_shape()
    r = A.find_repeats(y, FS, length_s=n / FS, start_s=0.03)
    assert r.method == "hand" and len(r.bounds) == 8
    t = np.arange(FS) / FS
    chirp = np.sin(2 * np.pi * (5 * t + 10 * t ** 2))                # no one length: rises still work
    assert A.find_repeats(chirp, FS).method == "crossing"
    pulse = np.where((t > 0.3) & (t < 0.31), 1.0, 0.0)
    assert len(A.find_repeats(pulse, FS).bounds) == 0
    assert len(A.find_repeats(np.random.default_rng(1).normal(size=FS), FS).bounds) == 0


def test_a_repeat_unlike_the_others_is_left_out_and_counted():
    y, n = _custom_shape()
    start = int(0.03 * FS) + 3 * n
    y[start:start + n] = -y[start:start + n] * 0.6 + 0.3 * np.sin(np.arange(n) / 40)
    r = A.find_repeats(y, FS, length_s=n / FS, start_s=0.03)
    assert len(r.bounds) == 7


def test_fits_clean_up_a_noisy_coarse_stimulus():
    t = np.arange(FS) / FS
    rng = np.random.default_rng(5)

    def recorded(y):
        return np.round((y + rng.normal(0, 0.02, len(y))) / 0.05) * 0.05    # noise, then 50 mV steps
    steps = np.where((t > 0.2) & (t < 0.5), 1.0, 0.0) + np.clip((t - 0.6) / 0.2, 0, 1) * (t < 0.8)
    fit = A.fit_stimulus(recorded(steps), FS, "Piecewise linear")
    assert np.abs(fit - steps).mean() < 0.3 * np.abs(recorded(steps) - steps).mean()
    assert abs(np.argmax(np.diff(fit)) - 0.2 * FS) < 20              # the step stays sharp, in the right place
    sine = np.sin(2 * np.pi * 10 * t)
    noisy = recorded(sine)
    assert np.abs(A.fit_stimulus(noisy, FS, "Sine") - sine).mean() < 0.3 * np.abs(noisy - sine).mean()
    y, n = _custom_shape(noise=0.05)
    clean, _ = _custom_shape(noise=0)
    for kind in ("Repeat average", "Harmonics", "Smooth"):
        assert np.abs(A.fit_stimulus(y, FS, kind) - clean).mean() < np.abs(y - clean).mean()
    assert A.fit_stimulus(y, FS, "Off") is not None


def test_a_sine_fit_keeps_a_pause_between_bursts():
    t = np.arange(FS) / FS
    y = np.sin(2 * np.pi * 10 * t)
    y[(t > 0.4) & (t < 0.6)] = 0
    fit = A.fit_stimulus(y, FS, "Sine")
    assert np.abs(fit[(t > 0.42) & (t < 0.58)]).max() < 0.01
    assert np.abs(fit - y).mean() < 0.01


def test_vector_strength():
    assert A.vector_strength(np.full(20, 90.0)) == pytest.approx((1, 90))
    assert A.vector_strength(np.arange(0, 360, 10.0))[0] == pytest.approx(0, abs=1e-9)


def test_a_sine_fit_reaches_the_first_and_last_cycles():
    t = np.arange(FS) / FS
    clean = np.sin(2 * np.pi * 5 * t)
    steps = np.round(clean * 40) / 40                                # a coarse recording
    fit = A.fit_stimulus(steps, FS, "Sine")
    assert np.abs(fit - clean).max() < 0.02 and not np.isclose(fit, steps, atol=1e-12).mean() > 0.05


def test_a_recorded_stimulus_lines_up_with_what_was_sent():
    from neusci_daq.stim import Stimulus
    s = Stimulus(shape="sine", amplitude_v=2, freq_hz=20, duration_ms=500, delay_ms=50)
    rate = 5000.0
    wave = s.waveform(rate)
    sent = A.Sent(0.12, wave, rate, *s.repeat())
    n = int(len(wave) / rate * FS)
    y = np.zeros(FS)
    at = int(0.2 * FS)
    shape = np.interp(np.arange(n) / FS, np.arange(len(wave)) / rate, wave)
    y[at:at + n] += 0.003 * shape + 0.001
    noisy = np.round((y + np.random.default_rng(0).normal(0, 0.0001, FS)) * 2000) / 2000
    found = A.line_up(noisy, FS, sent)
    assert abs(found[0] - at) <= 1 and found[1] == pytest.approx(0.003, rel=0.05)
    fit = A.fit_stimulus(noisy, FS, "Known waveform", sent=(sent,))
    assert np.abs(fit[at:at + n] - y[at:at + n]).max() < 0.0003
    r = A.find_repeats(noisy, FS, sent=(sent,))
    assert r.method == "known" and len(r.bounds) == 10
    assert r.bounds[0, 0] == pytest.approx(at + 0.05 * FS, abs=2)
    assert np.diff(r.bounds, axis=1) == pytest.approx(0.05 * FS)
    assert A.line_up(np.zeros(FS), FS, sent) is None
    assert A.fit_stimulus(noisy, FS, "Known waveform") == pytest.approx(noisy)     # nothing saved: left alone


def test_derivatives_of_a_sine():
    t = np.arange(FS) / FS
    y = np.sin(2 * np.pi * 5 * t)
    assert A.derivative(y, FS)[200:-200].max() == pytest.approx(2 * np.pi * 5, rel=0.01)
    assert A.derivative(y, FS, 2)[200:-200].max() == pytest.approx((2 * np.pi * 5) ** 2, rel=0.02)
    assert A.rate_trace(np.array([0.2, 0.5]), 1.0, 1000).sum() / 1000 == pytest.approx(2)


# ---------------------------------------------------------------- grouping by stimulus
def test_signature_and_notes():
    fs = 20000
    y = 0.002 * np.random.default_rng(0).standard_normal(fs)
    for k in range(3):
        y[2000 + k * 1000:2000 + k * 1000 + 20] += 2.0
    sig = A.pulse_signature(y, fs)
    assert sig == pytest.approx((3, 2.0, 1.0, 50.0), abs=0.01)
    assert A.describe_signature(sig) == "Train of 3 pulses of 2 V, 1 ms, every 50 ms"
    y2 = 0.002 * np.random.default_rng(2).standard_normal(fs)
    for t in (1000, 6000, 11000, 16000):                             # a pulse repeated every 250 ms
        y2[t:t + 20] += 1.0
    assert A.pulse_signature(y2, fs) == pytest.approx((1, 1.0, 1.0, 0.0), abs=0.01)   # one pulse, any repeats
    assert A.describe_signature((1, 1.0, 1.0, 0.0)) == "Pulse of 1 V, 1 ms"
    assert A.same_stimulus(A.pulse_signature(y * 1.004, fs), sig, fs)    # a little different: the same
    assert A.same_stimulus((1, 1.0, 0.95, 0.0), (1, 1.0, 0.9, 0.0), fs)  # a sample's jitter in width
    assert not A.same_stimulus((1, 1.0, 1.0, 0.0), (1, 2.0, 1.0, 0.0), fs)
    assert not A.same_stimulus((1, 1.0, 1.0, 0.0), (3, 1.0, 1.0, 50.0), fs)
    keys = A.cluster_signatures({0: (1, 1.0, 0.93, 0.0), 1: (1, 1.0, 0.9, 0.0), 2: None, 3: (1, 2.0, 0.9, 0.0)}, fs)
    assert keys[0] == keys[1] != keys[3] and keys[2] is None
    assert A.pulse_signature(0.002 * np.random.default_rng(1).standard_normal(fs), fs) is None
    assert A.stimulus_notes([(0, "Stim: Pulse 2 V, 1 ms"), (1, "Stim: Pulse 2 V, 1 ms"), (2, "note"),
                             (3, "Trial 4/20: a = 1 V")]) == ("Pulse 2 V, 1 ms", "a = 1 V")
    groups = A.group_blocks({0: "x", 1: None, 2: "y", 3: "x"}, {"x": "X", "y": "Y"})
    assert groups == [("X", [0, 3]), ("Y", [2]), ("No stimulus found", [1])]


def two_stimulus_session(app, win):
    """Blocks 1-2: 1 V pulses; blocks 3-4: 2 V pulses (every 0.25 s)."""
    from neusci_daq.stim import Stimulus
    net = sim.default_network()
    net.recordings = [sim.Recording("ai1", 0, "extracellular", noise_v=0.02)]
    net.cells[0].stimulus = 40
    win.cfg.sim_network = net
    win.apply_setup(sample_rate=20000)
    assert win.set_stim_monitor("ai2")
    win.stim_repeat_s = 0.25
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    for amp in (1, 1, 2, 2):
        win.set_stimulus(Stimulus(shape="pulse", amplitude_v=amp, width_ms=1))
        win.set_repeat(True)
        win.btn_rec.setChecked(True)
        pump(app, 0.8)
        win.btn_rec.setChecked(False)
        win.set_repeat(False)
        pump(app, 0.1)
    win.btn_run.setChecked(False)


def test_blocks_are_grouped_by_their_stimulus(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    raw = ap.raw
    assert ap.stim_channel.currentData() == "Stim"                  # a stimulus channel: exact times
    ap.stim_channel.setCurrentIndex(0)                               # None: the notes
    assert [(label, blocks) for label, blocks in ap.groups] == \
        [("Pulse 1 V, 1 ms", [0, 1]), ("Pulse 2 V, 1 ms", [2, 3])]
    ap.stim_channel.setCurrentIndex(ap.stim_channel.findData("Stim"))    # the same from the stimulus wire
    assert [blocks for _, blocks in ap.groups] == [[0, 1], [2, 3]]
    assert ap.groups[1][0].startswith("Pulse of 2 V")
    ap.select_blocks([2, 3])                                         # just the 2 V blocks
    assert raw.chosen_or_none() == [2, 3]
    assert trials_shown(raw) == 2
    ap.select_blocks([])                                             # all blocks, then compare groups
    raw.channel.setCurrentText("Ch2")
    raw.find()
    ap.show_layout("Response")
    pump(app, 0.2)
    ap.chart("Stimulus-triggered rate").trigger.setCurrentIndex(ap.chart("Stimulus-triggered rate").trigger.findData("Stim"))
    curves = ap.chart("Stimulus-triggered rate").plot.getPlotItem().listDataItems()
    assert len(curves) == 2 and "by stimulus group" in ap.chart("Stimulus-triggered rate").plot.getPlotItem().titleLabel.text



def test_stimulus_channel_is_set_after_recording_and_remembered(app, win, tmp_path):
    """The stimulus channel is picked after recording (None falls back to the notes),
    the other tabs follow it, and it's remembered for the session."""
    from neusci_daq.gui import MainWindow
    two_stimulus_session(app, win)
    win.open_session(win.session_dir)                                # review it
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.stim_channel.setCurrentIndex(0)                               # None: the notes still group
    assert len(ap.groups) == 2 and "notes" in ap.group_note.text()
    ap.stim_channel.setCurrentIndex(ap.stim_channel.findData("Stim"))
    ap.show_layout("Response")
    assert ap.chart("Stimulus-triggered rate").trigger.currentData() == "Stim"                 # Response follows it
    w2 = MainWindow(make_cfg(tmp_path), settings=win.settings)        # open it again: remembered
    w2.open_session(win.review_dir)
    w2.show()
    w2.tabs.pick(0, "analysis")
    pump(app, 0.2)
    assert w2.analysis_page.stim_channel.currentData() == "Stim"
    w2.close()


def test_raw_view_shows_the_stimulus_groups(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    assert "2 different stimuli" in ap.group_note.text()
    assert len(C(ap.raw, "Trials").group_avgs) == 2 and len(C(ap.raw, "Raster").group_marks.data) == 4   # an average each; row marks
    ap.select_blocks([2, 3])                                         # one group: just its blocks
    assert ap.raw.chosen_or_none() == [2, 3] and C(ap.raw, "Trials").group_avgs == []


def test_isi_histogram():
    blocks, _ = fake_blocks()
    ev = A.find_events(blocks)
    isi = A.intervals_ms(ev)
    assert len(isi) == len(ev.t) - ev.n_units * len(blocks)          # within a unit and a block
    assert isi.min() >= 10                                           # the fake spikes are >= 10 ms apart



def test_analysis_groups_are_the_named_pages(app, win):
    two_stimulus_session(app, win)
    win.rename_page("Pulse of 1 V, 0.9 ms", "Weak")
    win.move_block(3, "Odd one")
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    assert [(n, b) for n, b in ap.groups] == [("Weak", [0, 1]), ("Pulse of 2 V, 0.9 ms", [2]), ("Odd one", [3])]
    assert ap.blocks_panel.tree.topLevelItem(0).text(0) == "⠿ Weak  (2)"


# ---------------------------------------------------------------- a chosen stimulus channel: any waveform
def test_any_waveform_on_the_stimulus_channel_is_a_stimulus():
    from neusci_daq.analysispage import AnalysisPage
    fs = 20000
    t = np.arange(fs) / fs
    rng = np.random.default_rng(0)

    def block(y):
        y = y + 0.0002 * rng.standard_normal(fs)
        return {"data": np.vstack([y]), "channel_names": ["Stim"], "units": ["V"], "sample_rate": float(fs),
                "comments": []}
    blocks = ([block(0.005 * np.sin(2 * np.pi * 5 * t + ph)) for ph in (0, 1, 2)]         # same sine, any phase
              + [block(0.007 * np.sin(2 * np.pi * 5 * t)) for _ in range(2)]            # bigger
              + [block(0.005 * np.sin(2 * np.pi * 10 * t)) for _ in range(2)]           # faster
              + [block(0.01 * np.sign(np.sin(2 * np.pi * 10 * t)))]                     # square
              + [block(np.zeros(fs))])                                                  # nothing
    groups = AnalysisPage._group(blocks, "Stim")
    assert [b for _, b in groups] == [[0, 1, 2], [3, 4], [5, 6], [7], [8]]
    names = [n for n, _ in groups]
    assert names[0].startswith("Sine 5 Hz") and names[2].startswith("Sine 10 Hz")
    assert names[3].startswith("Square wave 10 Hz") and names[4] == "No stimulus found"
    assert len(set(names)) == len(names)                             # every stimulus its own name
    assert A.unique_names({1: "Sine 5 Hz", 2: "Sine 5 Hz", 3: "X"}) == {1: "Sine 5 Hz", 2: "Sine 5 Hz (2)", 3: "X"}


def test_the_lab_recording_sorts_into_its_25_sine_stimuli():
    """AU26_Lab1_Data.mat: 250 blocks, a sine on Channel 1 at 5 frequencies × 5 amplitudes."""
    import glob
    from neusci_daq.analysispage import AnalysisPage
    paths = sorted(glob.glob("data/imported/AU26_Lab1_Data/block_*.h5"))
    if len(paths) != 250:
        pytest.skip("the lab recording isn't on this computer")
    groups = AnalysisPage._group([session.read_block(p) for p in paths], "Channel 1")
    assert len(groups) == 25 and all(len(b) == 10 for _, b in groups)
    assert groups[0][1] == list(range(10)) and groups[0][0].startswith("Sine 5 Hz")


# ---------------------------------------------------------------- spread across trials
def test_mean_spread():
    rows = np.array([[1, 2, np.nan], [3, 4, 5], [5, 6, np.nan]], float)
    mean, sd, sem, n = A.mean_spread(rows)
    assert np.allclose(mean, [3, 4, 5]) and list(n) == [3, 3, 1]
    assert sd[0] == pytest.approx(2.0) and sem[0] == pytest.approx(2 / np.sqrt(3))
    assert np.isnan(sd[2]) and np.isnan(sem[2])                     # one trial: no spread
    centres, rates = A.triggered_rates({0: np.array([0.105, 0.11, 0.62])}, {0: np.array([0.1, 0.6])}, 10, 30, 10)
    assert rates.shape == (2, 4) and list(rates[:, 1]) == [100.0, 0.0]   # 5 ms after: 1 spike in 10 ms


def test_averages_show_and_export_sd_and_sem(app, win, tmp_path):
    import csv
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    raw = ap.raw
    raw.channel.setCurrentText("Ch1")
    ap.spread_box.setCurrentIndex(1)                                 # ± SD
    assert len(C(raw, "Trials").group_avgs) == 2 and len(C(raw, "Trials").bands) == 2 * 3      # each group: line + band
    chosen = raw.chosen_or_none()
    rows = [ap.trace(i, "Ch1") for i in chosen if i in ap.groups[0][1]][:2]
    n = min(len(y) for y in rows)                                    # blocks can differ by a chunk
    Y = np.array([y[:n] for y in rows])
    mean, sd = Y.mean(0), Y.std(0, ddof=1)
    up = C(raw, "Trials").bands[0].getOriginalDataset()[1][:n]
    assert np.allclose(up, mean + sd)                                # the band is mean ± SD
    ap.spread_box.setCurrentIndex(2)                                 # ± SEM
    assert np.allclose(C(raw, "Trials").bands[0].getOriginalDataset()[1][:n], mean + sd / np.sqrt(2))
    out = raw.export_averages(tmp_path / "avg.csv")
    rows = list(csv.reader(out.open()))
    assert rows[0][0] == "time_s" and rows[0][1].startswith(ap.groups[0][0]) and "mean" in rows[0][1]
    assert float(rows[11][1]) == pytest.approx(mean[10], rel=1e-4)
    assert float(rows[11][2]) == pytest.approx(sd[10], rel=1e-4) and rows[11][4] == "2"
    raw.channel.setCurrentText("Ch2")                                # the spike rate gets bands too
    raw.find()
    ap.show_layout("Response")
    pump(app, 0.2)
    ap.chart("Stimulus-triggered rate").trigger.setCurrentIndex(ap.chart("Stimulus-triggered rate").trigger.findData("Stim"))
    fills = [x for x in ap.chart("Stimulus-triggered rate").plot.getPlotItem().items if isinstance(x, __import__("pyqtgraph").FillBetweenItem)]
    assert len(fills) == 2 and "SEM across stimuli" in ap.chart("Stimulus-triggered rate").plot.getPlotItem().titleLabel.text
    ap.show_layout("Spike Sorting")
    ap.spread_box.setCurrentIndex(0)
    assert C(raw, "Trials").bands == []


# ---------------------------------------------------------------- DataPad
def test_datapad_measurements():
    from neusci_daq import datapad as D
    from neusci_daq.datapadview import fmt
    fs = 10000.0
    t = np.arange(int(fs)) / fs
    y = 2 * np.sin(2 * np.pi * 7 * t) + 1
    m = {k: D.measure(k, t, y, fs, [0.1, 0.5, 0.9, 1.5]) for k in D.MEASUREMENTS}
    assert m["mean"] == pytest.approx(1, abs=1e-3) and m["p2p"] == pytest.approx(4, abs=1e-3)
    assert m["rms"] == pytest.approx(np.sqrt(3), abs=1e-3) and m["freq"] == pytest.approx(7, abs=0.01)
    assert m["area"] == pytest.approx(1, abs=1e-3)
    bump = np.exp(-((t - 0.3) / 0.01) ** 2)
    assert D.measure("tmax", t, bump, fs) == pytest.approx(0.3) and D.measure("tmin", t, -bump, fs) == pytest.approx(0.3)
    assert m["rise"] == pytest.approx(4 * np.pi * 7, rel=1e-3) and m["spikes"] == 3
    w = D.window(t, 0.2, 0.3)
    assert t[w][0] == pytest.approx(0.2) and t[w][-1] == pytest.approx(0.3)
    assert D.window(t, None, None) == slice(0, len(t))
    assert D.measure("slope", t[w], 3 * t[w] + 1, fs) == pytest.approx(3)
    assert np.isnan(D.measure("spikes", t, y, fs)) and np.isnan(D.measure("mean", t, np.full(len(t), np.nan), fs))
    assert D.unit_of("slope", "mV") == "mV/s" and D.unit_of("freq", "V") == "Hz"
    assert fmt(0.0374, "V") == "37.4 mV" and fmt(12.5, "V/s") == "12.5 V/s" and fmt(0.002, "s") == "2 ms"
    assert fmt(float("nan"), "V") == "—" and fmt(3, "") == "3"


def test_datapad_measures_blocks_averages_and_groups(app, win, tmp_path):
    import csv
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.main.tabs.pick(0, "datapad")
    pad = ap.datapad
    pump(app, 0.1)
    assert pad.columns == [] and pad.whole.isChecked()               # no columns yet; the whole block
    assert pad.region.getRegion() == pytest.approx((0, ap.blocks[0]["duration_s"]), abs=1e-3)
    pad.add_column("Stim", "max")
    pad.add_column("Stim", "mean")
    pad.whole.setChecked(True)
    fs = ap.blocks[0]["sample_rate"]

    assert pad.add_rows("current") == 1                               # the highlighted block
    assert pad.rows[0]["values"][("Stim", "max")] == pytest.approx(ap.trace(0, "Stim").max())
    assert pad.add_rows("blocks") == 4                                # each block
    assert [r["group"] for r in pad.rows[1:]] == [g for g, b in ap.groups for _ in b]

    pad.clear_rows()
    pad.add_rows("stats")                                             # mean ± spread per group
    assert [r["n"] for r in pad.rows] == [2, 2]
    per = [ap.trace(i, "Stim").max() for i in ap.groups[1][1]]
    assert pad.rows[1]["values"][("Stim", "max")] == pytest.approx(np.mean(per))
    assert pad.rows[1]["spread"][("Stim", "max")] == pytest.approx(np.std(per, ddof=1))
    assert pad.rows[0]["values"][("Stim", "max")] < pad.rows[1]["values"][("Stim", "max")]  # 1 V < 2 V
    ap.spread_box.setCurrentIndex(2)                                  # SEM
    pad.add_rows("stats")
    assert pad.rows[3]["spread"][("Stim", "max")] == pytest.approx(np.std(per, ddof=1) / np.sqrt(2))
    assert "± SEM" in pad.rows[3]["row"]

    pad.clear_rows()
    pad.whole.setChecked(False)                                       # a window: just before the 1st pulse
    pad.t0.setValue(0.0)
    pad.t1.setValue(0.0005)
    assert pad.region.getRegion() == pytest.approx((0.0, 0.0005))
    pad.add_rows("avg")                                               # each group's average trace
    blocks = ap.groups[0][1]
    Y = np.array([ap.trace(i, "Stim")[:int(0.0005 * fs) + 1] for i in blocks])
    assert pad.rows[0]["values"][("Stim", "mean")] == pytest.approx(Y.mean(0).mean(), rel=1e-6)
    assert pad.rows[0]["window"] == "0–0.0005 s"

    pad.region.setRegion((0.1, 0.2))                                  # dragging sets the boxes
    pad.region.sigRegionChangeFinished.emit(pad.region)
    assert (pad.t0.value(), pad.t1.value()) == pytest.approx((0.1, 0.2))

    out = pad.export(tmp_path / "pad.csv")
    rows = list(csv.reader(out.open()))
    assert rows[0][:4] == ["row", "group", "window", "n"] and rows[0][4] == "Stim: Maximum (V)"
    assert len(rows) == 1 + len(pad.rows) and float(rows[1][6]) == pytest.approx(
        pad.rows[0]["values"][("Stim", "mean")], rel=1e-5)
    pad.copy()
    assert app.clipboard().text().splitlines()[0].startswith("row\tgroup\twindow\tn")
    assert pad.table.rowCount() == len(pad.rows) and pad.table.columnCount() == 6


def test_datapad_counts_spikes_found_on_the_raw_tab(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.main.tabs.pick(0, "datapad")
    pad = ap.datapad
    pad.columns = []
    pad.add_column("Ch2", "spikes")
    pad.whole.setChecked(True)
    pad.add_rows("current")
    assert np.isnan(pad.rows[0]["values"][("Ch2", "spikes")])       # nothing found yet
    assert "press Find" in pad.message.text()
    ap.raw.channel.setCurrentText("Ch2")
    ev = ap.raw.find()
    pad.add_rows("blocks")
    assert [r["values"][("Ch2", "spikes")] for r in pad.rows[1:]] == \
        [float(np.sum(ev.block == i)) for i in range(4)]



def test_fitting_models():
    from neusci_daq import fitting as F
    x = np.linspace(-2, 5, 60)
    r = F.fit("poly", x, 0.5 * x ** 3 - 2 * x + 1, 3)
    assert np.allclose(r.params, [1, -2, 0, 0.5], atol=1e-8) and r.r2 == pytest.approx(1)
    assert r.equation == "y = 0.5·x^3 − 2·x + 1"
    assert F.fit("poly", x, 3 * x + 2, 1).equation == "y = 3·x + 2"
    t = np.linspace(0, 0.1, 200)
    assert F.fit("exp", t, 5 * np.exp(-t / 0.02) + 1).params == pytest.approx([5, 0.02, 1], rel=1e-6)
    v = np.linspace(-80, 20, 40)
    assert F.fit("boltzmann", v, 1 / (1 + np.exp((-30 - v) / 6))).params[2:] == pytest.approx([-30, 6], rel=1e-6)
    assert F.fit("power", x[x > 0], 2 * x[x > 0] ** 1.5).params == pytest.approx([2, 1.5])
    with pytest.raises(ValueError, match="at least 4 points"):
        F.fit("poly", [1, 2, 3], [1, 2, 3], 3)
    with pytest.raises(ValueError, match="above zero"):
        F.fit("log", x, x, 1)


def test_datapad_overlays_scatters_fits_and_recomputes(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.main.tabs.pick(0, "datapad")
    pad = ap.datapad
    pump(app, 0.1)
    # the preview overlays the ticked channels
    assert list(pad.show_boxes) == ap.names()
    pad.set_shown(["Ch1", "Ch2", "Stim"])
    on = [n for n, c in pad.traces.items() if c.scene() is not None]
    assert on == ["Ch1", "Ch2", "Stim"]
    pad.show_boxes["Ch2"].setChecked(False)
    assert pad.traces["Ch2"].scene() is None and pad.traces["Ch1"].scene() is not None
    # a repeating window gives a row per range
    pad.columns = []
    pad.add_column("Stim", "max")
    pad.whole.setChecked(False)
    pad.t0.setValue(0.0)
    pad.t1.setValue(0.05)
    pad.every.setValue(0.25)
    dur = ap.blocks[0]["duration_s"]
    assert pad.add_rows("current") == len(np.arange(0, dur, 0.25))
    assert [r["t0"] for r in pad.rows] == pytest.approx(list(np.arange(0, dur, 0.25)))
    # scatter: Y against X, coloured by group
    pad.set_axes("t0", ("Stim", "max"))
    assert pad.sy.currentData() == ("Stim", "max")
    pts = [i for i in pad.scatter.getPlotItem().items if isinstance(i, __import__("pyqtgraph").ScatterPlotItem)]
    assert sum(len(i.getData()[0]) for i in pts) == len(pad.rows)
    # fits: a polynomial on the scatter, an exponential on the trace
    pad.fit_model.setCurrentIndex(pad.fit_model.findData("poly"))
    pad.fit_degree.setValue(2)
    r = pad.run_fit()
    assert r is not None and len(r.params) == 3 and pad.fit_params.rowCount() == 3
    pad.fit_on.setCurrentIndex(1)
    pad.set_shown(["Ch1"])
    pad.fit_model.setCurrentIndex(pad.fit_model.findData("poly"))
    pad.fit_degree.setValue(1)
    assert pad.run_fit() is not None and pad._fit_items[0][0] is pad.preview.getPlotItem()
    pad.clear_fit()
    assert pad.fit_result is None and not pad._fit_items
    # recompute: the same rows, measured again with new columns
    pad.add_column("Stim", "mean")
    n = len(pad.rows)
    assert pad.recompute() == n
    assert all(("Stim", "mean") in r["values"] for r in pad.rows)
    first = pad.rows[0]
    w = slice(0, int(0.05 * ap.blocks[0]["sample_rate"]) + 1)
    assert first["values"][("Stim", "mean")] == pytest.approx(ap.trace(ap.index(), "Stim")[w].mean())


# ---------------------------------------------------------------- Spike Sorting
def test_spike_sorting_shows_the_stimulus_and_leaves_it_out_of_the_channels(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap, raw = win.analysis_page, win.analysis_page.raw
    assert ap.canvas.layouts.currentText() == "Spike Sorting"
    channels = [raw.channel.itemText(i) for i in range(raw.channel.count())]
    assert ap.stim_channel.currentData() == "Stim" and "Stim" not in channels and "Ch2" in channels
    assert C(raw, "Stimulus").plot.isVisible()
    x, y = C(raw, "Stimulus").stim_items[0].getOriginalDataset()
    y = y[0]
    assert len(y) and np.nanmax(y) == pytest.approx(1, abs=0.05)     # the 1 V pulses of block 1
    assert len(C(raw, "Stimulus").stim_onsets.data) == 0            # the trace is enough: no triangles on it
    ap.stim_channel.setCurrentIndex(0)                               # None: the notes
    assert "Stim" in [raw.channel.itemText(i) for i in range(raw.channel.count())]
    assert "notes" in C(raw, "Stimulus").plot.getPlotItem().getAxis("left").labelText


def test_ticking_units_chooses_what_the_charts_show(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    raw = win.analysis_page.raw
    raw.channel.setCurrentText("Ch2")
    ev = raw.find()
    assert ev is not None and len(ev.t) >= 4
    ev.unit = np.arange(len(ev.t)) % 2                               # two units, whatever the sorter made
    raw._show_results()
    raw.page.events_changed()
    assert raw.table.rowCount() == 2
    assert C(raw, "Trials").event_hl.getData()[0] is None or not len(C(raw, "Trials").event_hl.getData()[0])   # nothing yet
    assert len(C(raw, "Trials").marks.data) == len(ev.t)                          # every chosen block's events are marked
    assert raw.table.item(0, 0).checkState() == QtCore.Qt.CheckState.Checked
    raw.table.item(0, 0).setCheckState(QtCore.Qt.CheckState.Unchecked)   # leave out unit 1
    mine = ev.unit == 1
    xs = C(raw, "Trials").event_hl.getData()[0]
    assert np.isfinite(xs).sum() > 0 and mine.sum() == np.isnan(xs).sum()   # one segment per event, in any block
    assert len(C(raw, "Trials").marks.data) == mine.sum() and len(C(raw, "Raster").raster_dots.data) == mine.sum()   # the others are gone
    assert len(C(raw, "AP sizes").plot.getPlotItem().listDataItems()) == 1   # just that unit on the charts
    assert set(C(raw, "Rate")._rate_items) == {1}
    C(raw, "Waveforms").norm_waves.setChecked(True)                                  # redrawn: the ticks stay
    assert raw.shown_units() == {1} and raw.table.item(0, 0).checkState() == QtCore.Qt.CheckState.Unchecked
    raw.show_units(set())                                            # none: nothing to see
    assert len(C(raw, "Trials").marks.data) == 0 and not C(raw, "Rate")._rate_items
    raw.show_units({0, 1})
    assert len(C(raw, "Trials").marks.data) == len(ev.t) and None in C(raw, "Rate")._rate_items


def test_normalize_options(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    raw = win.analysis_page.raw
    raw.channel.setCurrentText("Ch2")
    ev = raw.find()
    assert ev is not None and len(ev.t)
    C(raw, "AP sizes").norm.setChecked(True)
    C(raw, "Intervals").norm.setChecked(True)
    C(raw, "Waveforms").norm_waves.setChecked(True)
    C(raw, "Rate").norm_rate.setChecked(True)
    for pw in (C(raw, "AP sizes").plot, C(raw, "Intervals").plot):
        sums = [item.getData()[1].sum() for item in pw.getPlotItem().listDataItems()]
        assert max(sums) == pytest.approx(1)                         # a fraction of the unit's events (a
                                                                     # small unit may have no intervals)
    peak = int(np.argmin(np.abs(ev.wave_t_ms)))
    mean = C(raw, "Waveforms").plot.getPlotItem().listDataItems()[1].getData()[1]
    assert abs(mean[peak]) == pytest.approx(1, abs=1e-6)             # the unit's average peak is 1
    shown = C(raw, "Waveforms").plot.getPlotItem().listDataItems()[0].getData()[1]
    tall = ev.waves[ev.unit == 0][:, peak]
    if np.ptp(tall):                                                 # spikes keep their sizes within a unit
        spikes = shown.reshape(-1, len(ev.wave_t_ms) + 1)[:, peak]
        assert np.ptp(np.abs(spikes)) > 0
    rate = C(raw, "Rate").plot.getPlotItem().listDataItems()[-1].getData()[1]
    assert np.nanmax(rate) == pytest.approx(1)


def test_datapad_overlaid_axes_share_the_time_axis(app, win):
    two_stimulus_session(app, win)
    win.resize(1200, 800)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.main.tabs.pick(0, "datapad")
    pad = ap.datapad
    pad.set_shown(["Ch1", "Ch2"])
    ap.step(1)                                                       # redrawn for another block
    pump(app, 0.2)
    main = pad.preview.getPlotItem().vb
    for vb in pad.multi._extras():
        assert vb.viewRange()[0] == pytest.approx(main.viewRange()[0])
        assert vb.sceneBoundingRect().width() == pytest.approx(main.sceneBoundingRect().width(), abs=1)


def test_datapad_drag_picks_a_window_and_traces_stay_sharp(app, win):
    two_stimulus_session(app, win)
    win.resize(1200, 800)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.main.tabs.pick(0, "datapad")
    pad = ap.datapad
    pump(app, 0.1)
    vb = pad.preview.getPlotItem().vb
    vb.on_drag_select(0.1, 0.3, False)                              # dragging: the shading follows
    assert pad.whole.isChecked() and pad.region.getRegion() == pytest.approx((0.1, 0.3))
    vb.on_drag_select(0.1, 0.3, True)                               # let go: that's the window
    assert not pad.whole.isChecked() and pad.bounds() == pytest.approx((0.1, 0.3))
    pad.set_window(None, None)                                      # double-click: the whole block again
    assert pad.whole.isChecked() and pad.bounds() == (None, None)
    pad.set_shown(["Ch1", "Ch2"])
    ap.step(1)                                                      # another block
    pump(app, 0.3)
    ch2 = pad.traces["Ch2"]
    shown = len(ch2.xData) if ch2.xData is not None else 0
    assert shown > 400                                              # drawn at the view's width, not ~50 px


def trials_shown(raw):
    return sum(item.getOriginalDataset()[1].shape[0] for item in C(raw, "Trials").trial_items)


def C(raw, kind):
    """The first chart of this kind on the canvas."""
    return raw.page.chart(kind)


def _spike_sorting(app, win):
    stim_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.raw.channel.setCurrentText("Ch2")
    return ap, ap.raw


def test_the_blocks_panel_picks_the_blocks_on_every_sub_tab(app, win):
    ap, raw = _spike_sorting(app, win)
    assert ap.blocks_panel.isVisible() and ap.blocks_panel.title.text().startswith("<b>Trials</b>")
    ap.main.tabs.pick(0, "datapad")
    pump(app, 0.05)
    assert ap.blocks_panel.isVisible()
    ap.show_layout("Spike Sorting")
    tree = ap.blocks_panel.tree
    for item in ap.blocks_panel.items():                             # as a drag across two blocks would
        data = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        item.setSelected(data[0] == "block" and data[1] in (0, 2))
    pump(app, 0.3)
    assert ap.picked == [0, 2] and raw.chosen_or_none() == [0, 2]
    assert tree.selectedItems()
    ap.source_changed()                                              # a refill keeps the selection
    assert ap.blocks_panel.selected_blocks() == [0, 2]
    ap.blocks_panel.btn_all.click()
    pump(app, 0.05)
    assert ap.picked == [] and not tree.selectedItems() and len(raw.chosen_or_none()) == len(ap.blocks)


def test_the_raster_has_a_row_for_each_chosen_block(app, win):
    ap, raw = _spike_sorting(app, win)
    ap.select_blocks([0, 2])
    ev = raw.find()
    vb = C(raw, "Raster").plot.getPlotItem().vb
    assert vb.viewRange()[1] == pytest.approx([0.5, 2.5])
    assert set(C(raw, "Raster").raster_dots.data["y"]) <= {1, 2} and C(raw, "Raster").raster_dots.opts["size"] == 2
    assert np.array_equal(C(raw, "Raster").raster_dots.data["y"], np.where(ev.block == 0, 1, 2))
    assert not C(raw, "Raster").plot.getPlotItem().getAxis("left").style["showValues"]   # no block numbers
    ap.current.setValue(2)                                           # not chosen: no highlighted row
    assert not C(raw, "Raster").cur_band.isVisible()
    ap.current.setValue(3)
    assert C(raw, "Raster").cur_band.isVisible() and C(raw, "Raster").cur_band.getRegion() == pytest.approx((1.5, 2.5))


def test_the_central_plots_share_one_time_axis_that_stops_at_the_data(app, win):
    ap, raw = _spike_sorting(app, win)
    raw.find()
    pump(app, 0.05)
    plots = [C(raw, "Trials").plot, C(raw, "Stimulus").plot, C(raw, "Raster").plot, C(raw, "Rate").plot]
    x = lambda pw: pw.getPlotItem().vb.viewRange()[0]
    end = C(raw, "Trials")._end
    for pw in plots:
        assert x(pw) == pytest.approx([0, end], abs=1e-6)
    C(raw, "Rate").plot.getPlotItem().vb.zoom_time(0.5, end / 2)           # zoom one: they all follow
    pump(app, 0.05)
    assert x(C(raw, "Trials").plot)[1] - x(C(raw, "Trials").plot)[0] == pytest.approx(end / 2, rel=0.01)
    for pw in plots:
        assert x(pw) == pytest.approx(x(C(raw, "Trials").plot))
    C(raw, "Raster").plot.getPlotItem().vb.zoom_time(100, end / 2)              # out: never past the data
    pump(app, 0.05)
    for pw in plots:
        assert x(pw) == pytest.approx([0, end], abs=1e-6)


def test_spikes_per_second_during_a_block():
    ev = A.Events(block=np.array([0, 0, 1, 1]), t=np.array([0.1, 0.2, 0.1, 1.2]), unit=np.zeros(4, int),
                  amplitude=np.ones(4), waves=np.empty((4, 0)), wave_t_ms=np.empty(0), level=1.0, duration_s=3.0)
    edges, rates = A.block_rate(ev, 2, 1.5, 1.0)
    assert edges.tolist() == [0, 1, 1.5]                             # the last bin stops at the data
    assert rates[None].tolist() == [1.5, 1.0]                        # the mean of the 2 blocks


def test_the_stimulus_is_above_the_blocks_and_the_block_bar_leaves_spike_sorting(app, win):
    ap, raw = _spike_sorting(app, win)
    tops = [C(raw, k).dock.y() for k in ("Stimulus", "Trials", "Raster")]
    assert tops == sorted(tops) and len(set(tops)) == 3
    assert not ap.block_bar.isVisible()
    ap.show_layout("Response")
    pump(app, 0.1)
    assert not ap.block_bar.isVisible()                              # Response shows the chosen blocks too
    ap.main.tabs.pick(0, "datapad")
    pump(app, 0.1)
    assert ap.block_bar.isVisible() and ap.blocks_panel.isVisible()  # with the browser, borrowed from Analysis
    ap.main.tabs.pick(0, "analysis")
    pump(app, 0.1)
    assert not ap.block_bar.isVisible()
    ap._block_clicked(("block", 1))                                  # the Blocks panel still picks it
    assert ap.index() == 1
    ap.select_blocks([2, 3])                                         # and it stays among the chosen ones
    assert ap.index() == 2


def test_rate_bins_are_in_the_rate_charts_settings(app, win):
    ap, raw = _spike_sorting(app, win)
    chart = C(raw, "Rate")
    ap.canvas.open_settings(chart)
    pump(app, 0.05)
    assert chart.rate_bin.isVisible() and ap.windows["chart"].isVisible()
    raw.find()
    n = len(chart.plot.getPlotItem().listDataItems()[0].getData()[0])
    chart.rate_bin.setValue(0.05)
    assert len(chart.plot.getPlotItem().listDataItems()[0].getData()[0]) > n


def test_a_page_shows_its_average_and_blocks_their_trials(app, win):
    ap, raw = _spike_sorting(app, win)
    name = ap.shown[0][0]
    ap.select_pages([name])
    assert len(C(raw, "Trials").group_avgs) == 1 and not C(raw, "Trials").trial_items
    assert C(raw, "Trials").group_avgs[0].opts["pen"].color() == QtGui.QColor(ap.group_color(0))
    ap.select_blocks([0, 1])
    assert not C(raw, "Trials").group_avgs and trials_shown(raw) == 2
    alpha = C(raw, "Trials").trial_items[0].opts["pen"].color().alpha()
    ap.select_blocks([0])
    assert C(raw, "Trials").trial_items[0].opts["pen"].color().alpha() == 255 > alpha   # one on its own is solid


def test_page_colours_are_editable_and_saved_with_the_session(app, win):
    ap, raw = _spike_sorting(app, win)
    name = ap.shown[0][0]
    ap.set_page_color(name, "#123456")
    ap.select_pages([name])
    assert C(raw, "Trials").group_avgs[0].opts["pen"].color() == QtGui.QColor("#123456")
    assert pages.load(ap._folder())["colors"] == {name: "#123456"}
    ap.set_page_color(name, None)
    assert ap.group_color(0) != "#123456"


def test_colour_maps_run_across_pages_and_trials(app, win):
    ap, raw = _spike_sorting(app, win)
    ap.page_map.setCurrentText("Viridis")
    assert ap.group_color(0) == cm.sample("Viridis", 0).name()
    assert pages.load(ap._folder())["page_map"] == "Viridis"
    ap.select_blocks([0, 1, 2])
    ap.trial_map.setCurrentText("Opacity")
    alphas = sorted(item.opts["pen"].color().alpha() for item in C(raw, "Trials").trial_items)
    assert len(alphas) == 3 and alphas[0] < alphas[-1] == 255
    ap.trial_map.setCurrentText("Plasma")
    assert len({item.opts["pen"].color().name() for item in C(raw, "Trials").trial_items}) == 3


def test_the_stimulus_chart_shows_every_chosen_block(app, win):
    ap, raw = _spike_sorting(app, win)
    assert sum(s.getOriginalDataset()[1].shape[0] for s in C(raw, "Stimulus").stim_items) == len(ap.blocks)
    ap.select_blocks([0, 1])
    assert sum(s.getOriginalDataset()[1].shape[0] for s in C(raw, "Stimulus").stim_items) == 2


def test_the_stimulus_is_the_colour_of_its_response(app, win):
    ap, raw = _spike_sorting(app, win)
    name = ap.shown[0][0]
    ap.select_pages([name])
    assert C(raw, "Stimulus").stim_items[0].opts["pen"].color() == C(raw, "Trials").group_avgs[0].opts["pen"].color()
    ap.trial_map.setCurrentText("Plasma")
    ap.select_blocks([0, 1])
    trials = {item.opts["pen"].color().name() for item in C(raw, "Trials").trial_items}
    assert {item.opts["pen"].color().name() for item in C(raw, "Stimulus").stim_items} == trials and len(trials) == 2


def test_clear_stops_searching_until_find(app, win):
    ap, raw = _spike_sorting(app, win)
    raw.btn_find.click()
    assert raw.events is not None
    raw.btn_clear.click()
    assert raw.events is None and raw.table.rowCount() == 0
    raw.dead.setValue(2.0)
    pump(app, 0.5)
    assert raw.events is None                                        # paused
    raw.btn_find.click()
    assert raw.events is not None
    raw.dead.setValue(3.0)
    pump(app, 0.5)
    assert raw.events is not None                                    # and back on


def test_raster_points_keep_their_size_and_it_can_be_set(app, win):
    ap, raw = _spike_sorting(app, win)
    ev = raw.find()
    ev.unit = np.arange(len(ev.t)) % 2
    raw._show_results()
    raw.page.events_changed()
    C(raw, "Trials").mark_size.setValue(4)
    assert C(raw, "Trials").marks.opts["size"] == 4
    C(raw, "Trials").mark_size.setValue(9)
    C(raw, "Raster").point_size.setValue(5)
    assert C(raw, "Raster").raster_dots.opts["size"] == 5
    raw.find()
    raw.show_units({0})
    assert C(raw, "Raster").raster_dots.opts["size"] == 5
    C(raw, "Trials").mark_size.setValue(4)
    assert C(raw, "Trials").marks.opts["size"] == 4
    C(raw, "Trials").mark_size.setValue(9)
    raw.show_units(raw.shown_units() | set(range(raw.events.n_units)))


def test_the_threshold_says_what_it_is(app, win):
    ap, raw = _spike_sorting(app, win)
    raw.find()
    assert "threshold" in C(raw, "Trials").thr_label.toPlainText()


def test_the_threshold_can_be_hidden_without_changing_the_search(app, win):
    ap, raw = _spike_sorting(app, win)
    assert C(raw, "Trials").show_thr.isChecked()
    found = len(raw.find().t)
    C(raw, "Trials").show_thr.setChecked(False)
    assert not C(raw, "Trials").thr_line.isVisible()
    assert len(raw.find().t) == found
    assert not C(raw, "Trials").thr_line.isVisible()                              # a new search leaves it hidden
    C(raw, "Trials").show_thr.setChecked(True)
    assert C(raw, "Trials").thr_line.isVisible()


def test_the_raster_shows_as_many_rows_as_blocks_are_chosen(app, win):
    ap, raw = _spike_sorting(app, win)
    vb = C(raw, "Raster").plot.getPlotItem().vb
    for pick in ([0], [0, 1], [0, 1, 2], [1], [0, 2]):
        ap.select_blocks(pick)
        pump(app, 0.05)
        assert vb.viewRange()[1] == [0.5, len(pick) + 0.5]


def test_pca_map_shows_every_unit_even_while_one_is_highlighted(app, win):
    ap, raw = _spike_sorting(app, win)
    ev = raw.find()
    ev.unit = np.arange(len(ev.t)) % 2
    raw._show_results()
    raw.page.events_changed()
    p = C(raw, "PCA map").plot.getPlotItem()
    assert len(p.listDataItems()) == 2 and "PC1" in p.getAxis("bottom").labelText
    raw.show_units({0})
    assert all(item.isVisible() for item in p.listDataItems())
    raw.show_units({0, 1})


def test_charts_open_close_and_are_remembered_with_their_layout(app, win, tmp_path):
    from neusci_daq.gui import MainWindow
    ap, raw = _spike_sorting(app, win)
    canvas = ap.canvas
    canvas.close_chart(C(raw, "Intervals"))
    pump(app, 0.1)
    assert C(raw, "Intervals") is None and C(raw, "AP sizes").plot.isVisible()
    canvas.add("Intervals")
    pump(app, 0.1)
    assert C(raw, "Intervals").plot.isVisible()
    canvas.clear_all()
    pump(app, 0.1)
    assert not canvas.here() and canvas.space.hint.isVisible()
    canvas.add("Raster")
    canvas.save()
    w2 = MainWindow(make_cfg(tmp_path), settings=win.settings)        # open it again: remembered
    w2._ask_unsaved = lambda: "discard"
    assert [c.kind for c in w2.analysis_page.canvas.here()] == ["Raster"]
    w2.close()
    canvas.reset()
    pump(app, 0.1)
    assert C(raw, "Trials") is not None and len(canvas.here()) == 8


def test_a_chart_you_open_stays_and_says_why_it_is_empty(app, win):
    ap, raw = _spike_sorting(app, win)
    ap.stim_channel.setCurrentIndex(0)                               # no stimulus channel
    for c in ap.canvas.here():
        if c.kind == "Stimulus":
            ap.canvas.close_chart(c)
    ap.groups_changed()
    pump(app, 0.1)
    if not ap.how():
        chart = ap.canvas.add("Stimulus")
        pump(app, 0.2)
        assert chart.dock.isVisible() and chart.plot.width() > 50
        assert "Stimulus channel" in chart.plot.getPlotItem().titleLabel.text
    copy = ap.canvas.copy(C(raw, "Raster"))
    assert not copy.auto_hide
    fit = ap.canvas.add("Stimulus fit")
    pump(app, 0.2)
    assert fit.dock.isVisible() and "Stim fit" in fit.fit_plot.getPlotItem().titleLabel.text


def test_any_chart_can_be_open_twice_with_its_own_settings(app, win):
    ap, raw = _spike_sorting(app, win)
    canvas = ap.canvas
    ev = raw.find()
    ev.unit = np.arange(len(ev.t)) % 2
    raw._show_results()
    ap.events_changed()
    first = C(raw, "Raster")
    first.point_size.setValue(4)
    second = canvas.copy(first)
    pump(app, 0.2)
    assert second.name == "Raster 2" and second.point_size.value() == 4 and second.isVisible()
    second.point_size.setValue(7)
    assert first.point_size.value() == 4
    assert first.raster_dots.opts["size"] == 4 and second.raster_dots.opts["size"] == 7
    second.units_box.setCurrentIndex(1)                              # its own units
    second.show_unit(0, False)
    assert len(second.raster_dots.data) == (ev.unit == 1).sum() and len(first.raster_dots.data) == len(ev.t)
    raw.show_units({0})                                              # the others follow the ticks; it doesn't
    assert len(first.raster_dots.data) == (ev.unit == 0).sum()
    assert len(second.raster_dots.data) == (ev.unit == 1).sum()
    assert "Unit 2" in second.dock.bar.tag.text()
    for kind in KINDS:                                               # every kind can be opened again
        chart = canvas.add(kind)
        pump(app, 0.02)
        assert canvas.copy(chart).kind == kind
    assert ap.canvas.first("Raster") is first


def test_a_pinned_chart_keeps_its_blocks(app, win):
    ap, raw = _spike_sorting(app, win)
    ap.select_blocks([0])
    raster = C(raw, "Raster")
    raster.set_pinned(True)
    assert raster.pin["stems"] == [ap._paths[0].stem] and raster.dock.bar.btn_pin.isChecked()
    ap.select_blocks([1, 2])
    pump(app, 0.1)
    assert raster.blocks() == [0] and C(raw, "Trials").blocks() == [1, 2]
    ev = raw.find()
    assert set(ev.block.tolist()) <= {0, 1, 2} and 0 in ev.block.tolist()   # its blocks are searched too
    assert len(raster.raster_dots.data) == (ev.block == 0).sum()
    assert len(C(raw, "Trials").marks.data) == (ev.block != 0).sum()
    raster.set_pinned(False)
    assert raster.blocks() == [1, 2]


def test_a_copy_is_the_same_size_as_its_chart(app, win):
    ap, raw = _spike_sorting(app, win)
    win.resize(1400, 900)
    pump(app, 0.2)
    first = C(raw, "Raster")
    width = first.dock.width()
    second = ap.canvas.copy(first)
    pump(app, 0.2)
    assert abs(second.dock.width() - first.dock.width()) <= 12
    assert abs(second.dock.height() - first.dock.height()) <= 12
    assert second.dock.width() > 0.55 * width                        # room comes from the charts beside them


def test_stacked_charts_can_be_dragged_out_of_their_tabs(app, win):
    ap, raw = _spike_sorting(app, win)
    assert ap.canvas.space.dockOptions() & ap.canvas.space.DockOption.GroupedDragging


def test_the_repeats_show_with_whichever_phase_chart_was_clicked(app, win):
    ap, raw = _spike_sorting(app, win)
    ap.show_layout("Response")
    pump(app, 0.1)
    one, other = ap.chart("Spikes per phase"), ap.chart("Stimulus cycle")
    ap.canvas.open_settings(one)
    pump(app, 0.05)
    assert ap.repeats.cycle_len.isVisible() and ap.repeats.parent() is one.options
    ap.canvas.open_settings(other)
    assert ap.repeats.parent() is other.options
    ap.canvas.close_chart(other)                                     # they outlive it
    pump(app, 0.05)
    ap.repeats.cycle_len.setValue(50)
    assert ap.repeats.setting()[0] == pytest.approx(0.05)


def test_your_own_layouts_keep_their_charts_and_settings(app, win):
    ap, raw = _spike_sorting(app, win)
    canvas = ap.canvas
    C(raw, "Raster").point_size.setValue(6)
    canvas.copy(C(raw, "Raster")).name_box.setText("Second")
    canvas.here()[-1]._renamed()
    canvas.save_as("Lab 3")
    pump(app, 0.2)
    assert canvas.layouts.currentText() == "Lab 3" and canvas.space.name == "Lab 3"
    assert sum(c.kind == "Raster" for c in canvas.here()) == 2
    assert {c.name for c in canvas.here() if c.kind == "Raster"} == {"Raster", "Second"}
    assert all(c.point_size.value() == 6 for c in canvas.here() if c.kind == "Raster")
    ap.show_layout("Waveforms")
    ap.show_layout("Lab 3")
    canvas.reset()                                                   # your own: deleted
    assert canvas.layouts.findText("Lab 3") < 0 and canvas.space.name == "Spike Sorting"


def test_the_layout_can_be_locked(app, win):
    ap, raw = _spike_sorting(app, win)
    canvas = ap.canvas
    canvas.lock.setChecked(True)
    pump(app, 0.05)
    dock = C(raw, "Raster").dock
    assert not canvas.btn_add.isEnabled() and not dock.bar.btn_close.isVisible()
    assert dock.features() == dock.DockWidgetFeature.NoDockWidgetFeatures
    canvas.lock.setChecked(False)
    assert canvas.btn_add.isEnabled() and dock.bar.btn_close.isVisible()


def test_charts_against_time_zoom_together_unless_told_not_to(app, win):
    ap, raw = _spike_sorting(app, win)
    raster, rate = C(raw, "Raster"), C(raw, "Rate")
    raster.plot.getPlotItem().setXRange(0.1, 0.2, padding=0)
    pump(app, 0.05)
    assert rate.plot.getPlotItem().viewRange()[0] == pytest.approx([0.1, 0.2], abs=0.01)
    rate.link_box.setChecked(False)
    raster.plot.getPlotItem().setXRange(0.3, 0.4, padding=0)
    pump(app, 0.05)
    assert rate.plot.getPlotItem().viewRange()[0] == pytest.approx([0.1, 0.2], abs=0.01)


def test_clicking_a_chart_shows_its_settings(app, win):
    ap, raw = _spike_sorting(app, win)
    canvas = ap.canvas
    chart = C(raw, "Rate")
    chart.plot.setFocus()
    canvas._focus_moved(None, chart.plot)
    pump(app, 0.05)
    assert canvas.active is chart and canvas.options.currentWidget() is chart.options_panel
    assert not ap.windows["chart"].isVisible()                       # zooming doesn't open anything
    chart.dock.bar.btn_set.click()
    pump(app, 0.05)
    assert ap.windows["chart"].isVisible() and "Rate" in ap.windows["chart"].windowTitle()


def test_settings_open_in_windows_that_come_back_with_analysis(app, win):
    ap, raw = _spike_sorting(app, win)
    assert not raw.isVisible() and not ap.stim_channel.isVisible()   # only the block browser, until asked
    assert ap.blocks_panel.tree.isVisible()
    ap.windows["spikes"].open_()
    pump(app, 0.05)
    assert raw.btn_find.isVisible()
    win.tabs.pick(0, "acq")
    pump(app, 0.05)
    assert not raw.isVisible()                                       # only with Analysis
    win.tabs.pick(0, "analysis")
    pump(app, 0.05)
    assert raw.isVisible()
    ap.windows["spikes"].close()
    win.tabs.pick(0, "acq")
    win.tabs.pick(0, "analysis")
    pump(app, 0.05)
    assert not raw.isVisible()


def _sine_session(app, win, n=2):
    """As stim_session, with a 20 Hz sine on the Stim channel and Ch2 following it."""
    from neusci_daq.stim import Stimulus
    stim_session(app, win, n=0)
    win.set_stimulus(Stimulus(shape="sine", amplitude_v=1, freq_hz=20, duration_ms=800))
    win.btn_run.setChecked(True)
    pump(app, 0.2)
    win.set_repeat(True)
    for _ in range(n):
        win.btn_rec.setChecked(True)
        pump(app, 1.0)
        win.btn_rec.setChecked(False)
    win.set_repeat(False)
    win.btn_run.setChecked(False)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.raw.channel.setCurrentText("Ch2")
    ap.show_layout("Response")
    pump(app, 0.2)
    return ap, ap


def test_comparison_overlays_cycles_by_phase(app, win):
    ap, cmp = _sine_session(app, win)
    assert ap.repeats.channel() == "Stim" and ap.chart("Response by cycle").cycle_resp.currentText() != "Stim"
    assert "cycles" in ap.chart("Stimulus cycle").plot.getPlotItem().titleLabel.text
    stim = ap.chart("Stimulus cycle").plot.getPlotItem().listDataItems()[0].getData()
    assert stim[1].argmax() == pytest.approx(90, abs=15)             # a sine, from its rise
    assert ap.chart("Response by cycle").plot.getPlotItem().listDataItems()
    assert ap.chart("Response against stimulus").plot.getPlotItem().listDataItems()
    n = len(ap.chart("Response by cycle").plot.getPlotItem().items)
    ap.chart("Response by cycle").cycle_each.setChecked(False)
    assert len(ap.chart("Response by cycle").plot.getPlotItem().items) < n
    ap.repeats.cycle_skip.setValue(5)
    assert "cycles" in ap.chart("Stimulus cycle").plot.getPlotItem().titleLabel.text
    assert "find action potentials" in ap.chart("Spikes per phase").plot.getPlotItem().titleLabel.text
    ap.raw.find()
    pump(app, 0.1)
    assert "find action potentials" not in ap.chart("Spikes per phase").plot.getPlotItem().titleLabel.text


def test_cycle_charts_have_check_boxes_and_say_when_there_are_none(app, win):
    ap, raw = _spike_sorting(app, win)
    ap.show_layout("Response")
    pump(app, 0.1)
    ap.stim_channel.setCurrentIndex(ap.stim_channel.findData("Ch1"))
    assert "no repeats" in ap.chart("Stimulus cycle").plot.getPlotItem().titleLabel.text
    assert "None" in ap.repeats.message.text()


def test_waveforms_plot_v_against_dv_dt(app, win):
    ap, raw = _spike_sorting(app, win)
    ev = raw.find()
    ap.show_layout("Waveforms")
    pump(app, 0.1)
    view = ap.chart("V against dV/dt")
    assert str(len(ev.t)) in view.plot.getPlotItem().titleLabel.text
    v, d = view.plot.getPlotItem().listDataItems()[-1].getData()
    assert v == pytest.approx(ev.waves[ev.unit == ev.n_units - 1].mean(0))
    assert np.abs(d).max() > 0
    view.each.setChecked(False)
    assert len(view.plot.getPlotItem().listDataItems()) == ev.n_units
    if ev.n_units > 1:
        raw.show_units({0})                                          # the units ticked
        assert len(view.plot.getPlotItem().listDataItems()) == 1
        raw.show_units(set(range(ev.n_units)))
    raw.clear()
    assert "find action potentials" in view.plot.getPlotItem().titleLabel.text


def test_scale_bars_replace_the_axes_and_follow_the_zoom(app, win):
    ap, raw = _spike_sorting(app, win)
    win.resize(1600, 1000)
    pump(app, 0.1)
    p = C(raw, "Trials").plot.getPlotItem()
    assert p.getAxis("left").isVisible() and getattr(p, "_scale_bar", None) is None
    ap.canvas.bars.setChecked(True)
    pump(app, 0.05)
    bar = p._scale_bar
    assert not p.getAxis("left").isVisible() and not C(raw, "Raster").plot.getPlotItem().getAxis("left").isVisible()
    assert bar.x_text.endswith("s") and bar.y_text.endswith("V")
    assert C(raw, "Stimulus").plot.getPlotItem()._scale_bar.x_text == ""       # it shares the time below
    data = p.vb.sceneBoundingRect()
    line = bar.mapRectToScene(QtCore.QRectF(-1, -bar.py, bar.px + 1, bar.py + 1))
    assert line.left() < data.left() and line.bottom() > data.bottom()   # outside the plotting area
    first = bar.x_text
    p.setXRange(0, 0.01, padding=0)
    pump(app, 0.05)
    assert bar.x_text != first
    class Drag:
        def button(self): return QtCore.Qt.MouseButton.LeftButton
        def accept(self): pass
        def pos(self): return QtCore.QPointF(-30, -20)
        def lastPos(self): return QtCore.QPointF(0, 0)
    at = bar.pos()
    bar.mouseDragEvent(Drag())
    assert bar.pos().x() == pytest.approx(at.x() - 30)
    gap = bar.gap
    C(raw, "Trials").redraw()                                              # redrawing keeps it there
    assert p._scale_bar.gap == gap
    ap.canvas.bars.setChecked(False)
    assert p.getAxis("left").isVisible() and p._scale_bar is None
    assert win.settings.value("analysis/scale_bars", True, type=bool) is False
    ap.show_layout("Response")
    pump(app, 0.1)
    ap.canvas.bars.setChecked(True)
    plots = ap.chart("Traces")._plots
    assert plots[-1].getPlotItem()._scale_bar.x_text and not plots[0].getPlotItem()._scale_bar.x_text
    ap.canvas.bars.setChecked(False)


def test_a_trace_drawn_small_regroups_when_its_plot_grows(app, win):
    from neusci_daq.plotzoom import OverlayCurve, plot_widget
    pw = plot_widget()
    pw.resize(80, 200)
    pw.show()
    item = OverlayCurve()
    item.mode = "peak"
    pw.getPlotItem().addItem(item)
    pump(app, 0.05)
    item.set_traces(np.sin(np.arange(20000) / 20000 * 40 * np.pi)[None, :], 20000)
    pw.getPlotItem().setXRange(0, 1, padding=0)
    pump(app, 0.1)
    coarse = len(item.xData)
    pw.resize(1200, 200)
    pump(app, 0.2)
    assert len(item.xData) > coarse * 4


def test_response_traces_follow_spike_sortings_rules(app, win):
    ap, raw = _spike_sorting(app, win)
    ap.show_layout("Response")
    pump(app, 0.1)
    from neusci_daq.plotzoom import OverlayCurve, TraceCurve
    name = ap.shown[0][0]
    ap.select_pages([name])
    items = ap.chart("Traces")._plots[0].getPlotItem().listDataItems()
    assert len(items) == 1 and isinstance(items[0], TraceCurve) and not isinstance(items[0], OverlayCurve)
    assert items[0].opts["pen"].color() == QtGui.QColor(ap.group_color(0))
    ap.select_blocks([0, 1])
    ap.trial_map.setCurrentText("Plasma")
    items = ap.chart("Traces")._plots[0].getPlotItem().listDataItems()
    assert sum(i.getOriginalDataset()[1].shape[0] for i in items if isinstance(i, OverlayCurve)) == 2
    assert len({i.opts["pen"].color().name() for i in items}) == 2
    ap.spread_box.setCurrentIndex(1)
    ap.select_pages([name])
    assert any(isinstance(i, __import__("pyqtgraph").FillBetweenItem) for i in ap.chart("Traces")._plots[0].getPlotItem().items)
    ap.spread_box.setCurrentIndex(0)


def test_response_uses_the_units_ticked_on_spike_sorting(app, win):
    ap, cmp = _sine_session(app, win)
    raw = ap.raw
    ev = raw.find()
    ev.unit = np.arange(len(ev.t)) % 2
    raw._show_results()
    raw.page.events_changed()
    ap.show_layout("Response")
    pump(app, 0.1)
    assert sum(len(v) for v in ap.chart("Spike-triggered average")._times().values()) == len(ev.t)
    raw.color_of(0)
    dots = [i for i in ap.chart("Spikes by cycle").plot.getPlotItem().items if isinstance(i, __import__("pyqtgraph").ScatterPlotItem)]
    assert {d.opts["brush"].color().name() for d in dots} == {raw.color_of(0).lower(), raw.color_of(1).lower()}
    raw.show_units({1})
    assert sum(len(v) for v in ap.chart("Spike-triggered average")._times().values()) == int((ev.unit == 1).sum())
    dots = [i for i in ap.chart("Spikes by cycle").plot.getPlotItem().items if isinstance(i, __import__("pyqtgraph").ScatterPlotItem)]
    assert {d.opts["brush"].color().name() for d in dots} == {raw.color_of(1).lower()}


def test_gain_corrects_every_analysis_and_not_the_recording(app, win):
    ap, raw = _spike_sorting(app, win)
    before = ap.trace(0, "Ch2").copy()
    stored = ap.blocks[0]["data"][ap.blocks[0]["channel_names"].index("Ch2")].copy()
    amplitude = np.abs(raw.find().amplitude).mean()
    ap.gain_channel.setCurrentText("Ch2")
    ap.gain.setValue(2)
    pump(app, 0.3)
    assert ap.trace(0, "Ch2") == pytest.approx(before / 2)
    assert np.abs(raw.find().amplitude).mean() == pytest.approx(amplitude / 2, rel=0.02)
    assert ap.blocks[0]["data"][ap.blocks[0]["channel_names"].index("Ch2")] == pytest.approx(stored)
    saved = pages.load(ap._folder())
    assert saved["gains"] == {"Ch2": 2}
    ap.gain_channel.setCurrentText("Stim")
    assert ap.gain.value() == 1                                      # each channel has its own
    ap.gain_channel.setCurrentText("Ch2")
    assert ap.gain.value() == 2
    ap.gain.setValue(1)
    assert ap.trace(0, "Ch2") == pytest.approx(before) and pages.load(ap._folder())["gains"] == {}


def test_a_stimulus_fit_is_read_everywhere_and_shown_against_the_recording(app, win):
    ap, cmp = _sine_session(app, win)
    y = ap.trace(0, "Stim").copy()
    ap.fit_box.setCurrentText("Sine")
    pump(app, 0.2)
    assert np.abs(ap.trace(0, "Stim") - y).max() > 0 and ap.recorded(0, "Stim") == pytest.approx(y)
    assert ap.chart("Stimulus fit").isVisible() and "RMS" in ap.chart("Stimulus fit").resid_plot.getPlotItem().titleLabel.text
    assert pages.load(ap._folder())["stim_fit"] == "Sine"
    ap.fit_box.setCurrentText("Off")
    pump(app, 0.1)
    assert not ap.chart("Stimulus fit").isVisible() and ap.trace(0, "Stim") == pytest.approx(y)


def test_repeats_can_be_typed_or_marked_and_a_one_off_says_so(app, win):
    ap, cmp = _sine_session(app, win)
    title = ap.chart("Stimulus cycle").plot.getPlotItem().titleLabel.text
    assert "from what was sent" in title or "by shape" in title      # depends where the block cut the stimulus
    ap.repeats.cycle_len.setValue(50)                                       # 20 Hz
    assert "from your repeat" in ap.chart("Stimulus cycle").plot.getPlotItem().titleLabel.text
    ap.repeats.cycle_len.setValue(0)
    ap.repeats.btn_mark.setChecked(True)
    vb = ap.chart("Traces")._plots[0].getPlotItem().vb
    assert vb.on_drag_select is not None
    vb.on_drag_select(0.1, 0.15, True)
    assert not ap.repeats.btn_mark.isChecked() and ap.repeats.cycle_len.value() == pytest.approx(50)
    assert ap.repeats._cycle_start == pytest.approx(0.1) and vb.on_drag_select is None or ap.chart("Traces")._plots
    ap.repeats.cycle_len.setValue(0)
    assert ap.repeats._cycle_start is None
    marks = [i for pw in ap.chart("Traces")._plots for i in pw.getPlotItem().items
             if isinstance(i, __import__("pyqtgraph").ScatterPlotItem)]
    assert not marks                                                 # the cuts aren't marked on the traces


def test_a_pulse_recording_has_no_cycles_to_overlay_when_it_is_one_pulse():
    t = np.arange(FS) / FS
    assert A.find_repeats(np.where(t < 0.01, 1.0, 0.0), FS).method == ""


def test_every_analysis_chart_zooms_by_the_same_rules(app, win):
    from neusci_daq.plotzoom import WheelViewBox
    plots = win.analysis_page.findChildren(__import__("pyqtgraph").PlotWidget)
    assert plots and all(isinstance(pw.getPlotItem().vb, WheelViewBox) for pw in plots)
    assert all(isinstance(pw.getPlotItem().vb, WheelViewBox) for pw in win.findChildren(__import__("pyqtgraph").PlotWidget)
               if pw is not getattr(win.sim_page, "diagram", None))


def test_every_help_topic_opens(app, win):
    for key in TOPICS:
        dlg = HelpDialog(key)
        assert dlg.findChild(QtWidgets.QLabel) is not None and TOPICS[key]["glossary"]
        dlg.deleteLater()
    assert win.analysis_page.raw.btn_help.toolTip()


def test_find_rescales_y_and_settings_search_again(app, win):
    ap, raw = _spike_sorting(app, win)
    raw.btn_find.click()
    first = raw.events
    assert first is not None
    pump(app, 0.2)
    vb = C(raw, "Trials").plot.getPlotItem().vb
    fit = vb.viewRange()[1]
    vb.setYRange(fit[0] / 100, fit[1] / 100, padding=0)              # zoomed in, as by hand
    C(raw, "AP sizes").plot.getPlotItem().setYRange(0, 0.001, padding=0)
    C(raw, "Raster").plot.getPlotItem().setYRange(1, 2, padding=0)
    raw.btn_find.click()
    pump(app, 0.2)
    lo, hi = vb.viewRange()[1]
    assert lo <= C(raw, "Trials").marks.data["y"].min() and hi - lo > 50 * (fit[1] - fit[0]) / 100
    assert C(raw, "AP sizes").plot.getPlotItem().vb.viewRange()[1][1] > 0.001
    assert C(raw, "Raster").plot.getPlotItem().vb.viewRange()[1] == pytest.approx([0.5, len(C(raw, "Raster")._rows) + 0.5])
    zoomed = (fit[0] / 100, fit[1] / 100)
    vb.setYRange(*zoomed, padding=0)
    for change in (lambda: raw.dead.setValue(2.0), lambda: raw.post.setValue(3.0),
                   lambda: raw.units.setCurrentIndex(2), lambda: raw.bip_frac.setValue(0.5)):
        was = raw.events
        change()
        pump(app, 0.5)
        assert raw.events is not None and raw.events is not was      # searched without pressing Find
    assert vb.viewRange()[1] == pytest.approx(zoomed, rel=1e-3)      # and left the zoom alone
    C(raw, "Trials").thr_line.setValue(C(raw, "Trials").thr_line.value() * 1.1)
    C(raw, "Trials")._thr_dragged()
    pump(app, 0.5)
    assert raw.events is not None


def test_a_unit_colour_reaches_every_graph(app, win):
    ap, raw = _spike_sorting(app, win)
    ev = raw.find()
    ev.unit = np.arange(len(ev.t)) % 2
    raw._show_results()
    raw.page.events_changed()
    assert isinstance(raw.table.cellWidget(0, 5), ColorButton) and raw.table.cellWidget(0, 5).toolTip()
    raw.set_unit_color(0, "#123456")
    want = QtGui.QColor("#123456")
    assert raw.table.cellWidget(0, 5).color == "#123456"
    assert C(raw, "Rate")._rate_items[0].opts["pen"].color() == want
    for kind in ("AP sizes", "Intervals", "Waveforms"):
        assert C(raw, kind).plot.getPlotItem().listDataItems()[1 if kind == "Waveforms" else 0].opts["pen"].color() == want
    first = np.flatnonzero(ev.unit == 0)[0]
    assert C(raw, "Raster").raster_dots.data["brush"][first].color() == want
    assert C(raw, "Trials").marks.data["brush"][first].color() == want


def test_groups_and_trials_can_be_hidden_and_colour_maps_skip_them(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.raw.channel.setCurrentText("Ch2")
    ap.page_map.setCurrentText(next(n for n in cm.MAPS))
    first, second = (name for name, _ in ap.shown)
    before = ap.group_color(1)
    ap.set_hidden(("page", first), True)
    pump(app, 0.1)
    assert not set(ap.raw.chosen_or_none()) & set(ap.shown[0][1])    # gone from everything
    assert ap.group_color(1) != before                               # the map spreads across what's left
    assert pages.load(ap._folder())["hidden_groups"] == [first]
    assert ap.blocks_panel.tree.topLevelItem(0).checkState(0) == QtCore.Qt.CheckState.Unchecked
    one = ap.shown[1][1][0]
    ap.set_hidden(("block", one), True)
    assert one not in ap.raw.chosen_or_none()
    ap.show_all()
    pump(app, 0.1)
    assert len(ap.raw.chosen_or_none()) == len(ap.blocks) and ap.group_color(1) == before


def test_a_groups_tick_sets_its_trials_unless_you_set_one(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.raw.channel.setCurrentText("Ch2")
    name, members = ap.shown[0]
    keep, out = members[0], members[1]
    ap.set_hidden(("block", keep), False)                            # you ticked this one yourself
    ap.set_hidden(("page", name), True)
    assert keep in ap.raw.chosen_or_none() and out not in ap.raw.chosen_or_none()
    ap.set_hidden(("block", out), True)                              # and unticked this one
    ap.set_hidden(("page", name), False)
    assert out not in ap.raw.chosen_or_none() and keep in ap.raw.chosen_or_none()
    ap.hide_all()
    assert ap.raw.chosen_or_none() == []
    ap.show_all()
    assert len(ap.raw.chosen_or_none()) == len(ap.blocks)
    assert ap.blocks_panel.btn_select.isVisible() and ap.blocks_panel.btn_deselect.isVisible()


def test_groups_start_collapsed_and_stay_as_you_left_them(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    tree = ap.blocks_panel.tree
    assert not tree.topLevelItem(0).isExpanded()
    tree.topLevelItem(0).setExpanded(True)
    ap.set_hidden(("page", ap.shown[1][0]), True)                    # rebuilds the browser
    pump(app, 0.1)
    assert tree.topLevelItem(0).isExpanded() and not tree.topLevelItem(1).isExpanded()


def test_groups_can_be_dragged_and_sorted(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    names = [n for n, _ in ap.shown]
    ap.move_group(names[1], 0)
    assert [n for n, _ in ap.shown] == names[::-1]
    assert pages.load(ap._folder())["order"] == names[::-1]
    assert ap.blocks_panel.tree.topLevelItem(0).data(0, QtCore.Qt.ItemDataRole.UserRole) == ("page", names[1])
    ap.move_group(names[1], 2)                                       # to the end: back again
    assert [n for n, _ in ap.shown] == names
    ap.sort_groups("amplitude")
    sizes = [abs(ap._signature_of(ap._paths, "Stim")(b[0], ap.recorded(b[0], "Stim"), ap.blocks[b[0]]["sample_rate"])[2])
             for _, b in ap.shown]
    assert sizes == sorted(sizes)
    assert ap.blocks_panel.sort_box.currentData() == "amplitude"


def test_saved_order_goes_first_and_new_groups_after():
    shown = [("a", [0]), ("b", [1]), ("c", [2])]
    assert [n for n, _ in pages.arrange(shown, {"order": ["c", "a"]}, None)] == ["c", "a", "b"]
    size = {0: (3, 1), 1: (1, 2), 2: (2, 3)}
    assert [n for n, _ in pages.arrange(shown, {"sort": "frequency"}, lambda b: size[b[0]])] == ["b", "c", "a"]


def test_analysis_saves_with_the_session_and_comes_back(app, win, tmp_path):
    from neusci_daq import analysisfile
    from neusci_daq.gui import MainWindow
    ap, raw = _spike_sorting(app, win)
    assert not ap.has_unsaved_analysis()
    ev = raw.find()
    raw.threshold.setText("6 SD")
    raw.find()
    ev = raw.events
    raw.set_unit_color(0, "#123456")
    raw.unit_opacity[0] = 0.5
    ap.main.tabs.pick(0, "datapad")
    pump(app, 0.1)
    ap.datapad.add_column("Ch2", "spikes")
    assert ap.datapad.add_rows("blocks") > 0
    n = len(ap.datapad.rows)
    assert ap.has_unsaved_analysis()
    asked = []
    win._ask_unsaved_analysis = lambda: asked.append(1) or "cancel"
    assert not win._resolve_unsaved() and asked                      # Cancel stays
    assert ap.save_analysis() and not ap.has_unsaved_analysis()
    folder = ap._folder()
    assert analysisfile.exists(folder)
    before = sorted(p.name for p in session.list_blocks(folder))
    w2 = MainWindow(make_cfg(tmp_path), settings=win.settings)
    w2._ask_unsaved = lambda: "discard"
    w2.open_session(folder)
    w2.show()
    w2.tabs.pick(0, "analysis")
    pump(app, 0.3)
    a2 = w2.analysis_page
    assert a2.raw.events is not None and len(a2.raw.events.t) == len(ev.t)
    assert a2.raw.events.t == pytest.approx(ev.t) and a2.raw.threshold.text() == "6 SD"
    assert a2.raw.color_of(0) == "#123456" and a2.raw.opacity_of(0) == 0.5
    assert len(a2.datapad.rows) == n and a2.datapad.columns[-1] == ("Ch2", "spikes")
    assert not a2.has_unsaved_analysis()
    assert sorted(p.name for p in session.list_blocks(folder)) == before
    w2.close()


def test_zoom_and_move_buttons_show_over_a_chart(app, win):
    ap, raw = _spike_sorting(app, win)
    win.resize(1400, 900)
    pump(app, 0.2)
    chart, other = C(raw, "Raster"), C(raw, "Rate")
    pw = chart.plot
    assert pw.buttons.x.isHidden()
    app.sendEvent(pw, QtCore.QEvent(QtCore.QEvent.Type.Enter))
    assert pw.buttons.x.isVisible() and pw.buttons.y.isVisible()
    area = pw.mapFromScene(pw.getPlotItem().vb.sceneBoundingRect()).boundingRect()
    assert area.contains(pw.buttons.y.geometry().topLeft()) and area.contains(pw.buttons.x.geometry().bottomRight())
    lo, hi = pw.getPlotItem().viewRange()[0]
    pw.buttons.zoom(0, 0.5)
    pump(app, 0.05)
    lo2, hi2 = pw.getPlotItem().viewRange()[0]
    assert hi2 - lo2 == pytest.approx((hi - lo) / 2, rel=0.02)
    assert other.plot.getPlotItem().viewRange()[0] == pytest.approx([lo2, hi2], abs=0.01)   # linked charts follow
    pw.buttons.move(0, 1)
    pump(app, 0.05)
    assert pw.getPlotItem().viewRange()[0][0] > lo2
    app.sendEvent(pw, QtCore.QEvent(QtCore.QEvent.Type.Leave))
    assert pw.buttons.x.isHidden()


def test_channels_and_groups_settings_open_in_their_own_windows(app, win):
    ap, raw = _spike_sorting(app, win)
    ap.windows["channels"].open_()
    pump(app, 0.05)
    assert ap.stim_channel.isVisible() and ap.fit_box.isVisible() and ap.gain.isVisible()
    assert not ap.spread_box.isVisible()
    ap.blocks_panel.btn_groups.click()
    pump(app, 0.05)
    assert ap.spread_box.isVisible() and ap.page_map.isVisible() and ap.blocks_panel.btn_reset.isVisible()


def test_a_units_opacity_reaches_the_charts(app, win):
    ap, raw = _spike_sorting(app, win)
    raw.find()
    button = raw.table.cellWidget(0, 5)
    row = button._opacity_row()                                      # what the menu holds
    button.opacity_slider.setValue(30)
    pump(app, 0.2)
    assert raw.opacity_of(0) == pytest.approx(0.3)
    assert raw.table.cellWidget(0, 5) is button                      # the menu stays open as it moves
    brush = C(raw, "Trials").marks.data["brush"][0]
    assert brush.color().alphaF() == pytest.approx(0.3, abs=0.01)
    assert C(raw, "Raster").raster_dots.data["brush"][0].color().alphaF() == pytest.approx(0.3, abs=0.01)


def test_every_setting_says_what_it_does(app, win):
    kinds = (QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox, QtWidgets.QCheckBox, QtWidgets.QRadioButton,
             QtWidgets.QLineEdit, QtWidgets.QSlider)
    win.show()
    pump(app, 0.1)
    bare = []
    for page in (win, win.stim_page, win.trials_page, win.analysis_page, win.sim_page, win.camera_panel):
        for w in page.findChildren(QtWidgets.QWidget):
            owners = []
            q = w.parentWidget()
            while q is not None:
                owners.append(q)
                q = q.parentWidget()
            if (isinstance(w, kinds) and not w.toolTip() and
                    not any(isinstance(q, (QtWidgets.QMenu, QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox))
                            or type(q).__module__.startswith("pyqtgraph") for q in owners)):
                bare.append(f"{type(page).__name__}: {type(w).__name__} {getattr(w, 'text', str)()!r}")
    assert not bare, bare
