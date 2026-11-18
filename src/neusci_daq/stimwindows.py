"""Simple Stimulus's "save just around each stimulus": with Record on, each stimulus sent
becomes a trial of its own, from Before it to After it (always the whole stimulus), and
nothing in between is recorded. Complex Stimulus's protocols do the same for their trials
(protocolspage); this is that for single stimuli, lined up the same way: on the recorded
stimulus when there's a Stim channel, else on when it was sent.

Plain Python (protocols.Capture does the lining up), so the Qt app (windowsaver.py, on a
timer) and the HTML front end (web/record.py, on a thread) share it: call tick() every
~50 ms and write() each window it gives back.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import numpy as np

from . import protocols as T
from . import recorder, session


class StimWindows:
    def __init__(self, source):
        """`source` has the acquisition's .backend and .cfg (the Qt main window, or the web's Live)."""
        self.source = source
        self.capture: T.Capture | None = None
        self.rate = 0.0
        self._q = None
        self._sent: dict[int, tuple] = {}
        self._n = 0
        self._p = T.Protocol()

    @property
    def running(self) -> bool:
        return self.capture is not None

    @property
    def pending(self) -> bool:
        """A stimulus sent whose window isn't saved yet."""
        return self.capture is not None and bool(self.capture.pending)

    def start(self, before_ms: float, after_ms: float) -> None:
        """Acquiring has started: collect the stream for the windows. (Call stop() first if
        it's running, and save what that gives back.)"""
        backend, cfg = self.source.backend, self.source.cfg
        self._p = T.Protocol(before_ms=before_ms, after_ms=max(after_ms, 0.1))
        rate = backend.actual_rate if backend.hardware_timed else cfg.sample_rate
        chans = list(cfg.channels)
        mon = next((i for i, ch in enumerate(chans) if cfg.stim_monitor and ch.physical == cfg.stim_monitor), 0)
        to_units = lambda raw: np.vstack([ch.to_units(raw[i]) for i, ch in enumerate(chans)])
        keep = max(10.0, 3 * (before_ms + after_ms) / 1000.0 + 2)
        self.capture = T.Capture(rate, mon, to_units, keep, read_s=cfg.chunk_size / max(rate, 1e-9))
        self.rate = self.capture.rate
        self._q = backend.subscribe()
        self._sent, self._n = {}, 0

    def sent(self, wave: np.ndarray, out_rate: float, desc: str, stimulus, stimulus2, outs) -> None:
        """A stimulus just went out: keep its window once the After part has arrived."""
        if self.capture is None or stimulus is None:
            return
        self._drain()
        self._n += 1
        first = wave[0] if np.ndim(wave) == 2 else wave
        delay_ms = stimulus.onset_samples(out_rate) * 1000.0 / out_rate
        trial = T.Trial(n=self._n, rep=1, ia=0, ib=0, a=float(self._n), b=None)
        self.capture.sent(trial, np.asarray(first, float), out_rate, delay_ms)
        self._sent[self._n] = (desc, stimulus, stimulus2, delay_ms, out_rate, outs)

    def tick(self) -> list[T.Result]:
        """The windows whose After part has all arrived (write() each)."""
        self._drain()
        return self.capture.resolve(self._p) if self.capture is not None else []

    def stop(self) -> list[T.Result]:
        """Acquiring stopped: the windows still waiting, with whatever arrived (write() each)."""
        if self.capture is None:
            return []
        self._drain()
        out = self.capture.resolve(self._p, force=True)
        if self._q is not None:
            self.source.backend.unsubscribe(self._q)
            self._q = None
        self.capture = None
        return out

    def _drain(self) -> None:
        if self._q is None or self.capture is None:
            return
        while True:
            try:
                c = self._q.get_nowait()
            except Exception:
                break
            self.capture.add(c.t, c.data)

    def write(self, r: T.Result, folder: Callable[[], Path], label: str = "") -> Path | None:
        """Save window `r` as a trial of its own in folder() (asked only if there's one to
        save), with what was sent and a comment saying so: its path, or None if there was
        nothing to save. OSError if it couldn't be written."""
        if r.raw is None or r.trial.n not in self._sent:
            return None
        desc, s1, s2, delay_ms, out_rate, outs = self._sent.pop(r.trial.n)
        rate = self.rate
        at = r.raw_before_s                             # the stimulus's time 0 in the block
        sent = {"t": at - delay_ms / 1000.0, "rate": out_rate, "settings": s1.to_dict(), "output": outs[0]}
        if s2 is not None:
            sent["second"] = {"output": outs[1], "settings": s2.to_dict()}
        attrs = {"stimuli": json.dumps([sent])}
        if label:
            attrs["label"] = label
        path = session.next_block_path(folder())
        recorder.write_block(path, self.source.backend, r.raw, rate, attrs,
                             [{"t": at, "sample_index": int(round(at * rate)), "text": desc}])
        return path
