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

from . import breadboard as bb
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
        # The rate may have changed since this backend was made; hardware backends
        # replace it in _setup with what the device actually does.
        self.actual_rate = self.cfg.sample_rate
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
    """Fake recordings for developing and teaching without hardware: a small network
    of integrate-and-fire cells, each input recording one of them (see
    simulation.py, and the Neural network simulator page), or a breadboard circuit, an
    input a probe is set to recording its node (breadboard.py). Settings changed while
    running apply from the next chunk."""

    _pending = None    # (monitor input, waveform at the input rate) from SimulatedOutput

    def _setup(self):
        self._rng = np.random.default_rng(0)
        self._stim = None             # (monitor input, waveform, position) being played
        self._net = None              # NetworkState, made on the first chunk
        self._net_cfg = None          # the settings it was made from
        self._board = None            # breadboard.BoardState, made when a probe first records
        self._board_cfg = None
        self._tail: dict[str, np.ndarray] = {}    # per input: spike samples past the last chunk
        self._spike_log: list[tuple[float, int]] = []   # (time s, cell) of recent spikes
        self._time = 0.0

    def inject(self, monitor: str, wave: np.ndarray, rate: float) -> None:
        """Play a stimulus: `wave` appears on the `monitor` input, and is injected as a
        current into the cells that the stimulus drives."""
        fs = self.cfg.sample_rate
        t_in = np.arange(len(wave)) / rate
        t_ai = np.arange(0, len(wave) / rate, 1.0 / fs)
        w = np.interp(t_ai, t_in, wave) if len(wave) > 1 else np.asarray(wave, float)
        self._pending = (monitor, w)

    def rates(self, window_s: float = 10.0) -> list[float]:
        """Each cell's firing rate over the last `window_s` seconds (spikes/s)."""
        n = len(self.cfg.sim_network.cells)
        span = min(window_s, max(self._time, 1e-9))
        counts = np.zeros(n)
        for t, c in list(self._spike_log):
            if t >= self._time - window_s and c < n:
                counts[c] += 1
        return list(counts / span)

    def _network(self) -> sim.NetworkState:
        net = self.cfg.sim_network                    # may be replaced while running
        if self._net is None:
            self._net = sim.NetworkState(net, self._rng)
        elif net is not self._net_cfg:
            self._net.configure(net)
        self._net_cfg = net
        return self._net

    def _breadboard(self, fs: float) -> bb.BoardState:
        board = self.cfg.sim_breadboard                # may be replaced while running
        if self._board is None or self._board.dt != 1.0 / fs:
            self._board = bb.BoardState(board, 1.0 / fs)
        elif board is not self._board_cfg:
            try:
                self._board.configure(board)
            except ValueError:                         # the page checks first; keep the old circuit
                pass
        self._board_cfg = board
        return self._board

    def _record(self, rec: sim.Recording, cell: sim.Cell, v_mv: np.ndarray, spikes_s: list[float],
                t: np.ndarray, fs: float) -> np.ndarray:
        """One input's trace for this chunk, from its cell's membrane potential (mV at
        the chunk's samples) and spike times (s from the chunk's start)."""
        cs = len(t)
        if rec.kind == "intracellular":
            shape = sim.intracellular_ap(cell, fs)
            trace_mv = v_mv.copy()
            tail = self._tail.get(rec.physical)
            if tail is not None:                       # an action potential from the last chunk
                m = min(cs, len(tail))
                trace_mv[:m] = tail[:m]
            nxt = None
            for ts in spikes_s:
                i = int(round(ts * fs))
                end = i + len(shape)
                if end > cs:
                    nxt = shape[cs - i:] if i < cs else shape
                trace_mv[i:min(end, cs)] = shape[:min(end, cs) - i]
            self._tail[rec.physical] = nxt
            y = rec.gain * trace_mv / 1000.0
        else:
            w, trough = sim.spike_shape(cell, fs)
            tail = self._tail.get(rec.physical)
            out = np.zeros(cs + len(w))
            if tail is not None:
                out[:len(tail)] += tail
            for ts in spikes_s:
                a = int(round(ts * fs)) - trough
                out[max(a, 0):a + len(w)] += w[max(-a, 0):]
            self._tail[rec.physical] = out[cs:].copy()
            y = out[:cs]
        return y + rec.sine_v * np.sin(2 * np.pi * rec.sine_hz * t) + rec.noise_v * self._rng.standard_normal(cs)

    def _loop(self):
        fs, cs = self.cfg.sample_rate, self.cfg.chunk_size
        idx, t0 = 0, time.perf_counter()
        steps_done = 0
        while not self._stop.is_set():
            net_cfg = self.cfg.sim_network
            state = self._network()
            t = (idx + np.arange(cs)) / fs
            if self._pending is not None:
                monitor, w = self._pending
                self._pending = None
                self._stim = (monitor, w, 0)
                state.stim = []                    # a new stimulus (or Stop) replaces the one playing
                state.stimulate(w, fs)
            # run the network up to the end of this chunk
            steps = int(round((idx + cs) / fs / sim.SIM_DT)) - steps_done
            v, spikes = state.run(max(steps, 0))
            t_net = (steps_done + np.arange(len(v))) * sim.SIM_DT
            steps_done += len(v)
            chunk_start = idx / fs
            by_cell: dict[int, list[float]] = {}
            for k, c in spikes:
                ts = t_net[k]
                by_cell.setdefault(c, []).append(ts - chunk_start)
                self._spike_log.append((ts, c))
            self._time = (idx + cs) / fs
            while self._spike_log and self._spike_log[0][0] < self._time - 30:
                self._spike_log.pop(0)
            stim = np.zeros(cs)
            if self._stim is not None:
                monitor, w, pos = self._stim
                seg = w[pos:pos + cs]
                stim[:len(seg)] = seg
                self._stim = None if pos + cs >= len(w) else (monitor, w, pos + cs)
            # breadboard probes: stepped only while one records, driven by the same stimulus
            probes = {p.input: p for p in self.cfg.sim_breadboard.probes() if p.input}
            board_v = {}
            if probes:
                tr = self._breadboard(fs).run(stim, [p.a for p in probes.values()])
                board_v = dict(zip(probes, tr))
            data = np.empty((len(self.cfg.channels), cs))
            for i, ch in enumerate(self.cfg.channels):
                if ch.physical == self.cfg.stim_monitor:
                    data[i] = 0.002 * self._rng.standard_normal(cs) + stim    # a clean wire
                    continue
                if ch.physical in board_v:
                    data[i] = board_v[ch.physical] + probes[ch.physical].noise_v * self._rng.standard_normal(cs)
                    continue
                rec = sim.recording_for(net_cfg, ch.physical)
                c = min(rec.cell, len(net_cfg.cells) - 1)
                v_mv = np.interp(t, t_net, v[:, c]) if len(v) else np.full(cs, state.v[c])
                data[i] = self._record(rec, net_cfg.cells[c], v_mv, by_cell.get(c, []), t, fs)
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
    if cfg.backend == "audio":
        from .audio import AudioBackend          # imported here: it needs Backend from this module
        return AudioBackend(cfg)
    return BACKENDS[cfg.backend](cfg)
