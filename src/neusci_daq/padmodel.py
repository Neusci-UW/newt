"""The DataPad's workings, without Qt, in the style of LabChart's DataPad: a table whose
rows are stretches of the recording and whose columns are measurements of them.

Rows (Pad.rows_mode):
- trial: one row per trial (the whole trial);
- segment: one row per Complex Stimulus segment of each trial, every one, or just one letter;
- pulse: one row per pulse of a pulse train, from the train's settings;
- manual: rows added by hand, a stretch of a trial each ("Add to DataPad").

A column (Column) is one recipe:
- measure: a measurement (datapad.py) of a channel, over the row's own stretch (over="row")
  or over its own window (over="window"): segments by name (A, B, B–D), narrowed to their
  first or last so many ms or start + a to b ms, or a stretch of time. A window by segment
  finds the same segment in every trial, even when its timing changes from trial to trial;
- level / duration: a segment's level or its length, read from the stimulus that was sent;
- info: the trial's number, group, place in the group, the row's start;
- formula: one line of the other columns, worked on whole columns at once (dV / I_B * 1000);
- python: a short Python function body, run for each row (or once, for the whole column).

Every column has the name formulas use (an identifier: Vrest, I_B) and, if you like, a
name to show (V rest). Text written in the fx bar is read back by parse_recipe:
``mean(Vm, B, last 50 ms)``, ``max(Vm, row)``, ``slope(Im, 100-200 ms)``, ``level(B)``,
``length(B)``, else a formula (``= dV / I_B``).

Measurement columns are cached, so changing one column works out only it and the formulas.
"""
from __future__ import annotations

import json
import keyword
import math
import re
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace

import numpy as np

from . import datapad as D
from . import fitting
from . import stim as stimmod
from . import stimmeasures as SM

KINDS = {"measure": "Measure a channel", "level": "Stimulus level", "duration": "Segment length",
         "info": "Trial info", "formula": "Formula", "python": "Python"}
INFO = {"trial": ("Trial", ""), "group": ("Group", ""), "n_in_group": ("Trial in group", ""),
        "start": ("Row start", "ms")}
ROWS = {"trial": "Each trial", "segment": "Each segment", "pulse": "Each pulse", "manual": "By hand"}
# the measurements, grouped as LabChart's column setup groups them
GROUPS = (("Amplitude", ("value", "mean", "max", "min", "p2p", "amp", "start", "end", "sd", "rms")),
          ("Area and slope", ("area", "absarea", "slope", "rise", "fall")),
          ("Timing", ("tmax", "tmin")),
          ("Spikes", ("spikes", "rate")),
          ("Frequency", ("freq",)))


def _nan_stat(f):
    def g(x):
        x = np.asarray(x, float)
        x = x[np.isfinite(x)]
        return f(x) if x.size else float("nan")
    return g


FUNCS = {
    "abs": np.abs, "sqrt": np.sqrt, "log": np.log, "log10": np.log10, "exp": np.exp,
    "sin": np.sin, "cos": np.cos, "round": np.round,
    "mean": _nan_stat(np.mean), "median": _nan_stat(np.median),
    "sd": _nan_stat(lambda x: x.std(ddof=1) if x.size > 1 else float("nan")),
    "sem": _nan_stat(lambda x: x.std(ddof=1) / math.sqrt(x.size) if x.size > 1 else float("nan")),
    "min": _nan_stat(np.min), "max": _nan_stat(np.max), "sum": _nan_stat(np.sum),
    "norm": lambda x: np.asarray(x, float) / np.nanmax(np.abs(np.asarray(x, float))),
    "first": lambda x: np.asarray(x, float)[0],
    "cumsum": lambda x: np.nancumsum(np.asarray(x, float)),
    "pi": math.pi,
}
FUNC_HELP = {
    "abs": "abs(x): size without sign", "sqrt": "sqrt(x): square root", "log": "log(x): natural log",
    "log10": "log10(x): log base 10", "exp": "exp(x): e to the x", "mean": "mean(x): the column's average",
    "median": "median(x)", "sd": "sd(x): the column's standard deviation", "sem": "sem(x): standard error",
    "min": "min(x): the column's smallest", "max": "max(x): the column's largest",
    "norm": "norm(x): divided by the largest (largest becomes 1)", "first": "first(x): the first row's value",
    "cumsum": "cumsum(x): running total", "round": "round(x)",
}


