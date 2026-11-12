"""The Analysis whiteboard: an endless board the charts sit on, each where and as big as you
put it. Nothing resizes them for you: you choose every chart and where it goes.

Drag a chart's title bar to move it and its bottom-right corner to resize it; it snaps to a
20 px grid, and to the edges of the other charts, with a guide line while it does. Click a
chart to pick it (its settings show beside the board), shift-click to pick more, or drag a
box over empty space. Dragging one picked chart moves them all.

Pan by scrolling, or with the Hand tool (or the middle button). Zoom with the − and + of
the toolbar, ⌘/Ctrl and the scroll wheel, or a pinch; zooming makes the charts smaller or
bigger on screen and leaves where they are on the board alone. The minimap shows the
whole board and the part of it on screen; click it to go there.

With the Export tool, drag a box anywhere, over the charts too, to save what's in it as a
figure (see figure.py).
"""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import style

GRID = 20                       # snapping, in board pixels
NEAR = 10                       # how close an edge must come to snap to another chart's
MIN_W, MIN_H = 160, 120         # the smallest a chart can be made
ZOOMS = (0.25, 2.0)
SELECT, HAND, MEASURE, EXPORT = "select", "hand", "measure", "export"


def snap(v: float) -> float:
    return round(v / GRID) * GRID


def _near(v: float, edges: list[float]) -> float | None:
    return next((e for e in edges if abs(v - e) < NEAR), None)


class TitleBar(QtWidgets.QWidget):
    """A chart's name, what it has of its own, Copy and ×. Dragging it moves the chart."""

    def __init__(self, frame: "ChartFrame"):
        super().__init__()
        self.frame = frame
        self.setObjectName("chartBar")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(8, 2, 4, 2)
        row.setSpacing(6)
        self.name = QtWidgets.QLabel()
        self.tag = QtWidgets.QLabel()
        self.tag.setObjectName("chartTag")
        for w in (self.name, self.tag):       # a long name gives way, so a narrow chart can be narrow
            w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
            w.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)   # dragging the name moves the chart
        row.addWidget(self.name, 2)
        row.addWidget(self.tag, 1)
        self.btn_copy = self._button("Copy", "Open another of this chart, with the same settings", row)
        self.btn_close = self._button("×", "Close this chart", row)

    @staticmethod
    def _button(text: str, tip: str, row) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(text=text, autoRaise=True)
        b.setToolTip(tip)
        b.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        row.addWidget(b)
        return b

    def mousePressEvent(self, ev):
        self.frame.board.title_pressed(self.frame, ev)

    def mouseMoveEvent(self, ev):
        self.frame.board.drag_to(ev)

    def mouseReleaseEvent(self, ev):
        self.frame.board.drag_done()


class Grip(QtWidgets.QWidget):
    """The corner that resizes a chart."""

    def __init__(self, frame: "ChartFrame"):
        super().__init__(frame)
        self.frame = frame
        self.resize(16, 16)
        self.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
        self.setToolTip("Drag to resize")

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        p.setPen(QtGui.QPen(QtGui.QColor(self.frame.board.tokens["subtle"]), 1.4))
        p.drawLine(14, 4, 4, 14)
        p.drawLine(14, 9, 9, 14)

    def mousePressEvent(self, ev):
        self.frame.board.grip_pressed(self.frame, ev)

    def mouseMoveEvent(self, ev):
        self.frame.board.drag_to(ev)

    def mouseReleaseEvent(self, ev):
        self.frame.board.drag_done()


