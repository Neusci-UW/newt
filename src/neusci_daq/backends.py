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


# Simulated units, after a ×1000 amplifier: (trough depth in V, spikes/s, trough-to-peak
# in ms, repolarization peak as a fraction of the trough, chance a spike starts a burst).
# A narrow, fast-spiking-like unit; a medium one; and a broad, pyramidal-like unit
# that sometimes fires bursts. About 2 spikes/s in all.
SIM_UNITS = ((0.4, 1.0, 0.30, 0.55, 0.0),
             (0.9, 0.6, 0.55, 0.35, 0.0),
             (1.6, 0.3, 0.75, 0.25, 0.3))


def extracellular_spike(depth: float, fs: float, trough_to_peak_ms: float = 0.55,
                        peak_ratio: float = 0.35) -> tuple[np.ndarray, int]:
    """One extracellular action potential sampled at `fs`, as recorded near a cell
    body: a small positive bump, a sharp negative trough (`depth` V) that falls
    faster than it recovers, then a slower positive repolarization peak
    `trough_to_peak_ms` later that decays over about a millisecond. Broader units
    are slower in every phase. Returns the samples and the index of the trough,
    which always lands on a sample so the height doesn't depend on the sample rate
    (at 1 kHz the shape is only a blip; use 20 kHz or more to see it)."""
    k = np.arange(-int(np.ceil(1.0e-3 * fs)), int(np.ceil(3.0e-3 * fs)) + 1)
    t = k / fs * 1e3                                                    # ms from the trough
    b = trough_to_peak_ms / 0.55                                        # 1 = a medium unit
    pre = 0.08 * np.exp(-((t + 0.3 * b) / (0.12 * b)) ** 2)
    bt = 0.6 + 0.4 * b                          # trough width varies less than the peak's timing
    trough = -np.exp(-(t / np.where(t < 0, 0.11 * bt, 0.19 * bt)) ** 2)
    tp = t - trough_to_peak_ms
    peak = peak_ratio * np.exp(-(tp / np.where(tp < 0, 0.25 * b, 0.6 * b)) ** 2)
    w = pre + trough + peak
    i = int(-k[0])
    return w * depth / -w[i], i                  # the other phases overlap the trough; keep it at -depth


class SimulatedBackend(Backend):
    """Fake signals for developing without hardware: sine + noise + spikes from three
    simulated units, each with its own extracellular spike height."""

    _pending = None    # (monitor input, waveform at the input rate) from SimulatedOutput

    def _setup(self):
        self._rng = np.random.default_rng(0)
        self._stim = None             # (monitor, wave, response, position)
        self._spike_tail = None       # spike samples that run past the end of the last chunk

    def inject(self, monitor: str, wave: np.ndarray, rate: float) -> None:
        """Play a stimulus: `wave` appears on the `monitor` input, and the other
        channels get a small evoked response 5 ms later (simulation only)."""
        fs = self.cfg.sample_rate
        t_in = np.arange(len(wave)) / rate
        t_ai = np.arange(0, len(wave) / rate, 1.0 / fs)
        w = np.interp(t_ai, t_in, wave) if len(wave) > 1 else np.asarray(wave, float)
        peak = float(np.max(np.abs(w))) if w.size else 0.0
        k = np.arange(int(0.05 * fs)) / fs                      # 50 ms response
        resp = 0.3 * peak * (k / 0.004) * np.exp(1 - k / 0.004)   # alpha function, peak at 4 ms
        resp = np.concatenate([np.zeros(int(0.005 * fs)), resp])
        self._pending = (monitor, w, resp)

    def _spikes(self, shapes, n, cs, fs, pad) -> np.ndarray:
        """This chunk's spikes for every channel. Each spike's size varies by about 5%;
        a burst is 2-4 spikes 3-6 ms apart, each 80% the size of the one before. A
        spike that runs past the chunk's end is added to the start of the next one."""
        rng = self._rng
        out = np.zeros((n, cs + pad))
        out[:, :pad] += self._spike_tail
        for (w, trough), (_, rate, _, _, burst) in zip(shapes, SIM_UNITS):
            for i in range(n):
                for j in np.flatnonzero(rng.random(cs) < rate / fs):
                    gain = 1 + 0.05 * rng.standard_normal()
                    for _ in range(1 + (rng.integers(1, 4) if rng.random() < burst else 0)):
                        a = j - trough                   # where the spike starts in this chunk
                        out[i, max(a, 0):a + len(w)] += gain * w[max(-a, 0):]
                        j += max(1, int(round(rng.uniform(3, 6) * 1e-3 * fs)))
                        gain *= 0.8
        self._spike_tail = out[:, cs:].copy()
        return out[:, :cs]

    def _loop(self):
        fs, n, cs = self.cfg.sample_rate, len(self.cfg.channels), self.cfg.chunk_size
        idx, t0 = 0, time.perf_counter()
        shapes = [extracellular_spike(depth, fs, tp, ratio) for depth, _, tp, ratio, _ in SIM_UNITS]
        pad = max(len(w) for w, _ in shapes) + int(np.ceil(0.018 * fs)) + 1   # room for a burst
        self._spike_tail = np.zeros((n, pad))
        while not self._stop.is_set():
            t = (idx + np.arange(cs)) / fs
            data = np.empty((n, cs))
            for i, ch in enumerate(self.cfg.channels):
                # Each input has its own signal (aiN: a sine at N+1 Hz), so
                # switching a channel's input visibly changes its trace.
                k = int(ch.physical[2:]) if ch.physical[2:].isdigit() else i
                # Inputs listed in [simulated] no_sine are just noise and spikes.
                sine = 0.0 if ch.physical in self.cfg.sim_no_sine else 0.5 * np.sin(2 * np.pi * (1 + k) * t)
                data[i] = sine + 0.02 * self._rng.standard_normal(cs)
            data += self._spikes(shapes, n, cs, fs, pad)
            if self._pending is not None:
                monitor, w, resp = self._pending
                self._pending = None
                self._stim = (monitor, w, resp, 0)
            if self._stim is not None:
                monitor, w, resp, pos = self._stim
                for i, ch in enumerate(self.cfg.channels):
                    src = w if ch.physical == monitor else resp
                    if ch.physical == monitor:
                        data[i] = 0.002 * self._rng.standard_normal(cs)   # a clean wire
                    seg = src[pos:pos + cs]
                    data[i, :len(seg)] += seg
                pos += cs
                self._stim = None if pos >= max(len(w), len(resp)) else (monitor, w, resp, pos)
            else:
                for i, ch in enumerate(self.cfg.channels):
                    if ch.physical == self.cfg.stim_monitor:
                        data[i] = 0.002 * self._rng.standard_normal(cs)
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
