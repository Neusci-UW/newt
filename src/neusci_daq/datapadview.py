"""The DataPad sub-tab of Analysis: a table of measurements, like LabChart's DataPad.

Pick columns (a channel and a measurement), a time window (typed, or dragged on the
preview), and where the rows come from:
- the highlighted block, or each chosen block (the raw data);
- each stimulus group's average trace (measured once, on the average);
- each stimulus group: every block measured, then mean ± SD or SEM across them (n).
The window can repeat (every 0.1 s, say) to measure a series of ranges across each
block. Rows are added below those already there; export them as CSV or copy them to
paste into a spreadsheet, or plot any column against another on Scatter.

The preview overlays any of the channels (tick them above it) with the window shaded.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import datapad as D
from . import fitting as FIT
from . import style, theme
from .plotzoom import MultiAxis, TraceCurve, plot_widget
from .stimwindow import UnitSpinBox
from .units import best_prefix
from .viewkit import View, label_axis, style_plot, unit_color

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
        self.every = UnitSpinBox(0, 1e5, "s", step=0.01)
        self.every.setToolTip("Measure the window again every this long, to the block's end: a row per "
                              "range (e.g. 0–50 ms, 100–150 ms, …). 0: once")
        f.addRow("Repeat every", self.every)
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
        self.btn_recompute = QtWidgets.QPushButton("Recompute")
        self.btn_recompute.setToolTip("Measure every row again, each in its own blocks and window, with "
                                      "the columns, Spread, spikes and calculations as they are now")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        btns.addWidget(self.btn_add_rows, 1)
        btns.addWidget(self.btn_recompute)
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
        top = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(4)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Show"))
        self.show_bar = QtWidgets.QHBoxLayout()
        bar.addLayout(self.show_bar)
        bar.addStretch(1)
        self.separate = QtWidgets.QCheckBox("Separate y axes")
        self.separate.setChecked(True)
        self.separate.setToolTip("Each channel on its own y axis (in its colour), scaled to fill the plot. "
                                 "Scroll or drag on an axis to scale just that channel. Off: one shared axis")
        self.separate.toggled.connect(lambda *_: self.redraw())
        bar.addWidget(self.separate)
        tv.addLayout(bar)
        self.show_boxes: dict[str, QtWidgets.QCheckBox] = {}
        self.traces: dict[str, TraceCurve] = {}
        self.preview = plot_widget()
        p = self.preview.getPlotItem()
        p.setTitle("The highlighted block")
        p.setLabel("bottom", "time in block", units="s")
        p.showGrid(x=True, y=True, alpha=0.2)
        self.legend = p.addLegend(offset=(10, 5))
        self.multi = MultiAxis(p)
        self.region = pg.LinearRegionItem((0.0, 0.1))
        self.region.setZValue(10)
        self.region.setToolTip("The window measured: drag it, or its edges")
        p.addItem(self.region)
        tv.addWidget(self.preview, 1)
        right.addWidget(top)
        self.out_tabs = QtWidgets.QTabWidget()
        self.out_tabs.setDocumentMode(True)
        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.out_tabs.addTab(self.table, "Table")
        sc = QtWidgets.QWidget()
        sv = QtWidgets.QVBoxLayout(sc)
        sv.setContentsMargins(0, 6, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.sx, self.sy = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        for w in (self.sx, self.sy):
            w.setMinimumContentsLength(18)
        row.addWidget(QtWidgets.QLabel("Y"))
        row.addWidget(self.sy)
        row.addWidget(QtWidgets.QLabel("against X"))
        row.addWidget(self.sx)
        row.addStretch(1)
        sv.addLayout(row)
        self.scatter = plot_widget()
        self.scatter.getPlotItem().showGrid(x=True, y=True, alpha=0.2)
        self.scatter_legend = self.scatter.getPlotItem().addLegend(offset=(10, 5))
        sv.addWidget(self.scatter, 1)
        self.out_tabs.addTab(sc, "Scatter")
        self.out_tabs.addTab(self._fit_panel(), "Fit")
        right.addWidget(self.out_tabs)
        right.setSizes([300, 440])
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
        self.btn_recompute.clicked.connect(lambda: self.recompute())
        self.btn_csv.clicked.connect(self._export_dialog)
        self.btn_copy.clicked.connect(self.copy)
        for w in (self.sx, self.sy):
            w.currentIndexChanged.connect(lambda *_: self.draw_scatter())
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
        self._fill_show(names)
        if not self.columns and names:            # something to start with
            for key in ("mean", "max", "min", "p2p"):
                self.add_column(names[0], key, refresh=False)
        self._fill_columns()
        self.redraw()

    def _fill_show(self, names: list[str]) -> None:
        """A tick box per channel, in its colour; ticked ones are overlaid on the preview."""
        was = {n for n, b in self.show_boxes.items() if b.isChecked()}
        for b in self.show_boxes.values():
            self.show_bar.removeWidget(b)
            b.deleteLater()
        self.show_boxes = {}
        self.multi.clear()
        self.traces = {}
        self.legend.clear()
        for k, n in enumerate(names):
            b = QtWidgets.QCheckBox(n)
            color = unit_color(k)
            b.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: 600; }}")
            b.setChecked(n in was if was & set(names) else k == 0)
            b.toggled.connect(lambda *_: self.redraw())
            self.show_bar.addWidget(b)
            self.show_boxes[n] = b
            c = TraceCurve()
            c.mode = "peak"
            c.setPen(pg.mkPen(color, width=1.2))
            self.traces[n] = c

    def shown(self) -> list[str]:
        return [n for n, b in self.show_boxes.items() if b.isChecked()]

    def set_shown(self, names: list[str]) -> None:
        for n, b in self.show_boxes.items():
            b.blockSignals(True)
            b.setChecked(n in names)
            b.blockSignals(False)
        self.redraw()

    def add_column(self, channel: str, key: str, refresh: bool = True) -> None:
        if channel and (channel, key) not in self.columns:
            self.columns.append((channel, key))
            if refresh:
                self._fill_columns()
                box = self.show_boxes.get(channel)
                if box is not None and not box.isChecked():
                    box.setChecked(True)               # see what's being measured

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
        self._fill_axes()
        self._show_table()

    # -- the window -----------------------------------------------------------------------------
    def bounds(self) -> tuple[float | None, float | None]:
        if self.whole.isChecked():
            return None, None
        a, b = sorted((self.t0.value(), self.t1.value()))
        return a, b

    def windows(self, duration: float) -> list[tuple[float | None, float | None]]:
        """The ranges measured in a block this long: the window, or with Repeat every,
        the window and its copies every so often while they fit."""
        a, b = self.bounds()
        step = self.every.value()
        if a is None or step <= 0:
            return [(a, b)]
        out, k = [], 0
        while a + k * step < duration and len(out) < 10000:
            out.append((a + k * step, min(b + k * step, duration)))
            k += 1
        return out or [(a, b)]

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
        i = page.index()
        p = self.preview.getPlotItem()
        self.legend.clear()
        self.multi.clear()
        self.multi.separate = self.separate.isChecked()
        dur, units = 0.0, []
        for name, c in self.traces.items():
            y = page.trace(i, name) if name in self.shown() else None
            if y is None:
                continue
            fs = page.blocks[i]["sample_rate"]
            c.setData(np.arange(len(y)) / fs, y)
            self.multi.add(c, name, page.unit_of(name), c.opts["pen"].color())
            self.legend.addItem(c, name)
            dur = max(dur, len(y) / fs)
            units.append(page.unit_of(name))
        if not units:
            p.setTitle("Tick a channel to show it", color=self._fg())
            return
        shown = self.shown()
        if not self.multi.separate:
            if len(set(units)) == 1:
                label_axis(p, "left", shown[0] if len(shown) == 1 else "", units[0])
            else:
                p.setLabel("left", "mixed units: " + ", ".join(dict.fromkeys(units)), units=None)
            p.getAxis("left").setPen(pg.mkPen(self._fg()))
            p.getAxis("left").setTextPen(pg.mkPen(self._fg()))
        p.enableAutoRange(axis="y")                       # every trace fits the height
        self.multi.fit()
        p.setTitle(f"Block {i + 1}: drag the shaded window", color=self._fg())
        if self.t1.value() <= 0 or self.t0.value() >= dur:     # a first window: the middle fifth
            self.t0.setValue(0.4 * dur)
            self.t1.setValue(0.6 * dur)

    # -- rows -------------------------------------------------------------------------------------
    def _measure_block(self, i: int, spikes: bool, start=None, end=None) -> dict[tuple, float]:
        """Every column measured on block i, from start to end (s; None: the block's ends)."""
        page = self.page
        b = page.blocks[i]
        fs = b["sample_rate"]
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
        recipes = []                     # how each row is made, so Recompute can make it again
        if source in ("current", "blocks"):
            blocks = [page.index()] if source == "current" else raw.chosen_or_none()
            for i in blocks:
                if 0 <= i < len(page.blocks) and page.blocks[i] is not None:
                    recipes += [{"kind": "block", "blocks": [i], "a": a, "b": b}
                                for a, b in self.windows(page.blocks[i]["duration_s"])]
        else:
            chosen = set(raw.chosen_or_none())
            for label, blocks in page.groups or [("All chosen blocks", sorted(chosen))]:
                blocks = [i for i in blocks if i in chosen and page.blocks[i] is not None]
                if blocks:
                    shortest = min(page.blocks[i]["duration_s"] for i in blocks)
                    recipes += [{"kind": source, "blocks": blocks, "label": label, "a": a, "b": b}
                                for a, b in self.windows(shortest)]
        new = [self._make_row(r) for r in recipes]
        self.rows += new
        self._show_table()
        note = self._spike_note()
        self._show_message(f"Added {len(new)} row(s).{note}" if new else
                           f"No blocks to measure: choose some on the Raw Data tab.{note}", error=not new)
        return len(new)

    def recompute(self) -> int:
        """Measure every row again, each in its own blocks and window, with the columns,
        Spread, spikes and calculated channels as they are now."""
        if not self.rows:
            self._show_message("Nothing to recompute: add rows first.", error=True)
            return 0
        n = len(self.page.blocks)
        n_rows = len(self.rows)
        keep = [r for r in self.rows if all(i < n for i in r["recipe"]["blocks"])]
        self.rows = [self._make_row(r["recipe"]) for r in keep]
        self._show_table()
        gone = len(keep) < n_rows
        self._show_message(f"Recomputed {len(self.rows)} row(s)." + (" Some blocks are gone; their rows "
                           "were dropped." if gone else "") + self._spike_note())
        return len(self.rows)

    def _spike_note(self) -> str:
        if any(k in D.SPIKE_KEYS for _, k in self.columns) and self.page.raw.events is None:
            return " Spike columns need action potentials: find them on the Raw Data tab first."
        return ""

    def _make_row(self, rc: dict) -> dict:
        """One table row from its recipe."""
        page = self.page
        a, b = rc["a"], rc["b"]
        span = "whole block" if a is None else f"{a:.4g}–{b:.4g} s"
        spikes = any(k in D.SPIKE_KEYS for _, k in self.columns)
        blocks = rc["blocks"]
        row = {"window": span, "t0": a or 0.0, "n": len(blocks), "spread": {}, "recipe": rc,
               "block": blocks[0] if rc["kind"] == "block" else None}
        if rc["kind"] == "block":
            i = blocks[0]
            g = page.group_of(i)
            row.update(row=page._paths[i].stem if i < len(page._paths) else f"block {i + 1}",
                       group=page.groups[g][0] if g is not None else "",
                       values=self._measure_block(i, spikes, a, b))
        elif rc["kind"] == "stats":
            kind = page.spread() or "sd"
            per = [self._measure_block(i, spikes, a, b) for i in blocks]
            values, spread = {}, {}
            for c in self.columns:
                mean, sd, sem, _ = A.mean_spread(np.array([[m[c]] for m in per]))
                values[c] = float(mean[0])
                spread[c] = float(sd[0] if kind == "sd" else sem[0])
            row.update(row=f"{rc['label']}: mean ± {kind.upper()}", group=rc["label"], values=values,
                       spread=spread, kind=kind)
        else:                                            # the average trace of the group
            fs = page.blocks[blocks[0]]["sample_rate"]
            values = {}
            for c in self.columns:
                mean = self._average(blocks, c[0])
                if mean is None:
                    values[c] = float("nan")
                    continue
                t = np.arange(len(mean)) / fs
                w = D.window(t, a, b)
                values[c] = D.measure(c[1], t[w], mean[w], fs, None)
            row.update(row=f"{rc['label']}: average", group=rc["label"], values=values)
        return row

    def _average(self, blocks: list[int], name: str) -> np.ndarray | None:
        traces = [y for y in (self.page.trace(i, name) for i in blocks) if y is not None]
        if not traces:
            return None
        Y = np.full((len(traces), max(len(y) for y in traces)), np.nan)
        for r, y in enumerate(traces):
            Y[r, :len(y)] = y
        return A.mean_spread(Y)[0]

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
        self.draw_scatter()

    # -- scatter ------------------------------------------------------------------------------------
    def _fill_axes(self) -> None:
        """X and Y choices: the row number, the block, the window's start, and every column."""
        choices = [("Row", "row"), ("Block number", "block"), ("Window start (s)", "t0")] + \
            [(self._col_title(c), c) for c in self.columns]
        for w, default in ((self.sx, "row"), (self.sy, self.columns[0] if self.columns else "row")):
            keep = w.currentData()
            w.blockSignals(True)
            w.clear()
            for label, key in choices:
                w.addItem(label, key)
            k = self._find(w, keep)
            w.setCurrentIndex(k if k >= 0 else max(0, self._find(w, default)))
            w.blockSignals(False)

    @staticmethod
    def _find(combo: QtWidgets.QComboBox, key) -> int:
        """findData for keys Qt can't compare (the (channel, measurement) tuples)."""
        return next((i for i in range(combo.count()) if combo.itemData(i) == key), -1)

    def set_axes(self, x, y) -> None:
        """Scatter y against x: "row", "block", "t0" or a column (channel, measurement)."""
        for w, key in ((self.sx, x), (self.sy, y)):
            w.setCurrentIndex(self._find(w, key))

    def _axis_values(self, key) -> tuple[np.ndarray, np.ndarray, str, str]:
        """(values, spread, axis title, unit) over the rows, for a scatter axis."""
        nan = float("nan")
        if key == "row":
            return np.arange(1.0, len(self.rows) + 1), np.full(len(self.rows), nan), "Row", ""
        if key == "block":
            v = [r["block"] + 1 if r.get("block") is not None else nan for r in self.rows]
            return np.array(v, float), np.full(len(v), nan), "Block", ""
        if key == "t0":
            return np.array([r.get("t0", 0.0) for r in self.rows], float), np.full(len(self.rows), nan), \
                "Window start", "s"
        v = np.array([r["values"].get(key, nan) for r in self.rows], float)
        s = np.array([r["spread"].get(key, nan) for r in self.rows], float)
        return v, s, self._col_title(key), D.unit_of(key[1], self.page.unit_of(key[0]))

    def draw_scatter(self) -> None:
        p = self.scatter.getPlotItem()
        p.clear()
        self.scatter_legend.clear()
        kx, ky = self.sx.currentData(), self.sy.currentData()
        if kx is None or ky is None or not self.rows:
            p.setTitle("Add rows, then pick what to plot", color=self._fg())
            return
        x, xs, xt, xu = self._axis_values(kx)
        y, ys, yt, yu = self._axis_values(ky)
        label_axis(p, "bottom", xt, xu)
        label_axis(p, "left", yt, yu)
        p.setTitle(f"{yt} against {xt}", color=self._fg())
        names = [g for g, _ in self.page.groups]
        groups = list(dict.fromkeys(r["group"] for r in self.rows))
        for g in groups:
            idx = np.array([k for k, r in enumerate(self.rows) if r["group"] == g])
            ok = idx[np.isfinite(x[idx]) & np.isfinite(y[idx])]
            if not len(ok):
                continue
            color = self.page.group_color(names.index(g)) if g in names else "#888888"
            if np.isfinite(ys[ok]).any() or np.isfinite(xs[ok]).any():
                p.addItem(pg.ErrorBarItem(x=x[ok], y=y[ok], height=2 * np.nan_to_num(ys[ok]),
                                          width=2 * np.nan_to_num(xs[ok]), beam=0,
                                          pen=pg.mkPen(color, width=1)))
            p.addItem(pg.ScatterPlotItem(x[ok], y[ok], size=8, pen=pg.mkPen(None), brush=pg.mkBrush(color),
                                         name=(g or "No group") if len(groups) <= 8 else None))
        if self.fit_result is not None and self.fit_on.currentData() == "scatter":
            self.run_fit(quiet=True)                     # keep the fit on the new points

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

    # -- fitting -------------------------------------------------------------------------------------
    def _fit_panel(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 6, 0, 0)
        form = QtWidgets.QFormLayout()
        self.fit_on = QtWidgets.QComboBox()
        self.fit_on.addItem("The scatter points", "scatter")
        self.fit_on.addItem("The trace in the window", "trace")
        self.fit_on.setToolTip("Scatter: Y against X on the Scatter tab. Trace: the first channel shown on "
                               "the preview, in the window (e.g. an exponential for a time constant)")
        self.fit_model = QtWidgets.QComboBox()
        for key, label in FIT.MODELS.items():
            self.fit_model.addItem(label, key)
        self.fit_degree = QtWidgets.QSpinBox(minimum=0, maximum=12, value=1)
        self.fit_degree.setToolTip("Polynomial degree: 1 a line, 2 a parabola, 3 a cubic…")
        form.addRow("Fit", self.fit_on)
        form.addRow("Model", self.fit_model)
        form.addRow("Degree", self.fit_degree)
        btns = QtWidgets.QHBoxLayout()
        self.btn_fit = QtWidgets.QPushButton("Fit")
        self.btn_fit.setObjectName("start")
        self.btn_fit_clear = QtWidgets.QPushButton("Remove fit")
        self.btn_fit_copy = QtWidgets.QPushButton("Copy")
        self.btn_fit_copy.setToolTip("Copy the equation and parameters")
        for b in (self.btn_fit, self.btn_fit_clear, self.btn_fit_copy):
            btns.addWidget(b)
        form.addRow(btns)
        lay.addLayout(form)
        right = QtWidgets.QVBoxLayout()
        self.fit_eq = QtWidgets.QLabel("Pick a model and press Fit.")
        self.fit_eq.setWordWrap(True)
        self.fit_eq.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.fit_params = QtWidgets.QTableWidget(0, 3)
        self.fit_params.setHorizontalHeaderLabels(["Parameter", "Value", "± SE"])
        self.fit_params.verticalHeader().setVisible(False)
        self.fit_params.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        right.addWidget(self.fit_eq)
        right.addWidget(self.fit_params, 1)
        lay.addLayout(right, 1)
        self.fit_result: FIT.Fit | None = None
        self._fit_items: list = []
        self.fit_model.currentIndexChanged.connect(
            lambda *_: self.fit_degree.setEnabled(self.fit_model.currentData() == "poly"))
        self.btn_fit.clicked.connect(lambda: self.run_fit())
        self.btn_fit_clear.clicked.connect(self.clear_fit)
        self.btn_fit_copy.clicked.connect(self.copy_fit)
        self._fitting = False
        return w

    def _fit_data(self):
        """(x, y, plot, x unit, y unit) for what's being fitted."""
        if self.fit_on.currentData() == "scatter":
            if not self.rows or self.sx.currentData() is None:
                raise ValueError("Add rows and pick Y and X on the Scatter tab first")
            x, _, _, xu = self._axis_values(self.sx.currentData())
            y, _, _, yu = self._axis_values(self.sy.currentData())
            return x, y, self.scatter.getPlotItem(), xu, yu
        shown = self.shown()
        i = self.page.index()
        y = self.page.trace(i, shown[0]) if shown else None
        if y is None:
            raise ValueError("Tick a channel to show on the preview first")
        fs = self.page.blocks[i]["sample_rate"]
        t = np.arange(len(y)) / fs
        w = D.window(t, *self.bounds())
        step = max(1, (w.stop - w.start) // 20000)          # plenty of points, still quick
        return t[w][::step], y[w][::step], self.preview.getPlotItem(), "s", self.page.unit_of(shown[0])

    def run_fit(self, quiet: bool = False) -> FIT.Fit | None:
        """Fit the chosen model; draw it, and show the equation and parameters."""
        if self._fitting:
            return self.fit_result
        self._fitting = True
        try:
            self._remove_fit_items()
            x, y, plot, xu, yu = self._fit_data()
            r = FIT.fit(self.fit_model.currentData(), x, y, self.fit_degree.value())
        except ValueError as e:
            self.fit_result = None
            if not quiet:
                self.fit_eq.setText(f"<span style='color:#e0524f'>{e}</span>")
            return None
        finally:
            self._fitting = False
        ok = np.isfinite(x) & np.isfinite(y)
        xx = np.linspace(x[ok].min(), x[ok].max(), 400)
        item = pg.PlotDataItem(xx, r.predict(xx), pen=pg.mkPen("#D55E00", width=2,
                                                               style=QtCore.Qt.PenStyle.DashLine))
        item.setZValue(20)
        plot.addItem(item)
        self._fit_items = [(plot, item)]
        self.fit_result = r
        where = "scatter" if self.fit_on.currentData() == "scatter" else "trace"
        self.fit_eq.setText(f"<b>{r.equation}</b><br>R² = {r.r2:.4f} · n = {r.n} · on the {where} "
                            f"(x in {xu or 'its units'}, y in {yu or 'its units'})")
        self.fit_params.setRowCount(len(r.names))
        for k, (name, v, e) in enumerate(r.table()):
            for col, text in enumerate((name, f"{v:.6g}", "" if not np.isfinite(e) else f"{e:.3g}")):
                self.fit_params.setItem(k, col, QtWidgets.QTableWidgetItem(text))
        self.fit_params.resizeColumnsToContents()
        return r

    def _remove_fit_items(self) -> None:
        for plot, item in self._fit_items:
            if item.scene() is not None:
                plot.removeItem(item)
        self._fit_items = []

    def clear_fit(self) -> None:
        self._remove_fit_items()
        self.fit_result = None
        self.fit_params.setRowCount(0)
        self.fit_eq.setText("Pick a model and press Fit.")

    def copy_fit(self) -> None:
        r = self.fit_result
        if r is None:
            return
        lines = [r.equation, f"R²\t{r.r2:.6g}", f"n\t{r.n}", "parameter\tvalue\tSE"]
        lines += [f"{n}\t{v:.6g}\t{e:.6g}" for n, v, e in r.table()]
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        style_plot(self.preview, th)
        style_plot(self.scatter, th)
        self.redraw()                                    # re-colour the channel axes
        self.draw_scatter()
        c = QtGui.QColor("#E69F00")
        c.setAlpha(50)
        self.region.setBrush(pg.mkBrush(c))