# ------------------------------------------------------------------------------- windows
@dataclass
class Selection:
    first: str = ""            # a segment's letter; "" for a stretch of time
    last: str = ""             # the last segment of a run (same as first for one)
    mode: str = "all"          # all | first | last | range | phase (degrees ph0–ph1 of every cycle) | point
    a_ms: float = 50.0         # first/last: how long; range: from the start + a_ms …
    b_ms: float = 100.0        # … to the start + b_ms
    t0: float = 0.0            # time window, s from the trial's start
    t1: float = 0.0
    ph0: float = 0.0           # phase: from this many degrees of each cycle …
    ph1: float = 360.0         # … to this many (the same: a point, at that phase)

    @staticmethod
    def time(t0: float, t1: float) -> "Selection":
        return Selection("", "", "all", t0=min(t0, t1), t1=max(t0, t1))

    @staticmethod
    def phase(first: str, last: str, ph0: float, ph1: float) -> "Selection":
        """Part of every cycle of a periodic segment (B, 0–90°), measured cycle by cycle and averaged."""
        return Selection(first, last or first, "phase", ph0=min(ph0, ph1), ph1=max(ph0, ph1))

    def is_phase(self) -> bool:
        return not self.is_time() and self.mode == "phase"

    @staticmethod
    def point(t: float) -> "Selection":
        """One moment: what the trace is at t (s)."""
        return Selection("", "", "point", t0=t, t1=t)

    def is_point(self) -> bool:
        return self.is_time() and self.mode == "point"

    @staticmethod
    def seg(first: str, last: str | None = None) -> "Selection":
        return Selection(first, last or first)

    def is_time(self) -> bool:
        return not self.first

    def letters(self, order: list[str]) -> list[str]:
        if self.is_time() or self.first not in order or self.last not in order:
            return []
        a, b = sorted((order.index(self.first), order.index(self.last)))
        return order[a:b + 1]

    def label(self) -> str:
        if self.is_point():
            return f"at {self.t0 * 1000:.4g} ms"
        if self.is_time():
            return f"{self.t0 * 1000:.4g}-{self.t1 * 1000:.4g} ms"
        s = self.first if self.first == self.last else f"{self.first}-{self.last}"
        if self.mode == "first":
            s += f", first {self.a_ms:g} ms"
        elif self.mode == "last":
            s += f", last {self.a_ms:g} ms"
        elif self.mode == "range":
            s += f", +{self.a_ms:g} to +{self.b_ms:g} ms"
        elif self.mode == "phase":
            s += f", at {self.ph0:.4g}°" if self.ph0 == self.ph1 else f", {self.ph0:.4g}-{self.ph1:.4g}°"
        return s

    def window(self, segs: dict[str, tuple[float, float]], order: list[str]) -> tuple[float, float] | None:
        if self.is_point():
            return (self.t0, self.t0)
        if self.is_time():
            return (self.t0, self.t1) if self.t1 > self.t0 else None
        got = self.letters(order)          # phase: the whole of its segments (the cycles are inside)
        if not got:
            return None
        a, b = segs[got[0]][0], segs[got[-1]][1]
        if self.mode == "first":
            b = min(b, a + self.a_ms / 1000)
        elif self.mode == "last":
            a = max(a, b - self.a_ms / 1000)
        elif self.mode == "range":
            a, b = a + self.a_ms / 1000, min(b, a + self.b_ms / 1000)
        return (a, b) if b > a else None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict | None) -> "Selection":
        d = d or {}
        return Selection(**{k: d[k] for k in Selection.__dataclass_fields__ if k in d})


class _SegBuilder:
    """seg("B") in a Python column: .first(ms), .last(ms), .range(a, b) narrow it."""

    def __init__(self, first: str, last: str | None = None):
        self.sel = Selection.seg(first, last)

    def first(self, ms: float) -> Selection:
        return Selection(self.sel.first, self.sel.last, "first", float(ms))

    def last(self, ms: float) -> Selection:
        return Selection(self.sel.first, self.sel.last, "last", float(ms))

    def range(self, a: float, b: float) -> Selection:
        return Selection(self.sel.first, self.sel.last, "range", float(a), float(b))


# ------------------------------------------------------------------------------- columns
@dataclass
class Column:
    name: str                  # what formulas call it: an identifier (Vrest, I_B)
    kind: str = "measure"
    key: str = "mean"          # measure: datapad key; info: INFO key
    channel: str = ""
    sel: Selection = field(default_factory=Selection)
    expr: str = ""             # formula, or the python body
    scope: str = "row"         # python: row | column
    unit: str = ""             # the unit shown; blank: worked out where it can be
    over: str = "window"       # measure: "window" (its own) or "row" (the row's stretch)
    title: str = ""            # the name shown, if not the formula name
    title_auto: bool = True    # the title follows what it measures (Mean Voltage) until you name it
    units: list | None = None  # spike measurements: None = the units ticked in Spikes, [-1] = all, [k] = unit k
    digits: int = 4            # how its numbers show: this many significant figures …
    places: bool = False       # … or, with places, this many decimal places

    def fmt(self, v: float) -> str:
        return fmt(v, self.digits, self.places)

    def shown(self) -> str:
        return self.title or self.name

    def recipe(self) -> str:
        """The column as the fx bar writes it (parse_recipe reads it back)."""
        if self.kind == "measure":
            where = 'row' if self.over == 'row' else self.sel.label()
            if self.key in D.SPIKE_KEYS:
                who = "" if self.units is None else ", all units" if self.units == [-1] else \
                    f", unit {', '.join(str(u + 1) for u in self.units)}"
                return f"{self.key}({where}{who})"
            return f"{self.key}({self.channel}, {where})"
        if self.kind == "level":
            return f"level({self.sel.first or '?'})"
        if self.kind == "duration":
            return f"length({self.sel.label()})"
        if self.kind == "info":
            return f"info({self.key})"
        if self.kind == "formula":
            return "= " + self.expr
        return "python: " + (self.expr.strip().splitlines() or [""])[-1]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sel"] = self.sel.to_dict()
        return d

    @staticmethod
    def from_dict(d: dict) -> "Column":
        d = dict(d)
        d["sel"] = Selection.from_dict(d.get("sel"))
        if "title_auto" not in d:                 # saved before titles followed: a title given was yours
            d["title_auto"] = not d.get("title")
        return Column(**{k: d[k] for k in Column.__dataclass_fields__ if k in d})


NAME_RE = re.compile(r"^[^\W\d]\w*$")
_SEL_RE = re.compile(r"^(?:(?P<a>\d*[A-Z]{1,2})(?:\s*[-–]\s*(?P<b>\d*[A-Z]{1,2}))?"
                     r"(?:\s*,\s*(?P<m>first|last)\s+(?P<ms>[\d.]+)\s*ms"
                     r"|\s*,\s*\+(?P<ra>[\d.]+)\s*to\s*\+(?P<rb>[\d.]+)\s*ms"
                     r"|\s*,\s*(?P<p0>[\d.]+)\s*[-–]\s*(?P<p1>[\d.]+)\s*°"
                     r"|\s*,\s*at\s+(?P<pat>[\d.]+)\s*°)?"
                     r"|(?P<t0>[\d.]+)\s*[-–]\s*(?P<t1>[\d.]+)\s*ms"
                     r"|at\s+(?P<at>[\d.]+)\s*ms)$")


