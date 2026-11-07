"""Analysis: block lists, event detection, spike sorting, and the Analysis tab."""
import numpy as np
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets
import pytest

from neusci_daq import analysis as A
from neusci_daq import pages, colormaps as cm, pages, session, simulation as sim
from neusci_daq.channels import ColorButton
from neusci_daq.gui import REVIEW
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
    assert len(page.group_avgs) == 1 and not page.trial_items        # nothing picked: the page's average
    win.analysis_page.select_blocks([1, 2])
    assert trials_shown(page) == 2 and not page.group_avgs           # blocks picked: their trials
    win.analysis_page.select_blocks([])
    ev = page.find()
    assert ev is not None and len(ev.t) > 0 and page.table.rowCount() == ev.n_units
    assert len(page.raster_dots.data) == len(ev.t)
    assert "event" in page.message.text()
    out = page.export(tmp_path / "events.csv")
    assert out.read_text().splitlines()[0] == "block,time_s,unit,amplitude,channel"
    assert len(out.read_text().splitlines()) == len(ev.t) + 1


def test_dragging_the_threshold_sets_a_fixed_level(app, win):
    record(app, win, 1)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    page = win.analysis_page.raw
    base = page._baseline()
    page.thr_line.setValue(base - 0.3)
    page._threshold_dragged()
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
    assert [ap.tabs.tabText(i) for i in range(ap.tabs.count())] == ["Spike Sorting", "Response", "Waveforms", "DataPad"]
    ap.raw.overlay.setFocus()
    key(ap.raw.overlay, QtCore.Qt.Key.Key_Right)
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
    assert raw.rate_plot.getPlotItem().listDataItems()                # spikes/s during a block
    assert raw.hist.getPlotItem().listDataItems()                     # AP sizes
    assert raw.wave_plot.getPlotItem().listDataItems()


