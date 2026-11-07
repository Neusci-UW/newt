"""Small pieces the Analysis sub-tabs share: colours, plot styling, axis labels with
units, and a base class with the page, the theme and a message line."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from . import config as cfgmod
from . import style, theme
from .units import best_prefix


COMBO_CHARS = 8                 # the narrowest a settings drop-down gets, in characters


def shrink_combos(widget: QtWidgets.QWidget) -> None:
    """Let the drop-downs in `widget` be narrower than their longest choice. A drop-down as
    wide as its longest choice sets the window's minimum width: DataPad's made Analysis
    too wide for a 1024 px laptop on macOS. Each shows what fits; its open list still
    shows every choice in full."""
    for box in widget.findChildren(QtWidgets.QComboBox):
        if box.minimumContentsLength() == 0:
            box.setMinimumContentsLength(COMBO_CHARS)
        box.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)


def scroll_column(column: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    """`column` in a scroll area that scrolls up and down only, so a tall settings
    panel scrolls on a short screen instead of making the window taller than it."""
    shrink_combos(column)
    sa = QtWidgets.QScrollArea()
    sa.setWidget(column)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    bar = sa.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent)
    widest = column.maximumWidth()
    sa.setMinimumWidth(min(column.minimumSizeHint().width(), widest) + bar)   # room for the bar
    if widest < 16777215:
        sa.setMaximumWidth(widest + bar)
    sa.setSizePolicy(column.sizePolicy().horizontalPolicy(), QtWidgets.QSizePolicy.Policy.Ignored)
    return sa


class PlotCorner(QtCore.QObject):
    """A small control in a plot's top right corner, instead of a row of its own (which
    would make the window too tall for a small laptop). When the plot is too narrow
    for the control and the title side by side, the title gives way."""

    def __init__(self, plot: pg.PlotWidget, label: str, control: QtWidgets.QWidget):
        super().__init__(plot)
        self.plot = plot
        self.box = QtWidgets.QWidget(plot)
        row = QtWidgets.QHBoxLayout(self.box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(QtWidgets.QLabel(label))
        row.addWidget(control)
        plot.installEventFilter(self)

    def place(self) -> None:
        self.box.adjustSize()
        w = self.plot.width()
        self.box.move(w - self.box.width() - 6, 3)
        title = self.plot.getPlotItem().titleLabel
        # the title is centred on the plotting area, right of the axis
        title.setVisible((w - title.item.boundingRect().width()) / 2 > self.box.width() + 42)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Type.Resize:
            self.place()
        return False


def _round_down(v: float) -> float:
    """1, 2 or 5 times a power of ten, no more than v."""
    power = 10.0 ** np.floor(np.log10(v))
    return max(m for m in (1, 2, 5) if m * power <= v * (1 + 1e-9)) * power


class ScaleBar(pg.GraphicsObject):
    """A small L in place of a plot's axes, as in published traces: one line a round number
    of y units tall, one a round number of x units long. The numbers follow the zoom, and
    the bar can be dragged anywhere on the plot."""

    def __init__(self, plot: pg.PlotItem, y_unit: str | None, x_unit: str | None, color, gap=(100.0, 34.0)):
        super().__init__()
        self.vb = plot.vb
        self.y_unit, self.x_unit, self.color = y_unit, x_unit, color
        self.gap = gap                            # from the plot's right and bottom edges (pixels)
        self.px = self.py = 0.0
        self.x_text = self.y_text = ""
        self.setParentItem(self.vb)
        self.setZValue(1000)
        self.vb.sigRangeChanged.connect(self.refresh)
        self.vb.sigResized.connect(self.refresh)
        self.refresh()

    def refresh(self, *_) -> None:
        (x0, x1), (y0, y1) = self.vb.viewRange()
        w, h = self.vb.width(), self.vb.height()
        if w <= 0 or h <= 0 or x1 <= x0 or y1 <= y0:
            return
        self.prepareGeometryChange()
        self.px = self.py = 0.0
        self.x_text = self.y_text = ""
        if self.x_unit:
            dx = _round_down((x1 - x0) * 0.15)
            self.px, self.x_text = dx / (x1 - x0) * w, with_unit(dx, self.x_unit)
        if self.y_unit:
            dy = _round_down((y1 - y0) * 0.3)
            self.py, self.y_text = dy / (y1 - y0) * h, with_unit(dy, self.y_unit)
        self.setPos(max(24.0, w - self.gap[0]), min(h - 4, max(self.py + 4, h - self.gap[1])))
        self.update()

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(-24, -self.py - 4, self.px + 64, self.py + 26)

    def paint(self, p, *_) -> None:
        p.setPen(pg.mkPen(self.color, width=2.5))
        if self.py:
            p.drawLine(QtCore.QPointF(0, 0), QtCore.QPointF(0, -self.py))
        if self.px:
            p.drawLine(QtCore.QPointF(0, 0), QtCore.QPointF(self.px, 0))
        f = p.font()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        centre = QtCore.Qt.AlignmentFlag.AlignCenter
        if self.px:
            p.drawText(QtCore.QRectF(self.px / 2 - 40, 3, 80, 16), centre, self.x_text)
        if self.py:
            p.save()
            p.translate(-14, -self.py / 2)
            p.rotate(-90)
            p.drawText(QtCore.QRectF(-40, -8, 80, 16), centre, self.y_text)
            p.restore()

    def mouseDragEvent(self, ev) -> None:
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        ev.accept()
        at = self.mapToParent(ev.pos()) - self.mapToParent(ev.lastPos()) + self.pos()
        self.gap = (self.vb.width() - at.x(), self.vb.height() - at.y())
        self.refresh()

    def remove(self) -> None:
        self.vb.sigRangeChanged.disconnect(self.refresh)
        self.vb.sigResized.disconnect(self.refresh)
        self.setParentItem(None)
        if self.scene() is not None:
            self.scene().removeItem(self)


def show_scale_bar(pw: pg.PlotWidget, on: bool, color, y_unit: str | None = None, x_unit: str | None = None) -> None:
    """Swap a plot's axes for a ScaleBar (only a line for each unit given), or back again.
    A bar that's been dragged stays where it was put."""
    p = pw.getPlotItem()
    old = getattr(p, "_scale_bar", None)
    if old is not None:
        old.remove()
    p._scale_bar = None
    for side in ("left", "bottom"):
        p.showAxis(side, not on)                  # the grid goes with them
    if on and (y_unit or x_unit):
        p._scale_bar = ScaleBar(p, y_unit, x_unit, color, *([old.gap] if old is not None else []))


