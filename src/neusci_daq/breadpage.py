"""The Breadboard simulator page, under Data Simulation (recorded with the
simulated backend; editable and previewed with any): parts placed on a breadboard, and probes on it that Acquisition records
like any input. See breadboard.py for the board and how the circuit is solved.

Drag a part from the strip onto the board, or pick it and click two holes (one for
a probe or a ground). Less-used parts (LED, diode, push button, potentiometer,
inductor, battery) are under More parts. Click a part to select it and set its value; drag it, or one of its ends,
to move it; double-click a hand switch to flip it. Delete removes it, Esc cancels a placement. Changes
apply straight away, while recording too, and Setup › Save channel setup writes the
board to the config's [simulated] section.
"""
from __future__ import annotations

import math
import tomllib
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import breadboard as bb
from . import lessoncircuit
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
          ("switch", "Switch", "Flipped by hand, or by a gate voltage like a MOSFET (set it in Selected part): "
                               "drag it onto the board, or click its two ends"),
          ("wire", "Wire", "Joins two holes: drag it onto the board, or click its two ends"),
          ("probe", "Probe", "A recording site: drag it onto a hole, or click one; then pick the input it records on"),
          ("ground", "⏚ Ground", "Ties a hole to 0 V, like a wire to a − rail: drag it onto a hole, or click one"))
_MORE = (("led", "LED", "lights when current flows forward"),
         ("diode", "Diode", "current flows one way only"),
         None,
         ("button", "Push button", "closed only while you hold it"),
         ("pot", "Potentiometer", "three legs: an adjustable divider"),
         ("inductor", "Inductor", "henries"),
         None,
         ("battery", "Battery", "a fixed voltage, + and − ends"))
_PART_COLOR = {"vin": "#7c4dff", "resistor": "#b87a1e", "capacitor": "#1e88a8", "switch": "#c2185b", "wire": "#2e7d32",
               "ground": "#5f6670", "diode": "#455a64", "button": "#c2185b", "pot": "#b87a1e", "inductor": "#6d4c41",
               "battery": "#7c4dff"}   # all read on light and dark boards
_LED_RGB = {"red": "#e53935", "yellow": "#fdd835", "green": "#43a047", "blue": "#1e88e5", "white": "#e0e0e0"}
_LEGS = ("a", "b", "gate", "wiper")           # every leg a part can have
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


def placed_part(kind: str, a: str, b: str = "") -> bb.Part | None:
    """A new part from `a` to `b` with its defaults; a potentiometer gets its wiper beside
    the middle. None if there's no room for the wiper."""
    part = bb.new_part(kind, a, b)
    if kind == "pot":
        part.wiper = gate_hole(a, b) or ""
        if not part.wiper:
            return None
    return part


def dropped_part(kind: str, hole: str) -> bb.Part | None:
    """A part dropped on `hole`: a probe or ground there, anything else from there across _SPAN columns."""
    if kind in bb.ONE_HOLE:
        return bb.new_part(kind, hole)
    end = shifted(hole, 0, _SPAN) or shifted(hole, 0, -_SPAN)
    return placed_part(kind, hole, end)


