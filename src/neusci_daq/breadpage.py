"""The Breadboard simulator page, under Data Simulation (only with --backend
simulated): parts placed on a breadboard, and probes on it that Acquisition records
like any input. See breadboard.py for the board and how the circuit is solved.

Drag a part from the strip onto the board, or pick it and click two holes (one for
a probe). Click a part to select it and set its value; drag it, or one of its ends,
to move it. Delete removes it, Esc cancels a placement. Changes
apply straight away, while recording too, and Setup › Save channel setup writes the
board to the config's [simulated] section.
"""
from __future__ import annotations

import math

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import breadboard as bb
from . import style, theme
from .unitbox import UnitSpinBox
from .units import best_prefix
from .viewkit import scroll_column

PITCH = 20                                    # board units between holes
_ROW_Y = {"t+": 20, "t-": 40, **{r: 80 + 20 * i for i, r in enumerate("abcde")},
          **{r: 200 + 20 * i for i, r in enumerate("fghij")}, "b+": 300, "b-": 320}
_X0, _W, _H = 40, 40 + bb.COLS * PITCH + 20, 345
_TOOLS = (("select", "Select", "Click a part to select it and change its value; drag it, or one of its ends, to move it"),
          ("vin", "Input voltage", "The stimulator's output, or a fixed voltage: drag it onto the board, "
                                   "or click its + end, then its - end"),
          ("resistor", "Resistor", "Drag it onto the board, or click its two ends"),
          ("capacitor", "Capacitor", "Drag it onto the board, or click its two ends"),
          ("wire", "Wire", "Joins two holes: drag it onto the board, or click its two ends"),
          ("probe", "Probe", "A recording site: drag it onto a hole, or click one; then pick the input it records on"))
_PART_COLOR = {"vin": "#7c4dff", "resistor": "#b87a1e", "capacitor": "#1e88a8", "wire": "#2e7d32"}   # all read on light and dark boards
_PROBE_FREE = "#8a8f98"
_WARN = {style.LIGHT: "#8a5a00", style.DARK: "#f2c14e"}


def hole_xy(hole: str) -> QtCore.QPointF:
    row, col = bb.parse_hole(hole)
    return QtCore.QPointF(_X0 + (col - 1) * PITCH, _ROW_Y[row])


def nearest_hole(p: QtCore.QPointF) -> str | None:
    col = round((p.x() - _X0) / PITCH) + 1
    if not 1 <= col <= bb.COLS or abs(p.x() - (_X0 + (col - 1) * PITCH)) > 8:
        return None
    row = min(_ROW_Y, key=lambda r: abs(_ROW_Y[r] - p.y()))
    if abs(_ROW_Y[row] - p.y()) > 8:
        return None
    return f"{row}{col}"


_ROWS = ["t+", "t-", *"abcde", *"fghij", "b+", "b-"]   # top to bottom
_MIME = "application/x-newt-part"
_SPAN = 4                                     # columns a part dropped from the strip spans


def shifted(hole: str, drow: int, dcol: int) -> str | None:
    """`hole` moved down `drow` rows and right `dcol` columns, or None off the board."""
    row, col = bb.parse_hole(hole)
    r, c = _ROWS.index(row) + drow, col + dcol
    return f"{_ROWS[r]}{c}" if 0 <= r < len(_ROWS) and 1 <= c <= bb.COLS else None


def dropped_part(kind: str, hole: str) -> bb.Part:
    """A part dropped on `hole`: a probe there, anything else from there across _SPAN columns."""
    if kind == "probe":
        return bb.Part("probe", hole)
    end = shifted(hole, 0, _SPAN) or shifted(hole, 0, -_SPAN)
    return bb.Part(kind, hole, end, bb.DEFAULT_VALUE[kind], source="stim")


