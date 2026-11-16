"""Per-channel controls shown to the left of each live plot.

Each row is one channel: switch it on or off, rename it, pick which DAQ input
(ai0, ai1, ...) it reads, change its trace color, or remove it. Only
switched-on rows are acquired and recorded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pyqtgraph as pg

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from .colors import CHOICES, CHOICE_NAMES, normalize_color
from .config import Channel
from . import style

STRIP_WIDTH = 180
OFF_COLOR = "#9aa0a6"


@dataclass
class ChannelRow:
    ch: Channel     # ch.enabled is the on/off switch; ch.color is the trace color


class ColorButton(QtWidgets.QToolButton):
    """A colored bar. Click it to pick a palette color or a custom one.

    Calls on_pick(hex) with the chosen color. Used next to live plots (in the
    channel strip) and review plots. With on_opacity, its menu also has an Opacity slider,
    which calls on_opacity(0 to 1) as it moves.
    """

    def __init__(self, color: str, on_pick, tooltip: str = "Change this channel's trace color",
                 enabled_look: bool = True, opacity: float = 1.0, on_opacity=None):
        super().__init__()
        self._on_pick = on_pick
        self.opacity, self._on_opacity = opacity, on_opacity
        self.setToolTip(tooltip)
        self.setFixedWidth(8)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Expanding)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.set_color(color, enabled_look)
        self.clicked.connect(self._show_menu)

    def set_color(self, color: str, enabled_look: bool = True) -> None:
        self.color = color
        self.setStyleSheet(f"QToolButton {{ background: {color if enabled_look else OFF_COLOR}; "
                           "border: none; border-radius: 0; padding: 0; }")

    def _show_menu(self):
        menu = QtWidgets.QMenu(self)
        for c, name in zip(CHOICES, CHOICE_NAMES):
            pix = QtGui.QPixmap(14, 14)
            pix.fill(QtGui.QColor(c))
            painter = QtGui.QPainter(pix)          # border, so white shows on a white menu
            painter.setPen(QtGui.QColor("#808080"))
            painter.drawRect(0, 0, 13, 13)
            painter.end()
            a = menu.addAction(QtGui.QIcon(pix), name)
            a.setCheckable(True)
            a.setChecked(normalize_color(c) == self.color)
            a.triggered.connect(lambda _=False, c=c: self._pick(c))
        menu.addSeparator()
        menu.addAction("Custom…", self._custom)
        if self._on_opacity is not None:
            menu.addSeparator()
            action = QtWidgets.QWidgetAction(menu)
            action.setDefaultWidget(self._opacity_row())
            menu.addAction(action)
        menu.exec(self.mapToGlobal(QtCore.QPoint(self.width(), 0)))

    def _opacity_row(self) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(row)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.addWidget(QtWidgets.QLabel("Opacity"))
        self.opacity_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, minimum=5, maximum=100)
        self.opacity_slider.setValue(round(self.opacity * 100))
        self.opacity_slider.setMinimumWidth(100)
        shown = QtWidgets.QLabel(f"{self.opacity_slider.value()}%")
        self.opacity_slider.valueChanged.connect(lambda v: shown.setText(f"{v}%"))
        self.opacity_slider.valueChanged.connect(self._opacity_moved)
        lay.addWidget(self.opacity_slider, 1)
        lay.addWidget(shown)
        return row

    def _opacity_moved(self, v: int) -> None:
        self.opacity = v / 100
        self._on_opacity(self.opacity)

    def _custom(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self.color), self, "Trace color")
        if c.isValid():
            self._pick(c.name())

    def _pick(self, c: str):
        c = normalize_color(c)
        if c != self.color:
            # Deferred: the caller may rebuild this button.
            QtCore.QTimer.singleShot(0, lambda: self._on_pick(c))


COMPACT_WIDTH = 54
LINE_MIN, LINE_MAX = 0.5, 20.0

_PREFIX = {"p": 1e-12, "n": 1e-9, "µ": 1e-6, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "M": 1e6, "G": 1e9}


def format_y(v: float, unit: str, si: bool) -> str:
    """A Max/Min value as the plot's axis shows it: "-80 mV" for a channel in V."""
    if abs(v) < 1e-15:
        v = 0.0
    if si:
        return pg.siFormat(v, precision=3, suffix=unit)
    return f"{v:.4g} {unit}".strip()


def parse_y(text: str, unit: str, si: bool) -> float | None:
    """A typed Max/Min: "-80 mV", "-80m" or "-0.08" for a channel in V; None if it isn't a number."""
    t = text.strip().replace("−", "-")
    if unit and t.endswith(unit):
        t = t[:-len(unit)].strip()
    m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([pnµumkMG]?)", t)
    if not m or (m.group(2) and not si):
        return None
    return float(m.group(1)) * _PREFIX[m.group(2)]


