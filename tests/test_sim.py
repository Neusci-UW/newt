"""The simulated network: integrate-and-fire cells, synapses, recordings, config and the tab."""
import time

import numpy as np
import pytest

from neusci_daq import config, simulation as sim
from neusci_daq.backends import SimulatedBackend

from test_channels import app, make_cfg, pump, win  # noqa: F401  (fixtures)

FS = 20000.0


def spikes_of(spikes, cell):
    return [k for k, c in spikes if c == cell]


def pulse(width_ms=1.0, volts=1.0):
    return np.full(int(round(width_ms / 1000 * FS)), volts)


# ---------------------------------------------------------------- the cells
def test_cells_fire_now_and_then_at_rest():
    st = sim.NetworkState(sim.default_network(), np.random.default_rng(0))
    v, spikes = st.run(int(20 / sim.SIM_DT))                      # 20 s
    for i in range(3):
        assert 0.3 < len(spikes_of(spikes, i)) / 20 < 5            # ~1-2 spikes/s
    assert v.max() < -49 and v.min() > -90                         # held below threshold; resets


def test_a_cell_fires_at_threshold_and_respects_its_refractory_period():
    net = sim.Network([sim.Cell(drive_mv=40, noise_mv=0, refractory_ms=5)])   # driven past threshold
    net.set_weights(np.zeros((1, 1)))
    _, spikes = sim.NetworkState(net, np.random.default_rng(0)).run(int(1 / sim.SIM_DT))
    gaps = np.diff([k for k, _ in spikes]) * sim.SIM_DT
    assert len(spikes) > 10 and gaps.min() >= 0.005 - 1e-9


def test_stimulus_is_the_depolarization_from_a_1_v_1_ms_pulse():
    net = sim.Network([sim.Cell(drive_mv=0, noise_mv=0, stimulus=10.0), sim.Cell(drive_mv=0, noise_mv=0)])
    net.set_weights(np.zeros((2, 2)))
    st = sim.NetworkState(net, np.random.default_rng(0))
    st.stimulate(pulse(), FS)                                       # 1 V, 1 ms: 10 mV on cell 1
    v, _ = st.run(int(0.1 / sim.SIM_DT))
    assert v[:, 0].max() - (-70) == pytest.approx(10, abs=0.1)
    assert v[:, 1].max() == pytest.approx(-70, abs=1e-6)            # cell 2 isn't driven
    assert np.argmax(v[:, 0]) * sim.SIM_DT == pytest.approx(0.001, abs=2e-4)   # at the pulse's end


def test_synapse_sizes_are_the_psp_in_mv():
    net = sim.Network([sim.Cell(drive_mv=0, noise_mv=0, stimulus=60.0), sim.Cell(drive_mv=0, noise_mv=0)],
                      synapse_delay_ms=2)
    net.set_weights(np.array([[0, 5.0], [0, 0]]))
    st = sim.NetworkState(net, np.random.default_rng(0))
    st.stimulate(pulse(), FS)                                       # cell 1 fires once
    v, sp = st.run(int(0.1 / sim.SIM_DT))
    assert len(spikes_of(sp, 0)) == 1
    assert v[:, 1].max() - (-70) == pytest.approx(5, abs=0.1)       # a 5 mV EPSP on cell 2
    assert (np.argmax(v[:, 1]) - spikes_of(sp, 0)[0]) * sim.SIM_DT > 0.002   # after the synaptic delay


def quiet_cell(**kw):
    net = sim.Network([sim.Cell(drive_mv=0, noise_mv=0, **kw)])
    net.set_weights(np.zeros((1, 1)))
    return sim.NetworkState(net, np.random.default_rng(0))


def test_pulse_width_matters_strength_duration():
    fired = {}
    for w in (0.2, 0.5, 1, 2, 5):
        st = quiet_cell(stimulus=12.0)                              # threshold is 20 mV above rest
        st.stimulate(pulse(w), FS)
        fired[w] = len(st.run(int(0.05 / sim.SIM_DT))[1])
    assert fired[0.2] == fired[0.5] == fired[1] == 0 and fired[2] == 1 and fired[5] >= 1
    need = []                                                       # the volts needed fall with width
    for w in (0.5, 1, 2, 5):
        for volts in np.arange(0.1, 10, 0.05):
            st = quiet_cell(stimulus=12.0)
            st.stimulate(pulse(w, volts), FS)
            if st.run(int(0.05 / sim.SIM_DT))[1]:
                need.append(volts)
                break
    assert all(a > b for a, b in zip(need, need[1:]))


