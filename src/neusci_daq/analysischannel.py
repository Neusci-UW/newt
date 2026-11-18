"""Add analysis channel: LabChart's Channel Calculation dialog. Pick a source channel
and a calculation, fill in its settings, and the formula is written for you (dsp.py
works it out). The source can be a recorded channel or an analysis channel above, so
calculations build on each other: filter, then rectify, then smooth, then measure.

Each kind is a row of KINDS (channelcalc.py, shared with the HTML front end): its label,
its settings, and how they become a formula. `spec_of` reads a formula back into a kind and
settings, so Edit reopens the form.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from . import dsp
from .channelcalc import FORMULA_HELP, KINDS, _LEVEL, _NAMES, _OPS, formula_of, spec_of, unit_for
from .plotzoom import TraceCurve, plot_widget
from .unitbox import UnitSpinBox
from .units import unit_factor

class AnalysisChannelDialog(QtWidgets.QDialog):
    """Make or change one analysis channel. `sources`: names usable as the source (the
    recorded channels, then the analysis channels above this one); `units` their units;
    `preview(formula)` returns (t, y) of the result on the data shown, or raises ValueError."""

    def __init__(self, parent, sources: list[str], units: dict[str, str], fs: float,
                 calc: dsp.Calculation | None = None, taken: set[str] | None = None, preview=None,
                 sorter: dict | None = None):
        super().__init__(parent)
        # The spike search's settings, the defaults for cyclic measurements: {"channel", "level"
        # (absolute, in the channel's unit, or None), "dir" ("above"/"below"), "dead" (ms)}
        self.sorter = sorter or {}
        self._level_typed = False
        self.setWindowTitle("Edit analysis channel" if calc else "Add analysis channel")
        self.resize(720, 560)
        self.sources, self.units, self.fs = sources, units, fs
        self.taken = set(taken or ())
        self.preview_fn = preview
        self._auto_name = self._auto_unit = True
        self.deleted = False
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.source = QtWidgets.QComboBox()
        self.source.addItems(sources)
        self.kind = QtWidgets.QComboBox()
        group = None
        for key, (label, grp, _, _) in KINDS.items():
            if grp != group:
                if group is not None:
                    self.kind.insertSeparator(self.kind.count())
                group = grp
            self.kind.addItem(f"{grp} › {label}", key)
        form.addRow("Source channel", self.source)
        form.addRow("Calculation", self.kind)
        lay.addLayout(form)
        self.settings_box = QtWidgets.QGroupBox("Settings")
        self.settings = QtWidgets.QFormLayout(self.settings_box)
        lay.addWidget(self.settings_box)
        form2 = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.unit = QtWidgets.QLineEdit()
        self.formula = QtWidgets.QLabel()
        self.formula.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.formula.setStyleSheet("color: gray")
        form2.addRow("Name", self.name)
        form2.addRow("Unit", self.unit)
        form2.addRow("Formula", self.formula)
        lay.addLayout(form2)
        self.plot = plot_widget()
        self.plot.setMinimumHeight(160)
        self.plot.getPlotItem().setLabel("bottom", "time", units="s")
        self.plot.getPlotItem().showGrid(x=True, y=True, alpha=0.2)
        self.curve = TraceCurve()
        self.curve.mode = "peak"
        self.curve.setPen(pg.mkPen("#E69F00", width=1.2))
        self.plot.addItem(self.curve)
        self.plot.setVisible(preview is not None)
        lay.addWidget(self.plot, 1)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                             | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        if calc is not None:
            self.btn_delete = buttons.addButton("Delete channel", QtWidgets.QDialogButtonBox.ButtonRole.DestructiveRole)
            self.btn_delete.clicked.connect(self._delete)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.btn_ok = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        lay.addWidget(buttons)
        self._widgets: dict[str, QtWidgets.QWidget] = {}

        if calc is not None:
            spec = spec_of(calc.formula)
            kind, src, vals = spec if spec and spec[1] in sources else ("custom", sources[0] if sources else "",
                                                                         {"formula": calc.formula})
            self.source.setCurrentText(src)
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self._build(vals)
            self.name.setText(calc.name)
            self.unit.setText(calc.unit)
            self._auto_name = self._auto_unit = False
        else:
            self.kind.setCurrentIndex(self.kind.findData("bandpass"))
            self._build({})
        self.source.currentIndexChanged.connect(lambda *_: self._changed())
        self.kind.currentIndexChanged.connect(lambda *_: (self._build({}), self._changed()))
        self.name.textEdited.connect(lambda *_: setattr(self, "_auto_name", False) or self._check())
        self.unit.textEdited.connect(lambda *_: setattr(self, "_auto_unit", False) or self._check())
        self._changed()

    # -- the settings form -------------------------------------------------------------------
    def _build(self, vals: dict) -> None:
        while self.settings.rowCount():
            self.settings.removeRow(0)
        self._widgets = {}
        kind = self.kind.currentData()
        self._level_typed = "level" in vals
        for key, (label, default, unit, tip) in KINDS[kind][2]:
            v = vals.get(key, self._sorter_default(key, default))
            if key == "dir":
                w = QtWidgets.QComboBox()
                w.addItem("Going above the level", "above")
                w.addItem("Going below the level", "below")
                w.setCurrentIndex(max(0, w.findData(v)))
                w.currentIndexChanged.connect(lambda *_: self._changed())
            elif key == "per":
                w = QtWidgets.QComboBox()
                w.addItem("Minute (/min)", "min")
                w.addItem("Second (/s)", "s")
                w.setCurrentIndex(max(0, w.findData(v)))
                w.currentIndexChanged.connect(lambda *_: self._changed())
            elif key == "op":
                w = QtWidgets.QComboBox()
                w.addItems(list(_OPS))
                w.setCurrentText(v)
                w.currentIndexChanged.connect(lambda *_: self._changed())
            elif key == "other":
                w = QtWidgets.QComboBox()
                w.addItems(self.sources)
                if v in self.sources:
                    w.setCurrentText(v)
                elif len(self.sources) > 1:
                    w.setCurrentIndex(1)
                w.currentIndexChanged.connect(lambda *_: self._changed())
            elif key == "formula":
                w = QtWidgets.QLineEdit(v)
                w.setPlaceholderText('e.g. rms(highpass(Ch1, 100), 10) or "Ch1 band-pass" * 2')
                w.textChanged.connect(lambda *_: self._changed())
                self.formula_help = QtWidgets.QLabel(FORMULA_HELP)
                self.formula_help.setWordWrap(True)
                self.formula_help.setStyleSheet("color: gray")
            elif key == "order":
                w = QtWidgets.QSpinBox(minimum=1, maximum=10, value=int(v))
                w.valueChanged.connect(lambda *_: self._changed())
            else:
                # typed units work as everywhere else: "100 mV" in a V box, "2 kHz", "0.5 s"
                if key in ("level", "b"):
                    unit = self.units.get(self.source.currentText(), "")
                lo = 0.0 if unit in ("Hz", "ms") else -1e9
                w = UnitSpinBox(lo, 1e9, unit, decimals=6, step=0.05 if key == "level" else 1.0)
                w.setValue(float(v))
                if key == "level":                        # typed: it stops following the spike search
                    w.valueChanged.connect(lambda *_: setattr(self, "_level_typed", True))
                w.valueChanged.connect(lambda *_: self._changed())
            if tip:
                w.setToolTip(tip)
            self.settings.addRow(label, w)
            self._widgets[key] = w
            if key == "formula":
                self.settings.addRow(self.formula_help)
        self.settings_box.setVisible(bool(self._widgets))
        self.source.setEnabled(kind != "custom")
        uses = [k for k in ("level", "dir", "dead") if k in self._widgets and self._sorter_default(k, None) is not None]
        self.settings_box.setTitle("Settings" + (" (from Find action potentials: its threshold, direction and dead time)"
                                                 if uses else ""))

    def _sorter_default(self, key: str, default):
        """The spike search's setting for a cyclic measurement, if it has one for this source."""
        if key in ("dir", "dead") and self.sorter.get(key) is not None:
            return self.sorter[key]
        if key == "level" and self.sorter.get("level") is not None \
                and self.sorter.get("channel") == self.source.currentText():
            return self.sorter["level"]
        return default

    def values(self) -> dict:
        out = {}
        for key, w in self._widgets.items():
            if isinstance(w, QtWidgets.QComboBox):
                out[key] = w.currentData() if w.currentData() is not None else w.currentText()
            elif isinstance(w, QtWidgets.QLineEdit):
                out[key] = w.text().strip()
            else:
                out[key] = w.value()
        return out

    def current_formula(self) -> str:
        return formula_of(self.kind.currentData(), self.source.currentText(), self.values())

    # -- keeping it consistent --------------------------------------------------------------
    def _changed(self) -> None:
        kind, src, v = self.kind.currentData(), self.source.currentText(), self.values()
        if self._auto_name:
            base = (f"{src} {v.get('op', '')} {v.get('other', '')}" if kind == "combine"
                    else f"{src} {_NAMES[kind]}").strip()
            name, n = base, 2
            while name in self.taken or name in self.sources:
                name, n = f"{base} {n}", n + 1
            self.name.setText(name)
        if self._auto_unit:
            self.unit.setText(unit_for(kind, self.units.get(src, "V"), v))
        lw = self._widgets.get("level")
        if lw is not None and not self._level_typed:      # follow the spike search for its channel
            lw.blockSignals(True)
            lw.set_unit(self.units.get(src, ""))
            lw.setValue(float(self._sorter_default("level", _LEVEL[1][1])))
            lw.blockSignals(False)
        for key in ("level", "b"):                        # these are in the source's unit
            w = self._widgets.get(key)
            new = self.units.get(src, "")
            if w is not None and w.unit != new:
                k = unit_factor(w.unit, new) if w.unit and new else None
                w.blockSignals(True)
                w.set_unit(new)
                if k:
                    w.setValue(w.value() * k)              # 0.1 V becomes 100 mV
                w.blockSignals(False)
        self._check()

    def _check(self) -> bool:
        formula, name = self.current_formula(), self.name.text().strip()
        why = ""
        if not name:
            why = "Give it a name."
        elif name in self.taken or name in self.sources:
            why = f"{name!r} is already used: give it another name."
        else:
            try:
                dsp.Formula(formula, self.sources).check(self.fs)
            except ValueError as e:
                why = str(e)
        self.formula.setText(formula or "—")
        self.message.setText(why)
        self.message.setStyleSheet("color: #e0524f" if why else "")
        self.btn_ok.setEnabled(not why)
        self.curve.setData(np.empty(0), np.empty(0))
        if not why and self.preview_fn is not None:
            try:
                t, y = self.preview_fn(formula)
                self.curve.setData(t, y)
                label = self.unit.text().strip()
                self.plot.getPlotItem().setLabel("left", name, units=label if label in ("V", "mV", "A") else None)
            except ValueError as e:
                self.message.setText(f"Preview: {e}")
        return not why

    def calculation(self) -> dsp.Calculation:
        return dsp.Calculation(self.name.text().strip(), self.current_formula(), self.unit.text().strip())

    def _delete(self) -> None:
        self.deleted = True
        self.accept()
