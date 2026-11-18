"""The Stimulator's Python half: Simple Stimulus's settings, shared by the Stimulator tab and
the stimulator docked on Record (change one, the other follows), and sending them: once,
or every few seconds until Stop, raising the amplitude after each if asked, as the Qt
Simple Stimulus page does (simplestim.py). The stimulus itself is simplestimulus.py's.

Settings and presets are kept in ~/.newt/web.json (the Qt app keeps its own in its settings).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .. import simplestimulus as SS
from .. import stim

SETTINGS = Path.home() / ".newt" / "web.json"
INTS = ("count", "repeats")
BOOLS = ("ramp", "windows")


def _read_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Stimulator:
    def __init__(self, live):
        self.live, self.cfg = live, live.cfg
        self._lock = threading.RLock()
        saved = _read_settings()
        self.values = {**SS.DEFAULTS, **{k: v for k, v in saved.get("simple_stim", {}).items() if k in SS.DEFAULTS}}
        self.presets: list[dict] = list(saved.get("simple_stim_presets", []))
        self.log: list[str] = []                # what was sent, newest first
        self.sent = 0
        self.message, self.error = "", False
        self.repeating, self.rep_sent = False, 0
        self._timer: threading.Timer | None = None

    def _save(self) -> None:
        d = _read_settings()
        d["simple_stim"], d["simple_stim_presets"] = self.values, self.presets
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps(d, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _say(self, text: str, error: bool = False) -> None:
        self.message, self.error = text, error

    # -- settings ------------------------------------------------------------------------
    def set(self, changes: dict) -> None:
        with self._lock:
            for k, v in changes.items():
                if k not in SS.DEFAULTS:
                    continue
                if k == "mode":
                    self.values[k] = v if v in dict(SS.MODES) else "single"
                elif k in BOOLS:
                    self.values[k] = bool(v)
                elif k in INTS:
                    self.values[k] = max(0 if k == "repeats" else 1, int(float(v)))
                else:
                    self.values[k] = float(v)
            self._save()

    # -- sending -------------------------------------------------------------------------
    def fire(self) -> bool:
        """Send the stimulus once (starting acquiring first, if it isn't, as the Qt app does)."""
        with self._lock:
            out = self.live.output
            if out is None:
                self._say("No stimulator: it needs the simulated (or nidaqmx) backend.", True)
                return False
            if out.busy:
                self._say("The previous stimulus is still running.", True)
                return False
            s = SS.stimulus(self.values, self.cfg.stim_scale or 1.0)
            try:
                wave = s.waveform(self.cfg.stim_rate)
                stim.check_limit(wave, self.cfg.stim_max_v)
            except ValueError as e:
                self._say(str(e), True)
                return False
            if not self.live.backend.running:
                self.live.run(True)
            out.fire(wave, self.cfg.stim_rate)
            desc = SS.describe(self.values, self.cfg.stim_units or "V")
            self.sent += 1
            self.log.insert(0, f"#{self.sent}  {desc}  {time.strftime('%H:%M:%S')}")
            del self.log[200:]
            self.live.last_stim = desc
            self._say("")
            if self.repeating:
                self.rep_sent += 1
            if self.values["ramp"]:
                self._raise_amplitude()
            return True

    def _raise_amplitude(self) -> None:
        v, units = self.values, self.cfg.stim_units or "V"
        by, to = v["ramp_by"], v["ramp_to"]
        nxt = v["level"] + by
        if (by > 0 and nxt > to + 1e-9) or (by < 0 and nxt < to - 1e-9) or by == 0:
            if self.repeating:
                self.halt()
                self._say(f"Stopped after one: {v['level']:g} {units} is already Raise's limit ({to:g} {units})."
                          if self.rep_sent <= 1 else f"Reached {v['level']:g} {units}, Raise's limit: stopped.")
            return
        v["level"] = round(nxt, 6)
        self._save()

    def start_repeat(self) -> None:
        with self._lock:
            if self.repeating:
                return
            self.repeating, self.rep_sent = True, 0
            if not self.fire():
                self.repeating = False
                return
            if not self._all_sent() and self.repeating:      # Raise may have stopped it already
                self._next()

    def _next(self) -> None:
        self._timer = threading.Timer(max(0.05, float(self.values["every"])), self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        with self._lock:
            if not self.repeating:
                return
            if self.live.output is not None and self.live.output.busy:
                self._say("Skipped one: the previous stimulus was still running. Send them further apart.", True)
                return self._next()
            if not self.fire():
                return self.halt()
            if not self._all_sent():
                self._next()

    def _all_sent(self) -> bool:
        n = int(self.values["repeats"])
        if not self.repeating or not n or self.rep_sent < n:
            return False
        self.halt()
        self._say(f"Sent {n}: done.")
        return True

    def halt(self) -> None:
        with self._lock:
            self.repeating = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def stop(self) -> None:
        """Stop repeating, and the stimulus being sent."""
        self.halt()
        if self.live.output is not None:
            self.live.output.stop()

    # -- presets -------------------------------------------------------------------------
    def save_preset(self, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Give the preset a name.")
        with self._lock:
            self.presets = [p for p in self.presets if p["name"] != name] + [{"name": name, "values": dict(self.values)}]
            self._save()

    def load_preset(self, name: str) -> None:
        p = next((p for p in self.presets if p["name"] == name), None)
        if p is not None:
            self.set(p["values"])

    def delete_preset(self, name: str) -> None:
        with self._lock:
            self.presets = [p for p in self.presets if p["name"] != name]
            self._save()

    # -- what the pages show -------------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            out, cfg, v = self.live.output, self.cfg, self.values
            running = self.live.backend.running
            if out is None:
                status, ok = "No stimulator: it needs the simulated (or nidaqmx) backend.", False
            elif self.repeating:
                n = int(v["repeats"])
                status, ok = f"Sending every {v['every']:g} s · {f'{self.rep_sent} of {n}' if n else f'{self.rep_sent} so far'}…", True
            elif not running:
                status, ok = "Ready: sending starts acquiring", True
            else:
                status, ok = "Ready", True
            mon = next((c.name for c in cfg.channels if c.physical == cfg.stim_monitor), "")
            return {
                "values": v, "modes": SS.MODES, "fields": SS.FIELDS, "marked": SS.MARKED, "names": SS.NAMES,
                "units": cfg.stim_units or "V", "describe": SS.describe(v, cfg.stim_units or "V"),
                "log": self.log[:30], "sent": self.sent, "repeating": self.repeating, "busy": bool(out and out.busy),
                "status": status, "ok": ok, "message": self.message, "error": self.error,
                "output": cfg.stim_output, "monitor": cfg.stim_monitor, "monitor_name": mon,
                "limit": cfg.stim_max_v, "presets": [p["name"] for p in self.presets], "running": running,
            }