class ChartFrame(QtWidgets.QFrame):
    """A chart on the board: its title bar, the chart, and a corner to resize it. `place` is
    where it is on the board (board pixels, before zoom)."""

    def __init__(self, chart, board: "Board", place: QtCore.QRectF):
        super().__init__(board)
        self.chart, self.board, self.place = chart, board, QtCore.QRectF(place)
        chart.dock = self
        self.setObjectName("chartFrame")
        self.setMinimumSize(1, 1)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(chart, 1)
        chart.setMinimumSize(1, 1)
        self.bar = TitleBar(self)                 # over the chart's top, shown while the mouse is on it
        self.bar.setParent(self)
        self.bar.hide()
        self.grip = Grip(self)
        self.picked = False

    def show_title(self, name: str, tag: str, pinned: bool) -> None:
        self.bar.name.setText(f"<b>{name}</b>")
        self.bar.tag.setText(tag)
        self.bar.tag.setVisible(bool(tag))

    def set_locked(self, on: bool) -> None:
        self.bar.btn_close.setVisible(not on)
        self.bar.btn_copy.setVisible(not on)
        self.grip.setVisible(not on)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.bar.setGeometry(1, 1, self.width() - 2, self.bar.sizeHint().height())
        self.grip.move(self.width() - self.grip.width() - 1, self.height() - self.grip.height() - 1)
        self.grip.raise_()

    # the title bar shows only while the mouse is on the chart, so the board can be a figure
    def enterEvent(self, ev):
        super().enterEvent(ev)
        self.bar.show()
        self.bar.raise_()

    def leaveEvent(self, ev):
        super().leaveEvent(ev)
        if self.board.dragging() is not self:
            self.bar.hide()

    def restyle(self) -> None:
        t = self.board.tokens
        on = self.picked
        self.setStyleSheet(
            f"#chartFrame {{ background: {t['panel']}; border: {2 if on else 1}px solid "
            f"{t['focus'] if on else t['line']}; border-radius: 6px; }}"
            f"#chartBar {{ background: {t['selection'] if on else t['button']}; border-top-left-radius: 5px; "
            f"border-top-right-radius: 5px; }}"
            f"#chartBar QLabel, #chartBar QToolButton {{ color: {t['ink']}; }}"
            f"#chartTag {{ color: {t['muted']}; }}")


class _Overlay(QtWidgets.QWidget):
    """Guides and the selection box, over the charts. Clicks go through it, except with the
    Export tool, when it takes the box being dragged over the charts."""

    def __init__(self, board: "Board"):
        super().__init__(board)
        self.board = board
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton:
            self.board.box = QtCore.QRectF(ev.position(), ev.position())
            self.update()

    def mouseMoveEvent(self, ev):
        if self.board.box is not None:
            self.board.box = QtCore.QRectF(self.board.box.topLeft(), ev.position())
            self.update()

    def mouseReleaseEvent(self, ev):
        b = self.board
        r, b.box = (b.box.normalized() if b.box is not None else QtCore.QRectF()), None
        self.update()
        if r.width() < 4 and r.height() < 4:          # a click: the last box again, if there is one
            if b.last_export is not None:
                b.export_requested.emit(QtCore.QRectF(b.last_export))
            return
        b.last_export = QtCore.QRectF(b.to_board(r.topLeft()), b.to_board(r.bottomRight()))
        b.export_requested.emit(QtCore.QRectF(b.last_export))

    def paintEvent(self, ev):
        b = self.board
        p = QtGui.QPainter(self)
        focus = QtGui.QColor(b.tokens["focus"])
        if b.guide_x is not None or b.guide_y is not None:
            pen = QtGui.QPen(QtGui.QColor("#d1495b"), 1.2, QtCore.Qt.PenStyle.DashLine)
            p.setPen(pen)
            if b.guide_x is not None:
                x = b.to_screen(QtCore.QPointF(b.guide_x, 0)).x()
                p.drawLine(QtCore.QLineF(x, 0, x, self.height()))
            if b.guide_y is not None:
                y = b.to_screen(QtCore.QPointF(0, b.guide_y)).y()
                p.drawLine(QtCore.QLineF(0, y, self.width(), y))
        if b.box is not None:
            fill = QtGui.QColor(focus)
            fill.setAlpha(28)
            p.setPen(QtGui.QPen(focus, 1))
            p.setBrush(fill)
            p.drawRect(b.box.normalized())


