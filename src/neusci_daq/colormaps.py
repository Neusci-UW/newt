"""Colours for overlaid traces: colour maps that run across a set of trials or stimulus
pages, and how a set of trials is drawn (one colour, an opacity ramp, or a colour map).

A map's dark start is skipped, so the first trial still shows on a black plot. Trials
are drawn in a few levels rather than one colour each: trials that share a colour are
one item on the plot, so hundreds of them stay quick.
"""
from __future__ import annotations

from pyqtgraph.Qt import QtGui

MAPS = {
    "Viridis": ("#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"),
    "Plasma": ("#0d0887", "#7e03a8", "#cc4778", "#f89540", "#f0f921"),
    "Cividis": ("#00224e", "#434e6c", "#7d7c78", "#bcaf6f", "#fee838"),
}
SOLID, OPACITY = "Solid", "Opacity"
TRIAL_STYLES = (SOLID, OPACITY, *MAPS)
PALETTE = "Palette"
PAGE_STYLES = (PALETTE, *MAPS)
LEVELS = 12
START = 0.12                       # where a map starts


def sample(name: str, f: float) -> QtGui.QColor:
    """Map `name` at f (0 to 1)."""
    stops = MAPS[name]
    k = (START + (1 - START) * min(1.0, max(0.0, f))) * (len(stops) - 1)
    i = min(int(k), len(stops) - 2)
    a, b = QtGui.QColor(stops[i]), QtGui.QColor(stops[i + 1])
    g = k - i
    return QtGui.QColor(*(int(x + (y - x) * g) for x, y in
                          ((a.red(), b.red()), (a.green(), b.green()), (a.blue(), b.blue()))))


def spread(style: str, color: str, n: int) -> list[QtGui.QColor]:
    """How n trials in order are drawn, in a page of this colour."""
    out = []
    for k in range(n):
        f = round(k / max(1, n - 1) * (LEVELS - 1)) / (LEVELS - 1)
        c = sample(style, f) if style in MAPS else QtGui.QColor(color)
        if style == OPACITY:
            c.setAlpha(int(35 + 220 * f) if n > 1 else 255)
        elif style == SOLID:
            c.setAlpha(int(max(35, 255 / n ** 0.5)))     # fainter the more of them there are
        out.append(c)
    return out
