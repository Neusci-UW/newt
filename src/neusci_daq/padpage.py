"""The DataPad tab: a table first, in the style of LabChart's DataPad.

Under the preview, the fx bar: the picked column's recipe, written out and editable in place
(``mean(Vm, B, last 50 ms)``, ``= dV / I_B * 1000``), what makes a row (each trial, each
segment, each pulse, or by hand), + Column, Plot and the Recipe menu.

Under it, the preview: the row's trial, stimulus over response (pyqtgraph, so it zooms
with the wheel and stays quick), the Complex Stimulus segments in a lettered strip under
it, and the picked column's window shaded, with its value in orange. It only shows: a
column's window changes from its editor, or with Set window (then click a segment, or drag).
Fold it to just the letter strip. With rows by hand, drag across it and Add to DataPad.

Then the table: a row per trial (or segment, pulse…) with a tick to leave it out, a column
per recipe; click a header to pick its column (its ▾ opens the column's editor), click a
cell to preview its row. Mean ± SEM rows of all rows, each group or each value of a column
(by I_B: the points of an I–V curve).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import datapad as D
from . import padmodel as P
from . import style
from .simplestim import DIM



# ------------------------------------------------------------------------------- the trials
class PageSource(P.Source):
    """The Analysis page's trials, as the DataPad reads them."""

    def __init__(self, page):
        self.page = page

    def trials(self) -> list[int]:
        pg_ = self.page
        seen, out = set(), []
        for _, blocks in pg_.shown:
            for i in blocks:
                if i not in seen and 0 <= i < len(pg_.blocks) and pg_.blocks[i] is not None:
                    seen.add(i)
                    out.append(i)
        out += [i for i, b in enumerate(pg_.blocks) if b is not None and i not in seen]
        return out

    def trace(self, i, channel):
        y = self.page.trace(i, channel) if channel else None
        if y is None:
            return None
        if hasattr(self.page, "_less_baseline"):  # the baseline, when it's subtracted, is off here too
            y = self.page._less_baseline(i, channel, y)
        return np.asarray(y, float), float(self.page.blocks[i]["sample_rate"])

    def unit(self, channel):
        return self.page.unit_of(channel) if channel else ""

    def segments(self, i):
        page = self.page
        if not (0 <= i < len(page.blocks)) or page.blocks[i] is None:
            return []
        ch = page.stim_channel.currentData() or ""         # each stimulus is lined up on it
        return page.main.store.memo(page._paths[i], ("pad segments", ch),
                                    lambda: P.sent_segments(page.blocks[i].get("stimuli"), page.send_onsets(i)))

    def spikes(self, i, units=None):
        """Spike times (s) the spike sorter found in trial i: of the units ticked in the Spikes
        panel (units None), all of them ([-1]) or the ones listed (0-based)."""
        ev = self.page.events
        if ev is None:
            return None
        m = ev.block == i
        if units is None:
            m &= np.isin(ev.unit, list(self.page.raw.shown_units()))
        elif units != [-1]:
            m &= np.isin(ev.unit, units)
        return ev.t[m]

    def spike_channel(self):
        return self.page.raw.channel.currentText()

    def group(self, i):
        for name, blocks in self.page.shown:
            if i in blocks:
                return name, blocks.index(i) + 1
        return "", 1

    def stim_units(self):
        cfg = self.page.main.cfg
        return cfg.stim_units, cfg.stim_scale

    def duration(self, i):
        b = self.page.blocks[i]
        return float(b["duration_s"]) if b is not None else 0.0


# ------------------------------------------------------------------------------- the preview
class _View(pg.ViewBox):
    """A left drag picks a stretch of time; the wheel and the right button zoom as usual."""

    def __init__(self, preview: "Preview"):
        super().__init__()
        self.preview = preview

    def _refresh_limits(self) -> None:
        """Zooming out or panning stops at the data's ends in time (both plots' data)."""
        from .plotzoom import WheelViewBox
        lo, hi = np.inf, -np.inf
        for p in (getattr(self.preview, "p_stim", None), getattr(self.preview, "p_resp", None)):
            b = WheelViewBox._data_limits(p.getViewBox(), 0) if p is not None else None
            if b is not None:
                lo, hi = min(lo, b[0]), max(hi, b[1])
        self.state["limits"]["xLimits"] = [lo, hi] if hi > lo else [None, None]

    def setRange(self, *a, **kw):
        self._refresh_limits()
        super().setRange(*a, **kw)

    def updateViewRange(self, forceX=False, forceY=False):
        self._refresh_limits()
        super().updateViewRange(forceX, forceY)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return super().mouseDragEvent(ev, axis)
        ev.accept()
        x0 = self.mapSceneToView(ev.buttonDownScenePos()).x()
        x1 = self.mapSceneToView(ev.scenePos()).x()
        self.preview.dragged(x0, x1, ev.isFinish())

    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton:
            self.preview.clicked_at(self.mapSceneToView(ev.scenePos()).x(),
                                    bool(ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier))
            ev.accept()
        else:
            super().mouseClickEvent(ev)


