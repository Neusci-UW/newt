"""Simple Stimulus's "save just around each stimulus": with Record on, each stimulus sent
becomes a trial of its own, from Before it to After it (always the whole stimulus), and
nothing in between is recorded. Complex Stimulus's protocols do the same for their trials
(protocolspage); this is that for single stimuli, lined up the same way: on the recorded
stimulus when there's a Stim channel, else on when it was sent.
"""
from __future__ import annotations

import json

import numpy as np
from pyqtgraph.Qt import QtCore

from . import protocols as T
from . import recorder, session


class WindowSaver(QtCore.QObject):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.capture: T.Capture | None = None
        self._q = None
        self._sent: dict[int, tuple] = {}
        self._n = 0
        self._p = T.Protocol()
        self.timer = QtCore.QTimer(self, interval=50, timeout=self._tick)

    @property
    def running(self) -> bool:
        return self.capture is not None

    @property
    def pending(self) -> bool:
        """A stimulus sent whose window isn't saved yet."""
        return self.capture is not None and bool(self.capture.pending)

    def start(self, before_ms: float, after_ms: float) -> None:
        """Acquiring has started: collect the stream for the windows."""
        self.stop()
        main, cfg = self.main, self.main.cfg
        self._p = T.Protocol(before_ms=before_ms, after_ms=max(after_ms, 0.1))
        rate = main.backend.actual_rate if main.backend.hardware_timed else cfg.sample_rate
        chans = list(cfg.channels)
        mon = next((i for i, ch in enumerate(chans) if cfg.stim_monitor and ch.physical == cfg.stim_monitor), 0)
        to_units = lambda raw: np.vstack([ch.to_units(raw[i]) for i, ch in enumerate(chans)])
        keep = max(10.0, 3 * (before_ms + after_ms) / 1000.0 + 2)
        self.capture = T.Capture(rate, mon, to_units, keep, read_s=cfg.chunk_size / max(rate, 1e-9))
        self._q = main.backend.subscribe()
        self._sent, self._n = {}, 0
        self.timer.start()

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

    def stop(self) -> None:
        """Acquiring stopped: save what's still waiting, with whatever arrived."""
        if self.capture is None:
            return
        self._drain()
        for r in self.capture.resolve(self._p, force=True):
            self._save(r)
        self.timer.stop()
        if self._q is not None:
            self.main.backend.unsubscribe(self._q)
            self._q = None
        self.capture = None

    def _drain(self) -> None:
        if self._q is None or self.capture is None:
            return
        while True:
            try:
                c = self._q.get_nowait()
            except Exception:
                break
            self.capture.add(c.t, c.data)

    def _tick(self) -> None:
        self._drain()
        if self.capture is not None:
            for r in self.capture.resolve(self._p):
                self._save(r)

    def _save(self, r: T.Result) -> None:
        if r.raw is None or r.trial.n not in self._sent or self.capture is None:
            return
        main = self.main
        desc, s1, s2, delay_ms, out_rate, outs = self._sent.pop(r.trial.n)
        rate = self.capture.rate
        at = r.raw_before_s                             # the stimulus's time 0 in the block
        sent = {"t": at - delay_ms / 1000.0, "rate": out_rate, "settings": s1.to_dict(), "output": outs[0]}
        if s2 is not None:
            sent["second"] = {"output": outs[1], "settings": s2.to_dict()}
        attrs = {"stimuli": json.dumps([sent])}
        if main.trial_label:
            attrs["label"] = main.trial_label
        path = session.next_block_path(main._ensure_session_dir())
        try:
            recorder.write_block(path, main.backend, r.raw, rate, attrs,
                                 [{"t": at, "sample_index": int(round(at * rate)), "text": desc}])
        except OSError as e:
            main.statusBar().showMessage(f"⚠ Couldn't save the stimulus's trial: {e}", 10000)
            return
        main.session_block_added(path)
        if np.isfinite(r.t0):                           # a trial bar on the live history
            main.mark_trial_start(r.t0 - r.raw_before_s, r.raw.shape[1] / rate)
        main.analysis_page.source_changed()
        main._block_changed(started=False)
