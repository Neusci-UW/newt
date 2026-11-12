"""What `--backend simulated` records: a small network of cells, set on the
Neural network simulator page and saved in the config's [simulated] section.

The network is 1-10 leaky integrate-and-fire cells:

- Each cell's membrane potential relaxes toward rest + drive (time constant
  tau_ms), with noise, so it fires now and then on its own.
- When it reaches threshold the cell fires: an action potential, then it is held
  at reset for its refractory period.
- Each spike gives every other cell a PSP of synapses[pre][post] mV (positive
  excites, negative inhibits) synapse_delay_ms later. PSPs rise and fall with
  synapse_tau_ms, and sum.
- The stimulus is injected as a current that follows its waveform exactly, into
  each cell it drives. `stimulus` is the depolarization (mV) a 1 V, 1 ms pulse
  gives; it charges and leaks with the membrane time constant, so longer pulses
  depolarize more (a strength-duration curve), a long step makes the cell fire
  repeatedly (faster for a stronger step), and a slow sine can entrain it while
  a fast one is filtered out.

Each input records one cell: intracellularly (its membrane potential × gain:
resting level, PSPs, full action potentials) or extracellularly (its spikes as
an extracellular action potential of spike_depth_v). Inputs add their own noise
and an optional sine. The input wired to the stimulus ("Record on") records the
stimulus instead.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

import numpy as np

MAX_CELLS = 10
SIM_DT = 1e-4                     # s: the network is computed every 0.1 ms
AP_PEAK_MV = 30.0                 # intracellular action potential peak


@dataclass
class Cell:
    rest_mv: float = -70.0
    threshold_mv: float = -50.0
    reset_mv: float = -65.0
    tau_ms: float = 10.0          # membrane time constant
    refractory_ms: float = 2.0
    drive_mv: float = 8.0         # steady depolarization: with noise, sets the firing at rest (~1-2/s)
    noise_mv: float = 4.0         # membrane potential fluctuation (SD)
    stimulus: float = 0.0         # depolarization (mV) from a 1 V, 1 ms stimulus pulse; 0 = not driven
    spike_depth_v: float = 0.9    # extracellular recordings: spike trough on the trace
    spike_width_ms: float = 0.55  # extracellular: trough to repolarization peak
    synapses: list = field(default_factory=list)   # PSP (mV) this cell gives cell 1, 2, ...


@dataclass
class Recording:
    physical: str                 # "ai0"...
    cell: int = 0                 # index into Network.cells
    kind: str = "intracellular"   # or "extracellular"
    gain: float = 10.0            # intracellular: trace V per V of membrane potential
    noise_v: float = 0.005        # recording noise on the trace (RMS)
    sine_v: float = 0.0           # e.g. mains hum; 0 = none
    sine_hz: float = 60.0


@dataclass
class Network:
    cells: list = field(default_factory=list)
    synapse_delay_ms: float = 2.0
    synapse_tau_ms: float = 2.0
    recordings: list = field(default_factory=list)   # inputs not listed use default_recording

    def weights(self) -> np.ndarray:
        """synapses as an n×n matrix [pre, post] in mV; missing entries are 0 and a
        cell has no synapse onto itself."""
        n = len(self.cells)
        w = np.zeros((n, n))
        for i, c in enumerate(self.cells):
            row = list(c.synapses)[:n]
            w[i, :len(row)] = row
        np.fill_diagonal(w, 0.0)
        return w

    def set_weights(self, w: np.ndarray) -> None:
        for i, c in enumerate(self.cells):
            c.synapses = [float(v) for v in w[i]]


def default_network(n: int = 3) -> Network:
    """A chain: the stimulus drives cell 1, 1 excites 2, 2 excites 3, and 3
    inhibits 1. Cells differ in extracellular spike size, so they can be told apart."""
    sizes = [(0.4, 0.3), (0.9, 0.55), (1.6, 0.75)]
    cells = [Cell(spike_depth_v=sizes[i % 3][0], spike_width_ms=sizes[i % 3][1]) for i in range(n)]
    net = Network(cells)
    if n:
        cells[0].stimulus = 25.0
    w = np.zeros((n, n))
    for i in range(n - 1):
        w[i, i + 1] = 15.0
    if n >= 3:
        w[n - 1, 0] = -10.0
    net.set_weights(w)
    return net


def resize(net: Network, n: int) -> None:
    """Change the number of cells, keeping the settings of those that stay."""
    n = max(1, min(MAX_CELLS, int(n)))
    w = net.weights()
    while len(net.cells) < n:
        k = len(net.cells)
        net.cells.append(default_network(k + 1).cells[k])
    del net.cells[n:]
    new = np.zeros((n, n))
    m = min(n, w.shape[0])
    new[:m, :m] = w[:m, :m]
    net.set_weights(new)
    for r in net.recordings:
        r.cell = min(r.cell, n - 1)


def default_recording(physical: str, n_cells: int) -> Recording:
    """aiN records cell N+1 (wrapping round), intracellularly."""
    k = int(physical[2:]) if physical[2:].isdigit() else 0
    return Recording(physical, cell=k % max(1, n_cells))


def recording_for(net: Network, physical: str) -> Recording:
    return next((r for r in net.recordings if r.physical == physical), None) \
        or default_recording(physical, len(net.cells))


# -- the network -------------------------------------------------------------
def psp_scale(tau_ms: float, syn_tau_ms: float) -> float:
    """Peak membrane PSP (mV) from a synaptic kick of 1, so a synapse of w mV kicks w / this."""
    a, s = np.exp(-SIM_DT / (tau_ms / 1e3)), np.exp(-SIM_DT / (syn_tau_ms / 1e3))
    b = SIM_DT / (tau_ms / 1e3)
    k = np.arange(int(20 * max(tau_ms, syn_tau_ms) / 1e3 / SIM_DT) + 1)
    syn = s ** k
    v = np.zeros_like(syn)
    for i in range(1, len(k)):                      # short, and only when settings change
        v[i] = a * v[i - 1] + b * syn[i - 1]
    return float(v.max()) or 1.0


class NetworkState:
    """The network running in time, SIM_DT per step. Used by the simulated backend."""

    def __init__(self, net: Network, rng: np.random.Generator):
        self.rng = rng
        self.v = np.array([c.rest_mv + c.drive_mv for c in net.cells])
        self.syn = np.zeros(len(net.cells))
        self.refr = np.zeros(len(net.cells), int)
        self.pending: dict[int, np.ndarray] = {}     # step -> kicks arriving then
        self.stim: list[tuple[int, np.ndarray]] = []  # (first step, volts per step) being injected
        self.step = 0
        self.configure(net)

    def configure(self, net: Network) -> None:
        """Take new settings (the same cells keep their potentials)."""
        n = len(net.cells)
        if n != len(self.v):
            self.v = np.array([c.rest_mv + c.drive_mv for c in net.cells])
            self.syn, self.refr, self.pending = np.zeros(n), np.zeros(n, int), {}
        c = net.cells
        tau = np.array([x.tau_ms for x in c]) / 1e3
        self.a = np.exp(-SIM_DT / tau)
        self.b = SIM_DT / tau
        self.vinf = np.array([x.rest_mv + x.drive_mv for x in c])
        # OU noise with stationary SD noise_mv
        self.sigma = np.array([x.noise_mv for x in c]) * np.sqrt(1 - self.a ** 2)
        self.thr = np.array([x.threshold_mv for x in c])
        self.reset = np.array([x.reset_mv for x in c])
        self.nrefr = np.array([max(1, int(round(x.refractory_ms / 1e3 / SIM_DT))) for x in c])
        self.sdecay = np.exp(-SIM_DT / (net.synapse_tau_ms / 1e3))
        scale = np.array([psp_scale(x.tau_ms, net.synapse_tau_ms) for x in c])
        self.kick = net.weights() / scale[None, :]          # [pre, post]
        # injected current: 1 V held gives stim_gain mV at steady state; 1 V for 1 ms gives `stimulus`
        self.stim_gain = np.array([x.stimulus for x in c]) / (1 - np.exp(-1e-3 / tau))
        self.delay = max(1, int(round(net.synapse_delay_ms / 1e3 / SIM_DT)))

    def stimulate(self, wave: np.ndarray, fs: float, start_s: float = 0.0) -> None:
        """Inject a stimulus waveform (volts sampled at fs) starting start_s from now."""
        wave = np.asarray(wave, float)
        if not len(wave):
            return
        t = np.arange(int(np.ceil(len(wave) / fs / SIM_DT))) * SIM_DT
        per_step = np.interp(t, np.arange(len(wave)) / fs, wave, right=0.0)
        self.stim.append((self.step + int(round(start_s / SIM_DT)), per_step))

    def _drive(self, steps: int) -> np.ndarray:
        """The stimulus (V) at each of the next `steps` steps; forgets finished ones."""
        out = np.zeros(steps)
        keep = []
        for first, w in self.stim:
            a, b = max(first, self.step), min(first + len(w), self.step + steps)
            if b > a:
                out[a - self.step:b - self.step] += w[a - first:b - first]
            if first + len(w) > self.step + steps:
                keep.append((first, w))
        self.stim = keep
        return out

    def run(self, steps: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
        """Advance `steps`. Returns the membrane potential (steps × cells, mV, held at
        reset while refractory) and the spikes as (step within this run, cell)."""
        n = len(self.v)
        out = np.empty((steps, n))
        spikes = []
        noise = self.rng.standard_normal((steps, n)) * self.sigma
        inject = (1 - self.a) * self.stim_gain                  # exact for a constant current per step
        drive = self._drive(steps)
        v, syn, refr = self.v, self.syn, self.refr
        for k in range(steps):
            arriving = self.pending.pop(self.step, None)
            syn = syn * self.sdecay
            if arriving is not None:
                syn = syn + arriving
            v = self.vinf + (v - self.vinf) * self.a + self.b * syn + noise[k]
            if drive[k]:
                v = v + inject * drive[k]
            refr = refr - 1
            held = refr > 0
            if held.any():
                v = np.where(held, self.reset, v)
            fired = (v >= self.thr) & ~held
            if fired.any():
                for i in np.flatnonzero(fired):
                    spikes.append((k, int(i)))
                    t = self.step + self.delay
                    self.pending[t] = self.pending.get(t, 0) + self.kick[i]
                v = np.where(fired, self.reset, v)
                refr = np.where(fired, self.nrefr, refr)
            out[k] = v
            self.step += 1
        self.v, self.syn, self.refr = v, syn, refr
        return out, spikes


# -- what an input sees ------------------------------------------------------
def intracellular_ap(cell: Cell, fs: float) -> np.ndarray:
    """mV from the spike on: threshold up to AP_PEAK_MV in 0.25 ms, down to 5 mV below
    reset by 1 ms (the afterhyperpolarization), then back to reset by 1.4 ms."""
    t = np.arange(int(round(1.4e-3 * fs)) + 1) / fs * 1e3
    up = cell.threshold_mv + (AP_PEAK_MV - cell.threshold_mv) * np.sin(np.pi / 2 * np.clip(t / 0.25, 0, 1)) ** 2
    ahp = cell.reset_mv - 5.0
    down = AP_PEAK_MV + (ahp - AP_PEAK_MV) * np.sin(np.pi / 2 * np.clip((t - 0.25) / 0.75, 0, 1)) ** 2
    back = ahp + (cell.reset_mv - ahp) * np.clip((t - 1.0) / 0.4, 0, 1)
    return np.where(t < 0.25, up, np.where(t < 1.0, down, back))


def spike_shape(cell: Cell, fs: float) -> tuple[np.ndarray, int]:
    """One extracellular action potential sampled at `fs`, as recorded near a cell
    body: a small positive bump, a sharp negative trough (`spike_depth_v`) that
    falls faster than it recovers, then a slower positive repolarization peak
    `spike_width_ms` later. Broader cells are slower in every phase. Returns the
    samples and the index of the trough."""
    k = np.arange(-int(np.ceil(1.0e-3 * fs)), int(np.ceil(3.0e-3 * fs)) + 1)
    t = k / fs * 1e3                                                    # ms from the trough
    b = max(cell.spike_width_ms, 0.05) / 0.55                           # 1 = a medium cell
    pre = 0.08 * np.exp(-((t + 0.3 * b) / (0.12 * b)) ** 2)
    bt = 0.6 + 0.4 * b                          # trough width varies less than the peak's timing
    trough = -np.exp(-(t / np.where(t < 0, 0.11 * bt, 0.19 * bt)) ** 2)
    ratio = 0.55 - 0.3 * min(1.0, max(0.0, (cell.spike_width_ms - 0.3) / 0.45))   # broad: smaller peak
    tp = t - cell.spike_width_ms
    peak = ratio * np.exp(-(tp / np.where(tp < 0, 0.25 * b, 0.6 * b)) ** 2)
    w = pre + trough + peak
    i = int(-k[0])
    return w * cell.spike_depth_v / -w[i], i


# -- config ------------------------------------------------------------------
_CELL_KEYS = {"rest_mv": "rest", "threshold_mv": "threshold", "reset_mv": "reset", "tau_ms": "tau_ms",
              "refractory_ms": "refractory_ms", "drive_mv": "drive", "noise_mv": "noise",
              "stimulus": "stimulus", "spike_depth_v": "spike_depth", "spike_width_ms": "spike_width_ms"}
_REC_KEYS = {"gain": "gain", "noise_v": "noise", "sine_v": "sine_amplitude", "sine_hz": "sine_frequency"}


def from_toml(sim: dict) -> Network:
    """[simulated] -> the network. Without [[simulated.cells]] it's the default
    three-cell chain; older per-input settings keep their noise and sine."""
    if "cells" in sim:
        cells = []
        for raw in sim["cells"]:
            c = Cell()
            for attr, key in _CELL_KEYS.items():
                if key in raw:
                    setattr(c, attr, float(raw[key]))
            c.synapses = [float(v) for v in raw.get("synapses", [])]
            cells.append(c)
        net = Network(cells)
    else:
        net = default_network()
    net.synapse_delay_ms = float(sim.get("synapse_delay_ms", net.synapse_delay_ms))
    net.synapse_tau_ms = float(sim.get("synapse_tau_ms", net.synapse_tau_ms))
    for raw in sim.get("inputs", []):
        r = default_recording(str(raw.get("physical", "ai0")).lower(), len(net.cells))
        if "cell" in raw:
            r.cell = int(raw["cell"]) - 1                  # 1-based in the file, like the tab
        r.kind = str(raw.get("recording", r.kind)).lower()
        for attr, key in _REC_KEYS.items():
            if key in raw:
                setattr(r, attr, float(raw[key]))
        net.recordings.append(r)
    return net


def to_toml_lines(net: Network) -> list[str]:
    lines = [f"synapse_delay_ms = {net.synapse_delay_ms:g}", f"synapse_tau_ms = {net.synapse_tau_ms:g}"]
    w = net.weights()
    for i, c in enumerate(net.cells):
        lines += ["", "[[simulated.cells]]"]
        lines += [f"{key} = {getattr(c, attr):g}" for attr, key in _CELL_KEYS.items()]
        lines.append("synapses = [" + ", ".join(f"{v:g}" for v in w[i]) + "]   # mV onto cell 1, 2, ...")
    for r in net.recordings:
        lines += ["", "[[simulated.inputs]]", f'physical = "{r.physical}"', f"cell = {r.cell + 1}",
                  f'recording = "{r.kind}"']
        lines += [f"{key} = {getattr(r, attr):g}" for attr, key in _REC_KEYS.items()]
    return lines


def validate(net: Network) -> None:
    if not 1 <= len(net.cells) <= MAX_CELLS:
        raise ValueError(f"[simulated] needs 1 to {MAX_CELLS} cells.")
    if net.synapse_delay_ms < 0 or net.synapse_tau_ms <= 0:
        raise ValueError("[simulated] synapse delay can't be negative and its time constant must be positive.")
    for n, c in enumerate(net.cells, 1):
        if c.tau_ms <= 0 or c.refractory_ms < 0 or c.noise_mv < 0:
            raise ValueError(f"[simulated] cell {n}: time constant must be positive; refractory "
                             "period and noise can't be negative.")
        if c.reset_mv >= c.threshold_mv:
            raise ValueError(f"[simulated] cell {n}: reset must be below threshold.")
        if c.spike_depth_v < 0 or c.spike_width_ms <= 0:
            raise ValueError(f"[simulated] cell {n}: spike size can't be negative and width must be positive.")
    for r in net.recordings:
        if not 0 <= r.cell < len(net.cells):
            raise ValueError(f"[simulated] {r.physical}: there is no cell {r.cell + 1}.")
        if r.kind not in ("intracellular", "extracellular"):
            raise ValueError(f"[simulated] {r.physical}: recording must be intracellular or extracellular.")
        if r.noise_v < 0 or r.sine_v < 0 or r.sine_hz < 0:
            raise ValueError(f"[simulated] {r.physical}: noise and sine can't be negative.")


def copy_network(net: Network) -> Network:
    return network_from_dict(asdict(net))


def network_from_dict(d: dict) -> Network:
    """A network from asdict(network), as saved in a .newt file."""
    return Network([Cell(**c) for c in d["cells"]], d["synapse_delay_ms"], d["synapse_tau_ms"],
                   [Recording(**r) for r in d["recordings"]])


CELL_FIELDS = [f.name for f in fields(Cell)]
