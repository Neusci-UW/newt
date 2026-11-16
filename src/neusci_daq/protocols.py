"""Trial protocols: send a stimulus many times while stepping one or two of its
settings (a, and optionally b), then line up and measure the responses.

Everything here is plain Python and numpy, so it can be tested without a window.

How a protocol runs (see protocolspage.py for the window):
  1. `values()` turns each variable's Range (from / to / step) or List into numbers.
  2. `sequence()` orders every a × b combination, `repeats` times: Blocked,
     Interleaved or Random.
  3. For each trial, `make_wave()` builds the waveform with a (and b) applied.
  4. The output and the recording are separate tasks, so the moment a stimulus
     is sent isn't known exactly. `find_stimulus()` finds it in the recording of
     the stimulus (Stimulator › Record on) by matching the waveform that was sent.
     Time 0 of each trial is where the stimulus starts (after its delay).
  5. `cut()` takes the window around time 0 from every channel, and `measure()`
     measures one channel within a window, e.g. peak-to-peak 2–15 ms.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field, replace

import numpy as np

from . import stim as stimmod
from .ringbuffer import RingBuffer
from .stim import Stimulus

# Settings that can be stepped, per shape: (key, label, unit). "scale" multiplies
# the whole waveform, for shapes whose settings don't step sensibly.
SETTINGS = {
    "pulse": [("amplitude_v", "Amplitude", "V"), ("width_ms", "Pulse width", "ms"),
              ("count", "Number of pulses", ""), ("interval_ms", "Interval", "ms"),
              ("delay_ms", "Delay", "ms")],
    "step": [("amplitude_v", "Level", "V"), ("duration_ms", "Duration", "ms"), ("delay_ms", "Delay", "ms")],
    "ramp": [("end_v", "End level", "V"), ("start_v", "Start level", "V"),
             ("duration_ms", "Duration", "ms"), ("delay_ms", "Delay", "ms")],
    "sine": [("amplitude_v", "Amplitude", "V"), ("freq_hz", "Frequency", "Hz"),
             ("offset_v", "Offset", "V"), ("duration_ms", "Duration", "ms"), ("delay_ms", "Delay", "ms")],
    "custom": [("scale", "Scale (× the whole shape)", "×"), ("delay_ms", "Delay", "ms")],
    "file": [("scale", "Scale (× the whole shape)", "×"), ("delay_ms", "Delay", "ms")],
}
TIME_SETTINGS = {"width_ms", "interval_ms", "delay_ms", "duration_ms", "dur", "interval"}   # rounded to output samples
# A starting range (from, to, step) when a setting is picked.
DEFAULT_RANGES = {
    "amplitude_v": (0.5, 3.0, 0.5), "start_v": (0.0, 2.0, 0.5), "end_v": (0.5, 3.0, 0.5),
    "offset_v": (0.0, 1.0, 0.25), "width_ms": (0.2, 1.0, 0.2), "interval_ms": (10, 100, 30),
    "delay_ms": (0, 20, 5), "duration_ms": (10, 50, 10), "count": (1, 5, 1),
    "freq_hz": (5, 20, 5), "scale": (0.5, 2.0, 0.5),
    # a segment's fields (keys "seg:<part>:<field>"), and the trial number
    "level": (0.5, 3.0, 0.5), "dur": (1, 5, 1), "start": (0.0, 2.0, 0.5), "interval": (10, 100, 30),
    "freq": (5, 20, 5), "offset": (0.0, 1.0, 0.25), "trial": (1, 5, 1),
}
# A segment's fields that can be stepped, per kind: (field, label, unit). "V" levels are
# shown in the stimulator's units; a file's level is a scale.
SEG_FIELDS = {
    "base": [("dur", "duration", "ms")],
    "step": [("level", "level", "V"), ("dur", "duration", "ms")],
    "ramp": [("level", "end level", "V"), ("start", "start level", "V"), ("dur", "duration", "ms")],
    "pulse": [("level", "height", "V"), ("dur", "width", "ms"), ("count", "pulses", ""),
              ("interval", "interval", "ms")],
    "sine": [("level", "amplitude", "V"), ("freq", "frequency", "Hz"), ("offset", "offset", "V"),
             ("dur", "duration", "ms")],
    "file": [("level", "scale", "×")],
}
TRIAL = ("trial", "Trial", "")          # a: the trial number, which each segment's per-trial change follows
ORDERS = {"blocked": "Blocked", "interleaved": "Interleaved", "random": "Random"}
MEASURES = {"p2p": "Peak-to-peak", "max": "Maximum", "min": "Minimum", "latency": "Time of peak"}
MAX_VALUES = 200          # per variable
MAX_TRIALS = 2000
LONG_PROTOCOL_S = 600     # warn above 10 minutes
MATCH_THRESHOLD = 0.6     # how closely the recorded stimulus must match the one sent (0–1)


# With a second output, `shape` is (first shape, second shape, first output, second
# output), e.g. ("pulse", "step", "ao0", "ao1"), and the second output's settings are
# named after it: "ao1:amplitude_v". Plain names are the first output's, as before, so
# protocols saved with one output still load.
Shape = "str | tuple[str, str, str, str]"


def split_key(key: str) -> tuple[str, str]:
    """('ao1', 'amplitude_v') for the second output's setting; ('', key) for the first's."""
    out, sep, base = key.partition(":")
    return (out, base) if sep and out in stimmod.AO_CHANNELS else ("", key)


def field_of(key: str) -> str:
    """The setting's own name, without its output or segment: 'seg:1:level' -> 'level'."""
    return split_key(key)[1].split(":")[-1]


