"""The Analysis tab: every block of the session, the action potentials in them, and how
the channels respond, on a canvas of charts (see canvas.py).

Beside the charts is the Blocks panel, which picks the blocks to analyze, with a check box to
leave any out of everything. What every chart shares opens in windows from the top bar:
Data (the stimulus and analyzed channels, Spread, gains and the stimulus fit), and Action
potentials (threshold or bimodal, sorted into units, see analysis.py), run again whenever its
settings change until you Clear, with the units found, whose ticks decide what the charts
show. The ⚙ on a chart opens its own settings; the phase charts share the repeats'. DataPad
has a tab of its own, with a bar that picks the highlighted block (slider, ◀ ▶, or the ← →
keys), and borrows the Blocks panel while it's showing.

Averages can be shaded with their SD or SEM across trials (Spread, in the Blocks panel).

The blocks are the ones being reviewed, or those recorded so far in live mode.
"""
from __future__ import annotations

import csv
import hashlib
import json
import warnings
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import analysisfile
from . import colours
from . import dsp
from . import pages
from . import style
from .blockspanel import BlocksPanel
from .canvas import ChartCanvas
from .channels import ColorButton
from .datapadview import DataPadView
from .helpview import add_help
from .stim import Stimulus
from .unitbox import UnitSpinBox
from .units import best_prefix
from .viewkit import View, scroll_column, shrink_combos


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
        self.block_bar = QtWidgets.QWidget()      # with a DataPad: the other charts work on the chosen blocks
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
        self.blocks_panel = BlocksPanel(for_analysis=True)
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
        self._looks = pages.load(None)            # the colours saved with the session
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
        self.canvas = ChartCanvas(self)
        self.datapad = DataPadView(self)
        self.pad_page = DataPadPage(self)
        self.windows = {"channels": SettingsWindow(self, "channels", "Channels", self.blocks_panel.extras),
                        "groups": SettingsWindow(self, "groups", "Groups", self.blocks_panel.group_extras),
                        "spikes": SettingsWindow(self, "spikes", "Action potentials", self.raw),
                        "chart": SettingsWindow(self, "chart", "Chart settings", self.canvas.options)}
        for k, (key, text, tip) in enumerate((
                ("channels", "Channels…", "The stimulus and analyzed channels, amplifier gains and the stimulus fit"),
                ("spikes", "Action potentials…", "Find action potentials, and tick and colour the units"))):
            button = QtWidgets.QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(self.windows[key].open_)
            self.canvas.toolbar.layout().insertWidget(1 + k, button)
        self.blocks_panel.btn_groups.clicked.connect(self.windows["groups"].open_)
        self.btn_save = QtWidgets.QPushButton("Save")
        self.btn_save.setToolTip("Save everything in one .argus file: the recordings, the spikes found and units, "
                                 "DataPad's rows, and how Argus looks now, so it all comes back when it's opened")
        self.btn_save.clicked.connect(self.main.save)
        self.canvas.toolbar.layout().insertWidget(0, self.btn_save)
        top.addWidget(self.canvas.toolbar)
        top.addWidget(self.source, 1)
        self.blocks_panel.add_setting("Analyze", self.raw.channel)
        self.blocks_panel.add_group_setting("Spread", self.spread_box)
        self.blocks_panel.add_group_setting("Groups", self.page_map)
        self.blocks_panel.add_group_setting("Trials", self.trial_map)
        self.blocks_panel.add_setting("Amplifier Gain", self.gain_row)
        self.blocks_panel.add_setting("Stim fit", self.fit_box)
        self.blocks_panel.color_requested.connect(self.pick_color)
        self.blocks_panel.color_cleared.connect(lambda name: self.set_page_color(name, None))
        self.body = QtWidgets.QHBoxLayout()
        self.body.setSpacing(10)
        self.body.addWidget(self.blocks_panel)
        self.body.addWidget(self.canvas, 1)
        lay.addLayout(self.body, 1)

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
        if self.datapad.isVisible():
            self.datapad.redraw()

    # -- the blocks --------------------------------------------------------------------------
    @property
    def events(self) -> A.Events | None:
        return self.raw.events

    def source_changed(self) -> None:
        """The session or its blocks changed: reload when next shown."""
        self._dirty = True
        if self.isVisible() or self.pad_page.isVisible():
            self.refresh()

    def catch_up(self) -> None:
        """Read the blocks again if they've changed."""
        if self._dirty or self._paths != self.main.analysis_blocks():
            self.refresh()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.body.insertWidget(0, self.blocks_panel)   # it goes with whichever tab is showing
        self.catch_up()
        for w in self.windows.values():
            w.come_back()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        for w in self.windows.values():
            w.hide()                              # only with Analysis; they come back with it

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
        self.datapad.set_channels(names)
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
        return {"spikes": self.raw.state(), "datapad": self.datapad.state()}

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
                self.datapad.restore(state["datapad"])
            except (OSError, ValueError, KeyError, TypeError) as e:
                self.main.statusBar().showMessage(f"Couldn't read the saved analysis: {e}", 10000)
        self._saved = self._digest()

    def app_state(self) -> dict:
        """What Analysis shows, for an .argus file."""
        stems = self._stems()
        return {"stim_channel": self.stim_channel.currentData() or "", "current": self.index(),
                "picked": [stems[i] for i in self.picked if i < len(stems)], "picked_pages": list(self.picked_pages),
                "windows": [k for k, w in self.windows.items() if w.isVisible()], "canvas": self.canvas.app_state()}

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
        for key, w in self.windows.items():
            self.main.settings.setValue(f"analysis/window/{key}/open", key in s["windows"])
            if key in s["windows"] and self.isVisible():
                w.show()

    def has_unsaved_analysis(self) -> bool:
        """Whether there's analysis that would be lost."""
        folder = self._folder()
        if folder is None or self._saved is None:
            return False
        anything = self.raw.events is not None or self.datapad.rows or analysisfile.exists(folder)
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
        if self.datapad.rows:
            self.datapad.recompute()

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
        d = pages.load(self._folder())
        changed = d["moved"] or d["renamed"]
        stems = [p.stem for p in self._paths]
        shown = self.arranged(pages.apply(auto, stems, d), self._paths, how, d) if stems else []
        self.groups = shown if auto or changed else []
        self.shown = shown
        self._show_looks(d)
        labels = {i: (stem, f"{b['duration_s']:.1f} s" if b is not None else "?", *self._stimuli_note(b))
                  for i, (stem, b) in enumerate(zip(stems, self.blocks))}
        self.blocks_panel.populate(None, shown, [self.group_color(g) for g in range(max(1, len(shown)))], labels,
                                   lambda i: pages.auto_page(auto, i, d), bool(changed), False,
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
        return pages.arrange(shown, d, measure)

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
            d = pages.load(folder)
            pages.save(folder, {**d, **{k: self._looks[k] for k in ("colors", "page_map", "trial_map", "gains",
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

    # -- looks ----------------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        self.raw.apply_theme(mode)
        self.canvas.apply_theme(mode)
        self.datapad.apply_theme(mode)


def _flexible_label() -> QtWidgets.QLabel:
    """A message that wraps to whatever width is left, rather than setting the window's
    minimum width (one long message made the window 1,150 px wide)."""
    lbl = QtWidgets.QLabel()
    lbl.setWordWrap(True)
    lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
    return lbl


class DataPadPage(QtWidgets.QWidget):
    """The DataPad tab: the block bar above DataPad's settings and tables, beside the Blocks
    panel, which it borrows from Analysis while it's showing."""

    def __init__(self, page: AnalysisPage):
        super().__init__()
        self.page = page
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)
        lay.addWidget(page.block_bar)
        self.body = QtWidgets.QHBoxLayout()
        self.body.setSpacing(10)
        page.datapad.side.setMaximumWidth(360)
        self.body.addWidget(scroll_column(page.datapad.side))
        self.body.addWidget(page.datapad, 1)
        lay.addLayout(self.body, 1)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.body.insertWidget(0, self.page.blocks_panel)
        self.page.catch_up()


class SettingsWindow(QtWidgets.QWidget):
    """Settings in a small window of their own, opened from Analysis. It stays open beside
    the charts while you work, can go anywhere, and comes back where you left it."""

    def __init__(self, page: AnalysisPage, key: str, title: str, content: QtWidgets.QWidget):
        super().__init__(page, QtCore.Qt.WindowType.Tool)
        self.key, self.settings = key, page.main.settings
        self.setWindowTitle(title)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        scroll = QtWidgets.QScrollArea()          # a tall one scrolls on a short screen
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        shrink_combos(content)
        lay.addWidget(scroll)
        self.resize(340, 480)
        saved = self.settings.value(f"analysis/window/{key}/geometry")
        if saved is not None:
            self.restoreGeometry(saved)

    def open_(self) -> None:
        self.settings.setValue(f"analysis/window/{self.key}/open", True)
        self.show()
        self.raise_()

    def come_back(self) -> None:
        """Open again with Analysis, if it was open when Analysis was left."""
        if self.settings.value(f"analysis/window/{self.key}/open", False, type=bool):
            self.show()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.settings.setValue(f"analysis/window/{self.key}/geometry", self.saveGeometry())

    def closeEvent(self, ev):
        self.settings.setValue(f"analysis/window/{self.key}/open", False)
        super().closeEvent(ev)


class SpikeFinder(View):
    """Finding the action potentials every chart shares: its settings, the units found and
    their ticks and colours, and the exports."""

    def __init__(self, page: AnalysisPage):
        super().__init__(page)
        self.events: A.Events | None = None
        self.unit_colors: dict[int, str] = {}     # 0-based unit -> the colour picked for it
        self.unit_opacity: dict[int, float] = {}  # 0-based unit -> how solid it's drawn, if not fully
        self._unticked: set[int] = set()          # units left out of the charts
        left = QtWidgets.QVBoxLayout(self)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(6)
        self.channel = QtWidgets.QComboBox()
        self.channel.setToolTip("The channel to find action potentials on")

        box = QtWidgets.QGroupBox("Find action potentials")
        v = QtWidgets.QVBoxLayout(box)
        self.btn_find = QtWidgets.QPushButton("Find action potentials")
        self.btn_find.setObjectName("start")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_clear.setToolTip("Clear the action potentials found. Changing a setting won't search again "
                                  "until you press Find action potentials")
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.btn_find, 1)
        buttons.addWidget(self.btn_clear)
        v.addLayout(buttons)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Unit", "Count", "Rate", "Amplitude", "Width", "Colour"])
        self.table.horizontalHeaderItem(4).setToolTip("Mean waveform: trough to peak")
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.table.setToolTip("Tick the units to include: every chart shows just those, unless it has its own")
        self.table.setMinimumHeight(90)
        self.table.setMaximumHeight(170)
        v.addWidget(self.table)
        self.btn_more = QtWidgets.QToolButton(text="Search settings", checkable=True, autoRaise=True)
        self.btn_more.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_more.setToolTip("Show or hide how action potentials are found")
        v.addWidget(self.btn_more)
        self.search = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(self.search)
        f.setContentsMargins(0, 0, 0, 0)
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
        f.addRow("Within", self.bip_ms)
        f.addRow("Dead time", self.dead)
        f.addRow("Waveform before", self.pre)
        f.addRow("Waveform after", self.post)
        f.addRow("Units", self.units)
        v.addWidget(self.search)
        self.btn_more.toggled.connect(self._show_search)
        self.btn_more.setChecked(self.main.settings.value("analysis/search_settings", True, type=bool))
        self._show_search(self.btn_more.isChecked())
        left.addWidget(box)
        self.btn_help = add_help(box, "spike_sorting")
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        self.btn_export_avg = QtWidgets.QPushButton("Export averages…")
        self.btn_export_avg.setToolTip("Save the average of the chosen trials (each stimulus group's, when "
                                       "grouped) with its SD, SEM and n at every point, as a CSV file")
        self.btn_export_avg.clicked.connect(self._export_averages_dialog)
        self.btn_export = QtWidgets.QPushButton("Export events…")
        self.btn_export.setToolTip("Save every event (trial, time, unit, amplitude) as a CSV file")
        self.btn_export.setEnabled(False)
        exports = QtWidgets.QHBoxLayout()
        exports.addWidget(self.btn_export)
        exports.addWidget(self.btn_export_avg)
        left.addLayout(exports)

        self.channel.currentIndexChanged.connect(lambda *_: self.settings_changed())
        self.btn_find.clicked.connect(self._find_pressed)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_export.clicked.connect(self._export_dialog)
        self.table.itemChanged.connect(self._unit_ticked)
        # Changing how events are found searches again, once the changes stop.
        self._find_timer = QtCore.QTimer(self, singleShot=True, interval=250)
        self._find_timer.timeout.connect(self._find_again)
        self._opacity_timer = QtCore.QTimer(self, singleShot=True, interval=80)   # a slider moves many steps
        self._opacity_timer.timeout.connect(lambda: self.page.canvas.units_changed())
        self.threshold.editingFinished.connect(self._detection_changed)
        for w in (self.method, self.polarity, self.units):
            w.currentIndexChanged.connect(lambda *_: self._detection_changed())
        for w in (self.dead, self.pre, self.post, self.bip_frac, self.bip_ms):
            w.valueChanged.connect(lambda *_: self._detection_changed())
        self.biphasic.toggled.connect(lambda *_: self._detection_changed())
        self._paused = False                      # Clear stops searching again until the next Find

    def state(self) -> dict:
        """What's set here, to save with the session."""
        return {"channel": self.channel.currentText(), "method": self.method.currentIndex(),
                "threshold": self.threshold.text(), "polarity": self.polarity.currentData(),
                "biphasic": self.biphasic.isChecked(), "bip_frac": self.bip_frac.value(), "bip_ms": self.bip_ms.value(),
                "dead": self.dead.value(), "pre": self.pre.value(), "post": self.post.value(),
                "units": self.units.currentIndex(), "unticked": sorted(self._unticked),
                "colors": {str(u): c for u, c in self.unit_colors.items()},
                "opacity": {str(u): o for u, o in self.unit_opacity.items()}}

    def restore(self, state: dict, events: A.Events | None) -> None:
        """Back as it was saved, without searching again."""
        self._find_timer.stop()
        widgets = (self.channel, self.method, self.threshold, self.polarity, self.biphasic, self.bip_frac,
                   self.bip_ms, self.dead, self.pre, self.post, self.units)
        for w in widgets:
            w.blockSignals(True)
        self.channel.setCurrentText(state["channel"])
        self.method.setCurrentIndex(state["method"])
        self.threshold.setText(state["threshold"])
        self.polarity.setCurrentIndex(max(0, self.polarity.findData(state["polarity"])))
        self.biphasic.setChecked(state["biphasic"])
        self.units.setCurrentIndex(state["units"])
        for w, key in ((self.bip_frac, "bip_frac"), (self.bip_ms, "bip_ms"), (self.dead, "dead"), (self.pre, "pre"),
                       (self.post, "post")):
            w.setValue(state[key])
        for w in widgets:
            w.blockSignals(False)
        self._unticked = set(state["unticked"])
        self.unit_colors = {int(u): c for u, c in state["colors"].items()}
        self.unit_opacity = {int(u): o for u, o in state["opacity"].items()}
        self.events = events
        self._show_results()
        self.page.canvas.refresh()
        self.page.events_changed()

    def _show_search(self, on: bool) -> None:
        self.main.settings.setValue("analysis/search_settings", on)
        self.search.setVisible(on)
        self.btn_more.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)

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
        self.settings_changed()

    def usable(self, idx: list[int]) -> list[int]:
        """Those of these blocks that have this channel at the first block's rate."""
        name = self.channel.currentText()
        good = [b for b in self.blocks if b is not None]
        fs = good[0]["sample_rate"] if good else 0
        hidden = self.page.hidden_blocks()
        return [i for i in idx if i not in hidden and self.page.has(i, name) and self.blocks[i]["sample_rate"] == fs]

    def _chosen(self) -> list[int]:
        """Indices of the chosen blocks that have this channel at the first block's rate."""
        idx = list(range(len(self.blocks)))
        group = self.page.group_blocks()
        if group is not None:
            idx = sorted(set(idx) & set(group))
        return self.usable(idx)

    def chosen_or_none(self) -> list[int]:
        return self._chosen() if self.blocks else []

    def _searched(self) -> list[int]:
        """What to search: the chosen blocks, and those charts are pinned to."""
        return sorted(set(self.chosen_or_none()) | set(self.page.canvas.pinned_blocks()))

    def _trace(self, i: int) -> np.ndarray:
        return self.page.trace(i, self.channel.currentText())

    def settings_changed(self) -> None:
        if self.events is not None:              # the same search, on what's now chosen
            self._find_timer.start()
        self.events = None
        self._show_message("")
        self._show_results()
        self.page.canvas.refresh()

    def pins_changed(self) -> None:
        """A chart was pinned, or stopped being: its blocks need spikes too."""
        if self.events is not None and not set(self._searched()) <= set(self.events.block.tolist()):
            self._find_timer.start()

    def current_changed(self) -> None:
        self.page.canvas.threshold_changed()      # the baseline is the highlighted block's

    # -- the units --------------------------------------------------------------------------------
    def shown_units(self) -> set[int]:
        """0-based units ticked in the table: these are in the charts."""
        n = self.events.n_units if self.events is not None else 0
        return set(range(n)) - self._unticked

    def show_units(self, units) -> None:
        """Tick just these units."""
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(QtCore.Qt.CheckState.Checked if r in units
                                                else QtCore.Qt.CheckState.Unchecked)

    def _unit_ticked(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        u = item.row()
        was = u in self._unticked
        now = item.checkState() != QtCore.Qt.CheckState.Checked
        if was != now:
            self._unticked ^= {u}
            self.page.canvas.units_changed()

    def color_of(self, u: int) -> str:
        """Unit u's colour (0-based): the one picked for it, else its place in the palette."""
        return self.unit_colors.get(int(u), colours.unit_color(u))

    def opacity_of(self, u: int) -> float:
        return self.unit_opacity.get(int(u), 1.0)

    def qcolor(self, u: int) -> QtGui.QColor:
        """Unit u's colour with its opacity, to draw with."""
        c = QtGui.QColor(self.color_of(u))
        c.setAlphaF(self.opacity_of(u))
        return c

    def set_unit_color(self, u: int, color: str) -> None:
        self.unit_colors[u] = color
        self._show_results()
        self.page.canvas.units_changed()

    def set_unit_opacity(self, u: int, opacity: float) -> None:
        self.unit_opacity[u] = opacity
        self._opacity_timer.start()

    def brushes(self, units: np.ndarray) -> list:
        made = {int(u): pg.mkBrush(self.qcolor(u)) for u in np.unique(units)}
        return [made[int(u)] for u in units]

    # -- the threshold ---------------------------------------------------------------------------
    def baseline(self) -> float:
        y = self._trace(self.page.index()) if self.blocks else None
        return float(np.median(y)) if y is not None else 0.0

    def unit(self) -> str:
        return self.page.unit_of(self.channel.currentText())

    def fmt(self, v: float) -> str:
        """A level in the channel's unit, with a sensible prefix: 0.0374 V -> 37.4 mV."""
        n, u = best_prefix(v, self.unit())
        return f"{n:.3g} {u}"

    def _threshold(self) -> tuple[str, float]:
        """("sd", n) or ("level", trace units), as typed. Raises ValueError."""
        return A.parse_threshold(self.threshold.text(), self.unit())

    def level(self) -> float | None:
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
        """These settings as the defaults for a cyclic measurement (Add analysis channel): the
        threshold as an absolute level on this channel, the direction and the dead time."""
        pol = self.polarity.currentData()
        out = {"channel": self.channel.currentText(), "dir": "below" if pol == "negative" else "above",
               "dead": self.dead.value(), "level": None}
        level = self.level() if self.blocks else None
        if level is not None:
            out["level"] = self.baseline() + (-level if pol == "negative" else level)
        return out

    def _detection_changed(self) -> None:
        try:
            self._threshold()
        except ValueError as e:
            self._show_message(str(e), error=True)
            return
        self.events = None
        self._show_results()
        self.page.canvas.threshold_changed()
        self.page.events_changed()
        if not self._paused:
            self._find_timer.start()

    def _find_again(self) -> None:
        if self._searched():
            self.find()

    def threshold_dragged(self, at: float) -> None:
        """The red line was dragged to `at`: a level you set."""
        level = abs(at - self.baseline())
        self.method.blockSignals(True)
        self.method.setCurrentIndex(0)
        self.method.blockSignals(False)
        self.threshold.setText(self.fmt(level))
        if self.polarity.currentData() != "either" and \
                (at > self.baseline()) != (self.polarity.currentData() == "positive"):
            self.polarity.blockSignals(True)
            self.polarity.setCurrentIndex(self.polarity.findData("positive" if at > self.baseline() else "negative"))
            self.polarity.blockSignals(False)
        self.events = None
        self._show_results()
        self.page.events_changed()
        if not self._paused:
            self._find_timer.start()

    # -- finding -----------------------------------------------------------------------------
    def clear(self) -> None:
        """Forget the action potentials, and don't search again until Find is pressed."""
        self._find_timer.stop()
        self._paused = True
        self.events = None
        self._unticked = set()
        self._show_results()
        self.page.events_changed()
        self._show_message("Cleared. Press Find action potentials to search again.")

    def _find_pressed(self) -> None:
        self._find_timer.stop()
        if self.find() is not None:
            self.page.canvas.rescale()

    def find(self) -> A.Events | None:
        try:
            chosen = self._searched()
            if not chosen:
                raise ValueError("No trials to search: record or open some, and check Trials and Channel.")
            self.main.statusBar().showMessage(f"Searching {len(chosen)} trial(s)…")
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
        self.page.events_changed()
        return ev

    def _show_results(self) -> None:
        """The units found, in the table."""
        ev = self.events
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.btn_export.setEnabled(ev is not None and len(ev.t) > 0)
        if ev is None:
            self.table.blockSignals(False)
            return
        for s in ev.summary():
            u = s["unit"] - 1
            r = self.table.rowCount()
            self.table.insertRow(r)
            unit_item = QtWidgets.QTableWidgetItem(f"Unit {s['unit']}")
            unit_item.setIcon(style.dot_icon(self.color_of(u)))
            unit_item.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            unit_item.setCheckState(QtCore.Qt.CheckState.Unchecked if u in self._unticked
                                    else QtCore.Qt.CheckState.Checked)
            self.table.setItem(r, 0, unit_item)
            pick = ColorButton(self.color_of(u), lambda c, u=u: self.set_unit_color(u, c),
                               f"Pick Unit {s['unit']}'s colour and opacity, used on every graph",
                               opacity=self.opacity_of(u), on_opacity=lambda v, u=u: self.set_unit_opacity(u, v))
            pick.setFixedWidth(26)
            pick.setText("▾")
            self.table.setCellWidget(r, 5, pick)
            for c, text in enumerate((f"{s['count']}", f"{s['rate']:.2f} /s", self.fmt(s['amplitude']),
                                      f"{s['width_ms']:.2f} ms"), 1):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
        self.table.blockSignals(False)
        n = len(ev.t)
        msg = (f"{n} event(s) in {len(np.unique(ev.block))} trial(s), {ev.n_units} unit(s); threshold "
               f"{self.fmt(ev.level)} from the baseline." if n else
               f"No events past the threshold ({self.fmt(ev.level)}): try a lower one.")
        self._show_message(" ".join([msg] + ev.notes))

    def export(self, path: str | Path) -> Path:
        ev = self.events
        path = Path(path)
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["trial", "time_s", "unit", "amplitude", "channel"])
            for b, t, u, a in zip(ev.block, ev.t, ev.unit, ev.amplitude):
                w.writerow([self.page._paths[b].stem, f"{t:.6f}", int(u) + 1, f"{a:.6g}",
                            self.channel.currentText()])
        return path

    def export_averages(self, path: str | Path) -> Path:
        """time, then mean, SD, SEM and n for each average shown, as CSV."""
        chosen = self._chosen()
        if not chosen:
            raise ValueError("No trials chosen.")
        fs = self.blocks[chosen[0]]["sample_rate"]
        n = max(len(self._trace(i)) for i in chosen)
        Y = np.full((len(chosen), n), np.nan)
        for r, i in enumerate(chosen):
            y = self._trace(i)
            Y[r, :len(y)] = y
        unit = self.page.unit_of(self.channel.currentText())
        averaged = self.page.plan(chosen)[0] or [(None, "All chosen trials", chosen)]
        sets = self.page.averages(chosen, Y, averaged)
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

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        if self.events is not None:
            self._show_results()


class RepeatFinder(View):
    """The repeats of a repeating stimulus, on the Stimulus channel from Data. Every chart on
    its phase shares them: these settings show with whichever of those charts was clicked."""

    def __init__(self, page: AnalysisPage):
        super().__init__(page)
        self.setParent(page)
        self.hide()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QtWidgets.QGroupBox("Repeats, for every phase chart")
        f = QtWidgets.QFormLayout(box)
        self.cycle_skip = QtWidgets.QSpinBox(minimum=0, maximum=10000)
        self.cycle_skip.setToolTip("Leave out this many cycles at the start of each trial, "
                                   "while the response settles")
        self.cycle_len = UnitSpinBox(0, 600000, "ms", step=10)
        self.cycle_len.setToolTip("How long one repeat is. 0: Argus finds it, from what was sent if the trial saved "
                                  "it, else by matching the stimulus's shape against itself, or from its rises "
                                  "through its middle level")
        self._cycle_start: float | None = None      # where you marked a repeat (s)
        self.btn_mark = QtWidgets.QPushButton("Mark one")
        self.btn_mark.setCheckable(True)
        self.btn_mark.setToolTip("Then drag across one repeat on the Traces or Stimulus chart, to use it as the template")
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        f.addRow("Length", self.cycle_len)
        f.addRow(self.btn_mark)
        f.addRow("Skip first", self.cycle_skip)
        f.addRow(self.message)
        outer.addWidget(box)
        self.cycle_skip.valueChanged.connect(lambda *_: self.page.canvas.refresh_cycles())
        self.cycle_len.valueChanged.connect(self._repeat_typed)
        self.btn_mark.toggled.connect(lambda *_: self.listen())

    def stimulus_channel_changed(self) -> None:
        self.page.canvas.stimulus_changed()
        self.page.canvas.refresh_cycles()

    def channel(self) -> str:
        return self.page.stim_channel.currentData() or ""

    def setting(self) -> tuple[float, float | None]:
        """What you said of the repeats: (length s, 0 to find it; start s, or None)."""
        return self.cycle_len.value() / 1000, self._cycle_start

    def _repeat_typed(self) -> None:
        self._cycle_start = None
        self._changed()

    def _changed(self) -> None:
        if self.page._looks["stim_fit"] in ("Repeat average", "Harmonics"):
            self.page.groups_changed()              # those fits follow the repeats
        else:
            self.page.canvas.refresh_cycles()

    def listen(self) -> None:
        for c in self.page.canvas.here():
            c.listen_for_mark(self._marked if self.btn_mark.isChecked() else None)

    def _marked(self, x0: float, x1: float, finished: bool) -> None:
        if finished and x1 > x0:
            self.cycle_len.blockSignals(True)
            self.cycle_len.setValue((x1 - x0) * 1000)
            self.cycle_len.blockSignals(False)
            self._cycle_start = x0
            self.btn_mark.setChecked(False)
            self._changed()

    def found(self, b: int) -> A.Repeats:
        """The repeats in trial b, worked out once for each way of finding them."""
        page, stim = self.page, self.channel()
        y = page.trace(b, stim) if stim else None
        if y is None:
            return A.Repeats(np.empty((0, 2)))
        hand = self.setting()
        return self.main.store.memo(page._paths[b], ("repeats", stim, page.gain_of(stim), page._looks["stim_fit"], hand),
                                    lambda: A.find_repeats(y, page.blocks[b]["sample_rate"], *hand, page.sent(b)))

    def show_found(self, blocks: list[int]) -> None:
        """Say how many repeats these blocks have."""
        n = sum(len(self.found(b).bounds) for b in blocks)
        self.message.setText(f"{n} in {len(blocks)} trial(s)" if n else "None found")
