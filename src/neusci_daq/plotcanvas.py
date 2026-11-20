"""The Plot Canvas: a page to lay figures out on, as for a lab report or a poster. Figures
come from Single Plot (Send to Canvas). Drag a figure to move it, its corner to resize it;
each gets a panel letter (A, B, C…) in the order they sit, top to bottom and left to right.

A figure dragged catches where its sides, its middle or its plot's sides line up with another
figure's or the page's margins, a line showing which (Alt: free). Picked, it resizes by any
corner (Shift keeps its shape). Align lines up the figures picked or spaces them evenly; the
arrow keys move what's picked 1 mm (Shift 10, Alt 0.1). Drawings are caught by their lines and
reshaped by their ends or corners (Shift: level, upright, square). Every change can be undone
(Undo, ⌘/Ctrl Z), DataPad numbers apart: linked figures always show the table as it is.

The figures are drawn one at a time between what the mouse and keys ask for (draw_soon), only
when they changed or a zoom needs more pixels of them, so the window never waits on them.

A figure stays linked to the DataPad (its numbers follow the table) until you freeze it.
Double-click one to change it in Single Plot. Export saves the whole page as PDF or SVG
(every line and label its own object) or PNG.

Add image… puts a picture on the page too (PNG, JPEG, TIFF, SVG or PDF): a diagram of the
setup, a photo of the prep. It keeps its shape as you resize it. SVGs and PDFs are drawn
as pictures at 300 dpi when they're added, and every picture is kept inside the session.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import figpage
from . import figspec as F
from .figpage import CUSTOM, LINKS, MARGIN, PAPERS, SIDE_MM     # the page's papers, margin and links
from .viewkit import with_slider

PX = 4.0                          # scene units per mm
MAX_PX = 3000                     # the most pixels along a figure's longer side, however far the zoom
HANDLE = 8.0                      # a handle's side, in pixels on the screen
SNAP = 6.0                        # how near (pixels on the screen) an edge catches on another
STEPS = 50                        # changes kept, to undo
BLUE, GUIDE = "#1a5fd0", "#e0338a"                      # what's picked; the lines where edges line up
ALIGN = (("left", "Left sides"), ("center", "Centers"), ("right", "Right sides"), None,
         ("top", "Tops"), ("middle", "Middles"), ("bottom", "Bottoms"), None,
         ("across", "Even gaps across"), ("down", "Even gaps down"))


def render(spec: dict, data: dict, dpi: float) -> QtGui.QImage:
    return QtGui.QImage.fromData(figpage.png(spec, data, dpi))


def constrained(kind: str, p0: QtCore.QPointF, p1: QtCore.QPointF) -> QtCore.QPointF:
    """Where a pull from p0 to p1 ends with Shift held: a line or arrow level, upright or at
    45°; a box square, an ellipse a circle."""
    dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
    if kind in ("line", "arrow"):
        a = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        return QtCore.QPointF(p0.x() + math.hypot(dx, dy) * math.cos(a), p0.y() + math.hypot(dx, dy) * math.sin(a))
    s = max(abs(dx), abs(dy))
    return QtCore.QPointF(p0.x() + math.copysign(s, dx), p0.y() + math.copysign(s, dy))


class Panel(QtWidgets.QGraphicsObject):
    """One figure on the page."""

    moved = QtCore.Signal()

    def __init__(self, canvas: "PlotCanvasPage", spec: dict, data: dict, linked: bool = True):
        super().__init__()
        self.canvas, self.spec, self.data, self.linked = canvas, F.on_page(spec, data), data, linked
        self.letter = ""
        self._img: QtGui.QImage | None = None
        self._img_dpi = 0.0                       # what its picture was drawn at (0: to be drawn again)
        self.axes: tuple | None = None            # where the plot sits in it: x0, y0 (from the bottom), w, h
        self._resizing = None
        self.setFlags(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.rerender()

    def size(self) -> QtCore.QSizeF:
        return QtCore.QSizeF(self.spec["w_mm"] * PX, self.spec["h_mm"] * PX)

    def boundingRect(self):
        s = self.size()
        return QtCore.QRectF(-2, -2, s.width() + 4, s.height() + 4)

    def rerender(self) -> None:
        """The figure changed: drawn again soon (PlotCanvasPage.draw_soon), its old picture
        shown until then."""
        self._img_dpi = 0.0
        self.prepareGeometryChange()
        self.update()
        self.canvas.draw_soon(self)

    def dpi_wanted(self) -> float:
        """Sharp at this zoom on this screen, up to MAX_PX along the longer side."""
        view = self.canvas.view
        zoom = max(1.0, view.transform().m11()) * max(1.0, view.devicePixelRatioF())
        return min(25.4 * PX * zoom, MAX_PX * 25.4 / max(self.spec["w_mm"], self.spec["h_mm"]))

    def soft(self) -> bool:
        """Its picture isn't right for this zoom: too few pixels to look sharp, or so many it's
        slow to show."""
        if self.spec.get("kind") == "image":
            return self._img is None or not self._img_dpi    # a picture put on the page: as it is, at any zoom
        want = self.dpi_wanted()
        return self._img_dpi < want / 1.1 or self._img_dpi > want * 2.5

    def draw_now(self) -> None:
        if self.spec.get("kind") == "image":
            self._img = render(self.spec, self.data, 0).convertToFormat(
                QtGui.QImage.Format.Format_ARGB32_Premultiplied)
            self._img_dpi, self.axes = float("inf"), None
        else:                                           # matplotlib's pixels as they are, in the format quickest to paint
            dpi = self.dpi_wanted()
            buf, w, h, self.axes = figpage.pixels(self.spec, self.data, dpi)
            self._img = QtGui.QImage(buf, w, h, 4 * w, QtGui.QImage.Format.Format_RGBA8888).convertToFormat(
                QtGui.QImage.Format.Format_RGB32)
            self._img_dpi = dpi
        self.update()

    def lines(self, k: int, at: float | None = None) -> list[tuple[float, str]]:
        """What it lines up by, across (k 0) or down (k 1), in scene units, were it at `at` (else
        where it is): its sides ("side"), its middle ("mid") and its plot's sides ("plot")."""
        s = self.size()
        full = s.width() if k == 0 else s.height()
        o = (self.pos().x() if k == 0 else self.pos().y()) if at is None else at
        out = [(o, "side"), (o + full, "side"), (o + full / 2, "mid")]
        if self.axes is not None:
            x0, y0, w, h = self.axes
            a, b = (x0, x0 + w) if k == 0 else (1 - y0 - h, 1 - y0)
            out += [(o + a * full, "plot"), (o + b * full, "plot")]
        return out

    def paint(self, p, opt, widget=None):
        s = self.size()
        r = QtCore.QRectF(0, 0, s.width(), s.height())
        if self._img is not None:
            p.drawImage(r, self._img)
        else:                                           # not drawn yet: they're drawn one at a time
            p.fillRect(r, QtGui.QColor("white"))
            p.setPen(QtGui.QPen(QtGui.QColor("#d0d3d8"), 0))
            p.drawRect(r)
        if self.letter and self.canvas.letters.isChecked():
            f = QtGui.QFont()
            f.setBold(True)
            f.setPixelSize(int(F.paper_font(self.spec) * 1.4 / 72 * 25.4 * PX))
            p.setFont(f)
            p.setPen(QtGui.QColor("black"))
            p.drawText(QtCore.QPointF(2, f.pixelSize()), self.letter)
        if self.isSelected():
            p.setPen(QtGui.QPen(QtGui.QColor(BLUE), 1.5, QtCore.Qt.PenStyle.DashLine))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(BLUE))
            h = self._grip()
            for x in (r.left(), r.right() - h):         # a handle at each corner, to resize by
                for y in (r.top(), r.bottom() - h):
                    p.drawRect(QtCore.QRectF(x, y, h, h))
        if not self.linked and self.spec.get("kind") not in ("image", "curves"):
            p.setPen(QtGui.QColor("#888"))
            f = QtGui.QFont()
            f.setPixelSize(11)
            p.setFont(f)
            p.drawText(QtCore.QPointF(r.right() - 48, r.top() + 12), "frozen")

    def _grip(self) -> float:
        """A corner handle's side in scene units: HANDLE pixels on the screen, less on a small figure."""
        s = self.size()
        return min(self.canvas.handle(), s.width() / 3, s.height() / 3)

    def _corner(self, pos: QtCore.QPointF) -> str:
        """The corner `pos` is on, to resize by: "nw", "ne", "sw" or "se" ("" for none). The
        bottom right one always; the others once the figure is picked (their handles show)."""
        s, h = self.size(), max(self._grip() * 1.5, 12.0 if not self.isSelected() else 0.0)
        ns = "n" if pos.y() < h else "s" if pos.y() > s.height() - h else ""
        ew = "w" if pos.x() < h else "e" if pos.x() > s.width() - h else ""
        at = ns + ew if ns and ew else ""
        return at if at == "se" or self.isSelected() else ""

    def hoverMoveEvent(self, ev):
        at = self._corner(ev.pos())
        self.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor if at in ("nw", "se")
                       else QtCore.Qt.CursorShape.SizeBDiagCursor if at
                       else QtCore.Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, ev):
        left = ev.button() == QtCore.Qt.MouseButton.LeftButton
        at = self._corner(ev.pos()) if left else ""
        if at:
            self._resizing = (at, ev.scenePos(), self.pos(), self.spec["w_mm"], self.spec["h_mm"])
            self.setSelected(True)
            ev.accept()
            return
        if left:
            self.canvas.moving = self                   # dragged: it catches where edges line up (itemChange)
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._resizing:
            at, p0, pos, w0, h0 = self._resizing
            dx, dy = (ev.scenePos().x() - p0.x()) / PX, (ev.scenePos().y() - p0.y()) / PX      # mm
            w = max(15.0, round(w0 + (dx if "e" in at else -dx)))
            if self.spec.get("kind") == "image" or ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                h = round(w * h0 / w0, 1)               # a picture keeps its shape (any figure does, with Shift)
            else:
                h = max(15.0, round(h0 + (dy if "s" in at else -dy)))
            self.prepareGeometryChange()
            self.spec["w_mm"], self.spec["h_mm"] = w, h
            self.setPos(pos.x() + ((w0 - w) * PX if "w" in at else 0.0),      # the corner across from it stays put
                        pos.y() + ((h0 - h) * PX if "n" in at else 0.0))
            self.update()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._resizing:
            self._resizing = None
            self.rerender()
            self.canvas.relabel()
            return
        super().mouseReleaseEvent(ev)
        self.canvas.relabel()

    def mouseDoubleClickEvent(self, ev):
        if self.spec.get("kind") != "image":
            self.canvas.edit(self)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            c = self.canvas
            if self._resizing:                                # (its far corner stays where it is)
                return value
            if c.moving is self:                              # dragged: whole mm, or caught on an edge
                return c.snapped(self, value)
            step = PX if c.dragging else PX / 100             # dragged along with others: whole mm; put there: as asked
            return QtCore.QPointF(round(value.x() / step) * step, round(value.y() / step) * step)
        return super().itemChange(change, value)

    def state(self) -> dict:
        return {"spec": self.spec, "data": self.data, "linked": self.linked,
                "x_mm": round(self.pos().x() / PX, 2), "y_mm": round(self.pos().y() / PX, 2)}


