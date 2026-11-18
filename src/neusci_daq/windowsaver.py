"""Simple Stimulus's "save just around each stimulus" in the Qt app: stimwindows.py does
the work; this runs it on a timer and shows each new trial (the trials list, a bar on the
live history, Analysis).
"""
from __future__ import annotations

import numpy as np
from pyqtgraph.Qt import QtCore

from . import protocols as T
from .stimwindows import StimWindows


class WindowSaver(QtCore.QObject):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.windows = StimWindows(main)
        self.timer = QtCore.QTimer(self, interval=50, timeout=self._tick)

    @property
    def capture(self) -> T.Capture | None:
        return self.windows.capture

    @property
    def running(self) -> bool:
        return self.windows.running

    @property
    def pending(self) -> bool:
        """A stimulus sent whose window isn't saved yet."""
        return self.windows.pending

    def start(self, before_ms: float, after_ms: float) -> None:
        """Acquiring has started: collect the stream for the windows."""
        self.stop()
        self.windows.start(before_ms, after_ms)
        self.timer.start()

    def sent(self, wave: np.ndarray, out_rate: float, desc: str, stimulus, stimulus2, outs) -> None:
        """A stimulus just went out: keep its window once the After part has arrived."""
        self.windows.sent(wave, out_rate, desc, stimulus, stimulus2, outs)

    def stop(self) -> None:
        """Acquiring stopped: save what's still waiting, with whatever arrived."""
        if not self.windows.running:
            return
        for r in self.windows.stop():
            self._save(r)
        self.timer.stop()

    def _tick(self) -> None:
        for r in self.windows.tick():
            self._save(r)

    def _save(self, r: T.Result) -> None:
        main = self.main
        try:
            path = self.windows.write(r, main._ensure_session_dir, main.trial_label)
        except OSError as e:
            main.statusBar().showMessage(f"⚠ Couldn't save the stimulus's trial: {e}", 10000)
            return
        if path is None:
            return
        main.session_block_added(path)
        if np.isfinite(r.t0):                           # a trial bar on the live history
            main.mark_trial_start(r.t0 - r.raw_before_s, r.raw.shape[1] / self.windows.rate)
        main.analysis_page.source_changed()
        main._block_changed(started=False)
