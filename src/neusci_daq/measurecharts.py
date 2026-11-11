"""Measuring on the Analysis whiteboard, in place of the DataPad tab: the Measure tool's
windows, drawn on the charts they were dragged across, and the two charts they fill, the
DataPad table and Scatter (the numbers are worked out in measure.py).

With the Measure tool in Time, drag across any chart against time to measure that stretch;
in Phase, across a chart against phase to measure the same part of every stimulus cycle.
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


class _Region(pg.LinearRegionItem):
    """A measure window on a plot: drag it or its edges; a click picks it."""

    def __init__(self, tool: "MeasureTool", win: M.Window, start: float, end: float, ghost: bool):
        color = QtGui.QColor(TIME_COLOR if win.unit == M.TIME else PHASE_COLOR)
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

    def add(self, unit: str, start: float, width: float, channel: str, key: str) -> M.Window:
        w = M.Window(self._next, M.next_name(self.windows, unit), unit, start, width, key, channel)
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
    def unit_of_chart(chart) -> str | None:
        return {"time": M.TIME, "phase": M.PHASE}.get(chart.link)

    def to_window(self, win: M.Window, x: float) -> float:
        """A plot's x (s or degrees) in the window's terms (the same: s or degrees)."""
        return float(x)

    def decorate_all(self) -> None:
        for c in self.page.canvas.every():
            self.decorate(c)

    def decorate(self, chart) -> None:
        """Draw the windows of this chart's kind on it, and let the Measure tool drag on it."""
        unit = self.unit_of_chart(chart)
        for item, plot in getattr(chart, "_measure_items", []):
            try:
                plot.removeItem(item)
            except (RuntimeError, ValueError):
                pass
        chart._measure_items = []
        if unit is None:
            return
        canvas = self.page.canvas
        listening = canvas.tool() == "measure" and canvas.measure_unit() == unit and \
            not self.page.repeats.btn_mark.isChecked()
        end = self._length(chart, unit)
        for k, pw in enumerate(chart.plots()):
            p = pw.getPlotItem()
            vb = p.getViewBox()
            if listening:
                vb.on_drag_select = lambda x0, x1, done, c=chart, k=k: self._dragged(c, k, x0, x1, done)
            elif getattr(vb, "on_drag_select", None) is not None and getattr(vb, "_measuring", False):
                vb.on_drag_select = None
            vb._measuring = listening
            for w in self.windows:
                if w.unit != unit:
                    continue
                for j, s in enumerate(w.starts(end)):
                    item = _Region(self, w, s, s + w.width, ghost=j > 0)
                    p.addItem(item)
                    chart._measure_items.append((item, p))

    def _length(self, chart, unit: str) -> float:
        if unit == M.PHASE:
            return 360.0
        blocks = [i for i in chart.blocks() if self.page.blocks[i] is not None]
        return max((self.page.blocks[i]["duration_s"] for i in blocks), default=0.0)

    def _dragged(self, chart, k: int, x0: float, x1: float, done: bool) -> None:
        """The Measure tool dragged across plot k of a chart: a new window when it's let go."""
        unit = self.unit_of_chart(chart)
        p = chart.plots()[k].getPlotItem()
        if self._drag is not None:
            try:
                self._drag["plot"].removeItem(self._drag["item"])
            except (RuntimeError, ValueError):
                pass
            self._drag = None
        if not done:
            color = QtGui.QColor(TIME_COLOR if unit == M.TIME else PHASE_COLOR)
            color.setAlpha(45)
            item = pg.LinearRegionItem((x0, x1), movable=False, brush=pg.mkBrush(color))
            p.addItem(item)
            self._drag = {"item": item, "plot": p}
            return
        if x1 - x0 <= 0:
            return
        if unit == M.PHASE:
            x0, x1 = max(0.0, x0), min(360.0, x1)
        channel = chart.measure_channel(k)
        key = "spikes" if chart.group == "Spikes" else ("mean" if unit == M.PHASE else "max")
        if self.page.canvas.first(DataPadTableChart.kind) is None:
            self.page.canvas.add(DataPadTableChart.kind)
        self.add(unit, x0, x1 - x0, channel, key)
        self.pick(self.selected)


