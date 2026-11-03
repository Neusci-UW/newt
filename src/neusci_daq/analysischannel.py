"""Add analysis channel: LabChart's Channel Calculation dialog. Pick a source channel
and a calculation, fill in its settings, and the formula is written for you (dsp.py
works it out). The source can be a recorded channel or an analysis channel above, so
calculations build on each other: filter, then rectify, then smooth, then measure.

Each kind is a row of KINDS: its label, its settings, and how they become a formula.
`spec_of` reads a formula back into a kind and settings, so Edit reopens the form.
"""
from __future__ import annotations

import re

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from . import dsp
from .plotzoom import TraceCurve
from .stimwindow import UnitSpinBox
from .units import unit_factor

# setting: (label, default, unit shown, tooltip)
_LEVEL = ("level", ("Level", 0.5, "", "An event is each crossing of this level (in the source's units)"))
_DIR = ("dir", ("Direction", "above", "", "Count crossings going above the level, or going below it"))
_DEAD = ("dead", ("Dead time", 1.0, "ms", "Ignore crossings this soon after an event (noise on the edge)"))

# key: (menu label, group, [(setting key, (label, default, unit, tip))], unit rule)
KINDS = {
    "lowpass": ("Low-pass filter", "Digital filter", [("hz", ("Cut-off", 100.0, "Hz", "Keeps slower than this")),
                                                     ("order", ("Order", 4, "", "Steeper roll-off when higher"))], "{u}"),
    "highpass": ("High-pass filter", "Digital filter", [("hz", ("Cut-off", 100.0, "Hz", "Keeps faster than this")),
                                                       ("order", ("Order", 4, "", "Steeper roll-off when higher"))], "{u}"),
    "bandpass": ("Band-pass filter", "Digital filter", [("lo", ("Low cut-off", 300.0, "Hz", "")),
                                                       ("hi", ("High cut-off", 3000.0, "Hz", "")),
                                                       ("order", ("Order", 4, "", ""))], "{u}"),
    "bandstop": ("Band-stop filter", "Digital filter", [("lo", ("Low", 55.0, "Hz", "")), ("hi", ("High", 65.0, "Hz", "")),
                                                       ("order", ("Order", 4, "", ""))], "{u}"),
    "notch": ("Notch (mains hum)", "Digital filter", [("hz", ("Frequency", 60.0, "Hz", "60 Hz in the US, 50 Hz in Europe"))],
              "{u}"),
    "smooth": ("Smoothing (moving average)", "Smoothing & shape", [("ms", ("Window", 1.0, "ms", ""))], "{u}"),
    "rms": ("RMS (moving)", "Smoothing & shape", [("ms", ("Window", 10.0, "ms", ""))], "{u}"),
    "envelope": ("Rectify and smooth (envelope)", "Smoothing & shape", [("ms", ("Window", 10.0, "ms", ""))], "{u}"),
    "abs": ("Absolute value (rectify)", "Smoothing & shape", [], "{u}"),
    "derivative": ("Derivative", "Calculus", [], "{u}/s"),
    "integral": ("Integral", "Calculus", [], "{u}·s"),
    "scale": ("Scale and offset", "Arithmetic", [("a", ("Multiply by", 1.0, "", "")),
                                                ("b", ("Then add", 0.0, "", ""))], "{u}"),
    "combine": ("With another channel (+ − × ÷)", "Arithmetic", [("op", ("Operation", "−", "", "")),
                                                                ("other", ("Other channel", "", "", ""))], "{u}"),
    "rate": ("Rate meter", "Cyclic measurements", [_LEVEL, _DIR, _DEAD,
                                                   ("per", ("Per", "min", "", "Events per minute (e.g. heart "
                                                                              "rate) or per second"))], "/{per}"),
    "period": ("Period (time between events)", "Cyclic measurements", [_LEVEL, _DIR, _DEAD], "s"),
    "height": ("Cyclic height (max − min per cycle)", "Cyclic measurements", [_LEVEL, _DIR, _DEAD], "{u}"),
    "count": ("Event count", "Cyclic measurements", [_LEVEL, _DIR, _DEAD], ""),
    "custom": ("Custom formula", "Other", [("formula", ("Formula", "", "", ""))], "{u}"),
}
_NAMES = {"lowpass": "low-pass", "highpass": "high-pass", "bandpass": "band-pass", "bandstop": "band-stop",
          "notch": "no hum", "smooth": "smoothed", "rms": "RMS", "envelope": "envelope", "abs": "rectified",
          "derivative": "derivative", "integral": "integral", "scale": "scaled", "combine": "",
          "rate": "rate", "period": "period", "height": "cyclic height", "count": "count", "custom": "calc"}
_OPS = {"+": "+", "−": "-", "×": "*", "÷": "/"}


def quote(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_]\w*", name) else f'"{name}"'


def _g(v) -> str:
    return f"{float(v):g}"


