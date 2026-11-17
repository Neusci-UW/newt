"""The Protocols tab: run a stimulus many times while stepping one or two of its
settings, and see the responses lined up, overlaid, averaged and measured.

Left: the protocol (what to vary, repeats, order, timing, window).
Middle: Plan (the order of trials and the stimulus at each value), Responses
(one panel per value of b, colored by a) and Curve (the measurement against a).
Right: the measurement and a table of every trial; untick a trial to leave it out.

The protocol logic lives in protocols.py; this file is the window and the runner.
"""
from __future__ import annotations

import csv
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import stim as stimmod
from . import style, theme
from . import protocols as T
from . import recorder, session
from .plotzoom import AxisGroup, light_curve, plot_widget

ROW_GAP = 8          # px between the parts of a settings row (box, button, label)
REST_MIN_S = 3.0     # a wait this long or longer between stimuli: stop acquiring during it
FULL_MAX_SAMPLES = 20_000_000   # a whole protocol sent as one output, up to this many samples per output
WAKE_SETTLE_S = 0.1  # acquiring again this long before the earliest sample a trial keeps
B_COLORS = ("#E69F00", "#56B4E9", "#009E73", "#CC79A7", "#D55E00", "#0072B2", "#F0E442", "#999999")


class _Spin(QtWidgets.QDoubleSpinBox):
    """Shows 0.5 rather than 0.500, so values fit in narrow boxes."""

    def textFromValue(self, v: float) -> str:
        return f"{v:.{self.decimals()}f}".rstrip("0").rstrip(".") or "0"


def _spin(lo, hi, decimals=3, suffix="", step=1.0, width=78):
    s = _Spin(minimum=lo, maximum=hi, decimals=decimals, singleStep=step)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    s.setMinimumWidth(width)
    return s


class _Segmented(QtWidgets.QWidget):
    """A row of buttons where exactly one is on."""

    picked = QtCore.Signal(str)

    def __init__(self, items: list[tuple[str, str]], tip: str = ""):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QtWidgets.QPushButton] = {}
        for key, label in items:
            b = QtWidgets.QPushButton(label, checkable=True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, key=key: self.picked.emit(key))
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons[key] = b

    def set(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def value(self) -> str:
        for k, b in self.buttons.items():
            if b.isChecked():
                return k
        return next(iter(self.buttons))


class VarRow(QtWidgets.QWidget):
    """One variable: which setting, and its values as a Range or a List."""

    changed = QtCore.Signal()
    removed = QtCore.Signal()

    def __init__(self, name: str, removable: bool = False):
        super().__init__()
        self.name = name
        self._shape = "pulse"
        self._loading = False
        g = QtWidgets.QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(6)
        self.tag = tag = QtWidgets.QLabel(f"Vary {name}")
        tag.setMinimumWidth(56)
        self.setting = QtWidgets.QComboBox()
        self.setting.setToolTip(f"Which setting of the stimulus {name} steps through")
        self.mode = _Segmented([("range", "Range"), ("list", "List")],
                               "Range: from, to and step. List: any values, in the order typed.")
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.setting, 1)
        top.addWidget(self.mode)
        self.btn_remove = None
        if removable:
            self.btn_remove = QtWidgets.QToolButton()
            self.btn_remove.setObjectName("remove")
            self.btn_remove.setText("✕")
            self.btn_remove.setToolTip(f"Don't vary a second setting")
            self.btn_remove.setAccessibleName(f"Remove {name}")
            self.btn_remove.clicked.connect(self.removed.emit)
            top.addWidget(self.btn_remove)
        g.addWidget(tag, 0, 0)
        g.addLayout(top, 0, 1)

        self.range_box = QtWidgets.QWidget()
        r = QtWidgets.QHBoxLayout(self.range_box)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(4)
        self.start = _spin(-1e6, 1e6, width=80)
        self.stop = _spin(-1e6, 1e6, width=80)
        self.step = _spin(0, 1e6, width=80)
        for lab, w in (("from", self.start), ("to", self.stop), ("step", self.step)):
            r.addWidget(QtWidgets.QLabel(lab))
            r.addWidget(w)
        r.addStretch(1)
        self.list_edit = QtWidgets.QLineEdit()
        self.list_edit.setPlaceholderText("e.g. 0.5, 1, 2, 3")
        self.list_edit.setToolTip("Values separated by commas or spaces, in the order you want them")
        self.count = QtWidgets.QLabel()
        vals = QtWidgets.QVBoxLayout()
        vals.setSpacing(3)
        vals.addWidget(self.range_box)
        vals.addWidget(self.list_edit)
        vals.addWidget(self.count)
        g.addLayout(vals, 1, 1)
        g.setColumnStretch(1, 1)

        self.setting.activated.connect(self._setting_picked)
        self.mode.picked.connect(self._mode_picked)
        for w in (self.start, self.stop, self.step):
            w.valueChanged.connect(self._edited)
        self.list_edit.editingFinished.connect(self._edited)
        self._list_text = ""

    def set_inline(self, on: bool) -> None:
        """Inline in the stimulus form, beside the setting it varies: just its values."""
        for w in (self.tag, self.setting) + ((self.btn_remove,) if self.btn_remove is not None else ()):
            w.setVisible(not on)

    # -- in and out ------------------------------------------------------------
    def set_shape(self, shape, avoid: str = "") -> None:
        """Offer the settings of `shape` (with a second output, of both: see
        trials.settings_of); keep the current one if it still applies."""
        self._shape = shape
        cur = self.setting.currentData()
        self.setting.blockSignals(True)
        self.setting.clear()
        for key, label, unit in T.settings_of(shape):
            self.setting.addItem(f"{label} ({unit})" if unit and unit != "×" else label, key)
        keys = [k for k, _, _ in T.settings_of(shape)]
        if cur not in keys:
            cur = next((k for k in keys if k != avoid), keys[0] if keys else "")
            if cur:
                self._set_range(*T.default_range(cur))
        self.setting.setCurrentIndex(max(0, self.setting.findData(cur)))
        self.setting.blockSignals(False)
        self._update_units()

    def set_var(self, v: T.Variable) -> None:
        self._loading = True
        self.setting.setCurrentIndex(max(0, self.setting.findData(v.key)))
        self.mode.set(v.mode)
        self._set_range(v.start, v.stop, v.step)
        self._list_text = v.text
        self.list_edit.setText(v.text)
        self._loading = False
        self._show_mode()
        self._update_units()

    def var(self) -> T.Variable:
        return T.Variable(key=self.setting.currentData() or "", mode=self.mode.value(),
                          start=self.start.value(), stop=self.stop.value(), step=self.step.value(),
                          text=self.list_edit.text())

    def set_locked(self, locked: bool) -> None:
        for w in (self.setting, self.mode, self.start, self.stop, self.step, self.list_edit):
            w.setEnabled(not locked)
        if self.btn_remove is not None:
            self.btn_remove.setEnabled(not locked)

    def show_count(self, text: str, error: bool, mode: str) -> None:
        t = style.tokens(mode)
        self.count.setStyleSheet(f"color: {t['error'] if error else t['subtle']}")
        self.count.setText(text)

    # -- internals ---------------------------------------------------------------
    def _set_range(self, a, b, c):
        for w, v in ((self.start, a), (self.stop, b), (self.step, c)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)

    def _setting_picked(self, *_):
        key = self.setting.currentData()
        self._set_range(*T.default_range(key))
        self._update_units()
        self.changed.emit()

    def _mode_picked(self, *_):
        self._show_mode()
        self.changed.emit()

    def _show_mode(self):
        is_range = self.mode.value() == "range"
        self.range_box.setVisible(is_range)
        self.list_edit.setVisible(not is_range)

    def _edited(self, *_):
        if not self._loading:
            self.changed.emit()

    def _update_units(self):
        unit = T.setting_info(self._shape, self.setting.currentData() or "")[1]
        tip = f"In {unit}" if unit not in ("", "×") else ""
        for w in (self.start, self.stop, self.step):
            w.setToolTip(tip)      # the unit is in the setting's name, e.g. Amplitude (V)


