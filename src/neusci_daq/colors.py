"""Every color the data is drawn in, in one place: the channels' palette, the units', the
groups', the color maps that run across groups and across a group's trials, and how a
trace's color is adjusted to stay visible on a white or black plot. (The window's own look
is in style.py, the plots' backgrounds in theme.py.)

Plain hex strings throughout; the few functions that hand back QColors import Qt when called,
so the config and analysis code can use this module without it.
"""
from __future__ import annotations

# Trace colors, in the order new channels get them. Based on the Okabe-Ito
# color-blind-safe palette, ordered so the first few are bright on black.
PALETTE = ("#E69F00", "#56B4E9", "#009E73", "#F0E442",
           "#D55E00", "#CC79A7", "#FFFFFF", "#3D8FD1")
PALETTE_NAMES = ("Orange", "Sky blue", "Green", "Yellow", "Red-orange", "Pink", "White", "Blue")
# what can be picked: the palette, then black (never given out on its own; on a black plot
# it's drawn light, as white is drawn dark on a white one)
CHOICES = PALETTE + ("#000000",)
CHOICE_NAMES = PALETTE_NAMES + ("Black",)


def normalize_color(c: str) -> str:
    """'#abc', 'abcdef' or '#ABCDEF' -> '#abcdef'. Raises ValueError otherwise."""
    h = c.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in h):
        raise ValueError(f"{c!r} isn't a color; use #rrggbb, e.g. #e69f00.")
    return "#" + h.lower()


def color_label(c: str) -> str:
    """'Orange' for palette colors, otherwise the hex code, for messages."""
    c = normalize_color(c)
    for p, name in zip(CHOICES, CHOICE_NAMES):
        if normalize_color(p) == c:
            return name.lower()
    return c


def next_color(used) -> str:
    """First palette color not already used; cycles once all are taken."""
    used = {normalize_color(u) for u in used if u}
    for c in PALETTE:
        if normalize_color(c) not in used:
            return normalize_color(c)
    return normalize_color(PALETTE[len(used) % len(PALETTE)])


def fill_colors(channels) -> None:
    """Give every channel without a color the next unused palette color, in order."""
    for ch in channels:
        if ch.color:
            ch.color = normalize_color(ch.color)
    for ch in channels:
        if not ch.color:
            ch.color = next_color(c.color for c in channels)

# Groups' colors: a palette in a different order from the channels' and units', so a group
# doesn't look like a unit.
GROUP_PALETTE = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#999999")


def unit_color(u: int) -> str:
    """Unit u's color (0-based), until one is picked for it."""
    return PALETTE[u % len(PALETTE)]


def group_color(g: int) -> str:
    """Group g's color from the palette (0-based)."""
    return GROUP_PALETTE[g % len(GROUP_PALETTE)]


# -- color maps ----------------------------------------------------------------------
MAPS = {
    "Viridis": ("#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"),
    "Plasma": ("#0d0887", "#7e03a8", "#cc4778", "#f89540", "#f0f921"),
    "Cividis": ("#00224e", "#434e6c", "#7d7c78", "#bcaf6f", "#fee838"),
}
SOLID, OPACITY = "Solid", "Opacity"
TRIAL_STYLES = (SOLID, OPACITY, *MAPS)
PALETTE_STYLE = "Palette"
PAGE_STYLES = (PALETTE_STYLE, *MAPS)
LEVELS = 12
START = 0.12                       # where a map starts


def sample(name: str, f: float):
    """Map `name` at f (0 to 1), as a QColor."""
    from pyqtgraph.Qt import QtGui
    stops = MAPS[name]
    k = (START + (1 - START) * min(1.0, max(0.0, f))) * (len(stops) - 1)
    i = min(int(k), len(stops) - 2)
    a, b = QtGui.QColor(stops[i]), QtGui.QColor(stops[i + 1])
    g = k - i
    return QtGui.QColor(*(int(x + (y - x) * g) for x, y in
                          ((a.red(), b.red()), (a.green(), b.green()), (a.blue(), b.blue()))))


def spread(style: str, color: str, n: int) -> list:
    """How n trials in order are drawn (QColors), in a group of this color."""
    from pyqtgraph.Qt import QtGui
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


# -- on a white or black plot ----------------------------------------------------------
def _luminance(c) -> float:
    """Relative luminance (0 = black, 1 = white), as used for contrast ratios."""
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.red()) + 0.7152 * lin(c.green()) + 0.0722 * lin(c.blue())


def display_color(color: str, mode: str) -> str:
    """The color to draw a trace in. On white, too-light colors are darkened
    until they reach about 3:1 contrast; on black, too-dark ones are lightened."""
    from pyqtgraph.Qt import QtGui
    c = QtGui.QColor(color)
    if mode == "light":
        while _luminance(c) > 0.30:            # (1.05)/(L+0.05) >= 3
            c = c.darker(115)
    else:
        while _luminance(c) < 0.02 and c.lightness() < 250:   # pure black etc.
            c = c.lighter(150) if c.lightness() else QtGui.QColor("#606060")
    return c.name()