# ------------------------------------------------------------------------------- annotations
TOOLS = (("select", "Select", "Pick, move and resize things (Delete removes what's picked)"),
         ("text", "Text", "Click to type a label; double-click a label to change it"),
         ("line", "Line", "Drag to draw a line"),
         ("arrow", "Arrow", "Drag to draw an arrow, pointing where you let go"),
         ("rect", "Box", "Drag to draw a box"),
         ("ellipse", "Ellipse", "Drag to draw an ellipse or circle"))


def pt_px(pt: float) -> float:
    """Points (1/72 in) to scene units."""
    return pt / 72 * 25.4 * PX


class AnnText(QtWidgets.QGraphicsTextItem):
    """A label on the page: drag it to move it, double-click to change the words."""

    def __init__(self, canvas: "PlotCanvasPage", text: str, size: float, color: str, bold: bool = False):
        super().__init__(text)
        self.canvas, self.size, self.color, self.bold = canvas, size, color, bold
        self.setFlags(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(50)
        self.restyle()

    def restyle(self) -> None:
        f = QtGui.QFont()
        f.setPixelSize(max(1, int(round(pt_px(self.size)))))
        f.setBold(self.bold)
        self.setFont(f)
        self.setDefaultTextColor(QtGui.QColor(self.color))

    def mouseDoubleClickEvent(self, ev):
        self.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus()
        super().mouseDoubleClickEvent(ev)

    def focusOutEvent(self, ev):
        self.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)
        super().focusOutEvent(ev)
        if not self.toPlainText().strip():               # emptied: gone
            self.canvas.remove_annotation(self)
        self.canvas.changed()

    def state(self) -> dict:
        return {"kind": "text", "x_mm": self.pos().x() / PX, "y_mm": self.pos().y() / PX, "text": self.toPlainText(),
                "size": self.size, "color": self.color, "bold": self.bold}

    def put(self, d: dict) -> None:
        """As a state() had it (Undo)."""
        self.size, self.color, self.bold = float(d.get("size", 10)), d.get("color", "#000000"), bool(d.get("bold"))
        if self.toPlainText() != d.get("text", ""):
            self.setPlainText(d.get("text", ""))
        self.setPos(d["x_mm"] * PX, d["y_mm"] * PX)
        self.restyle()