class PartButton(QtWidgets.QPushButton):
    """A part in the strip: click it to place by clicking holes, or drag it onto the board."""

    def __init__(self, kind: str, label: str):
        super().__init__(label)
        self.kind = kind
        self._press = None

    def mousePressEvent(self, e):
        self._press = e.position() if e.button() == QtCore.Qt.MouseButton.LeftButton else None
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is None or (e.position() - self._press).manhattanLength() < QtWidgets.QApplication.startDragDistance():
            return super().mouseMoveEvent(e)
        self._press = None
        self.setDown(False)
        mime = QtCore.QMimeData()
        mime.setData(_MIME, self.kind.encode())
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.exec(QtCore.Qt.DropAction.CopyAction)


def _seg_dist(p: QtCore.QPointF, a: QtCore.QPointF, b: QtCore.QPointF) -> float:
    ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p.x() - ax) * dx + (p.y() - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p.x() - ax - t * dx, p.y() - ay - t * dy)


class BoardView(QtWidgets.QWidget):
    """The breadboard drawn with its parts; places, selects and removes parts."""
    edited = QtCore.Signal(object)            # a new Board
    selected = QtCore.Signal(int)             # index into board.parts, -1 = none

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.board = bb.Board()
        self.tool = "select"
        self.sel = -1
        self.pending: str | None = None       # first hole of a part being placed
        self.hover: str | None = None
        self.mouse = QtCore.QPointF()
        self.drag: dict | None = None         # a placed part (or one end of it) being moved
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(560, 260)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)

    def sizeHint(self):
        return QtCore.QSize(_W * 2, _H * 2)

    def heightForWidth(self, w: int) -> int:
        return int(w * _H / _W)

    def hasHeightForWidth(self) -> bool:
        return True

    # -- geometry: board units <-> widget pixels
    def _scale(self) -> tuple[float, float, float]:
        s = min(self.width() / _W, self.height() / _H)
        return s, (self.width() - _W * s) / 2, (self.height() - _H * s) / 2

    def _to_board(self, pos: QtCore.QPointF) -> QtCore.QPointF:
        s, ox, oy = self._scale()
        return QtCore.QPointF((pos.x() - ox) / s, (pos.y() - oy) / s)

    # -- input
    def set_tool(self, tool: str) -> None:
        self.tool, self.pending = tool, None
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor if tool == "select" else QtCore.Qt.CursorShape.CrossCursor)
        self.update()

    def mouseMoveEvent(self, e):
        self.mouse = self._to_board(e.position())
        self.hover = nearest_hole(self.mouse)
        d = self.drag
        if d is not None and self.hover:
            dr = _ROWS.index(bb.parse_hole(self.hover)[0]) - _ROWS.index(bb.parse_hole(d["from"])[0])
            dc = bb.parse_hole(self.hover)[1] - bb.parse_hole(d["from"])[1]
            part = d["board"].parts[d["i"]]
            ends = {"a": part.a, "b": part.b} if d["end"] is None else {d["end"]: getattr(part, d["end"])}
            moved = {k: shifted(h, dr, dc) for k, h in ends.items() if h}
            if all(moved.values()):
                board = bb.copy_board(d["board"])
                for k, h in moved.items():
                    setattr(board.parts[d["i"]], k, h)
                if board.parts[d["i"]].a != board.parts[d["i"]].b:
                    self.board = board                   # shown while dragging; applied on release
                    d["moved"] = (dr, dc) != (0, 0)
        self.update()

    def mouseReleaseEvent(self, e):
        d, self.drag = self.drag, None
        if d is not None and d.get("moved"):
            self.edited.emit(self.board)
        if self.tool == "select":
            self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    # dragged in from the parts strip
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        self.mouse = self._to_board(e.position())
        self.hover = nearest_hole(self.mouse)
        e.acceptProposedAction() if self.hover else e.ignore()
        self.update()

    def dragLeaveEvent(self, e):
        self.hover = None
        self.update()

    def dropEvent(self, e):
        hole = nearest_hole(self._to_board(e.position()))
        kind = bytes(e.mimeData().data(_MIME)).decode()
        if hole is None or kind not in bb.KINDS:
            return
        e.acceptProposedAction()
        self.page.tool_buttons["select"].click()          # back to Select, so the new part can be moved
        self._add(dropped_part(kind, hole))

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        p = self._to_board(e.position())
        if self.tool == "select":
            i = self._part_at(p)
            self.select(i)
            if i >= 0:                                  # grab it: an end moves that end, the body the whole part
                part = self.board.parts[i]
                end = next((k for k in ("a", "b") if getattr(part, k) and part.kind != "probe" and
                            math.hypot(p.x() - hole_xy(getattr(part, k)).x(), p.y() - hole_xy(getattr(part, k)).y()) < 6),
                           None)
                start = getattr(part, end) if end else (nearest_hole(p) or part.a)
                self.drag = {"i": i, "end": end, "from": start, "board": self.board, "moved": False}
                self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            return
        hole = nearest_hole(p)
        if hole is None:
            return
        if self.tool == "probe":
            self._add(bb.Part("probe", hole))
        elif self.pending is None:
            self.pending = hole
        elif hole != self.pending:
            self._add(bb.Part(self.tool, self.pending, hole, bb.DEFAULT_VALUE[self.tool],
                              source="stim"))
            self.pending = None
        self.update()

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key.Key_Escape:
            self.pending = None
            self.update()
        elif e.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            self.page.delete_selected()
        else:
            super().keyPressEvent(e)

    def _add(self, part: bb.Part) -> None:
        board = bb.copy_board(self.board)
        board.parts.append(part)
        self.sel = len(board.parts) - 1
        self.edited.emit(board)

    def _part_at(self, p: QtCore.QPointF) -> int:
        best, idx = 9.0, -1
        for i, part in enumerate(self.board.parts):
            a = hole_xy(part.a)
            d = math.hypot(p.x() - a.x(), p.y() - a.y()) if part.kind == "probe" else _seg_dist(p, a, hole_xy(part.b))
            if d < best:
                best, idx = d, i
        return idx

    def select(self, i: int) -> None:
        self.sel = i
        self.selected.emit(i)
        self.update()

    # -- drawing
    def paintEvent(self, e):
        t = style.tokens(self.page._mode)
        dark = self.page._mode == style.DARK
        q = QtGui.QPainter(self)
        q.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        s, ox, oy = self._scale()
        q.translate(ox, oy)
        q.scale(s, s)
        q.setPen(QtGui.QPen(QtGui.QColor(t["line"]), 1))
        q.setBrush(QtGui.QColor("#2a2d33" if dark else "#f7f6f2"))
        q.drawRoundedRect(QtCore.QRectF(4, 4, _W - 8, _H - 8), 10, 10)
        q.fillRect(QtCore.QRectF(12, 172, _W - 24, 16), QtGui.QColor(t["ground"]))   # the channel down the middle
        for y, col in ((14, "#d64545"), (46, "#2f6fd6"), (294, "#d64545"), (326, "#2f6fd6")):
            q.setPen(QtGui.QPen(QtGui.QColor(col), 1.2))
            q.drawLine(QtCore.QPointF(_X0 - 12, y), QtCore.QPointF(_X0 + (bb.COLS - 1) * PITCH + 12, y))
        q.setPen(QtGui.QColor(t["muted"]))
        f = q.font()
        f.setPointSizeF(7.5)
        q.setFont(f)
        for y, txt in ((24, "+"), (44, "−"), (304, "+"), (324, "−")):
            q.drawText(QtCore.QPointF(14, y), txt)
        for r in "abcdefghij":
            q.drawText(QtCore.QPointF(18, _ROW_Y[r] + 3), r)
        for c in range(1, bb.COLS + 1, 5):
            q.drawText(QtCore.QPointF(_X0 + (c - 1) * PITCH - 3, 62), str(c))
        hole = QtGui.QColor("#5a5f68" if dark else "#9aa0a8")
        q.setPen(QtCore.Qt.PenStyle.NoPen)
        q.setBrush(hole)
        for row, y in _ROW_Y.items():
            for c in range(bb.COLS):
                q.drawRect(QtCore.QRectF(_X0 + c * PITCH - 2.2, y - 2.2, 4.4, 4.4))
        for i, part in enumerate(self.board.parts):
            self._draw_part(q, part, i == self.sel, t)
        if self.pending:                                  # a part being placed: from its first hole to the mouse
            a = hole_xy(self.pending)
            q.setPen(QtGui.QPen(QtGui.QColor(t["focus"]), 2, QtCore.Qt.PenStyle.DashLine))
            q.drawLine(a, hole_xy(self.hover) if self.hover else self.mouse)
            q.setBrush(QtGui.QColor(t["focus"]))
            q.drawEllipse(a, 4, 4)
        if self.hover and self.tool != "select":
            q.setPen(QtGui.QPen(QtGui.QColor(t["focus"]), 1.5))
            q.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            q.drawEllipse(hole_xy(self.hover), 6, 6)
        q.end()

    def _draw_part(self, q: QtGui.QPainter, part: bb.Part, selected: bool, t: dict) -> None:
        if part.kind == "probe":
            c = QtGui.QColor(self.page.probe_color(part))
            p = hole_xy(part.a)
            if selected:
                q.setPen(QtGui.QPen(QtGui.QColor(t["focus"]), 2))
                q.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                q.drawEllipse(p, 11, 11)
            q.setPen(QtGui.QPen(c, 2.2))
            q.setBrush(QtGui.QColor(t["panel"]))
            q.drawEllipse(p, 7, 7)
            q.setBrush(c)
            q.drawEllipse(p, 2.5, 2.5)
            q.setPen(c)
            q.drawText(QtCore.QPointF(p.x() + 9, p.y() - 8), part.input or "—")
            return
        a, b = hole_xy(part.a), hole_xy(part.b)
        color = QtGui.QColor(_PART_COLOR[part.kind])
        if selected:
            glow = QtGui.QPen(QtGui.QColor(t["focus"]), 8)
            glow.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            q.setPen(glow)
            q.setOpacity(0.25)
            q.drawLine(a, b)
            q.setOpacity(1.0)
        q.setBrush(color)
        q.setPen(QtCore.Qt.PenStyle.NoPen)
        q.drawEllipse(a, 3, 3)
        q.drawEllipse(b, 3, 3)
        length = math.hypot(b.x() - a.x(), b.y() - a.y())
        body = min(34.0, length * 0.7) if part.kind != "wire" else 0.0
        q.save()
        q.translate(a)
        q.rotate(math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x())))
        pen = QtGui.QPen(color, 2.2 if part.kind != "wire" else 2.6)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        q.setPen(pen)
        q.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        s0, s1 = (length - body) / 2, (length + body) / 2
        if part.kind == "wire":
            q.drawLine(QtCore.QPointF(0, 0), QtCore.QPointF(length, 0))
        else:
            q.drawLine(QtCore.QPointF(0, 0), QtCore.QPointF(s0, 0))
            q.drawLine(QtCore.QPointF(s1, 0), QtCore.QPointF(length, 0))
        if part.kind == "resistor":
            path = QtGui.QPainterPath(QtCore.QPointF(s0, 0))
            for k in range(6):
                path.lineTo(s0 + body * (k + 0.5) / 6, -5 if k % 2 == 0 else 5)
            path.lineTo(s1, 0)
            q.drawPath(path)
        elif part.kind == "capacitor":
            mid = (s0 + s1) / 2
            q.drawLine(QtCore.QPointF(s0, 0), QtCore.QPointF(mid - 3, 0))
            q.drawLine(QtCore.QPointF(mid + 3, 0), QtCore.QPointF(s1, 0))
            q.setPen(QtGui.QPen(color, 3))
            q.drawLine(QtCore.QPointF(mid - 3, -8), QtCore.QPointF(mid - 3, 8))
            q.drawLine(QtCore.QPointF(mid + 3, -8), QtCore.QPointF(mid + 3, 8))
        elif part.kind == "vin":
            mid, r = (s0 + s1) / 2, min(10.0, body / 2)
            q.setBrush(QtGui.QColor(t["panel"]))
            q.drawLine(QtCore.QPointF(s0, 0), QtCore.QPointF(mid - r, 0))
            q.drawLine(QtCore.QPointF(mid + r, 0), QtCore.QPointF(s1, 0))
            q.drawEllipse(QtCore.QPointF(mid, 0), r, r)
            if part.source == "stim":                    # a square wave: the stimulator
                w = r * 0.55
                q.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(mid - w, 3), QtCore.QPointF(mid - w / 3, 3),
                                                QtCore.QPointF(mid - w / 3, -3), QtCore.QPointF(mid + w / 3, -3),
                                                QtCore.QPointF(mid + w / 3, 3), QtCore.QPointF(mid + w, 3)]))
            else:                                        # a battery: a fixed voltage
                q.drawLine(QtCore.QPointF(mid - 2, -5), QtCore.QPointF(mid - 2, 5))
                q.drawLine(QtCore.QPointF(mid + 2, -3), QtCore.QPointF(mid + 2, 3))
            q.setPen(QtGui.QColor(t["muted"]))
            q.drawText(QtCore.QPointF(s0 - 10, -6), "+")
        q.restore()


