"""The Trials pane's Python half: a session's trials in their groups, as the Qt Analysis tab
groups them (grouping.py) with what you changed (groupsfile.py, pages.json), and the
changes the page asks for, saved to pages.json the same way, so either front end can
open a session the other changed.

As AnalysisPage does, every channel is read here (recorded, trace): divided by its
amplifier gain, and the stimulus channel through the stimulus fit; calculated channels
(dsp.py, channelcalc.py) are worked out from them. Group alike stimuli joins stimuli close
on a map of their measures (stimmeasures.alike_map) until Keep these groups. The stimulus
channel picked is remembered for each session where the Qt app keeps it (its settings), so
each front end opens with the other's choice. Deleted trials go to the Trash, as in Qt.
The spikes found are spikes.py's.

Not yet as in Qt: stimulator units are volts; the calculated channels are kept in
~/.newt/web.json (the Qt app keeps its own in config.toml).
"""
from __future__ import annotations

import threading
import warnings
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .. import analysis as A
from .. import channelcalc as CC
from .. import chartplan, colors, dsp, grouping, groupsfile, session
from .. import stimmeasures as SM
from ..stim import Stimulus
from ..trialstore import TrialStore
from .stimulator import read_settings, update_settings

SPARK = 42                          # columns in a trial's little stimulus picture
NONE_FOUND = "No stimulus found"
HAND = (0.0, None)                  # the repeats, as Qt's Repeats box has them until set: found by itself
QT_SETTINGS = ("neusci", "neusci-daq-visualizer")


def session_folders(data: Path) -> list[Path]:
    """Session folders under the data folder (and data/open, where .newt files unpack, and
    data/unsaved, where Record starts new ones), newest first."""
    found = []
    for base in (data, data / "open", data / "unsaved"):
        if base.is_dir():
            found += [p for p in base.iterdir() if p.is_dir() and session.is_session(p)]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def session_name(p: Path, data: Path) -> str:
    """A session's name in the lists: where it is, if it isn't straight in the data folder."""
    where = {data / "open": " (opened)", data / "unsaved": " (recorded here, not saved yet)"}
    return p.name + where.get(p.parent, "")


# -- the stimulus channel, remembered for each session where the Qt app keeps it -------------------
def _qt_settings():
    try:
        from pyqtgraph.Qt import QtCore
        return QtCore.QSettings(*QT_SETTINGS)
    except Exception:                    # no Qt: in web.json instead
        return None


def saved_stim_channel(folder: Path) -> str | None:
    """The stimulus channel picked for this session ("" for none: the notes), or None if
    none was ever picked (AnalysisPage._fill_stim_channel)."""
    key = f"stim_channel/{Path(folder).resolve()}"
    s = _qt_settings()
    if s is not None:
        v = s.value(key)
        return None if v is None else str(v)
    return read_settings().get("stim_channels", {}).get(key)


def save_stim_channel(folder: Path, name: str) -> None:
    key = f"stim_channel/{Path(folder).resolve()}"
    s = _qt_settings()
    if s is not None:
        s.setValue(key, name)
        s.sync()
    else:
        update_settings(stim_channels={**read_settings().get("stim_channels", {}), key: name})


def to_trash(p: Path) -> bool:
    """Move a file to the Trash, as the Qt app does (QFile.moveToTrash)."""
    try:
        from pyqtgraph.Qt import QtCore
        got = QtCore.QFile.moveToTrash(str(p))
    except Exception:
        return False
    return bool(got[0] if isinstance(got, tuple) else got)


def _measure_name(key: str | None) -> str:
    """alikepanel.measure_name."""
    return SM.SHAPE_NAME.split(" (")[0].lower() if key == SM.SHAPE else SM.MEASURES[key][0] if key in SM.MEASURES else ""


