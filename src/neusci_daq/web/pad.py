"""The DataPad's Python half: the table of measurements (padmodel.py does the measuring, as
in the Qt app), worked out here, off any screen's thread, and sent to the page as text,
never the raw samples. Its rows follow the Trials pane (trials.py): its groups, their
order and its ticks (an unticked trial's rows are listed, struck out, not measured).

- the table: a row per trial (or segment, pulse, or stretch added by hand), a column per
  measurement the student adds (nothing is added for them), with mean ± SEM rows of all
  rows, each group or each value of a column;
- the preview: a row's trial, stimulus over response, shrunk to two points per screen
  column (chartplan.envelope), its segments, the picked column's stretch shaded with its
  value, and the quick values of whatever was last clicked or dragged there;
- charts of one column against another (Y against X), one dot per ticked row.

Saved with the session in analysis.json as the Qt app saves it: "pad" (padmodel's
Pad.state) and "baseline", so either front end opens what the other changed. The charts
are saved under "pad_charts" (the Qt app keeps them, and doesn't show them).

Not yet as in Qt: no spike measurements (no spike finder here yet); the preview shows
trial time only (not stimulus time or phase); a stimulus fit isn't applied; stimulator
units are volts; no Bars charts, typed-in points or Edit points; no recipe files or CSV.
"""
from __future__ import annotations

import json
import threading
import warnings

import numpy as np

from .. import analysisfile, chartplan
from .. import datapad as D
from .. import padmodel as P
from .charts import Charts

PAD, BASELINE, CHARTS = "pad", "baseline", analysisfile.PAD_CHARTS
STIM_UNITS = ("V", 1.0)                 # (unit, per volt) of a stimulus level: the stimulator's volts
QUICK_KEYS = ("value", "mean", "max", "min", "p2p", "slope", "area")
QUICK_SHORT = {"value": "Value", "mean": "Mean", "max": "Max", "min": "Min", "p2p": "Peak-to-peak",
               "slope": "Slope", "area": "Area"}
SERIES = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9")   # a chart's Y columns, when several
ROW_ORDER = "#row"                      # a chart's X: the rows in order (as padcharts.ROW_ORDER)
FAINT = 30                              # an average's own trials drawn faint behind it, at most


def menu() -> list:
    """What + Column offers, grouped: [(group, [(kind, key, label, help)])]; no spikes yet."""
    out = [(g, [("measure", k, D.MEASUREMENTS[k][0], D.MEASUREMENTS[k][2]) for k in keys])
           for g, keys in P.GROUPS if g != "Spikes"]
    out.append(("Stimulus", [("level", "", "Level of a segment", "Read from the stimulus that was sent"),
                             ("duration", "", "Length of a segment", "How long it lasts, from the stimulus")]))
    out.append(("Trial", [("info", k, lab, "") for k, (lab, _) in P.INFO.items()]))
    out.append(("Work out", [("formula", "", "Formula", "One line of the other columns: = dV / I_B * 1000"),
                             ("python", "", "Python", "A short Python function, run for each row")]))
    return out


class Source(P.Source):
    """The session's trials as the Trials pane shows them, as the DataPad reads them (the Qt
    app's padpage.PageSource)."""

    def __init__(self, pad: "Pad"):
        self.pad = pad

    def trials(self) -> list[int]:
        v = self.pad.v
        blocks = v.get("blocks", [])
        seen, out = set(), []
        for _, bl in v.get("shown", []):
            for i in bl:
                if i not in seen and 0 <= i < len(blocks) and blocks[i] is not None:
                    seen.add(i)
                    out.append(i)
        return out + [i for i, b in enumerate(blocks) if b is not None and i not in seen]

    def trace(self, i, channel):
        return self.pad.trace(i, channel)

    def unit(self, channel):
        return self.pad.units.get(channel, "V") if channel else ""

    def segments(self, i):
        return self.pad.segments(i)

    def spikes(self, i, units=None):
        return None

    def spike_channel(self):
        return ""

    def group(self, i):
        return self.pad.groups.get(i, ("", 1))

    def stim_units(self):
        return STIM_UNITS

    def duration(self, i):
        b = self.pad.v["blocks"][i]
        return float(b["duration_s"]) if b is not None else 0.0