def seg_key(i: int, fld: str) -> str:
    return f"seg:{i}:{fld}"


def _seg_settings(s: Stimulus) -> list[tuple[str, str, str]]:
    out = [TRIAL]
    for i, p in enumerate(s.parts):
        kind = p.get("kind", "")
        name = f"{stimmod.letter(i)} · {stimmod.SEG_KINDS.get(kind, kind)}"
        out += [(seg_key(i, f), f"{name} {label}", u) for f, label, u in SEG_FIELDS.get(kind, [])]
    return out


def _settings_one(shape) -> list[tuple[str, str, str]]:
    if isinstance(shape, Stimulus):
        return _seg_settings(shape) if shape.shape == "segments" else SETTINGS.get(shape.shape, [])
    return SETTINGS.get(shape, [])


def settings_of(shape) -> list[tuple[str, str, str]]:
    """The settings that can be stepped: (key, label, unit). `shape` is a shape's name or
    a Stimulus (segments have their own); with two outputs, (first, second, first output,
    second output) and both outputs' settings, each label saying which output, e.g.
    'ao1 · Level'. The trial number is shared."""
    if not isinstance(shape, tuple):
        return _settings_one(shape)
    s1, s2, o1, o2 = shape
    return ([(k, label if k == "trial" else f"{o1} · {label}", u) for k, label, u in _settings_one(s1)]
            + [(f"{o2}:{k}", f"{o2} · {label}", u) for k, label, u in _settings_one(s2) if k != "trial"])


def default_range(key: str) -> tuple[float, float, float]:
    base = split_key(key)[1]
    return DEFAULT_RANGES.get(base, DEFAULT_RANGES.get(field_of(key), (1, 5, 1)))


def setting_info(shape, key: str) -> tuple[str, str]:
    """(label, unit) of a setting."""
    for k, label, unit in settings_of(shape) + [("scale", "Scale", "×")]:
        if k == key:
            return label, unit
    return key, ""


def fmt(v: float, unit: str = "") -> str:
    """A value with its unit: '1.5 V', '0.2 ms', '×2'."""
    s = f"{v:.6g}"
    if unit == "×":
        return f"×{s}"
    return f"{s} {unit}".strip()


# ---------------------------------------------------------------------------- variables
_SPLIT = re.compile(r"[,;\s]+")


@dataclass
class Variable:
    key: str = "amplitude_v"
    mode: str = "range"           # "range" (from / to / step) or "list"
    start: float = 0.5
    stop: float = 3.0
    step: float = 0.5
    text: str = "0.5, 1, 2, 3"    # the list, as typed

    def values(self) -> list[float]:
        """The values, in order. Raises ValueError with a message for the window."""
        if self.mode == "list":
            return parse_list(self.text)
        return range_values(self.start, self.stop, self.step)


