"""Mouse wheel rules shared by every plot in the app:

  wheel                      zoom the time (x) axis around the mouse
  Cmd+wheel (Ctrl off a Mac) zoom the voltage (y) axis; so does the wheel over the left axis
  Shift+wheel, or sideways   move back and forward in time, a tenth of the view per notch

Acquisition plots zoom by changing Window instead (see gui._TraceViewBox).
"""
from __future__ import annotations

import warnings

import numpy as np
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


class TraceCurve(pg.PlotDataItem):
    """A long, evenly sampled trace. Zoomed out, each pixel shows either the average
    of the samples it covers (mode "mean": a moving average, smooth, but spikes
    shrink) or their min and max (mode "peak": keeps spikes). Groups of samples are
    fixed to sample numbers and their size steps in powers of two, so the trace
    doesn't shimmer as it scrolls or zooms (pyqtgraph's own downsampling regroups
    from the left edge of the view every frame, which aliases)."""

    mode = "mean"                      # for every TraceCurve; View › Zoomed-out traces

    def setData(self, *args, **kw):
        if len(args) == 2 and not kw:
            self._full = (np.asarray(args[0], float), np.asarray(args[1], float))
            self._key = None
            self._redisplay()
        else:
            super().setData(*args, **kw)

    def viewRangeChanged(self, vb=None, ranges=None, changed=None):
        super().viewRangeChanged(vb, ranges, changed)
        if changed is None or changed[0]:
            # Once, after this round of events: live updates move the view and set new
            # data together, and should only regroup once. The timer belongs to this
            # item, so if the plot is deleted first it goes too and never fires (a
            # QTimer.singleShot would call into the deleted item and crash).
            timer = getattr(self, "_timer", None)
            if timer is None:
                timer = self._timer = QtCore.QTimer(self)
                timer.setSingleShot(True)
                timer.setInterval(0)
                timer.timeout.connect(self._redisplay)
            if not timer.isActive():
                timer.start()

    def dataBounds(self, ax, frac=1.0, orthoRange=None):
        """Auto-scale to the samples themselves, not the averages drawn: averaging
        shrinks noise and spikes when zoomed out, and the voltage axis would shrink
        with it, looking as if the wheel zoomed voltage too."""
        x, y = getattr(self, "_full", (np.empty(0), np.empty(0)))
        if not len(x):
            return super().dataBounds(ax, frac, orthoRange)
        if ax == 0:
            return float(x[0]), float(x[-1])
        if orthoRange is not None:
            i0, i1 = np.searchsorted(x, orthoRange)
            y = y[i0:i1]
        if not len(y):
            return None, None
        if frac >= 1.0:
            return float(np.nanmin(y)), float(np.nanmax(y))
        return tuple(float(v) for v in np.nanpercentile(y, [50 * (1 - frac), 50 * (1 + frac)]))

    def getOriginalDataset(self):
        """The full trace, not the groups drawn."""
        if hasattr(self, "_full"):
            return self._full
        return super().getOriginalDataset()

    def refresh(self) -> None:
        """Redraw, e.g. after the mode changed."""
        self._key = None
        self._redisplay()

    def _redisplay(self) -> None:
        x, y = getattr(self, "_full", (np.empty(0), np.empty(0)))
        vb = self.getViewBox()
        if len(x) < 2 or vb is None:
            super().setData(x, y)
            return
        lo, hi = vb.viewRange()[0]
        dt = (x[-1] - x[0]) / (len(x) - 1) or 1.0
        i0, i1 = np.searchsorted(x, [lo, hi])
        visible = max(1, int(i1 - i0))
        width = max(1.0, vb.width())
        ds = 1 if visible <= width else 2 ** int(np.ceil(np.log2(visible / width)))
        first = int(round(x[0] / dt))                   # sample number of x[0]
        a = max(0, int(i0) - ds)
        a += (-(first + a)) % ds                        # start a group on a multiple of ds
        n = (min(len(x), int(i1) + ds) - a) // ds
        key = (ds, first + a, n, self.mode)
        if key == getattr(self, "_key", None):
            return
        self._key = key
        if n <= 0:
            super().setData(np.empty(0), np.empty(0))
        elif ds == 1:
            super().setData(x[a:a + n], y[a:a + n])
        elif self.mode == "peak":
            yy = y[a:a + n * ds].reshape(n, ds)
            xx = x[a:a + n * ds:ds]
            super().setData(np.repeat(xx, 2), np.column_stack([yy.min(1), yy.max(1)]).ravel())
        else:
            super().setData(x[a:a + n * ds].reshape(n, ds).mean(1), y[a:a + n * ds].reshape(n, ds).mean(1))


