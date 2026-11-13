"""Measuring on the Analysis whiteboard, in place of the DataPad tab: the Measure tool's
windows, drawn on the charts they were dragged across, and the two charts they fill, the
DataPad table and Scatter (the numbers are worked out in measure.py).

The Measure tool works on every chart (see measure.py for the kinds of window). In Time,
drag across a chart against time to measure that stretch, or across a triggered or spike
chart for ms from each event; in Phase, across a chart against phase, or one cycle of a
chart against time, to measure the same part of every cycle. On the others, drag across a
range of values; on the PCA map and Scatter, draw around the points.
The window shows on every chart of its kind; drag it, or its edges, to move it. Its
settings (what to measure, on which channel, where, and how often it repeats) are with the
DataPad table's. The first window puts a DataPad table on the board if there isn't one.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import datapad as D
from . import fitting as FIT
from . import measure as M
from .chartbase import Chart
from .plotzoom import plot_widget
from .unitbox import UnitSpinBox
from .viewkit import label_axis, with_slider

TIME_COLOR, PHASE_COLOR = "#2f6fb0", "#7b4fa0"
MAX_CYCLES_SHOWN = 200             # a phase window drawn on a chart against time: in this many cycles
MAX_CORNERS = 24                   # a shape drawn by hand keeps this many corners


class _Region(pg.LinearRegionItem):
    """A measure window on a plot: drag it or its edges; a click picks it."""

    def __init__(self, tool: "MeasureTool", win: M.Window, start: float, end: float, ghost: bool):
        color = QtGui.QColor(PHASE_COLOR if win.unit == M.PHASE else TIME_COLOR)
        fill = QtGui.QColor(color)
        fill.setAlpha(18 if ghost else (55 if tool.selected == win.id else 32))
        super().__init__((start, end), movable=not ghost, brush=pg.mkBrush(fill))
        pen = pg.mkPen(color, width=1 if ghost else 2, style=QtCore.Qt.PenStyle.DashLine if ghost else
                       QtCore.Qt.PenStyle.SolidLine)
        for line in self.lines:
            line.setPen(pen)
            line.setHoverPen(pg.mkPen(color, width=3))
        self.setZValue(-5)
        self.tool, self.win = tool, win
        if not ghost:
            pg.InfLineLabel(self.lines[0], win.name, position=0.95, anchors=[(0, 0), (0, 0)],
                            color=color, fill=pg.mkBrush(255, 255, 255, 200))
            self.sigRegionChangeFinished.connect(self._moved)

    def _moved(self) -> None:
        a, b = self.getRegion()
        self.tool.update(self.win.id, start=self.tool.to_window(self.win, a), width=self.tool.to_window(self.win, b - a))

    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton:
            ev.accept()
            self.tool.pick(self.win.id)
        else:
            super().mouseClickEvent(ev)


class MeasureTool(QtCore.QObject):
    """The windows (shared by every chart on the page), which is picked, and drawing them."""
    changed = QtCore.Signal()                 # the windows changed: the tables and scatters again
    picked = QtCore.Signal(int)

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.windows: list[M.Window] = []
        self.selected: int | None = None
        self._next = 1
        self._drag: dict | None = None

    # -- the windows -----------------------------------------------------------------------
    def get(self, wid: int) -> M.Window | None:
        return next((w for w in self.windows if w.id == wid), None)

    def add(self, unit: str, start: float, width: float, channel: str, key: str, **more) -> M.Window:
        """A new window; `more`: its other fields (on, points, axes)."""
        w = M.Window(self._next, M.next_name(self.windows, unit), unit, start, width, key, channel, **more)
        self._next += 1
        self.windows.append(w)
        self.selected = w.id
        self._changed()
        return w

    def update(self, wid: int, **changes) -> None:
        w = self.get(wid)
        if w is None:
            return
        for k, v in changes.items():
            setattr(w, k, v)
        w.width = max(w.width, 1e-6)
        self._changed()

    def remove(self, wid: int) -> None:
        self.windows = [w for w in self.windows if w.id != wid]
        if self.selected == wid:
            self.selected = None
        self._changed()

    def pick(self, wid: int) -> None:
        self.selected = wid
        self.decorate_all()
        table = self.page.canvas.first(DataPadTableChart.kind)
        if table is not None:
            self.page.canvas.select(table)
        self.picked.emit(wid)

    def _changed(self) -> None:
        self.decorate_all()
        self.changed.emit()

    def state(self) -> dict:
        return {"windows": [w.to_dict() for w in self.windows]}

    def restore(self, state: dict | None) -> None:
        self.windows = []
        for d in (state or {}).get("windows", []):
            try:
                self.windows.append(M.Window.from_dict(d))
            except (KeyError, TypeError):
                continue
        self._next = max((w.id for w in self.windows), default=0) + 1
        self.selected = None
        self.decorate_all()
        self.changed.emit()

    # -- on the charts ---------------------------------------------------------------------------
    @staticmethod
    def kind_of(chart) -> tuple[str, str] | None:
        """What a window dragged across this chart is: (kind, what it's on), or None."""
        if chart.measure is not None:
            return chart.measure
        unit = {"time": M.TIME, "phase": M.PHASE}.get(chart.link)
        return (unit, "") if unit else None

    @classmethod
    def unit_of_chart(cls, chart) -> str | None:
        kind = cls.kind_of(chart)
        return kind[0] if kind else None

    def to_window(self, win: M.Window, x: float) -> float:
        """A plot's x (s or degrees) in the window's terms (the same: s or degrees)."""
        return float(x)

    def decorate_all(self) -> None:
        for c in self.page.canvas.every():
            self.decorate(c)

    def _listens(self, kind: tuple[str, str]) -> bool:
        """Whether the Measure tool drags on charts of this kind now: Time on charts against
        time (in Phase too, across one cycle) and from an event, Phase on charts against phase;
        either on the rest."""
        canvas = self.page.canvas
        if canvas.tool() != "measure" or self.page.repeats.btn_mark.isChecked():
            return False
        mode = canvas.measure_unit()
        return {M.PHASE: mode == M.PHASE, M.EVENT: mode == M.TIME}.get(kind[0], True)

    def decorate(self, chart) -> None:
        """Draw the windows of this chart's kind on it, and let the Measure tool drag on it."""
        kind = self.kind_of(chart)
        for item, plot in getattr(chart, "_measure_items", []):
            try:
                plot.removeItem(item)
            except (RuntimeError, ValueError):
                pass
        chart._measure_items = []
        if kind is None:
            return
        unit, on = kind
        listening = self._listens(kind)
        end = self._length(chart, unit)
        for k, pw in enumerate(chart.plots()):
            p = pw.getPlotItem()
            vb = p.getViewBox()
            drag = (lambda x0, x1, done, c=chart, k=k: self._dragged(c, k, x0, x1, done)) if unit != M.SHAPE else None
            draw = (lambda pts, done, c=chart: self._drawn(c, pts, done)) if unit == M.SHAPE else None
            if listening:
                vb.on_drag_select, vb.on_draw = drag, draw
            elif getattr(vb, "_measuring", False):
                vb.on_drag_select = vb.on_draw = None
            vb._measuring = listening
            for item in self._items(chart, unit, on, end):
                p.addItem(item)
                chart._measure_items.append((item, p))

    def _items(self, chart, unit: str, on: str, end: float) -> list:
        """The windows to show on a chart of this kind."""
        out = []
        for w in self.windows:
            if unit == M.TIME and w.unit == M.PHASE:          # a phase window, in each cycle of the trial
                out += [_Region(self, w, a, b, ghost=True) for a, b in self._in_cycles(chart, w)]
                continue
            if w.unit != unit or (unit not in (M.TIME, M.PHASE) and w.on != on):
                continue
            if unit == M.SHAPE:
                if on == "scatter" and w.axes != chart.axes():
                    continue                                  # drawn around other measurements
                out.append(_Shape(self, w))
                continue
            starts = w.starts(end) if unit in (M.TIME, M.PHASE) else [w.start]
            out += [_Region(self, w, s, s + w.width, ghost=j > 0) for j, s in enumerate(starts)]
        return out

    def _length(self, chart, unit: str) -> float:
        if unit == M.PHASE:
            return 360.0
        blocks = [i for i in chart.blocks() if self.page.blocks[i] is not None]
        return max((self.page.blocks[i]["duration_s"] for i in blocks), default=0.0)

    def _cycles(self, chart) -> np.ndarray:
        """The stimulus cycles (s) of the trial a chart against time shows: the highlighted one."""
        blocks = [i for i in chart.blocks() if self.page.blocks[i] is not None]
        if not blocks:
            return np.empty((0, 2))
        i = self.page.index() if self.page.index() in blocks else blocks[0]
        return M._cycles_of(self.page, i) / self.page.blocks[i]["sample_rate"]

    def _in_cycles(self, chart, w: M.Window) -> list[tuple[float, float]]:
        """Where a phase window falls in each cycle of the trial shown (s)."""
        out = []
        for c0, c1 in self._cycles(chart)[:MAX_CYCLES_SHOWN]:
            for s in w.starts(360.0):
                out.append((c0 + (c1 - c0) * s / 360, c0 + (c1 - c0) * min(s + w.width, 360.0) / 360))
        return out

    def _dragged(self, chart, k: int, x0: float, x1: float, done: bool) -> None:
        """The Measure tool dragged across plot k of a chart: a new window when it's let go."""
        unit, on = self.kind_of(chart)
        if unit == M.TIME and self.page.canvas.measure_unit() == M.PHASE:
            unit = M.PHASE                                    # across one cycle: that part of every cycle
        p = chart.plots()[k].getPlotItem()
        self._drop_drag()
        if not done:
            color = QtGui.QColor(TIME_COLOR if unit == M.TIME else PHASE_COLOR)
            color.setAlpha(45)
            item = pg.LinearRegionItem((x0, x1), movable=False, brush=pg.mkBrush(color))
            p.addItem(item)
            self._drag = {"item": item, "plot": p}
            return
        if x1 - x0 <= 0:
            return
        if unit == M.PHASE and chart.link == "time":
            cycles = self._cycles(chart)
            c = np.searchsorted(cycles[:, 0], x0, side="right") - 1 if len(cycles) else -1
            if c < 0 or x0 >= cycles[c, 1]:
                self.page.main.statusBar().showMessage(
                    "No stimulus cycle there: Phase measures part of each cycle, so drag within one (the cycles "
                    "are found from the stimulus channel, or set them in Cycles)", 8000)
                return
            c0, c1 = cycles[c]
            x0, x1 = 360 * (x0 - c0) / (c1 - c0), min(360.0, 360 * (x1 - c0) / (c1 - c0))
        elif unit == M.PHASE:
            x0, x1 = max(0.0, x0), min(360.0, x1)
        channel = self.page.repeats.channel() if on == "loop" else chart.measure_channel(k)
        key = {M.PHASE: "mean", M.RANGE: "intime", M.VALUES: "count"}.get(unit) or \
            ("spikes" if on == "stimulus" or (chart.counts_spikes and unit == M.TIME) else "max")
        self._new(unit, x0, x1 - x0, channel, key, on)

    def _drop_drag(self) -> None:
        if self._drag is not None:
            try:
                self._drag["plot"].removeItem(self._drag["item"])
            except (RuntimeError, ValueError):
                pass
            self._drag = None

    def _drawn(self, chart, points: list, done: bool) -> None:
        """The Measure tool drawing around points on a 2-D chart: a shape window when it's let go."""
        p = chart.plots()[0].getPlotItem()
        self._drop_drag()
        xy = np.array(points, float)
        if not done:
            item = pg.PlotCurveItem(xy[:, 0], xy[:, 1], pen=pg.mkPen(TIME_COLOR, width=2))
            p.addItem(item)
            self._drag = {"item": item, "plot": p}
            return
        xy = xy[::max(1, -(-len(xy) // MAX_CORNERS))]       # a few corners, each one draggable
        if len(xy) < 3 or np.ptp(xy[:, 0]) <= 0 or np.ptp(xy[:, 1]) <= 0:
            return
        on = chart.measure[1]
        self._new(M.SHAPE, 0.0, 1.0, "", "count", on, points=xy.tolist(), axes=chart.axes() if on == "scatter" else [])

    def _new(self, unit: str, start: float, width: float, channel: str, key: str, on: str = "", **more) -> M.Window:
        if self.page.canvas.first(DataPadTableChart.kind) is None:
            self.page.canvas.add(DataPadTableChart.kind)
        w = self.add(unit, start, width, channel, key, on=on, **more)
        self.pick(self.selected)
        return w


class _Shape(pg.PolyLineROI):
    """A shape window, drawn around points: drag it to move it, or a corner to change it;
    a click picks it."""

    def __init__(self, tool: "MeasureTool", win: M.Window):
        color = QtGui.QColor(TIME_COLOR)
        super().__init__(win.points, closed=True, pen=pg.mkPen(color, width=3 if tool.selected == win.id else 2))
        self.setZValue(20)
        self.tool, self.win = tool, win
        self.sigRegionChangeFinished.connect(self._moved)
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.LeftButton)
        self.sigClicked.connect(lambda *_: tool.pick(win.id))
        label = pg.TextItem(win.name, color=color, anchor=(0, 1))
        label.setParentItem(self)
        label.setPos(*win.points[0])

    def _moved(self) -> None:
        corners = [self.mapToParent(at) for _, at in self.getLocalHandlePositions()]
        self.tool.update(self.win.id, points=[[c.x(), c.y()] for c in corners])


def _what(win: M.Window) -> str:
    return {M.TIME: "time", M.PHASE: "phase"}.get(win.unit) or M.ON.get(win.on, win.unit)


def _summary(win: M.Window) -> str:
    """A window's line in the DataPad table's list: its name, kind and where it is."""
    if win.unit == M.SHAPE:
        where = "shape"
    else:
        u = {M.TIME: " s", M.PHASE: "°", M.EVENT: " ms"}.get(win.unit, "")
        where = f"{win.start:.4g} to {win.end:.4g}{u}" + (" ×n" if win.repeat > 0 else "")
    return f"{win.name} · {_what(win)} · {where}"


class _WindowBox(QtWidgets.QWidget):
    """The picked window's settings, under the DataPad table's list of windows."""

    def __init__(self, chart: "DataPadTableChart", win: M.Window):
        super().__init__()
        self.chart, self.wid = chart, win.id
        tool = chart.page.measure
        f = QtWidgets.QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        self.key = QtWidgets.QComboBox()
        keys = M.measurements(win.unit)
        if win.unit == M.PHASE:
            keys = {k: keys[k] for k in M.PHASE_KEYS}
        for k in keys:
            self.key.addItem(M.name_of(k, win.unit), k)
            self.key.setItemData(self.key.count() - 1, keys[k][2], QtCore.Qt.ItemDataRole.ToolTipRole)
        self.key.setCurrentIndex(max(0, self.key.findData(win.key)))
        self.key.currentIndexChanged.connect(lambda *_: tool.update(self.wid, key=self.key.currentData()))
        f.addRow("Measure", self.key)
        if win.unit not in (M.VALUES, M.SHAPE):
            self.channel = QtWidgets.QComboBox()
            names = chart.page.names()
            self.channel.addItems(names)
            if win.channel not in names:
                self.channel.addItem(win.channel)
            self.channel.setCurrentText(win.channel)
            self.channel.currentTextChanged.connect(lambda v: tool.update(self.wid, channel=v))
            f.addRow("Of" if win.unit == M.RANGE else "Channel", self.channel)
        if win.unit == M.SHAPE:
            note = QtWidgets.QLabel("Drag the shape on the chart to move it, or a corner to change it.")
            note.setWordWrap(True)
            f.addRow(note)
            self._add_remove(f, tool)
            return
        u = {M.TIME: "s", M.PHASE: "°", M.EVENT: "ms"}.get(win.unit)
        if u is None:                             # a range of values: in their unit
            u = "ms" if win.on == "intervals" else chart.page.unit_of(win.channel or chart.page.raw.channel.currentText())
        if win.unit == M.TIME:
            self.start = UnitSpinBox(0, 3600, "s", step=0.01)
            self.width = UnitSpinBox(0.0001, 3600, "s", step=0.01)
        elif win.unit == M.PHASE:
            self.start = UnitSpinBox(0, 359, "°", step=5)
            self.width = UnitSpinBox(1, 360, "°", step=5)
        else:
            self.start = UnitSpinBox(-1e6, 1e6, u, step=1 if u == "ms" else 0.01)
            self.width = UnitSpinBox(1e-6, 1e6, u, step=1 if u == "ms" else 0.01)
        for box, v in ((self.start, win.start), (self.width, win.width)):
            box.setValue(v)
        self.start.valueChanged.connect(lambda v: tool.update(self.wid, start=v))
        self.width.valueChanged.connect(lambda v: tool.update(self.wid, width=v))
        if win.unit == M.EVENT:
            self.start.setToolTip("Where the window starts, after each event (before it, if negative)")
        f.addRow("From" if win.unit in (M.RANGE, M.VALUES) else "Starts at",
                 with_slider(self.start) if win.unit in (M.TIME, M.PHASE) else self.start)
        f.addRow("Width", with_slider(self.width) if win.unit in (M.TIME, M.PHASE) else self.width)
        if win.unit in (M.TIME, M.PHASE):
            self.repeat = UnitSpinBox(0, 3600, "s", step=0.01) if win.unit == M.TIME else \
                UnitSpinBox(0, 359, "°", step=5)
            self.repeat.setValue(win.repeat)
            self.repeat.valueChanged.connect(lambda v: tool.update(self.wid, repeat=v))
            self.repeat.setSpecialValueText("once")
            self.repeat.setToolTip("Measure the same width again this far along, all along the "
                                   + ("trial" if win.unit == M.TIME else "cycle") + ": a column each time")
            f.addRow("Repeat every", with_slider(self.repeat))
        self._add_remove(f, tool)

    def _add_remove(self, f: QtWidgets.QFormLayout, tool) -> None:
        remove = QtWidgets.QPushButton("Remove this window")
        remove.clicked.connect(lambda: tool.remove(self.wid))
        f.addRow(remove)


class DataPadTableChart(Chart):
    kind = "DataPad table"
    y = "—"
    tip = "What did each Measure window measure, trial by trial or group by group?"
    uses_units = False
    help = "datapad"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ContiguousSelection)
        self.empty_note = QtWidgets.QLabel("Pick the Measure tool and drag across a chart (or, on the PCA map and "
                                           "Scatter, draw around points): each window adds a column here.")
        self.empty_note.setWordWrap(True)
        self.empty_note.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self.empty_note)
        self.body.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 4)
        self.btn_copy = QtWidgets.QPushButton("Copy")
        self.btn_copy.setToolTip("Copy the table, to paste into a spreadsheet")
        self.btn_copy.clicked.connect(self.copy)
        self.btn_export = QtWidgets.QPushButton("Export CSV…")
        self.btn_export.clicked.connect(self._export_dialog)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_export)
        row.addStretch(1)
        self.body.addLayout(row)
        self.rows_from = QtWidgets.QComboBox()
        for key, label in M.ROWS.items():
            self.rows_from.addItem(label, key)
        self.rows_from.setToolTip("Each trial; each group's average trace, measured once; each group's "
                                  "trials measured one by one, as mean ± SD (and n); or each spike, for the "
                                  "windows that measure spikes one by one")
        self.field("rows", "Rows", self.rows_from)
        self.windows_box = QtWidgets.QWidget()
        self._windows = QtWidgets.QVBoxLayout(self.windows_box)
        self._windows.setContentsMargins(0, 0, 0, 0)
        self.window_list = QtWidgets.QListWidget()
        self.window_list.setToolTip("The Measure windows: pick one to edit it, and to see it on the charts")
        self.window_list.itemSelectionChanged.connect(self._list_picked)
        self.window_note = QtWidgets.QLabel("No windows yet: drag across a chart with the Measure tool.")
        self.window_note.setWordWrap(True)
        self.btn_dup = QtWidgets.QPushButton("Duplicate")
        self.btn_dup.setToolTip("A copy of the picked window, just after it")
        self.btn_dup.clicked.connect(self._duplicate)
        self.editor = QtWidgets.QVBoxLayout()
        self.editor.setContentsMargins(0, 0, 0, 0)
        self.reach = QtWidgets.QLabel()
        self.reach.setWordWrap(True)
        self.reach.setToolTip("Which of the trials measured have a value from the picked window")
        for w in (self.window_note, self.window_list):
            self._windows.addWidget(w)
        self._windows.addWidget(self.btn_dup)
        self._windows.addWidget(self.reach)
        self._windows.addLayout(self.editor)
        self._side.insertWidget(self._side.count() - 1, self.windows_box)
        page.measure.changed.connect(self._windows_changed)
        page.measure.picked.connect(lambda *_: self._fill_windows())
        self._fill_windows()
        self.cols: list[M.Column] = []
        self.rows: list[dict] = []

    def _windows_changed(self) -> None:
        tool = self.page.measure
        if [self.window_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole)
                for i in range(self.window_list.count())] != [w.id for w in tool.windows]:
            self._fill_windows()
        else:
            for i, w in enumerate(tool.windows):
                self.window_list.item(i).setText(_summary(w))
        self.refresh()

    def _list_picked(self) -> None:
        item = self.window_list.currentItem()
        if item is not None and item.data(QtCore.Qt.ItemDataRole.UserRole) != self.page.measure.selected:
            self.page.measure.pick(item.data(QtCore.Qt.ItemDataRole.UserRole))

    def _duplicate(self) -> None:
        tool = self.page.measure
        w = tool.get(tool.selected) if tool.selected is not None else None
        if w is None:
            return
        shift = 0.0 if w.unit == M.SHAPE else w.width
        tool.add(w.unit, w.start + shift, w.width, w.channel, w.key, on=w.on, repeat=w.repeat,
                 points=[list(p) for p in w.points], axes=list(w.axes))

    def _fill_windows(self) -> None:
        tool = self.page.measure
        while self.editor.count():
            w = self.editor.takeAt(0).widget()
            if w is not None:
                w.hide()                          # or it shows under the new one until it's deleted
                w.deleteLater()
        lst = self.window_list
        lst.blockSignals(True)
        lst.clear()
        for w in tool.windows:
            item = QtWidgets.QListWidgetItem(_summary(w))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, w.id)
            lst.addItem(item)
            if w.id == tool.selected:
                lst.setCurrentItem(item)
        lst.blockSignals(False)
        lst.setFixedHeight(min(6, max(1, len(tool.windows))) * 24 + 8)
        lst.setVisible(bool(tool.windows))
        self.window_note.setVisible(not tool.windows)
        self.btn_dup.setVisible(bool(tool.windows))
        win = tool.get(tool.selected) if tool.selected is not None else None
        if win is not None:
            self.editor.addWidget(_WindowBox(self, win))

    def draw(self) -> None:
        self.cols, self.rows = M.table(self.page, self.page.measure.windows, self.rows_from.currentData() or "trial",
                                       self.blocks(), self.plan)
        heads = ["Group", "n" if self.rows_from.currentData() == "spread" else
                 ("Trial" if self.rows_from.currentData() == "trial" else "")] + \
            [c.label(self.page.unit_of) for c in self.cols]
        if self.rows_from.currentData() == "trial" and self.rows:       # trials too short for a column
            for k in range(len(self.cols)):
                have = sum(1 for r in self.rows if np.isfinite(r["values"][k]))
                if have < len(self.rows):
                    heads[2 + k] += f" ({have} of {len(self.rows)})"
        t = self.table
        t.clear()
        t.setColumnCount(len(heads))
        t.setRowCount(len(self.rows))
        t.setHorizontalHeaderLabels(heads)
        picked = self.page.measure.selected
        for r, row in enumerate(self.rows):
            item = QtWidgets.QTableWidgetItem(row["group"])
            item.setIcon(_dot(row["color"]))
            t.setItem(r, 0, item)
            t.setItem(r, 1, QtWidgets.QTableWidgetItem(row["label"]))
            for c, (col, v) in enumerate(zip(self.cols, row["values"])):
                u = M.unit_label(col.window.key, self.page.unit_of(col.window.channel), col.window.unit)
                cell = QtWidgets.QTableWidgetItem(M.fmt(v, u))
                cell.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                if col.window.id == picked:
                    cell.setBackground(QtGui.QColor(47, 111, 176, 40))
                t.setItem(r, 2 + c, cell)
        t.resizeColumnsToContents()
        self._show_reach()
        self.empty_note.setVisible(not self.cols)
        self.table.setVisible(bool(self.cols))
        for c in self.page.canvas.every():
            if isinstance(c, ScatterChart):
                c.columns_changed()

    def _show_reach(self) -> None:
        """How the picked window carries over the trials: which have a value from it."""
        tool = self.page.measure
        mine = [k for k, c in enumerate(self.cols) if c.window.id == tool.selected]
        if not mine or self.rows_from.currentData() != "trial":
            self.reach.setText("" if not mine else "Set Rows to Each trial to see which trials this window reaches.")
            self.reach.setVisible(bool(mine))
            return
        miss = [r["label"] for r in self.rows if not any(np.isfinite(r["values"][k]) for k in mine)]
        n = len(self.rows)
        text = f"Value in {n - len(miss)} of {n} trials."
        if miss:
            text += " None in: " + ", ".join(miss[:6]) + (f" and {len(miss) - 6} more" if len(miss) > 6 else "")
        self.reach.setText(text)
        self.reach.setVisible(True)

    def text_rows(self) -> list[list[str]]:
        return M.to_csv_rows(self.cols, self.rows, self.page.unit_of)

    def copy(self) -> None:
        QtWidgets.QApplication.clipboard().setText("\n".join("\t".join(r) for r in self.text_rows()))

    def export(self, path: str | Path) -> Path:
        path = Path(path)
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(self.text_rows())
        return path

    def _export_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export the DataPad table", "datapad.csv", "CSV (*.csv)")
        if path:
            self.export(path)


