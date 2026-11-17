"""A figure, as Single Plot designs it and the Plot Canvas lays it out: a plain dict (the
spec) and the numbers it plots (a snapshot of DataPad columns), drawn with matplotlib so it
saves as PDF/SVG with every line and label its own object.

spec = {"kind": "xy" | "bar", "series": [{"x", "y", "by_group", "style", "average", "color",
"label"}], "title", "xlabel", "ylabel", "xlim", "ylim", "xlog", "ylog", "font", "lw", "ms",
"grid", "legend", "despine", "zero", "fit", "w_mm", "h_mm"}, and how it looks (LOOK_DEFAULTS, by
section in SECTIONS; a figure from before they existed draws as it did): "family", "font_legend",
"bold", "axw", "marker", "cap", "err_style", "box", "nticks", "palette", "legend_frame"

data = {"rows": [trial, …], "groups": [name, …], "colors": {group: "#hex"}, "cols": {name:
[values]}, "units": {name: unit}}

Labels are written plainly; a word with an underscore gets a true subscript (I_B → I with a
subscript B), so students never see the underscore on a figure.
"""
from __future__ import annotations

import copy
import re

import numpy as np

from . import fitting

PALETTE = ["#1f5fbf", "#d1495b", "#2a9d8f", "#e9a03b", "#6a4c93", "#8a8a8a", "#3d348b", "#00798c"]
STYLES = {"markers": "Points", "line": "Line", "both": "Line and points"}
AVERAGES = {"": "Every trial", "sem": "Mean ± SEM at each x", "sd": "Mean ± SD at each x", "mean": "Mean at each x"}
LEGENDS = ["best", "upper left", "upper right", "lower left", "lower right", "outside", "off"]
FAMILIES = ["Arial", "Helvetica", "Times New Roman", "Courier New"]
MARKERS = {"circle": "o", "square": "s", "triangle": "^", "diamond": "D", "open": "o", "none": None}
PALETTES = {"": None,                                         # the groups' own colors
            "safe": ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#000000"],
            "gray": ["#111111", "#6e6e6e", "#a8a8a8", "#3c3c3c", "#8c8c8c", "#cfcfcf"],
            "viridis": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
            "bold": ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf"]}
# how a figure looks, beyond its sizes: what each key is when a figure doesn't say (as before them)
LOOK_DEFAULTS = {"family": "Arial", "font_legend": 0.0, "bold": "none", "axw": 0.8, "marker": "circle",
                 "cap": 0.0, "err_style": "bars", "nticks": 0, "palette": "", "legend_frame": False,
                 "smooth": 0}
# the look by section, as Single Plot shows it; a section can be the same on every Canvas figure
SECTIONS = {"text": ("family", "font", "font_ticks", "font_title", "font_legend", "bold"),
            "lines": ("lw", "axw", "ms", "marker", "cap", "err_style", "smooth"),
            "axes": ("box", "despine", "ticks", "tick_len", "nticks", "grid", "zero"),
            "colors": ("palette",),
            "legend": ("legend", "legend_frame")}
# a whole look at once: text and lines in pt, size in mm
PRESETS = {"Paper": {"font": 8.0, "font_ticks": 7.0, "font_title": 9.0, "font_legend": 7.0, "lw": 0.75, "axw": 0.6,
                     "ms": 3.5, "cap": 2.0, "tick_len": 3.0, "w_mm": 85.0, "h_mm": 64.0},
           "Poster": {"font": 18.0, "font_ticks": 16.0, "font_title": 22.0, "font_legend": 16.0, "lw": 2.0,
                      "axw": 1.5, "ms": 8.0, "cap": 5.0, "tick_len": 6.0, "w_mm": 160.0, "h_mm": 120.0},
           "Slide": {"font": 14.0, "font_ticks": 12.0, "font_title": 16.0, "font_legend": 12.0, "lw": 1.5, "axw": 1.2,
                     "ms": 6.0, "cap": 4.0, "tick_len": 5.0, "w_mm": 150.0, "h_mm": 95.0},
           "Notebook": {"font": 10.0, "font_ticks": 9.0, "font_title": 11.0, "font_legend": 9.0, "lw": 1.0,
                        "axw": 0.8, "ms": 4.5, "cap": 3.0, "tick_len": 3.5, "w_mm": 120.0, "h_mm": 80.0}}


def look_of(spec: dict, sections=tuple(SECTIONS)) -> dict:
    """The figure's look in these sections (all: everything but its size and what it shows)."""
    out = {}
    for sec in sections:
        for k in SECTIONS[sec]:
            if k in spec:
                out[k] = copy.deepcopy(spec[k])
            elif k in LOOK_DEFAULTS:
                out[k] = LOOK_DEFAULTS[k]
    return out


def smoothed(y, n: int) -> np.ndarray:
    """A line averaged over n neighbouring points (a moving average centred on each point, the
    ends over what there is); missing points are left out. Under 3: the line as it is."""
    y = np.asarray(y, float)
    n = int(n or 0)
    if n < 3 or y.size < 3:
        return y
    ok = np.isfinite(y)
    k = np.ones(min(n, y.size))
    total = np.convolve(np.where(ok, y, 0.0), k, mode="same")
    count = np.convolve(ok.astype(float), k, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(count > 0, total / np.maximum(count, 1), np.nan)
    out[~ok] = np.nan                                     # a gap stays a gap
    return out


def _box(spec: dict) -> str:
    """"open" (no top or right line), "closed" or "none" (no lines, the ticks stay)."""
    return spec.get("box") or ("open" if spec.get("despine", True) else "closed")


def _marker_kw(spec: dict, color, ms: float, lw: float) -> dict:
    """matplotlib's marker keywords for the figure's point shape."""
    shape = spec.get("marker", "circle")
    kw = {"marker": MARKERS.get(shape, "o"), "markersize": ms, "markeredgewidth": 0}
    if shape == "open":
        kw.update(markerfacecolor="white", markeredgecolor=color, markeredgewidth=max(0.5, lw * 0.8))
    return kw


def _series_color(spec: dict, given: str, group_color, k: int) -> str:
    """A series' color: its own, else the palette's (when one is picked), else its group's."""
    pal = PALETTES.get(spec.get("palette") or "")
    if given:
        return given
    if pal:
        return pal[k % len(pal)]
    return group_color or PALETTE[k % len(PALETTE)]
FITS = {"": "None", **fitting.MODELS}


FIT_LOOK = {"fit_color": "#c62828", "fit_width": 1.5,    # the fitted curve: its color, width (pt), line
            "fit_style": "dash", "fit_show": [], "fit_where": "tl",   # and what's written beside it, where
            "fit_span": "plot"}                       # drawn across the whole plot, or just the fitted points
FIT_SPANS = {"plot": "Whole plot", "data": "Fitted points"}
FIT_STYLES = {"dash": "Dashed", "solid": "Solid", "dot": "Dotted"}
FIT_SHOW = {"eq": "Function", "coef": "Coefficients", "r2": "R²", "r": "R"}
FIT_WHERE = {"tl": "Top left", "tr": "Top right", "bl": "Bottom left", "br": "Bottom right"}
_MPL_STYLE = {"dash": "--", "solid": "-", "dot": ":"}


def fit_R(f) -> float:
    """R: for a straight line, the correlation coefficient (its sign the slope's); for any
    other model, √R² (how well the curve follows the points)."""
    r = float(np.sqrt(max(f.r2, 0.0)))
    if f.model == "poly" and len(f.params) == 2:
        r *= 1.0 if f.params[1] >= 0 else -1.0
    return r


def fit_text(spec: dict, f) -> list[str]:
    """What's written by the fitted curve, line by line, as chosen (Fit tab): the function,
    each coefficient ± its standard error, R², R."""
    show = set(spec.get("fit_show") or [])
    out = [f.equation] if "eq" in show else []
    if "coef" in show:
        out += [f"{n} = {v:.4g} ± {e:.2g}" for n, v, e in f.table()]
    if "r2" in show:
        out.append(f"R² = {f.r2:.4f}")
    if "r" in show:
        out.append(f"R = {fit_R(f):.4f}")
    return out


def new_spec(x: str = "", y: str = "", kind: str = "xy") -> dict:
    return {"kind": kind, "series": [new_series(x, y)] if y else [], "title": "", "xlabel": "", "ylabel": "",
            "xlim": [None, None], "ylim": [None, None], "xlog": False, "ylog": False,
            "font": 9.0, "font_ticks": 0.0, "font_title": 0.0, "ticks": "out", "tick_len": 3.5,
            "lw": 1.2, "ms": 4.0, "grid": False, "legend": "best", "despine": True,
            "zero": False, "fit": "", "fit_degree": 1, "fit_line": 0, "fit_x": [None, None],
            **{k: list(v) if isinstance(v, list) else v for k, v in FIT_LOOK.items()},
            "w_mm": 80.0, "h_mm": 60.0, "points": True}


def new_series(x: str, y: str) -> dict:
    return {"x": x, "y": y, "by_group": True, "style": "markers", "average": "", "color": "", "label": ""}


def clone(spec: dict) -> dict:
    return copy.deepcopy(spec)


_SUB = re.compile(r"(?<![\w$\\])([^\W\d_]+)_(\w+)")


def mathify(text: str) -> str:
    """Plain label text → matplotlib text with true subscripts: "I_B (pA)" → "$\\mathregular{I_{B}}$ (pA)"."""
    if not text or "$" in text:
        return text or ""
    return _SUB.sub(lambda m: f"$\\mathregular{{{m.group(1)}_{{{m.group(2)}}}}}$", text)


def default_label(col: str, data: dict) -> str:
    u = (data.get("units") or {}).get(col, "")
    return f"{col} ({u})" if u else col


def columns_used(spec: dict) -> list[str]:
    out = []
    for s in spec.get("series", []):
        out += [c for c in (s.get("x"), s.get("y")) if c]
    return list(dict.fromkeys(out))


def _xy(data: dict, s: dict):
    cols = data.get("cols") or {}
    y = np.asarray(cols.get(s["y"], []), float)
    x = np.asarray(cols.get(s["x"], []), float) if s.get("x") else np.arange(1, len(y) + 1, dtype=float)
    n = min(len(x), len(y))
    return x[:n], y[:n]


def _groups(data: dict, n: int, by_group: bool):
    groups = list(data.get("groups") or [])[:n]
    if not by_group or len(groups) < n:
        return [("", np.ones(n, bool))]
    names = list(dict.fromkeys(groups))
    g = np.array(groups, object)
    return [(name, g == name) for name in names]


def _average(x, y, how: str):
    ux = np.unique(x[np.isfinite(x)])
    m, e = [], []
    for v in ux:
        yy = y[(x == v) & np.isfinite(y)]
        m.append(yy.mean() if yy.size else np.nan)
        if how == "sd":
            e.append(yy.std(ddof=1) if yy.size > 1 else np.nan)
        elif how == "sem":
            e.append(yy.std(ddof=1) / np.sqrt(yy.size) if yy.size > 1 else np.nan)
        else:
            e.append(np.nan)
    return ux, np.array(m), np.array(e)


def draw(fig, spec: dict, data: dict, ax=None, letter: str = "") -> None:
    """Draw the figure into a matplotlib Figure (a new axes filling it), or into `ax`."""
    import matplotlib as mpl
    spec, data, k = _scaled(spec, data)
    font = float(spec.get("font", 9.0))
    ticks_pt = float(spec.get("font_ticks") or font * 0.9)
    title_pt = float(spec.get("font_title") or font * 1.1)
    legend_pt = float(spec.get("font_legend") or font * 0.85)
    way = spec.get("ticks", "out")                    # tick marks outside, inside or across the axis
    axw = float(spec.get("axw", LOOK_DEFAULTS["axw"]))
    bold = spec.get("bold", "none")
    family = spec.get("family") or LOOK_DEFAULTS["family"]
    with mpl.rc_context({"font.size": font, "axes.labelsize": font, "axes.titlesize": title_pt,
                         "axes.titleweight": "bold" if bold in ("title", "labels") else "normal",
                         "axes.labelweight": "bold" if bold == "labels" else "normal",
                         "xtick.labelsize": ticks_pt, "ytick.labelsize": ticks_pt,
                         "xtick.direction": way, "ytick.direction": way,
                         "xtick.major.size": float(spec.get("tick_len", 3.5)),
                         "ytick.major.size": float(spec.get("tick_len", 3.5)),
                         "xtick.major.width": axw, "ytick.major.width": axw,
                         "xtick.major.pad": 3.5 * k, "ytick.major.pad": 3.5 * k, "axes.labelpad": 4.0 * k,
                         "axes.titlepad": 6.0 * k, "axes.linewidth": axw,
                         "legend.fontsize": legend_pt,
                         "font.family": [family, "Arial", "Liberation Sans", "DejaVu Sans"],   # the face, by name
                         # (a generic "sans-serif" is looked up when drawn, after this context)
                         "mathtext.default": "regular", "svg.fonttype": "none", "pdf.fonttype": 42}):
        if ax is None:
            fig.clear()
            ax = fig.add_subplot(111)
        _draw(ax, spec, data)
        if letter:
            ax.text(-0.18, 1.06, letter, transform=ax.transAxes, fontsize=font * 1.4,
                    fontweight="bold", va="bottom", ha="left")


TEXT_KEYS = ("font", "font_ticks", "font_title")


def _base_px(data: dict | None) -> float:
    """The width a chart drew its plain lines on screen (the most common): Lines is theirs."""
    widths = [float(c["width_px"]) for c in (data or {}).get("curves", []) if c.get("width_px")]
    if not widths:
        return 1.0
    import statistics
    return float(statistics.median(widths)) or 1.0


def on_page(spec: dict, data: dict | None = None) -> dict:
    """The figure with its text, lines and tick marks in pt on the page, whatever size it's
    drawn: made from a live chart they scaled with its width (see _scaled); on the Plot Canvas
    they stay put, so the same size prints the same on every figure and resizing one doesn't
    change them. Lines (lw) is then what a plain line prints at; lines the chart drew thicker
    keep their ratio to it. Converted once, at the size it has now (so it looks the same),
    and marked on_page."""
    if spec.get("on_page"):
        return spec
    spec = dict(spec)
    w0, w = spec.get("w0_mm"), spec.get("w_mm")
    k = float(w) / float(w0) if w0 and w else 1.0
    for key in TEXT_KEYS + ("tick_len",):
        if spec.get(key):
            spec[key] = round(float(spec[key]) * k, 2)
    lw = float(spec.get("lw", 1.2) or 1.2)
    if data and data.get("curves") and any(c.get("width_px") for c in data["curves"]):
        lw = _base_px(data) * float(data.get("pt_px", 0.35)) * lw / float(data.get("lw0", lw) or lw)
    spec["lw"] = round(lw * k, 2)
    spec["on_page"] = True
    return spec


def with_text(spec: dict, pt: float) -> dict:
    """The figure with its labels at `pt` on the page (on_page), its numbers and title
    keeping their size against the labels (or Auto)."""
    spec = on_page(spec)
    old = float(spec.get("font") or 9.0)
    spec = dict(spec, font=float(pt))
    for key in ("font_ticks", "font_title"):
        if spec.get(key):
            spec[key] = round(float(spec[key]) * float(pt) / old, 2)
    return spec


def _scaled(spec: dict, data: dict) -> tuple[dict, dict, float]:
    """A figure made from a live chart is designed at the chart's size on screen (w0_mm), so
    its text, lines and markers keep the chart's proportions; drawn at another size (w_mm),
    all of them scale with it, as a picture would. On the page (on_page), only markers do:
    text, lines and ticks stay the pt they're set to."""
    w0 = spec.get("w0_mm")
    if not w0 or not spec.get("w_mm"):
        return spec, data, 1.0
    k = float(spec["w_mm"]) / float(w0)
    if abs(k - 1.0) < 1e-6:
        return spec, data, 1.0
    spec = dict(spec)
    for key in (("ms",) if spec.get("on_page") else TEXT_KEYS + ("font_legend", "tick_len", "lw", "ms")):
        if spec.get(key):
            spec[key] = float(spec[key]) * k
    data = dict(data)
    if data.get("pt_px"):
        data["pt_px"] = float(data["pt_px"]) * k
    if data.get("lw0"):
        data["lw0"] = float(data["lw0"]) * k
    return spec, data, (1.0 if spec.get("on_page") else k)     # axes, ticks and gaps: pt on the page


def _draw(ax, spec: dict, data: dict) -> None:
    lw, ms = float(spec.get("lw", 1.2)), float(spec.get("ms", 4.0))
    colors = data.get("colors") or {}
    k = 0
    series = spec.get("series", [])
    if spec.get("kind") == "image":
        _image(ax, data)
        return
    if spec.get("kind") == "bar":
        _bars(ax, spec, data, lw, ms)
    elif spec.get("kind") == "curves":
        _curves(ax, spec, data, lw)
        _marks_and_lines(ax, spec, data, lw)
    if spec.get("fit") and spec.get("kind") != "bar":
        got = do_fit(spec, data)
        if got is not None:
            _, f, x = got
            xl, yl = ax.get_xlim(), ax.get_ylim()
            lim = [v for v in (spec.get("xlim") or [None, None])]
            lo, hi = (lim[0] if lim[0] is not None else xl[0], lim[1] if lim[1] is not None else xl[1]) \
                if spec.get("fit_span", "plot") == "plot" else (np.nanmin(x), np.nanmax(x))
            xs = np.geomspace(lo, hi, 300) if spec.get("xlog") and lo > 0 else np.linspace(lo, hi, 300)
            color = spec.get("fit_color") or FIT_LOOK["fit_color"]
            ax.plot(xs, f.predict(xs), _MPL_STYLE.get(spec.get("fit_style"), "--"), color=color,
                    linewidth=float(spec.get("fit_width", FIT_LOOK["fit_width"])), zorder=8, label="fit")
            ax.set_xlim(xl)                           # the curve doesn't move the axes
            ax.set_ylim(yl)
            text = fit_text(spec, f)
            if text:
                where = spec.get("fit_where", "tl")
                ax.text(0.97 if "r" in where else 0.03, 0.03 if "b" in where else 0.97, "\n".join(text),
                        transform=ax.transAxes, ha="right" if "r" in where else "left",
                        va="bottom" if "b" in where else "top", color=color, zorder=9,
                        fontsize=float(spec.get("font", 9) or 9) * 0.9,
                        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 2})
    else:
        for s in series:
            x, y = _xy(data, s)
            for gname, m in _groups(data, len(y), s.get("by_group", True)):
                color = _series_color(spec, s.get("color"), colors.get(gname), k)
                k += 1
                label = s.get("label") or (gname if gname and len(series) == 1 else
                                           (f"{s['y']} · {gname}" if gname else s["y"]))
                xs, ys = x[m], y[m]
                if s.get("average"):
                    xs, ys, es = _average(xs, ys, s["average"])
                    how = spec.get("err_style", "bars")
                    if s["average"] in ("sem", "sd") and how == "band":
                        ax.fill_between(xs, ys - es, ys + es, color=color, alpha=0.2, linewidth=0, zorder=1)
                    elif s["average"] in ("sem", "sd") and how != "none":
                        ax.errorbar(xs, ys, yerr=es, fmt="none", ecolor=color, elinewidth=lw * 0.8,
                                    capsize=float(spec.get("cap") or ms * 0.6))
                else:
                    order = np.argsort(xs, kind="stable") if s.get("style") != "markers" else slice(None)
                    xs, ys = xs[order], ys[order]
                st = s.get("style", "markers")
                n = int(spec.get("smooth") or 0)
                if n >= 3 and st in ("line", "both"):    # the line smoothed; the points stay where they are
                    ax.plot(xs, smoothed(ys, n), linestyle="-", linewidth=lw, color=color, label=mathify(label))
                    if st == "both":
                        ax.plot(xs, ys, linestyle="none", color=color, **_marker_kw(spec, color, ms, lw))
                else:
                    kw = _marker_kw(spec, color, ms, lw) if st in ("markers", "both") else {"marker": None}
                    ax.plot(xs, ys, linestyle="-" if st in ("line", "both") else "none", linewidth=lw,
                            color=color, label=mathify(label), **kw)
        if spec.get("xlog"):
            ax.set_xscale("log")
    if spec.get("ylog"):
        ax.set_yscale("log")
    if spec.get("zero"):
        ax.axhline(0, color="0.6", linewidth=0.6, zorder=0)
        if spec.get("kind") != "bar":
            ax.axvline(0, color="0.6", linewidth=0.6, zorder=0)
    first = series[0] if series else {}
    if spec.get("kind") == "curves":
        first = {}
    xl = spec.get("xlabel") or data.get("xlabel") or (default_label(first.get("x"), data) if first.get("x") else
                                ("" if spec.get("kind") == "bar" else "Trial"))
    yl = spec.get("ylabel") or data.get("ylabel") or (default_label(first.get("y"), data) if first.get("y") else "")
    ax.set_xlabel(mathify(xl))
    ax.set_ylabel(mathify(yl))
    if spec.get("title"):
        ax.set_title(mathify(spec["title"]))
    for lim, setter in ((spec.get("xlim"), ax.set_xlim), (spec.get("ylim"), ax.set_ylim)):
        if lim and any(v is not None for v in lim):
            setter(*[v if v is not None else None for v in lim])
    for key, axis_, log in (("xticks", ax.xaxis, spec.get("xlog")), ("yticks", ax.yaxis, spec.get("ylog"))):
        if data.get(key) and data.get(key + "_text") and not log:     # its named ticks, each with its name
            axis_.set_ticks(data[key], data[key + "_text"])
            continue
        if data.get(key) and not log:                     # a live chart's own tick numbers
            lo_, hi_ = sorted((ax.get_xlim() if key == "xticks" else ax.get_ylim()))
            vals = [v for v in data[key] if lo_ - 1e-9 * abs(hi_ - lo_) <= v <= hi_ + 1e-9 * abs(hi_ - lo_)]
            if len(vals) >= 2:
                axis_.set_ticks(vals)
    if spec.get("nticks"):                                # about this many numbers along each axis
        from matplotlib.ticker import MaxNLocator
        for axis_, log in ((ax.xaxis, spec.get("xlog")), (ax.yaxis, spec.get("ylog"))):
            if not log and not (spec.get("kind") == "bar" and axis_ is ax.xaxis):
                axis_.set_major_locator(MaxNLocator(nbins=max(1, int(spec["nticks"]) - 1), min_n_ticks=2))
    grid = spec.get("grid")
    if grid:                                              # True (as before): both ways
        ax.grid(True, axis="y" if grid == "y" else "both", linewidth=0.4, alpha=0.5)
    box = _box(spec)
    for side, sp in ax.spines.items():
        sp.set_visible(box == "closed" or (box == "open" and side in ("left", "bottom")))
        sp.set_linewidth(float(spec.get("axw", LOOK_DEFAULTS["axw"])))
    if data.get("scalebar") is not None:
        _scale_bar(ax, spec, data)
    leg = spec.get("legend", "best")
    handles, labels = ax.get_legend_handles_labels()
    if leg != "off" and len(labels) > 1:
        frame = bool(spec.get("legend_frame"))
        if leg == "outside":
            ax.legend(frameon=frame, loc="upper left", bbox_to_anchor=(1.02, 1))
        else:
            ax.legend(frameon=frame, loc=leg)


def _bars(ax, spec, data, lw, ms):
    colors = data.get("colors") or {}
    series = spec.get("series", [])
    names = []
    width = 0.8 / max(1, len(series))
    for j, s in enumerate(series):
        _, y = _xy(data, s)
        groups = _groups(data, len(y), True)
        names = [g or "All" for g, _ in groups]
        for k, (g, m) in enumerate(groups):
            yy = y[m]
            yy = yy[np.isfinite(yy)]
            mean = yy.mean() if yy.size else np.nan
            how = s.get("average") or "sem"
            err = (yy.std(ddof=1) / (np.sqrt(yy.size) if how == "sem" else 1)) if yy.size > 1 else np.nan
            color = _series_color(spec, s.get("color"), colors.get(g), k)
            xpos = k + (j - (len(series) - 1) / 2) * width
            ax.bar(xpos, mean, width * 0.9, color=color, alpha=0.75, linewidth=0,
                   label=mathify(s.get("label") or s["y"]) if k == 0 and len(series) > 1 else None)
            if how in ("sem", "sd") and spec.get("err_style", "bars") != "none":
                ax.errorbar(xpos, mean, yerr=err, fmt="none", ecolor="0.15", elinewidth=lw * 0.8,
                            capsize=float(spec.get("cap") or ms * 0.8))
            if spec.get("points", True) and yy.size and spec.get("marker", "circle") != "none":
                jitter = (np.random.default_rng(k).random(yy.size) - 0.5) * width * 0.4
                ax.plot(xpos + jitter, yy, linestyle="none", color="0.15",
                        **_marker_kw(spec, "0.15", ms * 0.6, lw))
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([mathify(n) for n in names])


def _curves(ax, spec, data, lw):
    """A whiteboard chart's lines, as they were when it was made a figure (and the shaded bands
    around its averages)."""
    for f in data.get("fills", []):
        ax.fill_between(np.asarray(f["x"], float), np.asarray(f["lo"], float), np.asarray(f["hi"], float),
                        color=f.get("color") or "#888888", alpha=float(f.get("alpha", 0.2)), linewidth=0,
                        zorder=1)
    for k, c in enumerate(data.get("curves", [])):
        x, y = np.asarray(c["x"], float), np.asarray(c["y"], float)
        st = "o" if c.get("points") else None
        if "width_px" in c and spec.get("on_page"):   # Lines is a plain line's; thicker ones keep their ratio
            width = lw * float(c["width_px"]) / _base_px(data)
        elif "width_px" in c:                     # as on screen; Lines (lw) scales from what it was then
            width = c["width_px"] * float(data.get("pt_px", 0.35)) * lw / float(data.get("lw0", lw) or lw)
        else:                                     # an older figure
            width = lw * c.get("width", 1.0)
        if not c.get("points"):
            y = smoothed(y, int(spec.get("smooth") or 0))       # Smooth lines (Off: as drawn)
        ax.plot(x, y, color=c.get("color") or PALETTE[k % len(PALETTE)], linewidth=width,
                alpha=float(c.get("alpha", 1.0)), marker=st, markersize=float(spec.get("ms", 4)),
                linestyle="none" if c.get("points") else "-", markeredgewidth=0,
                label=mathify(c.get("name", "")) or None)


def _scale_bar(ax, spec, data) -> None:
    """The chart's scale bar in place of its axes: an L just outside the plot's bottom left
    corner, a round length of each unit, labelled."""
    sb = data["scalebar"]
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.grid(False)
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    xl = sb["dx"] / (x1 - x0) if sb.get("dx") and x1 > x0 else 0.0
    yl = sb["dy"] / (y1 - y0) if sb.get("dy") and y1 > y0 else 0.0
    gx, gy = sb.get("gap", (0.0, 0.0))                   # where it was dragged to on the chart
    c0, c1 = -0.02 + gx, -0.03 + gy
    width = 2.5 * float(data.get("pt_px", 0.75))
    size = float(spec.get("font", 9.0)) * 0.85
    kw = dict(transform=ax.transAxes, clip_on=False, color="black", linewidth=width, solid_capstyle="butt")
    if xl:
        ax.plot([c0, c0 + xl], [c1, c1], **kw)
        ax.text(c0 + xl / 2, c1 - 0.015, sb.get("x_text", ""), transform=ax.transAxes, ha="center", va="top",
                fontsize=size, fontweight="bold")
    if yl:
        ax.plot([c0, c0], [c1, c1 + yl], **kw)
        ax.text(c0 - 0.015, c1 + yl / 2, sb.get("y_text", ""), transform=ax.transAxes, ha="right", va="center",
                rotation=90, fontsize=size, fontweight="bold")


def _marks_and_lines(ax, spec, data, lw):
    """A chart's spike markers (each its unit's color and shape) and levels across (the threshold)."""
    for b in data.get("bands", []):                   # bars a fixed width on the page, as the raster's group colors
        from matplotlib.transforms import ScaledTranslation
        w = float(b.get("w_px", 6.0)) * float(data.get("pt_px", 0.75))     # screen pixels to points
        tr = ax.transData + ScaledTranslation(w / 2 / 72, 0, ax.figure.dpi_scale_trans)
        for x0, y0, h, c in zip(b["x0"], b["y0"], b["h"], b["colors"]):
            ax.plot([x0, x0], [y0, y0 + h], color=c, linewidth=w, solid_capstyle="butt", transform=tr, zorder=5)
    for m in data.get("marks", []):
        sym = np.asarray(m.get("symbols", []), object)
        x, y = np.asarray(m["x"], float), np.asarray(m["y"], float)
        cols, alphas = m.get("colors", []), m.get("alpha", [])
        size = float(m.get("size", 7.0)) * float(data.get("pt_px", 0.75))   # screen pixels to points
        for s_ in (set(sym.tolist()) if len(sym) else {"o"}):
            k = np.flatnonzero(sym == s_) if len(sym) else np.arange(len(x))
            from matplotlib.colors import to_rgba
            rgba = [to_rgba(cols[i] if i < len(cols) else "#d9822b", alphas[i] if i < len(alphas) else 1.0)
                    for i in k]
            ax.scatter(x[k], y[k], s=size ** 2, c=rgba, marker=_SYMBOLS.get(s_, "o"), linewidths=0, zorder=5)
    lo, hi = spec.get("ylim") or [None, None]
    if lo is None or hi is None:                      # automatic: a level off the traces doesn't stretch the axis
        d0, d1 = ax.get_ylim()
        pad = 0.05 * (d1 - d0)
        lo = d0 - pad if lo is None else lo
        hi = d1 + pad if hi is None else hi
    for ln in data.get("lines", []):
        if (lo is not None and ln["y"] < lo) or (hi is not None and ln["y"] > hi):
            continue                                  # off the plot: no line, and no label out in the margin
        ax.axhline(ln["y"], color=ln.get("color") or "#c62828", linewidth=lw * 0.8,
                   linestyle="--" if ln.get("dash") else "-", zorder=4)
        if ln.get("label"):
            ax.annotate(ln["label"], xy=(1, ln["y"]), xycoords=("axes fraction", "data"), ha="right",
                        va="bottom", color=ln.get("color") or "#c62828", fontsize=float(spec.get("font", 9)) * 0.85,
                        annotation_clip=True)


def _image(ax, data):
    """An image put on the Plot Canvas (kept as PNG bytes, base64)."""
    import base64
    import io as _io
    from matplotlib import image as mimg
    arr = mimg.imread(_io.BytesIO(base64.b64decode(data.get("png", ""))), format="png")
    ax.imshow(arr, interpolation="lanczos")
    ax.set_axis_off()


def _decimate(x: np.ndarray, y: np.ndarray, n: int = 4000) -> tuple[np.ndarray, np.ndarray]:
    """At most about 2n points, keeping each stretch's min and max, so spikes keep their height."""
    if len(x) <= 2 * n:
        return x, y
    edges = np.linspace(0, len(x), n + 1).astype(int)
    xs, ys = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        seg = y[a:b]
        ok = np.isfinite(seg)
        if not ok.any():
            xs += [x[a]]
            ys += [np.nan]
            continue
        i, j = a + int(np.nanargmin(seg)), a + int(np.nanargmax(seg))
        for k in sorted((i, j)):
            xs.append(x[k])
            ys.append(y[k])
    return np.asarray(xs), np.asarray(ys)


def _marker_px(it, d) -> float:
    """A scatter's marker size in screen pixels (each point's, else the item's own)."""
    sizes = np.asarray(d["size"], float) if len(d["size"]) else np.empty(0)
    sizes = sizes[sizes > 0]
    if sizes.size:
        return float(np.median(sizes))
    return float(it.opts.get("size", 7) or 7)


def paper_font(spec: dict) -> float:
    """The label size in pt as it prints, at the figure's width."""
    f = float(spec.get("font", 9.0))
    if spec.get("w0_mm") and spec.get("w_mm") and not spec.get("on_page"):
        f *= float(spec["w_mm"]) / float(spec["w0_mm"])
    return f


def _pt(css, default: float, dpi: float = 72.0) -> float:
    """A CSS font size ('9pt', '12px') in pt: a pixel is 72/dpi pt (1 on a Mac, 0.75 at
    Windows' 96 dpi)."""
    if not css:
        return float(default)
    m = re.match(r"\s*([\d.]+)\s*(pt|px)?", str(css))
    if not m:
        return float(default)
    return float(m.group(1)) * (72.0 / dpi if m.group(2) == "px" and dpi > 0 else 1.0)


def from_chart(chart, max_points: int = 4000, view: bool = True) -> tuple[dict, dict]:
    """(spec, data): a whiteboard chart's first plot as a figure, looking as it does: every line
    (each trial of a group too) in its color, opacity and width; the axes in the units shown
    (mV, ms…), over the same range; its grid."""
    import pyqtgraph as pg
    plots = chart.plots()
    if not plots:
        raise ValueError("this chart has nothing to make a figure of")
    pis = [p.getPlotItem() if hasattr(p, "getPlotItem") else p for p in plots]
    item = pis[chart.main_plot()] if hasattr(chart, "main_plot") else pis[0]   # the spike channel, else the first
    left = item.getAxis("left")
    bottom = next((p.getAxis("bottom") for p in reversed(pis) if (p.getAxis("bottom").labelText or "").strip()),
                  item.getAxis("bottom"))             # stacked charts label time on the last plot only

    def scale(ax) -> float:                           # the SI prefix the axis shows (×1000 for mV)
        k = getattr(ax, "autoSIPrefixScale", 1.0) if getattr(ax, "autoSIPrefix", False) else 1.0
        return float(k or 1.0) * float(getattr(ax, "scale", 1.0) or 1.0)
    kx, ky = scale(bottom), scale(left)
    prefixes = {}
    if not view:
        # drawn off screen, its axes never chose their units: choose them as pyqtgraph would, from
        # the size of the numbers (V → mV for millivolts, s stays s); the range is the data's
        def biggest(axis: int) -> float:
            m = 0.0
            for it in item.items:
                if not isinstance(it, pg.PlotDataItem) or not it.isVisible():
                    continue
                if hasattr(it, "_Y"):
                    X, Y = it.getOriginalDataset()
                    v = (X if axis == 0 else Y)
                elif getattr(it, "_full", None) is not None:
                    v = it._full[axis]
                else:
                    got = it.getData()
                    v = got[axis] if got[axis] is not None else np.empty(0)
                v = np.asarray(v, float)
                if v.size and np.isfinite(v).any():
                    m = max(m, float(np.nanmax(np.abs(v))))
            return m
        for ax_, k_, axis in ((bottom, "x", 0), (left, "y", 1)):
            if (ax_.labelUnits or "").strip():
                sc, pre = pg.siScale(biggest(axis) or 1.0)
                prefixes[k_] = pre
                if k_ == "x":
                    kx = sc
                else:
                    ky = sc
            else:
                prefixes[k_] = ""
                if k_ == "x":
                    kx = 1.0
                else:
                    ky = 1.0
    curves, marks, lines, bands, fills = [], [], [], [], []
    for it in item.items:
        if isinstance(it, pg.FillBetweenItem) and it.isVisible():
            edges = getattr(it, "curves", None) or ()     # a shaded band (an average's ± SD): its two edges
            got = []
            for c in edges[:2]:
                xy = c.getOriginalDataset() if hasattr(c, "getOriginalDataset") else c.getData()
                got.append((np.asarray(xy[0], float), np.asarray(xy[1], float)) if xy[0] is not None else None)
            if len(got) == 2 and all(g is not None and len(g[0]) for g in got) and len(got[0][0]) == len(got[1][0]):
                (x, y1), (_, y2) = got
                step = max(1, int(np.ceil(len(x) / (2 * max_points))))      # the same points of each edge
                c = it.brush().color()
                fills.append({"x": [float(v) for v in x[::step] * kx], "lo": [float(v) for v in y2[::step] * ky],
                              "hi": [float(v) for v in y1[::step] * ky], "color": c.name(),
                              "alpha": round(c.alphaF(), 3), "z": float(it.zValue())})
            continue
        if isinstance(it, pg.BarGraphItem) and it.isVisible() and len(it.opts.get("x0") or []):
            o = it.opts                                   # the raster's group colors: each bar its row, a fixed width
            px = item.getViewBox().viewPixelSize()[0] or 1.0
            n = len(o["x0"])
            bands.append({"x0": [float(v) * kx for v in o["x0"]], "y0": [float(v) * ky for v in o["y0"]],
                          "h": [float(o["height"]) * ky] * n, "w_px": float(o["width"]) / px,
                          "colors": [pg.mkBrush(b).color().name() for b in (o.get("brushes") or [])]})
            continue
        if isinstance(it, pg.ScatterPlotItem) and it.isVisible() and len(it.data):
            d = it.data                                   # spike markers: each point its color and shape
            cols = [pg.mkBrush(b).color() for b in d["brush"]]
            marks.append({"x": [float(v) * kx for v in d["x"]], "y": [float(v) * ky for v in d["y"]],
                          "colors": [c.name() for c in cols], "alpha": [round(c.alphaF(), 3) for c in cols],
                          "symbols": [str(sym) for sym in d["symbol"]],
                          "size": _marker_px(it, d)})
            continue
        if isinstance(it, pg.InfiniteLine) and it.isVisible() and it.angle % 180 == 0:
            pen = it.pen                                  # a level across, as the spike threshold
            lines.append({"y": float(it.value()) * ky, "color": pen.color().name(),
                          "dash": pen.style() != QtCore_SolidLine(), "label": ""})
            continue
        if not isinstance(it, pg.PlotDataItem) or not it.isVisible():
            continue
        pen = it.opts.get("pen")
        p = pg.mkPen(pen) if pen is not None else None
        color = p.color() if p is not None else None
        sym = it.opts.get("symbol") is not None and (p is None or p.style() == QtCore_NoPen())
        if p is not None and p.style() == QtCore_NoPen() and it.opts.get("symbol") is None:
            continue                                      # drawn as nothing: a shaded band's edge
        if color is None or not color.alpha() or (p is not None and p.style() == QtCore_NoPen()):
            brush = it.opts.get("symbolBrush")
            color = pg.mkBrush(brush).color() if brush is not None else color
        width = (p.widthF() or 1.0) if p is not None else 1.0
        if hasattr(it, "_Y"):                          # many trials as one item: each its own line
            X, Y = it.getOriginalDataset()
            rows = [(X, row) for row in np.atleast_2d(Y)]
        elif getattr(it, "_full", None) is not None:
            rows = [it._full]
        else:
            x, y = it.getData()
            rows = [(x, y)] if x is not None and y is not None else []
        for x, y in rows:
            x, y = np.asarray(x, float), np.asarray(y, float)
            if not len(x):
                continue
            x, y = _decimate(x, y, max_points)
            curves.append({"x": [float(v) for v in x * kx], "y": [float(v) for v in y * ky],
                           "color": color.name() if color is not None else "",
                           "alpha": round(color.alphaF(), 3) if color is not None else 1.0,
                           "name": it.name() or "", "width_px": round(width, 3), "points": bool(sym),
                           "z": float(it.zValue())})
    curves.sort(key=lambda c: c["z"])

    def label(ax) -> str:
        text = (ax.labelText or "").strip()
        units = (ax.labelUnits or "").strip()
        if not view:
            prefix = prefixes.get("x" if ax is bottom else "y", "") if units else ""
        else:
            prefix = (getattr(ax, "labelUnitPrefix", "") or "") if units else ""
        return f"{text} ({prefix}{units})" if units else text
    (x0, x1), (y0, y1) = item.getViewBox().viewRange()
    spec = new_spec(kind="curves")
    # no title by default: the chart's own (its channel, Ch2) only if you typed it on the chart;
    # a title set in its look (Single Plot) is added below. And the shape it has on screen.
    own = (item.titleLabel.text or "").strip() if getattr(item, "titleLabel", None) is not None else ""
    typed = any(k.endswith(":title") and v for k, v in (getattr(chart, "look", None) or {}).get("texts", {}).items())
    spec["title"] = re.sub("<[^>]+>", "", own) if own and typed else ""
    pw = plots[pis.index(item)]                       # the whole plot on screen, axes and labels too
    wpx, hpx = (pw.width(), pw.height()) if hasattr(pw, "width") and callable(pw.width) else \
        (item.sceneBoundingRect().width(), item.sceneBoundingRect().height())
    dpi = float(pw.logicalDpiX()) if hasattr(pw, "logicalDpiX") else 96.0
    if wpx > 50 and hpx > 50:
        spec["w0_mm"] = wpx / dpi * 25.4                 # drawn at its size on screen…
        spec["w_mm"] = 120.0                              # …shown at this width, everything scaled with it
        spec["h_mm"] = round(120.0 * hpx / wpx, 1)
    # its text as the chart draws it (a size you set in its look wins, below)
    from pyqtgraph.Qt import QtGui as _G
    app_pt = _G.QFont().pointSizeF() or 9.0
    tick_font = left.style.get("tickFont")
    spec["font_ticks"] = float(tick_font.pointSizeF()) if tick_font is not None and tick_font.pointSizeF() > 0 \
        else float(tick_font.pixelSize()) * 72.0 / dpi if tick_font is not None and tick_font.pixelSize() > 0 else app_pt
    css = getattr(left, "labelStyle", {}) or {}
    spec["font"] = _pt(css.get("font-size"), app_pt, dpi)
    title = getattr(item, "titleLabel", None)
    spec["font_title"] = _pt(title.opts.get("size") if title is not None else None, app_pt, dpi)
    if view:
        spec["xlim"], spec["ylim"] = [x0 * kx, x1 * kx], [y0 * ky, y1 * ky]     # the same view as the chart
    spec["grid"] = bool(item.ctrl.xGridCheck.isChecked() or item.ctrl.yGridCheck.isChecked()) \
        if hasattr(item, "ctrl") else False
    spec["lw"] = 1.2
    look = getattr(chart, "look", None)
    if look is not None:                                  # its look: the same record the chart draws from
        from . import chartstyle
        keys = chartstyle.figure_keys(look, kx, ky)
        if spec.get("w0_mm"):                             # text sizes you set are pt on the figure at its width
            w_fig = float(keys.get("w_mm") or spec["w_mm"])
            for key in ("font", "font_ticks", "font_title"):
                if key in keys:
                    keys[key] = keys[key] * float(spec["w0_mm"]) / w_fig
        spec.update(keys)
        if wpx > 50 and hpx > 50:
            spec["h_mm"] = round(float(spec["w_mm"]) * hpx / wpx, 1)
    # lines and markers keep their size against the plot: a screen pixel of a plot wpx wide is
    # w_mm / wpx of the figure (fonts stay in pt, to be read on paper)
    pt_px = 72.0 / dpi if wpx > 50 else 0.35            # at the design size: a screen pixel in pt
    if not spec.get("w0_mm"):
        spec["w0_mm"] = None
    for ln in lines:                                      # its label, if the chart wrote one (threshold 340 mV)
        for it in item.items:
            if isinstance(it, pg.InfiniteLine) and abs(float(it.value()) * ky - ln["y"]) < 1e-12:
                lab = next((c for c in it.childItems() if isinstance(c, pg.InfLineLabel)), None)
                if lab is not None and lab.isVisible():
                    ln["label"] = lab.toPlainText()
    data_ticks = {}
    bar = getattr(item, "_scale_bar", None)
    if bar is not None or not left.isVisible():           # axes swapped for a scale bar: the figure too
        data_ticks["scalebar"] = {"dx": float(getattr(bar, "dx", 0.0)) * kx, "dy": float(getattr(bar, "dy", 0.0)) * ky,
                                  "x_text": getattr(bar, "x_text", ""), "y_text": getattr(bar, "y_text", ""),
                                  "font_px": float(getattr(bar, "font_px", 11.0))}
        gx, gy = getattr(bar, "gap", (0.0, 0.0))          # dragged from its corner: as a share of the plot
        vw, vh = item.vb.width(), item.vb.height()
        if vw > 0 and vh > 0:
            data_ticks["scalebar"]["gap"] = (float(gx) / vw, -float(gy) / vh)
    for key, ax_, k_, length in (("xticks", bottom, kx, bottom.width()), ("yticks", left, ky, left.height())):
        if "scalebar" in data_ticks:
            break
        custom = getattr(ax_, "_tickLevels", None)       # ticks of its own, named (groups, 90°)
        if custom and custom[0]:
            data_ticks[key] = [round(float(v) * k_, 12) for v, _ in custom[0]]
            data_ticks[key + "_text"] = [str(s) for _, s in custom[0]]
            continue
        try:                                              # the numbers the chart labels its axes with
            lo_, hi_ = ax_.range
            levels = ax_.tickValues(lo_, hi_, length)
            vals = list(levels[0][1]) if levels else []
            if len(levels) > 1 and levels[1][1]:          # the finer level too, only if its numbers fit
                fine = sorted(vals + list(levels[1][1]))  # (as pyqtgraph labels it, at the tick font's size)
                from pyqtgraph.Qt import QtGui
                fm = QtGui.QFontMetricsF(ax_.style.get("tickFont") or QtGui.QFont())
                if key == "xticks":
                    texts = ax_.tickStrings(fine, 1.0, levels[1][0])
                    need = max((fm.horizontalAdvance(s) for s in texts), default=0.0) + fm.height() * 0.5
                else:
                    need = fm.height() * 1.5
                if length / max(1, len(fine)) >= max(40.0, need):
                    vals = fine
            data_ticks[key] = [round(float(v) * k_, 12) for v in vals]
        except Exception:
            pass
    data = {"curves": curves, "fills": fills, "marks": marks, "bands": bands, **data_ticks, "lines": lines, "xlabel": label(bottom), "ylabel": label(left),
            "chart": getattr(chart, "name", ""), "kx": kx, "ky": ky, "plot": pis.index(item),
            "pt_px": pt_px, "lw0": float(spec.get("lw", 1.2))}
    sets = getattr(chart, "fit_sets", None)            # a chart of points: its series and groups, to fit
    if callable(sets):
        data["points"] = [{"name": n, "x": [float(v) * kx for v in x], "y": [float(v) * ky for v in y]}
                          for n, x, y in sets()]
    return spec, data


def QtCore_NoPen():
    from pyqtgraph.Qt import QtCore
    return QtCore.Qt.PenStyle.NoPen


def QtCore_SolidLine():
    from pyqtgraph.Qt import QtCore
    return QtCore.Qt.PenStyle.SolidLine


# pyqtgraph's marker shapes as matplotlib's (t: ▼, t1: ▲ …)
_SYMBOLS = {"o": "o", "t": "v", "t1": "^", "t2": ">", "t3": "<", "s": "s", "d": "D", "+": "+", "x": "x",
            "star": "*", "p": "p", "h": "h", "|": "|", "_": "_"}


def image_spec(png: bytes, w_px: int, h_px: int, width_mm: float = 80.0) -> tuple[dict, dict]:
    import base64
    spec = new_spec(kind="image")
    spec["w_mm"], spec["h_mm"] = width_mm, width_mm * h_px / max(1, w_px)
    return spec, {"png": base64.b64encode(png).decode()}


def fit_lines(spec: dict, data: dict) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """What a fit can go through: [(name, x, y)], each line or series of the figure (each group
    of a series split by group; a chart's every line, its averages first)."""
    out = []
    if spec.get("kind") == "curves" and "points" in data:     # a chart of points names what can be fitted
        return [(p["name"], np.asarray(p["x"], float), np.asarray(p["y"], float)) for p in data["points"]]
    if spec.get("kind") == "curves":
        curves = data.get("curves", [])
        bold = [k for k, c in enumerate(curves) if c.get("width", 1) >= max((d.get("width", 1) for d in curves),
                                                                          default=1)]
        order = bold + [k for k in range(len(curves)) if k not in bold]
        for n, k in enumerate(order):
            c = curves[k]
            name = c.get("name") or ("bold line" if k in bold else "line")
            out.append((f"{name} {n + 1}", np.asarray(c["x"], float), np.asarray(c["y"], float)))
        return out
    for s in spec.get("series", []):
        x, y = _xy(data, s)
        groups = _groups(data, len(y), s.get("by_group", True))
        if len(groups) > 1:                           # every point together first: an I–V line, say
            xs, ys = (_average(x, y, s["average"])[:2] if s.get("average") else (x, y))
            out.append((f"{s['y']} · all points" + (f" against {s['x']}" if s.get("x") else ""), xs, ys))
        for gname, m in groups:
            xs, ys = x[m], y[m]
            if s.get("average"):
                xs, ys, _ = _average(xs, ys, s["average"])
            out.append((f"{s['y']}" + (f" · {gname}" if gname else "") + (f" against {s['x']}" if s.get("x") else ""),
                        xs, ys))
    return out


def do_fit(spec: dict, data: dict):
    """(name, Fit, x used) for the figure's fit settings, or None (nothing to fit; it says why
    in a ValueError when asked through fit_report)."""
    try:
        return _fit_now(spec, data)
    except ValueError:
        return None


def _fit_now(spec: dict, data: dict):
    lines = fit_lines(spec, data)
    if not spec.get("fit") or not lines:
        return None
    k = min(max(0, int(spec.get("fit_line", 0))), len(lines) - 1)
    name, x, y = lines[k]
    lo, hi = (list(spec.get("fit_x") or []) + [None, None])[:2]
    m = np.isfinite(x) & np.isfinite(y)
    if lo is not None:
        m &= x >= lo
    if hi is not None:
        m &= x <= hi
    if m.sum() < 3:
        raise ValueError("Fewer than 3 points in that x range")
    f = fitting.fit(spec["fit"], x[m], y[m], int(spec.get("fit_degree", 1)))
    return name, f, x[m]


def fit_report(spec: dict, data: dict) -> str:
    """The fit's result as rich text: the equation, each parameter ± its standard error, R², n."""
    try:
        got = _fit_now(spec, data)
    except ValueError as e:
        return f"<span style='color:#c62828'>{e}</span>"
    if got is None:
        return "Pick a model to fit."
    name, f, _ = got
    rows = "".join(f"<tr><td><b>{n}</b></td><td align=right>{v:.5g}</td><td>± {e:.3g}</td></tr>"
                   for n, v, e in f.table())
    return (f"<b>{name}</b><br>{f.equation}<br><table cellspacing=4>{rows}</table>"
            f"R² = {f.r2:.4f} &nbsp; R = {fit_R(f):.4f} &nbsp; n = {f.n}")


def _fit(ax, model, x, y, color, lw):
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return
    try:
        f = fitting.fit(model, x[ok], y[ok], 1)
    except Exception:
        return
    xs = np.linspace(np.nanmin(x[ok]), np.nanmax(x[ok]), 200)
    ax.plot(xs, f.predict(xs), "-", color=color, linewidth=lw * 0.9, alpha=0.8,
            label=f"fit: R² = {f.r2:.3f}")


def save(spec: dict, data: dict, path: str, dpi: int = 300) -> None:
    from matplotlib.figure import Figure
    fig = Figure(figsize=(spec.get("w_mm", 80) / 25.4, spec.get("h_mm", 60) / 25.4))
    draw(fig, spec, data)
    fig.tight_layout()
    fig.savefig(path, dpi=dpi, transparent=False, facecolor="white")