def range_values(start: float, stop: float, step: float) -> list[float]:
    """from, from ± step, ... never going past `stop`. Counts down if stop < start."""
    if not step > 0:
        raise ValueError("Step must be more than 0 (from and to set the direction).")
    span = abs(stop - start)
    n = int(math.floor(span / step + 1e-9)) + 1
    if n > MAX_VALUES:
        raise ValueError(f"That's {n} values; the most is {MAX_VALUES}. Use a bigger step.")
    sign = 1.0 if stop >= start else -1.0
    # whole steps from `start`, so 0.1-sized steps don't drift
    return [round(start + sign * k * step, 9) for k in range(n)]


def parse_list(text: str) -> list[float]:
    """'0.5, 1, 2 3' -> [0.5, 1, 2, 3], kept in the order typed."""
    cells = [c for c in _SPLIT.split(text.strip()) if c]
    if not cells:
        raise ValueError("Type at least one value.")
    out = []
    for c in cells:
        try:
            out.append(float(c))
        except ValueError:
            raise ValueError(f"{c!r} isn't a number. Separate values with commas or spaces.") from None
    dupes = sorted({v for v in out if out.count(v) > 1})
    if dupes:
        raise ValueError(f"{', '.join(f'{v:g}' for v in dupes)} is in the list twice.")
    if len(out) > MAX_VALUES:
        raise ValueError(f"That's {len(out)} values; the most is {MAX_VALUES}.")
    return out


# ---------------------------------------------------------------------------- protocol
@dataclass
class Protocol:
    name: str = "Protocol"
    a: Variable = field(default_factory=Variable)
    b: Variable | None = None
    repeats: int = 3
    order: str = "interleaved"
    gap_s: float = 2.0            # from the start of one trial to the start of the next
    before_ms: float = 5.0        # window shown around each stimulus
    after_ms: float = 30.0
    measure: str = "p2p"
    measure_from_ms: float = 2.0
    measure_to_ms: float = 15.0
    measure_channel: str = ""     # channel name; "" = first channel that isn't the stimulus

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Protocol":
        d = dict(d)
        a = Variable(**{k: v for k, v in (d.pop("a", None) or {}).items() if k in Variable.__dataclass_fields__})
        bd = d.pop("b", None)
        b = Variable(**{k: v for k, v in bd.items() if k in Variable.__dataclass_fields__}) if bd else None
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(a=a, b=b, **known)

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def loads(cls, text: str) -> "Protocol":
        try:
            return cls.from_dict(json.loads(text))
        except (json.JSONDecodeError, TypeError, AttributeError) as e:
            raise ValueError(f"That isn't a saved protocol: {e}") from e


@dataclass
class Trial:
    n: int            # 1-based, in the order they run
    rep: int          # 1-based
    ia: int           # index into a's values
    ib: int           # index into b's values (0 when there's no b)
    a: float
    b: float | None


def sequence(p: Protocol, a_vals: list[float], b_vals: list[float] | None,
             rng: np.random.Generator | None = None) -> list[Trial]:
    """Every a × b combination, `repeats` times, in the protocol's order."""
    nb = len(b_vals) if b_vals else 1
    combos = [(ia, ib) for ib in range(nb) for ia in range(len(a_vals))]
    order: list[tuple[int, int, int]] = []          # (rep, ia, ib)
    if p.order == "blocked":
        for ia, ib in combos:
            order += [(r, ia, ib) for r in range(1, p.repeats + 1)]
    else:
        rng = rng or np.random.default_rng()
        for r in range(1, p.repeats + 1):
            these = list(combos)
            if p.order == "random":
                rng.shuffle(these)
            order += [(r, ia, ib) for ia, ib in these]
    return [Trial(n=k + 1, rep=r, ia=ia, ib=ib, a=a_vals[ia], b=(b_vals[ib] if b_vals else None))
            for k, (r, ia, ib) in enumerate(order)]


