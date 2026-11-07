"""The Calculations dialog: add, edit and remove calculated channels (see dsp.py)."""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from . import dsp

_HELP = ("Formulas use the channel names, numbers, + − × ÷ (+ - * /), ** and brackets, and:<br>"
         "<b>lowpass</b>(x, Hz) · <b>highpass</b>(x, Hz) · <b>bandpass</b>(x, low, high) · "
         "<b>bandstop</b>(x, low, high) · <b>notch</b>(x, Hz) · <b>smooth</b>(x, ms) · <b>rms</b>(x, ms) · "
         "<b>envelope</b>(x, ms) · <b>derivative</b>(x) · <b>integral</b>(x) · abs, sqrt, exp, log, sin, cos, "
         "clip(x, lo, hi), max(a, b), min(a, b).<br>"
         "A name with spaces goes in quotes: lowpass(\"Channel 1\", 100). Recordings are never changed: "
         "these are worked out for display and analysis. Recorded trials are filtered without delay; "
         "live, filters have the small delay any live filter has.")


class CalcDialog(QtWidgets.QDialog):
    def __init__(self, parent, calcs: list[dsp.Calculation], channels: list[str], units: dict[str, str],
                 fs: float):
        super().__init__(parent)
        self.setWindowTitle("Calculated channels")
        self.resize(760, 420)
        self.channels, self.units, self.fs = channels, units, fs
        lay = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Formula", "Unit"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 170)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add ▾")
        menu = QtWidgets.QMenu(self)
        for i, (text, *_rest) in enumerate(dsp.PRESETS):
            menu.addAction(text, lambda i=i: self.add(*dsp.preset(i, channels, units)))
        menu.addSeparator()
        menu.addAction("Blank", lambda: self.add("", "", ""))
        self.btn_add.setMenu(menu)
        self.btn_remove = QtWidgets.QPushButton("Remove")
        self.btn_remove.clicked.connect(self._remove)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_remove)
        row.addStretch(1)
        lay.addLayout(row)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        help_ = QtWidgets.QLabel(_HELP)
        help_.setWordWrap(True)
        help_.setStyleSheet("color: gray")
        lay.addWidget(help_)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                             | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.btn_ok = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        lay.addWidget(buttons)
        for c in calcs:
            self.add(c.name, c.formula, c.unit)
        self.table.itemChanged.connect(lambda *_: self.check())
        self.check()

    def add(self, name: str, formula: str, unit: str) -> None:
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        for col, text in enumerate((name, formula, unit)):
            self.table.setItem(r, col, QtWidgets.QTableWidgetItem(text))
        self.table.blockSignals(False)
        self.check()

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()} or {self.table.rowCount() - 1})
        for r in reversed(rows):
            if r >= 0:
                self.table.removeRow(r)
        self.check()

    def calculations(self) -> list[dsp.Calculation]:
        out = []
        for r in range(self.table.rowCount()):
            text = [(self.table.item(r, c).text().strip() if self.table.item(r, c) else "") for c in range(3)]
            out.append(dsp.Calculation(*text))
        return out

    def check(self) -> bool:
        """Mark problems; OK only when every row is a working calculation."""
        problems, seen = [], set()
        for r, c in enumerate(self.calculations()):
            why = ""
            if not c.name:
                why = "needs a name"
            elif c.name in self.channels or c.name in seen:
                why = f"{c.name!r} is already used: give it another name"
            else:
                try:                                     # it can use the rows above it too
                    dsp.Formula(c.formula, self.channels + [x.name for x in self.calculations()[:r]]).check(self.fs)
                except ValueError as e:
                    why = str(e)
            seen.add(c.name)
            item = self.table.item(r, 1)
            if item is not None:
                self.table.blockSignals(True)
                item.setToolTip(why or "OK")
                item.setForeground(QtCore.Qt.GlobalColor.red if why else self.palette().text())
                self.table.blockSignals(False)
            if why:
                problems.append(f"Row {r + 1}: {why}")
        self.message.setText("<br>".join(problems) if problems else
                             ("Each is drawn below the channels, and is a channel on the Analysis tab."
                              if self.table.rowCount() else "No calculated channels."))
        self.message.setStyleSheet("color: #e0524f" if problems else "color: gray")
        self.btn_ok.setEnabled(not problems)
        return not problems
