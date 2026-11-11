"""USB audio: a sound card, audio interface, SpikerBox-style device or a webcam's
microphone as the acquisition source; listening to a channel; playing recordings back.

Uses sounddevice (PortAudio): pip install "neusci-daq-visualizer[audio]". Everything
here says what's wrong instead of failing when it isn't installed.

- AudioBackend: the device's inputs are channels in1, in2, …; samples are the
  device's full scale (±1), shown as volts until a channel's scale says otherwise.
  Audio inputs are AC coupled: they can't record steady levels.
- Monitor: plays a channel live through an output (speakers, headphones), resampled
  to the output's rate, with an automatic gain so quiet signals are audible.
- Player: plays a stretch of recorded data, and says how far it has got (to move a
  cursor and a video in step).
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

from . import colours, config
from .backends import Backend, Chunk

try:
    import sounddevice as _sd
    _WHY = ""
except Exception as e:                      # not installed, or no PortAudio
    _sd = None
    _WHY = f"USB audio needs the sounddevice package (pip install sounddevice): {e}"

RATES = (8000, 16000, 22050, 44100, 48000, 96000)


def available() -> str:
    """"" if audio works here, else why not."""
    return _WHY


def devices(kind: str) -> list[dict]:
    """Input ("input") or output ("output") devices: [{index, name, channels, rate}]."""
    if _sd is None:
        return []
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    out = []
    for i, d in enumerate(_sd.query_devices()):
        if d[key] > 0:
            out.append({"index": i, "name": d["name"], "channels": int(d[key]),
                        "rate": float(d["default_samplerate"])})
    return out


def find(name: str, kind: str) -> dict | None:
    """The device called `name` (or containing it), or the default one for "" ."""
    found = devices(kind)
    if not found:
        return None
    if not name:
        try:
            default = _sd.default.device[0 if kind == "input" else 1]
        except Exception:
            default = -1
        return next((d for d in found if d["index"] == default), found[0])
    exact = [d for d in found if d["name"] == name]
    return (exact or [d for d in found if name.lower() in d["name"].lower()] or [None])[0]


def input_names(n: int) -> list[str]:
    return [f"in{i + 1}" for i in range(max(1, n))]


def max_inputs(name: str) -> int:
    d = find(name, "input")
    return d["channels"] if d else 2


def resample(y: np.ndarray, fs: float, out_fs: float, state: tuple | None = None) -> tuple[np.ndarray, tuple]:
    """y (at fs) at out_fs by linear interpolation. `state` carries on from the previous
    chunk (where the next output sample falls, and the last input sample), so a stream
    resampled chunk by chunk matches it done all at once. Returns (output, state)."""
    if len(y) == 0:
        return np.empty(0, np.float32), state
    phase, last = state if state is not None else (0.0, None)
    y = np.asarray(y, float)
    if last is not None:                     # the gap between the chunks is interpolated too
        y = np.concatenate([[last], y])
        phase += 1.0
    step = fs / out_fs
    pos = np.arange(phase, len(y) - 1 + 1e-9, step)
    out = np.interp(pos, np.arange(len(y)), y).astype(np.float32)
    nxt = (pos[-1] + step if len(pos) else phase) - (len(y) - 1)
    return out, (nxt - 1.0, y[-1])


class AudioBackend(Backend):
    """Record from an audio input device (config: audio_device; channels in1, in2, …)."""
    hardware_timed = True

    def _setup(self):
        if _sd is None:
            raise RuntimeError(_WHY)
        dev = find(self.cfg.audio_device, "input")
        if dev is None:
            raise RuntimeError(f"No audio input called {self.cfg.audio_device!r}." if self.cfg.audio_device
                               else "No audio input device found.")
        self._sel = []
        for ch in self.cfg.channels:
            k = int(ch.physical[2:]) - 1 if ch.physical.startswith("in") and ch.physical[2:].isdigit() else -1
            if not 0 <= k < dev["channels"]:
                raise RuntimeError(f"{ch.name}: {dev['name']} has inputs in1–in{dev['channels']}, "
                                   f"not {ch.physical}.")
            self._sel.append(k)
        try:
            _sd.check_input_settings(device=dev["index"], channels=max(self._sel) + 1,
                                     samplerate=self.cfg.sample_rate, dtype="float32")
        except Exception:
            raise RuntimeError(f"{dev['name']} can't record at {self.cfg.sample_rate:,.0f} Hz; try "
                               f"{dev['rate']:,.0f} Hz (its usual rate), 44,100 or 48,000 Hz.") from None
        self._blocks: queue.Queue = queue.Queue()
        self._stream = _sd.InputStream(device=dev["index"], channels=max(self._sel) + 1,
                                       samplerate=self.cfg.sample_rate, blocksize=self.cfg.chunk_size,
                                       dtype="float32", callback=self._callback)
        self._stream.start()
        self.actual_rate = float(self._stream.samplerate)
        self.device_name = dev["name"]
        self._index = 0

    def _callback(self, indata, frames, t, status):
        self._blocks.put(indata.copy())

    def _loop(self):
        fs = self.actual_rate
        while not self._stop.is_set():
            try:
                block = self._blocks.get(timeout=0.2)
            except queue.Empty:
                continue
            data = block[:, self._sel].T.astype(float)
            m = data.shape[1]
            t = (self._index + np.arange(m)) / fs
            self._publish(Chunk(data, t, self._index))
            self._index += m

    def _teardown(self):
        s = getattr(self, "_stream", None)
        if s is not None:
            s.stop()
            s.close()
            self._stream = None


class Monitor:
    """Listen to a live channel: feed() it each chunk; it plays through `device`."""

    def __init__(self, device: str = "", volume: float = 0.8):
        self.device, self.volume = device, volume
        self._peak = 0.0                    # recent peak (decays), for the automatic gain
        self._buf = np.zeros(0, np.float32)
        self._lock = threading.Lock()
        self._state = None
        self._stream = None
        self.rate = 48000.0

    def start(self) -> None:
        if _sd is None:
            raise RuntimeError(_WHY)
        dev = find(self.device, "output")
        if dev is None:
            raise RuntimeError("No audio output device found.")
        self.rate = dev["rate"]
        self._stream = _sd.OutputStream(device=dev["index"], channels=1, samplerate=self.rate,
                                        dtype="float32", callback=self._callback, latency="low")
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._buf = np.zeros(0, np.float32)

    @property
    def running(self) -> bool:
        return self._stream is not None

    def feed(self, y: np.ndarray, fs: float) -> None:
        """The newest samples of the channel listened to (any unit; centred and scaled)."""
        y = np.nan_to_num(np.asarray(y, float))
        if not len(y):
            return
        y = y - np.median(y)
        # automatic gain: the recent peak (held, fading over a few seconds) plays at half scale
        self._peak = max(float(np.max(np.abs(y))), self._peak * 0.97)
        gain = 0.5 / self._peak if self._peak > 0 else 0.0
        out, self._state = resample(y * gain * self.volume, fs, self.rate, self._state)
        with self._lock:                    # keep at most ~0.3 s queued: listening stays live
            self._buf = np.concatenate([self._buf, np.clip(out, -1, 1)])[-int(0.3 * self.rate):]

    def _callback(self, outdata, frames, t, status):
        with self._lock:
            n = min(frames, len(self._buf))
            outdata[:n, 0] = self._buf[:n]
            outdata[n:, 0] = 0
            self._buf = self._buf[n:]


class Player:
    """Play recorded samples once; position() says how far it has got (seconds)."""

    def __init__(self, device: str = ""):
        self.device = device
        self._t0 = None
        self.duration = 0.0

    def play(self, y: np.ndarray, fs: float, volume: float = 0.8) -> None:
        if _sd is None:
            raise RuntimeError(_WHY)
        dev = find(self.device, "output")
        if dev is None:
            raise RuntimeError("No audio output device found.")
        y = np.nan_to_num(np.asarray(y, float))
        y = y - np.median(y) if len(y) else y
        peak = float(np.percentile(np.abs(y), 99.5)) if len(y) else 0.0
        out, _ = resample(y * (0.5 / peak if peak else 1.0) * volume, fs, dev["rate"])
        _sd.play(np.clip(out, -1, 1), dev["rate"], device=dev["index"])
        self.duration = len(y) / fs
        self._t0 = time.monotonic()

    def stop(self) -> None:
        if _sd is not None and self._t0 is not None:
            _sd.stop()
        self._t0 = None

    def position(self) -> float | None:
        """Seconds played, or None when it has finished (or was never started)."""
        if self._t0 is None:
            return None
        p = time.monotonic() - self._t0
        if p >= self.duration:
            self._t0 = None
            return None
        return p


def use_audio_channels(cfg) -> None:
    """Switching to an audio device: its inputs (in1, in2) as the channels unless they
    already are, at a rate it records at, and no stimulus input (it has no outputs here)."""
    dev = find(cfg.audio_device, "input")
    n = min(2, dev["channels"]) if dev else 2
    if not all(c.physical.startswith("in") for c in cfg.channels):
        cfg.spare_channels = []
        cfg.channels = [config.Channel(f"in{i + 1}", f"Audio {i + 1}") for i in range(n)]
        colours.fill_colors(cfg.channels)
    if cfg.sample_rate not in RATES:
        cfg.sample_rate = dev["rate"] if dev else 48000.0
    cfg.chunk_size = config.auto_chunk_size(cfg.sample_rate)
    cfg.stim_monitor = ""
