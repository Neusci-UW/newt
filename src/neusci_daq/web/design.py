"""Stimulus Design's Python half (the Stimulator tab's second sub-tab), as the Qt page does
it (stimuluspage.py): a stimulus built from segments, sent as trials.

The stimulus is a stim.Stimulus of segments; each segment's level and duration can change
from trial to trial ("+0.5" adds 0.5 each trial, "0.5, 1, 2" lists one value per trial).
Trials: how many, each repeated, in order / interleaved / shuffled, one every few
seconds. Planning and checking them is protocols.py's (the trial number is a, as on the
Qt page); sending them, one at a time on a thread of their own, is here: Run, Pause, Stop.

The picture is made here too: each trial's waveform as a min/max envelope (two points per
screen column), the segments' places, and the picked one's numbers. It is sent to the
page only when it changes (state's "pic_v").

The design being edited and the saved ones are kept in ~/.newt/web.json. Saved designs
also show under Simple Stimulus's Lab presets.
"""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from .. import protocols as T
from .. import stim
from .. import stimdesign as D
from .stimulator import read_settings, update_settings

ORDERS = (("blocked", "In order"), ("interleaved", "Interleaved"), ("random", "Shuffled"))
PIC_COLS = 700                 # columns for the trial shown (the page is about this wide)
FADED_COLS = 350               # for the other trials
EDITS = {"set", "per_trial", "points", "add", "move", "remove", "trials", "offset", "load"}


def _envelope(w: np.ndarray, rate: float, cols: int) -> tuple[list, list]:
    """(times in ms, values): every sample, or each column's min and max (in the order they
    come, so edges keep their direction) when there are more than two per column."""
    n = len(w)
    if n <= 2 * cols:
        x = np.arange(n) * (1000.0 / rate)
        return np.round(x, 4).tolist(), np.round(w, 6).tolist()
    edges = np.linspace(0, n, cols + 1).astype(int)
    a, b = edges[:-1], edges[1:]
    lo, hi = np.minimum.reduceat(w, a), np.maximum.reduceat(w, a)
    up = w[a] <= w[b - 1]
    y = np.empty(2 * cols)
    y[0::2], y[1::2] = np.where(up, lo, hi), np.where(up, hi, lo)
    xm = np.repeat((a + b) / 2 * (1000.0 / rate), 2)
    return np.round(xm, 4).tolist(), np.round(y, 6).tolist()


def _ms_step(v: float) -> float:
    v = abs(v)
    return 0.1 if v < 2 else 1.0 if v < 20 else 5.0 if v < 200 else 50.0