def apply(s: Stimulus, key: str, value: float) -> tuple[Stimulus, float]:
    """The stimulus with `key` set to `value`, and the scale for the whole waveform."""
    if key.startswith("seg:"):
        _, i, fld = key.split(":")
        i = int(i)
        if s.shape != "segments" or i >= len(s.parts):
            raise ValueError(f"There's no segment {stimmod.letter(i)} to step.")
        if fld == "count" and abs(value - round(value)) > 1e-9:
            raise ValueError(f"Number of pulses must be a whole number, not {value:g}.")
        parts = [dict(p) for p in s.parts]
        parts[i][fld] = int(round(value)) if fld == "count" else float(value)
        return replace(s, parts=parts), 1.0
    if key == "scale":
        return s, float(value)
    if key == "count":
        if abs(value - round(value)) > 1e-9:
            raise ValueError(f"Number of pulses must be a whole number, not {value:g}.")
        return replace(s, count=int(round(value))), 1.0
    if key == "amplitude_v" and s.shape == "sine":
        value = abs(value)
    return replace(s, **{key: float(value)}), 1.0


@dataclass
class TrialWave:
    """What one trial sends."""
    channels: list[str]           # the outputs, in the order of the wave's rows
    wave: np.ndarray              # 1-D for one output, one row per output for two
    first: np.ndarray             # the first output's waveform: what's recorded and lined up on
    delay_ms: float               # the first output's delay, rounded to output samples
    stimulus: Stimulus            # each output's stimulus with a (and b) applied
    stimulus2: Stimulus | None


def applied(s: Stimulus, p: Protocol, a: float, b: float | None, s2: Stimulus | None = None
            ) -> tuple[Stimulus, float, Stimulus | None, float]:
    """(first output's stimulus, its scale, second's, its scale) with a and b applied, each
    to whichever output's setting it steps. Raises ValueError."""
    k1 = k2 = 1.0
    steps = [(var, v) for var, v in ((p.a, a), (p.b, b)) if var is not None and v is not None]
    steps.sort(key=lambda x: x[0].key == "trial")      # a segment's value first, then the trial's change to it
    for var, v in steps:
        if var.key == "trial":
            if v < 1 or abs(v - round(v)) > 1e-9:
                raise ValueError(f"Trials are numbered 1, 2, 3…, not {v:g}.")
            s = s.at_trial(int(round(v)) - 1)
            s2 = s2.at_trial(int(round(v)) - 1) if s2 is not None else None
            continue
        out, base = split_key(var.key)
        if out:
            if s2 is None:
                raise ValueError(f"This steps a setting of {out}, which isn't in use: add it on the "
                                 "Stimulator, or pick another setting.")
            s2, k = apply(s2, base, v)
            k2 *= k
        else:
            s, k = apply(s, base, v)
            k1 *= k
    return s, k1, s2, k2


def make_trial(s: Stimulus, p: Protocol, a: float, b: float | None, rate: float,
               s2: Stimulus | None = None, outs: tuple[str, str] = ("ao0", "ao1")) -> TrialWave:
    """One trial's waveforms: a and b applied to whichever output's setting each
    steps. `s2` is the second output's stimulus, on outs[1]. Raises ValueError."""
    s, k1, s2, k2 = applied(s, p, a, b, s2)
    first = s.waveform(rate) * k1
    waves = {outs[0]: first}
    if s2 is not None:
        waves[outs[1]] = s2.waveform(rate) * k2
    chans, wave = stimmod.combine(waves)
    return TrialWave(chans, wave, first, s.onset_samples(rate) * 1000.0 / rate, s, s2)


def output_wave(s: Stimulus, p: Protocol, a: float, b: float | None, rate: float,
                s2: Stimulus | None = None, second: bool = False) -> np.ndarray:
    """One output's waveform for one trial (the second's if `second`): for previews,
    which show one output, without building the other. Raises ValueError."""
    s, k1, s2, k2 = applied(s, p, a, b, s2)
    return s2.waveform(rate) * k2 if second else s.waveform(rate) * k1


def make_wave(s: Stimulus, p: Protocol, a: float, b: float | None, rate: float) -> tuple[np.ndarray, float]:
    """(waveform, delay in ms) for one trial on one output. Raises ValueError."""
    t = make_trial(s, p, a, b, rate)
    return t.wave, t.delay_ms


