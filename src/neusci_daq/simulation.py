"""What `--backend simulated` records on each input, set on the Simulation tab and
saved in the config's [simulated] section.

Each input is a recording near one or more cells:

  trace = sine + noise + EPSPs + spikes

- Each stimulus pulse gives an EPSP (an alpha function) `epsp_delay_ms` later,
  `epsp_v_per_v` × the pulse's peak; pulses close together sum.
- The EPSPs depolarize the cells. Each cell fires at a rate that depends on that
  depolarization: max_rate / (1 + exp(-(d - threshold) / slope)), so it fires
  slowly at rest and faster as EPSPs push it past threshold.
- Each spike is an extracellular action potential (see spike_shape), at least
  2 ms after the cell's last one; some cells fire bursts.

The input wired to the stimulus ("Record on") records the stimulus instead.
Everything here is in volts on the trace, before channel scaling.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

import numpy as np

REFRACTORY_MS = 2.0


@dataclass
class SimCell:
    depth_v: float = 0.9          # spike trough, V on the trace (after the amplifier)
    width_ms: float = 0.55        # spike trough to repolarization peak
    peak_ratio: float = 0.35      # repolarization peak as a fraction of the trough
    max_rate: float = 100.0       # spikes/s when fully depolarized
    threshold_v: float = 0.255    # depolarization giving half the max rate
    slope_v: float = 0.05         # how sharply the rate rises around threshold
    burst: float = 0.0            # chance a spike starts a burst (2-4 spikes, 3-6 ms apart)

    def rate(self, d) -> np.ndarray:
        """Firing rate (spikes/s) at depolarization d (V)."""
        return self.max_rate / (1.0 + np.exp(-(np.asarray(d, float) - self.threshold_v) / self.slope_v))


def default_cells() -> list[SimCell]:
    """A narrow, fast-spiking-like cell; a medium one; and a broad, pyramidal-like one
    that sometimes bursts. Lowest threshold on the smallest (the size principle).
    At rest they fire about 1.0, 0.6 and 0.3 spikes/s."""
    return [SimCell(0.4, 0.30, 0.55, 100.0, 0.23, 0.05, 0.0),
            SimCell(0.9, 0.55, 0.35, 100.0, 0.255, 0.05, 0.0),
            SimCell(1.6, 0.75, 0.25, 100.0, 0.29, 0.05, 0.3)]


@dataclass
class SimInput:
    physical: str                 # "ai0"...
    noise_v: float = 0.02         # RMS
    sine_v: float = 0.5           # amplitude; 0 = no sine
    sine_hz: float = 1.0
    epsp_v_per_v: float = 0.3     # EPSP peak per volt of stimulus, for each pulse; 0 = none
    epsp_delay_ms: float = 5.0    # stimulus pulse to EPSP start
    epsp_peak_ms: float = 4.0     # EPSP start to its peak
    cells: list[SimCell] = field(default_factory=default_cells)


def default_input(physical: str) -> SimInput:
    """The defaults: aiN has a sine at N+1 Hz, so switching a channel's input visibly
    changes its trace."""
    k = int(physical[2:]) if physical[2:].isdigit() else 0
    return SimInput(physical, sine_hz=float(k + 1))


def input_for(inputs: list[SimInput], physical: str) -> SimInput:
    """The settings for `physical`: the configured ones, or the defaults."""
    return next((i for i in inputs if i.physical == physical), None) or default_input(physical)


# -- waveforms ---------------------------------------------------------------
def spike_shape(cell: SimCell, fs: float) -> tuple[np.ndarray, int]:
    """One extracellular action potential sampled at `fs`, as recorded near a cell
    body: a small positive bump, a sharp negative trough (`depth_v`) that falls
    faster than it recovers, then a slower positive repolarization peak `width_ms`
    later that decays over about a millisecond. Broader cells are slower in every
    phase. Returns the samples and the index of the trough, which always lands on a
    sample so the height doesn't depend on the sample rate (at 1 kHz the shape is
    only a blip; use 20 kHz or more to see it)."""
    k = np.arange(-int(np.ceil(1.0e-3 * fs)), int(np.ceil(3.0e-3 * fs)) + 1)
    t = k / fs * 1e3                                                    # ms from the trough
    b = max(cell.width_ms, 0.05) / 0.55                                 # 1 = a medium cell
    pre = 0.08 * np.exp(-((t + 0.3 * b) / (0.12 * b)) ** 2)
    bt = 0.6 + 0.4 * b                          # trough width varies less than the peak's timing
    trough = -np.exp(-(t / np.where(t < 0, 0.11 * bt, 0.19 * bt)) ** 2)
    tp = t - cell.width_ms
    peak = cell.peak_ratio * np.exp(-(tp / np.where(tp < 0, 0.25 * b, 0.6 * b)) ** 2)
    w = pre + trough + peak
    i = int(-k[0])
    return w * cell.depth_v / -w[i], i          # the other phases overlap the trough; keep it at -depth


def epsp_shape(inp: SimInput, fs: float) -> np.ndarray:
    """One EPSP of peak 1 V, starting `epsp_delay_ms` after sample 0 (an alpha
    function peaking `epsp_peak_ms` after it starts, lasting about 8× that)."""
    tau = max(inp.epsp_peak_ms, 0.05) / 1000.0
    t = np.arange(int(round(8 * tau * fs)) + 1) / fs
    a = (t / tau) * np.exp(1 - t / tau)
    return np.concatenate([np.zeros(int(round(inp.epsp_delay_ms / 1000.0 * fs))), a])


def pulse_onsets(wave: np.ndarray) -> list[tuple[int, float]]:
    """(sample, peak V) for each pulse in a stimulus: where |wave| rises above 5% of
    its peak, and the largest |value| until it falls back. A ramp or a sine is one
    pulse."""
    a = np.abs(np.asarray(wave, float))
    if not a.size or a.max() <= 0:
        return []
    on = a > 0.05 * a.max()
    edges = np.flatnonzero(np.diff(np.r_[0, on.astype(int), 0]))
    return [(int(s), float(a[s:e].max())) for s, e in zip(edges[::2], edges[1::2])]


# -- config ------------------------------------------------------------------
_INPUT_KEYS = {"noise_v": "noise", "sine_v": "sine_amplitude", "sine_hz": "sine_frequency",
               "epsp_v_per_v": "epsp", "epsp_delay_ms": "epsp_delay_ms", "epsp_peak_ms": "epsp_peak_ms"}
_CELL_KEYS = {"depth_v": "depth", "width_ms": "width_ms", "peak_ratio": "peak_ratio",
              "max_rate": "max_rate", "threshold_v": "threshold", "slope_v": "slope", "burst": "burst"}


def from_toml(sim: dict) -> list[SimInput]:
    """[simulated] -> inputs. Also reads the older `no_sine = ["ai1"]`."""
    out = []
    for raw in sim.get("inputs", []):
        inp = default_input(str(raw.get("physical", "ai0")).lower())
        for attr, key in _INPUT_KEYS.items():
            if key in raw:
                setattr(inp, attr, float(raw[key]))
        if "cells" in raw:
            inp.cells = []
            for rc in raw["cells"]:
                c = SimCell()
                for attr, key in _CELL_KEYS.items():
                    if key in rc:
                        setattr(c, attr, float(rc[key]))
                inp.cells.append(c)
        out.append(inp)
    for p in sim.get("no_sine", []):
        p = str(p).lower()
        inp = next((i for i in out if i.physical == p), None)
        if inp is None:
            inp = default_input(p)
            out.append(inp)
        inp.sine_v = 0.0
    return out


def to_toml_lines(inputs: list[SimInput]) -> list[str]:
    """Inputs -> TOML lines for the end of the [simulated] section."""
    lines = []
    for inp in inputs:
        lines += ["", "[[simulated.inputs]]", f'physical = "{inp.physical}"']
        lines += [f"{key} = {getattr(inp, attr):g}" for attr, key in _INPUT_KEYS.items()]
        for c in inp.cells:
            lines += ["", "[[simulated.inputs.cells]]"]
            lines += [f"{key} = {getattr(c, attr):g}" for attr, key in _CELL_KEYS.items()]
        if not inp.cells:
            lines.append("cells = []")
    return lines


def validate(inputs: list[SimInput]) -> None:
    for inp in inputs:
        where = f"[simulated] {inp.physical}"
        if inp.noise_v < 0 or inp.sine_v < 0 or inp.sine_hz < 0:
            raise ValueError(f"{where}: noise, sine amplitude and frequency can't be negative.")
        if inp.epsp_delay_ms < 0 or inp.epsp_peak_ms <= 0:
            raise ValueError(f"{where}: EPSP delay can't be negative and time to peak must be positive.")
        for n, c in enumerate(inp.cells, 1):
            if c.depth_v < 0 or c.width_ms <= 0 or c.max_rate < 0 or c.slope_v <= 0 or not 0 <= c.burst <= 1:
                raise ValueError(f"{where} cell {n}: spike size, rate can't be negative; width and "
                                 "slope must be positive; burst chance is between 0 and 1.")


def copy_input(inp: SimInput) -> SimInput:
    d = asdict(inp)
    d["cells"] = [SimCell(**c) for c in d["cells"]]
    return SimInput(**d)


CELL_FIELDS = [f.name for f in fields(SimCell)]
