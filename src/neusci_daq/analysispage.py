"""The Analysis tab: every block of the session, the action potentials in them,
and how the channels compare. A bar at the top of Comparison and DataPad picks the
highlighted block (slider, ◀ ▶, or the ← → keys); clicking a block in the Blocks panel
picks it on any sub-tab. The Blocks panel beside the sub-tabs picks the blocks to analyze,
the stimulus and analyzed channels, and Spread. Three sub-tabs:

- Spike Sorting: the chosen blocks' stimuli above their traces, a page's average when
  the page is picked and the trials when trials are; Find action potentials (threshold
  or bimodal, sorted into units, see analysis.py), run again whenever its settings
  change until you Clear; a raster of the events in the chosen blocks; spikes per
  second during a block; all on one time axis, zooming together; and beside them a
  PCA map of the units, their waveforms, and histograms of AP sizes and intervals.
  Pointing at a unit shows only that unit, on every graph but the PCA map.
- Comparison: for the highlighted block, any channels on one time axis as V,
  dV/dt or d²V/dt², with the spike rate; and over the chosen blocks, the
  stimulus-triggered spike rate (PSTH) and the spike-triggered average of a channel.
- DataPad: a table of measurements (mean, peak, area, slope, spike rate…) in a time
  window, of single blocks, stimulus averages, or mean ± SD/SEM per stimulus
  (see datapadview.py).

Averages can be shaded with their SD or SEM across trials (Spread, in the Blocks panel).

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
from . import colormaps as cm
from . import config as cfgmod
from . import dsp
from . import pages
from . import style, theme
from .blockspanel import BlocksPanel
from .channels import ColorButton
from .datapadview import DataPadView
from .helpview import add_help
from .plotzoom import AxisGroup, MultiAxis, OverlayCurve, TraceCurve, plot_widget
from .stimwindow import UnitSpinBox
from .units import best_prefix
from .viewkit import (ChartToggles, PlotCorner, View, even_out, label_axis, scroll_column, style_plot, unit_color,
                      with_unit)

MAX_WAVES = 150                    # per unit in the waveform plot
MAX_MARKED = 2000                  # spikes drawn over the blocks when a unit is highlighted
_SERIES = (("V", 0), ("dV/dt", 1), ("d²V/dt²", 2))


class AnalysisPage(QtWidgets.QWidget):
    """The container: loads the blocks, owns the highlighted block, holds the sub-tabs."""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self._paths: list[Path] = []
        self.blocks: list[dict | None] = []
        self._dirty = True
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        self.source = _flexible_label()
        top.addWidget(self.source, 1)
        self.block_bar = QtWidgets.QWidget()      # not on Spike Sorting: it works on the chosen blocks
        bar = QtWidgets.QHBoxLayout(self.block_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(QtWidgets.QLabel("Block"))
        self.btn_prev = QtWidgets.QToolButton(text="◀", autoRaise=True)
        self.btn_prev.setToolTip("Previous block (←)")
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, minimum=1, maximum=1)
        self.slider.setMinimumWidth(120)
        self.slider.setToolTip("Drag to flip through the blocks; ← and → step one at a time")
        self.btn_next = QtWidgets.QToolButton(text="▶", autoRaise=True)
        self.btn_next.setToolTip("Next block (→)")
        self.current = QtWidgets.QSpinBox(minimum=1, maximum=1)
        self.current.setToolTip("The highlighted block")
        self.n_label = QtWidgets.QLabel()
        for w in (self.btn_prev, self.slider, self.btn_next, self.current, self.n_label):
            bar.addWidget(w)
        top.addWidget(self.block_bar)
        lay.addLayout(top)

        # The Blocks panel, beside every sub-tab: pick the blocks to analyze, and how.
        self.blocks_panel = BlocksPanel(for_analysis=True)
        self.blocks_panel.selection.connect(self._blocks_picked)
        self.blocks_panel.activated.connect(self._block_clicked)
        self.blocks_panel.btn_all.clicked.connect(lambda: self.select_blocks([]))
        self.blocks_panel.rename_requested.connect(self.main._rename_dialog)
        self.blocks_panel.move_requested.connect(
            lambda i, page: self.main.move_block(i, page) if page else self.main._new_page_dialog(i))
        self.blocks_panel.reset_requested.connect(self.main._reset_pages_dialog)
        self.picked: list[int] = []               # the blocks selected; none means all of them
        self.picked_pages: list[str] = []         # the pages selected: their averages are shown
        self.shown: list[tuple[str, list[int]]] = []   # the pages of the Blocks panel
        self._looks = pages.load(None)            # the colours saved with the session
        self._pick_timer = QtCore.QTimer(self, singleShot=True, interval=150)   # dragging picks many at once
        self._pick_timer.timeout.connect(self.groups_changed)
        self.stim_channel = self.blocks_panel.stim_ch
        self.stim_channel.setToolTip("The channel that recorded the stimulus, if one did; you can set it any "
                                     "time after recording. Its pulses give exact stimulus times, and blocks "
                                     "with the same stimulus are grouped. With None, the notes the app made as "
                                     "it sent each stimulus are used. Remembered for this session")
        self.group_note = _flexible_label()
        self.blocks_panel.layout().addWidget(self.group_note)
        self.spread_box = QtWidgets.QComboBox()
        for label, key in (("None", ""), ("± SD", "sd"), ("± SEM", "sem")):
            self.spread_box.addItem(label, key)
        self.spread_box.setToolTip("Shade every average with its standard deviation or standard error "
                                   "across the trials (blocks or stimuli) it's made of")
        self.spread_box.currentIndexChanged.connect(lambda *_: self.spread_changed())
        self.page_map = QtWidgets.QComboBox()
        self.page_map.addItems(cm.PAGE_STYLES)
        self.page_map.setToolTip("Colours every page at once. Palette: a different colour each; a colour map: "
                                 "from the first page to the last. A colour you give a page (right-click it) "
                                 "stays. Saved with the session")
        self.trial_map = QtWidgets.QComboBox()
        self.trial_map.addItems(cm.TRIAL_STYLES)
        self.trial_map.setToolTip("How a page's trials are drawn. Solid: all in the page's colour; Opacity: "
                                  "faint to solid, first to last; a colour map: across its colours, first to "
                                  "last. Saved with the session")
        self.page_map.currentTextChanged.connect(lambda v: self._set_look("page_map", v))
        self.trial_map.currentTextChanged.connect(lambda v: self._set_look("trial_map", v))
        self.groups: list[tuple[str, list[int]]] = []
        self.stim_channel.currentIndexChanged.connect(lambda *_: self._stim_channel_picked())

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.raw = RawView(self)
        self.compare = CompareView(self)
        self.tabs.addTab(self.raw, "Spike Sorting")
        self.tabs.addTab(self.compare, "Comparison")
        self.datapad = DataPadView(self)
        self.tabs.addTab(self.datapad, "DataPad")
        self.blocks_panel.add_setting("Analyze", self.raw.channel)
        self.blocks_panel.add_setting("Spread", self.spread_box)
        self.blocks_panel.add_setting("Pages", self.page_map)
        self.blocks_panel.add_setting("Trials", self.trial_map)
        self.blocks_panel.color_requested.connect(self.pick_color)
        self.blocks_panel.color_cleared.connect(lambda name: self.set_page_color(name, None))
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self.blocks_panel)
        body.addWidget(self.tabs, 1)
        lay.addLayout(body, 1)

        self.btn_prev.clicked.connect(lambda: self.step(-1))
        self.btn_next.clicked.connect(lambda: self.step(1))
        self.slider.valueChanged.connect(self.current.setValue)
        self.current.valueChanged.connect(self._current_changed)
        self.tabs.currentChanged.connect(lambda *_: self.compare.redraw() if self.compare.isVisible() else None)
        self.tabs.currentChanged.connect(lambda *_: self.block_bar.setVisible(self.tabs.currentWidget() is not self.raw))
        self.block_bar.setVisible(self.tabs.currentWidget() is not self.raw)

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
        if self.datapad.isVisible():
            self.datapad.redraw()

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
        return self.main.store.read(path)            # shared with review and the Blocks panel

    def _signature_of(self, paths: list[Path], how: str):
        """sig(i, trace, fs): block i's stimulus on channel `how`, worked out once per file."""
        store = self.main.store
        return lambda i, y, fs: store.memo(paths[i], ("stimulus", how), lambda: A.stimulus_signature(y, fs))

    def refresh(self) -> None:
        self._dirty = False
        old, self._paths = self._paths, list(self.main.analysis_blocks())
        if self._paths[:len(old)] != old:
            self.picked, self.picked_pages = [], []    # other blocks: the selection meant something else
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
        self.picked = [i for i in self.picked if i < n]
        recorded = good[0]["channel_names"] if good else []
        self._chain = dsp.Chain(self.main.cfg.calculations, recorded, good[0]["sample_rate"] if good else None)
        # calculated channels these blocks can have (each may use the ones above it)
        self._calcs = {c.name: (c, f) for c, f in zip(self._chain.calcs, self._chain.formulas) if f is not None}
        names = self.names()
        self._fill_stim_channel(recorded)             # the stimulus is a recorded channel
        self.raw.set_channels(names)
        self.compare.set_channels(names)
        self.datapad.set_channels(names)
        self.compare.stimulus_channel_changed()

    def names(self) -> list[str]:
        """The recorded channels, then the calculated ones."""
        good = [b for b in self.blocks if b is not None]
        return (list(good[0]["channel_names"]) if good else []) + list(getattr(self, "_calcs", {}))

    def has(self, i: int, name: str) -> bool:
        b = self.blocks[i] if 0 <= i < len(self.blocks) else None
        if b is None:
            return False
        calc = getattr(self, "_calcs", {}).get(name)
        return name in b["channel_names"] or (calc is not None and
                                              self._chain.needs.get(name, {""}) <= set(b["channel_names"]))

    def trace(self, i: int, name: str) -> np.ndarray | None:
        b = self.blocks[i] if 0 <= i < len(self.blocks) else None
        if b is None:
            return None
        if name in b["channel_names"]:
            return b["data"][b["channel_names"].index(name)]
        calc = getattr(self, "_calcs", {}).get(name)
        if calc is None or not self._chain.needs.get(name, {""}) <= set(b["channel_names"]):
            return None
        env = dict(zip(b["channel_names"], b["data"]))
        # worked out once per block file (zero-phase), shared with the Acquisition tab
        ys = self.main.store.memo(self._paths[i], ("calcs", self._chain.key()),
                                  lambda: self._chain.offline(env, b["sample_rate"]))
        return ys[[c.name for c in self._chain.calcs].index(name)]

    def unit_of(self, name: str) -> str:
        calc = getattr(self, "_calcs", {}).get(name)
        if calc is not None:
            return calc[0].unit
        for b in self.blocks:
            if b is not None and name in b["channel_names"]:
                return b["units"][b["channel_names"].index(name)]
        return "V"

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
        self.raw.set_channels(self.names())         # the stimulus isn't one to sort spikes on
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
    def _group(blocks: list[dict | None], how: str, sig=None) -> list[tuple[str, list[int]]]:
        """Blocks with the same stimulus together: [(label, block indices)]. `how` is a
        stimulus channel's name, "notes", or "" (no groups). sig(i, trace, fs) reads a
        block's stimulus (default: analysis.stimulus_signature, every time)."""
        sig = sig or (lambda i, y, fs: A.stimulus_signature(y, fs))
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
                k = sig(i, y, b["sample_rate"]) if y is not None else None
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
        return self._group(blocks, how, self._signature_of(list(paths), how))

    def _compute_groups(self) -> None:
        """Group the blocks by their stimulus (see Stimulus channel) and list the groups."""
        how = self.how()
        auto = self._group(self.blocks, how, self._signature_of(self._paths, how))
        # the pages of the Blocks panel: your names, and blocks you moved
        d = pages.load(self._folder())
        changed = d["moved"] or d["renamed"]
        stems = [p.stem for p in self._paths]
        shown = pages.apply(auto, stems, d) if stems else []
        self.groups = shown if auto or changed else []
        self.shown = shown
        self._show_looks(d)
        labels = {i: (stem, f"{b['duration_s']:.1f} s" if b is not None else "?")
                  for i, (stem, b) in enumerate(zip(stems, self.blocks))}
        self.blocks_panel.populate(None, shown, [self.group_color(g) for g in range(max(1, len(shown)))], labels,
                                   lambda i: pages.auto_page(auto, i, d), bool(changed), False)
        self.blocks_panel.select(self.picked, self.picked_pages)
        n = len([g for g in self.groups if g[0] != "No stimulus found"])
        self.group_note.setText(
            "No stimulus channel or notes: pick the channel that recorded the stimulus" if not how else
            f"{n} different stimul{'us' if n == 1 else 'i'}" + (" (from the notes)" if how == "notes" else "")
            + ": select a page to see just its blocks" if n else
            f"No stimulus pulses found on {how}: is it the right channel?" if how != "notes" else
            "No stimuli in the notes")

    def _folder(self):
        return self.main.review_dir if self.main.mode == "review" else self.main.session_dir

    def _show_looks(self, d: dict) -> None:
        """Use the colours saved with this session, if it has any yet."""
        if self._folder() is not None:
            self._looks = d
        for box, key in ((self.page_map, "page_map"), (self.trial_map, "trial_map")):
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findText(self._looks.get(key, ""))))
            box.blockSignals(False)

    def _save_looks(self) -> None:
        folder = self._folder()
        if folder is not None:
            d = pages.load(folder)
            pages.save(folder, {**d, **{k: self._looks[k] for k in ("colors", "page_map", "trial_map")}})
        self._compute_groups()
        self.main._fill_recorded()                  # the Acquisition Blocks panel shows them too
        self.groups_changed()

    def _set_look(self, key: str, value: str) -> None:
        self._looks = {**self._looks, key: value}
        self._save_looks()

    def set_page_color(self, name: str, color: str | None) -> None:
        """Give a page its own colour; None goes back to the default."""
        colors = {k: v for k, v in self._looks["colors"].items() if k != name}
        if color:
            colors[name] = color
        self._looks = {**self._looks, "colors": colors}
        self._save_looks()

    def pick_color(self, name: str) -> None:
        g = next((k for k, (n, _) in enumerate(self.shown) if n == name), 0)
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.group_color(g)), self, f"Colour of {name}")
        if c.isValid():
            self.set_page_color(name, c.name())

    def group_color(self, g: int, names: list[str] | None = None, looks: dict | None = None) -> str:
        """Page g's colour (of `names`; default the pages here): the one you gave it, else from
        the colour map, else from a palette in a different order from the unit colours, so
        they don't look alike."""
        names = [n for n, _ in self.shown] if names is None else names
        looks = self._looks if looks is None else looks
        given = looks["colors"].get(names[g]) if g < len(names) else None
        if given:
            return given
        if looks["page_map"] in cm.MAPS:
            return cm.sample(looks["page_map"], g / max(1, len(names) - 1)).name()
        palette = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#999999")
        return palette[g % len(palette)]

    def plan(self, chosen: list[int]) -> tuple[list[tuple[int, str, list[int]]], list[int]]:
        """What to draw of the chosen blocks: ([(page number, name, its blocks)] to average,
        the blocks to show as trials). A page picked in the Blocks panel shows its average,
        a block picked on its own its trial, and with nothing picked every page's average."""
        wanted = set(chosen)
        averaged = [(g, name, [i for i in blocks if i in wanted]) for g, (name, blocks) in enumerate(self.shown)
                    if not self.picked or name in self.picked_pages]
        averaged = [a for a in averaged if a[2]]
        done = {i for _, _, blocks in averaged for i in blocks}
        return averaged, [i for i in chosen if i not in done]

    def trial_colors(self, blocks: list[int]) -> dict[int, QtGui.QColor]:
        """How each of these trials is drawn: in order within its page, by the Trials setting."""
        style_ = self._looks["trial_map"] or cm.SOLID
        out: dict[int, QtGui.QColor] = {}
        for g, (_, members) in enumerate(self.shown):
            rows = [i for i in blocks if i in members]
            out.update(zip(rows, cm.spread(style_, self.group_color(g), len(rows))))
        rest = [i for i in blocks if i not in out]
        out.update(zip(rest, cm.spread(style_, self.group_color(0), len(rest))))
        return out

    def group_of(self, i: int) -> int | None:
        return next((g for g, (_, blocks) in enumerate(self.groups) if i in blocks), None)

    def group_blocks(self) -> list[int] | None:
        """The blocks selected in the Blocks panel, or None for all blocks."""
        return self.picked or None

    def _blocks_picked(self, picked: list[int]) -> None:
        names = self.blocks_panel.selected_pages()
        if picked != self.picked or names != self.picked_pages:
            self.picked, self.picked_pages = picked, names
            self._pick_timer.start()

    def _block_clicked(self, data) -> None:
        if isinstance(data, tuple) and data[0] == "block":
            self.current.setValue(int(data[1]) + 1)

    def select_blocks(self, blocks: list[int]) -> None:
        """Analyze just these blocks (none: all of them)."""
        self._pick_timer.stop()
        self.picked, self.picked_pages = sorted(blocks), []
        self.blocks_panel.select(self.picked)
        self.groups_changed()

    def select_pages(self, names: list[str]) -> None:
        """Show these pages' averages."""
        self._pick_timer.stop()
        self.picked_pages = list(names)
        self.picked = sorted(i for name, blocks in self.shown if name in names for i in blocks)
        self.blocks_panel.select(self.picked, self.picked_pages)
        self.groups_changed()

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
        sig = self._signature_of(self._paths, how)(i, y, b["sample_rate"])
        if sig is not None and sig[0] == "wave":
            # a wave starts where the channel first leaves its middle level
            moved = np.flatnonzero(np.abs(np.nan_to_num(y) - sig[3]) > 0.1 * sig[2])
            return np.array([moved[0] / b["sample_rate"]]) if len(moved) else np.empty(0)
        return A.train_starts(A.stimulus_times(y, b["sample_rate"]))

    def spread(self) -> str:
        """"" (none), "sd" or "sem": the spread shown around averages."""
        return self.spread_box.currentData() or ""

    def spread_changed(self) -> None:
        self.raw._draw_overlay()
        if self.compare.isVisible():
            self.compare.redraw_averages()

    def groups_changed(self) -> None:
        if self.picked and self.index() not in self.picked:
            self.current.setValue(self.picked[0] + 1)     # the highlighted block is one of them
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
        self.datapad.apply_theme(mode)


