"""What a stimulus is like, measured from its trace, and how alike stimuli are.

measure(y, fs): a trial's stimulus (MEASURES): its size, how long it lasts, its pulses...
shape_alike(waves): how alike each pair of stimuli is in shape: each slid along the other
until they line up best (cross-correlation), 1 for the same shape at any size.
alike_map(measures, feats, shape): the stimuli placed on a map by the measures ticked, the
two directions they differ most along (principal components; with Shape, classical
multidimensional scaling, which is the same thing for plain measures).
cut(D, ...): the stimuli joined into groups, closest first (average linkage).

Plain numpy, so it runs (and is tested) without Qt.
"""
from __future__ import annotations

import numpy as np

# key: (name, unit): "u" is the stimulus channel's unit
MEASURES = {
    "amp": ("Amplitude", "u"),
    "p2p": ("Max−min", "u"),
    "var": ("Variance", "u²"),
    "len": ("Window length", "ms"),
    "freq": ("Frequency", "Hz"),
    "width": ("Pulse width", "ms"),
    "count": ("Pulses per train", ""),
    "area": ("Area (charge)", "u·ms"),
}
SHAPE = "shape"
SHAPE_NAME = "Shape (cross-correlation)"
ON = 0.05             # on: further from rest than this share of the biggest swing
MAX_POINTS = 2000     # shapes are compared at most this many samples long


def rest_level(y) -> float | None:
    """Where a stimulus channel rests: the level it sits at most. None if it's flat."""
    y = np.asarray(y, float)
    ok = y[np.isfinite(y)]
    if ok.size < 2 or not ok.max() > ok.min():
        return None
    counts, edges = np.histogram(ok, bins=200)
    k = int(np.argmax(counts))
    return float(np.median(ok[(ok >= edges[k]) & (ok <= edges[k + 1])]))


def _on(y) -> tuple[np.ndarray, int, int] | None:
    """(the stimulus as a swing from rest, its first and last sample on); None if it's flat."""
    y = np.asarray(y, float)
    rest = rest_level(y)
    if rest is None:
        return None
    d = np.nan_to_num(y - rest)
    big = float(np.max(np.abs(d)))
    if not big > 0:
        return None
    on = np.flatnonzero(np.abs(d) > ON * big)
    return d, int(on[0]), int(on[-1])


def window(y, fs: float) -> np.ndarray | None:
    """The stimulus as a swing from rest, first to last sample it's on; None if it's flat."""
    got = _on(y)
    return None if got is None else got[0][got[1]:got[2] + 1]


def on_span(y, fs: float) -> tuple[float, float] | None:
    """When the stimulus is on, (first, just after the last sample on) in s; None if it's flat."""
    got = _on(y)
    return None if got is None else (got[1] / fs, (got[2] + 1) / fs)


