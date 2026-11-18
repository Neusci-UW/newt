"""The Spikes panel's Python half: finding the action potentials every chart shares and
sorting them into units, as the Qt app's SpikeFinder does (spikefinder.py, analysis.py).

Its settings are SpikeFinder.state()'s, and they're saved where the Qt app saves them, with
the spikes found: analysis.json ("spikes") and analysis_spikes.npz (analysisfile.py), so
either front end opens what the other found. Searching runs on its own thread (the page
asks how it's going), never on a request's; each trial's noise and events are kept between
searches, so a new threshold is quick.

Not yet as in Qt: dragging the threshold on a chart; Export events / averages.
"""
from __future__ import annotations

import threading
from dataclasses import replace

import numpy as np

from .. import analysis as A
from .. import analysisfile, colors
from ..units import best_prefix

DEFAULTS = {"channel": "", "method": 0, "threshold": "5 SD", "polarity": "negative", "biphasic": False,
            "bip_frac": 0.3, "bip_ms": 1.0, "dead": 1.0, "pre": 1.0, "post": 2.0, "units": 0, "unticked": [],
            "identify": False, "colors": {}, "opacity": {}}
NUMBERS = {"bip_frac": (0.05, 1.0), "bip_ms": (0.1, 20.0), "dead": (0.1, 100.0), "pre": (0.1, 50.0),
           "post": (0.1, 50.0)}
POLARITIES = ("negative", "positive", "either")
SAVED = "saved"                     # _done before the first look: whatever the saved spikes covered