def _flexible_label() -> QtWidgets.QLabel:
    """A message that wraps to whatever width is left, rather than setting the window's
    minimum width (one long message made the window 1,150 px wide)."""
    lbl = QtWidgets.QLabel()
    lbl.setWordWrap(True)
    lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
    return lbl


class RawView(View):
    def __init__(self, page: AnalysisPage):
        super().__init__(page)
        self.events: A.Events | None = None
        self.unit_colors: dict[int, str] = {}     # 0-based unit -> the colour picked for it
        self._n_shown = 0                         # blocks overlaid
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(12)

        # ---- left: settings ------------------------------------------------------
        side = QtWidgets.QWidget()
        side.setMaximumWidth(360)
        left = QtWidgets.QVBoxLayout(side)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        self.channel = QtWidgets.QComboBox()
        self.channel.setToolTip("The channel to find action potentials on")
        self.rate_bin = UnitSpinBox(0.01, 600, "s", step=0.5)
        self.rate_bin.setValue(0.1)
        self.rate_bin.setToolTip("Spikes per second during a block are counted in bins this long")
        self.rate_bin.setMaximumWidth(76)
        self.point_size = QtWidgets.QSpinBox(minimum=1, maximum=12)
        self.point_size.setValue(self.main.settings.value("raster_point_size", 2, type=int))
        self.point_size.setToolTip("The size of the points in the raster")
        self.point_size.setMaximumWidth(56)

        box = QtWidgets.QGroupBox("Find action potentials")
        f = QtWidgets.QFormLayout(box)
        self.method = QtWidgets.QComboBox()
        self.method.addItem("Manual threshold", False)
        self.method.addItem("Bimodal (automatic)", True)
        self.method.setToolTip("Bimodal: the threshold goes in the valley between the many small noise "
                               "peaks and the few big spikes. If they don't form two groups, the threshold "
                               "below is used instead")
        self.threshold = QtWidgets.QLineEdit("5 SD")
        self.threshold.setToolTip("A level from the baseline, e.g. 50 mV or 0.05 V, or a number of noise "
                                  "SDs, e.g. 5 SD (a plain number means SDs). Or drag the red line")
        bip = QtWidgets.QHBoxLayout()
        self.biphasic = QtWidgets.QCheckBox("On")
        self.biphasic.setToolTip("Keep only events that also swing the other way, as a real extracellular "
                                 "spike does and many artifacts don't")
        self.bip_frac = QtWidgets.QDoubleSpinBox(minimum=0.05, maximum=1, value=0.3, singleStep=0.05, decimals=2)
        self.bip_frac.setToolTip("How far the other way, as a fraction of the threshold (0 to 1)")
        self.bip_ms = UnitSpinBox(0.1, 20, "ms", step=0.5)
        self.bip_ms.setValue(1.0)
        self.bip_ms.setToolTip("Within this long of the event's peak (before or after)")
        bip.addWidget(self.biphasic)
        bip.addWidget(self.bip_frac)
        bip.addWidget(QtWidgets.QLabel("within"))
        bip.addWidget(self.bip_ms)
        self.polarity = QtWidgets.QComboBox()
        self.polarity.setToolTip("Which way spikes go from the baseline: down, up, or either")
        for label, key in (("Negative (extracellular)", "negative"), ("Positive (intracellular)", "positive"),
                           ("Either", "either")):
            self.polarity.addItem(label, key)
        self.dead = UnitSpinBox(0.1, 100, "ms", step=0.5)
        self.dead.setValue(1.0)
        self.dead.setToolTip("After an event, no other can start for this long")
        self.pre = UnitSpinBox(0.1, 50, "ms", step=0.5)
        self.pre.setToolTip("How much of each event's waveform to keep before its peak")
        self.pre.setValue(1.0)
        self.post = UnitSpinBox(0.1, 50, "ms", step=0.5)
        self.post.setToolTip("How much of each event's waveform to keep after its peak")
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
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_clear.setToolTip("Clear the action potentials found. Changing a setting won't search again "
                                  "until you press Find action potentials")
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.btn_find, 1)
        buttons.addWidget(self.btn_clear)
        f.addRow(buttons)
        left.addWidget(box)
        self.btn_help = add_help(box, "spike_sorting")

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Unit", "Count", "Rate", "Amplitude", "Width", "Colour"])
        self.table.horizontalHeaderItem(4).setToolTip("Mean waveform: trough to peak")
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setToolTip("Point at a unit, or click it (Cmd/Shift-click for several), to see only "
                              "that unit on every graph")
        self.table.setMouseTracking(True)
        left.addWidget(self.table, 1)
        self.charts_slot = QtWidgets.QVBoxLayout()   # the Charts box, once the charts exist
        left.addLayout(self.charts_slot)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        self.btn_export_avg = QtWidgets.QPushButton("Export averages…")
        self.btn_export_avg.setToolTip("Save the average of the chosen blocks (each stimulus group's, when "
                                       "grouped) with its SD, SEM and n at every point, as a CSV file")
        self.btn_export_avg.clicked.connect(self._export_averages_dialog)
        self.btn_export = QtWidgets.QPushButton("Export events…")
        self.btn_export.setToolTip("Save every event (block, time, unit, amplitude) as a CSV file")
        self.btn_export.setEnabled(False)
        exports = QtWidgets.QHBoxLayout()
        exports.addWidget(self.btn_export)
        exports.addWidget(self.btn_export_avg)
        left.addLayout(exports)
        outer.addWidget(scroll_column(side))

        # ---- middle: the blocks, the raster, the rate ----------------------------------
        mid = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.overlay = plot_widget()
        self.stim_plot = plot_widget()              # the chosen blocks' stimuli
        self.raster = plot_widget()
        self.rate_plot = plot_widget()
        for pw, title, left_label in ((self.overlay, "Blocks overlaid", ""),
                                      (self.raster, "Events by block", "block"),
                                      (self.rate_plot, "Spikes per second", "spikes/s")):
            p = pw.getPlotItem()
            p.setTitle(title)
            p.setLabel("left", left_label)
            p.showGrid(x=True, y=True, alpha=0.2)
        # seconds with a prefix as you zoom (ms, µs)
        self.overlay.getPlotItem().setLabel("bottom", "time in block", units="s")
        self.raster.getPlotItem().setLabel("bottom", "time in block", units="s")
        self.rate_plot.getPlotItem().setLabel("bottom", "time in block", units="s")
        self.raster.setXLink(self.overlay)
        self.rate_plot.setXLink(self.overlay)                    # one time axis: they zoom together
        self.raster.getPlotItem().invertY(True)                  # block 1 at the top
        self.raster.getPlotItem().getAxis("left").setStyle(showValues=False)
        sp = self.stim_plot.getPlotItem()
        sp.showGrid(x=True, y=True, alpha=0.2)
        sp.setXLink(self.overlay)
        sp.getAxis("bottom").setStyle(showValues=False)
        self.stim_curve = OverlayCurve()
        self.stim_curve.mode = "peak"
        sp.addItem(self.stim_curve)
        self.stim_onsets = pg.ScatterPlotItem(size=10, symbol="t1", pen=None, brush=pg.mkBrush("#E69F00"))
        sp.addItem(self.stim_onsets)
        self.stim_plot.setMaximumHeight(170)
        self.rate_corner = PlotCorner(self.rate_plot, "Bins", self.rate_bin)
        self.raster_corner = PlotCorner(self.raster, "Size", self.point_size)
        for w in (self.stim_plot, self.overlay, self.raster, self.rate_plot):
            mid.addWidget(w)
        mid.setSizes([110, 360, 220, 180])
        self._mid = mid
        self._mid_axes = AxisGroup()                 # stacked plots line up
        self._mid_axes.set_plots([self.stim_plot, self.overlay, self.raster, self.rate_plot])
        outer.addWidget(mid, 3)

        # ---- right: sizes and shapes ---------------------------------------------------
        right_box = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right_box)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        norm = QtWidgets.QGridLayout()
        norm.addWidget(QtWidgets.QLabel("Normalize"), 0, 0, 2, 1)
        self.norm_sizes = QtWidgets.QCheckBox("AP sizes")
        self.norm_sizes.setToolTip("Each unit's histogram as a fraction of its own events, so units with "
                                   "different counts compare by shape")
        self.norm_isi = QtWidgets.QCheckBox("Intervals")
        self.norm_isi.setToolTip("Each unit's intervals as a fraction of its own intervals")
        self.norm_waves = QtWidgets.QCheckBox("Waveforms")
        self.norm_waves.setToolTip("Each unit's spikes divided by the average size of their peaks (the "
                                   "unit's average peak = ±1), so units compare by shape and spikes keep "
                                   "their sizes within a unit")
        self.norm_rate = QtWidgets.QCheckBox("Rate")
        self.norm_rate.setToolTip("Spikes per second during a block as a fraction of each unit's highest")
        for k, w in enumerate((self.norm_sizes, self.norm_isi, self.norm_waves, self.norm_rate)):
            norm.addWidget(w, k // 2, 1 + k % 2)
            w.toggled.connect(lambda *_: self._show_results() if self.events is not None else None)
        norm.setColumnStretch(3, 1)
        rv.addLayout(norm)
        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.pca = plot_widget()
        self.hist = plot_widget()
        self.isi = plot_widget()
        self.wave_plot = plot_widget()
        self.pca.getPlotItem().setTitle("PCA map of the units")
        self.hist.getPlotItem().setTitle("AP sizes")
        self.hist.getPlotItem().setLabel("left", "events")
        self.isi.getPlotItem().setTitle("Inter-spike intervals")
        self.isi.getPlotItem().setLabel("left", "intervals")
        self.isi.getPlotItem().setLabel("bottom", "ms between spikes of a unit")
        self.wave_plot.getPlotItem().setTitle("Waveforms by unit")
        self.wave_plot.getPlotItem().setLabel("bottom", "ms from the peak")
        for pw in (self.pca, self.wave_plot, self.hist, self.isi):
            pw.getPlotItem().showGrid(x=True, y=True, alpha=0.2)
            pw.setMinimumHeight(60)              # four of them still fit a small laptop screen
            right.addWidget(pw)
        for pw in (self.hist, self.isi, self.rate_plot, self.pca):
            pw.getPlotItem().getAxis("left").enableAutoSIPrefix(False)   # fractions stay plain decimals
        self.pca.getPlotItem().getAxis("bottom").enableAutoSIPrefix(False)
        right.setMinimumWidth(260)
        self._right_axes = AxisGroup()
        self._right_axes.set_plots([self.pca, self.wave_plot, self.hist, self.isi])
        rv.addWidget(right, 1)
        outer.addWidget(right_box, 2)
        self._right, self._right_box = right, right_box

        op = self.overlay.getPlotItem()
        self.trial_items: list[OverlayCurve] = []    # the trials shown, one item per colour
        self.marks = pg.ScatterPlotItem(size=9, pen=None)
        self.marks.setZValue(20)
        op.addItem(self.marks)
        # the highlighted unit's events, drawn over the trace in its colour
        self.event_hl = pg.PlotCurveItem(connect="finite")
        self.event_hl.setZValue(15)
        op.addItem(self.event_hl)
        self._hover_unit: int | None = None
        self._unit_items: dict[int, list] = {}      # unit -> its items on the size, interval and waveform plots
        self._rate_items: dict[int | None, object] = {}   # unit (None: all of them) -> its rate curve
        self._heights: tuple | None = None          # (events, each event's height on its trace)
        self.thr_line = pg.InfiniteLine(angle=0, movable=True)
        self.thr_line.setZValue(30)
        self.thr_line.setToolTip("Threshold: drag to set a fixed level")
        op.addItem(self.thr_line)
        self.thr_label = pg.InfLineLabel(self.thr_line, text="threshold", position=0.97, color="#e0524f",
                                         anchors=[(1, 1), (1, 1)])
        small = QtGui.QFont()
        small.setPixelSize(10)
        self.thr_label.setFont(small)
        self.raster_dots = pg.ScatterPlotItem(size=2, pen=None)
        self._rows: dict[int, int] = {}           # block -> its row in the raster
        self._end = 0.0                           # where the longest chosen block ends (s)
        self.raster.getPlotItem().addItem(self.raster_dots)
        # each block's stimulus group, as a coloured mark at the start of its row
        self.group_marks = pg.ScatterPlotItem(size=8, symbol="s", pen=None)
        self.group_marks.setZValue(5)
        self.raster.getPlotItem().addItem(self.group_marks)
        self.group_avgs: list[TraceCurve] = []
        self.bands: list = []                     # ± SD / SEM shading (Spread, in the Blocks panel)
        self.overlay_legend = op.addLegend(offset=(-10, 10))
        self.cur_band = pg.LinearRegionItem(movable=False, orientation="horizontal")
        self.cur_band.setZValue(-10)
        self.raster.getPlotItem().addItem(self.cur_band)

        # ---- signals -----------------------------------------------------------------
        self.channel.currentIndexChanged.connect(lambda *_: self._settings_changed())
        self.rate_bin.valueChanged.connect(lambda *_: self._draw_rate())
        self.btn_find.clicked.connect(self._find_pressed)
        self.btn_clear.clicked.connect(self.clear)
        self.point_size.valueChanged.connect(self._point_size_changed)
        self.btn_export.clicked.connect(self._export_dialog)
        self.thr_line.sigPositionChangeFinished.connect(self._threshold_dragged)
        self.table.itemSelectionChanged.connect(self._apply_highlight)
        self.table.cellEntered.connect(lambda r, _c: self._hover(r))
        self.table.viewport().installEventFilter(self)
        # Changing how events are found searches again, once the changes stop.
        self._find_timer = QtCore.QTimer(self, singleShot=True, interval=250)
        self._find_timer.timeout.connect(self._find_again)
        self.threshold.editingFinished.connect(self._detection_changed)
        for w in (self.method, self.polarity, self.units):
            w.currentIndexChanged.connect(lambda *_: self._detection_changed())
        for w in (self.dead, self.pre, self.post, self.bip_frac, self.bip_ms):
            w.valueChanged.connect(lambda *_: self._detection_changed())
        self.biphasic.toggled.connect(lambda *_: self._detection_changed())
        self._paused = False                      # Clear stops searching again until the next Find
        self.charts = ChartToggles(self.main.settings, "spike_sorting", [
            ("Stimulus", "The stimulus of every chosen block", None),
            ("Blocks", "The chosen blocks' traces or averages", self.overlay),
            ("Raster", "Each event in each chosen block", self.raster),
            ("Rate", "Spikes per second during a block", self.rate_plot),
            ("PCA map", "Each spike as a point, coloured by its unit", self.pca),
            ("Waveforms", "The spikes of each unit", self.wave_plot),
            ("AP sizes", "A histogram of each unit's spike sizes", self.hist),
            ("Intervals", "A histogram of the time between each unit's spikes", self.isi)], self._charts_changed)
        self.charts_slot.addWidget(self.charts)
        self.charts.apply()
        self.apply_theme(style.LIGHT)

    # -- data ------------------------------------------------------------------------------
    @property
    def blocks(self):
        return self.page.blocks

    def set_channels(self, names: list[str]) -> None:
        """The channels to sort spikes on: all but the stimulus channel (it has its own chart)."""
        stim = self.page.stim_channel.currentData()
        names = [n for n in names if n != stim]
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
        idx = list(range(len(self.blocks)))
        group = self.page.group_blocks()
        if group is not None:
            idx = sorted(set(idx) & set(group))
        name = self.channel.currentText()
        good = [b for b in self.blocks if b is not None]
        fs = good[0]["sample_rate"] if good else 0
        return [i for i in idx if self.page.has(i, name) and self.blocks[i]["sample_rate"] == fs]

    def chosen_or_none(self) -> list[int]:
        return self._chosen() if self.blocks else []

    def _charts_changed(self) -> None:
        """Hide a column with no charts left; the charts left in the right one share it."""
        self._draw_stimulus_visible()
        names = ("PCA map", "Waveforms", "AP sizes", "Intervals")
        self._right_box.setVisible(any(self.charts.is_on(n) for n in names))
        self._mid.setVisible(any(self.charts.is_on(n) for n in ("Stimulus", "Blocks", "Raster", "Rate")))
        even_out(self._right)

    def _trace(self, i: int) -> np.ndarray:
        return self.page.trace(i, self.channel.currentText())

    # -- drawing -----------------------------------------------------------------------------
    def _settings_changed(self) -> None:
        if self.events is not None:              # the same search, on what's now chosen
            self._find_timer.start()
        self.events = None
        self._show_results()
        self._draw_overlay()
        self.page.events_changed()

    def _averages(self, chosen: list[int], Y: np.ndarray, every: bool = False):
        """[(page number, label, mean, SD, SEM, n blocks)] across the chosen blocks (rows of Y): each
        page whose average is shown. every: with none, one of all of them (page None)."""
        rows = {i: r for r, i in enumerate(chosen)}
        sets = [(g, label, [rows[i] for i in blocks]) for g, label, blocks in self.page.plan(chosen)[0]]
        if every and not sets:
            sets = [(None, "All chosen blocks", list(range(len(chosen))))]
        out = []
        for g, label, picked in sets:
            mean, sd, sem, _ = A.mean_spread(Y[picked])
            out.append((g, label, mean, sd, sem, len(picked)))
        return out

    def _band(self, x: np.ndarray, mean: np.ndarray, spread: np.ndarray, color: str) -> None:
        """A shaded band from mean − spread to mean + spread."""
        p = self.overlay.getPlotItem()
        edges = []
        for sign in (1, -1):
            c = TraceCurve()
            c.setPen(pg.mkPen(None))
            c.setZValue(4)
            p.addItem(c)
            c.setData(x, mean + sign * np.nan_to_num(spread))
            edges.append(c)
        fill = QtGui.QColor(color)
        fill.setAlpha(55)
        band = pg.FillBetweenItem(edges[0], edges[1], brush=pg.mkBrush(fill))
        band.setZValue(4)
        p.addItem(band)
        self.bands += edges + [band]

    def _draw_trials(self, Y: np.ndarray, chosen: list[int], trials: list[int], fs: float) -> None:
        """The trials, one item for each colour, so hundreds of them stay quick."""
        rows = {i: r for r, i in enumerate(chosen)}
        by_color: dict[str, tuple[QtGui.QColor, list[int]]] = {}
        for i, c in self.page.trial_colors(trials).items():
            by_color.setdefault(c.name(QtGui.QColor.NameFormat.HexArgb), (c, []))[1].append(rows[i])
        p = self.overlay.getPlotItem()
        for color, picked in by_color.values():
            # Spikes are the point here: always draw min and max, whatever View says,
            # so they keep their height (and line up with the event marks).
            item = OverlayCurve()
            item.mode = "peak"
            item.setPen(pg.mkPen(color, width=1))
            p.addItem(item)
            item.set_traces(Y[picked], fs)
            self.trial_items.append(item)

    def _draw_overlay(self) -> None:
        self._show_message("")
        for item in self.group_avgs + self.bands + self.trial_items:
            self.overlay.getPlotItem().removeItem(item)
        self.group_avgs, self.bands, self.trial_items = [], [], []
        self.overlay_legend.clear()
        chosen = self.chosen_or_none()
        if chosen:
            fs = self.blocks[chosen[0]]["sample_rate"]
            n = max(len(self._trace(i)) for i in chosen)
            Y = np.full((len(chosen), n), np.nan)
            for r, i in enumerate(chosen):
                y = self._trace(i)
                Y[r, :len(y)] = y
            self._draw_trials(Y, chosen, self.page.plan(chosen)[1], fs)
            spread = self.page.spread()
            for g, label, mean, sd, sem, rows in self._averages(chosen, Y):
                x = np.arange(len(mean)) / fs
                color = self.page.group_color(g)
                item = TraceCurve(name=f"{label} (n={rows})")
                item.setPen(pg.mkPen(color, width=2.5))
                item.setZValue(6)
                self.overlay.getPlotItem().addItem(item)
                self.group_avgs.append(item)
                item.setData(x, mean)
                if spread:
                    self._band(x, mean, sd if spread == "sd" else sem, color)
            self._end = n / fs
            self._set_time_limits((0.0, self._end))
            self.overlay.getPlotItem().setXRange(0, self._end, padding=0)
            label_axis(self.overlay.getPlotItem(), "left", self.channel.currentText(),
                   self.page.unit_of(self.channel.currentText()))
        else:
            self._end = 0.0
            self._set_time_limits(None)
        self._set_rows(chosen)
        self.overlay.getPlotItem().setTitle(f"{len(chosen)} block(s)", color=self._fg())
        marks = [(i, self.page.group_of(i)) for i in chosen]
        marks = [(i, g) for i, g in marks if g is not None]
        self.group_marks.setData([0.0] * len(marks), [self._rows[i] for i, _ in marks],
                                 brush=[pg.mkBrush(self.page.group_color(g)) for _, g in marks])
        self._draw_stimulus()
        self.draw_current()

    def _set_time_limits(self, limits: tuple[float, float] | None) -> None:
        """The central plots share one time axis, ending where the data does."""
        for pw in (self.overlay, self.stim_plot, self.raster, self.rate_plot):
            pw.getPlotItem().vb.bounds[0] = limits

    def _set_rows(self, chosen: list[int]) -> None:
        """One raster row per chosen block."""
        self._rows = {i: r for r, i in enumerate(chosen, 1)}
        p = self.raster.getPlotItem()
        n = len(chosen)
        p.vb.bounds[1] = (0.5, n + 0.5) if n else None
        if n:
            p.setYRange(0.5, n + 0.5, padding=0)

    def _event_rows(self, ev: A.Events) -> np.ndarray:
        return np.array([self._rows.get(int(b), 0) for b in ev.block])

    def draw_current(self) -> None:
        i = self.page.index()
        row = self._rows.get(i)
        self.cur_band.setVisible(row is not None)
        if row is not None:
            self.cur_band.setRegion((row - 0.5, row + 0.5))
        self._apply_highlight()
        self._draw_threshold()

    def _draw_stimulus_visible(self) -> None:
        self.stim_plot.setVisible(bool(self.page.how()) and bool(self.chosen_or_none())
                                  and self.charts.is_on("Stimulus"))

    def _draw_stimulus(self) -> None:
        """The chosen blocks' stimulus channel above the traces (or the notes' times)."""
        how, chosen = self.page.how(), self.chosen_or_none()
        sp = self.stim_plot.getPlotItem()
        self.stim_curve.set_traces(np.empty((0, 0)), 1.0)
        self.stim_onsets.setData([], [])
        self._draw_stimulus_visible()
        if not how or not chosen:
            return
        ons = [(i, self.page.onsets(i)) for i in chosen]
        ons = [(i, on) for i, on in ons if on is not None and len(on)]
        if how == "notes":
            sp.setLabel("left", "Stimulus (notes)")
            if ons:
                x = np.concatenate([on for _, on in ons])
                self.stim_onsets.setData(x, np.zeros(len(x)))
            return
        traces = {i: self.page.trace(i, how) for i in chosen}
        traces = {i: y for i, y in traces.items() if y is not None}
        if not traces:
            return
        fs = self.blocks[chosen[0]]["sample_rate"]
        Y = np.full((len(traces), max(len(y) for y in traces.values())), np.nan)
        for r, y in enumerate(traces.values()):
            Y[r, :len(y)] = y
        self.stim_curve.set_traces(Y, fs)
        label_axis(sp, "left", how, self.page.unit_of(how))
        xs, ys = [], []
        for i, on in ons:
            if i in traces:
                y = traces[i]
                xs.append(on)
                ys.append(y[np.clip(np.round(np.asarray(on) * fs).astype(int), 0, len(y) - 1)])
        if xs:
            self.stim_onsets.setData(np.concatenate(xs), np.concatenate(ys))

    # -- highlighting a unit --------------------------------------------------------------------
    def highlighted_units(self) -> set[int]:
        """0-based units to highlight: the one pointed at, else the ones selected."""
        if self._hover_unit is not None:
            return {self._hover_unit}
        return {r.row() for r in self.table.selectionModel().selectedRows()}

    def select_units(self, units) -> None:
        self.table.clearSelection()
        mode = self.table.selectionMode()
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        for u in units:
            self.table.selectRow(u)
        self.table.setSelectionMode(mode)

    def _hover(self, row: int | None) -> None:
        if row != self._hover_unit:
            self._hover_unit = row
            self._apply_highlight()

    def eventFilter(self, obj, ev):
        if obj is self.table.viewport() and ev.type() == QtCore.QEvent.Type.Leave:
            self._hover(None)
        elif isinstance(obj, ColorButton) and ev.type() == QtCore.QEvent.Type.Enter:
            self._hover(obj.property("unit"))
        elif isinstance(obj, ColorButton) and ev.type() == QtCore.QEvent.Type.Leave:
            self._hover(None)
        return super().eventFilter(obj, ev)

    def color_of(self, u: int) -> str:
        """Unit u's colour (0-based): the one picked for it, else its place in the palette."""
        return self.unit_colors.get(int(u), unit_color(u))

    def set_unit_color(self, u: int, color: str) -> None:
        self.unit_colors[u] = color
        self._show_results()
        self.page.events_changed()

    def _brushes(self, units: np.ndarray) -> list:
        made = {int(u): pg.mkBrush(self.color_of(u)) for u in np.unique(units)}
        return [made[int(u)] for u in units]

    def _event_heights(self) -> np.ndarray:
        """Where each event is on its block's trace (worked out once per search)."""
        ev = self.events
        if self._heights is None or self._heights[0] is not ev:
            y = np.zeros(len(ev.t))
            for b in np.unique(ev.block):
                trace, m = self._trace(int(b)), ev.block == b
                if trace is not None:
                    k = np.round(ev.t[m] * self.blocks[int(b)]["sample_rate"]).astype(int)
                    y[m] = trace[np.clip(k, 0, len(trace) - 1)]
            self._heights = (ev, y)
        return self._heights[1]

    def _apply_highlight(self) -> None:
        """Mark the events on the chosen blocks. With units highlighted, every graph shows
        only those units, and their spikes are drawn over the blocks in their colour."""
        ev = self.events
        hl = self.highlighted_units() if ev is not None else set()
        self.event_hl.setData(np.empty(0), np.empty(0))
        for item in self.trial_items:                     # the highlighted spikes stand out from them
            item.setOpacity(0.35 if hl else 1.0)
        if ev is None or not len(ev.t):
            self.marks.setData([], [])
            self.raster_dots.setData([], [])
            return
        show = np.isin(ev.unit, list(hl)) if hl else np.ones(len(ev.t), bool)
        self.marks.setData(ev.t[show], self._event_heights()[show], brush=self._brushes(ev.unit[show]),
                           size=9 if hl else 6, pen=pg.mkPen(self._fg(), width=1) if hl else pg.mkPen(None))
        self.raster_dots.setData(ev.t[show], self._event_rows(ev)[show], brush=self._brushes(ev.unit[show]),
                                 size=self.point_size.value())
        if hl:
            self._draw_spikes(np.flatnonzero(show), hl)
        for u, items in self._unit_items.items():
            for item in items:
                item.setVisible(not hl or u in hl)
        self._show_rates(hl)

    def _point_size_changed(self, v: int) -> None:
        self.main.settings.setValue("raster_point_size", v)
        self._apply_highlight()

    def _draw_spikes(self, which: np.ndarray, hl: set[int]) -> None:
        """Events `which`, drawn over their blocks' traces."""
        ev = self.events
        which = which[::max(1, -(-len(which) // MAX_MARKED))]
        xs, ys = [], []
        for b in np.unique(ev.block[which]):
            y, fs = self._trace(int(b)), self.blocks[int(b)]["sample_rate"]
            if y is None:
                continue
            k = np.round(ev.t[which[ev.block[which] == b]] * fs).astype(int)
            span = np.arange(-int(round(self.pre.value() / 1000 * fs)), int(round(self.post.value() / 1000 * fs)) + 1)
            at = k[:, None] + span
            cut = np.where((at >= 0) & (at < len(y)), y[np.clip(at, 0, len(y) - 1)], np.nan)
            gap = np.full((len(k), 1), np.nan)
            xs.append(np.hstack([at / fs, gap]).ravel())
            ys.append(np.hstack([cut, gap]).ravel())
        if xs:
            color = self.color_of(next(iter(hl))) if len(hl) == 1 else self._fg()
            self.event_hl.setData(np.concatenate(xs), np.concatenate(ys), pen=pg.mkPen(color, width=2.5))

    def _show_rates(self, hl: set[int]) -> None:
        split = len(self._rate_items) > 1        # all units together is just one more curve
        for u, item in self._rate_items.items():
            item.setVisible(not hl or not split or u in hl)

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

    def cyclic_defaults(self) -> dict:
        """Spike Sorting's settings as the defaults for a cyclic measurement (Add analysis
        channel): the threshold as an absolute level on this channel, the direction and the
        dead time."""
        pol = self.polarity.currentData()
        out = {"channel": self.channel.currentText(), "dir": "below" if pol == "negative" else "above",
               "dead": self.dead.value(), "level": None}
        level = self._level() if self.blocks else None
        if level is not None:
            out["level"] = self._baseline() + (-level if pol == "negative" else level)
        return out

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
        if not self._paused:
            self._find_timer.start()

    def _find_again(self) -> None:
        if self.chosen_or_none():
            self.find()

    def _draw_threshold(self) -> None:
        level = self._level()
        if level is None:
            self.thr_line.hide()
            return
        sign = 1 if self.polarity.currentData() == "positive" else -1
        self.thr_line.blockSignals(True)
        self.thr_line.setValue(self._baseline() + sign * level)
        self.thr_line.blockSignals(False)
        self.thr_label.setFormat(f"threshold {self._fmt(level)}")
        self.thr_label.anchors = [(1, 1 if sign > 0 else 0)] * 2     # on the side away from the traces
        self.thr_label.updatePosition()
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
        self.thr_label.setFormat(f"threshold {self._fmt(level)}")
        if not self._paused:
            self._find_timer.start()

    # -- finding -----------------------------------------------------------------------------
    def clear(self) -> None:
        """Forget the action potentials, and don't search again until Find is pressed."""
        self._find_timer.stop()
        self._paused = True
        self.events = None
        self._show_results()
        self.draw_current()
        self.page.events_changed()
        self._show_message("Cleared. Press Find action potentials to search again.")

    def _find_pressed(self) -> None:
        self._find_timer.stop()
        if self.find() is not None:
            self._rescale_y()

    def _rescale_y(self) -> None:
        """Fit every graph's y axis to its data again; the raster shows all its rows."""
        for pw in (self.stim_plot, self.overlay, self.rate_plot, self.pca, self.wave_plot, self.hist, self.isi):
            pw.getPlotItem().enableAutoRange(axis="y")
        if self._rows:
            self.raster.getPlotItem().setYRange(0.5, len(self._rows) + 0.5, padding=0)

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
                               biphasic=self.bip_frac.value() if self.biphasic.isChecked() else None,
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
        self._paused = False
        self._show_results()
        self.draw_current()
        self.page.events_changed()
        return ev

    def _show_results(self) -> None:
        ev = self.events
        keep = {r.row() for r in self.table.selectionModel().selectedRows()}
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(False)
        wp, hp, ip, cp = (pw.getPlotItem() for pw in (self.wave_plot, self.hist, self.isi, self.pca))
        for p in (wp, hp, ip, cp):
            for item in list(p.listDataItems()):
                p.removeItem(item)
        self.btn_export.setEnabled(ev is not None and len(ev.t) > 0)
        self._draw_rate()
        if ev is None:
            self.raster_dots.setData([], [])
            return
        sizes = np.abs(ev.amplitude)
        edges = np.histogram_bin_edges(sizes, bins=40) if len(sizes) else np.array([0.0, 1.0])
        all_isi = A.intervals_ms(ev)
        # up to the 98th percentile, so a few long gaps don't squash the rest
        top = float(np.percentile(all_isi, 98)) if len(all_isi) else 100.0
        isi_edges = np.linspace(0, max(top, 1.0), 61)
        label_axis(hp, "bottom", "size from the baseline", self._unit())
        hp.setLabel("left", "fraction of the unit's events" if self.norm_sizes.isChecked() else "events")
        ip.setLabel("left", "fraction of the unit's intervals" if self.norm_isi.isChecked() else "intervals")
        if self.norm_waves.isChecked():
            wp.setLabel("left", "÷ the unit's average peak")
            wp.getAxis("left").enableAutoSIPrefix(False)
        else:
            label_axis(wp, "left", self.channel.currentText(), self._unit())
        peak = int(np.argmin(np.abs(ev.wave_t_ms))) if len(ev.wave_t_ms) else 0
        points, share = A.principal_components(ev.waves, 2) if len(ev.t) > 2 else (None, None)
        if points is not None:
            cp.setLabel("bottom", f"PC1 ({share[0]:.0%} of the differences)")
            cp.setLabel("left", f"PC2 ({share[1]:.0%})")

        def frac(counts, on):
            return counts / counts.sum() if on and counts.sum() else counts
        rng = np.random.default_rng(0)
        self._unit_items = {}
        for s in ev.summary():
            u = s["unit"] - 1
            color = QtGui.QColor(self.color_of(u))
            r = self.table.rowCount()
            self.table.insertRow(r)
            unit_item = QtWidgets.QTableWidgetItem(f"Unit {s['unit']}")
            unit_item.setIcon(style.dot_icon(self.color_of(u)))
            self.table.setItem(r, 0, unit_item)
            pick = ColorButton(self.color_of(u), lambda c, u=u: self.set_unit_color(u, c),
                               f"Pick Unit {s['unit']}'s colour, used on every graph")
            pick.setFixedWidth(26)
            pick.setText("▾")
            pick.setProperty("unit", u)
            pick.installEventFilter(self)            # pointing at it still highlights the unit
            self.table.setCellWidget(r, 5, pick)
            for c, text in enumerate((f"{s['count']}", f"{s['rate']:.2f} /s", self._fmt(s['amplitude']),
                                      f"{s['width_ms']:.2f} ms"), 1):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
            m = ev.unit == u
            fill = QtGui.QColor(color)
            fill.setAlpha(90)
            items = [hp.plot(edges, frac(np.histogram(sizes[m], edges)[0], self.norm_sizes.isChecked()),
                             stepMode="center", fillLevel=0, brush=fill, pen=pg.mkPen(color, width=1.5)),
                     ip.plot(isi_edges, frac(np.histogram(A.intervals_ms(ev, u), isi_edges)[0],
                                             self.norm_isi.isChecked()),
                             stepMode="center", fillLevel=0, brush=fill, pen=pg.mkPen(color, width=1.5))]
            if points is not None:                   # always every unit: this map is for comparing them
                cp.plot(points[m, 0], points[m, 1], pen=None, symbol="o", symbolSize=5, symbolPen=None,
                        symbolBrush=color)
            w = ev.waves[m]
            if self.norm_waves.isChecked() and len(w):
                size = float(np.abs(w[:, peak]).mean())
                w = w / size if size > 0 else w
            shown = rng.choice(len(w), min(MAX_WAVES, len(w)), replace=False)
            faint = QtGui.QColor(color)
            faint.setAlpha(40)
            if len(shown):
                xs = np.tile(np.append(ev.wave_t_ms, ev.wave_t_ms[-1]), len(shown))
                ys = np.column_stack([w[shown], np.full(len(shown), np.nan)]).ravel()
                items.append(wp.plot(xs, ys, pen=pg.mkPen(faint, width=1), connect="finite"))
            items.append(wp.plot(ev.wave_t_ms, w.mean(0), pen=pg.mkPen(color, width=3)))
            self._unit_items[u] = items
        if keep and max(keep) < self.table.rowCount():
            self.table.blockSignals(True)
            self.select_units(keep)
            self.table.blockSignals(False)
        self._apply_highlight()
        n = len(ev.t)
        msg = (f"{n} event(s) in {len(np.unique(ev.block))} block(s), {ev.n_units} unit(s); threshold "
               f"{self._fmt(ev.level)} from the baseline." if n else
               f"No events past the threshold ({self._fmt(ev.level)}): try a lower one.")
        self._show_message(" ".join([msg] + ev.notes))

    def _draw_rate(self) -> None:
        p = self.rate_plot.getPlotItem()
        for item in list(p.listDataItems()):
            p.removeItem(item)
        self._rate_items = {}
        ev = self.events
        if ev is None or not len(ev.t):
            return
        edges, rates = A.block_rate(ev, len(self._rows), self._end, self.rate_bin.value())
        if not len(edges):
            return
        norm = self.norm_rate.isChecked()
        scale = (lambda r: r / r.max() if r.max() > 0 else r) if norm else (lambda r: r)
        p.setLabel("left", "fraction of the highest" if norm else "spikes/s")
        if ev.n_units > 1:
            for u in range(ev.n_units):
                self._rate_items[u] = p.plot(edges, scale(rates[u]), stepMode="center",
                                             pen=pg.mkPen(self.color_of(u), width=1.5))
        self._rate_items[None] = p.plot(edges, scale(rates[None]), stepMode="center",
                                        pen=pg.mkPen(self._fg(), width=2))
        self._show_rates(self.highlighted_units())

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

    def export_averages(self, path: str | Path) -> Path:
        """time, then mean, SD, SEM and n for each average shown, as CSV."""
        chosen = self._chosen()
        if not chosen:
            raise ValueError("No blocks chosen.")
        fs = self.blocks[chosen[0]]["sample_rate"]
        n = max(len(self._trace(i)) for i in chosen)
        Y = np.full((len(chosen), n), np.nan)
        for r, i in enumerate(chosen):
            y = self._trace(i)
            Y[r, :len(y)] = y
        unit = self.page.unit_of(self.channel.currentText())
        sets = self._averages(chosen, Y, every=True)
        path = Path(path)
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            head = ["time_s"]
            for _, label, *_rest in sets:
                head += [f"{label} mean ({unit})", f"{label} SD ({unit})", f"{label} SEM ({unit})", f"{label} n"]
            w.writerow(head)
            counts = [A.mean_spread(Y[[r for r, i in enumerate(chosen) if g is None or
                                       i in self.page.shown[g][1]]])[3] for g, *_rest in sets]
            for k in range(n):
                row = [f"{k / fs:.6f}"]
                for (g, label, mean, sd, sem, _), cnt in zip(sets, counts):
                    row += [f"{mean[k]:.6g}", f"{sd[k]:.6g}", f"{sem[k]:.6g}", int(cnt[k])]
                w.writerow(row)
        return path

    def _export_averages_dialog(self) -> None:
        start = str(Path(self.main._start_dir()) / "averages.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export averages", start, "CSV (*.csv)")
        if path:
            try:
                self.export_averages(path)
                self._show_message(f"Saved the averages to {Path(path).name}.")
            except ValueError as e:
                self._show_message(str(e), error=True)

    def _export_dialog(self) -> None:
        start = str(Path(self.main._start_dir()) / "events.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export events", start, "CSV (*.csv)")
        if path:
            self.export(path)
            self._show_message(f"Saved {len(self.events.t)} event(s) to {Path(path).name}.")

    # -- looks ---------------------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        for pw in (self.overlay, self.stim_plot, self.raster, self.rate_plot, self.pca, self.wave_plot,
                   self.hist, self.isi):
            style_plot(pw, th)
        self.rate_corner.place()
        self.raster_corner.place()
        self.stim_curve.setPen(pg.mkPen(th.foreground, width=1.2))   # the stimulus, in the text colour
        trace = QtGui.QColor(cfgmod.PALETTE[1])
        self.thr_line.setPen(pg.mkPen("#e0524f", width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.raster_dots.setBrush(pg.mkBrush(th.foreground))
        band = QtGui.QColor(trace)
        band.setAlpha(45)
        self.cur_band.setBrush(pg.mkBrush(band))
        for line in self.cur_band.lines:
            line.setPen(pg.mkPen(None))
        if self.events is not None:
            self._show_results()


class CompareView(View):
    def __init__(self, page: AnalysisPage):
        super().__init__(page)
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
                                  "Spike Sorting tab (each spike smoothed into a Gaussian)")
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
        self.layout_mode.setToolTip("Show the channels on separate axes, or on one plot with each scaled to fit")
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
        self.before.setToolTip("How long before each stimulus to show")
        self.before.setValue(50)
        self.after = UnitSpinBox(1, 10000, "ms", step=5)
        self.after.setToolTip("How long after each stimulus to show")
        self.after.setValue(200)
        self.bin = UnitSpinBox(0.1, 1000, "ms", step=1)
        self.bin.setValue(5)
        self.bin.setToolTip("Bin width of the stimulus-triggered spike rate")
        self.sta_channel = QtWidgets.QComboBox()
        self.sta_channel.setToolTip("The channel averaged around each spike")
        self.sta_before = UnitSpinBox(0, 10000, "ms", step=1)
        self.sta_before.setToolTip("How long before each spike to average")
        self.sta_before.setValue(20)
        self.sta_after = UnitSpinBox(0, 10000, "ms", step=1)
        self.sta_after.setToolTip("How long after each spike to average")
        self.sta_after.setValue(5)
        f.addRow("Stimulus from", self.trigger)
        f.addRow("Before stimulus", self.before)
        f.addRow("After stimulus", self.after)
        f.addRow("Bin", self.bin)
        f.addRow("Average around spikes", self.sta_channel)
        f.addRow("Before spike", self.sta_before)
        f.addRow("After spike", self.sta_after)
        left.addWidget(box)
        add_help(box, "comparison")
        self.charts_slot = QtWidgets.QVBoxLayout()   # the Charts box, once the charts exist
        left.addLayout(self.charts_slot)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        left.addStretch(1)
        outer.addWidget(scroll_column(side))

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.traces_box = QtWidgets.QWidget()
        self.traces_lay = QtWidgets.QVBoxLayout(self.traces_box)
        self.traces_lay.setContentsMargins(0, 0, 0, 0)
        self.traces_lay.setSpacing(2)
        right.addWidget(self.traces_box)
        bottom = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.psth = plot_widget()
        self.sta = plot_widget()
        self._trace_axes = AxisGroup()
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
        self._bottom = bottom
        self.charts = ChartToggles(self.main.settings, "comparison", [
            ("Traces", "The highlighted block's channels", self.traces_box),
            ("Stimulus-triggered rate", "How spiking changes after a stimulus", self.psth),
            ("Spike-triggered average", "What a channel does around each spike", self.sta)], self._charts_changed)
        self.charts_slot.addWidget(self.charts)
        self.charts.apply()

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

    def _charts_changed(self) -> None:
        self._bottom.setVisible(self.charts.is_on("Stimulus-triggered rate")
                                or self.charts.is_on("Spike-triggered average"))

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
        """Use the Stimulus channel picked in the Blocks panel for the stimulus-triggered rate."""
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
            self.unit_pick.addItem(style.dot_icon(self.page.raw.color_of(u)), f"Unit {u + 1}", u)
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
                notes.append("No spike rate yet: find action potentials on the Spike Sorting tab first.")
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
            legend = p.addLegend(offset=(10, 10))
            multi = MultiAxis(p)                          # each on its own axis, fitted to the height
            for (label, unit, y), color in zip(series, colors):
                curve = TraceCurve()
                curve.mode = "peak"
                curve.setPen(pg.mkPen(color, width=1.5))
                curve.setData(t, y)
                multi.add(curve, label, unit, color)
                legend.addItem(curve, label)
            multi.fit()
            self._multi = multi                           # keep it (and its axes) alive
            plots = [pw]
        else:
            plots = []
            for (label, unit, y), color in zip(series, colors):
                pw = plot_widget()
                p = pw.getPlotItem()
                p.setTitle(label, size="9pt")                     # the name above, the unit beside
                label_axis(p, "left", "", unit)
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
            style_plot(pw, th)
            self.traces_lay.addWidget(pw, 1)
        # plain seconds on every row, so they read the same
        for pw in plots:
            pw.getPlotItem().getAxis("bottom").enableAutoSIPrefix(False)
        if self.layout_mode.currentData() == "overlaid":
            self._multi.recolor()                         # the theme reset the left axis's colour
        plots[-1].getPlotItem().setLabel("bottom", f"time in block {i + 1} (s)")
        plots[0].getPlotItem().setXRange(0, n / fs, padding=0)
        self._plots = plots
        self._trace_axes.set_plots(plots)             # the stacked traces line up

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
        spread = page.spread()
        tag = {"sd": ", ± SD across stimuli", "sem": ", ± SEM across stimuli"}.get(spread, "")

        def psth(trig: dict, color: str, name: str | None, filled: bool) -> int:
            """One stimulus-triggered rate: the mean over stimuli, with its spread."""
            centres, rates = A.triggered_rates(times, trig, self.before.value(), self.after.value(), b)
            if not len(rates):
                return 0
            mean, sd, sem, _ = A.mean_spread(rates)
            edges = np.append(centres - b / 2, centres[-1] + b / 2)
            c = QtGui.QColor(color)
            if spread:
                s_ = np.nan_to_num(sd if spread == "sd" else sem)
                hi = pp.plot(edges, mean + s_, stepMode="center", pen=pg.mkPen(None))
                lo = pp.plot(edges, np.maximum(mean - s_, 0), stepMode="center", pen=pg.mkPen(None))
                c.setAlpha(60)
                pp.addItem(pg.FillBetweenItem(hi, lo, brush=pg.mkBrush(c)))
                c.setAlpha(255)
            fill = QtGui.QColor(color)
            fill.setAlpha(90)
            pp.plot(edges, mean, stepMode="center", pen=pg.mkPen(c, width=2 if name else 1.5), name=name,
                    **({"fillLevel": 0, "brush": pg.mkBrush(fill)} if filled and not spread else {}))
            return len(rates)

        # the same as the Spike Sorting tab: a picked page's average, a picked block's trial
        averaged, trials = page.plan([k for k in chosen if k in triggers])
        colors = page.trial_colors(trials)
        sets = [(label, page.group_color(g), blocks) for g, label, blocks in averaged]
        sets += [(None, colors[k], [k]) for k in trials]
        self.psth_legend.clear()
        n_trig = 0
        for label, color, blocks in sets:
            n = len([t for k in blocks for t in triggers[k]])
            n_trig += psth({k: triggers[k] for k in blocks}, color, f"{label} (n={n})" if label and len(sets) > 1
                           else None, len(sets) == 1)
        if len(sets) > 1:
            pp.setTitle(f"Stimulus-triggered spike rate by {'stimulus group' if averaged else 'block'} "
                        f"({n_trig} stimuli{tag})", color=self._fg())
        else:
            pp.setTitle(f"Stimulus-triggered spike rate ({n_trig} stimuli{tag})" if n_trig else
                        "Stimulus-triggered spike rate: no stimuli found on that channel", color=self._fg())
        name = self.sta_channel.currentData()
        traces = {k: page.trace(k, name) for k in chosen} if name else {}
        traces = {k: v for k, v in traces.items() if v is not None}
        averaged, trials = page.plan(list(traces))
        colors = page.trial_colors(trials)
        total = 0
        for color, blocks in ([(page.group_color(g), blocks) for g, _, blocks in averaged]
                              + [(colors[k], [k]) for k in trials]):
            t, mean, sd, n = A.triggered_average({k: traces[k] for k in blocks}, fs,
                                                 {k: v for k, v in times.items() if k in blocks},
                                                 self.sta_before.value(), self.sta_after.value())
            if n:
                if spread:
                    s_ = sd if spread == "sd" else sd / np.sqrt(n)
                    hi = sp.plot(t, mean + s_, pen=pg.mkPen(None))
                    lo = sp.plot(t, mean - s_, pen=pg.mkPen(None))
                    fill = QtGui.QColor(color)
                    fill.setAlpha(50)
                    sp.addItem(pg.FillBetweenItem(hi, lo, brush=pg.mkBrush(fill)))
                sp.plot(t, mean, pen=pg.mkPen(color, width=2))
                total += n
        n = total
        if n:
            label_axis(sp, "left", name, page.unit_of(name))
        tag = {"sd": ", ± SD", "sem": ", ± SEM"}.get(spread, "")
        sp.setTitle(f"Spike-triggered average of {name} ({n} spikes{tag})" if n else
                    "Spike-triggered average: no spikes far enough from the block ends", color=self._fg())

    # -- looks -----------------------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        for pw in [self.psth, self.sta] + self._plots:
            style_plot(pw, th)
        if getattr(self, "_multi", None) is not None and self.layout_mode.currentData() == "overlaid":
            self._multi.recolor()