def describe(p: Protocol, shape, a: float, b: float | None) -> str:
    """'a = 1.5 V, b = 0.4 ms' for comments and the chip ('trial 3, b = 0.4 ms' when a is
    the trial number)."""
    la, ua = setting_info(shape, p.a.key)
    out = f"trial {a:g}" if p.a.key == "trial" else f"a = {fmt(a, ua)}"
    if p.b is not None and b is not None:
        out += f", b = {fmt(b, setting_info(shape, p.b.key)[1])}"
    return out


@dataclass
class Check:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    a_vals: list[float] = field(default_factory=list)
    b_vals: list[float] | None = None
    n_trials: int = 0
    duration_s: float = 0.0
    peak_v: float = 0.0
    longest_s: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.errors


def check(p: Protocol, s: Stimulus, rate: float, max_v: float, s2: Stimulus | None = None,
          outs: tuple[str, str] = ("ao0", "ao1")) -> Check:
    """Everything that would stop a protocol, found before it starts. `s2`: the second
    output's stimulus, on outs[1]."""
    c = Check()
    shape = s if s2 is None else (s, s2, *outs)
    keys = {k for k, _, _ in settings_of(shape)}
    for name, var in (("a", p.a), ("b", p.b)):
        if var is None:
            continue
        if var.key not in keys:
            out, base = split_key(var.key)
            if out and (s2 is None or out != outs[1]):
                c.errors.append(f"{name}: steps a setting of {out}, which isn't in use. Add it on the "
                                "Stimulator, or pick another setting.")
            else:
                which = s2 if out else s
                c.errors.append(f"{name}: {setting_info(which.shape, base)[0]} isn't a setting of a "
                                f"{stimmod.SHAPES.get(which.shape, which.shape).lower()} stimulus.")
            continue
        try:
            vals = var.values()
        except ValueError as e:
            c.errors.append(f"{name}: {e}")
            continue
        if name == "a":
            c.a_vals = vals
        else:
            c.b_vals = vals
        if field_of(var.key) in TIME_SETTINGS:          # values the output can't tell apart
            got: dict[int, float] = {}
            for v in vals:
                k = stimmod.samples(v, rate)
                if k in got:
                    c.warnings.append(f"{name}: {got[k]:g} and {v:g} ms both round to "
                                      f"{k * 1000 / rate:g} ms at {rate:,.0f} samples/s.")
                got.setdefault(k, v)
    if p.b is not None and p.a.key == p.b.key:
        c.errors.append("a and b are the same setting; pick two different ones.")
    if p.repeats < 1:
        c.errors.append("Repeats must be at least 1.")
    if p.before_ms < 0 or p.after_ms <= 0:
        c.errors.append("The window needs 0 ms or more before and more than 0 ms after the stimulus.")
    if c.errors:
        return c
    nb = len(c.b_vals) if c.b_vals else 1
    c.n_trials = len(c.a_vals) * nb * p.repeats
    if c.n_trials > MAX_TRIALS:
        c.errors.append(f"{c.n_trials} stimuli is too many (the most is {MAX_TRIALS}).")
        return c
    # Each output's peak and length. A stimulus that several trials share (all of ao0's,
    # when a only steps ao1) is built once: this runs on every edit on the Protocols page.
    built: dict[str, tuple[float, int] | ValueError] = {}

    def measure(st: Stimulus) -> tuple[float, int]:
        key = json.dumps(st.to_dict(), sort_keys=True)
        if key not in built:
            try:
                w = st.waveform(rate)
                built[key] = (float(np.max(np.abs(w))) if w.size else 0.0, len(w))
            except ValueError as e:
                built[key] = e
        got = built[key]
        if isinstance(got, ValueError):
            raise got
        return got

    bad = []
    for b in (c.b_vals or [None]):
        for a in c.a_vals:
            try:
                s1, k1, t2, k2 = applied(s, p, a, b, s2)
                streams = [(outs[0], s1, k1)] + ([(outs[1], t2, k2)] if t2 is not None else [])
                peak, n = 0.0, 0
                for out, st, k in streams:
                    try:
                        pk, m = measure(st)
                    except ValueError as e:
                        raise ValueError(f"{out}: {e}" if len(streams) > 1 else str(e)) from None
                    peak, n = max(peak, pk * abs(k)), max(n, m)
                stimmod.check_limit(np.array([peak]), max_v)
            except ValueError as e:
                bad.append(f"{describe(p, shape, a, b)}: {e}")
                continue
            c.peak_v = max(c.peak_v, peak)
            c.longest_s = max(c.longest_s, n / rate)
    if bad:
        c.errors += bad[:3] + ([f"…and {len(bad) - 3} more."] if len(bad) > 3 else [])
        return c
    need = c.longest_s + p.after_ms / 1000.0 + 0.1
    if p.gap_s < need:
        c.errors.append(f"Stimuli {p.gap_s:g} s apart would overlap: the longest stimulus plus the "
                        f"{p.after_ms:g} ms after it needs at least {need:.2f} s.")
    c.duration_s = c.n_trials * p.gap_s
    if c.duration_s > LONG_PROTOCOL_S:
        c.warnings.append(f"This takes about {c.duration_s / 60:.0f} minutes. Long protocols can "
                          "outlast the preparation; consider fewer values or repeats.")
    return c