class Pad:
    def __init__(self, trials):
        self.trials = trials
        self._lock = threading.RLock()
        self.pad = P.Pad()
        self.src = Source(self)
        self.v: dict = {}                    # the Trials pane as last seen (Trials.view)
        self.units: dict[str, str] = {}
        self.groups: dict[int, tuple[str, int]] = {}
        self.colors: dict[str, str] = {}
        self._folder = None
        self._seen = None
        self._data = None                    # what every measurement reads: when it changes, measure again
        self.tab = P.Table([])
        self._fresh = False
        self.version = 0                     # bumped each time the table is worked out
        self.by = "all"                      # mean ± SEM rows of: None, "all", "group" or a column
        self.quick: P.Selection | None = None   # what was last clicked or dragged on the preview
        self.base_on, self.base_sel = False, None
        self.charts: list[dict] = []

    # -- keeping up with the Trials pane -------------------------------------------------------
    def _sync(self) -> None:
        v = self.trials.view()
        folder = v.get("folder")
        if folder != self._folder:
            self._folder = folder
            st = analysisfile.read_state(folder) if folder else {}
            self.pad.restore(st.get(PAD))
            b = st.get(BASELINE) or {}
            self.base_sel = P.Selection.from_dict(b["sel"]) if isinstance(b.get("sel"), dict) else None
            self.base_on = bool(b.get("on")) and self.base_sel is not None
            got = (st.get(CHARTS) or {}).get("charts", [])
            self.charts = [c for c in got if isinstance(c, dict) and c.get("kind") == "Scatter" and c.get("id")]
            self.quick = None
            self._fresh = False
        if v is not self._seen:
            self._seen = self.v = v
            good = next((b for b in v.get("blocks", []) if b is not None), None)
            self.units = dict(zip(good["channel_names"], good["units"])) if good else {}
            self.groups = {i: (name, k + 1) for name, bl in v.get("shown", []) for k, i in enumerate(bl)}
            self.colors = {name: c for (name, _), c in zip(v.get("shown", []), v.get("colors", []))}
            self._data_changed()
            self._fresh = False

    def _data_changed(self) -> None:
        """The stimulus channel, a gain or the baseline changed: measure everything again."""
        v = self.v
        sig = json.dumps([str(v.get("folder")), v.get("how"), v.get("looks", {}).get("gains", {}),
                          self.base_on, self.base_sel.to_dict() if self.base_sel else None], sort_keys=True)
        if sig != self._data:
            self._data = sig
            self.pad.data_changed()
            self._fresh = False

    def table(self) -> P.Table:
        if not self._fresh:
            P.retitle(self.pad.columns, self.src)
            self.tab = self.pad.compute(self.src, skip=self.v.get("hidden", set()))
            self._fresh = True
            self.version += 1
        return self.tab

    # -- what the measurements read -------------------------------------------------------------
    def _gain(self, ch: str) -> float:
        return float(self.v.get("looks", {}).get("gains", {}).get(ch, 1.0) or 1.0)

    def raw(self, i: int, ch: str):
        """Trial i's channel with its amplifier gain taken off (no baseline), or None."""
        blocks = self.v.get("blocks", [])
        b = blocks[i] if 0 <= i < len(blocks) else None
        if b is None or not ch or ch not in b["channel_names"]:
            return None
        y = b["data"][b["channel_names"].index(ch)]
        g = self._gain(ch)
        return y / g if g != 1 else y

    def trace(self, i: int, ch: str):
        """(trial i's channel as every measurement reads it: gain and baseline off, its rate)."""
        y = self.raw(i, ch)
        if y is None:
            return None
        fs = float(self.v["blocks"][i]["sample_rate"])
        w = self.baseline_window(i) if ch != self.v.get("how") else None
        if w is not None:
            part = y[max(0, int(round(w[0] * fs))):int(round(w[1] * fs))]
            if len(part):
                y = y - float(np.nanmean(part))
        return np.asarray(y, float), fs

    def segments(self, i: int) -> list:
        """Trial i's segments: those of the stimuli it sent, lined up on the stimulus channel,
        else read off that channel (padmodel.trial_segments)."""
        blocks = self.v.get("blocks", [])
        if not (0 <= i < len(blocks)) or blocks[i] is None:
            return []
        b, how = blocks[i], self.v.get("how", "")
        ch = how if how in b["channel_names"] else ""

        def work():
            y = self.raw(i, ch) if ch else None
            fs = float(b["sample_rate"])
            return P.trial_segments(b.get("stimuli"), P.send_onsets(b.get("stimuli"), y, fs), y, fs)
        return self.trials.store.memo(self.v["paths"][i], ("pad segments", ch, self._gain(ch) if ch else 1.0), work)

    def baseline_window(self, i: int) -> tuple[float, float] | None:
        if not self.base_on or self.base_sel is None:
            return None
        segs = self.segments(i)
        w = self.base_sel.window({s[0]: (s[1], s[2]) for s in segs}, [s[0] for s in segs])
        return w if w is not None and w[1] > w[0] else None

    # -- the state the page shows ----------------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            self._sync()
            tab = self.table()
            return self._state(tab)

    def _state(self, tab: P.Table) -> dict:
        v, src, cols = self.v, self.src, self.pad.columns
        hidden = v.get("hidden", set())
        stems = [p.stem for p in v.get("paths", [])]
        rows, seen = [], set()
        for r in tab.rows:
            name, k = src.group(r.trial)
            rows.append({"trial": r.trial, "label": r.label, "stem": stems[r.trial] if r.trial < len(stems) else "",
                         "group": name, "n": k, "color": self.colors.get(name, ""), "off": r.trial in hidden,
                         "first": r.trial not in seen, "t0": r.t0, "t1": r.t1})
            seen.add(r.trial)
        cells = []
        for c in cols:
            if c.name in tab.text:
                cells.append([t if not row["off"] else "" for t, row in zip(tab.text[c.name], rows)])
            else:
                cells.append(["" if row["off"] else (c.fmt(x) or "—") for x, row in zip(tab.column(c.name), rows)])
        letters = []
        for i in src.trials():
            for s in src.segments(i):
                L = P.send_of(s[0])[1]           # B stands for B of every stimulus sent
                if L not in letters:
                    letters.append(L)
        return {
            "folder": str(self._folder or ""), "version": self.version, "how": v.get("how", ""),
            "channels": [{"name": n, "unit": u} for n, u in self.units.items()],
            "rows_mode": self.pad.rows_mode, "rows_seg": self.pad.rows_seg, "rows_modes": P.ROWS,
            "letters": letters, "manual": len(self.pad.manual),
            "columns": [self._column(c, tab) for c in cols], "rows": rows, "cells": cells,
            "summary": self._summary(tab), "by": self.by,
            "baseline": {"on": self.base_on, "sel": self.base_sel.to_dict() if self.base_sel else None,
                         "label": self.base_sel.label() if self.base_sel else ""},
            "quick": self.quick.to_dict() if self.quick else None,
            "quick_label": self.quick.label() if self.quick else "",
            "menu": menu(), "measures": {k: [m[0], m[2]] for k, m in D.MEASUREMENTS.items()},
            "info": {k: lab for k, (lab, _) in P.INFO.items()},
            "funcs": P.FUNC_HELP, "charts": self.charts,
        }

    @staticmethod
    def _column(c: P.Column, tab: P.Table) -> dict:
        return {"name": c.name, "shown": c.shown(), "title": c.title, "title_auto": c.title_auto, "kind": c.kind,
                "key": c.key, "channel": c.channel, "over": c.over, "sel": c.sel.to_dict(), "where": c.sel.label(),
                "expr": c.expr, "scope": c.scope, "own_unit": c.unit, "unit": tab.units.get(c.name, ""),
                "digits": c.digits, "places": c.places, "recipe": c.recipe(), "error": tab.errors.get(c.name, ""),
                "number": c.name not in tab.text}

    def _summary(self, tab: P.Table) -> list[dict]:
        by, cols = self.by, self.pad.columns
        if by is None or not cols or (by not in ("all", "group") and self.pad.get(by) is None):
            return []
        inc = tab.without(self.v.get("hidden", set()))
        sums = P.summarize(inc, self.src, by)
        keys = P.summary_keys(inc, self.src, by)
        out = []
        for g, stats in sums:
            trials = list(dict.fromkeys(r.trial for r, k in zip(inc.rows, keys) if k == g))
            if by == "all":
                label, title = "All rows", "Average of all rows"
            elif by == "group":
                label, title = g or "(no group)", f"Average of {g or '(no group)'}"
            else:
                shown = self.pad.get(by).shown()
                label, title = f"{shown} = {g}", f"Average where {shown} = {g}"
            texts, ns = [], []
            for c in cols:
                m, s, n = stats.get(c.name, (np.nan, np.nan, 0))
                texts.append((c.fmt(m) + (f" ± {c.fmt(s)}" if np.isfinite(s) and s > 0 else "")) if n else "")
                ns.append(n)
            out.append({"label": label, "title": f"{title} · {len(trials)} trial{'' if len(trials) == 1 else 's'}",
                        "trials": trials, "color": self.colors.get(g, "") if by == "group" else "",
                        "cells": texts, "n": ns})
        return out

    # -- changes from the page ------------------------------------------------------------------
    def command(self, d: dict) -> dict:
        """Do what the page asked; {"picked": k} when a column was added (the page picks it)."""
        with self._lock:
            self._sync()
            if self._folder is None:
                raise RuntimeError("Open a session first: the DataPad is saved with it.")
            cmd, out = d.get("cmd"), {}
            if cmd in ("add", "quick_add"):
                out["picked"] = self._add(d)
            elif cmd in ("segment", "point", "stretch"):
                self._clicked(cmd, d)
            elif cmd == "baseline":
                sel = d.get("sel")
                if sel:
                    self.base_sel = P.Selection.from_dict(sel)
                self.base_on = bool(d.get("on")) and self.base_sel is not None
                analysisfile.save_part(self._folder, BASELINE, {"on": self.base_on, "sel": self.base_sel.to_dict()
                                                                if self.base_sel else None})
                self._data_changed()
                return out
            elif cmd == "by":
                self.by = d.get("by")
                self._fresh = False
                return out
            elif cmd.startswith("chart_"):
                self._chart_cmd(cmd, d)
                analysisfile.save_part(self._folder, CHARTS, {"charts": self.charts})
                return out
            else:
                self._change(cmd, d)
            self._fresh = False
            P.retitle(self.pad.columns, self.src)
            analysisfile.save_part(self._folder, PAD, self.pad.state())
            return out

    def _col(self, d: dict) -> P.Column:
        k = d.get("k")
        if not isinstance(k, int) or not 0 <= k < len(self.pad.columns):
            raise KeyError("That column is gone.")
        return self.pad.columns[k]

    def _add(self, d: dict) -> int:
        names, q = self.pad.names(), self.quick
        if d["cmd"] == "quick_add":
            if q is None:
                raise ValueError("Click the trace, a segment or drag a stretch on the preview first.")
            ch = d.get("channel", "")
            c = P.quick_column(d["key"], ch, self.src.unit(ch), q, names)
        else:
            segs = self.segments(int(d.get("trial", -1)))
            if q is not None and (not q.is_time() or q.is_point() or q.t1 > q.t0):
                sel = P.Selection.from_dict(q.to_dict())          # where you last clicked or dragged
            else:
                sel = P.Selection.seg(segs[1][0] if len(segs) > 1 else (segs[0][0] if segs else "A"))
            over = "window" if (q is not None and q.is_time()) or (self.pad.rows_mode == "trial" and segs) else "row"
            ch = d.get("channel", "")
            kind = d.get("kind", "measure")
            if kind not in P.KINDS:
                raise ValueError(f"No column kind {kind!r}")
            c = P.new_column(kind, d.get("key", ""), names, sel, over, ch, self.src.unit(ch), "", STIM_UNITS[0])
        self.pad.columns.append(c)
        return len(self.pad.columns) - 1

    def _clicked(self, cmd: str, d: dict) -> None:
        """A click or drag on the preview: the quick values measure there, and the picked column
        (k, if one is) measures there in every trial (padpage's _segment_clicked and the rest)."""
        trial = int(d.get("trial", -1))
        c = self.pad.columns[d["k"]] if isinstance(d.get("k"), int) and 0 <= d["k"] < len(self.pad.columns) else None
        c = c if c is not None and c.kind in ("measure", "level", "duration") else None
        segs = self.segments(trial)
        if cmd == "segment":
            letter, extend = str(d["letter"]), bool(d.get("extend"))
            q = self.quick
            if extend and q is not None and not q.is_time():
                order = [s[0] for s in segs]
                a, b = q.first, letter
                if a in order and b in order and order.index(b) < order.index(a):
                    a, b = b, a
                self.quick = P.Selection(a, b)
            else:
                self.quick = P.Selection.seg(letter)
            if c is not None:
                P.window_to_segment(c, letter, extend, segs)
        elif cmd == "point":
            blocks = self.v.get("blocks", [])
            fs = float(blocks[trial]["sample_rate"]) if 0 <= trial < len(blocks) and blocks[trial] else 1000.0
            t = max(0.0, round(float(d["t"]) * fs) / fs)         # on a sample
            self.quick = P.Selection.point(t)
            if c is not None:
                P.window_to_point(c, t)
        else:
            t0, t1 = sorted((max(0.0, float(d["t0"])), max(0.0, float(d["t1"]))))
            if t1 - t0 > 1e-4:
                self.quick = P.Selection.time(t0, t1)
            if self.pad.rows_mode == "manual" and d.get("k") is None:
                return                                            # waits for Add as a row
            if c is not None:
                P.window_to_time(c, t0, t1)

    def _change(self, cmd: str, d: dict) -> None:
        cols = self.pad.columns
        if cmd == "set":
            c = self._col(d)
            for k, val in (d.get("set") or {}).items():
                if k == "title":
                    c.title, c.title_auto = str(val).strip(), not str(val).strip()
                elif k == "sel":
                    c.sel = P.Selection.from_dict(val)
                elif k in ("unit", "channel", "over", "expr", "scope"):
                    setattr(c, k, str(val))
                elif k == "key" and val in D.MEASUREMENTS | P.INFO:
                    c.key = val
                elif k == "digits":
                    c.digits = max(0, min(12, int(val)))
                elif k == "places":
                    c.places = bool(val)
                    c.digits = max(c.digits, 0 if c.places else 1)
        elif cmd == "what":                       # what a column is: a measurement, a stimulus value, info…
            c, kind, key = self._col(d), d["kind"], d.get("key") or ""
            if kind not in P.KINDS:
                raise ValueError(f"No column kind {kind!r}")
            c.kind = kind
            if kind == "measure" and not c.channel:
                c.channel = d.get("channel", "")
            if kind in ("measure", "level", "duration") and c.sel.is_time() and c.sel.t1 <= c.sel.t0 \
                    and not c.sel.is_point():
                segs = self.segments(int(d.get("trial", -1)))
                c.sel = P.Selection.seg(segs[1][0] if len(segs) > 1 else (segs[0][0] if segs else "A"))
            if kind == "measure":
                c.key = key if key in D.MEASUREMENTS else c.key if c.key in D.MEASUREMENTS else "mean"
            elif kind == "info":
                c.key = key if key in P.INFO else c.key if c.key in P.INFO else "trial"
        elif cmd == "name":
            c, new = self._col(d), str(d["name"]).strip()
            if new != c.name:
                why = P.check_name(new, [n for n in self.pad.names() if n != c.name])
                if why:
                    raise ValueError(why)
                self.pad.rename(c, new)
        elif cmd == "fx":
            c = self._col(d)
            cols[cols.index(c)] = P.parse_recipe(str(d["text"]), c, list(self.units))
        elif cmd == "move":
            c = self._col(d)
            k = cols.index(c)
            to = k + int(d["by"])
            if 0 <= to < len(cols):
                cols[k], cols[to] = cols[to], cols[k]
        elif cmd == "remove":
            cols.remove(self._col(d))
        elif cmd == "clear":
            self.pad.columns = []
        elif cmd == "rows":
            if d.get("mode") in P.ROWS:
                self.pad.rows_mode = d["mode"]
            self.pad.rows_seg = str(d.get("seg") or "")
        elif cmd == "add_row":
            t0, t1 = sorted((float(d["t0"]), float(d["t1"])))
            self.pad.manual.append({"trial": int(d["trial"]), "t0": t0, "t1": t1})
        elif cmd == "clear_rows":
            self.pad.manual = []
        else:
            raise ValueError(f"Unknown command {cmd!r}")

    # -- the preview ----------------------------------------------------------------------------
    def preview(self, d: dict) -> tuple[bytes, str]:
        """A row's trial (or a summary row's trials, averaged), stimulus over response, the
        times x0 to x1 (s; none: all of it) on `cols` screen columns, packed as Charts._pack
        packs a chart, with its segments, the picked column's stretch and value, the baseline
        and the quick values in the header."""
        with self._lock:
            self._sync()
            tab = self.table()
            head, arrays = self._preview(d, tab)
        return Charts._pack(head, arrays), "application/octet-stream"

    def _preview(self, d: dict, tab: P.Table):
        v = self.v
        if not v.get("folder"):
            return {"note": "Open a session"}, [np.zeros(0)]
        chans = [ch for ch in (d.get("stim"), d.get("resp")) if ch in self.units]
        avg = [int(i) for i in d.get("avg") or [] if 0 <= int(i) < len(v["blocks"]) and v["blocks"][int(i)]]
        trial = int(d.get("trial", -1))
        trials = avg or ([trial] if 0 <= trial < len(v["blocks"]) and v["blocks"][trial] is not None else [])
        if not trials or not chans:
            return {"note": "Pick a row of the table to see its trial" if not trials else "Pick the channels"}, [np.zeros(0)]
        fs = float(v["blocks"][trials[0]]["sample_rate"])
        trials = [i for i in trials if float(v["blocks"][i]["sample_rate"]) == fs]
        n = max(len(v["blocks"][i]["data"][0]) for i in trials)
        cols = max(50, min(4000, int(d.get("cols", 800))))
        x0, x1 = d.get("x0"), d.get("x1")
        if x0 is None or x1 is None or not float(x1) > float(x0):
            x0, x1 = 0.0, n / fs
        i0 = max(0, min(n - 1, int(np.floor(float(x0) * fs))))
        i1 = max(i0 + 2, min(n, int(np.ceil(float(x1) * fs)) + 1))
        arrays, plots = [chartplan.envelope_x(i0, i1, cols, fs)], []
        for ch in chans:
            series, lo, hi = [], np.inf, -np.inf
            ys = [y for y in (self.trace(i, ch) for i in trials) if y is not None]

            def add(y, k):
                nonlocal lo, hi
                e = chartplan.envelope(y, i0, i1, cols)
                if np.isfinite(e).any():
                    lo, hi = min(lo, float(np.nanmin(e))), max(hi, float(np.nanmax(e)))
                arrays.append(e)
                series.append(k)
            if len(ys) > 1:
                for y, _ in ys[:FAINT]:
                    add(y, "faint")
                Y = np.full((len(ys), max(len(y) for y, _ in ys)), np.nan)
                for r, (y, _) in enumerate(ys):
                    Y[r, :len(y)] = y
                with np.errstate(all="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore")       # (columns past a shorter trial's end)
                    add(np.nanmean(Y, axis=0), "bold")
            elif ys:
                add(ys[0][0], "bold")
            plots.append({"label": ch, "unit": self.units.get(ch, "V"), "series": series,
                          "yr": [lo, hi] if hi >= lo else None, "stim": ch == v.get("how")})
        head = {"n": len(arrays[0]), "end": n / fs, "x0": i0 / fs, "x1": (i1 - 1) / fs, "plots": plots,
                "fs": fs, "avg": bool(avg)}
        segs = self.segments(trials[0])
        head["segs"] = [[s[0], s[1], s[2], str(s[3].get("kind", ""))] for s in segs]
        bw = self.baseline_window(trials[0])
        head["base"] = list(bw) if bw is not None else None
        if avg:
            head.update(win=None, lit=[], value="", quick=None, label=d.get("title") or "Average")
            return head, arrays
        head.update(self._window_of(d, tab, trial, segs))
        head["quick"] = self._quick_values(trial, segs)
        return head, arrays

    def _window_of(self, d: dict, tab: P.Table, i: int, segs: list) -> dict:
        """The picked column's stretch in this row, the segments it measures, its value
        (padpage's _preview_window); with none, the quick values' own."""
        k = d.get("row")
        row = tab.rows[k] if isinstance(k, int) and 0 <= k < len(tab.rows) and tab.rows[k].trial == i else None
        cols = self.pad.columns
        c = cols[d["col"]] if isinstance(d.get("col"), int) and 0 <= d["col"] < len(cols) else None
        order = [s[0] for s in segs]
        send = P.send_at(segs, row.t0) if row is not None else 1
        seen = P.scoped(segs, send)               # the column's B is B of the row's own stimulus
        seen_order = [s[0] for s in seen]
        letters, w, value = set(), None, ""
        if c is not None:
            if c.kind in ("measure", "level", "duration") and not (c.kind == "measure" and c.over == "row"):
                letters = {L if send == 1 or P.send_of(L)[0] > 1 else f"{send}{L}" for L in c.sel.letters(seen_order)}
            wins = tab.windows.get(c.name)
            if wins is not None and row is not None and k < len(wins):
                w = wins[k]
            elif c.kind in ("level", "duration"):
                w = c.sel.window({s[0]: (s[1], s[2]) for s in seen}, seen_order)
            if row is not None and c.name in tab.values and row.trial not in self.v.get("hidden", set()):
                text = tab.text[c.name][k] if c.name in tab.text else (c.fmt(tab.values[c.name][k]) or "—")
                value = f"{text} {tab.units.get(c.name, '')}".strip()
        if row is not None and row.t0 is not None and not letters and self.pad.rows_mode in ("segment", "pulse"):
            letters = {row.label.split("·")[0]}
        if c is None or not letters:              # nothing of a column's: the quick values' own
            q = self.quick
            if q is not None and not q.is_time():
                letters = set(q.letters(order))
            if q is not None and w is None and c is None:
                w = q.window({s[0]: (s[1], s[2]) for s in segs}, order)
        if w is not None and not np.isfinite(w[1]):
            w = (w[0], self.src.duration(i))
        label = f"Trial {i + 1}" + (f" · row {row.label}" if row is not None and row.label else "")
        name, k_in = self.src.group(i)
        if name:
            label += f" · {name}, trial {k_in} of the group"
        return {"win": list(w) if w is not None else None, "lit": sorted(letters), "value": value,
                "vname": c.shown() if c is not None and value else "", "label": label,
                "recipe": c.recipe() if c is not None else ""}

    def _quick_values(self, i: int, segs: list) -> dict | None:
        """The common measurements of what was last clicked or dragged, in this trial, on every
        channel (padpage's quick row): each one, clicked, becomes a column."""
        q = self.quick
        if q is None:
            return None
        w = q.window({s[0]: (s[1], s[2]) for s in segs}, [s[0] for s in segs])
        if w is None:
            return {"where": q.label(), "missing": True, "lines": []}
        point = q.is_point() or (q.is_phase() and q.ph0 == q.ph1)
        keys = ("value",) if point else [k for k in QUICK_KEYS if k != "value"]
        lines = []
        for ch in self.units:
            if self.raw(i, ch) is None:
                continue
            u, out = self.units[ch], []
            for key in keys:
                x = P.measure_sel(self.src, i, ch, key, q, segs)
                out.append([key, QUICK_SHORT[key], f"{P.fmt(x, 3)} {D.unit_of(key, u)}".strip() if np.isfinite(x) else "—",
                            D.MEASUREMENTS[key][0]])
            lines.append([ch, out])
        return {"where": q.label(), "missing": False, "lines": lines}

    # -- for figures (web/figures.py) --------------------------------------------------------------
    def snapshot(self, cols: list[str]) -> dict:
        """The numbers a figure plots, as the Qt DataPad gives them (padpage.snapshot): these
        columns of the ticked rows, their groups and the groups' colors."""
        with self._lock:
            self._sync()
            inc = self.table().without(self.v.get("hidden", set()))
            return {"rows": [r.trial + 1 for r in inc.rows], "groups": [self.src.group(r.trial)[0] for r in inc.rows],
                    "colors": dict(self.colors),
                    "cols": {c: [float(v) for v in inc.column(c)] for c in cols if c in inc.values},
                    "units": {c: inc.units.get(c, "") for c in cols}}

    def fresh_version(self) -> int:
        """The table's version, worked out again first if the Trials pane or a column changed."""
        with self._lock:
            self._sync()
            self.table()
            return self.version

    def numbers(self) -> list[dict]:
        """The columns of numbers a figure can plot: name, shown name, unit."""
        with self._lock:
            self._sync()
            tab = self.table()
            return [{"name": c.name, "shown": c.shown(), "unit": tab.units.get(c.name, "")}
                    for c in self.pad.columns if c.name not in tab.text]

    def chart_specs(self) -> list[dict]:
        """The DataPad's charts (Y against X), as their settings."""
        with self._lock:
            self._sync()
            return json.loads(json.dumps(self.charts))

    # -- charts of one column against another -----------------------------------------------------
    def _chart_cmd(self, cmd: str, d: dict) -> None:
        if cmd == "chart_add":
            taken = {c["id"] for c in self.charts}
            cid = next(f"Scatter {k}" for k in range(1, 10000) if f"Scatter {k}" not in taken)
            self.charts.append({"id": cid, "kind": "Scatter", "name": cid, "fields": {"x": None, "y": []},
                                "look": {}, "typed": [], "removed": []})
            return
        chart = next((c for c in self.charts if c["id"] == d.get("id")), None)
        if chart is None:
            raise KeyError("That chart is gone.")
        if cmd == "chart_remove":
            self.charts.remove(chart)
        elif cmd == "chart_set":
            f = chart.setdefault("fields", {})
            if "x" in d:
                f["x"] = d["x"] or None
            if "y" in d:
                f["y"] = [str(n) for n in d["y"]]
            for k in ("xlog", "ylog"):
                if k in d:
                    chart.setdefault("look", {})[k] = bool(d[k])
        else:
            raise ValueError(f"Unknown command {cmd!r}")

    def chart(self, d: dict) -> dict:
        """What a chart draws: one dot per ticked row, Y against X (a column, or the rows in
        order); with one Y each dot in its group's color, with several each Y in its own."""
        with self._lock:
            self._sync()
            tab = self.table()
            chart = next((c for c in self.charts if c["id"] == d.get("id")), None)
            if chart is None:
                return {"note": "That chart is gone"}
            f, look = chart.get("fields", {}), chart.get("look", {})
            numbers = [c.name for c in self.pad.columns if c.name in tab.values and c.name not in tab.text]
            xk = f.get("x")
            ys = [y for y in f.get("y") or [] if y in numbers]
            if not numbers:
                return {"note": "Add columns to the DataPad, then choose Y and X here"}
            if not xk or not ys or (xk != ROW_ORDER and xk not in numbers):
                return {"note": "Choose what runs up (Y) and what runs across (X)"}
            inc = tab.without(self.v.get("hidden", set()))
            k_of = {r.key(): k for k, r in enumerate(tab.rows)}
            x = np.arange(1, len(inc.rows) + 1, dtype=float) if xk == ROW_ORDER else np.asarray(inc.column(xk), float)
            shown = {c.name: c.shown() for c in self.pad.columns}
            points, series = [], []
            for j, yk in enumerate(ys):
                y = np.asarray(inc.column(yk), float)
                keep = np.isfinite(x) & np.isfinite(y)
                if look.get("xlog"):
                    keep &= x > 0
                if look.get("ylog"):
                    keep &= y > 0
                color = SERIES[j % len(SERIES)]
                series.append({"name": shown.get(yk, yk), "unit": inc.units.get(yk, ""), "color": color})
                for r in np.flatnonzero(keep):
                    row = inc.rows[r]
                    g = self.src.group(row.trial)[0]
                    c = self.colors.get(g, color) if len(ys) == 1 else color
                    points.append([float(x[r]), float(y[r]), c, k_of.get(row.key(), -1), row.trial,
                                   f"Trial {row.trial + 1}" + (f" · {row.label}" if row.label else "") +
                                   (f" · {g}" if g else "")])
            return {"x": {"name": "Row" if xk == ROW_ORDER else shown.get(xk, xk),
                          "unit": "" if xk == ROW_ORDER else inc.units.get(xk, ""), "sub": xk != ROW_ORDER},
                    "ys": series, "points": points, "xlog": bool(look.get("xlog")), "ylog": bool(look.get("ylog")),
                    "left": len(inc.rows) - len({p[3] for p in points}) if len(ys) == 1 else 0}
