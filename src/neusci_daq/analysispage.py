"""The Analysis tab: every block of the session, the action potentials in them,
and how the channels compare. A bar at the top picks the highlighted block (slider,
◀ ▶, or the ← → keys); two sub-tabs:

- Raw Data Visualization: the chosen blocks overlaid with their average and the
  highlighted block; Find action potentials (threshold or bimodal, sorted into
  units, see analysis.py); a raster of the events in every block; spikes per
  second across the session; and beside them a histogram of AP sizes and each
  unit's waveforms.
- Comparison: for the highlighted block, any channels on one time axis as V,
  dV/dt or d²V/dt², with the spike rate; and over the chosen blocks, the
  stimulus-triggered spike rate (PSTH) and the spike-triggered average of a channel.

The blocks are the ones being reviewed, or those recorded so far in live mode.
"""
from __future__ import annotations

import csv
import warnings
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import config as cfgmod
from . import pages
from . import session
from . import style, theme
from .plotzoom import OverlayCurve, TraceCurve, plot_widget
from .stimwindow import UnitSpinBox
from .units import best_prefix

_SHOW = (("Blocks and average", "both"), ("Blocks", "blocks"), ("Average", "average"))
MAX_WAVES = 150                    # per unit in the waveform plot
_SERIES = (("V", 0), ("dV/dt", 1), ("d²V/dt²", 2))


def _unit_color(u: int) -> str:
    return cfgmod.PALETTE[u % len(cfgmod.PALETTE)]


def _style_plot(pw: pg.PlotWidget, th) -> None:
    pw.setBackground(th.background)
    p = pw.getPlotItem()
    p.setTitle(p.titleLabel.text, color=th.foreground)
    for ax in ("left", "bottom"):
        p.getAxis(ax).setPen(th.foreground)
        p.getAxis(ax).setTextPen(th.foreground)


def _with_unit(v: float, unit: str) -> str:
    """12.3 mV/s: the prefix goes on the part before any '/'."""
    head, _, tail = unit.partition("/")
    if head in ("V", "A"):
        n, head = best_prefix(v, head)
        return f"{n:.3g} {head}" + (f"/{tail}" if tail else "")
    return f"{v:.3g} {unit}"


def _label(p: pg.PlotItem, side: str, text: str, unit: str) -> None:
    """Label an axis with `unit`, letting pyqtgraph add prefixes (mV, µV/s) when it's SI."""
    si = unit.split("/")[0] in ("V", "A", "s")
    p.setLabel(side, text, units=unit if si else None)
    if not si and unit:
        p.setLabel(side, f"{text} ({unit})")
    p.getAxis(side).enableAutoSIPrefix(si)