def test_comparison_traces_derivatives_and_triggered_averages(app, win):
    stim_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.raw.channel.setCurrentText("Ch2")
    ap.raw.find()
    ap.tabs.setCurrentIndex(1)
    pump(app, 0.2)
    cmp = ap.compare
    assert len(cmp._plots) == 3                                      # Ch1, Ch2 and Stim V
    cmp.series.item(1, 1).setCheckState(QtCore.Qt.CheckState.Checked)   # + Ch2 dV/dt
    assert len(cmp._plots) == 4
    cmp.layout_mode.setCurrentIndex(1)                               # overlaid
    assert len(cmp._plots) == 1
    assert cmp.trigger.currentData() == "Stim"                       # picked as the stimulus channel
    pp = cmp.psth.getPlotItem().listDataItems()[0]
    edges, rate = pp.getData()                                       # a stepped plot: bin edges
    n_stim = int(cmp.psth.getPlotItem().titleLabel.text.split("(")[1].split()[0])
    assert n_stim >= 6
    peak_bin = edges[np.argmax(rate)]                                # its left edge
    assert 0 <= peak_bin < 30                                        # fires just after each stimulus
    cmp.sta_channel.setCurrentIndex(cmp.sta_channel.findData("Stim"))
    t, mean = cmp.sta.getPlotItem().listDataItems()[-1].getData()
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
    ap.tabs.setCurrentIndex(1)
    pump(app, 0.2)
    cmp = ap.compare
    cmp.trigger.setCurrentIndex(cmp.trigger.findData("Stim"))
    curves = cmp.psth.getPlotItem().listDataItems()
    assert len(curves) == 2 and "by stimulus group" in cmp.psth.getPlotItem().titleLabel.text



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
    assert ap.compare.trigger.currentData() == "Stim"                 # Comparison follows it
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
    assert len(ap.raw.group_avgs) == 2 and len(ap.raw.group_marks.data) == 4   # an average each; row marks
    ap.select_blocks([2, 3])                                         # one group: just its blocks
    assert ap.raw.chosen_or_none() == [2, 3] and ap.raw.group_avgs == []


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
    assert ap.blocks_panel.tree.topLevelItem(0).text(0) == "Weak  (2)"


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
    assert len(raw.group_avgs) == 2 and len(raw.bands) == 2 * 3      # each group: line + band
    chosen = raw.chosen_or_none()
    rows = [ap.trace(i, "Ch1") for i in chosen if i in ap.groups[0][1]][:2]
    n = min(len(y) for y in rows)                                    # blocks can differ by a chunk
    Y = np.array([y[:n] for y in rows])
    mean, sd = Y.mean(0), Y.std(0, ddof=1)
    up = raw.bands[0].getOriginalDataset()[1][:n]
    assert np.allclose(up, mean + sd)                                # the band is mean ± SD
    ap.spread_box.setCurrentIndex(2)                                 # ± SEM
    assert np.allclose(raw.bands[0].getOriginalDataset()[1][:n], mean + sd / np.sqrt(2))
    out = raw.export_averages(tmp_path / "avg.csv")
    rows = list(csv.reader(out.open()))
    assert rows[0][0] == "time_s" and rows[0][1].startswith(ap.groups[0][0]) and "mean" in rows[0][1]
    assert float(rows[11][1]) == pytest.approx(mean[10], rel=1e-4)
    assert float(rows[11][2]) == pytest.approx(sd[10], rel=1e-4) and rows[11][4] == "2"
    raw.channel.setCurrentText("Ch2")                                # the spike rate gets bands too
    raw.find()
    ap.tabs.setCurrentIndex(1)
    pump(app, 0.2)
    ap.compare.trigger.setCurrentIndex(ap.compare.trigger.findData("Stim"))
    fills = [x for x in ap.compare.psth.getPlotItem().items if isinstance(x, __import__("pyqtgraph").FillBetweenItem)]
    assert len(fills) == 2 and "SEM across stimuli" in ap.compare.psth.getPlotItem().titleLabel.text
    ap.spread_box.setCurrentIndex(0)
    assert raw.bands == []


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
    pad = ap.datapad
    ap.tabs.setCurrentWidget(pad)
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
    pad = ap.datapad
    pad.columns = []
    pad.add_column("Ch2", "spikes")
    pad.whole.setChecked(True)
    pad.add_rows("current")
    assert np.isnan(pad.rows[0]["values"][("Ch2", "spikes")])       # nothing found yet
    assert "find them" in pad.message.text()
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
    pad = ap.datapad
    ap.tabs.setCurrentWidget(pad)
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
    assert ap.tabs.tabText(0) == "Spike Sorting"
    channels = [raw.channel.itemText(i) for i in range(raw.channel.count())]
    assert ap.stim_channel.currentData() == "Stim" and "Stim" not in channels and "Ch2" in channels
    assert raw.stim_plot.isVisible()
    x, y = raw.stim_items[0].getOriginalDataset()
    y = y[0]
    assert len(y) and np.nanmax(y) == pytest.approx(1, abs=0.05)     # the 1 V pulses of block 1
    assert len(raw.stim_onsets.data) >= 1
    ap.stim_channel.setCurrentIndex(0)                               # None: the notes
    assert "Stim" in [raw.channel.itemText(i) for i in range(raw.channel.count())]
    assert "notes" in raw.stim_plot.getPlotItem().getAxis("left").labelText


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
    assert raw.table.rowCount() == 2
    assert raw.event_hl.getData()[0] is None or not len(raw.event_hl.getData()[0])   # nothing yet
    assert len(raw.marks.data) == len(ev.t)                          # every chosen block's events are marked
    assert raw.table.item(0, 0).checkState() == QtCore.Qt.CheckState.Checked
    raw.table.item(0, 0).setCheckState(QtCore.Qt.CheckState.Unchecked)   # leave out unit 1
    mine = ev.unit == 1
    xs = raw.event_hl.getData()[0]
    assert np.isfinite(xs).sum() > 0 and mine.sum() == np.isnan(xs).sum()   # one segment per event, in any block
    assert len(raw.marks.data) == mine.sum() and len(raw.raster_dots.data) == mine.sum()   # the others are gone
    assert all(not it.isVisible() for it in raw._unit_items[0])      # from sizes, intervals and waveforms
    assert all(it.isVisible() for it in raw._unit_items[1])
    assert raw._rate_items[1].isVisible() and not raw._rate_items[0].isVisible()
    assert not raw._rate_items[None].isVisible()
    raw.norm_waves.setChecked(True)                                  # redrawn: the ticks stay
    assert raw.shown_units() == {1} and raw.table.item(0, 0).checkState() == QtCore.Qt.CheckState.Unchecked
    raw.show_units(set())                                            # none: nothing to see
    assert len(raw.marks.data) == 0 and not any(i.isVisible() for i in raw._rate_items.values())
    raw.show_units({0, 1})
    assert len(raw.marks.data) == len(ev.t) and raw._rate_items[None].isVisible()


