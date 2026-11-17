"""The Analysis tab: every trial of the session, the action potentials in them, and how
the channels respond, on a whiteboard of charts that you put there (see canvas.py, board.py).

Left of the board, a strip folds out what every chart shares (sidepanels.py): Trials, which
picks the trials to analyze, with a check box to leave any out of everything; Groups,
which groups every chart shows and their colors (and Spread, the SD or SEM shaded round
averages); Channels (the stimulus and analyzed channels, gains and the stimulus fit); and
Spikes (threshold or bimodal, sorted into units, see analysis.py, run again whenever its
settings change until you Clear, with the units found, whose ticks decide what the charts
show). The middle is the DataPad (padpage.py), with the whiteboard's charts folded into it
as quick charts; a chart's settings show in Single Plot's chart focus (singleplot.py).

The trials are the ones being reviewed, or those recorded so far in live mode.
"""
from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path

import numpy as np
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import analysisfile
from . import colors
from . import dsp
from . import groupsfile
from . import style
from .trialsbrowser import TrialsBrowser
from .canvas import ChartCanvas
from .padpage import PadView
from .plotcanvas import PlotCanvasPage
from .singleplot import SinglePlotPage
from .sidepanels import GroupColors, Rail, SettingsPanel, drawer
from .stim import Stimulus
from .repeatfinder import RepeatFinder
from .spikefinder import SpikeFinder
from .viewkit import scroll_column
from .units import whole


