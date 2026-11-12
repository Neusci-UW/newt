"""Saving part of the Analysis whiteboard as a figure: the plots in a box dragged with the
Export tool, without the title bars, borders, corners or grid, as PDF or SVG (vector: lines
stay sharp at any size, and SVG keeps every line, label and axis its own object to finish in
Illustrator or Inkscape), PNG or TIFF (at a resolution you pick), or copied to paste.

The figure is the board's own size, whatever the zoom: one board pixel is one point (1/72 in).
"""
from __future__ import annotations

from pathlib import Path

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import style

FORMATS = (("pdf", "PDF (vector)"), ("svg", "SVG (vector, editable)"), ("png", "PNG"), ("tiff", "TIFF"),
           ("copy", "Copy to the clipboard"))
VECTOR = ("pdf", "svg")
POINTS = 72                     # board pixels per inch


def draw(painter: QtGui.QPainter, board, area: QtCore.QRectF, light: bool) -> None:
    """The charts inside `area` (board pixels), on a white background, at one board pixel a
    unit of `painter`."""
    painter.fillRect(QtCore.QRectF(0, 0, area.width(), area.height()), QtGui.QColor("white"))
    frames = [f for f in board.frames if f.place.intersects(area)]
    charts = [f.chart for f in frames]
    themed = [(c, c._mode) for c in charts if light and getattr(c, "_mode", style.LIGHT) != style.LIGHT]
    for c, _ in themed:
        c.apply_theme(style.LIGHT)
    try:
        painter.setClipRect(QtCore.QRectF(0, 0, area.width(), area.height()))
        for f in frames:
            c = f.chart
            if c.width() < 1 or c.height() < 1:
                continue
            painter.save()
            at = f.place.topLeft() - area.topLeft()
            painter.translate(at.x() + 1, at.y() + 1)             # inside the frame's 1 px edge
            k = (f.place.width() - 2) / c.width()
            painter.scale(k, k)                                   # the chart on screen is the zoomed size
            c.render(painter, QtCore.QPoint(0, 0), QtGui.QRegion(),
                     QtWidgets.QWidget.RenderFlag.DrawChildren)
            painter.restore()
    finally:
        for c, mode in themed:
            c.apply_theme(mode)


def save(board, area: QtCore.QRectF, fmt: str, path: str | Path | None = None, dpi: int = 300,
         light: bool = True) -> Path | QtGui.QImage:
    """Save the figure as fmt (see FORMATS) to path; "copy" puts it on the clipboard and
    returns the image."""
    w, h = area.width(), area.height()
    if fmt == "pdf":
        out = QtGui.QPdfWriter(str(path))
        out.setResolution(POINTS)
        out.setPageSize(QtGui.QPageSize(QtCore.QSizeF(w, h), QtGui.QPageSize.Unit.Point))
        out.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0))
        p = QtGui.QPainter(out)
        draw(p, board, area, light)
        p.end()
        return Path(path)
    if fmt == "svg":
        from PyQt6 import QtSvg
        out = QtSvg.QSvgGenerator()
        out.setFileName(str(path))
        out.setSize(QtCore.QSize(round(w), round(h)))
        out.setViewBox(QtCore.QRectF(0, 0, w, h))
        out.setResolution(POINTS)
        out.setTitle("NEWT figure")
        p = QtGui.QPainter(out)
        draw(p, board, area, light)
        p.end()
        return Path(path)
    k = dpi / POINTS
    img = QtGui.QImage(max(1, round(w * k)), max(1, round(h * k)), QtGui.QImage.Format.Format_ARGB32)
    img.setDotsPerMeterX(round(dpi / 0.0254))
    img.setDotsPerMeterY(round(dpi / 0.0254))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.scale(k, k)
    draw(p, board, area, light)
    p.end()
    if fmt == "copy":
        QtWidgets.QApplication.clipboard().setImage(img)
        return img
    if not img.save(str(path), "TIFF" if fmt == "tiff" else "PNG"):
        raise OSError(f"Couldn't save {path}.")
    return Path(path)


class ExportDialog(QtWidgets.QDialog):
    """How to save the box dragged: the format, resolution and background."""

    def __init__(self, parent, area: QtCore.QRectF, settings):
        super().__init__(parent)
        self.setWindowTitle("Export figure")
        self.settings = settings
        form = QtWidgets.QFormLayout(self)
        size = QtWidgets.QLabel(f"{area.width() / POINTS:.2f} × {area.height() / POINTS:.2f} in "
                                f"({area.width() * 25.4 / POINTS:.0f} × {area.height() * 25.4 / POINTS:.0f} mm)")
        form.addRow("Size", size)
        self.fmt = QtWidgets.QComboBox()
        for key, label in FORMATS:
            self.fmt.addItem(label, key)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(settings.value("export/format", "pdf"))))
        form.addRow("Format", self.fmt)
        self.dpi = QtWidgets.QSpinBox(minimum=72, maximum=1200, singleStep=50, suffix=" dpi")
        self.dpi.setValue(settings.value("export/dpi", 300, type=int))
        self.dpi.setToolTip("How many pixels to an inch, for PNG, TIFF and copying: journals usually ask "
                            "for 300 to 600")
        form.addRow("Resolution", self.dpi)
        self.light = QtWidgets.QCheckBox("Light plots, as in print")
        self.light.setToolTip("Draw the plots light, on white, even when NEWT is dark")
        self.light.setChecked(settings.value("export/light", True, type=bool))
        form.addRow("Background", self.light)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.fmt.currentIndexChanged.connect(lambda *_: self.dpi.setEnabled(self.format() not in VECTOR))
        self.dpi.setEnabled(self.format() not in VECTOR)

    def format(self) -> str:
        return self.fmt.currentData()

    def remember(self) -> None:
        self.settings.setValue("export/format", self.format())
        self.settings.setValue("export/dpi", self.dpi.value())
        self.settings.setValue("export/light", self.light.isChecked())
