"""The live acquisition view's Python half: the input device, a ring buffer, and frames for
the page.

All of it runs off any GUI thread: the backend's own thread makes chunks, a pump thread
here keeps the newest seconds (and works out the calculated channels, dsp.Chain, a chunk at
a time) and turns the window on screen into a min/max envelope (two points per screen
column), and the web server's threads hand those frames to the page.

The setup is the Qt app's Config: from a config.toml (--config, read as the Qt app reads
it) or, without one, the simulated rig below. The page changes it through rig.py, which
changes this Config in place, as the Qt app does, so the stimulators keep theirs.
"""
from __future__ import annotations

import queue
import struct
import threading
import time
from pathlib import Path

import numpy as np

from .. import backends, colors, config, dsp, stimout
from .. import simulation as sim
from ..config import Channel
from ..ringbuffer import RingBuffer
from .stimulator import read_settings, update_settings

HELD_S = 10.0                   # seconds kept, at least (more when the window is longer)
MAX_WINDOW_S = 60.0
WINDOWS = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 60.0)

# (input, name, units, displayed = volts * scale, what it records: (cell, kind) or None)
INPUTS = (("ai0", "Cell 1 (inside)", "mV", 100.0, (0, "intracellular")),
          ("ai1", "Cell 2 (inside)", "mV", 100.0, (1, "intracellular")),
          ("ai2", "Cell 3 (outside)", "V", 1.0, (2, "extracellular")),
          ("ai3", "Stimulus", "V", 1.0, None))


def make_cfg() -> config.Config:
    """The simulated rig: the default three-cell chain (the stimulus drives cell 1, 1
    excites 2, 2 excites 3, 3 inhibits 1), two cells recorded from inside, one from
    outside, and the stimulus wired back to ai3."""
    net = sim.default_network()
    net.recordings = [sim.Recording(phys, cell=rec[0], kind=rec[1]) for phys, _, _, _, rec in INPUTS if rec]
    chans = [Channel(phys, name, units=units, scale=scale, width=1.25) for phys, name, units, scale, _ in INPUTS]
    colors.fill_colors(chans)
    rate = 20000.0
    return config.Config(backend="simulated", sample_rate=rate, chunk_size=config.auto_chunk_size(rate),
                         channels=chans, stim_monitor="ai3", sim_network=net, window_s=5.0)


