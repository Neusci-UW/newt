"""The Analysis tab: every trial of the session, the action potentials in them, and how
the channels respond, on a whiteboard of charts that you put there (see canvas.py, board.py).

Left of the board, a strip folds out what every chart shares (sidepanels.py): Trials, which
picks the trials to analyze, with a check box to leave any out of everything; Groups,
which groups every chart shows and their colours (and Spread, the SD or SEM shaded round
averages); Channels (the stimulus and analyzed channels, gains and the stimulus fit); and
Spikes (threshold or bimodal, sorted into units, see analysis.py, run again whenever its
settings change until you Clear, with the units found, whose ticks decide what the charts
show). Right of the board are the settings of the chart you picked; the phase charts share
the repeats'. The Measure tool measures stretches of the charts into a DataPad table
(measurecharts.py). Above the board, a bar picks the highlighted trial (slider, ◀ ▶).

The trials are the ones being reviewed, or those recorded so far in live mode.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import analysisfile
from . import colours
from . import dsp
from . import groupsfile
from . import style
from .trialsbrowser import TrialsBrowser
from .canvas import ChartCanvas
from .measurecharts import MeasureTool
from .sidepanels import GroupColours, Rail, SettingsPanel, drawer
from .stim import Stimulus
from .repeatfinder import RepeatFinder
from .spikefinder import SpikeFinder
from .viewkit import scroll_column


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
        for w in (self.btn_prev, self.slider, self.btn_next, self.current, self.n_label):
            bar.addWidget(w)
        lay.addLayout(top)

        # The Blocks panel: pick the blocks to analyze, and how.
        self.blocks_panel = TrialsBrowser(for_analysis=True)
        self.blocks_panel.selection.connect(self._blocks_picked)
        self.blocks_panel.activated.connect(self._block_clicked)
        self.blocks_panel.btn_all.clicked.connect(lambda: self.select_blocks([]))
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
        self.picked_pages: list[str] = []         # the pages selected: their averages are shown
        self.shown: list[tuple[str, list[int]]] = []   # the pages of the Blocks panel
        self._looks = groupsfile.load(None)            # the colours saved with the session
        self._pick_timer = QtCore.QTimer(self, singleShot=True, interval=150)   # dragging picks many at once
        self._pick_timer.timeout.connect(self.groups_changed)
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
        self.page_map.addItems(colours.PAGE_STYLES)
        self.page_map.setToolTip("Colours every group at once. Palette: a different colour each; a colour map: "
                                 "from the first group to the last. A colour you give a group (right-click it) "
                                 "stays. Saved with the session")
        self.trial_map = QtWidgets.QComboBox()
        self.trial_map.addItems(colours.TRIAL_STYLES)
        self.trial_map.setToolTip("How a group's trials are drawn. Solid: all in the group's colour; Opacity: "
                                  "faint to solid, first to last; a colour map: across its colours, first to "
                                  "last. Saved with the session")
        self.page_map.currentTextChanged.connect(lambda v: self._set_look("page_map", v))
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
        self.measure = MeasureTool(self)
        self.canvas = ChartCanvas(self)
        self.btn_save = QtWidgets.QPushButton("Save")
        self.btn_save.setToolTip("Save everything in one .newt file: the recordings, the spikes found and units, "
                                 "the measure windows, and how Newt looks now, so it all comes back when it's opened")
        self.btn_save.clicked.connect(self.main.save)
        self.canvas.toolbar.layout().insertWidget(0, self.btn_save)
        top.addWidget(self.canvas.toolbar)
        top.addWidget(self.block_bar)
        top.addWidget(self.source, 1)
        self.canvas.tool_changed.connect(lambda *_: self.measure.decorate_all())
        self.blocks_panel.add_setting("Analyze", self.raw.channel)
        self.blocks_panel.add_group_setting("Spread", self.spread_box)
        self.blocks_panel.add_group_setting("Groups", self.page_map)
        self.blocks_panel.add_group_setting("Trials", self.trial_map)
        self.blocks_panel.add_setting("Amplifier Gain", self.gain_row)
        self.blocks_panel.add_setting("Stim fit", self.fit_box)
        self.blocks_panel.color_requested.connect(self.pick_color)
        self.blocks_panel.color_cleared.connect(lambda name: self.set_page_color(name, None))
        # left: the strip and the panel it folds out; right: the picked chart's settings
        self.group_colours = GroupColours(self)
        self.blocks_panel.group_extras.layout().insertWidget(0, self.group_colours)
        self.blocks_panel.btn_groups.clicked.connect(lambda: self.open_panel("groups"))
        self.rail = Rail()
        self.drawer = QtWidgets.QStackedWidget()
        self.panels = {"trials": self.blocks_panel, "groups": drawer(self.blocks_panel.group_extras),
                       "channels": drawer(self.blocks_panel.extras), "spikes": drawer(self.raw)}
        for w in self.panels.values():
            self.drawer.addWidget(w)
        self.rail.picked.connect(self.open_panel)
        self.side = SettingsPanel(self)
        self.side_scroll = scroll_column(self.side)
        self.side_scroll.setMinimumWidth(260)
        self.side_scroll.setMaximumWidth(340)
        self.body = QtWidgets.QHBoxLayout()
        self.body.setSpacing(0)
        self.body.addWidget(self.rail)
        self.body.addWidget(self.drawer)
        self.body.addWidget(self.canvas, 1)
        self.body.addWidget(self.side_scroll)
        lay.addLayout(self.body, 1)
        self.open_panel(self.main.settings.value("analysis/panel", "trials"))

        self.btn_prev.clicked.connect(lambda: self.step(-1))
        self.btn_next.clicked.connect(lambda: self.step(1))
        self.slider.valueChanged.connect(self.current.setValue)
        self.current.valueChanged.connect(self._current_changed)
        self.canvas.start()

    # -- the highlighted block -----------------------------------------------------------
    def index(self) -> int:
        return self.current.value() - 1

    def step(self, d: int) -> None:
        self.current.setValue(self.current.value() + d)          # the spin box keeps it in range

    def _current_changed(self, v: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.raw.current_changed()
        self.canvas.current_changed()

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
        self.catch_up()

    def open_panel(self, key: str) -> None:
        """Fold out one of the left panels (Trials, Groups, Channels, Spikes), or none ("")."""
        key = key if key in self.panels else ""
        self.rail.set_open(key)
        self.drawer.setVisible(bool(key))
        if key:
            self.drawer.setCurrentWidget(self.panels[key])
        self.main.settings.setValue("analysis/panel", key)

    def panel(self) -> str:
        """The left panel folded out, or ""."""
        if not self.drawer.isVisibleTo(self):
            return ""
        return next(k for k, w in self.panels.items() if w is self.drawer.currentWidget())

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
            self.source.setText("No trials yet. Record some, or open a session or .mat file (File menu).")
        else:
            durs = [b["duration_s"] for b in good]
            src = self.main.review_dir.name if self.main.review_dir else "this session"
            lo, hi = f"{min(durs):.3g}", f"{max(durs):.3g}"
            self.source.setText(f"<b>{src}</b>: {n} trial(s), {lo if lo == hi else lo + '–' + hi} s each, "
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

    # -- saving ---------------------------------------------------------------------------------
    def _stems(self) -> list[str]:
        return [p.stem for p in self._paths]

    def _analysis_state(self) -> dict:
        return {"spikes": self.raw.state(), "measure": self.measure.state()}

    def _digest(self) -> str:
        """What there is to save, as one string, to tell if it's changed."""
        ev = self.raw.events
        spikes = (ev.t.tobytes() + ev.unit.tobytes()) if ev is not None else b""
        return hashlib.sha1(json.dumps(self._analysis_state(), sort_keys=True).encode() + spikes).hexdigest()

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
                self.measure.restore(state.get("measure"))
            except (OSError, ValueError, KeyError, TypeError) as e:
                self.main.statusBar().showMessage(f"Couldn't read the saved analysis: {e}", 10000)
        self._saved = self._digest()

    def app_state(self) -> dict:
        """What Analysis shows, for an .newt file."""
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
        where = {stem: i for i, stem in enumerate(self._stems())}
        if s["picked_pages"]:
            self.select_pages(s["picked_pages"])
        else:
            self.select_blocks([where[x] for x in s["picked"] if x in where])
        self.current.setValue(s["current"] + 1)
        self._analysis_from = None                # picking the blocks cleared the spikes: as saved again
        self._load_analysis()
        self.open_panel(s.get("panel", "trials"))

    def has_unsaved_analysis(self) -> bool:
        """Whether there's analysis that would be lost."""
        folder = self._folder()
        if folder is None or self._saved is None:
            return False
        anything = self.raw.events is not None or self.measure.windows or analysisfile.exists(folder)
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

    def _show_gain(self) -> None:
        self.gain.blockSignals(True)
        self.gain.setValue(self.gain_of(self.gain_channel.currentText()))
        self.gain.blockSignals(False)

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
        self.repeats.stimulus_channel_changed()
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
        ch = saved if saved is not None else next((n for n in names if "stim" in n.lower()), "")
        how = ch if ch in names else ""
        if not how and any(A.stimulus_notes(b["comments"]) for b in good):
            how = "notes"
        return how

    def _compute_groups(self) -> None:
        """Group the blocks by their stimulus (see Stimulus channel) and list the groups."""
        how = self.how()
        auto = self._group(self.blocks, how, self._signature_of(self._paths, how))
        # the pages of the Blocks panel: your names, and blocks you moved
        d = groupsfile.load(self._folder())
        changed = d["moved"] or d["renamed"]
        stems = [p.stem for p in self._paths]
        shown = self.arranged(groupsfile.apply(auto, stems, d), self._paths, how, d) if stems else []
        self.groups = shown if auto or changed else []
        self.shown = shown
        self._show_looks(d)
        labels = {i: (stem, f"{b['duration_s']:.1f} s" if b is not None else "?", *self._stimuli_note(b))
                  for i, (stem, b) in enumerate(zip(stems, self.blocks))}
        self.blocks_panel.populate(None, shown, [self.group_color(g) for g in range(max(1, len(shown)))], labels,
                                   lambda i: groupsfile.auto_page(auto, i, d), bool(changed), False,
                                   self._looks["hidden_groups"], self.hidden_blocks())
        self.blocks_panel.sort_box.setCurrentIndex(max(0, self.blocks_panel.sort_box.findData(self._looks["sort"])))
        self.blocks_panel.select(self.picked, self.picked_pages)
        n = len([g for g in self.groups if g[0] != "No stimulus found"])
        self.group_note.setText(
            "No stimulus channel or notes: pick the channel that recorded the stimulus" if not how else
            f"{n} different stimul{'us' if n == 1 else 'i'}" + (" (from the notes)" if how == "notes" else "")
            + ": select a group to see just its trials" if n else
            f"No stimulus pulses found on {how}: is it the right channel?" if how != "notes" else
            "No stimuli in the notes")
        if hasattr(self, "side"):                 # the group lists beside the board
            self.group_colours.fill()
            self.side.groups.show_for(self.canvas.picked())


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
        """Use the colours saved with this session, if it has any yet."""
        if self._folder() is not None:
            self._looks = d
        for box, key in ((self.page_map, "page_map"), (self.trial_map, "trial_map"), (self.fit_box, "stim_fit")):
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findText(self._looks.get(key, ""))))
            box.blockSignals(False)
        self._show_gain()

    def _save_looks(self) -> None:
        folder = self._folder()
        if folder is not None:
            d = groupsfile.load(folder)
            groupsfile.save(folder, {**d, **{k: self._looks[k] for k in ("colors", "page_map", "trial_map", "gains",
                                                                    "stim_fit", "hidden_groups", "hidden_trials",
                                                                    "shown_trials", "order", "sort")}})
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
        if looks["page_map"] in colours.MAPS:           # across the pages left showing
            left = [n for n in names if n not in looks["hidden_groups"]]
            place = left.index(names[g]) if g < len(names) and names[g] in left else g
            return colours.sample(looks["page_map"], place / max(1, len(left) - 1)).name()
        return colours.group_color(g)

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

    def trial_colors(self, blocks: list[int]) -> dict[int, QtGui.QColor]:
        """How each of these trials is drawn: in order within its page, by the Trials setting."""
        style_ = self._looks["trial_map"] or colours.SOLID
        out: dict[int, QtGui.QColor] = {}
        for g, (_, members) in enumerate(self.shown):
            rows = [i for i in blocks if i in members]
            out.update(zip(rows, colours.spread(style_, self.group_color(g), len(rows))))
        rest = [i for i in blocks if i not in out]
        out.update(zip(rest, colours.spread(style_, self.group_color(0), len(rest))))
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
        if self.picked and self.index() not in self.picked:
            self.current.setValue(self.picked[0] + 1)     # the highlighted block is one of them
        self.raw.settings_changed()

    def events_changed(self) -> None:
        self.canvas.events_changed()

    # -- the canvas ---------------------------------------------------------------------------
    def chart(self, kind: str):
        """The first chart of this kind on the canvas."""
        return self.canvas.first(kind)

    def show_layout(self, name: str) -> None:
        self.canvas.show_layout(name)

    def layout_changed(self) -> None:
        """Other charts are showing."""
        self.repeats.listen()
        self.measure.decorate_all()

    # -- looks ----------------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        self.raw.apply_theme(mode)
        self.canvas.apply_theme(mode)
        self.rail.restyle(mode)
        self.side.restyle(mode)


def _flexible_label() -> QtWidgets.QLabel:
    """A message that wraps to whatever width is left, rather than setting the window's
    minimum width (one long message made the window 1,150 px wide)."""
    lbl = QtWidgets.QLabel()
    lbl.setWordWrap(True)
    lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
    return lbl
