"""The simulated recording: per-input settings, EPSPs, voltage-dependent firing, and the tab."""
import numpy as np
import pytest
from scipy.signal import find_peaks

from neusci_daq import config, simulation as sim
from neusci_daq.backends import SimulatedBackend

from test_channels import app, make_cfg, pump, win  # noqa: F401  (fixtures)

FS = 20000.0


def backend(inputs=(), monitor=""):
    cfg = config.Config(backend="simulated", sample_rate=FS, chunk_size=1000,
                        channels=[config.Channel("ai0", "A"), config.Channel("ai1", "B")],
                        sim_inputs=list(inputs), stim_monitor=monitor)
    be = SimulatedBackend(cfg)
    be._setup()                                              # what start() does first
    return be


# ---------------------------------------------------------------- the model
def test_spike_shape():
    w, trough = sim.spike_shape(sim.SimCell(depth_v=1.0, width_ms=0.55, peak_ratio=0.35), FS)
    assert w[trough] == pytest.approx(-1.0) and w.argmin() == trough
    peak = trough + w[trough:].argmax()
    assert w[peak] == pytest.approx(0.35, abs=0.05)                  # repolarization peak
    assert (peak - trough) / FS * 1e3 == pytest.approx(0.55, abs=0.05)
    assert w[:trough].max() > 0                                      # small bump before the trough


def test_rate_rises_with_depolarization():
    c = sim.SimCell(max_rate=100, threshold_v=0.25, slope_v=0.05)
    assert c.rate(0.0) < 1 and c.rate(0.25) == pytest.approx(50) and c.rate(1.0) == pytest.approx(100, abs=0.1)
    rest = [float(c.rate(0)) for c in sim.default_cells()]
    assert rest == pytest.approx([1.0, 0.6, 0.3], abs=0.05)          # the old fixed rates


def test_pulse_onsets_and_epsp_shape():
    w = np.zeros(400)
    w[10:20], w[110:120] = 1.0, 2.0                                  # two pulses
    assert sim.pulse_onsets(w) == [(10, 1.0), (110, 2.0)]
    k = sim.epsp_shape(sim.SimInput("ai0", epsp_delay_ms=5, epsp_peak_ms=4), FS)
    assert k.argmax() == int(0.005 * FS) + int(0.004 * FS) and k.max() == pytest.approx(1.0)


# ---------------------------------------------------------------- the backend
def test_each_pulse_gives_an_epsp_that_sums():
    be = backend([sim.SimInput("ai1", epsp_v_per_v=0.5, epsp_delay_ms=5, epsp_peak_ms=4)])
    w = np.zeros(int(0.03 * FS))
    w[:20] = 1.0                                                     # 1 V pulse at 0 ms
    w[int(0.002 * FS):int(0.002 * FS) + 20] = 1.0                    # and another 2 ms later
    be._start_stimulus("", w, be.cfg.sim_inputs, FS)
    d0 = be._take_epsp("ai0", 2000)                                  # ai0: default 0.3 V per V
    d1 = be._take_epsp("ai1", 2000)
    assert d1.max() > 0.5 * 1.2 and d1.max() < 1.0                  # two EPSPs summed
    assert d0.max() == pytest.approx(d1.max() * 0.3 / 0.5, rel=0.01)
    assert np.argmax(d1) / FS > 0.009                                # after the delay and rise


def test_monitor_input_records_the_stimulus_not_epsps():
    be = backend(monitor="ai0")
    w = np.ones(200)
    be._start_stimulus("ai0", w, [], FS)
    assert not len(be._epsp.get("ai0", [])) and be._epsp["ai1"].max() > 0


def test_firing_rate_follows_depolarization():
    cells = [sim.SimCell(depth_v=1.0, max_rate=200, threshold_v=0.3, slope_v=0.05)]
    be = backend()

    def count(d_value):
        be._tail.clear(); be._last_spike.clear()
        y = np.concatenate([be._spikes("ai0", cells, np.full(1000, d_value), k * 1000, FS)
                            for k in range(200)])                    # 10 s
        return len(find_peaks(-y, height=0.5, distance=int(0.002 * FS))[0])
    at_rest, depolarized = count(0.0), count(0.6)
    assert at_rest < 10 and depolarized > 1000                       # ~0.5/s vs ~200/s
    assert depolarized < 10 * 200 * 1.1                              # refractory keeps it sane


