"""The Waveforms sub-tab of Analysis: the shape of the action potentials found on the
Spike Sorting tab, as a phase plot of V against dV/dt, and against time.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import style, theme
from .helpview import add_help
from .plotzoom import plot_widget
from .stimwindow import UnitSpinBox
from .viewkit import ChartToggles, View, even_out, label_axis, scroll_column, style_plot

MAX_SHOWN = 150                    # spikes of each unit drawn behind its average


class WaveformView(View):
    def __init__(self, page):
        super().__init__(page)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(12)

        side = QtWidgets.QWidget()
        side.setMaximumWidth(360)
        left = QtWidgets.QVBoxLayout(side)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        box = QtWidgets.QGroupBox("Action potentials")
        f = QtWidgets.QFormLayout(box)
        self.each = QtWidgets.QCheckBox("Each spike")
        self.each.setChecked(True)
        self.each.setToolTip(f"Draw the spikes behind each unit's average (up to {MAX_SHOWN} of each)")
        self.smooth = UnitSpinBox(0, 5, "ms", step=0.05)
        self.smooth.setValue(0.1)
        self.smooth.setToolTip("Differentiating amplifies noise: average over this long first (0 = none)")
        f.addRow(self.each)
        f.addRow("Smooth for dV/dt", self.smooth)
        left.addWidget(box)
        add_help(box, "waveforms")
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.phase = plot_widget()
        self.phase.getPlotItem().setTitle("V against dV/dt")
        column = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.volts = plot_widget()
        self.volts.getPlotItem().setTitle("V")
        self.slope = plot_widget()
        self.slope.getPlotItem().setTitle("dV/dt")
        self.slope.getPlotItem().setXLink(self.volts)
        self.slope.getPlotItem().setLabel("bottom", "ms from the peak")
        self.volts.getPlotItem().setLabel("bottom", "ms from the peak")
        for pw in (self.phase, self.volts, self.slope):
            pw.getPlotItem().showGrid(x=True, y=True, alpha=0.2)
        column.addWidget(self.volts)
        column.addWidget(self.slope)
        right.addWidget(self.phase)
        right.addWidget(column)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        self._column, self._right = column, right
        self.charts = ChartToggles(self.main.settings, "waveforms", [
            ("V against dV/dt", "The phase plot: how fast the voltage changes at each voltage", self.phase),
            ("V", "The spikes against time", self.volts),
            ("dV/dt", "How fast the voltage changes through each spike", self.slope)], self._charts_changed)
        left.addWidget(self.charts)
        left.addWidget(self.message)
        left.addStretch(1)
        outer.addWidget(scroll_column(side))
        outer.addWidget(right, 1)
        self.charts.apply()

        self.each.toggled.connect(lambda *_: self.redraw())
        self.smooth.valueChanged.connect(lambda *_: self.redraw())
        self.apply_theme(style.LIGHT)

    def _charts_changed(self) -> None:
        on = self.charts.is_on
        self._column.setVisible(on("V") or on("dV/dt"))
        even_out(self._right)
        even_out(self._column)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.redraw()

    def redraw(self) -> None:
        plots = [pw.getPlotItem() for pw in (self.phase, self.volts, self.slope)]
        for p in plots:
            p.clear()
        pp, vp, sp = plots
        ev = self.page.events
        if ev is None or not len(ev.t):
            pp.setTitle("V against dV/dt: find action potentials first", color=self._fg())
            vp.setTitle("V", color=self._fg())
            sp.setTitle("dV/dt", color=self._fg())
            return
        fs = 1000 / float(np.median(np.diff(ev.wave_t_ms)))
        total = 0
        for u in sorted(self.page.raw.shown_units()):
            w = ev.waves[ev.unit == u]
            if not len(w):
                continue
            total += len(w)
            color = self.page.raw.color_of(u)
            if self.each.isChecked():
                self._draw_each(w[::max(1, -(-len(w) // MAX_SHOWN))], ev.wave_t_ms, fs, color)
            mean = w.mean(0)
            slope = A.derivative(mean, fs, 1, self.smooth.value())
            pen = pg.mkPen(color, width=2.5)
            pp.plot(mean, slope, pen=pen)
            vp.plot(ev.wave_t_ms, mean, pen=pen)
            sp.plot(ev.wave_t_ms, slope, pen=pen)
        name = self.page.raw.channel.currentText()
        unit = self.page.unit_of(name)
        label_axis(pp, "bottom", f"{name} from baseline", unit)
        label_axis(pp, "left", "dV/dt", f"{unit}/s")
        label_axis(vp, "left", name, unit)
        label_axis(sp, "left", "dV/dt", f"{unit}/s")
        pp.setTitle(f"V against dV/dt ({total} spikes)", color=self._fg())
        vp.setTitle("V", color=self._fg())
        sp.setTitle("dV/dt", color=self._fg())
        for p in plots:
            p.enableAutoRange()

    def _draw_each(self, w: np.ndarray, t: np.ndarray, fs: float, color: str) -> None:
        """Spikes behind the average, each kind of plot as one item."""
        c = QtGui.QColor(color)
        c.setAlpha(45)
        pen = pg.mkPen(c, width=1)
        d = np.array([A.derivative(y, fs, 1, self.smooth.value()) for y in w])
        gap = np.full((len(w), 1), np.nan)
        T = np.hstack((np.broadcast_to(t, w.shape), gap)).ravel()
        V, D = np.hstack((w, gap)).ravel(), np.hstack((d, gap)).ravel()
        self.phase.getPlotItem().addItem(pg.PlotCurveItem(V, D, connect="finite", pen=pen))
        self.volts.getPlotItem().addItem(pg.PlotCurveItem(T, V, connect="finite", pen=pen))
        self.slope.getPlotItem().addItem(pg.PlotCurveItem(T, D, connect="finite", pen=pen))

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        for pw in (self.phase, self.volts, self.slope):
            style_plot(pw, th)