class Minimap(QtWidgets.QWidget):
    """The whole board, small: the charts, and the part on screen. Click to go there."""

    def __init__(self, board: "Board"):
        super().__init__(board)
        self.board = board
        self.setFixedSize(176, 108)
        self.setToolTip("The whole board. Click or drag to go there")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def _frame(self):
        """(world rect shown, scale, offset) so the charts and the view fit."""
        b = self.board
        view = b.view_rect()
        r = QtCore.QRectF(view)
        for f in b.frames:
            r = r.united(f.place)
        r.adjust(-40, -40, 40, 40)
        inner = QtCore.QRectF(6, 20, self.width() - 12, self.height() - 26)
        k = min(inner.width() / r.width(), inner.height() / r.height())
        return r, k, inner.topLeft()

    def paintEvent(self, ev):
        b, t = self.board, self.board.tokens
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(QtGui.QPen(QtGui.QColor(t["line"]), 1))
        p.setBrush(QtGui.QColor(t["panel"]))
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.setPen(QtGui.QColor(t["muted"]))
        f = p.font()
        f.setPointSizeF(max(8.0, f.pointSizeF() - 2))
        p.setFont(f)
        n = len(b.frames)
        p.drawText(QtCore.QRectF(8, 2, self.width() - 16, 18), QtCore.Qt.AlignmentFlag.AlignVCenter,
                   f"Minimap · {n} chart{'' if n == 1 else 's'}")
        r, k, at = self._frame()
        to = lambda q: QtCore.QRectF(at.x() + (q.x() - r.x()) * k, at.y() + (q.y() - r.y()) * k,
                                     q.width() * k, q.height() * k)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        for fr in b.frames:
            c = QtGui.QColor(t["focus"])
            c.setAlpha(150 if fr.picked else 60)
            p.setBrush(c)
            p.drawRect(to(fr.place))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.setPen(QtGui.QPen(QtGui.QColor(t["focus"]), 1.5))
        p.drawRect(to(b.view_rect()))

    def _go(self, pos: QtCore.QPointF) -> None:
        r, k, at = self._frame()
        world = QtCore.QPointF(r.x() + (pos.x() - at.x()) / k, r.y() + (pos.y() - at.y()) / k)
        self.board.centre_on(world)

    def mousePressEvent(self, ev):
        self._go(ev.position())

    def mouseMoveEvent(self, ev):
        if ev.buttons() & QtCore.Qt.MouseButton.LeftButton:
            self._go(ev.position())