class ChannelStrip(QtWidgets.QFrame):
    """On/off, name, input, line thickness, color and remove controls for one channel.

    In split view the strip is compact (color bar and name) and shows its full
    controls while the mouse is over it, or while you're editing one of them.
    """

    hover_changed = QtCore.Signal(bool)

    def __init__(self, row: ChannelRow, inputs: list[str], diff: bool,
                 on_change, on_remove, on_color, can_remove: bool, on_width=None,
                 is_stim: bool = False, on_stim=None):
        super().__init__()
        self.row = row
        self.setObjectName("channelStrip")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(STRIP_WIDTH)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Color stays changeable while recording: it's display-only.
        self.color_btn = ColorButton(row.ch.color, lambda c: on_color(self, c),
                                     enabled_look=row.ch.enabled)
        outer.addWidget(self.color_btn)
        # Compact view: just the name.
        self.mini = QtWidgets.QLabel(row.ch.name)
        self.mini.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.mini.setWordWrap(True)
        self.mini.setToolTip("Point at this to show the channel's controls")
        self.mini.hide()
        outer.addWidget(self.mini, 1)
        self.body = QtWidgets.QWidget()
        outer.addWidget(self.body, 1)
        lay = QtWidgets.QGridLayout(self.body)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setVerticalSpacing(6)

        self.on = QtWidgets.QCheckBox()
        self.on.setChecked(row.ch.enabled)
        self.on.setToolTip("Record and show this channel")
        self.name = QtWidgets.QLineEdit(row.ch.name)
        self.name.setToolTip("Channel name")
        self.input = QtWidgets.QComboBox()
        self.input.addItems(inputs)
        if row.ch.physical not in inputs:          # e.g. ai5 after switching to DIFF
            self.input.addItem(row.ch.physical)
        self.input.setCurrentText(row.ch.physical)
        self.input.setToolTip("Which DAQ input this channel reads"
                              + (" (DIFF: aiN is measured against ai(N+4))" if diff else ""))
        self.remove = QtWidgets.QToolButton(text="✕")
        self.remove.setObjectName("remove")
        self.remove.setFixedSize(26, 26)
        self.remove.setToolTip("Remove this channel")
        self._can_remove = can_remove
        self.remove.setEnabled(can_remove)
        # Line thickness: typed, 0.5-20 px. Display only, so it works while recording.
        self.line_width = QtWidgets.QDoubleSpinBox(minimum=LINE_MIN, maximum=LINE_MAX, decimals=1, singleStep=0.5)
        self.line_width.setSuffix(" px")
        self.line_width.setValue(float(getattr(row.ch, "width", 1.0)))
        self.line_width.setKeyboardTracking(False)
        self.line_width.setToolTip(f"Line thickness ({LINE_MIN:g}–{LINE_MAX:g} px)")

        lay.addWidget(self.on, 0, 0)
        lay.addWidget(self.name, 0, 1, 1, 2)
        lay.addWidget(QtWidgets.QLabel("Input"), 1, 0, 1, 1)
        lay.addWidget(self.input, 1, 1)
        lay.addWidget(self.remove, 1, 2)
        lay.addWidget(QtWidgets.QLabel("Line"), 2, 0, 1, 1)
        lay.addWidget(self.line_width, 2, 1)
        # The plot's y range: typed, or zoomed with the buttons. Display only, so it works
        # while recording. Set once the plot exists (attach_plot).
        self._vb = None
        self.y_max, self.y_min = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
        for w, end in ((self.y_max, "top"), (self.y_min, "bottom")):
            w.setToolTip(f"The value at the {end} of the plot. Type one (e.g. 80 mV) to set it; "
                         "right-click the plot › Autoscale to fit it to the data again")
            w.editingFinished.connect(lambda w=w: self._typed_y(w))
        self.y_out, self.y_in, self.y_fit = (QtWidgets.QToolButton() for _ in range(3))
        for b, tip in ((self.y_out, "Zoom out on the y axis (show twice the range)"),
                       (self.y_in, "Zoom in on the y axis (show half the range)"),
                       (self.y_fit, "Autoscale: fit the y axis to the data")):
            b.setToolTip(tip)
            b.setAccessibleName(tip.split(" (")[0].split(":")[0])
            b.setFixedSize(30, 26)
        self.set_ink(None)
        self.y_out.clicked.connect(lambda: self._zoom_y(2.0))
        self.y_in.clicked.connect(lambda: self._zoom_y(0.5))
        self.y_fit.clicked.connect(lambda: self._autoscale and self._autoscale(self._vb))
        btns = QtWidgets.QHBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(4)
        for b in (self.y_out, self.y_in, self.y_fit):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addWidget(QtWidgets.QLabel("Max"), 3, 0)
        lay.addWidget(self.y_max, 3, 1, 1, 2)
        lay.addWidget(QtWidgets.QLabel("Min"), 4, 0)
        lay.addWidget(self.y_min, 4, 1, 1, 2)
        lay.addWidget(QtWidgets.QLabel("Scale"), 5, 0)
        lay.addLayout(btns, 5, 1, 1, 2)
        self._y_widgets = (self.y_max, self.y_min, self.y_out, self.y_in, self.y_fit)
        for w in self._y_widgets:
            w.setEnabled(False)                     # until there's a plot (off: none)
        self.stim = QtWidgets.QCheckBox("⚡ Stimulus")
        self.stim.setChecked(is_stim)
        self.stim.setToolTip("This channel records the stimulus: tick if this input records the stimulator's output (wired to it, or in the "
                             "simulation). Its pulses give exact stimulus times for Trials, and group the "
                             "trials by stimulus. Only one channel can")
        self.stim.setVisible(on_stim is not None)
        lay.addWidget(self.stim, 6, 0, 1, 3)
        info = QtWidgets.QLabel(f"{row.ch.units} · scale ×{row.ch.scale:g}")
        info.setToolTip("Units and scale come from the config file")
        info.setStyleSheet("color: gray")
        lay.addWidget(info, 7, 1, 1, 2)
        lay.setRowStretch(8, 1)

        # Defer: the strip is rebuilt after a change, and must not be deleted
        # while its own signal is still running.
        later = lambda f: (lambda *_: QtCore.QTimer.singleShot(0, f))
        self.on.toggled.connect(later(lambda: on_change(self, "enabled", self.on.isChecked())))
        self.input.activated.connect(later(lambda: on_change(self, "physical", self.input.currentText())))
        self.name.editingFinished.connect(later(lambda: self.name.text().strip() != row.ch.name
                                                and on_change(self, "name", self.name.text().strip())))
        self.remove.clicked.connect(later(lambda: on_remove(self)))
        if on_width is not None:
            self.line_width.valueChanged.connect(lambda v: on_width(self, float(v)))
        if on_stim is not None:
            self.stim.toggled.connect(later(lambda: on_stim(self, self.stim.isChecked())))

        self.compact = False
        self.expanded = True
        self._collapse_timer = QtCore.QTimer(self, interval=250, timeout=self._maybe_collapse)

    # -- y scale -------------------------------------------------------------------
    def attach_plot(self, vb, unit: str, si: bool, autoscale, on_hold=None) -> None:
        """Tie Max/Min and the scale buttons to this channel's plot. autoscale(vb) fits it
        to the data; on_hold() is called when the range is set by hand."""
        self._vb, self._unit, self._si = vb, unit, si
        self._autoscale, self._on_hold = autoscale, on_hold
        for w in self._y_widgets:
            w.setEnabled(True)
        vb.sigYRangeChanged.connect(self._show_y)
        self._show_y(vb, vb.viewRange()[1])

    def set_ink(self, ink: str | None) -> None:
        """Draw the scale buttons' icons in the app's text color (None: the current theme's)."""
        for b, kind in ((self.y_out, "out"), (self.y_in, "in"), (self.y_fit, "fit_y")):
            b.setIcon(style.scale_icon(kind, ink))

    def _show_y(self, _vb, rng) -> None:
        try:
            for w, v in ((self.y_min, rng[0]), (self.y_max, rng[1])):
                if not w.hasFocus():                # don't overwrite what's being typed
                    w.setText(format_y(float(v), self._unit, self._si))
        except RuntimeError:                        # the strip went before its plot
            pass

    def _set_y(self, lo: float, hi: float) -> None:
        vb = self._vb
        vb.y_free = True                            # may go past the data
        vb.enableAutoRange(axis="y", enable=False)
        vb.setYRange(lo, hi, padding=0)
        vb.sigRangeChangedManually.emit([False, True])     # overlaid axes follow (MultiAxis)
        if self._on_hold is not None:
            self._on_hold()

    def _typed_y(self, w: QtWidgets.QLineEdit) -> None:
        if self._vb is None:
            return
        lo, hi = self._vb.viewRange()[1]
        v = parse_y(w.text(), self._unit, self._si)
        if w is self.y_max:
            hi = v
        else:
            lo = v
        if v is None or not hi > lo:
            self._show_y(self._vb, self._vb.viewRange()[1])     # not a usable range: put it back
            return
        self._set_y(lo, hi)

    def _zoom_y(self, f: float) -> None:
        if self._vb is None:
            return
        lo, hi = self._vb.viewRange()[1]
        c, h = (lo + hi) / 2, (hi - lo) / 2 * f
        self._set_y(c - h, c + h)

    def set_locked(self, locked: bool) -> None:
        """While recording, lock everything except the color and line thickness."""
        for w in (self.on, self.name, self.input, self.stim):
            w.setEnabled(not locked)
        self.remove.setEnabled(self._can_remove and not locked)

    # -- compact (split view) ----------------------------------------------------
    def set_compact(self, compact: bool) -> None:
        self.compact = compact
        self._set_expanded(not compact)

    def _set_expanded(self, on: bool) -> None:
        self.expanded = on
        self.body.setVisible(on)
        self.mini.setVisible(not on)
        self.setFixedWidth(STRIP_WIDTH if on else COMPACT_WIDTH)
        if self.compact and on:
            self._collapse_timer.start()
        else:
            self._collapse_timer.stop()
        self.hover_changed.emit(on)

    def enterEvent(self, ev):
        if self.compact and not self.expanded:
            self._set_expanded(True)
        super().enterEvent(ev)

    def _busy(self) -> bool:
        """Still in use: under the mouse, editing a field, or a drop-down is open."""
        if self.underMouse():
            return True
        fw = QtWidgets.QApplication.focusWidget()
        if fw is not None and (fw is self or self.isAncestorOf(fw)) and fw is not self.color_btn:
            return True
        popup = QtWidgets.QApplication.activePopupWidget()
        return popup is not None

    def _maybe_collapse(self):
        if self.compact and self.expanded and not self._busy():
            self._set_expanded(False)