def gate_hole(a: str, b: str) -> str | None:
    """A free hole beside the middle of a part from `a` to `b`, for a MOSFET's gate leg."""
    pa, pb = hole_xy(a), hole_xy(b)
    mid = (pa + pb) / 2
    dx, dy = pb.x() - pa.x(), pb.y() - pa.y()
    n = math.hypot(dx, dy) or 1.0
    for d in (PITCH, -PITCH, 2 * PITCH, -2 * PITCH):
        p = mid + QtCore.QPointF(-dy / n * d, dx / n * d)
        col = min(max(round((p.x() - _X0) / PITCH) + 1, 1), bb.COLS)    # snap to a column, then a row
        row = min(_ROW_Y, key=lambda r: abs(_ROW_Y[r] - p.y()))
        h = f"{row}{col}"
        if abs(_ROW_Y[row] - p.y()) < PITCH and h not in (a, b):
            return h
    return None


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
        self.live: dict[int, bool] = {}       # MOSFET switches closed, by part index: while recording, else at rest
        self.lit: dict[int, float] = {}       # LED currents (A), by part index: while recording, else at rest
        self.held = -1                        # a push button held down
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
        self.page._show_key()
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
            legs = {k: getattr(part, k) for k in _LEGS if getattr(part, k) in part.holes()}
            ends = legs if d["end"] is None else {d["end"]: legs[d["end"]]}
            moved = {k: shifted(h, dr, dc) for k, h in ends.items() if h}
            if all(moved.values()):
                board = bb.copy_board(d["board"])
                for k, h in moved.items():
                    setattr(board.parts[d["i"]], k, h)
                legs = board.parts[d["i"]].holes()
                if len(set(legs)) == len(legs):
                    self.board = board                   # shown while dragging; applied on release
                    d["moved"] = (dr, dc) != (0, 0)
        self.update()

    def mouseReleaseEvent(self, e):
        if self.held >= 0:                                # a push button springs open
            board = bb.copy_board(self.board)
            if self.held < len(board.parts):
                board.parts[self.held].closed = False
            self.held = -1
            self.edited.emit(board)
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
        part = dropped_part(kind, hole)
        if part is None:
            self.page._show_message("No free hole beside it for the wiper: drop it somewhere else.", error=True)
        else:
            self._add(part)

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
            if i >= 0 and self.board.parts[i].kind == "button" and self._on_body(i, p):   # press it: closed while held
                board = bb.copy_board(self.board)
                board.parts[i].closed = True
                self.held = i
                self.edited.emit(board)
                return
            if i >= 0:                                  # grab it: an end moves that end, the body the whole part
                part = self.board.parts[i]
                end = next((k for k in _LEGS if getattr(part, k) in part.holes() and part.kind not in bb.ONE_HOLE and
                            math.hypot(p.x() - hole_xy(getattr(part, k)).x(), p.y() - hole_xy(getattr(part, k)).y()) < 6),
                           None)
                start = getattr(part, end) if end else (nearest_hole(p) or part.a)
                self.drag = {"i": i, "end": end, "from": start, "board": self.board, "moved": False}
                self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            return
        hole = nearest_hole(p)
        if hole is None:
            return
        if self.tool in bb.ONE_HOLE:
            self._add(bb.new_part(self.tool, hole))
        elif self.pending is None:
            self.pending = hole
        elif hole != self.pending:
            part = placed_part(self.tool, self.pending, hole)
            if part is None:
                self.page._show_message("No free hole beside it for the wiper: pick other ends.", error=True)
            else:
                self._add(part)
            self.pending = None
        self.update()

    def mouseDoubleClickEvent(self, e):
        if self.tool != "select" or e.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        i = self._part_at(self._to_board(e.position()))
        if i >= 0 and self.board.parts[i].kind == "switch" and self.board.parts[i].mode == "toggle":
            board = bb.copy_board(self.board)
            board.parts[i].closed = not board.parts[i].closed
            self.drag = None
            self.edited.emit(board)

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

    def _dist(self, part: bb.Part, p: QtCore.QPointF) -> float:
        a = hole_xy(part.a)
        if part.kind in bb.ONE_HOLE:
            return math.hypot(p.x() - a.x(), p.y() - a.y())
        d = _seg_dist(p, a, hole_xy(part.b))
        for leg in (part.gate, part.wiper):
            if leg and leg in part.holes():
                d = min(d, _seg_dist(p, (a + hole_xy(part.b)) / 2, hole_xy(leg)))
        return d

    def _part_at(self, p: QtCore.QPointF) -> int:
        best, idx = 9.0, -1
        for i, part in enumerate(self.board.parts):
            d = self._dist(part, p)
            if d < best:
                best, idx = d, i
        return idx

    def _on_body(self, i: int, p: QtCore.QPointF) -> bool:
        """Is `p` on the part's body, away from its legs (so a press there is a press, not a grab)?"""
        part = self.board.parts[i]
        return all(math.hypot(p.x() - hole_xy(h).x(), p.y() - hole_xy(h).y()) >= 6 for h in part.holes())

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
            on = part.closed if part.kind == "button" or part.mode == "toggle" else self.live.get(i, False)
            self._draw_part(q, part, i == self.sel, t, on, self.lit.get(i, 0.0))
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

    def _draw_part(self, q: QtGui.QPainter, part: bb.Part, selected: bool, t: dict, on: bool = False,
                   amps: float = 0.0) -> None:
        if part.kind == "ground":                          # the ground symbol hanging below its hole
            c = QtGui.QColor(_PART_COLOR["ground"])
            p = hole_xy(part.a)
            if selected:
                q.setPen(QtGui.QPen(QtGui.QColor(t["focus"]), 2))
                q.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                q.drawEllipse(p + QtCore.QPointF(0, 5), 11, 11)
            q.setPen(QtGui.QPen(c, 2))
            q.setBrush(c)
            q.drawEllipse(p, 2.5, 2.5)
            q.drawLine(p, p + QtCore.QPointF(0, 7))
            for k, w in enumerate((8, 5, 2)):
                y = p.y() + 7 + 3 * k
                q.drawLine(QtCore.QPointF(p.x() - w, y), QtCore.QPointF(p.x() + w, y))
            return
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
        color = QtGui.QColor(_LED_RGB.get(part.color, "#e53935") if part.kind == "led" else _PART_COLOR[part.kind])
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
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        gate = None
        leg = part.gate if part.gate in part.holes() else part.wiper if part.wiper in part.holes() else ""
        if leg:                                          # the gate or wiper leg, in the part's own frame
            g = hole_xy(leg)
            q.drawEllipse(g, 3, 3)
            gx, gy = g.x() - a.x(), g.y() - a.y()
            gate = (math.cos(ang) * gx + math.sin(ang) * gy, -math.sin(ang) * gx + math.cos(ang) * gy)
        q.save()
        q.translate(a)
        q.rotate(math.degrees(ang))
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
        elif part.kind == "pot":                         # a resistor with an arrow from the wiper
            path = QtGui.QPainterPath(QtCore.QPointF(s0, 0))
            for k in range(6):
                path.lineTo(s0 + body * (k + 0.5) / 6, -5 if k % 2 == 0 else 5)
            path.lineTo(s1, 0)
            q.drawPath(path)
            tip = QtCore.QPointF(s0 + body * part.pos, 0)
            side = 1 if gate[1] >= 0 else -1
            q.drawLine(QtCore.QPointF(*gate), QtCore.QPointF(tip.x(), side * 7))
            q.setBrush(color)
            q.drawPolygon(QtGui.QPolygonF([tip + QtCore.QPointF(0, side * 5), tip + QtCore.QPointF(-3, side * 10),
                                           tip + QtCore.QPointF(3, side * 10)]))
        elif part.kind == "inductor":                    # four loops
            path = QtGui.QPainterPath(QtCore.QPointF(s0, 0))
            w = body / 4
            for k in range(4):
                path.arcTo(QtCore.QRectF(s0 + k * w, -w / 2 - 1, w, w + 2), 180, -180)
            q.drawPath(path)
        elif part.kind in ("diode", "led"):              # a triangle pointing to the bar: anode at a
            mid, h = (s0 + s1) / 2, 6.0
            if part.kind == "led" and amps > 0:          # glow, brighter with more current
                glow = QtGui.QRadialGradient(QtCore.QPointF(mid, 0), 18)
                c0 = QtGui.QColor(color)
                c0.setAlphaF(min(0.85, 0.25 + 0.6 * amps / max(part.value, 1e-9)))
                glow.setColorAt(0, c0)
                glow.setColorAt(1, QtGui.QColor(0, 0, 0, 0))
                q.setPen(QtCore.Qt.PenStyle.NoPen)
                q.setBrush(glow)
                q.drawEllipse(QtCore.QPointF(mid, 0), 18, 18)
                q.setPen(pen)
            q.drawLine(QtCore.QPointF(s0, 0), QtCore.QPointF(mid - h, 0))
            q.drawLine(QtCore.QPointF(mid + h, 0), QtCore.QPointF(s1, 0))
            q.setBrush(color if part.kind == "diode" or amps > 0 else QtGui.QColor(t["panel"]))
            q.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(mid - h, -h), QtCore.QPointF(mid - h, h), QtCore.QPointF(mid + h, 0)]))
            q.setPen(QtGui.QPen(color, 2.6))
            q.drawLine(QtCore.QPointF(mid + h, -h), QtCore.QPointF(mid + h, h))
            if part.kind == "led":                       # two little arrows: light out
                q.setPen(QtGui.QPen(color, 1.2))
                for dx in (-2, 3):
                    q.drawLine(QtCore.QPointF(mid + dx, -h - 1), QtCore.QPointF(mid + dx + 4, -h - 6))
                if amps > part.value:                    # over its maximum current
                    q.setPen(QtGui.QColor(_WARN.get(self.page._mode, _WARN[style.LIGHT])))
                    q.drawText(QtCore.QPointF(mid - 3, h + 12), "!")
        elif part.kind == "button":                      # a push button: a bar lifted off two contacts, down while held
            q.setBrush(color)
            q.drawEllipse(QtCore.QPointF(s0, 0), 2.5, 2.5)
            q.drawEllipse(QtCore.QPointF(s1, 0), 2.5, 2.5)
            lift = 0 if on else -6
            if on:
                self._glow(q, QtCore.QPointF(s0, 0), QtCore.QPointF(s1, 0))
            q.drawLine(QtCore.QPointF(s0, lift), QtCore.QPointF(s1, lift))
            mid = (s0 + s1) / 2
            q.drawLine(QtCore.QPointF(mid, lift), QtCore.QPointF(mid, lift - 6))
            q.drawLine(QtCore.QPointF(mid - 4, lift - 6), QtCore.QPointF(mid + 4, lift - 6))
        elif part.kind == "switch" and gate is None:     # a hand switch: a lever, down when closed
            q.setBrush(color)
            q.drawEllipse(QtCore.QPointF(s0, 0), 2.5, 2.5)
            q.drawEllipse(QtCore.QPointF(s1, 0), 2.5, 2.5)
            if on:
                self._glow(q, QtCore.QPointF(s0, 0), QtCore.QPointF(s1, 0))
            q.drawLine(QtCore.QPointF(s0, 0), QtCore.QPointF(s1, 0) if on else QtCore.QPointF(s1 - 2, -body * 0.4))
        elif part.kind == "switch":                      # a MOSFET: drain at a, source at b, gate to one side
            mid, side = (s0 + s1) / 2, (1 if gate[1] >= 0 else -1)
            ch, pl = side * 6, side * 11
            q.drawLine(QtCore.QPointF(s0, 0), QtCore.QPointF(s0, ch))
            q.drawLine(QtCore.QPointF(s1, 0), QtCore.QPointF(s1, ch))
            q.drawLine(QtCore.QPointF(mid, 0), QtCore.QPointF(mid, ch))          # body, tied to the source
            q.drawLine(QtCore.QPointF(mid, 0), QtCore.QPointF(s1, 0))
            if on:
                self._glow(q, QtCore.QPointF(s0, ch), QtCore.QPointF(s1, ch))
            q.setPen(QtGui.QPen(color, 3))
            if on:
                q.drawLine(QtCore.QPointF(s0, ch), QtCore.QPointF(s1, ch))
            else:                                        # broken channel: off
                w = (s1 - s0) / 3
                for k in range(3):
                    q.drawLine(QtCore.QPointF(s0 + k * w + 1.5, ch), QtCore.QPointF(s0 + (k + 1) * w - 1.5, ch))
            q.setPen(pen)
            q.drawLine(QtCore.QPointF(s0 + 2, pl), QtCore.QPointF(s1 - 2, pl))
            q.drawLine(QtCore.QPointF(mid, pl), QtCore.QPointF(*gate))
            tip, base = (side * 5, side * 1) if part.channel == "n" else (side * 1, side * 5)   # n points into the channel
            q.setBrush(color)
            q.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(mid, tip), QtCore.QPointF(mid - 3, base),
                                           QtCore.QPointF(mid + 3, base)]))
        elif part.kind == "battery":                     # long plate (+) at a, short plate (−) toward b
            mid = (s0 + s1) / 2
            q.drawLine(QtCore.QPointF(s0, 0), QtCore.QPointF(mid - 2, 0))
            q.drawLine(QtCore.QPointF(mid + 3, 0), QtCore.QPointF(s1, 0))
            q.drawLine(QtCore.QPointF(mid - 2, -8), QtCore.QPointF(mid - 2, 8))
            q.setPen(QtGui.QPen(color, 3.5))
            q.drawLine(QtCore.QPointF(mid + 3, -4), QtCore.QPointF(mid + 3, 4))
            q.setPen(QtGui.QColor(t["muted"]))
            q.drawText(QtCore.QPointF(s0 - 10, -6), "+")
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


    @staticmethod
    def _glow(q: QtGui.QPainter, a: QtCore.QPointF, b: QtCore.QPointF) -> None:
        """A green halo along a closed switch's path."""
        glow = QtGui.QPen(QtGui.QColor("#2e7d32"), 7)
        glow.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        q.save()
        q.setPen(glow)
        q.setOpacity(0.3)
        q.drawLine(a, b)
        q.restore()