def test_a_long_step_fires_repeatedly_faster_when_stronger():
    counts = []
    for volts in (0.15, 0.3, 0.6):                                  # 16, 32, 63 mV held
        st = quiet_cell(stimulus=10.0)
        st.stimulate(pulse(500, volts), FS)
        counts.append(len(st.run(int(0.5 / sim.SIM_DT))[1]))
    assert counts[0] == 0 and 3 < counts[1] < counts[2]


def test_a_slow_sine_entrains_the_cell_and_a_fast_one_is_filtered_out():
    t = np.arange(int(FS)) / FS
    st = quiet_cell(stimulus=4.0)
    st.stimulate(np.sin(2 * np.pi * 5 * t), FS)
    sp = np.array([k for k, _ in st.run(int(1 / sim.SIM_DT))[1]]) * sim.SIM_DT
    assert len(sp) >= 5
    phase = (sp * 5) % 1
    assert np.all(phase < 0.5)                                      # only on the rising, positive half
    st = quiet_cell(stimulus=4.0)
    st.stimulate(np.sin(2 * np.pi * 500 * t), FS)
    assert not st.run(int(1 / sim.SIM_DT))[1]


def test_activity_spreads_along_excitatory_synapses_and_inhibition_hyperpolarizes():
    net = sim.default_network()                                     # 1 -> 2 -> 3, 3 -| 1
    st = sim.NetworkState(net, np.random.default_rng(1))
    hits = np.zeros(3)
    for _ in range(30):
        st.stimulate(pulse(), FS)
        _, sp = st.run(int(1 / sim.SIM_DT))
        for i in range(3):
            hits[i] += any(c == i and k * sim.SIM_DT < 0.03 for k, c in sp)
    assert hits[0] == 30 and hits[1] > 20 and hits[2] > 15          # down the chain
    quiet = sim.Network([sim.Cell(noise_mv=0, drive_mv=0, stimulus=40), sim.Cell(noise_mv=0, drive_mv=0)])
    quiet.set_weights(np.array([[0, -10.0], [0, 0]]))               # 1 inhibits 2
    st = sim.NetworkState(quiet, np.random.default_rng(0))
    st.stimulate(pulse(), FS)
    v, sp = st.run(int(0.1 / sim.SIM_DT))
    assert spikes_of(sp, 0) and v[:, 1].min() == pytest.approx(-80, abs=0.2)   # a 10 mV IPSP


def test_changing_settings_while_running_keeps_the_cells():
    net = sim.default_network()
    st = sim.NetworkState(net, np.random.default_rng(0))
    st.run(1000)
    v = st.v.copy()
    net2 = sim.copy_network(net)
    net2.cells[0].threshold_mv = -45
    st.configure(net2)
    assert np.array_equal(st.v, v) and st.thr[0] == -45
    sim.resize(net2, 5)
    st.configure(net2)
    assert len(st.v) == 5


def test_resize_keeps_settings_and_synapses():
    net = sim.default_network()
    net.cells[1].tau_ms = 25
    sim.resize(net, 5)
    assert len(net.cells) == 5 and net.cells[1].tau_ms == 25 and net.weights()[0, 1] == 15
    net.recordings = [sim.Recording("ai0", cell=4)]
    sim.resize(net, 2)
    assert net.weights().shape == (2, 2) and net.recordings[0].cell == 1
    sim.resize(net, 50)
    assert len(net.cells) == sim.MAX_CELLS


# ---------------------------------------------------------------- what an input records
def test_backend_records_intracellular_and_extracellular():
    net = sim.default_network()
    net.recordings = [sim.Recording("ai0", 0, "intracellular", gain=10, noise_v=0),
                      sim.Recording("ai1", 1, "extracellular", noise_v=0)]
    cfg = config.Config(backend="simulated", sample_rate=FS, chunk_size=1000, sim_network=net,
                        channels=[config.Channel("ai0", "A"), config.Channel("ai1", "B")])
    be = SimulatedBackend(cfg)
    q = be.subscribe()
    be.start()
    time.sleep(0.3)
    w = np.zeros(100)
    w[:20] = 1.0
    be.inject("", w, 5000)                                          # cell 1 fires, then 2
    time.sleep(1.5)
    be.stop()
    assert be.error is None
    d = np.concatenate([q.get_nowait().data for _ in range(q.qsize())], axis=1)
    mv = d[0] * 100                                                 # gain 10: V on the trace -> mV
    assert mv.max() == pytest.approx(sim.AP_PEAK_MV, abs=1)         # action potentials
    assert -75 < np.median(mv) < -55                                # resting near rest + drive
    assert d[1].min() == pytest.approx(-0.9, abs=0.1)               # cell 2's extracellular spikes
    assert abs(np.median(d[1])) < 0.01
    assert len(be.rates()) == 3 and be.rates()[0] > 0


