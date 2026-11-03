"""The DataPad sub-tab of Analysis: a table of measurements, like LabChart's DataPad.

Pick columns (a channel and a measurement), a time window (typed, or dragged on the
preview), and where the rows come from:
- the highlighted block, or each chosen block (the raw data);
- each stimulus group's average trace (measured once, on the average);
- each stimulus group: every block measured, then mean ± SD or SEM across them (n).
Rows are added below those already there; export them as CSV or copy them to paste
into a spreadsheet.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import datapad as D
from . import style, theme
from .plotzoom import TraceCurve, plot_widget
from .stimwindow import UnitSpinBox
from .units import best_prefix
from .viewkit import View, label_axis, style_plot

SOURCES = (("The highlighted block", "current"), ("Each chosen block", "blocks"),
           ("Each stimulus group's average trace", "avg"),
           ("Each stimulus group: mean ± spread of its blocks", "stats"))


def fmt(v: float, unit: str) -> str:
    """A value with its unit and a sensible prefix: 0.0374 V -> 37.4 mV; 12 V/s -> 12 V/s."""
    if v is None or not np.isfinite(v):
        return "—"
    head, sep, tail = unit.partition("/") if "/" in unit else unit.partition("·")
    if head[-1:] in ("V", "A") and len(head) <= 2:          # V, mV, µA…
        n, head = best_prefix(v, head)
        return f"{n:.4g} {head}{sep}{tail}"
    if unit == "s":
        n, u = best_prefix(v, "s", prefixes=("µ", "m", ""))
        return f"{n:.4g} {u}"
    return f"{v:.4g} {unit}".strip()


class DataPadView(View):
    def __init__(self, page):
        super().__init__(page)
        self.columns: list[tuple[str, str]] = []          # (channel, measurement key)
        self.rows: list[dict] = []                        # what's in the table, for export
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(12)

        side = QtWidgets.QWidget()
        side.setMaximumWidth(380)
        left = QtWidgets.QVBoxLayout(side)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)

        box = QtWidgets.QGroupBox("Columns: what to measure")
        v = QtWidgets.QVBoxLayout(box)
        pick = QtWidgets.QHBoxLayout()
        self.channel = QtWidgets.QComboBox()
        self.measurement = QtWidgets.QComboBox()
        for key, (label, _, tip) in D.MEASUREMENTS.items():
            self.measurement.addItem(label, key)
            self.measurement.setItemData(self.measurement.count() - 1, tip, QtCore.Qt.ItemDataRole.ToolTipRole)
        self.btn_add_col = QtWidgets.QPushButton("Add")
        pick.addWidget(self.channel, 1)
        pick.addWidget(self.measurement, 1)
        pick.addWidget(self.btn_add_col)
        v.addLayout(pick)
        self.col_list = QtWidgets.QListWidget()
        self.col_list.setMaximumHeight(130)
        self.col_list.setToolTip("The table's columns, in order")
        v.addWidget(self.col_list)
        self.btn_del_col = QtWidgets.QPushButton("Remove column")
        v.addWidget(self.btn_del_col, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        left.addWidget(box)

        box = QtWidgets.QGroupBox("Window: when, in each block")
        f = QtWidgets.QFormLayout(box)
        self.whole = QtWidgets.QCheckBox("The whole block")
        self.t0 = UnitSpinBox(0, 1e5, "s", step=0.01)
        self.t1 = UnitSpinBox(0, 1e5, "s", step=0.01)
        self.t0.setToolTip("From this time after the block's start (or drag the shaded region)")
        self.t1.setToolTip("To this time after the block's start")
        f.addRow(self.whole)
        f.addRow("From", self.t0)
        f.addRow("To", self.t1)
        left.addWidget(box)

        box = QtWidgets.QGroupBox("Rows")
        f = QtWidgets.QFormLayout(box)
        self.source = QtWidgets.QComboBox()
        for label, key in SOURCES:
            self.source.addItem(label, key)
        self.source.setToolTip("Blocks follow Blocks and Show (Raw Data tab and the top bar); the mean ± "
                               "spread rows use Spread at the top (SD or SEM; SD if None)")
        f.addRow("From", self.source)
        btns = QtWidgets.QHBoxLayout()
        self.btn_add_rows = QtWidgets.QPushButton("Add rows")
        self.btn_add_rows.setObjectName("start")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        btns.addWidget(self.btn_add_rows, 1)
        btns.addWidget(self.btn_clear)
        f.addRow(btns)
        left.addWidget(box)
        out = QtWidgets.QHBoxLayout()
        self.btn_csv = QtWidgets.QPushButton("Export CSV…")
        self.btn_copy = QtWidgets.QPushButton("Copy")
        self.btn_copy.setToolTip("Copy the table, to paste into a spreadsheet")
        out.addWidget(self.btn_csv)
        out.addWidget(self.btn_copy)
        left.addLayout(out)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        left.addStretch(1)
        outer.addWidget(side)

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.preview = plot_widget()
        p = self.preview.getPlotItem()
        p.setTitle("The highlighted block")
        p.setLabel("bottom", "time in block", units="s")
        p.showGrid(x=True, y=True, alpha=0.2)
        self.trace = TraceCurve()
        self.trace.mode = "peak"
        p.addItem(self.trace)
        self.region = pg.LinearRegionItem((0.0, 0.1))
        self.region.setZValue(10)
        self.region.setToolTip("The window measured: drag it, or its edges")
        p.addItem(self.region)
        right.addWidget(self.preview)
        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        right.addWidget(self.table)
        right.setSizes([260, 480])
        outer.addWidget(right, 1)

        self.btn_add_col.clicked.connect(lambda: self.add_column(self.channel.currentText(),
                                                                 self.measurement.currentData()))
        self.btn_del_col.clicked.connect(self._remove_column)
        self.channel.currentIndexChanged.connect(lambda *_: self.redraw())
        self.whole.toggled.connect(self._whole_toggled)
        for w in (self.t0, self.t1):
            w.valueChanged.connect(lambda *_: self._window_typed())
        self.region.sigRegionChangeFinished.connect(self._region_dragged)
        self.btn_add_rows.clicked.connect(lambda: self.add_rows())
        self.btn_clear.clicked.connect(self.clear_rows)
        self.btn_csv.clicked.connect(self._export_dialog)
        self.btn_copy.clicked.connect(self.copy)
        self.apply_theme(style.LIGHT)

    # -- set up -------------------------------------------------------------------------------
    def set_channels(self, names: list[str]) -> None:
        keep = self.channel.currentText()
        self.channel.blockSignals(True)
        self.channel.clear()
        self.channel.addItems(names)
        if keep in names:
            self.channel.setCurrentText(keep)
        self.channel.blockSignals(False)
        if not self.columns and names:            # something to start with
            for key in ("mean", "max", "min", "p2p"):
                self.add_column(names[0], key, refresh=False)
        self._fill_columns()
        self.redraw()

    def add_column(self, channel: str, key: str, refresh: bool = True) -> None:
        if channel and (channel, key) not in self.columns:
            self.columns.append((channel, key))
            if refresh:
                self._fill_columns()

    def _remove_column(self) -> None:
        r = self.col_list.currentRow()
        if 0 <= r < len(self.columns):
            del self.columns[r]
            self._fill_columns()

    def _col_title(self, c: tuple[str, str]) -> str:
        return f"{c[0]}: {D.MEASUREMENTS[c[1]][0]}"

    def _fill_columns(self) -> None:
        self.col_list.clear()
        self.col_list.addItems([self._col_title(c) for c in self.columns])
        self._show_table()

    # -- the window -----------------------------------------------------------------------------
    def bounds(self) -> tuple[float | None, float | None]:
        if self.whole.isChecked():
            return None, None
        a, b = sorted((self.t0.value(), self.t1.value()))
        return a, b

    def _whole_toggled(self, on: bool) -> None:
        self.t0.setEnabled(not on)
        self.t1.setEnabled(not on)
        self.region.setVisible(not on)

    def _window_typed(self) -> None:
        self.region.blockSignals(True)
        self.region.setRegion(sorted((self.t0.value(), self.t1.value())))
        self.region.blockSignals(False)

    def _region_dragged(self) -> None:
        a, b = self.region.getRegion()
        for w, v in ((self.t0, a), (self.t1, b)):
            w.blockSignals(True)
            w.setValue(max(0.0, v))
            w.blockSignals(False)

    # -- the preview ----------------------------------------------------------------------------
    def showEvent(self, ev):
        super().showEvent(ev)
        self.redraw()

    def redraw(self) -> None:
        page = self.page
        i, name = page.index(), self.channel.currentText()
        y = page.trace(i, name) if name else None
        p = self.preview.getPlotItem()
        if y is None:
            self.trace.setData(np.empty(0), np.empty(0))
            return
        fs = page.blocks[i]["sample_rate"]
        self.trace.setData(np.arange(len(y)) / fs, y)
        label_axis(p, "left", name, page.unit_of(name))
        p.setTitle(f"Block {i + 1}: drag the shaded window", color=self._fg())
        dur = len(y) / fs
        if self.t1.value() <= 0 or self.t0.value() >= dur:     # a first window: the middle fifth
            self.t0.setValue(0.4 * dur)
            self.t1.setValue(0.6 * dur)

    # -- rows -------------------------------------------------------------------------------------
    def _measure_block(self, i: int, spikes: bool) -> dict[tuple, float]:
        """Every column measured on block i, in the window."""
        page = self.page
        b = page.blocks[i]
        fs = b["sample_rate"]
        start, end = self.bounds()
        ev = page.raw.events
        st = ev.t[ev.block == i] if (ev is not None and spikes) else None
        out = {}
        for c in self.columns:
            y = page.trace(i, c[0])
            if y is None:
                out[c] = float("nan")
                continue
            t = np.arange(len(y)) / fs
            w = D.window(t, start, end)
            out[c] = D.measure(c[1], t[w], y[w], fs, st)
        return out

    def add_rows(self, source: str | None = None) -> int:
        """Measure and add rows (source: one of SOURCES' keys; default the one picked).
        Returns how many were added."""
        source = source or self.source.currentData()
        page, raw = self.page, self.page.raw
        if not self.columns:
            self._show_message("Add a column first: a channel and what to measure.", error=True)
            return 0
        start, end = self.bounds()
        span = "whole block" if start is None else f"{start:.4g}–{end:.4g} s"
        wants_spikes = any(k in D.SPIKE_KEYS for _, k in self.columns)
        note = ""
        if wants_spikes and page.raw.events is None:
            note = " Spike columns need action potentials: find them on the Raw Data tab first."
        new = []
        if source in ("current", "blocks"):
            blocks = [page.index()] if source == "current" else raw.chosen_or_none()
            for i in blocks:
                if 0 <= i < len(page.blocks) and page.blocks[i] is not None:
                    name = page._paths[i].stem if i < len(page._paths) else f"block {i + 1}"
                    g = page.group_of(i)
                    new.append({"row": name, "group": page.groups[g][0] if g is not None else "",
                                "window": span, "n": 1, "values": self._measure_block(i, wants_spikes),
                                "spread": {}})
        else:
            chosen = set(raw.chosen_or_none())
            groups = page.groups or [("All chosen blocks", sorted(chosen))]
            kind = page.spread() or "sd"
            for label, blocks in groups:
                blocks = [i for i in blocks if i in chosen]
                if not blocks:
                    continue
                if source == "stats":
                    per = [self._measure_block(i, wants_spikes) for i in blocks]
                    values, spread = {}, {}
                    for c in self.columns:
                        mean, sd, sem, n = A.mean_spread(np.array([[m[c]] for m in per]))
                        values[c] = float(mean[0])
                        spread[c] = float(sd[0] if kind == "sd" else sem[0])
                    new.append({"row": f"{label}: mean ± {kind.upper()}", "group": label, "window": span,
                                "n": len(blocks), "values": values, "spread": spread, "kind": kind})
                else:
                    values = {}
                    for c in self.columns:
                        traces = [page.trace(i, c[0]) for i in blocks]
                        traces = [y for y in traces if y is not None]
                        if not traces:
                            values[c] = float("nan")
                            continue
                        n = max(len(y) for y in traces)
                        Y = np.full((len(traces), n), np.nan)
                        for r, y in enumerate(traces):
                            Y[r, :len(y)] = y
                        mean = A.mean_spread(Y)[0]
                        fs = page.blocks[blocks[0]]["sample_rate"]
                        t = np.arange(n) / fs
                        w = D.window(t, start, end)
                        values[c] = D.measure(c[1], t[w], mean[w], fs, None)
                    new.append({"row": f"{label}: average", "group": label, "window": span,
                                "n": len(blocks), "values": values, "spread": {}})
        self.rows += new
        self._show_table()
        self._show_message(f"Added {len(new)} row(s).{note}" if new else
                           f"No blocks to measure: choose some on the Raw Data tab.{note}", error=not new)
        return len(new)

    def clear_rows(self) -> None:
        self.rows = []
        self._show_table()

    def _headers(self) -> list[str]:
        page = self.page
        return ["Row", "Group", "Window", "n"] + [
            f"{self._col_title(c)} ({D.unit_of(c[1], page.unit_of(c[0]))})".replace(" ()", "")
            for c in self.columns]

    def _show_table(self) -> None:
        heads = self._headers()
        self.table.clear()
        self.table.setColumnCount(len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            for col, text in enumerate((row["row"], row["group"], row["window"], str(row["n"]))):
                self.table.setItem(r, col, QtWidgets.QTableWidgetItem(text))
            for k, c in enumerate(self.columns, 4):
                unit = D.unit_of(c[1], self.page.unit_of(c[0]))
                v = row["values"].get(c)
                text = fmt(v, unit) if v is not None else ""
                if c in row["spread"] and np.isfinite(row["spread"][c]):
                    text += f" ± {fmt(row['spread'][c], unit)}"
                self.table.setItem(r, k, QtWidgets.QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    # -- out -------------------------------------------------------------------------------------
    def table_rows(self) -> list[list[str]]:
        """Header and rows as plain numbers (spread in its own column), for CSV and copying."""
        page = self.page
        head = ["row", "group", "window", "n"]
        for c in self.columns:
            unit = D.unit_of(c[1], page.unit_of(c[0]))
            title = f"{self._col_title(c)}" + (f" ({unit})" if unit else "")
            head += [title, f"{title} SD/SEM"]
        out = [head]
        for row in self.rows:
            line = [row["row"], row["group"], row["window"], str(row["n"])]
            for c in self.columns:
                v, s = row["values"].get(c), row["spread"].get(c)
                line += ["" if v is None or not np.isfinite(v) else f"{v:.6g}",
                         "" if s is None or not np.isfinite(s) else f"{s:.6g}"]
            out.append(line)
        return out

    def export(self, path: str | Path) -> Path:
        path = Path(path)
        with path.open("w", newline="") as f:
            csv.writer(f).writerows(self.table_rows())
        return path

    def copy(self) -> None:
        QtWidgets.QApplication.clipboard().setText("\n".join("\t".join(r) for r in self.table_rows()))
        self._show_message("Copied: paste it into a spreadsheet.")

    def _export_dialog(self) -> None:
        start = str(Path(self.main._start_dir()) / "datapad.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export DataPad", start, "CSV (*.csv)")
        if path:
            self.export(path)
            self._show_message(f"Saved {len(self.rows)} row(s) to {Path(path).name}.")

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        style_plot(self.preview, th)
        self.trace.setPen(pg.mkPen("#56B4E9", width=1.2))
        c = QtGui.QColor("#E69F00")
        c.setAlpha(50)
        self.region.setBrush(pg.mkBrush(c))