# ---------------------------------------------------------------- the key: what each leg of a part is
# (leg, tag on the drawing, name, where it goes and what it does)
_ENDS = [("a", "1", "end", "the first hole you click"), ("b", "2", "end", "the second hole; either way round works")]


def key_legs(part: bb.Part) -> list[tuple[str, str, str, str]]:
    k = part.kind
    if k in ("vin", "battery"):
        what = "the stimulator's output" if k == "vin" and part.source == "stim" else "the fixed voltage"
        return [("a", "+", "+ end", f"first hole: held at {what} above the − end"),
                ("b", "−", "− end", "second hole: usually ground (a − rail)")]
    if k in ("led", "diode"):
        return [("a", "+", "anode", "first hole: toward the higher voltage"),
                ("b", "−", "cathode", "second hole: toward ground")]
    if k == "switch" and part.mode == "mosfet":
        return [("a", "D", "drain", "first hole"),
                ("b", "S", "source", "second hole: the gate is measured against it"),
                ("gate", "G", "gate", "placed beside the middle; draws no current")]
    if k == "pot":
        return [("a", "1", "end", "first hole"), ("b", "2", "end", "second hole"),
                ("wiper", "W", "wiper", "placed beside the middle: taps the resistance part-way along")]
    if k == "probe":
        return [("a", "P", "probe", "one hole: records the voltage of its strip")]
    if k == "ground":
        return [("a", "0", "ground", "one hole: ties its strip to 0 V")]
    return _ENDS