def trials_variable(n: int) -> Variable:
    """a, when it's the trial number: 1 to n."""
    return Variable("trial", "range", 1, max(1, int(n)), 1)


def upgrade(p: Protocol, s: Stimulus, s2: Stimulus | None = None) -> tuple[Protocol, Stimulus, Stimulus | None]:
    """A protocol saved before segments, whose a steps a setting of an older shape: a
    becomes the trial number and that setting's values a per-trial list on its segment;
    b steps the same setting of its segment. Anything already in the new form is returned
    as it is."""
    def where(var: Variable):
        out, base = split_key(var.key)
        st = s2 if out else s
        if base == "trial" or base.startswith("seg:") or st is None:
            return None
        got = stimmod.old_key_to_segment(st, base)
        return (out, got) if got else ("missing", None)

    a_at = where(p.a)
    b = p.b
    if p.b is not None:
        b_at = where(p.b)
        if b_at == ("missing", None):
            b = None
        elif b_at is not None:
            out, (i, fld) = b_at
            b = replace(p.b, key=(f"{out}:" if out else "") + seg_key(i, fld))
    if a_at is None:
        return replace(p, b=b), s, s2
    n = 1
    if a_at != ("missing", None):
        out, (i, fld) = a_at
        try:
            vals = p.a.values()
        except ValueError:
            vals = []
        if fld in ("level", "dur") and vals:
            st = s2 if out else s
            parts = [dict(q) for q in st.parts]
            parts[i]["d_" + fld] = stimmod.format_per_trial(("list", vals))
            st = replace(st, parts=parts)
            if out:
                s2 = st
            else:
                s = st
            n = len(vals)
        elif vals:                                     # pulses, interval…: b steps it instead
            if b is None:
                b = replace(p.a, key=(f"{out}:" if out else "") + seg_key(i, fld))
    return replace(p, a=trials_variable(n), b=b), s, s2


# ---------------------------------------------------------------------------- finding the stimulus
def template(wave: np.ndarray, out_rate: float, in_rate: float, pad_s: float = 0.02) -> tuple[np.ndarray, int]:
    """The sent waveform as the input would record it, with 0 V before and after it
    (so a stimulus that starts straight away still has an edge to find).
    Returns (template, samples of padding before the waveform)."""
    t_out = np.arange(len(wave)) / out_rate
    t_in = np.arange(0, len(wave) / out_rate, 1.0 / in_rate)
    # zero-order hold: what the output really does between its samples
    idx = np.minimum((t_in * out_rate + 1e-9).astype(int), len(wave) - 1)
    body = wave[idx] if len(wave) else np.zeros(0)
    pad = max(1, int(round(pad_s * in_rate)))
    return np.concatenate([np.zeros(pad), body, np.zeros(pad)]), pad