def test_normalize_options(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    raw = win.analysis_page.raw
    raw.channel.setCurrentText("Ch2")
    ev = raw.find()
    assert ev is not None and len(ev.t)
    raw.norm_sizes.setChecked(True)
    raw.norm_isi.setChecked(True)
    raw.norm_waves.setChecked(True)
    raw.norm_rate.setChecked(True)
    for pw in (raw.hist, raw.isi):
        sums = [item.getData()[1].sum() for item in pw.getPlotItem().listDataItems()]
        assert max(sums) == pytest.approx(1)                         # a fraction of the unit's events (a
                                                                     # small unit may have no intervals)
    peak = int(np.argmin(np.abs(ev.wave_t_ms)))
    mean = raw.wave_plot.getPlotItem().listDataItems()[1].getData()[1]
    assert abs(mean[peak]) == pytest.approx(1, abs=1e-6)             # the unit's average peak is 1
    shown = raw.wave_plot.getPlotItem().listDataItems()[0].getData()[1]
    tall = ev.waves[ev.unit == 0][:, peak]
    if np.ptp(tall):                                                 # spikes keep their sizes within a unit
        spikes = shown.reshape(-1, len(ev.wave_t_ms) + 1)[:, peak]
        assert np.ptp(np.abs(spikes)) > 0
    rate = raw.rate_plot.getPlotItem().listDataItems()[-1].getData()[1]
    assert np.nanmax(rate) == pytest.approx(1)


def test_datapad_overlaid_axes_share_the_time_axis(app, win):
    two_stimulus_session(app, win)
    win.resize(1200, 800)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.tabs.setCurrentWidget(ap.datapad)
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
    ap.tabs.setCurrentWidget(ap.datapad)
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
    return sum(item.getOriginalDataset()[1].shape[0] for item in raw.trial_items)


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
    assert ap.blocks_panel.isVisible() and ap.blocks_panel.title.text().startswith("<b>Blocks</b>")
    ap.tabs.setCurrentIndex(2)
    pump(app, 0.05)
    assert ap.blocks_panel.isVisible()
    ap.tabs.setCurrentIndex(0)
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
    vb = raw.raster.getPlotItem().vb
    assert vb.viewRange()[1] == pytest.approx([0.5, 2.5])
    assert set(raw.raster_dots.data["y"]) <= {1, 2} and raw.raster_dots.opts["size"] == 2
    assert np.array_equal(raw.raster_dots.data["y"], np.where(ev.block == 0, 1, 2))
    assert not raw.raster.getPlotItem().getAxis("left").style["showValues"]   # no block numbers
    ap.current.setValue(2)                                           # not chosen: no highlighted row
    assert not raw.cur_band.isVisible()
    ap.current.setValue(3)
    assert raw.cur_band.isVisible() and raw.cur_band.getRegion() == pytest.approx((1.5, 2.5))


def test_the_central_plots_share_one_time_axis_that_stops_at_the_data(app, win):
    ap, raw = _spike_sorting(app, win)
    raw.find()
    pump(app, 0.05)
    plots = [raw.overlay, raw.stim_plot, raw.raster, raw.rate_plot]
    x = lambda pw: pw.getPlotItem().vb.viewRange()[0]
    end = raw._end
    for pw in plots:
        assert x(pw) == pytest.approx([0, end], abs=1e-6)
    raw.rate_plot.getPlotItem().vb.zoom_time(0.5, end / 2)           # zoom one: they all follow
    pump(app, 0.05)
    assert x(raw.overlay)[1] - x(raw.overlay)[0] == pytest.approx(end / 2, rel=0.01)
    for pw in plots:
        assert x(pw) == pytest.approx(x(raw.overlay))
    raw.raster.getPlotItem().vb.zoom_time(100, end / 2)              # out: never past the data
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
    mid = raw.overlay.parentWidget()
    assert mid.indexOf(raw.stim_plot) < mid.indexOf(raw.overlay) < mid.indexOf(raw.raster)
    assert not ap.block_bar.isVisible()
    ap.tabs.setCurrentWidget(ap.compare)
    pump(app, 0.1)
    assert not ap.block_bar.isVisible()                              # Response shows the chosen blocks too
    ap.tabs.setCurrentWidget(ap.datapad)
    pump(app, 0.1)
    assert ap.block_bar.isVisible()
    ap.tabs.setCurrentWidget(ap.raw)
    pump(app, 0.1)
    assert not ap.block_bar.isVisible()
    ap._block_clicked(("block", 1))                                  # the Blocks panel still picks it
    assert ap.index() == 1
    ap.select_blocks([2, 3])                                         # and it stays among the chosen ones
    assert ap.index() == 2


def test_rate_bins_sit_with_the_rate_graph(app, win):
    ap, raw = _spike_sorting(app, win)
    corner = raw.rate_corner
    assert corner.box.parentWidget() is raw.rate_plot and raw.rate_bin.isVisible()
    assert corner.box.geometry().right() < raw.rate_plot.width()
    title = raw.rate_plot.getPlotItem().titleLabel
    raw.rate_plot.resize(200, 150)                                   # no room for both: the title gives way
    pump(app, 0.05)
    assert not title.isVisible()


def test_a_page_shows_its_average_and_blocks_their_trials(app, win):
    ap, raw = _spike_sorting(app, win)
    name = ap.shown[0][0]
    ap.select_pages([name])
    assert len(raw.group_avgs) == 1 and not raw.trial_items
    assert raw.group_avgs[0].opts["pen"].color() == QtGui.QColor(ap.group_color(0))
    ap.select_blocks([0, 1])
    assert not raw.group_avgs and trials_shown(raw) == 2
    alpha = raw.trial_items[0].opts["pen"].color().alpha()
    ap.select_blocks([0])
    assert raw.trial_items[0].opts["pen"].color().alpha() == 255 > alpha   # one on its own is solid


def test_page_colours_are_editable_and_saved_with_the_session(app, win):
    ap, raw = _spike_sorting(app, win)
    name = ap.shown[0][0]
    ap.set_page_color(name, "#123456")
    ap.select_pages([name])
    assert raw.group_avgs[0].opts["pen"].color() == QtGui.QColor("#123456")
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
    alphas = sorted(item.opts["pen"].color().alpha() for item in raw.trial_items)
    assert len(alphas) == 3 and alphas[0] < alphas[-1] == 255
    ap.trial_map.setCurrentText("Plasma")
    assert len({item.opts["pen"].color().name() for item in raw.trial_items}) == 3


def test_the_stimulus_chart_shows_every_chosen_block(app, win):
    ap, raw = _spike_sorting(app, win)
    assert sum(s.getOriginalDataset()[1].shape[0] for s in raw.stim_items) == len(ap.blocks)
    ap.select_blocks([0, 1])
    assert sum(s.getOriginalDataset()[1].shape[0] for s in raw.stim_items) == 2


def test_the_stimulus_is_the_colour_of_its_response(app, win):
    ap, raw = _spike_sorting(app, win)
    name = ap.shown[0][0]
    ap.select_pages([name])
    assert raw.stim_items[0].opts["pen"].color() == raw.group_avgs[0].opts["pen"].color()
    ap.trial_map.setCurrentText("Plasma")
    ap.select_blocks([0, 1])
    trials = {item.opts["pen"].color().name() for item in raw.trial_items}
    assert {item.opts["pen"].color().name() for item in raw.stim_items} == trials and len(trials) == 2


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
    assert raw.point_size.parentWidget() is not raw.raster           # beside the units, not on the chart
    raw.mark_size.setValue(4)
    assert raw.marks.opts["size"] == 4
    raw.mark_size.setValue(9)
    raw.point_size.setValue(5)
    assert raw.raster_dots.opts["size"] == 5
    raw.find()
    raw.show_units({0})
    assert raw.raster_dots.opts["size"] == 5
    raw.mark_size.setValue(4)
    assert raw.marks.opts["size"] == 4
    raw.mark_size.setValue(9)
    raw.show_units(raw.shown_units() | set(range(raw.events.n_units)))


def test_the_threshold_says_what_it_is(app, win):
    ap, raw = _spike_sorting(app, win)
    raw.find()
    assert "threshold" in raw.thr_label.toPlainText()


def test_the_threshold_can_be_hidden_without_changing_the_search(app, win):
    ap, raw = _spike_sorting(app, win)
    assert raw.overlay_corner.box.parentWidget() is raw.overlay and raw.show_thr.isVisible()
    found = len(raw.find().t)
    raw.show_thr.setChecked(False)
    assert not raw.thr_line.isVisible()
    assert len(raw.find().t) == found
    assert not raw.thr_line.isVisible()                              # a new search leaves it hidden
    raw.show_thr.setChecked(True)
    assert raw.thr_line.isVisible()


def test_the_raster_shows_as_many_rows_as_blocks_are_chosen(app, win):
    ap, raw = _spike_sorting(app, win)
    vb = raw.raster.getPlotItem().vb
    for pick in ([0], [0, 1], [0, 1, 2], [1], [0, 2]):
        ap.select_blocks(pick)
        pump(app, 0.05)
        assert vb.viewRange()[1] == [0.5, len(pick) + 0.5]


def test_pca_map_shows_every_unit_even_while_one_is_highlighted(app, win):
    ap, raw = _spike_sorting(app, win)
    ev = raw.find()
    ev.unit = np.arange(len(ev.t)) % 2
    raw._show_results()
    p = raw.pca.getPlotItem()
    assert len(p.listDataItems()) == 2 and "PC1" in p.getAxis("bottom").labelText
    raw.show_units({0})
    assert all(item.isVisible() for item in p.listDataItems())
    raw.show_units({0, 1})
    right = raw.pca.parentWidget()
    assert [right.widget(k) for k in range(4)] == [raw.pca, raw.wave_plot, raw.hist, raw.isi]


def test_charts_can_be_turned_off_and_are_remembered(app, win):
    ap, raw = _spike_sorting(app, win)
    charts = raw.charts
    charts.boxes["Intervals"].setChecked(False)
    assert not raw.isi.isVisible() and raw.hist.isVisible()
    assert win.settings.value("charts/spike_sorting/Intervals", True, type=bool) is False
    for name in ("PCA map", "Waveforms", "AP sizes"):
        charts.boxes[name].setChecked(False)
    assert not raw._right_box.isVisible()                            # no column left to show
    charts.boxes["Stimulus"].setChecked(False)
    assert not raw.stim_plot.isVisible()
    charts.btn_reset.click()
    assert raw._right_box.isVisible() and raw.isi.isVisible() and raw.stim_plot.isVisible()
    charts.btn_clear.click()
    assert not any(b.isChecked() for b in charts.boxes.values()) and not raw.overlay.isVisible()
    charts.btn_reset.click()
    ap.tabs.setCurrentWidget(ap.compare)
    pump(app, 0.1)
    ap.compare.charts.boxes["Spike-triggered average"].setChecked(False)
    assert not ap.compare.sta.isVisible() and ap.compare.psth.isVisible()
    ap.compare.charts.btn_reset.click()
    ap.tabs.setCurrentWidget(ap.datapad)
    pump(app, 0.1)
    ap.datapad.charts.boxes["Results"].setChecked(False)
    assert not ap.datapad.out_tabs.isVisible()
    ap.datapad.charts.btn_reset.click()


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
    ap.tabs.setCurrentWidget(ap.compare)
    pump(app, 0.2)
    return ap, ap.compare


def test_comparison_overlays_cycles_by_phase(app, win):
    ap, cmp = _sine_session(app, win)
    assert cmp.cycle_stim.currentText() == "Stim" and cmp.cycle_resp.currentText() != "Stim"
    assert "cycles" in cmp.cycle_stim_plot.getPlotItem().titleLabel.text
    stim = cmp.cycle_stim_plot.getPlotItem().listDataItems()[0].getData()
    assert stim[1].argmax() == pytest.approx(90, abs=15)             # a sine, from its rise
    assert cmp.cycle_plot.getPlotItem().listDataItems()
    assert cmp.loop.getPlotItem().listDataItems()
    n = len(cmp.cycle_plot.getPlotItem().items)
    cmp.cycle_each.setChecked(False)
    assert len(cmp.cycle_plot.getPlotItem().items) < n
    cmp.cycle_skip.setValue(5)
    assert "cycles" in cmp.cycle_stim_plot.getPlotItem().titleLabel.text
    assert "find action potentials" in cmp.phase_hist.getPlotItem().titleLabel.text
    ap.raw.find()
    pump(app, 0.1)
    assert "find action potentials" not in cmp.phase_hist.getPlotItem().titleLabel.text


def test_cycle_charts_have_check_boxes_and_say_when_there_are_none(app, win):
    ap, raw = _spike_sorting(app, win)
    ap.tabs.setCurrentWidget(ap.compare)
    pump(app, 0.1)
    cmp = ap.compare
    cmp.cycle_stim.setCurrentText("Ch1")
    charts = cmp.charts
    for name in ("Stimulus cycle", "Response by cycle", "Spikes by cycle", "Spikes per phase"):
        charts.boxes[name].setChecked(False)
    assert not cmp._cycles.isVisible()
    charts.boxes["Spikes per phase"].setChecked(True)
    assert cmp._cycles.isVisible() and cmp.phase_hist.isVisible()
    assert cmp.phase_hist.getPlotItem().getAxis("bottom").style["showValues"]
    charts.btn_clear.click()
    assert not cmp._right.isVisible() and not cmp._cycles.isVisible()
    charts.btn_reset.click()
    assert cmp._right.isVisible() and cmp.loop.isVisible()


def test_waveforms_plot_v_against_dv_dt(app, win):
    ap, raw = _spike_sorting(app, win)
    ev = raw.find()
    ap.tabs.setCurrentWidget(ap.waveforms)
    pump(app, 0.1)
    view = ap.waveforms
    assert str(len(ev.t)) in view.phase.getPlotItem().titleLabel.text
    v, d = view.phase.getPlotItem().listDataItems()[-1].getData()
    assert v == pytest.approx(ev.waves[ev.unit == ev.n_units - 1].mean(0))
    assert np.abs(d).max() > 0
    view.each.setChecked(False)
    assert len(view.phase.getPlotItem().listDataItems()) == ev.n_units
    if ev.n_units > 1:
        raw.show_units({0})                                          # the units ticked on Spike Sorting
        assert len(view.phase.getPlotItem().listDataItems()) == 1
        raw.show_units(set(range(ev.n_units)))
    view.charts.boxes["V"].setChecked(False)
    view.charts.boxes["dV/dt"].setChecked(False)
    assert not view._column.isVisible() and view.phase.isVisible()
    view.charts.btn_reset.click()
    raw.clear()
    view.redraw()
    assert "find action potentials" in view.phase.getPlotItem().titleLabel.text


def test_scale_bars_replace_the_axes_and_follow_the_zoom(app, win):
    ap, raw = _spike_sorting(app, win)
    win.resize(1600, 1000)
    pump(app, 0.1)
    p = raw.overlay.getPlotItem()
    assert p.getAxis("left").isVisible() and getattr(p, "_scale_bar", None) is None
    raw.charts.bars.setChecked(True)
    pump(app, 0.05)
    bar = p._scale_bar
    assert not p.getAxis("left").isVisible() and not raw.raster.getPlotItem().getAxis("left").isVisible()
    assert bar.x_text.endswith("s") and bar.y_text.endswith("V")
    assert raw.stim_plot.getPlotItem()._scale_bar.x_text == ""       # it shares the time below
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
    raw._draw_overlay()                                              # redrawing keeps it there
    assert p._scale_bar.gap == gap
    raw.charts.bars.setChecked(False)
    assert p.getAxis("left").isVisible() and p._scale_bar is None
    assert win.settings.value("charts/spike_sorting/scale_bars", True, type=bool) is False
    ap.tabs.setCurrentWidget(ap.compare)
    pump(app, 0.1)
    ap.compare.charts.bars.setChecked(True)
    plots = ap.compare._plots
    assert plots[-1].getPlotItem()._scale_bar.x_text and not plots[0].getPlotItem()._scale_bar.x_text
    ap.compare.charts.bars.setChecked(False)


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
    ap.tabs.setCurrentWidget(ap.compare)
    pump(app, 0.1)
    cmp = ap.compare
    from neusci_daq.plotzoom import OverlayCurve, TraceCurve
    name = ap.shown[0][0]
    ap.select_pages([name])
    items = cmp._plots[0].getPlotItem().listDataItems()
    assert len(items) == 1 and isinstance(items[0], TraceCurve) and not isinstance(items[0], OverlayCurve)
    assert items[0].opts["pen"].color() == QtGui.QColor(ap.group_color(0))
    ap.select_blocks([0, 1])
    ap.trial_map.setCurrentText("Plasma")
    items = cmp._plots[0].getPlotItem().listDataItems()
    assert sum(i.getOriginalDataset()[1].shape[0] for i in items if isinstance(i, OverlayCurve)) == 2
    assert len({i.opts["pen"].color().name() for i in items}) == 2
    ap.spread_box.setCurrentIndex(1)
    ap.select_pages([name])
    assert any(isinstance(i, __import__("pyqtgraph").FillBetweenItem) for i in cmp._plots[0].getPlotItem().items)
    ap.spread_box.setCurrentIndex(0)


def test_response_uses_the_units_ticked_on_spike_sorting(app, win):
    ap, cmp = _sine_session(app, win)
    raw = ap.raw
    ev = raw.find()
    ev.unit = np.arange(len(ev.t)) % 2
    raw._show_results()
    ap.tabs.setCurrentWidget(ap.compare)
    pump(app, 0.1)
    assert sum(len(v) for v in cmp._events_of_unit().values()) == len(ev.t)
    raw.color_of(0)
    dots = [i for i in cmp.cycle_raster.getPlotItem().items if isinstance(i, __import__("pyqtgraph").ScatterPlotItem)]
    assert {d.opts["brush"].color().name() for d in dots} == {raw.color_of(0).lower(), raw.color_of(1).lower()}
    raw.show_units({1})
    assert sum(len(v) for v in cmp._events_of_unit().values()) == int((ev.unit == 1).sum())
    dots = [i for i in cmp.cycle_raster.getPlotItem().items if isinstance(i, __import__("pyqtgraph").ScatterPlotItem)]
    assert {d.opts["brush"].color().name() for d in dots} == {raw.color_of(1).lower()}


def test_gain_corrects_every_analysis_and_not_the_recording(app, win):
    ap, raw = _spike_sorting(app, win)
    before = ap.trace(0, "Ch2").copy()
    stored = ap.blocks[0]["data"][ap.blocks[0]["channel_names"].index("Ch2")].copy()
    amplitude = np.abs(raw.find().amplitude).mean()
    ap.gain_channel.setCurrentText("Ch2")
    ap.gain.setValue(2)
    pump(app, 0.3)
    assert ap.trace(0, "Ch2") == pytest.approx(before * 2)
    assert np.abs(raw.find().amplitude).mean() == pytest.approx(amplitude * 2, rel=0.02)
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
    assert cmp.fit_box.isVisible() and "RMS" in cmp.resid_plot.getPlotItem().titleLabel.text
    assert pages.load(ap._folder())["stim_fit"] == "Sine"
    cmp.charts.boxes["Stimulus fit"].setChecked(False)
    assert not cmp.fit_box.isVisible()
    cmp.charts.boxes["Stimulus fit"].setChecked(True)
    ap.fit_box.setCurrentText("Off")
    pump(app, 0.1)
    assert not cmp.fit_box.isVisible() and ap.trace(0, "Stim") == pytest.approx(y)


def test_repeats_can_be_typed_or_marked_and_a_one_off_says_so(app, win):
    ap, cmp = _sine_session(app, win)
    assert "by shape" in cmp.cycle_stim_plot.getPlotItem().titleLabel.text
    cmp.cycle_len.setValue(50)                                       # 20 Hz
    assert "from your repeat" in cmp.cycle_stim_plot.getPlotItem().titleLabel.text
    cmp.cycle_len.setValue(0)
    cmp.btn_mark.setChecked(True)
    vb = cmp._plots[0].getPlotItem().vb
    assert vb.on_drag_select is not None
    vb.on_drag_select(0.1, 0.15, True)
    assert not cmp.btn_mark.isChecked() and cmp.cycle_len.value() == pytest.approx(50)
    assert cmp._cycle_start == pytest.approx(0.1) and vb.on_drag_select is None or cmp._plots
    cmp.cycle_len.setValue(0)
    assert cmp._cycle_start is None
    cmp.cycle_stim.setCurrentText("Ch1" if "Ch1" in ap.names() else ap.names()[0])
    marks = [i for pw in cmp._plots for i in pw.getPlotItem().items
             if isinstance(i, __import__("pyqtgraph").ScatterPlotItem)]
    cmp.cycle_stim.setCurrentText("Stim")
    cmp.redraw()
    marks = [i for pw in cmp._plots for i in pw.getPlotItem().items
             if isinstance(i, __import__("pyqtgraph").ScatterPlotItem)]
    assert marks                                                     # the cuts, on the stimulus's trace


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
    vb = raw.overlay.getPlotItem().vb
    fit = vb.viewRange()[1]
    vb.setYRange(fit[0] / 100, fit[1] / 100, padding=0)              # zoomed in, as by hand
    raw.hist.getPlotItem().setYRange(0, 0.001, padding=0)
    raw.raster.getPlotItem().setYRange(1, 2, padding=0)
    raw.btn_find.click()
    pump(app, 0.2)
    lo, hi = vb.viewRange()[1]
    assert lo <= raw.marks.data["y"].min() and hi - lo > 50 * (fit[1] - fit[0]) / 100
    assert raw.hist.getPlotItem().vb.viewRange()[1][1] > 0.001
    assert raw.raster.getPlotItem().vb.viewRange()[1] == pytest.approx([0.5, len(raw._rows) + 0.5])
    zoomed = (fit[0] / 100, fit[1] / 100)
    vb.setYRange(*zoomed, padding=0)
    for change in (lambda: raw.dead.setValue(2.0), lambda: raw.post.setValue(3.0),
                   lambda: raw.units.setCurrentIndex(2), lambda: raw.bip_frac.setValue(0.5)):
        was = raw.events
        change()
        pump(app, 0.5)
        assert raw.events is not None and raw.events is not was      # searched without pressing Find
    assert vb.viewRange()[1] == pytest.approx(zoomed, rel=1e-3)      # and left the zoom alone
    raw.thr_line.setValue(raw.thr_line.value() * 1.1)
    raw._threshold_dragged()
    pump(app, 0.5)
    assert raw.events is not None


def test_a_unit_colour_reaches_every_graph(app, win):
    ap, raw = _spike_sorting(app, win)
    ev = raw.find()
    ev.unit = np.arange(len(ev.t)) % 2
    raw._show_results()
    assert isinstance(raw.table.cellWidget(0, 5), ColorButton) and raw.table.cellWidget(0, 5).toolTip()
    raw.set_unit_color(0, "#123456")
    want = QtGui.QColor("#123456")
    assert raw.table.cellWidget(0, 5).color == "#123456"
    assert raw._rate_items[0].opts["pen"].color() == want
    assert raw._unit_items[0][0].opts["pen"].color() == want         # sizes
    assert raw._unit_items[0][1].opts["pen"].color() == want         # intervals
    assert raw._unit_items[0][-1].opts["pen"].color() == want        # waveforms
    first = np.flatnonzero(ev.unit == 0)[0]
    assert raw.raster_dots.data["brush"][first].color() == want
    assert raw.marks.data["brush"][first].color() == want


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