class AnalysisPage(QtWidgets.QWidget):
    """The container: loads the blocks, owns the highlighted block, holds the canvas."""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self._paths: list[Path] = []
        self.blocks: list[dict | None] = []
        self._dirty = True
        self._analysis_from = None                # the session whose saved analysis is loaded
        self._saved: str | None = None            # what was last saved or loaded (_digest)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        self.source = _flexible_label()
        self.block_bar = QtWidgets.QWidget()      # the highlighted trial
        bar = QtWidgets.QHBoxLayout(self.block_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(QtWidgets.QLabel("Trial"))
        self.btn_prev = QtWidgets.QToolButton(text="◀", autoRaise=True)
        self.btn_prev.setToolTip("Previous trial (←)")
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, minimum=1, maximum=1)
        self.slider.setMinimumWidth(120)
        self.slider.setToolTip("Drag to flip through the trials; ← and → step one at a time")
        self.btn_next = QtWidgets.QToolButton(text="▶", autoRaise=True)
        self.btn_next.setToolTip("Next trial (→)")
        self.current = QtWidgets.QSpinBox(minimum=1, maximum=1)
        self.current.setToolTip("The highlighted trial")
        self.n_label = QtWidgets.QLabel()
        for w in (self.current, self.n_label):
            bar.addWidget(w)
        self.btn_prev.hide()                      # kept, unseen: ← and → still step through the trials
        self.btn_next.hide()
        self.slider.hide()                        # kept, unseen: the spin box and the arrows move through the trials
        lay.addLayout(top)

        # The Blocks panel: pick the blocks to analyze, and how.
        self.blocks_panel = TrialsBrowser(for_analysis=True)
        self.blocks_panel.selection.connect(self._blocks_picked)
        self.blocks_panel.step_requested.connect(self.step_list)
        self.blocks_panel.step_extend_requested.connect(lambda d: self.step_list(d, extend=True))
        self.blocks_panel.activated.connect(self._block_clicked)
        self.blocks_panel.organize_picked.connect(lambda k: self.main.set_organize(k, self._folder()))
        self.blocks_panel.regroup_requested.connect(self.main.regroup)
        self.blocks_panel.delete_requested.connect(
            lambda idx: self.main.delete_trials([p for i, p in enumerate(self._paths) if i in idx]))
        self.blocks_panel.split_requested.connect(
            lambda idx: self.main.split_trials([p for i, p in enumerate(self._paths) if i in idx]))
        self.blocks_panel.stimuli_of = lambda i: (len(self.blocks[i].get("stimuli") or [])
                                                  if 0 <= i < len(self.blocks) and self.blocks[i] is not None else 0)
        self.blocks_panel.tick_highlighted.connect(self.tick_highlighted)
        self.blocks_panel.btn_select.clicked.connect(self.show_all)
        self.blocks_panel.btn_deselect.clicked.connect(self.hide_all)
        self.blocks_panel.hide_changed.connect(self.set_hidden)
        self.blocks_panel.group_moved.connect(self.move_group)
        self.blocks_panel.sort_picked.connect(self.sort_groups)
        self.blocks_panel.rename_requested.connect(self.main._rename_dialog)
        self.blocks_panel.move_requested.connect(
            lambda i, page: self.main.move_block(i, page) if page else self.main._new_page_dialog(i))
        self.blocks_panel.reset_requested.connect(self.main._reset_pages_dialog)
        self.picked: list[int] = []               # the blocks selected; none means all of them
        self.picked_pages: list[str] = []         # the pages ticked whole: their averages are shown
        self._highlighted: tuple[list[int], list[str]] = ([], [])   # in the Trials list: just where you're looking
        self._avg_shown: str | None = None      # a group whose Average is highlighted (the preview shows it)
        self.shown: list[tuple[str, list[int]]] = []   # the pages of the Blocks panel
        self._looks = groupsfile.load(None)            # the colors saved with the session
        self.stim_channel = self.blocks_panel.stim_ch
        self.stim_channel.setToolTip("The channel that recorded the stimulus, if one did; you can set it any "
                                     "time after recording. Its pulses give exact stimulus times, and trials "
                                     "with the same stimulus are grouped. With None, the notes the app made as "
                                     "it sent each stimulus are used. Remembered for this session")
        self.group_note = _flexible_label()
        self.blocks_panel.layout().addWidget(self.group_note)
        self.spread_box = QtWidgets.QComboBox()
        for label, key in (("None", ""), ("± SD", "sd"), ("± SEM", "sem")):
            self.spread_box.addItem(label, key)
        self.spread_box.setToolTip("Shade every average with its standard deviation or standard error "
                                   "across the trials (trials or stimuli) it's made of")
        self.spread_box.currentIndexChanged.connect(lambda *_: self.spread_changed())
        self.page_map = QtWidgets.QComboBox()
        self.page_map.addItems(colors.PAGE_STYLES)
        self.page_map.setToolTip("Colors every group at once. Palette: a different color each; One color: all "
                                 "the same (Color… picks it); Shades of one color: pale to deep, first group to "
                                 "last; a color map: across its colors. A color you give a group (right-click it) "
                                 "stays. Saved with the session")
        self.trial_map = QtWidgets.QComboBox()
        self.trial_map.addItems(colors.TRIAL_STYLES)
        self.trial_map.setToolTip("How a group's trials are drawn. Solid: all in the group's color; Shades: "
                                  "pale to deep shades of it, first to last; Opacity: "
                                  "faint to solid, first to last; a color map: across its colors, first to "
                                  "last. Saved with the session")
        self.page_map.currentTextChanged.connect(lambda v: self._set_look("page_map", v))
        self.base_btn = QtWidgets.QPushButton("Color…")      # the one color (and the shades' hue)
        self.base_btn.setToolTip("The color every group shares (One color), or whose shades they take")
        self.base_btn.clicked.connect(self._pick_base_color)
        self.page_map.currentTextChanged.connect(lambda *_: self._show_base_btn())
        self._show_base_btn()
        self.trial_map.currentTextChanged.connect(lambda v: self._set_look("trial_map", v))
        self.groups: list[tuple[str, list[int]]] = []
        self.stim_channel.currentIndexChanged.connect(lambda *_: self._stim_channel_picked())

        self.gain_channel = QtWidgets.QComboBox()
        self.gain_channel.setToolTip("The channel to correct")
        self.gain = QtWidgets.QDoubleSpinBox(decimals=4, minimum=0.0001, maximum=1000000, value=1, prefix="÷ ")
        self.gain.setKeyboardTracking(False)
        self.gain.setMinimumWidth(56)                # the Blocks panel is narrow
        self.gain_channel.setMinimumWidth(50)
        self.gain.setToolTip("Divides this channel by the amplifier's gain in every analysis and analysis export, to give "
                             "the signal before the amplifier. The recorded trials never change. Saved with the session")
        self.gain_channel.currentIndexChanged.connect(lambda *_: self._show_gain())
        self.gain.valueChanged.connect(self._gain_edited)
        self.gain_row = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self.gain_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(self.gain_channel, 1)
        row.addWidget(self.gain, 1)
        self.fit_box = QtWidgets.QComboBox()
        self.fit_box.addItems(A.FITS)
        self.fit_box.setToolTip("Removes sampling noise and coarse voltage steps from the stimulus channel. Piecewise "
                                "linear: pulses, steps, ramps, custom shapes. Smooth: other shapes, without shifting "
                                "timing. Sine: a sine. Repeat average, Harmonics: anything that repeats. The recorded "
                                "trials never change. Saved with the session")
        self.fit_box.currentTextChanged.connect(self._fit_picked)

        self.raw = SpikeFinder(self)
        self.repeats = RepeatFinder(self)
        self.canvas = ChartCanvas(self)
        self.btn_save = QtWidgets.QPushButton("Save")
        self.btn_save.setToolTip("Save everything in one .newt file: the recordings, the spikes found and units, "
                                 "the DataPad, figures, and how NEWT looks now, so it all comes back when it's opened")
        self.btn_save.clicked.connect(self.main.save)
        self.canvas.layouts.hide()                # the layout picker: kept, unseen
        # The DataPad in the middle; the whiteboard's charts are its quick charts, folded away
        # until opened. Single Plot and Plot Canvas are tabs of their own beside it.
        self.pad_view = PadView(self)
        self.pad_view.hold_quick(self.canvas.toolbar, self.canvas)
        self.single_plot = SinglePlotPage(self)
        self._only_boxes = []
        self.single_plot._fbar2.insertWidget(0, self.only_selection_box())
        self._baseline_boxes: list = []
        self.baseline_on, self.baseline_sel = False, None
        self.single_plot._fbar2.insertWidget(1, self.baseline_box())
        gains2 = QtWidgets.QPushButton("Amplifier gain…")          # beside the Baseline here too
        gains2.setToolTip("The gain of the amplifier on each channel: every analysis divides the channel by it")
        gains2.clicked.connect(lambda: self.gains_dialog())
        self.single_plot._fbar2.insertWidget(2, gains2)
        self.plot_canvas = PlotCanvasPage(self)
        self.pad_view.table_changed.connect(self.single_plot.data_changed)
        self.pad_view.table_changed.connect(self.plot_canvas.data_changed)
        self.btn_quick = QtWidgets.QPushButton("Quick charts")
        self.btn_quick.setCheckable(True)
        self.btn_quick.setToolTip("Show the quick charts (responses, spikes, rates…) between the traces and the "
                                  "table, with their settings on the right")
        self.btn_quick.toggled.connect(self.set_quick)
        self.canvas.focus_requested.connect(lambda c: self.single_plot.focus(c))
        top.addWidget(self.btn_save)
        top.addWidget(self.btn_quick)
        self.btn_quick.hide()                     # the whiteboard is skipped: quick charts open in Single Plot
        top.addWidget(self.only_selection_box())
        top.addWidget(self.baseline_box())
        self.btn_gains = QtWidgets.QPushButton("Amplifier gain…")
        self.btn_gains.setToolTip("The gain of the amplifier on each channel: every analysis divides the channel by "
                                  "it, to give the voltage before the amplifier. The recordings never change")
        self.btn_gains.clicked.connect(self.gains_dialog)
        top.addWidget(self.btn_gains)
        self.block_bar.hide()                     # the Trials list picks the trial now: kept, unseen
        top.addWidget(self.block_bar)
        top.addWidget(self.source, 1)
        self.blocks_panel.add_setting("Analyze", self.raw.channel)
        self.blocks_panel.add_group_setting("Spread", self.spread_box)
        self.blocks_panel.add_group_setting("Groups", self.page_map)
        self.blocks_panel.add_group_setting("", self.base_btn)
        self.blocks_panel.add_group_setting("Trials", self.trial_map)
        # how solid trials are drawn: Auto (by the Trials style) or a level you set
        op = QtWidgets.QWidget()
        opl = QtWidgets.QHBoxLayout(op)
        opl.setContentsMargins(0, 0, 0, 0)
        self.opacity_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, minimum=0, maximum=100)
        self.opacity_slider.setToolTip("How solid each trial is drawn. All the way left: Auto (fainter the more "
                                       "trials there are, as the Trials style sets). Saved with the session")
        self.opacity_label = QtWidgets.QLabel("Auto")
        self.opacity_label.setMinimumWidth(34)
        opl.addWidget(self.opacity_slider, 1)
        opl.addWidget(self.opacity_label)
        self._opacity_timer = QtCore.QTimer(self, singleShot=True, interval=150)    # redraw once you stop
        self._opacity_timer.timeout.connect(lambda: self._set_look("trial_opacity", self.opacity_slider.value()))
        self.opacity_slider.valueChanged.connect(self._opacity_moved)
        self.blocks_panel.add_group_setting("Opacity", op)
        self.blocks_panel.add_setting("Amplifier Gain", self.gain_row)
        self.blocks_panel.add_setting("Stim fit", self.fit_box)
        self.blocks_panel.color_requested.connect(self.pick_color)
        self.blocks_panel.color_cleared.connect(lambda name: self.set_page_color(name, None))
        # left: the strip and the panel it folds out; right: the picked chart's settings
        self.group_colors = GroupColors(self)
        self.blocks_panel.group_extras.layout().insertWidget(0, self.group_colors)
        self.blocks_panel.btn_groups.clicked.connect(lambda: self.open_panel("groups"))
        self.rail = Rail()
        self.drawer = QtWidgets.QWidget()            # one panel shows at a time; the hidden ones take no room
        drawer_row = QtWidgets.QHBoxLayout(self.drawer)
        drawer_row.setContentsMargins(0, 0, 0, 0)
        self.blocks_panel.setMinimumWidth(260)        # as wide as the other panels: the names and buttons fit
        self.panels = {"trials": self.blocks_panel, "groups": drawer(self.blocks_panel.group_extras),
                       "channels": drawer(self.blocks_panel.extras), "spikes": drawer(self.raw)}
        for w in self.panels.values():
            drawer_row.addWidget(w)
            w.hide()
        self.rail.picked.connect(self.open_panel)
        self.side = SettingsPanel(self)
        self.side_scroll = scroll_column(self.side)
        self.side_scroll.setMinimumWidth(300)         # the settings fit without cutting off their right
        self.side_scroll.setMaximumWidth(340)
        self.body = QtWidgets.QHBoxLayout()
        self.body.setSpacing(0)
        self.body.addWidget(self.rail)
        self.body.addWidget(self.drawer)
        self.body.addWidget(self.pad_view, 1)
        self.side_scroll.hide()                       # shown in Single Plot's chart focus, where charts change
        lay.addLayout(self.body, 1)
        self.open_panel(self.main.settings.value("analysis/panel", "trials"))

        self.btn_prev.clicked.connect(lambda: self.step(-1))
        self.btn_next.clicked.connect(lambda: self.step(1))
        self.slider.valueChanged.connect(self.current.setValue)
        self.current.valueChanged.connect(self._current_changed)
        self.canvas.start()
        self.btn_quick.setChecked(False)
        self.set_quick(False)

    def set_quick(self, on: bool) -> None:
        """Show or hide the quick charts (their settings are in Single Plot's chart focus)."""
        self.pad_view.set_quick(on)
        self.btn_quick.setText("Hide quick charts" if on else "Show quick charts")
        self.main.settings.setValue("analysis/quick", "true" if on else "false")

    def open_plot(self, spec: dict) -> None:
        """A new figure of DataPad columns, in Single Plot."""
        self.single_plot.editing = None
        self.single_plot.show_spec(spec)
        self.show_sub("singleplot")

    def show_sub(self, pid: str) -> None:
        self.main.tabs.show_tab(pid)

    # -- the highlighted block -----------------------------------------------------------
    def index(self) -> int:
        return self.current.value() - 1

    def step(self, d: int) -> None:
        self.current.setValue(self.current.value() + d)          # the spin box keeps it in range

    def step_list(self, d: int, extend: bool = False) -> None:
        """Up and Down: on to the entry before or after in the Trials list (each group's Average,
        then its trials), highlighting just it, opening its group and closing the one left. With
        Shift (extend): on to the next trial, keeping the ones highlighted."""
        stops = []
        for name, blocks in self.shown:
            if len(blocks) >= 2 and not extend:
                stops.append(("avg", name))
            stops += [("block", i) for i in blocks]
        if not stops:
            return
        here = ("avg", self._avg_shown) if self._avg_shown else ("block", self.index())
        pos = stops.index(here) if here in stops else (0 if d > 0 else len(stops) - 1) - d
        new = stops[max(0, min(len(stops) - 1, pos + d))]
        if extend:
            picked = sorted(set(self._highlighted[0]) | ({here[1]} if here[0] == "block" else set()) | {new[1]})
            self._show_entry(new)
            self._highlighted = (picked, [])
            self.blocks_panel.select(picked)
            self.blocks_panel.mark(new, picked)
            return
        self._show_entry(new)
        self._highlighted = ([new[1]], []) if new[0] == "block" else ([], [new[1]])
        self.blocks_panel.reveal(new, here if here != new else None)

    def _show_entry(self, entry) -> None:
        """Look at a Trials list entry: a trial, or ("avg", group) the group's average."""
        if entry[0] == "avg":
            self._avg_shown = entry[1]
            self.update_preview()
            return
        self._avg_shown = None
        if self.index() == entry[1]:
            self.update_preview()
        else:
            self.current.setValue(entry[1] + 1)

    def _current_changed(self, v: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.raw.current_changed()
        self.canvas.current_changed()
        self.pad_view.current_changed()
        self.update_preview()

    def update_preview(self) -> None:
        """The Trials list's preview: every recorded channel of the highlighted trial (or of the
        group's average, with its Average highlighted)."""
        if self._avg_shown:
            self._preview_average(self._avg_shown)
            return
        i = self.index()
        b = self.blocks[i] if 0 <= i < len(self.blocks) else None
        if b is None:
            self.blocks_panel.preview.show_trial("", [])
            return
        names = list(b["channel_names"])
        title, color = self._paths[i].stem, None
        for g, (name, blocks) in enumerate(self.shown):
            if i in blocks:                       # the trial's name in its group, and the group's color
                title, color = f"{name} {blocks.index(i) + 1}", self.group_color(g)
                break
        self.blocks_panel.preview.show_trial(
            title, [(n, self.recorded(i, n), b["sample_rate"]) for n in names], color)

    def _preview_average(self, name: str) -> None:
        """Every recorded channel averaged sample by sample over the group's trials, each from
        its start: the average stimulus and response."""
        groups = dict(self.shown)
        idx = [i for i in groups.get(name, []) if 0 <= i < len(self.blocks) and self.blocks[i] is not None]
        if len(idx) < 2:
            self.blocks_panel.preview.show_trial("", [])
            return
        first = self.blocks[idx[0]]
        fs = first["sample_rate"]
        idx = [i for i in idx if self.blocks[i]["sample_rate"] == fs]
        chans = []
        for n in first["channel_names"]:
            rows = [np.asarray(self.recorded(i, n), float) for i in idx if n in self.blocks[i]["channel_names"]]
            if not rows:
                continue
            Y = np.full((len(rows), max(len(r) for r in rows)), np.nan)
            for r, y in enumerate(rows):
                Y[r, :len(y)] = y
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                chans.append((n, np.nanmean(Y, axis=0), fs))
        g = next((k for k, (nm, _) in enumerate(self.shown) if nm == name), 0)
        self.blocks_panel.preview.show_trial(f"{name} · average of {len(idx)}", chans, self.group_color(g))

    # -- the blocks --------------------------------------------------------------------------
    @property
    def events(self) -> A.Events | None:
        return self.raw.events

    def source_changed(self) -> None:
        """The session or its blocks changed: reload when next shown."""
        self._dirty = True
        if self.isVisible():
            self.refresh()

    def catch_up(self) -> None:
        """Read the blocks again if they've changed."""
        if self._dirty or self._paths != self.main.analysis_blocks():
            self.refresh()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.dock_panels(self.body)               # the Trials, Groups… rail comes home
        self.catch_up()

    def dock_panels(self, lay: QtWidgets.QBoxLayout) -> None:
        """Put the left rail (Trials, Groups, Channels, Spikes) and its drawer at the start of
        `lay`: the DataPad's, or Single Plot's, whichever tab is showing."""
        for k, w in enumerate((self.rail, self.drawer)):
            if lay.indexOf(w) == k:
                continue
            old = w.parentWidget().layout() if w.parentWidget() is not None else None
            if old is not None:
                old.removeWidget(w)
            lay.insertWidget(k, w)
            w.show() if w is self.rail else w.setVisible(bool(self.panel_key()))

    def panel_key(self) -> str:
        return self.main.settings.value("analysis/panel", "trials") or ""

    def open_panel(self, key: str) -> None:
        """Fold out one of the left panels (Trials, Groups, Channels, Spikes), or none ("")."""
        key = key if key in self.panels else ""
        self.rail.set_open(key)
        self.drawer.setVisible(bool(key))
        for k, w in self.panels.items():
            w.setVisible(k == key)
        self.main.settings.setValue("analysis/panel", key)

    def panel(self) -> str:
        """The left panel folded out, or ""."""
        if not self.drawer.isVisibleTo(self):
            return ""
        return next(k for k, w in self.panels.items() if w.isVisibleTo(self.drawer))

    def _read(self, path: Path) -> dict:
        return self.main.store.read(path)            # shared with review and the Blocks panel

    def _signature_of(self, paths: list[Path], how: str):
        """sig(i, trace, fs): block i's stimulus on channel `how`, worked out once per file."""
        store = self.main.store
        return lambda i, y, fs: store.memo(paths[i], ("stimulus", how), lambda: A.stimulus_signature(y, fs))

    def _fitted_signature_of(self, paths: list[Path], how: str):
        """Like _signature_of, but reading the stimulus fit when one is chosen (and the gain)."""
        fit = self._looks["stim_fit"]
        if not fit or fit == "Off" or how == "notes":
            return self._signature_of(paths, how)
        hand, store = self.repeats.setting(), self.main.store
        return lambda i, y, fs: store.memo(
            paths[i], ("stimulus", how, "fit", fit, self.gain_of(how), hand),
            lambda: A.stimulus_signature(self.trace(i, how), fs))

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
            self.source.setText("No trials yet. Record some, or open a session or .mat file (File menu).")
        else:
            durs = [b["duration_s"] for b in good]
            src = self.main.review_dir.name if self.main.review_dir else "this session"
            lo, hi = f"{min(durs):.3g}", f"{max(durs):.3g}"
            self.source.setText(f"<b>{src}</b>: {n} trial(s), {lo if lo == hi else lo + '–' + hi} s each, "
                                f"{whole(good[0]['sample_rate'])}")
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
        self.canvas.channels_changed()
        self._fill_stim_channel(recorded)             # the stimulus is a recorded channel
        self.raw.set_channels(names)
        keep = self.gain_channel.currentText()
        self.gain_channel.blockSignals(True)
        self.gain_channel.clear()
        self.gain_channel.addItems(recorded)
        self.gain_channel.setCurrentIndex(max(0, self.gain_channel.findText(keep)))
        self.gain_channel.blockSignals(False)
        self._show_gain()
        self.repeats.stimulus_channel_changed()
        self._load_analysis()
        self.raw.trials_changed()                     # find the spikes in trials not searched yet
        self.pad_view.trials_changed()

    # -- saving ---------------------------------------------------------------------------------
    def _stems(self) -> list[str]:
        return [p.stem for p in self._paths]

    def _analysis_state(self) -> dict:
        return {"spikes": self.raw.state(), "pad": self.pad_view.pad.state(),
                "figure": self.single_plot.state(), "plot_canvas": self.plot_canvas.state(),
                "baseline": {"on": self.baseline_on,
                             "sel": self.baseline_sel.to_dict() if self.baseline_sel is not None else None}}

    def _digest(self) -> str:
        """What there is to save, as one string, to tell if it's changed. A column's automatic
        title is left out: it follows what the column measures, and is only filled in when
        the DataPad works out its table (so it changes just by opening a session)."""
        ev = self.raw.events
        spikes = (ev.t.tobytes() + ev.unit.tobytes()) if ev is not None else b""
        state = self._analysis_state()
        state["pad"]["columns"] = [{**c, "title": ""} if c.get("title_auto") else c for c in state["pad"]["columns"]]
        return hashlib.sha1(json.dumps(state, sort_keys=True).encode() + spikes).hexdigest()

    def save_analysis(self) -> bool:
        """Save the analysis in the session's folder, beside its recordings. False if there's no
        session to save it in."""
        folder = self._folder()
        if folder is None:
            self.main.statusBar().showMessage("Record a trial or open a session first: analysis is saved with it.", 8000)
            return False
        if self.raw._find_timer.isActive():       # a search waiting to run again: run it now, so it's saved
            self.raw._find_timer.stop()
            self.raw._find_again()
        analysisfile.save(folder, self._analysis_state(), self.raw.events, self._stems())
        self._saved = self._digest()
        return True

    def _load_analysis(self) -> None:
        """Bring back what was saved with this session, the first time it's opened."""
        folder = self._folder()
        if folder == self._analysis_from:
            return
        self._analysis_from = folder
        if folder is not None and analysisfile.exists(folder):
            try:
                state, events = analysisfile.load(folder, self._stems())
                self.raw.restore(state["spikes"], events)
                self.pad_view.pad.restore(state.get("pad"))
                self.plot_canvas.restore(state.get("plot_canvas"))
                b = state.get("baseline") or {}
                from .padmodel import Selection
                self.baseline_sel = Selection.from_dict(b["sel"]) if b.get("sel") else None
                self.set_baseline_on(bool(b.get("on")) and self.baseline_sel is not None, refresh=False)
                self.single_plot.show_spec(state.get("figure"))
                self.pad_view.restored()
            except (OSError, ValueError, KeyError, TypeError) as e:
                self.main.statusBar().showMessage(f"Couldn't read the saved analysis: {e}", 10000)
        self._saved = self._digest()

    def app_state(self) -> dict:
        """What Analysis shows, for a .newt file."""
        stems = self._stems()
        return {"stim_channel": self.stim_channel.currentData() or "", "current": self.index(),
                "picked": [stems[i] for i in self.picked if i < len(stems)], "picked_pages": list(self.picked_pages),
                "panel": self.panel(), "canvas": self.canvas.app_state()}

    def restore_app_state(self, s: dict) -> None:
        self.catch_up()
        i = self.stim_channel.findData(s["stim_channel"])
        if i >= 0 and i != self.stim_channel.currentIndex():
            self.stim_channel.setCurrentIndex(i)
        self.canvas.restore_app_state(s["canvas"])
        self.groups_changed()                     # the ticks were saved beside the trials
        self.current.setValue(s["current"] + 1)
        self._analysis_from = None                # picking the blocks cleared the spikes: as saved again
        self._load_analysis()
        self.open_panel(s.get("panel", "trials"))

    def has_unsaved_analysis(self) -> bool:
        """Whether there's analysis that would be lost."""
        folder = self._folder()
        if folder is None or self._saved is None:
            return False
        anything = self.raw.events is not None or bool(self.pad_view.pad.columns) or analysisfile.exists(folder)
        return bool(anything) and self._digest() != self._saved

    def resolve_unsaved(self) -> bool:
        """True if it's OK to leave this session's analysis."""
        if not self.has_unsaved_analysis():
            return True
        choice = self.main._ask_unsaved_analysis()
        if choice == "save":
            return self.save_analysis()
        return choice == "discard"

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

    def gain_of(self, name: str) -> float:
        return self._looks["gains"].get(name, 1.0)

    def recorded(self, i: int, name: str) -> np.ndarray | None:
        """A channel of block i with its gain, before any stimulus fit."""
        b = self.blocks[i] if 0 <= i < len(self.blocks) else None
        if b is None:
            return None
        if name in b["channel_names"]:
            y = b["data"][b["channel_names"].index(name)]
            g = self.gain_of(name)
            return y if g == 1 else y / g
        calc = getattr(self, "_calcs", {}).get(name)
        if calc is None or not self._chain.needs.get(name, {""}) <= set(b["channel_names"]):
            return None
        gains = self._looks["gains"]
        env = {n: y / gains.get(n, 1.0) for n, y in zip(b["channel_names"], b["data"])}
        # worked out once per block file (zero-phase), shared with the Acquisition tab
        key = ("calcs", self._chain.key()) if not gains else ("calcs", self._chain.key(), tuple(sorted(gains.items())))
        ys = self.main.store.memo(self._paths[i], key, lambda: self._chain.offline(env, b["sample_rate"]))
        return ys[[c.name for c in self._chain.calcs].index(name)]

    # -- quick charts on just the selection ------------------------------------------------------
    # -- a baseline taken off every chart -----------------------------------------------------
    def baseline_box(self) -> QtWidgets.QWidget:
        """Baseline: off ▾ — one menu: subtract each trial's mean over a segment, over the stretch
        selected on the DataPad's preview, or over the first 50 ms; or off (one wherever charts are)."""
        b = QtWidgets.QPushButton()                  # a button like Amplifier gain…, opening its menu
        b.setToolTip("Baseline correction: each trial's mean over the window you pick is taken off its traces, "
                     "in the quick charts, the DataPad's preview and its measurements (not the stimulus "
                     "channel). The recordings never change")
        menu = QtWidgets.QMenu(b)
        menu.aboutToShow.connect(lambda m=menu: self._fill_baseline_menu(m))
        b.setMenu(menu)
        self._baseline_boxes.append(b)
        self._show_baseline()
        return b

    def _fill_baseline_menu(self, menu: QtWidgets.QMenu) -> None:
        from .padmodel import Selection, send_of
        menu.clear()
        head = menu.addAction("Subtract each trial's mean over:")
        head.setEnabled(False)
        cur = self.baseline_sel if self.baseline_on else None

        def item(text: str, sel) -> None:
            a = menu.addAction(text, lambda: self._baseline_to(sel))
            a.setCheckable(True)
            a.setChecked(cur is not None and sel is not None and cur.to_dict() == sel.to_dict())
        i = self.index()
        segs = self.pad_view.src.segments(i) if 0 <= i < len(self.blocks) else []
        for letter, t0, t1, part in segs:
            if send_of(letter)[0] != 1:            # one baseline per trial: the first stimulus's segments
                continue
            item(f"Segment {letter}  ·  {str(part.get('kind', '')).capitalize()} {(t1 - t0) * 1000:.4g} ms",
                 Selection.seg(letter))
        q = self.pad_view.quick_sel
        if q is not None and q.is_time() and not q.is_point() and q.t1 > q.t0:
            item(f"The stretch selected on the preview ({q.label()})", Selection.from_dict(q.to_dict()))
        item("The first 50 ms", Selection.time(0.0, 0.05))
        if cur is not None and not any(a.isChecked() for a in menu.actions()):
            item(f"{cur.label()} (set before)", cur)
        menu.addSeparator()
        off = menu.addAction("Off: no baseline", lambda: self.set_baseline_on(False))
        off.setCheckable(True)
        off.setChecked(not self.baseline_on)
        if not segs:
            note = menu.addAction("Drag across the DataPad's preview to pick a stretch of your own")
            note.setEnabled(False)

    def _baseline_to(self, sel) -> None:
        self.baseline_sel = sel
        self.set_baseline_on(True)

    def _show_baseline(self) -> None:
        sel = self.baseline_sel
        text = f"Baseline: {sel.label()}" if self.baseline_on and sel is not None else "Baseline: off"
        for b in self._baseline_boxes:
            b.setText(text + "…")

    def set_baseline_from_selection(self) -> None:
        pv = self.pad_view
        sel = pv.quick_sel or pv._view_base()
        if sel is None or sel.is_point() or sel.is_phase():
            self.main.statusBar().showMessage("Pick the baseline first: click a segment (A, say) or drag a stretch "
                                              "on the DataPad's preview, then Set baseline", 8000)
            return
        from .padmodel import Selection
        self.baseline_sel = Selection.from_dict(sel.to_dict())
        self.set_baseline_on(True)

    def set_baseline_on(self, on: bool, refresh: bool = True) -> None:
        if on and self.baseline_sel is None:      # nothing set yet: the first segment, else the first 50 ms
            from .padmodel import Selection
            i = self.index()
            segs = self.pad_view.src.segments(i) if 0 <= i < len(self.blocks) else []
            self.baseline_sel = Selection.seg(segs[0][0]) if segs else Selection.time(0.0, 0.05)
        self.baseline_on = on
        self._show_baseline()
        if refresh:
            self.canvas.refresh()
            self.pad_view.data_changed()          # the DataPad measures without the baseline too

    def baseline_window(self, i: int) -> tuple[float, float] | None:
        if not self.baseline_on or self.baseline_sel is None:
            return None
        segs = self.pad_view.src.segments(i)
        w = self.baseline_sel.window({s[0]: (s[1], s[2]) for s in segs}, [s[0] for s in segs])
        return w if w is not None and w[1] > w[0] else None

    def baseline_offset(self, i: int, name: str) -> float:
        """What the baseline takes off trial i's channel (0 when it's off, or for the stimulus)."""
        w = self.baseline_window(i) if 0 <= i < len(self.blocks) and self.blocks[i] is not None else None
        if w is None or name == self.stim_channel.currentData():
            return 0.0
        y = self.trace(i, name)
        if y is None:
            return 0.0
        fs = self.blocks[i]["sample_rate"]
        part = y[max(0, int(round(w[0] * fs))):int(round(w[1] * fs))]
        return float(np.nanmean(part)) if len(part) else 0.0

    def _less_baseline(self, i: int, name: str, y):
        """y with trial i's mean over the baseline window taken off (not the stimulus channel)."""
        w = self.baseline_window(i)
        if y is None or w is None or name == self.stim_channel.currentData():
            return y
        fs = self.blocks[i]["sample_rate"]
        part = y[max(0, int(round(w[0] * fs))):int(round(w[1] * fs))]
        if not len(part):
            return y
        return y - float(np.nanmean(part))

    def only_selection_box(self) -> QtWidgets.QCheckBox:
        """A tick: quick charts on only the DataPad's selection (there's one wherever charts are)."""
        box = QtWidgets.QCheckBox("Charts: only the selection")
        box.setToolTip("Quick charts show just the selected segments (or stretch) of each trial, from its "
                       "start, and only the spikes in it. Select on the DataPad's preview")
        box.setChecked(bool(getattr(self, "chart_only", False)))
        box.toggled.connect(self.set_chart_only)
        self._only_boxes.append(box)
        return box

    def set_chart_only(self, on: bool) -> None:
        self.chart_only = on
        for b in self._only_boxes:
            b.blockSignals(True)
            b.setChecked(on)
            b.blockSignals(False)
        self.selection_for_charts()

    def selection_for_charts(self) -> None:
        """The DataPad's selection changed (or the tick): the quick charts follow it."""
        sel = self.pad_view._view_base() if getattr(self, "chart_only", False) else None
        label = sel.label() if sel is not None else ""
        for b in self._only_boxes:
            b.setText(f"Charts: only {label}" if label else "Charts: only the selection")
        self.set_chart_selection(sel)

    def set_chart_selection(self, sel) -> None:
        """Quick charts show only this stretch of each trial (a padmodel Selection: segments, found
        in every trial, or a stretch of time), from its start; None: whole trials."""
        if repr(sel) == repr(getattr(self, "chart_sel", None)):
            return
        self.chart_sel = sel
        self.canvas.refresh()

    def chart_window(self, i: int) -> tuple[float, float] | None:
        sel = getattr(self, "chart_sel", None)
        if sel is None:
            return None
        segs = self.pad_view.src.segments(i)
        w = sel.window({s[0]: (s[1], s[2]) for s in segs}, [s[0] for s in segs])
        return w if w is not None and w[1] > w[0] else None

    def _crop(self, i: int, y):
        w = self.chart_window(i)
        if y is None or w is None:
            return y
        fs = self.blocks[i]["sample_rate"]
        return y[max(0, int(round(w[0] * fs))):int(round(w[1] * fs))]

    def chart_trace(self, i: int, name: str) -> np.ndarray | None:
        """trace(), as the quick charts see it: just the selection, with only the selection on."""
        return self._crop(i, self._less_baseline(i, name, self.trace(i, name)))

    def chart_recorded(self, i: int, name: str) -> np.ndarray | None:
        return self._crop(i, self.recorded(i, name))

    def chart_events(self, ev):
        """The spikes, as the quick charts see them: those in the selection, timed from its start."""
        if ev is None or getattr(self, "chart_sel", None) is None or not len(ev.t):
            return ev
        from dataclasses import replace
        from .chartbase import replace_events
        start = np.full(len(ev.t), np.nan)
        end = np.full(len(ev.t), np.nan)
        for b in np.unique(ev.block):
            w = self.chart_window(int(b))
            if w is not None:
                m = ev.block == b
                start[m], end[m] = w
        keep = (ev.t >= start) & (ev.t < end)
        got = replace_events(ev, keep)
        return replace(got, t=got.t - start[keep])

    def trace(self, i: int, name: str) -> np.ndarray | None:
        """A channel of block i as every analysis sees it: with its gain and, on the
        stimulus channel, the stimulus fit."""
        y = self.recorded(i, name)
        fit = self._looks["stim_fit"]
        if y is None or not fit or fit == "Off" or name != self.stim_channel.currentData():
            return y
        hand = self.repeats.setting()
        return self.main.store.memo(
            self._paths[i], ("fit", name, fit, self.gain_of(name), hand),
            lambda: A.fit_stimulus(y, self.blocks[i]["sample_rate"], fit, *hand, sent=self.sent(i)))

    def sent(self, i: int) -> tuple[A.Sent, ...]:
        """The stimuli sent during block i, from the settings saved with it. A stimulus from a
        file that's gone is left out."""
        def rebuild():
            out = []
            for s in self.blocks[i].get("stimuli", ()):
                try:
                    stimulus = Stimulus.from_dict(s["settings"])
                    out.append(A.Sent(s["t"], stimulus.waveform(s["rate"]), s["rate"], *stimulus.repeat()))
                except (ValueError, KeyError, TypeError, OSError):
                    continue
            return tuple(out)
        return self.main.store.memo(self._paths[i], ("sent",), rebuild)

    def send_onsets(self, i: int) -> list[float | None]:
        """Where each stimulus sent during block i really started (s), found on the stimulus
        channel: its note can be up to A.SEARCH_S off. None for one not found there, or for
        all of them without a stimulus channel (their notes are used)."""
        stimuli = self.blocks[i].get("stimuli") or []
        name = self.stim_channel.currentData() or ""
        y = self.recorded(i, name) if name else None
        if y is None:
            return [None] * len(stimuli)
        fs = float(self.blocks[i]["sample_rate"])
        notes = [float(s.get("t", 0.0)) for s in stimuli]
        out = []
        for k, s in enumerate(stimuli):
            try:
                stimulus = Stimulus.from_dict(s["settings"])
                sent = A.Sent(notes[k], stimulus.waveform(s["rate"]), s["rate"])
            except (ValueError, KeyError, TypeError, OSError):
                out.append(None)
                continue
            out.append(A.sent_onset(np.asarray(y, float), fs, sent, notes[k + 1] if k + 1 < len(notes) else None))
        return out

    def _show_gain(self) -> None:
        self.gain.blockSignals(True)
        self.gain.setValue(self.gain_of(self.gain_channel.currentText()))
        self.gain.blockSignals(False)

    def gains_dialog(self) -> None:
        """Every recorded channel's amplifier gain in one box: each channel is divided by it in
        every analysis (1 = no amplifier)."""
        good = [b for b in self.blocks if b is not None]
        names = list(good[0]["channel_names"]) if good else []
        if not names:
            QtWidgets.QMessageBox.information(self, "Amplifier gain", "Open or record some trials first.")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Amplifier gain")
        lay = QtWidgets.QVBoxLayout(dlg)
        note = QtWidgets.QLabel("The gain of the amplifier on each channel. NEWT divides the channel by it in every "
                                "analysis, so the numbers are the voltage before the amplifier (×100 amplifier: "
                                "100). 1 means no amplifier. The recordings themselves never change.")
        note.setWordWrap(True)
        note.setMaximumWidth(420)
        lay.addWidget(note)
        form = QtWidgets.QFormLayout()
        boxes = {}
        for n in names:
            box = QtWidgets.QDoubleSpinBox(decimals=4, minimum=0.0001, maximum=1000000, value=self.gain_of(n))
            box.setPrefix("÷ ")
            box.setKeyboardTracking(False)
            box.setMinimumWidth(140)
            stim = " (stimulus)" if n == self.stim_channel.currentData() else ""
            form.addRow(f"{n}{stim}", box)
            boxes[n] = box
        lay.addLayout(form)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if not dlg.exec():
            return
        gains = {**self._looks["gains"], **{n: b.value() for n, b in boxes.items()}}
        self._looks = {**self._looks, "gains": {k: g for k, g in gains.items() if g != 1}}
        self._show_gain()
        self._data_changed()

    def _gain_edited(self, v: float) -> None:
        gains = {**self._looks["gains"], self.gain_channel.currentText(): v}
        self._looks = {**self._looks, "gains": {k: g for k, g in gains.items() if g != 1}}
        self._data_changed()

    def _fit_picked(self, kind: str) -> None:
        self._looks = {**self._looks, "stim_fit": "" if kind == "Off" else kind}
        self._data_changed()

    def _data_changed(self) -> None:
        """A gain or fit changes what every analysis reads."""
        self._save_looks()
        self.pad_view.data_changed()

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
        """None and each channel; this session's saved choice, else the Record on channel (or Stim)."""
        key = self._settings_key()
        saved = self.main.settings.value(key) if key else None
        keep = saved if saved is not None else self.main.default_stim_channel(names)
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
        self.repeats.stimulus_channel_changed()
        self._compute_groups()
        self.groups_changed()
        self.pad_view.trials_changed()

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
            size = lambda k: abs(k[2])            # a pulse's size, a wave's amplitude
            biggest = max((size(k) for k in keys.values() if k is not None), default=0.0)
            keys = {i: (k if k is not None and size(k) >= 0.1 * biggest else None) for i, k in keys.items()}
            # measured signatures jitter: blocks with the same stimulus share the first one's
            good = [b for b in blocks if b is not None]
            keys = A.cluster_signatures(keys, good[0]["sample_rate"] if good else 1.0)
            # Trials sent from Complex Stimulus group by exactly what was sent (saved with
            # each): a measured shape can't tell steps of 1 and 1.5 V apart reliably.
            for i, b in enumerate(blocks):
                k = AnalysisPage._sent_key(b)
                if k is not None and i in keys:
                    keys[i] = k
            unit = next((b["units"][b["channel_names"].index(how)] for b in good if how in b["channel_names"]), "V")
            labels = A.unique_names({k: A.describe_signature(k, unit) for k in dict.fromkeys(keys.values())
                                     if k is not None and not (isinstance(k, tuple) and k[0] == "sent")})
            complex_names = AnalysisPage._complex_names(blocks, keys, how)
            labels.update(complex_names)
            groups = A.group_blocks(keys, labels)
            # the Complex groups in number order, where the first of them was
            cx = sorted((g for g in groups if g[0] in complex_names.values()), key=lambda g: int(g[0].split()[-1]))
            it = iter(cx)
            return [next(it) if g[0] in complex_names.values() else g for g in groups]
        return A.group_blocks(keys, labels)

    @staticmethod
    def _sent_key(b: dict | None):
        """("sent", what was sent) for a trial sent from Complex Stimulus (a segment
        stimulus, on one output or two), else None."""
        if b is None:
            return None
        for sent in b.get("stimuli") or ():
            st = sent.get("settings") or {}
            if st.get("shape") == "segments":
                second = (sent.get("second") or {}).get("settings")
                return ("sent", json.dumps([st, second], sort_keys=True))
        return None

    @staticmethod
    def _complex_names(blocks: list[dict | None], keys: dict, how: str) -> dict:
        """Groups of trials sent from Complex Stimulus (segment stimuli) are named Complex 1,
        Complex 2..., numbered by how much the stimulus channel varies (its variance over the
        trial, averaged over the group's trials), least first."""
        spread: dict[object, list[float]] = {}
        for i, k in keys.items():
            b = blocks[i]
            if k is None or b is None or how not in b["channel_names"]:
                continue
            sent = b.get("stimuli") or ()
            if not any((s.get("settings") or {}).get("shape") == "segments" for s in sent):
                continue
            y = np.asarray(b["data"][b["channel_names"].index(how)], float)
            if y.size:
                spread.setdefault(k, []).append(float(np.nanvar(y)))
        order = sorted(spread, key=lambda k: float(np.mean(spread[k])))
        return {k: f"Complex {n}" for n, k in enumerate(order, 1)}

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
        how = self.stimulus_source(paths, where)
        return self._group(blocks, how, self._signature_of(list(paths), how))

    def stimulus_source(self, paths: list[Path], where: Path | None) -> str:
        """Where any session's stimuli come from (as how): its saved Stimulus channel, else one
        called Stim, else the notes."""
        good = []
        for p in paths:
            try:
                good.append(self._read(p))
            except Exception:
                pass
        names = good[0]["channel_names"] if good else []
        saved = self.main.settings.value(f"stim_channel/{Path(where).resolve()}") if where else None
        ch = saved if saved is not None else self.main.default_stim_channel(names)
        how = ch if ch in names else ""
        if not how and any(A.stimulus_notes(b["comments"]) for b in good):
            how = "notes"
        return how

    def _compute_groups(self) -> None:
        """Group the blocks by their stimulus (see Stimulus channel) and list the groups."""
        how = self.how()
        d = groupsfile.load(self._folder())
        self._show_looks(d)                           # the stimulus fit is one of them
        self.blocks_panel.set_organize(d["organize"], self._folder() is not None)
        auto = self._group(self.blocks, how, self._fitted_signature_of(self._paths, how))
        # the pages of the Blocks panel: your names, and blocks you moved
        changed = d["moved"] or d["renamed"]
        stems = [p.stem for p in self._paths]
        shown = self.arranged(groupsfile.apply(auto, stems, d), self._paths, how, d) if stems else []
        self.groups = shown if auto or changed else []
        self.shown = shown
        self.picked, self.picked_pages = self._ticked()
        labels = {i: (stem, f"{b['duration_s']:.1f} s" if b is not None else "?", *self._stimuli_note(b),
                      *self._label_note(i))
                  for i, (stem, b) in enumerate(zip(stems, self.blocks))}
        self.blocks_panel.populate(None, shown, [self.group_color(g) for g in range(max(1, len(shown)))], labels,
                                   lambda i: groupsfile.auto_page(auto, i, d), bool(changed), False,
                                   self._looks["hidden_groups"], self.hidden_blocks())
        self.blocks_panel.sort_box.setCurrentIndex(max(0, self.blocks_panel.sort_box.findData(self._looks["sort"])))
        self.blocks_panel.select(*self._highlighted)
        self.update_preview()
        n = len([g for g in self.groups if g[0] != "No stimulus found"])
        none = sum(len(b) for name, b in self.groups if name == "No stimulus found")
        self.group_note.setText(
            "No stimulus channel or notes: pick the channel that recorded the stimulus" if not how else
            f"{n} different stimul{'us' if n == 1 else 'i'}" + (" (from the notes)" if how == "notes" else "")
            + (f", and {none} trial{'' if none == 1 else 's'} with none" if none else "")
            + ": tick a group to see just its trials" if n else
            f"No stimulus pulses found on {how}: is it the right channel?" if how != "notes" else
            "No stimuli in the notes")
        if hasattr(self, "side"):                 # the group lists beside the board
            self.group_colors.fill()


    def _label_note(self, i: int) -> tuple:
        """("Cell 1",) for a trial recorded with a label (Record page's Trial label)."""
        try:
            label = self.main.store.info(self._paths[i]).get("label", "")
        except Exception:
            label = ""
        return (label,) if label else ()

    @staticmethod
    def _stimuli_note(b: dict | None) -> tuple:
        """("20 stimuli",) for a block that holds several."""
        n = len(A.note_times(b["comments"])) if b is not None else 0
        return (f"{n} stimuli",) if n > 1 else ()

    def arranged(self, shown: list, paths: list[Path], how: str, d: dict) -> list:
        """The pages in the order asked for (`how`: as for _group)."""
        sig = self._signature_of(paths, how)

        def measure(blocks: list[int]) -> tuple[float, float]:
            """The frequency and amplitude of a page's stimulus: the first of its blocks'."""
            b = self._read(paths[blocks[0]])
            k = sig(blocks[0], b["data"][b["channel_names"].index(how)], b["sample_rate"]) \
                if how in b["channel_names"] else None
            if k is None:
                return 0.0, 0.0
            if k[0] == "pulse":
                return (1000 / k[4] if k[4] else 0.0), abs(k[2])
            return float(k[1]), float(k[2])
        return groupsfile.arrange(shown, d, measure)

    def hidden_blocks(self) -> set[int]:
        """The blocks left out of everything: those of unticked pages, and those you unticked,
        but not those you ticked yourself."""
        looks = self._looks
        out = {i for name, blocks in self.shown if name in looks["hidden_groups"] for i in blocks}
        for i, p in enumerate(self._paths):
            if p.stem in looks["hidden_trials"]:
                out.add(i)
            elif p.stem in looks["shown_trials"]:
                out.discard(i)
        return out

    def set_hidden(self, item: tuple, hidden: bool) -> None:
        """Leave a page ("page", name) or block ("block", i) out of everything, or put it back.
        A page takes its blocks with it, except any you set yourself; those keep your choice."""
        looks = dict(self._looks)
        if item[0] == "page":
            looks["hidden_groups"] = [n for n in looks["hidden_groups"] if n != item[1]] + ([item[1]] if hidden else [])
        else:
            stem = self._paths[item[1]].stem
            for key in ("hidden_trials", "shown_trials"):
                looks[key] = [n for n in looks[key] if n != stem]
            looks["hidden_trials" if hidden else "shown_trials"].append(stem)
        self._looks = looks
        self._save_looks()

    def show_all(self) -> None:
        """Tick everything, forgetting what you set."""
        self._looks = {**self._looks, "hidden_groups": [], "hidden_trials": [], "shown_trials": []}
        self._save_looks()

    def hide_all(self) -> None:
        """Untick everything, forgetting what you set."""
        self._looks = {**self._looks, "hidden_groups": [n for n, _ in self.shown], "hidden_trials": [],
                       "shown_trials": []}
        self._save_looks()

    def move_group(self, name: str, row: int) -> None:
        """Put a page at this row, as your order."""
        names = [n for n, _ in self.shown]
        at = names.index(name)
        names.pop(at)
        names.insert(row - 1 if at < row else row, name)
        self._looks = {**self._looks, "order": names, "sort": ""}
        self._save_looks()

    def sort_groups(self, how: str) -> None:
        self._looks = {**self._looks, "sort": how}
        self._save_looks()

    def _folder(self):
        return self.main.review_dir if self.main.mode == "review" else self.main.session_dir

    def _show_looks(self, d: dict) -> None:
        """Use the colors saved with this session, if it has any yet."""
        if self._folder() is not None:
            self._looks = d
        for box, key in ((self.page_map, "page_map"), (self.trial_map, "trial_map"), (self.fit_box, "stim_fit")):
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findText(self._looks.get(key, ""))))
            box.blockSignals(False)
        self._show_base_btn()
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(int(self._looks.get("trial_opacity", 0) or 0))
        self.opacity_label.setText("Auto" if not self.opacity_slider.value() else f"{self.opacity_slider.value()}%")
        self.opacity_slider.blockSignals(False)
        self._show_gain()

    def _show_base_btn(self) -> None:
        on = self.page_map.currentText() in (colors.ONE, colors.SHADES)
        self.base_btn.setVisible(on)
        c = self._looks.get("base_color") or colors.BASE_COLOR
        pix = QtGui.QPixmap(14, 14)
        pix.fill(QtGui.QColor(c))
        self.base_btn.setIcon(QtGui.QIcon(pix))

    def _pick_base_color(self) -> None:
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._looks.get("base_color") or colors.BASE_COLOR), self,
                                            "The groups' color")
        if c.isValid():
            self._set_look("base_color", c.name())
            self._show_base_btn()

    def _save_looks(self) -> None:
        folder = self._folder()
        if folder is not None:
            d = groupsfile.load(folder)
            groupsfile.save(folder, {**d, **{k: self._looks.get(k, "") for k in ("colors", "page_map", "trial_map",
                                                                    "base_color", "trial_opacity", "gains",
                                                                    "stim_fit", "hidden_groups", "hidden_trials",
                                                                    "shown_trials", "order", "sort")}})
        self._compute_groups()
        self.main._fill_recorded()                  # the Acquisition Blocks panel shows them too
        self.groups_changed()

    def _set_look(self, key: str, value: str) -> None:
        self._looks = {**self._looks, key: value}
        self._save_looks()

    def set_page_color(self, name: str, color: str | None) -> None:
        """Give a page its own color; None goes back to the default."""
        colors = {k: v for k, v in self._looks["colors"].items() if k != name}
        if color:
            colors[name] = color
        self._looks = {**self._looks, "colors": colors}
        self._save_looks()

    def pick_color(self, name: str) -> None:
        g = next((k for k, (n, _) in enumerate(self.shown) if n == name), 0)
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.group_color(g)), self, f"Color of {name}")
        if c.isValid():
            self.set_page_color(name, c.name())

    def group_color(self, g: int, names: list[str] | None = None, looks: dict | None = None) -> str:
        """Page g's color (of `names`; default the pages here): the one you gave it, else from
        the color map, else from a palette in a different order from the unit colors, so
        they don't look alike."""
        names = [n for n, _ in self.shown] if names is None else names
        looks = self._looks if looks is None else looks
        given = looks["colors"].get(names[g]) if g < len(names) else None
        if given:
            return given
        if looks["page_map"] in (colors.ONE, colors.SHADES):
            base = looks.get("base_color") or colors.BASE_COLOR
            if looks["page_map"] == colors.ONE:
                return base
            left = [n for n in names if n not in looks["hidden_groups"]]
            place = left.index(names[g]) if g < len(names) and names[g] in left else g
            return colors.shade(base, place / max(1, len(left) - 1) if len(left) > 1 else 1.0).name()
        if looks["page_map"] in colors.MAPS:           # across the pages left showing
            left = [n for n in names if n not in looks["hidden_groups"]]
            place = left.index(names[g]) if g < len(names) and names[g] in left else g
            return colors.sample(looks["page_map"], place / max(1, len(left) - 1)).name()
        return colors.group_color(g)

    def plan(self, chosen: list[int], picked: list[int] | None = None, pages_picked: list[str] | None = None
             ) -> tuple[list[tuple[int, str, list[int]]], list[int]]:
        """What to draw of the chosen blocks: ([(page number, name, its blocks)] to average,
        the blocks to show as trials). A page picked in the Blocks panel (or when a chart was
        pinned: `picked`, `pages_picked`) shows its average, a block picked on its own its
        trial, and with nothing picked every page's average."""
        wanted = set(chosen)
        picked = self.picked if picked is None else picked
        pages_picked = self.picked_pages if pages_picked is None else pages_picked
        averaged = [(g, name, [i for i in blocks if i in wanted]) for g, (name, blocks) in enumerate(self.shown)
                    if not picked or name in pages_picked]
        averaged = [a for a in averaged if a[2]]
        done = {i for _, _, blocks in averaged for i in blocks}
        return averaged, [i for i in chosen if i not in done]

    def averages(self, chosen: list[int], Y: np.ndarray, averaged: list):
        """[(page number, label, mean, SD, SEM, n blocks)]: each of these pages (as from plan)
        across the chosen blocks (rows of Y)."""
        rows = {i: r for r, i in enumerate(chosen)}
        out = []
        for g, label, blocks in averaged:
            picked = [rows[i] for i in blocks]
            mean, sd, sem, _ = A.mean_spread(Y[picked])
            out.append((g, label, mean, sd, sem, len(picked)))
        return out

    def trial_opacity(self) -> float | None:
        """How solid trials are drawn, 0–1, as set on the Opacity slider; None: automatic."""
        v = int(self._looks.get("trial_opacity", 0) or 0)
        return v / 100 if v > 0 else None

    def _opacity_moved(self, v: int) -> None:
        self.opacity_label.setText("Auto" if v == 0 else f"{v}%")
        self._opacity_timer.start()

    def trial_colors(self, blocks: list[int]) -> dict[int, QtGui.QColor]:
        """How each of these trials is drawn: in order within its page, by the Trials setting."""
        style_ = self._looks["trial_map"] or colors.SOLID
        out: dict[int, QtGui.QColor] = {}
        for g, (_, members) in enumerate(self.shown):
            rows = [i for i in blocks if i in members]
            out.update(zip(rows, colors.spread(style_, self.group_color(g), len(rows), self.trial_opacity())))
        rest = [i for i in blocks if i not in out]
        out.update(zip(rest, colors.spread(style_, self.group_color(0), len(rest), self.trial_opacity())))
        return out

    def in_browser_order(self, blocks) -> list[int]:
        """These trials as the Trials browser lists them: group by group, in the groups' order
        (yours, or sorted), each group's trials as listed; any not in a group after, in order.
        Every chart that stacks trials (rows, a y axis by trial) uses this order."""
        listed = dict.fromkeys(b for _, bl in (self.shown or []) for b in bl)
        at = {b: k for k, b in enumerate(listed)}
        return sorted(blocks, key=lambda b: (at.get(b, len(at)), b))

    def group_of(self, i: int) -> int | None:
        return next((g for g, (_, blocks) in enumerate(self.groups) if i in blocks), None)

    def group_blocks(self) -> list[int] | None:
        """The blocks ticked in the Trials list, or None for all of them."""
        return self.picked or None

    def _ticked(self) -> tuple[list[int], list[str]]:
        """(the trials ticked, the groups ticked whole), or ([], []) when everything is: a group
        ticked whole shows its average, a trial ticked on its own its trace."""
        hidden = self.hidden_blocks()
        every = sorted(i for _, blocks in self.shown for i in blocks)
        picked = [i for i in every if i not in hidden]
        if picked == every:
            return [], []
        return picked, [n for n, blocks in self.shown if blocks and not set(blocks) & hidden]

    def _blocks_picked(self, picked: list[int]) -> None:
        """Highlighting in the Trials list: just the trial you're looking at (ticks choose what's
        analyzed)."""
        self._highlighted = (picked, self.blocks_panel.selected_pages())
        if picked and self.index() not in picked:
            self.current.setValue(picked[0] + 1)

    def tick_highlighted(self, blocks: list[int], pages: list[str], only: bool) -> None:
        """Tick the highlighted trials and groups; with `only`, untick everything else."""
        looks = dict(self._looks)
        stems = {self._paths[i].stem for i in blocks if i < len(self._paths)}
        whole = set(pages)
        if only:
            looks["hidden_groups"] = [n for n, _ in self.shown if n not in whole]
            looks["hidden_trials"] = []
            looks["shown_trials"] = sorted(stems)
        else:
            looks["hidden_groups"] = [n for n in looks["hidden_groups"] if n not in whole]
            looks["hidden_trials"] = [s for s in looks["hidden_trials"] if s not in stems]
            looks["shown_trials"] = sorted(set(looks["shown_trials"]) | stems)
        self._looks = looks
        self._save_looks()

    def _block_clicked(self, data) -> None:
        if isinstance(data, tuple) and data[0] in ("block", "avg"):
            self._show_entry(data)

    def select_blocks(self, blocks: list[int]) -> None:
        """Analyze just these blocks, ticking only them (none: tick all of them)."""
        if blocks:
            self.tick_highlighted(sorted(blocks), [], True)
        else:
            self.show_all()

    def show_trial(self, i: int) -> None:
        """Look at block i, highlighted in the Trials list, as Up and Down do (a chart's dot clicked)."""
        here = ("avg", self._avg_shown) if self._avg_shown else ("block", self.index())
        self._show_entry(("block", i))
        self._highlighted = ([i], [])
        self.blocks_panel.reveal(("block", i), here if here != ("block", i) else None)

    def select_pages(self, names: list[str]) -> None:
        """Show these pages' averages, ticking only them."""
        self.tick_highlighted([], list(names), True)

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
        sig = self.main.store.memo(self._paths[i], ("as read", how, self.gain_of(how), self._looks["stim_fit"]),
                                   lambda: A.stimulus_signature(y, b["sample_rate"]))
        if sig is not None and sig[0] == "wave":
            # a wave starts where the channel first leaves its middle level
            moved = np.flatnonzero(np.abs(np.nan_to_num(y) - sig[3]) > 0.1 * sig[2])
            return np.array([moved[0] / b["sample_rate"]]) if len(moved) else np.empty(0)
        return A.train_starts(A.stimulus_times(y, b["sample_rate"]))

    def spread(self) -> str:
        """"" (none), "sd" or "sem": the spread shown around averages."""
        return self.spread_box.currentData() or ""

    def spread_changed(self) -> None:
        self.canvas.refresh()

    def groups_changed(self) -> None:
        self.picked, self.picked_pages = self._ticked()
        if self.picked and self.index() not in self.picked:
            self.current.setValue(self.picked[0] + 1)     # the highlighted block is one of them
        self.raw.settings_changed()
        self.pad_view.refresh()

    def events_changed(self) -> None:
        self.canvas.events_changed()
        self.pad_view.data_changed()

    # -- the canvas ---------------------------------------------------------------------------
    def chart(self, kind: str):
        """The first chart of this kind on the canvas."""
        return self.canvas.first(kind)

    def show_layout(self, name: str) -> None:
        self.canvas.show_layout(name)

    def layout_changed(self) -> None:
        """Other charts are showing."""
        self.repeats.listen()

    # -- looks ----------------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        self.blocks_panel.preview.apply_theme(mode)   # the Trials list's preview, light or dark too
        self.raw.apply_theme(mode)
        self.canvas.apply_theme(mode)
        self.pad_view.apply_theme(mode)
        self.pad_view.refresh()
        self.rail.restyle(mode)
        self.side.restyle(mode)


def _flexible_label() -> QtWidgets.QLabel:
    """A message that wraps to whatever width is left, rather than setting the window's
    minimum width (one long message made the window 1,150 px wide)."""
    lbl = QtWidgets.QLabel()
    lbl.setWordWrap(True)
    lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
    return lbl
