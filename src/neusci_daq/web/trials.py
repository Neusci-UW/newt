"""The Trials pane's Python half: a session's trials in their groups, as the Qt Analysis tab
groups them (grouping.py) with what you changed (groupsfile.py, pages.json), and the
changes the page asks for, saved to pages.json the same way, so either front end can
open a session the other changed.

Not yet as in Qt: Group alike stimuli is shown as saved (merged), not worked out again;
a stimulus fit, amplifier gains and calculated channels aren't applied to the stimulus;
the stimulus channel picked here isn't remembered between runs; stimulator units are volts.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from .. import analysis as A
from .. import colors, grouping, groupsfile, session
from .. import stimmeasures as SM
from ..trialstore import TrialStore

SPARK = 42                          # columns in a trial's little stimulus picture
NONE_FOUND = "No stimulus found"


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


class Trials:
    def __init__(self, data: Path):
        self.data = Path(data)
        self.store = TrialStore()
        self.folder: Path | None = None
        self.paths: list[Path] = []
        self.blocks: list[dict | None] = []
        self.how = ""
        self.busy = ""                       # what's being worked on, for the page
        self._lock = threading.RLock()
        self._state: dict | None = None      # the last state, until something changes
        self._auto: tuple | None = None      # (how, the stimulus groups): grouping is the slow part

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
            self.how = self._default_how()
            self._state = self._auto = None
            self.busy = ""

    def added(self, folder: Path) -> None:
        """Record added trials to `folder`: read again if it's the one open (or none is), and
        the list of sessions made again."""
        with self._lock:
            again = self.folder is None or Path(self.folder).resolve() == Path(folder).resolve()
            self._state = None
        if again:
            self.open(folder)

    def names(self) -> list[str]:
        good = next((b for b in self.blocks if b is not None), None)
        return list(good["channel_names"]) if good else []

    def _default_how(self) -> str:
        """As the Qt app does for a session with no channel picked: one called Stim, else
        what each trial saved it sent, else the notes."""
        names = self.names()
        ch = next((n for n in names if "stim" in n.lower()), "")
        if ch:
            return ch
        if any(grouping.sent_key(b) for b in self.blocks):
            return "sent"
        return "notes" if any(b is not None and A.stimulus_notes(b["comments"]) for b in self.blocks) else ""

    # -- what the page shows ---------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            if self._state is None:
                self._state = self._work_out()
            return {**self._state, "busy": self.busy}

    def _sig(self, i, y, fs):
        return self.store.memo(self.paths[i], ("stimulus", self.how), lambda: A.stimulus_signature(y, fs))

    def _measures(self, i: int) -> dict | None:
        def work():
            w = grouping.stim_wave(self.blocks[i], self.how)
            return SM.measure(*w) if w is not None else None
        return self.store.memo(self.paths[i], ("stim measures", self.how), work)

    def _page_measure(self, blocks: list[int]) -> dict:
        ms = [m for m in (self._measures(i) for i in blocks) if m]
        out = {k: float(np.mean([m[k] for m in ms])) for k in SM.MEASURES} if ms else {}
        out["trials"] = float(len(blocks))
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
        """The stimulus groups (grouping.group), worked out again only for another channel."""
        if self._auto is None or self._auto[0] != self.how:
            self._auto = (self.how, grouping.group(self.blocks, self.how, self._sig) if self.how else [])
        return self._auto[1]

    def _work_out(self) -> dict:
        d = groupsfile.load(self.folder) if self.folder else groupsfile.load(None)
        stems = [p.stem for p in self.paths]
        auto = self.auto()
        kinds = groupsfile.kinds(auto, d)
        shown = groupsfile.arrange(groupsfile.apply(auto, stems, d), d, self._page_measure) if stems else []
        names = [n for n, _ in shown]
        hidden = {i for n, bl in shown if n in d["hidden_groups"] for i in bl}
        for i, s in enumerate(stems):
            if s in d["hidden_trials"]:
                hidden.add(i)
            elif s in d["shown_trials"]:
                hidden.discard(i)
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
        n = len([x for x in names if x != NONE_FOUND])
        return {
            "folder": str(self.folder) if self.folder else "", "name": self.folder.name if self.folder else "",
            "sessions": [{"path": str(p), "name": session_name(p, self.data)} for p in session_folders(self.data)],
            "how": self.how, "channels": self.names(),
            "can_sent": any(grouping.sent_key(b) for b in self.blocks),
            "can_notes": any(b is not None and A.stimulus_notes(b["comments"]) for b in self.blocks),
            "groups": groups, "n_trials": len(self.paths), "n_stimuli": n,
            "sorts": d["sorts"], "organize": d["organize"],
            "measures": [[k, name] for k, (name, _) in SM.MEASURES.items()] + [["trials", "Number of trials"]],
            "looks": {k: d[k] for k in ("page_map", "trial_map", "trial_opacity", "base_color", "first_color",
                                        "last_color", "color_by")},
            "page_styles": list(colors.PAGE_STYLES), "trial_styles": list(colors.TRIAL_STYLES),
            "changed": bool(d["moved"] or d["renamed"]),
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

    # -- changes from the page -------------------------------------------------------
    def command(self, c: dict) -> None:
        cmd = c.get("cmd")
        if cmd == "open":
            return self.open(c["folder"])
        with self._lock:
            if self.folder is None:
                raise RuntimeError("Open a session first.")
            if cmd == "channel":
                self.how = str(c["how"])
                self._state = None
                return
            if cmd == "regroup":
                self.store.forget()
                self.open(self.folder)
                return
            d = groupsfile.load(self.folder)
            stems = [p.stem for p in self.paths]
            auto = self.auto()
            shown = [n for n, _ in groupsfile.arrange(groupsfile.apply(auto, stems, d), d, self._page_measure)]
            d = self._change(cmd, c, d, auto, stems, shown)
            groupsfile.save(self.folder, d)
            self._state = None

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
            for k in ("page_map", "trial_map", "base_color", "first_color", "last_color"):
                if k in c:
                    d[k] = str(c[k])
            if "trial_opacity" in c:
                d["trial_opacity"] = int(c["trial_opacity"])
        elif cmd == "reset":
            d.update(renamed={}, moved={})
        else:
            raise ValueError(f"Unknown command {cmd!r}")
        return d
