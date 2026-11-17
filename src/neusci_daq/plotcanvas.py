"""The Plot Canvas: a page to lay figures out on, as for a lab report or a poster. Figures
come from Single Plot (Send to Canvas). Drag a figure to move it, its corner to resize it;
each gets a panel letter (A, B, C…) in the order they sit, top to bottom and left to right.

A figure stays linked to the DataPad (its numbers follow the table) until you freeze it.
Double-click one to change it in Single Plot. Export saves the whole page as PDF or SVG
(every line and label its own object) or PNG.

Add image… puts a picture on the page too (PNG, JPEG, TIFF, SVG or PDF): a diagram of the
setup, a photo of the prep. It keeps its shape as you resize it. SVGs and PDFs are drawn
as pictures at 300 dpi when they're added, and every picture is kept inside the session.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from matplotlib.figure import Figure
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import figspec as F
from . import stim as stimmod

PX = 4.0                          # scene units per mm
PAPERS = {"A4 portrait": (210, 297), "A4 landscape": (297, 210), "Letter portrait": (216, 279),
          "Letter landscape": (279, 216), "Slide 16:9": (338, 190)}
MARGIN = 12.0                     # mm


def _laid_out_axes(spec: dict, data: dict):
    """Where the axes sit in a figure of this spec's size, laid out tightly: (x0, y0, w, h)
    as fractions of the figure."""
    if spec.get("kind") == "image":
        return 0.0, 0.0, 1.0, 1.0
    fig = Figure(figsize=(spec["w_mm"] / 25.4, spec["h_mm"] / 25.4))
    F.draw(fig, spec, data)
    try:
        fig.tight_layout()
    except Exception:
        pass
    p = fig.axes[0].get_position()
    return p.x0, p.y0, p.width, p.height


def render(spec: dict, data: dict, dpi: float) -> QtGui.QImage:
    if spec.get("kind") == "image":
        import base64
        return QtGui.QImage.fromData(base64.b64decode(data.get("png", "")))
    fig = Figure(figsize=(spec["w_mm"] / 25.4, spec["h_mm"] / 25.4), dpi=dpi)
    try:
        F.draw(fig, spec, data)
        fig.tight_layout()
    except Exception as e:
        fig.clear()
        fig.text(0.5, 0.5, f"Can't draw: {e}", ha="center", va="center")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    return QtGui.QImage.fromData(buf.getvalue())


class Panel(QtWidgets.QGraphicsObject):
    """One figure on the page."""

    moved = QtCore.Signal()

    def __init__(self, canvas: "PlotCanvasPage", spec: dict, data: dict, linked: bool = True):
        super().__init__()
        self.canvas, self.spec, self.data, self.linked = canvas, spec, data, linked
        self.letter = ""
        self._img: QtGui.QImage | None = None
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
        zoom = max(1.0, self.canvas.view.transform().m11())
        self._img = render(self.spec, self.data, 25.4 * PX * zoom * 1.5)
        self.prepareGeometryChange()
        self.update()

    def paint(self, p, opt, widget=None):
        s = self.size()
        r = QtCore.QRectF(0, 0, s.width(), s.height())
        if self._img is not None:
            p.drawImage(r, self._img)
        if self.letter and self.canvas.letters.isChecked():
            f = QtGui.QFont()
            f.setBold(True)
            f.setPixelSize(int(self.spec.get("font", 9) * 1.4 / 72 * 25.4 * PX))
            p.setFont(f)
            p.setPen(QtGui.QColor("black"))
            p.drawText(QtCore.QPointF(2, f.pixelSize()), self.letter)
        if self.isSelected():
            p.setPen(QtGui.QPen(QtGui.QColor("#1a5fd0"), 1.5, QtCore.Qt.PenStyle.DashLine))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.setBrush(QtGui.QColor("#1a5fd0"))
            p.drawRect(QtCore.QRectF(r.right() - 8, r.bottom() - 8, 8, 8))
        if not self.linked and self.spec.get("kind") not in ("image", "curves"):
            p.setPen(QtGui.QColor("#888"))
            f = QtGui.QFont()
            f.setPixelSize(11)
            p.setFont(f)
            p.drawText(QtCore.QPointF(r.right() - 48, r.top() + 12), "frozen")

    def _corner(self, pos: QtCore.QPointF) -> bool:
        s = self.size()
        return pos.x() > s.width() - 12 and pos.y() > s.height() - 12

    def hoverMoveEvent(self, ev):
        self.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor if self._corner(ev.pos())
                       else QtCore.Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton and self._corner(ev.pos()):
            self._resizing = (ev.pos(), self.spec["w_mm"], self.spec["h_mm"])
            self.setSelected(True)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._resizing:
            p0, w, h = self._resizing
            d = ev.pos() - p0
            self.prepareGeometryChange()
            self.spec["w_mm"] = max(15.0, round(w + d.x() / PX))
            if self.spec.get("kind") == "image":       # a picture keeps its shape
                self.spec["h_mm"] = round(self.spec["w_mm"] * h / w, 1)
            else:
                self.spec["h_mm"] = max(15.0, round(h + d.y() / PX))
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
            x = round(value.x() / PX) * PX                    # whole mm
            y = round(value.y() / PX) * PX
            return QtCore.QPointF(x, y)
        return super().itemChange(change, value)

    def state(self) -> dict:
        return {"spec": self.spec, "data": self.data, "linked": self.linked,
                "x_mm": self.pos().x() / PX, "y_mm": self.pos().y() / PX}


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
        if ev.button() == QtCore.Qt.MouseButton.MiddleButton:    # the middle button drags the page about
            self._pan = ev.position()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._pan is not None:
            d = ev.position() - self._pan
            self._pan = ev.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._pan is not None:
            self._pan = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            self.canvas.remove_picked()
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
        self.paper.addItems(list(PAPERS))
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
        bar.addWidget(QtWidgets.QLabel("Page"))
        bar.addWidget(self.paper)
        bar.addWidget(self.letters)
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
        self.scene = QtWidgets.QGraphicsScene(self)
        self.view = _View(self.scene, self)
        lay.addWidget(self.view, 1)
        self.sheet = self.scene.addRect(QtCore.QRectF(), QtGui.QPen(QtGui.QColor("#b0b4ba")),
                                        QtGui.QBrush(QtGui.QColor("white")))
        self.sheet.setZValue(-100)
        self.hint = self.scene.addText("Send figures here from Single Plot (Send to Canvas).")
        self.hint.setDefaultTextColor(QtGui.QColor("#888"))
        self.panels: list[Panel] = []
        self.paper.currentTextChanged.connect(self._paper)
        self.letters.toggled.connect(lambda *_: self.scene.update())
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

    def _paper(self, name: str) -> None:
        w, h = PAPERS.get(name, (210, 297))
        self.sheet.setRect(0, 0, w * PX, h * PX)
        self.scene.setSceneRect(-40, -40, w * PX + 80, h * PX + 80)
        self.hint.setPos(MARGIN * PX, MARGIN * PX)
        self.fit()

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
        if not hasattr(self, "_sharp"):
            self._sharp = QtCore.QTimer(self, singleShot=True, interval=200)
            self._sharp.timeout.connect(lambda: [p.rerender() for p in self.panels])
        fit = getattr(self, "_fit_scale", None) or self.view.transform().m11()
        self.zoom_label.setText(f"{self.view.transform().m11() / fit * 100:.0f}%")
        self._sharp.start()

    def showEvent(self, ev):
        super().showEvent(ev)
        QtCore.QTimer.singleShot(0, self.fit)

    # -- panels -----------------------------------------------------------------------------
    def add_panel(self, spec: dict, data: dict, at: tuple[float, float] | None = None,
                  linked: bool = True, edited: "Panel | None" = None) -> Panel:
        if edited is not None and edited in self.panels:
            edited.spec, edited.data = spec, data
            edited.rerender()
            self.relabel()
            return edited
        p = Panel(self, spec, data, linked)
        self.scene.addItem(p)
        x, y = at if at else self._free_spot(spec)
        p.setPos(x * PX, y * PX)
        self.panels.append(p)
        self.relabel()
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
        W, H = self.paper_mm()
        x, y, row_h = MARGIN, MARGIN, 0.0
        taken = [QtCore.QRectF(p.pos().x() / PX, p.pos().y() / PX, p.spec["w_mm"], p.spec["h_mm"])
                 for p in self.panels]
        for _ in range(200):
            r = QtCore.QRectF(x, y, spec["w_mm"], spec["h_mm"])
            if not any(r.intersects(t) for t in taken) and r.right() <= W - MARGIN + 1:
                return x, y
            x += 5
            if x + spec["w_mm"] > W - MARGIN:
                x, y = MARGIN, y + 5
        return MARGIN, MARGIN

    def relabel(self) -> None:
        order = sorted(self.panels, key=lambda p: (round(p.pos().y() / PX / 20), p.pos().x()))
        for k, p in enumerate(order):
            p.letter = stimmod.letter(k)
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
        for b in (self.btn_freeze, self.btn_dup, self.btn_del):
            b.setEnabled(n > 0)

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
        self.data_changed()

    def _duplicate(self) -> None:
        for p in self.picked():
            self.add_panel(F.clone(p.spec), dict(p.data), linked=p.linked)

    def remove_picked(self) -> None:
        for p in self.picked():
            self.scene.removeItem(p)
            self.panels.remove(p)
        self.relabel()

    def data_changed(self) -> None:
        """The DataPad changed: the linked figures follow."""
        pad = self.page.pad_view
        for p in self.panels:
            if p.spec.get("kind") in ("image", "curves") or not F.columns_used(p.spec):
                continue
            if p.linked and all(c in pad.pad.names() for c in F.columns_used(p.spec)):
                p.data = pad.snapshot(F.columns_used(p.spec))
                if self.isVisible():
                    p.rerender()

    # -- export -----------------------------------------------------------------------------
    def export(self) -> None:
        if not self.panels:
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
        W, H = self.paper_mm()
        fig = Figure(figsize=(W / 25.4, H / 25.4))
        for p in self.panels:
            x0, y0, w, h = _laid_out_axes(p.spec, p.data)
            px, py = p.pos().x() / PX, p.pos().y() / PX
            pw, ph = p.spec["w_mm"], p.spec["h_mm"]
            rect = [(px + x0 * pw) / W, 1 - (py + ph) / H + y0 * ph / H, w * pw / W, h * ph / H]
            ax = fig.add_axes(rect)
            F.draw(fig, p.spec, p.data, ax=ax)
            if self.letters.isChecked():
                fig.text(px / W, 1 - py / H, p.letter, fontsize=p.spec.get("font", 9) * 1.4,
                         fontweight="bold", va="top", ha="left")
        fig.savefig(path, dpi=dpi, facecolor="white")

    # -- saving with the session ---------------------------------------------------------------
    def state(self) -> dict:
        return {"paper": self.paper.currentText(), "letters": self.letters.isChecked(),
                "panels": [p.state() for p in self.panels]}

    def restore(self, s: dict | None) -> None:
        for p in list(self.panels):
            self.scene.removeItem(p)
        self.panels = []
        if not s:
            self.relabel()
            return
        self.paper.setCurrentText(s.get("paper", "A4 portrait"))
        self.letters.setChecked(s.get("letters", True))
        for d in s.get("panels", []):
            try:
                self.add_panel(d["spec"], d["data"], (d["x_mm"], d["y_mm"]), d.get("linked", True))
            except (KeyError, TypeError):
                continue


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
    try:
        import pypdfium2 as pdfium
        page = pdfium.PdfDocument(path)[0]
        pil = page.render(scale=dpi / 72).to_pil()
        b = io.BytesIO()
        pil.save(b, "PNG")
        return QtGui.QImage.fromData(b.getvalue())
    except ImportError:
        pass
    if shutil.which("sips"):                      # macOS draws PDFs itself
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "page.png"
            subprocess.run(["sips", "-s", "format", "png", "-s", "dpiWidth", str(dpi), "-s", "dpiHeight",
                            str(dpi), path, "--out", str(out)], check=True, capture_output=True)
            img = QtGui.QImage(str(out))
            if not img.isNull():
                return img
    raise ValueError("PDFs need the pypdfium2 package here (pip install pypdfium2)")