def _pushes(w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(starts, ends) of the swings the same way as the first: gaps of a sample or two
    (noise at the edge) don't split one, and one-sample blips don't count."""
    thr = ON * float(np.max(np.abs(w)))
    on = np.sign(w[0]) * w > thr
    starts = np.flatnonzero(on & ~np.r_[False, on[:-1]])
    ends = np.flatnonzero(on & ~np.r_[on[1:], False]) + 1
    if starts.size > 1:
        apart = starts[1:] - ends[:-1] > 2
        starts, ends = starts[np.r_[True, apart]], ends[np.r_[apart, True]]
    long = ends - starts > 1
    if long.any():
        starts, ends = starts[long], ends[long]
    return starts, ends


def measure(y, fs: float) -> dict[str, float] | None:
    """The stimulus's MEASURES; None if the trace is flat. Max−min and Variance count rest
    as part of the stimulus (Variance is about rest, so a square pulse has one)."""
    w = window(y, fs)
    if w is None:
        return None
    starts, ends = _pushes(w)
    n = int(starts.size)
    return {
        "amp": float(np.max(np.abs(w))),
        "p2p": float(max(np.max(w), 0.0) - min(np.min(w), 0.0)),
        "var": float(np.mean(w * w)),
        "len": w.size / fs * 1000,
        "freq": float((n - 1) / ((starts[-1] - starts[0]) / fs)) if n > 1 and starts[-1] > starts[0] else 0.0,
        "width": float((ends[0] - starts[0]) / fs * 1000) if n else 0.0,
        "count": float(n),
        "area": float(np.sum(np.abs(w))) / fs * 1000,
    }


def shape_alike(waves: list) -> np.ndarray:
    """n × n, 0 to 1: how alike each pair of stimuli ((trace, sample rate), or None) is in
    shape, whatever their size: the best match of the two lined up any way, as a share of a
    perfect one (normalized cross-correlation, found with FFTs)."""
    n = len(waves)
    out = np.eye(n)
    cut = [(window(*w), float(w[1])) if w is not None else (None, 1.0) for w in waves]
    have = [(w, fs) for w, fs in cut if w is not None]
    if len(have) < 2:
        return out
    longest = max(w.size / fs for w, fs in have)
    dt = max(1 / max(fs for _, fs in have), longest / MAX_POINTS)     # all at one rate, not too long
    even = []
    for w, fs in cut:
        if w is None:
            even.append(None)
            continue
        t = np.arange(w.size) / fs
        e = np.interp(np.arange(0, t[-1] + dt / 2, dt), t, w)
        norm = float(np.sqrt(np.sum(e * e)))
        even.append((e, norm) if norm > 0 else None)
    size = 1 << int(np.ceil(np.log2(2 * max((x[0].size for x in even if x is not None), default=1) + 1)))
    spectra = [np.fft.rfft(x[0], size) if x is not None else None for x in even]
    for i in range(n):
        for j in range(i + 1, n):
            if even[i] is None or even[j] is None:
                continue
            c = np.fft.irfft(spectra[i] * np.conj(spectra[j]), size)
            out[i, j] = out[j, i] = min(1.0, max(0.0, float(c.max()) / (even[i][1] * even[j][1])))
    return out


def alike_map(meas: list, feats: list[str], shape: np.ndarray | None = None) -> dict | None:
    """The stimuli (their measures, dicts or None) on a map, by the measures `feats` (and
    SHAPE, from shape_alike), each counting the same: {"X": n × 2 places, "share": how much
    of the differences each direction holds, "D": n × n distances, "mostly": the measure
    each direction follows most (it grows right, and up), or SHAPE, or None}. None with
    fewer than two stimuli, or nothing ticked that tells them apart."""
    n = len(meas)
    if n < 2:
        return None
    d2 = np.zeros((n, n))
    used = 0
    for f in feats:
        if f == SHAPE:
            if shape is None:
                continue
            s = 1 - np.asarray(shape, float)
            mean = s[~np.eye(n, dtype=bool)].mean()
            if mean > 0:                            # scaled to count like one measure
                d2 += 2 * s / mean
                used += 1
            continue
        v = np.array([float(m[f]) if m and f in m else np.nan for m in meas])
        if not np.isfinite(v).any():
            continue
        v = np.where(np.isfinite(v), v, np.nanmean(v))
        sd = float(v.std())
        if sd > 0:
            z = (v - v.mean()) / sd
            d2 += (z[:, None] - z[None, :]) ** 2
            used += 1
    if not used:
        return None
    j = np.eye(n) - 1 / n
    vals, vecs = np.linalg.eigh(-0.5 * j @ d2 @ j)
    order = np.argsort(vals)[::-1]
    vals, vecs = np.clip(vals[order], 0, None), vecs[:, order]
    x = np.zeros((n, 2))
    k = min(2, n)
    x[:, :k] = vecs[:, :k] * np.sqrt(vals[:k])
    total = float(vals.sum())
    share = [float(vals[a] / total) if total > 0 and a < k else 0.0 for a in range(2)]
    mostly = []
    for a in range(2):
        best, r_best = None, 0.0
        for f in feats:
            if f == SHAPE:
                continue
            v = np.array([float(m[f]) if m and f in m else np.nan for m in meas])
            ok = np.isfinite(v)
            if ok.sum() > 2 and v[ok].std() > 0 and x[ok, a].std() > 0:
                r = float(np.corrcoef(v[ok], x[ok, a])[0, 1])
                if abs(r) > abs(r_best):
                    best, r_best = f, r
        if r_best < 0:
            x[:, a] = -x[:, a]                      # that measure grows to the right (and up)
        mostly.append(best if abs(r_best) > 0.5 else (SHAPE if SHAPE in feats and share[a] > 0 else None))
    return {"X": x, "share": share, "D": np.sqrt(np.clip(d2, 0, None)), "mostly": mostly}


def cut(D: np.ndarray, groups: int | None = None, close: float | None = None) -> list[list[int]]:
    """The stimuli joined into groups, the two closest groups first (by their average
    distance), until there are `groups` of them, or until the closest are farther apart than
    `close`. Neither: each stimulus its own group."""
    n = len(D)
    out = [[i] for i in range(n)]
    if groups is None and not close:
        return out
    while len(out) > 1 and (groups is None or len(out) > groups):
        best, pair = np.inf, (0, 1)
        for a in range(len(out)):
            for b in range(a + 1, len(out)):
                d = float(np.mean(D[np.ix_(out[a], out[b])]))
                if d < best:
                    best, pair = d, (a, b)
        if groups is None and best > close:
            break
        a, b = pair
        out[a] = sorted(out[a] + out[b])
        del out[b]
    return out
