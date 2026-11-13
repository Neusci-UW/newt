"""Double-click text on a chart to edit or delete it: its title, an axis label, a legend entry, or
a note drawn on the plot. What you change is kept with the chart (in its look), and put back each
time the chart is drawn again; Restore text in the chart's settings undoes all of it.
"""
from __future__ import annotations

import re

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets


class Element:
    """One piece of text on a plot. `key` names it across redraws; value None is the text as drawn,
    "" is deleted, anything else is what you wrote."""

    def __init__(self, key: str, text: str, rect, apply):
        self.key, self.text, self.rect, self.apply = key, text, rect, apply


def _axis(axis: pg.AxisItem):
    if not hasattr(axis, "_label_drawn"):               # its label, as it works itself out, kept to go back to
        axis._label_drawn, axis._label_text = axis.labelString, None

        def drawn() -> str:                             # your words, dressed as the label was (its colour)
            was = axis._label_drawn()
            if axis._label_text is None:
                return was
            m = re.match(r"(<span[^>]*>).*(</span>)$", was)
            return f"{m.group(1)}{axis._label_text}{m.group(2)}" if m else axis._label_text
        axis.labelString = drawn

    def apply(value):
        axis._label_text = value
        axis.showLabel(value != "")
        axis._updateLabel()
    return apply


def elements(pw: pg.PlotWidget, index: int) -> list[Element]:
    """The text showing on a plot: its title, axis labels, legend entries and notes."""
    pi = pw.getPlotItem()
    found = []
    title = pi.titleLabel
    if title.isVisible() and title.text:
        found.append(Element(f"{index}:title", title.text, title.sceneBoundingRect,
                             lambda v: pi.setTitle(v or None)))
    for side in ("left", "bottom"):
        axis = pi.getAxis(side)
        shown = axis.isVisible() and axis.label.isVisible() and axis.label.toPlainText()
        if shown:
            found.append(Element(f"{index}:{side}", shown, axis.label.sceneBoundingRect, _axis(axis)))
    legend = getattr(pi, "legend", None)
    for sample, label in (legend.items if legend is not None else []):
        if not label.isVisible():
            continue
        label._drawn = getattr(label, "_drawn", label.text)

        def apply(v, sample=sample, label=label):
            label.setText(label._drawn if v is None else v)
            for w in (sample, label):
                w.setVisible(v != "")
        found.append(Element(f"{index}:legend:{label._drawn}", label.text, label.sceneBoundingRect, apply))
    notes = [it for it in pi.vb.allChildren() if isinstance(it, pg.TextItem)]
    for k, note in enumerate(notes):
        if note.isVisible() and note.toPlainText():
            note._drawn = getattr(note, "_drawn", note.toPlainText())

            def apply(v, note=note):
                note.setText(note._drawn if v is None else v)
                note.setVisible(v != "")
            found.append(Element(f"{index}:note:{k}", note.toPlainText(), note.sceneBoundingRect, apply))
    return found


def apply_texts(chart) -> None:
    """Put the chart's edited text back on its plots."""
    edits = chart.look.get("texts") or {}
    if not edits:
        return
    for i, pw in enumerate(chart.plots()):
        for e in elements(pw, i):
            if e.key in edits:
                e.apply(edits[e.key])


class _Dialog(QtWidgets.QDialog):
    def __init__(self, text: str, edited: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit text")
        self.result_value: str | None = None             # "" is delete; None, back to what was drawn
        lay = QtWidgets.QVBoxLayout(self)
        self.line = QtWidgets.QLineEdit(text)
        self.line.setMinimumWidth(320)
        lay.addWidget(self.line)
        row = QtWidgets.QHBoxLayout()
        lay.addLayout(row)
        delete = QtWidgets.QPushButton("Delete")
        delete.setToolTip("Take this text off the chart")
        delete.clicked.connect(lambda: self._done(""))
        row.addWidget(delete)
        if edited:
            back = QtWidgets.QPushButton("Restore")
            back.setToolTip("Put back the text the chart draws")
            back.clicked.connect(lambda: self._done(None))
            row.addWidget(back)
        row.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QtWidgets.QPushButton("OK")
        ok.setDefault(True)
        ok.clicked.connect(lambda: self._done(self.line.text()))
        row.addWidget(cancel)
        row.addWidget(ok)
        self.line.selectAll()

    def _done(self, value) -> None:
        self.result_value = value
        self.accept()


class TextEditor(QtCore.QObject):
    """Watches a chart's plots for a double-click on text."""

    def __init__(self, chart):
        super().__init__(chart)
        self.chart = chart

    def watch(self, pw: pg.PlotWidget) -> None:
        if not getattr(pw, "_text_watched", False):
            pw._text_watched = True
            pw.viewport().installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() != QtCore.QEvent.Type.MouseButtonDblClick or ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        pw = obj.parent()
        plots = self.chart.plots()
        if pw not in plots:
            return False
        at = pw.mapToScene(ev.position().toPoint())
        hit = next((e for e in elements(pw, plots.index(pw)) if e.rect().contains(at)), None)
        if hit is None:
            return False
        self.edit(hit)
        return True

    def edit(self, e: Element) -> None:
        texts = self.chart.look.setdefault("texts", {})
        dlg = _Dialog(e.text, e.key in texts, self.chart.window())
        if not dlg.exec():
            return
        if dlg.result_value is None:                      # back to what the chart draws: draw it again
            texts.pop(e.key, None)
            self.chart.refresh()
        else:
            texts[e.key] = dlg.result_value
            e.apply(dlg.result_value)
        self.chart.saved()
        self.chart.texts_changed()
