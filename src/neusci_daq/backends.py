"""Acquisition backends. Each runs a thread that publishes Chunks to subscriber queues.

Data is always published as raw volts; per-channel scaling happens at display/export
time so the recorded file keeps what the ADC actually measured.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import numpy as np

from . import simulation as sim
from .config import Config


@dataclass
class Chunk:
    data: np.ndarray      # (n_channels, n_samples), volts
    t: np.ndarray         # (n_samples,), seconds since start
    start_index: int      # index of first sample in the stream


class Backend:
    hardware_timed = True

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.actual_rate = cfg.sample_rate
        self.error: BaseException | None = None
        self._subs: list[queue.Queue] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- public API -------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self._subs = self._subs + [q]       # replaced, not changed, so _publish never sees it mid-change
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """Stop sending chunks to `q` (a queue nobody reads would grow forever)."""
        self._subs = [s for s in self._subs if s is not q]

    def start(self) -> None:
        """Configure hardware in the caller's thread so setup errors raise here."""
        self.error = None
        self._stop.clear()
        self._setup()
        self._thread = threading.Thread(target=self._run, daemon=True, name="daq-acq")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- internals --------------------------------------------------------
    def _run(self):
        try:
            self._loop()
        except BaseException as e:  # surfaced to the GUI via .error
            self.error = e
        finally:
            try:
                self._teardown()
            except Exception:
                pass

    def _publish(self, chunk: Chunk):
        for q in self._subs:
            q.put(chunk)

    def _setup(self): ...
    def _loop(self): raise NotImplementedError
    def _teardown(self): ...


