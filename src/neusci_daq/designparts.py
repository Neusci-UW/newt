"""The parts of Acquisition › Complex Stimulus (stimuluspage.py), in the Simple Stimulus style:

- StimPicture: the whole stimulus drawn large, segment by segment (A, B, C…). Every trial
  is drawn faded and the one shown bold; the selected segment is shaded and its numbers
  written in orange. Click a segment to pick it.
- SegmentPanel: the picked segment's settings in big boxes, each level and duration with a
  dashed "per trial" box under it (blank: the same every trial; +0.5: added each trial;
  0.5, 1, 2: one value per trial).
- KindPicker: "+ Add segment"'s menu, a small picture of each kind.

Levels show in the stimulator's units (×scale); the Stimulus keeps volts at the output.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import stim as stimmod
from . import style
from .simplestim import DIM, _spin

# (label in the picker, kind, settings it starts with, a few words)
KINDS = [("Baseline", "base", {}, "0 V for a while"),
         ("Step", "step", {}, "hold a level"),
         ("Pulse", "pulse", {}, "one short pulse"),
         ("Train", "pulse", {"count": 5, "interval": 50.0}, "pulses at a rate"),
         ("Ramp", "ramp", {}, "from one level to another"),
         ("Sine", "sine", {}, "amplitude, frequency"),
         ("Triangle", "triangle", {}, "straight up and down"),
         ("Square", "square", {}, "on and off at a frequency"),
         ("Chirp", "chirp", {}, "a frequency sweep"),
         ("Noise", "noise", {}, "random, Gaussian"),
         ("Exponential", "exp", {}, "a jump, then a decay"),
         ("Alpha", "alpha", {}, "an EPSC / EPSP shape"),
         ("Custom", "points", {}, "points you type, or a file")]

# Each kind's settings: (key, label, what it is, its per-trial key). What it is: "level"
# (stimulator units), "ms", "Hz", "x" (a multiplier) or "n" (a count).
FIELDS = {
    "base": [("dur", "Duration", "ms", "d_dur")],
    "step": [("level", "Level", "level", "d_level"), ("dur", "Duration", "ms", "d_dur")],
    "pulse": [("level", "Height", "level", "d_level"), ("dur", "Width", "ms", "d_dur"),
              ("count", "Pulses", "n", None), ("interval", "Interval", "ms", None)],
    "ramp": [("start", "From", "level", None), ("level", "To", "level", "d_level"), ("dur", "Duration", "ms", "d_dur")],
    "sine": [("level", "Amplitude", "level", "d_level"), ("freq", "Frequency", "Hz", None),
             ("dur", "Duration", "ms", "d_dur")],
    "triangle": [("level", "Amplitude", "level", "d_level"), ("freq", "Frequency", "Hz", None),
                 ("dur", "Duration", "ms", "d_dur")],
    "square": [("level", "Level", "level", "d_level"), ("freq", "Frequency", "Hz", None),
               ("dur", "Duration", "ms", "d_dur")],
    "chirp": [("level", "Start amplitude", "level", "d_level"), ("level2", "End amplitude", "level", None),
              ("freq", "Start", "Hz", None), ("freq2", "End", "Hz", None), ("dur", "Duration", "ms", "d_dur")],
    "noise": [("level", "SD", "level", "d_level"), ("dur", "Duration", "ms", "d_dur")],
    "exp": [("level", "Jump", "level", "d_level"), ("tau", "τ (decay)", "ms", None), ("dur", "Duration", "ms", "d_dur")],
    "alpha": [("level", "Peak", "level", "d_level"), ("tau", "Peak at", "ms", None), ("dur", "Duration", "ms", "d_dur")],
    "points": [("level", "Scale", "x", "d_level")],
    "file": [("level", "Scale", "x", "d_level")],
}
for _f in FIELDS.values():                   # every kind can sit on a DC offset
    _f.append(("offset", "Offset", "level", None))
HINTS = {"base": "0 V at the output.", "step": "Holds a level, then the next segment starts.",
         "pulse": "One pulse, or a train of them.", "ramp": "A straight line from one level to another.",
         "sine": "A sine wave around the offset.", "triangle": "Straight up to the amplitude and down to minus it.",
         "square": "On at the level for the first half of each cycle, then 0.",
         "chirp": "A sine wave whose frequency and amplitude sweep from start to end.",
         "noise": "Random values around 0, Gaussian with this standard deviation.",
         "exp": "Jumps to the level, then decays with τ.", "alpha": "Rises and falls like an EPSC, peaking at τ.",
         "points": "Your own shape: times and levels, joined by lines or steps (levels × Scale).",
         "file": "A waveform from a file (one column of volts, or time and volts), × Scale."}


# ---------------------------------------------------------------------------- icons
def kind_icon(kind: str, extra: dict, color: str, w: int = 64, h: int = 30) -> QtGui.QPixmap:
    """A small picture of a kind of segment, for the picker."""
    m = h / 2
    if kind == "base":
        pts = [(2, m), (w - 2, m)]
    elif kind == "step":
        pts = [(2, 24), (18, 24), (18, 6), (46, 6), (46, 24), (62, 24)]
    elif kind == "pulse" and extra.get("count", 1) > 1:
        pts = [(2, 24)] + sum([[(8 + i * 12, 24), (8 + i * 12, 6), (12 + i * 12, 6), (12 + i * 12, 24)]
                               for i in range(5)], []) + [(62, 24)]
    elif kind == "pulse":
        pts = [(2, 24), (28, 24), (28, 6), (34, 6), (34, 24), (62, 24)]
    elif kind == "ramp":
        pts = [(2, 24), (14, 24), (50, 6), (50, 24), (62, 24)]
    elif kind == "sine":
        pts = [(2 + i, m - 9 * math.sin(i / 60 * 4 * math.pi)) for i in range(61)]
    elif kind == "triangle":
        pts = [(2, m), (9.5, 6), (24.5, 24), (39.5, 6), (54.5, 24), (62, m)]
    elif kind == "square":
        pts = [(2, 6), (17, 6), (17, 24), (32, 24), (32, 6), (47, 6), (47, 24), (62, 24)]
    elif kind == "chirp":
        pts = [(2 + i * 0.5, m - 9 * math.sin(2 * math.pi * (i / 120) ** 2 * 6)) for i in range(121)]
    elif kind == "noise":
        r = np.random.default_rng(2)
        pts = [(2 + i, m + float(np.clip(r.normal(0, 4), -10, 10))) for i in range(61)]
    elif kind == "exp":
        pts = [(2, 24), (10, 24), (10, 6)] + [(10 + i, 24 - 18 * math.exp(-i / 10)) for i in range(53)]
    elif kind == "alpha":
        pts = [(2, 24)] + [(10 + i, 24 - 18 * (i / 8) * math.exp(1 - i / 8)) for i in range(53)]
    else:
        pts = [(2, 24), (14, 24), (20, 8), (28, 18), (36, 10), (44, 24), (62, 24)]
    pix = QtGui.QPixmap(w * 2, h * 2)
    pix.setDevicePixelRatio(2.0)
    pix.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setPen(QtGui.QPen(QtGui.QColor(color), 2))
    p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(x * w / 64, y * h / 30) for x, y in pts]))
    p.end()
    return pix


class KindPicker(QtWidgets.QFrame):
    """A popup grid: a picture, the name and a few words for each kind."""

    picked = QtCore.Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Popup)
        self.setObjectName("kindPicker")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(10, 10, 10, 10)
        self.grid.setSpacing(6)
        self.buttons = []
        for i, (label, kind, extra, hint) in enumerate(KINDS):
            b = QtWidgets.QToolButton()
            b.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setText(f"{label}\n{hint}")
            b.setIconSize(QtCore.QSize(64, 30))
            b.setMinimumWidth(150)
            b.setAutoRaise(True)
            b.clicked.connect(lambda _=False, k=kind, e=extra: self._pick(k, e))
            self.buttons.append((b, kind, extra))
            self.grid.addWidget(b, i // 4, i % 4)
        self.apply_theme(style.LIGHT)

    def _pick(self, kind: str, extra: dict) -> None:
        self.hide()
        self.picked.emit(kind, dict(extra))

    def popup_under(self, w: QtWidgets.QWidget) -> None:
        self.adjustSize()
        pos = w.mapToGlobal(QtCore.QPoint(w.width() - self.width(), w.height() + 4))
        self.move(pos)
        self.show()

    def apply_theme(self, mode: str) -> None:
        t = style.tokens(mode)
        self.setStyleSheet(f"QFrame#kindPicker {{ background: {t['panel']}; border: 1px solid {t['field_border']};"
                           f" border-radius: 8px; }}"
                           f"QToolButton {{ border: 1px solid {t['line']}; border-radius: 6px; padding: 4px; }}"
                           f"QToolButton:hover {{ border-color: {t['focus']}; background: {t['selection']}; }}")
        for b, kind, extra in self.buttons:
            b.setIcon(QtGui.QIcon(kind_icon(kind, extra, t["focus"])))


# ---------------------------------------------------------------------------- the picture
def segment_spans(s: stimmod.Stimulus, rate: float) -> list[tuple[int, int]]:
    """Where each segment is in the waveform, in samples: [(start, end)]. A segment that
    can't be made (a bad setting) spans its nominal duration."""
    t0, at, out = s.delay_ms, stimmod.samples(s.delay_ms, rate), []
    for p in s.parts:
        try:
            n = len(stimmod._part_wave(p, rate, t0))
        except (ValueError, OSError):
            n = stimmod.span(t0, stimmod._part_ms(p), rate)
        out.append((at, at + n))
        at += n
        t0 += n * 1000.0 / rate if p.get("kind") == "file" else stimmod._part_ms(p)
    return out