class Design:
    def __init__(self, live, stimulator):
        self.live, self.cfg, self.stimulator = live, live.cfg, stimulator
        stimulator.design = self
        self._lock = threading.RLock()
        saved = read_settings()
        self.designs: list[dict] = [d for d in saved.get("designs", []) if isinstance(d, dict) and d.get("name")]
        self.stim, self.n, self.repeats, self.order, self.gap, self.name = stim.Stimulus(), 1, 1, "interleaved", 2.0, ""
        cur = saved.get("design")
        if isinstance(cur, dict):
            try:
                self._take(cur)
            except (ValueError, TypeError, KeyError):
                pass
        self.sel, self.cur = min(1, len(self.stim.parts) - 1), 0
        self.log: list[str] = []
        self.sent = 0
        self.message, self.error = "", False
        # the run
        self.running, self.paused = False, False
        self.plan: list[T.Trial] = []
        self.next_i, self._next_fire, self._token = 0, 0.0, None
        self._wake = threading.Event()
        # what's made from the design, remade when it changes
        self._dirty = True
        self._pic: dict = {}
        self._pic_v = 0
        self._chk: T.Check | None = None
        self.problem = ""

    # -- keeping it ----------------------------------------------------------------------
    def _as_dict(self) -> dict:
        return {"name": self.name, "stimulus": self.stim.to_dict(), "n": self.n, "repeats": self.repeats,
                "order": self.order, "gap": self.gap}

    def _take(self, d: dict) -> None:
        s = stim.Stimulus.from_dict(d["stimulus"])
        if not s.parts:
            raise ValueError("That design has no segments.")
        self.stim = s
        self.n = max(1, min(T.MAX_VALUES, int(d.get("n", 1))))
        self.repeats = max(1, int(d.get("repeats", 1)))
        self.order = d.get("order") if d.get("order") in dict(ORDERS) else "interleaved"
        self.gap = max(0.1, float(d.get("gap", 2.0)))
        self.name = str(d.get("name", ""))

    def _changed(self) -> None:
        self._dirty = True
        update_settings(design=self._as_dict())

    def names(self) -> list[str]:
        """The saved designs' names (read without the lock: Simple Stimulus asks)."""
        return [d["name"] for d in self.designs]

    # -- units ---------------------------------------------------------------------------
    @property
    def units(self) -> str:
        return self.cfg.stim_units or "V"

    @property
    def k(self) -> float:
        return self.cfg.stim_scale or 1.0

    def _protocol(self) -> T.Protocol:
        return T.Protocol(name=self.name or "Stimulus Design", a=T.trials_variable(self.n), repeats=self.repeats,
                          order=self.order, gap_s=self.gap)

    # -- commands from the page ----------------------------------------------------------
    def command(self, d: dict) -> None:
        cmd = d.get("cmd")
        with self._lock:
            if self.running and cmd in EDITS:
                raise ValueError("Stop the trials first, then change the design.")
            self.message, self.error = "", False
            parts = [dict(p) for p in self.stim.parts]
            sel = max(0, min(self.sel, len(parts) - 1))
            if cmd == "pick":
                self.sel = max(0, min(int(d["i"]), len(parts) - 1))
            elif cmd == "show":
                self.cur = max(0, min(int(d["trial"]) - 1, self.n - 1))
                self._dirty = True
            elif cmd == "set":
                self._set(parts, sel, d["key"], d["value"])
            elif cmd == "per_trial":
                self._per_trial(parts, sel, d["key"], str(d.get("text", "")))
            elif cmd == "points":
                pts = [[float(a), float(b) / self.k] for a, b in d["points"]]
                if len(pts) < 2:
                    raise ValueError("Custom points need at least two points.")
                if any(b <= a for (a, _), (b, _) in zip(pts, pts[1:])):
                    raise ValueError("Each point's time must come after the one before.")
                parts[sel]["points"], parts[sel]["dur"] = pts, max(a for a, _ in pts)
                self._push(parts)
            elif cmd == "add":
                _, kind, extra, _ = D.KINDS[int(d["i"])]
                at = min(sel + 1, len(parts))
                parts.insert(at, {**stim.new_part(kind), **extra})
                self.sel = at
                self._push(parts)
            elif cmd == "move":
                j = sel + int(d["step"])
                if 0 <= j < len(parts):
                    parts[sel], parts[j] = parts[j], parts[sel]
                    self.sel = j
                    self._push(parts)
            elif cmd == "remove":
                if len(parts) > 1:
                    del parts[sel]
                    self.sel = min(sel, len(parts) - 1)
                    self._push(parts)
            elif cmd == "trials":
                self._trials(d)
            elif cmd == "offset":
                self.stim = replace(self.stim, offset_v=float(d["value"]) / self.k)
                self._changed()
            elif cmd == "run":
                self.run()
            elif cmd == "pause":
                self.pause(bool(d.get("on", not self.paused)))
            elif cmd == "stop":
                self.stop("Stopped")
            elif cmd == "once":
                self.once()
            elif cmd == "save":
                self.save(str(d.get("name", "")))
            elif cmd == "load":
                self.load(str(d.get("name", "")))
            elif cmd == "delete":
                name = str(d.get("name", ""))
                self.designs = [x for x in self.designs if x["name"] != name]
                update_settings(designs=self.designs)
            else:
                raise ValueError(f"Unknown command {cmd!r}")

    def _push(self, parts: list[dict]) -> None:
        self.stim = replace(self.stim, shape="segments", parts=parts)
        self._changed()

    def _set(self, parts: list[dict], i: int, key: str, value) -> None:
        p = parts[i]
        what = next((w for k_, _, w, _ in D.FIELDS.get(p.get("kind"), []) if k_ == key), None)
        if key in ("biphasic", "log"):
            p[key] = bool(value)
        elif key == "seed":
            p[key] = 7 if value else 0
        elif key == "join":
            p[key] = "step" if value == "step" else "line"
        elif what == "n":
            p[key] = max(1, min(1000, int(round(float(value)))))
        elif what == "level":
            p[key] = float(value) / self.k
        elif what in ("ms", "Hz", "x"):
            v = float(value)
            if what != "x" and v < 0:
                raise ValueError("Times and frequencies can't be below 0.")
            p[key] = v
        else:
            raise ValueError(f"{stim.letter(i)} has no setting {key!r}.")
        self._push(parts)

    def _per_trial(self, parts: list[dict], i: int, key: str, text: str) -> None:
        if key not in ("d_level", "d_dur"):
            raise ValueError(f"No per-trial box {key!r}.")
        try:
            got = stim.parse_per_trial(text)
        except ValueError as e:
            raise ValueError(f"{stim.letter(i)}: {e}") from None
        what = next((w for _, _, w, dk in D.FIELDS.get(parts[i].get("kind"), []) if dk == key), "")
        k = self.k if what == "level" else 1.0
        parts[i][key] = stim.format_per_trial(got, 1.0 / k)
        if got and got[0] == "list" and self.n < len(got[1]):      # one trial per value, if there were fewer
            self.n = min(T.MAX_VALUES, len(got[1]))
        self._push(parts)

    def _trials(self, d: dict) -> None:
        if "n" in d:
            self.n = max(1, min(T.MAX_VALUES, int(round(float(d["n"])))))
            self.cur = min(self.cur, self.n - 1)
        if "repeats" in d:
            self.repeats = max(1, min(100, int(round(float(d["repeats"])))))
        if "order" in d and d["order"] in dict(ORDERS):
            self.order = d["order"]
        if "gap" in d:
            self.gap = max(0.1, min(3600.0, float(d["gap"])))
        self._changed()

    # -- saving --------------------------------------------------------------------------
    def save(self, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Give the design a name.")
        with self._lock:
            self.name = name
            self.designs = [x for x in self.designs if x["name"] != name] + [self._as_dict()]
            update_settings(designs=self.designs, design=self._as_dict())
            self.message = f"Saved “{name}”: it's also under Simple Stimulus's Lab presets."

    def load(self, name: str) -> None:
        with self._lock:
            d = next((x for x in self.designs if x["name"] == name), None)
            if d is None:
                raise ValueError(f"No saved design “{name}”.")
            if self.running:
                raise ValueError("Stop the trials first.")
            self._take(d)
            self.sel, self.cur = min(1, len(self.stim.parts) - 1), 0
            self._changed()

    # -- sending -------------------------------------------------------------------------
    def _output(self):
        out = self.live.output
        if out is None:
            raise ValueError("No stimulator: it needs the simulated (or nidaqmx) backend.")
        return out

    def _send(self, wave: np.ndarray, desc: str) -> None:
        """Out now (acquiring first, if it isn't, as the Qt app does), and logged."""
        out = self._output()
        if not self.live.backend.running:
            self.live.run(True)
        out.fire(wave, self.cfg.stim_rate)
        self.sent += 1
        self.log.insert(0, f"#{self.sent}  {desc}  {time.strftime('%H:%M:%S')}")
        del self.log[200:]
        self.live.last_stim = desc

    def once(self) -> None:
        """The trial the picture shows, once."""
        with self._lock:
            out = self._output()
            if self.running:
                raise ValueError("The trials are running.")
            if self.stimulator.repeating:
                raise ValueError("Simple Stimulus is repeating: stop it there first.")
            if out.busy:
                raise ValueError("The previous stimulus is still running.")
            st = self.stim.at_trial(self.cur)
            wave = st.waveform(self.cfg.stim_rate)
            stim.check_limit(wave, self.cfg.stim_max_v)
            self._send(wave, f"Trial {self.cur + 1}: " + st.describe(self.units, self.k))

    def run(self) -> None:
        with self._lock:
            if self.running:
                return
            out = self._output()
            c = self._check()
            if not c.ok:
                raise ValueError(c.errors[0])
            if c.n_trials == 1:                       # one stimulus: just send it
                self.cur = 0
                return self.once()
            if out.busy:
                raise ValueError("A stimulus is still being sent; wait for it to finish.")
            self.stimulator.halt()                    # Simple Stimulus's repeating, if it was
            if not self.live.backend.running:
                self.live.run(True)
            self.plan = T.sequence(self._protocol(), c.a_vals, None)
            self._run_p, self._run_s = self._protocol(), self.stim
            self.running, self.paused, self.next_i = True, False, 0
            self._next_fire = time.monotonic() + 0.2
            self._token = token = object()
            self.message = f"Running {len(self.plan)} stimuli"
            threading.Thread(target=self._loop, args=(token,), daemon=True, name="web-design-run").start()

    def _loop(self, token) -> None:
        """Send each trial when its time comes; stop at the end, on Stop, or if acquiring stops."""
        rate = self.cfg.stim_rate
        while True:
            wait = 0.02
            with self._lock:
                if self._token is not token or not self.running:
                    return
                out, now = self.live.output, time.monotonic()
                if not self.live.backend.running:
                    return self._end(f"Stopped after {self.next_i} of {len(self.plan)}: acquiring stopped", True)
                if self.next_i >= len(self.plan):
                    if not out.busy:
                        return self._end(f"Sent all {len(self.plan)}: done")
                elif not self.paused and now >= self._next_fire and not out.busy:
                    tr = self.plan[self.next_i]
                    try:
                        t = T.make_trial(self._run_s, self._run_p, tr.a, None, rate)
                        stim.check_limit(t.wave, self.cfg.stim_max_v)
                        desc = (f"Stimulus {tr.n}/{len(self.plan)}, trial {tr.a:g}: "
                                + t.stimulus.describe(self.units, self.k))
                        self._send(t.wave, desc)
                    except (ValueError, RuntimeError) as e:
                        return self._end(f"Stopped at stimulus {tr.n}: {e}", True)
                    self.cur = int(tr.a) - 1
                    self._dirty = True
                    self.next_i += 1
                    self._next_fire = max(self._next_fire + self._run_p.gap_s, now + 0.05)
                elif not self.paused:
                    wait = min(0.05, max(0.002, self._next_fire - now))
            self._wake.wait(wait)
            self._wake.clear()

    def _end(self, why: str, bad: bool = False) -> None:
        self.running, self.paused, self._token = False, False, None
        self.message, self.error = why, bad
        self._wake.set()

    def pause(self, on: bool) -> None:
        with self._lock:
            if not self.running:
                return
            self.paused = on
            if not on:
                self._next_fire = time.monotonic()
            self.message = f"Paused: Resume carries on with stimulus {self.next_i + 1}" if on else "Resumed"
            self._wake.set()

    def stop(self, why: str = "Stopped") -> None:
        """Stop the trials, and the stimulus being sent."""
        with self._lock:
            was = self.running
            if was:
                self._end(f"{why} after {self.next_i} of {len(self.plan)}")
        out = self.live.output
        if out is not None and (was or out.busy):
            out.stop()

    # -- what the page shows -------------------------------------------------------------
    def _check(self) -> T.Check:
        if self._dirty or self._chk is None:
            self._remake()
        return self._chk

    def _remake(self) -> None:
        """The picture and the check, after a change."""
        s, rate, n = self.stim, self.cfg.stim_rate, self.n
        cur = min(self.cur, n - 1)
        order = [i for i in range(n) if i != cur][:D.MAX_FADED] + [cur]
        waves, problem = [], ""
        for t in order:
            try:
                waves.append((t, s.at_trial(t).waveform(rate)))
            except (ValueError, OSError) as e:
                if t == cur:
                    problem = f"Trial {t + 1}: {e}"
        try:
            spans = D.segment_spans(s.at_trial(cur), rate)
        except ValueError:
            spans = D.segment_spans(s, rate)
        total = max([len(w) for _, w in waves] + [spans[-1][1] if spans else 1, 2])
        lo = min([0.0] + [float(w.min()) for _, w in waves if len(w)])
        hi = max([0.0] + [float(w.max()) for _, w in waves if len(w)])
        traces = []
        for t, w in waves:
            cols = max(2, round((PIC_COLS if t == cur else FADED_COLS) * len(w) / total))
            x, y = _envelope(w, rate, cols)
            traces.append({"t": t, "x": x, "y": y, "rgb": D.trial_rgb(t / (n - 1)) if n > 1 else None})
        ms = 1000.0 / rate
        self._pic = {"traces": traces, "spans": [[a * ms, b * ms] for a, b in spans], "total_ms": (total - 1) * ms,
                     "lo": lo, "hi": hi, "n": n, "cur": cur,
                     "names": [D.seg_name(p) for p in s.parts], "letters": [stim.letter(i) for i in range(len(s.parts))],
                     "stops": [[f, list(c)] for f, c in D.TRIAL_STOPS]}
        self._pic_v += 1
        self.problem = problem or D.trials_note(s, n)
        self._chk = T.check(self._protocol(), s, rate, self.cfg.stim_max_v)
        self._dirty = False

    def _segment(self) -> dict:
        """The picked segment, as the editor shows it (levels in the stimulator's units)."""
        parts = self.stim.parts
        i = max(0, min(self.sel, len(parts) - 1))
        p, k, u = parts[i], self.k, self.units
        kind = p.get("kind", "step")
        fields = []
        for key, label, what, dkey in D.FIELDS.get(kind, []):
            v = float(p.get(key, p.get("level", 0.0) if key == "level2" else 0.0) or 0.0)
            kk = k if what == "level" else 1.0
            dtext = ""
            if dkey:
                try:
                    dtext = stim.format_per_trial(stim.parse_per_trial(p.get(dkey, "")), kk)
                except ValueError:
                    dtext = p.get(dkey, "")
            fields.append({"key": key, "label": label, "what": what, "main": key != "offset",
                           "unit": {"level": u, "ms": "ms", "Hz": "Hz", "x": "×", "n": ""}[what],
                           "value": round(v * kk, 9) if what != "n" else int(v),
                           "step": {"level": 0.1, "Hz": 1.0 if v < 20 else 5.0, "x": 0.1, "n": 1}.get(what) or _ms_step(v),
                           "min": 0 if what in ("ms", "Hz") else (1 if what == "n" else None),
                           "dkey": dkey, "dtext": dtext})
        extra = {}
        if kind == "pulse":
            extra["biphasic"] = bool(p.get("biphasic"))
        elif kind == "chirp":
            extra["log"] = bool(p.get("log"))
        elif kind == "noise":
            extra["same"] = int(p.get("seed", 0)) != 0
        elif kind == "points":
            extra["points"] = [[float(a), round(float(b) * k, 9)] for a, b in p.get("points") or []]
            extra["join"] = p.get("join", "line")
        elif kind == "file":
            extra["file"] = Path(p.get("file", "")).name or "(none)"
        return {"i": i, "letter": stim.letter(i), "name": D.seg_name(p), "kind": kind, "hint": D.HINTS.get(kind, ""),
                "fields": fields, "extra": extra, "n_parts": len(parts),
                "dims": " · ".join(D.dims(p, u, k))}

    def state(self, have=None) -> dict:
        with self._lock:
            c = self._check()
            out, cfg = self.live.output, self.cfg
            total = len(self.plan) if self.running else (c.n_trials if c.ok else 0)
            if out is None:
                lamp, ok = "No stimulator: it needs the simulated (or nidaqmx) backend.", False
            elif self.running:
                lamp, ok = f"Stimulus {min(self.next_i, total)} of {total}" + (" · paused" if self.paused else ""), True
                wait = self._next_fire - time.monotonic()
                if self.next_i < total and not self.paused and wait >= 1:
                    lamp += f" · next in {int(wait)} s"
            elif not c.ok:
                lamp, ok = c.errors[0], False
            else:
                d = c.duration_s
                about = f"about {d:.0f} s" if d < 120 else f"about {d / 60:.0f} min"
                lamp = (f"Ready · {total} stimuli · {about} · highest {c.peak_v * self.k:.3g} {self.units}"
                        if total > 1 else f"Ready · highest {c.peak_v * self.k:.3g} {self.units}")
                if not self.live.backend.running:
                    lamp += " · sending starts acquiring"
                ok = True
            mon = next((ch.name for ch in cfg.channels if ch.physical == cfg.stim_monitor), "")
            st = {
                "seg": self._segment(), "k": self.k,
                "n": self.n, "repeats": self.repeats, "order": self.order, "orders": ORDERS, "gap": self.gap,
                "cur": min(self.cur, self.n - 1), "summary": f"{len(self.stim.parts)} segments",
                "pic_problem": self.problem, "warnings": c.warnings[:2],
                "running": self.running, "paused": self.paused, "sent": self.next_i, "total": total,
                "lamp": lamp, "ok": ok, "busy": bool(out and out.busy), "message": self.message, "error": self.error,
                "log": self.log[:30], "units": self.units, "offset": round(self.stim.offset_v * self.k, 9),
                "name": self.name, "designs": self.names(), "output": cfg.stim_output, "monitor": cfg.stim_monitor,
                "monitor_name": mon, "limit": cfg.stim_max_v, "rate": cfg.stim_rate,
                "acquiring": self.live.backend.running, "pic_v": self._pic_v,
            }
            if str(have) != str(self._pic_v):
                st["picture"] = self._pic
            if have in (None, "", "null", "undefined"):          # the page's first look: the picker's kinds
                st["kinds"] = [{"label": lb, "hint": h, "pts": [[round(x, 1), round(y, 1)] for x, y in D.icon_points(kd, ex)]}
                               for lb, kd, ex, h in D.KINDS]
            return st