class Board(QtWidgets.QWidget):
    picked_changed = QtCore.pyqtSignal()
    moved = QtCore.pyqtSignal()                       # charts moved or resized, or the view changed
    context_requested = QtCore.pyqtSignal(QtCore.QPointF, QtCore.QPoint)   # board place, screen place
    delete_requested = QtCore.pyqtSignal()
    zoom_changed = QtCore.pyqtSignal(float)
    export_requested = QtCore.pyqtSignal(QtCore.QRectF)   # the part of the board to save, in board pixels

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frames: list[ChartFrame] = []
        self.zoom = 1.0
        self.offset = QtCore.QPointF(24, 24)          # where the board's 0, 0 is on screen
        self.tool = SELECT
        self.locked = False
        self.tokens = style.tokens(style.LIGHT)
        self.guide_x: float | None = None
        self.guide_y: float | None = None
        self.box: QtCore.QRectF | None = None
        self.last_export: QtCore.QRectF | None = None
        self.grid = True                                  # the dots
        self._drag: dict | None = None
        self._at: QtCore.QPoint | None = None         # where the board's corner is in the window
        self.setMouseTracking(False)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.ClickFocus)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setMinimumSize(200, 200)
        self.overlay = _Overlay(self)
        self.minimap = Minimap(self)
        self.toolbar: QtWidgets.QWidget | None = None
        self.hint = QtWidgets.QLabel("The board is empty. Right-click anywhere on it, or use + Add chart, to put "
                                     "a chart here: you choose every chart and where it goes.", self)
        self.hint.setWordWrap(True)
        self.hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.grabGesture(QtCore.Qt.GestureType.PinchGesture)

    # -- where things are ---------------------------------------------------------------------
    def to_screen(self, p: QtCore.QPointF) -> QtCore.QPointF:
        return QtCore.QPointF(p.x() * self.zoom + self.offset.x(), p.y() * self.zoom + self.offset.y())

    def to_board(self, p: QtCore.QPointF) -> QtCore.QPointF:
        return QtCore.QPointF((p.x() - self.offset.x()) / self.zoom, (p.y() - self.offset.y()) / self.zoom)

    def view_rect(self) -> QtCore.QRectF:
        """The part of the board on screen (board pixels)."""
        return QtCore.QRectF(self.to_board(QtCore.QPointF(0, 0)),
                             self.to_board(QtCore.QPointF(self.width(), self.height())))

    def place_all(self) -> None:
        for f in self.frames:
            a = self.to_screen(f.place.topLeft())
            f.setGeometry(round(a.x()), round(a.y()), max(1, round(f.place.width() * self.zoom)),
                          max(1, round(f.place.height() * self.zoom)))
        self.hint.setVisible(not self.frames)
        self.overlay.update()
        self.minimap.update()
        self.update()

    def set_toolbar(self, w: QtWidgets.QWidget) -> None:
        self.toolbar = w
        w.setParent(self)
        self._place_floating()

    def _place_floating(self) -> None:
        self.overlay.setGeometry(self.rect())
        self.minimap.move(self.width() - self.minimap.width() - 12, 12)
        self.hint.setGeometry(self.rect().adjusted(60, 0, -60, 0))
        if self.toolbar is not None:
            self.toolbar.adjustSize()
            tb = self.toolbar
            tb.move(max(8, (self.width() - tb.width()) // 2), self.height() - tb.height() - 12)
        self.overlay.raise_()
        self.hint.raise_()
        self.minimap.raise_()
        if self.toolbar is not None:
            self.toolbar.raise_()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # a panel folding out beside the board moves its edge: keep the charts still on screen
        at = self.mapTo(self.window(), QtCore.QPoint(0, 0))
        if self._at is not None and self.isVisible():
            self.offset -= QtCore.QPointF(at - self._at)
        self._at = at
        self._place_floating()
        self.place_all()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._at = self.mapTo(self.window(), QtCore.QPoint(0, 0))

    # -- the charts ---------------------------------------------------------------------------
    def add(self, chart, place: QtCore.QRectF) -> ChartFrame:
        f = ChartFrame(chart, self, place)
        f.restyle()
        f.set_locked(self.locked)
        self.frames.append(f)
        f.show()
        self.place_all()
        self._place_floating()
        return f

    def remove(self, frame: ChartFrame) -> None:
        if frame in self.frames:
            self.frames.remove(frame)
        frame.hide()
        frame.setParent(None)
        frame.deleteLater()
        self.place_all()

    def free_place(self, w: float = 400, h: float = 280, near: QtCore.QPointF | None = None) -> QtCore.QRectF:
        """Room for a new chart: at `near`, else the first empty spot in view, left to right."""
        if near is not None:
            return QtCore.QRectF(snap(near.x()), snap(near.y()), w, h)
        view = self.view_rect()
        y = snap(max(view.top(), 0) + GRID)
        while y < view.top() + 20000:
            x = snap(max(view.left(), 0) + GRID)
            while x + w <= max(view.right(), x + w):
                r = QtCore.QRectF(x, y, w, h)
                if not any(r.adjusted(-GRID, -GRID, GRID, GRID).intersects(f.place) for f in self.frames):
                    return r
                x += GRID * 2
                if x + w > view.right():
                    break
            y += GRID * 2
        bottom = max((f.place.bottom() for f in self.frames), default=0)
        return QtCore.QRectF(GRID, snap(bottom + 2 * GRID), w, h)

    def picked(self) -> list[ChartFrame]:
        return [f for f in self.frames if f.picked]

    def pick(self, frames: list[ChartFrame], add: bool = False) -> None:
        for f in self.frames:
            on = (f.picked and add) or f in frames
            if on != f.picked:
                f.picked = on
                f.restyle()
        self.minimap.update()
        self.picked_changed.emit()

    def toggle(self, frame: ChartFrame) -> None:
        frame.picked = not frame.picked
        frame.restyle()
        self.minimap.update()
        self.picked_changed.emit()

    def set_locked(self, on: bool) -> None:
        self.locked = on
        for f in self.frames:
            f.set_locked(on)

    def apply_theme(self, mode: str) -> None:
        self.tokens = style.tokens(mode)
        for f in self.frames:
            f.restyle()
        self.hint.setStyleSheet(f"color: {self.tokens['muted']};")
        self.update()
        self.minimap.update()

    # -- moving and resizing ------------------------------------------------------------------
    def title_pressed(self, frame: ChartFrame, ev) -> None:
        shift = bool(ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
        if shift:
            self.toggle(frame)
            return
        if not frame.picked:
            self.pick([frame])
        else:
            self.picked_changed.emit()                 # it's the one whose settings show
        frame.raise_()
        self._raise_floating()
        if self.locked or ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        moving = self.picked() or [frame]
        self._drag = {"mode": "move", "frame": frame, "at": ev.globalPosition(),
                      "starts": {id(f): QtCore.QPointF(f.place.topLeft()) for f in moving}, "frames": moving}

    def grip_pressed(self, frame: ChartFrame, ev) -> None:
        if self.locked:
            return
        self.pick([frame], add=frame.picked)
        self._drag = {"mode": "size", "frame": frame, "at": ev.globalPosition(), "size": frame.place.size()}

    def _raise_floating(self) -> None:
        self.overlay.raise_()
        self.minimap.raise_()
        if self.toolbar is not None:
            self.toolbar.raise_()

    def drag_to(self, ev) -> None:
        d = self._drag
        if d is None:
            return
        delta = (ev.globalPosition() - d["at"]) / self.zoom
        me: ChartFrame = d["frame"]
        if d["mode"] == "move":
            others = [f for f in self.frames if f not in d["frames"]]
            ex = [e for f in others for e in (f.place.left(), f.place.right())]
            ey = [e for f in others for e in (f.place.top(), f.place.bottom())]
            start = d["starts"][id(me)]
            x, y = snap(start.x() + delta.x()), snap(start.y() + delta.y())
            gx = gy = None
            w, h = me.place.width(), me.place.height()
            if (e := _near(x, ex)) is not None:
                x, gx = e, e
            elif (e := _near(x + w, ex)) is not None:
                x, gx = e - w, e
            if (e := _near(y, ey)) is not None:
                y, gy = e, e
            elif (e := _near(y + h, ey)) is not None:
                y, gy = e - h, e
            dx, dy = x - start.x(), y - start.y()
            for f in d["frames"]:
                s = d["starts"][id(f)]
                f.place.moveTo(s.x() + dx, s.y() + dy)
        else:
            others = [f for f in self.frames if f is not me]
            ex = [e for f in others for e in (f.place.left(), f.place.right())]
            ey = [e for f in others for e in (f.place.top(), f.place.bottom())]
            w = max(MIN_W, snap(d["size"].width() + delta.x()))
            h = max(MIN_H, snap(d["size"].height() + delta.y()))
            gx = gy = None
            if (e := _near(me.place.left() + w, ex)) is not None:
                w, gx = e - me.place.left(), e
            if (e := _near(me.place.top() + h, ey)) is not None:
                h, gy = e - me.place.top(), e
            me.place.setSize(QtCore.QSizeF(w, h))
        self.guide_x, self.guide_y = gx, gy
        self.place_all()

    def dragging(self) -> ChartFrame | None:
        """The chart being moved or resized, if one is."""
        return self._drag["frame"] if self._drag is not None and "frame" in self._drag else None

    def drag_done(self) -> None:
        if self._drag is not None and self._drag["mode"] in ("move", "size"):
            self.moved.emit()
            me = self._drag["frame"]
            if not me.underMouse():
                me.bar.hide()
        self._drag = None
        self.guide_x = self.guide_y = None
        self.overlay.update()

    # -- the empty board ------------------------------------------------------------------------
    def mousePressEvent(self, ev):
        self.setFocus()
        b = ev.button()
        if b == QtCore.Qt.MouseButton.RightButton:
            if not self.locked:
                self.context_requested.emit(self.to_board(ev.position()), ev.globalPosition().toPoint())
            return
        if b == QtCore.Qt.MouseButton.MiddleButton or (b == QtCore.Qt.MouseButton.LeftButton and self.tool == HAND):
            self._drag = {"mode": "pan", "at": ev.position(), "offset": QtCore.QPointF(self.offset)}
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            return
        if b == QtCore.Qt.MouseButton.LeftButton:
            shift = bool(ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
            self._drag = {"mode": "box", "at": ev.position(), "shift": shift,
                          "before": self.picked() if shift else []}
            self.box = QtCore.QRectF(ev.position(), ev.position())

    def mouseMoveEvent(self, ev):
        d = self._drag
        if d is None:
            return
        if d["mode"] == "pan":
            self.offset = d["offset"] + (ev.position() - d["at"])
            self.place_all()
            self.moved.emit()
        elif d["mode"] == "box":
            self.box = QtCore.QRectF(d["at"], ev.position())
            self.overlay.update()

    def mouseReleaseEvent(self, ev):
        d, self._drag = self._drag, None
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor if self.tool == HAND else QtCore.Qt.CursorShape.ArrowCursor)
        if d is None or d["mode"] != "box":
            return
        r = self.box.normalized() if self.box is not None else QtCore.QRectF()
        self.box = None
        self.overlay.update()
        if r.width() < 4 and r.height() < 4:
            if not d["shift"]:
                self.pick([])
            return
        world = QtCore.QRectF(self.to_board(r.topLeft()), self.to_board(r.bottomRight()))
        self.pick(d["before"] + [f for f in self.frames if f.place.intersects(world)])

    def wheelEvent(self, ev):
        mods = ev.modifiers()
        if mods & (QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.MetaModifier):
            self.zoom_about(self.zoom * 1.0015 ** ev.angleDelta().y(), ev.position())
            return
        d = ev.pixelDelta() if not ev.pixelDelta().isNull() else ev.angleDelta() / 2
        self.offset += QtCore.QPointF(d.x(), d.y())
        self.place_all()
        self.moved.emit()

    def event(self, ev):
        if ev.type() == QtCore.QEvent.Type.Gesture:
            pinch = ev.gesture(QtCore.Qt.GestureType.PinchGesture)
            if pinch is not None:
                centere = self.mapFromGlobal(pinch.centerPoint().toPoint())
                self.zoom_about(self.zoom * pinch.scaleFactor(), QtCore.QPointF(centere))
                return True
        if ev.type() == QtCore.QEvent.Type.NativeGesture and \
                ev.gestureType() == QtCore.Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom_about(self.zoom * (1 + ev.value()), ev.position())
            return True
        return super().event(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace) and self.picked() and not self.locked:
            self.delete_requested.emit()
            return
        super().keyPressEvent(ev)

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor if tool == HAND else QtCore.Qt.CursorShape.ArrowCursor)
        self.overlay.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, tool != EXPORT)

    def set_grid(self, on: bool) -> None:
        self.grid = on
        self.update()
        self.moved.emit()

    # -- the view --------------------------------------------------------------------------
    def zoom_about(self, z: float, at: QtCore.QPointF | None = None) -> None:
        z = max(ZOOMS[0], min(ZOOMS[1], z))
        at = at if at is not None else QtCore.QPointF(self.width() / 2, self.height() / 2)
        world = self.to_board(at)
        self.zoom = z
        self.offset = QtCore.QPointF(at.x() - world.x() * z, at.y() - world.y() * z)
        self.place_all()
        self.zoom_changed.emit(z)
        self.moved.emit()

    def centre_on(self, world: QtCore.QPointF) -> None:
        self.offset = QtCore.QPointF(self.width() / 2 - world.x() * self.zoom, self.height() / 2 - world.y() * self.zoom)
        self.place_all()
        self.moved.emit()

    def fit_all(self) -> None:
        """Zoom and pan to show every chart."""
        if not self.frames:
            self.zoom, self.offset = 1.0, QtCore.QPointF(24, 24)
            self.place_all()
            self.zoom_changed.emit(1.0)
            return
        r = self.frames[0].place
        for f in self.frames[1:]:
            r = r.united(f.place)
        r = r.adjusted(-GRID, -GRID, GRID, GRID + 70)              # room for the toolbar
        z = max(ZOOMS[0], min(1.0, self.width() / r.width(), self.height() / r.height()))
        self.zoom = z
        self.offset = QtCore.QPointF(-r.x() * z + (self.width() - r.width() * z) / 2, -r.y() * z)
        self.place_all()
        self.zoom_changed.emit(z)
        self.moved.emit()

    def tidy(self) -> None:
        """Line the charts up in rows, keeping their sizes and order, as wide as the view."""
        width = max(600.0, self.view_rect().width() - 2 * GRID)
        x = y = GRID
        row = 0.0
        for f in sorted(self.frames, key=lambda f: (snap(f.place.top() / 4), f.place.left())):
            if x > GRID and x + f.place.width() > width:
                x, y, row = GRID, y + row + 2 * GRID, 0.0
            f.place.moveTo(x, y)
            x += f.place.width() + 2 * GRID
            row = max(row, f.place.height())
        self.place_all()
        self.moved.emit()

    # -- drawing --------------------------------------------------------------------------------
    def paintEvent(self, ev):
        t = self.tokens
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(t["ground"]))
        if not self.grid:
            return
        step = GRID * self.zoom
        while step < 12:
            step *= 2
        dot = QtGui.QColor(t["line"])
        p.setPen(QtGui.QPen(dot, 1.6 if self.zoom >= 0.6 else 1.2))
        x0 = self.offset.x() % step
        y0 = self.offset.y() % step
        pts = []
        y = y0
        while y < self.height():
            x = x0
            while x < self.width():
                pts.append(QtCore.QPointF(x, y))
                x += step
            y += step
        p.drawPoints(pts)

    def state(self) -> dict:
        return {"zoom": self.zoom, "x": self.offset.x(), "y": self.offset.y(), "grid": self.grid}

    def restore(self, s: dict) -> None:
        self.zoom = float(s.get("zoom", 1.0))
        self.offset = QtCore.QPointF(float(s.get("x", 24)), float(s.get("y", 24)))
        self.grid = bool(s.get("grid", True))
        self.place_all()
        self.zoom_changed.emit(self.zoom)
