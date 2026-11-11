"""A number box with units: it shows its value with whichever prefix keeps the number
between 1 and 999 ("10 s", not "10000 ms"), takes typed units, and steps in the unit shown.
Every settings panel uses it (the arithmetic of units is in units.py, without Qt)."""
from __future__ import annotations

import numpy as np
from pyqtgraph.Qt import QtGui, QtWidgets

from .units import _PREFIX, _converter, _tidy_unit, parse_quantity, unit_factor


# Prefixes a field may show, by base unit, smallest first. Others get µ, m and none.
_SHOW_PREFIXES = {"Hz": ("", "k"), "A": ("n", "µ", "m", "")}


class UnitSpinBox(QtWidgets.QDoubleSpinBox):
    """A number box whose value is in a base unit (`unit`) but which shows it with
    whichever prefix keeps the number between 1 and 999: 10000 ms shows as "10 s",
    0.5 V as "500 mV". Typed units are converted ("1 s" in a ms box: value() is 1000)
    and the kind of unit typed is kept (mV vs µA when both are accepted). A bare
    number is in the unit shown, and the arrows step in it.
    `families` lists other units it takes: [(unit, how many base units one is)]."""

    def __init__(self, lo, hi, unit, families=None, decimals=4, step=1.0):
        super().__init__(minimum=lo, maximum=hi, decimals=decimals, singleStep=step)
        self.setKeyboardTracking(False)       # update when you finish typing, not on every digit
        self.unit = ""
        self.set_unit(unit, families)

    def set_unit(self, unit: str, families=None) -> None:
        """Base unit and accepted units. Shows the base unit again."""
        self.unit = unit
        self._families = families or [(unit, 1.0)]
        self._convert = _converter(self._families)
        self.shown = unit
        self.lineEdit().setText(self.textFromValue(self.value()))

    def _shown_factor(self) -> float:
        convert = getattr(self, "_convert", None)    # Qt asks for text before __init__ finishes
        return (convert(self.shown) if convert else None) or 1.0

    def _tidy(self, typed: str) -> str:
        for fam, _ in self._families:
            if unit_factor(typed, fam) is not None:
                return _tidy_unit(typed, fam)
        return typed

    def _best_unit(self, v: float) -> str:
        """The unit to show v in: same kind as the unit shown now, with the largest
        prefix that keeps the number at least 1 (so under 1000)."""
        fam = next((f for f, _ in self._families if unit_factor(self.shown, f) is not None), None)
        if fam is None or v == 0:
            return self.shown
        base = fam[1:] if len(fam) > 1 and fam[0] in _PREFIX else fam
        options = [u for u in (p + base for p in _SHOW_PREFIXES.get(base, ("µ", "m", ""))) if self._convert(u)]
        if not options:                       # a unit without prefixes (mmHg, /min, none)
            return self.shown
        best = options[0]
        for u in options:
            if abs(v) >= self._convert(u) * (1 - 1e-9):
                best = u
        return best

    def textFromValue(self, v: float) -> str:
        if not hasattr(self, "shown"):
            return f"{v:g}"
        self.shown = self._best_unit(v)
        shown = v / self._shown_factor()
        # as many decimals as the box keeps in base units, in the unit shown
        dec = max(0, self.decimals() + round(np.log10(self._shown_factor())))
        return f"{round(shown, dec):.10g} {self.shown}"

    def valueFromText(self, text: str) -> float:
        if not hasattr(self, "_convert"):            # still in __init__
            return super().valueFromText(text)
        if self.specialValueText() and text == self.specialValueText():
            return self.minimum()
        try:
            v, unit = parse_quantity(text, self._convert, self.shown)
        except ValueError:
            return self.value()
        self.shown = self._tidy(unit)          # show what was typed from now on
        return v

    def validate(self, text: str, pos: int):
        S = QtGui.QValidator.State
        if not hasattr(self, "_convert"):            # still in __init__
            return S.Intermediate, text, pos
        if self.specialValueText() and text == self.specialValueText():
            return S.Acceptable, text, pos
        try:
            v, _ = parse_quantity(text, self._convert, self.shown)
        except ValueError:
            return S.Intermediate, text, pos   # still typing; Qt puts the old value back if left like this
        ok = self.minimum() - 1e-9 <= v <= self.maximum() + 1e-9
        return (S.Acceptable if ok else S.Intermediate), text, pos

    def fixup(self, text: str) -> str:
        return self.textFromValue(self.value())

    def stepBy(self, steps: int) -> None:
        # The step is set for the base unit (0.2 ms); in seconds that's 0.2 s.
        base = self.singleStep()
        self.setSingleStep(base * self._shown_factor() / (self._convert(self.unit) or 1.0))
        try:
            super().stepBy(steps)
        finally:
            self.setSingleStep(base)