class Spikes:
    def __init__(self, trials):
        self.trials = trials
        self._lock = threading.RLock()
        self._folder = None
        self._stems: list[str] = []
        self.s = dict(DEFAULTS)
        self.events: A.Events | None = None
        self._last: A.Events | None = None       # the last spikes found, to keep their units' numbers
        self._memo: dict = {}                     # each trial's noise and events, kept between searches
        self._fresh = False                       # Find again: everything over again
        self._done: set | str = set()             # the trials the spikes found are from
        self._on: tuple | None = None             # (channel, how it was read) they were found on
        self._gen = 0                             # bumped by every change: a search from before is dropped
        self._worker: threading.Thread | None = None
        self._again = False
        self.busy = ""
        self.message, self.error = "", False
        self.version = 0                          # bumped whenever the spikes change (the charts redraw)

    # -- what the trials are now (asked of trials.py first: its lock, then this one's) -------------
    def _snapshot(self) -> dict:
        t = self.trials
        v = t.view() if t.folder is not None else {}
        names = [n for n in t.names() if n != t.how]
        return {"folder": t.folder, "paths": t.paths, "stems": [p.stem for p in t.paths], "names": names,
                "hidden": v.get("hidden", set()), "blocks": t.blocks}

    def _channel(self, snap: dict) -> str:
        """The channel searched: the one picked, else the first (never the stimulus)."""
        ch = self.s["channel"]
        return ch if ch in snap["names"] else (snap["names"][0] if snap["names"] else "")

    def _searched(self, snap: dict) -> list[int]:
        """The ticked trials that have this channel at the first trial's rate (SpikeFinder.usable)."""
        ch, blocks, t = self._channel(snap), snap["blocks"], self.trials
        good = [b for b in blocks if b is not None]
        fs = good[0]["sample_rate"] if good else 0
        return [i for i in range(len(blocks)) if i not in snap["hidden"] and t.has(i, ch)
                and blocks[i]["sample_rate"] == fs]

    def _sync(self, snap: dict) -> None:
        """The spikes of the session open now: read from its folder when another is opened,
        and kept by trial name when trials are added or deleted."""
        if snap["folder"] != self._folder:
            self._folder, self._stems = snap["folder"], snap["stems"]
            self._load()
        elif snap["stems"] != self._stems:
            where = {s: i for i, s in enumerate(snap["stems"])}
            old = self._stems
            self._stems = snap["stems"]
            def moved(ev):
                if ev is None:
                    return None
                keep = np.array([old[b] in where for b in ev.block], bool)
                return replace(ev, block=np.array([where[old[b]] for b in ev.block[keep]], int), t=ev.t[keep],
                               unit=ev.unit[keep], amplitude=ev.amplitude[keep], waves=ev.waves[keep])
            same = self._last is self.events
            self.events = moved(self.events)
            self._last = self.events if same else moved(self._last)
            if isinstance(self._done, set):
                self._done = {where[old[i]] for i in self._done if i < len(old) and old[i] in where}
            self.version += 1

    def _load(self) -> None:
        self.s, self.events, self._last, self._memo = dict(DEFAULTS), None, None, {}
        self._done, self._on, self.message, self.error = set(), None, "", False
        self._gen += 1
        self.version += 1
        folder = self._folder
        saved = analysisfile.read_state(folder).get("spikes") if folder is not None else None
        if not isinstance(saved, dict):
            return
        try:
            _, ev = analysisfile.load(folder, self._stems)
        except (OSError, ValueError, KeyError, TypeError):
            ev = None
        self.s = {**DEFAULTS, **{k: saved[k] for k in DEFAULTS if k in saved}}
        self.s["identify"] = bool(saved.get("identify", ev is not None))
        self.events = self._last = ev
        self._done = SAVED if ev is not None else set()

    def trials_changed(self) -> None:
        """The trials were read again or ticked otherwise, or the stimulus channel changed:
        search those not searched yet (SpikeFinder.trials_changed, settings_changed). Looked
        into on a thread of its own: the caller (a request, or Record saving a trial) never waits."""
        threading.Thread(target=self._trials_changed, daemon=True, name="web-spikes-check").start()

    def _trials_changed(self) -> None:
        snap = self._snapshot()
        with self._lock:
            self._sync(snap)
            on = (self._channel(snap), self.trials.tag(self._channel(snap)))
            if self._done == SAVED:
                self._done, self._on = set(self._searched(snap)), on
                return
            if not self.s["identify"]:
                return
            if on != self._on or set(self._searched(snap)) != self._done:
                self._gen += 1
                self._kick()

    # -- what the page and the charts see --------------------------------------------------------
    def color_of(self, u: int) -> str:
        return self.s["colors"].get(str(int(u)), colors.unit_color(u))

    def opacity_of(self, u: int) -> float:
        return float(self.s["opacity"].get(str(int(u)), 1.0))

    def shown_units(self) -> set[int]:
        n = self.events.n_units if self.events is not None else 0
        return set(range(n)) - set(self.s["unticked"])

    def _fmt(self, v: float, unit: str) -> str:
        n, u = best_prefix(v, unit)
        return f"{n:.3g} {u}"

    def view(self) -> dict:
        """What the spike charts draw: the spikes found, the units ticked, their colors."""
        snap = self._snapshot()
        with self._lock:
            self._sync(snap)
            ch = self._channel(snap)
            return {"events": self.events, "shown": self.shown_units(), "channel": ch,
                    "unit": self.trials.unit_of(ch) if ch else "V", "version": self.version,
                    "color": {u: self.color_of(u) for u in range(self.events.n_units if self.events else 0)},
                    "opacity": {u: self.opacity_of(u) for u in range(self.events.n_units if self.events else 0)}}

    def state(self) -> dict:
        snap = self._snapshot()
        with self._lock:
            self._sync(snap)
            ch = self._channel(snap)
            unit = self.trials.unit_of(ch) if ch else "V"
            ev, rows = self.events, []
            message, error = self.message, self.error
            if ev is not None:
                for s in ev.summary():
                    u = s["unit"] - 1
                    rows.append({"unit": s["unit"], "count": s["count"], "rate": round(float(s["rate"]), 2),
                                 "size": self._fmt(s["amplitude"], unit), "width": round(s["width_ms"], 2),
                                 "color": self.color_of(u), "opacity": self.opacity_of(u),
                                 "on": u not in self.s["unticked"]})
                if not error:
                    n = len(ev.t)
                    message = " ".join([(f"{n} event(s) in {len(np.unique(ev.block))} trial(s), {ev.n_units} unit(s); "
                                         f"threshold {self._fmt(ev.level, unit)} from the baseline." if n else
                                         f"No events past the threshold ({self._fmt(ev.level, unit)}): try a lower one.")]
                                       + list(ev.notes))
            return {"folder": str(self._folder or ""), "settings": {**self.s, "channel": ch}, "channels": snap["names"],
                    "unit": unit, "units": rows, "message": message, "error": error,
                    "busy": self.busy if self._worker is not None else "", "searching": self._worker is not None,
                    "version": self.version, "polarities": [["negative", "Negative (extracellular)"],
                                                            ["positive", "Positive (intracellular)"],
                                                            ["either", "Either"]]}

    # -- changes from the page --------------------------------------------------------------------
    def command(self, c: dict) -> None:
        snap = self._snapshot()
        with self._lock:
            self._sync(snap)
            if self._folder is None:
                raise RuntimeError("Open a session first.")
            cmd = c.get("cmd")
            if cmd == "set":
                self._set(dict(c.get("values") or {}), snap)
            elif cmd == "identify":
                if c.get("on"):
                    self._fresh = True
                    self.s["identify"] = True
                    self._gen += 1
                    self._kick()
                else:
                    self.s["identify"] = False
                    self._gen += 1
                    self.events, self.message, self.error = None, "", False
                    self.version += 1
                    self._save(True)
            elif cmd == "find":
                self._fresh = True
                self._gen += 1
                self._kick()
            elif cmd == "clear":
                self._gen += 1
                self.s["identify"] = False
                self.events = self._last = None
                self.s["unticked"] = []
                self.message, self.error = "Cleared. Press Find again to search again.", False
                self.version += 1
                self._save(True)
            elif cmd == "unit":                   # a unit ticked into the charts, or out
                u = int(c["u"])
                left = set(self.s["unticked"]) - {u}
                self.s["unticked"] = sorted(left if c.get("on") else left | {u})
                self.version += 1
                self._save(False)
            elif cmd == "unit_color":
                self.s["colors"] = {**self.s["colors"], str(int(c["u"])): str(c["color"])}
                self.version += 1
                self._save(False)
            elif cmd == "unit_opacity":
                self.s["opacity"] = {**self.s["opacity"], str(int(c["u"])): max(0.05, min(1.0, float(c["opacity"])))}
                self.version += 1
                self._save(False)
            else:
                raise ValueError(f"Unknown command {cmd!r}")

    def _set(self, values: dict, snap: dict) -> None:
        s = dict(self.s)
        for k, v in values.items():
            if k == "channel":
                s[k] = str(v)
            elif k == "threshold":
                A.parse_threshold(str(v), self.trials.unit_of(self._channel(snap)) or "V")   # raises: shown
                s[k] = str(v).strip()
            elif k == "method":
                s[k] = 1 if v else 0
            elif k == "polarity":
                if v not in POLARITIES:
                    raise ValueError(f"No direction called {v!r}")
                s[k] = v
            elif k == "biphasic":
                s[k] = bool(v)
            elif k == "units":
                s[k] = max(0, min(8, int(v)))
            elif k in NUMBERS:
                lo, hi = NUMBERS[k]
                s[k] = max(lo, min(hi, float(v)))
        changed = s != self.s
        self.s = s
        self.message, self.error = "", False
        if not changed:
            return
        self._gen += 1
        if self.s["identify"]:
            self._kick()                          # the spikes shown stay until the new ones are found
        else:
            self.events = None
            self.version += 1
        self._save(not self.s["identify"])

    # -- searching ------------------------------------------------------------------------------------
    def _kick(self) -> None:
        """Search now on the worker thread, or once more when it's done."""
        if self._worker is not None:
            self._again = True
            return
        self._again = False
        self.busy = "Searching…"
        self._worker = threading.Thread(target=self._run, daemon=True, name="web-spikes")
        self._worker.start()

    def _run(self) -> None:
        while True:
            try:
                self._find_once()
            except ValueError as e:
                with self._lock:
                    self.message, self.error = str(e), True
            except Exception as e:                 # shown, never a dead thread
                with self._lock:
                    self.message, self.error = f"Couldn't search: {e}", True
            with self._lock:
                if not self._again:
                    self._worker, self.busy = None, ""
                    return
                self._again = False

    def _progress(self, done: int, total: int) -> None:
        if total > 4:
            self.busy = f"Finding action potentials: {done} of {total} trials…"

    def _find_once(self) -> None:
        snap = self._snapshot()
        with self._lock:
            self._sync(snap)
            s, gen = dict(self.s), self._gen
            before = self.events if self.events is not None else self._last
            if self._fresh or len(self._memo) > 20000:
                self._memo, self._fresh = {}, False
            memo = self._memo
        t = self.trials
        ch = self._channel(snap)
        chosen = self._searched(snap)
        if not chosen:
            raise ValueError("No trials to search: tick some in Trials, and check the Channel.")
        self.busy = f"Searching {len(chosen)} trial(s)…"
        kind, v = A.parse_threshold(s["threshold"], t.unit_of(ch))
        if s["method"] and kind == "level":
            kind, v = "sd", 5.0                       # bimodal's fallback is in SDs
        tag = t.tag(ch)
        ev = A.find_events([(i, t.trace(i, ch), snap["blocks"][i]["sample_rate"]) for i in chosen],
                           threshold=v, fixed=kind == "level", bimodal=bool(s["method"]),
                           biphasic=s["bip_frac"] if s["biphasic"] else None, biphasic_ms=s["bip_ms"],
                           polarity=s["polarity"], dead_ms=s["dead"], pre_ms=s["pre"], post_ms=s["post"],
                           n_units=s["units"] or None, memo=memo,
                           keys=[(str(snap["paths"][i]), ch, *tag) for i in chosen], progress=self._progress)
        if before is not None:
            ev.unit = A.match_units(before, ev)       # the same unit keeps its number, so its color
        with self._lock:
            if gen != self._gen or snap["paths"] is not t.paths:
                return                                # changed meanwhile: searched again
            self.events = self._last = ev
            self._done, self._on = set(chosen), (ch, tag)
            self.s["channel"] = ch                    # as searched, for the Qt app too
            self.s["identify"] = True
            self.message, self.error = "", False
            self.version += 1
            self._save(True)

    def _save(self, with_events: bool) -> None:
        """Saved as the Qt app saves it: analysis.json's "spikes", and the spikes found in
        analysis_spikes.npz (none: the file goes)."""
        folder = self._folder
        if folder is None:
            return
        try:
            with analysisfile.LOCK:                   # (the other parts of analysis.json stay as they are)
                analysisfile.save_part(folder, "spikes", dict(self.s))
                if with_events:
                    analysisfile.save_spikes(folder, self.events, self._stems)
        except OSError as e:
            self.message, self.error = f"Couldn't save the spikes: {e}", True
