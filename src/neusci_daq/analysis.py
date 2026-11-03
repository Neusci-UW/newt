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
from dataclasses import dataclass, field

import numpy as np

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


def find_events(blocks: list[tuple[int, np.ndarray, float]], *, threshold: float = 5.0, fixed: bool = False,
                bimodal: bool = False, polarity: str = "negative", dead_ms: float = 1.0,
                biphasic: float | None = None, biphasic_ms: float = 1.0, pre_ms: float = 1.0,
                post_ms: float = 2.0, n_units: int | None = None) -> Events:
    """Detect and sort events in `blocks`: (block index, trace, sample rate). The
    threshold is in noise SDs (the median noise of the blocks), or with fixed=True a
    level in trace units from the baseline, or with bimodal=True found from the peak
    sizes (falling back to `threshold` SDs, with a note, if they don't form two groups)."""
    if not blocks:
        raise ValueError("No blocks to search.")
    fs = blocks[0][2]
    if any(b[2] != fs for b in blocks):
        raise ValueError("These blocks were recorded at different sample rates; search them separately.")
    notes = []
    level = None
    if bimodal:
        level, note = bimodal_level([y for _, y, _ in blocks], fs, polarity, dead_ms)
        notes.append(note + ("" if level is not None else f"; used {abs(threshold):g} noise SDs instead") + ".")
    if level is None:
        level = abs(threshold) if fixed else abs(threshold) * float(np.median([noise_sd(y) for _, y, _ in blocks]))
    if level <= 0:
        raise ValueError("The threshold is 0: these blocks look flat.")
    bi, ts, amps, ws = [], [], [], []
    for b, y, _ in blocks:
        idx = detect(y, fs, level, polarity, dead_ms, biphasic, biphasic_ms)
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
                sum(len(y) / fs for _, y, _ in blocks), notes)
    ev.unit = sort_spikes(waves, n_units)
    if len(waves) < 3 and len(waves):
        ev.notes.append("Too few events to sort into units.")
    return ev


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


def session_rate(events: "Events", starts_s: dict[int, float], bin_s: float, units=None):
    """Spikes/s in bins of bin_s across the whole session (blocks one after another,
    block b starting at starts_s[b]). Returns (bin centres, {unit or None for all: rate})."""
    if not len(events.t) or not starts_s:
        return np.empty(0), {}
    t = np.array([starts_s[b] for b in events.block]) + events.t
    end = max(starts_s.values()) + events.duration_s / max(1, len(starts_s))
    edges = np.arange(0, end + bin_s, bin_s)
    centres = edges[:-1] + bin_s / 2
    out = {None: np.histogram(t, edges)[0] / bin_s}
    for u in (range(events.n_units) if units is None else units):
        out[u] = np.histogram(t[events.unit == u], edges)[0] / bin_s
    return centres, out


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


def triggered_rate(event_times: dict[int, np.ndarray], triggers: dict[int, np.ndarray],
                   before_ms: float, after_ms: float, bin_ms: float):
    """Stimulus-triggered spike rate (a PSTH): spikes/s in bins from before_ms before
    each trigger to after_ms after it, averaged over the triggers. `event_times` and
    `triggers` are seconds within each block. Returns (bin centres ms, rate, n triggers)."""
    edges = np.arange(-before_ms, after_ms + bin_ms / 2, bin_ms)
    counts = np.zeros(len(edges) - 1)
    n = 0
    for b, trig in triggers.items():
        ev = event_times.get(b, np.empty(0)) * 1000
        for t in np.asarray(trig) * 1000:
            counts += np.histogram(ev - t, edges)[0]
            n += 1
    rate = counts / max(n, 1) / (bin_ms / 1000)
    return edges[:-1] + bin_ms / 2, rate, n


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
_TRIAL_NOTE = re.compile(r"^Trial \d+(?:/\d+)?:\s*")


def stimulus_notes(comments: list[tuple[float, str]]) -> tuple[str, ...]:
    """The stimuli a block's notes say were sent (the app notes each one while
    recording): "Stim: ..." and "Trial n/m: ..." without the trial number. Distinct,
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


def pulse_signature(y: np.ndarray, fs: float) -> tuple | None:
    """One stimulus as a stimulus channel shows it: (pulses per train, size, width ms,
    interval ms). Pulses under TRAIN_GAP_MS apart make one train; repeats further
    apart (however many fit in the block) don't change it. None without pulses.
    Measurements jitter by a sample, so compare them with same_stimulus."""
    d = np.asarray(y, float) - np.median(y)
    a = np.abs(d)
    if not a.size or a.max() < 10 * max(noise_sd(y), 1e-12):
        return None
    on = a > 0.5 * a.max()
    edges = np.flatnonzero(np.diff(np.r_[0, on.astype(np.int8), 0]))
    starts, ends = edges[::2], edges[1::2]
    peaks = [d[s:e][np.argmax(a[s:e])] for s, e in zip(starts, ends)]
    widths = (ends - starts) / fs * 1000
    gaps = np.diff(starts) / fs * 1000
    inside = gaps[gaps < TRAIN_GAP_MS]
    trains = 1 + int(np.sum(gaps >= TRAIN_GAP_MS))
    per_train = int(round(len(starts) / trains))
    gap = float(np.median(inside)) if len(inside) else 0.0
    return per_train, float(np.median(peaks)), float(np.median(widths)), gap


def same_stimulus(a: tuple, b: tuple, fs: float) -> bool:
    """Whether two pulse signatures are the same stimulus: the same pulses per train,
    size within 5%, width and interval within 10% or 2 samples."""
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
    # to 0.1 ms: finer than that is a sample's jitter, not a different stimulus
    n, size, width, gap = sig[0], _sig(sig[1]), round(sig[2], 1), round(sig[3], 1)
    v, u = best_prefix(size, unit)
    if n == 1:
        return f"Pulse of {v:.2g} {u}, {width:g} ms"
    return f"Train of {n} pulses of {v:.2g} {u}, {width:g} ms, every {gap:g} ms"


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
