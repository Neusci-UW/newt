"""Finding and sorting action potentials (spikes) in recorded blocks.

1. Detect: the baseline is the trace's median and the noise its median absolute
   deviation / 0.6745 (a noise estimate the spikes themselves barely change). An
   event is where the trace goes past the threshold, marked at its peak; another
   can't start within the dead time. The threshold is a number of noise SDs, a
   level ("50 mV"), or bimodal: every peak's size is collected, and since noise
   peaks are many and small and spikes few and big, their sizes form two groups;
   two Gaussians are fitted (on a log scale) and the threshold goes where they
   cross. Optionally an event must also be biphasic: swing the other way by a
   fraction of the threshold within a short window, as a real extracellular spike
   does and many artifacts don't.
2. Cut out each event's waveform, from `pre_ms` before its peak to `post_ms` after.
3. Sort: the waveforms' first principal components are clustered with k-means into
   units, either a set number or, with "auto", however many separate best
   (silhouette score; one unit if nothing separates clearly). Unit 1 is the
   smallest. Spikes from the same cell look alike, so each unit is likely one cell.

Numpy only: the lab computers don't need scikit-learn.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy import optimize, signal

from .units import _same_kind, parse_quantity, unit_factor

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
            raise ValueError(f"{part!r} isn't a trial number or range. Use e.g. all, 1-20, 35, 100-")
        if a < 1 or b < a:
            raise ValueError(f"{part!r} isn't a range of trials 1 to {n}.")
        out.update(range(a - 1, min(b, n)))
    if not out:
        raise ValueError(f"No trials in {text!r}: there are {n}.")
    return sorted(out)


def noise_sd(y: np.ndarray) -> float:
    """Robust noise SD: median absolute deviation / 0.6745."""
    y = np.asarray(y, float)
    return float(np.median(np.abs(y - np.median(y))) / 0.6745) if y.size else 0.0


def parse_threshold(text: str, unit: str) -> tuple[str, float]:
    """A threshold as typed: ("sd", 5.0) for "5", "5 SD" or "5 σ"; ("level", value in
    the trace's `unit`) for "50 mV" or "0.05 V" (any prefix of the trace's unit).
    Raises ValueError with a message."""
    t = text.strip()
    m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+))\s*(sds?|σ|x\s*sd|× ?sd)?", t, re.IGNORECASE)
    if m:
        return "sd", abs(float(m.group(1)))
    try:
        v, _ = parse_quantity(t, _same_kind(unit))
    except ValueError:
        raise ValueError(f"Threshold {t!r}: type a number of noise SDs (e.g. 5 SD) or a level "
                         f"in {unit} with any prefix (e.g. 50 m{unit})." if unit else
                         f"Threshold {t!r}: type a number of noise SDs (e.g. 5 SD) or a level.") from None
    return "level", abs(v)


def _signal(y: np.ndarray, polarity: str) -> np.ndarray:
    """The trace from its median, turned so that events go up."""
    d = np.asarray(y, float) - np.median(y)
    return -d if polarity == "negative" else d if polarity == "positive" else np.abs(d)


def bimodal_level(traces: list[np.ndarray], fs: float, polarity: str = "negative",
                  dead_ms: float = 1.0) -> tuple[float | None, str]:
    """(threshold, note) from the sizes of every peak in `traces`: two Gaussians
    fitted to log(peak size) by EM; the threshold is where the bigger one (the
    spikes) becomes the more likely. None if the sizes don't form two groups."""
    from scipy.signal import find_peaks
    dist = max(1, int(round(dead_ms / 1000 * fs)))
    sizes = []
    for y in traces:
        s = _signal(y, polarity)
        idx, _ = find_peaks(s, height=noise_sd(y), distance=dist)   # noise peaks too: that's the point
        sizes.append(s[idx])
    a = np.concatenate(sizes) if sizes else np.empty(0)
    a = a[a > 0]
    if len(a) < 30:
        return None, "Too few peaks to see two groups"
    x = np.log(a)
    mu = np.array([np.percentile(x, 30), np.percentile(x, 99.5)])  # spikes are the rare, big ones
    sd = np.array([x.std() / 2, x.std() / 2]) + 1e-6
    w = np.array([0.95, 0.05])
    for _ in range(300):
        p = w / (sd * np.sqrt(2 * np.pi)) * np.exp(-0.5 * ((x[:, None] - mu) / sd) ** 2)
        r = p / np.maximum(p.sum(1, keepdims=True), 1e-300)
        n = r.sum(0) + 1e-12
        new_mu = (r * x[:, None]).sum(0) / n
        sd = np.sqrt((r * (x[:, None] - new_mu) ** 2).sum(0) / n) + 1e-3
        w = n / n.sum()
        if np.allclose(new_mu, mu, atol=1e-6):
            mu = new_mu
            break
        mu = new_mu
    lo, hi = np.argsort(mu)
    separation = np.sqrt(2) * abs(mu[hi] - mu[lo]) / np.sqrt(sd[lo] ** 2 + sd[hi] ** 2)   # Ashman's D
    n_big = int(round(w[hi] * len(x)))
    if separation < 2 or n_big < 5:
        return None, f"The peak sizes don't form two clear groups (separation {separation:.1f}, needs 2)"
    grid = np.linspace(mu[lo], mu[hi], 2000)
    dens = [w[k] / sd[k] * np.exp(-0.5 * ((grid - mu[k]) / sd[k]) ** 2) for k in (lo, hi)]
    cross = grid[np.argmax(dens[1] >= dens[0])]
    return float(np.exp(cross)), (f"Bimodal: {n_big} big peaks apart from {len(x) - n_big} small ones "
                                  f"(separation {separation:.1f})")


