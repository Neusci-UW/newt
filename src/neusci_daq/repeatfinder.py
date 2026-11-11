"""The repeats of a repeating stimulus, which every chart against phase cuts its cycles by."""
from __future__ import annotations

import numpy as np
from pyqtgraph.Qt import QtWidgets

from . import analysis as A
from .unitbox import UnitSpinBox
from .viewkit import View


class RepeatFinder(View):
    """The repeats of a repeating stimulus, on the Stimulus channel from Data. Every chart on
    its phase shares them: these settings show with whichever of those charts was clicked."""

    def __init__(self, page: "AnalysisPage"):
        super().__init__(page)
        self.setParent(page)
        self.hide()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QtWidgets.QGroupBox("Repeats, for every phase chart")
        f = QtWidgets.QFormLayout(box)
        self.cycle_skip = QtWidgets.QSpinBox(minimum=0, maximum=10000)
        self.cycle_skip.setToolTip("Leave out this many cycles at the start of each trial, "
                                   "while the response settles")
        self.cycle_len = UnitSpinBox(0, 600000, "ms", step=10)
        self.cycle_len.setToolTip("How long one repeat is. 0: Argus finds it, from what was sent if the trial saved "
                                  "it, else by matching the stimulus's shape against itself, or from its rises "
                                  "through its middle level")
        self._cycle_start: float | None = None      # where you marked a repeat (s)
        self.btn_mark = QtWidgets.QPushButton("Mark one")
        self.btn_mark.setCheckable(True)
        self.btn_mark.setToolTip("Then drag across one repeat on the Traces or Stimulus chart, to use it as the template")
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        f.addRow("Length", self.cycle_len)
        f.addRow(self.btn_mark)
        f.addRow("Skip first", self.cycle_skip)
        f.addRow(self.message)
        outer.addWidget(box)
        self.cycle_skip.valueChanged.connect(lambda *_: self.page.canvas.refresh_cycles())
        self.cycle_len.valueChanged.connect(self._repeat_typed)
        self.btn_mark.toggled.connect(lambda *_: self.listen())

    def stimulus_channel_changed(self) -> None:
        self.page.canvas.stimulus_changed()
        self.page.canvas.refresh_cycles()

    def channel(self) -> str:
        return self.page.stim_channel.currentData() or ""

    def setting(self) -> tuple[float, float | None]:
        """What you said of the repeats: (length s, 0 to find it; start s, or None)."""
        return self.cycle_len.value() / 1000, self._cycle_start

    def _repeat_typed(self) -> None:
        self._cycle_start = None
        self._changed()

    def _changed(self) -> None:
        if self.page._looks["stim_fit"] in ("Repeat average", "Harmonics"):
            self.page.groups_changed()              # those fits follow the repeats
        else:
            self.page.canvas.refresh_cycles()

    def listen(self) -> None:
        for c in self.page.canvas.here():
            c.listen_for_mark(self._marked if self.btn_mark.isChecked() else None)

    def _marked(self, x0: float, x1: float, finished: bool) -> None:
        if finished and x1 > x0:
            self.cycle_len.blockSignals(True)
            self.cycle_len.setValue((x1 - x0) * 1000)
            self.cycle_len.blockSignals(False)
            self._cycle_start = x0
            self.btn_mark.setChecked(False)
            self._changed()

    def found(self, b: int) -> A.Repeats:
        """The repeats in trial b, worked out once for each way of finding them."""
        page, stim = self.page, self.channel()
        y = page.trace(b, stim) if stim else None
        if y is None:
            return A.Repeats(np.empty((0, 2)))
        hand = self.setting()
        return self.main.store.memo(page._paths[b], ("repeats", stim, page.gain_of(stim), page._looks["stim_fit"], hand),
                                    lambda: A.find_repeats(y, page.blocks[b]["sample_rate"], *hand, page.sent(b)))

    def show_found(self, blocks: list[int]) -> None:
        """Say how many repeats these blocks have."""
        n = sum(len(self.found(b).bounds) for b in blocks)
        self.message.setText(f"{n} in {len(blocks)} trial(s)" if n else "None found")
