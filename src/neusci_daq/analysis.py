"""Finding and sorting action potentials (spikes) in recorded blocks.

1. Detect: the baseline is the trace's median and the noise its median absolute
   deviation / 0.6745 (a noise estimate the spikes themselves barely change). An
   event is where the trace goes past the threshold (a number of noise SDs, or a
   fixed level), marked at its peak; another can't start within the dead time.
2. Cut out each event's waveform, from `pre_ms` before its peak to `post_ms` after.
3. Sort: the waveforms' first principal components are clustered with k-means into
   units, either a set number or, with "auto", however many separate best
   (silhouette score; one unit if nothing separates clearly). Unit 1 is the
   smallest. Spikes from the same cell look alike, so each unit is likely one cell.

Numpy only: the lab computers don't need scikit-learn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

POLARITIES = ("negative", "positive", "either")


def parse_blocks(text: str, n: int) -> list[int]:
    """'all' / '' -> every block; '1-20, 35, 100-' -> those (1-based in the text,
    0-based out). Raises ValueError with a message."""
    text = text.strip().lower()
    if text in ("", "all", "*"):
        return list(range(n))
    out: set[int] = set()
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else n
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ValueError(f"{part!r} isn't a block number or range. Use e.g. all, 1-20, 35, 100-")
        if a < 1 or b < a:
            raise ValueError(f"{part!r} isn't a range of blocks 1 to {n}.")
        out.update(range(a - 1, min(b, n)))
    if not out:
        raise ValueError(f"No blocks in {text!r}: there are {n}.")
    return sorted(out)


def noise_sd(y: np.ndarray) -> float:
    """Robust noise SD: median absolute deviation / 0.6745."""
    y = np.asarray(y, float)
    return float(np.median(np.abs(y - np.median(y))) / 0.6745) if y.size else 0.0


def detect(y: np.ndarray, fs: float, level: float, polarity: str = "negative",
           dead_ms: float = 1.0) -> np.ndarray:
    """Sample indices of events: where y - median(y) goes past `level` (> 0) in the
    `polarity` direction, each marked at its peak within the dead time."""
    y = np.asarray(y, float)
    if not y.size or level <= 0:
        return np.empty(0, int)
    d = y - np.median(y)
    s = -d if polarity == "negative" else d if polarity == "positive" else np.abs(d)
    over = s > level
    starts = np.flatnonzero(np.diff(over.astype(np.int8)) == 1) + 1
    if over[0]:
        starts = np.r_[0, starts]
    dead = max(1, int(round(dead_ms / 1000 * fs)))
    out, last = [], -10 ** 12
    for i in starts:
        if i - last < dead:
            continue
        peak = i + int(np.argmax(s[i:i + dead]))
        out.append(peak)
        last = peak
    return np.asarray(out, int)


def waveforms(y: np.ndarray, idx: np.ndarray, fs: float, pre_ms: float, post_ms: float):
    """(waveforms n×samples with the baseline taken off, the indices kept): events too
    close to the ends are left out."""
    pre, post = int(round(pre_ms / 1000 * fs)), int(round(post_ms / 1000 * fs))
    idx = np.asarray(idx, int)
    keep = idx[(idx - pre >= 0) & (idx + post < len(y))]
    if not len(keep):
        return np.empty((0, pre + post + 1)), keep
    w = np.stack([y[i - pre:i + post + 1] for i in keep]).astype(float)
    return w - np.median(y), keep


def _kmeans(X: np.ndarray, k: int, rng: np.random.Generator, n_init: int = 5, iters: int = 60):
    """k-means with k-means++ starts; the best of n_init runs. Returns labels."""
    best, best_cost = None, np.inf
    for _ in range(n_init):
        centers = [X[rng.integers(len(X))]]
        for _ in range(1, k):
            d2 = np.min(((X[:, None, :] - np.array(centers)[None]) ** 2).sum(2), 1)
            p = d2 / d2.sum() if d2.sum() > 0 else None
            centers.append(X[rng.choice(len(X), p=p)])
        c = np.array(centers)
        for _ in range(iters):
            labels = np.argmin(((X[:, None, :] - c[None]) ** 2).sum(2), 1)
            new = np.array([X[labels == j].mean(0) if np.any(labels == j) else c[j] for j in range(k)])
            if np.allclose(new, c):
                break
            c = new
        cost = ((X - c[labels]) ** 2).sum()
        if cost < best_cost:
            best, best_cost = labels, cost
    return best


def _silhouette(X: np.ndarray, labels: np.ndarray) -> float:
    ks = np.unique(labels)
    if len(ks) < 2:
        return -1.0
    D = np.sqrt(((X[:, None, :] - X[None]) ** 2).sum(2))
    s = np.zeros(len(X))
    for i in range(len(X)):
        own = labels == labels[i]
        a = D[i, own].sum() / max(1, own.sum() - 1)
        b = min(D[i, labels == k].mean() for k in ks if k != labels[i])
        s[i] = (b - a) / max(a, b) if max(a, b) > 0 else 0
    return float(s.mean())


def sort_spikes(w: np.ndarray, n_units: int | None = None, max_units: int = 6,
                seed: int = 0) -> np.ndarray:
    """Unit label (0 = the smallest unit) for each waveform (rows of w). n_units=None
    picks the number by silhouette score."""
    n = len(w)
    if n == 0:
        return np.empty(0, int)
    if n < 3 or n_units == 1:
        return np.zeros(n, int)
    rng = np.random.default_rng(seed)
    centered = w - w.mean(0)
    _, sv, vt = np.linalg.svd(centered, full_matrices=False)
    X = centered @ vt[:3].T                                   # first 3 principal components
    # One scale for all three: scaling each to the same spread would blow the weak,
    # mostly-noise components up and split cells on noise.
    X = X / (X[:, 0].std() or 1.0)
    if n_units is None:
        sub = rng.choice(n, min(n, 600), replace=False)       # silhouette is n²; a sample will do
        scores = {k: _silhouette(X[sub], _kmeans(X[sub], k, rng)) for k in range(2, min(max_units, n - 1) + 1)}
        good = {k: s for k, s in scores.items() if s >= 0.5}  # below this, call it one unit
        best = max(good.values(), default=None)
        # the fewest units that separate (almost) as well as the best
        n_units = min((k for k, s in good.items() if s >= best - 0.02), default=1)
    labels = _kmeans(X, min(n_units, n), rng) if n_units > 1 else np.zeros(n, int)
    # A few odd events (two spikes overlapping, an artifact) can get a unit of their
    # own; fold units smaller than 1% (and at least 3) into the nearest real one.
    small = max(3, int(0.01 * n))
    counts = np.bincount(labels)
    big = np.flatnonzero(counts >= small)
    if 0 < len(big) < len(counts):
        centers = np.array([X[labels == j].mean(0) for j in big])
        for j in np.flatnonzero((counts > 0) & (counts < small)):
            m = labels == j
            labels[m] = big[np.argmin(((X[m][:, None, :] - centers[None]) ** 2).sum(2), 1)]
    labels = np.unique(labels, return_inverse=True)[1]
    # number units by size, smallest first
    size = [np.abs(w[labels == j]).max(1).mean() if np.any(labels == j) else np.inf
            for j in range(labels.max() + 1)]
    order = np.argsort(size)
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    return rank[labels]


@dataclass
class Events:
    """What was found in a set of blocks."""
    block: np.ndarray            # block index (into the session's blocks) of each event
    t: np.ndarray                # seconds from its block's start
    unit: np.ndarray             # 0-based unit
    amplitude: np.ndarray        # peak, from the baseline, in trace units
    waves: np.ndarray            # waveforms (events × samples), baseline taken off
    wave_t_ms: np.ndarray        # time of each waveform sample from the peak, ms
    level: float                 # the threshold used (trace units, from the baseline)
    duration_s: float            # total time searched
    notes: list = field(default_factory=list)

    @property
    def n_units(self) -> int:
        return int(self.unit.max()) + 1 if len(self.unit) else 0

    def summary(self) -> list[dict]:
        """Per unit: count, rate (/s), mean amplitude, and the mean waveform's trough
        to peak time (ms)."""
        out = []
        for u in range(self.n_units):
            m = self.unit == u
            mean = self.waves[m].mean(0)
            i_min, i_max = int(np.argmin(mean)), int(np.argmax(mean))
            a, b = sorted((i_min, i_max))
            width = abs(self.wave_t_ms[b] - self.wave_t_ms[a])
            out.append({"unit": u + 1, "count": int(m.sum()), "rate": m.sum() / max(self.duration_s, 1e-9),
                        "amplitude": float(np.mean(self.amplitude[m])), "width_ms": float(width)})
        return out


def find_events(blocks: list[tuple[int, np.ndarray, float]], *, threshold: float, fixed: bool,
                polarity: str = "negative", dead_ms: float = 1.0, pre_ms: float = 1.0,
                post_ms: float = 2.0, n_units: int | None = None) -> Events:
    """Detect and sort events in `blocks`: (block index, trace, sample rate). With
    fixed=False the threshold is in noise SDs (from the median noise of the blocks);
    with fixed=True it's a level in trace units from the baseline."""
    if not blocks:
        raise ValueError("No blocks to search.")
    fs = blocks[0][2]
    if any(b[2] != fs for b in blocks):
        raise ValueError("These blocks were recorded at different sample rates; search them separately.")
    level = abs(threshold) if fixed else abs(threshold) * float(np.median([noise_sd(y) for _, y, _ in blocks]))
    if level <= 0:
        raise ValueError("The threshold is 0: these blocks look flat.")
    bi, ts, amps, ws = [], [], [], []
    for b, y, _ in blocks:
        idx = detect(y, fs, level, polarity, dead_ms)
        w, keep = waveforms(y, idx, fs, pre_ms, post_ms)
        base = np.median(y)
        bi.append(np.full(len(keep), b))
        ts.append(keep / fs)
        amps.append(y[keep] - base)
        ws.append(w)
    pre = int(round(pre_ms / 1000 * fs))
    waves = np.concatenate(ws) if ws else np.empty((0, 0))
    ev = Events(np.concatenate(bi), np.concatenate(ts), np.empty(0, int), np.concatenate(amps), waves,
                (np.arange(waves.shape[1]) - pre) / fs * 1000 if waves.size else np.empty(0), level,
                sum(len(y) / fs for _, y, _ in blocks))
    ev.unit = sort_spikes(waves, n_units)
    if len(waves) < 3 and len(waves):
        ev.notes.append("Too few events to sort into units.")
    return ev