def key_text(part: bb.Part) -> str:
    k = part.kind
    if k == "vin":
        return ("Drives its + end to the stimulator's output, so whatever the Stimulus tab sends plays into the circuit."
                if part.source == "stim" else "Holds its + end a fixed voltage above its − end.")
    if k == "switch":
        if part.mode == "mosfet":
            side = "above" if part.channel == "n" else "below"
            return f"Joins drain to source while the gate is more than V<sub>th</sub> {side} the source; otherwise connects nothing."
        return "Joins its two ends while closed; double-click it, or use Close it, to flip it."
    return {
        "resistor": "Lets current through in proportion to the voltage across it (Ohm's law). Either way round.",
        "capacitor": "Stores charge: current flows in while the voltage across it changes, then stops. Either way round.",
        "wire": "Joins two strips into one, so they're at the same voltage.",
        "probe": "Records its strip's voltage on the input you pick, for Acquisition.",
        "ground": "The 0 V reference, the same as a wire to a − rail.",
        "battery": "Holds its + end a fixed voltage above its − end.",
        "led": "Lights when + is more than its forward voltage above −, brighter with more current. "
               "Put a resistor in series to keep the current under its maximum.",
        "diode": "Lets current flow from + to − only, once + is more than its forward voltage above −.",
        "button": "Joins its two ends only while you hold the mouse down on it.",
        "pot": "A resistor with a third leg: the wiper splits it in two, so the wiper's voltage is a set fraction "
               "of the way between the ends (a divider).",
        "inductor": "Resists changes in current: the voltage across it is L times how fast the current changes. Either way round.",
    }.get(k, "")