class _WindowBox(QtWidgets.QGroupBox):
    """One window's settings, in the DataPad table's."""

    def __init__(self, chart: "DataPadTableChart", win: M.Window):
        super().__init__(f"{win.name} · {'time' if win.unit == M.TIME else 'phase'}")
        self.chart, self.wid = chart, win.id
        tool = chart.page.measure
        f = QtWidgets.QFormLayout(self)
        self.key = QtWidgets.QComboBox()
        keys = D.MEASUREMENTS if win.unit == M.TIME else {k: D.MEASUREMENTS[k] for k in M.PHASE_KEYS}
        for k in keys:
            self.key.addItem(M.name_of(k, win.unit), k)
            self.key.setItemData(self.key.count() - 1, D.MEASUREMENTS[k][2], QtCore.Qt.ItemDataRole.ToolTipRole)
        self.key.setCurrentIndex(max(0, self.key.findData(win.key)))
        self.key.currentIndexChanged.connect(lambda *_: tool.update(self.wid, key=self.key.currentData()))
        self.channel = QtWidgets.QComboBox()
        names = chart.page.names()
        self.channel.addItems(names)
        if win.channel not in names:
            self.channel.addItem(win.channel)
        self.channel.setCurrentText(win.channel)
        self.channel.currentTextChanged.connect(lambda v: tool.update(self.wid, channel=v))
        if win.unit == M.TIME:
            self.start = UnitSpinBox(0, 3600, "s", step=0.01)
            self.width = UnitSpinBox(0.0001, 3600, "s", step=0.01)
            self.repeat = UnitSpinBox(0, 3600, "s", step=0.01)
        else:
            self.start = UnitSpinBox(0, 359, "°", step=5)
            self.width = UnitSpinBox(1, 360, "°", step=5)
            self.repeat = UnitSpinBox(0, 359, "°", step=5)
        for box, v in ((self.start, win.start), (self.width, win.width), (self.repeat, win.repeat)):
            box.setValue(v)
        self.start.valueChanged.connect(lambda v: tool.update(self.wid, start=v))
        self.width.valueChanged.connect(lambda v: tool.update(self.wid, width=v))
        self.repeat.valueChanged.connect(lambda v: tool.update(self.wid, repeat=v))
        self.repeat.setSpecialValueText("once")
        self.repeat.setToolTip("Measure the same width again this far along, all along the "
                               + ("trial" if win.unit == M.TIME else "cycle") + ": a column each time")
        f.addRow("Measure", self.key)
        f.addRow("Channel", self.channel)
        f.addRow("Starts at", with_slider(self.start))
        f.addRow("Width", with_slider(self.width))
        f.addRow("Repeat every", with_slider(self.repeat))
        remove = QtWidgets.QPushButton("Remove this window")
        remove.clicked.connect(lambda: tool.remove(self.wid))
        f.addRow(remove)


class DataPadTableChart(Chart):
    kind = "DataPad table"
    group = "Measure"
    tip = "The Measure tool's numbers: a column for each window, a row for each trial or group"
    uses_units = False
    help = "datapad"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ContiguousSelection)
        self.empty_note = QtWidgets.QLabel("Pick the Measure tool and drag across a chart against time (in Time) "
                                           "or phase (in Phase): each window adds a column here.")
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
        self.rows_from.setToolTip("Each trial; each group's average trace, measured once; or each group's "
                                  "trials measured one by one, as mean ± SD (and n)")
        self.field("rows", "Rows", self.rows_from)
        self.windows_box = QtWidgets.QWidget()
        self._windows = QtWidgets.QVBoxLayout(self.windows_box)
        self._windows.setContentsMargins(0, 0, 0, 0)
        self._side.insertWidget(self._side.count() - 1, self.windows_box)
        page.measure.changed.connect(self._windows_changed)
        page.measure.picked.connect(lambda *_: self._fill_windows())
        self._fill_windows()
        self.cols: list[M.Column] = []
        self.rows: list[dict] = []

    def _windows_changed(self) -> None:
        if [b.wid for b in self._boxes()] != [w.id for w in self.page.measure.windows]:
            self._fill_windows()
        self.refresh()

    def _boxes(self) -> list[_WindowBox]:
        return [self._windows.itemAt(i).widget() for i in range(self._windows.count())
                if isinstance(self._windows.itemAt(i).widget(), _WindowBox)]

    def _fill_windows(self) -> None:
        while self._windows.count():
            w = self._windows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        tool = self.page.measure
        if not tool.windows:
            note = QtWidgets.QLabel("No windows yet: drag across a chart with the Measure tool.")
            note.setWordWrap(True)
            self._windows.addWidget(note)
        for w in tool.windows:
            box = _WindowBox(self, w)
            if w.id == tool.selected:
                box.setStyleSheet("QGroupBox { font-weight: 600; }")
            self._windows.addWidget(box)

    def draw(self) -> None:
        self.cols, self.rows = M.table(self.page, self.page.measure.windows, self.rows_from.currentData() or "trial",
                                       self.blocks(), self.plan)
        heads = ["Group", "n" if self.rows_from.currentData() == "spread" else
                 ("Trial" if self.rows_from.currentData() == "trial" else "")] + \
            [c.label(self.page.unit_of) for c in self.cols]
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
        self.empty_note.setVisible(not self.cols)
        self.table.setVisible(bool(self.cols))
        for c in self.page.canvas.every():
            if isinstance(c, ScatterChart):
                c.columns_changed()

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
    group = "Measure"
    tip = "One of the DataPad table's columns (or the stimulus) against another, with a fitted curve"
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

    def columns_changed(self) -> None:
        t = self._table()
        labels = [c.label(self.page.unit_of) for c in (t.cols if t is not None else [])]
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