class ChannelRowWidget(QtWidgets.QWidget):
    """One channel row: its strip and its plot (or an "off" label).

    In split view the strip is compact, and when it expands it slides over the
    plot instead of squeezing it, so the trace doesn't jump.
    """

    def __init__(self, strip: ChannelStrip, content: QtWidgets.QWidget):
        super().__init__()
        self.strip, self.content = strip, content
        strip.setParent(self)
        content.setParent(self)
        strip.raise_()
        strip.hover_changed.connect(lambda _: self._strip_resized())
        self.full_height = strip.sizeHint().height()    # with all its settings showing
        self.fit_strip = False                          # an "off" row: just as tall as its strip
        self.overlaid = False                           # its trace is in the overlay plot: stays folded

    def _strip_resized(self) -> None:
        """A row that's just its strip grows while the strip is open, so nothing is cut off."""
        if self.fit_strip:
            self.setFixedHeight(self.strip.sizeHint().height())
        self._relayout()

    def set_compact(self, compact: bool) -> None:
        self.strip.set_compact(compact)
        if self.fit_strip:
            self.setFixedHeight(self.strip.sizeHint().height())
        self._relayout()

    def sizeHint(self):
        s = self.content.sizeHint()
        return QtCore.QSize(STRIP_WIDTH + s.width(), max(s.height(), self.strip.sizeHint().height()))

    def minimumSizeHint(self):
        # Not the strip's full height: when the rows don't fit, their strips fold up
        # (MainWindow._fit_strips) instead of pushing the window past the screen.
        return QtCore.QSize(COMPACT_WIDTH + 50, 40)

    def resizeEvent(self, ev):
        self._relayout()
        super().resizeEvent(ev)

    def _relayout(self):
        w, h = self.width(), self.height()
        left = COMPACT_WIDTH if self.strip.compact else STRIP_WIDTH
        self.content.setGeometry(left, 0, max(0, w - left), h)
        self.strip.setGeometry(0, 0, STRIP_WIDTH if self.strip.expanded else COMPACT_WIDTH, h)
        self.strip.raise_()


