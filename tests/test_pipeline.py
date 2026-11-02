import time
import warnings

import numpy as np
import pytest

from neusci_daq import config
from neusci_daq.backends import SimulatedBackend
from neusci_daq.recorder import Recorder, load_recording


def make_cfg(**kw):
    c = config.Config(backend="simulated", sample_rate=1000, chunk_size=50,
                      channels=[config.Channel("ai0", "A", "mV", scale=2.0, offset=1.0),
                                config.Channel("ai1", "B")], **kw)
    config.validate(c)
    return c


def test_record_roundtrip(tmp_path):
    be = SimulatedBackend(make_cfg())
    rec = Recorder(be)
    be.start()
    time.sleep(0.2)
    rec.start(tmp_path / "r.h5")
    time.sleep(0.5)
    rec.add_comment("drug on")
    time.sleep(0.2)
    rec.stop()
    be.stop()
    assert be.error is None
    r = load_recording(tmp_path / "r.h5")
    assert r["data"].shape[0] == 2 and r["data"].shape[1] > 300
    np.testing.assert_allclose(r["data"][0], r["raw_volts"][0] * 2 + 1)
    assert r["comments"]["text"][0] in (b"drug on", "drug on")
    assert list(r["attrs"]["channel_names"]) == ["A", "B"]
    assert r["t"][0] == 0


def test_rejects_over_aggregate_rate():
    c = make_cfg()
    c.backend, c.sample_rate = "nidaqmx", 60_000
    with pytest.raises(ValueError, match="limit"):
        config.validate(c)


def test_diff_limits_channels():
    c = make_cfg()
    c.backend, c.terminal_config = "nidaqmx", "DIFF"
    c.channels[1].physical = "ai5"
    with pytest.raises(ValueError, match="ai0–ai3"):
        config.validate(c)


def test_warns_on_fake_gain():
    c = make_cfg()
    c.backend = "nidaqmx"
    c.channels[0].range = (-0.1, 0.1)
    with pytest.warns(UserWarning, match="does NOT add gain"):
        config.validate(c)


def test_example_config_loads():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = config.load("config.toml")
    assert cfg.backend == "nidaqmx" and cfg.channels   # whatever channels this rig uses


def test_simulated_no_sine_input_is_just_noise_and_spikes(tmp_path):
    cfg = make_cfg(sim_no_sine=("ai1",))
    be = SimulatedBackend(cfg)
    q = be.subscribe()
    be.start()
    time.sleep(1.2)                                          # > one period of ai0's 1 Hz sine
    be.stop()
    data = np.concatenate([q.get_nowait().data for _ in range(q.qsize())], axis=1)
    quiet = lambda x: np.median(np.abs(x))                   # spikes don't move the median
    assert quiet(data[0]) > 0.2 and quiet(data[1]) < 0.05    # ai0 has the sine, ai1 doesn't
    back = config.load(config.save(cfg, tmp_path / "s.toml"))
    assert back.sim_no_sine == ("ai1",)


def test_simulated_spikes_have_three_extracellular_heights():
    from scipy.signal import find_peaks
    from neusci_daq.backends import SIM_UNITS, extracellular_spike
    fs = 20000
    w, trough = extracellular_spike(1.0, fs, trough_to_peak_ms=0.55, peak_ratio=0.35)
    assert w[trough] == pytest.approx(-1.0) and w.argmin() == trough
    peak = trough + w[trough:].argmax()
    assert w[peak] == pytest.approx(0.35, abs=0.05)                  # repolarization peak
    assert (peak - trough) / fs * 1e3 == pytest.approx(0.55, abs=0.05)
    assert w[:trough].max() > 0                                      # small bump before the trough
    be = SimulatedBackend(make_cfg())
    be._setup()                                              # what start() does first
    shapes = [extracellular_spike(d, fs, tp, r) for d, _, tp, r, _ in SIM_UNITS]
    pad = max(len(s) for s, _ in shapes) + int(np.ceil(0.018 * fs)) + 1
    be._spike_tail = np.zeros((1, pad))
    y = np.concatenate([be._spikes(shapes, 1, 1000, fs, pad)[0] for _ in range(20 * 60)])  # 60 s
    peaks, _ = find_peaks(-y, height=0.2, distance=int(0.002 * fs))
    depths = -y[peaks]
    for d, *_ in SIM_UNITS:                                  # each unit's spikes are there, ±15%
        assert np.sum(np.abs(depths - d) < 0.15 * d) > 10
