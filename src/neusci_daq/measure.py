"""The Measure tool's windows, and the DataPad table they fill (no Qt here).

A window is a stretch of a trace to measure, dragged across a chart on the Analysis
whiteboard: in time (seconds from the trial's start, on a chart against time) or in phase
(degrees of the stimulus's cycle, on a chart against phase, where every cycle is measured
and the trial's value is their mean). It can repeat along the trace, every so many seconds
or degrees, giving one column each time. Each column is one measurement (see datapad.py)
of one channel.

The table's rows are each trial, each group's average trace, or each group's trials
measured one by one and given as mean ± SD (and n).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from . import analysis as A
from . import datapad as D

TIME, PHASE = "time", "phase"
ROWS = {"trial": "Each trial", "average": "Each group's average", "spread": "Each group: mean ± SD of its trials"}
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
    key: str              # what to measure (datapad.MEASUREMENTS)
    channel: str
    repeat: float = 0.0   # every this many s or degrees; 0: once

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
                      repeat=d.get("repeat", 0.0))


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
        u = unit_label(w.key, unit_of(w.channel), w.unit)
        return f"{w.name}{where} {w.channel} {name_of(w.key, w.unit)}" + (f" ({u})" if u else "")


def name_of(key: str, unit: str) -> str:
    name = D.MEASUREMENTS[key][0]
    if unit == PHASE:
        name = name.replace("Time of", "Phase of")
    return name


def unit_label(key: str, u: str, unit: str) -> str:
    """The unit of a measurement: in a phase window, time is degrees."""
    s = D.unit_of(key, u)
    if unit == PHASE:
        s = {"s": "°"}.get(s, s.replace("/s", "/°").replace("·s", "·°"))
        if key == "spikes":
            s = "per cycle"
    return s


def next_name(windows: list[Window], unit: str) -> str:
    letter = "W" if unit == TIME else "P"
    used = {w.name for w in windows}
    return next(f"{letter}{k}" for k in range(1, 10000) if f"{letter}{k}" not in used)


def columns(windows: list[Window], duration: float) -> list[Column]:
    out = []
    for w in windows:
        for s in w.starts(duration if w.unit == TIME else 360.0):
            out.append(Column(w, s, s + w.width))
    return out


# -- measuring ---------------------------------------------------------------------------------
def measure_time(col: Column, y: np.ndarray | None, fs: float, spike_times=None) -> float:
    """One column of a trace against time (from the trial's start)."""
    if y is None:
        return float("nan")
    t = np.arange(len(y)) / fs
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
    averaged, singles = plan(good)
    groups = [(page.group_color(g), name, blocks) for g, name, blocks in averaged]
    if not groups:
        groups = [(page.group_color(0), "All trials", good)]
    rows = []
    if rows_from == "trial":
        for color, name, blocks in groups:
            for i in blocks:
                rows.append({"label": page._paths[i].stem, "group": name, "color": color,
                             "values": [_one(page, i, c) for c in cols]})
        for i in singles:
            rows.append({"label": page._paths[i].stem, "group": "", "color": page.group_color(0),
                         "values": [_one(page, i, c) for c in cols]})
        return cols, rows
    for color, name, blocks in groups:
        if rows_from == "average":
            values = [_of_average(page, blocks, c) for c in cols]
        else:
            values = []
            for c in cols:
                v = np.array([_one(page, i, c) for i in blocks], float)
                v = v[np.isfinite(v)]
                values.append((float(v.mean()), float(v.std(ddof=1)) if len(v) > 1 else float("nan"), len(v))
                              if len(v) else (float("nan"), float("nan"), 0))
        rows.append({"label": f"n = {len(blocks)}", "group": name, "color": color, "values": values})
    return cols, rows


def _spikes_of(page, i: int) -> np.ndarray | None:
    ev = page.events
    if ev is None:
        return None
    return ev.t[ev.block == i]


def _cycles_of(page, i: int) -> np.ndarray:
    skip = page.repeats.cycle_skip.value()
    return page.repeats.found(i).bounds[skip:]


def _one(page, i: int, col: Column) -> float:
    """Trial i's value of this column."""
    w, fs = col.window, page.blocks[i]["sample_rate"]
    if w.unit == TIME:
        return measure_time(col, page.trace(i, w.channel), fs, _spikes_of(page, i))
    bounds = _cycles_of(page, i)
    if not len(bounds):
        return float("nan")
    y = page.trace(i, w.channel)
    R = A.cycle_rows(y, bounds) if y is not None else None
    spikes = _spikes_of(page, i)
    phases = A.spike_phases(spikes, bounds, fs)[1] if spikes is not None else None
    return measure_cycles(col, R, phases, len(bounds))


def _of_average(page, blocks: list[int], col: Column) -> float:
    """The value of a group's average trace (or, in phase, the mean of all its cycles)."""
    w = col.window
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