class SegStrip(QtWidgets.QWidget):
    """The segments under the traces, lettered, lined up with the plot's time axis."""

    clicked = QtCore.Signal(str, bool)

    def __init__(self, preview: "Preview"):
        super().__init__()
        self.preview = preview
        self.setFixedHeight(38)
        self.setMouseTracking(True)
        self.segs: list = []
        self.picked: set[str] = set()
        self.hover = ""

    def _x(self, t: float) -> float:
        vb = self.preview.p_resp.getViewBox()
        pt = vb.mapViewToScene(QtCore.QPointF(self.preview.x_of(t), 0))
        glw = self.preview.glw
        return glw.mapFromScene(pt).x() + glw.x() - self.x()      # glw and the strip are siblings, side by side

    def _at(self, x: float) -> str:
        return next((s[0] for s in self.segs if self._x(s[1]) <= x <= self._x(s[2])), "")

    def mouseMoveEvent(self, ev):
        h = self._at(ev.position().x())
        if h != self.hover:
            self.hover = h
            self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor if h else QtCore.Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, ev):
        self.hover = ""
        self.update()

    def mousePressEvent(self, ev):
        got = self._at(ev.position().x())
        if got:
            self.clicked.emit(got, bool(ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier))

    def paintEvent(self, ev):
        c = self.preview.colors
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        bold = QtGui.QFont(self.font())
        bold.setBold(True)
        h = self.height()
        left, right = 0, self.width()
        if not self.segs:
            p.setPen(QtGui.QColor(c["text"]))
            p.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                       "No Complex Stimulus segments in this trial: windows are stretches of time")
            p.end()
            return
        for letter, t0, t1, part in self.segs:
            x0, x1 = max(left, self._x(t0)), min(right, self._x(t1))
            if x1 <= x0:
                continue
            r = QtCore.QRectF(x0, 2, x1 - x0, h - 4)
            on = letter in self.picked
            if on or letter == self.hover:
                col = QtGui.QColor(c["sel"])
                if not on:
                    col.setAlpha(col.alpha() // 2)
                p.fillRect(r, col)
            pen = QtGui.QPen(QtGui.QColor(c["wave"] if on else c["line"]), 1)
            if not on:
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRect(r)
            p.setPen(QtGui.QColor(c["wave"] if on else c["ink"]))
            p.setFont(bold)
            text = letter
            if x1 - x0 > 110:
                text += f"  ·  {str(part.get('kind', '')).capitalize()} {(t1 - t0) * 1000:.4g} ms"
            elif x1 - x0 > 60:
                text += f" · {(t1 - t0) * 1000:.4g} ms"
            p.drawText(r, QtCore.Qt.AlignmentFlag.AlignCenter,
                       p.fontMetrics().elidedText(text, QtCore.Qt.TextElideMode.ElideRight, int(x1 - x0) - 4))
        p.end()


class Preview(QtWidgets.QWidget):
    """The row's trial, stimulus over response, with its segments and the picked window."""

    segment_clicked = QtCore.Signal(str, bool)
    point_clicked = QtCore.Signal(float)        # a click on the trace: one moment, s
    time_dragged = QtCore.Signal(float, float)
    phase_clicked = QtCore.Signal(float)        # in phase view: degrees
    phase_dragged = QtCore.Signal(float, float)
    xmode_changed = QtCore.Signal()

    XMODES = (("trial", "Trial time"), ("time", "Stimulus time"), ("phase", "Stimulus phase"))

    def __init__(self, view: "PadView"):
        super().__init__()
        self.view = view
        self.colors = {}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(6)
        self.btn_fold = QtWidgets.QToolButton(text="▾")
        self.btn_fold.setAutoRaise(True)
        self.btn_fold.setCheckable(True)
        self.btn_fold.setToolTip("Fold the preview to its segment strip, to give the table room")
        head.addWidget(self.btn_fold)
        head.addWidget(QtWidgets.QLabel("<b>Preview</b>"))
        self.info = QtWidgets.QLabel()
        self.info.setMinimumWidth(0)
        self.info.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        head.addWidget(self.info, 1)
        self.setting = QtWidgets.QLabel()
        self.setting.setObjectName("setting")
        self.setting.hide()
        head.addWidget(self.setting)
        self.btn_add = QtWidgets.QPushButton("Add to DataPad")
        self.btn_add.setToolTip("Add the stretch you dragged as a row (⌘D)")
        self.btn_add.setShortcut(QtGui.QKeySequence("Ctrl+D"))
        head.addWidget(self.btn_add)
        self.stim_ch = QtWidgets.QComboBox()
        self.stim_ch.setToolTip("The channel drawn on top: the stimulus")
        self.resp_ch = QtWidgets.QComboBox()
        self.resp_ch.setToolTip("The channel drawn below: the response")
        for w in (self.stim_ch, self.resp_ch):
            w.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            w.setMinimumContentsLength(4)
            head.addWidget(w)
        self.xmode = QtWidgets.QComboBox()
        for key, lab in self.XMODES:
            self.xmode.addItem(lab, key)
        self.xmode.setToolTip("The x axis. Trial time: the whole trial. Stimulus time: just the selection, "
                              "from its start. Stimulus phase: every cycle of the selected segment laid over "
                              "each other, 0–360° (sine, square, triangle, pulse trains)")
        head.addWidget(self.xmode)
        self.btn_all = QtWidgets.QToolButton(text="⤢")
        self.btn_all.setAutoRaise(True)
        self.btn_all.setToolTip("Show the whole trial (the wheel zooms, the right button drags)")
        head.addWidget(self.btn_all)
        lay.addLayout(head)
        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setMinimumHeight(140)
        self.p_stim = self.glw.addPlot(row=0, col=0, viewBox=_View(self))
        self.p_resp = self.glw.addPlot(row=1, col=0, viewBox=_View(self))
        self.glw.ci.layout.setRowStretchFactor(0, 1)
        self.glw.ci.layout.setRowStretchFactor(1, 3)
        self.glw.ci.layout.setSpacing(2)
        self.p_resp.setXLink(self.p_stim)
        self.p_stim.hideAxis("bottom")
        for p in (self.p_stim, self.p_resp):
            p.setClipToView(True)
            p.setDownsampling(auto=True, mode="peak")
            p.hideButtons()
            p.setMenuEnabled(False)
            p.getAxis("left").setWidth(56)
            p.getViewBox().setMouseEnabled(x=True, y=False)
        self.p_resp.getAxis("bottom").setLabel("time", "s")
        lay.addWidget(self.glw, 1)
        self.strip = SegStrip(self)
        lay.addWidget(self.strip)
        self.quick = QuickRow()
        self.quick_scroll = QtWidgets.QScrollArea()      # long rows scroll sideways; never widen the window
        self.quick_scroll.setWidget(self.quick)
        self.quick_scroll.setWidgetResizable(True)
        self.quick_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.quick_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.quick_scroll.setMinimumWidth(80)
        self.quick_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        def fit():
            self.quick.lay.activate()
            self.quick_scroll.setFixedHeight(self.quick.lay.sizeHint().height()
                                             + self.quick_scroll.horizontalScrollBar().sizeHint().height())
        self.quick.resized = lambda: QtCore.QTimer.singleShot(0, fit)
        lay.addWidget(self.quick_scroll)
        self._curves = {"stim": [], "resp": []}
        self._bands: list = []
        self._win: list = []
        self._drag = None
        self.value = pg.TextItem(anchor=(0.5, 0))
        self.value.setZValue(20)
        self.p_resp.addItem(self.value, ignoreBounds=True)
        self.p_resp.getViewBox().sigRangeChanged.connect(lambda *_: (self.strip.update(), self._place_value()))
        self.p_resp.getViewBox().sigResized.connect(lambda *_: self.strip.update())
        self.strip.clicked.connect(self.segment_clicked.emit)
        self.btn_fold.toggled.connect(self.set_folded)
        self.btn_all.clicked.connect(lambda: self.p_resp.enableAutoRange(axis="x"))
        self._wpos = None
        self.xf: tuple = ("trial",)              # how trial time maps onto the x axis (show_trial)
        self.xmode.currentIndexChanged.connect(lambda *_: self.xmode_changed.emit())
        self.spikes = pg.ScatterPlotItem(symbol="o", size=7, pen=None)    # the spikes found, a point on each
        self.spikes.setZValue(15)
        self.p_resp.addItem(self.spikes, ignoreBounds=True)
        self.apply_theme(style.LIGHT)

    def apply_theme(self, mode: str) -> None:
        t = style.tokens(mode)
        sel = QtGui.QColor(t["focus"])
        sel.setAlpha(26)
        self.colors = dict(wave=t["focus"], dim=DIM[mode], text=t["subtle"], ink=t["ink"], line=t["line"], sel=sel,
                           panel=t["panel"], axis=t["subtle"])
        self.glw.setBackground(t["panel"])
        for p in (self.p_stim, self.p_resp):
            for ax in ("left", "bottom"):
                a = p.getAxis(ax)
                a.setPen(pg.mkPen(t["line"]))
                a.setTextPen(pg.mkPen(t["subtle"]))
        self.value.setColor(QtGui.QColor(DIM[mode]))
        self.setting.setStyleSheet(f"QLabel#setting {{ background: {t['selection']}; color: {t['focus']};"
                                   f" border: 1px solid {t['focus']}; border-radius: 4px; padding: 2px 8px; }}")
        self.info.setStyleSheet(f"color: {t['subtle']}")
        self.strip.update()

    def set_folded(self, on: bool) -> None:
        self.btn_fold.setText("▸" if on else "▾")
        self.glw.setVisible(not on)
        self.view.folded_changed(on)

    # -- drawing ----------------------------------------------------------------------------
    # -- the x axis: trial time, stimulus time (from the selection's start), or phase ------------
    def x_of(self, t: float) -> float:
        """Where trial time t sits on the x axis (trial and stimulus time)."""
        return t - self.xf[1] if self.xf[0] == "time" else t

    def t_of(self, x: float) -> float:
        return x + self.xf[1] if self.xf[0] == "time" else x

    def _pieces(self, y: np.ndarray, fs: float) -> list[tuple[np.ndarray, np.ndarray, bool]]:
        """The trace as (x, y, bold) pieces for the x axis: the trial; the selection; or each
        cycle (faint) and their mean (bold)."""
        t = np.arange(len(y)) / fs
        kind = self.xf[0]
        if kind == "trial":
            return [(t, y, True)]
        if kind == "time":
            _, a, b = self.xf
            k0, k1 = max(0, int(round(a * fs))), min(len(y), int(round(b * fs)))
            return [(t[k0:k1] - a, y[k0:k1], True)]
        _, starts, period, _a, _b = self.xf
        n = max(2, int(round(period * fs)))
        out, rows = [], []
        for s0 in starts:
            k0 = int(round(s0 * fs))
            if k0 + n > len(y):
                break
            seg = y[k0:k0 + n]
            rows.append(seg)
            out.append((np.arange(n) / n * 360.0, seg, False))
        if rows:
            out.append((np.arange(n) / n * 360.0, np.nanmean(np.vstack(rows), axis=0), True))
        return out

    def show_trial(self, i: int, others: list[int]) -> None:
        """Draw trial i on the x axis chosen. Only when the trial, channels or axis change."""
        src = self.view.src
        t = self.colors
        faint = QtGui.QColor(t["wave"])
        faint.setAlpha(45)
        for key, plot, ch in (("stim", self.p_stim, self.stim_ch.currentText()),
                              ("resp", self.p_resp, self.resp_ch.currentText())):
            got = src.trace(i, ch)
            pieces = self._pieces(*got) if got is not None else []
            curves = self._curves[key]
            while len(curves) < len(pieces):
                c = pg.PlotDataItem()
                c.setDownsampling(auto=True, method="peak")
                c.setClipToView(True)
                plot.addItem(c)
                curves.append(c)
            for k, c in enumerate(curves):
                if k >= len(pieces):
                    c.setData([], [])
                    c.hide()
                    continue
                x, y, bold = pieces[k]
                c.setPen(pg.mkPen(t["wave"], width=2) if bold else pg.mkPen(faint, width=1))
                c.setZValue(10 if bold else 1)
                c.setData(x, y)
                c.show()
            plot.getAxis("left").setLabel(ch, src.unit(ch))
        phase = self.xf[0] == "phase"
        if phase:
            self.p_resp.getAxis("bottom").setLabel("phase (°)")
        else:
            self.p_resp.getAxis("bottom").setLabel("time" + (" from the selection's start" if self.xf[0] == "time"
                                                            else ""), "s")
        segs = src.segments(i)
        for p, b in self._bands:
            p.removeItem(b)
        self._bands = []
        edge = QtGui.QColor(t["line"])
        lines = (90.0, 180.0, 270.0) if phase else [self.x_of(t0) for _, t0, _t1, _p in segs[1:]]
        for x in lines:
            for p in (self.p_stim, self.p_resp):
                ln = pg.InfiniteLine(x, pen=pg.mkPen(edge, width=1, style=QtCore.Qt.PenStyle.DashLine))
                ln.setZValue(-5)
                p.addItem(ln, ignoreBounds=True)
                self._bands.append((p, ln))
        self.strip.segs = segs
        self.strip.setVisible(not phase)
        self.strip.update()
        self.p_resp.enableAutoRange(axis="x")

    def show_baseline(self, region: tuple[float, float] | None) -> None:
        """The baseline window, shaded grey with a "baseline" tag, when it's subtracted."""
        for p, r in getattr(self, "_base", []):
            p.removeItem(r)
        self._base = []
        if region is None:
            return
        col = QtGui.QColor(self.colors["text"])
        col.setAlpha(40)
        for p in (self.p_stim, self.p_resp):
            r = pg.LinearRegionItem(region, movable=False, brush=col, pen=pg.mkPen(None))
            r.setZValue(-9)
            for ln in r.lines:
                ln.setPen(pg.mkPen(self.colors["text"], width=1, style=QtCore.Qt.PenStyle.DotLine))
            p.addItem(r, ignoreBounds=True)
            self._base.append((p, r))
        tag = pg.TextItem("baseline", color=self.colors["text"], anchor=(0, 1))
        tag.setPos(region[0], self.p_resp.getViewBox().viewRange()[1][0])
        self.p_resp.addItem(tag, ignoreBounds=True)
        self._base.append((self.p_resp, tag))

    def show_spikes(self, t: np.ndarray, y: np.ndarray, brushes: list, side=None) -> None:
        """A marker at each spike the spike sorter found, in its unit's color: just above it
        (side +1, ▼) or below it (-1, ▲), the way the spikes were found."""
        if not len(t):
            self.spikes.setData([], [])
            return
        symbols = "o" if side is None else ["t" if s > 0 else "t1" for s in side]
        self.spikes.setData(t, y, brush=brushes, symbol=symbols, size=9)

    def show_window(self, letters: set[str], w: tuple[float, float] | None, value_html: str) -> None:
        """Shade the picked column's window in this row; its value in orange above it."""
        for p, r in self._win:
            p.removeItem(r)
        self._win = []
        self.strip.picked = letters
        self.strip.update()
        if w is not None and np.isfinite(w[1]):
            col = QtGui.QColor(self.colors["wave"])
            col.setAlpha(38)
            for p in (self.p_stim, self.p_resp):
                r = pg.LinearRegionItem(w, movable=False, brush=col, pen=pg.mkPen(None))
                r.setZValue(-8)
                for ln in r.lines:
                    ln.setPen(pg.mkPen(self.colors["wave"], width=1))
                p.addItem(r, ignoreBounds=True)
                self._win.append((p, r))
        self._wpos = w
        self.value.setHtml(f"<span style='font-weight:600; font-size:11pt; color:{self.colors['dim']}'>{value_html}</span>"
                           if value_html else "")
        self._place_value()

    def _place_value(self) -> None:
        vb = self.p_resp.getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()
        w = self._wpos
        x = (max(w[0], x0) + min(w[1], x1)) / 2 if w is not None and np.isfinite(w[1]) else (x0 + x1) / 2
        self.value.setPos(x, y1)

    # -- the mouse --------------------------------------------------------------------------
    def clicked_at(self, x: float, shift: bool) -> None:
        """A click on the trace is a point (in phase view, a phase); segments are picked on the
        strip under it."""
        if self.xf[0] == "phase":
            self.phase_clicked.emit(min(360.0, max(0.0, x)))
        else:
            self.point_clicked.emit(max(0.0, self.t_of(x)))

    def dragged(self, x0: float, x1: float, done: bool) -> None:
        a, b = sorted((x0, x1))
        if self._drag is None:
            col = QtGui.QColor(self.colors["dim"])
            col.setAlpha(50)
            self._drag = [pg.LinearRegionItem((a, b), movable=False, brush=col) for _ in range(2)]
            for p, r in zip((self.p_stim, self.p_resp), self._drag):
                p.addItem(r, ignoreBounds=True)
        for r in self._drag:
            r.setRegion((a, b))
        if done:
            if self.xf[0] == "phase":
                self.phase_dragged.emit(max(0.0, a), min(360.0, b))
            else:
                self.time_dragged.emit(self.t_of(a), self.t_of(b))

    def drag_region(self) -> tuple[float, float] | None:
        """The dragged stretch in trial time (none in phase view)."""
        if not self._drag or self.xf[0] == "phase":
            return None
        a, b = self._drag[0].getRegion()
        return self.t_of(a), self.t_of(b)

    def clear_drag(self) -> None:
        if self._drag:
            for p, r in zip((self.p_stim, self.p_resp), self._drag):
                p.removeItem(r)
        self._drag = None


class QuickRow(QtWidgets.QWidget):
    """Under the preview: the common measurements of what you clicked or dragged, on the trial
    shown, for every channel, straight away. Click one to make it a column (measured in
    every trial)."""

    picked = QtCore.Signal(str, str)             # a measurement's key, its channel

    KEYS = ("value", "mean", "max", "min", "p2p", "slope", "area", "spikes")
    SHORT = {"value": "Value", "mean": "Mean", "max": "Max", "min": "Min", "p2p": "Peak-to-peak", "slope": "Slope",
             "area": "Area", "spikes": "Spikes"}

    def __init__(self):
        super().__init__()
        self.lay = QtWidgets.QGridLayout(self)
        self.lay.setContentsMargins(4, 2, 4, 2)
        self.lay.setHorizontalSpacing(4)
        self.lay.setVerticalSpacing(2)
        self.where = QtWidgets.QLabel()
        self.where.setObjectName("quickWhere")
        self.lay.addWidget(self.where, 0, 0, 1, -1)
        self._rows: list = []
        self.show_values("", [])

    def show_values(self, where_html: str, channels: list[tuple[str, dict[str, str]]]) -> None:
        """channels: [(name, {key: "−54.2 mV"})], a line each; blank where: nothing picked yet."""
        for w in self._rows:
            self.lay.removeWidget(w)
            w.hide()
            w.deleteLater()
        self._rows = []
        self.where.setText(f"<b>{where_html}</b>" if where_html else
                           "Click the trace for the value there, a segment below for all of it, "
                           "or drag across a stretch")
        for r, (ch, values) in enumerate(channels, 1):     # a line per channel, from the start: nothing jumps
            lab = QtWidgets.QLabel(f"{ch}:")
            lab.setObjectName("quickWhere")
            self.lay.addWidget(lab, r, 0)
            self._rows.append(lab)
            col = 1
            for key in self.KEYS:
                text = values.get(key)
                if text is None:
                    continue
                b = QtWidgets.QToolButton()
                b.setObjectName("quickValue")
                b.setAutoRaise(True)
                b.setText(f"{self.SHORT[key]} {text}")
                b.setToolTip(f"Add a column: {D.MEASUREMENTS[key][0].lower()} of {ch} over {where_html} "
                             "in every trial")
                b.clicked.connect(lambda _=False, key=key, ch=ch: self.picked.emit(key, ch))
                self.lay.addWidget(b, r, col)
                self._rows.append(b)
                col += 1
            if col == 1:                              # nothing picked yet: a placeholder the chips' height
                b = QtWidgets.QToolButton()
                b.setObjectName("quickValue")
                b.setEnabled(False)
                b.setText("—")
                self.lay.addWidget(b, r, 1)
                self._rows.append(b)
        self.lay.setColumnStretch(len(self.KEYS) + 1, 1)
        self.resized()

    def resized(self) -> None:              # the scroll area round it fits its rows (set by Preview)
        pass


# ------------------------------------------------------------------------------- what a column measures
WHAT = {"measure": "Measure", "formula": "Formula", "python": "Python"}
MEASURE_KINDS = ("measure", "level", "duration", "info")


def measure_tree() -> list[tuple[str, list[tuple[str, str, str, str]]]]:
    """The measurements, grouped: [(group, [(kind, key, label, tip)])], for the nested menus."""
    out = [(group, [("measure", k, D.MEASUREMENTS[k][0], D.MEASUREMENTS[k][2]) for k in keys])
           for group, keys in P.GROUPS]
    out.append(("Stimulus", [("level", "", "Level of a segment", "Read from the stimulus that was sent"),
                             ("duration", "", "Length of a segment", "How long it lasts, from the stimulus")]))
    out.append(("Trial", [("info", k, lab, "") for k, (lab, _) in P.INFO.items()]))
    return out


def measure_label(c: P.Column) -> str:
    """Where a column sits in the tree: "Amplitude › Mean"."""
    for group, items in measure_tree():
        for kind, key, label, _ in items:
            if kind == c.kind and (key == c.key or kind in ("level", "duration")):
                return f"{group} › {label}"
    return "Choose…"


def measure_menu(parent: QtWidgets.QWidget, pick) -> QtWidgets.QMenu:
    """A menu with a submenu for each group; pick(kind, key) gets the one chosen."""
    menu = QtWidgets.QMenu(parent)
    menu.setToolTipsVisible(True)
    for group, items in measure_tree():
        sub = menu.addMenu(group)
        sub.setToolTipsVisible(True)
        for kind, key, label, tip in items:
            a = sub.addAction(label, lambda kind=kind, key=key: pick(kind, key))
            a.setToolTip(tip)
    return menu


# ------------------------------------------------------------------------------- the column editor
class ColumnEditor(QtWidgets.QDialog):
    """A column's settings, in a small window of their own beside the table (move it by its
    title bar; it stays on screen and scrolls when long). Changes apply as you make them."""

    def __init__(self, view: "PadView", k: int):
        super().__init__(view.window(), QtCore.Qt.WindowType.Tool)
        self.view, self.k = view, k
        self.setObjectName("colEditor")
        self.setWindowTitle("Column settings")
        self.setMinimumWidth(340)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QtWidgets.QWidget()
        self.scroll.setWidget(inner)
        outer.addWidget(self.scroll)
        self.lay = QtWidgets.QVBoxLayout(inner)
        self.lay.setContentsMargins(12, 10, 12, 12)
        self.lay.setSpacing(6)
        self._timer = QtCore.QTimer(self, singleShot=True, interval=300)
        self._timer.timeout.connect(self.view.column_edited)
        self.build()

    @property
    def c(self) -> P.Column:
        return self.view.pad.columns[self.k]

    def place(self, at: QtCore.QPoint | None = None) -> None:
        """Size to the settings (scrolling past most of the screen's height) and keep the whole
        window, title bar included, on the screen the DataPad is on."""
        win = self.view.window()
        screen = win.screen() or QtGui.QGuiApplication.primaryScreen()
        room = screen.availableGeometry()
        inner = self.scroll.widget()
        inner.adjustSize()
        bar = 30                                          # the title bar, roughly, before it's shown
        w = max(self.minimumWidth(), inner.sizeHint().width() + 24)
        h = min(inner.sizeHint().height() + 4, int(room.height() * 0.8))
        self.resize(w, h)
        at = at if at is not None else self.pos()
        x = max(room.left() + 8, min(at.x(), room.right() - w - 8))
        y = max(room.top() + bar, min(at.y(), room.bottom() - h - 8))
        self.move(x, y)

    def build(self) -> None:
        _clear(self.lay)
        c = self.c
        head = QtWidgets.QHBoxLayout()
        head.addWidget(QtWidgets.QLabel(f"<b>Column {self.k + 1}</b>"), 1)
        for text, tip, fn in (("◀", "Move left", lambda: self._move(-1)), ("▶", "Move right", lambda: self._move(1)),
                              ("Remove", "Take this column out", self._remove)):
            b = QtWidgets.QToolButton(text=text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            head.addWidget(b)
        self.lay.addLayout(head)
        f = QtWidgets.QFormLayout()
        f.setHorizontalSpacing(8)
        title = QtWidgets.QLineEdit(c.title)
        title.setPlaceholderText(c.name)
        title.setToolTip("The name shown on the table and figures; _ makes a subscript (V_rest)")
        name = QtWidgets.QLineEdit(c.name)
        name.setToolTip("What formulas call it: letters, digits and _")
        name.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        unit = QtWidgets.QLineEdit(c.unit)
        unit.setPlaceholderText("automatic")
        kind = QtWidgets.QComboBox()
        for key, lab in WHAT.items():
            kind.addItem(lab, key)
        kind.setCurrentIndex(max(0, kind.findData("measure" if c.kind in MEASURE_KINDS else c.kind)))
        f.addRow("Name", title)
        f.addRow("In formulas", name)
        f.addRow("Unit", unit)
        f.addRow("What", kind)
        self.lay.addLayout(f)
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #c62828")
        def titled():
            t = title.text().strip()
            if t != c.title:                      # typed: yours from now on (cleared: automatic again)
                self._set(title=t, title_auto=not t, rebuild=not t)
        title.editingFinished.connect(titled)
        unit.editingFinished.connect(lambda: self._set(unit=unit.text().strip()))

        def renamed():
            new = name.text().strip()
            if new == c.name:
                return
            why = P.check_name(new, [n for n in self.view.pad.names() if n != c.name])
            if why:
                self.error.setText(why)
                name.setText(c.name)
                return
            self.view.rename(c, new)
        name.editingFinished.connect(renamed)
        kind.currentIndexChanged.connect(lambda *_: self._kind(kind.currentData()))
        body = QtWidgets.QFormLayout()
        if c.kind in MEASURE_KINDS:                    # what it measures: a menu of groups that open
            fn = QtWidgets.QPushButton(measure_label(c))
            fn.setToolTip("What to measure: open a group to see its measurements")
            fn.setStyleSheet("text-align: left; padding: 4px 8px")
            fn.setMenu(measure_menu(fn, self._measure_chosen))
            body.addRow("Measure", fn)
        if c.kind == "measure":
            ch = QtWidgets.QComboBox()
            ch.addItems(self.view.page.names())
            ch.setCurrentIndex(max(0, ch.findText(c.channel)))
            ch.currentIndexChanged.connect(lambda *_: self._set(channel=ch.currentText()))
            if c.key in D.SPIKE_KEYS:                 # what the spike sorter found, on its channel
                who = QtWidgets.QComboBox()
                who.addItem("Units ticked in Spikes", None)
                who.addItem("All units", [-1])
                ev = self.view.page.events
                for u in range(ev.n_units if ev is not None else 0):
                    who.addItem(f"Unit {u + 1}", [u])
                who.setCurrentIndex(max(0, who.findData(c.units)))
                who.setToolTip("Which spikes: the units ticked in the Spikes panel (they follow your ticks), "
                               "every unit, or one")
                who.currentIndexChanged.connect(lambda *_: self._set(units=who.currentData()))
                body.addRow("Of", QtWidgets.QLabel(f"spikes found on <b>{self.view.src.spike_channel()}</b>"))
                body.addRow("Units", who)
                if ev is None:
                    body.addRow("", QtWidgets.QLabel("No spikes found yet: find them in the Spikes panel"))
            else:
                body.addRow("Of", ch)
            over = QtWidgets.QComboBox()
            over.addItem("Its own window", "window")
            over.addItem("The row (whole trial, segment, pulse…)", "row")
            over.setCurrentIndex(max(0, over.findData(c.over)))
            over.setToolTip("Measure the stretch the row stands for, or a window of its own in the row's trial")
            over.currentIndexChanged.connect(lambda *_: self._set(over=over.currentData(), rebuild=True))
            body.addRow("Over", over)
            if c.over == "window":
                self._window_rows(body, with_part=True)
        elif c.kind == "level":
            self._window_rows(body, with_part=False, single=True)
        elif c.kind == "duration":
            self._window_rows(body, with_part=True)
        elif c.kind == "formula":
            e = QtWidgets.QLineEdit(c.expr)
            e.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
            e.setPlaceholderText("dV / I_B * 1000")
            e.textChanged.connect(lambda t: (setattr(c, "expr", t.strip()), self._timer.start()))
            body.addRow("=", e)
            body.addRow("", self._inserts(e))
        elif c.kind == "python":
            code = QtWidgets.QPlainTextEdit(c.expr)
            code.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
            code.setMinimumSize(360, 170)
            code.setPlaceholderText("# runs for each row: row.<name> is a column's value in it;\n"
                                    "# trace(channel) is the row's stretch, trace(channel, seg(\"B\").last(50))\n"
                                    "# a window of its own (samples, with .t in ms); spikes() their times.\n"
                                    "v = trace(\"Vm\", seg(\"B\").first(80))\nreturn fit_exp(v).tau")
            scope = QtWidgets.QComboBox()
            scope.addItem("For each row", "row")
            scope.addItem("Once, whole column (col.<name>)", "column")
            scope.setCurrentIndex(max(0, scope.findData(c.scope)))
            code.textChanged.connect(lambda: (setattr(c, "expr", code.toPlainText()), self._timer.start()))
            scope.currentIndexChanged.connect(lambda *_: self._set(scope=scope.currentData()))
            body.addRow("Runs", scope)
            body.addRow(code)
            body.addRow("", self._inserts(code))
        if c.kind != "info" or c.key != "group":        # how its numbers show
            fmt_row = QtWidgets.QWidget()
            fl = QtWidgets.QHBoxLayout(fmt_row)
            fl.setContentsMargins(0, 0, 0, 0)
            digits = QtWidgets.QSpinBox(minimum=0 if c.places else 1, maximum=12, value=c.digits)
            digits.setKeyboardTracking(False)
            how = QtWidgets.QComboBox()
            how.addItem("significant figures", False)
            how.addItem("decimal places", True)
            how.setCurrentIndex(1 if c.places else 0)
            how.setToolTip("How the column's numbers show in the table, its summary rows and the CSV. "
                           "The numbers themselves keep full precision")
            digits.valueChanged.connect(lambda v: self._set(digits=int(v)))
            how.currentIndexChanged.connect(lambda *_: self._set(places=bool(how.currentData()), rebuild=True))
            fl.addWidget(digits)
            fl.addWidget(how, 1)
            body.addRow("Format", fmt_row)
        self.lay.addLayout(body)
        self.lay.addWidget(self.error)
        err = self.view.tab.errors.get(c.name)
        if err:
            self.error.setText(err)
        if self.isVisible():                      # grew or shrank: fit again, where you put it
            QtCore.QTimer.singleShot(0, lambda: self.place(self.pos()))

    def _inserts(self, target) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(3)
        put = (lambda s: target.insert(s)) if isinstance(target, QtWidgets.QLineEdit) else \
            (lambda s: target.insertPlainText(s))
        for j, n in enumerate(self.view.pad.names()[:self.k]):
            b = QtWidgets.QToolButton(text=n)
            b.setToolTip(f"Put {n} in")
            b.clicked.connect(lambda _=False, n=n: (put(n), target.setFocus()))
            g.addWidget(b, j // 4, j % 4)
        fn = QtWidgets.QToolButton(text="Functions ▾")
        fn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QtWidgets.QMenu(fn)
        for key, tip in P.FUNC_HELP.items():
            m.addAction(tip, lambda key=key: (put(key + "("), target.setFocus()))
        fn.setMenu(m)
        g.addWidget(fn, 99, 0, 1, 4)
        return w

    def _window_rows(self, body: QtWidgets.QFormLayout, with_part: bool, single: bool = False) -> None:
        c = self.c
        segs = self.view.src.segments(self.view.preview_trial())
        many = any(P.send_of(s[0])[0] > 1 for s in segs)
        order = [s[0] for s in segs if P.send_of(s[0])[0] == 1]    # B: each row's own stimulus's B
        picked = set(c.sel.letters(order))
        w = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(3)
        for n, letter in enumerate(order):
            b = QtWidgets.QPushButton(letter)
            b.setCheckable(True)
            b.setChecked(letter in picked)
            b.setMinimumWidth(34)
            b.setToolTip(letter + (" of the stimulus each row is in" if many else "")
                         + ("" if single else "; shift-click for a run of segments"))
            b.clicked.connect(lambda _=False, L=letter: self._segment(L, single))
            g.addWidget(b, n // 6, n % 6)
        if c.sel.is_time():
            g.addWidget(QtWidgets.QLabel(f"<b>{c.sel.label()}</b> (a stretch of time)"), 9, 0, 1, 6)
        elif not order:
            g.addWidget(QtWidgets.QLabel("This trial has no segments: use Set window and drag"), 9, 0, 1, 6)
        elif not picked:                          # a later stimulus's segment, clicked on the strip (3B)
            g.addWidget(QtWidgets.QLabel(f"<b>{c.sel.label()}</b> (in every row)"), 9, 0, 1, 6)
        body.addRow("Segments", w)
        if with_part and not c.sel.is_time():
            part = QtWidgets.QWidget()
            pl = QtWidgets.QHBoxLayout(part)
            pl.setContentsMargins(0, 0, 0, 0)
            mode = QtWidgets.QComboBox()
            for key, text in (("all", "Whole"), ("first", "First"), ("last", "Last"), ("range", "Start +")):
                mode.addItem(text, key)
            mode.setCurrentIndex(max(0, mode.findData(c.sel.mode)))
            a = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0, maximum=1e6, value=c.sel.a_ms, suffix=" ms")
            to = QtWidgets.QLabel("to +")
            b = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0, maximum=1e6, value=c.sel.b_ms, suffix=" ms")
            for x in (a, b):
                x.setKeyboardTracking(False)

            def show():
                m = mode.currentData()
                a.setVisible(m != "all")
                to.setVisible(m == "range")
                b.setVisible(m == "range")

            def edited(*_):
                show()
                s = P.Selection.from_dict(c.sel.to_dict())
                s.mode, s.a_ms, s.b_ms = mode.currentData(), a.value(), b.value()
                self._set(sel=s)
            mode.currentIndexChanged.connect(edited)
            a.valueChanged.connect(edited)
            b.valueChanged.connect(edited)
            for x in (mode, a, to, b):
                pl.addWidget(x)
            pl.addStretch(1)
            show()
            body.addRow("Part", part)
        if not single:
            go = QtWidgets.QPushButton("Set window on the preview…")
            go.setToolTip("Then click a segment (shift-click for a run) or drag a stretch of time on the preview")
            go.clicked.connect(lambda: (self.close(), self.view.start_setting(self.c)))
            body.addRow("", go)

    def _measure_chosen(self, kind: str, key: str) -> None:
        """Picked from the Measure menu: a measurement, a stimulus value or trial info."""
        c = self.c
        if kind != c.kind:
            if kind == "measure" and key:
                c.key = key
            if kind == "info":
                c.key = key or "trial"
            self._kind(kind)
            if kind != "measure":
                return
        if kind == "info":
            self._set(key=key, rebuild=True)
            return
        self._measure_picked(key)

    def _measure_picked(self, key: str) -> None:
        c = self.c
        was_spiky = c.key in D.SPIKE_KEYS
        c.key = key
        if (key in D.SPIKE_KEYS) != was_spiky and not c.title and re.fullmatch(r"(V|y|n|rate)\d*", c.name):
            base = ("rate" if key == "rate" else "n") if key in D.SPIKE_KEYS else "V"
            new = P.free_name(base, [n for n in self.view.pad.names() if n != c.name])
            self.view.rename(c, new)               # a default name follows what it measures
        else:
            self.view.column_edited()
        self.build()

    def _segment(self, letter: str, single: bool) -> None:
        shift = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
        self.view.set_segment(self.c, letter, shift and not single)
        self.build()

    def _set(self, rebuild: bool = False, **changes) -> None:
        for k, v in changes.items():
            setattr(self.c, k, v)
        self.view.column_edited()
        if rebuild:
            self.build()

    def _kind(self, kind: str) -> None:
        c = self.c
        c.kind = kind
        if kind == "measure" and not c.channel:
            c.channel = self.view.preview.resp_ch.currentText()
        if kind in ("measure", "level", "duration") and c.sel.is_time() and c.sel.t1 <= c.sel.t0:
            segs = self.view.src.segments(self.view.preview_trial())
            c.sel = P.Selection.seg(segs[1][0] if len(segs) > 1 else (segs[0][0] if segs else "A"))
        if kind == "info" and c.key not in P.INFO:
            c.key = "trial"
        if kind == "measure" and c.key not in D.MEASUREMENTS:
            c.key = "mean"
        self.view.column_edited()
        self.build()

    def _move(self, d: int) -> None:
        if self.view.move_column(self.k, d):
            self.k += d
            self.build()

    def _remove(self) -> None:
        self.close()
        self.view.remove_column(self.k)


def _clear(lay) -> None:
    while lay.count():
        it = lay.takeAt(0)
        if it.widget() is not None:
            it.widget().hide()
            it.widget().deleteLater()
        elif it.layout() is not None:
            _clear(it.layout())


# ------------------------------------------------------------------------------- the table
def _name_action(menu: QtWidgets.QMenu, html: str, fn, tip: str = "") -> None:
    """A menu item whose name has true subscripts (I<sub>B</sub>), which a QAction's text can't."""
    lab = QtWidgets.QLabel(html)
    lab.setTextFormat(QtCore.Qt.TextFormat.RichText)
    lab.setContentsMargins(20, 3, 20, 3)
    lab.setStyleSheet("QLabel:hover { background: palette(highlight); color: palette(highlighted-text); }")
    lab.setToolTip(tip)
    act = QtWidgets.QWidgetAction(menu)
    act.setDefaultWidget(lab)

    def released(_ev) -> None:
        top = menu
        while isinstance(top.parent(), QtWidgets.QMenu):
            top = top.parent()
        top.close()
        QtCore.QTimer.singleShot(0, fn)

    lab.mouseReleaseEvent = released
    menu.addAction(act)


class _Header(QtWidgets.QHeaderView):
    """Names with true subscripts and units; the picked column tinted (and a ⌘-clicked one, the
    X of a scatter); ▾ opens its editor; right-click for its menu."""

    menu_clicked = QtCore.Signal(int)
    plus_clicked = QtCore.Signal(QtCore.QPoint)   # the last column, "+": add a column
    context_clicked = QtCore.Signal(int, QtCore.QPoint)   # right-click on a column

    def __init__(self, view: "PadView"):
        super().__init__(QtCore.Qt.Orientation.Horizontal)
        self.view = view
        self.setSectionsClickable(True)
        self.setMinimumHeight(28)

    def _doc(self, idx) -> QtGui.QTextDocument:
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(self.font())
        doc.setDocumentMargin(0)
        doc.setHtml(self.view.header_html(idx))
        return doc

    def _is_plus(self, idx: int) -> bool:
        return idx == self.view.first_col + len(self.view.pad.columns)

    def sectionSizeFromContents(self, idx):
        if self._is_plus(idx):
            return QtCore.QSize(34, 28)
        return QtCore.QSize(int(self._doc(idx).idealWidth()) + (30 if idx >= self.view.first_col else 12), 28)

    def mousePressEvent(self, ev):
        idx = self.logicalIndexAt(ev.position().toPoint())
        right = self.sectionViewportPosition(idx) + self.sectionSize(idx)
        if self._is_plus(idx):
            self.plus_clicked.emit(self.mapToGlobal(QtCore.QPoint(self.sectionViewportPosition(idx), self.height())))
            return
        if idx >= self.view.first_col and ev.position().x() > right - 20:
            self.menu_clicked.emit(idx)
            return
        super().mousePressEvent(ev)

    def contextMenuEvent(self, ev):
        idx = self.logicalIndexAt(ev.pos())
        if self.view.first_col <= idx < self.view.first_col + len(self.view.pad.columns):
            self.context_clicked.emit(idx, ev.globalPos())

    def paintSection(self, painter, rect, idx):
        painter.save()
        super().paintSection(painter, rect, -1)
        painter.restore()
        k = idx - self.view.first_col
        tok = style.tokens(self.view.page._mode)
        if self._is_plus(idx):                    # "+": add a column
            painter.save()
            f = QtGui.QFont(self.font())
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() * 1.3)
            painter.setFont(f)
            painter.setPen(QtGui.QColor(tok["focus"]))
            painter.drawText(QtCore.QRectF(rect), QtCore.Qt.AlignmentFlag.AlignCenter, "+")
            painter.restore()
            return
        painter.save()
        if k >= 0 and (k == self.view.picked or self.view.pad.columns[k].name == self.view.scatter_x):
            col = QtGui.QColor(tok["focus"])
            col.setAlpha(34)
            painter.fillRect(rect, col)
        doc = self._doc(idx)
        dw = doc.idealWidth()
        x = rect.right() - dw - 22 if k >= 0 else rect.left() + 6
        painter.translate(x, rect.top() + (rect.height() - doc.size().height()) / 2)
        doc.drawContents(painter)
        painter.restore()
        if k >= 0:
            painter.save()
            painter.setPen(QtGui.QColor(tok["subtle"]))
            painter.drawText(QtCore.QRectF(rect.right() - 18, rect.top(), 14, rect.height()),
                             QtCore.Qt.AlignmentFlag.AlignCenter, "▾")
            painter.restore()


class PadView(QtWidgets.QWidget):
    """The DataPad tab's middle (see the module's notes)."""

    columns_changed = QtCore.Signal()
    table_changed = QtCore.Signal()
    went_stale = QtCore.Signal()            # its trials or groups changed; it works the table out when next shown

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.src = PageSource(page)
        self.pad = P.Pad()
        self.tab = P.Table([])
        self.picked = -1
        self.scatter_x = ""                     # a column ⌘-clicked before the picked one: Scatter's X
        self.cur_row = -1                       # the table row the preview shows
        self.setting = ""                       # a column whose window is being set on the preview
        self.quick_sel: P.Selection | None = None   # what the quick measure row measures
        self.view_sel: P.Selection | None = None    # what stimulus time and phase show: segments, or a stretch
        self._stale = True
        self._shown = None                      # what the preview last drew
        self.first_col = 3                      # ☑, Trial, Row, then the columns
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(6)

        # -- the fx bar ------------------------------------------------------------------
        bar = QtWidgets.QFrame()
        bar.setObjectName("fxBar")
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(6, 3, 6, 3)
        bl.setSpacing(6)
        self.fx_name = QtWidgets.QLabel()
        self.fx_name.setMinimumWidth(40)
        fx = QtWidgets.QLabel("<i><b>fx</b></i>")
        fx.setObjectName("fx")
        self.fx = QtWidgets.QLineEdit()
        self.fx.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.fx.setPlaceholderText("Pick a column, or + Column")
        self.fx.setToolTip("The picked column, written out; change it and press Enter.\n"
                           "mean(Vm, B)  ·  max(Vm, B, first 20 ms)  ·  slope(Vm, 100-200 ms)  ·  mean(Vm, row)\n"
                           "by name too: mean(channel=Vm, window=B-D, part=last 50 ms)  ·  spikes(window=B, units=2)\n"
                           "level(B)  ·  length(B)  ·  = dV / I_B * 1000")
        self.fx.setMinimumWidth(120)
        self.fx_error = QtWidgets.QLabel()
        self.fx_error.setObjectName("fxError")
        self.fx_error.setMinimumWidth(0)
        self.fx_error.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        bl.addWidget(self.fx_name)
        bl.addWidget(fx)
        bl.addWidget(self.fx, 3)
        bl.addWidget(self.fx_error, 1)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(bar, 1)
        top.addWidget(QtWidgets.QLabel("Rows"))
        self.rows = QtWidgets.QComboBox()
        for key, lab in P.ROWS.items():
            self.rows.addItem(lab, key)
        self.rows.setToolTip("What makes a row: each trial; each segment of each trial (or just one letter); "
                             "each pulse of a train; or stretches you add by hand (drag on the preview, then "
                             "Add to DataPad)")
        self.rows_seg = QtWidgets.QComboBox()
        self.rows_seg.setToolTip("Which segment's rows: every one, or one letter")
        top.addWidget(self.rows)
        top.addWidget(self.rows_seg)
        self.btn_col = QtWidgets.QPushButton("+ Column")
        self.btn_col.setObjectName("addColumn")
        menu = QtWidgets.QMenu(self.btn_col)
        meas = measure_menu(menu, lambda kind, key: self.add_column(kind, key))
        meas.setTitle("Measure")
        menu.addMenu(meas)
        menu.addAction("Formula", lambda: self.add_column("formula"))
        menu.addAction("Python", lambda: self.add_column("python"))
        self.btn_col.setMenu(menu)
        self.btn_plot = QtWidgets.QPushButton("Plot…")
        self.btn_plot.setToolTip("Plot columns in a live chart: Y against X (Scatter) or by group (Bars)")
        self.btn_scatter = QtWidgets.QPushButton("Scatter")
        self.btn_scatter.hide()                 # shown once two columns are picked (click, then ⌘-click)
        self.btn_recipe = QtWidgets.QToolButton(text="Recipe ▾")
        self.btn_recipe.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        rm = QtWidgets.QMenu(self.btn_recipe)
        for text, fn in (("Save recipe…", self.save_recipe), ("Apply recipe…", self.apply_recipe), (None, None),
                         ("Copy table", self.copy_table), ("Export CSV…", self.export_csv), (None, None),
                         ("Clear rows added by hand", self.clear_manual), ("Clear columns", self.clear_columns)):
            rm.addSeparator() if text is None else rm.addAction(text, fn)
        self.btn_recipe.setMenu(rm)
        for w in (self.btn_col, self.btn_plot, self.btn_scatter, self.btn_recipe):
            top.addWidget(w)

        # -- preview, quick charts, table --------------------------------------------------
        self.split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.split.setChildrenCollapsible(False)
        self.preview = Preview(self)
        self.split.addWidget(self.preview)
        self.quick = QtWidgets.QWidget()
        self.quick_lay = QtWidgets.QVBoxLayout(self.quick)
        self.quick_lay.setContentsMargins(0, 0, 0, 0)
        self.split.addWidget(self.quick)
        bottom = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(bottom)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(3)
        tv.addSpacing(4)
        tv.addLayout(top)                         # the fx bar sits under the preview, over the table
        trow = QtWidgets.QHBoxLayout()
        trow.addWidget(QtWidgets.QLabel("<b>Table</b>"))
        self.table_hint = QtWidgets.QLabel("click a header to pick its column, then the trace (a point), a segment "
                                           "or a drag above to measure there · its ▾ changes it · untick to leave a trial out")
        self.table_hint.setMinimumWidth(0)
        self.table_hint.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        trow.addWidget(self.table_hint, 1)
        trow.addWidget(QtWidgets.QLabel("Mean ± SEM by"))
        self.summary = QtWidgets.QComboBox()
        self.summary.setToolTip("Rows under the table: each column's mean ± SEM (n in the tooltip), of all rows, "
                                "each group, or each value of a column (by I_B: an I–V curve's points)")
        trow.addWidget(self.summary)
        tv.addLayout(trow)
        self.table = QtWidgets.QTableWidget()
        self.header = _Header(self)
        self.table.setHorizontalHeader(self.header)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.table.setWordWrap(False)
        tv.addWidget(self.table, 1)
        self.split.addWidget(bottom)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 2)
        self.split.setStretchFactor(2, 3)
        outer.addWidget(self.split, 1)

        self.fx.returnPressed.connect(self._fx_entered)
        self.rows.currentIndexChanged.connect(self._rows_picked)
        self.rows_seg.currentIndexChanged.connect(self._rows_picked)
        self.btn_plot.clicked.connect(self.plot_dialog)
        self.btn_scatter.clicked.connect(lambda: self.scatter(*self._scatter_pair()) if self._scatter_pair() else None)
        self.summary.currentIndexChanged.connect(lambda *_: self.fill_table())
        self.header.sectionClicked.connect(self._header_clicked)
        self.header.sectionDoubleClicked.connect(lambda i: self.edit_column(i - self.first_col)
                                                 if i >= self.first_col else None)
        self.header.menu_clicked.connect(lambda i: self.edit_column(i - self.first_col))
        self.header.plus_clicked.connect(lambda at: self.btn_col.menu().exec(at))
        self.header.context_clicked.connect(self._header_menu)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.itemChanged.connect(self._ticked)
        self.preview.segment_clicked.connect(self._segment_clicked)
        self.preview.point_clicked.connect(self._point_clicked)
        self.preview.phase_clicked.connect(self._phase_clicked)
        self.preview.phase_dragged.connect(self._phase_dragged)
        self.preview.xmode.setCurrentIndex(max(0, self.preview.xmode.findData(
            self.page.main.settings.value("analysis/pad_xmode", "trial"))))
        self.preview.xmode_changed.connect(self._xmode_picked)
        self.preview.time_dragged.connect(self._time_dragged)
        self.preview.btn_add.clicked.connect(self.add_manual_row)
        self.preview.quick.picked.connect(self.add_quick_column)
        for w in (self.preview.stim_ch, self.preview.resp_ch):
            w.currentIndexChanged.connect(lambda *_: self.draw_preview(force=True))
        esc = QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Escape), self)
        esc.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)   # anywhere in the DataPad
        esc.activated.connect(self.cancel)
        for w in (self.preview.glw, self.preview.glw.viewport(), self.preview.strip, self.table,
                  self.table.viewport(), self.header):
            w.installEventFilter(self)            # Esc where the shortcut doesn't reach (the plots take keys)
        self._timer = QtCore.QTimer(self, singleShot=True, interval=40)
        self._timer.timeout.connect(self._recompute)
        self._mode = style.LIGHT
        self.apply_theme(style.LIGHT)
        self._fill_rows_controls()
        self._fill_summary_choices()
        if self.page.main.settings.value("analysis/pad_folded", "false") == "true":
            self.preview.btn_fold.setChecked(True)

    # -- looks ------------------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        t = style.tokens(mode)
        self.setStyleSheet(f"QFrame#fxBar {{ border: 1px solid {t['field_border']}; border-radius: 6px;"
                           f" background: {t['field']}; }}"
                           f"QFrame#fxBar QLineEdit {{ border: 0; background: transparent; }}"
                           f"QLabel#fx {{ color: {t['focus']}; }}"
                           f"QLabel#fxError {{ color: {t['error']}; }}"
                           f"QPushButton#addColumn {{ color: {t['focus']}; font-weight: 600; border: 1px solid"
                           f" {t['focus']}; background: {t['selection']}; padding: 4px 12px; }}"
                           f"QToolButton#quickValue {{ border: 1px solid {t['field_border']}; border-radius: 10px;"
                           f" padding: 2px 9px; color: {t['ink']}; }}"
                           f"QToolButton#quickValue:hover {{ border-color: {t['focus']}; color: {t['focus']};"
                           f" background: {t['selection']}; }}"
                           f"QLabel#quickWhere {{ color: {t['subtle']}; }}"
                           f"QFrame#colEditor {{ background: {t['panel']}; border: 1px solid {t['field_border']};"
                           f" border-radius: 8px; }}")
        self.table_hint.setStyleSheet(f"color: {t['subtle']}")
        self.preview.apply_theme(mode)
        self._shown = None
        self.header.viewport().update()

    def folded_changed(self, on: bool) -> None:
        sizes = self.split.sizes()
        if on and sum(sizes):
            want = self.preview.sizeHint().height()
            self.split.setSizes([want, sizes[1], max(120, sum(sizes) - want - sizes[1])])
        self.page.main.settings.setValue("analysis/pad_folded", "true" if on else "false")

    # -- the quick charts -------------------------------------------------------------------
    def hold_quick(self, toolbar: QtWidgets.QWidget, canvas: QtWidgets.QWidget) -> None:
        self.quick_lay.addWidget(toolbar)
        self.quick_lay.addWidget(canvas, 1)

    def set_quick(self, on: bool) -> None:
        self.quick.setVisible(on)

    # -- keeping up -------------------------------------------------------------------------
    def excluded(self) -> set[int]:
        return self.page.hidden_blocks()

    def resp_channel(self) -> str:
        return self.preview.resp_ch.currentText()

    def trials_changed(self) -> None:
        names = self.page.names()
        stim = self.page.stim_channel.currentData() or ""
        for box, want in ((self.preview.stim_ch, stim or (names[0] if names else "")),
                          (self.preview.resp_ch, next((n for n in names if n != stim), names[0] if names else ""))):
            keep = box.currentText()
            box.blockSignals(True)
            box.clear()
            box.addItems(names)
            box.setCurrentIndex(max(0, box.findText(keep if keep in names else want)))
            box.blockSignals(False)
        self._fill_rows_controls()
        self.data_changed()

    def data_changed(self) -> None:
        """The recordings, gains, fits or spikes changed: measure again."""
        self.pad.data_changed()
        self._shown = None
        self.refresh()

    def current_changed(self) -> None:
        i = self.page.index()
        if not (0 <= self.cur_row < len(self.tab.rows)) or self.tab.rows[self.cur_row].trial != i:
            self.cur_row = next((k for k, r in enumerate(self.tab.rows) if r.trial == i), -1)
        if self.isVisible():
            self.draw_preview()
            self._mark_row()

    def refresh(self) -> None:
        self._stale = True
        self.went_stale.emit()
        if self.isVisible():
            self._timer.start()

    def fresh(self) -> None:
        """Work the table out now if it's waiting to be (the DataPad hidden, a chart of it showing)."""
        if self._stale:
            self._timer.stop()
            self._recompute()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._stale:
            self._timer.start()

    def _retitle(self) -> None:
        """Columns not named by hand take a name from what they measure; two the same get
        where they measure added (Mean Voltage (B))."""
        used: dict[str, int] = {}
        for c in self.pad.columns:
            if not c.title_auto:
                continue
            ch = self.src.spike_channel() if c.kind == "measure" and c.key in D.SPIKE_KEYS else c.channel
            t = P.auto_title(c, self.src.unit(ch) if ch else "")
            if t and c.kind in ("measure", "duration", "level") and not (c.kind == "measure" and c.over == "row") \
                    and not c.sel.is_point():
                same = [o for o in self.pad.columns if o is not c and o.title_auto and o.kind == c.kind
                        and P.auto_title(o, self.src.unit(o.channel) if o.channel else "") == t]
                if same:
                    t += f" ({c.sel.label()})"
            if t in used:
                used[t] += 1
                t += f" {used[t]}"
            else:
                used[t] = 1
            c.title = t

    def _recompute(self) -> None:
        self._stale = False
        self._retitle()
        self.tab = self.pad.compute(self.src, skip=self.excluded())
        i = self.page.index()
        if not (0 <= self.cur_row < len(self.tab.rows)) or self.tab.rows[self.cur_row].trial != i:
            self.cur_row = next((k for k, r in enumerate(self.tab.rows) if r.trial == i), 0 if self.tab.rows else -1)
        self._fill_summary_choices()
        self.fill_table()
        self.draw_preview()
        self._show_fx()
        self.table_changed.emit()

    # -- the preview ------------------------------------------------------------------------
    def current(self) -> P.Column | None:
        return self.pad.columns[self.picked] if 0 <= self.picked < len(self.pad.columns) else None

    def preview_trial(self) -> int:
        if 0 <= self.cur_row < len(self.tab.rows):
            return self.tab.rows[self.cur_row].trial
        return self.page.index()

    def draw_preview(self, force: bool = False) -> None:
        i = self.preview_trial()
        if not (0 <= i < len(self.page.blocks)) or self.page.blocks[i] is None:
            return
        others: list[int] = []                         # just the row's trial
        xf = self._xform(i)
        key = (i, self.preview.stim_ch.currentText(), self.preview.resp_ch.currentText(), repr(xf))
        if force or key != self._shown:                # the traces only when they change: picking stays quick
            self._shown = key
            self.preview.xf = xf
            self.preview.show_trial(i, others)
            self._preview_spikes(i)
        self._preview_window()

    def _xmode_picked(self) -> None:
        self.page.main.settings.setValue("analysis/pad_xmode", self.preview.xmode.currentData())
        self.draw_preview(force=True)

    def _view_base(self) -> P.Selection | None:
        """What stimulus time and phase show: the segments (or stretch) last clicked or dragged
        in trial time, else the picked column's."""
        if self.view_sel is not None:
            return self.view_sel
        c = self.current()
        if c is not None and c.kind in ("measure", "duration", "level") and c.over == "window" \
                and not c.sel.is_point():
            return P.Selection.seg(c.sel.first, c.sel.last) if not c.sel.is_time() else c.sel
        return None

    def _xform(self, i: int) -> tuple:
        """The x axis for trial i: ("trial",), ("time", a, b) or ("phase", starts, period, a, b)."""
        mode = self.preview.xmode.currentData()
        self._x_note = ""
        if mode == "trial":
            return ("trial",)
        base = self._view_base()
        segs = self.src.segments(i)
        w = base.window({s[0]: (s[1], s[2]) for s in segs}, [s[0] for s in segs]) if base is not None else None
        if w is None or w[1] <= w[0]:
            self._x_note = "click a segment or drag a stretch in Trial time first"
            return ("trial",)
        if mode == "time":
            return ("time", w[0], w[1])
        got = P.cycles(base, segs) if not base.is_time() else None
        if not got or not got[0]:
            self._x_note = (f"{base.label()} doesn't repeat: phase needs a sine, square, triangle or pulse train"
                            if not base.is_time() else "phase needs a segment: click one in Trial time")
            return ("time", w[0], w[1])
        starts, period = got
        return ("phase", tuple(round(x, 9) for x in starts), period, w[0], w[1])

    def spikes_restyled(self) -> None:
        """Unit colors or opacity changed: just the points again."""
        if self.isVisible():
            self._preview_spikes(self.preview_trial())

    def _preview_spikes(self, i: int) -> None:
        """Mark the ticked units' spikes on the response, when it's the channel they were found on."""
        ev = self.page.events
        ch = self.preview.resp_ch.currentText()
        if ev is None or ch != self.src.spike_channel():
            self.preview.show_spikes(np.array([]), np.array([]), [])
            return
        m = (ev.block == i) & np.isin(ev.unit, list(self.page.raw.shown_units()))
        got = self.src.trace(i, ch)
        if got is None or not m.any():
            self.preview.show_spikes(np.array([]), np.array([]), [])
            return
        y, fs = got
        t = ev.t[m]
        units = ev.unit[m]
        k = np.clip((t * fs).round().astype(int), 0, len(y) - 1)
        xf = self.preview.xf
        if xf[0] == "time":
            keep = (t >= xf[1]) & (t < xf[2])
            x = t - xf[1]
        elif xf[0] == "phase":
            keep = (t >= xf[3]) & (t < xf[3] + len(xf[1]) * xf[2])
            x = ((t - xf[3]) % xf[2]) / xf[2] * 360
        else:
            keep, x = np.ones(len(t), bool), t
        side = self.page.raw.marker_side(ev.amplitude[m])
        lift = 0.10 * float(np.nanmax(y) - np.nanmin(y) or 1.0)      # well clear of the AP
        self.preview.show_spikes(x[keep], (y[k] + side * lift)[keep], self.page.raw.brushes(units[keep]),
                                 side[keep])

    def _preview_window(self) -> None:
        c = self.current()
        i = self.preview_trial()
        k = self.cur_row
        row = self.tab.rows[k] if 0 <= k < len(self.tab.rows) else None
        segs = self.src.segments(i)
        order = [s[0] for s in segs]
        send = P.send_at(segs, row.t0) if row is not None and row.trial == i else 1
        seen = P.scoped(segs, send)                   # the column's B is B of the row's own stimulus
        seen_order = [s[0] for s in seen]
        letters, w, value = set(), None, ""
        if c is not None:
            if c.kind in ("measure", "level", "duration") and not (c.kind == "measure" and c.over == "row"):
                letters = {L if send == 1 or P.send_of(L)[0] > 1 else f"{send}{L}"
                           for L in c.sel.letters(seen_order)}
            wins = self.tab.windows.get(c.name)
            if wins is not None and row is not None and k < len(wins):
                w = wins[k]
            elif c.kind in ("level", "duration"):
                w = c.sel.window({s[0]: (s[1], s[2]) for s in seen}, seen_order)
            if row is not None and c.name in self.tab.values:
                text = self.tab.text[c.name][k] if c.name in self.tab.text else (c.fmt(self.tab.values[c.name][k]) or "—")
                value = f"{P.html_name(c.shown())} = {text} {self.tab.units.get(c.name, '')}".strip()
        if row is not None and row.t0 is not None and not letters and self.pad.rows_mode in ("segment", "pulse"):
            letters = {row.label.split("·")[0]}
        label = f"trial {i + 1}" + (f" · row {row.label}" if row is not None and row.label else "")
        if c is not None:
            label += f" · {P.html_name(c.shown())}: {c.recipe()}"
        self.preview.info.setText(label)
        if self.quick_sel is None and c is not None and c.kind in ("measure", "duration") and \
                not (c.kind == "measure" and c.over == "row"):
            self.quick_sel = P.Selection.from_dict(c.sel.to_dict())
        if c is None or not letters:                  # nothing of a column's: show the quick row's own
            q = self.quick_sel
            if q is not None and not q.is_time():
                letters = set(q.letters(order))
            if q is not None and w is None:
                w = q.window({s[0]: (s[1], s[2]) for s in segs}, order)
        self.preview.show_window(letters, self._region(w, c), value)
        bw = self.page.baseline_window(i) if hasattr(self.page, "baseline_window") else None
        self.preview.show_baseline(None if bw is None or self.preview.xf[0] == "phase"
                                   else (self.preview.x_of(bw[0]), self.preview.x_of(bw[1])))
        if getattr(self, "_x_note", ""):
            self.preview.info.setText(label + f" · <i>{self._x_note}</i>")
        self._show_quick()
        self.preview.btn_add.setVisible(self.pad.rows_mode == "manual")
        if hasattr(self.page, "_only_boxes"):
            self.page.selection_for_charts()       # quick charts on just the selection follow it

    def _region(self, w, c) -> tuple[float, float] | None:
        """The shaded window on the x axis shown."""
        xf = self.preview.xf
        if xf[0] == "phase":
            for sel in ((c.sel if c is not None and c.kind == "measure" else None), self.quick_sel):
                if sel is not None and sel.is_phase():
                    return (sel.ph0, sel.ph1)
            return None
        if w is None:
            return None
        return (self.preview.x_of(w[0]), self.preview.x_of(w[1]))

    def _phase_clicked(self, deg: float) -> None:
        """Click in phase view: the value at that phase, every cycle, averaged."""
        base = self._view_base()
        if base is None or base.is_time():
            return
        q = self.quick_sel = P.Selection.phase(base.first, base.last, deg, deg)
        c = self._target()
        if c is not None and c.kind == "measure":
            c.sel, c.over = P.Selection.from_dict(q.to_dict()), "window"
            if c.key in D.SPIKE_KEYS or c.key not in ("value", "start", "end"):
                c.key = "value"
            self.column_edited()
        else:
            self._preview_window()

    def _phase_dragged(self, d0: float, d1: float) -> None:
        """Drag in phase view: that part of every cycle."""
        base = self._view_base()
        self.preview.clear_drag()
        if base is None or base.is_time() or d1 - d0 < 0.5:
            return
        q = self.quick_sel = P.Selection.phase(base.first, base.last, d0, d1)
        c = self._target()
        if c is not None and c.kind == "measure":
            c.sel, c.over = P.Selection.from_dict(q.to_dict()), "window"
            if c.key == "value":
                c.key = "mean"
            self.column_edited()
        else:
            self._preview_window()

    def _target(self) -> P.Column | None:
        """The column a click or drag on the preview sets: the one being set, else the picked
        one, if it measures over a window."""
        c = self.pad.get(self.setting) if self.setting else self.current()
        return c if c is not None and c.kind in ("measure", "level", "duration") else None

    def _segment_clicked(self, letter: str, shift: bool) -> None:
        """Click a segment: measure it in the quick row, and the picked column measures over it
        (shift-click: a run of them)."""
        q = self.quick_sel
        if shift and q is not None and not q.is_time():
            order = [x[0] for x in self.src.segments(self.preview_trial())]
            a, b = q.first, letter
            if a in order and b in order and order.index(b) < order.index(a):
                a, b = b, a
            self.quick_sel = P.Selection(a, b)
        else:
            self.quick_sel = P.Selection.seg(letter)
        self.view_sel = P.Selection.seg(self.quick_sel.first, self.quick_sel.last)
        c = self._target()
        if c is not None:
            self.set_segment(c, letter, shift)
            self.stop_setting()
        else:
            self._preview_window()

    def _point_clicked(self, t: float) -> None:
        """Click on the trace: the value there, in the quick row, and the picked measurement
        becomes the value at that moment in every trial."""
        fs = float(self.page.blocks[self.preview_trial()]["sample_rate"]) if self.page.blocks else 1000.0
        t = round(t * fs) / fs                     # on a sample
        self.quick_sel = P.Selection.point(t)
        c = self._target()
        if c is not None and c.kind == "measure":
            c.sel, c.over = P.Selection.point(t), "window"
            if c.key in D.SPIKE_KEYS or c.key not in ("value", "start", "end"):
                c.key = "value"
            self.column_edited()
        else:
            self._preview_window()

    def _time_dragged(self, t0: float, t1: float) -> None:
        """Drag on the preview: the picked column measures over that stretch of time (with rows by
        hand, the stretch waits for Add to DataPad instead)."""
        if t1 - t0 > 1e-4:
            self.quick_sel = P.Selection.time(t0, t1)
            if self.preview.xf[0] == "trial":         # what stimulus time then shows
                self.view_sel = P.Selection.time(t0, t1)
        if self.pad.rows_mode == "manual" and not self.setting:
            self._show_quick()
            return
        c = self._target()
        if c is not None and c.kind != "level" and t1 - t0 > 1e-4:
            c.sel = P.Selection.time(t0, t1)
            if c.kind == "measure":
                c.over = "window"
                if c.key == "value":
                    c.key = "mean"
            self.column_edited()
        self.preview.clear_drag()
        self.stop_setting()
        self._preview_window()

    def start_setting(self, c: P.Column) -> None:
        self.setting = c.name
        self.preview.setting.setText(f"Setting {c.shown()}'s window: click a segment (shift-click for a run) "
                                     "or drag a stretch · Esc stops")
        self.preview.setting.show()
        if self.preview.btn_fold.isChecked():
            self.preview.btn_fold.setChecked(False)
        self.setFocus()

    def stop_setting(self) -> None:
        self.setting = ""
        self.preview.setting.hide()

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QtCore.QEvent.Type.KeyPress and ev.key() == QtCore.Qt.Key.Key_Escape:
            self.cancel()
            return True
        return super().eventFilter(obj, ev)

    def cancel(self) -> None:
        """Esc: drop the stretch dragged on the preview, stop setting a window, and undo a
        half-typed fx bar."""
        self.stop_setting()
        self.preview.clear_drag()
        if self.fx.hasFocus():
            self.fx.clearFocus()
            self._show_fx()

    def set_segment(self, c: P.Column, letter: str, extend: bool) -> None:
        if c.kind == "level":
            c.sel = P.Selection.seg(letter)
        elif extend and not c.sel.is_time():
            order = [x[0] for x in self.src.segments(self.preview_trial())]
            a, b = c.sel.first, letter
            if a in order and b in order and order.index(b) < order.index(a):
                a, b = b, a                       # B–D, however you clicked them
            c.sel = P.Selection(a, b)
        else:
            old = c.sel
            c.sel = P.Selection(letter, letter, old.mode if not old.is_time() else "all", old.a_ms, old.b_ms,
                                ph0=old.ph0, ph1=old.ph1)
            if c.sel.is_phase() and not P.cycles(c.sel, self.src.segments(self.preview_trial())):
                c.sel.mode = "all"                # this segment doesn't repeat: all of it
        if c.sel.first != c.sel.last and c.sel.mode == "range":
            c.sel.mode = "all"
        if c.kind == "measure":
            c.over = "window"
            if c.key == "value":                  # a point's value, now over a stretch: its mean
                c.key = "mean"
        self.column_edited()

    # -- rows -------------------------------------------------------------------------------
    def _fill_rows_controls(self) -> None:
        for w in (self.rows, self.rows_seg):
            w.blockSignals(True)
        self.rows.setCurrentIndex(max(0, self.rows.findData(self.pad.rows_mode)))
        letters = []
        for i in self.src.trials():
            for s in self.src.segments(i):
                L = P.send_of(s[0])[1]            # B stands for B of every stimulus sent
                if L not in letters:
                    letters.append(L)
        self.rows_seg.clear()
        self.rows_seg.addItem("every segment" if self.pad.rows_mode != "pulse" else "every train", "")
        for L in letters:
            self.rows_seg.addItem(L, L)
        self.rows_seg.setCurrentIndex(max(0, self.rows_seg.findData(self.pad.rows_seg)))
        self.rows_seg.setVisible(self.pad.rows_mode in ("segment", "pulse"))
        for w in (self.rows, self.rows_seg):
            w.blockSignals(False)

    def _rows_picked(self, *_) -> None:
        self.pad.rows_mode = self.rows.currentData()
        self.pad.rows_seg = self.rows_seg.currentData() or ""
        self._fill_rows_controls()
        self.columns_changed.emit()
        self.refresh()

    def _show_quick(self) -> None:
        """The quick row: the common measurements of what was last clicked or dragged, here,
        on every channel."""
        q = self.quick_sel
        i = self.preview_trial()
        if not (0 <= i < len(self.page.blocks)) or self.page.blocks[i] is None:
            self.preview.quick.show_values("", [])
            return
        empty = [(ch, {}) for ch in self.page.names() if self.page.has(i, ch)]
        if q is None:
            self.preview.quick.show_values("", empty)
            return
        segs = self.src.segments(i)
        w = q.window({s[0]: (s[1], s[2]) for s in segs}, [s[0] for s in segs])
        if w is None:
            self.preview.quick.show_values(f"{q.label()} (not in this trial)", empty)
            return
        point = q.is_point() or (q.is_phase() and q.ph0 == q.ph1)
        keys = ("value",) if point else [k for k in QuickRow.KEYS if k != "value"]
        lines = []
        for ch in self.page.names():
            if not self.page.has(i, ch):
                continue
            u = self.src.unit(ch)
            out = {}
            for key in keys:
                if key in D.SPIKE_KEYS:
                    st = self.src.spikes(i)
                    if st is None or ch != self.src.spike_channel():
                        continue                  # spikes only where the spike sorter looked
                    v = P.measure_sel(self.src, i, ch, key, q, segs, st)
                else:
                    v = P.measure_sel(self.src, i, ch, key, q, segs)
                out[key] = f"{P.fmt(v, 3)} {D.unit_of(key, u)}".strip() if np.isfinite(v) else "—"
            lines.append((ch, out))
        self.preview.quick.show_values(q.label(), lines)

    def add_quick_column(self, key: str, ch: str = "") -> None:
        """A quick-row value clicked: a column of it, measured over the same place in every trial."""
        q = self.quick_sel
        if q is None:
            return
        names = self.pad.names()
        ch = ch or (self.src.spike_channel() if key in D.SPIKE_KEYS else self.resp_channel())
        base = {"spikes": "n", "slope": "slope", "area": "area", "p2p": "pp"}.get(
            key, "V" if self.src.unit(ch) in ("V", "mV") else "y")
        where = (q.first if q.first == q.last else f"{q.first}{q.last}") if not q.is_time() else ""
        name = P.free_name(f"{base}_{where}" if where else base, names)
        c = P.Column(name, "measure", key, ch, P.Selection.from_dict(q.to_dict()), over="window")
        self.pad.columns.append(c)
        self.picked = len(self.pad.columns) - 1
        self.column_edited()

    def add_manual_row(self) -> None:
        got = self.preview.drag_region()
        if got is None:
            self.preview.info.setText("Drag across the preview first, then Add to DataPad")
            return
        self.pad.manual.append({"trial": self.preview_trial(), "t0": float(got[0]), "t1": float(got[1])})
        self.preview.clear_drag()
        self.columns_changed.emit()
        self.refresh()

    def clear_manual(self) -> None:
        self.pad.manual = []
        self.columns_changed.emit()
        self.refresh()

    # -- columns ----------------------------------------------------------------------------
    def add_column(self, kind: str, key: str = "") -> None:
        names = self.pad.names()
        c0 = self.current()
        segs = self.src.segments(self.preview_trial())
        default = P.Selection.seg(segs[1][0] if len(segs) > 1 else (segs[0][0] if segs else "A"))
        q = self.quick_sel
        if q is not None and (not q.is_time() or q.is_point() or q.t1 > q.t0):
            sel = P.Selection.from_dict(q.to_dict())   # where you last clicked or dragged
        elif c0 is not None and c0.kind in ("measure", "duration") and not c0.sel.is_time():
            sel = P.Selection.from_dict(c0.sel.to_dict())
        else:
            sel = default
        resp = self.resp_channel()
        # trials without Complex Stimulus segments: measure the whole row (the trial) at first
        over = "row" if self.pad.rows_mode != "trial" or not segs else "window"
        if kind == "measure":
            key = key or "mean"
            spiky = key in D.SPIKE_KEYS
            base = ("rate" if key == "rate" else "n") if spiky else ("V" if self.src.unit(resp) in ("V", "mV") else "y")
            c = P.Column(P.free_name(base, names), "measure", key, self.src.spike_channel() if spiky else resp,
                         sel, over=over)
        elif kind == "level":
            base = "I" if self.src.stim_units()[0].endswith("A") else "L"
            c = P.Column(P.free_name(f"{base}_{sel.first or 'A'}", names), "level", sel=P.Selection.seg(sel.first or "A"))
        elif kind == "duration":
            c = P.Column(P.free_name(f"t_{sel.first or 'x'}", names), "duration", sel=sel)
        elif kind == "info":
            key = key or "trial"
            c = P.Column(P.free_name({"trial": "trial", "group": "group", "n_in_group": "n_in_group",
                                      "start": "t_start"}.get(key, key), names), "info", key)
        elif kind == "python":
            c = P.Column(P.free_name("p", names), "python")
        else:
            c = P.Column(P.free_name("f", names), "formula")
        self.pad.columns.append(c)
        self.picked = len(self.pad.columns) - 1
        self.columns_changed.emit()
        self._recompute()
        QtCore.QTimer.singleShot(0, lambda: self.edit_column(self.picked))

    def edit_column(self, k: int) -> None:
        if not 0 <= k < len(self.pad.columns):
            return
        self.pick(k, follow=False)
        old = getattr(self, "_editor", None)
        if old is not None:                      # one at a time: it moves to the column asked for
            old.close()
            old.deleteLater()
        ed = self._editor = ColumnEditor(self, k)
        idx = k + self.first_col
        x = self.header.sectionViewportPosition(idx)
        at = self.header.mapToGlobal(QtCore.QPoint(max(0, x), self.header.height()))
        ed.place(at)
        ed.show()
        ed.raise_()
        ed.activateWindow()

    def column_edited(self) -> None:
        self.columns_changed.emit()
        self._recompute()

    def pick(self, k: int, follow: bool = True) -> None:
        self.picked = k
        self.scatter_x = ""
        c = self.current()
        if follow and c is not None and c.kind in ("measure", "duration", "level") and c.over == "window":
            self.view_sel = None                  # stimulus time and phase follow the picked column
        self.header.viewport().update()
        self._tint()
        self.draw_preview()
        self._show_fx()

    def _header_clicked(self, idx: int) -> None:
        """Pick a column. ⌘-click a second (Ctrl elsewhere): the one picked before is X, this
        one Y, for Scatter."""
        if self.first_col <= idx < self.first_col + len(self.pad.columns):
            before = self.current()
            add = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.KeyboardModifier.ControlModifier)
            self.pick(idx - self.first_col)
            if add and before is not None and before is not self.current():
                self.scatter_x = before.name
                self.header.viewport().update()
                self._show_scatter()

    def _scatter_pair(self) -> tuple[str, str] | None:
        """(X, Y) for Scatter: the ⌘-clicked pair, if both are still number columns."""
        c = self.current()
        names = [k.name for k in self.pad.columns if k.name not in self.tab.text]
        if c is None or not self.scatter_x or self.scatter_x == c.name \
                or self.scatter_x not in names or c.name not in names:
            return None
        return self.scatter_x, c.name

    def _show_scatter(self) -> None:
        pair = self._scatter_pair()
        self.btn_scatter.setVisible(pair is not None)
        if pair:
            x, y = (P.html_name(self._shown_name(n)) for n in pair)
            self.btn_scatter.setToolTip(f"{y} against {x}, one point per row, in a Scatter chart")

    def _header_menu(self, idx: int, at: QtCore.QPoint) -> None:
        """Right-click a column: edit it, plot, or scatter it against another."""
        k = idx - self.first_col
        c = self.pad.columns[k]
        menu = QtWidgets.QMenu(self)
        menu.addAction("Edit column…", lambda: self.edit_column(k))
        menu.addAction("Plot…", lambda: (self.pick(k), self.plot_dialog()))
        others = [o.name for o in self.pad.columns if o.name not in self.tab.text and o.name != c.name]
        if c.name not in self.tab.text and others:
            sub = menu.addMenu("Scatter against")
            sub.setToolTipsVisible(True)
            for n in others:
                _name_action(sub, P.html_name(self._shown_name(n)), lambda n=n: self.scatter(n, c.name),
                             f"{P.html_name(c.shown())} up, {P.html_name(self._shown_name(n))} across")
        menu.exec(at)

    def scatter(self, x: str, y: str) -> None:
        """Y against X, one point per row: a Scatter chart (live, on the whiteboard) of these
        columns, opened big in chart focus. Make a figure there turns it into a figure."""
        self._open_chart("Scatter", x, [y])

    def _open_chart(self, kind: str, *chosen) -> None:
        """A new Scatter or Bars chart (padcharts.py) of the columns chosen here, big in chart focus."""
        chart = self.page.canvas.add(kind)
        if chart is None:
            return
        chart.choose(*chosen)
        self.page.single_plot.focus(chart)

    def rename(self, c: P.Column, new: str) -> None:
        old = c.name
        c.name = new
        for other in self.pad.columns:
            if other.kind in ("formula", "python"):
                other.expr = _rename(other.expr, old, new)
        self.pad._cache.pop(old, None)
        self.column_edited()

    def move_column(self, k: int, d: int) -> bool:
        cols = self.pad.columns
        if not 0 <= k + d < len(cols):
            return False
        cols[k], cols[k + d] = cols[k + d], cols[k]
        self.picked = k + d
        self.column_edited()
        return True

    def remove_column(self, k: int) -> None:
        if 0 <= k < len(self.pad.columns):
            self.pad.columns.pop(k)
            self.picked = min(self.picked, len(self.pad.columns) - 1)
            self.column_edited()

    def clear_columns(self) -> None:
        if self.pad.columns and QtWidgets.QMessageBox.question(
                self, "Clear the DataPad", "Take every column out?") == QtWidgets.QMessageBox.StandardButton.Yes:
            self.pad.columns = []
            self.picked = -1
            self.column_edited()

    def restored(self) -> None:
        """The DataPad was loaded with a session: show it."""
        self.picked = 0 if self.pad.columns else -1
        self._fill_rows_controls()
        self.data_changed()

    # -- the fx bar -------------------------------------------------------------------------
    def _show_fx(self) -> None:
        c = self.current()
        self.fx_name.setText(f"<b>{P.html_name(c.shown())}</b>" if c else "")
        if not self.fx.hasFocus():
            self.fx.setText(c.recipe() if c else "")
        self.fx.setReadOnly(c is None or c.kind == "python")
        self.fx_error.setText(self.tab.errors.get(c.name, "") if c else "")
        self._show_scatter()
        self.fx_error.setToolTip(self.fx_error.text())

    def _fx_entered(self) -> None:
        c = self.current()
        if c is None:
            return
        if c.kind == "python":
            self.edit_column(self.picked)
            return
        try:
            new = P.parse_recipe(self.fx.text(), c, self.page.names())
        except ValueError as e:
            self.fx_error.setText(str(e))
            return
        self.pad.columns[self.picked] = new
        self.fx.clearFocus()
        self.column_edited()

    # -- the table --------------------------------------------------------------------------
    def header_html(self, idx: int) -> str:
        fixed = {0: "", 1: "<b>Trial</b>", 2: "<b>Row</b>"}
        if idx in fixed:
            return fixed[idx]
        k = idx - self.first_col
        if not 0 <= k < len(self.pad.columns):
            return ""
        c = self.pad.columns[k]
        u = self.tab.units.get(c.name, "")
        err = c.name in self.tab.errors
        sub = style.tokens(self.page._mode)["subtle"]
        return (f"<b>{'⚠ ' if err else ''}{P.html_name(c.shown())}</b>"
                + (f" <span style='color:{sub}'>{u}</span>" if u else ""))

    def fill_table(self) -> None:
        cols, tab = self.pad.columns, self.tab
        excl = self.excluded()
        by = self.summary.currentData()
        sums = P.summarize(self._included_table(), self.src, by) if by is not None and cols else []
        t = self.table
        t.blockSignals(True)
        t.setUpdatesEnabled(False)
        t.clearSpans()
        t.clearContents()
        t.setColumnCount(len(cols) + self.first_col + 1)       # the last: "+", to add a column
        t.setRowCount(len(tab.rows) + len(sums))
        tok = style.tokens(self.page._mode)
        dim = QtGui.QColor(tok["disabled_ink"])
        right = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        Item = QtWidgets.QTableWidgetItem
        seen = set()
        for r, row in enumerate(tab.rows):
            out = row.trial in excl
            tick = Item()
            if row.trial not in seen:              # one tick per trial (on its first row)
                tick.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled)
                tick.setCheckState(QtCore.Qt.CheckState.Unchecked if out else QtCore.Qt.CheckState.Checked)
                tick.setToolTip("Untick to leave this trial out of the summary rows and plots")
            else:
                tick.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            seen.add(row.trial)
            tick.setData(QtCore.Qt.ItemDataRole.UserRole, row.trial)
            t.setItem(r, 0, tick)
            name, k = self.src.group(row.trial)
            ti = Item(str(row.trial + 1))
            ti.setToolTip(f"{name}, trial {k} of the group" if name else "")
            t.setItem(r, 1, ti)
            t.setItem(r, 2, Item(row.label))
            for j, c in enumerate(cols):
                if out:
                    text = ""                          # not analyzed: tick it to measure it
                else:
                    text = tab.text[c.name][r] if c.name in tab.text else (c.fmt(tab.column(c.name)[r]) or "—")
                it = Item(text)
                it.setTextAlignment(right)
                if c.name in tab.errors:
                    it.setToolTip(tab.errors[c.name])
                t.setItem(r, j + self.first_col, it)
            if out:
                for j in range(1, len(cols) + self.first_col):
                    it = t.item(r, j)
                    f = it.font()
                    f.setStrikeOut(True)
                    it.setFont(f)
                    it.setForeground(dim)
        r0 = len(tab.rows)
        sub = QtGui.QColor(tok["subtle"])
        band = QtGui.QColor(tok["ground"])
        for k, (g, stats) in enumerate(sums):
            label = "all rows" if by == "all" else (g or "(no group)") if by == "group" else f"{self._shown_name(by)} = {g}"
            lab = Item(label)
            lab.setForeground(sub)
            t.setItem(r0 + k, 1, lab)
            t.setSpan(r0 + k, 1, 1, 2)
            for j, c in enumerate(cols):
                m, s, n = stats.get(c.name, (np.nan, np.nan, 0))
                text = (c.fmt(m) + (f" ± {c.fmt(s)}" if np.isfinite(s) and s > 0 else "")) if n else ""
                cell = Item(text)
                cell.setToolTip(f"mean ± SEM, n = {n}")
                cell.setForeground(sub)
                cell.setTextAlignment(right)
                t.setItem(r0 + k, j + self.first_col, cell)
            for j in range(len(cols) + self.first_col):
                if t.item(r0 + k, j) is None:
                    t.setItem(r0 + k, j, Item())
                t.item(r0 + k, j).setBackground(band)
        t.setColumnHidden(2, self.pad.rows_mode == "trial")
        t.resizeColumnsToContents()
        t.setColumnWidth(0, 26)
        t.setUpdatesEnabled(True)
        t.blockSignals(False)
        self._tint()
        self.header.viewport().update()
        self._mark_row()

    def _shown_name(self, name: str) -> str:
        c = self.pad.get(name)
        return c.shown() if c else name

    def _fill_summary_choices(self) -> None:
        keep = self.summary.currentData() if self.summary.count() else "all"
        self.summary.blockSignals(True)
        self.summary.clear()
        self.summary.addItem("nothing", None)
        self.summary.addItem("all rows", "all")
        self.summary.addItem("group", "group")
        for c in self.pad.columns:
            self.summary.addItem(c.shown(), c.name)
        i = self.summary.findData(keep)
        self.summary.setCurrentIndex(i if i >= 0 else 1)
        self.summary.blockSignals(False)

    def _tint(self) -> None:
        tok = style.tokens(self.page._mode)
        col = QtGui.QColor(tok["focus"])
        col.setAlpha(20)
        cur = QtGui.QColor(tok["focus"])
        cur.setAlpha(45)
        self.table.blockSignals(True)
        for r in range(len(self.tab.rows)):
            for j in range(1, self.table.columnCount()):
                it = self.table.item(r, j)
                if it is None:
                    continue
                k = j - self.first_col
                on_col, on_row = k >= 0 and k == self.picked, r == self.cur_row
                it.setBackground(cur if on_col and on_row else col if (on_col or on_row) else QtGui.QBrush())
        self.table.blockSignals(False)

    def _mark_row(self) -> None:
        self._tint()
        if 0 <= self.cur_row < self.table.rowCount() and self.table.item(self.cur_row, 1) is not None:
            self.table.scrollToItem(self.table.item(self.cur_row, 1))

    def _cell_clicked(self, r: int, col: int) -> None:
        if r >= len(self.tab.rows) or col == 0:
            return
        self.cur_row = r
        if self.first_col <= col < self.first_col + len(self.pad.columns):
            if self.picked != col - self.first_col:
                self.scatter_x = ""
            self.picked = col - self.first_col
            self.header.viewport().update()
            self._show_fx()
        trial = self.tab.rows[r].trial
        if trial != self.page.index():
            self.page.current.setValue(trial + 1)    # draws the preview (current_changed)
        else:
            self.draw_preview()
        self._tint()

    def _ticked(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() != 0 or not item.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable:
            return
        trial = item.data(QtCore.Qt.ItemDataRole.UserRole)
        hidden = item.checkState() != QtCore.Qt.CheckState.Checked
        if trial is not None and (trial in self.excluded()) != hidden:
            self.page.set_hidden(("block", int(trial)), hidden)     # the Trials list follows

    def _included_table(self) -> P.Table:
        excl = self.excluded()
        keep = [k for k, r in enumerate(self.tab.rows) if r.trial not in excl]
        out = P.Table([self.tab.rows[k] for k in keep])
        out.values = {n: v[keep] for n, v in self.tab.values.items()}
        out.text = {n: [v[k] for k in keep] for n, v in self.tab.text.items()}
        out.units = dict(self.tab.units)
        return out

    # -- recipes ----------------------------------------------------------------------------
    def save_recipe(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save the recipe", "recipe.newtpad",
                                                        "DataPad recipe (*.newtpad)")
        if path:
            st = self.pad.state()
            st.pop("manual", None)                 # rows by hand belong to these trials
            Path(path).write_text(json.dumps(st, indent=1))

    def apply_recipe(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Apply a recipe", "", "DataPad recipe (*.newtpad)")
        if not path:
            return
        try:
            state = json.loads(Path(path).read_text())
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, "DataPad", f"Couldn't read the recipe: {e}")
            return
        state["manual"] = self.pad.manual
        self.pad.restore(state)
        self.restored()

    def copy_table(self) -> None:
        rows = P.to_csv(self.tab, self.pad.columns)
        QtWidgets.QApplication.clipboard().setText("\n".join("\t".join(r) for r in rows))

    def export_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export the table", "datapad.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", newline="") as f:
                csv.writer(f).writerows(P.to_csv(self.tab, self.pad.columns))

    # -- plotting ---------------------------------------------------------------------------
    def snapshot(self, cols: list[str]) -> dict:
        """The numbers a figure plots: these columns of the ticked rows, their groups and the
        groups' colors."""
        tab = self._included_table()
        groups = [self.src.group(r.trial)[0] for r in tab.rows]
        names = [n for n, _ in self.page.shown]
        colors = {n: self.page.group_color(g) for g, n in enumerate(names)}
        return {"rows": [r.trial + 1 for r in tab.rows], "groups": groups, "colors": colors,
                "cols": {c: [float(v) for v in tab.column(c)] for c in cols if c in tab.values},
                "units": {c: tab.units.get(c, "") for c in cols}}

    def plot_dialog(self) -> None:
        names = [c.name for c in self.pad.columns if c.name not in self.tab.text]
        if not names:
            QtWidgets.QMessageBox.information(self, "Plot", "Add a column or two first.")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Plot")
        f = QtWidgets.QFormLayout(dlg)
        kind = QtWidgets.QComboBox()
        kind.addItem("Y against X", "xy")
        kind.addItem("Bars by group (mean ± SEM)", "bar")
        x = QtWidgets.QComboBox()
        x.addItem("Row order", "")
        for n in names:
            x.addItem(self._shown_name(n), n)
        ys = QtWidgets.QListWidget()
        for n in names:
            it = QtWidgets.QListWidgetItem(self._shown_name(n))
            it.setData(QtCore.Qt.ItemDataRole.UserRole, n)
            it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.CheckState.Unchecked)
            ys.addItem(it)
        for box in (kind, x):                       # nothing chosen for you: you choose what to plot
            box.setPlaceholderText("Choose…")
            box.setCurrentIndex(-1)
        ys.setMaximumHeight(160)
        f.addRow("Kind", kind)
        f.addRow("X", x)
        f.addRow("Y", ys)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        ok = bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        ok.setText("Open chart")

        def ready(*_) -> None:
            """Open only once the kind, the Y columns and (for Y against X) the X are chosen."""
            x.setEnabled(kind.currentData() != "bar")
            ticked = any(ys.item(k).checkState() == QtCore.Qt.CheckState.Checked for k in range(ys.count()))
            ok.setEnabled(kind.currentIndex() >= 0 and ticked and (kind.currentData() == "bar" or x.currentIndex() >= 0))

        kind.currentIndexChanged.connect(ready)
        x.currentIndexChanged.connect(ready)
        ys.itemChanged.connect(ready)
        ready()
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if not dlg.exec():
            return
        picked = [ys.item(k).data(QtCore.Qt.ItemDataRole.UserRole) for k in range(ys.count())
                  if ys.item(k).checkState() == QtCore.Qt.CheckState.Checked]
        if not picked:
            return
        if kind.currentData() == "bar":
            self._open_chart("Bars", picked)
        else:
            self._open_chart("Scatter", x.currentData(), picked)


def _rename(expr: str, old: str, new: str) -> str:
    return re.sub(rf"(?<![\w.]){re.escape(old)}(?!\w)", new, expr)