class CalcStrip(QtWidgets.QFrame):
    """The left of a calculated channel's row: its name and formula, and Edit. It has
    the channel strip's widths, so its plot lines up with the channels' plots."""

    hover_changed = QtCore.Signal(bool)

    def __init__(self, name: str, formula: str, problem: str, on_edit):
        super().__init__()
        self.setObjectName("channelStrip")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.compact = False
        self.expanded = True
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 8, 8)
        lay.setSpacing(4)
        self.title = QtWidgets.QLabel(f"<b>ƒ {name}</b>")
        self.title.setWordWrap(True)
        self.formula = QtWidgets.QLabel(problem or formula)
        self.formula.setWordWrap(True)
        self.formula.setStyleSheet("color: #e0524f" if problem else "color: gray")
        self.btn_edit = QtWidgets.QPushButton("Edit…")
        self.btn_edit.setToolTip("Change or delete this analysis channel")
        self.btn_edit.clicked.connect(on_edit)
        lay.addWidget(self.title)
        lay.addWidget(self.formula)
        lay.addWidget(self.btn_edit, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        self.setToolTip(f"Calculated channel {name} = {formula}" + (f"\n{problem}" if problem else ""))
        self.setFixedWidth(STRIP_WIDTH)

    def set_compact(self, compact: bool) -> None:
        self.compact = compact
        self.expanded = not compact
        self.formula.setVisible(not compact)
        self.btn_edit.setVisible(not compact)
        self.setFixedWidth(COMPACT_WIDTH if compact else STRIP_WIDTH)
