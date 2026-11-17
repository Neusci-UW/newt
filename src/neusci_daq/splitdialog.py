"""Right-click › Split where the stimulus goes back to 0…: the trial's stimulus channel and
a response, with the cuts drawn where they'd land as you move the settings, and the new
trials listed with the stimulus found in each. Splitting itself is trialsplit.split_at_rest.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import trialsplit


class SplitAtRestDialog(QtWidgets.QDialog):
    def __init__(self, parent, rec: dict, stim_channel: str, n_trials: int, before_ms: float, title: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Split where the stimulus goes back to 0")
        names = rec["channel_names"]
        self.fs = float(rec["sample_rate"])
        self.y = np.asarray(rec["data"][names.index(stim_channel)], float)
        units = rec.get("units") or ["V"] * len(names)
        self.unit = units[names.index(stim_channel)] or "V"
        other = next((n for n in names if n != stim_channel), None)
        t = np.arange(self.y.size) / self.fs
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(f"<b>{title}</b> · {t[-1] + 1 / self.fs:.1f} s · stimulus channel: {stim_channel}"
                                       if title else f"Stimulus channel: {stim_channel}"))
        self.plots = pg.GraphicsLayoutWidget()
        self.plots.setMinimumSize(640, 260)
        self.stim_plot = self.plots.addPlot(row=0, col=0)
        self.stim_plot.setLabel("left", stim_channel)
        self.stim_plot.plot(t, self.y, pen=pg.mkPen(self.palette().color(QtGui.QPalette.ColorRole.WindowText)))
        self.resp_plot = None
        if other is not None:
            self.resp_plot = self.plots.addPlot(row=1, col=0)
            self.resp_plot.setLabel("left", other)
            self.resp_plot.setXLink(self.stim_plot)
            self.resp_plot.plot(t, np.asarray(rec["data"][names.index(other)], float), pen=pg.mkPen("#d0532c"))
        for p in (self.stim_plot, self.resp_plot):
            if p is not None:
                p.setDownsampling(auto=True, mode="peak")
                p.setClipToView(True)
                p.setMouseEnabled(x=True, y=False)
        (self.resp_plot or self.stim_plot).setLabel("bottom", "Time (s)")
        self._marks: list = []
        lay.addWidget(self.plots, 1)

        form = QtWidgets.QFormLayout()
        self.gap = self._slider(1, 1000, int(trialsplit.GAP_MS), " ms")
        self.gap[0].setToolTip("Short: each pulse of a train becomes its own trial. Long: a train stays one trial")
        form.addRow("A stimulus ends when it's back to 0 for", self.gap[2])
        self.on = self._slider(1, 50, int(round(trialsplit.ON * 100)), "")
        self.on[0].setToolTip("How close to 0 counts as back at 0, as a share of the stimulus's biggest swing")
        form.addRow("Back to 0 means within", self.on[2])
        self.before = self._slider(0, 1000, int(round(before_ms)), " ms")
        self.before[0].setToolTip("Each new trial starts this long before its stimulus, but never in the stimulus "
                                  "before it")
        form.addRow("Start each trial before its stimulus by", self.before[2])
        lay.addLayout(form)
        note = QtWidgets.QLabel("The new trials sit back to back, so nothing after the first is lost; the last runs "
                                "to the end. The originals are hidden, not deleted (tick one to bring it back), and "
                                "Groups puts the same stimuli together.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray")
        lay.addWidget(note)
        self.table = QtWidgets.QTreeWidget()
        self.table.setHeaderLabels(["New trial", "Stimulus found", "Length"])
        self.table.setRootIsDecorated(False)
        self.table.setMaximumHeight(170)
        lay.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        self.only_this = QtWidgets.QRadioButton("This trial")
        self.every = QtWidgets.QRadioButton(f"The {n_trials} highlighted trials")
        self.only_this.setChecked(True)
        row.addWidget(self.only_this)
        row.addWidget(self.every)
        self.every.setVisible(n_trials > 1)
        self.every.toggled.connect(lambda *_: self._timer.start())
        row.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton("Split", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        lay.addLayout(row)
        self._timer = QtCore.QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self.refresh)
        self.refresh()
        self.resize(760, 640)

    def _slider(self, lo: int, hi: int, value: int, unit: str):
        s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, minimum=lo, maximum=hi, value=min(hi, max(lo, value)))
        label = QtWidgets.QLabel()
        label.setMinimumWidth(150)
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(s, 1)
        h.addWidget(label)
        s.valueChanged.connect(lambda *_: self._timer.start())
        return s, label, w

    def settings(self) -> tuple[float, float, float, bool]:
        """(back at 0 for, ms; on, share of the biggest swing; Before, ms; every highlighted trial)."""
        return (float(self.gap[0].value()), self.on[0].value() / 100, float(self.before[0].value()),
                self.every.isChecked())

    def cuts(self) -> list:
        gap, on, before, _ = self.settings()
        return trialsplit.rest_cuts(self.y, self.fs, before, gap, on)

    def refresh(self) -> None:
        gap, on, before, _ = self.settings()
        rest = trialsplit.SM.rest_level(self.y)
        swing = float(np.nanmax(np.abs(self.y - rest))) if rest is not None else 0.0
        self.gap[1].setText(f"{gap:g} ms")
        self.on[1].setText(f"±{trialsplit._sig(on * swing)} {self.unit} ({round(on * 100)}% of the biggest)")
        self.before[1].setText(f"{before:g} ms")
        cuts = self.cuts()
        for plot, item in self._marks:
            plot.removeItem(item)
        self._marks = []
        accent = QtGui.QColor("#0d6b7c")
        for k, c in enumerate(cuts):
            shade = QtGui.QColor(accent)
            shade.setAlpha(22 if k % 2 else 50)
            for plot in (self.stim_plot, self.resp_plot):
                if plot is None:
                    continue
                region = pg.LinearRegionItem((c.lo / self.fs, c.hi / self.fs), movable=False, brush=shade,
                                             pen=pg.mkPen(None))
                region.setZValue(-10)
                plot.addItem(region)
                self._marks.append((plot, region))
                if k:
                    line = pg.InfiniteLine(c.lo / self.fs, pen=pg.mkPen(accent, width=1.5,
                                                                         style=QtCore.Qt.PenStyle.DashLine))
                    plot.addItem(line)
                    self._marks.append((plot, line))
            label = pg.TextItem(str(k + 1), color=accent, anchor=(0, 0))
            label.setPos(c.lo / self.fs, float(np.nanmax(self.y)))
            self.stim_plot.addItem(label)
            self._marks.append((self.stim_plot, label))
        if rest is not None:                          # the band that counts as back at 0
            band = pg.LinearRegionItem((rest - on * swing, rest + on * swing), orientation="horizontal",
                                       movable=False, brush=QtGui.QColor(128, 128, 128, 40), pen=pg.mkPen(None))
            self.stim_plot.addItem(band)
            self._marks.append((self.stim_plot, band))
        self.table.clear()
        for k, c in enumerate(cuts):
            self.table.addTopLevelItem(QtWidgets.QTreeWidgetItem(
                [str(k + 1), trialsplit.describe(self.y[c.lo:c.hi], self.fs, self.unit),
                 f"{(c.hi - c.lo) / self.fs:.2f} s"]))
        n = len(cuts)
        self.go.setEnabled(n >= 2 or self.every.isChecked())
        self.go.setText("Split them" if self.every.isChecked() else f"Split into {n} trials" if n >= 2
                        else "Only one stimulus: nothing to split")
