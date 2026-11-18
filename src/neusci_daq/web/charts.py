"""The Analysis board's Python half: the charts you add beside the Trials pane, and what
each one draws, worked out here and sent to the page already shrunk (chartplan.envelope:
two points per screen column), never the raw samples.

The kinds, as the Qt board has them, drawn as Qt draws them (responsecharts.py,
spikecharts.py): the trials over time (Response: each ticked group's average, with its
± SD or SEM shaded, and trials ticked on their own as traces), the stimulus over time
(Stimulus), one number from each trial against the stimulus (a measure against the
stimulus: an input–output curve, the trials as dots and each group's average as a big one),
and the spikes found (spikes.py): a raster, trial by trial, and each unit's waveforms.
Every channel is read through trials.py (its gain, the stimulus fit, calculated channels).

Saved with the session in analysis.json, under "board", in the Qt board's format (canvas.py:
state_of), so the Qt app keeps them; it skips the kind it doesn't have.

Not yet as in Qt: no spikes or threshold on the traces; the rate, PCA, sizes and intervals
charts; one board (no saved layouts); charts aren't moved or resized by dragging.
"""
from __future__ import annotations

import json
import struct
import threading
from collections import OrderedDict

import numpy as np

from .. import analysis as A
from .. import analysisfile, chartplan, colors, grouping
from .. import datapad as D
from .. import stimmeasures as SM

RESPONSE, STIMULUS, MEASURE = "Response", "Stimulus", "Measure against stimulus"
RASTER, WAVEFORMS = "Raster", "Waveforms"
KINDS = {RESPONSE: "Trials over time", STIMULUS: "Stimulus over time", MEASURE: "A measure against the stimulus",
         RASTER: "Spikes, trial by trial", WAVEFORMS: "Each unit's spike shape"}
SPIKE_KINDS = (RASTER, WAVEFORMS)
MAX_WAVES = 150                       # spikes of each unit drawn behind its average (spikecharts.MAX_WAVES)
SERIES = ("", "dV/dt", "d²V/dt²")       # a channel itself, its slope, its slope's slope (Qt: V, dV/dt, d²V/dt²)
FIELDS = {RESPONSE: {"smooth": 0.2, "bin": 0.0, "layout": "stacked", "show_trials": False, "show_average": True,
                     "threshold": False, "dots": False, "dot_size": 9},
          STIMULUS: {"bin": 0.0},
          MEASURE: {"x": "", "channel": "", "measure": "", "window": "stim", "show_trials": True,
                    "show_average": True},
          RASTER: {"dots": 3},
          WAVEFORMS: {"norm": False, "show": 0, "smooth": 0.1}}
Y_MEASURES = [k for k in D.MEASUREMENTS if k not in D.SPIKE_KEYS and k != "value"]
WINDOWS = {"stim": "While the stimulus is on", "trial": "The whole trial"}
SPREADS = ("", "sd", "sem")
BAND_ALPHA = 55 / 255                 # a group's shaded spread (spikecharts.draw_band)
BEHIND_ALPHA = 80 / 255               # trials behind the averages, with Opacity on Auto
DOT_ALPHA, DOT_BEHIND = 0.6, 0.4      # a trial's dot on its own, or behind its group's average (Auto)
KEEP = 600                            # full-length traces and averages kept, newest used last


def css(c) -> str:
    """A QColor (or a hex string) as CSS."""
    if isinstance(c, str):
        return c
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alphaF():.3f})"


def with_alpha(c: str, a: float) -> str:
    c = c.lstrip("#")
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{a:.3f})"


