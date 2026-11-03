"""Analysis: block lists, event detection, spike sorting, and the Analysis tab."""
import numpy as np
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
    page = win.analysis_page
    assert page.channel.count() == 2 and page.current.maximum() == 3
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
    page = win.analysis_page
    base = page._baseline()
    page.thr_line.setValue(base - 0.3)
    page._threshold_dragged()
    assert page.thr_kind.currentData() is True and page.threshold.value() == pytest.approx(0.3)
    assert page.polarity.currentData() == "negative"
