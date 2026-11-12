"""The shape of the action potentials found, as a phase plot of V against dV/dt, and
against time.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtGui, QtWidgets

from . import analysis as A
from .chartbase import Chart
from .plotzoom import plot_widget
from .unitbox import UnitSpinBox
from .viewkit import label_axis

MAX_SHOWN = 150                    # spikes of each unit drawn behind its average


class ShapeChart(Chart):
    help = "waveforms"
    title = ""

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.plot = plot_widget()
        p = self.plot.getPlotItem()
        p.setTitle(self.title)
        p.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setMinimumHeight(40)
        self.body.addWidget(self.plot)
        self.each = QtWidgets.QCheckBox("Each spike")
        self.each.setChecked(True)
        self.each.setToolTip(f"Draw the spikes behind each unit's average (up to {MAX_SHOWN} of each)")
        self.field("each", "", self.each)
        self.smooth = UnitSpinBox(0, 5, "ms", step=0.05)
        self.smooth.setValue(0.1)
        self.smooth.setToolTip("Differentiating amplifies noise: average over this long first (0 = none)")
        self.field("smooth", "Smooth for dV/dt", self.smooth)
        self.apply_theme(self._mode)

    def plots(self) -> list:
        return [self.plot]

    def xy(self, t: np.ndarray, v: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """What it plots of the time, voltage and slope of a spike."""
        raise NotImplementedError

    def labels(self, p: pg.PlotItem, name: str, unit: str) -> None:
        raise NotImplementedError

    def no_events(self) -> str:
        return self.title

    def draw(self) -> None:
        p = self.plot.getPlotItem()
        p.clear()
        ev = self.events()
        if ev is None or not len(ev.t):
            p.setTitle(self.no_events(), color=self._fg())
            return
        fs = 1000 / float(np.median(np.diff(ev.wave_t_ms)))
        total = 0
        for u in sorted(self.units()):
            w = ev.waves[ev.unit == u]
            if not len(w):
                continue
            total += len(w)
            color = self.finder.qcolor(u)
            if self.each.isChecked():
                self._draw_each(w[::max(1, -(-len(w) // MAX_SHOWN))], ev.wave_t_ms, fs, color)
            mean = w.mean(0)
            x, y = self.xy(ev.wave_t_ms, mean, A.derivative(mean, fs, 1, self.smooth.value()))
            p.plot(x, y, pen=pg.mkPen(color, width=2.5))
        name = self.finder.channel.currentText()
        self.labels(p, name, self.page.unit_of(name))
        p.setTitle(self.titled(total), color=self._fg())
        p.enableAutoRange()

    def titled(self, total: int) -> str:
        return self.title

    def _draw_each(self, w: np.ndarray, t: np.ndarray, fs: float, color) -> None:
        """Spikes behind the average, as one item."""
        c = QtGui.QColor(color)
        c.setAlpha(round(45 * c.alphaF()))
        d = np.array([A.derivative(y, fs, 1, self.smooth.value()) for y in w])
        gap = np.full((len(w), 1), np.nan)
        x, y = self.xy(np.broadcast_to(t, w.shape), w, d)
        self.plot.getPlotItem().addItem(pg.PlotCurveItem(np.hstack((x, gap)).ravel(), np.hstack((y, gap)).ravel(),
                                                         connect="finite", pen=pg.mkPen(c, width=1)))


class PhasePlotChart(ShapeChart):
    kind = "V against dV/dt"
    tip = "How fast does the voltage change at each voltage (the phase plot)?"
    title = "V against dV/dt"

    def xy(self, t, v, d):
        return v, d

    def labels(self, p, name, unit):
        label_axis(p, "bottom", f"{name} from baseline", unit)
        label_axis(p, "left", "dV/dt", f"{unit}/s")

    def no_events(self) -> str:
        return "V against dV/dt: find action potentials first"

    def titled(self, total: int) -> str:
        return f"V against dV/dt ({total} spikes)"


class VoltsChart(ShapeChart):
    kind = "V"
    tip = "What does the voltage do through a spike?"
    link = "spike"
    title = "V"

    def xy(self, t, v, d):
        return t, v

    def labels(self, p, name, unit):
        p.setLabel("bottom", "ms from the peak")
        label_axis(p, "left", name, unit)


class SlopeChart(ShapeChart):
    kind = "dV/dt"
    tip = "How fast does the voltage rise and fall through a spike?"
    link = "spike"
    title = "dV/dt"

    def xy(self, t, v, d):
        return t, d

    def labels(self, p, name, unit):
        p.setLabel("bottom", "ms from the peak")
        label_axis(p, "left", "dV/dt", f"{unit}/s")
