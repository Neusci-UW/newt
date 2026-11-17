"""On a real USB-6003 only: sending a whole Complex Stimulus protocol as ONE hardware-timed
output (protocolspage._start_output) has only been tried on the simulated backend. These run
the real thing and skip themselves (saying why) when no USB-6003 is connected.

Wiring: a wire from ao0 to an analog input, ai3 unless NEWT_6003_LOOPBACK says otherwise
(e.g. NEWT_6003_LOOPBACK=ai1). Run with:

    .venv/bin/python -m pytest -v tests/test_hw_6003.py

What's checked:
  * a very long output (FULL_MAX_SAMPLES, the most a protocol is sent as in one go) can be
    written to the device and starts, and how long writing it takes before the clock starts
  * a protocol with long waits (acquisition stops between trials) lines every trial up on
    the recorded stimulus, at the same place in its window
  * Pause lets the playing stimulus finish, and Resume sends the rest
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
import pytest

LOOPBACK = os.environ.get("NEWT_6003_LOOPBACK", "ai3")


def _usb6003() -> str | None:
    """The name of a connected USB-6003 (e.g. "Dev1"), or None."""
    try:
        import nidaqmx.system
        for d in nidaqmx.system.System.local().devices:
            if "6003" in d.product_type:
                return d.name
    except Exception:
        return None
    return None


DEVICE = _usb6003()
pytestmark = pytest.mark.skipif(DEVICE is None, reason="no USB-6003 connected (needs nidaqmx and the device)")


def pump(app, s):
    end = time.time() + s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def hw(tmp_path):
    """NEWT on the USB-6003: ai0 and the loopback input, the stimulus recorded on the loopback."""
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtCore
    from neusci_daq import config
    from neusci_daq.config import Channel
    from neusci_daq.gui import MainWindow
    app = pg.mkQApp()
    cfg = config.Config(backend="nidaqmx", device=DEVICE, sample_rate=20000, chunk_size=400,
                        channels=[Channel("ai0", "Ch1"), Channel(LOOPBACK, "Stim")],
                        data_dir=str(tmp_path / "data"))
    st = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    w = MainWindow(cfg, settings=st, config_path=tmp_path / "config.toml")
    w._ask_unsaved = lambda: "discard"
    assert w.set_stim_monitor(LOOPBACK)
    yield app, w
    w.btn_run.setChecked(False)
    if w.stim_out is not None:
        w.stim_out.stop()
    w.close()


def _protocol(w, gap_s: float, n: int = 4):
    """A 100 ms baseline / 100 ms step / 100 ms baseline stimulus whose step steps per trial."""
    from neusci_daq import stim as S
    levels = ", ".join(f"{0.5 * (k + 1):g}" for k in range(n))
    w.set_stimulus(S.Stimulus.from_dict({"shape": "segments", "parts": [
        {"kind": "base", "level": 0.0, "dur": 100.0},
        {"kind": "step", "level": 1.0, "dur": 100.0, "d_level": levels},
        {"kind": "base", "level": 0.0, "dur": 100.0}]}))
    w.stimulus_page.n_trials.setValue(n)
    tr = w.trials_page
    tr.repeats.setValue(1)
    tr.gap.setValue(gap_s)
    tr.before.setValue(5)
    tr.after.setValue(30)
    w.set_record_stim("complex")
    w.btn_rec.setChecked(True)
    return tr


def _steps_ms(w) -> list[float]:
    """Where the step starts in each saved trial (ms into its window), from the Stim channel."""
    import h5py
    out = []
    for b in w.blocks:
        with h5py.File(b) as f:
            a = f.attrs
            y = f["raw_volts"][list(a["channel_names"]).index("Stim")]
            on = np.flatnonzero(y > 0.25)
            assert json.loads(a["trials"])["trials"][0]["found"], f"{b.name} wasn't lined up"
            out.append(on[0] / a["sample_rate"] * 1000 if len(on) else float("nan"))
    return out


def test_the_longest_whole_protocol_output_writes_and_starts(hw):
    """FULL_MAX_SAMPLES of output in one go: it must start (and how long writing it took is
    printed: that's how late the protocol's first stimulus is after Run)."""
    app, w = hw
    from neusci_daq.protocolspage import FULL_MAX_SAMPLES
    rate = w.cfg.stim_rate
    wave = np.zeros(FULL_MAX_SAMPLES)
    wave[: int(0.1 * rate)] = 1.0                       # a 100 ms pulse first, to see it start
    w.btn_run.setChecked(True)
    pump(app, 0.5)
    asked = time.monotonic()
    w.stim_out.fire(wave, rate, [w.cfg.stim_output])
    deadline = asked + 60
    while w.stim_out.started_at is None and time.monotonic() < deadline and w.stim_out.error is None:
        pump(app, 0.05)
    assert w.stim_out.error is None, f"the device refused it: {w.stim_out.error!r}"
    assert w.stim_out.started_at is not None, "it never started (60 s)"
    print(f"\n{FULL_MAX_SAMPLES / 1e6:.0f} M samples ({FULL_MAX_SAMPLES / rate / 60:.0f} min) "
          f"took {w.stim_out.started_at - asked:.2f} s to write before the clock started")
    pump(app, 1.0)
    assert w.stim_out.busy
    w.stim_out.stop()                                   # no need to wait out the hour
    pump(app, 0.5)
    assert not w.stim_out.busy


def test_a_protocol_with_long_waits_lines_every_trial_up(hw):
    """Every 6 s: acquisition stops between trials while the output plays on. Each trial is
    found on the recorded stimulus, with its step at the same place in its window."""
    app, w = hw
    tr = _protocol(w, gap_s=6.0, n=4)
    pump(app, 0.3)
    assert tr.btn_run.isEnabled(), tr.summary.text()
    fired = w.stim_out.fired
    tr.btn_run.click()
    end = time.time() + 60
    while tr.running and time.time() < end:
        pump(app, 0.1)
    assert not tr.running, "it didn't finish"
    assert w.stim_out.fired - fired == 1, "the protocol wasn't sent as one output"
    steps = _steps_ms(w)
    print("\nstep at (ms into each trial):", [round(s, 2) for s in steps])
    assert len(steps) == 4
    assert max(steps) - min(steps) < 0.5, "trials aren't lined up alike"


def test_pause_finishes_the_playing_stimulus_and_resume_sends_the_rest(hw):
    app, w = hw
    tr = _protocol(w, gap_s=1.0, n=4)
    pump(app, 0.3)
    tr.btn_run.click()
    end = time.time() + 30
    while tr.next_i < 2 and time.time() < end:
        pump(app, 0.05)
    tr.set_paused(True)
    pump(app, 2.0)
    assert not w.stim_out.busy, "the output didn't stop after the playing stimulus"
    tr.set_paused(False)
    end = time.time() + 30
    while tr.running and time.time() < end:
        pump(app, 0.1)
    assert not tr.running
    steps = _steps_ms(w)
    print("\nstep at (ms into each trial):", [round(s, 2) for s in steps])
    assert len(steps) == 4 and max(steps) - min(steps) < 0.5