def formula_of(kind: str, src: str, v: dict) -> str:
    """The formula for a kind of calculation of channel `src` with settings v."""
    q = quote(src)
    if kind in ("lowpass", "highpass"):
        return f"{kind}({q}, {_g(v['hz'])}" + (f", {int(v['order'])})" if int(v["order"]) != 4 else ")")
    if kind in ("bandpass", "bandstop"):
        return f"{kind}({q}, {_g(v['lo'])}, {_g(v['hi'])}" + (f", {int(v['order'])})" if int(v["order"]) != 4 else ")")
    if kind == "notch":
        return f"notch({q}, {_g(v['hz'])})"
    if kind in ("smooth", "rms", "envelope"):
        return f"{kind}({q}, {_g(v['ms'])})"
    if kind in ("abs", "derivative", "integral"):
        return f"{kind}({q})"
    if kind == "scale":
        a, b = float(v["a"]), float(v["b"])
        out = q if a == 1 else f"{_g(a)} * {q}"
        return out if b == 0 else f"{out} {'+' if b > 0 else '-'} {_g(abs(b))}"
    if kind == "combine":
        return f"{q} {_OPS.get(v['op'], v['op'])} {quote(v['other'])}"
    if kind in ("rate", "period", "height", "count"):
        x, level = (q, float(v["level"])) if v["dir"] == "above" else (f"-{q}", -float(v["level"]))
        dead = "" if float(v["dead"]) == 1 else f", {_g(v['dead'])}"
        body = f"{kind}({x}, {_g(level)}{dead})"
        return f"60 * {body}" if kind == "rate" and v.get("per") == "min" else body
    return v.get("formula", "")


_NUM = r"(-?[\d.]+(?:e-?\d+)?)"
_SRC = r'("[^"]+"|[A-Za-z_]\w*)'


def spec_of(formula: str) -> tuple[str, str, dict] | None:
    """(kind, source, settings) for a formula this dialog writes; None for others."""
    f = formula.strip()
    unq = lambda s: s[1:-1] if s.startswith('"') else s
    m = re.fullmatch(rf"(lowpass|highpass)\({_SRC}, {_NUM}(?:, (\d+))?\)", f)
    if m:
        return m[1], unq(m[2]), {"hz": float(m[3]), "order": int(m[4] or 4)}
    m = re.fullmatch(rf"(bandpass|bandstop)\({_SRC}, {_NUM}, {_NUM}(?:, (\d+))?\)", f)
    if m:
        return m[1], unq(m[2]), {"lo": float(m[3]), "hi": float(m[4]), "order": int(m[5] or 4)}
    m = re.fullmatch(rf"notch\({_SRC}, {_NUM}\)", f)
    if m:
        return "notch", unq(m[1]), {"hz": float(m[2])}
    m = re.fullmatch(rf"(smooth|rms|envelope)\({_SRC}, {_NUM}\)", f)
    if m:
        return m[1], unq(m[2]), {"ms": float(m[3])}
    m = re.fullmatch(rf"(abs|derivative|integral)\({_SRC}\)", f)
    if m:
        return m[1], unq(m[2]), {}
    m = re.fullmatch(rf"(60 \* )?(rate|period|height|count)\((-?){_SRC}, {_NUM}(?:, {_NUM})?\)", f)
    if m and (not m[1] or m[2] == "rate"):
        below = m[3] == "-"
        v = {"level": -float(m[5]) if below else float(m[5]), "dir": "below" if below else "above",
             "dead": float(m[6] or 1)}
        if m[2] == "rate":
            v["per"] = "min" if m[1] else "s"
        return m[2], unq(m[4]), v
    m = re.fullmatch(rf"(?:{_NUM} \* )?{_SRC}(?: ([+-]) {_NUM})?", f)
    if m:                                             # a plain copy is a scale of 1
        b = float(m[4]) * (1 if m[3] == "+" else -1) if m[3] else 0.0
        return "scale", unq(m[2]), {"a": float(m[1] or 1), "b": b}
    m = re.fullmatch(rf"{_SRC} ([-+*/]) {_SRC}", f)
    if m:
        op = {v: k for k, v in _OPS.items()}[m[2]]
        return "combine", unq(m[1]), {"op": op, "other": unq(m[3])}
    return None


def unit_for(kind: str, src_unit: str, v: dict) -> str:
    return KINDS[kind][3].format(u=src_unit, per=v.get("per", "s"))


class AnalysisChannelDialog(QtWidgets.QDialog):
    """Make or change one analysis channel. `sources`: names usable as the source (the
    recorded channels, then the analysis channels above this one); `units` their units;
    `preview(formula)` returns (t, y) of the result on the data shown, or raises ValueError."""

    def __init__(self, parent, sources: list[str], units: dict[str, str], fs: float,
                 calc: dsp.Calculation | None = None, taken: set[str] | None = None, preview=None,
                 sorter: dict | None = None):
        super().__init__(parent)
        # Spike Sorting's settings, the defaults for cyclic measurements: {"channel", "level"
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
        self.plot = pg.PlotWidget()
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
                if key == "level":                        # typed: it stops following Spike Sorting
                    w.valueChanged.connect(lambda *_: setattr(self, "_level_typed", True))
                w.valueChanged.connect(lambda *_: self._changed())
            if tip:
                w.setToolTip(tip)
            self.settings.addRow(label, w)
            self._widgets[key] = w
        self.settings_box.setVisible(bool(self._widgets))
        self.source.setEnabled(kind != "custom")
        uses = [k for k in ("level", "dir", "dead") if k in self._widgets and self._sorter_default(k, None) is not None]
        self.settings_box.setTitle("Settings" + (" (from Spike Sorting: its threshold, direction and dead time)"
                                                 if uses else ""))

    def _sorter_default(self, key: str, default):
        """Spike Sorting's setting for a cyclic measurement, if it has one for this source."""
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
        if lw is not None and not self._level_typed:      # follow Spike Sorting for its channel
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
