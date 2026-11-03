"""The Analysis tab: every block of the session at once, and the action potentials
in them.

- Overlay: the chosen blocks on top of each other (each from its own start), their
  average, and one highlighted block (◀ ▶ to step through them).
- Find action potentials: threshold events, sorted into units by shape (see
  analysis.py). They're marked on the highlighted block, shown for every block in
  the raster, and each unit's waveforms are overlaid with their mean.

The blocks are the ones being reviewed, or those recorded so far in live mode.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import config as cfgmod
from . import session
from . import style, theme
from .plotzoom import OverlayCurve, TraceCurve, plot_widget
from .stimwindow import UnitSpinBox

_SHOW = (("Blocks and average", "both"), ("Blocks", "blocks"), ("Average", "average"))
MAX_WAVES = 150                    # per unit in the waveform plot


def _unit_color(u: int) -> str:
    return cfgmod.PALETTE[u % len(cfgmod.PALETTE)]


class AnalysisPage(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self._cache: dict[Path, tuple[float, dict]] = {}
        self._paths: list[Path] = []
        self.blocks: list[dict] = []           # read blocks, in session order
        self.events: A.Events | None = None
        self._dirty = True
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(16)

        # ---- left: settings ------------------------------------------------------
        side = QtWidgets.QWidget()
        side.setMaximumWidth(380)
        left = QtWidgets.QVBoxLayout(side)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        self.source = QtWidgets.QLabel()
        self.source.setWordWrap(True)
        left.addWidget(self.source)

        box = QtWidgets.QGroupBox("Blocks")
        f = QtWidgets.QFormLayout(box)
        self.channel = QtWidgets.QComboBox()
        self.which = QtWidgets.QLineEdit(placeholderText="all   (or e.g. 1-20, 35, 100-)")
        self.which.setToolTip("Which blocks to overlay and search: all, or numbers and ranges")
        self.show_what = QtWidgets.QComboBox()
        for label, key in _SHOW:
            self.show_what.addItem(label, key)
        nav = QtWidgets.QHBoxLayout()
        self.btn_prev = QtWidgets.QToolButton(text="◀", autoRaise=True)
        self.current = QtWidgets.QSpinBox(minimum=1, maximum=1)
        self.current.setToolTip("The block drawn on top, with its events marked")
        self.n_label = QtWidgets.QLabel()
        self.btn_next = QtWidgets.QToolButton(text="▶", autoRaise=True)
        for w in (self.btn_prev, self.current, self.n_label, self.btn_next):
            nav.addWidget(w)
        nav.addStretch(1)
        f.addRow("Channel", self.channel)
        f.addRow("Blocks", self.which)
        f.addRow("Show", self.show_what)
        f.addRow("Highlight", nav)
        left.addWidget(box)

        box = QtWidgets.QGroupBox("Find action potentials")
        f = QtWidgets.QFormLayout(box)
        thr = QtWidgets.QHBoxLayout()
        self.threshold = QtWidgets.QDoubleSpinBox(minimum=0.0, maximum=1e6, decimals=4, value=5.0, singleStep=0.5)
        self.threshold.setKeyboardTracking(False)
        self.thr_kind = QtWidgets.QComboBox()
        self.thr_kind.addItem("× noise SD", False)
        self.thr_kind.addItem("V from baseline", True)
        self.thr_kind.setToolTip("A number of noise SDs adapts to each recording; a fixed level is what "
                                 "you drag the threshold line to")
        thr.addWidget(self.threshold, 1)
        thr.addWidget(self.thr_kind)
        self.polarity = QtWidgets.QComboBox()
        for label, key in (("Negative (extracellular)", "negative"), ("Positive (intracellular)", "positive"),
                           ("Either", "either")):
            self.polarity.addItem(label, key)
        self.dead = UnitSpinBox(0.1, 100, "ms", step=0.5)
        self.dead.setValue(1.0)
        self.dead.setToolTip("After an event, no other can start for this long")
        self.pre = UnitSpinBox(0.1, 50, "ms", step=0.5)
        self.pre.setValue(1.0)
        self.post = UnitSpinBox(0.1, 50, "ms", step=0.5)
        self.post.setValue(2.0)
        self.units = QtWidgets.QComboBox()
        self.units.addItem("Auto", None)
        for k in range(1, 9):
            self.units.addItem(str(k), k)
        self.units.setToolTip("How many units to sort the events into. Auto picks however many "
                              "separate clearly by shape")
        f.addRow("Threshold", thr)
        f.addRow("Direction", self.polarity)
        f.addRow("Dead time", self.dead)
        f.addRow("Waveform before", self.pre)
        f.addRow("Waveform after", self.post)
        f.addRow("Units", self.units)
        self.btn_find = QtWidgets.QPushButton("Find action potentials")
        self.btn_find.setObjectName("start")
        f.addRow(self.btn_find)
        left.addWidget(box)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Unit", "Count", "Rate", "Amplitude", "Width"])
        self.table.horizontalHeaderItem(4).setToolTip("Mean waveform: trough to peak")
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        left.addWidget(self.table, 1)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        self.btn_export = QtWidgets.QPushButton("Export events…")
        self.btn_export.setToolTip("Save every event (block, time, unit, amplitude) as a CSV file")
        self.btn_export.setEnabled(False)
        left.addWidget(self.btn_export)
        outer.addWidget(side)

        # ---- right: plots ------------------------------------------------------------
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.overlay = plot_widget()
        self.raster = plot_widget()
        self.wave_plot = plot_widget()
        for pw, title, left_label, bottom in ((self.overlay, "Blocks overlaid", "", "time in block"),
                                              (self.raster, "Events in every block", "block", "time in block"),
                                              (self.wave_plot, "Waveforms by unit", "", "ms from the peak")):
            p = pw.getPlotItem()
            p.setTitle(title)
            p.setLabel("left", left_label)
            # seconds with a prefix as you zoom (ms, µs); the waveform axis is already ms
            p.setLabel("bottom", bottom, units="s" if bottom == "time in block" else None)
            p.showGrid(x=True, y=True, alpha=0.2)
        self.raster.setXLink(self.overlay)
        self.raster.getPlotItem().invertY(True)                  # block 1 at the top
        split.addWidget(self.overlay)
        split.addWidget(self.raster)
        split.addWidget(self.wave_plot)
        split.setSizes([400, 250, 250])
        outer.addWidget(split, 1)

        op = self.overlay.getPlotItem()
        self.blocks_item = OverlayCurve()
        self.avg_item = TraceCurve()
        self.cur_item = TraceCurve()
        for item, z in ((self.blocks_item, 0), (self.avg_item, 5), (self.cur_item, 10)):
            item.setZValue(z)
            op.addItem(item)
        # Spikes are the point here: always draw min and max, whatever View says,
        # so they keep their height (and line up with the event marks).
        self.blocks_item.mode = self.cur_item.mode = "peak"
        self.marks = pg.ScatterPlotItem(size=9, pen=None)
        self.marks.setZValue(20)
        op.addItem(self.marks)
        self.thr_line = pg.InfiniteLine(angle=0, movable=True)
        self.thr_line.setZValue(30)
        self.thr_line.setToolTip("Threshold: drag to set a fixed level")
        op.addItem(self.thr_line)
        self.raster_dots = pg.ScatterPlotItem(size=4, pen=None)
        self.raster.getPlotItem().addItem(self.raster_dots)

        # ---- signals -----------------------------------------------------------------
        self.channel.currentIndexChanged.connect(lambda *_: self._settings_changed())
        self.which.editingFinished.connect(self._settings_changed)
        self.show_what.currentIndexChanged.connect(lambda *_: self._draw_overlay())
        self.current.valueChanged.connect(lambda *_: self._draw_current())
        self.btn_prev.clicked.connect(lambda: self.current.setValue(self.current.value() - 1))
        self.btn_next.clicked.connect(lambda: self.current.setValue(self.current.value() + 1))
        self.btn_find.clicked.connect(self.find)
        self.btn_export.clicked.connect(self._export_dialog)
        self.thr_line.sigPositionChangeFinished.connect(self._threshold_dragged)
        for w in (self.threshold, self.dead):
            w.valueChanged.connect(lambda *_: self._draw_threshold())
        self.thr_kind.currentIndexChanged.connect(lambda *_: self._draw_threshold())
        self.polarity.currentIndexChanged.connect(lambda *_: self._draw_threshold())
        self.apply_theme(style.LIGHT)

    # -- the blocks ------------------------------------------------------------------------
    def source_changed(self) -> None:
        """The session or its blocks changed: reload when next shown."""
        self._dirty = True
        if self.isVisible():
            self.refresh()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._dirty or self._paths != self.main.analysis_blocks():
            self.refresh()

    def _read(self, path: Path) -> dict:
        mtime = path.stat().st_mtime
        hit = self._cache.get(path)
        if hit is None or hit[0] != mtime:
            hit = (mtime, session.read_block(path))
            self._cache[path] = hit
        return hit[1]

    def refresh(self) -> None:
        self._dirty = False
        self._paths = list(self.main.analysis_blocks())
        self.blocks = []
        for p in self._paths:
            try:
                self.blocks.append(self._read(p))
            except Exception:
                self.blocks.append(None)
        good = [b for b in self.blocks if b is not None]
        n = len(self.blocks)
        if not good:
            self.source.setText("No blocks yet. Record some, or open a session or .mat file (File menu).")
        else:
            durs = [b["duration_s"] for b in good]
            src = self.main.review_dir.name if self.main.review_dir else "this session"
            lo, hi = f"{min(durs):.3g}", f"{max(durs):.3g}"
            self.source.setText(f"<b>{src}</b>: {n} block(s), {lo if lo == hi else lo + '–' + hi} s each, "
                                f"{good[0]['sample_rate']:,.0f} Hz")
        names = good[0]["channel_names"] if good else []
        keep = self.channel.currentText()
        self.channel.blockSignals(True)
        self.channel.clear()
        self.channel.addItems(names)
        if keep in names:
            self.channel.setCurrentText(keep)
        self.channel.blockSignals(False)
        self.current.blockSignals(True)
        self.current.setMaximum(max(1, n))
        self.current.blockSignals(False)
        self.n_label.setText(f"of {n}")
        self._settings_changed()

    def _chosen(self) -> list[int]:
        """Indices of the chosen blocks that have this channel at the first block's rate."""
        idx = A.parse_blocks(self.which.text(), len(self.blocks))
        name = self.channel.currentText()
        good = [b for b in self.blocks if b is not None]
        fs = good[0]["sample_rate"] if good else 0
        return [i for i in idx if self.blocks[i] is not None and name in self.blocks[i]["channel_names"]
                and self.blocks[i]["sample_rate"] == fs]

    def _trace(self, i: int) -> np.ndarray:
        b = self.blocks[i]
        return b["data"][b["channel_names"].index(self.channel.currentText())]

    # -- drawing -----------------------------------------------------------------------------
    def _settings_changed(self) -> None:
        self.events = None
        self._show_results()
        self._draw_overlay()

    def _draw_overlay(self) -> None:
        self._show_message("")
        try:
            chosen = self._chosen() if self.blocks else []
        except ValueError as e:
            self._show_message(str(e), error=True)
            chosen = []
        show = self.show_what.currentData()
        if chosen:
            fs = self.blocks[chosen[0]]["sample_rate"]
            n = max(len(self._trace(i)) for i in chosen)
            Y = np.full((len(chosen), n), np.nan)
            for r, i in enumerate(chosen):
                y = self._trace(i)
                Y[r, :len(y)] = y
            self.blocks_item.set_traces(Y if show != "average" else Y[:0], fs)
            with np.errstate(all="ignore"):
                avg = np.nanmean(Y, 0)
            self.avg_item.setData(np.arange(n) / fs, avg if show != "blocks" else avg[:0])
            self.overlay.getPlotItem().setXRange(0, n / fs, padding=0.01)
            unit = self.blocks[chosen[0]]["units"][self.blocks[chosen[0]]["channel_names"].index(self.channel.currentText())]
            ax = self.overlay.getPlotItem().getAxis("left")
            if unit in ("V", "A"):                         # pyqtgraph picks mV, µV...
                self.overlay.getPlotItem().setLabel("left", self.channel.currentText(), units=unit)
                ax.enableAutoSIPrefix(True)
            else:
                self.overlay.getPlotItem().setLabel("left", f"{self.channel.currentText()} ({unit})")
                ax.enableAutoSIPrefix(False)
            self.raster.getPlotItem().setYRange(0.5, len(self.blocks) + 0.5, padding=0)
        else:
            self.blocks_item.set_traces(np.empty((0, 0)), 1.0)
            self.avg_item.setData(np.empty(0), np.empty(0))
        self.overlay.getPlotItem().setTitle(f"{len(chosen)} block(s) overlaid", color=self._fg())
        self._draw_current()

    def _draw_current(self) -> None:
        i = self.current.value() - 1
        if not (0 <= i < len(self.blocks)) or self.blocks[i] is None \
                or self.channel.currentText() not in self.blocks[i]["channel_names"]:
            self.cur_item.setData(np.empty(0), np.empty(0))
            self.marks.setData([], [])
            return
        y = self._trace(i)
        fs = self.blocks[i]["sample_rate"]
        self.cur_item.setData(np.arange(len(y)) / fs, y)
        ev = self.events
        if ev is not None and len(ev.t):
            m = ev.block == i
            idx = np.clip(np.round(ev.t[m] * fs).astype(int), 0, len(y) - 1)
            self.marks.setData(ev.t[m], y[idx], brush=[pg.mkBrush(_unit_color(u)) for u in ev.unit[m]])
        else:
            self.marks.setData([], [])
        self._draw_threshold()

    def _baseline(self) -> float:
        i = self.current.value() - 1
        if 0 <= i < len(self.blocks) and self.blocks[i] is not None \
                and self.channel.currentText() in self.blocks[i]["channel_names"]:
            return float(np.median(self._trace(i)))
        return 0.0

    def _level(self) -> float | None:
        """The threshold in trace units from the baseline, as Find would use it."""
        if self.events is not None:
            return self.events.level
        if self.thr_kind.currentData():
            return self.threshold.value()
        i = self.current.value() - 1
        if 0 <= i < len(self.blocks) and self.blocks[i] is not None \
                and self.channel.currentText() in self.blocks[i]["channel_names"]:
            return self.threshold.value() * A.noise_sd(self._trace(i))
        return None

    def _draw_threshold(self) -> None:
        level = self._level()
        if level is None:
            self.thr_line.hide()
            return
        sign = 1 if self.polarity.currentData() == "positive" else -1
        self.thr_line.blockSignals(True)
        self.thr_line.setValue(self._baseline() + sign * level)
        self.thr_line.blockSignals(False)
        self.thr_line.show()

    def _threshold_dragged(self) -> None:
        level = abs(self.thr_line.value() - self._baseline())
        self.thr_kind.blockSignals(True)
        self.thr_kind.setCurrentIndex(1)            # a fixed level
        self.thr_kind.blockSignals(False)
        self.threshold.blockSignals(True)
        self.threshold.setValue(level)
        self.threshold.blockSignals(False)
        if self.polarity.currentData() != "either" and \
                (self.thr_line.value() > self._baseline()) != (self.polarity.currentData() == "positive"):
            self.polarity.setCurrentIndex(self.polarity.findData(
                "positive" if self.thr_line.value() > self._baseline() else "negative"))
        self.events = None
        self._show_results()
        self._show_message(f"Threshold set to {level:.4g} from the baseline. Press Find to search.")

    # -- finding -----------------------------------------------------------------------------
    def find(self) -> A.Events | None:
        try:
            chosen = self._chosen()
            if not chosen:
                raise ValueError("No blocks to search: record or open some, and check Blocks and Channel.")
            self.main.statusBar().showMessage(f"Searching {len(chosen)} block(s)…")
            QtWidgets.QApplication.processEvents()
            ev = A.find_events([(i, self._trace(i), self.blocks[i]["sample_rate"]) for i in chosen],
                               threshold=self.threshold.value(), fixed=bool(self.thr_kind.currentData()),
                               polarity=self.polarity.currentData(), dead_ms=self.dead.value(),
                               pre_ms=self.pre.value(), post_ms=self.post.value(),
                               n_units=self.units.currentData())
        except ValueError as e:
            self._show_message(str(e), error=True)
            return None
        finally:
            self.main.statusBar().clearMessage()
        self.events = ev
        self._show_results()
        self._draw_current()
        return ev

    def _show_results(self) -> None:
        ev = self.events
        self.table.setRowCount(0)
        rp, wp = self.raster.getPlotItem(), self.wave_plot.getPlotItem()
        for item in list(wp.listDataItems()):
            wp.removeItem(item)
        self.btn_export.setEnabled(ev is not None and len(ev.t) > 0)
        if ev is None:
            self.raster_dots.setData([], [])
            return
        self.raster_dots.setData(ev.t, ev.block + 1, brush=[pg.mkBrush(_unit_color(u)) for u in ev.unit])
        rng = np.random.default_rng(0)
        for s in ev.summary():
            u = s["unit"] - 1
            color = QtGui.QColor(_unit_color(u))
            r = self.table.rowCount()
            self.table.insertRow(r)
            unit_item = QtWidgets.QTableWidgetItem(f"Unit {s['unit']}")
            unit_item.setIcon(style.dot_icon(_unit_color(u)))
            self.table.setItem(r, 0, unit_item)
            for c, text in enumerate((f"{s['count']}", f"{s['rate']:.2f} /s", f"{s['amplitude']:.3g}",
                                      f"{s['width_ms']:.2f} ms"), 1):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
            w = ev.waves[ev.unit == u]
            pick = rng.choice(len(w), min(MAX_WAVES, len(w)), replace=False)
            faint = QtGui.QColor(color)
            faint.setAlpha(40)
            if len(pick):
                xs = np.tile(np.append(ev.wave_t_ms, ev.wave_t_ms[-1]), len(pick))
                ys = np.column_stack([w[pick], np.full(len(pick), np.nan)]).ravel()
                wp.plot(xs, ys, pen=pg.mkPen(faint, width=1), connect="finite")
            wp.plot(ev.wave_t_ms, w.mean(0), pen=pg.mkPen(color, width=3))
        n = len(ev.t)
        msg = (f"{n} event(s) in {len(np.unique(ev.block))} block(s), {ev.n_units} unit(s); threshold "
               f"{ev.level:.3g} from the baseline." if n else "No events past the threshold: try a lower one.")
        self._show_message(" ".join([msg] + ev.notes))

    def export(self, path: str | Path) -> Path:
        ev = self.events
        path = Path(path)
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["block", "time_s", "unit", "amplitude", "channel"])
            for b, t, u, a in zip(ev.block, ev.t, ev.unit, ev.amplitude):
                w.writerow([self._paths[b].stem, f"{t:.6f}", int(u) + 1, f"{a:.6g}", self.channel.currentText()])
        return path

    def _export_dialog(self) -> None:
        start = str(Path(self.main._start_dir()) / "events.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export events", start, "CSV (*.csv)")
        if path:
            self.export(path)
            self._show_message(f"Saved {len(self.events.t)} event(s) to {Path(path).name}.")

    # -- looks ---------------------------------------------------------------------------------
    def _fg(self):
        return theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT].foreground

    def _show_message(self, text: str, error: bool = False) -> None:
        t = style.tokens(self._mode)
        self.message.setStyleSheet(f"color: {t['error'] if error else t['muted']}")
        self.message.setText(text)

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        for pw in (self.overlay, self.raster, self.wave_plot):
            pw.setBackground(th.background)
            p = pw.getPlotItem()
            p.setTitle(p.titleLabel.text, color=th.foreground)
            for ax in ("left", "bottom"):
                p.getAxis(ax).setPen(th.foreground)
                p.getAxis(ax).setTextPen(th.foreground)
        trace = QtGui.QColor(cfgmod.PALETTE[1])
        faint = QtGui.QColor(th.foreground)
        faint.setAlpha(35)
        self.blocks_item.setPen(pg.mkPen(faint, width=1))
        self.avg_item.setPen(pg.mkPen("#E69F00", width=2.5))
        self.cur_item.setPen(pg.mkPen(trace, width=1.5))
        self.thr_line.setPen(pg.mkPen("#e0524f", width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.raster_dots.setBrush(pg.mkBrush(th.foreground))
        if self.events is not None:
            self._show_results()
