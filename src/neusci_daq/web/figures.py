"""Single Plot and the Plot Canvas, the HTML front end's Python half: the figures for a lab
report, drawn by the Qt app's own code (figspec.py, with matplotlib), so a figure looks
the same in either front end and saves as PDF or SVG with every line and label its own
object, or PNG. The pages show pictures drawn here, off any screen's thread, one at a time
(matplotlib's settings are the whole program's).

Single Plot: one figure made to look right. It starts from DataPad columns (Y against X, or
bars by group), from a DataPad chart, or from a chart on Analysis (its lines as they are),
and figures of DataPad columns stay linked to it: change the table and the figure follows.

The Plot Canvas: a page (A4, Letter, a slide) to lay figures out on, with panel letters,
text, lines, arrows, boxes, ellipses and pictures, exported whole.

Saved with the session in analysis.json as the Qt app saves them: "figure" (Single Plot's
spec) and "plot_canvas" (plotcanvas.PlotCanvasPage.state), so either front end opens what
the other saved.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import logging
import re
import threading

import numpy as np

from .. import analysisfile, figpage
from .. import figspec as F
from . import charts as C
from .pad import ROW_ORDER

FIGURE, CANVAS = "figure", "plot_canvas"
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)   # (Arial's stand-ins it doesn't have)
_DRAW = threading.Lock()            # matplotlib's settings (rc_context) are the whole program's: one at a time
SPEC_KEYS = set(F.new_spec()) | set(F.LOOK_DEFAULTS) | {"box", "aspect", "points", "auto_size"}
LEGEND_MAX = 8                      # more groups than this: no legend (it would cover the plot)
PT_PX = 0.75                        # a screen pixel in pt (at 96 dpi), as a chart's lines are drawn
PREFIXES, BASES = "pnµumkMG", "VAsSWHΩ"     # (V, A, s, S, W, Hz, Ω)
SI = [(1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"), (1.0, ""), (1e3, "k"), (1e6, "M"), (1e9, "G")]


def si(values, unit: str) -> tuple[float, str]:
    """(the factor, the unit with its prefix) to show these numbers in, as pyqtgraph's axes
    choose them: V of 0.05 shows as 50 mV."""
    if not unit or unit in ("%", "1", "°") or (len(unit) > 1 and unit[0] in PREFIXES and unit[1:2] in BASES):
        return 1.0, unit                          # no unit, or one with its prefix already
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    big = float(np.max(np.abs(v))) if v.size else 0.0
    if big == 0:
        return 1.0, unit
    k, p = SI[0]
    for k_, p_ in SI:
        if big >= k_ * 0.999:
            k, p = k_, p_
    return 1.0 / k, p + unit


def rgba(css: str) -> tuple[str, float]:
    """A CSS color ("#rrggbb" or "rgba(r,g,b,a)") as (hex, opacity)."""
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)", css or "")
    if m:
        r, g, b = (int(m.group(k)) for k in (1, 2, 3))
        return f"#{r:02x}{g:02x}{b:02x}", float(m.group(4)) if m.group(4) else 1.0
    return (css or "#333333")[:7], 1.0


def _floats(a) -> list:
    return [float(v) if np.isfinite(v) else float("nan") for v in np.asarray(a, float)]


def _clean(o):
    """JSON without NaN (JSON has no NaN): None in its place."""
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


class Figures:
    def __init__(self, trials, pad, charts):
        self.trials, self.pad, self.charts = trials, pad, charts
        self._lock = threading.RLock()
        self._folder = None
        self.spec: dict | None = None            # Single Plot's figure, and its numbers
        self.data: dict = {}
        self.editing: int | None = None          # the Canvas figure Single Plot is changing, if any
        self.v = 0                               # bumped at every change of the figure
        self.copied: dict | None = None          # a look copied, to paste on another figure
        self.page = self._blank_page()
        self.panels: list[dict] = []             # {"id", "spec", "data", "linked", "x_mm", "y_mm", "v"}
        self.anns: list[dict] = []               # Qt's annotation states, each with an "id"
        self._ids = 0
        self._pad_seen = None
        self.ask_path = None                     # in a window: asks where to save (name, fmt) -> path or None

    @staticmethod
    def _blank_page() -> dict:
        return {"paper": "A4 portrait", "letters": True, "text_pt": 9.0, "links": dict(figpage.LINKS), "look": {}}

    def _id(self) -> int:
        self._ids += 1
        return self._ids

    # -- the session's figures ----------------------------------------------------------------
    def _sync(self) -> None:
        folder = self.trials.folder
        if folder == self._folder:
            return
        self._folder = folder
        st = analysisfile.read_state(folder) if folder else {}
        spec = st.get(FIGURE)
        self.spec = spec if isinstance(spec, dict) and spec.get("kind") else None
        self.data, self.editing, self._pad_seen = {}, None, None
        self.v += 1
        self._restore(st.get(CANVAS))

    def _restore(self, s) -> None:
        self.page, self.panels, self.anns = self._blank_page(), [], []
        if not isinstance(s, dict):
            return
        self.page.update(paper=s.get("paper") if s.get("paper") in figpage.PAPERS else "A4 portrait",
                         letters=bool(s.get("letters", True)))
        if s.get("text_pt"):
            self.page["text_pt"] = float(s["text_pt"])
        self.page["links"] = {**figpage.LINKS, **{k: bool(v) for k, v in (s.get("links") or {}).items()
                                                  if k in figpage.LINKS}}
        self.page["look"] = dict(s.get("look") or {})
        for d in s.get("panels", []):                # each as it was saved, not made alike again
            try:
                self._add(d["spec"], d["data"], (d["x_mm"], d["y_mm"]), d.get("linked", True),
                          restoring=True)
            except (KeyError, TypeError, ValueError):
                continue
        for d in s.get("annotations", []):
            if isinstance(d, dict) and d.get("kind") in ("text", "line", "arrow", "rect", "ellipse"):
                self.anns.append({**d, "id": self._id()})

    def canvas_state(self) -> dict:
        """The Plot Canvas as the Qt app saves it (PlotCanvasPage.state)."""
        p = self.page
        return {"paper": p["paper"], "letters": p["letters"], "text_pt": p["text_pt"], "links": dict(p["links"]),
                "look": dict(p["look"]),
                "panels": [{"spec": q["spec"], "data": q["data"], "linked": q["linked"], "x_mm": q["x_mm"],
                            "y_mm": q["y_mm"]} for q in self.panels],
                "annotations": [{k: v for k, v in a.items() if k != "id"} for a in self.anns]}

    def _save(self) -> None:
        if self._folder is not None:
            analysisfile.save_parts(self._folder, {FIGURE: F.clone(self.spec) if self.spec else None,
                                                   CANVAS: self.canvas_state()})     # NaN kept, as Qt keeps it

    # -- following the DataPad ---------------------------------------------------------------------
    def _follow(self) -> None:
        """The DataPad changed (or the Trials pane): linked figures read their columns again."""
        ver = self.pad.fresh_version()
        if ver == self._pad_seen:
            return
        self._pad_seen = ver
        names = {c["name"] for c in self.pad.numbers()}
        for p in self.panels:
            cols = F.columns_used(p["spec"])
            if p["spec"].get("kind") in ("image", "curves") or not cols or not p["linked"]:
                continue
            if all(c in names for c in cols):
                new = self.pad.snapshot(cols)
                if new != p["data"]:
                    p["data"], p["v"] = new, p["v"] + 1
        if self.spec is not None and self.spec.get("kind") not in ("curves", "image"):
            new = self.pad.snapshot(F.columns_used(self.spec))
            if new != self.data:
                self.data = new
                self.v += 1

    # -- what the pages show -------------------------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            self._sync()
            self._follow()
            s = self.spec
            info = ""
            if s is not None and s.get("kind") == "curves":
                info = f"{self.data.get('chart') or 'A chart'} · its lines as they were when the figure was made"
            elif s is not None and s.get("kind") != "image":
                info = f"{len(self.data.get('rows', []))} trials, linked to the DataPad"
            return _clean({"folder": str(self._folder or ""), "spec": s, "v": self.v, "info": info,
                           "editing": self.editing is not None and any(p["id"] == self.editing for p in self.panels),
                           "copied": self.copied is not None, "links": self.page["links"],
                           "names": self.pad.numbers(), "sources": self._sources(), "fit": self._fit_info(),
                           "panels": len(self.panels), "consts": CONSTS})

    def _sources(self) -> list[dict]:
        """Charts a figure can be made of: Analysis's and the DataPad's."""
        with self.charts._lock:
            self.charts._sync()
            board = [{"src": "board", "id": c["id"], "name": c.get("name") or c["id"], "kind": C.KINDS[c["kind"]]}
                     for c in self.charts.board["charts"]]
        pad = [{"src": "pad", "id": c["id"], "name": c.get("name") or c["id"], "kind": "DataPad: Y against X"}
               for c in self.pad.chart_specs()]
        return board + pad

    def _fit_info(self) -> dict | None:
        s = self.spec
        if s is None or s.get("kind") in ("bar", "image"):
            return None
        out = {"lines": [n for n, _, _ in F.fit_lines(s, self.data)]}
        if s.get("fit"):
            try:
                got = F._fit_now(s, self.data)
            except ValueError as e:
                return {**out, "error": str(e)}
            if got is not None:
                name, f, _ = got
                out.update(name=name, equation=f.equation, table=[list(r) for r in f.table()], r2=f.r2,
                           R=F.fit_R(f), n=f.n)
        return out

    def canvas(self) -> dict:
        with self._lock:
            self._sync()
            self._follow()
            letters = figpage.letters([(p["x_mm"], p["y_mm"]) for p in self.panels])
            panels = [{"id": p["id"], "x": p["x_mm"], "y": p["y_mm"], "w": p["spec"]["w_mm"], "h": p["spec"]["h_mm"],
                       "kind": p["spec"].get("kind"), "linked": p["linked"], "v": p["v"], "letter": L,
                       "font": F.paper_font(p["spec"]), "title": p["spec"].get("title", "")}
                      for p, L in zip(self.panels, letters)]
            return _clean({"folder": str(self._folder or ""), "page": {**self.page, "size": figpage.PAPERS[self.page["paper"]]},
                           "papers": list(figpage.PAPERS), "panels": panels, "anns": self.anns,
                           "editing": self.editing})

    def picture(self, d: dict) -> dict:
        """A figure drawn: Single Plot's (which "single", at `dpi`, its plot's place in a picture
        w × h pixels) or a Canvas figure's (which "panel", its "id")."""
        dpi = max(20.0, min(1200.0, float(d.get("dpi", 150))))
        with self._lock:
            self._sync()
            self._follow()
            if d.get("which") == "single":
                if self.spec is None:
                    return {"none": True}
                spec, data, v = F.clone(self.spec), dict(self.data), self.v
            else:
                p = self._panel(d.get("id"))
                spec, data, v = F.clone(p["spec"]), p["data"], p["v"]
        with _DRAW:
            if d.get("which") == "single":
                png, geo = figpage.view_png(spec, data, dpi, (float(d.get("w", 300)), float(d.get("h", 200))))
            else:
                png, geo = figpage.png(spec, data, dpi), None
        return _clean({"png": base64.b64encode(png).decode(), "geo": geo, "v": v})

    def export(self, d: dict) -> tuple[bytes, str, str]:
        """(the file's bytes, its type, a name for it): Single Plot's figure ("single") or the
        whole page, as PDF, SVG or PNG."""
        fmt = d.get("fmt", "pdf")
        if fmt not in figpage.FORMATS:
            raise ValueError(f"Can't save as {fmt!r}")
        dpi = int(max(72, min(1200, int(d.get("dpi", 300)))))
        with self._lock:
            self._sync()
            self._follow()
            if d.get("what") == "single":
                if self.spec is None:
                    raise ValueError("Make a figure first")
                spec, data = F.clone(self.spec), dict(self.data)
            else:
                if not self.panels and not self.anns:
                    raise ValueError("The page is empty: send figures here from Single Plot")
                letters = figpage.letters([(p["x_mm"], p["y_mm"]) for p in self.panels])
                panels = [(F.clone(p["spec"]), p["data"], p["x_mm"], p["y_mm"], L) for p, L in zip(self.panels, letters)]
                anns = [{k: v for k, v in a.items() if k != "id"} for a in self.anns]
                page = (figpage.PAPERS[self.page["paper"]], self.page["letters"])
        with _DRAW:
            if d.get("what") == "single":
                out, name = figpage.figure_bytes(spec, data, fmt, dpi), "figure"
            else:
                buf = io.BytesIO()
                figpage.save_page(buf, page[0], panels, anns, page[1], dpi, fmt)
                out, name = buf.getvalue(), "page"
        ctype = {"pdf": "application/pdf", "svg": "image/svg+xml", "png": "image/png"}[fmt]
        return out, ctype, f"{name}.{fmt}"

    def save_as(self, d: dict) -> dict:
        """Export, asking where (a window's own Save dialog)."""
        out, _, name = self.export(d)
        if self.ask_path is None:
            raise RuntimeError("No window to ask where to save in")
        path = self.ask_path(name, d.get("fmt", "pdf"))
        if not path:
            return {"saved": ""}
        with open(path, "wb") as f:
            f.write(out)
        return {"saved": str(path)}

    # -- Single Plot's commands ------------------------------------------------------------------
    def command(self, d: dict) -> dict:
        with self._lock:
            self._sync()
            if self._folder is None:
                raise RuntimeError("Open a session first: figures are saved with it.")
            cmd = d.get("cmd")
            out = {}
            if cmd == "new":
                self._new(d)
            elif cmd == "chart":
                self._of_chart(d.get("src"), d.get("id"))
            elif cmd == "clear":
                self.spec, self.data, self.editing = None, {}, None
            elif cmd == "edit":                       # a Canvas figure, to change here
                p = self._panel(d.get("id"))
                self.spec, self.data, self.editing = F.clone(p["spec"]), copy.deepcopy(p["data"]), p["id"]
                self._pad_seen = None
            else:
                if self.spec is None:
                    raise ValueError("Make a figure first")
                s = self.spec
                if cmd == "set":
                    self._set(d.get("values") or {})
                elif cmd == "series":
                    s["series"] = [{**F.new_series("", ""), **{k: x.get(k) for k in F.new_series("", "") if k in x}}
                                   for x in d.get("series") or [] if isinstance(x, dict) and x.get("y")]
                    self._pad_seen = None
                elif cmd == "look":                   # a preset (with its size) or the look copied
                    look = F.PRESETS.get(d.get("preset")) if d.get("preset") else self.copied
                    if look:
                        self._set({k: v for k, v in look.items() if v is not None})
                elif cmd == "copy_look":
                    self.copied = F.look_of(s)
                elif cmd == "every":                  # this look (not the size) on every Canvas figure
                    self._set_look(F.look_of(s))
                elif cmd == "link":
                    sec = d.get("section")
                    if sec not in figpage.LINKS:
                        raise ValueError(f"No section {sec!r}")
                    self._set_link(sec, bool(d.get("on")), F.look_of(s, (sec,)))
                elif cmd == "to_canvas":
                    p = self._add(F.clone(s), copy.deepcopy(self.data), edited=self._panel_or_none(self.editing))
                    self.editing = p["id"]
                    out = {"to_canvas": p["id"]}
                else:
                    raise ValueError(f"Unknown command {cmd!r}")
            self.v += 1
            self._save()
            return out

    def _new(self, d: dict) -> None:
        """A figure of DataPad columns: Y (one or more) against X (or the rows in order), or
        bars by group. Nothing is chosen for the student."""
        kind = d.get("kind")
        ys = [str(y) for y in d.get("ys") or []]
        if kind not in ("xy", "bar") or not ys:
            raise ValueError("Choose the kind of figure and what runs up (Y)")
        x = "" if kind == "bar" or d.get("x") in (None, "", ROW_ORDER) else str(d["x"])
        spec = F.new_spec(kind=kind)
        spec["series"] = [F.new_series(x, y) for y in ys]
        if kind == "xy":                              # x against y: a square figure
            spec["h_mm"] = spec["w_mm"]
        self._take(spec, {})

    def _take(self, spec: dict, data: dict) -> None:
        self.spec, self.data, self.editing = spec, data, None
        self._pad_seen = None                         # its numbers read again (_follow)

    def _set(self, values: dict) -> None:
        s = self.spec
        if ("w_mm" in values or "h_mm" in values) and \
                (values.get("w_mm", s.get("w_mm")), values.get("h_mm", s.get("h_mm"))) != (s.get("w_mm"), s.get("h_mm")):
            s["auto_size"] = False                    # a size you set: kept
        for k, v in values.items():
            if k not in SPEC_KEYS and k not in F.FIT_LOOK:
                continue
            if k == "kind" and (s.get("kind") in ("curves", "image") or v not in ("xy", "bar")):
                continue                              # a chart stays a chart
            if k in ("xlim", "ylim", "fit_x"):
                v = [(float(a) if a is not None and a != "" else None) for a in (list(v or []) + [None, None])[:2]]
            elif k in ("w_mm", "h_mm"):
                v = max(20.0, min(400.0, float(v)))
            elif k == "box":
                s["despine"] = v == "open"
            s[k] = v

    # -- figures of charts ------------------------------------------------------------------------------
    def _of_chart(self, src: str, cid: str) -> None:
        if src == "pad":
            chart = next((c for c in self.pad.chart_specs() if c["id"] == cid), None)
            if chart is None:
                raise KeyError("That chart is gone.")
            f, look = chart.get("fields", {}), chart.get("look", {})
            ys = [y for y in f.get("y") or []]
            if not f.get("x") or not ys:
                raise ValueError("Choose the chart's Y and X first, on the DataPad")
            x = "" if f["x"] == ROW_ORDER else f["x"]
            spec = F.new_spec(kind="xy")
            spec["series"] = [{**F.new_series(x, y), "by_group": len(ys) == 1} for y in ys]
            spec.update(xlog=bool(look.get("xlog")), ylog=bool(look.get("ylog")), h_mm=spec["w_mm"])
            self._take(spec, {})
            return
        with self.charts._lock:
            self.charts._sync()
            chart = json.loads(json.dumps(self.charts._chart(cid)))
            spread = self.charts.board["spread"]
            v = self.trials.view()
        if not v.get("folder"):
            raise ValueError("Open a session first")
        spec, data = (self._measure_figure if chart["kind"] == C.MEASURE else self._time_figure)(chart, v, spread)
        data["chart"] = chart.get("name") or chart["id"]
        self._take(spec, data)

    def _time_figure(self, chart: dict, v: dict, spread: str) -> tuple[dict, dict]:
        """A trials-over-time or stimulus chart's first plot: every line in its color, opacity and
        width, each group's average named, its spread shaded."""
        head, arrays = self.charts._time_chart(chart, v, spread, None, None, 1500)
        if head.get("note"):
            raise ValueError(head["note"])
        plot = head["plots"][0]
        first = 1
        x = np.asarray(arrays[0], float)
        series = arrays[first:first + len(plot["series"])]
        kx, xu = si([head["x0"], head["x1"]], "s")
        ky, yu = si(np.concatenate([np.asarray(a, float) for a in series]) if series else [], plot["unit"])
        curves, fills = [], []
        named = sum(1 for s in plot["series"] if s["k"] == "mean") <= LEGEND_MAX
        for s, y in zip(plot["series"], series):
            if s["k"] in ("hi", "lo"):
                continue
            color, alpha = rgba(s["c"])
            curves.append({"x": _floats(x * kx), "y": _floats(np.asarray(y, float) * ky), "color": color,
                           "alpha": round(alpha, 3), "name": s["g"].split(":")[0] if s["k"] == "mean" and named else "",
                           "width_px": float(s["w"] or 1), "points": False, "z": 1.0 if s["k"] == "mean" else 0.0})
        for a, b, c in plot["bands"]:
            color, alpha = rgba(c)
            fills.append({"x": _floats(x * kx), "lo": _floats(np.asarray(series[b - 1], float) * ky),
                          "hi": _floats(np.asarray(series[a - 1], float) * ky), "color": color, "alpha": round(alpha, 3),
                          "z": 0.5})
        curves.sort(key=lambda c: c["z"])
        spec = F.new_spec(kind="curves")
        spec.update(w_mm=120.0, h_mm=70.0, xlim=[head["x0"] * kx, head["x1"] * kx], w0_mm=None)
        data = {"curves": curves, "fills": fills, "marks": [], "bands": [], "lines": [],
                "xlabel": f"Time ({xu})", "ylabel": f"{plot['label']} ({yu})" if yu else plot["label"],
                "kx": kx, "ky": ky, "plot": 0, "pt_px": PT_PX, "lw0": 1.2}
        return spec, data

    def _measure_figure(self, chart: dict, v: dict, spread: str) -> tuple[dict, dict]:
        """A measure against the stimulus: each trial's dot, each group's average ± its spread."""
        m = self.charts._measure_chart(chart, v, spread)
        if m.get("note"):
            raise ValueError(m["note"])
        pts, groups = m["points"], m["groups"]
        kx, xu = si([p[0] for p in pts] + [g["x"] for g in groups], m["x"]["unit"])
        ky, yu = si([p[1] for p in pts] + [g["y"] for g in groups], m["y"]["unit"])
        cols = [rgba(p[2]) for p in pts]
        marks = [{"x": [p[0] * kx for p in pts], "y": [p[1] * ky for p in pts], "colors": [c for c, _ in cols],
                  "alpha": [round(a, 3) for _, a in cols], "symbols": ["o"] * len(pts), "size": 7.0}] if pts else []
        bars = [{"x": [g["x"] * kx], "y": [g["y"] * ky], "ex": [g["ex"] * kx], "ey": [g["ey"] * ky], "color": g["c"],
                 "size": 12.0, "name": g["name"] if len(groups) <= LEGEND_MAX else ""} for g in groups]
        fit = []
        if pts:
            fit.append({"name": "Every trial's dot", "x": [p[0] * kx for p in pts], "y": [p[1] * ky for p in pts]})
        if len(groups) > 1:
            gs = sorted(groups, key=lambda g: g["x"])
            fit.append({"name": "The groups' averages", "x": [g["x"] * kx for g in gs], "y": [g["y"] * ky for g in gs]})
        spec = F.new_spec(kind="curves")
        spec.update(w_mm=80.0, h_mm=80.0, w0_mm=None)          # x against y: square
        lab = lambda a, u: f"{a['label']} ({u})" if u else a["label"]
        data = {"curves": [], "fills": [], "marks": marks, "errorbars": bars, "bands": [], "lines": [],
                "xlabel": lab(m["x"], xu), "ylabel": lab(m["y"], yu), "kx": kx, "ky": ky, "plot": 0,
                "pt_px": PT_PX, "lw0": 1.2, "points": fit}
        return spec, data

    # -- the Plot Canvas ------------------------------------------------------------------------------
    def _panel(self, pid) -> dict:
        p = self._panel_or_none(pid)
        if p is None:
            raise KeyError("That figure is gone.")
        return p

    def _panel_or_none(self, pid):
        return next((p for p in self.panels if p["id"] == pid), None) if pid is not None else None

    def _charts(self, but=None) -> list[dict]:
        return [q for q in self.panels if q["spec"].get("kind") != "image" and q is not but]

    def _add(self, spec: dict, data: dict, at=None, linked: bool = True, edited: dict | None = None,
             restoring: bool = False) -> dict:
        """A figure on the page (as PlotCanvasPage.add_panel): the sections linked across the
        page made the same, the first figure setting them."""
        spec = F.on_page(spec, data)
        if spec.get("kind") != "image" and not restoring:
            keys = figpage.linked_keys(self.page["links"])
            if edited is not None:
                changed = {k: spec.get(k) for k in keys if k in spec and spec.get(k) != edited["spec"].get(k)}
                if changed:
                    self._set_look(changed, but=edited)      # you changed this one's: the others follow
            elif not self._charts():
                self.page["look"] = F.look_of(spec, [s for s, on in self.page["links"].items() if on and s in F.SECTIONS])
                if spec.get("font"):
                    self.page["text_pt"] = float(spec["font"])
            else:
                spec = {**spec, **{k: v for k, v in self.page["look"].items() if k in keys}}
        if edited is not None:
            edited["spec"], edited["data"], edited["v"] = spec, data, edited["v"] + 1
            return edited
        x, y = at if at else figpage.free_spot(spec, [(p["x_mm"], p["y_mm"], p["spec"]["w_mm"], p["spec"]["h_mm"])
                                                      for p in self.panels], figpage.PAPERS[self.page["paper"]],
                                                     tries=20000)       # (Qt looks 200 times: often the top left)
        p = {"id": self._id(), "spec": spec, "data": data, "linked": bool(linked), "x_mm": float(round(float(x))),
             "y_mm": float(round(float(y))), "v": 1}
        self.panels.append(p)
        return p

    def _set_look(self, look: dict, but: dict | None = None) -> None:
        keys = set(figpage.linked_keys(self.page["links"]))
        self.page["look"].update({k: v for k, v in look.items() if k in keys})
        if look.get("font"):
            self.page["text_pt"] = float(look["font"])
        for p in self.panels:
            if p is but or p["spec"].get("kind") == "image":
                continue
            new = {**p["spec"], **look}
            if new != p["spec"]:
                p["spec"], p["v"] = new, p["v"] + 1

    def _set_link(self, section: str, on: bool, look: dict | None = None) -> None:
        self.page["links"] = {**self.page["links"], section: bool(on)}
        if not on:
            return
        if look is None:
            charts = self._charts()
            look = F.look_of(charts[0]["spec"], (section,)) if charts else {}
        if look:
            self._set_look(look)

    def _set_text(self, pt: float) -> None:
        self.page["text_pt"] = pt
        for p in self._charts():
            if abs(float(p["spec"].get("font") or 9) - pt) > 1e-6:
                p["spec"], p["v"] = F.with_text(p["spec"], pt), p["v"] + 1
        charts = self._charts()
        if charts and self.page["links"].get("text"):
            self.page["look"].update(F.look_of(charts[0]["spec"], ("text",)))

    def canvas_command(self, d: dict) -> dict:
        with self._lock:
            self._sync()
            if self._folder is None:
                raise RuntimeError("Open a session first: the page is saved with it.")
            self._follow()
            cmd, out = d.get("cmd"), {}
            if cmd == "paper":
                if d.get("name") not in figpage.PAPERS:
                    raise ValueError("No such paper")
                self.page["paper"] = d["name"]
            elif cmd == "letters":
                self.page["letters"] = bool(d.get("on"))
            elif cmd == "text":
                self._set_text(max(4.0, min(36.0, float(d["pt"]))))
            elif cmd == "move":                        # figures to whole mm; drawings where they're put
                for pid, x, y in d.get("panels") or []:
                    p = self._panel(pid)
                    p["x_mm"], p["y_mm"] = float(round(float(x))), float(round(float(y)))
                for aid, dx, dy in d.get("anns") or []:
                    a = self._ann(aid)
                    if a["kind"] == "text":
                        a["x_mm"], a["y_mm"] = a["x_mm"] + float(dx), a["y_mm"] + float(dy)
                    else:
                        for k, by in (("x0", dx), ("x1", dx), ("y0", dy), ("y1", dy)):
                            a[k] = a[k] + float(by)
            elif cmd == "resize":
                p = self._panel(d.get("id"))
                s = p["spec"]
                w0, h0 = s["w_mm"], s["h_mm"]
                s["w_mm"] = max(15.0, float(round(float(d["w_mm"]))))
                if s.get("kind") == "image":           # a picture keeps its shape
                    s["h_mm"] = round(s["w_mm"] * h0 / w0, 1)
                else:
                    s["h_mm"] = max(15.0, float(round(float(d["h_mm"]))))
                p["v"] += 1
            elif cmd == "freeze":
                got = [self._panel(i) for i in d.get("ids") or []]
                on = not all(not p["linked"] for p in got)
                for p in got:
                    p["linked"] = not on
                self._pad_seen = None
            elif cmd == "duplicate":
                for i in d.get("ids") or []:
                    p = self._panel(i)
                    self._add(F.clone(p["spec"]), copy.deepcopy(p["data"]), linked=p["linked"])
            elif cmd == "remove":
                ids, aids = set(d.get("ids") or []), set(d.get("anns") or [])
                self.panels = [p for p in self.panels if p["id"] not in ids]
                self.anns = [a for a in self.anns if a["id"] not in aids]
                if self.editing in ids:
                    self.editing = None
            elif cmd == "image":
                self._image(d)
            elif cmd == "ann_add":
                a = self._ann_of(d.get("ann") or {})
                a["id"] = self._id()
                self.anns.append(a)
                out = {"ann": a["id"]}
            elif cmd == "ann_set":                     # color, width, size, bold or text of the ones picked
                for aid in d.get("ids") or []:
                    a = self._ann(aid)
                    for k in (("color", "size", "bold", "text") if a["kind"] == "text" else ("color", "width")):
                        if k in d:
                            a[k] = d[k] if k in ("color", "text") else bool(d[k]) if k == "bold" else float(d[k])
                    if a["kind"] == "text" and not str(a.get("text", "")).strip():
                        self.anns.remove(a)            # emptied: gone
            else:
                raise ValueError(f"Unknown command {cmd!r}")
            self._save()
            return out

    def _ann(self, aid) -> dict:
        a = next((a for a in self.anns if a["id"] == aid), None)
        if a is None:
            raise KeyError("That drawing is gone.")
        return a

    @staticmethod
    def _ann_of(d: dict) -> dict:
        kind = d.get("kind")
        if kind == "text":
            return {"kind": "text", "x_mm": float(d["x_mm"]), "y_mm": float(d["y_mm"]), "text": str(d.get("text", "Text")),
                    "size": float(d.get("size", 10)), "color": str(d.get("color", "#000000")), "bold": bool(d.get("bold"))}
        if kind not in ("line", "arrow", "rect", "ellipse"):
            raise ValueError(f"No drawing called {kind!r}")
        return {"kind": kind, **{k: float(d[k]) for k in ("x0", "y0", "x1", "y1")},
                "color": str(d.get("color", "#000000")), "width": float(d.get("width", 1.0))}

    def _image(self, d: dict) -> None:
        """A picture put on the page, kept inside the session (as PNG)."""
        raw = base64.b64decode(d.get("b64", ""))
        name = str(d.get("name") or "picture")
        try:
            if d.get("drawn"):                        # an SVG the page drew as PNG
                png_, w, h = figpage.picture_png(raw, "picture.png")
            else:
                png_, w, h = figpage.picture_png(raw, name)
        except Exception as e:
            raise ValueError(f"Couldn't read {name}: {e}") from e
        W = figpage.PAPERS[self.page["paper"]][0]
        spec, data = F.image_spec(png_, w, h, width_mm=min(100.0, W - 2 * figpage.MARGIN))
        spec["title"] = name
        self._add(spec, data, linked=False)


CONSTS = {"styles": F.STYLES, "averages": F.AVERAGES, "legends": F.LEGENDS, "families": F.FAMILIES,
          "fits": F.FITS, "fit_styles": F.FIT_STYLES, "fit_spans": F.FIT_SPANS, "fit_show": F.FIT_SHOW,
          "fit_where": F.FIT_WHERE, "fit_look": F.FIT_LOOK, "palette": F.PALETTE, "presets": F.PRESETS,
          "look_defaults": F.LOOK_DEFAULTS, "sections": {k: list(v) for k, v in F.SECTIONS.items()},
          "formats": figpage.FORMATS}
