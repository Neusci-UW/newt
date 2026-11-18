"""Simple Stimulus's stimulus in plain Python: the pulse types, their settings, and the
Stimulus each sends (volts at the output). Shared by the Qt page (simplestim.py) and the
HTML front end; moved here from simplestim.py, unchanged."""
from __future__ import annotations

from dataclasses import replace

from .stim import Stimulus, to_segments

MODES = (("single", "Single pulse"), ("pair", "Pulse pair"), ("train", "Train"),
         ("sine", "Sine wave"), ("triangle", "Triangle wave"))
WAVES = ("sine", "triangle")
FIELDS = {"single": ("level", "width", "offset"), "pair": ("level", "width", "interval", "offset"),
          "train": ("level", "width", "rate", "count", "offset"), "sine": ("level", "freq", "dur", "offset"),
          "triangle": ("level", "freq", "dur", "offset")}
MARKED = {"single": ("level", "width"), "pair": ("level", "interval"), "train": ("level", "rate", "count"),
          "sine": ("level", "freq", "dur"), "triangle": ("level", "freq", "dur")}
NAMES = {"level": "Amplitude", "width": "Width", "interval": "Interval", "rate": "Rate", "count": "Pulses",
         "freq": "Frequency", "dur": "Duration", "offset": "Offset"}
MAX_CYCLES = 500               # a triangle wave is made of ramps: 3 per cycle
DELAY_MS = 2.0                 # 0 V before the first pulse, so the recorded stimulus has a clear start
DEFAULTS = dict(mode="single", level=1.0, width=0.2, interval=20.0, rate=20.0, count=5,
                every=2.0, ramp=False, ramp_by=0.2, ramp_to=10.0, freq=10.0, dur=500.0, offset=0.0,
                windows=False, before=100.0, after=500.0, repeats=0)


def triangle_parts(amp_v: float, freq_hz: float, dur_ms: float) -> list[dict]:
    """A triangle wave around 0 V as ramp segments: up to +amp, down to −amp, back to 0,
    for as many whole cycles as fit in `dur_ms` (at least one)."""
    period = 1000.0 / freq_hz
    cycles = max(1, min(MAX_CYCLES, round(dur_ms / period)))
    ramp = lambda a, b, ms: {"kind": "ramp", "start": a, "level": b, "dur": ms, "d_level": "", "d_dur": ""}
    parts = [{"kind": "base", "level": 0.0, "dur": DELAY_MS, "d_level": "", "d_dur": ""}]
    for _ in range(cycles):
        parts += [ramp(0.0, amp_v, period / 4), ramp(amp_v, -amp_v, period / 2), ramp(-amp_v, 0.0, period / 4)]
    return parts


def shape(v: dict, scale: float = 1.0) -> Stimulus:
    """A pulse, two, a train, or a wave, before the offset (`v`: DEFAULTS' keys; levels in
    the stimulator's units, `scale` of them to a volt)."""
    mode = v["mode"]
    amp, freq, dur = v["level"] / scale, v["freq"], v["dur"]
    if mode == "sine":
        return Stimulus(shape="sine", delay_ms=DELAY_MS, amplitude_v=amp, freq_hz=freq, duration_ms=dur)
    if mode == "triangle":
        return Stimulus(shape="segments", parts=triangle_parts(amp, freq, dur))
    count = {"single": 1, "pair": 2}.get(mode, int(v["count"]))
    interval = {"single": 100.0, "pair": v["interval"]}.get(mode, 1000.0 / v["rate"])
    return Stimulus(shape="pulse", delay_ms=DELAY_MS, amplitude_v=amp, width_ms=v["width"], count=count,
                    interval_ms=interval)


def stimulus(v: dict, scale: float = 1.0) -> Stimulus:
    """What Stimulate sends, in volts at the output, on its offset (from the first sample to
    the last; the output goes back to 0 V after)."""
    s = to_segments(shape(v, scale))
    off = v["offset"] / scale
    return replace(s, parts=[{**p, "offset": off} for p in s.parts]) if off else s


def describe(v: dict, units: str = "V") -> str:
    mode = v["mode"]
    off = f" · offset {v['offset']:g} {units}" if v["offset"] else ""
    if mode in WAVES:
        return (f"{'Sine' if mode == 'sine' else 'Triangle'} {v['level']:g} {units} · "
                f"{v['freq']:g} Hz · {v['dur']:g} ms" + off)
    s = f"{v['level']:g} {units} · {v['width']:g} ms"
    if mode == "pair":
        s += f" · pair {v['interval']:g} ms apart"
    elif mode == "train":
        s += f" · {int(v['count'])} at {v['rate']:g} Hz"
    return s + off