class SimulatedBackend(Backend):
    """Fake signals for developing and teaching without hardware. What each input
    records (noise, sine, EPSPs after each stimulus pulse, and spiking cells whose
    rate follows the EPSPs) is set per input on the Simulation tab; see simulation.py.
    Settings changed while running apply from the next chunk."""

    _pending = None    # (monitor input, waveform at the input rate) from SimulatedOutput

    def _setup(self):
        self._rng = np.random.default_rng(0)
        self._stim = None             # (monitor input, waveform, position) being played
        self._epsp: dict[str, np.ndarray] = {}    # per input: depolarization still to come
        self._tail: dict[str, np.ndarray] = {}    # per input: spike samples past the last chunk
        self._last_spike: dict = {}               # (input, cell #) -> sample index of last spike
        self._shapes: dict = {}                   # (cell, fs) -> spike_shape

    def inject(self, monitor: str, wave: np.ndarray, rate: float) -> None:
        """Play a stimulus: `wave` appears on the `monitor` input, and every other
        input gets an EPSP per pulse (simulation only)."""
        fs = self.cfg.sample_rate
        t_in = np.arange(len(wave)) / rate
        t_ai = np.arange(0, len(wave) / rate, 1.0 / fs)
        w = np.interp(t_ai, t_in, wave) if len(wave) > 1 else np.asarray(wave, float)
        self._pending = (monitor, w)

    def _start_stimulus(self, monitor, w, inputs, fs):
        self._stim = (monitor, w, 0)
        onsets = sim.pulse_onsets(w)
        for ch in self.cfg.channels:
            inp = sim.input_for(inputs, ch.physical)
            if ch.physical == monitor or not onsets or not inp.epsp_v_per_v:
                continue
            k = sim.epsp_shape(inp, fs)
            resp = np.zeros(onsets[-1][0] + len(k))
            for i, amp in onsets:
                resp[i:i + len(k)] += inp.epsp_v_per_v * amp * k
            old = self._epsp.get(ch.physical, np.zeros(0))
            if len(old) < len(resp):
                old = np.concatenate([old, np.zeros(len(resp) - len(old))])
            old[:len(resp)] += resp                   # EPSPs sum with any still going
            self._epsp[ch.physical] = old

    def _take_epsp(self, phys: str, cs: int) -> np.ndarray:
        e = self._epsp.get(phys, np.zeros(0))
        d = np.zeros(cs)
        d[:min(cs, len(e))] = e[:cs]
        self._epsp[phys] = e[cs:]
        return d

    def _spikes(self, phys: str, cells, d: np.ndarray, idx: int, fs: float) -> np.ndarray:
        """Spikes for this chunk on one input, each cell firing at cell.rate(d). A
        spike that runs past the chunk's end is added to the start of the next one."""
        rng, cs = self._rng, len(d)
        shapes = []
        for c in cells:
            key = (tuple(vars(c).values()), fs)
            if key not in self._shapes:
                self._shapes[key] = sim.spike_shape(c, fs)
            shapes.append(self._shapes[key])
        tail = self._tail.get(phys, np.zeros(0))
        pad = max([len(w) for w, _ in shapes] + [0]) + int(np.ceil(0.018 * fs)) + 1
        out = np.zeros(cs + max(pad, len(tail)))
        out[:len(tail)] += tail
        refractory = max(1, int(round(sim.REFRACTORY_MS / 1000 * fs)))
        for n, (c, (w, trough)) in enumerate(zip(cells, shapes)):
            last = self._last_spike.get((phys, n), -10 ** 12)
            for j in np.flatnonzero(rng.random(cs) < c.rate(d) / fs):
                if idx + j - last < refractory:
                    continue
                gain = 1 + 0.05 * rng.standard_normal()           # each spike's size varies ~5%
                for _ in range(1 + (rng.integers(1, 4) if rng.random() < c.burst else 0)):
                    a = j - trough                                # where the spike starts in this chunk
                    out[max(a, 0):a + len(w)] += gain * w[max(-a, 0):]
                    last = idx + j
                    j += max(refractory, int(round(rng.uniform(3, 6) * 1e-3 * fs)))
                    gain *= 0.8                                   # bursts shrink
            self._last_spike[(phys, n)] = last
        self._tail[phys] = out[cs:].copy()
        return out[:cs]

    def _loop(self):
        fs, cs = self.cfg.sample_rate, self.cfg.chunk_size
        idx, t0 = 0, time.perf_counter()
        while not self._stop.is_set():
            inputs = self.cfg.sim_inputs                  # may be edited while running
            t = (idx + np.arange(cs)) / fs
            if self._pending is not None:
                monitor, w = self._pending
                self._pending = None
                self._start_stimulus(monitor, w, inputs, fs)
            stim = np.zeros(cs)
            if self._stim is not None:
                monitor, w, pos = self._stim
                seg = w[pos:pos + cs]
                stim[:len(seg)] = seg
                self._stim = None if pos + cs >= len(w) else (monitor, w, pos + cs)
            data = np.empty((len(self.cfg.channels), cs))
            for i, ch in enumerate(self.cfg.channels):
                if ch.physical == self.cfg.stim_monitor:
                    data[i] = 0.002 * self._rng.standard_normal(cs) + stim    # a clean wire
                    continue
                inp = sim.input_for(inputs, ch.physical)
                d = self._take_epsp(ch.physical, cs)
                data[i] = (inp.sine_v * np.sin(2 * np.pi * inp.sine_hz * t)
                           + inp.noise_v * self._rng.standard_normal(cs)
                           + d + self._spikes(ch.physical, inp.cells, d, idx, fs))
            self._publish(Chunk(data, t, idx))
            idx += cs
            # pace to real time
            delay = t0 + idx / fs - time.perf_counter()
            if delay > 0:
                time.sleep(delay)


