"""Every color the data is drawn in, in one place: the channels' palette, the units', the
groups', the color maps that run across groups and across a group's trials, and how a
trace's color is adjusted to stay visible on a white or black plot. (The window's own look
is in style.py, the plots' backgrounds in theme.py.)

Plain hex strings throughout; the few functions that hand back QColors import Qt when called,
so the config and analysis code can use this module without it.
"""
from __future__ import annotations

# Trace colors, in the order new channels get them: fully saturated hues spread around the
# wheel, ordered so the first few are bright on black.
PALETTE = ("#FF8000", "#00BFFF", "#00FF00", "#FFFF00",
           "#FF0000", "#FF00FF", "#FFFFFF", "#0040FF")
PALETTE_NAMES = ("Orange", "Sky blue", "Green", "Yellow", "Red", "Magenta", "White", "Blue")
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
SOLID, OPACITY, SHADES_T = "Solid", "Opacity", "Shades"
TRIAL_STYLES = (SOLID, OPACITY, SHADES_T, *MAPS)
PALETTE_STYLE, ONE, SHADES, TWO = "Palette", "One color", "Shades of one color", "From one color to another"
PAGE_STYLES = (PALETTE_STYLE, ONE, SHADES, TWO, *MAPS)
# the colors a Groups setting is made from (saved under these keys), until you pick them
PICKED = {"base_color": "#0072B2", "first_color": "#0072B2", "last_color": "#D55E00"}
PICKS = {ONE: ("base_color",), SHADES: ("base_color",), TWO: ("first_color", "last_color")}
LEVELS = 12
START = 0.12                       # where a map starts


def _clamp(f: float) -> float:
    return min(1.0, max(0.0, f))


def _mix(a, b, f: float):
    """The color f (0 to 1) of the way from a to b, as a QColor."""
    from pyqtgraph.Qt import QtGui
    a, b = QtGui.QColor(a), QtGui.QColor(b)
    return QtGui.QColor(*(round(x + (y - x) * f) for x, y in
                          ((a.red(), b.red()), (a.green(), b.green()), (a.blue(), b.blue()))))


def shade(color: str, f: float):
    """Color at f (0 to 1) along its shades: pale and greyish (0) to full and deep (1), the same hue."""
    from pyqtgraph.Qt import QtGui
    h, s, _, _ = QtGui.QColor(color).getHslF()
    f = _clamp(f)
    s2 = 0.18 + (max(s, 0.55) - 0.18) * f       # saturation up
    l2 = 0.78 - 0.42 * f                        # light to dark
    return QtGui.QColor.fromHslF(max(h, 0.0), s2, l2)


def sample(name: str, f: float):
    """Map `name` at f (0 to 1), as a QColor."""
    stops = MAPS[name]
    k = (START + (1 - START) * _clamp(f)) * (len(stops) - 1)
    i = min(int(k), len(stops) - 2)
    return _mix(stops[i], stops[i + 1], k - i)


def picked(looks: dict, key: str) -> str:
    """The color you picked for a Groups setting (PICKED's keys), or its default."""
    return looks.get(key) or PICKED[key]


def along(style: str, f: float, looks: dict):
    """A group's color f (0 to 1) of the way along a Groups setting, as a QColor: pale to deep
    (Shades of one color), first color to last (From one color to another), or across a color
    map (Viridis for the settings that don't run from one end to the other)."""
    if style == SHADES:
        return shade(picked(looks, "base_color"), f)
    if style == TWO:
        return _mix(picked(looks, "first_color"), picked(looks, "last_color"), _clamp(f))
    return sample(style if style in MAPS else "Viridis", f)


def page_color(g: int, names: list[str], looks: dict, values: dict | None = None) -> str:
    """Group g's color ("#rrggbb") among `names`, the groups in order: the one you gave it; else
    along the Groups setting, by Color groups by's measure (`values`: {group: its value}, when
    one is picked) or by its place among the groups left showing; else (Palette) from the
    groups' palette. `looks` is the session's (groupsfile.load). The Qt Analysis tab and the
    HTML front end both color groups with this."""
    name = names[g] if g < len(names) else None
    if looks["colors"].get(name):
        return looks["colors"][name]
    way, values = looks["page_map"], values or {}
    if name in values:                             # Color groups by: along a measure
        left = [n for n in names if n in values and n not in looks["hidden_groups"]] or [name]
        lo, hi = min(values[n] for n in left), max(values[n] for n in left)
        return along(way, (values[name] - lo) / (hi - lo) if hi > lo else 0.5, looks).name()
    if way == ONE:
        return picked(looks, "base_color")
    if way in MAPS or way in (SHADES, TWO):        # first group left showing to last
        left = [n for n in names if n not in looks["hidden_groups"]]
        place = left.index(name) if name in left else g
        f = 1.0 if way == SHADES and len(left) < 2 else place / max(1, len(left) - 1)
        return along(way, f, looks).name()
    return group_color(g)


def alpha(opacity: float) -> float:
    """How solid (0 to 255) the Opacity setting (0 to 1) draws, never quite invisible."""
    return 255 * min(1.0, max(0.02, opacity))


def spread(style: str, color: str, n: int, opacity: float | None = None) -> list:
    """How n trials in order are drawn (QColors), in a group of this color. opacity (0–1), when
    given, sets how solid they are (Opacity: the last; the first fades from a seventh of it)."""
    from pyqtgraph.Qt import QtGui
    out = []
    for k in range(n):
        f = round(k / max(1, n - 1) * (LEVELS - 1)) / (LEVELS - 1)
        c = sample(style, f) if style in MAPS else shade(color, f) if style == SHADES_T else QtGui.QColor(color)
        if opacity is not None:
            top = alpha(opacity)
            c.setAlpha(int(top * (0.14 + 0.86 * f)) if style == OPACITY and n > 1 else int(top))
        elif style == OPACITY:
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
