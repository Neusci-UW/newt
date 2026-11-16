"""The Stimulator's segment table: one output's stimulus as a row of segments, left to
right, in the style of Clampex's epochs.

Each segment is a column: its kind (Baseline, Step, Ramp, Pulse, Sine, From file), its
level and duration, and under each a dashed "per trial" box. Blank, the value is the same
every trial; "+0.5" adds 0.5 each trial; "0.5, 1, 2" gives trial 1, 2, 3 their own value.
Anything a kind needs besides (a ramp's start, a train's pulses and interval, a sine's
frequency) sits at the bottom of its column.

Levels show in the stimulator's units (the [stimulator] units and scale); the Stimulus
keeps volts at the output.
"""
from __future__ import annotations

from pathlib import Path

from pyqtgraph.Qt import QtCore, QtWidgets

from . import style
from . import stim as stimmod
from .stim import Stimulus
from .unitbox import UnitSpinBox

# What the level means for each kind, and the duration (shown in the tooltips and under
# the column's kind).
LEVEL_NAME = {"step": "Level", "ramp": "To", "pulse": "Height", "sine": "Amplitude", "file": "Scale"}
DUR_NAME = {"base": "Duration", "step": "Duration", "ramp": "Duration", "pulse": "Width", "sine": "Duration"}
FIELD_WIDTH = 92


