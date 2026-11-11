"""What the parts of the main window share (see gui.py): its modes, and the small widgets
of its toolbar and plots."""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from .plotzoom import WheelViewBox

LIVE, REVIEW = "live", "review"
_SI_BASE = {"V", "A", "s", "Hz", "Ω", "ohm", "N", "Pa", "W", "m", "g", "T"}


WINDOW_MAX = 600.0


class WindowBox(QtWidgets.QDoubleSpinBox):
    """Window length in seconds, shown as "10 s" rather than "10.00 s"."""

    def textFromValue(self, v: float) -> str:
        return f"{v:g}"

    def sizeHint(self) -> QtCore.QSize:
        # Qt sizes the box for "600.00 s" (the maximum, with every decimal); size it for
        # what's actually typed, like "600 s", so the toolbar fits on a laptop.
        h = super().sizeHint()
        return QtCore.QSize(min(h.width(), 76), h.height())

    def minimumSizeHint(self) -> QtCore.QSize:
        return self.sizeHint()


class _TraceViewBox(WheelViewBox):
    """The app's wheel rules (see plotzoom), except that time zoom and pan go through
    Window and the main window, so every acquisition plot follows and live mode keeps
    the newest data at the right edge."""

    def __init__(self, zoom_time, pan_time, zoom_to=None, **kw):
        super().__init__(**kw)
        self.zoom_time, self.pan_time = zoom_time, pan_time
        self._zoom_to = zoom_to

    def set_time_range(self, x0: float, x1: float) -> None:
        if self._zoom_to is not None:
            self._zoom_to(x0, x1)
        else:
            super().set_time_range(x0, x1)


class _CommentEdit(QtWidgets.QLineEdit):
    """Asks the toolbar for little width, so nothing gets pushed into its overflow
    menu on a narrow screen; it still grows into any free space."""

    def sizeHint(self) -> QtCore.QSize:
        h = super().sizeHint()
        return QtCore.QSize(90, h.height())