def detect(y: np.ndarray, fs: float, level: float, polarity: str = "negative",
           dead_ms: float = 1.0, biphasic: float | None = None, biphasic_ms: float = 1.0) -> np.ndarray:
    """Sample indices of events: where y - median(y) goes past `level` (> 0) in the
    `polarity` direction, each marked at its peak within the dead time. With
    `biphasic` (a fraction), an event must also go the other way by at least
    biphasic × level, and by 3 noise SDs (or noise alone would pass), within
    biphasic_ms of its peak."""
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
    other_min = max(biphasic * level, 3 * noise_sd(y)) if biphasic else 0.0
    out, last = [], -10 ** 12
    for i in starts:
        if i - last < dead:
            continue
        peak = i + int(np.argmax(s[i:i + dead]))
        last = peak
        if biphasic:
            w = max(1, int(round(biphasic_ms / 1000 * fs)))
            seg = d[max(0, peak - w):peak + w + 1]
            other = seg.max() if d[peak] < 0 else -seg.min()      # the opposite phase's size
            if other < other_min:
                continue
        out.append(peak)
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


def principal_components(w: np.ndarray, n: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Waveforms (rows of w) as points on their first n principal components, and the
    share of the differences between them that each component holds."""
    centered = w - w.mean(0)
    _, sv, vt = np.linalg.svd(centered, full_matrices=False)
    power = sv ** 2
    return centered @ vt[:n].T, (power / power.sum() if power.sum() else power)[:n]


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
    X, _ = principal_components(w)
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


def find_events(blocks: list[tuple[int, np.ndarray, float]], *, threshold: float = 5.0, fixed: bool = False,
                bimodal: bool = False, polarity: str = "negative", dead_ms: float = 1.0,
                biphasic: float | None = None, biphasic_ms: float = 1.0, pre_ms: float = 1.0,
                post_ms: float = 2.0, n_units: int | None = None, memo: dict | None = None,
                keys: list | None = None, progress=None) -> Events:
    """Detect and sort events in `blocks`: (block index, trace, sample rate). The
    threshold is in noise SDs (the median noise of the blocks), or with fixed=True a
    level in trace units from the baseline, or with bimodal=True found from the peak
    sizes (falling back to `threshold` SDs, with a note, if they don't form two groups).

    With `memo` (a dict kept between searches) and `keys` (one per block, naming what its
    trace is), each block's noise and the events found in it are kept, so searching again
    with another block added only looks through that one; sorting into units is always done
    over them all. progress(done, total) is called after each block."""
    if not blocks:
        raise ValueError("No trials to search.")
    fs = blocks[0][2]
    if any(b[2] != fs for b in blocks):
        raise ValueError("These trials were recorded at different sample rates; search them separately.")
    notes = []
    level = None
    if bimodal:
        level, note = bimodal_level([y for _, y, _ in blocks], fs, polarity, dead_ms)
        notes.append(note + ("" if level is not None else f"; used {abs(threshold):g} noise SDs instead") + ".")
    memo = {} if memo is None else memo
    keys = keys if keys is not None else [object() for _ in blocks]      # nothing to remember
    if level is None:
        noise = [memo.setdefault(("noise", k), noise_sd(y)) if not fixed else 0.0 for k, (_, y, _) in zip(keys, blocks)]
        level = abs(threshold) if fixed else abs(threshold) * float(np.median(noise))
    if level <= 0:
        raise ValueError("The threshold is 0: these trials look flat.")
    bi, ts, amps, ws = [], [], [], []
    how = (level, polarity, dead_ms, biphasic, biphasic_ms, pre_ms, post_ms)
    for n, ((b, y, _), k) in enumerate(zip(blocks, keys)):
        found = memo.get(("events", k, how))
        if found is None:
            idx = detect(y, fs, level, polarity, dead_ms, biphasic, biphasic_ms)
            w, keep = waveforms(y, idx, fs, pre_ms, post_ms)
            found = memo[("events", k, how)] = (keep / fs, y[keep] - np.median(y), w)
        t, a, w = found
        bi.append(np.full(len(t), b))
        ts.append(t)
        amps.append(a)
        ws.append(w)
        if progress is not None:
            progress(n + 1, len(blocks))
    pre = int(round(pre_ms / 1000 * fs))
    waves = np.concatenate(ws) if ws else np.empty((0, 0))
    ev = Events(np.concatenate(bi), np.concatenate(ts), np.empty(0, int), np.concatenate(amps), waves,
                (np.arange(waves.shape[1]) - pre) / fs * 1000 if waves.size else np.empty(0), level,
                sum(len(y) / fs for _, y, _ in blocks), notes)
    ev.unit = sort_spikes(waves, n_units)
    if len(waves) < 3 and len(waves):
        ev.notes.append("Too few events to sort into units.")
    return ev


def match_units(before: Events, after: Events) -> np.ndarray:
    """after's unit numbers, changed so each unit that looks like one of before's (by its mean
    waveform) has that one's number: searching more trials sorts the units again, and the same
    unit should keep its number, and so its color. New units take the numbers left."""
    if not len(before.unit) or not len(after.unit) or before.waves.shape[1:] != after.waves.shape[1:]:
        return after.unit
    old = {u: before.waves[before.unit == u].mean(axis=0) for u in np.unique(before.unit) if u >= 0}
    new = {u: after.waves[after.unit == u].mean(axis=0) for u in np.unique(after.unit) if u >= 0}
    pairs = sorted((float(np.linalg.norm(a - b)), n, o) for n, a in new.items() for o, b in old.items())
    to, taken = {}, set()
    for _, n, o in pairs:                          # closest first
        if n not in to and o not in taken:
            to[n] = o
            taken.add(o)
    free = (u for u in range(len(old) + len(new)) if u not in taken)
    for n in sorted(new):
        if n not in to:
            to[n] = next(free)
    out = np.array([to.get(u, u) for u in after.unit], dtype=after.unit.dtype)
    used = sorted(set(out.tolist()) - {-1})
    if used != list(range(len(used))):             # a unit went: close the gap, keeping their order
        out = np.array([used.index(u) if u >= 0 else u for u in out], dtype=after.unit.dtype)
    return out


# -- comparing signals ---------------------------------------------------------
def smooth(y: np.ndarray, fs: float, ms: float) -> np.ndarray:
    """Moving average over `ms` (0 = none)."""
    n = int(round(ms / 1000 * fs))
    if n <= 1:
        return np.asarray(y, float)
    return np.convolve(np.asarray(y, float), np.ones(n) / n, mode="same")


def derivative(y: np.ndarray, fs: float, order: int = 1, smooth_ms: float = 0.0) -> np.ndarray:
    """dV/dt (order 1) or d²V/dt² (order 2), per second. Differentiating amplifies
    noise, so the trace can be smoothed first (and again between the two)."""
    d = smooth(y, fs, smooth_ms)
    for _ in range(order):
        d = smooth(np.gradient(d, 1.0 / fs), fs, smooth_ms)
    return d


def rate_trace(times_s: np.ndarray, duration_s: float, fs: float, sigma_ms: float = 20.0) -> np.ndarray:
    """Spikes/s through a block at `fs`: each spike a Gaussian of SD sigma_ms with
    area 1, summed. Smooth enough to see slow changes, still shows bursts."""
    n = max(1, int(round(duration_s * fs)))
    train = np.zeros(n)
    idx = np.clip(np.round(np.asarray(times_s) * fs).astype(int), 0, n - 1)
    np.add.at(train, idx, fs)                          # each spike: area 1 at this rate
    s = max(1e-6, sigma_ms / 1000 * fs)
    k = np.exp(-0.5 * (np.arange(-int(4 * s), int(4 * s) + 1) / s) ** 2)
    return np.convolve(train, k / k.sum(), mode="same")


def block_rate(events: "Events", n_blocks: int, end_s: float, bin_s: float):
    """Spikes/s during a block, from 0 to end_s in bins of bin_s (the last may be shorter),
    averaged over n_blocks blocks. Returns (bin edges, {unit or None for all: rate})."""
    if not len(events.t) or n_blocks < 1 or end_s <= 0:
        return np.empty(0), {}
    edges = np.append(np.arange(0, end_s, bin_s), end_s)
    per_bin = np.diff(edges) * n_blocks
    out = {None: np.histogram(events.t, edges)[0] / per_bin}
    for u in range(events.n_units):
        out[u] = np.histogram(events.t[events.unit == u], edges)[0] / per_bin
    return edges, out


def stimulus_times(y: np.ndarray, fs: float) -> np.ndarray:
    """Onsets (s) of the pulses on a stimulus channel: where it rises past half its
    largest value, at most one per millisecond. None if it's only noise."""
    d = np.abs(np.asarray(y, float) - np.median(y))
    if not d.size or d.max() < 10 * max(noise_sd(y), 1e-12):
        return np.empty(0)
    on = d > 0.5 * d.max()
    rises = np.flatnonzero(np.diff(np.r_[0, on.astype(np.int8)]) == 1)
    out, last = [], -np.inf
    for i in rises:
        if i - last >= fs / 1000:
            out.append(i / fs)
        last = i
    return np.asarray(out)


def triggered_rates(event_times: dict[int, np.ndarray], triggers: dict[int, np.ndarray],
                    before_ms: float, after_ms: float, bin_ms: float):
    """Spikes/s in bins around each trigger, one row per trigger (trial): from before_ms
    before it to after_ms after. `event_times` and `triggers` are seconds within each
    block. Returns (bin centeres ms, rates: triggers × bins)."""
    edges = np.arange(-before_ms, after_ms + bin_ms / 2, bin_ms)
    rows = []
    for b, trig in triggers.items():
        ev = event_times.get(b, np.empty(0)) * 1000
        for t in np.asarray(trig) * 1000:
            rows.append(np.histogram(ev - t, edges)[0] / (bin_ms / 1000))
    rates = np.array(rows) if rows else np.empty((0, len(edges) - 1))
    return edges[:-1] + bin_ms / 2, rates


def triggered_rate(event_times: dict[int, np.ndarray], triggers: dict[int, np.ndarray],
                   before_ms: float, after_ms: float, bin_ms: float):
    """Stimulus-triggered spike rate (a PSTH): the trials' rates (triggered_rates)
    averaged. Returns (bin centeres ms, rate, n triggers)."""
    centeres, rates = triggered_rates(event_times, triggers, before_ms, after_ms, bin_ms)
    return centeres, rates.mean(0) if len(rates) else np.zeros(len(centeres)), len(rates)


def mean_spread(rows: np.ndarray):
    """Across trials (the rows; NaN where a trial has no value): (mean, SD, SEM, n) at
    each point. SD is the sample SD (n − 1); SEM = SD / √n; both are NaN where n < 2."""
    rows = np.atleast_2d(np.asarray(rows, float))
    n = np.sum(np.isfinite(rows), 0)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(rows, 0)
        sd = np.where(n >= 2, np.nanstd(rows, 0, ddof=1), np.nan)
    sem = np.where(n >= 2, sd / np.sqrt(np.maximum(n, 1)), np.nan)
    return mean, sd, sem, n


def triggered_average(traces: dict[int, np.ndarray], fs: float, times: dict[int, np.ndarray],
                      before_ms: float, after_ms: float):
    """Average of `traces` around each time in `times` (seconds within each block),
    e.g. the stimulus around each spike (spike-triggered average). Times too near a
    block's ends are skipped. Returns (t ms, mean, SD, n)."""
    pre, post = int(round(before_ms / 1000 * fs)), int(round(after_ms / 1000 * fs))
    segs = []
    for b, ts in times.items():
        y = traces.get(b)
        if y is None:
            continue
        for i in np.round(np.asarray(ts) * fs).astype(int):
            if i - pre >= 0 and i + post < len(y):
                segs.append(y[i - pre:i + post + 1])
    t = (np.arange(pre + post + 1) - pre) / fs * 1000
    if not segs:
        return t, np.full(len(t), np.nan), np.full(len(t), np.nan), 0
    s = np.asarray(segs, float)
    return t, s.mean(0), s.std(0), len(s)


# -- grouping blocks by their stimulus ------------------------------------------
_TRIAL_NOTE = re.compile(r"^(?:Trial|Stimulus) \d+(?:/\d+)?:\s*")


def stimulus_notes(comments: list[tuple[float, str]]) -> tuple[str, ...]:
    """The stimuli a block's notes say were sent (the app notes each one while
    recording): "Stim: ..." and "Stimulus n/m: ..." (older sessions: "Trial n/m: ...") without the number. Distinct,
    in order; empty if none."""
    out = []
    for _, text in comments:
        if text.startswith("Stim: "):
            out.append(text[6:].strip())
        elif _TRIAL_NOTE.match(text):
            out.append(_TRIAL_NOTE.sub("", text).strip())
    return tuple(dict.fromkeys(out))


def _sig(x: float, n: int = 2) -> float:
    return float(f"{x:.{n}g}")


TRAIN_GAP_MS = 100.0               # pulses closer than this are one stimulus (a train)
CYCLE_POINTS = 360                 # each stimulus cycle, stretched to one point per degree
MIN_MATCH = 0.7                    # how alike a repeat and the template must be (correlation)
SINE_MATCH = 0.25                  # how far from a sine a stretch may be and still be it (RMS ÷ amplitude)
SEARCH_S = 1.0                     # a stimulus sent is within this long of its note
FITS = ("Off", "Known waveform", "Piecewise linear", "Smooth", "Sine", "Repeat average", "Harmonics")


def pulse_signature(y: np.ndarray, fs: float) -> tuple | None:
    """One stimulus as a stimulus channel shows it: (pulses per train, size, width ms,
    interval ms). Pulses under TRAIN_GAP_MS apart make one train; repeats further
    apart (however many fit in the block) don't change it. None without pulses.
    Measurements jitter by a sample, so compare them with same_stimulus."""
    y = np.asarray(y, float)
    if not y.size:                    # e.g. a block that's still being recorded
        return None
    y = np.nan_to_num(y)              # missing samples (a channel off part of the time) as 0
    d = y - np.median(y)
    a = np.abs(d)
    if a.max() < 10 * max(noise_sd(y), 1e-12):
        return None
    on = a > 0.5 * a.max()
    edges = np.flatnonzero(np.diff(np.r_[0, on.astype(np.int8), 0]))
    starts, ends = edges[::2], edges[1::2]
    if not len(starts):
        return None
    peaks = [d[s:e][np.argmax(a[s:e])] for s, e in zip(starts, ends)]
    widths = (ends - starts) / fs * 1000
    gaps = np.diff(starts) / fs * 1000
    inside = gaps[gaps < TRAIN_GAP_MS]
    trains = 1 + int(np.sum(gaps >= TRAIN_GAP_MS))
    per_train = int(round(len(starts) / trains))
    gap = float(np.median(inside)) if len(inside) else 0.0
    return per_train, float(np.median(peaks)), float(np.median(widths)), gap


def wave_signature(y: np.ndarray, fs: float) -> tuple | None:
    """A stimulus that isn't brief pulses (a sine, a square wave, a long step...):
    ("wave", main frequency Hz, amplitude, offset, shape). Amplitude is half the
    peak-to-peak (from the 2nd to the 98th percentile, so noise and stray samples
    don't count); shape is RMS / amplitude (sine 0.71, square 1, triangle 0.58).
    None if it's perfectly flat."""
    y = np.nan_to_num(np.asarray(y, float))
    if y.size < 4:
        return None
    lo, hi = np.percentile(y, [2, 98])
    amp = (hi - lo) / 2
    if amp <= 0:
        return None
    mid = (hi + lo) / 2
    d = y - mid
    spec = np.abs(np.fft.rfft(d - d.mean()))
    k = 1 + int(np.argmax(spec[1:])) if len(spec) > 2 else 1
    if 1 <= k < len(spec) - 1:                      # between FFT bins: a parabola through the peak
        a, b, c = spec[k - 1], spec[k], spec[k + 1]
        k = k + 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else k
    freq = k * fs / len(y)
    return "wave", float(freq), float(amp), float(mid), float(np.sqrt(np.mean(d ** 2)) / amp)


def stimulus_signature(y: np.ndarray, fs: float) -> tuple | None:
    """What a channel you've said records the stimulus shows in a block: brief pulses
    on a flat baseline as ("pulse", pulses per train, size, width ms, interval ms),
    anything else as wave_signature. None when there's no samples or it's flat."""
    y = np.nan_to_num(np.asarray(y, float))
    if not y.size:
        return None
    d = y - np.median(y)
    a = np.abs(d)
    pulsy = a.max() >= 10 * max(noise_sd(y), 1e-12) and np.mean(a > 0.5 * a.max()) < 0.3
    if pulsy:
        p = pulse_signature(y, fs)
        if p is not None:
            return ("pulse",) + tuple(p)
    return wave_signature(y, fs)


def same_stimulus(a: tuple, b: tuple, fs: float) -> bool:
    """Whether two signatures are the same stimulus. Pulses: the same pulses per train,
    size within 5%, width and interval within 10% or 2 samples. Waves: frequency
    within 3% (or 0.6 Hz), amplitude within 5%, offset within 5% of the amplitude,
    and the same shape. Signatures without a kind are pulse_signature's."""
    if isinstance(a[0], str) or isinstance(b[0], str):
        if a[0] != b[0]:
            return False
        if a[0] == "pulse":
            return same_stimulus(a[1:], b[1:], fs)
        _, fa, aa, oa, sa = a
        _, fb, ab, ob, sb = b
        near = lambda x, y, rel, floor: abs(x - y) <= max(rel * max(abs(x), abs(y)), floor)
        return (near(fa, fb, 0.03, 0.6) and near(aa, ab, 0.05, 0.0)
                and abs(oa - ob) <= 0.05 * max(aa, ab) and abs(sa - sb) <= 0.08)
    if a[0] != b[0]:
        return False
    sample_ms = 2000.0 / fs
    near = lambda x, y, rel, floor: abs(x - y) <= max(rel * max(abs(x), abs(y)), floor)
    return (near(a[1], b[1], 0.05, 0.0) and near(a[2], b[2], 0.10, sample_ms)
            and near(a[3], b[3], 0.10, sample_ms))


def cluster_signatures(sigs: dict[int, tuple | None], fs: float) -> dict[int, tuple | None]:
    """Each block's signature replaced by the first one it matches (same_stimulus), so
    blocks with the same stimulus share one key."""
    reps: list[tuple] = []
    out = {}
    for b, s in sigs.items():
        if s is None:
            out[b] = None
            continue
        rep = next((r for r in reps if same_stimulus(r, s, fs)), None)
        if rep is None:
            reps.append(s)
            rep = s
        out[b] = rep
    return out


def describe_signature(sig: tuple, unit: str = "V") -> str:
    from .units import best_prefix
    if sig[0] == "pulse":
        sig = sig[1:]
    elif sig[0] == "wave":
        _, freq, amp, offset, shape = sig
        kind = ("Sine" if abs(shape - 0.707) < 0.06 else "Square wave" if shape > 0.9
                else "Triangle wave" if abs(shape - 0.577) < 0.05 else "Wave")
        v, u = best_prefix(_sig(amp), unit)
        out = f"{kind} {_sig(freq):g} Hz, ±{v:.2g} {u}"
        if abs(offset) > 0.1 * amp:
            o, ou = best_prefix(_sig(offset), unit)
            out += f", offset {o:.2g} {ou}"
        return out
    # to 0.1 ms: finer than that is a sample's jitter, not a different stimulus
    n, size, width, gap = sig[0], _sig(sig[1]), round(sig[2], 1), round(sig[3], 1)
    v, u = best_prefix(size, unit)
    if n == 1:
        return f"Pulse of {v:.2g} {u}, {width:g} ms"
    return f"Train of {n} pulses of {v:.2g} {u}, {width:g} ms, every {gap:g} ms"


def unique_names(labels: dict[object, str]) -> dict[object, str]:
    """Different stimuli that round to the same name get (2), (3)... after it."""
    out, seen = {}, {}
    for k, name in labels.items():
        seen[name] = seen.get(name, 0) + 1
        out[k] = name if seen[name] == 1 else f"{name} ({seen[name]})"
    return out


def group_blocks(keys: dict[int, object], labels: dict[object, str]) -> list[tuple[str, list[int]]]:
    """Blocks with the same key together: [(label, blocks)], in the order each key
    first appears, with blocks that have no stimulus (key None) last."""
    groups: dict[object, list[int]] = {}
    for b, k in keys.items():
        groups.setdefault(k, []).append(b)
    order = [k for k in groups if k is not None] + ([None] if None in groups else [])
    return [(labels.get(k, "No stimulus found") if k is not None else "No stimulus found", groups[k])
            for k in order]


def train_starts(times_s: np.ndarray, gap_ms: float = TRAIN_GAP_MS) -> np.ndarray:
    """The first pulse of each train: onsets closer than gap_ms to the one before drop out."""
    t = np.asarray(times_s, float)
    if not len(t):
        return t
    keep = np.r_[True, np.diff(t) * 1000 >= gap_ms]
    return t[keep]


def cycle_bounds(y: np.ndarray) -> np.ndarray:
    """Where each full cycle of a repeating stimulus starts and ends: (n, 2), in samples
    (a fraction when it falls between two). A cycle starts where the channel rises through
    its middle level, and it has to swing well past the middle both ways, so noise on the
    way through doesn't start another. Cycles under half or over twice the usual length
    (the gap between two bursts) are left out, and so are the unfinished ones at the ends."""
    none = np.empty((0, 2))
    y = np.asarray(y, float)
    if y.size < 4 or not np.isfinite(y).any():
        return none
    lo, hi = np.nanpercentile(y, [2, 98])
    mid, swing = (hi + lo) / 2, (hi - lo) / 4
    if swing <= 0:
        return none
    side = np.where(y > mid + swing, 1, np.where(y < mid - swing, -1, 0))
    at = np.flatnonzero(side)
    if not at.size:
        return none
    s = side[at]
    up = at[1:][(s[:-1] == -1) & (s[1:] == 1)]
    if s[0] == 1 and y[0] <= mid:                   # it starts at the middle, on its way up
        up = np.r_[at[0], up]
    if len(up) < 2:
        return none
    below = np.flatnonzero(y <= mid)
    k = below[np.searchsorted(below, up) - 1]       # the last sample at or under the middle
    starts = k + (mid - y[k]) / (y[k + 1] - y[k])
    length = np.diff(starts)
    usual = np.median(length)
    # noise crosses too, but a stimulus's cycles are fast enough to see and each like the last
    if usual < 8 or np.median(np.abs(np.diff(length))) > 0.2 * usual:
        return none
    keep = (length > usual / 2) & (length < usual * 2)
    return np.column_stack((starts[:-1], starts[1:]))[keep]


@dataclass(frozen=True, eq=False)
class Sent:
    """A stimulus sent during a block, from the settings saved with it."""
    t: float                       # about when (s into the block): the note's time
    wave: np.ndarray               # what was sent (V at `rate`)
    rate: float
    period_s: float = 0.0          # how long one repeat is, if it repeats
    first_s: float = 0.0           # where the first repeat starts in `wave`
    repeats: int = 0


@dataclass
class Repeats:
    """The repeats of a stimulus in one block."""
    bounds: np.ndarray           # (n, 2): where each starts and ends, in samples
    method: str = ""             # how they were found: "shape", "crossing", "hand", or "" for none
    left_out: int = 0            # candidates too unlike the template to use


def repeat_length(y: np.ndarray) -> int | None:
    """How often a trace repeats itself, in samples, from its autocorrelation: the shortest
    lag with a peak nearly as high as the best. None if nothing repeats."""
    d = np.asarray(y, float)
    d = np.nan_to_num(d - np.nanmean(d))
    n = len(d)
    if n < 16 or not d.any():
        return None
    f = np.fft.rfft(d, 1 << int(np.ceil(np.log2(2 * n))))
    ac = np.fft.irfft(np.abs(f) ** 2)[:n // 2]
    ac = ac / ac[0] * n / (n - np.arange(len(ac)))  # later lags have fewer samples to compare
    past = np.flatnonzero(ac < 0.2)
    if not len(past):
        return None
    peaks = signal.find_peaks(ac[past[0]:], height=0.4, prominence=0.1)[0] + past[0]
    if not len(peaks):
        return None
    k = int(peaks[ac[peaks] >= 0.9 * ac[peaks].max()][0])
    return k if k >= 8 else None


def first_rise(y: np.ndarray) -> int | None:
    """Where a stimulus first rises through the middle of its range, well clear of any
    noise at rest: the first repeat starts here."""
    y = np.asarray(y, float)
    lo, hi = np.nanmin(y), np.nanmax(y)
    mid = (lo + hi) / 2
    if hi <= lo:
        return None
    start = 0
    if y[0] > mid:                                  # it starts high: wait for it to come down
        low = np.flatnonzero(y < mid)
        if not len(low):
            return None
        start = int(low[0])
    high = np.flatnonzero(y[start:] > mid + (hi - lo) / 4)
    if not len(high):
        return None
    at = start + int(high[0])
    below = np.flatnonzero(y[:at] <= mid)
    return int(below[-1]) if len(below) else 0


def match_repeats(y: np.ndarray, start: int, n: int) -> tuple[np.ndarray, int]:
    """Slide the n samples at `start` along the trace as a template. Returns (bounds of each
    good match, how many candidates were too unlike it to use). Every repeat is n long, so
    none is stretched."""
    y = np.nan_to_num(np.asarray(y, float), nan=float(np.nanmedian(y)))
    t = y[start:start + n]
    if len(t) < n or n < 8:
        return np.empty((0, 2)), 0
    t = t - t.mean()
    energy = float(np.sum(t ** 2))
    if energy <= 0:
        return np.empty((0, 2)), 0
    s1 = np.convolve(y, np.ones(n), "valid") if n < 64 else signal.fftconvolve(y, np.ones(n), "valid")
    s2 = signal.fftconvolve(y ** 2, np.ones(n), "valid")
    var = s2 - s1 ** 2 / n
    # a floor on the window's size, or a flat stretch would match anything
    score = signal.fftconvolve(y, t[::-1], "valid") / np.sqrt(np.maximum(var, 0.05 * energy) * energy)
    peaks = signal.find_peaks(score, height=0.3, distance=max(1, int(0.75 * n)))[0]
    good = peaks[score[peaks] >= MIN_MATCH]
    at = good.astype(float)
    inside = (good > 0) & (good < len(score) - 1)   # between samples: a parabola through the peak
    a, b, c = score[good[inside] - 1], score[good[inside]], score[good[inside] + 1]
    bend = a - 2 * b + c
    at[inside] += np.where(bend != 0, 0.5 * (a - c) / np.where(bend != 0, bend, 1), 0)
    at = at[at + n <= len(y) - 1]
    return np.column_stack((at, at + n)), int(len(peaks) - len(good))


def line_up(y: np.ndarray, fs: float, sent: Sent) -> tuple[int, float, float] | None:
    """Where a stimulus that was sent is on the trace that recorded it: (first sample, size,
    offset), with only its timing and size fitted. None if it isn't there."""
    w = np.interp(np.arange(int(len(sent.wave) / sent.rate * fs)) / fs, np.arange(len(sent.wave)) / sent.rate, sent.wave)
    if len(w) < 4 or len(y) < 4 or np.ptp(w) == 0:
        return None
    scores = signal.correlate(y - y.mean(), w - w.mean(), mode="full", method="fft")
    lags = np.arange(-len(w) + 1, len(y))
    near = np.abs(lags / fs - sent.t) <= SEARCH_S
    if not near.any():
        return None
    at = int(lags[near][np.argmax(scores[near])])
    i, j = max(0, at), min(len(y), at + len(w))
    if j - i < 4:
        return None
    part = w[i - at:j - at]
    if np.ptp(part) == 0 or np.ptp(y[i:j]) == 0 or np.corrcoef(part, y[i:j])[0, 1] < MIN_MATCH:
        return None
    size, offset = np.polyfit(part, y[i:j], 1)
    return at, float(size), float(offset)


def _known(y: np.ndarray, fs: float, sent: list[Sent]) -> np.ndarray:
    """The waveforms that were sent, each lined up with the trace, with their rests left as
    they are."""
    out = y.copy()
    for s in sent:
        found = line_up(y, fs, s)
        if found is not None:
            at, size, offset = found
            w = np.interp(np.arange(int(len(s.wave) / s.rate * fs)) / fs, np.arange(len(s.wave)) / s.rate, s.wave)
            i, j = max(0, at), min(len(y), at + len(w))
            out[i:j] = size * w[i - at:j - at] + offset
    return out


def known_repeats(y: np.ndarray, fs: float, sent: list[Sent]) -> np.ndarray:
    """The repeats of stimuli sent as a repeating shape: exact, from their settings."""
    out = []
    for s in sent:
        found = line_up(y, fs, s) if s.period_s > 0 else None
        if found is not None:
            n = s.period_s * fs
            first = found[0] + s.first_s * fs
            out += [(a, a + n) for a in first + n * np.arange(s.repeats) if a >= 0 and a + n <= len(y)]
    return np.array(out, float).reshape(-1, 2)


def find_repeats(y: np.ndarray, fs: float, length_s: float = 0.0, start_s: float | None = None,
                 sent: tuple[Sent, ...] = ()) -> Repeats:
    """The repeats of a stimulus, by the most reliable way that works. With a length, you
    said how long one is (and maybe where): that's the template. Otherwise what was sent is
    lined up with the trace, if the block saved it and it repeats. Otherwise its shape is
    matched, which suits any shape however many times it rises, then the rises through its
    middle level are used, which also follow a changing frequency. No repeats: none found."""
    y = np.asarray(y, float)
    none = np.empty((0, 2))
    if y.size < 16 or not np.isfinite(y).any():
        return Repeats(none)
    if length_s > 0:
        n = int(round(length_s * fs))
        start = int(round(start_s * fs)) if start_s is not None else first_rise(y)
        bounds, left = match_repeats(y, start, n) if start is not None else (none, 0)
        return Repeats(bounds, "hand" if len(bounds) else "", left)
    bounds = known_repeats(y, fs, list(sent))
    if len(bounds) >= 2:
        return Repeats(bounds, "known")
    n, start = repeat_length(y), first_rise(y)
    if n is not None and start is not None:
        bounds, left = match_repeats(y, start, n)
        if len(bounds) >= 2:
            return Repeats(bounds, "shape", left)
    bounds = cycle_bounds(y)
    return Repeats(bounds, "crossing" if len(bounds) else "")


def _piecewise(y: np.ndarray) -> np.ndarray:
    """Straight lines through a trace: split wherever it strays further from a line than
    its noise explains, then fit each piece by least squares. Pieces needn't join, so a
    step stays a step."""
    steps = np.abs(np.diff(y))
    noise = float(np.median(steps)) / 0.6745 / np.sqrt(2)
    grain = float(np.median(steps[steps > 0])) if (steps > 0).any() else 0.0   # the recording's resolution
    tol = max(5 * noise, grain)
    if tol <= 0:
        return y.copy()
    calm = signal.medfilt(y, 5)                     # a lone noisy sample shouldn't start a piece
    cuts, todo = [0, len(y) - 1], [(0, len(y) - 1)]
    while todo:
        a, b = todo.pop()
        if b - a < 4:
            continue
        line = np.linspace(calm[a], calm[b], b - a + 1)
        k = int(np.argmax(np.abs(calm[a:b + 1] - line)))
        if abs(calm[a + k] - line[k]) > tol and 0 < k < b - a:
            cuts.append(a + k)
            todo += [(a, a + k), (a + k, b)]
    out = y.copy()
    cuts = sorted(cuts)
    for a, b in zip(cuts[:-1], cuts[1:]):
        x = np.arange(a, b + 1 if b == cuts[-1] else b)
        if len(x) >= 2:
            out[x] = np.polyval(np.polyfit(x - a, y[x], 1), x - a)
    return out


def _sine(y: np.ndarray, fs: float) -> np.ndarray:
    """A sine through each unbroken run of full cycles, carried on past either end for as
    long as it still matches, so a block's first and last cycles are fitted too and a pause
    between bursts stays a pause. The rest of the trace stays as it is."""
    bounds = cycle_bounds(y)
    out = y.copy()
    if not len(bounds):
        return out

    def wave(t, amp, f, phase, offset):
        return amp * np.sin(2 * np.pi * f * t + phase) + offset
    runs = np.split(bounds, np.flatnonzero(bounds[1:, 0] > bounds[:-1, 1]) + 1)
    for k, run in enumerate(runs):
        a, b = int(run[0, 0]), int(np.ceil(run[-1, 1]))
        t = np.arange(a, b) / fs
        hz = fs / float(np.median(np.diff(run, axis=1)))
        guess = (np.ptp(y[a:b]) / 2, hz, -2 * np.pi * hz * run[0, 0] / fs, float(np.mean(y[a:b])))
        try:
            best = optimize.curve_fit(wave, t, y[a:b], p0=guess)[0]
        except RuntimeError:
            best = guess
        lo = int(np.ceil(runs[k - 1][-1, 1])) if k else 0
        hi = int(runs[k + 1][0, 0]) if k + 1 < len(runs) else len(y)
        step = max(1, int(round(fs / abs(best[1]) / 4)))      # a quarter of a cycle at a time

        def matches(i: int, j: int) -> bool:
            there = wave(np.arange(i, j) / fs, *best)
            return float(np.sqrt(np.mean((y[i:j] - there) ** 2))) < SINE_MATCH * abs(best[0])
        while a > lo and matches(max(lo, a - step), a):
            a = max(lo, a - step)
        while b < hi and matches(b, min(hi, b + step)):
            b = min(hi, b + step)
        out[a:b] = wave(np.arange(a, b) / fs, *best)
    return out


def _repeat_average(y: np.ndarray, fs: float, harmonics: int | None, length_s: float, start_s: float | None,
                    sent: tuple[Sent, ...] = ()):
    """Every repeat replaced by the average of them all, or with `harmonics`, by that
    average's first few harmonics. The rest of the trace stays as it is."""
    bounds = find_repeats(y, fs, length_s, start_s, sent).bounds
    if len(bounds) < 2:
        return y.copy()
    n = int(round(float(np.median(np.diff(bounds, axis=1)))))
    mean = cycle_rows(y, bounds, n).mean(0)
    if harmonics is not None:
        spectrum = np.fft.rfft(mean)
        spectrum[harmonics + 1:] = 0
        mean = np.fft.irfft(spectrum, n)
    out = y.copy()
    for a, b in bounds:
        k = np.arange(int(np.ceil(a)), min(int(np.ceil(b)), len(y)))
        out[k] = np.interp((k - a) / (b - a) * n, np.arange(n + 1), np.r_[mean, mean[0]])
    return out


def fit_stimulus(y: np.ndarray, fs: float, kind: str, length_s: float = 0.0, start_s: float | None = None,
                 smooth_ms: float = 1.0, harmonics: int = 8, sent: tuple[Sent, ...] = ()) -> np.ndarray:
    """A recorded stimulus without its sampling noise and coarse voltage steps, by the fit
    that suits its shape (one of FITS): what was sent, lined up, if the block saved it;
    straight pieces for pulses, steps, ramps and custom shapes; smoothing that doesn't shift
    timing; a sine; or the average of its repeats."""
    y = np.asarray(y, float)
    y = np.nan_to_num(y, nan=float(np.nanmedian(y))) if np.isfinite(y).any() else y
    if y.size < 16 or kind == "Off":
        return y
    if kind == "Known waveform":
        return _known(y, fs, list(sent))
    if kind == "Piecewise linear":
        return _piecewise(y)
    if kind == "Smooth":
        window = max(5, int(round(smooth_ms / 1000 * fs)) | 1)
        return signal.savgol_filter(y, min(window, (len(y) - 1) | 1), 2)
    if kind == "Sine":
        return _sine(y, fs)
    return _repeat_average(y, fs, harmonics if kind == "Harmonics" else None, length_s, start_s, sent)


def cycle_rows(y: np.ndarray, bounds: np.ndarray, n: int = CYCLE_POINTS) -> np.ndarray:
    """A channel cut into cycles: one row per cycle, stretched to n points from 0° up to
    (but not including) 360°, so cycles of different lengths line up by phase."""
    if not len(bounds):
        return np.empty((0, n))
    where = bounds[:, :1] + (bounds[:, 1:] - bounds[:, :1]) * (np.arange(n) / n)
    return np.interp(where.ravel(), np.arange(len(y)), np.asarray(y, float)).reshape(where.shape)


def spike_phases(times_s: np.ndarray, bounds: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Which cycle each spike fell in, and where in it (degrees). Spikes outside every
    cycle are left out. Returns (cycle numbers from 0, phases)."""
    at = np.asarray(times_s, float) * fs
    if not len(bounds) or not at.size:
        return np.empty(0, int), np.empty(0)
    k = np.searchsorted(bounds[:, 0], at, side="right") - 1
    inside = (k >= 0) & (at < bounds[np.maximum(k, 0), 1])
    k, at = k[inside], at[inside]
    return k, 360 * (at - bounds[k, 0]) / (bounds[k, 1] - bounds[k, 0])


def vector_strength(phases: np.ndarray) -> tuple[float, float]:
    """How tightly spikes lock to one phase: (1 if all at the same phase, near 0 if spread
    evenly round the cycle; the phase they gather at, in degrees)."""
    if not len(phases):
        return 0.0, 0.0
    z = np.mean(np.exp(1j * np.radians(phases)))
    return float(abs(z)), float(np.degrees(np.angle(z)) % 360)


def note_times(comments: list[tuple[float, str]]) -> np.ndarray:
    """When the stimulus notes were made (s): close to, not exactly at, each stimulus."""
    return np.asarray([t for t, text in comments if text.startswith("Stim: ") or _TRIAL_NOTE.match(text)])


def intervals_ms(events: "Events", unit: int | None = None) -> np.ndarray:
    """Inter-spike intervals (ms): between consecutive spikes of the same unit in the
    same block (never across blocks). unit=None: every unit, each on its own."""
    out = []
    units = range(events.n_units) if unit is None else [unit]
    for u in units:
        m = events.unit == u
        for b in np.unique(events.block[m]):
            t = np.sort(events.t[m & (events.block == b)])
            out.append(np.diff(t) * 1000)
    return np.concatenate(out) if out else np.empty(0)