class SegmentEditor(QtWidgets.QWidget):
    """One output's segments. `units()` gives (units, volts → units scale, families)."""

    edited = QtCore.Signal()                 # the segments changed (stimulus() has them)
    problem = QtCore.Signal(str)             # a per-trial box that isn't a number or list
    list_typed = QtCore.Signal(int)          # a per-trial list was typed: how many values

    def __init__(self, units):
        super().__init__()
        self._units = units
        self._parts: list[dict] = []
        self._mode = style.LIGHT
        self._locked = False
        self._per_trial: list[QtWidgets.QLineEdit] = []
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        chips = QtWidgets.QHBoxLayout()
        chips.setSpacing(4)
        self.add_buttons = []
        for kind, label in stimmod.SEG_KINDS.items():
            b = QtWidgets.QPushButton(f"+ {label}")
            b.setToolTip(f"Add a {label.lower()} at the end")
            b.setStyleSheet("QPushButton { padding: 2px 8px; }")
            b.clicked.connect(lambda _=False, k=kind: self._add(k))
            chips.addWidget(b)
            self.add_buttons.append(b)
        chips.addStretch(1)
        v.addLayout(chips)
        self.grid_holder = QtWidgets.QWidget()
        v.addWidget(self.grid_holder)
        self.grid = QtWidgets.QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(4)

    # -- in and out ------------------------------------------------------------------
    def set_stimulus(self, s: Stimulus) -> None:
        self._parts = [dict(p) for p in stimmod.to_segments(s).parts]
        self._build()

    def stimulus(self) -> Stimulus:
        return Stimulus(shape="segments", parts=[dict(p) for p in self._parts])

    def refresh_units(self) -> None:
        """The stimulator's units changed: show the levels in the new ones."""
        self._build()

    def set_locked(self, locked: bool) -> None:
        """While a protocol runs, the segments stay as they are."""
        self._locked = locked
        for w in self.grid_holder.findChildren(QtWidgets.QWidget):
            if not isinstance(w, QtWidgets.QLabel):
                w.setEnabled(not locked)
        for b in self.add_buttons:
            b.setEnabled(not locked)

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        for w in self._per_trial:
            self._style_per_trial(w)

    # -- building ----------------------------------------------------------------------
    def _build(self) -> None:
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self._per_trial = []
        units, k, fams = self._units()
        rows = ("", "Level", "per trial", "Duration", "per trial", "")
        for r, text in enumerate(rows):
            lab = QtWidgets.QLabel(text)
            if text == "per trial":
                lab.setStyleSheet(f"color: {style.tokens(self._mode)['subtle']}")
            self.grid.addWidget(lab, r, 0, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        for i, p in enumerate(self._parts):
            c = i + 1
            kind = p.get("kind", "step")
            self.grid.addWidget(self._header(i, kind), 0, c)
            # level
            if kind == "base":
                zero = QtWidgets.QLabel(f"0 {units}")
                zero.setToolTip("A baseline is 0 at the output. Use a Step for any other holding level.")
                self.grid.addWidget(zero, 1, c)
            elif kind == "file":
                box = QtWidgets.QDoubleSpinBox(minimum=-100, maximum=100, decimals=3, singleStep=0.1)
                box.setPrefix("× ")
                box.setValue(float(p.get("level", 1.0)))
                box.setToolTip("The file's voltages are multiplied by this")
                box.setKeyboardTracking(False)
                box.valueChanged.connect(lambda v, i=i: self._set(i, "level", float(v)))
                self.grid.addWidget(box, 1, c)
            else:
                box = UnitSpinBox(-10 * k, 10 * k, units, fams, decimals=self._decimals(k), step=0.1 * k)
                box.setValue(float(p.get("level", 0.0)) * k)
                box.setMinimumWidth(FIELD_WIDTH)
                box.setToolTip(f"{LEVEL_NAME[kind]} of {stimmod.letter(i)}")
                box.valueChanged.connect(lambda v, i=i: self._set(i, "level", float(v) / k))
                self.grid.addWidget(box, 1, c)
            if kind not in ("base",):
                self.grid.addWidget(self._per_trial_box(i, "d_level", k if kind != "file" else 1.0,
                                                        units if kind != "file" else "×"), 2, c)
            # duration
            if kind != "file":
                dur = UnitSpinBox(0, 60000, "ms", decimals=4, step=1.0 if kind == "pulse" else 10.0)
                dur.setValue(float(p.get("dur", 0.0)))
                dur.setMinimumWidth(FIELD_WIDTH)
                dur.setToolTip(f"{DUR_NAME[kind]} of {stimmod.letter(i)}"
                               + (" (each pulse)" if kind == "pulse" else ""))
                dur.valueChanged.connect(lambda v, i=i: self._set(i, "dur", float(v)))
                self.grid.addWidget(dur, 3, c)
                self.grid.addWidget(self._per_trial_box(i, "d_dur", 1.0, "ms"), 4, c)
            more = self._more(i, p, k, units, fams)
            if more is not None:
                self.grid.addWidget(more, 5, c, QtCore.Qt.AlignmentFlag.AlignTop)
        self.grid.setColumnStretch(len(self._parts) + 1, 1)
        if self._locked:
            self.set_locked(True)

    def _header(self, i: int, kind: str) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)
        name = QtWidgets.QLabel(f"<b>{stimmod.letter(i)}</b>")
        combo = QtWidgets.QComboBox()
        for key, label in stimmod.SEG_KINDS.items():
            combo.addItem(label, key)
        combo.setCurrentIndex(max(0, combo.findData(kind)))
        combo.setToolTip("What this segment is")
        combo.activated.connect(lambda _=0, i=i, c=combo: self._kind(i, c.currentData()))
        h.addWidget(name)
        h.addWidget(combo, 1)
        for text, tip, slot in (("‹", "Move it left", lambda _=False, i=i: self._move(i, -1)),
                                ("›", "Move it right", lambda _=False, i=i: self._move(i, 1)),
                                ("✕", "Remove this segment", lambda _=False, i=i: self._remove(i))):
            b = QtWidgets.QToolButton(text=text, autoRaise=True)
            b.setToolTip(tip)
            b.setAccessibleName(f"{tip} ({stimmod.letter(i)})")
            b.setFixedWidth(20)
            b.clicked.connect(slot)
            h.addWidget(b)
        return w

    def _more(self, i: int, p: dict, k: float, units: str, fams) -> QtWidgets.QWidget | None:
        kind = p.get("kind")
        if kind not in ("ramp", "pulse", "sine", "file"):
            return None
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        f.setContentsMargins(0, 2, 0, 0)
        f.setHorizontalSpacing(6)
        f.setVerticalSpacing(4)
        if kind == "ramp":
            b = UnitSpinBox(-10 * k, 10 * k, units, fams, decimals=self._decimals(k), step=0.1 * k)
            b.setValue(float(p.get("start", 0.0)) * k)
            b.setToolTip("The level the ramp starts at")
            b.valueChanged.connect(lambda v: self._set(i, "start", float(v) / k))
            f.addRow("From", b)
        elif kind == "pulse":
            n = QtWidgets.QSpinBox(minimum=1, maximum=10000)
            n.setValue(int(p.get("count", 1)))
            n.setToolTip("How many pulses in the train")
            n.setKeyboardTracking(False)
            n.valueChanged.connect(lambda v: self._set(i, "count", int(v)))
            every = UnitSpinBox(0, 60000, "ms", decimals=4, step=10)
            every.setValue(float(p.get("interval", 100.0)))
            every.setToolTip("From the start of one pulse to the start of the next")
            every.valueChanged.connect(lambda v: self._set(i, "interval", float(v)))
            every.setEnabled(int(p.get("count", 1)) > 1)
            n.valueChanged.connect(lambda v, e=every: e.setEnabled(v > 1))
            bi = QtWidgets.QCheckBox("Biphasic")
            bi.setChecked(bool(p.get("biphasic")))
            bi.setToolTip("Each pulse goes up, then down by the same amount: no net charge")
            bi.toggled.connect(lambda on: self._set(i, "biphasic", bool(on)))
            f.addRow("Pulses", n)
            f.addRow("Every", every)
            f.addRow("", bi)
        elif kind == "sine":
            fr = UnitSpinBox(0.01, 2500, "Hz", decimals=3, step=1)
            fr.setValue(float(p.get("freq", 10.0)))
            fr.setToolTip("Cycles per second")
            fr.valueChanged.connect(lambda v: self._set(i, "freq", float(v)))
            off = UnitSpinBox(-10 * k, 10 * k, units, fams, decimals=self._decimals(k), step=0.1 * k)
            off.setValue(float(p.get("offset", 0.0)) * k)
            off.setToolTip("The level the sine wave swings around")
            off.valueChanged.connect(lambda v: self._set(i, "offset", float(v) / k))
            f.addRow("Freq", fr)
            f.addRow("Offset", off)
        elif kind == "file":
            row = QtWidgets.QHBoxLayout()
            path = QtWidgets.QLineEdit(p.get("file", ""), placeholderText="CSV or text file")
            path.setToolTip("One column of volts, or two: time (s) and volts")
            path.editingFinished.connect(lambda: self._set(i, "file", path.text().strip()))
            browse = QtWidgets.QPushButton("…")
            browse.setToolTip("Pick the file")
            browse.clicked.connect(lambda: self._browse(i))
            row.addWidget(path, 1)
            row.addWidget(browse)
            rate = UnitSpinBox(0, 1e6, "Hz", decimals=2, step=100)
            rate.setSpecialValueText("output's")
            rate.setValue(float(p.get("file_rate", 0.0)))
            rate.setToolTip("Sample rate of a one-column file. Two-column files don't need it.")
            rate.valueChanged.connect(lambda v: self._set(i, "file_rate", float(v)))
            f.addRow("File", row)
            f.addRow("Rate", rate)
        return w

    def _per_trial_box(self, i: int, key: str, k: float, unit: str) -> QtWidgets.QLineEdit:
        box = QtWidgets.QLineEdit()
        box.setObjectName("perTrial")
        box.setPlaceholderText("+0")
        box.setMinimumWidth(FIELD_WIDTH)
        try:
            box.setText(stimmod.format_per_trial(stimmod.parse_per_trial(self._parts[i].get(key, "")), k))
        except ValueError:
            box.setText(self._parts[i].get(key, ""))
        what = "level" if key == "d_level" else "duration"
        box.setToolTip(f"Change the {what} each trial. +0.5 adds 0.5 {unit} per trial; a list like "
                       f"1, 2, 5 gives each trial its own {what}. Blank: the same every trial.")
        box.editingFinished.connect(lambda: self._per_trial_edited(i, key, box, k))
        self._per_trial.append(box)
        self._style_per_trial(box)
        return box

    def _style_per_trial(self, box: QtWidgets.QLineEdit, error: bool = False) -> None:
        t = style.tokens(self._mode)
        if error:
            box.setStyleSheet(f"QLineEdit {{ border: 1px solid {t['error']}; }}")
        elif box.text().strip():
            box.setStyleSheet(f"QLineEdit {{ border: 1px solid {t['focus']}; background: {t['selection']}; }}")
        else:
            box.setStyleSheet(f"QLineEdit {{ border: 1px dashed {t['field_border']}; }}")

    # -- edits ---------------------------------------------------------------------------
    def _per_trial_edited(self, i: int, key: str, box: QtWidgets.QLineEdit, k: float) -> None:
        if i >= len(self._parts):
            return
        try:
            got = stimmod.parse_per_trial(box.text())
        except ValueError as e:
            self._style_per_trial(box, error=True)
            self.problem.emit(f"{stimmod.letter(i)}: {e}")
            return
        text = stimmod.format_per_trial(got, 1.0 / k)
        self._style_per_trial(box)
        if text != self._parts[i].get(key, ""):
            self._parts[i][key] = text
            self.edited.emit()
            if got is not None and got[0] == "list":
                self.list_typed.emit(len(got[1]))

    def _set(self, i: int, key: str, value) -> None:
        if i < len(self._parts) and self._parts[i].get(key) != value:
            self._parts[i][key] = value
            self.edited.emit()

    def _add(self, kind: str) -> None:
        self._parts.append(stimmod.new_part(kind))
        self._build()
        self.edited.emit()

    def _remove(self, i: int) -> None:
        if len(self._parts) <= 1:
            self.problem.emit("A stimulus needs at least one segment.")
            return
        del self._parts[i]
        QtCore.QTimer.singleShot(0, self._rebuilt)      # not while its own button's signal runs

    def _move(self, i: int, step: int) -> None:
        j = i + step
        if 0 <= j < len(self._parts):
            self._parts[i], self._parts[j] = self._parts[j], self._parts[i]
            QtCore.QTimer.singleShot(0, self._rebuilt)

    def _kind(self, i: int, kind: str) -> None:
        old = self._parts[i]
        if old.get("kind") == kind:
            return
        new = stimmod.new_part(kind)
        if kind != "base" and old.get("kind") not in ("base", "file") and kind != "file":
            new["level"] = old.get("level", new["level"])     # keep what still means the same
        if kind != "file" and old.get("kind") != "file":
            new["dur"], new["d_dur"] = old.get("dur", new["dur"]), old.get("d_dur", "")
        self._parts[i] = new
        QtCore.QTimer.singleShot(0, self._rebuilt)

    def _rebuilt(self) -> None:
        self._build()
        self.edited.emit()

    def _browse(self, i: int) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Waveform file", "",
                                                        "Waveform (*.csv *.txt *.tsv);;All files (*)")
        if path:
            self._parts[i]["file"] = path
            self._rebuilt()

    @staticmethod
    def _decimals(k: float) -> int:
        import math
        return max(4, min(8, 4 - math.floor(math.log10(k))))


def file_name(p: dict) -> str:
    return Path(p.get("file", "")).name