class OverlayCurve(TraceCurve):
    """Many traces sampled at the same rate, all starting at time 0 (the rows of
    Y; shorter ones padded with NaN), drawn on top of each other as one item and
    grouped together like TraceCurve, so hundreds of blocks stay quick to zoom."""

    def set_traces(self, Y: np.ndarray, fs: float) -> None:
        self._Y, self._fs = np.asarray(Y, float), float(fs)
        self._key = None
        self._redisplay()

    def getOriginalDataset(self):
        Y = getattr(self, "_Y", np.empty((0, 0)))
        return np.arange(Y.shape[1]) / getattr(self, "_fs", 1.0), Y

    def dataBounds(self, ax, frac=1.0, orthoRange=None):
        Y = getattr(self, "_Y", None)
        if Y is None or not Y.size:
            return None, None
        if ax == 0:
            return 0.0, (Y.shape[1] - 1) / self._fs
        if orthoRange is not None:
            i0, i1 = (int(np.clip(v * self._fs, 0, Y.shape[1])) for v in orthoRange)
            Y = Y[:, i0:max(i1, i0 + 1)]
        with np.errstate(all="ignore"):
            return float(np.nanmin(Y)), float(np.nanmax(Y))

    def _redisplay(self) -> None:
        Y = getattr(self, "_Y", None)
        vb = self.getViewBox()
        if Y is None or not Y.size or vb is None:
            pg.PlotDataItem.setData(self, np.empty(0), np.empty(0))
            return
        rows, N = Y.shape
        fs = self._fs
        lo, hi = vb.viewRange()[0]
        i0, i1 = (int(np.clip(v * fs, 0, N)) for v in (lo, hi))
        visible = max(1, i1 - i0)
        width = max(1.0, vb.width())
        ds = 1 if visible <= width else 2 ** int(np.ceil(np.log2(visible / width)))
        a = max(0, i0 - ds)
        a -= a % ds                                     # groups start on multiples of ds
        n = (min(N, i1 + ds) - a) // ds
        key = (ds, a, n, self.mode, rows)
        if key == getattr(self, "_key", None):
            return
        self._key = key
        if n <= 0:
            pg.PlotDataItem.setData(self, np.empty(0), np.empty(0))
            return
        block = Y[:, a:a + n * ds].reshape(rows, n, ds)
        start = (a + np.arange(n) * ds) / fs
        # shorter blocks are padded with NaN: all-NaN groups are expected, not a problem
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            if ds == 1:
                xs, ys = start, block[:, :, 0]
            elif self.mode == "peak":
                xs = np.repeat(start, 2)
                ys = np.stack([np.nanmin(block, 2), np.nanmax(block, 2)], 2).reshape(rows, 2 * n)
            else:
                xs, ys = start + (ds - 1) / 2 / fs, np.nanmean(block, 2)
        # one row after another, each ended by a NaN so they aren't joined up
        xs = np.tile(np.append(xs, xs[-1]), rows)
        ys = np.column_stack([ys, np.full(rows, np.nan)]).ravel()
        pg.PlotDataItem.setData(self, xs, ys, connect="finite")


def light_grid(p: pg.PlotItem) -> None:
    """Grid lines at the major and middle ticks only. pyqtgraph also draws one at every
    minor tick, and redrawing those see-through lines was most of a live screen
    update's time (21 ms a frame at 20 kHz, 7.6 ms without them)."""
    for ax in ("bottom", "left"):
        p.getAxis(ax).setStyle(maxTickLevel=1)


def plot_widget(**kw) -> pg.PlotWidget:
    """A pg.PlotWidget with the app's wheel rules and a light grid."""
    pw = pg.PlotWidget(viewBox=WheelViewBox(), **kw)
    light_grid(pw.getPlotItem())
    return pw