class NidaqmxBackend(Backend):
    """Hardware-timed continuous acquisition with NI's official `nidaqmx` package.
    All channels are in ONE task, so each scan samples every channel in order on the
    device clock (still multiplexed: ~10 us inter-channel skew, not simultaneous)."""

    def _setup(self):
        import nidaqmx
        from nidaqmx.constants import AcquisitionType, TerminalConfiguration
        from nidaqmx.stream_readers import AnalogMultiChannelReader

        term = {"RSE": TerminalConfiguration.RSE,
                "DIFF": getattr(TerminalConfiguration, "DIFF", None)
                or getattr(TerminalConfiguration, "DIFFERENTIAL")}[self.cfg.terminal_config]
        self._task = nidaqmx.Task()
        try:
            for ch in self.cfg.channels:
                self._task.ai_channels.add_ai_voltage_chan(
                    f"{self.cfg.device}/{ch.physical}", name_to_assign_to_channel=ch.name,
                    terminal_config=term, min_val=ch.range[0], max_val=ch.range[1])
            # Host buffer: ~10 s, so a GUI hiccup doesn't overflow the 2047-sample FIFO.
            buf = int(max(10 * self.cfg.sample_rate, 10 * self.cfg.chunk_size))
            self._task.timing.cfg_samp_clk_timing(
                rate=self.cfg.sample_rate, sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=buf)
            self.actual_rate = self._task.timing.samp_clk_rate  # device may coerce
            self._reader = AnalogMultiChannelReader(self._task.in_stream)
            self._task.start()
        except Exception:
            self._task.close()
            raise

    def _loop(self):
        n, cs, idx = len(self.cfg.channels), self.cfg.chunk_size, 0
        while not self._stop.is_set():
            data = np.empty((n, cs))
            # Blocks until cs samples/channel are available. Overflow raises
            # DaqError -200279, which ends the run and is shown in the GUI.
            self._reader.read_many_sample(data, number_of_samples_per_channel=cs, timeout=10.0)
            t = (idx + np.arange(cs)) / self.actual_rate
            self._publish(Chunk(data, t, idx))
            idx += cs

    def _teardown(self):
        self._task.stop()
        self._task.close()


class MultiChannelIOBackend(Backend):
    """Adapter for sjfleming/MultiChannelIO. SOFTWARE-TIMED polling:
    every sample is a separate start/read/stop per channel, so the rate is whatever
    the USB round-trips allow, with jitter, and channels are not sampled together.
    Timestamps are recorded per scan; `actual_rate` reports what was achieved."""

    hardware_timed = False

    def _setup(self):
        try:
            from MultiChannelIO import MultiChannelIO
        except ImportError as e:
            raise ImportError("MultiChannelIO.py not importable. Clone sjfleming/MultiChannelIO "
                              "and add it to PYTHONPATH (see README).") from e
        dev = self.cfg.device
        self._names = [f"{dev}/{ch.physical}" for ch in self.cfg.channels]
        # Override the library defaults: scaling would otherwise be x100,
        # reset=True would zero your outputs, and the DO line defaults to Dev1.
        self._daq = MultiChannelIO(
            self._names, [], digitalChannels=[f"/{dev}/port0/line0"],
            scalings=[1.0] * len(self._names),
            limit=[tuple(ch.range) for ch in self.cfg.channels],
            reset=False)
        self._daq.configure()
        self.actual_rate = float("nan")

    def _loop(self):
        n, cs, fs = len(self._names), self.cfg.chunk_size, self.cfg.sample_rate
        period, idx = 1.0 / fs, 0
        t0 = time.perf_counter()
        next_t = t0
        data, ts = np.empty((n, cs)), np.empty(cs)
        k = 0
        while not self._stop.is_set():
            ts[k] = time.perf_counter() - t0
            for i, name in enumerate(self._names):
                data[i, k] = self._daq.read(name, 1)
            k += 1
            if k == cs:
                self.actual_rate = (cs - 1) / (ts[-1] - ts[0]) if ts[-1] > ts[0] else float("nan")
                self._publish(Chunk(data.copy(), ts.copy(), idx))
                idx += cs
                k = 0
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()  # behind: don't burst to catch up

    def _teardown(self):
        from PyDAQmx.DAQmxFunctions import DAQmxClearTask  # library never clears its tasks
        for handles in (self._daq.inputTaskHandles, self._daq.outputTaskHandles,
                        self._daq.digitalTaskHandles):
            for h in handles.values():
                DAQmxClearTask(h)


BACKENDS = {"nidaqmx": NidaqmxBackend, "multichannelio": MultiChannelIOBackend,
            "simulated": SimulatedBackend}


def make_backend(cfg: Config) -> Backend:
    return BACKENDS[cfg.backend](cfg)
