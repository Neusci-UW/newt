"""The Measure tool's windows, and the DataPad table they fill (no Qt here).

A window is dragged across a chart on the Analysis whiteboard, and what it is depends on
what runs along that chart's x axis:

- time: seconds from the trial's start (Response, Stimulus, Raster, Rate…);
- phase: degrees of the stimulus's cycle, on a chart against phase, or dragged across one
  cycle of a chart against time; every cycle is measured and the trial's value is their mean;
- event: ms from each event, a stimulus or a spike (the triggered charts, the spike shapes),
  measured at every event and averaged for the trial;
- range: a range of one signal plotted against another (Response against stimulus, V
  against dV/dt), e.g. how long V stays above −20 mV;
- values: a range of a spike property (AP sizes, Intervals): the spikes in it;
- shape: a shape drawn around points (PCA map, Scatter): the points inside.

Time and phase windows can repeat along the trace, every so many seconds or degrees, giving
one column each time. Each column is one measurement (see datapad.py) of one channel.

The table's rows are each trial, each group's average trace, each group's trials measured
one by one and given as mean ± SD (and n), or each spike.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from . import analysis as A
from . import datapad as D

TIME, PHASE, EVENT, RANGE, VALUES, SHAPE = "time", "phase", "event", "range", "values", "shape"
ROWS = {"trial": "Each trial", "average": "Each group's average", "spread": "Each group: mean ± SD of its trials",
        "spike": "Each spike"}
# what each kind of window is on (Window.on), in words
ON = {"stimulus": "after each stimulus", "spike": "around each spike", "loop": "of the stimulus",
      "phaseplot": "of V, through each spike", "sizes": "of AP size", "intervals": "of interval",
      "pca": "on the PCA map", "scatter": "on Scatter"}
# the measurements of the newer kinds: key: (label, unit from the trace's unit u, help)
RANGE_KEYS = {
    "intime": ("Time in range", "s", "How long the x value stays in the window: in each trial, or through each "
                                     "spike (each spike's, averaged for the trial)"),
    "share": ("Share of the time in range", "%", "The part of the time the x value is in the window"),
}
VALUE_KEYS = {
    "count": ("Spikes in range", "", "How many of the trial's spikes are in the window (each spike: 1 if it is)"),
    "share": ("Share of spikes in range", "%", "The part of the trial's spikes in the window"),
    "mean": ("Mean value in range", "{u}", "The average of the spikes' values in the window (each spike: its value)"),
}
SHAPE_KEYS = {
    "count": ("Points inside", "", "How many of the trial's points are inside the shape (each point: 1 if it is)"),
    "share": ("Share of points inside", "%", "The part of the trial's points inside the shape"),
}
# what a phase window can measure: those that make sense of one cycle
PHASE_KEYS = ("mean", "max", "min", "p2p", "amp", "sd", "rms", "area", "absarea", "slope", "tmax", "tmin",
              "start", "end", "spikes")
MAX_REPEATS = 50


@dataclass
class Window:
    id: int
    name: str
    unit: str             # TIME or PHASE
    start: float          # s, or degrees
    width: float          # s, or degrees
    key: str              # what to measure (measurements(unit))
    channel: str
    repeat: float = 0.0   # every this many s or degrees; 0: once
    on: str = ""          # event, range, values and shape windows: what they're on (ON)
    points: list = field(default_factory=list)   # a shape's corners [[x, y]…]; on Scatter, its axes too
    axes: list = field(default_factory=list)     # on Scatter: the columns along x and up y

    @property
    def end(self) -> float:
        return self.start + self.width

    def starts(self, length: float) -> list[float]:
        """Where each copy starts, up to `length` (s, or 360°)."""
        out = [self.start]
        if self.repeat > 0:
            s = self.start + self.repeat
            while s + self.width <= length + 1e-9 and len(out) < MAX_REPEATS:
                out.append(s)
                s += self.repeat
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Window":
        return Window(**{k: d[k] for k in ("id", "name", "unit", "start", "width", "key", "channel")},
                      repeat=d.get("repeat", 0.0), on=d.get("on", ""), points=list(d.get("points", [])),
                      axes=list(d.get("axes", [])))


@dataclass
class Column:
    window: Window
    start: float
    end: float

    def label(self, unit_of) -> str:
        """Its heading: the window, where (if it repeats), what and in what unit."""
        w = self.window
        where = ""
        if w.repeat > 0:
            where = f" @ {self.start * 1000:g} ms" if w.unit == TIME else f" @ {self.start:g}°"
        u = unit_label(w.key, unit_of(w.channel), w.unit, w.on)
        what = f" {w.channel}" if w.channel and w.unit not in (VALUES, SHAPE) else ""
        return f"{w.name}{where}{what} {name_of(w.key, w.unit)}" + (f" ({u})" if u else "")


def measurements(unit: str) -> dict:
    """What a window of this kind can measure: key: (label, unit, help)."""
    return {RANGE: RANGE_KEYS, VALUES: VALUE_KEYS, SHAPE: SHAPE_KEYS}.get(unit, D.MEASUREMENTS)


def name_of(key: str, unit: str) -> str:
    name = measurements(unit)[key][0]
    if unit == PHASE:
        name = name.replace("Time of", "Phase of")
    return name


def unit_label(key: str, u: str, unit: str, on: str = "") -> str:
    """The unit of a measurement: in a phase window, time is degrees."""
    if unit in (RANGE, VALUES, SHAPE):
        return measurements(unit)[key][1].format(u="ms" if on == "intervals" else u)
    s = D.unit_of(key, u)
    if unit == PHASE:
        s = {"s": "°"}.get(s, s.replace("/s", "/°").replace("·s", "·°"))
        if key == "spikes":
            s = "per cycle"
    return s


def next_name(windows: list[Window], unit: str) -> str:
    letter = {PHASE: "P", EVENT: "E", RANGE: "R", VALUES: "V", SHAPE: "S"}.get(unit, "W")
    used = {w.name for w in windows}
    return next(f"{letter}{k}" for k in range(1, 10000) if f"{letter}{k}" not in used)


def columns(windows: list[Window], duration: float) -> list[Column]:
    out = []
    for w in windows:
        if w.unit not in (TIME, PHASE):
            out.append(Column(w, w.start, w.end))
            continue
        for s in w.starts(duration if w.unit == TIME else 360.0):
            out.append(Column(w, s, s + w.width))
    return out


# -- measuring ---------------------------------------------------------------------------------
def measure_time(col: Column, y: np.ndarray | None, fs: float, spike_times=None) -> float:
    """One column of a trace against time (from the trial's start)."""
    if y is None:
        return float("nan")
    t = np.arange(len(y)) / fs
    if col.start >= len(y) / fs:                   # past the end of a shorter trial: nothing, not a zero count
        return float("nan")
    sl = D.window(t, col.start, col.end)
    return D.measure(col.window.key, t[sl], y[sl], fs, spike_times)


def measure_cycles(col: Column, R: np.ndarray, spike_phases: np.ndarray | None = None,
                   n_cycles: int = 0) -> float:
    """One column of a trace's cycles (rows of R, A.CYCLE_POINTS each, 0° to 360°): measured
    on each cycle, then their mean. Spike counts are per cycle."""
    key = col.window.key
    if key == "spikes":
        if spike_phases is None or not n_cycles:
            return float("nan")
        ph = np.asarray(spike_phases, float)
        return float(np.sum((ph >= col.start) & (ph <= col.end))) / n_cycles
    if R is None or not len(R):
        return float("nan")
    deg = np.arange(R.shape[1]) * 360 / R.shape[1]
    sl = D.window(deg, col.start, col.end)
    fs = R.shape[1] / 360                  # samples per degree, for rates of change
    vals = [D.measure(key, deg[sl], row[sl], fs) for row in R]
    vals = [v for v in vals if np.isfinite(v)]
    return float(np.mean(vals)) if vals else float("nan")


def table(page, windows: list[Window], rows_from: str, chosen: list[int], plan):
    """The DataPad table: (columns, rows), each row {"label", "group", "color", "values":
    [value, or (mean, SD, n)]}. `page` is the Analysis page (what the charts read), `chosen`
    the trials to measure, and plan(chosen) which groups they make (Chart.plan)."""
    good = [i for i in chosen if page.blocks[i] is not None]
    duration = max((page.blocks[i]["duration_s"] for i in good), default=0.0)
    cols = columns(windows, duration)
    if not good or not cols:
        return cols, []
    ctx = _context(page, good, windows)
    averaged, singles = plan(good)
    groups = [(page.group_color(g), name, blocks) for g, name, blocks in averaged]
    if not groups:
        groups = [(page.group_color(0), "All trials", good)]
    rows = []
    if rows_from == "spike":
        rows = _spike_rows(page, groups + [(page.group_color(0), "", [i]) for i in singles], cols, ctx)
    elif rows_from == "trial":
        for color, name, blocks in groups:
            for i in blocks:
                rows.append({"label": page._paths[i].stem, "group": name, "color": color,
                             "values": [_one(page, i, c, ctx) for c in cols]})
        for i in singles:
            rows.append({"label": page._paths[i].stem, "group": "", "color": page.group_color(0),
                         "values": [_one(page, i, c, ctx) for c in cols]})
    else:
        for color, name, blocks in groups:
            if rows_from == "average":
                values = [_of_average(page, blocks, c, ctx) for c in cols]
            else:
                values = []
                for c in cols:
                    v = np.array([_one(page, i, c, ctx) for i in blocks], float)
                    v = v[np.isfinite(v)]
                    values.append((float(v.mean()), float(v.std(ddof=1)) if len(v) > 1 else float("nan"), len(v))
                                  if len(v) else (float("nan"), float("nan"), 0))
            rows.append({"label": f"n = {len(blocks)}", "group": name, "color": color, "values": values})
    _on_scatter(page, cols, rows)
    return cols, rows


def _context(page, good: list[int], windows: list[Window]) -> dict:
    """What several windows share, worked out once: the spikes' places on the PCA map (as the
    map draws them: every spike of the trials measured)."""
    ctx = {}
    ev = page.events
    if ev is not None and any(w.unit == SHAPE and w.on == "pca" for w in windows):
        keep = np.flatnonzero(np.isin(ev.block, good))
        if len(keep) > 2:
            points = np.full((len(ev.t), 2), np.nan)
            points[keep] = A.principal_components(ev.waves[keep], 2)[0]
            ctx["pca"] = points
    return ctx


def inside(points, corners) -> np.ndarray:
    """Which points (n × 2) are inside the closed shape with these corners (m × 2)."""
    pts = np.asarray(points, float).reshape(-1, 2)
    c = np.asarray(corners, float).reshape(-1, 2)
    if len(c) < 3:
        return np.zeros(len(pts), bool)
    x, y = pts[:, :1], pts[:, 1:]
    x0, y0 = c[:, 0], c[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    with np.errstate(all="ignore"):              # a ray to the right crosses the edge: odd times is inside
        cross = ((y0 > y) != (y1 > y)) & (x < (x1 - x0) * (y - y0) / np.where(y1 != y0, y1 - y0, 1) + x0)
    return cross.sum(1) % 2 == 1


def _spike_index(page, i: int) -> np.ndarray:
    """Trial i's spikes of the units shown (indices into page.events), in time order."""
    ev = page.events
    if ev is None:
        return np.empty(0, int)
    idx = np.flatnonzero((ev.block == i) & np.isin(ev.unit, list(page.raw.shown_units())))
    return idx[np.argsort(ev.t[idx], kind="stable")]


def _event_times(page, i: int, on: str) -> np.ndarray | None:
    """When each event of an event window happened in trial i (s): each stimulus, or each spike."""
    if on == "stimulus":
        return page.onsets(i)
    ev = page.events
    return None if ev is None else ev.t[_spike_index(page, i)]


def _around(page, i: int, col: Column, times: np.ndarray) -> np.ndarray:
    """An event window's measurement at each of these times (s) in trial i."""
    w, fs = col.window, page.blocks[i]["sample_rate"]
    a_s, b_s = col.start / 1000, col.end / 1000
    if w.key in D.SPIKE_KEYS:
        spikes = _spikes_of(page, i)
        if spikes is None:
            return np.full(len(times), np.nan)
        n = np.array([np.sum((spikes >= te + a_s) & (spikes <= te + b_s)) for te in times], float)
        return n if w.key == "spikes" else n / max(b_s - a_s, 1e-12)
    y = page.trace(i, w.channel)
    out = np.full(len(times), np.nan)
    if y is None:
        return out
    for k, te in enumerate(times):
        a, b = int(np.floor((te + a_s) * fs)), int(np.ceil((te + b_s) * fs)) + 1
        if a >= 0 and b <= len(y):                # near the trial's ends: left out
            out[k] = D.measure(w.key, np.arange(a, b) / fs - te, y[a:b], fs)
    return out


def _intervals_before(ev, idx: np.ndarray) -> np.ndarray:
    """The interval (ms) before each of these spikes, from the last of its unit in its trial."""
    out = np.full(len(idx), np.nan)
    for u in np.unique(ev.unit[idx]):
        mine = np.flatnonzero(ev.unit[idx] == u)
        order = mine[np.argsort(ev.t[idx][mine], kind="stable")]
        out[order[1:]] = np.diff(ev.t[idx][order]) * 1000
    return out


def _per_spike(page, i: int, col: Column, ctx: dict, idx: np.ndarray) -> np.ndarray | None:
    """A column's value for each of trial i's spikes `idx` (None: it isn't measured per spike).
    Counts and shares give 1 for a spike in the window and 0 for one outside."""
    w, ev = col.window, page.events
    if ev is None:
        return None
    if w.unit == EVENT and w.on == "spike":
        return _around(page, i, col, ev.t[idx])
    if w.unit == RANGE and w.on == "phaseplot":
        x = ev.waves[idx]
        within = (x >= col.start) & (x <= col.end)
        dt = float(np.median(np.diff(ev.wave_t_ms))) / 1000 if len(ev.wave_t_ms) > 1 else 0.0
        return within.sum(1) * dt if w.key == "intime" else 100 * within.mean(1)
    if w.unit == VALUES:
        v = np.abs(ev.amplitude[idx]) if w.on == "sizes" else _intervals_before(ev, idx)
        if w.key == "mean":
            return v
        return np.where(np.isfinite(v), ((v >= col.start) & (v <= col.end)).astype(float), np.nan)
    if w.unit == SHAPE and w.on == "pca":
        points = ctx.get("pca")
        if points is None:
            return np.full(len(idx), np.nan)
        return np.where(np.isfinite(points[idx, 0]), inside(points[idx], w.points).astype(float), np.nan)
    return None


def _spike_rows(page, sets: list, cols: list[Column], ctx: dict) -> list[dict]:
    """A row for each spike: the columns measured per spike, the others blank."""
    ev = page.events
    rows = []
    if ev is None:
        return rows
    for _, name, blocks in sets:
        for i in blocks:
            idx = _spike_index(page, i)
            per = [_per_spike(page, i, c, ctx, idx) for c in cols]
            for k, j in enumerate(idx):
                rows.append({"label": f"{page._paths[i].stem} · {ev.t[j] * 1000:.1f} ms", "group": name,
                             "color": page.raw.color_of(int(ev.unit[j])),
                             "values": [float(v[k]) if v is not None else float("nan") for v in per]})
    return rows


def _on_scatter(page, cols: list[Column], rows: list[dict]) -> None:
    """Shapes drawn on Scatter: whether each row's point is inside (1) or not (0)."""
    labels = [c.label(page.unit_of) for c in cols]
    names = list(dict.fromkeys(r["group"] for r in rows))
    for j, c in enumerate(cols):
        w = c.window
        if w.unit != SHAPE or w.on != "scatter" or len(w.axes) != 2:
            continue
        for r in rows:
            xy = []
            for axis in w.axes:
                if axis == "group":
                    xy.append(names.index(r["group"]) + 1.0)
                else:
                    v = r["values"][labels.index(axis)] if axis in labels else float("nan")
                    xy.append(v[0] if isinstance(v, tuple) else v)
            if not all(np.isfinite(xy)):
                r["values"][j] = float("nan")
                continue
            yes = float(inside([xy], w.points)[0])
            r["values"][j] = yes if w.key == "count" else 100 * yes


def _spikes_of(page, i: int) -> np.ndarray | None:
    ev = page.events
    if ev is None:
        return None
    return ev.t[ev.block == i]


def _cycles_of(page, i: int) -> np.ndarray:
    skip = page.repeats.cycle_skip.value()
    return page.repeats.found(i).bounds[skip:]


def _one(page, i: int, col: Column, ctx: dict | None = None) -> float:
    """Trial i's value of this column."""
    w, fs = col.window, page.blocks[i]["sample_rate"]
    ctx = ctx or {}
    if w.unit == TIME:
        return measure_time(col, page.trace(i, w.channel), fs, _spikes_of(page, i))
    if w.unit == EVENT:                           # at each event, then their mean
        times = _event_times(page, i, w.on)
        if times is None or not len(times):
            return float("nan")
        return _mean(_around(page, i, col, np.asarray(times, float)))
    if w.unit == RANGE and w.on == "loop":        # the stimulus channel, all through the trial
        x = page.trace(i, w.channel)
        if x is None or not len(x):
            return float("nan")
        within = (x >= col.start) & (x <= col.end)
        return float(within.sum() / fs) if w.key == "intime" else float(100 * within.mean())
    if w.unit in (RANGE, VALUES, SHAPE):
        if w.on == "scatter":
            return float("nan")                   # filled in once the other columns are (_on_scatter)
        v = _per_spike(page, i, col, ctx, _spike_index(page, i))
        if v is None:
            return float("nan")
        if w.unit == RANGE or (w.unit == VALUES and w.key == "mean"):
            if w.unit == VALUES:                  # the mean of those in range
                v = v[(v >= col.start) & (v <= col.end)]
            return _mean(v)
        v = v[np.isfinite(v)]
        if not len(v):
            return float("nan")
        return float(v.sum()) if w.key == "count" else float(100 * v.mean())
    bounds = _cycles_of(page, i)
    if not len(bounds):
        return float("nan")
    y = page.trace(i, w.channel)
    R = A.cycle_rows(y, bounds) if y is not None else None
    spikes = _spikes_of(page, i)
    phases = A.spike_phases(spikes, bounds, fs)[1] if spikes is not None else None
    return measure_cycles(col, R, phases, len(bounds))


def _mean(v: np.ndarray) -> float:
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return float(v.mean()) if len(v) else float("nan")


def _of_average(page, blocks: list[int], col: Column, ctx: dict | None = None) -> float:
    """The value of a group's average trace (or, in phase, the mean of all its cycles; for
    windows measured at events, spikes or points, the mean of its trials' values)."""
    w = col.window
    if w.unit not in (TIME, PHASE):
        return _mean([_one(page, i, col, ctx) for i in blocks])
    if w.unit == TIME:
        ys = [page.trace(i, w.channel) for i in blocks]
        ys = [y for y in ys if y is not None]
        if not ys:
            return float("nan")
        n = min(len(y) for y in ys)
        fs = page.blocks[blocks[0]]["sample_rate"]
        if w.key in D.SPIKE_KEYS:                 # spikes: the trials' mean count
            return float(np.nanmean([_one(page, i, col) for i in blocks]))
        return measure_time(col, np.mean([y[:n] for y in ys], axis=0), fs)
    rows, phases, n_cycles = [], [], 0
    for i in blocks:
        bounds = _cycles_of(page, i)
        y = page.trace(i, w.channel)
        if not len(bounds) or y is None:
            continue
        rows.append(A.cycle_rows(y, bounds))
        n_cycles += len(bounds)
        spikes = _spikes_of(page, i)
        if spikes is not None:
            phases.append(A.spike_phases(spikes, bounds, page.blocks[i]["sample_rate"])[1])
    if not rows:
        return float("nan")
    R = np.vstack(rows)
    return measure_cycles(col, R.mean(axis=0, keepdims=True) if w.key != "spikes" else R,
                          np.concatenate(phases) if phases else None, n_cycles)


def fmt(v, unit: str = "") -> str:
    """A table cell: a value with its unit and a sensible prefix (0.0374 V: 37.4 mV), or
    mean ± SD."""
    if isinstance(v, tuple):
        m, sd, _ = v
        if not np.isfinite(m):
            return ""
        return _with_unit(m, unit) + (f" ± {_with_unit(sd, unit)}" if np.isfinite(sd) else "")
    return _with_unit(v, unit) if np.isfinite(v) else ""


def _with_unit(v: float, unit: str) -> str:
    from .units import best_prefix
    head, sep, tail = unit.partition("/") if "/" in unit else unit.partition("·")
    if head[-1:] in ("V", "A") and len(head) <= 2:
        n, head = best_prefix(v, head)
        return f"{n:.4g} {head}{sep}{tail}"
    if unit == "s":
        n, u = best_prefix(v, "s")
        return f"{n:.4g} {u}"
    if unit == "°":
        return f"{v:.4g}°"
    return f"{v:.4g} {unit}".strip()


def _num(v: float) -> str:
    a = abs(v)
    if a == 0:
        return "0"
    if a >= 1e4 or a < 1e-3:
        return f"{v:.3e}"
    return f"{v:.4g}"


def to_csv_rows(cols: list[Column], rows: list[dict], unit_of) -> list[list[str]]:
    """The table as text rows, headings first (mean ± SD cells become mean, SD, n columns)."""
    spread = any(isinstance(v, tuple) for r in rows for v in r["values"])
    head = ["Group", "Trial" if not spread else "n"]
    for c in cols:
        head += [c.label(unit_of)] + (["SD", "n"] if spread else [])
    out = [head]
    for r in rows:
        line = [r["group"], r["label"]]
        for v in r["values"]:
            if isinstance(v, tuple):
                line += [_num(v[0]) if np.isfinite(v[0]) else "", _num(v[1]) if np.isfinite(v[1]) else "", str(v[2])]
            else:
                line.append(_num(v) if np.isfinite(v) else "")
        out.append(line)
    return out