def test_monitor_input_records_the_stimulus():
    cfg = config.Config(backend="simulated", sample_rate=FS, chunk_size=1000, stim_monitor="ai0",
                        channels=[config.Channel("ai0", "Stim"), config.Channel("ai1", "B")])
    be = SimulatedBackend(cfg)
    q = be.subscribe()
    be.start()
    time.sleep(0.2)
    be.inject("ai0", np.full(500, 2.0), 5000)
    time.sleep(0.4)
    be.stop()
    d = np.concatenate([q.get_nowait().data for _ in range(q.qsize())], axis=1)
    assert d[0].max() == pytest.approx(2.0, abs=0.02)


def test_spike_shape():
    w, trough = sim.spike_shape(sim.Cell(spike_depth_v=1.0, spike_width_ms=0.55), FS)
    assert w[trough] == pytest.approx(-1.0) and w.argmin() == trough
    assert (trough + w[trough:].argmax() - trough) / FS * 1e3 == pytest.approx(0.55, abs=0.05)
    assert w[:trough].max() > 0


# ---------------------------------------------------------------- config
def test_network_round_trips_through_the_config(tmp_path):
    net = sim.default_network(4)
    net.cells[2].stimulus = 7
    net.set_weights(np.array([[0, 5, 0, -3], [0, 0, 9, 0], [1, 0, 0, 0], [0, 0, 0, 0]], float))
    net.synapse_delay_ms = 3.5
    net.recordings = [sim.Recording("ai1", 3, "extracellular", noise_v=0.03, sine_v=0.1, sine_hz=50)]
    c = config.Config(backend="simulated", channels=[config.Channel("ai0", "A")], sim_network=net)
    back = config.load(config.save(c, tmp_path / "s.toml")).sim_network
    assert back == net


def test_default_network_isnt_written_and_old_settings_still_load(tmp_path):
    c = config.Config(backend="simulated", channels=[config.Channel("ai0", "A")])
    assert "[simulated]" not in config.dumps(c)
    p = tmp_path / "old.toml"
    p.write_text('[device]\nbackend = "simulated"\n[simulated]\nno_sine = ["ai1"]\n\n'
                 '[[simulated.inputs]]\nphysical = "ai1"\nsine_amplitude = 0\nepsp = 0.3\n\n'
                 '[[simulated.inputs.cells]]\ndepth = 0.4\n\n'
                 '[[channels]]\nphysical = "ai0"\nname = "A"\n')
    net = config.load(p).sim_network
    assert len(net.cells) == 3 and net.recordings[0].physical == "ai1" and net.recordings[0].sine_v == 0


def test_example_config_loads():
    net = config.load("config.toml").sim_network
    assert 1 <= len(net.cells) <= sim.MAX_CELLS


def test_validate_catches_bad_settings():
    net = sim.default_network()
    net.cells[0].reset_mv = -40                                     # above threshold
    with pytest.raises(ValueError, match="reset"):
        sim.validate(net)


# ---------------------------------------------------------------- the tab
def test_simulation_tab_edits_the_running_network(app, win):
    page = win.sim_page
    assert page.cells.rowCount() == 3 and page.syn.rowCount() == 3
    page.syn.item(0, 2).setText("-8 mV")                            # 1 inhibits 3
    page.cells.item(1, 1).setText("-45 mV")                         # cell 2 threshold
    net = win.cfg.sim_network
    assert net.weights()[0, 2] == -8 and net.cells[1].threshold_mv == -45
    assert win.backend.cfg.sim_network is net                       # the running backend sees it
    page.recs.cellWidget(1, 1).setCurrentIndex(1)                   # ai1: extracellular
    page.recs.cellWidget(1, 0).setCurrentIndex(2)                   # recording cell 3
    rec = sim.recording_for(win.cfg.sim_network, "ai1")
    assert rec.kind == "extracellular" and rec.cell == 2
    page.cells.item(0, 0).setText("lots")                           # bad: message, nothing stored
    assert "must be a number" in page.message.text()
    assert win.cfg.sim_network.cells[0].rest_mv == -70
    page.load()
    page.n_cells.setValue(6)
    assert len(win.cfg.sim_network.cells) == 6 and page.syn.columnCount() == 6
    assert win.cfg.sim_network.weights()[0, 2] == -8                # kept
    page.n_cells.setValue(2)
    assert sim.recording_for(win.cfg.sim_network, "ai1").cell == 1  # moved to a cell that exists
    page._reset()
    assert win.cfg.sim_network == sim.default_network()