class ScatterChart(Chart):
    kind = "Scatter"
    measure = ("shape", "scatter")
    y = "measurement"
    tip = "How does one measurement change with another (or with the stimulus)?"
    uses_units = False

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.plot = plot_widget()
        self.body.addWidget(self.plot, 1)
        self.x_box = QtWidgets.QComboBox()
        self.y_box = QtWidgets.QComboBox()
        self.field("x", "Across (x)", self.x_box)
        self.field("y", "Up (y)", self.y_box)
        self.fit_model = QtWidgets.QComboBox()
        self.fit_model.addItem("None", "")
        for key, label in FIT.MODELS.items():
            self.fit_model.addItem(label, key)
        self.field("fit", "Fit", self.fit_model)
        self.fit_degree = QtWidgets.QSpinBox(minimum=0, maximum=12, value=1)
        self.fit_degree.setToolTip("Polynomial degree: 1 a line, 2 a parabola, 3 a cubic…")
        self.field("degree", "Degree", self.fit_degree)
        self.fit_note = QtWidgets.QLabel()
        self.fit_note.setWordWrap(True)
        self.fit_note.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.form.addRow(self.fit_note)
        self.apply_theme(self._mode)
        self.columns_changed()

    def plots(self) -> list:
        return [self.plot]

    def _table(self):
        return self.page.canvas.first(DataPadTableChart.kind)

    def axes(self) -> list[str]:
        """What it plots across and up: the DataPad columns' headings (or "group")."""
        return [self.x_box.currentData() or "group", self.y_box.currentData() or ""]

    def columns_changed(self) -> None:
        t = self._table()
        labels = [c.label(self.page.unit_of) for c in (t.cols if t is not None else [])
                  if not (c.window.unit == M.SHAPE and c.window.on == "scatter")]   # (drawn on a scatter)
        self.fill(self.x_box, labels, ("Stimulus group", "group"))
        self.fill(self.y_box, labels, None, labels[0] if labels else None)
        self.refresh()

    def _values(self, t, key) -> tuple[np.ndarray, np.ndarray]:
        """(values, SDs) of a column, or the group number, for each of the table's rows."""
        if key == "group":
            names = list(dict.fromkeys(r["group"] for r in t.rows))
            return np.array([names.index(r["group"]) + 1 for r in t.rows], float), np.zeros(len(t.rows))
        labels = [c.label(self.page.unit_of) for c in t.cols]
        if key not in labels:
            return np.full(len(t.rows), np.nan), np.zeros(len(t.rows))
        j = labels.index(key)
        v = [r["values"][j] for r in t.rows]
        mean = np.array([x[0] if isinstance(x, tuple) else x for x in v], float)
        sd = np.array([x[1] if isinstance(x, tuple) else 0.0 for x in v], float)
        return mean, np.nan_to_num(sd)

    def draw(self) -> None:
        p = self.plot.getPlotItem()
        p.clear()
        t = self._table()
        self.fit_note.setText("")
        if t is None or not t.rows or not t.cols:
            p.setTitle("Scatter: measure with the Measure tool first, into a DataPad table", color=self._fg())
            return
        xk, yk = self.x_box.currentData() or "group", self.y_box.currentData()
        x, xs = self._values(t, xk)
        y, ys = self._values(t, yk)
        ok = np.isfinite(x) & np.isfinite(y)
        p.setTitle(f"{yk} against {self.x_box.currentText()}", color=self._fg())
        for r, row in enumerate(t.rows):
            if not ok[r]:
                continue
            color = QtGui.QColor(row["color"])
            p.addItem(pg.ScatterPlotItem([x[r]], [y[r]], size=9, brush=pg.mkBrush(color), pen=pg.mkPen(None)))
            if ys[r] or xs[r]:
                p.addItem(pg.ErrorBarItem(x=np.array([x[r]]), y=np.array([y[r]]), height=np.array([2 * ys[r]]),
                                          width=np.array([2 * xs[r]]), pen=pg.mkPen(color)))
        model = self.fit_model.currentData()
        self.fit_degree.setEnabled(model == "poly")
        if model and ok.sum() >= 2:
            try:
                fit = FIT.fit(model, x[ok], y[ok], self.fit_degree.value())
            except (RuntimeError, ValueError, TypeError) as e:
                self.fit_note.setText(f"Can't fit: {e}")
            else:
                xx = np.linspace(x[ok].min(), x[ok].max(), 200)
                p.plot(xx, fit.predict(xx), pen=pg.mkPen(self._fg(), width=1.5, style=QtCore.Qt.PenStyle.DashLine))
                self.fit_note.setText(f"{fit.equation}<br>R² = {fit.r2:.4g}, n = {fit.n}")
        p.setLabel("bottom", self.x_box.currentText())
        label_axis(p, "left", yk, "")


def _dot(color: str) -> QtGui.QIcon:
    pix = QtGui.QPixmap(10, 10)
    pix.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setBrush(QtGui.QColor(color))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.drawRoundedRect(0, 0, 10, 10, 3, 3)
    p.end()
    return QtGui.QIcon(pix)