def find_stimulus(x: np.ndarray, tpl: np.ndarray) -> tuple[int, float]:
    """Where `tpl` best matches inside `x`: (index of the template's start, match 0–1).

    Normalized cross-correlation, so it doesn't matter how strongly the stimulus
    was recorded, or whether the input has an offset.
    """
    from scipy.signal import correlate
    x = np.asarray(x, float)
    m = len(tpl)
    if len(x) < m or m < 2:
        return 0, 0.0
    t = tpl - tpl.mean()
    tn = float(np.sqrt(np.sum(t * t)))
    if tn == 0:
        return 0, 0.0
    num = correlate(x, t, mode="valid", method="fft")
    c1 = np.concatenate([[0.0], np.cumsum(x)])
    c2 = np.concatenate([[0.0], np.cumsum(x * x)])
    s1 = c1[m:] - c1[:-m]
    s2 = c2[m:] - c2[:-m]
    var = np.maximum(s2 - s1 * s1 / m, 0.0)
    den = np.sqrt(var) * tn
    ncc = np.where(den > 1e-12 * max(1.0, tn), num / np.where(den > 0, den, 1.0), 0.0)
    k = int(np.argmax(ncc))
    return k, float(ncc[k])


def cut(t: np.ndarray, data: np.ndarray, t0: float, before_ms: float, after_ms: float,
        rate: float) -> tuple[np.ndarray, np.ndarray] | None:
    """The window from `before_ms` before `t0` to `after_ms` after it, from every
    channel: (time in ms relative to t0, data). None if the data doesn't cover it."""
    i0 = int(round((t0 - t[0]) * rate))
    lo = i0 - int(round(before_ms / 1000.0 * rate))
    hi = i0 + int(round(after_ms / 1000.0 * rate)) + 1
    if lo < 0 or hi > len(t):
        return None
    return (np.arange(lo - i0, hi - i0) / rate * 1000.0), data[:, lo:hi].copy()


def measure(kind: str, t_ms: np.ndarray, y: np.ndarray, t_from: float, t_to: float) -> float:
    """One number from one trace between t_from and t_to (ms after the stimulus)."""
    sel = (t_ms >= t_from) & (t_ms <= t_to)
    if not np.any(sel):
        return float("nan")
    seg = y[sel]
    if kind == "max":
        return float(np.max(seg))
    if kind == "min":
        return float(np.min(seg))
    if kind == "latency":
        base_sel = t_ms < 0
        base = float(np.mean(y[base_sel])) if np.any(base_sel) else 0.0
        return float(t_ms[sel][int(np.argmax(np.abs(seg - base)))])
    return float(np.max(seg) - np.min(seg))


def stats(xs) -> tuple[int, float, float]:
    """(n, mean, SD) ignoring NaN; SD is 0 for one value."""
    xs = np.asarray([x for x in xs if np.isfinite(x)], float)
    if xs.size == 0:
        return 0, float("nan"), float("nan")
    return int(xs.size), float(xs.mean()), float(xs.std(ddof=1)) if xs.size > 1 else 0.0


# ---------------------------------------------------------------------------- capturing trials
@dataclass
class Result:
    trial: Trial
    found: bool
    match: float = 0.0
    t0: float = float("nan")          # stream time (s) of the stimulus start
    t_ms: np.ndarray | None = None    # window time axis, ms after the stimulus
    data: np.ndarray | None = None    # (channels, samples), in each channel's units
    use: bool = True                  # left out of averages and the curve when False


@dataclass
class _Pending:
    trial: Trial
    t_fire: float          # newest recorded sample when it was sent (stream time, s)
    tpl: np.ndarray
    pad: int
    delay_s: float
    dur_s: float
    t_next: float | None = None   # the same, for the trial sent after it