def parse_selection(text: str) -> Selection:
    m = _SEL_RE.match(text.strip())
    if not m:
        raise ValueError(f"Can't read “{text}”: try B, B-D, B, last 50 ms, B, +5 to +25 ms, 100-200 ms or at 120 ms")
    if m["at"] is not None:
        return Selection.point(float(m["at"]) / 1000)
    if m["t0"] is not None:
        return Selection.time(float(m["t0"]) / 1000, float(m["t1"]) / 1000)
    s = Selection.seg(m["a"], m["b"])
    if m["m"]:
        s.mode, s.a_ms = m["m"], float(m["ms"])
    elif m["ra"]:
        s.mode, s.a_ms, s.b_ms = "range", float(m["ra"]), float(m["rb"])
    elif m["p0"]:
        s = Selection.phase(s.first, s.last, float(m["p0"]), float(m["p1"]))
    elif m["pat"]:
        s = Selection.phase(s.first, s.last, float(m["pat"]), float(m["pat"]))
    return s


# the fx bar's argument names, and other names students may reach for
_ARG = {"channel": "channel", "ch": "channel", "of": "channel",
        "window": "window", "over": "window", "where": "window", "in": "window", "segment": "window",
        "seg": "window", "part": "part", "units": "units", "unit": "units", "which": "which"}


def _args(body: str) -> tuple[list[str], dict[str, str]]:
    """Split "Vm, B, part=last 50 ms" into positional and named arguments. Commas inside
    quotes or brackets stay; quotes round a value come off."""
    parts, depth, cur, quote = [], 0, "", ""
    for ch in body:
        if quote:
            cur += ch
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote, cur = ch, cur + ch
        elif ch in "([":
            depth, cur = depth + 1, cur + ch
        elif ch in ")]":
            depth, cur = depth - 1, cur + ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    pos, named = [], {}
    for p in (x.strip() for x in parts):
        m = re.match(r"^(\w+)\s*=\s*(.*)$", p)
        if m and not re.match(r"^\d", m[1]):
            key = _ARG.get(m[1].lower())
            if key is None:
                raise ValueError(f"Unknown argument {m[1]}=: use channel=, window=, part= or units=")
            if key in named:
                raise ValueError(f"{m[1]}= is given twice")
            named[key] = m[2].strip().strip("\"'")
        else:
            if named:
                raise ValueError("Positional arguments go before named ones: mean(Vm, B, part=last 50 ms)")
            pos.append(p.strip("\"'"))
    return pos, named


def _bind(fn: str, pos: list[str], named: dict[str, str], order: tuple[str, ...]) -> dict[str, str]:
    """Positional arguments in `order`, then the named ones; each given once."""
    if len(pos) > len(order):
        raise ValueError(f"{fn} takes at most {len(order)} arguments ({', '.join(order)})")
    got = dict(zip(order, pos))
    for k, v in named.items():
        if k not in order:
            raise ValueError(f"{fn} has no {k}=; it takes {', '.join(order)}")
        if k in got:
            raise ValueError(f"{k} is given twice (by place and by name)")
        got[k] = v
    return got


def _where(out: Column, window: str, part: str | None) -> None:
    if window == "row":
        if part:
            raise ValueError("part= needs segments: mean(Vm, B, part=last 50 ms)")
        out.over = "row"
        return
    out.over, out.sel = "window", parse_selection(window + (f", {part}" if part else ""))


def _units(text: str | None) -> list | None:
    if not text or text in ("ticked", "shown"):
        return None
    if text.startswith("all"):
        return [-1]
    got = [int(x) - 1 for x in re.findall(r"\d+", text)]
    if not got:
        raise ValueError(f"Can't read units “{text}”: try units=2, units=all or units=ticked")
    return got


def parse_recipe(text: str, c: Column, channels: list[str]) -> Column:
    """The fx bar's text as a column (a copy of c, changed). Raises ValueError, saying why.
    Arguments go by place or by name, as in Python:
    mean(Vm, B, last 50 ms) = mean(channel=Vm, window=B, part=last 50 ms) = mean(Vm, window=B-D)."""
    out = Column.from_dict(c.to_dict())
    t = text.strip()
    if t.startswith("python:"):
        return out                                 # Python is edited in its own box
    m = re.match(r"^(\w+)\s*\((.*)\)$", t)
    if m is None or t.startswith("="):            # = …: a formula of the columns, mean(dV) included
        out.kind, out.expr = "formula", t.lstrip("=").strip()
        return out
    fn = m[1]
    if fn in D.MEASUREMENTS or fn in ("level", "length", "info"):
        pos, named = _args(m[2])
        if fn in FUNCS and len(pos) == 1 and not named and pos[0] not in channels:
            out.kind, out.expr = "formula", t     # mean(dV): the column's mean, not a measurement
            return out
        # an old-style part after the window, written without part= ("B, last 50 ms")
        if len(pos) >= 2 and re.match(r"^(first|last)\s|^\+|^(at\s+)?[\d.]+\s*([-–]\s*[\d.]+\s*)?°", pos[-1]) \
                and "part" not in named:
            named["part"] = pos.pop()
    if fn in D.SPIKE_KEYS:                         # spikes(B), rate(row, units=2): from the spike sorter
        if pos and pos[0] in channels:
            pos = pos[1:]                          # the channel is the spike sorter's anyway
        named.pop("channel", None)
        if pos and re.match(r"^(units?\b|all units)", pos[-1]):
            named.setdefault("units", re.sub(r"^units?\s*", "", pos.pop()))
        got = _bind(fn, pos, named, ("window", "part", "units"))
        if "window" not in got:
            raise ValueError(f"{fn} needs where: {fn}(B), {fn}(row) or {fn}(window=B-D)")
        out.kind, out.key, out.units = "measure", fn, _units(got.get("units"))
        _where(out, got["window"], got.get("part"))
        return out
    if fn in D.MEASUREMENTS:
        got = _bind(fn, pos, named, ("channel", "window", "part"))
        if "channel" not in got or "window" not in got:
            ex = channels[0] if channels else "Vm"
            raise ValueError(f"{fn} needs a channel and where: {fn}({ex}, B) or {fn}(channel={ex}, window=B)")
        if got["channel"] not in channels:
            raise ValueError(f"No channel called {got['channel']}; the channels are {', '.join(channels)}")
        out.kind, out.key, out.channel = "measure", fn, got["channel"]
        _where(out, got["window"], got.get("part"))
        return out
    if fn == "level":
        got = _bind(fn, pos, named, ("window",))
        if "window" not in got:
            raise ValueError("level needs a segment: level(B) or level(window=B)")
        out.kind, out.sel = "level", Selection.seg(got["window"])
        return out
    if fn == "length":
        got = _bind(fn, pos, named, ("window", "part"))
        if "window" not in got:
            raise ValueError("length needs where: length(B) or length(window=B, part=first 20 ms)")
        out.kind = "duration"
        out.sel = parse_selection(got["window"] + (f", {got['part']}" if got.get("part") else ""))
        return out
    if fn == "info":
        got = _bind(fn, pos, named, ("which",))
        if got.get("which") not in INFO:
            raise ValueError(f"info takes one of {', '.join(INFO)}")
        out.kind, out.key = "info", got["which"]
        return out
    out.kind, out.expr = "formula", t.lstrip("=").strip()
    return out