class AnalysisPage(QtWidgets.QWidget):
    """The container: loads the blocks, owns the highlighted block, holds the sub-tabs."""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self._cache: dict[Path, tuple[float, dict]] = {}
        self._paths: list[Path] = []
        self.blocks: list[dict | None] = []
        self._dirty = True
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        self.source = QtWidgets.QLabel()
        top.addWidget(self.source)
        top.addStretch(1)
        top.addWidget(QtWidgets.QLabel("Block"))
        self.btn_prev = QtWidgets.QToolButton(text="◀", autoRaise=True)
        self.btn_prev.setToolTip("Previous block (←)")
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, minimum=1, maximum=1)
        self.slider.setMinimumWidth(260)
        self.slider.setToolTip("Drag to flip through the blocks; ← and → step one at a time")
        self.btn_next = QtWidgets.QToolButton(text="▶", autoRaise=True)
        self.btn_next.setToolTip("Next block (→)")
        self.current = QtWidgets.QSpinBox(minimum=1, maximum=1)
        self.current.setToolTip("The highlighted block")
        self.n_label = QtWidgets.QLabel()
        for w in (self.btn_prev, self.slider, self.btn_next, self.current, self.n_label):
            top.addWidget(w)
        lay.addLayout(top)

        grp = QtWidgets.QHBoxLayout()
        self.stim_channel = QtWidgets.QComboBox()
        self.stim_channel.setToolTip("The channel that recorded the stimulus, if one did; you can set it any "
                                     "time after recording. Its pulses give exact stimulus times, and blocks "
                                     "with the same stimulus are grouped. With None, the notes the app made as "
                                     "it sent each stimulus are used. Remembered for this session")
        self.group = QtWidgets.QComboBox()
        self.group.setMinimumContentsLength(28)
        self.group.setToolTip("Look at just this group of blocks, on every sub-tab")
        self.group_note = QtWidgets.QLabel()
        grp.addWidget(QtWidgets.QLabel("Stimulus channel"))
        grp.addWidget(self.stim_channel)
        grp.addWidget(QtWidgets.QLabel("Show"))
        grp.addWidget(self.group)
        grp.addWidget(self.group_note, 1)
        lay.addLayout(grp)
        self.groups: list[tuple[str, list[int]]] = []
        self.stim_channel.currentIndexChanged.connect(lambda *_: self._stim_channel_picked())
        self.group.currentIndexChanged.connect(lambda *_: self.groups_changed())

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.raw = RawView(self)
        self.compare = CompareView(self)
        self.tabs.addTab(self.raw, "Raw Data Visualization")
        self.tabs.addTab(self.compare, "Comparison")
        lay.addWidget(self.tabs, 1)

        self.btn_prev.clicked.connect(lambda: self.step(-1))
        self.btn_next.clicked.connect(lambda: self.step(1))
        self.slider.valueChanged.connect(self.current.setValue)
        self.current.valueChanged.connect(self._current_changed)
        self.tabs.currentChanged.connect(lambda *_: self.compare.redraw() if self.compare.isVisible() else None)

    # -- the highlighted block -----------------------------------------------------------
    def index(self) -> int:
        return self.current.value() - 1

    def step(self, d: int) -> None:
        self.current.setValue(self.current.value() + d)          # the spin box keeps it in range

    def _current_changed(self, v: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.raw.draw_current()
        if self.compare.isVisible():
            self.compare.redraw_traces()

    # -- the blocks --------------------------------------------------------------------------
    @property
    def events(self) -> A.Events | None:
        return self.raw.events

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
        for w in (self.current, self.slider):
            w.blockSignals(True)
            w.setMaximum(max(1, n))
            w.blockSignals(False)
        self.n_label.setText(f"of {n}")
        names = good[0]["channel_names"] if good else []
        self._fill_stim_channel(names)
        self.raw.set_channels(names)
        self.compare.set_channels(names)
        self.compare.stimulus_channel_changed()

    def names(self) -> list[str]:
        good = [b for b in self.blocks if b is not None]
        return good[0]["channel_names"] if good else []

    def trace(self, i: int, name: str) -> np.ndarray | None:
        b = self.blocks[i] if 0 <= i < len(self.blocks) else None
        if b is None or name not in b["channel_names"]:
            return None
        return b["data"][b["channel_names"].index(name)]

    def unit_of(self, name: str) -> str:
        for b in self.blocks:
            if b is not None and name in b["channel_names"]:
                return b["units"][b["channel_names"].index(name)]
        return "V"

    def starts(self) -> dict[int, float]:
        """Each block's start in session time: blocks one after another."""
        out, t = {}, 0.0
        for i, b in enumerate(self.blocks):
            out[i] = t
            t += b["duration_s"] if b is not None else 0.0
        return out

    # -- stimulus groups ------------------------------------------------------------------
    def _settings_key(self) -> str | None:
        where = self.main.review_dir if self.main.mode == "review" else self.main.session_dir
        return f"stim_channel/{Path(where).resolve()}" if where else None

    def _fill_stim_channel(self, names: list[str]) -> None:
        """None and each channel; this session's saved choice, else a channel called Stim."""
        key = self._settings_key()
        saved = self.main.settings.value(key) if key else None
        keep = saved if saved is not None else next((n for n in names if "stim" in n.lower()), "")
        self.stim_channel.blockSignals(True)
        self.stim_channel.clear()
        self.stim_channel.addItem("None (use the notes)", "")
        for name in names:
            self.stim_channel.addItem(name, name)
        self.stim_channel.setCurrentIndex(max(0, self.stim_channel.findData(keep)))
        self.stim_channel.blockSignals(False)
        self._compute_groups()

    def _stim_channel_picked(self) -> None:
        key = self._settings_key()
        if key:
            self.main.settings.setValue(key, self.stim_channel.currentData() or "")
        self.main._fill_recorded()                  # the Acquisition Blocks panel groups the same way
        self.compare.stimulus_channel_changed()
        self._compute_groups()
        self.groups_changed()

    def how(self) -> str:
        """Where stimuli come from: a channel name, "notes", or "" (nowhere)."""
        ch = self.stim_channel.currentData()
        if ch:
            return ch
        has_notes = any(b is not None and A.stimulus_notes(b["comments"]) for b in self.blocks)
        return "notes" if has_notes else ""

    @staticmethod
    def _group(blocks: list[dict | None], how: str) -> list[tuple[str, list[int]]]:
        """Blocks with the same stimulus together: [(label, block indices)]. `how` is a
        stimulus channel's name, "notes", or "" (no groups)."""
        if not how:
            return []
        keys, labels = {}, {}
        for i, b in enumerate(blocks):
            if b is None:
                continue
            if how == "notes":
                k = A.stimulus_notes(b["comments"]) or None
                if k is not None:
                    labels[k] = " · ".join(k)
            else:
                # you said this channel is the stimulus: whatever it shows is the stimulus
                y = b["data"][b["channel_names"].index(how)] if how in b["channel_names"] else None
                k = A.stimulus_signature(y, b["sample_rate"]) if y is not None else None
            keys[i] = k
        if how != "notes":
            # a block where the channel is nearly flat, next to the others, had no stimulus
            size = lambda k: abs(k[3]) if k[0] == "pulse" else k[2]
            biggest = max((size(k) for k in keys.values() if k is not None), default=0.0)
            keys = {i: (k if k is not None and size(k) >= 0.1 * biggest else None) for i, k in keys.items()}
            # measured signatures jitter: blocks with the same stimulus share the first one's
            good = [b for b in blocks if b is not None]
            keys = A.cluster_signatures(keys, good[0]["sample_rate"] if good else 1.0)
            unit = next((b["units"][b["channel_names"].index(how)] for b in good if how in b["channel_names"]), "V")
            labels = A.unique_names({k: A.describe_signature(k, unit) for k in dict.fromkeys(keys.values())
                                     if k is not None})
        return A.group_blocks(keys, labels)

    def stimulus_groups_of(self, paths: list[Path], where: Path | None) -> list[tuple[str, list[int]]]:
        """The stimulus groups of any session's blocks (for the Acquisition Blocks panel),
        the same way this tab would group them, without changing what it shows: the
        session's saved Stimulus channel, else one called Stim, else the notes."""
        blocks = []
        for p in paths:
            try:
                blocks.append(self._read(p))
            except Exception:
                blocks.append(None)
        good = [b for b in blocks if b is not None]
        names = good[0]["channel_names"] if good else []
        saved = self.main.settings.value(f"stim_channel/{Path(where).resolve()}") if where else None
        ch = saved if saved is not None else next((n for n in names if "stim" in n.lower()), "")
        how = ch if ch in names else ""
        if not how and any(A.stimulus_notes(b["comments"]) for b in good):
            how = "notes"
        return self._group(blocks, how)

    def _compute_groups(self) -> None:
        """Group the blocks by their stimulus (see Stimulus channel) and list the groups."""
        how = self.how()
        auto = self._group(self.blocks, how)
        # the pages of the Blocks panel: your names, and blocks you moved
        folder = self.main.review_dir if self.main.mode == "review" else self.main.session_dir
        d = pages.load(folder)
        changed = d["moved"] or d["renamed"]
        self.groups = pages.apply(auto, [p.stem for p in self._paths], d) if auto or changed else []
        keep = self.group.currentText().rsplit(" (", 1)[0]
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem("All blocks")
        for g, (label, blocks) in enumerate(self.groups):
            self.group.addItem(style.dot_icon(self.group_color(g)),
                               f"{label} ({len(blocks)} block{'s' if len(blocks) != 1 else ''})")
        i = next((k + 1 for k, (label, _) in enumerate(self.groups) if label == keep), 0)
        self.group.setCurrentIndex(i)
        self.group.blockSignals(False)
        n = len([g for g in self.groups if g[0] != "No stimulus found"])
        self.group_note.setText(
            "No stimulus channel or notes: pick the channel that recorded the stimulus" if not how else
            f"{n} different stimul{'us' if n == 1 else 'i'}" + (" (from the notes)" if how == "notes" else "")
            + ": pick one with Show" if n else
            f"No stimulus pulses found on {how}: is it the right channel?" if how != "notes" else
            "No stimuli in the notes")

    def group_color(self, g: int) -> str:
        """Group colours: a different order from the unit colours, so they don't look alike."""
        palette = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#999999")
        return palette[g % len(palette)]

    def group_of(self, i: int) -> int | None:
        return next((g for g, (_, blocks) in enumerate(self.groups) if i in blocks), None)

    def group_blocks(self) -> list[int] | None:
        """The blocks of the picked group, or None for all blocks."""
        i = self.group.currentIndex()
        return self.groups[i - 1][1] if i > 0 and i - 1 < len(self.groups) else None

    def onsets(self, i: int) -> np.ndarray | None:
        """When each stimulus started in block i (s): the first pulse of each train on the
        stimulus channel (exact), or the notes' times (close). None when not grouping."""
        how = self.how()
        b = self.blocks[i] if 0 <= i < len(self.blocks) else None
        if b is None or not how:
            return None
        if how == "notes":
            return A.note_times(b["comments"])
        y = self.trace(i, how)
        if y is None:
            return None
        sig = A.stimulus_signature(y, b["sample_rate"])
        if sig is not None and sig[0] == "wave":
            # a wave starts where the channel first leaves its middle level
            moved = np.flatnonzero(np.abs(np.nan_to_num(y) - sig[3]) > 0.1 * sig[2])
            return np.array([moved[0] / b["sample_rate"]]) if len(moved) else np.empty(0)
        return A.train_starts(A.stimulus_times(y, b["sample_rate"]))

    def groups_changed(self) -> None:
        self.raw._settings_changed()
        if self.compare.isVisible():
            self.compare.redraw()

    def events_changed(self) -> None:
        if self.compare.isVisible():
            self.compare.redraw()

    # -- looks ----------------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        self.raw.apply_theme(mode)
        self.compare.apply_theme(mode)


class RawView(QtWidgets.QWidget):
    def __init__(self, page: AnalysisPage):
        super().__init__()
        self.page = page
        self.main = page.main
        self._mode = style.LIGHT
        self.events: A.Events | None = None
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(12)

        # ---- left: settings ------------------------------------------------------
        side = QtWidgets.QWidget()
        side.setMaximumWidth(360)
        left = QtWidgets.QVBoxLayout(side)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        box = QtWidgets.QGroupBox("Blocks")
        f = QtWidgets.QFormLayout(box)
        self.channel = QtWidgets.QComboBox()
        self.which = QtWidgets.QLineEdit(placeholderText="all   (or e.g. 1-20, 35, 100-)")
        self.which.setToolTip("Which blocks to overlay and search: all, or numbers and ranges")
        self.show_what = QtWidgets.QComboBox()
        for label, key in _SHOW:
            self.show_what.addItem(label, key)
        self.rate_bin = UnitSpinBox(0.01, 600, "s", step=0.5)
        self.rate_bin.setValue(1.0)
        self.rate_bin.setToolTip("Spikes per second across the session are counted in bins this long")
        f.addRow("Channel", self.channel)
        f.addRow("Blocks", self.which)
        f.addRow("Show", self.show_what)
        f.addRow("Rate bins", self.rate_bin)
        left.addWidget(box)

        box = QtWidgets.QGroupBox("Find action potentials")
        f = QtWidgets.QFormLayout(box)
        self.method = QtWidgets.QComboBox()
        self.method.addItem("Set threshold", False)
        self.method.addItem("Bimodal (automatic)", True)
        self.method.setToolTip("Bimodal: the threshold goes in the valley between the many small noise "
                               "peaks and the few big spikes. If they don't form two groups, the threshold "
                               "below is used instead")
        self.threshold = QtWidgets.QLineEdit("5 SD")
        self.threshold.setToolTip("A level from the baseline, e.g. 50 mV or 0.05 V, or a number of noise "
                                  "SDs, e.g. 5 SD (a plain number means SDs). Or drag the red line")
        bip = QtWidgets.QHBoxLayout()
        self.biphasic = QtWidgets.QCheckBox("Required")
        self.biphasic.setToolTip("Keep only events that also swing the other way, as a real extracellular "
                                 "spike does and many artifacts don't")
        self.bip_pct = QtWidgets.QSpinBox(minimum=5, maximum=100, value=30, suffix=" %")
        self.bip_pct.setToolTip("How far the other way, as a fraction of the threshold")
        self.bip_ms = UnitSpinBox(0.1, 20, "ms", step=0.5)
        self.bip_ms.setValue(1.0)
        self.bip_ms.setToolTip("Within this long of the event's peak (before or after)")
        bip.addWidget(self.biphasic)
        bip.addWidget(self.bip_pct)
        bip.addWidget(QtWidgets.QLabel("within"))
        bip.addWidget(self.bip_ms)
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
        f.addRow("Method", self.method)
        f.addRow("Threshold", self.threshold)
        f.addRow("Direction", self.polarity)
        f.addRow("Biphasic", bip)
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

        # ---- middle: the blocks, the raster, the rate ----------------------------------
        mid = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.overlay = plot_widget()
        self.raster = plot_widget()
        self.rate_plot = plot_widget()
        for pw, title, left_label in ((self.overlay, "Blocks overlaid", ""),
                                      (self.raster, "Events in every block", "block"),
                                      (self.rate_plot, "Spikes per second across the session", "spikes/s")):
            p = pw.getPlotItem()
            p.setTitle(title)
            p.setLabel("left", left_label)
            p.showGrid(x=True, y=True, alpha=0.2)
        # seconds with a prefix as you zoom (ms, µs)
        self.overlay.getPlotItem().setLabel("bottom", "time in block", units="s")
        self.raster.getPlotItem().setLabel("bottom", "time in block", units="s")
        self.rate_plot.getPlotItem().setLabel("bottom", "time in the session", units="s")
        self.raster.setXLink(self.overlay)
        self.raster.getPlotItem().invertY(True)                  # block 1 at the top
        for w in (self.overlay, self.raster, self.rate_plot):
            mid.addWidget(w)
        mid.setSizes([380, 240, 200])
        outer.addWidget(mid, 3)

        # ---- right: sizes and shapes ---------------------------------------------------
        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.hist = plot_widget()
        self.isi = plot_widget()
        self.wave_plot = plot_widget()
        self.hist.getPlotItem().setTitle("AP sizes")
        self.hist.getPlotItem().setLabel("left", "events")
        self.isi.getPlotItem().setTitle("Inter-spike intervals")
        self.isi.getPlotItem().setLabel("left", "intervals")
        self.isi.getPlotItem().setLabel("bottom", "ms between spikes of a unit")
        self.wave_plot.getPlotItem().setTitle("Waveforms by unit")
        self.wave_plot.getPlotItem().setLabel("bottom", "ms from the peak")
        for pw in (self.hist, self.isi, self.wave_plot):
            pw.getPlotItem().showGrid(x=True, y=True, alpha=0.2)
            right.addWidget(pw)
        right.setMinimumWidth(260)
        outer.addWidget(right, 2)

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
        # each block's stimulus group, as a coloured mark at the start of its row
        self.group_marks = pg.ScatterPlotItem(size=8, symbol="s", pen=None)
        self.group_marks.setZValue(5)
        self.raster.getPlotItem().addItem(self.group_marks)
        self.group_avgs: list[TraceCurve] = []
        self.overlay_legend = op.addLegend(offset=(-10, 10))
        self.cur_band = pg.LinearRegionItem(movable=False, orientation="horizontal")
        self.cur_band.setZValue(-10)
        self.raster.getPlotItem().addItem(self.cur_band)

        # ---- signals -----------------------------------------------------------------
        self.channel.currentIndexChanged.connect(lambda *_: self._settings_changed())
        self.which.editingFinished.connect(self._settings_changed)
        self.show_what.currentIndexChanged.connect(lambda *_: self._draw_overlay())
        self.rate_bin.valueChanged.connect(lambda *_: self._draw_rate())
        self.btn_find.clicked.connect(self.find)
        self.btn_export.clicked.connect(self._export_dialog)
        self.thr_line.sigPositionChangeFinished.connect(self._threshold_dragged)
        # Changing how events are found makes the results out of date.
        self.threshold.editingFinished.connect(self._detection_changed)
        for w in (self.method, self.polarity):
            w.currentIndexChanged.connect(lambda *_: self._detection_changed())
        self.dead.valueChanged.connect(lambda *_: self._detection_changed())
        self.biphasic.toggled.connect(lambda *_: self._detection_changed())
        self.apply_theme(style.LIGHT)

    # -- data ------------------------------------------------------------------------------
    @property
    def blocks(self):
        return self.page.blocks

    def set_channels(self, names: list[str]) -> None:
        keep = self.channel.currentText()
        self.channel.blockSignals(True)
        self.channel.clear()
        self.channel.addItems(names)
        if keep in names:
            self.channel.setCurrentText(keep)
        self.channel.blockSignals(False)
        self._settings_changed()

    def _chosen(self) -> list[int]:
        """Indices of the chosen blocks that have this channel at the first block's rate."""
        idx = A.parse_blocks(self.which.text(), len(self.blocks))
        group = self.page.group_blocks()
        if group is not None:
            idx = sorted(set(idx) & set(group))
        name = self.channel.currentText()
        good = [b for b in self.blocks if b is not None]
        fs = good[0]["sample_rate"] if good else 0
        return [i for i in idx if self.blocks[i] is not None and name in self.blocks[i]["channel_names"]
                and self.blocks[i]["sample_rate"] == fs]

    def chosen_or_none(self) -> list[int]:
        try:
            return self._chosen() if self.blocks else []
        except ValueError:
            return []

    def _trace(self, i: int) -> np.ndarray:
        return self.page.trace(i, self.channel.currentText())

    # -- drawing -----------------------------------------------------------------------------
    def _settings_changed(self) -> None:
        self.events = None
        self._show_results()
        self._draw_overlay()
        self.page.events_changed()

    def _draw_overlay(self) -> None:
        self._show_message("")
        for item in self.group_avgs:
            self.overlay.getPlotItem().removeItem(item)
        self.group_avgs = []
        self.overlay_legend.clear()
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
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)               # padding: all-NaN ends
                avg = np.nanmean(Y, 0)
            by_group = self.page.group.currentIndex() == 0 and len(self.page.groups) > 1
            if show != "blocks" and not by_group:
                self.avg_item.setData(np.arange(n) / fs, avg)
            else:
                self.avg_item.setData(np.empty(0), np.empty(0))
            if by_group and show != "blocks":
                # all blocks, grouped: one average per stimulus group
                for g, (label, blocks) in enumerate(self.page.groups):
                    rows = [r for r, i in enumerate(chosen) if i in blocks]
                    if not rows:
                        continue
                    with np.errstate(all="ignore"), warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)       # padding: all-NaN ends
                        gavg = np.nanmean(Y[rows], 0)
                    item = TraceCurve(name=f"{label} ({len(rows)})")
                    item.setPen(pg.mkPen(self.page.group_color(g), width=2.5))
                    item.setZValue(6)
                    self.overlay.getPlotItem().addItem(item)
                    item.setData(np.arange(n) / fs, gavg)
                    self.group_avgs.append(item)
            self.overlay.getPlotItem().setXRange(0, n / fs, padding=0.01)
            _label(self.overlay.getPlotItem(), "left", self.channel.currentText(),
                   self.page.unit_of(self.channel.currentText()))
            self.raster.getPlotItem().setYRange(0.5, len(self.blocks) + 0.5, padding=0)
        else:
            self.blocks_item.set_traces(np.empty((0, 0)), 1.0)
            self.avg_item.setData(np.empty(0), np.empty(0))
        self.overlay.getPlotItem().setTitle(f"{len(chosen)} block(s) overlaid", color=self._fg())
        marks = [(i, self.page.group_of(i)) for i in range(len(self.blocks))]
        marks = [(i, g) for i, g in marks if g is not None]
        self.group_marks.setData([0.0] * len(marks), [i + 1 for i, _ in marks],
                                 brush=[pg.mkBrush(self.page.group_color(g)) for _, g in marks])
        self.draw_current()

    def draw_current(self) -> None:
        i = self.page.index()
        self.cur_band.setRegion((i + 0.5, i + 1.5))
        y = self._trace(i) if self.blocks else None
        if y is None:
            self.cur_item.setData(np.empty(0), np.empty(0))
            self.marks.setData([], [])
            return
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
        y = self._trace(self.page.index()) if self.blocks else None
        return float(np.median(y)) if y is not None else 0.0

    def _unit(self) -> str:
        return self.page.unit_of(self.channel.currentText())

    def _fmt(self, v: float) -> str:
        """A level in the channel's unit, with a sensible prefix: 0.0374 V -> 37.4 mV."""
        n, u = best_prefix(v, self._unit())
        return f"{n:.3g} {u}"

    def _threshold(self) -> tuple[str, float]:
        """("sd", n) or ("level", trace units), as typed. Raises ValueError."""
        return A.parse_threshold(self.threshold.text(), self._unit())

    def _level(self) -> float | None:
        """The threshold in trace units from the baseline, as Find would use it. None
        before a bimodal search (it depends on every block)."""
        if self.events is not None:
            return self.events.level
        if self.method.currentData():
            return None
        try:
            kind, v = self._threshold()
        except ValueError:
            return None
        if kind == "level":
            return v
        y = self._trace(self.page.index()) if self.blocks else None
        return v * A.noise_sd(y) if y is not None else None

    def _detection_changed(self) -> None:
        try:
            self._threshold()
        except ValueError as e:
            self._show_message(str(e), error=True)
            return
        self.events = None
        self._show_results()
        self._draw_threshold()
        self.page.events_changed()

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
        self.method.blockSignals(True)
        self.method.setCurrentIndex(0)              # a level you set
        self.method.blockSignals(False)
        self.threshold.setText(self._fmt(level))
        if self.polarity.currentData() != "either" and \
                (self.thr_line.value() > self._baseline()) != (self.polarity.currentData() == "positive"):
            self.polarity.setCurrentIndex(self.polarity.findData(
                "positive" if self.thr_line.value() > self._baseline() else "negative"))
        self.events = None
        self._show_results()
        self._show_message(f"Threshold set to {self._fmt(level)} from the baseline. Press Find to search.")

    # -- finding -----------------------------------------------------------------------------
    def find(self) -> A.Events | None:
        try:
            chosen = self._chosen()
            if not chosen:
                raise ValueError("No blocks to search: record or open some, and check Blocks and Channel.")
            self.main.statusBar().showMessage(f"Searching {len(chosen)} block(s)…")
            QtWidgets.QApplication.processEvents()
            kind, v = self._threshold()
            if self.method.currentData() and kind == "level":
                kind, v = "sd", 5.0                      # bimodal's fallback is in SDs
            ev = A.find_events([(i, self._trace(i), self.blocks[i]["sample_rate"]) for i in chosen],
                               threshold=v, fixed=kind == "level", bimodal=bool(self.method.currentData()),
                               biphasic=self.bip_pct.value() / 100 if self.biphasic.isChecked() else None,
                               biphasic_ms=self.bip_ms.value(),
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
        self.draw_current()
        self.page.events_changed()
        return ev

    def _show_results(self) -> None:
        ev = self.events
        self.table.setRowCount(0)
        wp, hp, ip = self.wave_plot.getPlotItem(), self.hist.getPlotItem(), self.isi.getPlotItem()
        for p in (wp, hp, ip):
            for item in list(p.listDataItems()):
                p.removeItem(item)
        self.btn_export.setEnabled(ev is not None and len(ev.t) > 0)
        self._draw_rate()
        if ev is None:
            self.raster_dots.setData([], [])
            return
        self.raster_dots.setData(ev.t, ev.block + 1, brush=[pg.mkBrush(_unit_color(u)) for u in ev.unit])
        sizes = np.abs(ev.amplitude)
        edges = np.histogram_bin_edges(sizes, bins=40) if len(sizes) else np.array([0.0, 1.0])
        all_isi = A.intervals_ms(ev)
        # up to the 98th percentile, so a few long gaps don't squash the rest
        top = float(np.percentile(all_isi, 98)) if len(all_isi) else 100.0
        isi_edges = np.linspace(0, max(top, 1.0), 61)
        _label(hp, "bottom", "size from the baseline", self._unit())
        _label(wp, "left", self.channel.currentText(), self._unit())
        rng = np.random.default_rng(0)
        for s in ev.summary():
            u = s["unit"] - 1
            color = QtGui.QColor(_unit_color(u))
            r = self.table.rowCount()
            self.table.insertRow(r)
            unit_item = QtWidgets.QTableWidgetItem(f"Unit {s['unit']}")
            unit_item.setIcon(style.dot_icon(_unit_color(u)))
            self.table.setItem(r, 0, unit_item)
            for c, text in enumerate((f"{s['count']}", f"{s['rate']:.2f} /s", self._fmt(s['amplitude']),
                                      f"{s['width_ms']:.2f} ms"), 1):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
            m = ev.unit == u
            fill = QtGui.QColor(color)
            fill.setAlpha(90)
            hp.plot(edges, np.histogram(sizes[m], edges)[0], stepMode="center", fillLevel=0,
                    brush=fill, pen=pg.mkPen(color, width=1.5))
            ip.plot(isi_edges, np.histogram(A.intervals_ms(ev, u), isi_edges)[0], stepMode="center",
                    fillLevel=0, brush=fill, pen=pg.mkPen(color, width=1.5))
            w = ev.waves[m]
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
               f"{self._fmt(ev.level)} from the baseline." if n else
               f"No events past the threshold ({self._fmt(ev.level)}): try a lower one.")
        self._show_message(" ".join([msg] + ev.notes))

    def _draw_rate(self) -> None:
        p = self.rate_plot.getPlotItem()
        for item in list(p.listDataItems()):
            p.removeItem(item)
        ev = self.events
        if ev is None or not len(ev.t):
            return
        centres, rates = A.session_rate(ev, self.page.starts(), self.rate_bin.value())
        if not len(centres):
            return
        b = self.rate_bin.value()
        edges = np.append(centres - b / 2, centres[-1] + b / 2)
        if ev.n_units > 1:
            for u in range(ev.n_units):
                p.plot(edges, rates[u], stepMode="center", pen=pg.mkPen(_unit_color(u), width=1.5))
        p.plot(edges, rates[None], stepMode="center", pen=pg.mkPen(self._fg(), width=2))

    def export(self, path: str | Path) -> Path:
        ev = self.events
        path = Path(path)
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["block", "time_s", "unit", "amplitude", "channel"])
            for b, t, u, a in zip(ev.block, ev.t, ev.unit, ev.amplitude):
                w.writerow([self.page._paths[b].stem, f"{t:.6f}", int(u) + 1, f"{a:.6g}",
                            self.channel.currentText()])
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
        for pw in (self.overlay, self.raster, self.rate_plot, self.hist, self.isi, self.wave_plot):
            _style_plot(pw, th)
        trace = QtGui.QColor(cfgmod.PALETTE[1])
        faint = QtGui.QColor(th.foreground)
        faint.setAlpha(35)
        self.blocks_item.setPen(pg.mkPen(faint, width=1))
        self.avg_item.setPen(pg.mkPen("#E69F00", width=2.5))
        self.cur_item.setPen(pg.mkPen(trace, width=1.5))
        self.thr_line.setPen(pg.mkPen("#e0524f", width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.raster_dots.setBrush(pg.mkBrush(th.foreground))
        band = QtGui.QColor(trace)
        band.setAlpha(45)
        self.cur_band.setBrush(pg.mkBrush(band))
        for line in self.cur_band.lines:
            line.setPen(pg.mkPen(None))
        if self.events is not None:
            self._show_results()


class CompareView(QtWidgets.QWidget):
    def __init__(self, page: AnalysisPage):
        super().__init__()
        self.page = page
        self.main = page.main
        self._mode = style.LIGHT
        self._plots: list[pg.PlotWidget] = []
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(12)

        side = QtWidgets.QWidget()
        side.setMaximumWidth(360)
        left = QtWidgets.QVBoxLayout(side)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)

        box = QtWidgets.QGroupBox("Traces: the highlighted block")
        v = QtWidgets.QVBoxLayout(box)
        self.series = QtWidgets.QTableWidget(0, len(_SERIES))
        self.series.setHorizontalHeaderLabels([s for s, _ in _SERIES])
        self.series.setToolTip("Tick what to show for each channel, on one time axis")
        self.series.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.series.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.series)
        f = QtWidgets.QFormLayout()
        rate = QtWidgets.QHBoxLayout()
        self.show_rate = QtWidgets.QCheckBox("Spike rate")
        self.show_rate.setChecked(True)
        self.show_rate.setToolTip("Spikes per second through the block, from the events found on the "
                                  "Raw Data tab (each spike smoothed into a Gaussian)")
        self.rate_sigma = UnitSpinBox(1, 1000, "ms", step=5)
        self.rate_sigma.setValue(20)
        self.rate_sigma.setToolTip("Smoothing: the SD of each spike's Gaussian")
        rate.addWidget(self.show_rate)
        rate.addWidget(QtWidgets.QLabel("smoothed"))
        rate.addWidget(self.rate_sigma)
        self.d_smooth = UnitSpinBox(0, 50, "ms", step=0.1)
        self.d_smooth.setValue(0.2)
        self.d_smooth.setToolTip("Differentiating amplifies noise: average over this long first (0 = none)")
        self.layout_mode = QtWidgets.QComboBox()
        self.layout_mode.addItem("Stacked, each on its own axis", "stacked")
        self.layout_mode.addItem("Overlaid, each scaled to fit", "overlaid")
        self.unit_pick = QtWidgets.QComboBox()
        self.unit_pick.setToolTip("Which unit's spikes the rate and averages use")
        f.addRow(rate)
        f.addRow("Smooth for d/dt", self.d_smooth)
        f.addRow("Layout", self.layout_mode)
        f.addRow("Spikes of", self.unit_pick)
        v.addLayout(f)
        left.addWidget(box)

        box = QtWidgets.QGroupBox("Triggered averages: the chosen blocks")
        f = QtWidgets.QFormLayout(box)
        self.trigger = QtWidgets.QComboBox()
        self.trigger.setToolTip("When each stimulus happened: from the pulses on a channel that records "
                                "the stimulus, or the start of every block (for triggered sweeps)")
        self.before = UnitSpinBox(0, 10000, "ms", step=5)
        self.before.setValue(50)
        self.after = UnitSpinBox(1, 10000, "ms", step=5)
        self.after.setValue(200)
        self.bin = UnitSpinBox(0.1, 1000, "ms", step=1)
        self.bin.setValue(5)
        self.bin.setToolTip("Bin width of the stimulus-triggered spike rate")
        self.sta_channel = QtWidgets.QComboBox()
        self.sta_channel.setToolTip("The channel averaged around each spike")
        self.sta_before = UnitSpinBox(0, 10000, "ms", step=1)
        self.sta_before.setValue(20)
        self.sta_after = UnitSpinBox(0, 10000, "ms", step=1)
        self.sta_after.setValue(5)
        f.addRow("Stimulus from", self.trigger)
        f.addRow("Before stimulus", self.before)
        f.addRow("After stimulus", self.after)
        f.addRow("Bin", self.bin)
        f.addRow("Average around spikes", self.sta_channel)
        f.addRow("Before spike", self.sta_before)
        f.addRow("After spike", self.sta_after)
        left.addWidget(box)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        left.addStretch(1)
        outer.addWidget(side)

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.traces_box = QtWidgets.QWidget()
        self.traces_lay = QtWidgets.QVBoxLayout(self.traces_box)
        self.traces_lay.setContentsMargins(0, 0, 0, 0)
        self.traces_lay.setSpacing(2)
        right.addWidget(self.traces_box)
        bottom = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.psth = plot_widget()
        self.sta = plot_widget()
        p = self.psth.getPlotItem()
        p.setTitle("Stimulus-triggered spike rate")
        self.psth_legend = p.addLegend(offset=(-10, 10))           # one entry per stimulus group
        p.setLabel("left", "spikes/s")
        p.setLabel("bottom", "ms from the stimulus")
        p = self.sta.getPlotItem()
        p.setTitle("Spike-triggered average")
        p.setLabel("bottom", "ms from the spike")
        for pw in (self.psth, self.sta):
            pw.getPlotItem().showGrid(x=True, y=True, alpha=0.2)
            pw.getPlotItem().addItem(pg.InfiniteLine(0, angle=90, pen=pg.mkPen("#888", style=QtCore.Qt.PenStyle.DashLine)))
            bottom.addWidget(pw)
        right.addWidget(bottom)
        right.setSizes([520, 300])
        outer.addWidget(right, 1)

        self.series.itemChanged.connect(lambda *_: self.redraw_traces())
        for w in (self.show_rate,):
            w.toggled.connect(lambda *_: self.redraw_traces())
        for w in (self.rate_sigma, self.d_smooth):
            w.valueChanged.connect(lambda *_: self.redraw_traces())
        self.layout_mode.currentIndexChanged.connect(lambda *_: self.redraw_traces())
        self.unit_pick.currentIndexChanged.connect(lambda *_: self.redraw())
        for w in (self.trigger, self.sta_channel):
            w.currentIndexChanged.connect(lambda *_: self.redraw_averages())
        for w in (self.before, self.after, self.bin, self.sta_before, self.sta_after):
            w.valueChanged.connect(lambda *_: self.redraw_averages())
        self.apply_theme(style.LIGHT)

    # -- settings ------------------------------------------------------------------------------
    def set_channels(self, names: list[str]) -> None:
        old = self._ticked()
        self.series.blockSignals(True)
        self.series.setRowCount(len(names))
        self.series.setVerticalHeaderLabels(names)
        for r, name in enumerate(names):
            for c in range(len(_SERIES)):
                item = QtWidgets.QTableWidgetItem()
                item.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled)
                on = (name, c) in old if old else c == 0                # every channel's V to start
                item.setCheckState(QtCore.Qt.CheckState.Checked if on else QtCore.Qt.CheckState.Unchecked)
                self.series.setItem(r, c, item)
        self.series.blockSignals(False)
        for combo, first in ((self.trigger, ("Start of each block", "")), (self.sta_channel, None)):
            keep = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            if first:
                combo.addItem(*first)
            for name in names:
                combo.addItem(f"Pulses on {name}" if combo is self.trigger else name, name)
            i = combo.findData(keep)
            if i < 0 and combo is self.trigger:
                # a channel that looks like it records the stimulus
                i = next((k for k in range(combo.count()) if "stim" in str(combo.itemData(k)).lower()), 0)
            combo.setCurrentIndex(max(0, i))
            combo.blockSignals(False)
        if self.isVisible():
            self.redraw()

    def stimulus_channel_changed(self) -> None:
        """Use the Stimulus channel picked at the top for the stimulus-triggered rate."""
        ch = self.page.stim_channel.currentData()
        i = self.trigger.findData(ch) if ch else 0
        if i >= 0:
            self.trigger.setCurrentIndex(i)

    def _ticked(self) -> set[tuple[str, int]]:
        out = set()
        for r in range(self.series.rowCount()):
            name = self.series.verticalHeaderItem(r).text() if self.series.verticalHeaderItem(r) else ""
            for c in range(self.series.columnCount()):
                item = self.series.item(r, c)
                if item is not None and item.checkState() == QtCore.Qt.CheckState.Checked:
                    out.add((name, c))
        return out

    def _events_of_unit(self):
        """(block -> spike times s) for the picked unit, or None without events."""
        ev = self.page.events
        if ev is None:
            return None
        u = self.unit_pick.currentData()
        m = np.ones(len(ev.t), bool) if u is None else ev.unit == u
        out: dict[int, np.ndarray] = {}
        for b in np.unique(ev.block[m]):
            out[int(b)] = ev.t[m & (ev.block == b)]
        return out

    def _sync_units(self) -> None:
        ev = self.page.events
        keep = self.unit_pick.currentData()
        self.unit_pick.blockSignals(True)
        self.unit_pick.clear()
        self.unit_pick.addItem("All units", None)
        for u in range(ev.n_units if ev is not None else 0):
            self.unit_pick.addItem(style.dot_icon(_unit_color(u)), f"Unit {u + 1}", u)
        i = self.unit_pick.findData(keep)
        self.unit_pick.setCurrentIndex(max(0, i))
        self.unit_pick.blockSignals(False)

    # -- drawing -------------------------------------------------------------------------------
    def showEvent(self, ev):
        super().showEvent(ev)
        self.redraw()

    def redraw(self) -> None:
        self._sync_units()
        self.redraw_traces()
        self.redraw_averages()

    def _clear_traces(self) -> None:
        for pw in self._plots:
            pw.setParent(None)
            pw.deleteLater()
        self._plots = []

    def redraw_traces(self) -> None:
        self._clear_traces()
        page, i = self.page, self.page.index()
        if not page.blocks or not (0 <= i < len(page.blocks)) or page.blocks[i] is None:
            return
        fs = page.blocks[i]["sample_rate"]
        series = []                                   # (label, unit, y)
        for name, c in sorted(self._ticked(), key=lambda nc: (page.names().index(nc[0])
                                                              if nc[0] in page.names() else 99, nc[1])):
            y = page.trace(i, name)
            if y is None:
                continue
            unit = page.unit_of(name)
            if c == 0:
                series.append((name, unit, np.asarray(y, float)))
            else:
                series.append((f"{name} {_SERIES[c][0]}", f"{unit}/s" if c == 1 else f"{unit}/s²",
                               A.derivative(y, fs, c, self.d_smooth.value())))
        n = len(page.blocks[i]["data"][0]) if page.blocks[i]["data"].size else 0
        notes = []
        if self.show_rate.isChecked():
            times = self._events_of_unit()
            if times is None:
                notes.append("No spike rate yet: find action potentials on the Raw Data tab first.")
            else:
                series.append(("Spike rate", "spikes/s",
                               A.rate_trace(times.get(i, np.empty(0)), n / fs, fs, self.rate_sigma.value())))
        self._show_message(" ".join(notes))
        if not series:
            return
        t = np.arange(n) / fs
        th = theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT]
        colors = [cfgmod.PALETTE[(k + 1) % len(cfgmod.PALETTE)] for k in range(len(series))]
        if self.layout_mode.currentData() == "overlaid":
            pw = plot_widget()
            p = pw.getPlotItem()
            p.addLegend(offset=(10, 10))
            p.setLabel("left", "each scaled to fit")
            for (label, unit, y), color in zip(series, colors):
                d = y - np.median(y)
                scale = np.percentile(np.abs(d), 99.9) or 1.0
                curve = TraceCurve(name=f"{label} (±{_with_unit(scale, unit)})")
                curve.mode = "peak"
                curve.setPen(pg.mkPen(color, width=1.5))
                p.addItem(curve)
                curve.setData(t, d / scale)
            plots = [pw]
        else:
            plots = []
            for (label, unit, y), color in zip(series, colors):
                pw = plot_widget()
                p = pw.getPlotItem()
                p.setTitle(label, size="9pt")                     # the name above, the unit beside
                _label(p, "left", "", unit)
                p.getAxis("left").setWidth(80)                    # line the time axes up
                curve = TraceCurve()
                curve.mode = "peak"
                curve.setPen(pg.mkPen(color, width=1.2))
                p.addItem(curve)
                curve.setData(t, y)
                if plots:
                    p.setXLink(plots[0])
                plots.append(pw)
        for pw in plots:
            p = pw.getPlotItem()
            p.showGrid(x=True, y=True, alpha=0.2)
            _style_plot(pw, th)
            self.traces_lay.addWidget(pw, 1)
        # plain seconds on every row, so they read the same
        for pw in plots:
            pw.getPlotItem().getAxis("bottom").enableAutoSIPrefix(False)
        plots[-1].getPlotItem().setLabel("bottom", f"time in block {i + 1} (s)")
        plots[0].getPlotItem().setXRange(0, n / fs, padding=0)
        self._plots = plots

    def redraw_averages(self) -> None:
        page = self.page
        pp, sp = self.psth.getPlotItem(), self.sta.getPlotItem()
        for p in (pp, sp):
            for item in list(p.listDataItems()):
                p.removeItem(item)
            for item in [x for x in p.items if isinstance(x, pg.FillBetweenItem)]:
                p.removeItem(item)
        times = self._events_of_unit()
        chosen = page.raw.chosen_or_none()
        if times is None or not chosen:
            pp.setTitle("Stimulus-triggered spike rate: find action potentials first", color=self._fg())
            sp.setTitle("Spike-triggered average: find action potentials first", color=self._fg())
            return
        fs = page.blocks[chosen[0]]["sample_rate"]
        stim = self.trigger.currentData()
        triggers = {}
        for b in chosen:
            if not stim:
                triggers[b] = np.array([0.0])
            else:
                y = page.trace(b, stim)
                if y is not None:
                    triggers[b] = A.stimulus_times(y, fs)
        b = self.bin.value()
        groups = [(label, [k for k in blocks if k in triggers]) for label, blocks in page.groups]
        groups = [(label, blocks) for label, blocks in groups if blocks]
        self.psth_legend.clear()
        if page.group.currentIndex() == 0 and len(groups) > 1:
            # all blocks, grouped by stimulus: one line each, to compare the responses
            n_trig = 0
            for g, (label, blocks) in enumerate(groups):
                centres, rate, n = A.triggered_rate(times, {k: triggers[k] for k in blocks}, self.before.value(),
                                                    self.after.value(), b)
                n_trig += n
                pp.plot(np.append(centres - b / 2, centres[-1] + b / 2), rate, stepMode="center",
                        pen=pg.mkPen(_unit_color(g), width=2), name=f"{label} ({n})")
            pp.setTitle(f"Stimulus-triggered spike rate by stimulus group ({n_trig} stimuli)", color=self._fg())
        else:
            centres, rate, n_trig = A.triggered_rate(times, triggers, self.before.value(), self.after.value(), b)
            pp.plot(np.append(centres - b / 2, centres[-1] + b / 2), rate, stepMode="center", fillLevel=0,
                    brush=pg.mkBrush(86, 180, 233, 90), pen=pg.mkPen("#56B4E9", width=1.5))
            pp.setTitle(f"Stimulus-triggered spike rate ({n_trig} stimuli)" if n_trig else
                        "Stimulus-triggered spike rate: no stimuli found on that channel", color=self._fg())
        name = self.sta_channel.currentData()
        traces = {k: page.trace(k, name) for k in chosen} if name else {}
        t, mean, sd, n = A.triggered_average({k: v for k, v in traces.items() if v is not None}, fs,
                                             {k: v for k, v in times.items() if k in traces},
                                             self.sta_before.value(), self.sta_after.value())
        if n:
            hi = sp.plot(t, mean + sd, pen=pg.mkPen(None))
            lo = sp.plot(t, mean - sd, pen=pg.mkPen(None))
            sp.addItem(pg.FillBetweenItem(hi, lo, brush=pg.mkBrush(230, 159, 0, 50)))
            sp.plot(t, mean, pen=pg.mkPen("#E69F00", width=2))
            _label(sp, "left", name, page.unit_of(name))
        sp.setTitle(f"Spike-triggered average of {name} ({n} spikes, ± SD)" if n else
                    "Spike-triggered average: no spikes far enough from the block ends", color=self._fg())

    # -- looks -----------------------------------------------------------------------------------
    def _fg(self):
        return theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT].foreground

    def _show_message(self, text: str, error: bool = False) -> None:
        t = style.tokens(self._mode)
        self.message.setStyleSheet(f"color: {t['error'] if error else t['muted']}")
        self.message.setText(text)

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        for pw in [self.psth, self.sta] + self._plots:
            _style_plot(pw, th)
