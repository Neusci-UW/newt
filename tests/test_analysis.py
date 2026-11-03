"""Analysis: block lists, event detection, spike sorting, and the Analysis tab."""
import numpy as np
from pyqtgraph.Qt import QtCore
import pytest

from neusci_daq import analysis as A, session, simulation as sim
from neusci_daq.gui import REVIEW

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


def test_page_buttons_step_through_blocks(app, win):
    record(app, win, 3)
    win.open_session(win.session_dir)
    assert win.mode == REVIEW and win.block_combo.currentIndex() == 0
    win.btn_next_block.click()
    win.step_block(1)
    assert win.block_combo.currentIndex() == 2
    win.step_block(1)                                                # past the end: stays
    win.btn_prev_block.click()
    assert win.block_combo.currentIndex() == 1


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
    assert page.blocks_item.getOriginalDataset()[1].shape[0] == 3   # all three overlaid
    page.which.setText("2-3")
    page.which.editingFinished.emit()
    assert page.blocks_item.getOriginalDataset()[1].shape[0] == 2
    page.which.setText("all")
    page.which.editingFinished.emit()
    ev = page.find()
    assert ev is not None and len(ev.t) > 0 and page.table.rowCount() == ev.n_units
    assert len(page.raster_dots.data) == len(ev.t)
    assert "event" in page.message.text()
    out = page.export(tmp_path / "events.csv")
    assert out.read_text().splitlines()[0] == "block,time_s,unit,amplitude,channel"
    assert len(out.read_text().splitlines()) == len(ev.t) + 1
    page.which.setText("nonsense")
    page.which.editingFinished.emit()
    assert "isn't a block number" in page.message.text()


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
    assert [ap.tabs.tabText(i) for i in range(ap.tabs.count())] == \
        ["Raw Data Visualization", "Comparison", "Stimuli"]
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
    win.step_block(1)                                                # on Acquisition: the review block
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
    assert raw.rate_plot.getPlotItem().listDataItems()                # spikes/s across the session
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
    assert len(cmp._plots) == 4                                      # Ch1, Ch2, Stim V and the spike rate
    cmp.series.item(1, 1).setCheckState(QtCore.Qt.CheckState.Checked)   # + Ch2 dV/dt
    assert len(cmp._plots) == 5
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
    ap.group.setCurrentIndex(2)                                      # just the 2 V blocks
    assert raw.chosen_or_none() == [2, 3]
    assert raw.blocks_item.getOriginalDataset()[1].shape[0] == 2
    ap.group.setCurrentIndex(0)                                      # all blocks, then compare groups
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


def test_stimuli_tab_shows_each_group(app, win):
    two_stimulus_session(app, win)
    win.show()
    win.tabs.pick(0, "analysis")
    pump(app, 0.2)
    ap = win.analysis_page
    ap.raw.channel.setCurrentText("Ch2")
    ap.raw.find()
    ap.tabs.setCurrentIndex(2)
    pump(app, 0.2)
    st = ap.stimuli
    assert st.table.rowCount() == 2 and st.table.item(0, 2).text() == "1-2"
    assert len(st.stim_plot.getPlotItem().listDataItems()) == 2      # each group's stimulus
    assert len(st.resp_plot.getPlotItem().listDataItems()) == 2      # and response
    assert len(st.rate_plot.getPlotItem().listDataItems()) == 2      # and spike rate
    peaks = [c.getData()[1].max() for c in st.stim_plot.getPlotItem().listDataItems()]
    assert sorted(round(p) for p in peaks) == [1, 2]                  # 1 V and 2 V
    st.table.selectRow(1)                                            # click a group: picked everywhere
    assert ap.group.currentIndex() == 2 and ap.raw.chosen_or_none() == [2, 3]
    # Raw shows the groups too: an average each, and a mark on each block's raster row
    ap.group.setCurrentIndex(0)
    assert len(ap.raw.group_avgs) == 2 and len(ap.raw.group_marks.data) == 4


def test_isi_histogram():
    blocks, _ = fake_blocks()
    ev = A.find_events(blocks)
    isi = A.intervals_ms(ev)
    assert len(isi) == len(ev.t) - ev.n_units * len(blocks)          # within a unit and a block
    assert isi.min() >= 10                                           # the fake spikes are >= 10 ms apart