class Live:
    def __init__(self, cfg: config.Config | None = None, config_path: str | Path | None = None):
        self.cfg = cfg if cfg is not None else make_cfg()
        self.config_path = Path(config_path) if config_path else None
        self.backend = backends.make_backend(self.cfg)
        self.output = stimout.make_output(self.cfg, self.backend)
        self.q = self.backend.subscribe()
        self._sim_backend = None          # kept while another input is used: its cells carry on
        self.window_s = min(MAX_WINDOW_S, max(0.01, float(self.cfg.window_s)))
        self.cols = 1200              # screen columns the page draws (it tells us)
        self.last_stim = ""              # what the Stimulator sent last (stimulator.py)
        self.recording = None            # Record (record.py), once it's made
        self.y_ranges: dict[str, list[float]] = {k: v for k, v in (read_settings().get("y_ranges") or {}).items()
                                                 if isinstance(v, list) and len(v) == 2}
        self._lock = threading.RLock()   # the rings and what frames are made of (the pump, setup changes)
        self.ring: RingBuffer | None = None
        self._ring_fs = 0.0
        self._clear = False           # the pump empties the rings
        self._dirty = True            # make a frame even without new data (window changed...)
        self._newest_wall = 0.0       # wall time (ms) the newest chunk reached us
        self._frame: bytes | None = None
        self._frame_id = 0
        self._cond = threading.Condition()
        self.relayout()
        from .review import Review
        from .rig import Rig
        self.rig, self.review = Rig(self), Review(self)
        threading.Thread(target=self._pump, daemon=True, name="web-pump").start()

    # -- the setup ----------------------------------------------------------------
    def relayout(self) -> None:
        """After the channels, rate or calculations changed: the rings, scaling, colors and
        calculations for the channels on now. Kept data stays if the inputs did."""
        with self._lock:
            cfg = self.cfg
            chans = cfg.channels
            fs = float(cfg.sample_rate)
            cap = int(max(HELD_S, self.window_s) * fs * 1.05) + 2 * cfg.chunk_size
            if self.ring is None or self.ring.n != len(chans) or self._ring_fs != fs:
                self.ring, self._ring_fs = RingBuffer(len(chans), cap, np.float32), fs
            elif self.ring.cap < cap:
                self.ring.resize(cap)
            self.scale = np.array([c.scale for c in chans], np.float32)[:, None]
            self.offset = np.array([c.offset for c in chans], np.float32)[:, None]
            self.chain = dsp.Chain(cfg.calculations, [c.name for c in chans], fs)
            k = len(self.chain.calcs)
            self.calc_colors = [colors.PALETTE[(len(chans) + j) % len(colors.PALETTE)] for j in range(k)]
            self.calc_ring = RingBuffer(k, self.ring.cap, np.float32)
            if k and len(self.ring):          # the calculations over what's held
                self.chain.reset()
                self.calc_ring.append(self.ring.t, self._calc(self.ring.y))
            self._dirty = True

    def _calc(self, y_raw: np.ndarray) -> np.ndarray:
        """The calculated channels for samples of the channels (raw volts, a row each)."""
        k, m = len(self.chain.calcs), y_raw.shape[1]
        if not k:
            return np.empty((0, m), np.float32)
        env = {ch.name: y_raw[i] * self.scale[i, 0] + self.offset[i, 0] for i, ch in enumerate(self.cfg.channels)}
        out = self.chain.stream(env, self.cfg.sample_rate)
        return np.array([np.full(m, np.nan) if y is None else y for y in out], np.float32).reshape(k, m)

    def use_backend(self, backend: str, device: str = "") -> None:
        """Swap the input device under everything that reads it (acquiring is stopped)."""
        with self._lock:
            self.cfg.backend, self.cfg.audio_device = backend, device
            old = self.backend
            old.stop()
            old.unsubscribe(self.q)
            if self.output is not None and self.output.busy:
                self.output.stop()
            if isinstance(old, backends.SimulatedBackend):
                self._sim_backend = old
            reuse = self._sim_backend if backend == "simulated" else None
            self.backend = reuse or backends.make_backend(self.cfg)
            self.backend.subscribe(self.q)          # the same queue: the pump keeps reading it
            if self.recording is not None:
                self.recording.rec.rebind(self.backend)
            self.output = stimout.make_output(self.cfg, self.backend)
            self._dirty = True

    # -- commands from the page ---------------------------------------------------
    def run(self, on: bool) -> None:
        rec = self.recording
        if on and not self.backend.running:
            self._clear = True
            self.backend.start()
            if rec is not None:            # with Record on, a trial starts with the run
                rec.run_started()
        elif not on and self.backend.running:
            if rec is not None:            # the trial ends; Record stays on for the next Run
                rec.run_stopping()
            if self.output is not None:
                self.output.stop()
            self.backend.stop()
        self._dirty = True

    def set_window(self, s: float) -> None:
        s = float(s)
        if not s > 0:
            raise ValueError("The window is a number of seconds, more than 0.")
        self.window_s = self.cfg.window_s = min(MAX_WINDOW_S, max(0.01, s))
        self.relayout()                     # (holds more, if it's longer)

    def set_cols(self, n: int) -> None:
        self.cols = max(50, min(4000, int(n)))
        self._dirty = True

    def set_yrange(self, name: str, lo, hi) -> None:
        """A channel's Y range, kept from launch to launch; None for Auto (fit the trace)."""
        if lo is None or hi is None:
            self.y_ranges.pop(name, None)
        else:
            lo, hi = float(lo), float(hi)
            if not (np.isfinite(lo) and np.isfinite(hi)) or lo == hi:
                raise ValueError("The top and bottom of the plot need two different numbers.")
            self.y_ranges[name] = [min(lo, hi), max(lo, hi)]
        update_settings(y_ranges=self.y_ranges)

    def sent(self, desc: str, stimulus=None, wave=None) -> None:
        """A stimulator just sent `stimulus` (stim.Stimulus, described as `desc`, as `wave`)."""
        self.last_stim = desc
        if self.recording is not None:
            self.recording.sent(desc, stimulus, wave)

    def _rates(self) -> list:
        """Each channel's cell's firing rate (spikes/s) on the simulated rig; None elsewhere."""
        cfg, n = self.cfg, len(self.cfg.channels)
        if not (self.backend.running and hasattr(self.backend, "rates")):
            return [None] * n
        rates = self.backend.rates(10.0)
        out = []
        for c in cfg.channels:
            r = None if c.physical == cfg.stim_monitor else sim.recording_for(cfg.sim_network, c.physical)
            out.append(rates[r.cell] if r is not None and 0 <= r.cell < len(rates) else None)
        return out

    def state(self) -> dict:
        busy = self.output is not None and self.output.busy
        busy = busy() if callable(busy) else busy
        cfg = self.cfg
        chans = [{"name": c.name, "units": c.units, "light": colors.display_color(c.color, "light"),
                  "dark": colors.display_color(c.color, "dark"), "rate": r, "width": c.width,
                  "y": self.y_ranges.get(c.name), "stim": c.physical == cfg.stim_monitor}
                 for c, r in zip(cfg.channels, self._rates())]
        chans += [{"name": c.name, "units": c.unit, "light": colors.display_color(col, "light"),
                   "dark": colors.display_color(col, "dark"), "rate": None, "width": 1.0, "calc": True,
                   "formula": c.formula, "problem": why, "y": self.y_ranges.get(c.name)}
                  for c, col, why in zip(self.chain.calcs, self.calc_colors, self.chain.problems)]
        return {
            "channels": chans,
            "running": self.backend.running,
            "error": str(self.backend.error or ""),
            "window_s": self.window_s, "windows": WINDOWS, "max_window": MAX_WINDOW_S,
            "fs": self.backend.actual_rate, "chunk_ms": 1000 * cfg.chunk_size / cfg.sample_rate,
            "stim_busy": bool(busy), "last_stim": self.last_stim, "can_stim": self.output is not None,
            "record": self.recording.state() if self.recording is not None else None,
            "setup": self.rig.state(), "review": self.review.state(),
        }

    def close(self) -> None:
        self.run(False)

    # -- frames -------------------------------------------------------------------
    def wait_frame(self, after: int, timeout: float = 1.0) -> tuple[int, bytes | None]:
        """The newest frame once there is one newer than `after` (None if none came)."""
        with self._cond:
            self._cond.wait_for(lambda: self._frame_id != after, timeout)
            return self._frame_id, (self._frame if self._frame_id != after else None)

    def _pump(self) -> None:
        while True:
            try:
                c = self.q.get(timeout=0.1)
            except queue.Empty:
                c = None
            with self._lock:
                if self._clear:
                    self._clear = False
                    self.ring.clear()
                    self.calc_ring.clear()
                    self.chain.reset()
                got = False
                while c is not None:
                    if c.data.shape[0] == self.ring.n:       # (not one from before the channels changed)
                        self.ring.append(c.t, c.data)
                        if self.calc_ring.n:
                            self.calc_ring.append(c.t, self._calc(c.data))
                        self._newest_wall = time.time() * 1000
                        got = True
                    try:
                        c = self.q.get_nowait()
                    except queue.Empty:
                        c = None
                if not (got or self._dirty):
                    continue
                self._dirty = False
                frame = self._build()
            with self._cond:
                self._frame, self._frame_id = frame, self._frame_id + 1
                self._cond.notify_all()

    def _build(self) -> bytes:
        """One frame: the window on screen, channels then calculated channels, in display units."""
        t0 = time.perf_counter()
        n_win = max(1, int(round(self.window_s * self.backend.actual_rate)))
        t, y = self.ring.t[-n_win:], self.ring.y[:, -n_win:]
        calc = self.calc_ring.y[:, -n_win:] if self.calc_ring.n and len(self.calc_ring) == len(self.ring) else None
        k = self.calc_ring.n
        x, ys, newest = envelope(t, y, self.cols, self.scale, self.offset)
        if k:
            if calc is not None:
                _, yc, nc = envelope(t, calc, self.cols)
            else:
                yc, nc = np.full((k, len(x)), np.nan, np.float32), np.full(k, np.nan, np.float32)
            ys, newest = np.vstack([ys, yc]), np.concatenate([newest, nc])
        return pack(self._newest_wall, float(t[-1]) if len(t) else 0.0, self.window_s,
                    (time.perf_counter() - t0) * 1000, x, newest, ys)