class ProtocolsPage(QtWidgets.QWidget):
    placed = QtCore.Signal()       # what's varied, or whether it can be changed, may have changed

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.varying = main.settings.value("stim_varying", False, type=bool)   # a setting is stepped
        self._mode = style.LIGHT
        self._loading = False
        self._has_b = False
        self.protocol = self._saved_protocol()
        self.chk: T.Check | None = None
        self.plan: list[T.Trial] = []
        self.trials: list[T.Trial] = []
        self.results: dict[int, T.Result] = {}
        self.running = False
        self.resting, self._block_trials = False, []   # not acquiring during a long wait; trials in this block
        self._full, self._out_on, self._sched, self._tw = True, False, {}, []   # the whole protocol as one output
        self._windows, self._sent = False, {}            # saving each stimulus's window; what each trial sent
        self._blocks_timer = QtCore.QTimer(self, singleShot=True, interval=500, timeout=self._blocks_saved)
        self.paused = False
        self.next_i = 0
        self._next_fire = 0.0
        self.capture: T.Capture | None = None
        self._q = None
        self._run = None                # what the current/last run used: dict
        self.picked: int | None = None
        self.hidden: set[int] = set()
        self.show_mode = "overlay"
        self._items: dict[int, pg.PlotDataItem] = {}
        self._avg_items: dict[tuple[int, int], pg.PlotDataItem] = {}
        self._plots: list[pg.PlotItem] = []
        self._regions: list[pg.LinearRegionItem] = []
        self._plan_row = 0
        self.timer = QtCore.QTimer(self, interval=20, timeout=self._tick)

        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.left_panel, self.middle_panel, self.right_panel = \
            self._build_left(), self._build_middle(), self._build_right()
        for w in (self.left_panel, self.middle_panel, self.right_panel):
            self.split.addWidget(w)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setStretchFactor(2, 0)
        outer.addWidget(self.split)
        self._sized = False

        self._load_protocol_into_widgets(self.protocol)
        self.apply_theme(style.LIGHT)
        self.sync()
        self.show_view("plan")

    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._sized:
            self._sized = True
            QtCore.QTimer.singleShot(0, self._size_panels)    # once the window has its size

    def _size_panels(self):
        """The side panels get what their contents need; the plots get the rest."""
        total = self.split.width()
        right = 270
        left = max(300, min(440, total - right - 300))
        self.split.setSizes([left, max(200, total - left - right), right])

    # ======================================================================
    # building
    # ======================================================================
    def _build_left(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setObjectName("trialsSide")
        v = QtWidgets.QVBoxLayout(panel)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)
        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.name = QtWidgets.QLineEdit()
        self.name.setToolTip("A name for this protocol, saved with it")
        self.name.setAccessibleName("Protocol name")
        self.btn_save = QtWidgets.QPushButton("Save…")
        self.btn_save.setToolTip("Save this protocol to a file")
        self.btn_load = QtWidgets.QPushButton("Load…")
        self.btn_load.setToolTip("Load a saved protocol")
        name_row = QtWidgets.QHBoxLayout()
        name_row.setSpacing(ROW_GAP)
        name_row.addWidget(self.name, 1)
        name_row.addWidget(self.btn_save)
        name_row.addWidget(self.btn_load)
        form.addRow("Protocol", name_row)
        shape_row = QtWidgets.QHBoxLayout()
        shape_row.setSpacing(ROW_GAP)
        self.shape = QtWidgets.QComboBox()
        for key, label in stimmod.SHAPES.items():
            self.shape.addItem(label, key)
        self.shape.setToolTip("The stimulus is the Stimulator page's; changing the shape here changes it there too")
        self.btn_edit_stim = QtWidgets.QPushButton("Edit…")
        self.btn_edit_stim.setToolTip("Open the Stimulator page to change the other settings")
        shape_row.addWidget(self.shape, 1)
        shape_row.addWidget(self.btn_edit_stim)
        self.shape_label = QtWidgets.QLabel("Shape")
        form.addRow(self.shape_label, shape_row)
        self.stim_info = QtWidgets.QLabel()
        self.stim_info.setWordWrap(True)
        form.addRow("", self.stim_info)
        v.addLayout(form)
        v.addWidget(self._line())

        self.row_a = VarRow("a")
        self.row_b = VarRow("b", removable=True)
        self.btn_add_b = QtWidgets.QPushButton("+ Add a second variable (b)")
        v.addWidget(self.row_a)
        v.addWidget(self.row_b)
        v.addWidget(self.btn_add_b, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

        form2 = QtWidgets.QFormLayout()
        form2.setHorizontalSpacing(10)
        form2.setVerticalSpacing(8)
        rep_row = QtWidgets.QHBoxLayout()
        rep_row.setSpacing(ROW_GAP)
        self.repeats = QtWidgets.QSpinBox(minimum=1, maximum=1000)
        self.repeats.setToolTip("How many times each value is sent")
        self.repeats.setKeyboardTracking(False)
        self.rep_note = QtWidgets.QLabel("of each value")
        rep_row.addWidget(self.repeats)
        rep_row.addWidget(self.rep_note, 1)
        form2.addRow("Repeats", rep_row)
        self.order = _Segmented(list(T.ORDERS.items()),
                                "Blocked: all repeats of one value, then the next. Interleaved: every value "
                                "once per repeat. Random: every value once per repeat, shuffled.")
        form2.addRow("Order", self.order)
        self.gap = _spin(0.05, 3600, decimals=2, suffix=" s", step=0.5)
        self.gap.setToolTip("From the start of one stimulus to the start of the next")
        form2.addRow("Between", self.gap)
        win_row = QtWidgets.QHBoxLayout()
        win_row.setSpacing(ROW_GAP)
        self.before = _spin(0, 10000, decimals=1, suffix=" ms", step=1, width=70)
        self.before.setToolTip("How much of the recording to keep before each stimulus")
        self.after = _spin(0.1, 10000, decimals=1, suffix=" ms", step=5, width=70)
        self.after.setToolTip("How much of the recording to keep after each stimulus")
        win_row.addWidget(self.before)
        win_row.addWidget(QtWidgets.QLabel("before,"))
        win_row.addWidget(self.after)
        win_row.addWidget(QtWidgets.QLabel("after"))
        win_row.addStretch(1)
        form2.addRow("Show", win_row)
        align_row = QtWidgets.QHBoxLayout()
        align_row.setSpacing(ROW_GAP)
        self.align = QtWidgets.QLabel()
        self.align.setWordWrap(True)
        self.btn_monitor = QtWidgets.QPushButton("Choose…")
        self.btn_monitor.setToolTip("Pick the input the stimulus is wired to (Output › Record on)")
        align_row.addWidget(self.align, 1)
        align_row.addWidget(self.btn_monitor)
        form2.addRow("Line up on", align_row)
        v.addLayout(form2)

        v.addStretch(1)
        # Pinned below the settings, so Run is always on screen.
        self.foot = foot = QtWidgets.QWidget()
        foot.setObjectName("trialsFoot")
        fv = QtWidgets.QVBoxLayout(foot)
        fv.setContentsMargins(14, 8, 14, 10)
        fv.setSpacing(8)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        fv.addWidget(self.summary)
        run_row = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton("Run protocol")
        self.btn_run.setObjectName("start")
        self.btn_pause = QtWidgets.QPushButton("Pause", checkable=True)
        self.btn_pause.setToolTip("Hold the next stimulus; Resume carries on where it left off")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setToolTip("End the protocol and keep the stimuli so far (Esc)")
        for b in (self.btn_run, self.btn_pause, self.btn_stop):
            run_row.addWidget(b)
        run_row.addStretch(1)
        fv.addLayout(run_row)

        # signals
        self.name.editingFinished.connect(self._settings_changed)
        self.shape.activated.connect(self._shape_picked)
        self.btn_edit_stim.clicked.connect(self.main.open_stim_window)
        self.row_a.changed.connect(self._settings_changed)
        self.row_b.changed.connect(self._settings_changed)
        self.row_b.removed.connect(lambda: self._set_b(False))
        self.btn_add_b.clicked.connect(lambda: self._set_b(True))
        self.repeats.valueChanged.connect(self._settings_changed)
        self.order.picked.connect(self._settings_changed)
        for w in (self.gap, self.before, self.after):
            w.valueChanged.connect(self._settings_changed)
        self.btn_monitor.clicked.connect(self.main.open_stim_window)
        self.btn_run.clicked.connect(self.run_protocol)
        self.btn_pause.toggled.connect(self.set_paused)
        self.btn_stop.clicked.connect(lambda: self.stop_protocol("Protocol stopped"))
        self.btn_save.clicked.connect(self._save)
        self.btn_load.clicked.connect(self._load)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        lv.addWidget(scroll, 1)
        lv.addWidget(self._line())
        lv.addWidget(foot)
        left.setMinimumWidth(260)
        return left

    def _build_middle(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)
        self.view = _Segmented([("plan", "Plan"), ("responses", "Responses"), ("curve", "Curve")])
        self.view.set("plan")
        bar.addWidget(self.view)
        self.show_seg = _Segmented([("overlay", "Overlay"), ("averages", "Averages"), ("both", "Both")],
                                   "Every stimulus, the average of each a, or both")
        self.show_seg.set("overlay")
        bar.addStretch(1)
        v.addLayout(bar)
        row2 = QtWidgets.QVBoxLayout()
        row2.setSpacing(6)
        seg_row = QtWidgets.QHBoxLayout()
        seg_row.addWidget(self.show_seg)
        seg_row.addStretch(1)
        row2.addLayout(seg_row)
        legend_inner = QtWidgets.QWidget()
        self.legend = QtWidgets.QHBoxLayout(legend_inner)
        self.legend.setContentsMargins(0, 0, 0, 0)
        self.legend.setSpacing(4)
        self.legend_box = QtWidgets.QScrollArea()
        self.legend_box.setWidget(legend_inner)
        self.legend_box.setWidgetResizable(True)
        self.legend_box.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.legend_box.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.legend_box.setFixedHeight(46)
        self.legend_box.setToolTip("Click a value of a to show or hide it")
        self.legend_box.setMinimumWidth(60)
        row2.addWidget(self.legend_box)
        self.row2 = QtWidgets.QWidget()
        self.row2.setLayout(row2)
        row2.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.row2)

        self.stack = QtWidgets.QStackedWidget()
        # plan
        plan = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(plan)
        pv.setContentsMargins(0, 0, 0, 0)
        self.plan_title = QtWidgets.QLabel()
        self.grid = QtWidgets.QTableWidget()
        self.grid.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.grid.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.grid.setToolTip("The stimuli each combination runs in. Click a row to preview its stimuli.")
        self.plan_note = QtWidgets.QLabel()
        self.plan_note.setWordWrap(True)
        self.preview = plot_widget()
        self.preview.getPlotItem().setLabel("left", "Output", units="V")
        self.preview.getPlotItem().setLabel("bottom", "time", units="s")
        self.preview.getPlotItem().showGrid(x=True, y=True, alpha=0.25)
        self.preview_title = QtWidgets.QLabel()
        self.preview_title.setWordWrap(True)
        self.plan_title.setWordWrap(True)
        pv.addWidget(self.plan_title)
        pv.addWidget(self.grid)
        pv.addWidget(self.plan_note)
        pv.addWidget(self.preview_title)
        pv.addWidget(self.preview, 1)
        self.stack.addWidget(plan)
        # responses: one plot per value of b. Separate plot widgets, deleted
        # explicitly when rebuilt: a pyqtgraph GraphicsLayoutWidget left to the
        # garbage collector can crash Qt in a later layout pass.
        self.resp = QtWidgets.QWidget()
        self.resp_lay = QtWidgets.QVBoxLayout(self.resp)
        self.resp_lay.setContentsMargins(0, 0, 0, 0)
        self.resp_lay.setSpacing(2)
        self._resp_widgets: list[pg.PlotWidget] = []
        self.stack.addWidget(self.resp)
        # curve
        self.curve_plot = plot_widget()
        self.curve_plot.getPlotItem().vb.limit_pad = (0.05, 0.05)    # room for the points at the edges
        self.curve_plot.getPlotItem().showGrid(x=True, y=True, alpha=0.25)
        self.stack.addWidget(self.curve_plot)
        v.addWidget(self.stack, 1)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        v.addWidget(self.message)

        self.view.picked.connect(self.show_view)
        self.show_seg.picked.connect(self._show_mode_picked)
        self.grid.itemSelectionChanged.connect(self._plan_row_picked)
        return w

    def _build_right(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setObjectName("trialsSide")
        w.setMinimumWidth(270)
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)
        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(8)
        self.m_channel = QtWidgets.QComboBox()
        self.m_channel.setToolTip("The channel to measure the responses on")
        self.m_kind = QtWidgets.QComboBox()
        self.m_kind.setToolTip("What to measure from each response")
        for k, label in T.MEASURES.items():
            self.m_kind.addItem(label, k)
        rng = QtWidgets.QHBoxLayout()
        rng.setSpacing(ROW_GAP)
        self.m_from = _spin(-10000, 10000, decimals=1, suffix=" ms", step=1, width=88)
        self.m_from.setToolTip("Start of the measuring window, from the stimulus")
        self.m_to = _spin(-10000, 10000, decimals=1, suffix=" ms", step=1, width=88)
        self.m_to.setToolTip("End of the measuring window, from the stimulus")
        rng.addWidget(self.m_from)
        rng.addWidget(QtWidgets.QLabel("to"))
        rng.addWidget(self.m_to)
        form.addRow("Channel", self.m_channel)
        form.addRow("Measure", self.m_kind)
        form.addRow("From", rng)
        v.addLayout(form)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Use", "#", "a", "b", "rep", "value"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        for c, mode in enumerate([QtWidgets.QHeaderView.ResizeMode.ResizeToContents] * 5
                                 + [QtWidgets.QHeaderView.ResizeMode.Stretch]):
            hh.setSectionResizeMode(c, mode)
        self.table.setToolTip("Untick a stimulus to leave it out of the averages and the curve. "
                              "Click a row to highlight its trace.")
        v.addWidget(self.table, 1)
        self.btn_export = QtWidgets.QPushButton("Export CSV…")
        self.btn_export.setToolTip("Every stimulus's values and measurement, for a spreadsheet")
        v.addWidget(self.btn_export, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

        self.m_channel.activated.connect(self._measure_changed)
        self.m_kind.activated.connect(self._measure_changed)
        self.m_from.valueChanged.connect(self._measure_changed)
        self.m_to.valueChanged.connect(self._measure_changed)
        self.table.itemChanged.connect(self._use_toggled)
        self.table.itemSelectionChanged.connect(self._row_picked)
        self.btn_export.clicked.connect(self._export)
        return w

    @staticmethod
    def _line() -> QtWidgets.QFrame:
        f = QtWidgets.QFrame()
        f.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        return f

    # ======================================================================
    # protocol <-> widgets
    # ======================================================================
    def _saved_protocol(self) -> T.Protocol:
        text = self.main.settings.value("trials_protocol", "") or ""
        if text:
            try:
                return T.Protocol.loads(text)
            except ValueError:
                pass
        return T.Protocol(name="Protocol", a=T.trials_variable(1))

    def _load_protocol_into_widgets(self, p: T.Protocol) -> None:
        # A protocol from before segments: its a becomes per-trial values on the stimulus.
        m = self.main
        p, s, s2 = T.upgrade(p, m.stimulus, m.stimulus2)
        if s2 is not m.stimulus2:
            m.set_stimulus2(s2)
        if s is not m.stimulus:
            m.set_stimulus(s)
        self._loading = True
        shape = self._shapes()
        self.name.setText(p.name)
        self.row_a.set_shape(shape)
        self.row_a.set_var(p.a)
        self.row_b.set_shape(shape, avoid=p.a.key)
        if p.b is not None:
            self.row_b.set_var(p.b)
        self._show_b(p.b is not None)
        self.repeats.setValue(p.repeats)
        self.order.set(p.order)
        self.gap.setValue(p.gap_s)
        self.before.setValue(p.before_ms)
        self.after.setValue(p.after_ms)
        self.m_kind.setCurrentIndex(max(0, self.m_kind.findData(p.measure)))
        self.m_from.setValue(p.measure_from_ms)
        self.m_to.setValue(p.measure_to_ms)
        self._loading = False
        self._settings_changed()
        self.placed.emit()

    def set_varying(self, on: bool) -> None:
        """Whether a setting is stepped: off, Fire sends the stimulus as it is."""
        self.varying = on
        self.main.settings.setValue("stim_varying", on)
        if not on and self._has_b:
            self._set_b(False)
        self._update_buttons()

    def protocol_from_widgets(self) -> T.Protocol:
        return T.Protocol(name=self.name.text().strip() or "Protocol", a=self.row_a.var(),
                          b=self.row_b.var() if self._has_b else None, repeats=self.repeats.value(),
                          order=self.order.value(), gap_s=self.gap.value(),
                          before_ms=self.before.value(), after_ms=self.after.value(),
                          measure=self.m_kind.currentData(), measure_from_ms=self.m_from.value(),
                          measure_to_ms=self.m_to.value(), measure_channel=self.m_channel.currentText())

    def _show_b(self, on: bool) -> None:
        self._has_b = on
        self.row_b.setVisible(on)
        self.btn_add_b.setVisible(not on)
        self.rep_note.setText("of each trial × b" if on else "of each trial")

    def _set_b(self, on: bool) -> None:
        if on:
            a_key = self.row_a.var().key
            self.row_b.set_shape(self._shapes(), avoid=a_key)
            if self.row_b.var().key == a_key:
                keys = [k for k, _, _ in T.settings_of(self._shapes()) if k != a_key]
                # with two outputs, b starts on the other output's first setting
                other = [k for k in keys if bool(T.split_key(k)[0]) != bool(T.split_key(a_key)[0])]
                pick = (other or keys or [""])[0]
                if pick:
                    self.row_b.set_var(T.Variable(pick, "range", *T.default_range(pick)))
        self._show_b(on)
        self._settings_changed()

    def _shapes(self):
        """The stimulus shape, or with a second output, (first shape, second shape, first
        output, second output): what trials.settings_of and setting_info take."""
        m = self.main
        if m.stimulus2 is None:
            return m.stimulus
        return (m.stimulus, m.stimulus2, m.cfg.stim_output, m.second_output)

    def _shape_picked(self, *_):
        key = self.shape.currentData()
        if key != self.main.stimulus.shape:
            self.main.set_stimulus(replace(self.main.stimulus, shape=key))   # calls stimulus_changed

    def stimulus_changed(self) -> None:
        """The Stimulator tab's stimulus changed (here or there)."""
        if self.running:
            return
        self.shape.blockSignals(True)
        self.shape.setCurrentIndex(max(0, self.shape.findData(self.main.stimulus.shape)))
        self.shape.blockSignals(False)
        shape = self._shapes()
        keys = [k for k, _, _ in T.settings_of(shape)]
        if self._has_b and self.row_b.var().key not in keys:      # the setting it varied is gone
            self._show_b(False)
        if self.varying and self.row_a.var().key not in keys:
            self.varying = False
            self.main.settings.setValue("stim_varying", False)
        self.row_a.set_shape(shape)
        self.row_b.set_shape(shape, avoid=self.row_a.var().key)
        self._settings_changed()
        self.placed.emit()

    # ======================================================================
    # reacting to settings
    # ======================================================================
    def _settings_changed(self, *_):
        if self._loading:
            return
        self.protocol = self.protocol_from_widgets()
        self.main.settings.setValue("trials_protocol", self.protocol.dumps())
        cfg, main = self.main.cfg, self.main
        s = main.stimulus
        # with two outputs, the Shape box is the first output's (the second's: Edit…)
        self.shape_label.setText("Shape" if main.stimulus2 is None else f"{cfg.stim_output} shape")
        if main.stimulus2 is None:
            self.stim_info.setText(s.describe(cfg.stim_units, cfg.stim_scale) + f" · on {cfg.stim_output}")
        else:
            self.stim_info.setText(main.describe_stimuli().replace("; ", "\n"))
        self.chk = T.check(self.protocol, s, cfg.stim_rate, cfg.stim_max_v, main.stimulus2,
                           (cfg.stim_output, main.second_output))
        self._show_counts()
        self._update_summary()
        if not self.running:
            self._update_plan()

    def _show_counts(self):
        shape = self._shapes()
        for row, var in ((self.row_a, self.protocol.a), (self.row_b, self.protocol.b)):
            if var is None:
                continue
            try:
                vals = var.values()
            except ValueError as e:
                row.show_count(str(e), True, self._mode)
                continue
            unit = T.setting_info(shape, var.key)[1]
            text = f"{len(vals)} value{'s' if len(vals) != 1 else ''}"
            if var.mode == "range":
                text += f", last {T.fmt(vals[-1], unit)}"
            else:
                text += ": " + ", ".join(T.fmt(v, unit) for v in vals[:8]) + (" …" if len(vals) > 8 else "")
            row.show_count(text, False, self._mode)

    def _update_summary(self):
        t = style.tokens(self._mode)
        c, cfg = self.chk, self.main.cfg
        problems = list(c.errors) if c else []
        if problems:
            self.summary.setStyleSheet(f"color: {t['error']}")
            self.summary.setText("\n".join(problems))
        else:
            mins, secs = divmod(int(round(c.duration_s)), 60)
            dur = f"{mins} min {secs} s" if mins else f"{secs} s"
            text = (f"{c.n_trials} stimuli · about {dur} · highest {c.peak_v:g} V "
                    f"(limit {cfg.stim_max_v:g} V)")
            if c.warnings:
                text += "\n" + "\n".join(c.warnings)
            self.summary.setStyleSheet(f"color: {t['muted'] if not c.warnings else t['error']}")
            self.summary.setText(text)
        self._update_buttons()

    def _update_buttons(self):
        idle = not self.running
        can = idle and self.varying and bool(self.chk and self.chk.ok)
        self.btn_run.setEnabled(can)
        self.btn_pause.setEnabled(self.running)
        self.btn_stop.setEnabled(self.running)
        for w in (self.name, self.shape, self.btn_edit_stim, self.btn_add_b, self.repeats, self.order,
                  self.gap, self.before, self.after, self.btn_monitor, self.btn_load):
            w.setEnabled(idle)
        self.row_a.set_locked(not idle)
        self.row_b.set_locked(not idle)
        self.btn_export.setEnabled(bool(self.results))
        self.placed.emit()

    def sync(self) -> None:
        """Channels or the stimulus input changed in the main window."""
        cfg = self.main.cfg
        t = style.tokens(self._mode)
        mon = next((c for c in cfg.channels if c.physical == cfg.stim_monitor), None) if cfg.stim_monitor else None
        if mon is not None:
            self.align.setStyleSheet("")
            self.align.setText(f"{mon.name} ({mon.physical}), where the stimulus starts")
        else:
            self.align.setStyleSheet(f"color: {t['error']}")
            self.align.setText("Nothing yet: wire the output to a spare input and pick it")
        if not self.running:
            names = [c.name for c in cfg.channels]
            cur = self.m_channel.currentText() or self.protocol.measure_channel
            self.m_channel.blockSignals(True)
            self.m_channel.clear()
            self.m_channel.addItems(names)
            if cur not in names:
                cur = next((c.name for c in cfg.channels if c.physical != cfg.stim_monitor), names[0] if names else "")
            self.m_channel.setCurrentText(cur)
            self.m_channel.blockSignals(False)
        self._settings_changed()

    # ======================================================================
    # plan view
    # ======================================================================
    def _update_plan(self):
        c, p = self.chk, self.protocol
        shape = self._shapes()
        self.grid.clear()
        if not c or not c.a_vals:
            self.grid.setRowCount(0)
            self.grid.setColumnCount(0)
            self.plan_title.setText("Fix the settings on the left to see the plan.")
            self.preview.getPlotItem().clear()
            return
        self.plan = T.sequence(p, c.a_vals, c.b_vals, np.random.default_rng(0))
        ua = T.setting_info(shape, p.a.key)[1]
        nb = len(c.b_vals) if c.b_vals else 1
        self.plan_title.setText(
            (f"{len(c.a_vals)} trials" if p.a.key == "trial" else f"{len(c.a_vals)} a")
            + (f" × {nb} b" if c.b_vals else "") + f" × {p.repeats} repeats = "
            f"{len(self.plan)} trials" + (" (one random order; each run shuffles anew)" if p.order == "random" else ""))
        self.grid.setColumnCount(len(c.a_vals))
        self.grid.setRowCount(nb)
        self.grid.setHorizontalHeaderLabels([f"Trial {a:g}" if p.a.key == "trial" else f"a = {T.fmt(a, ua)}"
                                             for a in c.a_vals])
        ub = T.setting_info(shape, p.b.key)[1] if p.b else ""
        self.grid.setVerticalHeaderLabels([f"b = {T.fmt(b, ub)}" for b in c.b_vals] if c.b_vals else ["Stimuli"])
        where: dict[tuple[int, int], list[int]] = {}
        for tr in self.plan:
            where.setdefault((tr.ia, tr.ib), []).append(tr.n)
        colors = self._a_colors(len(c.a_vals))
        for ib in range(nb):
            for ia in range(len(c.a_vals)):
                it = QtWidgets.QTableWidgetItem(", ".join(map(str, where.get((ia, ib), []))))
                it.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                col = QtGui.QColor(colors[ia])
                it.setBackground(col)
                it.setForeground(QtGui.QColor("#0b0d10" if col.lightness() > 120 else "#ffffff"))
                self.grid.setItem(ib, ia, it)
        self.grid.resizeColumnsToContents()
        h = self.grid.horizontalHeader().height() + sum(self.grid.rowHeight(r) for r in range(nb)) + 4
        self.grid.setFixedHeight(min(h, 220))
        notes = {"blocked": "Blocked: all repeats of one value, then the next. Easy to follow, but slow "
                            "changes in the preparation look like an effect of a.",
                 "interleaved": "Interleaved: every value once per repeat, in order. Spreads slow changes "
                                "across the values.",
                 "random": "Random: every value once per repeat, shuffled; a new order each run."}
        self.plan_note.setText(notes.get(p.order, ""))
        self._plan_row = min(self._plan_row, nb - 1)
        self._draw_preview()

    def _plan_row_picked(self):
        rows = {i.row() for i in self.grid.selectedIndexes()}
        if rows:
            self._plan_row = min(rows)
            self._draw_preview()

    def _draw_preview(self):
        c, p, cfg = self.chk, self.protocol, self.main.cfg
        pi = self.preview.getPlotItem()
        pi.clear()
        pi.enableAutoRange()                          # new settings can change the scale
        if not c or not c.a_vals:
            return
        b = c.b_vals[self._plan_row] if c.b_vals else None
        shape = self._shapes()
        main = self.main
        out = T.split_key(p.a.key)[0]                   # show the output a steps
        where = f" on {out or cfg.stim_output}" if main.stimulus2 is not None else ""
        self.preview_title.setText(f"Stimulus{where} at each a" + (
            f", for b = {T.fmt(b, T.setting_info(shape, p.b.key)[1])} (click a row above to change)" if b is not None else ""))
        colors = self._a_colors(len(c.a_vals))
        for ia, a in enumerate(c.a_vals):
            try:
                wave = T.output_wave(main.stimulus, p, a, b, cfg.stim_rate, main.stimulus2, second=bool(out))
            except ValueError:
                continue
            t = np.arange(len(wave)) / cfg.stim_rate
            pi.plot(np.repeat(t, 2)[1:], np.repeat(wave, 2)[:-1], pen=pg.mkPen(colors[ia], width=2))

    # ======================================================================
    # running
    # ======================================================================
    def _ensure_stim_recorded(self) -> bool:
        """Stimuli are lined up on the recorded stimulus: record it on a spare input if it isn't yet."""
        cfg = self.main.cfg
        if cfg.stim_monitor and any(ch.physical == cfg.stim_monitor for ch in cfg.channels):
            return True
        spare = next((phys for _, phys, ok, _ in self.main.monitor_choices() if phys and ok), "")
        return bool(spare) and self.main.set_stim_monitor(spare)

    def run_protocol(self) -> bool:
        main, cfg = self.main, self.main.cfg
        self._settings_changed()
        c = self.chk
        problem = None
        if main.stim_out is None:
            problem = "Protocols needs the nidaqmx (or simulated) backend to send stimuli."
        elif main.mode != "live":
            problem = "Go back to live mode first (Trials panel › Live): protocols record new data."
        elif not self._ensure_stim_recorded():
            problem = "No spare input to record the stimulus on: free one up so stimuli can be lined up."
        elif not c or not c.ok:
            problem = "Fix the protocol first: " + (c.errors[0] if c and c.errors else "")
        elif main.stim_out.busy:
            problem = "A stimulus is still being sent; wait for it to finish."
        if problem:
            self.show_message(problem, error=True)
            return False
        # With Record on, each stimulus's window (Before to After) is saved as a block of
        # its own, and nothing in between: no block of the whole run.
        self._windows = main.btn_rec.isChecked()
        if not main.backend.running:                     # start acquiring for it
            main.ensure_acquiring(record=not self._windows)
            if not main.backend.running:
                self.show_message("Couldn't start acquiring.", error=True)
                return False
        main.set_repeat(False)
        p = self.protocol
        self.trials = T.sequence(p, c.a_vals, c.b_vals)
        # Every trial's waveform, made now: the whole protocol goes to the output as one
        # hardware-timed waveform (each trial at its exact time), not trial by trial.
        try:
            self._tw = [T.make_trial(main.stimulus, p, tr.a, tr.b, cfg.stim_rate, main.stimulus2,
                                     (cfg.stim_output, main.second_output)) for tr in self.trials]
        except ValueError as e:
            self.show_message(f"Can't make the stimuli: {e}", error=True)
            return False
        longest = max((t.wave.shape[-1] for t in self._tw), default=0) / cfg.stim_rate
        if longest > p.gap_s:
            self.show_message(f"Every ({p.gap_s:g} s) is shorter than a stimulus ({longest:.3g} s): "
                              "make Every longer.", error=True)
            return False
        total = (len(self._tw) - 1) * p.gap_s * cfg.stim_rate + (self._tw[-1].wave.shape[-1] if self._tw else 0)
        self._full = total <= FULL_MAX_SAMPLES
        if not self._full:
            self.show_message(f"The whole protocol would be {total / 1e6:.0f} million samples per output: "
                              "sending it stimulus by stimulus instead.")
        self._out_on, self._sched = False, {}
        if self._windows and main.recorder.recording:   # a block from before: end it here
            main._stop_block()
        self._sent = {}
        self._new_capture(p, c)
        self._q = main.backend.subscribe()
        self._run = {"protocol": p, "stimulus": main.stimulus, "shape": self._shapes(),
                     "stimulus2": main.stimulus2, "outs": (cfg.stim_output, main.second_output),
                     "a_vals": c.a_vals, "b_vals": c.b_vals, "channels": [ch.name for ch in cfg.channels],
                     "units": [ch.units for ch in cfg.channels], "out_rate": cfg.stim_rate,
                     "block": main.recorder.path.name if main.recorder.recording and main.recorder.path else ""}
        self.results = {}
        self.hidden = set()
        self.picked = None
        self.running, self.paused, self.next_i = True, False, 0
        self.resting, self._block_trials = False, []
        self._next_fire = time.monotonic() + 0.2
        main._lock_channels(True)
        self.btn_pause.blockSignals(True)
        self.btn_pause.setChecked(False)
        self.btn_pause.setText("Pause")
        self.btn_pause.blockSignals(False)
        self._fill_table()
        self._build_response_plots()
        self._rebuild_legend()
        self.show_view("responses")
        self._update_buttons()
        self.timer.start()
        self.show_message(f"Running {p.name}: {len(self.trials)} trials")
        main._update_stim_chip()
        return True

    def _new_capture(self, p: T.Protocol, c) -> None:
        """Somewhere to collect the stream for lining stimuli up: at the start, and
        after a rest (the stream's clock starts again from 0)."""
        main, cfg = self.main, self.main.cfg
        rate = main.backend.actual_rate if main.backend.hardware_timed else cfg.sample_rate
        chans = list(cfg.channels)
        mon_row = next(i for i, ch in enumerate(chans) if ch.physical == cfg.stim_monitor)
        to_units = lambda raw: np.vstack([ch.to_units(raw[i]) for i, ch in enumerate(chans)])
        keep = max(10.0, 3 * (min(p.gap_s, REST_MIN_S) + c.longest_s + (p.before_ms + p.after_ms) / 1000.0) + 2)
        self.capture = T.Capture(rate, mon_row, to_units, keep, read_s=cfg.chunk_size / max(rate, 1e-9))

    def _start_output(self, first: int) -> bool:
        """Send trials first.. to the end as one waveform, each `Every` after the one before,
        timed by the output's own clock. Trial `first` starts now."""
        p, main = self._run["protocol"], self.main
        rate = self._run["out_rate"]
        waves = self._tw[first:]
        step = int(round(p.gap_s * rate))
        n = step * (len(waves) - 1) + waves[-1].wave.shape[-1]
        chans = waves[0].channels
        full = np.zeros((len(chans), n)) if len(chans) > 1 else np.zeros(n)
        for j, t in enumerate(waves):
            full[..., j * step:j * step + t.wave.shape[-1]] = t.wave
        if not main.send_wave(full, f"{p.name}: stimuli {first + 1}–{len(self.trials)}, every {p.gap_s:g} s",
                              None, channels=chans):
            self.stop_protocol(f"Stopped at stimulus {first + 1}")
            return False
        # each trial's time comes from when the output's clock really starts (writing a long
        # protocol to the device takes a moment): set once it reports it
        self._sched, self._sched_from = {}, (first, step / rate, len(waves))
        self._next_fire = float("inf")
        self._out_on = True
        return True

    def _mark_trial(self, i: int) -> None:
        """Trial i is playing (the whole protocol was sent at once): note it to be lined up
        on the recorded stimulus, and log it as sending it would."""
        p, main = self._run["protocol"], self.main
        tr, t = self.trials[i], self._tw[i]
        label = f"Stimulus {tr.n}/{len(self.trials)}: " + T.describe(p, self._run["shape"], tr.a, tr.b)
        # its time is known exactly (the output's clock): if this runs late, the app having
        # been busy, look for it from when it really started, not from now
        late = time.monotonic() - self._sched.get(i, time.monotonic())
        self.capture.sent(tr, t.first, self._run["out_rate"], t.delay_ms, late_s=late)
        if main.recorder.recording:
            main._log_comment(label)
        for fn in getattr(main, "sent_hooks", ()):
            fn(label, t.stimulus)
        self._set_row_status(tr.n, "sent")
        self._block_trials.append(tr.n)
        self._sent[tr.n] = (label, t.stimulus, t.stimulus2, t.delay_ms)
        self.next_i += 1
        self._next_fire = self._sched.get(self.next_i, time.monotonic())
        main._update_stim_chip()

    def _pre_s(self) -> float:
        """How much of a trial is recorded before its stimulus is sent: the part of Before
        that reaches past the start of the waveform (its leading baseline is sent, so it's
        recorded after)."""
        p = self._run["protocol"] if self._run else self.protocol
        s = self._run["stimulus"] if self._run else self.main.stimulus
        rate = self.main.cfg.stim_rate
        onset = s.onset_samples(rate) / rate if rate > 0 else 0.0
        return max(0.0, p.before_ms / 1000.0 - onset)

    def _wake_lead(self) -> float:
        """How long before the next stimulus to start acquiring again after a rest: just
        what the trial keeps from before it's sent, and a moment to settle."""
        return self._pre_s() + WAKE_SETTLE_S

    def _rest(self) -> None:
        """A long wait before the next stimulus: stop acquiring until just before it.
        While recording, the stretch so far is saved as a block of its own."""
        self._save_to_block()
        self._block_trials = []
        self.resting = True
        self.main.rest_acquiring()
        self.main._lock_channels(True)          # ending the block unlocked them

    def _wake(self) -> bool:
        """Acquiring again after a rest. False (protocol stopped) if it couldn't start."""
        self.resting = False
        self._drain()                           # anything left from before the rest
        if not self.main.resume_acquiring(record=not self._windows, keep_trace=True):
            self.stop_protocol("Stopped: couldn't start acquiring again")
            return False
        self.main._lock_channels(True)
        self._new_capture(self._run["protocol"], self.chk)
        return True

    def set_paused(self, on: bool) -> None:
        if not self.running:
            return
        self.paused = on
        self.btn_pause.setText("Resume" if on else "Pause")
        if on and self._full and self._out_on:
            # the rest of the protocol is sent again on Resume; the stimulus playing now finishes
            self._out_on = False
            self._pause_token = token = object()
            i = self.next_i - 1
            end = (self._sched.get(i, 0.0) + self._tw[i].wave.shape[-1] / self._run["out_rate"]) if i >= 0 else 0.0
            wait_ms = max(0, int((end - time.monotonic()) * 1000) + 20)
            QtCore.QTimer.singleShot(wait_ms, lambda: self.paused and self._pause_token is token
                                     and self.main.stim_out.stop())
        if not on:
            self._next_fire = time.monotonic()
        self.show_message("Paused: Resume carries on with stimulus "
                          f"{self.next_i + 1}" if on else "Resumed")
        self.main._update_stim_chip()

    def stop_protocol(self, why: str = "Protocol stopped") -> None:
        if not self.running:
            return
        self.running = False
        if self.resting:                        # Start is still on: back to streaming
            self.resting = False
            if self.main.btn_run.isChecked():
                self.main.resume_acquiring(record=not self._windows, keep_trace=True)
        if self.main.stim_out is not None:
            self.main.stim_out.stop()          # output to 0 V
        self._drain()
        if self.capture is not None:
            for r in self.capture.resolve(self._run["protocol"], force=True):
                self._add_result(r)
        self._end(why)

    def _end(self, why: str):
        self.running = False
        self.timer.stop()
        if self._q is not None:
            self.main.backend.unsubscribe(self._q)
            self._q = None
        self.main._lock_channels(self.main.recorder.recording)
        self._save_to_block()
        found = sum(1 for r in self.results.values() if r.found)
        missed = len(self.results) - found
        msg = f"{why}: {found} of {len(self.trials)} stimuli recorded"
        if missed:
            msg += (f"; {missed} couldn't be lined up (the recorded stimulus didn't match what was "
                    "sent: check the wire from the output to the input)")
        self.show_message(msg, error=bool(missed))
        self.main._stim_message(msg, error=bool(missed))
        self._update_buttons()
        self._redraw_curve()
        self.main._update_stim_chip()

    def chip_text(self) -> str:
        if not self.running:
            return ""
        n = min(self.next_i, len(self.trials))
        cur = self.trials[n - 1] if n else None
        text = f"Stimulus {n} of {len(self.trials)}"
        if cur is not None:
            text += " · " + T.describe(self._run["protocol"], self._run["shape"], cur.a, cur.b)
        wait = self._next_fire - time.monotonic()
        if n < len(self.trials) and not self.paused and 1 <= wait < float("inf"):
            text += f" · {'not acquiring, ' if self.resting else ''}next in {int(wait)} s"
        return ("Paused · " if self.paused else "") + text

    def _drain(self):
        if self._q is None or self.capture is None:
            return
        while True:
            try:
                c = self._q.get_nowait()
            except Exception:
                break
            self.capture.add(c.t, c.data)

    def _tick(self):
        if self.capture is None:
            return
        self._drain()
        p = self._run["protocol"]
        for r in self.capture.resolve(p):
            self._add_result(r)
        if not self.running:
            return
        now = time.monotonic()
        lead = self._wake_lead()
        if self.resting:
            if self.paused or now < self._next_fire - lead:
                return
            if not self._wake():
                return
        if not self.main.backend.running:
            self.stop_protocol("Acquisition stopped")
            return
        if self.next_i < len(self.trials):
            if (self.next_i and not self.paused and not self.capture.pending
                    and (self._full or not self.main.stim_out.busy)        # (whole protocol: it's always busy)
                    and self._next_fire - now >= max(REST_MIN_S, lead + 1.0)):
                self._rest()                  # this one's data is in; nothing to record till the next
                return
            held = float(self.capture.t[-1] - self.capture.t[0]) if self.capture.t.size else 0.0
            if self._full:
                if not self._out_on:              # the start, or Resume: send the rest of it, once
                    if self.paused or now < self._next_fire or held < self._pre_s() + 0.05:
                        return                    # (the baseline before it isn't recorded yet)
                    if self.main.stim_out.busy:   # (Resume: the last stimulus is still finishing)
                        return
                    if not self._start_output(self.next_i):
                        return
                if not self._sched:
                    started = getattr(self.main.stim_out, "started_at", None)
                    if started is None:
                        return                    # still being written to the output
                    first, dt, n = self._sched_from
                    self._sched = {first + j: started + j * dt for j in range(n)}
                    self._next_fire = self._sched[self.next_i]
                if self.paused or now < self._next_fire:
                    return
                self._mark_trial(self.next_i)     # the output plays it now: line it up and log it
                return
            if self.paused or now < self._next_fire or held < self._pre_s() + 0.05:
                return                        # (the baseline before it isn't recorded yet)
            if self.main.stim_out.busy or not self.capture.can_send(p.gap_s):
                return                        # too soon after the last one; try again shortly
            tr = self.trials[self.next_i]
            try:
                t = T.make_trial(self._run["stimulus"], p, tr.a, tr.b, self._run["out_rate"],
                                 self._run["stimulus2"], self._run["outs"])
            except ValueError as e:
                self.stop_protocol(f"Stopped at stimulus {tr.n}: {e}")
                return
            label = (f"Stimulus {tr.n}/{len(self.trials)}: "
                     + T.describe(p, self._run["shape"], tr.a, tr.b))
            if not self.main.send_wave(t.wave, label, t.stimulus, channels=t.channels,
                                       stimulus2=t.stimulus2):
                self.stop_protocol(f"Stopped at stimulus {tr.n}")
                return
            # lined up on the first output's waveform: that's the one recorded (Line up on)
            self.capture.sent(tr, t.first, self._run["out_rate"], t.delay_ms)
            self._set_row_status(tr.n, "sent")
            self._block_trials.append(tr.n)
            self._sent[tr.n] = (label, t.stimulus, t.stimulus2, t.delay_ms)
            self.next_i += 1
            self._next_fire = max(self._next_fire + p.gap_s, now + 0.05)
            self.main._update_stim_chip()
        elif not self.capture.pending:
            self._end("Protocol finished")
            if self.main.btn_run.isChecked() and self.main.settings.value("record_stim", "simple") != "none":
                self.main.btn_run.setChecked(False)   # its last window is in: stop now, no tail

    # ======================================================================
    # results
    # ======================================================================
    def _value(self, r: T.Result) -> float:
        if not r.found or r.data is None:
            return float("nan")
        p = self.protocol
        row = self._channel_row()
        if row is None:
            return float("nan")
        return T.measure(p.measure, r.t_ms, r.data[row], p.measure_from_ms, p.measure_to_ms)

    def _channel_row(self) -> int | None:
        names = self._run["channels"] if self._run else [c.name for c in self.main.cfg.channels]
        name = self.m_channel.currentText()
        return names.index(name) if name in names else None

    def set_saving_windows(self, on: bool) -> None:
        """Record turned on or off during a protocol: save the stimuli from now on, or not."""
        self._windows = on

    def _save_window(self, r: T.Result) -> None:
        """One stimulus's window as a block of its own: Before to After, every channel,
        with the stimulus that was sent, so Analysis groups it with the identical ones."""
        if r.raw is None or self.capture is None or r.trial.n not in self._sent:
            return
        import json
        main, run = self.main, self._run
        p = run["protocol"]
        label, s1, s2, delay_ms = self._sent[r.trial.n]
        rate = self.capture.rate
        at = r.raw_before_s                             # the stimulus's time 0 is here in the block
        sent = {"t": at - delay_ms / 1000.0, "rate": run["out_rate"], "settings": s1.to_dict(),
                "output": run["outs"][0]}
        if s2 is not None:
            sent["second"] = {"output": run["outs"][1], "settings": s2.to_dict()}
        tr = r.trial
        trials = {"protocol": p.to_dict(), "stimulus": run["stimulus"].to_dict(),
                  "trials": [{"n": tr.n, "rep": tr.rep, "a": tr.a, "b": tr.b, "found": bool(r.found),
                              "use": bool(r.use), "t": at if r.found else None}]}
        path = session.next_block_path(main._ensure_session_dir())
        try:
            recorder.write_block(path, main.backend, r.raw, rate,
                                 {"stimuli": json.dumps([sent]), "trials": json.dumps(trials),
                                  **({"label": main.trial_label} if main.trial_label else {})},
                                 [{"t": at, "sample_index": int(round(at * rate)), "text": label}])
        except OSError as e:
            self.show_message(f"Couldn't save stimulus {tr.n}: {e}", error=True)
            return
        main.session_block_added(path)
        if np.isfinite(r.t0):                           # a trial bar on the live history
            main.mark_trial_start(r.t0 - r.raw_before_s, r.raw.shape[1] / rate)
        self._blocks_timer.start()                      # show them, a few at a time

    def _blocks_saved(self) -> None:
        main = self.main
        main.analysis_page.source_changed()             # new blocks to analyze and group
        main._block_changed(started=False)

    def _add_result(self, r: T.Result):
        self.results[r.trial.n] = r
        if self._windows:
            self._save_window(r)
        self._set_row_status(r.trial.n, "done")
        self._draw_result(r)
        self._update_average(r.trial.ia, r.trial.ib)
        self._rebuild_legend()
        if self.stack.currentIndex() == 2:
            self._redraw_curve()
        self._update_buttons()

    def _fill_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.trials))
        shape = self._run["shape"] if self._run else self._shapes()
        p = self._run["protocol"] if self._run else self.protocol
        ua = T.setting_info(shape, p.a.key)[1]
        ub = T.setting_info(shape, p.b.key)[1] if p.b else ""
        for k, tr in enumerate(self.trials):
            use = QtWidgets.QTableWidgetItem()
            use.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled
                         | QtCore.Qt.ItemFlag.ItemIsSelectable)
            use.setCheckState(QtCore.Qt.CheckState.Checked)
            use.setData(QtCore.Qt.ItemDataRole.UserRole, tr.n)
            self.table.setItem(k, 0, use)
            for c, text in enumerate([str(tr.n), T.fmt(tr.a, ua),
                                      T.fmt(tr.b, ub) if tr.b is not None else "", str(tr.rep), "—"], 1):
                self.table.setItem(k, c, QtWidgets.QTableWidgetItem(text))
        self.table.setColumnHidden(3, p.b is None)
        self.table.blockSignals(False)

    def _set_row_status(self, n: int, status: str):
        k = n - 1
        if not (0 <= k < self.table.rowCount()):
            return
        self.table.blockSignals(True)
        item = self.table.item(k, 5)
        r = self.results.get(n)
        if status == "sent":
            item.setText("…")
            self.table.scrollToItem(item)
        elif r is not None:
            if not r.found:
                item.setText("not found")
                item.setToolTip(f"The recorded stimulus didn't match what was sent (match {r.match:.2f}).")
                self.table.item(k, 0).setCheckState(QtCore.Qt.CheckState.Unchecked)
                r.use = False
            else:
                v = self._value(r)
                item.setText("—" if not np.isfinite(v) else f"{v:.4g}")
        self.table.blockSignals(False)

    def _use_toggled(self, item: QtWidgets.QTableWidgetItem):
        if item.column() != 0:
            return
        n = item.data(QtCore.Qt.ItemDataRole.UserRole)
        r = self.results.get(n)
        if r is None:
            return
        r.use = item.checkState() == QtCore.Qt.CheckState.Checked and r.found
        self._style_item(r)
        self._update_average(r.trial.ia, r.trial.ib)
        self._redraw_curve()

    def _row_picked(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        self.picked = (min(rows) + 1) if rows else None
        for r in self.results.values():
            self._style_item(r)

    def _measure_changed(self, *_):
        self._settings_changed()
        for reg in self._regions:
            reg.blockSignals(True)
            reg.setRegion((self.m_from.value(), self.m_to.value()))
            reg.blockSignals(False)
        for n in self.results:
            self._set_row_status(n, "done")
        if self._run and self.m_channel.currentText():
            self._build_response_plots()
        self._redraw_curve()

    def _region_moved(self, reg):
        lo, hi = reg.getRegion()
        for w, v in ((self.m_from, lo), (self.m_to, hi)):
            w.blockSignals(True)
            w.setValue(round(v, 1))
            w.blockSignals(False)
        for other in self._regions:
            if other is not reg:
                other.blockSignals(True)
                other.setRegion((lo, hi))
                other.blockSignals(False)
        self._settings_changed()
        for n in self.results:
            self._set_row_status(n, "done")
        self._redraw_curve()

    # ======================================================================
    # responses view
    # ======================================================================
    def _a_colors(self, n: int) -> list[str]:
        cmap = pg.colormap.get("viridis")
        lo, hi = (0.25, 1.0) if self._mode == style.DARK else (0.0, 0.8)
        pos = [0.5 * (lo + hi)] if n == 1 else list(np.linspace(lo, hi, n))
        return [cmap.map(x, mode="qcolor").name() for x in pos]

    def _clear_response_plots(self):
        for pw in self._resp_widgets:
            self.resp_lay.removeWidget(pw)
            pw.setParent(None)
            pw.deleteLater()
        self._resp_widgets = []

    def _build_response_plots(self):
        self._clear_response_plots()
        self._plots, self._regions = [], []
        self._items, self._avg_items = {}, {}
        if not self._run:
            return
        th = self._theme()
        b_vals = self._run["b_vals"] or [None]
        row = self._channel_row()
        name = self.m_channel.currentText()
        unit = self._run["units"][row] if row is not None else ""
        ub = T.setting_info(self._run["shape"], self._run["protocol"].b.key)[1] if self._run["protocol"].b else ""
        for ib, b in enumerate(b_vals):
            pw = plot_widget()
            pw.setBackground(th.background)
            self.resp_lay.addWidget(pw, 1)
            self._resp_widgets.append(pw)
            pi = pw.getPlotItem()
            pi.showGrid(x=True, y=True, alpha=0.25)
            pi.setDownsampling(auto=True, mode="peak")     # with light_curve: ~1 point per pixel
            pi.setClipToView(True)
            pi.setLabel("left", f"{name} ({unit})")
            if b is not None:
                pi.setTitle(f"b = {T.fmt(b, ub)}", color=th.foreground, size="10pt")
            if ib == len(b_vals) - 1:
                pi.setLabel("bottom", "ms after the stimulus")
            for ax in ("left", "bottom"):
                pi.getAxis(ax).setPen(th.foreground)
                pi.getAxis(ax).setTextPen(th.foreground)
                pi.getAxis(ax).enableAutoSIPrefix(False)
            pi.addItem(pg.InfiniteLine(0, angle=90, pen=pg.mkPen(th.foreground, style=QtCore.Qt.PenStyle.DashLine)))
            fg = QtGui.QColor(th.foreground)
            fg.setAlpha(150)
            reg = pg.LinearRegionItem((self.m_from.value(), self.m_to.value()),
                                      brush=pg.mkBrush(128, 128, 128, 40), pen=pg.mkPen(fg, width=1))
            reg.setToolTip("Measured between these lines; drag them to change it")
            reg.sigRegionChangeFinished.connect(self._region_moved)
            pi.addItem(reg)
            p = self._run["protocol"]
            pi.setXRange(-p.before_ms, p.after_ms, padding=0.02)
            pi.enableAutoRange(axis="x", enable=False)
            if self._plots:
                pi.setXLink(self._plots[0])
            self._plots.append(pi)
            self._regions.append(reg)
        if not hasattr(self, "_resp_axes"):
            self._resp_axes = AxisGroup()
        self._resp_axes.set_plots(self._plots)           # the panels line up
        for r in self.results.values():
            self._draw_result(r)
        for ia in range(len(self._run["a_vals"])):
            for ib in range(len(b_vals)):
                self._update_average(ia, ib)

    def _draw_result(self, r: T.Result):
        if not r.found or r.data is None or not self._plots:
            return
        row = self._channel_row()
        if row is None:
            return
        pi = self._plots[min(r.trial.ib, len(self._plots) - 1)]
        item = self._items.get(r.trial.n)
        if item is None:
            item = light_curve(pi.plot())
            self._items[r.trial.n] = item
        item.setData(r.t_ms, r.data[row])
        self._style_item(r)

    def _style_item(self, r: T.Result):
        item = self._items.get(r.trial.n)
        if item is None or not self._run:
            return
        color = QtGui.QColor(self._a_colors(len(self._run["a_vals"]))[r.trial.ia])
        sel = self.picked == r.trial.n
        alpha = 255 if sel else (60 if self.show_mode == "both" or (self.picked and not sel) else 200)
        if not r.use:
            alpha = 50
        color.setAlpha(alpha)
        pen = pg.mkPen(color, width=2.6 if sel else 1,
                       style=QtCore.Qt.PenStyle.DotLine if not r.use else QtCore.Qt.PenStyle.SolidLine)
        item.setPen(pen)
        item.setVisible(self.show_mode != "averages" and r.trial.ia not in self.hidden)
        if sel:
            item.setZValue(10)
        else:
            item.setZValue(0)

    def _update_average(self, ia: int, ib: int):
        if not self._plots or not self._run:
            return
        row = self._channel_row()
        mine = [r for r in self.results.values()
                if r.trial.ia == ia and r.trial.ib == ib and r.use and r.found and r.data is not None]
        key = (ia, ib)
        item = self._avg_items.get(key)
        if not mine or row is None:
            if item is not None:
                item.setData([], [])
            return
        if item is None:
            item = light_curve(self._plots[min(ib, len(self._plots) - 1)].plot())
            item.setZValue(5)
            self._avg_items[key] = item
        n = min(len(r.data[row]) for r in mine)
        avg = np.mean([r.data[row][:n] for r in mine], axis=0)
        item.setData(mine[0].t_ms[:n], avg)
        item.setPen(pg.mkPen(self._a_colors(len(self._run["a_vals"]))[ia], width=2.4))
        item.setVisible(self.show_mode != "overlay" and ia not in self.hidden)

    def _show_mode_picked(self, mode: str):
        self.show_mode = mode
        for r in self.results.values():
            self._style_item(r)
        for (ia, ib) in list(self._avg_items):
            self._update_average(ia, ib)

    def _rebuild_legend(self):
        while self.legend.count():
            w = self.legend.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if not self._run:
            return
        shape, p = self._run["shape"], self._run["protocol"]
        ua = T.setting_info(shape, p.a.key)[1]
        colors = self._a_colors(len(self._run["a_vals"]))
        for ia, a in enumerate(self._run["a_vals"]):
            n = sum(1 for r in self.results.values() if r.trial.ia == ia and r.found)
            b = QtWidgets.QPushButton(f"{T.fmt(a, ua)} · {n}", checkable=True)
            b.setChecked(ia not in self.hidden)
            b.setIcon(style.dot_icon(colors[ia]))
            b.setToolTip("Show or hide this value of a (number = stimuli so far)")
            b.toggled.connect(lambda on, ia=ia: self._legend_toggled(ia, on))
            self.legend.addWidget(b)
        self.legend.addStretch(1)

    def _legend_toggled(self, ia: int, on: bool):
        (self.hidden.discard if on else self.hidden.add)(ia)
        for r in self.results.values():
            self._style_item(r)
        for (a, b) in list(self._avg_items):
            self._update_average(a, b)

    # ======================================================================
    # curve view
    # ======================================================================
    def _redraw_curve(self):
        pi = self.curve_plot.getPlotItem()
        pi.clear()
        if pi.legend is not None:
            pi.legend.scene().removeItem(pi.legend) if pi.legend.scene() else None
            pi.legend = None
        if not self._run or not self.results:
            return
        th = self._theme()
        p, shape = self.protocol, self._run["shape"]
        la, ua = T.setting_info(shape, p.a.key)
        row = self._channel_row()
        unit = "ms" if p.measure == "latency" else (self._run["units"][row] if row is not None else "")
        pi.setLabel("bottom", f"a: {la}" + (f" ({ua})" if ua and ua != "×" else ""))
        pi.setLabel("left", f"{T.MEASURES[p.measure]} ({unit})")
        for ax in ("left", "bottom"):
            pi.getAxis(ax).setPen(th.foreground)
            pi.getAxis(ax).setTextPen(th.foreground)
            pi.getAxis(ax).enableAutoSIPrefix(False)
        b_vals = self._run["b_vals"] or [None]
        if len(b_vals) > 1:
            pi.addLegend(offset=(10, 10), labelTextColor=th.foreground)
        ub = T.setting_info(shape, self._run["protocol"].b.key)[1] if self._run["protocol"].b else ""
        a_vals = self._run["a_vals"]
        for ib, b in enumerate(b_vals):
            color = B_COLORS[ib % len(B_COLORS)] if len(b_vals) > 1 else ("#4d8dff" if self._mode == style.DARK else "#1a5fd0")
            xs, ys, means, sds = [], [], [], []
            for ia, a in enumerate(a_vals):
                vals = [self._value(r) for r in self.results.values()
                        if r.trial.ia == ia and r.trial.ib == ib and r.use]
                n, m, sd = T.stats(vals)
                xs += [a] * len([v for v in vals if np.isfinite(v)])
                ys += [v for v in vals if np.isfinite(v)]
                means.append(m)
                sds.append(sd)
            ma = np.asarray(a_vals, float)
            mm, ss = np.asarray(means, float), np.asarray(sds, float)
            ok = np.isfinite(mm)
            if xs:
                pi.plot(xs, ys, pen=None, symbol="o", symbolSize=6, symbolPen=None,
                        symbolBrush=pg.mkBrush(QtGui.QColor(color).lighter(100).name() + "88"))
            if ok.any():
                name = f"b = {T.fmt(b, ub)}" if b is not None else None
                pi.plot(ma[ok], mm[ok], pen=pg.mkPen(color, width=2), symbol="s", symbolSize=9,
                        symbolBrush=color, symbolPen=th.background, name=name)
                pi.addItem(pg.ErrorBarItem(x=ma[ok], y=mm[ok], top=ss[ok], bottom=ss[ok], beam=0.0,
                                           pen=pg.mkPen(color, width=1.5)))

    # ======================================================================
    # views, theme, messages
    # ======================================================================
    def show_view(self, key: str):
        self.view.set(key)
        self.stack.setCurrentIndex({"plan": 0, "responses": 1, "curve": 2}[key])
        self.row2.setVisible(key == "responses")
        if key == "curve":
            self._redraw_curve()

    def _theme(self):
        return theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT]

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = self._theme()
        for pw in (self.preview, self.curve_plot):
            pw.setBackground(th.background)
            for ax in ("left", "bottom"):
                pw.getPlotItem().getAxis(ax).setPen(th.foreground)
                pw.getPlotItem().getAxis(ax).setTextPen(th.foreground)
        self._settings_changed()
        if self._run:
            self._build_response_plots()
            self._rebuild_legend()
            self._redraw_curve()

    def shutdown(self) -> None:
        """Called when the window closes: stop any protocol and delete the plots now.

        Left to Python's garbage collector, pyqtgraph plots can be deleted while Qt
        still has a layout pass queued for them, which crashes Qt. The main window
        deletes its own plots the same way.
        """
        if self.running:
            self.stop_protocol("Window closed")
        self.timer.stop()
        if self._q is not None:
            self.main.backend.unsubscribe(self._q)
            self._q = None
        self._clear_response_plots()
        self._plots, self._regions, self._items, self._avg_items = [], [], {}, {}
        for pw in (self.preview, self.curve_plot):
            pw.setParent(None)
            pw.deleteLater()

    def show_message(self, text: str, error: bool = False):
        t = style.tokens(self._mode)
        self.message.setStyleSheet(f"color: {t['error'] if error else t['muted']}")
        self.message.setText(text)

    # ======================================================================
    # saving
    # ======================================================================
    def _save_to_block(self):
        """Keep the protocol and each trial's values and timing with the recording."""
        rec = self.main.recorder
        if not rec.recording or not self._run or not self._block_trials:
            return
        import json
        trials = []
        for tr in self.trials:
            if tr.n not in self._block_trials:   # sent in an earlier block (before a rest), or not yet
                continue
            r = self.results.get(tr.n)
            trials.append({"n": tr.n, "rep": tr.rep, "a": tr.a, "b": tr.b,
                           "found": bool(r and r.found), "use": bool(r and r.use),
                           "t": (r.t0 - rec.block_start) if (r and r.found) else None})
        saved = {"protocol": self._run["protocol"].to_dict(), "stimulus": self._run["stimulus"].to_dict(),
                 "trials": trials}
        if self._run.get("stimulus2") is not None:
            saved["output"] = self._run["outs"][0]
            saved["second"] = {"output": self._run["outs"][1], "stimulus": self._run["stimulus2"].to_dict()}
        rec.set_attr("trials", json.dumps(saved))

    def _export(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export stimuli", "trials.csv", "CSV (*.csv)")
        if path:
            self.export_csv(path)

    def export_csv(self, path: str | Path) -> Path:
        path = Path(path)
        p, shape = self.protocol, self._run["shape"]
        la, ua = T.setting_info(shape, p.a.key)
        row = self._channel_row()
        unit = "ms" if p.measure == "latency" else (self._run["units"][row] if row is not None else "")
        head = ["trial", "repeat", f"a: {la} ({ua})"]
        if p.b is not None:
            lb, ub = T.setting_info(shape, p.b.key)
            head.append(f"b: {lb} ({ub})")
        head += ["used", "found",
                 f"{T.MEASURES[p.measure]} of {self.m_channel.currentText()}, "
                 f"{p.measure_from_ms:g} to {p.measure_to_ms:g} ms ({unit})"]
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(head)
            for tr in self.trials:
                r = self.results.get(tr.n)
                v = self._value(r) if r else float("nan")
                line = [tr.n, tr.rep, f"{tr.a:g}"] + ([f"{tr.b:g}"] if p.b is not None else [])
                line += ["yes" if r and r.use else "no", "yes" if r and r.found else "no",
                         "" if not np.isfinite(v) else f"{v:.6g}"]
                w.writerow(line)
        self.show_message(f"Exported {len(self.trials)} stimuli to {path.name}")
        return path

    def _save(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save protocol", "protocol.json", "Protocol (*.json)")
        if path:
            Path(path).write_text(self.protocol_from_widgets().dumps(), encoding="utf-8")
            self.show_message(f"Saved to {Path(path).name}")

    def _load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load protocol", "", "Protocol (*.json)")
        if not path:
            return
        try:
            p = T.Protocol.loads(Path(path).read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            self.show_message(str(e), error=True)
            return
        self._load_protocol_into_widgets(p)
