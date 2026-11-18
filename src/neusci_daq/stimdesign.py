"""Stimulus Design in plain Python: the kinds of segment "+ Add segment" offers, each kind's
settings, the words that explain them, where each segment sits in the waveform, the trial
colors, and the picked segment's numbers for the picture. Shared by the Qt page
(designparts.py, stimuluspage.py) and the HTML front end (web/design.py); moved here from
designparts.py and stimuluspage.py, unchanged."""
from __future__ import annotations

import math

import numpy as np

from . import stim as stimmod

MAX_FADED = 30               # trials drawn faded behind the one shown

# (label in the picker, kind, settings it starts with, a few words)
KINDS = [("Baseline", "base", {}, "0 V for a while"),
         ("Step", "step", {}, "hold a level"),
         ("Pulse", "pulse", {}, "one short pulse"),
         ("Train", "pulse", {"count": 5, "interval": 50.0}, "pulses at a rate"),
         ("Ramp", "ramp", {}, "from one level to another"),
         ("Sine", "sine", {}, "amplitude, frequency"),
         ("Triangle", "triangle", {}, "straight up and down"),
         ("Square", "square", {}, "on and off at a frequency"),
         ("Chirp", "chirp", {}, "a frequency sweep"),
         ("Noise", "noise", {}, "random, Gaussian"),
         ("Exponential", "exp", {}, "a jump, then a decay"),
         ("Alpha", "alpha", {}, "an EPSC / EPSP shape"),
         ("Custom", "points", {}, "points you type, or a file")]

# Each kind's settings: (key, label, what it is, its per-trial key). What it is: "level"
# (stimulator units), "ms", "Hz", "x" (a multiplier) or "n" (a count).
FIELDS = {
    "base": [("dur", "Duration", "ms", "d_dur")],
    "step": [("level", "Level", "level", "d_level"), ("dur", "Duration", "ms", "d_dur")],
    "pulse": [("level", "Height", "level", "d_level"), ("dur", "Width", "ms", "d_dur"),
              ("count", "Pulses", "n", None), ("interval", "Interval", "ms", None)],
    "ramp": [("start", "From", "level", None), ("level", "To", "level", "d_level"), ("dur", "Duration", "ms", "d_dur")],
    "sine": [("level", "Amplitude", "level", "d_level"), ("freq", "Frequency", "Hz", None),
             ("dur", "Duration", "ms", "d_dur")],
    "triangle": [("level", "Amplitude", "level", "d_level"), ("freq", "Frequency", "Hz", None),
                 ("dur", "Duration", "ms", "d_dur")],
    "square": [("level", "Level", "level", "d_level"), ("freq", "Frequency", "Hz", None),
               ("dur", "Duration", "ms", "d_dur")],
    "chirp": [("level", "Start amplitude", "level", "d_level"), ("level2", "End amplitude", "level", None),
              ("freq", "Start", "Hz", None), ("freq2", "End", "Hz", None), ("dur", "Duration", "ms", "d_dur")],
    "noise": [("level", "SD", "level", "d_level"), ("dur", "Duration", "ms", "d_dur")],
    "exp": [("level", "Jump", "level", "d_level"), ("tau", "τ (decay)", "ms", None), ("dur", "Duration", "ms", "d_dur")],
    "alpha": [("level", "Peak", "level", "d_level"), ("tau", "Peak at", "ms", None), ("dur", "Duration", "ms", "d_dur")],
    "points": [("level", "Scale", "x", "d_level")],
    "file": [("level", "Scale", "x", "d_level")],
}
for _f in FIELDS.values():                   # every kind can sit on a DC offset
    _f.append(("offset", "Offset", "level", None))
HINTS = {"base": "0 V at the output.", "step": "Holds a level, then the next segment starts.",
         "pulse": "One pulse, or a train of them.", "ramp": "A straight line from one level to another.",
         "sine": "A sine wave around the offset.", "triangle": "Straight up to the amplitude and down to minus it.",
         "square": "On at the level for the first half of each cycle, then 0.",
         "chirp": "A sine wave whose frequency and amplitude sweep from start to end.",
         "noise": "Random values around 0, Gaussian with this standard deviation.",
         "exp": "Jumps to the level, then decays with τ.", "alpha": "Rises and falls like an EPSC, peaking at τ.",
         "points": "Your own shape: times and levels, joined by lines or steps (levels × Scale).",
         "file": "A waveform from a file (one column of volts, or time and volts), × Scale."}


def seg_name(p: dict) -> str:
    """A segment's kind in words: "Train" for a pulse that repeats."""
    kind = p.get("kind")
    return "Train" if kind == "pulse" and int(p.get("count", 1)) > 1 else stimmod.SEG_KINDS.get(kind, kind)