def check_name(name: str, taken: list[str]) -> str:
    if not NAME_RE.match(name or ""):
        return "Formula names start with a letter and have only letters, digits and _ (I_B, dV, R2)."
    if keyword.iskeyword(name) or name in FUNCS or name in D.MEASUREMENTS or \
            name in ("row", "col", "trace", "seg", "time", "np", "level", "length", "info"):
        return f"“{name}” is taken by NEWT; try another."
    if name in taken:
        return f"There's already a column called {name}."
    return ""


def free_name(base: str, taken: list[str]) -> str:
    base = re.sub(r"\W", "_", base) or "c"
    if not NAME_RE.match(base):
        base = "c" + base
    if base not in taken and not check_name(base, []):
        return base
    k = 2
    while f"{base}{k}" in taken:
        k += 1
    return f"{base}{k}"


WORDS = {"value": "", "mean": "Mean", "max": "Max", "min": "Min", "p2p": "Peak-to-Peak", "amp": "Amplitude of",
         "start": "Starting", "end": "Ending", "sd": "SD of", "rms": "RMS", "area": "Area of",
         "absarea": "Absolute Area of", "slope": "Slope of", "rise": "Max Rise of", "fall": "Max Fall of",
         "tmax": "Time of Max", "tmin": "Time of Min", "freq": "Frequency of"}


def quantity(channel: str, unit: str) -> str:
    """What a channel records, in words, from its unit: Voltage, Current, else its name."""
    u = (unit or "").strip()
    if u.endswith("V") and len(u) <= 2:
        return "Voltage"
    if u.endswith("A") and len(u) <= 2:
        return "Current"
    return channel


def auto_title(c: Column, unit: str) -> str:
    """A plain-words name for what a column measures: Mean Voltage, Total Spikes, Stimulus Level.
    `unit` is the unit of its channel. Formulas and Python keep their formula name."""
    if c.kind == "measure":
        if c.key == "spikes":
            return "Total Spikes"
        if c.key == "rate":
            return "Spike Rate"
        q = quantity(c.channel, unit)
        if c.key == "value":
            return f"{q} {c.sel.label()}" if c.over == "window" and c.sel.is_point() else q
        return f"{WORDS.get(c.key, D.MEASUREMENTS[c.key][0])} {q}".strip()
    if c.kind == "level":
        return "Stimulus Level"
    if c.kind == "duration":
        return "Segment Length"
    if c.kind == "info":
        return {"trial": "Trial", "group": "Group", "n_in_group": "Trial in Group",
                "start": "Row Start"}.get(c.key, c.key)
    return ""


def html_name(name: str) -> str:
    """A name with what follows its first _ as a true subscript: I_B → I<sub>B</sub>."""
    base, _, sub = name.partition("_")
    return f"{base}<sub>{sub}</sub>" if sub else base


def math_name(name: str) -> str:
    base, _, sub = name.partition("_")
    return f"${base}_{{{sub}}}$" if sub else name


# ------------------------------------------------------------------------------- the trials
class Source:
    """What the table reads from the trials (padpage.PageSource; tests give a fake)."""

    def trials(self) -> list[int]: ...
    def trace(self, i: int, channel: str) -> tuple[np.ndarray, float] | None: ...      # (y, fs)
    def unit(self, channel: str) -> str: ...
    def segments(self, i: int) -> list[tuple[str, float, float, dict]]: ...            # (letter, t0, t1, part)
    def spikes(self, i: int, units: list | None = None) -> np.ndarray | None: ...   # s; None: none found yet
    def spike_channel(self) -> str: ...
    def group(self, i: int) -> tuple[str, int]: ...
    def stim_units(self) -> tuple[str, float]: ...
    def duration(self, i: int) -> float: ...


def sent_segments(stimuli: list[dict] | None,
                  onsets: list[float | None] | None = None) -> list[tuple[str, float, float, dict]]:
    """The segments of every stimulus sent in a trial: [(name, start, end, part)], s from the
    trial's start. A Simple Stimulus (or any older shape) is the Complex Stimulus it makes:
    a baseline for its delay, then its shape. The first stimulus's segments are A, B, …; when
    more were sent (Repeat, Raise), the second's are 2A, 2B, …, the third's 3A … `onsets`:
    where each one really started (s), lined up with the recording, None to go by its note.
    [] when nothing was sent."""
    out, n_sent = [], 0
    for k, sent in enumerate(stimuli or ()):
        st = sent.get("settings")
        if not st:
            continue
        try:
            s = stimmod.to_segments(stimmod.Stimulus.from_dict(st))
            rate = float(sent["rate"])
        except (ValueError, KeyError, TypeError):
            continue
        n_sent += 1
        prefix = "" if n_sent == 1 else str(n_sent)
        onset = onsets[k] if onsets is not None and k < len(onsets) else None
        start = float(sent.get("t", 0.0)) if onset is None else onset
        t0, at = s.delay_ms, stimmod.samples(s.delay_ms, rate)
        for j, p in enumerate(s.parts):
            try:
                n = len(stimmod._part_wave(p, rate, t0))
            except (ValueError, OSError):
                n = stimmod.span(t0, stimmod._part_ms(p), rate)
            out.append((prefix + stimmod.letter(j), start + at / rate, start + (at + n) / rate, p))
            at += n
            t0 += n * 1000.0 / rate if p.get("kind") == "file" else stimmod._part_ms(p)
    return out


