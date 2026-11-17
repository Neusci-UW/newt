"""A chart's look: one record, kept on the chart (chart.look) and saved with it, that both the
live chart (pyqtgraph, on screen) and its figure (matplotlib, on the Plot Canvas and in saved
files) are drawn from. Every setting is stored once, here; the two drawings only read it.

Units are converted in this file and nowhere else:
  thick      line thickness, × the chart's own pens (1.0: as drawn). On a figure: 1.2 pt × thick.
  x, y       axis ranges in the chart's own units (s, V); None: that edge is auto.
  font…      text sizes in pt; None: the chart's own (on a figure, 9 pt and Auto).
  tick_len   tick marks in pt; on screen 1 pt = 1.33 px.
  ms         marker size in pt; None: as the chart draws them. On screen 1 pt = 2.25 px of diameter.
  title, xlabel, ylabel   "" : the chart's own.
"""
from __future__ import annotations

LOOK = {"thick": 1.0, "grid": True, "x": [None, None], "y": [None, None],
        "texts": {},                       # text you edited or deleted on it: key -> text ("" deleted)
        "title": "", "xlabel": "", "ylabel": "", "xlog": False, "ylog": False,
        "font": None, "font_ticks": None, "font_title": None, "ticks": "out", "tick_len": 3.5,
        "ms": None, "despine": True, "zero": False, "legend": "best",
        "w_mm": None, "h_mm": None,
        "aspect": None,                    # height / width of the plot; None: it fills its room        # None: the chart's shape on screen
        "fit": "", "fit_degree": 1, "fit_line": 0, "fit_x": [None, None]}

LINE_PT = 1.2          # a figure line, in pt, at thick 1.0
FONT_PT = 9.0          # a figure's label size when the chart's own is kept
PT_PX = 1.33           # screen px per pt (tick marks)
MARK_PX = 2.25         # screen px of marker diameter per pt


def new_look() -> dict:
    return restore({})


def restore(saved: dict) -> dict:
    """A full look from a saved one (older saves lack the newer keys)."""
    out = {}
    for k, v in LOOK.items():
        got = saved.get(k, v)
        out[k] = list(got) if isinstance(v, list) else dict(got) if isinstance(v, dict) else got
    return out


def line_pt(look: dict) -> float:
    return round(LINE_PT * float(look.get("thick", 1.0)), 2)


def thick_of(pt: float) -> float:
    return float(pt) / LINE_PT


FAMILY = "Arial"       # every chart's text, on screen and on its figure: on Windows and macOS both,
                       # and the same width per pt at any size (the macOS system font narrows when big)
FIG_MM = 120.0        # a figure's width on the Canvas unless you set one


def fonts(look: dict, scale: float = 1.0) -> tuple[float | None, float | None, float | None]:
    """(labels, numbers, title) in pt on the figure, × scale; None where the chart keeps its own."""
    lab = look.get("font")
    tick = look.get("font_ticks") or (lab * 0.9 if lab else None)
    title = look.get("font_title") or (lab * 1.1 if lab else None)
    return tuple(v * scale if v else None for v in (lab, tick, title))


def screen_scale(look: dict, wpx: float, dpi: float) -> float:
    """Text sizes are pt on the figure (FIG_MM wide, or the size you set); the chart on screen
    is wider, so its text is that much bigger, keeping the same proportions."""
    if wpx < 50 or not dpi:
        return 1.0
    return (wpx / dpi * 25.4) / float(look.get("w_mm") or FIG_MM)


def tick_px(look: dict) -> int:
    """pyqtgraph's tickLength: + points in, − points out."""
    px = float(look.get("tick_len", 3.5)) * PT_PX
    return int(round(px if look.get("ticks") == "in" else -px))


def marker_px(look: dict) -> float | None:
    ms = look.get("ms")
    return float(ms) * MARK_PX if ms else None


def figure_keys(look: dict, kx: float = 1.0, ky: float = 1.0) -> dict:
    """The look as figure-spec keys (figspec). kx, ky: the axes' unit scale (1000 for mV), so
    ranges come out in the units the figure shows. Ranges left auto are left out (the figure
    keeps the chart's view); so are labels left blank and a size left to the chart's shape."""
    lab, tick, title = fonts(look)
    out = {"lw": line_pt(look), "grid": bool(look.get("grid")), "xlog": bool(look.get("xlog")),
           "ylog": bool(look.get("ylog")), "ticks": look.get("ticks", "out"),
           "tick_len": float(look.get("tick_len", 3.5)), "despine": bool(look.get("despine", True)),
           "zero": bool(look.get("zero")), "legend": look.get("legend", "best")}
    for key, v in (("font", lab), ("font_ticks", tick), ("font_title", title)):
        if v:                              # set here; else the figure keeps the chart's own
            out[key] = float(v)
    if look.get("ms"):
        out["ms"] = float(look["ms"])
    for k in ("title", "xlabel", "ylabel"):
        if look.get(k):
            out[k] = look[k]
    for key, axis, k_ in (("xlim", "x", kx), ("ylim", "y", ky)):
        lo, hi = (list(look.get(axis) or []) + [None, None])[:2]
        if lo is not None and hi is not None:
            out[key] = [lo * k_, hi * k_]
    if look.get("w_mm"):                   # its width; the height comes from the plot's shape
        out["w_mm"] = float(look["w_mm"])
    for k in ("fit", "fit_degree", "fit_line"):
        out[k] = look.get(k, LOOK[k])
    out["fit_x"] = list(look.get("fit_x") or [None, None])
    return out


def html_sub(text: str) -> str:
    """Plain label text with true subscripts for the live chart: V_m → V<sub>m</sub>."""
    import re
    return re.sub(r"(?<![\w])([^\W\d_]+)_(\w+)", r"\1<sub>\2</sub>", text or "")
