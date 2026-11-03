"""Mouse wheel rules shared by every plot in the app:

  wheel                      zoom the time (x) axis around the mouse
  Cmd+wheel (Ctrl off a Mac) zoom the voltage (y) axis; so does the wheel over the left axis
  Shift+wheel, or sideways   move back and forward in time, a tenth of the view per notch

Acquisition plots zoom by changing Window instead (see gui._TraceViewBox).
"""
from __future__ import annotations

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore


class WheelViewBox(pg.ViewBox):
    def wheelEvent(self, ev, axis=None):
        mods = ev.modifiers()
        # Qt reports Cmd as Control on macOS.
        if axis == 1 or (axis is None and mods & QtCore.Qt.KeyboardModifier.ControlModifier):
            super().wheelEvent(ev, axis=1)
            return
        # macOS turns Shift+wheel into a sideways scroll, so check for both.
        if mods & QtCore.Qt.KeyboardModifier.ShiftModifier or ev.orientation() == QtCore.Qt.Orientation.Horizontal:
            self.pan_time(ev.delta())
            ev.accept()
            return
        s = 1.02 ** (ev.delta() * self.state["wheelScaleFactor"])     # same feel as pyqtgraph's zoom
        self.zoom_time(s, self.mapToView(ev.pos()).x() if axis is None else None)
        ev.accept()

    def zoom_time(self, s: float, x: float | None = None) -> None:
        """Scale the x range by s around x (the middle when the wheel is over an axis)."""
        lo, hi = self.viewRange()[0]
        x = (lo + hi) / 2 if x is None else x
        self.setXRange(x - (x - lo) * s, x + (hi - x) * s, padding=0)

    def pan_time(self, delta: float) -> None:
        """Move a tenth of the view per wheel notch: earlier for wheel up, later for down."""
        lo, hi = self.viewRange()[0]
        d = -delta / 120 * 0.1 * (hi - lo)
        self.setXRange(lo + d, hi + d, padding=0)


def light_curve(item: pg.PlotDataItem) -> pg.PlotDataItem:
    """Draw about one min/max pair per pixel instead of pyqtgraph's ~5 samples per
    pixel. Peaks (spikes) stay; at 20 kHz this is most of the drawing time. The plot
    needs setDownsampling(auto=True, mode="peak")."""
    item.opts["autoDownsampleFactor"] = 1.0
    item.updateItems()
    return item


def plot_widget(**kw) -> pg.PlotWidget:
    """A pg.PlotWidget with the app's wheel rules."""
    return pg.PlotWidget(viewBox=WheelViewBox(), **kw)
