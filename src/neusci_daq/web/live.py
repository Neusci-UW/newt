"""The live acquisition view's Python half: the simulated rig, a ring buffer, and frames
for the page.

All of it runs off any GUI thread: the backend's own thread makes chunks, a pump thread
here keeps the newest 10 s and turns the window on screen into a min/max envelope (two
points per screen column), and the web server's threads hand those frames to the page.
"""
from __future__ import annotations

import queue
import struct
import threading
import time

import numpy as np

from .. import backends, colors, config, stim, stimout
from .. import simulation as sim
from ..config import Channel
from ..ringbuffer import RingBuffer

MAX_WINDOW_S = 10.0
WINDOWS = (1.0, 2.0, 5.0, 10.0)

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
    chans = [Channel(phys, name, units=units, scale=scale) for phys, name, units, scale, _ in INPUTS]
    colors.fill_colors(chans)
    rate = 20000.0
    return config.Config(backend="simulated", sample_rate=rate, chunk_size=config.auto_chunk_size(rate),
                         channels=chans, stim_monitor="ai3", sim_network=net)


class Live:
    def __init__(self):
        self.cfg = make_cfg()
        self.backend = backends.make_backend(self.cfg)
        self.output = stimout.make_output(self.cfg, self.backend)
        self.q = self.backend.subscribe()
        n = len(self.cfg.channels)
        self.ring = RingBuffer(n, int(MAX_WINDOW_S * self.cfg.sample_rate), np.float32)
        self.scale = np.array([c.scale for c in self.cfg.channels], np.float32)[:, None]
        self.offset = np.array([c.offset for c in self.cfg.channels], np.float32)[:, None]
        self.trace_colors = [(colors.display_color(c.color, "light"), colors.display_color(c.color, "dark"))
                             for c in self.cfg.channels]
        self.window_s = 5.0
        self.cols = 1200              # screen columns the page draws (it tells us)
        self.last_stim = ""
        self._clear = False           # the pump empties the ring (it alone touches it)
        self._dirty = True            # make a frame even without new data (window changed...)
        self._newest_wall = 0.0       # wall time (ms) the newest chunk reached us
        self._frame: bytes | None = None
        self._frame_id = 0
        self._cond = threading.Condition()
        self._lag_seen = time.monotonic()
        threading.Thread(target=self._pump, daemon=True, name="web-pump").start()

    # -- commands from the page ---------------------------------------------------
    def run(self, on: bool) -> None:
        if on and not self.backend.running:
            self._clear = True
            self.backend.start()
        elif not on and self.backend.running:
            if self.output is not None:
                self.output.stop()
            self.backend.stop()
        self._dirty = True

    def set_window(self, s: float) -> None:
        self.window_s = min(WINDOWS, key=lambda w: abs(w - float(s)))
        self._dirty = True

    def set_cols(self, n: int) -> None:
        self.cols = max(50, min(4000, int(n)))
        self._dirty = True

    def stimulate(self, amp_v: float, width_ms: float, count: int, interval_ms: float) -> str:
        if not self.backend.running:
            raise RuntimeError("Press Run first.")
        s = stim.Stimulus(shape="pulse", amplitude_v=float(amp_v), width_ms=float(width_ms),
                          count=int(count), interval_ms=float(interval_ms))
        wave = s.waveform(self.cfg.stim_rate)
        stim.check_limit(wave, self.cfg.stim_max_v)
        self.output.fire(wave, self.cfg.stim_rate)
        self.last_stim = s.describe(self.cfg.stim_units, self.cfg.stim_scale)
        return self.last_stim

    def state(self) -> dict:
        rates = self.backend.rates(10.0) if self.backend.running and hasattr(self.backend, "rates") else []
        busy = self.output is not None and self.output.busy
        busy = busy() if callable(busy) else busy
        return {
            "channels": [{"name": c.name, "units": c.units, "light": light, "dark": dark,
                          "rate": (rates[rec[0]] if rec and rec[0] < len(rates) else None)}
                         for c, (light, dark), (_, _, _, _, rec) in zip(self.cfg.channels, self.trace_colors, INPUTS)],
            "running": self.backend.running,
            "error": str(self.backend.error or ""),
            "window_s": self.window_s, "windows": WINDOWS,
            "fs": self.backend.actual_rate, "chunk_ms": 1000 * self.cfg.chunk_size / self.cfg.sample_rate,
            "stim_busy": bool(busy), "last_stim": self.last_stim,
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
            if self._clear:
                self._clear = False
                self.ring.clear()
            got = False
            while c is not None:
                self.ring.append(c.t, c.data)
                self._newest_wall = time.time() * 1000
                got = True
                try:
                    c = self.q.get_nowait()
                except queue.Empty:
                    c = None
            if got or self._dirty:
                self._dirty = False
                frame = self._build()
                with self._cond:
                    self._frame, self._frame_id = frame, self._frame_id + 1
                    self._cond.notify_all()

    def _build(self) -> bytes:
        """One frame: the window on screen as two points per screen column (its min and
        max, alternating so the line zigzags through every column), in display units.

        Layout (little-endian): f64 wall ms the newest chunk arrived, f64 time of the
        newest sample (s), f32 window (s), f32 ms this took, u32 channels, u32 points m,
        f64[m] times, f32[channels] newest values, f32[channels * m] values."""
        t0 = time.perf_counter()
        n_win = int(round(self.window_s * self.backend.actual_rate))
        t, y = self.ring.t[-n_win:], self.ring.y[:, -n_win:]
        n, m = y.shape
        cols = self.cols
        if m <= 2 * cols:
            x, ys = t.astype(np.float64), y * self.scale + self.offset
        else:
            edges = np.arange(cols) * m // cols
            lo = np.minimum.reduceat(y, edges, axis=1) * self.scale + self.offset
            hi = np.maximum.reduceat(y, edges, axis=1) * self.scale + self.offset
            ys = np.empty((n, 2 * cols), np.float32)
            ys[:, 0::2], ys[:, 1::2] = lo, hi
            a = ys[:, 2::4].copy()                 # odd columns go max then min: one zigzag
            ys[:, 2::4], ys[:, 3::4] = ys[:, 3::4], a
            x = np.empty(2 * cols)
            x[0::2] = t[edges]
            x[1::2] = t[np.minimum(edges + max(1, m // cols // 2), m - 1)]
        newest = (y[:, -1] * self.scale[:, 0] + self.offset[:, 0]) if m else np.zeros(n, np.float32)
        t_end = float(t[-1]) if m else 0.0
        build_ms = (time.perf_counter() - t0) * 1000
        head = struct.pack("<ddffII", self._newest_wall, t_end, self.window_s, build_ms, n, len(x))
        return b"".join((head, x.astype("<f8").tobytes(), newest.astype("<f4").tobytes(),
                         np.ascontiguousarray(ys, "<f4").tobytes()))