class ChartToggles(QtWidgets.QGroupBox):
    """A checkbox for each chart of a tab. Unticking one hides it and the layout gives its
    room to the others; the choices are remembered. charts: [(name, what it shows, its
    widget)]; a chart with no widget (None) is shown or hidden by changed(), which is
    called after every change."""

    def __init__(self, settings, key: str, charts: list, changed=None, scale_bars=None):
        super().__init__("Charts")
        self.settings, self.key, self.charts, self.changed = settings, key, charts, changed
        grid = QtWidgets.QGridLayout(self)
        grid.setVerticalSpacing(2)
        self.boxes: dict[str, QtWidgets.QCheckBox] = {}
        rows = (len(charts) + 1) // 2
        for k, (name, tip, _) in enumerate(charts):
            b = QtWidgets.QCheckBox(name)
            b.setToolTip(tip)
            b.setChecked(settings.value(f"charts/{key}/{name}", True, type=bool))
            b.toggled.connect(lambda on, name=name: self._toggled(name, on))
            grid.addWidget(b, k % rows, k // rows)
            self.boxes[name] = b
        self.btn_reset = QtWidgets.QPushButton("Show all charts")
        self.btn_reset.setToolTip("Turn every chart on again")
        self.btn_reset.clicked.connect(self.show_all)
        self.btn_clear = QtWidgets.QPushButton("Clear all charts")
        self.btn_clear.setToolTip("Turn every chart off, to then pick just the ones you want")
        self.btn_clear.clicked.connect(self.clear_all)
        grid.addWidget(self.btn_reset, rows, 0)
        grid.addWidget(self.btn_clear, rows, 1)
        self.bars = None
        if scale_bars is not None:                # scale_bars(): called when the box changes
            self.bars = QtWidgets.QCheckBox("Scale bars instead of axes")
            self.bars.setToolTip("Swap the axes of the plots against time for small scale bars, as in a published "
                                 "figure. Drag a bar to move it; its numbers follow the zoom")
            self.bars.setChecked(settings.value(f"charts/{key}/scale_bars", False, type=bool))
            self.bars.toggled.connect(lambda on: settings.setValue(f"charts/{key}/scale_bars", on))
            self.bars.toggled.connect(lambda *_: scale_bars())
            grid.addWidget(self.bars, rows + 1, 0, 1, 2)

    def is_on(self, name: str) -> bool:
        return self.boxes[name].isChecked()

    def _toggled(self, name: str, on: bool) -> None:
        self.settings.setValue(f"charts/{self.key}/{name}", on)
        self.apply()

    def apply(self) -> None:
        for name, _, widget in self.charts:
            if widget is not None:
                widget.setVisible(self.is_on(name))
        if self.changed is not None:
            self.changed()

    def show_all(self) -> None:
        for b in self.boxes.values():
            b.setChecked(True)

    def clear_all(self) -> None:
        for b in self.boxes.values():
            b.setChecked(False)


def even_out(splitter: QtWidgets.QSplitter) -> None:
    """Share a splitter's room equally among what's showing."""
    splitter.setSizes([1000 if splitter.widget(k).isVisibleTo(splitter) else 0
                       for k in range(splitter.count())])


def unit_color(u: int) -> str:
    return cfgmod.PALETTE[u % len(cfgmod.PALETTE)]


def style_plot(pw: pg.PlotWidget, th) -> None:
    pw.setBackground(th.background)
    p = pw.getPlotItem()
    p.setTitle(p.titleLabel.text, color=th.foreground)
    for ax in ("left", "bottom"):
        p.getAxis(ax).setPen(th.foreground)
        p.getAxis(ax).setTextPen(th.foreground)


def with_unit(v: float, unit: str) -> str:
    """12.3 mV/s: the prefix goes on the part before any '/'."""
    head, _, tail = unit.partition("/")
    if head in ("V", "A"):
        n, head = best_prefix(v, head)
        return f"{n:.3g} {head}" + (f"/{tail}" if tail else "")
    return f"{v:.3g} {unit}"


def label_axis(p: pg.PlotItem, side: str, text: str, unit: str) -> None:
    """Label an axis with `unit`, letting pyqtgraph add prefixes (mV, µV/s) when it's SI."""
    si = unit.split("/")[0] in ("V", "A", "s")
    p.setLabel(side, text, units=unit if si else None)
    if not si and unit:
        p.setLabel(side, f"{text} ({unit})")
    p.getAxis(side).enableAutoSIPrefix(si)


class View(QtWidgets.QWidget):
    """What the sub-tabs share: the page, the theme, and a message line (self.message)."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.main = page.main
        self._mode = style.LIGHT

    def _fg(self):
        return theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT].foreground

    def _show_message(self, text: str, error: bool = False) -> None:
        t = style.tokens(self._mode)
        self.message.setStyleSheet(f"color: {t['error'] if error else t['muted']}")
        self.message.setText(text)

    def _plots_themed(self, *widgets) -> None:
        th = theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT]
        for pw in widgets:
            style_plot(pw, th)
