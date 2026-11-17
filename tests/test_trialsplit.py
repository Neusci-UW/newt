import json

import h5py
import numpy as np

from neusci_daq import trialfile, trialsplit
from test_channels import app  # noqa: F401  (fixture)

FS = 2000.0


def stimulus_trial():
    """10 s: two 2 V pulses, a train of five 2 ms pulses every 20 ms, a 4 V pulse, a 20 Hz wave."""
    y = np.zeros(int(10 * FS))

    def pulse(t, v, ms):
        a = int(t * FS)
        y[a:a + max(1, int(ms / 1000 * FS))] += v
    pulse(0.5, 2, 5)
    pulse(1.5, 2, 5)
    for k in range(5):
        pulse(2.5 + k * 0.02, 2, 2)
    pulse(4.0, 4, 5)
    a = int(5 * FS)
    y[a:a + 400] += np.sin(2 * np.pi * 20 * np.arange(400) / FS)
    return y + 0.01 * np.random.default_rng(1).standard_normal(y.size)


def test_cuts_land_where_the_stimulus_goes_back_to_rest():
    y = stimulus_trial()
    cuts = trialsplit.rest_cuts(y, FS, before_ms=100)
    assert [trialsplit.describe(y[c.lo:c.hi], FS) for c in cuts] == [
        "Pulse of 2 V, 5 ms", "Pulse of 2 V, 5 ms", "Train of 5 pulses of 2 V, 2 ms, every 20 ms",
        "Pulse of 4 V, 5 ms", "Wave, 1 V, 200 ms"]
    assert cuts[0].lo == cuts[0].start - 200 and cuts[-1].hi == y.size        # Before, and to the end
    assert all(a.hi == b.lo for a, b in zip(cuts, cuts[1:]))                    # back to back
    assert len(trialsplit.rest_cuts(y, FS, 100, gap_ms=5)) == 9                 # each pulse of the train
    long_before = trialsplit.rest_cuts(y, FS, before_ms=5000)
    assert all(b.lo >= a.end for a, b in zip(long_before, long_before[1:]))     # never into the one before
    assert trialsplit.rest_cuts(np.zeros(100), FS, 100) == []


def test_split_at_rest_writes_a_trial_per_stimulus(tmp_path):
    y = stimulus_trial()
    resp = -65 + 0.1 * np.arange(y.size) / y.size
    src = tmp_path / "block001.h5"
    trialfile.write_block(src, np.vstack([y, resp]), FS, ["Stim", "Vm"], ["V", "mV"], ["#fff", "#f80"],
                          [(1.6, "second pulse")], "2026-11-17T10:00:00",
                          stimuli=json.dumps([{"t": 1.5, "rate": FS, "settings": {}}]))
    n = iter(range(2, 99))
    made = trialsplit.split_at_rest(src, "Stim", 100, lambda: tmp_path / f"block{next(n):03d}.h5")
    assert len(made) == 5 and src.exists()
    assert made[1][1] == 1.4 and made[-1][2] == 10.0                            # where each was in the original
    second = trialfile.read_file(made[1][0])
    assert [round(x["t"], 6) for x in second["stimuli"]] == [0.1]               # the stimulus noted goes with it
    assert [round(t, 3) for t, _ in second["comments"]] == [0.2]
    assert trialfile.read_file(made[0][0])["stimuli"] == []
    with h5py.File(made[1][0], "r") as f:
        assert json.loads(f.attrs["split_from"]) == {"trial": "block001", "stimulus": 2, "at_rest": True}
    lengths = [trialfile.read_file(p)["data"].shape[1] for p, _, _ in made]
    assert sum(lengths) == y.size - int(0.4 * FS)                               # nothing after the first lost
    assert np.allclose(trialfile.read_file(made[2][0])["data"][1], resp[int(2.4 * FS):int(3.9 * FS)], atol=1e-4)
    assert trialsplit.split_at_rest(made[0][0], "Stim", 100, lambda: tmp_path / "x.h5") == []   # one stimulus


def test_the_split_dialog_shows_the_cuts_as_you_set_them(app):
    from neusci_daq.splitdialog import SplitAtRestDialog
    y = stimulus_trial()
    rec = {"data": np.vstack([y, np.zeros_like(y)]), "sample_rate": FS, "channel_names": ["Stim", "Vm"],
           "units": ["V", "mV"]}
    dlg = SplitAtRestDialog(None, rec, "Stim", 3, 100, "block001")
    assert dlg.table.topLevelItemCount() == 5 and dlg.go.text() == "Split into 5 trials"
    assert dlg.table.topLevelItem(2).text(1) == "Train of 5 pulses of 2 V, 2 ms, every 20 ms"
    dlg.gap[0].setValue(5)
    dlg.refresh()
    assert dlg.table.topLevelItemCount() == 9
    assert dlg.settings() == (5.0, 0.05, 100.0, False)
    dlg.every.setChecked(True)
    assert dlg.settings()[3]