class Capture:
    """Keeps the last few seconds of the recording and turns each sent stimulus
    into a Result once the data after it has arrived.

    Each stimulus is looked for between when it was sent and when the next one
    was: the next is only sent once this one has finished, so it must be in
    between, however slow the computer was. Looking any further could find the
    next trial's stimulus instead, which matches just as well when only its size
    differs.
    """

    SEARCH_AFTER_S = 1.0      # for the last trial, which has no next one

    def __init__(self, rate: float, monitor_row: int, to_units, keep_s: float, read_s: float = 0.05):
        self.rate = float(rate)
        # 'When it was sent' is the newest recorded sample at the time, which can
        # be up to one read away from the real moment, either way.
        self.slack = read_s + 0.03
        self.monitor_row = monitor_row
        self.to_units = to_units              # (channels, n) volts -> display units
        self.keep_s = keep_s
        # The newest keep_s of every channel, in a ring: adding a chunk writes only its
        # samples (concatenating the whole window every chunk copied ~150 MB 20×/s on a
        # long protocol and froze the computer). t and data are views of it.
        self._ring: RingBuffer | None = None
        self.pending: list[_Pending] = []
        self.last_fire: float | None = None    # stream time of the last stimulus sent
        self.last_dur = 0.0

    @property
    def latest(self) -> float:
        return float(self.t[-1]) if self.t.size else 0.0

    @property
    def t(self) -> np.ndarray:
        return self._ring.t if self._ring is not None else np.zeros(0)

    @property
    def data(self) -> np.ndarray | None:
        return self._ring.y if self._ring is not None else None

    def add(self, t: np.ndarray, data: np.ndarray) -> None:
        data = np.asarray(data, float)
        if self._ring is None or self._ring.n != data.shape[0]:
            self._ring = RingBuffer(data.shape[0], int(self.keep_s * self.rate))
        self._ring.append(t, data)

    def sent(self, trial: Trial, wave: np.ndarray, out_rate: float, delay_ms: float) -> None:
        tpl, pad = template(wave, out_rate, self.rate)
        now = self.latest
        if self.pending and self.pending[-1].t_next is None:
            self.pending[-1].t_next = now
        self.pending.append(_Pending(trial, now, tpl, pad, delay_ms / 1000.0, len(wave) / out_rate))
        self.last_fire, self.last_dur = now, len(wave) / out_rate

    def can_send(self, gap_s: float) -> bool:
        """True once the recording itself has moved on far enough since the last
        stimulus. Going by the clock alone isn't enough: after the computer stalls,
        a late trial would be sent straight after the one before, and the two
        stimuli couldn't be told apart."""
        if self.last_fire is None:
            return True
        need = max(0.8 * gap_s, self.last_dur + 2 * self.slack + 0.05)
        return self.latest - self.last_fire >= need

    def _span(self, pd: _Pending) -> tuple[float, float]:
        """Where the stimulus can start (stream time, s)."""
        lo = pd.t_fire - self.slack
        hi = (pd.t_next - self.slack) if pd.t_next is not None else pd.t_fire + self.SEARCH_AFTER_S
        return lo, max(hi, lo + 0.01)

    def resolve(self, p: Protocol, force: bool = False) -> list[Result]:
        """Results for sent stimuli whose data has all arrived (or all of them, when `force`)."""
        done, keep = [], []
        for pd in self.pending:
            ready = self._span(pd)[1] + pd.dur_s + p.after_ms / 1000.0 + 0.05
            if self.latest < ready and not force:
                keep.append(pd)
                continue
            done.append(self._result(pd, p))
        self.pending = keep
        return done

    def _result(self, pd: _Pending, p: Protocol) -> Result:
        if self.data is None or self.t.size == 0:
            return Result(pd.trial, False)
        s_lo, s_hi = self._span(pd)
        lo = max(0, int((s_lo - self.t[0]) * self.rate) - pd.pad)
        hi = min(self.t.size, int((s_hi + pd.dur_s - self.t[0]) * self.rate) + pd.pad + 1)
        k, match = find_stimulus(self.data[self.monitor_row, lo:hi], pd.tpl)
        if match < MATCH_THRESHOLD:
            return Result(pd.trial, False, match)
        t0 = float(self.t[lo + k + pd.pad]) + pd.delay_s
        got = cut(self.t, self.data, t0, p.before_ms, p.after_ms, self.rate)
        if got is None:
            return Result(pd.trial, False, match, t0)
        t_ms, raw = got
        return Result(pd.trial, True, match, t0, t_ms, self.to_units(raw))
