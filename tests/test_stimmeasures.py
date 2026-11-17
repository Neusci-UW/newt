import numpy as np

from neusci_daq import stimmeasures as SM

FS = 10000
T = np.arange(int(0.2 * FS))


def pulses(v, width_ms, n=1, every_ms=10, start_ms=20, noise=0.005):
    y = np.zeros(T.size)
    for k in range(n):
        a = int((start_ms + k * every_ms) / 1000 * FS)
        y[a:a + int(width_ms / 1000 * FS)] = v
    return y + noise * np.random.default_rng(n + int(10 * v)).standard_normal(T.size)


def sine(v, hz, ms=100):
    return np.where(T < ms / 1000 * FS, v * np.sin(2 * np.pi * hz * T / FS), 0.0)


def test_a_pulse_train_and_a_sine_are_measured():
    p = SM.measure(pulses(2, 5), FS)
    assert abs(p["amp"] - 2) < 0.05 and abs(p["p2p"] - 2) < 0.05 and abs(p["var"] - 4) < 0.2
    assert p["width"] == 5 and p["len"] == 5 and p["count"] == 1 and p["freq"] == 0
    assert abs(p["area"] - 10) < 0.2                                  # 2 V for 5 ms
    t = SM.measure(pulses(2, 1, n=5, every_ms=10), FS)
    assert t["count"] == 5 and abs(t["freq"] - 100) < 1 and t["width"] == 1 and abs(t["len"] - 41) < 0.5
    w = SM.measure(sine(1, 20), FS)                                   # starts at time 0: rest is still 0
    assert abs(w["amp"] - 1) < 0.01 and abs(w["p2p"] - 2) < 0.01 and abs(w["freq"] - 20) < 0.5
    assert w["count"] == 2 and abs(w["len"] - 100) < 1 and abs(w["var"] - 0.5) < 0.02


def test_a_flat_trace_has_no_stimulus():
    assert SM.measure(np.zeros(100), FS) is None
    assert SM.measure(np.full(100, 3.0), FS) is None


def test_the_same_shape_at_any_size_is_alike():
    s = SM.shape_alike([(pulses(1, 1), FS), (pulses(4, 1), FS), (pulses(2, 5), FS), None, (sine(1, 20), FS)])
    assert s[0, 1] > 0.99                                             # same pulse, 4 times bigger
    assert abs(s[0, 2] - np.sqrt(1 / 5)) < 0.05                       # 1 ms in a 5 ms pulse
    assert s[0, 4] < 0.3 and s[3, 0] == 0 and s[3, 3] == 1            # nothing to compare: not alike
    assert np.allclose(s, s.T)


def test_the_map_follows_the_measures_and_cuts_into_groups():
    meas = [SM.measure(y, FS) for y in (pulses(1, 1), pulses(2, 1), pulses(4, 1), pulses(2, 10))]
    m = SM.alike_map(meas, ["amp", "width"])
    assert m["share"][0] > 0.5 and abs(sum(m["share"]) - 1) < 1e-6   # two measures: two directions hold it all
    for a, key in enumerate(m["mostly"]):                             # each direction's measure: bigger right (up)
        assert key in ("amp", "width")
        assert np.corrcoef([x[key] for x in meas], m["X"][:, a])[0, 1] > 0.5
    assert SM.cut(m["D"]) == [[0], [1], [2], [3]]
    assert SM.cut(m["D"], groups=1) == [[0, 1, 2, 3]]
    assert [0, 1] in SM.cut(m["D"], groups=3)                          # 1 and 2 V, 1 ms: the closest
    assert len(SM.cut(m["D"], close=float(m["D"].max()))) == 1
    shape = SM.shape_alike([(pulses(v, w), FS) for v, w in ((1, 1), (2, 1), (4, 1), (2, 10))])
    s = SM.alike_map(meas, [SM.SHAPE], shape)
    assert s["D"][0, 1] < 0.05 * s["D"][0, 3]                          # shape alone: sizes don't count


def test_no_map_without_two_stimuli_or_a_measure_that_differs():
    meas = [SM.measure(pulses(2, 1), FS), SM.measure(pulses(2, 1), FS)]
    assert SM.alike_map(meas[:1], ["amp"]) is None
    assert SM.alike_map(meas, []) is None
    assert SM.alike_map(meas, ["width"]) is None                      # the same for both