class Charts:
    def __init__(self, trials):
        self.trials = trials
        self._lock = threading.RLock()
        self._folder = None
        self.board: dict = {"charts": [], "spread": "sd"}
        self._kept: OrderedDict = OrderedDict()      # (what, ...) -> array: traces and averages worked out
        self._kept_lock = threading.Lock()

    # -- the board ---------------------------------------------------------------------------
    def _sync(self) -> None:
        """The board of the session open now (read again when another one is opened)."""
        folder = self.trials.folder
        if folder == self._folder:
            return
        self._folder = folder
        saved = analysisfile.read_state(folder).get(analysisfile.BOARD) if folder else None
        charts = []
        for c in (saved or {}).get("charts", []):
            if isinstance(c, dict) and c.get("kind") in KINDS:
                charts.append({**c, "fields": {**FIELDS[c["kind"]], **c.get("fields", {})}})
        spread = (saved or {}).get("spread", "sd")
        self.board = {"charts": charts, "spread": spread if spread in SPREADS else "sd"}
        with self._kept_lock:
            self._kept.clear()

    def _save(self) -> None:
        if self._folder is None:
            return
        y, charts = 0, []
        for c in self.board["charts"]:                # lined up down the board, as the page shows them
            w, h = (420, 420) if c["kind"] in (MEASURE, WAVEFORMS) else (560, 320)
            charts.append({**c, "place": [0, y, w, h]})
            y += h + 20
        analysisfile.save_part(self._folder, analysisfile.BOARD, {**self.board, "charts": charts})

    def state(self) -> dict:
        with self._lock:
            self._sync()
            v = self.trials.view()
        names = self.trials.names()
        units = self._units(v)
        return {"folder": str(self._folder or ""), "charts": self.board["charts"], "spread": self.board["spread"],
                "kinds": KINDS, "series": SERIES, "how": v.get("how", ""),
                "channels": [{"name": n, "unit": units.get(n, "V")} for n in names],
                "x_measures": [[k, name, unit.replace("u", units.get(v.get("how"), "V"))]
                               for k, (name, unit) in SM.MEASURES.items()],
                "y_measures": [[k, D.MEASUREMENTS[k][0], D.MEASUREMENTS[k][2]] for k in Y_MEASURES],
                "windows": WINDOWS}

    def command(self, c: dict) -> None:
        with self._lock:
            self._sync()
            if self._folder is None:
                raise RuntimeError("Open a session first: charts are saved with it.")
            cmd, charts = c.get("cmd"), self.board["charts"]
            if cmd == "add":
                kind = c["kind"]
                if kind not in KINDS:
                    raise ValueError(f"No chart called {kind!r}")
                taken = {x["id"] for x in charts}
                k = next(k for k in range(1, 10000) if f"{kind} {k}" not in taken)
                names = {x.get("name") for x in charts}
                name = next(n for n in (kind if j == 1 else f"{kind} {j}" for j in range(1, 10000)) if n not in names)
                charts.append({"id": f"{kind} {k}", "kind": kind, "name": name, "pin": None, "linked": True,
                               "auto_hide": False, "fields": dict(FIELDS[kind]), "look": {}, "units": None,
                               **({"ticked": [], "spikes_set": False} if kind == RESPONSE else {})})
            elif cmd == "remove":
                self.board["charts"] = [x for x in charts if x["id"] != c["id"]]
            elif cmd == "set":
                chart = self._chart(c["id"])
                for k, val in (c.get("fields") or {}).items():
                    if k in FIELDS[chart["kind"]]:
                        chart["fields"][k] = val
                if "ticked" in c and chart["kind"] == RESPONSE:
                    chart["ticked"] = sorted([str(n), int(s)] for n, s in c["ticked"] if 0 <= int(s) < len(SERIES))
            elif cmd == "move":                       # up or down the board
                chart = self._chart(c["id"])
                at = charts.index(chart)
                to = max(0, min(len(charts) - 1, at + int(c["by"])))
                charts.insert(to, charts.pop(at))
            elif cmd == "spread":
                if c["spread"] not in SPREADS:
                    raise ValueError(f"No spread called {c['spread']!r}")
                self.board["spread"] = c["spread"]
            else:
                raise ValueError(f"Unknown command {cmd!r}")
            self._save()

    def _chart(self, cid: str) -> dict:
        chart = next((x for x in self.board["charts"] if x["id"] == cid), None)
        if chart is None:
            raise KeyError("That chart is gone.")
        return chart

    # -- what a chart draws ------------------------------------------------------------------
    def data(self, c: dict) -> tuple[bytes, str]:
        """(what chart c["id"] draws, its content type): a time chart as binary (see _pack),
        the times x0 to x1 (s; none: all of it) on `cols` screen columns; a measure
        chart as JSON."""
        with self._lock:
            self._sync()
            chart = json.loads(json.dumps(self._chart(c["id"])))      # a copy: drawn outside the lock
            spread = self.board["spread"]
            v = self.trials.view()
        if not v.get("folder"):
            return json.dumps({"note": "Open a session"}).encode(), "application/json"
        if chart["kind"] == MEASURE:
            return json.dumps(self._measure_chart(chart, v, spread)).encode(), "application/json"
        if chart["kind"] == RASTER:
            return json.dumps(self._raster(chart, v)).encode(), "application/json"
        if chart["kind"] == WAVEFORMS:
            return json.dumps(self._waveforms(chart, v, spread)).encode(), "application/json"
        cols = max(50, min(4000, int(c.get("cols", 800))))
        head, arrays = self._time_chart(chart, v, spread, c.get("x0"), c.get("x1"), cols)
        return self._pack(head, arrays), "application/octet-stream"

    @staticmethod
    def _pack(head: dict, arrays: list[np.ndarray]) -> bytes:
        """[u32 header length][header JSON][padding to 8 bytes][f64 x][f32 each series], little-endian."""
        h = json.dumps(head).encode()
        pad = (-(4 + len(h))) % 8
        return b"".join([struct.pack("<I", len(h) + pad), h, b" " * pad,
                         *(np.ascontiguousarray(a, "<f8" if k == 0 else "<f4").tobytes() for k, a in enumerate(arrays))])

    def _units(self, v: dict) -> dict[str, str]:
        return {n: self.trials.unit_of(n) for n in self.trials.names()}

    def _chosen(self, v: dict, names: list[str]) -> tuple[float, list[int]]:
        """(the sample rate, the trials ticked that have these channels at the first trial's
        rate), as the Trials pane lists them (AnalysisPage.in_browser_order)."""
        blocks = v["blocks"]
        good = [b for b in blocks if b is not None]
        if not good:
            return 0.0, []
        fs = float(good[0]["sample_rate"])
        listed = dict.fromkeys(i for _, bl in v["shown"] for i in bl)
        at = {i: k for k, i in enumerate(listed)}
        ok = [i for i, b in enumerate(blocks) if b is not None and i not in v["hidden"] and b["sample_rate"] == fs
              and all(self.trials.has(i, n) for n in names)]
        return fs, sorted(ok, key=lambda i: (at.get(i, len(at)), i))

    def _plan(self, v: dict, chosen: list[int]):
        picked, pages = chartplan.ticked(v["shown"], v["hidden"], v["looks"]["hidden_groups"])
        kinds, label = v.get("kinds", {}), v.get("stim_label", {})

        def each(entry):                          # a combined group: an average for each stimulus in it
            g, name, blocks = entry
            if name not in kinds:
                return [entry]
            parts: dict[str, list[int]] = {}
            for i in blocks:
                parts.setdefault(label.get(i, name), []).append(i)
            return [(g, f"{name}: {lab}", bl) for lab, bl in parts.items()] if len(parts) > 1 else [entry]
        return chartplan.plan(v["shown"], chosen, picked, pages, each if v["looks"].get("avg_each") else None)

    def _opacity(self, v: dict) -> float | None:
        o = int(v["looks"].get("trial_opacity", 0) or 0)
        return o / 100 if o > 0 else None

    def _trial_colors(self, v: dict, blocks: list[int]) -> dict:
        color = lambda g: v["colors"][g] if g < len(v["colors"]) else colors.group_color(g)
        return chartplan.trial_colors(v["shown"], blocks, color, v["looks"]["trial_map"] or colors.SOLID,
                                      self._opacity(v))

    def _keep(self, key, make):
        with self._kept_lock:
            if key in self._kept:
                self._kept.move_to_end(key)
                return self._kept[key]
        out = make()
        with self._kept_lock:
            self._kept[key] = out
            while len(self._kept) > KEEP:
                self._kept.popitem(last=False)
        return out

    def _trace(self, v: dict, i: int, name: str, series: int, fs: float, bin_ms: float, smooth: float):
        """Trial i's channel as every analysis reads it (trials.trace: its gain, the stimulus
        fit), and for series 1 or 2 its d/dt, averaged in bins."""
        def make():
            out = np.asarray(self.trials.trace(i, name), float)
            if series:
                out = A.derivative(out, fs, series, smooth)
            return np.asarray(A.bin_average(out, fs, bin_ms), np.float32)
        return self._keep(("trace", str(v["paths"][i]), name, self.trials.tag(name), series, bin_ms,
                           smooth if series else 0), make)

    def _average(self, v, blocks, name, series, fs, bin_ms, smooth):
        """(mean, SD, SEM) of these trials' traces at each sample (A.mean_spread)."""
        def make():
            ys = [self._trace(v, i, name, series, fs, bin_ms, smooth) for i in blocks]
            Y = np.full((len(ys), max(len(y) for y in ys)), np.nan, np.float32)
            for r, y in enumerate(ys):
                Y[r, :len(y)] = y
            return tuple(np.asarray(a, np.float32) for a in A.mean_spread(Y)[:3])
        return self._keep(("mean", tuple(str(v["paths"][i]) for i in blocks), name, self.trials.tag(name), series,
                           bin_ms, smooth if series else 0), make)

    def _time_chart(self, chart, v, spread, x0, x1, cols):
        """(header, [x, each series]) for a Response or Stimulus chart."""
        f = chart["fields"]
        names = self.trials.names()
        units = self._units(v)
        how = v["how"]
        if chart["kind"] == STIMULUS:
            if how not in names:
                note = ("Pick the stimulus channel in the Trials pane" if not how else
                        "This session's stimulus isn't a recorded channel: pick the one that recorded it, if any")
                return {"note": note}, [np.zeros(0)]
            keys, show_trials, show_avg = [(how, 0)], True, False
        else:
            keys = sorted(((n, s) for n, s in chart.get("ticked", []) if n in names and n != how),
                          key=lambda k: (names.index(k[0]), k[1]))
            if not keys:
                return {"note": "Tick a channel to plot, above"}, [np.zeros(0)]
            show_trials, show_avg = bool(f.get("show_trials")), bool(f.get("show_average", True))
            if not (show_trials or show_avg):
                show_avg = True
        fs, chosen = self._chosen(v, [k[0] for k in keys])
        if not chosen:
            return {"note": "No trials ticked: tick some in the Trials pane"}, [np.zeros(0)]
        n = max(len(v["blocks"][i]["data"][0]) for i in chosen)
        bin_ms, smooth = float(f.get("bin") or 0), float(f.get("smooth") or 0)
        if x0 is None or x1 is None or not float(x1) > float(x0):
            x0, x1 = 0.0, n / fs
        i0 = max(0, min(n - 1, int(np.floor(float(x0) * fs))))
        i1 = max(i0 + 2, min(n, int(np.ceil(float(x1) * fs)) + 1))
        x = chartplan.envelope_x(i0, i1, cols, fs)
        arrays, plots = [x], []
        averaged, alone = self._plan(v, chosen)
        opacity = self._opacity(v)
        for name, s in keys:
            unit = units.get(name, "V")
            unit = unit if not s else f"{unit}/s" if s == 1 else f"{unit}/s²"
            series, bands, lo, hi = [], [], np.inf, -np.inf

            def add(y, color, width, k, label, how="zigzag"):
                nonlocal lo, hi
                e = chartplan.envelope(y, i0, i1, cols, how)
                if np.isfinite(e).any():
                    lo, hi = min(lo, float(np.nanmin(e))), max(hi, float(np.nanmax(e)))
                arrays.append(e)
                series.append({"c": color, "w": width, "k": k, "g": label})
                return len(series)                    # its place among the plot's series (x is 0)

            tr = lambda i: self._trace(v, i, name, s, fs, bin_ms, smooth)
            stems = [p.stem for p in v["paths"]]
            if chart["kind"] == STIMULUS:             # each group's trials in its color; trials alone in theirs
                for g, gname, blocks in averaged:
                    c = v["colors"][g]
                    c = with_alpha(c, min(1.0, max(0.02, opacity))) if opacity is not None else c
                    for i in blocks:
                        add(tr(i), c, 1.2, "trial", f"{gname} · {stems[i]}")
            elif show_trials:                         # every trial of a group, faint behind its average
                for g, gname, blocks in averaged:
                    for i, c in self._trial_colors(v, blocks).items():
                        if show_avg and opacity is None:
                            c.setAlpha(min(c.alpha(), int(BEHIND_ALPHA * 255)))
                        add(tr(i), css(c), 1, "trial", f"{gname} · {stems[i]}")
            for i, c in self._trial_colors(v, alone).items():
                add(tr(i), css(c), 1 if chart["kind"] == RESPONSE else 1.2, "trial", stems[i])
            if show_avg:
                for g, gname, blocks in averaged:
                    color = v["colors"][g]
                    mean, sd, sem = self._average(v, blocks, name, s, fs, bin_ms, smooth)
                    if spread and len(blocks) > 1:
                        sp = np.nan_to_num(sd if spread == "sd" else sem)
                        a = add(mean + sp, color, 0, "hi", gname, "high")
                        b = add(mean - sp, color, 0, "lo", gname, "low")
                        bands.append([a, b, with_alpha(color, BAND_ALPHA)])
                    add(mean, color, 2, "mean", f"{gname}: average of {len(blocks)}")
            label = name if not s else f"{name} {SERIES[s]}"
            plots.append({"label": label, "unit": unit, "series": series, "bands": bands,
                          "yr": [lo, hi] if hi >= lo else None})
        head = {"n": len(x), "end": n / fs, "x0": i0 / fs, "x1": (i1 - 1) / fs, "plots": plots,
                "trials": len(chosen)}
        return head, arrays

    # -- a measure against the stimulus ---------------------------------------------------------
    def _measure_chart(self, chart, v, spread) -> dict:
        f = chart["fields"]
        names = self.trials.names()
        units = self._units(v)
        how = v["how"]
        xk, ch, yk = f.get("x"), f.get("channel"), f.get("measure")
        if not how:
            return {"note": "Pick the stimulus channel in the Trials pane"}
        if xk not in SM.MEASURES or ch not in names or yk not in Y_MEASURES:
            return {"note": "Choose what to plot: a measure of a channel (Y) against a measure of the stimulus (X)"}
        fs, chosen = self._chosen(v, [ch])
        if not chosen:
            return {"note": "No trials ticked: tick some in the Trials pane"}
        stim_unit = units.get(how, "V") if how in names else "V"
        xs = {i: self._stim_measure(v, i, xk) for i in chosen}
        ys = {i: self._response_measure(v, i, ch, yk, f.get("window", "stim"), fs) for i in chosen}
        averaged, alone = self._plan(v, chosen)
        stems = [p.stem for p in v["paths"]]
        points, groups = [], []
        show_trials, show_avg = bool(f.get("show_trials", True)), bool(f.get("show_average", True))
        if not (show_trials or show_avg):
            show_avg = True
        auto = self._opacity(v) is None

        def dot(i, c, gname, behind=False):       # a dot is small: with Opacity on Auto, never very faint
            if auto:
                c.setAlpha(int(255 * DOT_BEHIND) if behind else max(c.alpha(), int(255 * DOT_ALPHA)))
            points.append([xs[i], ys[i], css(c), f"{gname}{stems[i]}"])
        if show_trials:
            for g, gname, blocks in averaged:
                for i, c in self._trial_colors(v, blocks).items():
                    dot(i, c, f"{gname} · ", show_avg)
        for i, c in self._trial_colors(v, alone).items():
            dot(i, c, "")
        if show_avg:
            for g, gname, blocks in averaged:
                X = np.array([xs[i] for i in blocks], float)
                Y = np.array([ys[i] for i in blocks], float)
                ok = np.isfinite(X) & np.isfinite(Y)
                if not ok.any():
                    continue
                mx, sx, ex, _ = A.mean_spread(X[ok][:, None])
                my, sy, ey, _ = A.mean_spread(Y[ok][:, None])
                pick = lambda sd, sem: float(np.nan_to_num((sd if spread == "sd" else sem)[0])) if spread else 0.0
                groups.append({"x": float(mx[0]), "y": float(my[0]), "ex": pick(sx, ex), "ey": pick(sy, ey),
                               "c": v["colors"][g], "name": gname, "n": int(ok.sum())})
        points = [p for p in points if np.isfinite(p[0]) and np.isfinite(p[1])]
        when = {"stim": "while the stimulus is on", "trial": "through the trial"}.get(f.get("window"), "")
        return {"x": {"label": f"Stimulus {SM.MEASURES[xk][0].lower()}",
                      "unit": SM.MEASURES[xk][1].replace("u", stim_unit)},
                "y": {"label": f"{D.MEASUREMENTS[yk][0]} of {ch}", "unit": D.unit_of(yk, units.get(ch, "V")),
                      "when": when},
                "points": points, "groups": groups, "spread": spread,
                "missing": sum(1 for i in chosen if not (np.isfinite(xs[i]) and np.isfinite(ys[i])))}

    def _stim_wave(self, v: dict, i: int):
        """(trial i's stimulus with its gain, its rate), or None (grouping.stim_wave)."""
        w = grouping.stim_wave(v["blocks"][i], v["how"])
        if w is None:
            return None
        gain = float(v["looks"].get("gains", {}).get(v["how"], 1.0) or 1.0)
        return (w[0] / gain if gain != 1 else w[0]), w[1]

    def _stim_measure(self, v: dict, i: int, key: str) -> float:
        def make():
            w = self._stim_wave(v, i)
            return SM.measure(*w) if w is not None else None
        gain = v["looks"].get("gains", {}).get(v["how"], 1.0)
        m = self.trials.store.memo(v["paths"][i], ("stim measures", v["how"], gain), make)
        return float(m[key]) if m and key in m else float("nan")

    def _response_measure(self, v: dict, i: int, ch: str, key: str, when: str, fs: float) -> float:
        def make():
            y = np.asarray(self._trace(v, i, ch, 0, fs, 0.0, 0.0), float)
            t0, t1 = 0.0, len(y) / fs
            if when == "stim":
                w = self._stim_wave(v, i) if v["how"] in v["blocks"][i]["channel_names"] else None
                span = SM.on_span(*w) if w is not None else None
                if span is None:
                    return float("nan")
                t0, t1 = span
            a, b = int(round(t0 * fs)), min(len(y), int(round(t1 * fs)))
            if b - a < 1:
                return float("nan")
            return float(D.measure(key, np.arange(a, b) / fs, y[a:b], fs))
        return self._keep(("measure", str(v["paths"][i]), ch, key, when, v["how"], self.trials.tag(ch)), make)

    # -- the spikes found (spikes.py) --------------------------------------------------------------
    def _spikes(self, v: dict):
        """(what spikes.py found, the trials ticked that were searched, in the Trials pane's
        order), or a note to show instead."""
        sp = self.trials.spikes.view()
        ev, ch = sp["events"], sp["channel"]
        if not ch:
            return None, None, "No channel to find spikes on"
        if ev is None:
            return None, None, "No spikes found yet: tick Identify spikes in the Spikes panel"
        _, chosen = self._chosen(v, [ch])
        if not chosen:
            return None, None, "No trials ticked: tick some in the Trials pane"
        return sp, chosen, ""

    def _raster(self, chart: dict, v: dict) -> dict:
        """Every spike of the units ticked, at its time in its trial, a row per trial ticked
        (spikecharts.RasterChart), each row marked in its group's color."""
        sp, chosen, note = self._spikes(v)
        if note:
            return {"note": note}
        ev = sp["events"]
        rows = {i: r for r, i in enumerate(chosen)}
        group = {i: g for g, (_, bl) in enumerate(v["shown"]) for i in bl}
        stems = [p.stem for p in v["paths"]]
        show = np.isin(ev.unit, sorted(sp["shown"])) & np.isin(ev.block, chosen)
        units = ev.unit[show]
        return {"rows": [{"i": i, "stem": stems[i], "c": v["colors"][group[i]] if i in group and group[i] < len(v["colors"])
                          else None} for i in chosen],
                "t": np.round(ev.t[show], 5).tolist(), "r": [rows[int(b)] for b in ev.block[show]],
                "u": units.astype(int).tolist(),
                "colors": {int(u): with_alpha(sp["color"][u], sp["opacity"][u]) for u in sp["color"]},
                "end": max(float(v["blocks"][i]["duration_s"]) for i in chosen), "dots": int(chart["fields"].get("dots", 3))}

    def _waveforms(self, chart: dict, v: dict, spread: str) -> dict:
        """Each unit's spikes (up to MAX_WAVES, faint) behind their average, with its spread
        (spikecharts.WaveformsChart): the voltage, or its first or second d/dt; normalized
        to the unit's average peak if asked."""
        sp, chosen, note = self._spikes(v)
        if note:
            return {"note": note}
        ev, f = sp["events"], chart["fields"]
        mine = np.isin(ev.block, chosen)
        if not mine.any():
            return {"note": "No spikes in the trials ticked"}
        order, norm, smooth = int(f.get("show") or 0), bool(f.get("norm")), float(f.get("smooth") or 0)
        tms = np.asarray(ev.wave_t_ms, float)
        fs = 1000 / float(np.median(np.diff(tms))) if len(tms) > 1 else 1.0
        deriv = lambda w: np.array([A.derivative(r, fs, order, smooth) for r in w])
        peak = int(np.argmin(np.abs(tms))) if len(tms) else 0
        rng = np.random.default_rng(0)
        out = []
        for u in sorted(sp["shown"]):
            w = ev.waves[mine & (ev.unit == u)]
            if not len(w):
                continue
            if norm:
                size = float(np.abs(w[:, peak]).mean())
                w = w / size if size > 0 else w
            shown = rng.choice(len(w), min(MAX_WAVES, len(w)), replace=False)
            mean = w.mean(0)
            if order:
                mean = A.derivative(mean, fs, order, smooth)
                each, w = deriv(w[shown]), (deriv(w) if spread else w)
            else:
                each = w[shown]
            band = None
            if spread:
                _, sd, sem, _ = A.mean_spread(w)
                band = np.nan_to_num(sd if spread == "sd" else sem).astype(np.float32).tolist()
            out.append({"u": int(u), "n": int(len(w)), "c": sp["color"][u], "a": sp["opacity"][u],
                        "mean": np.asarray(mean, np.float32).tolist(), "band": band,
                        "each": np.asarray(each, np.float32).tolist()})
        unit = sp["unit"]
        what = ("V", "dV/dt", "d²V/dt²")[order]
        return {"t": tms.astype(np.float32).tolist(), "units": out, "spread": spread,
                "y": {"label": ("÷ the unit's average peak" if not order else f"{what}, ÷ the unit's average peak")
                      if norm else sp["channel"] if not order else what,
                      "unit": "" if norm else unit if not order else f"{unit}/s" if order == 1 else f"{unit}/s²"}}