def test_simulation_tab_shows_live_firing_rates(app, win):
    win.show()
    win.btn_run.setChecked(True)
    pump(app, 1.5)
    win.tabs.pick(0, "sim")
    pump(app, 0.1)
    win.sim_page._show_rates()
    from neusci_daq.simpage import _LIVE_COL
    assert win.sim_page.cells.item(0, _LIVE_COL).text().endswith("/s")
    win.btn_run.setChecked(False)


def test_hardware_has_no_simulation_tab(app, tmp_path):
    from pyqtgraph.Qt import QtCore
    from neusci_daq.gui import MainWindow
    cfg = make_cfg(tmp_path)
    cfg.backend = "multichannelio"
    st = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    w = MainWindow(cfg, settings=st, config_path=tmp_path / "config.toml")
    assert w.sim_page is None
    w.close()


def test_simulation_tab_shows_and_sets_what_the_stimulator_drives(app, win):
    page = win.sim_page
    assert [t.isChecked() for t, _ in page.stim_boxes] == [True, False, False]   # the default chain
    assert "drives cell 1" in page.stim_wire.text() and page.btn_record_stim.isVisible() is not None
    tick, strength = page.stim_boxes[1]
    tick.setChecked(True)                                            # cell 2 too
    strength.setValue(15)
    assert [c.stimulus for c in win.cfg.sim_network.cells] == [25.0, 15.0, 0.0]
    page.stim_boxes[0][0].setChecked(False)                          # not cell 1
    assert win.cfg.sim_network.cells[0].stimulus == 0
    page.stim_boxes[0][0].setChecked(True)                           # back, with its old strength
    assert win.cfg.sim_network.cells[0].stimulus == 25.0
    col = [c[1] for c in __import__("neusci_daq.simpage", fromlist=["x"])._CELL_COLUMNS].index("stimulus")
    page.cells.item(2, col).setText("5 mV")                          # the table and the box agree
    assert page.stim_boxes[2][0].isChecked() and page.stim_boxes[2][1].value() == 5
    assert not win.cfg.stim_monitor
    page.btn_record_stim.click()                                     # record the stimulus for Trials
    assert win.cfg.stim_monitor and "recorded on" in page.stim_wire.text()
    win.stim_page._show_sim_note()
    assert "cell 1 (25 mV), cell 2 (15 mV), cell 3 (5 mV)" in win.stim_page.sim_note.text()


def test_the_backend_injects_the_waveform_and_stop_cuts_it_off():
    cfg = config.Config(backend="simulated", sample_rate=FS, chunk_size=200, stim_monitor="ai0",
                        channels=[config.Channel("ai0", "Stim"), config.Channel("ai1", "Cell")])
    net = sim.Network([sim.Cell(drive_mv=0, noise_mv=0, stimulus=10.0)])
    net.set_weights(np.zeros((1, 1)))
    net.recordings = [sim.Recording("ai1", 0, "intracellular", gain=1, noise_v=0)]
    cfg.sim_network = net
    be = SimulatedBackend(cfg)
    q = be.subscribe()
    be.start()
    time.sleep(0.1)
    be.inject("ai0", np.full(int(0.2 * FS), 0.1), FS)                # 0.1 V for 200 ms: ~10 mV held
    time.sleep(0.1)
    be.inject("ai0", np.zeros(1), FS)                                # Stop
    time.sleep(0.2)
    be.stop()
    d = np.concatenate([q.get_nowait().data for _ in range(q.qsize())], axis=1)
    dep = d[1] * 1000 + 70                                           # mV above rest
    assert 5 < dep.max() < 12                                        # charged toward ~10.5 mV
    assert dep[-int(0.05 * FS):].max() < 1                           # and relaxed after Stop