def found_segments(y, fs: float, min_ms: float = 1.0) -> list[tuple[str, float, float, dict]]:
    """Segments read off a stimulus channel, for a trial that didn't save what it sent (one
    imported from LabChart, say): the trace cut wherever it jumps (by more than a third of its
    biggest swing from rest), each stretch a base where it sits at rest, else a step at its
    level: [(A, start, end, part), (B, …)…] in s, as sent_segments. Jumps within min_ms are one
    edge; neighbours at the same level are one segment. [] if it never jumps."""
    y = np.nan_to_num(np.asarray(y, float))
    rest = SM.rest_level(y)
    if rest is None or y.size < 3:
        return []
    swing = float(np.max(np.abs(y - rest)))
    if not swing > 0:
        return []
    jumps = np.flatnonzero(np.abs(np.diff(y)) > swing / 3) + 1
    near = max(1, int(round(min_ms / 1000 * fs)))
    cuts: list[int] = []
    for j in jumps:                                     # an edge over a few samples: one cut, where it ends
        if cuts and j - cuts[-1] <= near:
            cuts[-1] = int(j)
        else:
            cuts.append(int(j))
    if not cuts:
        return []
    edges = [0] + cuts + [y.size]
    stretches = []                                      # (start, end, level), same-level neighbours joined
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        level = float(np.median(y[a:b]))
        if stretches and abs(level - stretches[-1][2]) < 0.05 * swing:
            a0, _, _ = stretches[-1]
            stretches[-1] = (a0, b, float(np.median(y[a0:b])))
        else:
            stretches.append((a, b, level))
    if len(stretches) < 2:
        return []
    out = []
    for k, (a, b, level) in enumerate(stretches):
        at_rest = abs(level - rest) < 0.05 * swing
        part = {"kind": "base" if at_rest else "step", "level": 0.0 if at_rest else round(level, 6),
                "dur": (b - a) / fs * 1000, "found": True}
        out.append((stimmod.letter(k), a / fs, b / fs, part))
    return out


_SENT_KEY = re.compile(r"^(\d+)([A-Z]+)$")


def send_of(name: str) -> tuple[int, str]:
    """(which stimulus sent, the segment's letter) a segment's name stands for: B is the first
    stimulus's B, 3B the third's."""
    m = _SENT_KEY.match(name)
    return (int(m[1]), m[2]) if m else (1, name)


def send_at(segs: list, t: float | None) -> int:
    """Which stimulus sent a row starting at t (s) belongs to: the last one started by then
    (the first, for a whole trial)."""
    out = 1
    if t is None:
        return out
    for name, t0, _t1, _p in segs:
        n = send_of(name)[0]
        if n > out and t0 <= t + 1e-9:
            out = n
    return out


def scoped(segs: list, send: int) -> list:
    """The segments as a row of stimulus `send` sees them: a plain letter (B) is that
    stimulus's own segment, a numbered one (3B) the one it names."""
    if send <= 1:
        return segs
    own = [(send_of(s[0])[1], *s[1:]) for s in segs if send_of(s[0])[0] == send]
    return own + [s for s in segs if send_of(s[0])[0] != 1]


def row_segments(segs: list, r: "Row") -> list:
    """The segments as row r sees them (see scoped)."""
    return scoped(segs, send_at(segs, r.t0))


@dataclass(frozen=True)
class Row:
    trial: int
    t0: float | None = None          # the row's stretch, s; None: the whole trial
    t1: float | None = None
    label: str = ""                  # B, B·3 (the third pulse), …

    def key(self) -> tuple:
        return (self.trial, self.t0, self.t1, self.label)


def pulses(letter: str, t0: float, t1: float, part: dict) -> list[Row]:
    """The pulses of a pulse (or train) segment, each from its start to the next one's."""
    if part.get("kind") != "pulse":
        return []
    n = max(1, int(part.get("count", 1)))
    every = float(part.get("interval", 0.0)) / 1000 if n > 1 else (t1 - t0)
    out = []
    for k in range(n):
        a = t0 + k * every
        b = min(t1, a + every) if k < n - 1 else t1
        if b > a:
            out.append(Row(-1, a, b, f"{letter}·{k + 1}" if n > 1 else letter))
    return out


class Trace(np.ndarray):
    """A stretch of a channel in a Python column: the samples, with .t (ms) and .fs."""

    def __new__(cls, y, t, fs):
        obj = np.asarray(y, float).view(cls)
        obj.t, obj.fs = np.asarray(t, float), fs
        return obj

    def __array_finalize__(self, obj):
        self.t = getattr(obj, "t", None)
        self.fs = getattr(obj, "fs", None)


def fit_exp(y, t=None) -> SimpleNamespace:
    """An exponential through a Trace (or y against t, ms): .tau (ms), .A, .C, .r2."""
    t = getattr(y, "t", None) if t is None else t
    if t is None:
        raise ValueError("fit_exp needs times: give it a trace, or y and t")
    y, t = np.asarray(y, float), np.asarray(t, float)
    f = fitting.fit("exp", t - t[0], y)
    A, tau, C = f.params
    return SimpleNamespace(A=float(A), tau=float(tau), C=float(C), r2=f.r2)


def fit_line(x, y) -> SimpleNamespace:
    f = fitting.fit("poly", np.asarray(x, float), np.asarray(y, float), 1)
    return SimpleNamespace(intercept=float(f.params[0]), slope=float(f.params[1]), r2=f.r2)