def envelope(t: np.ndarray, y: np.ndarray, cols: int, scale=None, offset=None):
    """(times, values, newest values): `y` as two points per screen column (its min and max,
    alternating so the line zigzags through every column), or every sample if there are
    few; scaled (y * scale + offset) if given."""
    n, m = y.shape
    sc = (lambda a: a * scale + offset) if scale is not None else (lambda a: a)
    if m <= 2 * cols:
        x, ys = t.astype(np.float64), sc(y).astype(np.float32, copy=False)
    else:
        edges = np.arange(cols) * m // cols
        lo = sc(np.minimum.reduceat(y, edges, axis=1))
        hi = sc(np.maximum.reduceat(y, edges, axis=1))
        ys = np.empty((n, 2 * cols), np.float32)
        ys[:, 0::2], ys[:, 1::2] = lo, hi
        a = ys[:, 2::4].copy()                 # odd columns go max then min: one zigzag
        ys[:, 2::4], ys[:, 3::4] = ys[:, 3::4], a
        x = np.empty(2 * cols)
        x[0::2] = t[edges]
        x[1::2] = t[np.minimum(edges + max(1, m // cols // 2), m - 1)]
    newest = sc(y[:, -1:])[:, 0] if m else np.full(n, np.nan, np.float32)
    return x, ys, newest


def pack(wall: float, t_end: float, window: float, build_ms: float, x, newest, ys) -> bytes:
    """A frame as the page reads it (little-endian): f64 wall ms the newest chunk arrived,
    f64 time of the right edge (s), f32 window (s), f32 ms this took, u32 rows, u32 points m,
    f64[m] times, f32[rows] newest values, f32[rows * m] values."""
    n = len(newest)
    head = struct.pack("<ddffII", wall, t_end, window, build_ms, n, len(x))
    return b"".join((head, np.asarray(x, "<f8").tobytes(), np.asarray(newest, "<f4").tobytes(),
                     np.ascontiguousarray(ys, "<f4").tobytes()))