class KeyDrawing(QtWidgets.QWidget):
    """A part drawn large, the way it looks on the board, with each leg tagged and named."""

    _DEMO = {"a": "c3", "b": "c8", "gate": "e6", "wiper": "e6"}   # the part laid out flat, legs well apart

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.part: bb.Part | None = None
        self.setMinimumHeight(118)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

    def set_part(self, part: bb.Part | None) -> None:
        if part is not None:
            part = bb.copy_board(bb.Board([part])).parts[0]
            part.a = self._DEMO["a"] if part.kind not in bb.ONE_HOLE else "c5"
            part.b = "" if part.kind in bb.ONE_HOLE else self._DEMO["b"]
            if part.gate:
                part.gate = self._DEMO["gate"]
            if part.wiper:
                part.wiper = self._DEMO["wiper"]
        self.part = part
        self.update()

    def paintEvent(self, e):
        if self.part is None:
            return
        t = style.tokens(self.page._mode)
        q = QtGui.QPainter(self)
        q.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        legs = [(leg, tag, name) for leg, tag, name, _ in key_legs(self.part) if getattr(self.part, leg)]
        pts = [hole_xy(getattr(self.part, leg)) for leg, _t, _n in legs]
        x0, x1 = min(p.x() for p in pts), max(p.x() for p in pts)
        y0, y1 = min(p.y() for p in pts), max(p.y() for p in pts)
        s = min(2.2, (self.width() - 60) / max(x1 - x0, 1.0)) if x1 > x0 else 2.2
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 + (6 if self.part.kind == "ground" else 0)
        top = 34 if len(legs) > 1 else 40                 # room above for the names
        to_px = lambda p: QtCore.QPointF(self.width() / 2 + (p.x() - cx) * s, top + 12 + (p.y() - y0) * s)
        q.save()
        o = to_px(QtCore.QPointF(cx, y0))
        q.translate(o.x() - cx * s, o.y() - y0 * s)
        q.scale(s, s)
        on = self.part.closed if self.part.kind in ("switch", "button") else False
        amps = self.part.value * 0.6 if self.part.kind == "led" else 0.0   # shown lit
        self.page.view._draw_part(q, self.part, False, t, on, amps)
        q.restore()
        f = q.font()
        for (leg, tag, name), p in zip(legs, pts):
            c = to_px(p)
            below = leg in ("a", "b")
            # the tag: a small dark disc, right under (or beside) the leg
            tc = c + (QtCore.QPointF(0, 20) if below else QtCore.QPointF(20, 4))
            if self.part.kind in bb.ONE_HOLE:            # tag to the left, name to the right, clear of the symbol
                tc = c + QtCore.QPointF(-28, 0)
            q.setPen(QtCore.Qt.PenStyle.NoPen)
            q.setBrush(QtGui.QColor(t["ink"]))
            q.drawEllipse(tc, 9, 9)
            f.setPointSizeF(9)
            f.setBold(True)
            q.setFont(f)
            q.setPen(QtGui.QColor(t["panel"]))
            q.drawText(QtCore.QRectF(tc.x() - 9, tc.y() - 9, 18, 18), QtCore.Qt.AlignmentFlag.AlignCenter, tag)
            # the name, above the end legs, beside the middle one
            f.setBold(False)
            q.setFont(f)
            q.setPen(QtGui.QColor(t["muted"]))
            if self.part.kind in bb.ONE_HOLE:
                q.drawText(QtCore.QPointF(c.x() + 26, c.y() + 4), name)
            elif below:
                q.drawText(QtCore.QRectF(c.x() - 40, c.y() - 30, 80, 16), QtCore.Qt.AlignmentFlag.AlignCenter, name)
            else:
                q.drawText(QtCore.QPointF(tc.x() + 14, tc.y() + 4), name)
        q.end()


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
            "two holes (one for a probe or a ground); drag a placed part, or one of its ends, to move it. LEDs and other parts are under More parts. In each column, holes a–e are joined, and so are f–j; the − rails are ground. "
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
        self.more = QtWidgets.QToolButton()
        self.more.setText("More parts")
        self.more.setCheckable(True)
        self.more.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setToolTip("Less-used parts: pick one, then click its holes on the board")
        menu = QtWidgets.QMenu(self.more)
        for item in _MORE:
            if item is None:
                menu.addSeparator()
                continue
            key, label, hint = item
            act = menu.addAction(f"{label}  —  {hint}")
            act.triggered.connect(lambda _=False, k=key, lab=label: self._pick_more(k, lab))
        self.more.setMenu(menu)
        self.tool_group.addButton(self.more)
        self.tool_group.buttonToggled.connect(lambda b, on: b is self.more and not on and self.more.setText("More parts"))
        tools.addWidget(self.more)
        tools.addStretch(1)
        self.btn_delete = QtWidgets.QPushButton("Delete part")
        self.btn_delete.setToolTip("Remove the selected part (Delete key)")
        self.btn_reset = QtWidgets.QPushButton("Reset board")
        self.btn_reset.setToolTip("Back to the example: the stimulator through 10 kΩ into 1 µF")
        self.btn_clear = QtWidgets.QPushButton("Clear board")
        self.btn_save = QtWidgets.QPushButton("Save board…")
        self.btn_save.setToolTip("Save this circuit to a file, to open again later or share")
        self.btn_save.clicked.connect(self.save_board)
        self.btn_open = QtWidgets.QPushButton("Open board…")
        self.btn_open.setToolTip("Replace the board with a circuit saved before")
        self.btn_open.clicked.connect(self.open_board)
        left.addLayout(tools)
        # The board's own actions get a row of their own, so the parts row stays narrow
        # enough for the side panel to fit beside it.
        actions = QtWidgets.QHBoxLayout()
        for b in (self.btn_save, self.btn_open):
            actions.addWidget(b)
        actions.addStretch(1)
        for b in (self.btn_delete, self.btn_reset, self.btn_clear):
            actions.addWidget(b)
        left.addLayout(actions)
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
        self.sw_mode = QtWidgets.QComboBox()
        self.sw_mode.addItem("Hand (toggle)", "toggle")
        self.sw_mode.addItem("Gate voltage (MOSFET)", "mosfet")
        self.sw_mode.setToolTip("Hand: you open and close it. MOSFET: the voltage on its gate leg does")
        self.flip = QtWidgets.QPushButton()
        self.flip.setToolTip("Flip the switch (or double-click it on the board), while recording too")
        self.channel = QtWidgets.QComboBox()
        self.channel.addItem("N-channel", "n")
        self.channel.addItem("P-channel", "p")
        self.channel.setToolTip("N: closes when the gate is above the source by the threshold. P: below it")
        self.vth = UnitSpinBox(0.01, 10, "V", step=0.1)
        self.vth.setToolTip("How far the gate must be from the source to close it")
        self.ron = UnitSpinBox(1e-3, 1e6, "Ω", families=[("mΩ", 1e-3), ("Ω", 1.0), ("kΩ", 1e3)], decimals=4, step=0.1)
        self.ron.setToolTip("The switch's resistance when closed")
        self.led_color = QtWidgets.QComboBox()
        for name in bb.LED_VF:
            self.led_color.addItem(name.capitalize(), name)
        self.led_color.setToolTip("The colour sets the usual forward voltage; you can change it below")
        self.vf = UnitSpinBox(0.05, 10, "V", step=0.1)
        self.vf.setToolTip("Below this, no current flows forward; above it, the part conducts")
        self.imax = UnitSpinBox(1e-4, 1, "A", families=[("A", 1.0), ("mA", 1e-3)], decimals=4, step=1)
        self.imax.setToolTip("Above this the LED would be damaged: the board warns instead")
        self.henries = UnitSpinBox(1e-9, 100, "H", families=[("H", 1.0), ("mH", 1e-3), ("µH", 1e-6), ("uH", 1e-6),
                                                             ("nH", 1e-9)], decimals=12, step=1)
        self.wiper = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.wiper.setRange(0, 100)
        self.wiper.setToolTip("Where the wiper sits: 0% at the first end, 100% at the other")
        self.rows = {}
        for key, label, w in (("source", "Source", self.source), ("volts", "Voltage", self.volts),
                              ("led_color", "Colour", self.led_color), ("vf", "Forward V", self.vf),
                              ("imax", "Max current", self.imax), ("henries", "Inductance", self.henries),
                              ("wiper", "Wiper", self.wiper),
                              ("ohms", "Resistance", self.ohms), ("farads", "Capacitance", self.farads),
                              ("sw_mode", "Flipped by", self.sw_mode), ("flip", "State", self.flip),
                              ("channel", "Type", self.channel), ("vth", "Threshold V<sub>th</sub>", self.vth),
                              ("ron", "R<sub>on</sub>", self.ron),
                              ("input", "Records on", self.probe_input), ("noise", "Noise", self.noise)):
            form.addRow(label, w)
            self.rows[key] = (form.labelForField(w), w)
        self.part_info = QtWidgets.QLabel()
        self.part_info.setWordWrap(True)
        form.addRow(self.part_info)
        right.addWidget(self.part_box)
        self.key_box = QtWidgets.QGroupBox("Key")
        kv = QtWidgets.QVBoxLayout(self.key_box)
        self.key_draw = KeyDrawing(self)
        kv.addWidget(self.key_draw)
        self.key_legs = QtWidgets.QLabel()
        self.key_legs.setWordWrap(True)
        self.key_legs.setTextFormat(QtCore.Qt.TextFormat.RichText)
        kv.addWidget(self.key_legs)
        self.key_what = QtWidgets.QLabel()
        self.key_what.setWordWrap(True)
        self.key_what.setTextFormat(QtCore.Qt.TextFormat.RichText)
        kv.addWidget(self.key_what)
        right.addWidget(self.key_box)
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
        self._rest_lit: dict[int, float] = {}
        self._rest_live: dict[int, bool] = {}   # MOSFET switches closed at rest
        self.btn_reset.clicked.connect(lambda: self._apply(bb.default_board(), keep_sel=False))
        self.btn_clear.clicked.connect(lambda: self._apply(bb.Board(), keep_sel=False))
        self.source.currentIndexChanged.connect(self._part_edited)
        self.sw_mode.currentIndexChanged.connect(self._part_edited)
        self.channel.currentIndexChanged.connect(self._part_edited)
        self.flip.clicked.connect(self._flip)
        self._live = QtCore.QTimer(self, interval=100, timeout=self._poll_switches)
        for w in (self.volts, self.ohms, self.farads, self.noise, self.vth, self.ron, self.vf, self.imax, self.henries):
            w.valueChanged.connect(self._part_edited)
        self.led_color.currentIndexChanged.connect(self._led_color_picked)
        self.wiper.valueChanged.connect(self._part_edited)
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
        self.view.lit = self._lit_at_rest()
        self.view.live = self._rest_live
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
        self._live.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._live.stop()

    def _poll_switches(self) -> None:
        """Show which MOSFET switches the recording has closed, and how bright its LEDs are."""
        state = getattr(getattr(self.main, "backend", None), "_board", None)
        recording = state is not None and getattr(state, "board", None) is self.board
        live = state.closed_now() if recording else self._rest_live
        lit = state.lit_now() if recording else self._rest_lit
        if live != self.view.live or lit != self.view.lit:
            self.view.live, self.view.lit = live, lit
            self.view.update()
            self._show_part_info()

    def _lit_at_rest(self) -> dict[int, float]:
        """Each LED's current with the stimulator at 0 V, once the circuit has settled
        (and, in `_rest_live`, which MOSFET switches that leaves closed)."""
        self._rest_lit, self._rest_live = {}, {}
        if any(p.kind == "led" or (p.kind == "switch" and p.mode == "mosfet") for p in self.board.parts):
            try:
                state = bb.BoardState(self.board, 1e-4)
                state.run(np.zeros(2000), [])
                self._rest_lit = state.lit_now()
                self._rest_live = state.closed_now()
            except ValueError:
                pass
        return self._rest_lit

    def _pick_more(self, kind: str, label: str) -> None:
        self.more.setText(f"More parts: {label}")
        self.more.setChecked(True)
        self.view.set_tool(kind)

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
            ends = part.a if part.kind in bb.ONE_HOLE else f"{part.a} to {part.b}"
            if part.gate in part.holes():
                ends = f"drain {part.a}, source {part.b}, gate {part.gate}"
            elif part.kind == "pot":
                ends = f"ends {part.a} and {part.b}, wiper {part.wiper}"
            elif bb.is_diode(part):
                ends = f"+ {part.a}, − {part.b}"
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
            elif part.kind == "switch":
                shown |= {"sw_mode", "ron"}
                self.sw_mode.setCurrentIndex(max(0, self.sw_mode.findData(part.mode)))
                self.ron.setValue(part.value)
                if part.mode == "toggle":
                    shown.add("flip")
                    self.flip.setText("Open it" if part.closed else "Close it")
                    info = f"<b>{'Closed' if part.closed else 'Open'}</b>. Double-click it on the board to flip it."
                else:
                    shown |= {"channel", "vth"}
                    self.channel.setCurrentIndex(max(0, self.channel.findData(part.channel)))
                    self.vth.setValue(part.vth)
                    above = "above" if part.channel == "n" else "below"
                    info = (f"Closes while the gate is more than V<sub>th</sub> {above} the source; open, it connects "
                            "nothing. No current flows into the gate. The preview's 1 V pulse may not reach V<sub>th</sub>.")
            elif part.kind == "wire":
                info = "Joins the two strips it connects."
            elif part.kind == "ground":
                info = "Ties this hole's strip to 0 V, like a wire to a − rail."
            elif part.kind == "battery":
                shown.add("volts")
                self.volts.setValue(part.value)
                info = "+ end at " + part.a
            elif part.kind == "inductor":
                shown.add("henries")
                self.henries.setValue(part.value)
            elif part.kind == "pot":
                shown |= {"ohms", "wiper"}
                self.ohms.setValue(part.value)
                self.wiper.setValue(round(part.pos * 100))
                info = (f"{part.pos:.0%} of the way from {part.a}: {part.value * part.pos:,.0f} Ω to the wiper, "
                        f"{part.value * (1 - part.pos):,.0f} Ω on to {part.b}.")
            elif part.kind == "button":
                shown.add("ron")
                self.ron.setValue(part.value)
                info = "Closed only while you hold the mouse down on it; it opens when you let go."
            elif bb.is_diode(part):
                shown.add("vf")
                self.vf.setValue(part.vf)
                if part.kind == "led":
                    shown |= {"led_color", "imax"}
                    self.led_color.setCurrentIndex(max(0, self.led_color.findData(part.color)))
                    self.imax.setValue(part.value)
            self.part_info.setText(info)
            if bb.is_diode(part):
                self._show_part_info()
        for key, (label, w) in self.rows.items():
            label.setVisible(key in shown)
            w.setVisible(key in shown)
        self.btn_delete.setEnabled(part is not None)
        self._show_key()
        self._loading = False

    def _show_key(self) -> None:
        """The key: the selected part, or else the part about to be placed."""
        i, tool = self.view.sel, self.view.tool
        part = self.board.parts[i] if 0 <= i < len(self.board.parts) else None
        placing = part is None and tool in bb.KINDS
        if placing:
            part = bb.new_part(tool, "c3", "c8")
            if tool == "pot":
                part.wiper = "e6"
        self.key_box.setVisible(part is not None)
        if part is None:
            return
        self.key_box.setTitle(f"Key: {bb.NAMES[part.kind]}" + (" (placing)" if placing else ""))
        self.key_draw.set_part(part)
        t = style.tokens(self._mode)
        rows = [f"<tr><td style='padding:2px 6px 2px 0'><b>{tag}</b></td><td style='padding:2px 0'><b>{name.capitalize()}</b>: {what}</td></tr>"
                for _leg, tag, name, what in key_legs(part)]
        self.key_legs.setText("<table>" + "".join(rows) + "</table>")
        self.key_what.setText(f"<span style='color:{t['muted']}'>{key_text(part)}</span>")

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

    def _show_part_info(self) -> None:
        """A diode's or LED's current now (while recording) or at rest."""
        i = self.view.sel
        part = self.board.parts[i] if 0 <= i < len(self.board.parts) else None
        if part is None or not bb.is_diode(part):
            return
        if part.kind == "diode":
            self.part_info.setText("Current flows from + to − only, once + is more than the forward voltage above −.")
            return
        amps = self.view.lit.get(i, 0.0)
        x, u = best_prefix(amps, "A", ("µ", "m", ""))
        if amps <= 0:
            state = "<b>Off</b>: no current flows forward."
        elif amps > part.value:
            warn = _WARN.get(self._mode, _WARN[style.LIGHT])
            state = f'<span style="color:{warn}">⚠ <b>{x:.3g} {u}</b>, over its maximum: add resistance in series.</span>'
        else:
            state = f"<b>Lit</b>, {x:.3g} {u}."
        self.part_info.setText(state + " Brightness follows the current (at rest with the stimulator at 0 V, or live while recording).")

    def _led_color_picked(self, *_) -> None:
        """A new colour brings that colour's usual forward voltage."""
        if self._loading:
            return
        self._loading = True
        self.vf.setValue(bb.LED_VF[self.led_color.currentData()])
        self._loading = False
        self._part_edited()

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
        elif part.kind == "battery":
            part.value = self.volts.value()
        elif part.kind == "inductor":
            part.value = self.henries.value()
        elif part.kind == "pot":
            part.value = self.ohms.value()
            part.pos = self.wiper.value() / 100
        elif part.kind == "button":
            part.value = self.ron.value()
        elif bb.is_diode(part):
            part.vf = self.vf.value()
            if part.kind == "led":
                part.color = self.led_color.currentData()
                part.value = self.imax.value()
        elif part.kind == "switch":
            part.mode = self.sw_mode.currentData()
            part.value = self.ron.value()
            part.channel = self.channel.currentData()
            part.vth = self.vth.value()
            if part.mode == "mosfet" and not part.gate:
                part.gate = gate_hole(part.a, part.b) or ""
                if not part.gate:
                    self._show_message("No free hole beside the switch for its gate: move it first.", error=True)
                    self._show_part()
                    return
        self._apply(board)

    def _flip(self) -> None:
        i = self.view.sel
        if 0 <= i < len(self.board.parts) and self.board.parts[i].kind == "switch":
            board = bb.copy_board(self.board)
            board.parts[i].closed = not board.parts[i].closed
            self._apply(board)

    def delete_selected(self) -> None:
        i = self.view.sel
        if not 0 <= i < len(self.board.parts):
            return
        board = bb.copy_board(self.board)
        del board.parts[i]
        self.view.sel = -1
        self._apply(board)

    # -- board files -------------------------------------------------------------------
    _FILTER = "Breadboard (*.board.toml);;TOML (*.toml)"

    def _board_folder(self) -> str:
        return str(Path(self.main.settings.value("board_folder", self.main.cfg.data_dir or str(Path.home()))))

    def save_board(self) -> None:
        """Save the circuit as a .board.toml file (the same lines as in a config file)."""
        start = str(Path(self._board_folder()) / "circuit.board.toml")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save board", start, self._FILTER)
        if not path:
            return
        if not path.endswith(".toml"):
            path += ".board.toml"
        text = "# A NEWT breadboard circuit: Breadboard › Open board… to use it\n" + "\n".join(bb.to_toml_lines(self.board)) + "\n"
        try:
            Path(path).write_text(text)
        except OSError as e:
            self._show_message(f"Couldn't save the board: {e}", error=True)
            return
        self.main.settings.setValue("board_folder", str(Path(path).parent))
        self._show_message(f"Saved {Path(path).name}")

    def open_board(self) -> None:
        """Replace the board with one saved by Save board… (or a config file's breadboard)."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open board", self._board_folder(), self._FILTER)
        if not path:
            return
        try:
            sim = tomllib.loads(Path(path).read_text()).get("simulated", {})
            if "breadboard" not in sim:
                raise ValueError("there's no breadboard circuit in it")
            board = bb.from_toml(sim)
        except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
            self._show_message(f"Couldn't open {Path(path).name}: {e}", error=True)
            return
        self.main.settings.setValue("board_folder", str(Path(path).parent))
        self._apply(board, keep_sel=False)

    def load_lesson_circuit(self, spec: dict) -> str:
        """Replace the board with a lesson's circuit (lessoncircuit.build), its probes on the
        first free inputs. Returns "" when done, otherwise what went wrong."""
        try:
            board, notes = lessoncircuit.build(spec)
        except (ValueError, KeyError, TypeError) as e:
            return str(e)
        inputs = [c.physical for c in self._inputs()]
        for probe, phys in zip(board.probes(), inputs):
            probe.input = phys
        self._apply(board, keep_sel=False)
        if self.board is not board:
            return self.message.text() or "the circuit couldn't be solved"
        title = spec.get("title", "the lesson's circuit")
        self._show_message(f"Built from {title}. " + ". ".join(notes) + ".")
        return ""

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
            part = next((q for q in s.parts if q.get("kind") in ("pulse", "step")), None) \
                if s.shape == "segments" else None
            length = float(part["dur"]) if part is not None else None
            if length is not None and length / 1000 < 5 * tau:
                reach = 1 - math.exp(-length / 1000 / tau)
                what = part["kind"]
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
        self._show_key()