class StimPicture(QtWidgets.QWidget):
    picked = QtCore.Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(230)
        self.setMouseTracking(True)
        self.waves: list[np.ndarray] = []      # every trial's waveform; the shown one last
        self.spans: list[tuple[int, int]] = []
        self.names: list[str] = []
        self.sel = -1
        self.dims: list[str] = []
        self.rate = 1.0
        self.units = "V"
        self.k = 1.0
        self.colors = dict(wave="#1a5fd0", dim="#d9822b", text="#5c6370", ink="#1f2328", line="#d9dce1",
                           sel=QtGui.QColor(26, 95, 208, 20), axis="#c9ced6")
        self._hover = -1
        self.compact = False          # a small copy: just the traces, no letters, axis or numbers

    def set_data(self, waves, spans, names, sel, dims, rate, units, k) -> None:
        self.waves, self.spans, self.names, self.sel, self.dims = waves, spans, names, sel, dims
        self.rate, self.units, self.k = rate, units, k
        self.update()

    def _geom(self):
        L, R, T, B = ((4, self.width() - 4, 4, self.height() - 4) if self.compact
                      else (52, self.width() - 14, 30, self.height() - 46))
        n = max((len(w) for w in self.waves), default=1)
        n = max(n, self.spans[-1][1] if self.spans else 1, 2)
        lo = min([0.0] + [float(w.min()) for w in self.waves if len(w)])
        hi = max([0.0] + [float(w.max()) for w in self.waves if len(w)])
        if hi - lo < 1e-9:
            hi, lo = lo + 1.0, lo - 0.1
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad
        X = lambda i: L + i / (n - 1) * (R - L)
        Y = lambda v: B - (v - lo) / (hi - lo) * (B - T)
        return L, R, T, B, X, Y, n, lo, hi

    def _band_at(self, x: float) -> int:
        if not self.spans:
            return -1
        L, R, T, B, X, Y, n, lo, hi = self._geom()
        for i, (a, b) in enumerate(self.spans):
            if X(a) <= x <= X(max(a + 1, b)):
                return i
        return -1

    def mouseMoveEvent(self, ev):
        if self.compact:
            return
        i = self._band_at(ev.position().x())
        if i != self._hover:
            self._hover = i
            self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor if i >= 0 else QtCore.Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, ev):
        self._hover = -1
        self.update()

    def mousePressEvent(self, ev):
        if self.compact:
            return
        i = self._band_at(ev.position().x())
        if i >= 0:
            self.picked.emit(i)

    def _trace(self, w: np.ndarray, X, Y, L, R) -> QtGui.QPainterPath:
        """The waveform as a path, at most two points per pixel (min and max), so noise
        and fast sines still show their full height."""
        path = QtGui.QPainterPath()
        n = len(w)
        cols = max(1, int(R - L))
        if n <= cols * 2:
            for i, v in enumerate(w):
                (path.lineTo if i else path.moveTo)(QtCore.QPointF(X(i), Y(float(v))))
            return path
        edges = np.linspace(0, n, cols + 1).astype(int)
        first = True
        for c in range(cols):
            a, b = edges[c], max(edges[c] + 1, edges[c + 1])
            seg = w[a:b]
            for v in ((seg.min(), seg.max()) if seg[0] <= seg[-1] else (seg.max(), seg.min())):
                pt = QtCore.QPointF(X((a + b) / 2), Y(float(v)))
                if first:
                    path.moveTo(pt)
                    first = False
                else:
                    path.lineTo(pt)
        return path

    def paintEvent(self, ev):
        c = self.colors
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        L, R, T, B, X, Y, n, lo, hi = self._geom()
        small = QtGui.QFont(self.font())
        bold = QtGui.QFont(self.font())
        bold.setBold(True)
        # the segments: shaded when picked or under the mouse, dashed edges, letter and kind under
        for i, (a, b) in enumerate([] if self.compact else self.spans):
            x0, x1 = X(a), X(max(a + 1, b))
            r = QtCore.QRectF(x0, T - 22, x1 - x0, B - T + 22)
            if i == self.sel or i == self._hover:
                col = QtGui.QColor(c["sel"])
                if i != self.sel:
                    col.setAlpha(col.alpha() // 2)
                p.fillRect(r, col)
            pen = QtGui.QPen(QtGui.QColor(c["wave"] if i == self.sel else c["line"]), 1)
            if i != self.sel:
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.setFont(bold)
            p.setPen(QtGui.QColor(c["wave"] if i == self.sel else c["ink"]))
            if x1 - x0 >= 12 or i == self.sel:       # too narrow: no room for its letter
                p.drawText(QtCore.QRectF(x0 - 10, B + 4, x1 - x0 + 20, 16), QtCore.Qt.AlignmentFlag.AlignHCenter,
                           stimmod.letter(i))
            if x1 - x0 > 46 and i < len(self.names):
                p.setFont(small)
                p.setPen(QtGui.QColor(c["text"]))
                p.drawText(QtCore.QRectF(x0, B + 20, x1 - x0, 16), QtCore.Qt.AlignmentFlag.AlignHCenter,
                           p.fontMetrics().elidedText(self.names[i], QtCore.Qt.TextElideMode.ElideRight, int(x1 - x0) - 4))
        # 0 and the axis labels
        p.setFont(small)
        p.setPen(QtGui.QPen(QtGui.QColor(c["axis"]), 1))
        p.drawLine(QtCore.QPointF(L, Y(0)), QtCore.QPointF(R, Y(0)))
        p.setPen(QtGui.QColor(c["text"]))
        for v in () if self.compact else (hi, 0.0, lo):
            if v == 0.0 or abs(Y(v) - Y(0)) > 14:
                p.drawText(QtCore.QRectF(0, Y(v) - 8, L - 6, 16),
                           QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
                           f"{v * self.k:.3g} {self.units}")
        total_ms = n / self.rate * 1000
        if not self.compact:
            p.drawText(QtCore.QRectF(R - 120, B + 20, 120, 16), QtCore.Qt.AlignmentFlag.AlignRight, f"{total_ms:.4g} ms")
        # every trial faded, the shown one bold
        wc = QtGui.QColor(c["wave"])
        for j, w in enumerate(self.waves):
            last = j == len(self.waves) - 1
            col = QtGui.QColor(wc)
            col.setAlpha(255 if last else 55)
            p.setPen(QtGui.QPen(col, 2.2 if last else 1))
            p.drawPath(self._trace(w, X, Y, L, R))
        # the picked segment's numbers, in orange, above it
        if 0 <= self.sel < len(self.spans) and self.dims:
            a, b = self.spans[self.sel]
            p.setFont(bold)
            p.setPen(QtGui.QColor(c["dim"]))
            text = " · ".join(self.dims)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(text) + 8
            cx = (X(a) + X(max(a + 1, b))) / 2
            x = min(max(L, cx - tw / 2), R - tw)
            p.drawText(QtCore.QRectF(x, T - 20, tw, 16), QtCore.Qt.AlignmentFlag.AlignHCenter, text)
        p.end()

    def apply_theme(self, mode: str) -> None:
        t = style.tokens(mode)
        sel = QtGui.QColor(t["focus"])
        sel.setAlpha(28)
        self.colors = dict(wave=t["focus"], dim=DIM[mode], text=t["subtle"], ink=t["ink"], line=t["line"],
                           sel=sel, axis=t["field_border"])
        self.update()


# ---------------------------------------------------------------------------- the editor
class SegmentPanel(QtWidgets.QFrame):
    """The picked segment's settings. `edited` gives the changed segment back; `list_typed`
    the length of a per-trial list that was typed (so there can be that many trials)."""

    edited = QtCore.Signal(dict)
    moved = QtCore.Signal(int)
    removed = QtCore.Signal()
    problem = QtCore.Signal(str)
    list_typed = QtCore.Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("segPanel")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self._mode = style.LIGHT
        self.part: dict = {}
        self.index = -1
        self.units, self.k = "V", 1.0
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(10)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(10)
        self.title = QtWidgets.QLabel()
        self.hint = QtWidgets.QLabel()
        self.hint.setWordWrap(True)
        self.btn_left = QtWidgets.QPushButton("◀")
        self.btn_left.setToolTip("Move this segment earlier")
        self.btn_right = QtWidgets.QPushButton("▶")
        self.btn_right.setToolTip("Move this segment later")
        self.btn_remove = QtWidgets.QPushButton("Remove")
        self.btn_remove.setToolTip("Take this segment out")
        head.addWidget(self.title)
        head.addWidget(self.hint, 1)
        for b in (self.btn_left, self.btn_right, self.btn_remove):
            b.setFlat(True)
            head.addWidget(b)
        v.addLayout(head)
        self.body = QtWidgets.QWidget()
        self.body_lay = QtWidgets.QHBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(16)
        v.addWidget(self.body)
        self.btn_left.clicked.connect(lambda: self.moved.emit(-1))
        self.btn_right.clicked.connect(lambda: self.moved.emit(1))
        self.btn_remove.clicked.connect(self.removed.emit)

    # -- building ------------------------------------------------------------------------
    def show_part(self, i: int, part: dict, units: str, k: float, n_parts: int) -> None:
        self.index, self.part, self.units, self.k = i, dict(part), units, k
        self.n_parts = n_parts
        kind = part.get("kind", "step")
        name = "Train" if kind == "pulse" and int(part.get("count", 1)) > 1 else stimmod.SEG_KINDS.get(kind, kind)
        self.title.setText(f"<b>{stimmod.letter(i)} · {name}</b>")
        self.hint.setText(HINTS.get(kind, ""))
        self.btn_left.setEnabled(i > 0)
        self.btn_right.setEnabled(i < n_parts - 1)
        self.btn_remove.setEnabled(n_parts > 1)
        while self.body_lay.count():
            it = self.body_lay.takeAt(0)
            w = it.widget()
            if w is not None:                    # hidden at once, not when Qt gets round to deleting it
                w.hide()
                w.deleteLater()
        for key, label, what, dkey in FIELDS.get(kind, []):
            self.body_lay.addWidget(self._field(key, label, what, dkey))
        extra = self._extras(kind)
        if extra is not None:
            self.body_lay.addWidget(extra)
        self.body_lay.addStretch(1)

    def _field(self, key, label, what, dkey) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lab = QtWidgets.QLabel(label if key == "offset" else f"<span style='color:{DIM[self._mode]}'>■</span> {label}")
        lay.addWidget(lab)
        val = self.part.get(key, 0)
        if what == "n":
            box = QtWidgets.QSpinBox(minimum=1, maximum=1000)
            box.setKeyboardTracking(False)
            box.setValue(int(val))
            box.setMinimumWidth(110)
            box.valueChanged.connect(lambda v, key=key: self._set(key, int(v)))
        else:
            k = self.k if what == "level" else 1.0
            lim = {"level": (-1000 * k, 1000 * k, 3, self.units), "ms": (0, 600000, 3, "ms"),
                   "Hz": (0.001, 100000, 3, "Hz"), "x": (-1000, 1000, 3, "×")}[what]
            box = _spin(lim[0], lim[1], lim[2], lim[3], f"{label} of {stimmod.letter(self.index)}")
            box.setValue(float(val) * k)
            box.valueChanged.connect(lambda v, key=key, k=k: self._set(key, float(v) / k))
        lay.addWidget(box)
        if dkey:
            pt = QtWidgets.QLineEdit()
            pt.setObjectName("perTrial")
            k = self.k if what == "level" else 1.0
            try:
                pt.setText(stimmod.format_per_trial(stimmod.parse_per_trial(self.part.get(dkey, "")), k))
            except ValueError:
                pt.setText(self.part.get(dkey, ""))
            pt.setPlaceholderText("same every trial")
            pt.setToolTip("How it changes each trial: blank = the same, +0.5 = added each trial, "
                          "0.5, 1, 2 = one value per trial")
            pt.setProperty("set", bool(pt.text()))
            pt.editingFinished.connect(lambda pt=pt, dkey=dkey, k=k: self._per_trial(pt, dkey, k))
            lay.addWidget(pt)
        lay.addStretch(1)
        return w

    def _extras(self, kind: str) -> QtWidgets.QWidget | None:
        p = self.part
        if kind == "pulse":
            cb = QtWidgets.QCheckBox("Biphasic")
            cb.setToolTip("Each pulse goes up, then down by as much")
            cb.setChecked(bool(p.get("biphasic")))
            cb.toggled.connect(lambda on: self._set("biphasic", bool(on)))
            return cb
        if kind == "chirp":
            return self._toggle("Sweep", ("Linear", "Log"), 1 if p.get("log") else 0, lambda i: self._set("log", i == 1))
        if kind == "noise":
            same = int(p.get("seed", 0)) != 0
            return self._toggle("Each trial", ("New noise", "Same noise"), 1 if same else 0,
                                lambda i: self._set("seed", 7 if i == 1 else 0))
        if kind == "points":
            return self._points()
        if kind == "file":
            w = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(QtWidgets.QLabel("File"))
            row = QtWidgets.QHBoxLayout()
            name = QtWidgets.QLabel(Path(p.get("file", "")).name or "(none)")
            b = QtWidgets.QPushButton("Choose…")
            b.clicked.connect(self._browse)
            row.addWidget(name)
            row.addWidget(b)
            lay.addLayout(row)
            tp = QtWidgets.QPushButton("Type points instead")
            tp.setFlat(True)
            tp.clicked.connect(lambda: self.edited.emit({**stimmod.new_part("points"), "level": 1.0}))
            lay.addWidget(tp)
            lay.addStretch(1)
            return w
        return None

    def _toggle(self, label, names, on, fn) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(QtWidgets.QLabel(label))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(2)
        group = QtWidgets.QButtonGroup(w)
        for i, n in enumerate(names):
            b = QtWidgets.QPushButton(n, checkable=True)
            b.setChecked(i == on)
            group.addButton(b, i)
            row.addWidget(b)
        group.idClicked.connect(fn)
        lay.addLayout(row)
        lay.addStretch(1)
        return w

    def _points(self) -> QtWidgets.QWidget:
        p = self.part
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(QtWidgets.QLabel(f"Points (time in ms, level in {self.units})"))
        table = QtWidgets.QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["time (ms)", f"level ({self.units})"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumWidth(220)
        table.setMaximumHeight(170)
        for a, b in p.get("points") or []:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QtWidgets.QTableWidgetItem(f"{float(a):g}"))
            table.setItem(r, 1, QtWidgets.QTableWidgetItem(f"{float(b) * self.k:g}"))
        table.itemChanged.connect(lambda *_: self._points_edited(table))
        lay.addWidget(table)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("+ Point")
        rm = QtWidgets.QPushButton("− Last")

        def add_row():
            pts = p.get("points") or [[0.0, 0.0]]
            last = pts[-1]
            self._set_points(pts + [[float(last[0]) + 10.0, 0.0]], rebuild=True)
        add.clicked.connect(add_row)
        rm.clicked.connect(lambda: self._set_points((p.get("points") or [])[:-1], rebuild=True))
        row.addWidget(add)
        row.addWidget(rm)
        row.addWidget(self._toggle("", ("Lines", "Steps"), 1 if p.get("join") == "step" else 0,
                                   lambda i: self._set("join", "step" if i == 1 else "line")))
        frm = QtWidgets.QPushButton("From file…")
        frm.setToolTip("Use a waveform file instead (one column of volts, or time and volts)")
        frm.clicked.connect(self._browse)
        row.addWidget(frm)
        lay.addLayout(row)
        return w

    def _points_edited(self, table) -> None:
        pts = []
        for r in range(table.rowCount()):
            try:
                a = float(table.item(r, 0).text())
                b = float(table.item(r, 1).text()) / self.k
            except (AttributeError, ValueError):
                self.problem.emit(f"Row {r + 1}: type a number in both columns.")
                return
            pts.append([a, b])
        self._set_points(pts)

    def _set_points(self, pts, rebuild=False) -> None:
        if len(pts) < 2:
            self.problem.emit("Custom points need at least two points.")
            return
        self.part["points"] = pts
        self.part["dur"] = max(float(a) for a, _ in pts)
        self.edited.emit(dict(self.part))
        if rebuild:
            self.show_part(self.index, self.part, self.units, self.k, self.n_parts)

    def _browse(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Waveform file", "",
                                                        "Waveforms (*.csv *.txt *.tsv);;All files (*)")
        if path:
            self.edited.emit({**stimmod.new_part("file", 1.0), "file": path,
                              "d_level": self.part.get("d_level", "")})

    # -- edits ---------------------------------------------------------------------------
    def _set(self, key, value) -> None:
        self.part[key] = value
        self.edited.emit(dict(self.part))

    def _per_trial(self, box: QtWidgets.QLineEdit, dkey: str, k: float) -> None:
        try:
            got = stimmod.parse_per_trial(box.text())
        except ValueError as e:
            self.problem.emit(f"{stimmod.letter(self.index)}: {e}")
            return
        text = stimmod.format_per_trial(got, 1.0 / k)
        if text == self.part.get(dkey, ""):
            return
        box.setProperty("set", bool(text))
        box.style().unpolish(box)
        box.style().polish(box)
        self.part[dkey] = text
        self.edited.emit(dict(self.part))
        if got and got[0] == "list":
            self.list_typed.emit(len(got[1]))

    def dims(self) -> list[str]:
        """The picked segment's main numbers, for the picture."""
        p, u, k = self.part, self.units, self.k
        kind = p.get("kind")
        lv = lambda key: f"{float(p.get(key, 0)) * k:g} {u}"
        out = []
        for key, label, what, dkey in FIELDS.get(kind, []):
            if key == "offset":
                if float(p.get("offset", 0) or 0):
                    out.append(f"offset {lv('offset')}")
                continue
            if key == "level2":
                if float(p.get("level2", p.get("level", 0))) != float(p.get("level", 0)):
                    out[-1] = out[-1].split(" (")[0] + f" → {lv('level2')}"
                continue
            if what == "level":
                out.append(lv(key))
            elif what == "ms":
                out.append(f"{float(p.get(key, 0)):g} ms" if key == "dur" else f"{label} {float(p.get(key, 0)):g} ms")
            elif what == "Hz":
                out.append(f"{float(p.get(key, 0)):g} Hz")
            elif what == "n" and int(p.get(key, 1)) > 1:
                out.append(f"×{int(p[key])}")
            if dkey and p.get(dkey):
                got = stimmod.parse_per_trial(p[dkey])
                kk = k if what == "level" else 1.0
                out[-1] += f" ({stimmod.format_per_trial(got, kk)}{' / trial' if got[0] == 'step' else ''})"
        if kind == "chirp" and len(out) >= 3:              # amplitude, start Hz, end Hz …
            out[1:3] = [f"{out[1]} → {out[2]}"]
        return out

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        t = style.tokens(mode)
        self.setStyleSheet(f"QFrame#segPanel {{ border: 1px solid {t['line']}; border-radius: 8px; }}"
                           f"QLineEdit#perTrial {{ border: 1px dashed {t['field_border']}; }}"
                           f"QLineEdit#perTrial[set=\"true\"] {{ border: 1px solid {t['focus']};"
                           f" background: {t['selection']}; }}")
        self.hint.setStyleSheet(f"color: {t['subtle']}")