class BreadboardPage(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self._loading = False
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(16)

        # ---- left: tools, the board, the preview ----------------------------------
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        intro = QtWidgets.QLabel(
            "Build a circuit on a breadboard and record from it. Drag a part onto the board, or pick it and click "
            "two holes (one for a probe); drag a placed part, or one of its ends, to move it. In each column, holes a–e are joined, and so are f–j; the − rails are ground. "
            "Give a probe an input and Acquisition records that point of the circuit. An input voltage from "
            "the stimulator plays whatever the Stimulus tab sends.")
        intro.setWordWrap(True)
        left.addWidget(intro)
        tools = QtWidgets.QHBoxLayout()
        tools.setSpacing(6)
        self.tool_group = QtWidgets.QButtonGroup(self)
        self.tool_buttons: dict[str, QtWidgets.QPushButton] = {}
        for key, label, tip in _TOOLS:
            b = QtWidgets.QPushButton(label) if key == "select" else PartButton(key, label)
            b.setCheckable(True)
            b.setToolTip(tip)
            self.tool_group.addButton(b)
            self.tool_buttons[key] = b
            b.clicked.connect(lambda _=False, k=key: self.view.set_tool(k))
            tools.addWidget(b)
        tools.addStretch(1)
        self.btn_delete = QtWidgets.QPushButton("Delete part")
        self.btn_delete.setToolTip("Remove the selected part (Delete key)")
        self.btn_reset = QtWidgets.QPushButton("Reset board")
        self.btn_reset.setToolTip("Back to the example: the stimulator through 10 kΩ into 1 µF")
        self.btn_clear = QtWidgets.QPushButton("Clear board")
        for b in (self.btn_delete, self.btn_reset, self.btn_clear):
            tools.addWidget(b)
        left.addLayout(tools)
        self.view = BoardView(self)
        left.addWidget(self.view, 1)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        self.preview = pg.PlotWidget()
        self.preview.setMinimumHeight(170)
        self.preview.setMouseEnabled(False, False)
        self.preview.hideButtons()
        pi = self.preview.getPlotItem()
        pi.setLabel("bottom", "time", units="s")
        pi.setLabel("left", "V")
        self.legend = pi.addLegend(offset=(-10, 10))
        left.addWidget(self.preview)
        left_box = QtWidgets.QWidget()
        left_box.setLayout(left)
        outer.addWidget(scroll_column(left_box), 3)

        # ---- right: the selected part ---------------------------------------------
        right = QtWidgets.QVBoxLayout()
        self.part_box = QtWidgets.QGroupBox("Selected part")
        form = QtWidgets.QFormLayout(self.part_box)
        self.part_name = QtWidgets.QLabel("Click a part to select it")
        self.part_name.setWordWrap(True)
        form.addRow(self.part_name)
        self.source = QtWidgets.QComboBox()
        self.source.addItem("Stimulator output", "stim")
        self.source.addItem("Fixed voltage", "dc")
        self.source.setToolTip("Stimulator output: whatever the Stimulus tab sends, in V")
        self.volts = UnitSpinBox(-10, 10, "V", step=0.1)
        self.ohms = UnitSpinBox(1, 1e9, "Ω", families=[("Ω", 1.0), ("kΩ", 1e3), ("MΩ", 1e6)], step=1)
        self.farads = UnitSpinBox(1e-12, 1, "F", families=[("F", 1.0), ("mF", 1e-3), ("µF", 1e-6), ("uF", 1e-6),
                                                           ("nF", 1e-9), ("pF", 1e-12)], decimals=15, step=1)
        self.probe_input = QtWidgets.QComboBox()
        self.probe_input.setToolTip("The input this probe records on: Acquisition shows this point of the circuit")
        self.noise = UnitSpinBox(0, 1, "V", decimals=6, step=0.001)
        self.noise.setToolTip("Recording noise added to the probe's trace (RMS)")
        self.rows = {}
        for key, label, w in (("source", "Source", self.source), ("volts", "Voltage", self.volts),
                              ("ohms", "Resistance", self.ohms), ("farads", "Capacitance", self.farads),
                              ("input", "Records on", self.probe_input), ("noise", "Noise", self.noise)):
            form.addRow(label, w)
            self.rows[key] = (form.labelForField(w), w)
        self.part_info = QtWidgets.QLabel()
        self.part_info.setWordWrap(True)
        form.addRow(self.part_info)
        right.addWidget(self.part_box)
        self.probe_box = QtWidgets.QGroupBox("Recording sites")
        pv = QtWidgets.QVBoxLayout(self.probe_box)
        self.probe_list = QtWidgets.QLabel()
        self.probe_list.setWordWrap(True)
        pv.addWidget(self.probe_list)
        right.addWidget(self.probe_box)
        self.timing_box = QtWidgets.QGroupBox("Timing")
        tv = QtWidgets.QVBoxLayout(self.timing_box)
        self.timing = QtWidgets.QLabel()
        self.timing.setWordWrap(True)
        self.timing.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.timing.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        tv.addWidget(self.timing)
        right.addWidget(self.timing_box)
        right.addStretch(1)
        right_box = QtWidgets.QWidget()
        right_box.setLayout(right)
        right_box.setMinimumWidth(250)
        right_box.setMaximumWidth(320)
        outer.addWidget(right_box, 1)

        self.view.edited.connect(self._apply)
        self.view.selected.connect(lambda _i: self._show_part())
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_reset.clicked.connect(lambda: self._apply(bb.default_board(), keep_sel=False))
        self.btn_clear.clicked.connect(lambda: self._apply(bb.Board(), keep_sel=False))
        self.source.currentIndexChanged.connect(self._part_edited)
        for w in (self.volts, self.ohms, self.farads, self.noise):
            w.valueChanged.connect(self._part_edited)
        self.probe_input.currentIndexChanged.connect(self._part_edited)
        main.window_box.valueChanged.connect(lambda _v: self._show_timing())
        self.tool_buttons["select"].setChecked(True)
        self.load()
        self.apply_theme(style.LIGHT)

    # -- config -> widgets ------------------------------------------------------------
    @property
    def board(self) -> bb.Board:
        return self.main.cfg.sim_breadboard

    def sync(self) -> None:
        """After a channel or stimulus-wire change: the inputs a probe can record on may differ."""
        self.load()

    def load(self) -> None:
        self.view.board = self.board
        if self.view.sel >= len(self.board.parts):
            self.view.sel = -1
        self._show_part()
        self._show_probes()
        self._redraw_preview()
        self._show_timing()
        self.view.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._show_timing()                  # the Stimulator or Window may have changed meanwhile

    def _inputs(self) -> list:
        cfg = self.main.cfg
        return [c for c in cfg.channels if c.physical != cfg.stim_monitor]

    def probe_color(self, part: bb.Part) -> str:
        ch = next((c for c in self.main.cfg.channels if c.physical == part.input), None)
        return ch.color if ch is not None and ch.color else _PROBE_FREE

    def _show_part(self) -> None:
        self._loading = True
        i = self.view.sel
        part = self.board.parts[i] if 0 <= i < len(self.board.parts) else None
        shown = set()
        if part is None:
            self.part_name.setText("Click a part to select it, or pick a part above to place one.")
            self.part_info.setText("")
        else:
            ends = part.a if part.kind == "probe" else f"{part.a} to {part.b}"
            self.part_name.setText(f"<b>{bb.NAMES[part.kind]}</b>  ({ends})")
            info = ""
            if part.kind == "vin":
                shown |= {"source"} | ({"volts"} if part.source == "dc" else set())
                self.source.setCurrentIndex(max(0, self.source.findData(part.source)))
                self.volts.setValue(part.value)
                info = "+ end at " + part.a
            elif part.kind == "resistor":
                shown.add("ohms")
                self.ohms.setValue(part.value)
            elif part.kind == "capacitor":
                shown.add("farads")
                self.farads.setValue(part.value)
            elif part.kind == "probe":
                shown |= {"input", "noise"}
                self.probe_input.clear()
                self.probe_input.addItem("None (not recorded)", "")
                for c in self._inputs():
                    self.probe_input.addItem(f"{c.physical} ({c.name})", c.physical)
                if part.input and self.probe_input.findData(part.input) < 0:
                    self.probe_input.addItem(f"{part.input} (not an input now)", part.input)
                self.probe_input.setCurrentIndex(max(0, self.probe_input.findData(part.input)))
                self.noise.setValue(part.noise_v)
            elif part.kind == "wire":
                info = "Joins the two strips it connects."
            self.part_info.setText(info)
        for key, (label, w) in self.rows.items():
            label.setVisible(key in shown)
            w.setVisible(key in shown)
        self.btn_delete.setEnabled(part is not None)
        self._loading = False

    def _show_probes(self) -> None:
        probes = self.board.probes()
        if not probes:
            self.probe_list.setText("No probes yet: pick Probe and click a hole.")
            return
        lines = []
        for p in probes:
            c = self.probe_color(p)
            where = f"records on <b>{p.input}</b>" if p.input else "not recorded (pick an input)"
            lines.append(f"<span style='color:{c}'>●</span> {p.a}: {where}")
        self.probe_list.setText("<br>".join(lines))

    # -- widgets -> config ------------------------------------------------------------
    def _part_edited(self, *_) -> None:
        if self._loading or not 0 <= self.view.sel < len(self.board.parts):
            return
        board = bb.copy_board(self.board)
        part = board.parts[self.view.sel]
        if part.kind == "vin":
            part.source = self.source.currentData()
            part.value = self.volts.value()
        elif part.kind == "resistor":
            part.value = self.ohms.value()
        elif part.kind == "capacitor":
            part.value = self.farads.value()
        elif part.kind == "probe":
            part.input = self.probe_input.currentData() or ""
            part.noise_v = self.noise.value()
        self._apply(board)

    def delete_selected(self) -> None:
        i = self.view.sel
        if not 0 <= i < len(self.board.parts):
            return
        board = bb.copy_board(self.board)
        del board.parts[i]
        self.view.sel = -1
        self._apply(board)

    def _apply(self, board: bb.Board, keep_sel: bool = True) -> None:
        """Check a changed board and, if it can be solved, make it the one recorded."""
        if not keep_sel:
            self.view.sel = -1
        try:
            bb.validate(board)
            bb.BoardState(board, 1e-4)                  # can the circuit be solved?
        except ValueError as e:
            self._show_message(str(e), error=True)
            self.view.board = self.board                  # a refused move or drop: back as it was
            self.view.update()
            return
        # A new object, so the acquisition thread picks up the change all at once.
        self.main.cfg.sim_breadboard = board
        self._show_message("")
        self.load()

    def _show_timing(self) -> None:
        """The circuit's time constant, what to set to see it charge, and warnings when the
        Stimulator's pulse or Acquisition's Window would hide it."""
        def t(sec):
            x, u = best_prefix(sec, "s")
            return f"{x:.3g} {u}"
        try:
            taus = bb.time_constants(self.board)
        except ValueError:
            self.timing.setText("")
            return
        if not taus:
            self.timing.setText("No capacitor charges through a resistor here, so the probes follow the input at once.")
            return
        tau = taus[0]
        rs = [p for p in self.board.parts if p.kind == "resistor"]
        cs = [p for p in self.board.parts if p.kind == "capacitor"]
        parts = ""
        if len(rs) == 1 and len(cs) == 1:
            r, ru = best_prefix(rs[0].value, "Ω", ("", "k", "M"))
            c, cu = best_prefix(cs[0].value, "F", ("p", "n", "µ", "m", ""))
            parts = f" ({r:.3g} {ru} × {c:.3g} {cu})"
        lines = [f"Time constant <b>τ = {t(tau)}</b>{parts}"
                 + (" (the slowest of several)" if len(taus) > 1 else ""),
                 f"The capacitor charges 63% in τ and almost fully in 5τ = {t(5 * tau)}. To see it, send a pulse "
                 f"or step of at least {t(5 * tau)} and set Acquisition's Window to about {t(10 * tau)}–{t(50 * tau)}."]
        warn = _WARN.get(self._mode, _WARN[style.LIGHT])
        warnings = []
        s = getattr(self.main, "stimulus", None)
        if s is not None and any(p.kind == "vin" and p.source == "stim" for p in self.board.parts):
            length = {"pulse": s.width_ms, "step": s.duration_ms}.get(s.shape)
            if length is not None and length / 1000 < 5 * tau:
                reach = 1 - math.exp(-length / 1000 / tau)
                what = "pulse" if s.shape == "pulse" else "step"
                warnings.append(f"The Stimulator's {what} is {t(length / 1000)}, shorter than 5τ: the capacitor "
                                f"only reaches about {reach:.0%} of the input.")
        window = self.main.window_box.value()
        if window > 200 * tau:
            warnings.append(f"Acquisition's Window is {t(window)}, so a {t(5 * tau)} charge will look like a thin spike.")
        lines += [f'<span style="color:{warn}">⚠ {w}</span>' for w in warnings]
        self.timing.setText("<br><br>".join(lines))

    def _show_message(self, text: str, error: bool = False) -> None:
        t = style.tokens(self._mode)
        self.message.setStyleSheet(f"color: {t['error'] if error else t['muted']}")
        self.message.setText(text)

    def _redraw_preview(self) -> None:
        pi = self.preview.getPlotItem()
        pi.clear()
        self.legend.clear()
        th = theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT]
        try:
            t, stim, traces = bb.preview(self.board)
        except ValueError:
            return
        if any(p.kind == "vin" and p.source == "stim" for p in self.board.parts):
            pi.plot(t, stim, pen=pg.mkPen(th.foreground, width=1, style=QtCore.Qt.PenStyle.DashLine),
                    name="stimulator: a 1 V, 100 ms pulse")
        for p, y in zip(self.board.probes(), traces):
            pi.plot(t, y, pen=pg.mkPen(self.probe_color(p), width=2), name=f"probe {p.a}" + (f" → {p.input}" if p.input else ""))
        pi.setTitle("Preview: what the probes would record", color=th.foreground)

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        self.preview.setBackground(th.background)
        for ax in ("left", "bottom"):
            a = self.preview.getPlotItem().getAxis(ax)
            a.setPen(th.foreground)
            a.setTextPen(th.foreground)
        self._redraw_preview()
        self._show_timing()
        self.view.update()
