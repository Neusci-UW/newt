"""DataPad measurements, like LabChart's DataPad: numbers measured from a time window
of a trace (a recorded block, or a stimulus group's average).

Each measurement takes the window's samples y (and their times t, in seconds from
the block's start) and returns one number. Units follow the trace's unit u.
"""
from __future__ import annotations

import numpy as np

# key: (label, unit from the trace's unit u, help)
MEASUREMENTS = {
    "mean": ("Mean", "{u}", "Average value"),
    "max": ("Maximum", "{u}", "Largest value"),
    "min": ("Minimum", "{u}", "Smallest value"),
    "p2p": ("Peak-to-peak", "{u}", "Maximum − minimum"),
    "amp": ("Amplitude from start", "{u}", "Largest departure from the value at the window's start (signed)"),
    "sd": ("SD", "{u}", "Standard deviation of the samples"),
    "rms": ("RMS", "{u}", "Root mean square"),
    "area": ("Area", "{u}·s", "Integral of the trace over the window (from zero)"),
    "absarea": ("Absolute area", "{u}·s", "Integral of |trace|"),
    "slope": ("Slope", "{u}/s", "Straight-line fit"),
    "tmax": ("Time of maximum", "s", "Time of the largest value, from the block's start"),
    "tmin": ("Time of minimum", "s", "Time of the smallest value, from the block's start"),
    "rise": ("Max rate of rise", "{u}/s", "Largest dV/dt"),
    "fall": ("Max rate of fall", "{u}/s", "Most negative dV/dt"),
    "freq": ("Main frequency", "Hz", "Frequency of the largest peak in the spectrum"),
    "start": ("Value at start", "{u}", "The first sample in the window"),
    "end": ("Value at end", "{u}", "The last sample in the window"),
    "spikes": ("Spike count", "", "Action potentials found on the Spike Sorting tab, in the window"),
    "rate": ("Spike rate", "/s", "Spike count ÷ window length"),
}
SPIKE_KEYS = ("spikes", "rate")


def unit_of(key: str, u: str) -> str:
    return MEASUREMENTS[key][1].format(u=u)


def measure(key: str, t: np.ndarray, y: np.ndarray, fs: float, spike_times=None) -> float:
    """One measurement of the samples y at times t (s). spike_times: the spikes'
    times (s, same clock as t) for the spike measurements; NaN without them."""
    y = np.asarray(y, float)
    t = np.asarray(t, float)
    ok = np.isfinite(y)
    if key in SPIKE_KEYS:
        if spike_times is None or not len(t):
            return float("nan")
        st = np.asarray(spike_times, float)
        n = int(np.sum((st >= t[0]) & (st <= t[-1])))
        return float(n) if key == "spikes" else n / max(t[-1] - t[0] + 1 / fs, 1e-12)
    if not ok.any():
        return float("nan")
    y, t = y[ok], t[ok]
    if key == "mean":
        return float(y.mean())
    if key == "max":
        return float(y.max())
    if key == "min":
        return float(y.min())
    if key == "p2p":
        return float(y.max() - y.min())
    if key == "amp":
        d = y - y[0]
        return float(d[np.argmax(np.abs(d))])
    if key == "sd":
        return float(y.std(ddof=1)) if len(y) > 1 else float("nan")
    if key == "rms":
        return float(np.sqrt(np.mean(y ** 2)))
    if key == "area":
        return float(np.trapezoid(y, t)) if len(y) > 1 else 0.0
    if key == "absarea":
        return float(np.trapezoid(np.abs(y), t)) if len(y) > 1 else 0.0
    if key == "slope":
        return float(np.polyfit(t, y, 1)[0]) if len(y) > 1 else float("nan")
    if key == "tmax":
        return float(t[np.argmax(y)])
    if key == "tmin":
        return float(t[np.argmin(y)])
    if key in ("rise", "fall"):
        if len(y) < 2:
            return float("nan")
        d = np.gradient(y, 1 / fs)
        return float(d.max() if key == "rise" else d.min())
    if key == "freq":
        if len(y) < 4:
            return float("nan")
        spec = np.abs(np.fft.rfft(y - y.mean()))
        k = 1 + int(np.argmax(spec[1:]))
        if 1 <= k < len(spec) - 1:                  # between FFT bins: a parabola through the peak
            a, b, c = spec[k - 1], spec[k], spec[k + 1]
            den = a - 2 * b + c
            k = k + (0.5 * (a - c) / den if den else 0.0)
        return float(k * fs / len(y))
    if key == "start":
        return float(y[0])
    if key == "end":
        return float(y[-1])
    raise KeyError(key)


def window(t: np.ndarray, start: float | None, end: float | None) -> slice:
    """The samples of t (sorted, s) from start to end (None: the block's start/end)."""
    i0 = 0 if start is None else int(np.searchsorted(t, start, "left"))
    i1 = len(t) if end is None else int(np.searchsorted(t, end, "right"))
    return slice(i0, max(i0, i1))
