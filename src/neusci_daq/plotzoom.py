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


# Ctrl (or Cmd: Qt reports Cmd as Control on macOS, and Ctrl as Meta) + drag: box zoom.
ZOOM_MODS = QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.MetaModifier


class WheelViewBox(pg.ViewBox):
    """The app's mouse rules: the wheel zooms time (Cmd+wheel: the y axis, Shift+wheel:
    pans time); Ctrl+drag draws a box and zooms to it; Ctrl+click goes back a zoom."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._zoom_history: list = []

    def mouseDragEvent(self, ev, axis=None):
        if axis is None and ev.button() == QtCore.Qt.MouseButton.LeftButton and ev.modifiers() & ZOOM_MODS:
            ev.accept()
            if ev.isFinish():
                self.rbScaleBox.hide()
                r = QtCore.QRectF(pg.Point(ev.buttonDownPos(ev.button())), pg.Point(ev.pos())).normalized()
                r = self.childGroup.mapRectFromParent(r).normalized()
                if r.width() > 0 and r.height() > 0:
                    self.zoom_box(r.left(), r.right(), r.top(), r.bottom())
            else:
                self.updateScaleBox(ev.buttonDownPos(), ev.pos())
            return
        super().mouseDragEvent(ev, axis)

    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton and ev.modifiers() & ZOOM_MODS:
            ev.accept()
            self.zoom_back()
            return
        super().mouseClickEvent(ev)

    def zoom_box(self, x0: float, x1: float, y0: float, y1: float) -> None:
        """Show just this box (data coordinates); Ctrl+click goes back."""
        self._zoom_history.append((self.viewRange(), bool(self.state["autoRange"][1])))
        self.set_time_range(x0, x1)
        self.enableAutoRange(axis="y", enable=False)
        self.setYRange(y0, y1, padding=0)
        self.sigRangeChangedManually.emit([True, True])     # overlaid axes follow (MultiAxis)

    def zoom_back(self) -> None:
        """Undo the last box zoom; with none left, fit the y axis to the data."""
        if not self._zoom_history:
            self.enableAutoRange(axis="y")
            return
        (xr, yr), auto = self._zoom_history.pop()
        self.set_time_range(*xr)
        if auto:
            self.enableAutoRange(axis="y")
        else:
            self.setYRange(*yr, padding=0)
        self.sigRangeChangedManually.emit([True, True])

    def set_time_range(self, x0: float, x1: float) -> None:
        self.setXRange(x0, x1, padding=0)

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


class _FollowerViewBox(pg.ViewBox):
    """An extra y axis's view: it shares the plot's time axis, and leaves the mouse to
    the plot, except on its own axis, where the wheel and dragging scale just it."""

    def wheelEvent(self, ev, axis=None):
        if axis == 1:
            super().wheelEvent(ev, axis=1)
            ev.accept()
        else:
            ev.ignore()

    def mouseDragEvent(self, ev, axis=None):
        if axis == 1:
            super().mouseDragEvent(ev, axis=1)
        else:
            ev.ignore()

    def mouseClickEvent(self, ev):
        ev.ignore()


def _si(unit: str) -> bool:
    return unit.split("/")[0] in ("V", "A", "s")


class MultiAxis:
    """Overlaid traces on one plot, each with its own y axis by default.

    The first trace uses the plot's own (left) axis; each other one gets a view and an
    axis on the right, in its colour. Every trace starts fitted to the plot's height, so
    they overlay as closely as they can. The wheel (Cmd) or a drag on the plot scales
    them all together; on a trace's own axis, it scales just that trace.
    With separate=False they all share the left axis.
    """

    def __init__(self, plot: pg.PlotItem, separate: bool = True):
        self.plot = plot
        self.separate = separate
        self.views: list[pg.ViewBox] = []     # one per trace (the first is the plot's own)
        self.axes: list[pg.AxisItem] = []     # the extra (right) axes
        self.items: list = []
        self._added: list = []                # items this put in the plot (not ones already there)
        self._labels: list = []               # (axis, name, unit, colour) to restore after a restyle
        self._auto = True
        self._prev = self._last = tuple(plot.vb.viewRange()[1])
        plot.vb.sigResized.connect(self._sync_geometry)
        plot.vb.sigYRangeChanged.connect(self._main_y_changed)
        plot.vb.sigRangeChangedManually.connect(self._main_zoomed)

    def add(self, item, name: str, unit: str, color) -> pg.ViewBox:
        p = self.plot
        if not self.items or not self.separate:
            if item.getViewBox() is not p.vb:
                p.addItem(item)
                self._added.append(item)
            vb = p.vb
            if not self.items:
                self._label(p.getAxis("left"), name if self.separate else "", unit, color if self.separate else None)
        else:
            vb = _FollowerViewBox()
            vb.setZValue(p.vb.zValue() - 1)                   # under the plot, which takes the mouse
            p.scene().addItem(vb)
            vb.setXLink(p.vb)
            vb.addItem(item)
            vb.enableAutoRange(axis="y", enable=self._auto)
            ax = pg.AxisItem("right")
            ax.linkToView(vb)
            self._label(ax, name, unit, color)
            p.layout.addItem(ax, 2, 3 + len(self.axes))
            self.axes.append(ax)
            self._sync_geometry()
        self.views.append(vb)
        self.items.append(item)
        return vb

    def _extras(self) -> list[pg.ViewBox]:
        """The extra views (not the plot's own), once each."""
        return [vb for vb in self.views if vb is not self.plot.vb]

    def recolor(self) -> None:
        """Put the axes' colours and labels back (after a theme restyled the plot)."""
        for args in list(self._labels):
            self._label(*args, keep=False)

    def _label(self, ax: pg.AxisItem, name: str, unit: str, color, keep: bool = True) -> None:
        if keep:
            self._labels.append((ax, name, unit, color))
        kw = {"color": pg.mkColor(color).name()} if color is not None else {}
        if _si(unit):
            ax.setLabel(name, units=unit, **kw)
        else:
            ax.setLabel(f"{name} ({unit})" if unit else name, **kw)
        ax.enableAutoSIPrefix(_si(unit))
        if color is not None:
            ax.setPen(pg.mkPen(color))
            ax.setTextPen(pg.mkPen(color))

    def clear(self) -> None:
        p = self.plot
        for item, vb in zip(self.items, self.views):
            if vb is not p.vb:
                vb.removeItem(item)
            elif item in self._added:
                p.removeItem(item)
        for vb in self.views:
            if vb is not p.vb and vb.scene() is not None:
                vb.scene().removeItem(vb)
        for ax in self.axes:
            p.layout.removeItem(ax)
            if ax.scene() is not None:
                ax.scene().removeItem(ax)
        self.views, self.axes, self.items, self._added, self._labels = [], [], [], [], []

    def disconnect(self) -> None:
        self.clear()
        vb = self.plot.vb
        for sig, slot in ((vb.sigResized, self._sync_geometry), (vb.sigYRangeChanged, self._main_y_changed),
                          (vb.sigRangeChangedManually, self._main_zoomed)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def set_auto(self, on: bool) -> None:
        """Keep every extra trace fitted to the height as its data changes (or not)."""
        self._auto = bool(on)
        for vb in self._extras():
            vb.enableAutoRange(axis="y", enable=self._auto)

    def fit(self) -> None:
        """Fit every extra trace to the plot's height now (the plot's own trace is the
        host's: it may be auto-scaled or not)."""
        for vb in self._extras():
            vb.enableAutoRange(axis="y", enable=True)
            vb.updateAutoRange()
            vb.enableAutoRange(axis="y", enable=self._auto)

    def _sync_geometry(self, *_) -> None:
        r = self.plot.vb.sceneBoundingRect()
        for vb in self._extras():
            vb.setGeometry(r)
            vb.linkedViewChanged(self.plot.vb, vb.XAxis)

    def _main_y_changed(self, _vb, rng) -> None:
        self._prev, self._last = self._last, tuple(rng)

    def _main_zoomed(self, mask) -> None:
        """The plot was zoomed or dragged in y: do the same to every extra trace,
        relative to its own range, so they stay overlaid."""
        if len(mask) > 1 and not mask[1]:
            return
        (a0, b0), (a1, b1) = self._prev, self._last
        if b0 == a0 or (a0, b0) == (a1, b1):
            return
        for vb in self._extras():
            lo, hi = vb.viewRange()[1]
            k = (hi - lo) / (b0 - a0)
            vb.enableAutoRange(axis="y", enable=False)
            vb.setYRange(lo + (a1 - a0) * k, lo + (b1 - a0) * k, padding=0)