def icon_points(kind: str, extra: dict, w: int = 64, h: int = 30) -> list[tuple[float, float]]:
    """A small picture of a kind of segment, for the picker: its line's points in a w × h box."""
    m = h / 2
    if kind == "base":
        pts = [(2, m), (w - 2, m)]
    elif kind == "step":
        pts = [(2, 24), (18, 24), (18, 6), (46, 6), (46, 24), (62, 24)]
    elif kind == "pulse" and extra.get("count", 1) > 1:
        pts = [(2, 24)] + sum([[(8 + i * 12, 24), (8 + i * 12, 6), (12 + i * 12, 6), (12 + i * 12, 24)]
                               for i in range(5)], []) + [(62, 24)]
    elif kind == "pulse":
        pts = [(2, 24), (28, 24), (28, 6), (34, 6), (34, 24), (62, 24)]
    elif kind == "ramp":
        pts = [(2, 24), (14, 24), (50, 6), (50, 24), (62, 24)]
    elif kind == "sine":
        pts = [(2 + i, m - 9 * math.sin(i / 60 * 4 * math.pi)) for i in range(61)]
    elif kind == "triangle":
        pts = [(2, m), (9.5, 6), (24.5, 24), (39.5, 6), (54.5, 24), (62, m)]
    elif kind == "square":
        pts = [(2, 6), (17, 6), (17, 24), (32, 24), (32, 6), (47, 6), (47, 24), (62, 24)]
    elif kind == "chirp":
        pts = [(2 + i * 0.5, m - 9 * math.sin(2 * math.pi * (i / 120) ** 2 * 6)) for i in range(121)]
    elif kind == "noise":
        r = np.random.default_rng(2)
        pts = [(2 + i, m + float(np.clip(r.normal(0, 4), -10, 10))) for i in range(61)]
    elif kind == "exp":
        pts = [(2, 24), (10, 24), (10, 6)] + [(10 + i, 24 - 18 * math.exp(-i / 10)) for i in range(53)]
    elif kind == "alpha":
        pts = [(2, 24)] + [(10 + i, 24 - 18 * (i / 8) * math.exp(1 - i / 8)) for i in range(53)]
    else:
        pts = [(2, 24), (14, 24), (20, 8), (28, 18), (36, 10), (44, 24), (62, 24)]
    return [(x * w / 64, y * h / 30) for x, y in pts]


def segment_spans(s: stimmod.Stimulus, rate: float) -> list[tuple[int, int]]:
    """Where each segment is in the waveform, in samples: [(start, end)]. A segment that
    can't be made (a bad setting) spans its nominal duration."""
    t0, at, out = s.delay_ms, stimmod.samples(s.delay_ms, rate), []
    for p in s.parts:
        try:
            n = len(stimmod._part_wave(p, rate, t0))
        except (ValueError, OSError):
            n = stimmod.span(t0, stimmod._part_ms(p), rate)
        out.append((at, at + n))
        at += n
        t0 += n * 1000.0 / rate if p.get("kind") == "file" else stimmod._part_ms(p)
    return out


# Trial colors: a continuous map from the first trial to the last (dark blue, teal,
# amber, red-orange), readable on light and dark backgrounds.
TRIAL_STOPS = ((0.0, (40, 70, 190)), (0.35, (16, 150, 150)), (0.7, (225, 160, 30)), (1.0, (215, 70, 40)))


def trial_rgb(f: float) -> tuple[int, int, int]:
    """The color for a trial at fraction f (0 = the first, 1 = the last), as (r, g, b)."""
    f = min(1.0, max(0.0, f))
    for (a, ca), (b, cb) in zip(TRIAL_STOPS, TRIAL_STOPS[1:]):
        if f <= b:
            u = (f - a) / (b - a)
            return tuple(round(x + (y - x) * u) for x, y in zip(ca, cb))
    return TRIAL_STOPS[-1][1]


def dims(p: dict, units: str, k: float, every: bool = False) -> list[str]:
    """A segment's main numbers, for the picture: levels in `units` (volts × k). `every`:
    every setting can have a per-trial change (stim.PER_TRIAL), not only those in FIELDS."""
    kind = p.get("kind")
    lv = lambda key: f"{float(p.get(key, 0)) * k:g} {units}"
    out = []
    for key, label, what, dkey in FIELDS.get(kind, []):
        if every and key in stimmod.PER_TRIAL:
            dkey = "d_" + key
        if key == "offset":
            if float(p.get("offset", 0) or 0) or (dkey and p.get(dkey)):
                out.append(f"offset {lv('offset')}")
            else:
                continue
        elif key == "level2":
            if float(p.get("level2", p.get("level", 0))) != float(p.get("level", 0)) or (dkey and p.get(dkey)):
                out[-1] = out[-1].split(" (")[0] + f" → {lv('level2')}"
            else:
                continue
        elif what == "n" and int(p.get(key, 1)) <= 1 and not (dkey and p.get(dkey)):
            continue
        elif what == "level":
            out.append(lv(key))
        elif what == "ms":
            out.append(f"{float(p.get(key, 0)):g} ms" if key == "dur" else f"{label} {float(p.get(key, 0)):g} ms")
        elif what == "Hz":
            out.append(f"{float(p.get(key, 0)):g} Hz")
        elif what == "n":
            out.append(f"×{int(p.get(key, 1))}")
        else:
            continue
        if dkey and p.get(dkey):
            got = stimmod.parse_per_trial(p[dkey])
            kk = k if what == "level" else 1.0
            out[-1] += f" ({stimmod.format_per_trial(got, kk)}{' / trial' if got[0] == 'step' else ''})"
    if kind == "chirp" and len(out) >= 3:              # amplitude, start Hz, end Hz …
        out[1:3] = [f"{out[1]} → {out[2]}"]
    return out


def trials_note(s: stimmod.Stimulus, n: int, s2: stimmod.Stimulus | None = None) -> str:
    """What's wrong between the per-trial changes and How many, or ""."""
    lists = s.list_lengths() + (s2.list_lengths() if s2 is not None else [])
    steps = s.steps or (s2 is not None and s2.steps)
    if n == 1 and steps:
        return "Set How many above 1 to use the per-trial changes."
    if lists and min(lists) < n:
        return f"A per-trial list has only {min(lists)} values."
    return ""