def test_cells_have_their_own_heights():
    be = backend()
    cells = sim.default_cells()
    y = np.concatenate([be._spikes("ai0", cells, np.zeros(1000), k * 1000, FS) for k in range(20 * 60)])
    depths = -y[find_peaks(-y, height=0.2, distance=int(0.002 * FS))[0]]
    for c in cells:                                                  # each cell's spikes are there, ±15%
        assert np.sum(np.abs(depths - c.depth_v) < 0.15 * c.depth_v) > 10


# ---------------------------------------------------------------- config
def test_settings_round_trip_through_the_config(tmp_path):
    inp = sim.SimInput("ai1", noise_v=0.01, sine_v=0, epsp_v_per_v=0.8,
                       cells=[sim.SimCell(depth_v=0.7, threshold_v=0.4, burst=0.2)])
    c = config.Config(backend="simulated", channels=[config.Channel("ai0", "A")], sim_inputs=[inp])
    back = config.load(config.save(c, tmp_path / "s.toml"))
    assert back.sim_inputs == [inp]
    empty = config.Config(backend="simulated", channels=[config.Channel("ai0", "A")],
                          sim_inputs=[sim.SimInput("ai2", cells=[])])
    assert config.load(config.save(empty, tmp_path / "e.toml")).sim_inputs[0].cells == []


def test_old_no_sine_setting_still_loads(tmp_path):
    p = tmp_path / "old.toml"
    p.write_text('[device]\nbackend = "simulated"\n[simulated]\nno_sine = ["ai1"]\n'
                 '[[channels]]\nphysical = "ai0"\nname = "A"\n')
    (inp,) = config.load(p).sim_inputs
    assert inp.physical == "ai1" and inp.sine_v == 0 and len(inp.cells) == 3


def test_example_config_loads_its_simulation():
    cfg = config.load("config.toml")
    assert sim.input_for(cfg.sim_inputs, "ai1").sine_v == 0


# ---------------------------------------------------------------- the tab
def test_simulation_tab_edits_the_running_settings(app, win):
    assert "Simulation" in [win.tabs.panes[0].bar.tabText(i) for i in range(win.tabs.panes[0].bar.count())]
    page = win.sim_page
    page.input.setCurrentIndex(page.input.findData("ai1"))
    page.sine_v.setValue(0)
    page.epsp.setValue(0.5)
    inp = sim.input_for(win.cfg.sim_inputs, "ai1")
    assert inp.sine_v == 0 and inp.epsp_v_per_v == 0.5
    assert win.backend.cfg.sim_inputs is win.cfg.sim_inputs          # the running backend sees it
    page.table.item(0, 0).setText("700 mV")                          # spike size, with a unit
    page.table.item(0, 4).setText("0.1")                             # threshold (V)
    c = sim.input_for(win.cfg.sim_inputs, "ai1").cells[0]
    assert c.depth_v == pytest.approx(0.7) and c.threshold_v == pytest.approx(0.1)
    assert float(page.table.item(0, 7).text().split()[0]) == pytest.approx(float(c.rate(0)), rel=0.01)
    page.table.item(0, 3).setText("lots")                            # bad cell: message, nothing stored
    assert "must be a number" in page.message.text()
    page._show_input()
    page._add_cell()
    assert len(sim.input_for(win.cfg.sim_inputs, "ai1").cells) == 4
    page._reset()
    assert not any(i.physical == "ai1" for i in win.cfg.sim_inputs)
    assert page.sine_v.value() == pytest.approx(0.5)                 # defaults shown again


def test_hardware_has_no_simulation_tab(app, tmp_path):
    from pyqtgraph.Qt import QtCore
    from neusci_daq.gui import MainWindow
    cfg = make_cfg(tmp_path)
    cfg.backend = "multichannelio"                                   # no hardware needed to build it
    st = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    w = MainWindow(cfg, settings=st, config_path=tmp_path / "config.toml")
    assert w.sim_page is None
    w.close()