class AnnShape(QtWidgets.QGraphicsObject):
    """A line, arrow, box or ellipse, from one corner (or end) to the other, in scene units.
    Caught by its line, not by what's inside it (a box round a figure leaves the figure to be
    picked); once picked, pulled by its ends or corners to reshape it (Shift: see constrained)."""

    def __init__(self, canvas: "PlotCanvasPage", kind: str, p0: QtCore.QPointF, p1: QtCore.QPointF,
                 color: str, width: float):
        super().__init__()
        self.canvas, self.kind, self.color, self.width = canvas, kind, color, width
        self.p0, self.p1 = QtCore.QPointF(p0), QtCore.QPointF(p1)
        self._pull: tuple[bool, bool] | None = None      # the handle being pulled: its x and y are p1's (else p0's)
        self.setFlags(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setZValue(40)

    def set_end(self, p1: QtCore.QPointF) -> None:
        self.prepareGeometryChange()
        self.p1 = QtCore.QPointF(p1)
        self.update()

    def put(self, d: dict) -> None:
        """As a state() had it (Undo)."""
        self.prepareGeometryChange()
        self.setPos(0, 0)
        self.p0, self.p1 = QtCore.QPointF(d["x0"] * PX, d["y0"] * PX), QtCore.QPointF(d["x1"] * PX, d["y1"] * PX)
        self.color, self.width = d.get("color", "#000000"), float(d.get("width", 1.0))
        self.update()

    def handles(self) -> list[tuple[QtCore.QPointF, tuple[bool, bool]]]:
        """Where it's pulled to reshape it: a line's two ends, a box's or an ellipse's four
        corners; with each, whether its x and its y are p1's (else p0's)."""
        if self.kind in ("line", "arrow"):
            return [(self.p0, (False, False)), (self.p1, (True, True))]
        return [(QtCore.QPointF((self.p1 if ix else self.p0).x(), (self.p1 if iy else self.p0).y()), (ix, iy))
                for ix in (False, True) for iy in (False, True)]

    def _handle_at(self, pos: QtCore.QPointF) -> tuple[bool, bool] | None:
        if not self.isSelected():
            return None
        h = self.canvas.handle() / 2 + 1
        return next((which for at, which in self.handles()
                     if abs(pos.x() - at.x()) <= h and abs(pos.y() - at.y()) <= h), None)

    def boundingRect(self):
        m = pt_px(self.width) + pt_px(6) + 8             # (its line's width, an arrow's head, a handle)
        return QtCore.QRectF(self.p0, self.p1).normalized().adjusted(-m, -m, m, m)

    def shape(self):
        path = QtGui.QPainterPath()
        if self.kind in ("line", "arrow"):
            path.moveTo(self.p0)
            path.lineTo(self.p1)
        elif self.kind == "rect":
            path.addRect(QtCore.QRectF(self.p0, self.p1).normalized())
        else:
            path.addEllipse(QtCore.QRectF(self.p0, self.p1).normalized())
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(max(pt_px(self.width), self.canvas.handle()))
        out = stroker.createStroke(path)
        if self.isSelected():
            h = self.canvas.handle()
            for at, _ in self.handles():
                out.addRect(QtCore.QRectF(at.x() - h / 2, at.y() - h / 2, h, h))
        return out

    def hoverMoveEvent(self, ev):
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor if self._handle_at(ev.pos()) is not None
                       else QtCore.Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, ev):
        which = self._handle_at(ev.pos()) if ev.button() == QtCore.Qt.MouseButton.LeftButton else None
        if which is not None:
            self._pull = which
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._pull is None:
            return super().mouseMoveEvent(ev)
        ix, iy = self._pull
        at = ev.pos()                                    # the end or corner across from it stays put
        still = QtCore.QPointF((self.p0 if ix else self.p1).x(), (self.p0 if iy else self.p1).y())
        if ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
            at = constrained(self.kind, still, at)
        self.prepareGeometryChange()
        (self.p1 if ix else self.p0).setX(at.x())
        (self.p1 if iy else self.p0).setY(at.y())
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._pull is None:
            return super().mouseReleaseEvent(ev)
        self._pull = None

    def paint(self, p, opt, widget=None):
        pen = QtGui.QPen(QtGui.QColor(self.color), pt_px(self.width))
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        r = QtCore.QRectF(self.p0, self.p1).normalized()
        if self.kind in ("line", "arrow"):
            p.drawLine(self.p0, self.p1)
            if self.kind == "arrow":
                d = self.p1 - self.p0
                L = (d.x() ** 2 + d.y() ** 2) ** 0.5 or 1.0
                ux, uy = d.x() / L, d.y() / L
                head = pt_px(4 + 2 * self.width)
                a = QtCore.QPointF(self.p1.x() - head * (ux - 0.5 * uy), self.p1.y() - head * (uy + 0.5 * ux))
                b = QtCore.QPointF(self.p1.x() - head * (ux + 0.5 * uy), self.p1.y() - head * (uy - 0.5 * ux))
                p.setBrush(QtGui.QColor(self.color))
                p.drawPolygon(QtGui.QPolygonF([self.p1, a, b]))
        elif self.kind == "rect":
            p.drawRect(r)
        else:
            p.drawEllipse(r)
        if self.isSelected():
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            if self.kind not in ("line", "arrow"):
                p.setPen(QtGui.QPen(QtGui.QColor(BLUE), 0, QtCore.Qt.PenStyle.DashLine))
                p.drawRect(r)
            p.setPen(QtGui.QPen(QtGui.QColor(BLUE), 0))
            p.setBrush(QtGui.QColor("white"))
            h = self.canvas.handle()
            for at, _ in self.handles():                 # an end or a corner, to pull
                p.drawRect(QtCore.QRectF(at.x() - h / 2, at.y() - h / 2, h, h))

    def state(self) -> dict:
        o = self.pos()
        return {"kind": self.kind, "x0": (self.p0.x() + o.x()) / PX, "y0": (self.p0.y() + o.y()) / PX,
                "x1": (self.p1.x() + o.x()) / PX, "y1": (self.p1.y() + o.y()) / PX,
                "color": self.color, "width": self.width}


class _View(QtWidgets.QGraphicsView):
    def __init__(self, scene, canvas):
        super().__init__(scene)
        self.canvas = canvas
        self.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        self.setBackgroundBrush(QtGui.QColor("#d9dce1"))
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._pan = None

    def zoom_by(self, k: float) -> None:
        """Zoom in (k > 1) or out, around the mouse, between a tenth and 8× the page's fit."""
        now = self.transform().m11()
        k = max(0.05 / now, min(8.0 / now, k))
        self.scale(k, k)
        self.canvas.zoomed()

    def wheelEvent(self, ev):
        # ⌘/Ctrl + scroll zooms (a trackpad pinch too, below); plain scrolling moves round the page
        if ev.modifiers() & (QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.MetaModifier):
            self.zoom_by(1.0015 ** ev.angleDelta().y())
        else:
            super().wheelEvent(ev)

    def viewportEvent(self, ev):
        if ev.type() == QtCore.QEvent.Type.NativeGesture and \
                ev.gestureType() == QtCore.Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom_by(1.0 + ev.value())              # a pinch on the trackpad
            return True
        return super().viewportEvent(ev)

    def mousePressEvent(self, ev):
        tool = self.canvas.tool()
        if ev.button() == QtCore.Qt.MouseButton.LeftButton and tool != "select":
            at = self.mapToScene(ev.position().toPoint())
            self.canvas.start_drawing(tool, at)
            return
        if ev.button() == QtCore.Qt.MouseButton.MiddleButton:    # the middle button drags the page about
            self._pan = ev.position()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            return
        self.canvas.dragging = ev.button() == QtCore.Qt.MouseButton.LeftButton
        super().mousePressEvent(ev)

    def _drawn_to(self, ev) -> QtCore.QPointF:
        """Where the shape being drawn ends, at the mouse (Shift: see constrained)."""
        at, d = self.mapToScene(ev.position().toPoint()), self.canvas.drawing
        return constrained(d.kind, d.p0, at) if ev.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier else at

    def mouseMoveEvent(self, ev):
        if self.canvas.drawing is not None:
            self.canvas.drawing.set_end(self._drawn_to(ev))
            return
        if self._pan is not None:
            d = ev.position() - self._pan
            self._pan = ev.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self.canvas.drawing is not None:
            self.canvas.end_drawing(self._drawn_to(ev))
            return
        if self._pan is not None:
            self._pan = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(ev)
        self.canvas.dropped()

    def keyPressEvent(self, ev):
        focus = self.scene().focusItem()
        if isinstance(focus, AnnText) and focus.textInteractionFlags() != QtCore.Qt.TextInteractionFlag.NoTextInteraction:
            return super().keyPressEvent(ev)            # typing in a label
        K, M = QtCore.Qt.Key, QtCore.Qt.KeyboardModifier
        step = {K.Key_Left: (-1, 0), K.Key_Right: (1, 0), K.Key_Up: (0, -1), K.Key_Down: (0, 1)}.get(ev.key())
        if ev.matches(QtGui.QKeySequence.StandardKey.Undo):
            self.canvas.undo()
        elif ev.matches(QtGui.QKeySequence.StandardKey.Redo):
            self.canvas.redo()
        elif ev.key() in (K.Key_Delete, K.Key_Backspace):
            self.canvas.remove_picked()
        elif ev.key() == K.Key_Escape:
            self.canvas.set_tool("select")
        elif step and self.canvas.nudge(*step, 10.0 if ev.modifiers() & M.ShiftModifier
                                        else 0.1 if ev.modifiers() & M.AltModifier else 1.0):
            pass                                        # the arrows move what's picked (else the page, below)
        else:
            super().keyPressEvent(ev)


