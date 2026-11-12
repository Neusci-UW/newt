"""Write acquired chunks to HDF5 on a background thread (the layout is in trialfile.py).

File layout:
  /raw_volts   (n_channels, N) float32   -- unscaled ADC volts
  /t           (N,) float64              -- only for software-timed backends
  /comments    compound(t, sample_index, text)
  attrs: sample_rate, hardware_timed, device, backend, start_time,
         channel_names, physical, units, scale, offset, colors
         (colors are display-only and may be changed later)
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

import h5py

from .backends import Backend, Chunk
from .trialfile import COMMENT_DT, decode_comments, write_comments_dataset



class Recorder:
    def __init__(self, backend: Backend):
        self.backend = backend
        self.cfg = backend.cfg
        self._q = backend.subscribe()
        self._lock = threading.Lock()
        self._file: h5py.File | None = None
        self.path: Path | None = None
        self.samples_written = 0
        self.last_t = 0.0
        self.last_index = 0
        self._thread = threading.Thread(target=self._consume, daemon=True, name="daq-rec")
        self._thread.start()

    @property
    def recording(self) -> bool:
        return self._file is not None

    def start(self, path: str | Path | None = None) -> Path:
        cfg = self.cfg
        if path is None:
            Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
            path = Path(cfg.data_dir) / datetime.now().strftime("rec_%Y%m%d_%H%M%S.h5")
        n = len(cfg.channels)
        f = h5py.File(path, "w")
        f.create_dataset("raw_volts", shape=(n, 0), maxshape=(n, None), dtype="f4",
                         chunks=(n, max(1024, cfg.chunk_size)))
        if not self.backend.hardware_timed:
            f.create_dataset("t", shape=(0,), maxshape=(None,), dtype="f8", chunks=(4096,))
        f.create_dataset("comments", shape=(0,), maxshape=(None,), dtype=COMMENT_DT)
        a = f.attrs
        a["sample_rate"] = self.backend.actual_rate
        a["hardware_timed"] = self.backend.hardware_timed
        a["device"], a["backend"] = cfg.device, cfg.backend
        a["terminal_config"] = cfg.terminal_config
        a["start_time"] = datetime.now().isoformat()
        a["channel_names"] = [c.name for c in cfg.channels]
        a["physical"] = [c.physical for c in cfg.channels]
        a["units"] = [c.units for c in cfg.channels]
        a["scale"] = [c.scale for c in cfg.channels]
        a["offset"] = [c.offset for c in cfg.channels]
        a["colors"] = [c.color for c in cfg.channels]
        with self._lock:
            self.samples_written = 0
            self._t_offset = None
            self._file, self.path = f, Path(path)
        return self.path

    def close(self) -> None:
        """Stop recording and end the writer thread (call before the program exits)."""
        self.stop()
        self._q.put(None)
        self._thread.join(timeout=5)

    def stop(self) -> None:
        with self._lock:
            if self._file is not None:
                if not self.backend.hardware_timed:
                    self._file.attrs["sample_rate"] = self.backend.actual_rate
                self._file.close()
                self._file = None

    def set_colors(self, colors: list[str]) -> None:
        """Update the colors of the block being recorded (display metadata only)."""
        with self._lock:
            if self._file is not None:
                self._file.attrs["colors"] = list(colors)
                self._file.flush()

    @property
    def block_started(self) -> bool:
        """True once the first sample of the current block has arrived."""
        return self._t_offset is not None

    @property
    def block_start(self) -> float:
        """Stream time (s) of the first sample of the current or last block."""
        return self._t_offset or 0.0

    def set_attr(self, key: str, value) -> None:
        """Store extra information with the block being recorded (e.g. a trial protocol)."""
        with self._lock:
            if self._file is not None:
                self._file.attrs[key] = value
                self._file.flush()

    def add_stimulus(self, settings: dict, rate: float) -> None:
        """Save what was just sent with the block being recorded, at the latest acquired
        sample, so analysis can line up the known waveform with the recording."""
        with self._lock:
            if self._file is not None:
                sent = json.loads(self._file.attrs.get("stimuli", "[]"))
                sent.append({"t": self.last_t - (self._t_offset or 0.0), "rate": rate, "settings": settings})
                self._file.attrs["stimuli"] = json.dumps(sent)
                self._file.flush()

    def get_comments(self) -> list[dict]:
        """Comments of the block being recorded, as [{t, sample_index, text}]."""
        with self._lock:
            if self._file is None:
                return []
            return decode_comments(self._file["comments"][:])

    def set_comments(self, comments: list[dict]) -> None:
        """Replace the comments of the block being recorded (edit or delete)."""
        with self._lock:
            if self._file is not None:
                write_comments_dataset(self._file, comments)
                self._file.flush()

    def add_comment(self, text: str) -> float:
        """Comment at the latest acquired sample. Returns its stream time (s)."""
        with self._lock:
            if self._file is not None:
                ds = self._file["comments"]
                ds.resize((ds.shape[0] + 1,))
                ds[-1] = (self.last_t - (self._t_offset or 0.0),
                          self.samples_written, text)
                self._file.flush()
        return self.last_t

    def _consume(self):
        while True:
            chunk: Chunk | None = self._q.get()
            if chunk is None:   # sent by close()
                return
            self.last_t, self.last_index = float(chunk.t[-1]), chunk.start_index
            with self._lock:
                if self._file is None:
                    continue
                if self._t_offset is None:
                    self._t_offset = float(chunk.t[0])
                ds = self._file["raw_volts"]
                n0, k = ds.shape[1], chunk.data.shape[1]
                ds.resize((ds.shape[0], n0 + k))
                ds[:, n0:] = chunk.data
                if "t" in self._file:
                    tds = self._file["t"]
                    tds.resize((n0 + k,))
                    tds[n0:] = chunk.t - self._t_offset
                self.samples_written = n0 + k
