"""Small pieces the Analysis sub-tabs share: colours, plot styling, axis labels with
units, and a base class with the page, the theme and a message line."""
from __future__ import annotations

import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets

from . import config as cfgmod
from . import style, theme
from .units import best_prefix


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