class PlotCanvasPage(QtWidgets.QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        bar = QtWidgets.QHBoxLayout()
        self.paper = QtWidgets.QComboBox()
        self.paper.addItems([*PAPERS, CUSTOM])
        self.paper.setToolTip("The page's size: a paper, or Custom for a size of your own")
        self.page_w, self.page_h = QtWidgets.QSpinBox(), QtWidgets.QSpinBox()
        for box, what in ((self.page_w, "width"), (self.page_h, "height")):
            box.setRange(*SIDE_MM)
            box.setSuffix(" mm")
            box.setKeyboardTracking(False)                # once it's typed, not at every digit
            box.setToolTip(f"The page's {what}")
        self.letters = QtWidgets.QCheckBox("Panel letters")
        self.letters.setChecked(True)
        self.btn_edit = QtWidgets.QPushButton("Edit in Single Plot")
        self.btn_freeze = QtWidgets.QPushButton("Freeze")
        self.btn_freeze.setToolTip("Stop following the DataPad: keep the numbers the figure shows now. "
                                   "Again to follow it again")
        self.btn_dup = QtWidgets.QPushButton("Duplicate")
        self.btn_del = QtWidgets.QPushButton("Remove")
        self.btn_image = QtWidgets.QPushButton("Add image…")
        self.btn_image.setToolTip("Put a picture on the page: PNG, JPEG, TIFF, SVG or PDF")
        self.btn_out = QtWidgets.QToolButton(text="−")
        self.btn_out.setToolTip("Zoom out (⌘/Ctrl and scroll, or pinch)")
        self.zoom_label = QtWidgets.QLabel("100%")
        self.zoom_label.setMinimumWidth(40)
        self.zoom_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.btn_in = QtWidgets.QToolButton(text="+")
        self.btn_in.setToolTip("Zoom in (⌘/Ctrl and scroll, or pinch)")
        self.btn_fit = QtWidgets.QPushButton("Fit page")
        self.btn_export = QtWidgets.QPushButton("Export…")
        # one text size for every figure on the page: the same number prints the same everywhere
        self.text_pt = QtWidgets.QDoubleSpinBox(decimals=1, minimum=4, maximum=36, value=9, singleStep=0.5,
                                                suffix=" pt")
        self.text_pt.setToolTip("The axis labels of every figure on the page, in pt as they print; each "
                                "figure's numbers and title keep their size against them. Changing one "
                                "figure's labels in Single Plot changes them all")
        self._text_timer = QtCore.QTimer(self, singleShot=True, interval=200)     # redraw once you stop
        self._text_timer.timeout.connect(lambda: self.set_text(self.text_pt.value()))
        self._quiet = False                               # the box set from here: don't redraw again
        self.links = dict(LINKS)                          # sections the same on every figure
        self.page_look: dict = {}                         # their settings
        self._restoring = False                           # figures put back as they were saved
        self.text_pt.valueChanged.connect(lambda *_: None if self._quiet else self._text_timer.start())
        text_row = with_slider(self.text_pt)
        text_row.setMinimumWidth(170)
        bar.addWidget(QtWidgets.QLabel("Page"))
        bar.addWidget(self.paper)
        bar.addWidget(self.page_w)
        bar.addWidget(QtWidgets.QLabel("×"))
        bar.addWidget(self.page_h)
        bar.addWidget(self.letters)
        bar.addSpacing(8)
        bar.addWidget(QtWidgets.QLabel("Text"))
        bar.addWidget(text_row)
        bar.addSpacing(12)
        for b in (self.btn_edit, self.btn_freeze, self.btn_dup, self.btn_del):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_image)
        for w in (self.btn_out, self.zoom_label, self.btn_in):
            bar.addWidget(w)
        bar.addWidget(self.btn_fit)
        bar.addWidget(self.btn_export)
        lay.addLayout(bar)
        # the annotation tools: text and shapes drawn on the page, over the figures
        draw = QtWidgets.QHBoxLayout()
        draw.addWidget(QtWidgets.QLabel("Draw"))
        self.tools = QtWidgets.QButtonGroup(self)
        self._tool_btn = {}
        for key, label, tip in TOOLS:
            b = QtWidgets.QToolButton(text=label)
            b.setCheckable(True)
            b.setToolTip(tip)
            self.tools.addButton(b)
            self._tool_btn[key] = b
            draw.addWidget(b)
        self._tool_btn["select"].setChecked(True)
        self.ann_color = "#000000"
        self.btn_color = QtWidgets.QToolButton()
        self.btn_color.setToolTip("The color of new drawings, and of the ones picked")
        self.btn_color.clicked.connect(self._pick_color)
        self.ann_width = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0.25, maximum=12, value=1.0, singleStep=0.25,
                                                  suffix=" pt")
        self.ann_width.setToolTip("Line width of new lines and shapes, and of the ones picked")
        self.ann_size = QtWidgets.QDoubleSpinBox(decimals=1, minimum=4, maximum=72, value=10, singleStep=1,
                                                 suffix=" pt")
        self.ann_size.setToolTip("Text size of new labels, and of the ones picked")
        self.ann_bold = QtWidgets.QToolButton(text="B")
        self.ann_bold.setCheckable(True)
        self.ann_bold.setToolTip("Bold labels")
        for lab, w in (("Color", self.btn_color), ("Width", self.ann_width), ("Text", self.ann_size)):
            draw.addSpacing(6)
            draw.addWidget(QtWidgets.QLabel(lab))
            draw.addWidget(w)
        draw.addWidget(self.ann_bold)
        draw.addStretch(1)
        self.btn_align = QtWidgets.QToolButton(text="Align")
        self.btn_align.setToolTip("Line up the figures picked, or space them out evenly (pick two or more)")
        self.btn_align.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(self.btn_align)
        self._align_acts = {}
        for row in ALIGN:
            if row is None:
                menu.addSeparator()
                continue
            self._align_acts[row[0]] = menu.addAction(row[1])
            self._align_acts[row[0]].triggered.connect(lambda _=False, how=row[0]: self.align(how))
        menu.addSeparator()
        self.act_plots = menu.addAction("Line up the plots, not the figures' edges")
        self.act_plots.setCheckable(True)
        self.act_plots.setChecked(True)
        self.btn_align.setMenu(menu)
        self.btn_undo, self.btn_redo = QtWidgets.QToolButton(text="Undo"), QtWidgets.QToolButton(text="Redo")
        for b, key, what in ((self.btn_undo, QtGui.QKeySequence.StandardKey.Undo, "Take back the last change to the page"),
                             (self.btn_redo, QtGui.QKeySequence.StandardKey.Redo, "Put back what Undo took away")):
            keys = QtGui.QKeySequence(key).toString(QtGui.QKeySequence.SequenceFormat.NativeText)
            b.setToolTip(f"{what} ({keys})")
        draw.addWidget(self.btn_align)
        draw.addSpacing(6)
        draw.addWidget(self.btn_undo)
        draw.addWidget(self.btn_redo)
        lay.addLayout(draw)
        self.moving: Panel | None = None                  # the figure the mouse is dragging: it catches on edges
        self.dragging = False                             # the mouse is down on the page, in Select
        self._todo: list[Panel] = []                      # figures to draw again, one at a time (draw_soon)
        self._draw_timer = QtCore.QTimer(self, singleShot=True, interval=0)
        self._draw_timer.timeout.connect(self._draw_next)
        self._sharp = QtCore.QTimer(self, singleShot=True, interval=200)       # once a zoom settles
        self._sharp.timeout.connect(self._sharpen)
        self._undo: list[dict] = []                       # the page before each change (see _now), and those undone
        self._redo: list[dict] = []
        self._was: dict | None = None                     # the page as last kept
        self._undoing, self._tag, self._tag_at = False, None, 0.0
        self._show_color()
        self.ann_width.valueChanged.connect(lambda v: self._restyle_picked(width=v))
        self.ann_size.valueChanged.connect(lambda v: self._restyle_picked(size=v))
        self.ann_bold.toggled.connect(lambda on: self._restyle_picked(bold=on))
        self.tools.buttonClicked.connect(lambda *_: self._tool_changed())
        self.annotations: list = []
        self.drawing: AnnShape | None = None
        self.scene = QtWidgets.QGraphicsScene(self)
        self.view = _View(self.scene, self)
        lay.addWidget(self.view, 1)
        self.sheet = self.scene.addRect(QtCore.QRectF(), QtGui.QPen(QtGui.QColor("#b0b4ba")),
                                        QtGui.QBrush(QtGui.QColor("white")))
        self.sheet.setZValue(-100)
        self.hint = self.scene.addText("Send figures here from Single Plot (Send to Canvas).")
        self.hint.setDefaultTextColor(QtGui.QColor("#888"))
        self._guides = [self.scene.addLine(QtCore.QLineF(), QtGui.QPen(QtGui.QColor(GUIDE), 0)) for _ in (0, 1)]
        for g in self._guides:                            # shown where a dragged figure lines up (snapped)
            g.setZValue(200)
            g.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            g.hide()
        self.panels: list[Panel] = []
        self.paper.currentTextChanged.connect(self._paper)
        self.page_w.valueChanged.connect(lambda *_: self.set_page(size=(self.page_w.value(), self.page_h.value())))
        self.page_h.valueChanged.connect(lambda *_: self.set_page(size=(self.page_w.value(), self.page_h.value())))
        self.letters.toggled.connect(lambda *_: (self.scene.update(), self.changed()))
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)
        self.btn_edit.clicked.connect(lambda: self.edit(self._one()))
        self.btn_freeze.clicked.connect(self._freeze)
        self.btn_dup.clicked.connect(self._duplicate)
        self.btn_del.clicked.connect(self.remove_picked)
        self.btn_fit.clicked.connect(self.fit)
        self.btn_in.clicked.connect(lambda: self._zoom_center(1.25))
        self.btn_out.clicked.connect(lambda: self._zoom_center(0.8))
        self.btn_export.clicked.connect(self.export)
        self.btn_image.clicked.connect(self.add_image_dialog)
        self.view.setAcceptDrops(True)
        self.setAcceptDrops(True)
        self.scene.selectionChanged.connect(self._picked)
        self._paper(self.paper.currentText())
        self._picked()
        self._fresh()

    def _paper(self, name: str) -> None:
        if name in PAPERS:                                # Custom: the size stays, to type your own
            self.set_page(name)

    def set_page(self, paper: str | None = None, size=None) -> None:
        """The page as a paper, or a width and height of its own (mm)."""
        name, (w, h) = figpage.page_of(paper, size)
        for box, put, v in ((self.paper, self.paper.setCurrentText, name), (self.page_w, self.page_w.setValue, int(w)),
                            (self.page_h, self.page_h.setValue, int(h))):
            box.blockSignals(True)                        # shown, not asked for again
            put(v)
            box.blockSignals(False)
        self.sheet.setRect(0, 0, w * PX, h * PX)
        self.scene.setSceneRect(-40, -40, w * PX + 80, h * PX + 80)
        self.hint.setPos(MARGIN * PX, MARGIN * PX)
        self.fit()
        self.changed("page")

    def paper_mm(self) -> tuple[float, float]:
        r = self.sheet.rect()
        return r.width() / PX, r.height() / PX

    def _zoom_center(self, k: float) -> None:
        self.view.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.view.zoom_by(k)
        self.view.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def fit(self) -> None:
        self.view.fitInView(self.sheet, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        self._fit_scale = self.view.transform().m11()     # 100%: the whole page in view
        self.zoomed()

    def zoomed(self) -> None:
        """The zoom changed: say how much now; draw the figures sharp at it once it settles."""
        fit = getattr(self, "_fit_scale", None) or self.view.transform().m11()
        self.zoom_label.setText(f"{self.view.transform().m11() / fit * 100:.0f}%")
        self._sharp.start()

    def showEvent(self, ev):
        super().showEvent(ev)
        QtCore.QTimer.singleShot(0, self.fit)
        self._draw_timer.start()                          # what changed while it was out of sight

    # -- drawing the figures: one at a time, between what the mouse and the keys ask for ---------
    def draw_soon(self, p: Panel) -> None:
        """This figure drawn again when there's a moment: never several in a row with the window
        stuck, and not at all while the page is out of sight."""
        if p not in self._todo:
            self._todo.append(p)
        if self.isVisible():
            self._draw_timer.start()

    def _draw_next(self) -> None:
        self._todo = [p for p in self._todo if p in self.panels]
        if not self._todo or not self.isVisible():
            return
        if QtWidgets.QApplication.mouseButtons() != QtCore.Qt.MouseButton.NoButton:
            self._draw_timer.start(60)                    # mid-drag: after it
            return
        seen = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        p = next((q for q in self._todo if q.sceneBoundingRect().intersects(seen)), self._todo[0])
        self._todo.remove(p)                              # the ones in view first
        p.draw_now()
        if self._todo:
            self._draw_timer.start(0)

    def _sharpen(self) -> None:
        """The zoom settled: the figures that would look soft at it (or have far more pixels
        than it shows) are drawn again; the rest are left as they are."""
        for p in self.panels:
            if p.soft():
                self.draw_soon(p)

    def handle(self) -> float:
        """A handle's side in scene units: HANDLE pixels on the screen at this zoom (16 at most)."""
        return min(HANDLE / max(self.view.transform().m11(), 1e-6), 16.0)

    # -- lining figures up -------------------------------------------------------------------
    def snapped(self, p: Panel, pos: QtCore.QPointF) -> QtCore.QPointF:
        """Where a figure dragged to `pos` settles: on whole mm, or caught where its sides or its
        middle (or its plot's sides) line up with another figure's, the page's margins or its
        middle, a line showing which. Not with Alt held, nor with other things picked too."""
        out, shown = [round(pos.x() / PX) * PX, round(pos.y() / PX) * PX], [None, None]
        free = QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.KeyboardModifier.AltModifier
        if not free and len(self.scene.selectedItems()) <= 1:
            near = SNAP / max(self.view.transform().m11(), 1e-6)
            page = (self.sheet.rect().width(), self.sheet.rect().height())
            for k in (0, 1):
                at = pos.x() if k == 0 else pos.y()
                there = [(MARGIN * PX, "side"), (page[k] - MARGIN * PX, "side"), (page[k] / 2, "mid")]
                there += [ln for q in self.panels if q is not p for ln in q.lines(k)]
                best = None
                for mine, kind in p.lines(k, at):
                    for v, kind2 in there:
                        if kind == kind2 and abs(v - mine) <= near and (best is None or abs(v - mine) < abs(best[0])):
                            best = (v - mine, v)
                if best is not None:
                    out[k], shown[k] = round((at + best[0]) / PX, 2) * PX, best[1]
        self._show_guides(shown)
        return QtCore.QPointF(*out)

    def _show_guides(self, at) -> None:
        r = self.sheet.rect()
        for k, (g, v) in enumerate(zip(self._guides, at)):
            g.setVisible(v is not None)
            if v is not None:
                g.setLine(*((v, r.top(), v, r.bottom()) if k == 0 else (r.left(), v, r.right(), v)))

    def dropped(self) -> None:
        """The mouse let go: nothing is being dragged now, and what it changed is kept, to undo."""
        self.moving, self.dragging = None, False
        self._show_guides((None, None))
        self.changed()

    def nudge(self, dx: int, dy: int, mm: float) -> bool:
        """What's picked moved `mm` across or down (the arrow keys); False if nothing is."""
        got = [it for it in self.scene.selectedItems() if it in self.panels or it in self.annotations]
        for it in got:
            it.moveBy(dx * mm * PX, dy * mm * PX)
        if got:
            self.relabel()
            self.changed("nudge")
        return bool(got)

    def align(self, how: str) -> None:
        """The figures picked lined up (ALIGN: by their plots if that's ticked, else their outer
        edges), or spaced with even gaps between the first and the last."""
        got = self.picked()
        if len(got) < 2:
            return
        by_plot = self.act_plots.isChecked() and how not in ("across", "down")

        def box(p: Panel) -> tuple[float, float, float, float]:     # left, top, right, bottom (scene units)
            s, x, y = p.size(), p.pos().x(), p.pos().y()
            if by_plot and p.axes is not None:
                x0, y0, w, h = p.axes
                return (x + x0 * s.width(), y + (1 - y0 - h) * s.height(),
                        x + (x0 + w) * s.width(), y + (1 - y0) * s.height())
            return x, y, x + s.width(), y + s.height()

        boxes = [box(p) for p in got]
        if how in ("across", "down"):
            k = 0 if how == "across" else 1
            order = sorted(range(len(got)), key=lambda i: boxes[i][k])
            room = max(b[k + 2] for b in boxes) - boxes[order[0]][k] - sum(b[k + 2] - b[k] for b in boxes)
            at, gap = boxes[order[0]][k], room / (len(got) - 1)
            moves = []
            for i in order:
                moves.append((got[i], at - boxes[i][k]))
                at += boxes[i][k + 2] - boxes[i][k] + gap
        else:
            k, f = {"left": (0, 0.0), "center": (0, 0.5), "right": (0, 1.0),
                    "top": (1, 0.0), "middle": (1, 0.5), "bottom": (1, 1.0)}[how]
            lo, hi = min(b[k] for b in boxes), max(b[k + 2] for b in boxes)
            to = lo + f * (hi - lo)
            moves = [(p, to - (b[k] + f * (b[k + 2] - b[k]))) for p, b in zip(got, boxes)]
        for p, d in moves:
            p.moveBy(d if k == 0 else 0.0, d if k == 1 else 0.0)
        self.relabel()
        self.changed()

    # -- undo ----------------------------------------------------------------------------------
    def _now(self) -> dict:
        """The page as it is, to come back to: its figures and drawings themselves, each with
        what it has now (a figure's numbers aren't copied: they're replaced, never changed)."""
        return {"paper": self.paper.currentText(), "size": self.paper_mm(), "letters": self.letters.isChecked(),
                "text_pt": self.text_pt.value(), "links": dict(self.links), "look": dict(self.page_look),
                "panels": [(p, F.clone(p.spec), p.data, p.linked, p.pos().x(), p.pos().y()) for p in self.panels],
                "anns": [(a, a.state()) for a in self.annotations]}

    @staticmethod
    def _same(a: dict, b: dict) -> bool:
        pa, pb = a["panels"], b["panels"]
        return (len(pa) == len(pb)
                and all(x[0] is y[0] and x[2] is y[2] and x[1] == y[1] and x[3:] == y[3:] for x, y in zip(pa, pb))
                and all(a[k] == b[k] for k in a if k != "panels"))

    def changed(self, tag: str | None = None) -> None:
        """Something on the page was changed by hand: how it was before is kept, to undo.
        Changes with the same `tag` within a second of each other (the arrow keys, a size being
        dialled) are one step."""
        if self._was is None or self._undoing:
            return
        now = self._now()
        if self._same(now, self._was):
            return
        t = time.monotonic()
        if not (tag and tag == self._tag and t - self._tag_at < 1.0 and self._undo):
            self._undo.append(self._was)
            del self._undo[:-STEPS]
        self._tag, self._tag_at, self._was = tag, t, now
        self._redo.clear()
        self._show_undo()

    def undo(self) -> None:
        self.changed()                                    # (anything not kept yet)
        if self._undo:
            self._redo.append(self._was)
            self._put(self._undo.pop())

    def redo(self) -> None:
        if self._redo:
            self._undo.append(self._was)
            self._put(self._redo.pop())

    def _put(self, s: dict) -> None:
        """The page as it was at `s` (a _now): the same figures and drawings put back as they
        were. Linked figures then follow the DataPad as it is now."""
        self._undoing = True
        try:
            if s["paper"] != self.paper.currentText() or tuple(s["size"]) != self.paper_mm():
                self.set_page(s["paper"], s["size"])
            self.letters.setChecked(s["letters"])
            self._show_text(s["text_pt"])
            self.links, self.page_look = dict(s["links"]), dict(s["look"])
            for old, new in ((self.panels, [r[0] for r in s["panels"]]), (self.annotations, [r[0] for r in s["anns"]])):
                for it in old:
                    if it not in new:
                        self.scene.removeItem(it)
                for it in new:
                    if it.scene() is None:
                        self.scene.addItem(it)
            self.panels, self.annotations = [r[0] for r in s["panels"]], [r[0] for r in s["anns"]]
            for p, spec, data, linked, x, y in s["panels"]:
                if p.spec != spec or p.data is not data:
                    p.spec, p.data = F.clone(spec), data
                    p.rerender()
                elif p.soft():
                    self.draw_soon(p)
                p.linked = linked
                p.setPos(x, y)
            for a, state in s["anns"]:
                a.put(state)
        finally:
            self._undoing = False
        self.moving, self._tag = None, None
        self.relabel()
        self.data_changed()
        self._was = self._now()
        self._picked()
        self._show_undo()

    def _fresh(self) -> None:
        """A page just opened: nothing to undo yet."""
        self._undo, self._redo, self._tag = [], [], None
        self._was = self._now()
        self._show_undo()

    def _show_undo(self) -> None:
        self.btn_undo.setEnabled(bool(self._undo))
        self.btn_redo.setEnabled(bool(self._redo))

    # -- panels -----------------------------------------------------------------------------
    def add_panel(self, spec: dict, data: dict, at: tuple[float, float] | None = None,
                  linked: bool = True, edited: "Panel | None" = None) -> Panel:
        spec = F.on_page(spec, data)
        charts = [q for q in self.panels if q.spec.get("kind") != "image" and q is not edited]
        if spec.get("kind") != "image" and not self._restoring:     # the linked sections, the same everywhere
            keys = self.linked_keys()
            if edited is not None and edited in self.panels:
                changed = {k: spec.get(k) for k in keys if k in spec and spec.get(k) != edited.spec.get(k)}
                if changed:
                    self.set_look(changed, but=edited)    # you changed this one's: the others follow
            elif not charts:
                self._take_look(spec)                     # the first figure sets the page's
            else:
                spec = {**spec, **{k: v for k, v in self.page_look.items() if k in keys}}
        if edited is not None and edited in self.panels:
            edited.spec, edited.data = spec, data
            edited.rerender()
            self.relabel()
            self.changed("add")
            return edited
        p = Panel(self, spec, data, linked)
        self.scene.addItem(p)
        x, y = at if at else self._free_spot(spec)
        p.setPos(x * PX, y * PX)
        self.panels.append(p)
        self.relabel()
        if not self._restoring:
            self.changed("add")
        return p

    # -- images -----------------------------------------------------------------------------
    def add_image_dialog(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Add images", "", "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.svg *.pdf)")
        for p in paths:
            self.add_image(p)

    def add_image(self, path: str) -> Panel | None:
        try:
            png, w, h = image_png(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Add image", f"Couldn't read {Path(path).name}: {e}")
            return None
        spec, data = F.image_spec(png, w, h, width_mm=min(100.0, self.paper_mm()[0] - 2 * MARGIN))
        spec["title"] = Path(path).name
        return self.add_panel(spec, data, linked=False)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        for url in ev.mimeData().urls():
            if url.isLocalFile():
                self.add_image(url.toLocalFile())

    def _free_spot(self, spec) -> tuple[float, float]:
        """The next place along the rows, left to right, top to bottom."""
        taken = [(p.pos().x() / PX, p.pos().y() / PX, p.spec["w_mm"], p.spec["h_mm"]) for p in self.panels]
        return figpage.free_spot(spec, taken, self.paper_mm())

    def relabel(self) -> None:
        for p, letter in zip(self.panels, figpage.letters([(p.pos().x() / PX, p.pos().y() / PX)
                                                         for p in self.panels])):
            p.letter = letter
        self.hint.setVisible(not self.panels)
        self.scene.update()

    def picked(self) -> list[Panel]:
        return [p for p in self.panels if p.isSelected()]

    def _one(self) -> Panel | None:
        got = self.picked()
        return got[0] if len(got) == 1 else None

    def _picked(self) -> None:
        n = len(self.picked())
        self.btn_edit.setEnabled(n == 1)
        for b in (self.btn_freeze, self.btn_dup):
            b.setEnabled(n > 0)
        self.btn_del.setEnabled(n > 0 or any(a.isSelected() for a in self.annotations))
        self.btn_align.setEnabled(n > 1)
        for how in ("across", "down"):                    # even gaps: between three or more
            self._align_acts[how].setEnabled(n > 2)

    def _show_text(self, pt: float) -> None:
        """The page's text size in its box (and slider), without redrawing for it."""
        self._quiet = True
        self.text_pt.setValue(pt)
        self._quiet = False

    def set_text(self, pt: float, but: "Panel | None" = None) -> None:
        """Every figure's labels at `pt` on the page (their numbers and titles keeping their size
        against them), but `but`, which already has them."""
        self._show_text(pt)
        for p in self.panels:
            if p is not but and p.spec.get("kind") != "image" and abs(float(p.spec.get("font") or 9) - pt) > 1e-6:
                p.spec = F.with_text(p.spec, pt)
                p.rerender()
        charts = [p for p in self.panels if p.spec.get("kind") != "image"]
        if charts and self.links.get("text"):
            self.page_look.update(F.look_of(charts[0].spec, ("text",)))
        self.scene.update()                               # the panel letters follow
        self.changed("text")

    # -- the look the figures share: sections linked across the page (figspec.SECTIONS) ----------
    def linked_keys(self) -> list[str]:
        return figpage.linked_keys(self.links)

    def _take_look(self, spec: dict) -> None:
        """The page's shared look from this figure (the first on the page)."""
        self.page_look = F.look_of(spec, [sec for sec, on in self.links.items() if on and sec in F.SECTIONS])
        if spec.get("font"):
            self._show_text(float(spec["font"]))

    def set_look(self, look: dict, but: "Panel | None" = None) -> None:
        """These look settings on every figure (but `but`, which has them already); those of
        linked sections become the page's, for figures added later."""
        keys = set(self.linked_keys())
        self.page_look.update({k: v for k, v in look.items() if k in keys})
        if look.get("font"):
            self._show_text(float(look["font"]))
        for p in self.panels:
            if p is but or p.spec.get("kind") == "image":
                continue
            new = {**p.spec, **look}
            if new != p.spec:
                p.spec = new
                p.rerender()
        self.scene.update()
        self.changed("look")

    def set_link(self, section: str, on: bool, look: dict | None = None) -> None:
        """Make a section the same on every figure (from `look`, the figure you're on, else the
        first), or let each figure keep its own."""
        self.links = {**self.links, section: bool(on)}
        self.changed("look")
        if not on:
            return
        if look is None:
            charts = [p for p in self.panels if p.spec.get("kind") != "image"]
            look = F.look_of(charts[0].spec, (section,)) if charts else {}
        if look:
            self.set_look(look)

    def edit(self, p: Panel | None) -> None:
        if p is None:
            return
        self.page.single_plot.editing = p
        self.page.single_plot.data = dict(p.data)
        self.page.single_plot.show_spec(F.clone(p.spec))
        self.page.show_sub("singleplot")

    def _freeze(self) -> None:
        got = self.picked()
        on = not all(not p.linked for p in got)
        for p in got:
            p.linked = not on
            p.update()
        self.changed()
        self.data_changed()

    def _duplicate(self) -> None:
        for p in self.picked():
            self.add_panel(F.clone(p.spec), dict(p.data), linked=p.linked)

    def remove_picked(self) -> None:
        for p in self.picked():
            self.scene.removeItem(p)
            self.panels.remove(p)
        for a in [a for a in self.annotations if a.isSelected()]:
            self.remove_annotation(a)
        self.relabel()
        self.changed()

    # -- annotations ------------------------------------------------------------------------
    def tool(self) -> str:
        return next((k for k, b in self._tool_btn.items() if b.isChecked()), "select")

    def set_tool(self, key: str) -> None:
        self._tool_btn[key].setChecked(True)
        self._tool_changed()

    def _tool_changed(self) -> None:
        drawing = self.tool() != "select"
        self.view.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag if drawing
                              else QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        self.view.viewport().setCursor(QtCore.Qt.CursorShape.CrossCursor if drawing
                                       else QtCore.Qt.CursorShape.ArrowCursor)

    def start_drawing(self, tool: str, at: QtCore.QPointF) -> None:
        if tool == "text":
            t = AnnText(self, "Text", self.ann_size.value(), self.ann_color, self.ann_bold.isChecked())
            self.scene.addItem(t)
            t.setPos(at)
            self.annotations.append(t)
            self.set_tool("select")
            t.setSelected(True)
            t.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextEditorInteraction)
            t.setFocus()
            cur = t.textCursor()
            cur.select(QtGui.QTextCursor.SelectionType.Document)
            t.setTextCursor(cur)
            return
        self.drawing = AnnShape(self, tool, at, at, self.ann_color, self.ann_width.value())
        self.scene.addItem(self.drawing)

    def end_drawing(self, at: QtCore.QPointF) -> None:
        d, self.drawing = self.drawing, None
        d.set_end(at)
        if (d.p1 - d.p0).manhattanLength() < 3:          # just a click: nothing drawn
            self.scene.removeItem(d)
            return
        self.annotations.append(d)
        self.scene.clearSelection()
        d.setSelected(True)
        self.changed()

    def remove_annotation(self, a) -> None:
        if a in self.annotations:
            self.annotations.remove(a)
            self.scene.removeItem(a)

    def _show_color(self) -> None:
        pix = QtGui.QPixmap(16, 16)
        pix.fill(QtGui.QColor(self.ann_color))
        self.btn_color.setIcon(QtGui.QIcon(pix))

    def _pick_color(self) -> None:
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.ann_color), self, "Drawing color")
        if c.isValid():
            self.ann_color = c.name()
            self._show_color()
            self._restyle_picked(color=self.ann_color)

    def _restyle_picked(self, **kw) -> None:
        for a in self.annotations:
            if not a.isSelected():
                continue
            if isinstance(a, AnnText):
                for k in ("color", "size", "bold"):
                    if k in kw:
                        setattr(a, k, kw[k])
                a.restyle()
            else:
                for k in ("color", "width"):
                    if k in kw:
                        setattr(a, k, kw[k])
                a.prepareGeometryChange()
                a.update()
        self.changed("style")

    def _add_annotation(self, d: dict) -> None:
        if d.get("kind") == "text":
            t = AnnText(self, d.get("text", ""), float(d.get("size", 10)), d.get("color", "#000000"),
                        bool(d.get("bold")))
            t.setPos(d["x_mm"] * PX, d["y_mm"] * PX)
            self.scene.addItem(t)
            self.annotations.append(t)
        else:
            a = AnnShape(self, d["kind"], QtCore.QPointF(d["x0"] * PX, d["y0"] * PX),
                         QtCore.QPointF(d["x1"] * PX, d["y1"] * PX), d.get("color", "#000000"),
                         float(d.get("width", 1.0)))
            self.scene.addItem(a)
            self.annotations.append(a)

    def _export_annotations(self, fig, W: float, H: float) -> None:
        """The drawings in the saved page: text as text, lines and shapes as vector objects."""
        figpage.draw_annotations(fig, [a.state() for a in self.annotations], W, H)

    def data_changed(self) -> None:
        """The DataPad changed: the linked figures follow."""
        pad = self.page.pad_view
        for p in self.panels:
            if p.spec.get("kind") in ("image", "curves") or not F.columns_used(p.spec):
                continue
            if p.linked and all(c in pad.pad.names() for c in F.columns_used(p.spec)):
                new = pad.snapshot(F.columns_used(p.spec))
                if new != p.data:                         # drawn again only if its numbers changed
                    p.data = new
                    p.rerender()
        if self._was is not None and not self._undoing:
            self._was = self._now()                       # the table's doing, not a change to undo

    # -- export -----------------------------------------------------------------------------
    def export(self) -> None:
        if not self.panels and not self.annotations:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export the page", "figure.pdf",
                                                        "PDF (*.pdf);;SVG (*.svg);;PNG (*.png)")
        if not path:
            return
        try:
            self.save(path)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Export", f"Couldn't save it: {e}")

    def save(self, path: str, dpi: int = 300) -> None:
        panels = [(p.spec, p.data, p.pos().x() / PX, p.pos().y() / PX, p.letter) for p in self.panels]
        figpage.save_page(path, self.paper_mm(), panels, [a.state() for a in self.annotations],
                          self.letters.isChecked(), dpi)

    # -- saving with the session ---------------------------------------------------------------
    def state(self) -> dict:
        return {"paper": self.paper.currentText(), "size": list(self.paper_mm()), "letters": self.letters.isChecked(),
                "text_pt": self.text_pt.value(), "links": dict(self.links), "look": dict(self.page_look),
                "panels": [p.state() for p in self.panels], "annotations": [a.state() for a in self.annotations]}

    def restore(self, s: dict | None) -> None:
        for p in list(self.panels):
            self.scene.removeItem(p)
        self.panels = []
        for a in list(self.annotations):
            self.scene.removeItem(a)
        self.annotations = []
        if not s:
            self.relabel()
            self._fresh()
            return
        self.set_page(s.get("paper"), s.get("size"))
        self.letters.setChecked(s.get("letters", True))
        if s.get("text_pt"):
            self._show_text(float(s["text_pt"]))
        self.links = {**LINKS, **{k: bool(v) for k, v in (s.get("links") or {}).items() if k in LINKS}}
        self.page_look = dict(s.get("look") or {})
        self._restoring = True                            # each as it was saved, not made alike again
        try:
            for d in s.get("panels", []):
                try:
                    self.add_panel(d["spec"], d["data"], (d["x_mm"], d["y_mm"]), d.get("linked", True))
                except (KeyError, TypeError):
                    continue
        finally:
            self._restoring = False
        for d in s.get("annotations", []):
            try:
                self._add_annotation(d)
            except (KeyError, TypeError, ValueError):
                continue
        self._fresh()


def image_png(path: str, dpi: int = 300) -> tuple[bytes, int, int]:
    """A picture file as PNG bytes and its size in pixels. SVG and PDF (its first page) are
    drawn at `dpi`."""
    ext = Path(path).suffix.lower()
    if ext == ".svg":
        from PyQt6 import QtSvg
        r = QtSvg.QSvgRenderer(path)
        if not r.isValid():
            raise ValueError("not an SVG NEWT can read")
        size = r.defaultSize()
        k = dpi / 96.0
        img = QtGui.QImage(int(size.width() * k), int(size.height() * k), QtGui.QImage.Format.Format_ARGB32)
        img.fill(QtGui.QColor("white"))
        p = QtGui.QPainter(img)
        r.render(p)
        p.end()
    elif ext == ".pdf":
        img = _pdf_page(path, dpi)
    else:
        img = QtGui.QImage(path)
        if img.isNull():
            raise ValueError("not a picture NEWT can read")
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data()), img.width(), img.height()


def _pdf_page(path: str, dpi: int) -> QtGui.QImage:
    img = QtGui.QImage.fromData(figpage.pdf_png(path, dpi))
    if img.isNull():
        raise ValueError("PDFs need the pypdfium2 package here (pip install pypdfium2)")
    return img
