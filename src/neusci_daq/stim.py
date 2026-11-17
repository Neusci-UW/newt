"""Stimulus shapes for the analog outputs, and how they become a waveform.

A Stimulus describes one stimulus (a pulse train, a ramp, a custom shape...).
`waveform(rate)` turns it into the voltages written to ao0/ao1, one per
output sample. Every waveform ends at 0 V, because the output keeps its last
value after a stimulus finishes.

Shapes:
  pulse   one pulse or a train (optionally biphasic: +A then -A)
  step    a constant level for a while
  ramp    a straight line from one level to another
  sine    a sine wave (amplitude, offset, frequency)
  custom  any shape, as straight-line segments: (duration, start V, end V)
  file    voltages from a CSV/text file: one column (at a given rate) or
          two columns (time in s, volts), resampled to the output rate
  segments  the one the Stimulator builds now: a row of parts, left to right
          (baseline, step, ramp, pulse or train, sine, file). Each part's level and
          duration can change from trial to trial: "+0.5" adds 0.5 each trial, and
          "0.5, 1, 2" gives each trial its own value. The older shapes above load as
          segments (to_segments) and are kept so saved settings still describe themselves.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

import numpy as np

# USB-6003 analog output (NI spec): 2 channels, 16-bit, +/-10 V, 5 kS/s.
AO_MAX_RATE = 5000.0
AO_RANGE = (-10.0, 10.0)
AO_CHANNELS = ("ao0", "ao1")
MAX_DURATION_S = 60.0

SHAPES = {
    "pulse": "Pulse / train",
    "step": "Step",
    "ramp": "Ramp",
    "sine": "Sine",
    "custom": "Custom segments",
    "file": "From file",
    "segments": "Segments",
}

# A segment's kinds, in the order the Stimulator offers them.
SEG_KINDS = {"base": "Baseline", "step": "Step", "ramp": "Ramp", "pulse": "Pulse", "sine": "Sine",
             "triangle": "Triangle", "square": "Square", "chirp": "Chirp", "noise": "Noise",
             "exp": "Exponential", "alpha": "Alpha", "points": "Custom points", "file": "From file"}


def samples(ms: float, rate: float) -> int:
    """Number of output samples for `ms` milliseconds, rounding halves up (0.3 ms at 5 kS/s -> 2)."""
    return int(math.floor(ms * rate / 1000.0 + 0.5 + 1e-9))


def span(t0_ms: float, ms: float, rate: float) -> int:
    """Samples from `t0_ms` to `t0_ms + ms`, each end rounded on its own, so rounding
    never piles up from one segment or pulse to the next."""
    return samples(t0_ms + ms, rate) - samples(t0_ms, rate)


def _train(one: np.ndarray, count: int, interval_ms: float, t0_ms: float, rate: float) -> np.ndarray:
    """`count` copies of `one`, each starting at t0 + i × interval on the sample grid."""
    first = samples(t0_ms, rate)
    starts = [samples(t0_ms + i * interval_ms, rate) - first for i in range(count)]
    out = np.zeros(starts[-1] + len(one))
    for s0 in starts:
        out[s0:s0 + len(one)] = one
    return out


def new_part(kind: str, level: float | None = None, dur: float | None = None) -> dict:
    """A segment of `kind` with sensible starting values. Its level and duration mean:
    base 0 V for dur ms; step level V for dur ms; ramp from `start` to level V over dur ms;
    pulse level V, dur ms wide, `count` of them every `interval` ms; sine level V
    amplitude around `offset` at `freq` Hz for dur ms; triangle and square level V at `freq`
    Hz (square is on for the first half of each cycle); chirp level V amplitude sweeping from
    `freq` to `freq2` Hz (`log` for a logarithmic sweep) while its amplitude goes in a straight
    line from level to `level2` V; noise Gaussian with level V standard
    deviation (`seed` 0: new noise every time, else the same noise each time); exp a jump to
    level V decaying with `tau` ms; alpha a rise and fall peaking at level V at `tau` ms
    (an EPSC's shape); points straight lines (or steps, `join`) through [ms, V] `points`,
    each V × level; file the file's volts × level."""
    p = {"kind": kind, "level": 0.0, "dur": 10.0, "d_level": "", "d_dur": "", "offset": 0.0}
    p.update({"step": {"level": 1.0, "dur": 10.0},
              "ramp": {"level": 1.0, "dur": 20.0, "start": 0.0},
              "pulse": {"level": 1.0, "dur": 1.0, "count": 1, "interval": 100.0, "biphasic": False},
              "sine": {"level": 1.0, "dur": 100.0, "freq": 10.0, "offset": 0.0},
              "triangle": {"level": 1.0, "dur": 100.0, "freq": 10.0},
              "square": {"level": 1.0, "dur": 100.0, "freq": 10.0},
              "chirp": {"level": 1.0, "level2": 1.0, "dur": 200.0, "freq": 5.0, "freq2": 50.0, "log": False},
              "noise": {"level": 0.2, "dur": 100.0, "seed": 0},
              "exp": {"level": 1.0, "dur": 50.0, "tau": 10.0},
              "alpha": {"level": 1.0, "dur": 50.0, "tau": 5.0},
              "points": {"level": 1.0, "dur": 40.0, "points": [[0.0, 0.0], [10.0, 1.0], [20.0, -0.5], [40.0, 0.0]],
                         "join": "line"},
              "file": {"level": 1.0, "dur": 0.0, "file": "", "file_rate": 0.0}}.get(kind, {}))
    if level is not None:
        p["level"] = float(level)
    if dur is not None:
        p["dur"] = float(dur)
    return p


def _default_parts():
    return [new_part("base", dur=10.0), new_part("pulse"), new_part("base", dur=30.0)]


def letter(i: int) -> str:
    """A segment's name: A, B, … Z, AA, AB …"""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def parse_per_trial(text: str) -> tuple[str, float | list[float]] | None:
    """A "per trial" box: None when blank, ("step", 0.5) for "+0.5" (added each trial),
    ("list", [0.5, 1, 2]) for "0.5, 1, 2" (one value per trial). Raises ValueError."""
    t = (text or "").strip().replace("−", "-")
    if not t:
        return None
    bits = [b for b in re.split(r"[,;\s]+", t) if b]
    try:
        nums = [float(b) for b in bits]
    except ValueError:
        raise ValueError(f"\"{text.strip()}\" isn't a step like +0.5 or a list like 0.5, 1, 2.") from None
    if len(nums) == 1:
        return ("step", nums[0])
    return ("list", nums)


def per_trial(base: float, spec: str, t: int) -> float:
    """The value for trial t (0 = the first): base, base + t × step, or the list's t-th."""
    got = parse_per_trial(spec)
    if got is None:
        return float(base)
    kind, v = got
    if kind == "step":
        return float(base) + v * t
    if t >= len(v):
        raise ValueError(f"its list has {len(v)} values, so there's no value for trial {t + 1}.")
    return float(v[t])


def format_per_trial(got: tuple[str, float | list[float]] | None, k: float = 1.0) -> str:
    """The per-trial box's text for a parsed value, each number × k."""
    if got is None:
        return ""
    kind, v = got
    if kind == "step":
        return f"{v * k:+g}"
    return ", ".join(f"{x * k:g}" for x in v)


def _default_segments():
    return [{"duration_ms": 10.0, "start_v": 0.0, "end_v": 1.0},
            {"duration_ms": 10.0, "start_v": 1.0, "end_v": 1.0},
            {"duration_ms": 10.0, "start_v": 1.0, "end_v": 0.0}]


@dataclass
class Stimulus:
    shape: str = "segments"
    delay_ms: float = 0.0          # 0 V before the stimulus starts
    amplitude_v: float = 1.0       # pulse, step, sine
    width_ms: float = 1.0          # pulse
    count: int = 1                 # pulse: number of pulses
    interval_ms: float = 100.0     # pulse: start of one pulse to start of the next
    biphasic: bool = False         # pulse: +A for width, then -A for width
    duration_ms: float = 100.0     # step, ramp, sine
    start_v: float = 0.0           # ramp
    end_v: float = 1.0             # ramp
    offset_v: float = 0.0          # sine; segments: the whole stimulus sits on it
    freq_hz: float = 10.0          # sine
    segments: list = field(default_factory=_default_segments)   # custom
    file: str = ""                 # file
    file_rate: float = 0.0         # file: rate of a one-column file (0 = output rate)
    parts: list = field(default_factory=_default_parts)       # segments: see new_part

    # -- description -----------------------------------------------------
    def describe(self, units: str = "V", scale: float = 1.0) -> str:
        """Short text for comments and the status bar, e.g. 'Pulse 2 V, 1 ms ×5 @ 100 ms'.
        Levels are shown as volts × `scale` in `units` (the [stimulator] units setting)."""
        lv = lambda v: f"{v * scale:g}"
        if self.shape == "segments":
            text = " · ".join(_describe_part(p, units, scale) for p in self.parts) or "No segments"
            return text + (f" (all on {lv(self.offset_v)} {units})" if self.offset_v else "")
        d = f"{self.delay_ms:g} ms delay, " if self.delay_ms else ""
        if self.shape == "pulse":
            s = f"{'Biphasic pulse' if self.biphasic else 'Pulse'} {lv(self.amplitude_v)} {units}, {self.width_ms:g} ms"
            if self.count > 1:
                s += f" ×{self.count} every {self.interval_ms:g} ms"
        elif self.shape == "step":
            s = f"Step {lv(self.amplitude_v)} {units} for {self.duration_ms:g} ms"
        elif self.shape == "ramp":
            s = f"Ramp {lv(self.start_v)}→{lv(self.end_v)} {units} over {self.duration_ms:g} ms"
        elif self.shape == "sine":
            s = f"Sine {lv(self.amplitude_v)} {units}, {self.freq_hz:g} Hz for {self.duration_ms:g} ms"
            if self.offset_v:
                s += f", offset {lv(self.offset_v)} {units}"
        elif self.shape == "custom":
            s = f"Custom ({len(self.segments)} segments)"
        else:
            s = f"File {Path(self.file).name or '(none)'}"
        return d + s

    # -- waveform ----------------------------------------------------------
    def waveform(self, rate: float) -> np.ndarray:
        """Voltages, one per output sample at `rate`, ending at 0 V. Raises ValueError."""
        if not 0 < rate <= AO_MAX_RATE:
            raise ValueError(f"Output rate must be between 0 and {AO_MAX_RATE:,.0f} S/s (USB-6003 limit).")
        if self.shape not in SHAPES:
            raise ValueError(f"Unknown stimulus shape {self.shape!r}.")
        n = lambda ms: samples(ms, rate)
        if self.delay_ms < 0:
            raise ValueError("Delay can't be negative.")
        parts = [np.zeros(n(self.delay_ms))]
        dt_ms = 1000.0 / rate

        if self.shape == "pulse":
            w = n(self.width_ms)
            if self.width_ms * rate / 1000.0 < 1 - 1e-9:      # never stretch a pulse that's too short
                raise ValueError(f"Pulse width {self.width_ms:g} ms is shorter than one output sample "
                                 f"({dt_ms:g} ms at {rate:,.0f} S/s). Let the stimulus isolator set "
                                 "short widths, or use a wider pulse.")
            if self.count < 1:
                raise ValueError("Number of pulses must be at least 1.")
            one = np.full(w, float(self.amplitude_v))
            if self.biphasic:
                one = np.concatenate([one, -one])
            gap = n(self.interval_ms) - len(one)
            if self.count > 1 and (gap < 0 or self.interval_ms * rate / 1000.0 < len(one) - 1e-9):
                raise ValueError(f"Interval {self.interval_ms:g} ms is shorter than one pulse "
                                 f"({len(one) * dt_ms:g} ms). Use a longer interval.")
            parts.append(_train(one, self.count, self.interval_ms, self.delay_ms, rate))
        elif self.shape == "step":
            k = self._need(self.duration_ms, "Duration", dt_ms, rate)
            parts.append(np.full(k, float(self.amplitude_v)))
        elif self.shape == "ramp":
            k = self._need(self.duration_ms, "Duration", dt_ms, rate)
            parts.append(np.linspace(self.start_v, self.end_v, k))
        elif self.shape == "sine":
            k = self._need(self.duration_ms, "Duration", dt_ms, rate)
            if self.freq_hz <= 0 or self.freq_hz > rate / 2:
                raise ValueError(f"Frequency must be between 0 and {rate / 2:,.0f} Hz "
                                 f"(half the output rate) to be drawn correctly.")
            t = np.arange(k) / rate
            parts.append(self.offset_v + self.amplitude_v * np.sin(2 * np.pi * self.freq_hz * t))
        elif self.shape == "segments":
            parts_ = self.at_trial(0).parts
            if not parts_:
                raise ValueError("Add at least one segment.")
            t0 = self.delay_ms
            for i, p in enumerate(parts_):
                try:
                    w = _part_wave(p, rate, t0)
                    parts.append(w)
                    t0 += len(w) * 1000.0 / rate if p.get("kind") == "file" else _part_ms(p)
                except ValueError as e:
                    raise ValueError(f"{letter(i)} ({SEG_KINDS.get(p.get('kind'), '?')}): {e}") from None
            if self.offset_v:                            # the whole stimulus, baselines too
                parts = [w + self.offset_v for w in parts]
        elif self.shape == "custom":
            if not self.segments:
                raise ValueError("Add at least one segment.")
            t0 = self.delay_ms
            for i, seg in enumerate(self.segments, 1):
                k = span(t0, float(seg["duration_ms"]), rate)
                t0 += float(seg["duration_ms"])
                if float(seg["duration_ms"]) * rate / 1000.0 < 1 - 1e-9:
                    raise ValueError(f"Segment {i} is shorter than one output sample ({dt_ms:g} ms).")
                parts.append(np.linspace(float(seg["start_v"]), float(seg["end_v"]), k, endpoint=False)
                             if seg["start_v"] != seg["end_v"] else np.full(k, float(seg["start_v"])))
        else:
            parts.append(load_wave_file(self.file, rate, self.file_rate or rate))

        wave = np.concatenate(parts + [np.zeros(1)])     # always end at 0 V
        if len(wave) / rate > MAX_DURATION_S:
            raise ValueError(f"Stimulus is {len(wave) / rate:.1f} s long; the limit is {MAX_DURATION_S:g} s.")
        return wave

    @staticmethod
    def _need(ms: float, what: str, dt_ms: float, rate: float) -> int:
        k = samples(ms, rate)
        if ms * rate / 1000.0 < 1 - 1e-9:
            raise ValueError(f"{what} is shorter than one output sample ({dt_ms:g} ms at {rate:,.0f} S/s).")
        return k

    # -- segments --------------------------------------------------------------
    @property
    def steps(self) -> bool:
        """Whether any segment changes from trial to trial."""
        return self.shape == "segments" and any(p.get("d_level") or p.get("d_dur") for p in self.parts)

    def list_lengths(self) -> list[int]:
        """How many values each per-trial list has (lists can't run past their end)."""
        out = []
        for p in self.parts if self.shape == "segments" else []:
            for key in ("d_level", "d_dur"):
                try:
                    got = parse_per_trial(p.get(key, ""))
                except ValueError:
                    continue
                if got and got[0] == "list":
                    out.append(len(got[1]))
        return out

    def at_trial(self, t: int) -> "Stimulus":
        """This stimulus as trial t sends it (0 = the first): each segment's per-trial
        change applied. Raises ValueError, naming the segment."""
        if self.shape != "segments":
            return self
        parts = []
        for i, p in enumerate(self.parts):
            q = dict(p)
            for key, dk in (("level", "d_level"), ("dur", "d_dur")):
                try:
                    q[key] = per_trial(float(p.get(key, 0.0)), p.get(dk, ""), t)
                except ValueError as e:
                    raise ValueError(f"{letter(i)}: {e}") from None
                q[dk] = ""
            parts.append(q)
        return replace(self, parts=parts)

    def onset_samples(self, rate: float) -> int:
        """Output samples before the stimulus starts: the delay, or the leading baselines.
        Time 0 of a protocol's trial is here."""
        if self.shape != "segments":
            return samples(self.delay_ms, rate)
        ms = 0.0
        for p in self.parts:
            if p.get("kind") != "base":
                break
            ms += float(p.get("dur", 0.0))
        return samples(self.delay_ms + ms, rate)

    def rounding_notes(self, rate: float) -> list[str]:
        """Times that don't fit the output's sample grid and will be rounded."""
        dt = 1000.0 / rate
        notes = []
        if self.shape == "segments":
            checks = []
            try:
                parts = self.at_trial(0).parts
            except ValueError:
                parts = []
            for i, p in enumerate(parts):
                if p.get("kind") == "file":
                    continue
                name = f"{letter(i)}'s {'width' if p.get('kind') == 'pulse' else 'duration'}"
                checks.append((name, float(p.get("dur", 0.0))))
                if p.get("kind") == "pulse" and int(p.get("count", 1)) > 1:
                    checks.append((f"{letter(i)}'s interval", float(p.get("interval", 0.0))))
            for name, ms in checks:
                got = samples(ms, rate) * dt
                if ms and abs(got - ms) > 1e-9:
                    notes.append(f"{name} {ms:g} ms rounds to {got:g} ms (output steps are {dt:g} ms).")
            return notes
        checks = [("Delay", self.delay_ms)]
        if self.shape == "pulse":
            checks += [("Width", self.width_ms)] + ([("Interval", self.interval_ms)] if self.count > 1 else [])
        elif self.shape in ("step", "ramp", "sine"):
            checks.append(("Duration", self.duration_ms))
        for name, ms in checks:
            got = samples(ms, rate) * dt
            if ms and abs(got - ms) > 1e-9:
                notes.append(f"{name} {ms:g} ms rounds to {got:g} ms (output steps are {dt:g} ms).")
        return notes

    def repeat(self) -> tuple[float, float, int]:
        """How it repeats: (one repeat in s, where the first starts in s, how many). No
        repeats: all 0."""
        if self.shape == "segments":
            try:
                parts = self.at_trial(0).parts
            except ValueError:
                return 0.0, 0.0, 0
            t0 = 0.0
            for p in parts:
                kind, dur = p.get("kind"), float(p.get("dur", 0.0))
                if kind == "pulse" and int(p.get("count", 1)) > 1:
                    return float(p["interval"]) / 1000, t0 / 1000, int(p["count"])
                if kind == "sine" and float(p.get("freq", 0)) > 0:
                    return 1 / float(p["freq"]), t0 / 1000, int(dur / 1000 * float(p["freq"]))
                t0 += _part_ms(p)
            return 0.0, 0.0, 0
        if self.shape == "sine" and self.freq_hz > 0:
            return 1 / self.freq_hz, self.delay_ms / 1000, int(self.duration_ms / 1000 * self.freq_hz)
        if self.shape == "pulse" and self.count > 1:
            return self.interval_ms / 1000, self.delay_ms / 1000, self.count
        return 0.0, 0.0, 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Stimulus":
        """A saved stimulus. The older shapes (pulse, step…) come back as segments.
        Settings saved before shapes existed are pulses."""
        known = {f.name for f in fields(cls)}
        s = cls(**{"shape": "pulse", **{k: v for k, v in d.items() if k in known}})
        return to_segments(s)

    # -- files ---------------------------------------------------------------
    def save(self, path: str | Path, second: "Stimulus | None" = None) -> Path:
        """Save as JSON. A second output's stimulus goes under "second": older versions
        (Argus, before the rename) ignore it and load the first."""
        path = Path(path)
        d = self.to_dict()
        if second is not None:
            d["second"] = second.to_dict()
        path.write_text(json.dumps(d, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Stimulus":
        return cls.load_pair(path)[0]

    @classmethod
    def load_pair(cls, path: str | Path) -> "tuple[Stimulus, Stimulus | None]":
        """(first output's stimulus, second's or None) from a saved file."""
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            second = d.get("second")
            return cls.from_dict(d), (cls.from_dict(second) if isinstance(second, dict) else None)
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            raise ValueError(f"{Path(path).name} isn't a saved stimulus: {e}") from e


def _part_ms(p: dict) -> float:
    """How long a segment lasts, in ms (a file's length isn't known without reading it)."""
    kind, dur = p.get("kind"), float(p.get("dur", 0.0))
    if kind == "pulse":
        one = dur * (2 if p.get("biphasic") else 1)
        n = int(p.get("count", 1))
        return (n - 1) * float(p.get("interval", 0.0)) + one if n > 1 else one
    return 0.0 if kind == "file" else dur


def _part_wave(p: dict, rate: float, t0_ms: float = 0.0) -> np.ndarray:
    """One segment's samples, for a segment starting `t0_ms` into the output (its
    edges land on the sample nearest their true time), shifted by its `offset` V (every
    kind has one; a baseline's makes it a holding level). Raises ValueError."""
    w = _part_shape(p, rate, t0_ms)
    off = float(p.get("offset", 0.0) or 0.0)
    return w + off if off else w


def _part_shape(p: dict, rate: float, t0_ms: float = 0.0) -> np.ndarray:
    """A segment's samples before its offset."""
    kind = p.get("kind")
    dt_ms = 1000.0 / rate
    level, dur = float(p.get("level", 0.0)), float(p.get("dur", 0.0))
    if kind != "file":
        if dur < 0:
            raise ValueError("its duration can't be negative.")
        if 0 < dur * rate / 1000.0 < 1 - 1e-9:
            raise ValueError(f"{dur:g} ms is shorter than one output sample ({dt_ms:g} ms at {rate:,.0f} S/s).")
    k = span(t0_ms, dur, rate)
    if kind == "base":
        return np.zeros(k)
    if kind == "step":
        return np.full(k, level)
    if kind == "ramp":
        return np.linspace(float(p.get("start", 0.0)), level, k)
    if kind == "sine":
        freq = float(p.get("freq", 0.0))
        if freq <= 0 or freq > rate / 2:
            raise ValueError(f"the frequency must be between 0 and {rate / 2:,.0f} Hz (half the output rate).")
        t = np.arange(k) / rate
        return abs(level) * np.sin(2 * np.pi * freq * t)
    if kind == "pulse":
        k = samples(dur, rate)                       # every pulse the same width
        if k == 0:
            raise ValueError("a pulse needs a width.")
        count = int(p.get("count", 1))
        if count < 1:
            raise ValueError("the number of pulses must be at least 1.")
        one = np.full(k, level)
        if p.get("biphasic"):
            one = np.concatenate([one, -one])
        interval = float(p.get("interval", 0.0))
        if count > 1 and interval * rate / 1000.0 < len(one) - 1e-9:
            raise ValueError(f"pulses {float(p.get('interval', 0.0)):g} ms apart are closer than one pulse "
                             f"({len(one) * dt_ms:g} ms).")
        out = _train(one, count, interval, t0_ms, rate)
        end = span(t0_ms, _part_ms(p), rate)            # end on the grid too
        return np.concatenate([out, np.zeros(end - len(out))]) if end > len(out) else out[:end]
    if kind == "file":
        return load_wave_file(p.get("file", ""), rate, float(p.get("file_rate", 0.0)) or rate) * level
    t = np.arange(k) / rate                                      # s, from the segment's start
    if kind in ("triangle", "square"):
        freq = float(p.get("freq", 0.0))
        if freq <= 0 or freq > rate / 2:
            raise ValueError(f"the frequency must be between 0 and {rate / 2:,.0f} Hz (half the output rate).")
        f = (freq * t) % 1.0
        if kind == "square":
            return np.where(f < 0.5, level, 0.0)
        return level * np.where(f < 0.25, 4 * f, np.where(f < 0.75, 2 - 4 * f, 4 * f - 4))
    if kind == "chirp":
        f0, f1, T = float(p.get("freq", 0.0)), float(p.get("freq2", 0.0)), dur / 1000.0
        if min(f0, f1) <= 0 or max(f0, f1) > rate / 2:
            raise ValueError(f"both frequencies must be between 0 and {rate / 2:,.0f} Hz (half the output rate).")
        if T <= 0:
            return np.zeros(k)
        if p.get("log") and f0 != f1:
            r = f1 / f0
            phase = 2 * np.pi * f0 * T / np.log(r) * (r ** (t / T) - 1)
        else:
            phase = 2 * np.pi * (f0 * t + (f1 - f0) * t ** 2 / (2 * T))
        end = float(p.get("level2", level))
        return (level + (end - level) * t / T) * np.sin(phase)
    if kind == "noise":
        seed = int(p.get("seed", 0))
        rng = np.random.default_rng(seed if seed else None)
        return abs(level) * rng.standard_normal(k)
    if kind in ("exp", "alpha"):
        tau = float(p.get("tau", 0.0)) / 1000.0
        if tau <= 0:
            raise ValueError("τ must be more than 0.")
        if kind == "exp":
            return level * np.exp(-t / tau)
        return level * (t / tau) * np.exp(1 - t / tau)
    if kind == "points":
        pts = sorted((float(a), float(b)) for a, b in (p.get("points") or []))
        if len(pts) < 2:
            raise ValueError("custom points need at least two points.")
        tp = np.array([a for a, _ in pts]) / 1000.0
        vp = np.array([b for _, b in pts]) * level
        if p.get("join") == "step":
            idx = np.clip(np.searchsorted(tp, t, side="right") - 1, 0, len(vp) - 1)
            return vp[idx]
        return np.interp(t, tp, vp)
    raise ValueError(f"unknown kind of segment {kind!r}.")


def _describe_part(p: dict, units: str, scale: float) -> str:
    lv = lambda v: f"{float(v) * scale:g}"
    kind, dur = p.get("kind"), float(p.get("dur", 0.0))
    if kind == "base":
        s = f"Baseline {dur:g} ms"
    elif kind == "step":
        s = f"Step {lv(p['level'])} {units} for {dur:g} ms"
    elif kind == "ramp":
        s = f"Ramp {lv(p.get('start', 0))}→{lv(p['level'])} {units} over {dur:g} ms"
    elif kind == "pulse":
        s = f"{'Biphasic pulse' if p.get('biphasic') else 'Pulse'} {lv(p['level'])} {units}, {dur:g} ms"
        if int(p.get("count", 1)) > 1:
            s += f" ×{int(p['count'])} every {float(p['interval']):g} ms"
    elif kind == "sine":
        s = f"Sine {lv(abs(float(p['level'])))} {units}, {float(p.get('freq', 0)):g} Hz for {dur:g} ms"
    elif kind in ("triangle", "square"):
        s = f"{SEG_KINDS[kind]} {lv(p['level'])} {units}, {float(p.get('freq', 0)):g} Hz for {dur:g} ms"
    elif kind == "chirp":
        a0, a1 = float(p["level"]), float(p.get("level2", p["level"]))
        amp = lv(a0) if a0 == a1 else f"{lv(a0)}→{lv(a1)}"
        s = (f"Chirp {amp} {units}, {float(p.get('freq', 0)):g}→{float(p.get('freq2', 0)):g} Hz"
             f"{' (log)' if p.get('log') else ''} over {dur:g} ms")
    elif kind == "noise":
        s = f"Noise SD {lv(abs(float(p['level'])))} {units} for {dur:g} ms" + (" (same each time)" if int(p.get("seed", 0)) else "")
    elif kind == "exp":
        s = f"Exponential {lv(p['level'])} {units}, τ {float(p.get('tau', 0)):g} ms, for {dur:g} ms"
    elif kind == "alpha":
        s = f"Alpha {lv(p['level'])} {units}, peak at {float(p.get('tau', 0)):g} ms, for {dur:g} ms"
    elif kind == "points":
        s = f"Custom {len(p.get('points') or [])} points, {dur:g} ms"
    elif kind == "file":
        s = f"File {Path(p.get('file', '')).name or '(none)'}" + (
            f" ×{float(p['level']):g}" if float(p.get("level", 1)) != 1 else "")
    else:
        s = "?"
    off = float(p.get("offset", 0.0) or 0.0)
    if off:
        s += f", offset {lv(off)} {units}"
    changes = [w for w, k in (("level", "d_level"), ("duration", "d_dur")) if p.get(k)]
    return s + (f" ({' and '.join(changes)} change per trial)" if changes else "")


def _short_part(p: dict, units: str, scale: float) -> str:
    lv = lambda v: f"{round(float(v) * scale, 6):g} {units}"
    hz = lambda f: f"{round(float(f), 6):g} Hz"
    kind = p.get("kind")
    if kind == "pulse":
        name, n = ("Biphasic pulse" if p.get("biphasic") else "Pulse"), int(p.get("count", 1))
        return f"{name} {lv(p['level'])}" if n <= 1 else f"{n} {name.lower()}s {lv(p['level'])}"
    if kind == "ramp":
        start = float(p.get("start", 0.0))
        return f"Ramp to {lv(p['level'])}" if not start else f"Ramp {lv(start)} to {lv(p['level'])}"
    if kind in ("sine", "triangle", "square"):
        return f"{SEG_KINDS[kind]} {hz(p.get('freq', 0))} {lv(abs(float(p['level'])))}"
    if kind == "chirp":
        return f"Chirp {hz(p.get('freq', 0))} to {hz(p.get('freq2', 0))}"
    if kind in ("step", "noise", "exp", "alpha"):
        return f"{SEG_KINDS[kind]} {lv(abs(float(p['level'])) if kind == 'noise' else p['level'])}"
    if kind == "file":
        return f"File {Path(p.get('file', '')).name or '(none)'}"
    return SEG_KINDS.get(kind, "?")


def stimulus_name(s: Stimulus, units: str = "V", scale: float = 1.0, long: bool = False) -> str:
    """What a stimulus is, to name the group of its trials: "Pulse 5 V", "5 pulses 2 V",
    "Sine 10 Hz 1 V"; its segments after each other with " + " (baselines left out).
    long: with widths and durations, to tell apart two that share the short name."""
    parts = [p for p in to_segments(s).parts if p.get("kind") != "base"]
    if not parts:
        return "Baseline only"
    if len(parts) > 2 and all(p.get("kind") == "ramp" for p in parts):    # a triangle wave, as ramps
        top = max(abs(float(p["level"])) for p in parts)
        period = sum(float(p.get("dur", 0.0)) for p in parts[:3])
        name = (f"Triangle {round(1000.0 / period, 6):g} Hz" if period > 0 else "Triangle") + \
            f" {round(top * scale, 6):g} {units}"
        return name + (f", {sum(float(p.get('dur', 0.0)) for p in parts):g} ms" if long else "")
    names = [_describe_part(p, units, scale) if long else _short_part(p, units, scale) for p in parts]
    return " + ".join(names if len(names) <= 3 else names[:2] + ["…"])


def stimulus_size(s: Stimulus) -> tuple[float, float]:
    """(frequency Hz, amplitude V) of a stimulus, to sort groups by: its pulses' rate or its
    wave's frequency (0 for neither), and its biggest level."""
    parts = [p for p in to_segments(s).parts if p.get("kind") != "base"]
    amp = max((abs(float(p.get("level", 0.0))) for p in parts), default=0.0)
    for p in parts:
        if p.get("kind") == "pulse" and int(p.get("count", 1)) > 1 and float(p.get("interval", 0)) > 0:
            return 1000.0 / float(p["interval"]), amp
        if p.get("kind") in ("sine", "triangle", "square", "chirp"):
            return float(p.get("freq", 0.0)), amp
    return 0.0, amp


def to_segments(s: Stimulus) -> Stimulus:
    """An older shape as segments: a baseline for its delay, then the shape."""
    if s.shape == "segments":
        return s
    parts = [new_part("base", dur=s.delay_ms)]
    if s.shape == "pulse":
        parts.append({**new_part("pulse", s.amplitude_v, s.width_ms), "count": int(s.count),
                      "interval": float(s.interval_ms), "biphasic": bool(s.biphasic)})
    elif s.shape == "step":
        parts.append(new_part("step", s.amplitude_v, s.duration_ms))
    elif s.shape == "ramp":
        parts.append({**new_part("ramp", s.end_v, s.duration_ms), "start": float(s.start_v)})
    elif s.shape == "sine":
        parts.append({**new_part("sine", abs(s.amplitude_v), s.duration_ms), "freq": float(s.freq_hz),
                      "offset": float(s.offset_v)})
    elif s.shape == "custom":
        for seg in s.segments:
            a, b = float(seg["start_v"]), float(seg["end_v"])
            parts.append(new_part("step", a, seg["duration_ms"]) if a == b
                         else {**new_part("ramp", b, seg["duration_ms"]), "start": a})
    elif s.shape == "file":
        parts.append({**new_part("file", 1.0), "file": s.file, "file_rate": float(s.file_rate)})
    return Stimulus(shape="segments", parts=parts)


# The part and field an older shape's protocol setting became (see to_segments):
# (kinds it can be, field). Index 0 is the delay's baseline.
OLD_KEYS = {"amplitude_v": (("pulse", "step", "sine"), "level"), "width_ms": (("pulse",), "dur"),
            "count": (("pulse",), "count"), "interval_ms": (("pulse",), "interval"),
            "duration_ms": (("step", "ramp", "sine"), "dur"), "start_v": (("ramp",), "start"),
            "end_v": (("ramp",), "level"), "offset_v": (("sine",), "offset"), "freq_hz": (("sine",), "freq"),
            "scale": (("file",), "level")}


def old_key_to_segment(s: Stimulus, key: str) -> tuple[int, str] | None:
    """(part index, field) an older shape's setting is in a converted stimulus, or None."""
    if s.shape != "segments" or not s.parts:
        return None
    if key == "delay_ms":
        return (0, "dur") if s.parts[0].get("kind") == "base" else None
    kinds, fld = OLD_KEYS.get(key, ((), ""))
    i = next((i for i, p in enumerate(s.parts) if p.get("kind") in kinds), None)
    return None if i is None else (i, fld)


def other_output(ch: str) -> str:
    """The analog output that isn't `ch`: where a second stimulus goes."""
    return AO_CHANNELS[1] if ch == AO_CHANNELS[0] else AO_CHANNELS[0]


def combine(waves: dict[str, np.ndarray]) -> tuple[list[str], np.ndarray]:
    """Waveforms for several outputs as one array to send together: (outputs in order,
    array of one row per output). The shorter ones are padded with 0 V at the end, so
    every output starts at the same sample and ends at 0 V. One output: a 1-D array."""
    chans = sorted(waves)
    if len(chans) == 1:
        return chans, np.asarray(waves[chans[0]], float)
    n = max(len(waves[c]) for c in chans)
    out = np.zeros((len(chans), n))
    for i, c in enumerate(chans):
        out[i, :len(waves[c])] = waves[c]
    return chans, out


def check_limit(wave: np.ndarray, max_v: float) -> None:
    """Refuse waveforms beyond the configured safety limit."""
    peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    if peak > max_v + 1e-9:
        raise ValueError(f"Stimulus reaches {peak:g} V, above the {max_v:g} V limit set in the config "
                         "([stimulator] max_voltage). Lower the amplitude, or ask an instructor to raise the limit.")


_SPLIT = re.compile(r"[,;\t ]+")


def load_wave_file(path: str | Path, rate: float, file_rate: float) -> np.ndarray:
    """Read a waveform file and resample it to `rate`.

    One column: volts, one per sample at `file_rate`.
    Two columns: time (s) and volts; any spacing.
    Lines that aren't numbers (headers, comments) are skipped.
    """
    if not path:
        raise ValueError("Choose a waveform file.")
    p = Path(path)
    if not p.exists():
        raise ValueError(f"{p.name} not found.")
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        cells = [c for c in _SPLIT.split(line.strip()) if c]
        try:
            rows.append([float(c) for c in cells])
        except ValueError:
            continue
    rows = [r for r in rows if r]
    if not rows:
        raise ValueError(f"{p.name} has no numbers in it.")
    width = len(rows[0])
    if any(len(r) != width for r in rows) or width > 2:
        raise ValueError(f"{p.name}: every line needs the same 1 or 2 columns (volts, or time and volts).")
    a = np.asarray(rows, dtype=float)
    if width == 1:
        v = a[:, 0]
        if file_rate <= 0:
            raise ValueError("Give the file's sample rate for a one-column file.")
        t = np.arange(len(v)) / file_rate
    else:
        t, v = a[:, 0] - a[0, 0], a[:, 1]
        if np.any(np.diff(t) <= 0):
            raise ValueError(f"{p.name}: times in the first column must increase.")
    if len(v) == 1:
        return v.copy()
    out_t = np.arange(0, t[-1] + 0.5 / rate, 1.0 / rate)
    return np.interp(out_t, t, v)