class Trials:
    def __init__(self, data: Path):
        self.data = Path(data)
        self.store = TrialStore()
        self.folder: Path | None = None
        self.paths: list[Path] = []
        self.blocks: list[dict | None] = []
        self.how = ""
        self.busy = ""                       # what's being worked on, for the page
        self.note = ""                       # a message for the page, shown once
        self.busy_path = lambda: None        # the trial being recorded (Record's): never deleted
        self._lock = threading.RLock()
        self._state: dict | None = None      # the last state, until something changes
        self._view: dict = {}                # what the charts need of it (view())
        self._auto: tuple | None = None      # (how it was read, the stimulus groups): grouping is the slow part
        self._looks = groupsfile.load(None)  # pages.json: gains and the stimulus fit are read from it
        self.calcs = self._read_calcs()      # the calculated channels (dsp.Calculation)
        self._chain = dsp.Chain([], [], None)
        self._calcs: dict = {}               # name -> (calculation, formula) of those these trials can have
        self._map: dict | None = None        # Group alike stimuli's map
        self._shape_cache: tuple = (None, None)
        self._stim_label: dict[int, str] = {}
        self._kinds: dict[str, str] = {}
        self._averages: OrderedDict = OrderedDict()   # the preview's group averages
        from .spikes import Spikes
        self.spikes = Spikes(self)

    # -- opening -------------------------------------------------------------------
    def open(self, folder: str | Path) -> None:
        folder = Path(folder)
        with self._lock:
            self.busy = f"Reading {folder.name}…"
            self.folder, self.paths = folder, session.list_blocks(folder)
            self.blocks = []
            for p in self.paths:
                try:
                    self.blocks.append(self.store.read(p))
                except Exception:
                    self.blocks.append(None)
            self._looks = groupsfile.load(folder)
            self._make_chain()
            self.how = self._remembered_how()
            self._state = self._auto = None
            self._averages.clear()
            self.busy = ""
        self.spikes.trials_changed()

    def added(self, folder: Path) -> None:
        """Record added trials to `folder`: read again if it's the one open (or none is), and
        the list of sessions made again."""
        with self._lock:
            again = self.folder is None or Path(self.folder).resolve() == Path(folder).resolve()
            self._state = None
        if again:
            self.open(folder)

    def recorded_names(self) -> list[str]:
        good = next((b for b in self.blocks if b is not None), None)
        return list(good["channel_names"]) if good else []

    def names(self) -> list[str]:
        """The recorded channels, then the calculated ones."""
        return self.recorded_names() + list(self._calcs)

    def _remembered_how(self) -> str:
        """As the Qt app opens a session: the stimulus channel picked for it, else one called
        Stim; with none, what each trial saved it sent, else the notes."""
        names = self.recorded_names()
        saved = saved_stim_channel(self.folder) if self.folder else None
        ch = saved if saved is not None else next((n for n in names if "stim" in n.lower()), "")
        if ch in names:
            return ch
        if any(grouping.sent_key(b) for b in self.blocks):
            return "sent"
        return "notes" if any(b is not None and A.stimulus_notes(b["comments"]) for b in self.blocks) else ""

    # -- reading a channel, as every analysis sees it (AnalysisPage.recorded, trace) --------------
    @staticmethod
    def _read_calcs() -> list:
        out = []
        for c in read_settings().get("calculations", []):
            try:
                out.append(dsp.Calculation(str(c["name"]).strip(), str(c["formula"]).strip(), str(c.get("unit", ""))))
            except (KeyError, TypeError):
                continue
        return out

    def _make_chain(self) -> None:
        good = next((b for b in self.blocks if b is not None), None)
        self._chain = dsp.Chain(self.calcs, list(good["channel_names"]) if good else [],
                                good["sample_rate"] if good else None)
        self._calcs = {c.name: (c, f) for c, f in zip(self._chain.calcs, self._chain.formulas) if f is not None}

    def has(self, i: int, name: str) -> bool:
        b = self.blocks[i] if 0 <= i < len(self.blocks) else None
        if b is None:
            return False
        return name in b["channel_names"] or (name in self._calcs and
                                              self._chain.needs.get(name, {""}) <= set(b["channel_names"]))

    def gain_of(self, name: str) -> float:
        return float(self._looks["gains"].get(name, 1.0) or 1.0)

    def fit(self) -> str:
        f = self._looks.get("stim_fit") or ""
        return "" if f == "Off" else f

    def tag(self, name: str) -> tuple:
        """What changes how a channel reads: for the charts' kept traces."""
        if name in self._calcs:
            return ("calc", self._chain.key(), tuple(sorted(self._looks["gains"].items())))
        return (self.gain_of(name), self.fit() if name == self.how else "")

    def unit_of(self, name: str) -> str:
        if name in self._calcs:
            return self._calcs[name][0].unit
        for b in self.blocks:
            if b is not None and name in b["channel_names"]:
                return b["units"][b["channel_names"].index(name)]
        return "V"

    def recorded(self, i: int, name: str) -> np.ndarray | None:
        """A channel of trial i with its gain, before any stimulus fit."""
        b = self.blocks[i] if 0 <= i < len(self.blocks) else None
        if b is None:
            return None
        if name in b["channel_names"]:
            y = b["data"][b["channel_names"].index(name)]
            g = self.gain_of(name)
            return y if g == 1 else y / g
        if name not in self._calcs or not self._chain.needs.get(name, {""}) <= set(b["channel_names"]):
            return None
        gains, chain = self._looks["gains"], self._chain
        env = {n: y / gains.get(n, 1.0) for n, y in zip(b["channel_names"], b["data"])}
        key = ("calcs", chain.key()) if not gains else ("calcs", chain.key(), tuple(sorted(gains.items())))
        ys = self.store.memo(self.paths[i], key, lambda: chain.offline(env, b["sample_rate"]))
        return ys[[c.name for c in chain.calcs].index(name)]

    def trace(self, i: int, name: str) -> np.ndarray | None:
        """A channel of trial i as every analysis sees it: with its gain and, on the stimulus
        channel, the stimulus fit."""
        y = self.recorded(i, name)
        fit = self.fit()
        if y is None or not fit or name != self.how:
            return y
        return self.store.memo(self.paths[i], ("fit", name, fit, self.gain_of(name), HAND),
                               lambda: A.fit_stimulus(y, self.blocks[i]["sample_rate"], fit, *HAND, sent=self.sent(i)))

    def sent(self, i: int) -> tuple:
        """The stimuli sent during trial i, from the settings saved with it (AnalysisPage.sent)."""
        def rebuild():
            out = []
            for s in self.blocks[i].get("stimuli", ()):
                try:
                    stimulus = Stimulus.from_dict(s["settings"])
                    out.append(A.Sent(s["t"], stimulus.waveform(s["rate"]), s["rate"], *stimulus.repeat()))
                except (ValueError, KeyError, TypeError, OSError):
                    continue
            return tuple(out)
        return self.store.memo(self.paths[i], ("sent",), rebuild)

    # -- what the page shows ---------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            if self._state is None:
                self._state = self._work_out()
            note, self.note = self.note, ""
            return {**self._state, "busy": self.busy, "note": note}

    def view(self) -> dict:
        """What the charts draw from (charts.py), as the pane shows it now: the trials, their
        groups [(name, trials)], each group's color, the trials left out, and the looks."""
        with self._lock:
            if self._state is None:
                self._state = self._work_out()
            return self._view

    def _sig(self, i, y, fs):
        """Trial i's stimulus, read through the stimulus fit when one is chosen (and the gain):
        AnalysisPage._fitted_signature_of."""
        fit, how = self.fit(), self.how
        if not fit or how == "notes":
            return self.store.memo(self.paths[i], ("stimulus", how), lambda: A.stimulus_signature(y, fs))
        return self.store.memo(self.paths[i], ("stimulus", how, "fit", fit, self.gain_of(how), HAND),
                               lambda: A.stimulus_signature(self.trace(i, how), fs))

    def _measures(self, i: int) -> dict | None:
        def work():
            w = grouping.stim_wave(self.blocks[i], self.how)
            return SM.measure(*w) if w is not None else None
        return self.store.memo(self.paths[i], ("stim measures", self.how), work)

    def _mean_measures(self, blocks: list[int]) -> dict | None:
        ms = [m for m in (self._measures(i) for i in blocks) if m]
        return {k: float(np.mean([m[k] for m in ms])) for k in SM.MEASURES} if ms else None

    def _page_measure(self, blocks: list[int]) -> dict:
        """A group's measures, to sort and color by: its trials' stimulus measures, averaged;
        "trials", how many it has; "pc1", where it sits along the map."""
        out = dict(self._mean_measures(blocks) or {})
        out["trials"] = float(len(blocks))
        m = self._map
        if m is not None:
            at = {label: k for k, label in enumerate(m["labels"])}
            xs = [m["X"][at[self._stim_label[i]], 0] for i in blocks if self._stim_label.get(i) in at]
            if xs:
                out["pc1"] = float(np.mean(xs))
        return out

    def _spark(self, i: int):
        """Block i's stimulus as SPARK columns of (min, max), in volts (None: no stimulus)."""
        def work():
            w = grouping.stim_wave(self.blocks[i], self.how)
            if w is None:
                return None
            y = np.asarray(w[0], float)
            edges = np.linspace(0, len(y), SPARK + 1).astype(int)[:-1]
            return np.minimum.reduceat(y, edges), np.maximum.reduceat(y, edges)
        return self.store.memo(self.paths[i], ("spark", self.how, SPARK), work)

    def auto(self) -> list:
        """The stimulus groups (grouping.group), worked out again only when the stimulus is
        read another way (another channel, fit or gain)."""
        key = (self.how, self.fit(), self.gain_of(self.how))
        if self._auto is None or self._auto[0] != key:
            self._auto = (key, grouping.group(self.blocks, self.how, self._sig) if self.how else [])
        return self._auto[1]

    def _work_out(self) -> dict:
        d = groupsfile.load(self.folder) if self.folder else groupsfile.load(None)
        self._looks = d
        stems = [p.stem for p in self.paths]
        auto = self.auto()
        self._stim_label = {i: label for label, bl in auto for i in bl}
        d = self._alike_groups(auto, d)
        self._looks = d
        kinds = self._kinds = groupsfile.kinds(auto, d)
        shown = groupsfile.arrange(groupsfile.apply(auto, stems, d), d, self._page_measure) if stems else []
        names = [n for n, _ in shown]
        hidden = chartplan.hidden_blocks(shown, stems, d)
        values = self._values(shown, d)
        sparks = {i: self._spark(i) for _, bl in shown for i in bl}
        top = max([float(np.max(np.abs(np.r_[lo, hi]))) for s in sparks.values() if s for lo, hi in [s]] or [1.0]) or 1.0
        groups = []
        for g, (name, bl) in enumerate(shown):
            trials = []
            for i in bl:
                b = self.blocks[i]
                s = sparks.get(i)
                note = [*grouping.stimuli_note(b), *self._label(i)]
                trials.append({"i": i, "stem": stems[i], "dur": round(b["duration_s"], 2) if b else None,
                               "note": " · ".join(note), "off": i in hidden,
                               "spark": [np.round(s[0] / top, 3).tolist(), np.round(s[1] / top, 3).tolist()] if s else None})
            groups.append({"name": name, "color": self.group_color(g, names, d, values), "own": name in d["colors"],
                           "kind": kinds.get(name, ""), "hidden": name in d["hidden_groups"], "trials": trials})
        self._view = {"folder": self.folder, "paths": list(self.paths), "blocks": list(self.blocks), "how": self.how,
                      "shown": shown, "colors": [g["color"] for g in groups], "hidden": hidden, "looks": d,
                      "kinds": dict(kinds), "stim_label": dict(self._stim_label)}
        n = len([x for x in names if x != NONE_FOUND])
        recorded = self.recorded_names()
        return {
            "folder": str(self.folder) if self.folder else "", "name": self.folder.name if self.folder else "",
            "sessions": [{"path": str(p), "name": session_name(p, self.data)} for p in session_folders(self.data)],
            "how": self.how, "channels": recorded,
            "can_sent": any(grouping.sent_key(b) for b in self.blocks),
            "can_notes": any(b is not None and A.stimulus_notes(b["comments"]) for b in self.blocks),
            "groups": groups, "n_trials": len(self.paths), "n_stimuli": n,
            "sorts": d["sorts"], "organize": d["organize"],
            "measures": [[k, name] for k, (name, _) in SM.MEASURES.items()] + [["trials", "Number of trials"]]
                        + ([["pc1", "Place along the map"]] if self._map is not None else []),
            "looks": {k: d[k] for k in ("page_map", "trial_map", "trial_opacity", "base_color", "first_color",
                                        "last_color", "color_by", "avg_each")},
            "page_styles": list(colors.PAGE_STYLES), "trial_styles": list(colors.TRIAL_STYLES),
            "changed": bool(d["moved"] or d["renamed"]),
            "alike": self._alike_view(shown, auto, d, [g["color"] for g in groups]),
            # Channels: gains, the stimulus fit, calculated channels
            "recorded": [{"name": nm, "unit": self.unit_of(nm), "gain": self.gain_of(nm)} for nm in recorded],
            "fits": list(A.FITS), "stim_fit": d["stim_fit"] or "Off",
            "calcs": [{"name": c.name, "formula": c.formula, "unit": c.unit, "problem": why,
                       "spec": CC.spec_of(c.formula)} for c, why in zip(self._chain.calcs, self._chain.problems)],
            "calc_kinds": [[k, label, grp, [[s, *v] for s, v in sets]] for k, (label, grp, sets, _) in CC.KINDS.items()],
            "formula_help": CC.FORMULA_HELP,
        }

    def _label(self, i: int) -> tuple:
        try:
            label = self.store.info(self.paths[i]).get("label", "")
        except Exception:
            label = ""
        return (label,) if label else ()

    def _values(self, shown, d) -> dict:
        key = d.get("color_by")
        if not key:
            return {}
        out = {}
        for name, bl in shown:
            v = self._page_measure(bl).get(key)
            if v is not None and v == v:
                out[name] = float(v)
        return out

    @staticmethod
    def group_color(g: int, names: list[str], looks: dict, values: dict) -> str:
        """Group g's color, as the Qt Analysis tab works it out (AnalysisPage.group_color)."""
        given = looks["colors"].get(names[g]) if g < len(names) else None
        if given:
            return given
        style = looks["page_map"]
        left = [n for n in names if n not in looks["hidden_groups"]]
        if g < len(names) and names[g] in values:      # Color groups by: along a measure
            vals = [values[n] for n in left if n in values] or [values[names[g]]]
            lo, hi = min(vals), max(vals)
            f = (values[names[g]] - lo) / (hi - lo) if hi > lo else 0.5
            if style == colors.SHADES:
                return colors.shade(looks.get("base_color") or colors.BASE_COLOR, f).name()
            if style == colors.TWO:
                return colors.blend(looks.get("first_color"), looks.get("last_color"), f).name()
            return colors.sample(style if style in colors.MAPS else "Viridis", f).name()
        place = left.index(names[g]) if g < len(names) and names[g] in left else g
        if style in (colors.ONE, colors.SHADES):
            base = looks.get("base_color") or colors.BASE_COLOR
            if style == colors.ONE:
                return base
            return colors.shade(base, place / max(1, len(left) - 1) if len(left) > 1 else 1.0).name()
        if style in colors.MAPS or style == colors.TWO:
            f = place / max(1, len(left) - 1)
            if style == colors.TWO:
                return colors.blend(looks.get("first_color"), looks.get("last_color"), f).name()
            return colors.sample(style, f).name()
        return colors.group_color(g)

    # -- Group alike stimuli (AnalysisPage._alike_groups, _show_alike) ------------------------------
    def _shapes(self, points: list) -> np.ndarray:
        key = (tuple(str(self.paths[blocks[0]]) for _, blocks in points), self.how)
        if self._shape_cache[0] != key:
            waves = []
            for _, blocks in points:
                try:
                    waves.append(grouping.stim_wave(self.blocks[blocks[0]], self.how))
                except Exception:
                    waves.append(None)
            self._shape_cache = (key, SM.shape_alike(waves))
        return self._shape_cache[1]

    def _alike_groups(self, auto: list, d: dict) -> dict:
        """The map of the stimulus groups by the measures ticked (self._map), and the ones it
        joins now, saved (d["merged"]) when they change."""
        a, how = d["alike"], self.how
        points = [(label, blocks) for label, blocks in auto if label != NONE_FOUND]
        self._map = None
        merged = []
        if a["feats"] and len(points) >= 2 and how and how != "notes":
            meas = [self._mean_measures(blocks) for _, blocks in points]
            shape = self._shapes(points) if SM.SHAPE in a["feats"] else None
            m = SM.alike_map(meas, a["feats"], shape)
            if m is not None:
                m["labels"] = [label for label, _ in points]
                self._map = m
                if a["by"] == "close":
                    groups = SM.cut(m["D"], close=a["close"] / 100 * float(m["D"].max()))
                else:
                    groups = SM.cut(m["D"], groups=a["n"] if 0 < a["n"] < len(points) else None)
                merged = [[m["labels"][k] for k in g] for g in groups if len(g) > 1]
        if merged != d["merged"]:
            d = {**d, "merged": merged}
            if self.folder is not None:
                groupsfile.save(self.folder, d)
        return d

    def _alike_view(self, shown: list, auto: list, d: dict, group_colors: list[str]) -> dict:
        a, m, how = d["alike"], self._map, self.how
        n = len([label for label, _ in auto if label != NONE_FOUND])
        page_of = {i: g for g, (_, blocks) in enumerate(shown) for i in blocks}
        drawn = None
        if m is not None:
            points, joined = [], {}
            for k, label in enumerate(m["labels"]):
                blocks = next((b for lab, b in auto if lab == label), [])
                g = next((page_of[i] for i in blocks if i in page_of), None)
                points.append([float(m["X"][k, 0]), float(m["X"][k, 1]),
                               group_colors[g] if g is not None and g < len(group_colors) else "#888888", label])
                if g is not None:
                    joined.setdefault(g, []).append(k)
            axes = [{"share": round(100 * float(m["share"][k])), "mostly": _measure_name(m["mostly"][k]),
                     "measure": m["mostly"][k] in SM.MEASURES} for k in (0, 1)]
            drawn = {"points": points, "joined": list(joined.values()), "axes": axes}
        why = ("" if m is not None else
               "Pick the stimulus channel (in the Trials pane) to compare the stimuli" if not how or how == "notes" else
               "Needs two different stimuli or more" if n < 2 else
               "Tick a measure to place each stimulus on a map" if not a["feats"] else
               "The measures ticked are the same for every stimulus")
        return {"feats": a["feats"], "by": a["by"], "n": a["n"], "close": a["close"], "stimuli": n, "map": drawn,
                "why": why, "color_by": d.get("color_by", ""), "avg_each": bool(d.get("avg_each")),
                "can_keep": "alike" in self._kinds.values(),
                "ticks": [[SM.SHAPE, SM.SHAPE_NAME]] + [[k, name] for k, (name, _) in SM.MEASURES.items()]}

    # -- the preview: every recorded channel of a trial, or of a group's average ---------------------
    def preview(self, c: dict) -> tuple[bytes, str]:
        from .charts import Charts
        cols = max(50, min(2000, int(c.get("cols", 300))))
        with self._lock:
            if self._state is None:
                self._state = self._work_out()
            shown, colors_ = self._view.get("shown", []), self._view.get("colors", [])
            if c.get("avg") is not None:
                got = self._average(str(c["avg"]), shown, colors_)
            else:
                got = self._one(int(c.get("i", -1)), shown, colors_)
        if got is None:
            return Charts._pack({"note": "Nothing to preview"}, [np.zeros(0)]), "application/octet-stream"
        title, color, fs, chans = got
        n = max(len(y) for _, _, y in chans)
        x = chartplan.envelope_x(0, n, cols, fs)
        arrays, plots = [x], []
        for name, unit, y in chans:
            y = np.asarray(y, float)
            if len(y) < n:
                y = np.r_[y, np.full(n - len(y), np.nan)]
            e = chartplan.envelope(y, 0, n, cols)
            ok = np.isfinite(e)
            plots.append({"label": name, "unit": unit,
                          "yr": [float(np.min(e[ok])), float(np.max(e[ok]))] if ok.any() else None})
            arrays.append(e)
        return Charts._pack({"title": title, "color": color, "n": len(x), "end": n / fs, "plots": plots}, arrays), \
            "application/octet-stream"

    def _one(self, i: int, shown, colors_):
        b = self.blocks[i] if 0 <= i < len(self.blocks) else None
        if b is None:
            return None
        title, color = self.paths[i].stem, None
        for g, (name, blocks) in enumerate(shown):
            if i in blocks:                       # the trial's name in its group, and the group's color
                title, color = f"{name} {blocks.index(i) + 1}", colors_[g] if g < len(colors_) else None
                break
        units = dict(zip(b["channel_names"], b["units"]))
        return title, color, float(b["sample_rate"]), [(n, units[n], self.recorded(i, n)) for n in b["channel_names"]]

    def _average(self, name: str, shown, colors_):
        """Every recorded channel averaged sample by sample over the group's trials ticked (all
        of them if none is), each from its start (AnalysisPage._preview_average)."""
        g = next((k for k, (nm, _) in enumerate(shown) if nm == name), None)
        if g is None:
            return None
        idx = [i for i in shown[g][1] if 0 <= i < len(self.blocks) and self.blocks[i] is not None]
        hidden = self._view.get("hidden", set())
        ticked = [i for i in idx if i not in hidden]
        idx = ticked if ticked else idx
        if len(idx) < 2:
            return None
        first = self.blocks[idx[0]]
        fs = first["sample_rate"]
        idx = [i for i in idx if self.blocks[i]["sample_rate"] == fs]
        key = (tuple(str(self.paths[i]) for i in idx), tuple(sorted(self._looks["gains"].items())))
        chans = self._averages.get(key)
        if chans is None:
            units = dict(zip(first["channel_names"], first["units"]))
            chans = []
            for n in first["channel_names"]:
                rows = [np.asarray(self.recorded(i, n), float) for i in idx if n in self.blocks[i]["channel_names"]]
                if not rows:
                    continue
                Y = np.full((len(rows), max(len(r) for r in rows)), np.nan)
                for r, y in enumerate(rows):
                    Y[r, :len(y)] = y
                with np.errstate(all="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    chans.append((n, units.get(n, "V"), np.nanmean(Y, axis=0)))
            self._averages[key] = chans
            while len(self._averages) > 12:
                self._averages.popitem(last=False)
        return f"{name} · average of {len(idx)}", colors_[g] if g < len(colors_) else None, float(fs), chans

    # -- calculated channels ---------------------------------------------------------------------------
    def calc_check(self, c: dict) -> dict:
        """A calculated channel as the form has it: its formula, name and unit (made up until
        typed), and why it can't be made, if it can't (AnalysisChannelDialog._check)."""
        with self._lock:
            old = str(c.get("old") or "")
            above = [x.name for x in self.calcs]
            above = above[:above.index(old)] if old in above else above
            sources = self.recorded_names() + above
            kind, src, v = str(c.get("kind", "")), str(c.get("source", "")), dict(c.get("values") or {})
            if kind not in CC.KINDS:
                return {"why": "Pick a calculation."}
            taken = {x.name for x in self.calcs if x.name != old}
            formula = CC.formula_of(kind, src, v) if kind != "custom" else str(v.get("formula", "")).strip()
            name = str(c.get("name") or "").strip() or CC.auto_name(kind, src, v, taken | set(sources))
            unit = c.get("unit")
            unit = str(unit).strip() if unit is not None else CC.unit_for(kind, self.unit_of(src), v)
            good = next((b for b in self.blocks if b is not None), None)
            why = ""
            if not name:
                why = "Give it a name."
            elif name in taken or name in sources:
                why = f"{name!r} is already used: give it another name."
            else:
                try:
                    dsp.Formula(formula, sources).check(float(good["sample_rate"]) if good else 1000.0)
                except ValueError as e:
                    why = str(e)
            return {"formula": formula, "name": name, "unit": unit, "why": why, "sources": sources}

    def _save_calcs(self, calcs: list) -> None:
        self.calcs = calcs
        update_settings(calculations=[{"name": x.name, "formula": x.formula, "unit": x.unit} for x in calcs])
        self._make_chain()

    # -- changes from the page -------------------------------------------------------
    def command(self, c: dict) -> None:
        cmd = c.get("cmd")
        if cmd == "open":
            return self.open(c["folder"])
        with self._lock:
            if cmd == "calc":                          # make, change or delete a calculated channel
                return self._calc(c)
            if self.folder is None:
                raise RuntimeError("Open a session first.")
            if cmd == "channel":
                self.how = str(c["how"])
                save_stim_channel(self.folder, self.how if self.how in self.recorded_names() else "")
                self._state = None
                self._averages.clear()
            elif cmd == "regroup":
                self.store.forget()
                self.open(self.folder)
                return
            elif cmd == "delete":
                self._delete([int(i) for i in c.get("blocks", [])])
                return
            else:
                d = groupsfile.load(self.folder)
                stems = [p.stem for p in self.paths]
                auto = self.auto()
                shown = [n for n, _ in groupsfile.arrange(groupsfile.apply(auto, stems, d), d, self._page_measure)]
                d = self._change(cmd, c, d, auto, stems, shown)
                groupsfile.save(self.folder, d)
                self._looks = d
                self._state = None
                if cmd == "gain":
                    self._averages.clear()
        self.spikes.trials_changed()

    def _calc(self, c: dict) -> None:
        old = str(c.get("old") or "")
        calcs = list(self.calcs)
        at = next((k for k, x in enumerate(calcs) if x.name == old), None)
        if c.get("delete"):
            if at is not None:
                calcs.pop(at)
        else:
            got = self.calc_check(c)
            if got["why"]:
                raise ValueError(got["why"])
            new = dsp.Calculation(got["name"], got["formula"], got["unit"])
            if at is None:
                calcs.append(new)
            else:
                calcs[at] = new
        self._save_calcs(calcs)
        self._state = None

    def _delete(self, blocks: list[int]) -> None:
        """Move these trials' files to the Trash (the page asked first), with their other files
        (video, edits…), as the Qt app does. Not the one being recorded."""
        busy = self.busy_path()
        paths = [self.paths[i] for i in sorted(set(blocks)) if 0 <= i < len(self.paths)]
        left = [p for p in paths if (busy is None or Path(p) != Path(busy)) and p.exists()]
        if not left:
            self.note = "That trial is being recorded: stop recording to delete it" if paths else ""
            return
        gone, failed = [], []
        for p in left:
            extras = [x for x in p.parent.glob(p.stem + ".*") if x != p]
            if to_trash(p):
                gone.append(p)
                for x in extras:
                    to_trash(x)
                self.store.forget(p)
            else:
                failed.append(p.name)
        self.open(self.folder)
        one = len(gone) == 1
        self.note = f"Moved {len(gone)} trial{'' if one else 's'} to the Trash (put {'it' if one else 'them'} back from there)"
        if failed:
            self.note += f"; couldn't move {', '.join(failed)}"

    @staticmethod
    def _change(cmd: str, c: dict, d: dict, auto, stems, shown) -> dict:
        drop = lambda xs, x: [n for n in xs if n != x]
        if cmd == "tick":                         # a group, or a trial; a group takes its trials,
            if c["kind"] == "page":              # except those you set yourself
                d["hidden_groups"] = drop(d["hidden_groups"], c["name"]) + ([] if c["on"] else [c["name"]])
            else:
                s = stems[int(c["i"])]
                d["hidden_trials"], d["shown_trials"] = drop(d["hidden_trials"], s), drop(d["shown_trials"], s)
                d["shown_trials" if c["on"] else "hidden_trials"].append(s)
        elif cmd == "tick_all":
            d.update(hidden_groups=[] if c["on"] else list(shown), hidden_trials=[], shown_trials=[])
        elif cmd == "tick_lit":                  # Only these (only) / tick the highlighted
            ss = {stems[int(i)] for i in c.get("blocks", [])}
            pages = set(c.get("pages", []))
            if c.get("only"):
                d.update(hidden_groups=[n for n in shown if n not in pages], hidden_trials=[], shown_trials=sorted(ss))
            else:
                d["hidden_groups"] = [n for n in d["hidden_groups"] if n not in pages]
                d["hidden_trials"] = [s for s in d["hidden_trials"] if s not in ss]
                d["shown_trials"] = sorted(set(d["shown_trials"]) | ss)
        elif cmd == "rename":
            d = groupsfile.rename(auto, d, c["old"], c["new"])
        elif cmd == "move_group":                 # your order: this group at this row
            names = list(shown)
            at, row = names.index(c["name"]), int(c["row"])
            names.insert(row - 1 if at < row else row, names.pop(at))
            d.update(order=names, sort="", sorts=[])
        elif cmd == "sorts":
            d.update(sorts=[[str(k), -1 if w == -1 else 1] for k, w in c["sorts"]], sort="")
        elif cmd == "organize":
            d["organize"] = "time" if c["key"] == "time" else ""
        elif cmd == "combine":
            d = groupsfile.combine(auto, d, list(c["names"]))
        elif cmd == "split":
            d = groupsfile.split(auto, d, c["name"])
        elif cmd == "move":                       # trials to a group (an existing one or a new name)
            for i in c["blocks"]:
                d = groupsfile.move(auto, stems, d, int(i), str(c["page"]))
        elif cmd == "color":                      # a group's own color; None: back to the setting
            d["colors"] = {k: v for k, v in d["colors"].items() if k != c["name"]}
            if c.get("color"):
                d["colors"][c["name"]] = c["color"]
        elif cmd == "looks":
            for k in ("page_map", "trial_map", "base_color", "first_color", "last_color", "color_by"):
                if k in c:
                    d[k] = str(c[k])
            if "trial_opacity" in c:
                d["trial_opacity"] = int(c["trial_opacity"])
            if "avg_each" in c:
                d["avg_each"] = bool(c["avg_each"])
        elif cmd == "gain":                       # a channel's amplifier gain (1: none)
            g = float(c["gain"])
            if not g > 0:
                raise ValueError("A gain is a number above 0 (1 means no amplifier).")
            d["gains"] = {k: v for k, v in {**d["gains"], str(c["name"]): g}.items() if v != 1}
        elif cmd == "fit":
            kind = str(c["kind"])
            if kind not in A.FITS:
                raise ValueError(f"No stimulus fit called {kind!r}")
            d["stim_fit"] = "" if kind == "Off" else kind
        elif cmd == "alike":                      # Group alike stimuli's setting: the groups follow
            change = {k: c[k] for k in ("feats", "by", "n", "close") if k in c}
            d["alike"] = {**d["alike"], **change}
        elif cmd == "keep_alike":                 # the groups the map joined, kept as ordinary groups
            d = groupsfile.keep(auto, d)
        elif cmd == "reset":
            d.update(renamed={}, moved={})
        else:
            raise ValueError(f"Unknown command {cmd!r}")
        return d