# ------------------------------------------------------------------------------- the table
class Table:
    """The worked-out table: rows, and each column's values, unit and error."""

    def __init__(self, rows: list[Row]):
        self.rows = rows
        self.values: dict[str, np.ndarray] = {}
        self.text: dict[str, list[str]] = {}
        self.units: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        self.windows: dict[str, list] = {}      # measure columns: the stretch each row measured
        self.skipped: list[bool] = [False] * len(rows)   # rows of unticked trials: listed, not measured

    def column(self, name: str) -> np.ndarray:
        return self.values.get(name, np.full(len(self.rows), np.nan))

    def trials(self) -> list[int]:
        return [r.trial for r in self.rows]


class Pad:
    """The DataPad's rows, columns and how to work them out."""

    def __init__(self):
        self.columns: list[Column] = []
        self.rows_mode = "trial"
        self.rows_seg = ""                       # segment rows: one letter, or "" for every segment
        self.manual: list[dict] = []             # by-hand rows: {"trial", "t0", "t1"}
        self.version = 0                         # bumped when the recordings (or spikes) change
        self._skip: set[int] = set()
        self._cache: dict[str, tuple] = {}

    def names(self) -> list[str]:
        return [c.name for c in self.columns]

    def get(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    def data_changed(self) -> None:
        self.version += 1

    # -- the recipe -----------------------------------------------------------------------
    def state(self) -> dict:
        return {"columns": [c.to_dict() for c in self.columns], "rows": self.rows_mode,
                "rows_seg": self.rows_seg, "manual": list(self.manual)}

    def restore(self, state: dict | None) -> None:
        state = state or {}
        self.columns = []
        for d in state.get("columns", []):
            try:
                self.columns.append(Column.from_dict(d))
            except TypeError:
                continue
        self.rows_mode = state.get("rows", "trial") if state.get("rows") in ROWS else "trial"
        self.rows_seg = state.get("rows_seg", "")
        self.manual = list(state.get("manual", []))
        self._cache.clear()

    # -- rows ---------------------------------------------------------------------------
    def rows(self, src: Source) -> list[Row]:
        trials = list(src.trials())
        if self.rows_mode == "manual":
            ok = set(trials)
            return [Row(int(m["trial"]), float(m["t0"]), float(m["t1"]), f"{k + 1}")
                    for k, m in enumerate(self.manual) if int(m["trial"]) in ok]
        if self.rows_mode == "trial":
            return [Row(i) for i in trials]
        out = []
        for i in trials:
            for letter, t0, t1, part in src.segments(i):
                mine = not self.rows_seg or send_of(letter)[1] == self.rows_seg   # B: B of every stimulus sent
                if self.rows_mode == "segment" and mine:
                    out.append(Row(i, t0, t1, letter))
                elif self.rows_mode == "pulse" and mine:
                    out += [Row(i, r.t0, r.t1, r.label) for r in pulses(letter, t0, t1, part)]
        return out

    # -- working it out ---------------------------------------------------------------------
    def compute(self, src: Source, skip: set[int] | None = None) -> Table:
        """The table. Rows of the trials in `skip` (unticked) are listed but not measured."""
        rows = self.rows(src)
        tab = Table(rows)
        self._skip = skip = set(skip or ())
        tab.skipped = [r.trial in skip for r in rows]
        sig = json.dumps([r.key() for r in rows]) + f"|{self.version}|{sorted(skip)}"
        segs = {}
        for r in rows:
            if r.trial not in segs:
                segs[r.trial] = src.segments(r.trial)
        # where each stimulus was found on the stimulus channel moves its segments
        sig += json.dumps([[(s[0], round(s[1], 6), round(s[2], 6)) for s in segs[i]] for i in sorted(segs)])
        live = set()
        for c in self.columns:
            key = json.dumps(c.to_dict(), sort_keys=True) + sig
            cached = self._cache.get(c.name)
            live.add(c.name)
            try:
                if c.kind in ("measure", "level", "duration", "info") and cached and cached[0] == key:
                    _, values, text, unit, wins = cached
                    tab.values[c.name] = values
                    if text is not None:
                        tab.text[c.name] = text
                    tab.units[c.name] = unit
                    if wins is not None:
                        tab.windows[c.name] = wins
                else:
                    self._compute(c, src, tab, segs)
                    self._cache[c.name] = (key, tab.values[c.name], tab.text.get(c.name),
                                           tab.units.get(c.name, ""), tab.windows.get(c.name))
            except Exception as e:                  # a student's formula: the column says why
                tab.values[c.name] = np.full(len(rows), np.nan)
                tab.errors[c.name] = _why(e)
                self._cache.pop(c.name, None)
            if c.unit:
                tab.units[c.name] = c.unit
            tab.units.setdefault(c.name, "")
        for k in set(self._cache) - live:
            self._cache.pop(k)
        return tab

    def _window(self, c: Column, r: Row, segs: list) -> tuple[float, float] | None:
        if c.over == "row":
            return (r.t0, r.t1) if r.t0 is not None else (0.0, float("inf"))
        return c.sel.window({s[0]: (s[1], s[2]) for s in segs}, [s[0] for s in segs])

    def _compute(self, c: Column, src: Source, tab: Table, segs: dict) -> None:
        rows, out = tab.rows, np.full(len(tab.rows), np.nan)
        if c.kind == "measure":
            wins = []
            spiky = c.key in D.SPIKE_KEYS
            channel = src.spike_channel() if spiky else c.channel
            found = False
            used = 0
            for k, r in enumerate(rows):
                w = self._window(c, r, row_segments(segs[r.trial], r))
                wins.append(w)
                if r.trial in self._skip:
                    continue
                used += 1
                st = src.spikes(r.trial, c.units) if spiky else None
                found = found or st is not None
                if c.over == "window" and c.sel.is_phase():
                    out[k] = measure_phase(src, r.trial, channel, c.key, c.sel, row_segments(segs[r.trial], r), st)
                else:
                    out[k] = measure_window(src, r.trial, channel, c.key, w, st)
            if spiky and used and not found:
                raise ValueError("No spikes found yet: find them in the Spikes panel (the rail on the left)")
            missing = sum(w is None for w, r in zip(wins, rows) if r.trial not in self._skip)
            if used and missing == used:
                raise ValueError(_no_window(c))
            if missing:
                tab.errors[c.name] = f"{missing} row(s) have no {c.sel.label()}: " + _no_window(c)
            tab.windows[c.name] = wins
            tab.units[c.name] = D.unit_of(c.key, src.unit(channel)) if channel else ""
        elif c.kind in ("level", "duration"):
            units, per_v = src.stim_units()
            for k, r in enumerate(rows):
                if r.trial in self._skip:
                    continue
                sg = row_segments(segs[r.trial], r)
                order = [s[0] for s in sg]
                got = c.sel.letters(order) if c.kind == "level" else None
                if c.kind == "level":
                    if got:
                        part = next(s[3] for s in sg if s[0] == got[0])
                        out[k] = float(part.get("level", 0.0)) * per_v
                else:
                    w = c.sel.window({s[0]: (s[1], s[2]) for s in sg}, order)
                    out[k] = (w[1] - w[0]) * 1000 if w else np.nan
            tab.units[c.name] = units if c.kind == "level" else "ms"
        elif c.kind == "info":
            if c.key == "group":
                tab.text[c.name] = [src.group(r.trial)[0] for r in rows]
            for k, r in enumerate(rows):
                if r.trial in self._skip:
                    continue
                if c.key == "trial":
                    out[k] = r.trial + 1
                elif c.key == "n_in_group":
                    out[k] = src.group(r.trial)[1]
                elif c.key == "start":
                    out[k] = (r.t0 or 0.0) * 1000
            tab.units[c.name] = INFO.get(c.key, ("", ""))[1]
        elif c.kind == "formula":
            env = dict(FUNCS)
            env.update(tab.values)
            v = eval(compile(c.expr or "nan", f"<{c.name}>", "eval"), {"__builtins__": {}, "nan": np.nan}, env)
            out = np.broadcast_to(np.asarray(v, float), (len(rows),)).copy()
        elif c.kind == "python":
            out = self._python(c, src, tab, segs)
        if self._skip:                             # unticked trials aren't analyzed
            out[[r.trial in self._skip for r in rows]] = np.nan
        tab.values[c.name] = out

    def _python(self, c: Column, src: Source, tab: Table, segs: dict) -> np.ndarray:
        rows = tab.rows
        fn = _function(c)
        cols = dict(tab.values)
        if c.scope == "column":
            got = fn(SimpleNamespace(**cols), None, None, None, None, None)
            return np.broadcast_to(np.asarray(got, float), (len(rows),)).copy()
        out = np.full(len(rows), np.nan)
        errs = []
        live = 0
        for k, r in enumerate(rows):
            i = r.trial
            if i in self._skip:
                continue
            live += 1
            row = SimpleNamespace(**{n: float(v[k]) for n, v in cols.items()}, trial=i + 1,
                                  t0=(r.t0 or 0.0) * 1000, t1=(r.t1 * 1000 if r.t1 is not None else None))
            seen = row_segments(segs[i], r)
            order = [s[0] for s in seen]
            spans = {s[0]: (s[1], s[2]) for s in seen}

            def pick(sel, _r=r, _o=order, _s=spans):
                sel = sel.sel if isinstance(sel, _SegBuilder) else sel
                if sel is None:
                    return (_r.t0, _r.t1) if _r.t0 is not None else None
                return sel.window(_s, _o)

            def trace(channel: str, sel=None, _i=i, _pick=pick):
                got = src.trace(_i, channel)
                if got is None:
                    raise ValueError(f"trial {_i + 1} has no channel {channel}")
                y, fs = got
                t = np.arange(len(y)) / fs
                w = _pick(sel)
                if w is not None:
                    sl = D.window(t, w[0], w[1] - 0.5 / fs)
                    y, t = y[sl], t[sl]
                return Trace(y, t * 1000, fs)

            def spikes(sel=None, _i=i, _pick=pick):
                st = src.spikes(_i)
                st = np.array([]) if st is None else np.asarray(st, float)
                w = _pick(sel)
                if w is not None:
                    st = st[(st >= w[0]) & (st <= w[1])]
                return st * 1000

            try:
                out[k] = float(fn(row, trace, spikes, i + 1, None, None))
            except Exception as e:
                errs.append(f"row {k + 1}: {_why(e)}")
        if errs and len(errs) == live:
            raise RuntimeError(errs[0])
        if errs:
            tab.errors[c.name] = f"{len(errs)} row(s) failed. " + errs[0]
        return out


def cycle_period(part: dict) -> float | None:
    """How long one cycle of a segment lasts (s): a sine's, triangle's or square's 1/freq, a
    pulse train's interval. None for segments that don't repeat."""
    kind = part.get("kind")
    if kind in ("sine", "triangle", "square") and float(part.get("freq", 0) or 0) > 0:
        return 1.0 / float(part["freq"])
    if kind == "pulse" and int(part.get("count", 1)) > 1 and float(part.get("interval", 0) or 0) > 0:
        return float(part["interval"]) / 1000
    return None


def cycles(sel: Selection, segs: list) -> tuple[list[float], float] | None:
    """(the start of every whole cycle, the period) of the segment(s) `sel` names, or None."""
    order = [s[0] for s in segs]
    got = sel.letters(order)
    if not got:
        return None
    first = next(s for s in segs if s[0] == got[0])
    period = cycle_period(first[3])
    if period is None:
        return None
    a, b = first[1], next(s for s in segs if s[0] == got[-1])[2]
    n = int(np.floor((b - a) / period + 1e-6))
    return [a + k * period for k in range(n)], period


def measure_phase(src: Source, i: int, channel: str, key: str, sel: Selection, segs: list,
                  spike_times=None) -> float:
    """A measurement of the same part of every cycle (degrees ph0–ph1), averaged over the cycles."""
    got = cycles(sel, segs)
    if got is None:
        return float("nan")
    starts, period = got
    vals = []
    for s0 in starts:
        a, b = s0 + sel.ph0 / 360 * period, s0 + sel.ph1 / 360 * period
        vals.append(measure_window(src, i, channel, key, (a, b), spike_times))
    vals = np.asarray(vals, float)
    return float(np.nanmean(vals)) if np.isfinite(vals).any() else float("nan")


def measure_sel(src: Source, i: int, channel: str, key: str, sel: Selection, segs: list,
                spike_times=None) -> float:
    """A measurement over a selection in trial i: a window, a point or a phase of every cycle."""
    if sel.is_phase():
        return measure_phase(src, i, channel, key, sel, segs, spike_times)
    w = sel.window({s[0]: (s[1], s[2]) for s in segs}, [s[0] for s in segs])
    return measure_window(src, i, channel, key, w, spike_times)


def measure_window(src: Source, i: int, channel: str, key: str, w, spike_times=None) -> float:
    if w is None:
        return float("nan")
    got = src.trace(i, channel)
    if got is None:
        return float("nan")
    y, fs = got
    if w[1] == w[0]:                               # a point: the sample there
        k = int(round(w[0] * fs))
        return float(y[k]) if 0 <= k < len(y) and key not in D.SPIKE_KEYS else float("nan")
    t = np.arange(len(y)) / fs
    sl = D.window(t, w[0], min(w[1], t[-1] + 1 / fs) - 0.5 / fs)   # half-open: the next segment's first sample isn't in it
    if sl.stop - sl.start < 1:
        return float("nan")
    return D.measure(key, t[sl], y[sl], fs, spike_times)


_ENV = {"np": np, "seg": lambda a, b=None: _SegBuilder(a, b),
        "time": lambda a_ms, b_ms: Selection.time(a_ms / 1000, b_ms / 1000),
        "fit_exp": fit_exp, "fit_line": fit_line, "nan": float("nan"), **FUNCS}


def _function(c: Column):
    body = c.expr.strip() or "return nan"
    if "return" not in body:
        lines = body.splitlines()
        lines[-1] = "return " + lines[-1]
        body = "\n".join(lines)
    args = "col, _1, _2, _3, _4, _5" if c.scope == "column" else "row, trace, spikes, trial, _4, _5"
    src = f"def _column({args}):\n" + "\n".join("    " + ln for ln in body.splitlines())
    env = dict(_ENV)

    def measure(key, y):
        t = np.asarray(getattr(y, "t", np.arange(len(y))), float) / 1000
        return D.measure(key, t, np.asarray(y, float), getattr(y, "fs", 1.0) or 1.0)
    env["measure"] = measure
    exec(compile(src, f"<{c.name}>", "exec"), env)
    return env["_column"]


def _no_window(c: Column) -> str:
    if c.sel.is_time():
        return "The window is past the end of the trial. Set it again on the preview."
    return (f"Segment {c.sel.first} isn't in these trials (they weren't sent from Complex Stimulus). "
            "Measure over the row (the whole trial), or set a stretch of time on the preview.")


def _why(e: Exception) -> str:
    if isinstance(e, NameError):
        msg = str(e).replace("name ", "")
        return msg[:1].upper() + msg[1:] + " (check the column names)"
    if isinstance(e, SyntaxError):
        return f"Can't read line {e.lineno}: {e.msg}"
    if isinstance(e, ValueError):               # NEWT's own messages say what to do
        return str(e)
    return f"{type(e).__name__}: {e}"


# ------------------------------------------------------------------------------- summaries
def summarize(tab: Table, src: Source, by: str) -> list[tuple[str, dict[str, tuple[float, float, int]]]]:
    """Mean, SEM and n of every number column, for all rows ("all"), each group ("group") or
    each value of a column (its name: "I_B" gives the I–V points)."""
    if by == "group":
        keys = [src.group(r.trial)[0] for r in tab.rows]
    elif by in tab.values:
        keys = [fmt(v) for v in tab.values[by]]
    elif by in tab.text:
        keys = list(tab.text[by])
    else:
        keys = ["all" for _ in tab.rows]
    names = list(dict.fromkeys(keys))
    if by in tab.values:
        names.sort(key=lambda k: float(k) if k else math.inf)
    out = []
    for g in names:
        m = np.array([k == g for k in keys])
        stats = {}
        for c, v in tab.values.items():
            x = v[m]
            x = x[np.isfinite(x)]
            n = int(x.size)
            sem = float(x.std(ddof=1) / math.sqrt(n)) if n > 1 else np.nan
            if n > 1 and abs(sem) <= 1e-9 * max(1e-300, abs(float(x.mean()))):
                sem = 0.0
            stats[c] = (float(x.mean()) if n else np.nan, sem, n)
        out.append((g, stats))
    return out


def fmt(v: float, digits: int = 4, places: bool = False) -> str:
    """A number for the table: `digits` significant figures (written out in full, so 0.0500
    keeps its zeros), or with places, `digits` decimal places. Very big or small numbers
    go in scientific notation."""
    if v is None or not np.isfinite(v):
        return ""
    digits = max(0 if places else 1, int(digits))
    if places:
        return f"{v:.{digits}f}"
    a = abs(v)
    if a and (a >= 1e6 or a < 1e-4):
        return f"{v:.{digits - 1}e}"
    if a == 0:
        return "0" if digits == 1 else "0." + "0" * (digits - 1)
    exp = int(np.floor(np.log10(a)))
    dec = digits - 1 - exp                 # negative: round to tens, hundreds… (108 to 2 → 110)
    r = round(v, dec)
    if r and np.floor(np.log10(abs(r))) > exp:     # 9.99 to 2 rounds up to 10: one place fewer
        dec -= 1
        r = round(v, dec)
    return f"{r:.{max(0, dec)}f}"


def to_csv(tab: Table, cols: list[Column], src: Source | None = None) -> list[list[str]]:
    head = ["Trial", "Row"] + [f"{c.shown()} ({tab.units.get(c.name, '')})" if tab.units.get(c.name)
                               else c.shown() for c in cols]
    out = [head]
    for k, r in enumerate(tab.rows):
        line = [str(r.trial + 1), r.label]
        for c in cols:
            line.append(tab.text[c.name][k] if c.name in tab.text else c.fmt(tab.column(c.name)[k]))
        out.append(line)
    return out
