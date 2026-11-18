"""Record's setup on the HTML front end: the input device, the sampling rate and wiring, the
channels (on or off, name, input, units, scale and offset, color, line width, which one
records the stimulus) and the calculated channels, as the Qt app's channelsetup.py and
mediacontrol.py set them.

As in Qt: a change is refused while a trial is being recorded (colors aside: they're only
for showing); acquiring stops for a change of inputs or rate, and carries on after; and if
the session's trials were recorded with other channels, it asks first, then starts a new
session, since every trial in a session must match. "Save setup…" writes the setup as a
config file (config.toml's form, config.dumps) that either app starts from:
neusci-daq -c FILE, or python -m neusci_daq.web --config FILE.
"""
from __future__ import annotations

import dataclasses
import threading
import warnings
from pathlib import Path

from .. import audio, backends, colors, dsp, rigsetup, trialfile
from .. import config as cfgmod
from ..config import valid_inputs
from ..units import whole
from .stimulator import read_settings, update_settings

COMMANDS = ("setup_set", "setup_add", "setup_remove", "setup_stim", "setup_rate", "setup_wiring",
            "setup_device", "setup_devices", "setup_save", "calc_set", "calc_remove", "calc_check",
            "calc_preset", "yrange", "recolor")
FIELDS = ("name", "units", "scale", "offset", "physical", "enabled", "color", "width")
DEVICE_NAMES = {"simulated": "Simulated cells", "nidaqmx": "NI DAQ (USB-6003)",
                "multichannelio": "NI DAQ, software-timed (MultiChannelIO)"}


class Rig:
    def __init__(self, live):
        self.live, self.cfg = live, live.cfg
        self.rows = list(self.cfg.channels) + list(self.cfg.spare_channels)    # all, on and off, in order
        self.message, self.error = "", False
        self.ask_path = None                 # ask_path(name, "toml"): the window's Save dialog, if any
        self._devices: list[dict] | None = None
        self._inputs: tuple[str, list[str]] | None = None      # (audio device, its inputs)
        self._lock = threading.RLock()

    # -- commands from the page ---------------------------------------------------------
    def command(self, c: dict) -> dict:
        cmd, ok = c.get("cmd"), bool(c.get("confirmed"))
        with self._lock:
            if cmd == "setup_set":
                return self.set_field(int(c["i"]), str(c["what"]), c["value"], ok)
            if cmd == "setup_add":
                ch = rigsetup.new_channel(self.cfg, self.rows)
                if ch is None:
                    raise ValueError("All inputs are already in use.")
                return self.apply(self._copies() + [ch], confirmed=ok)
            if cmd == "setup_remove":
                return self.remove(int(c["i"]), ok)
            if cmd == "setup_stim":
                return self.set_stim(int(c["i"]), bool(c["on"]))
            if cmd == "setup_rate":
                rate = rigsetup.parse_rate(str(c["text"]))
                if rate is None:
                    raise ValueError(f"{c['text']!r} isn't a sampling rate. Type a number of Hz, e.g. 1000 or 20k.")
                return self.apply(rate=rate, confirmed=ok) if rate != self.cfg.sample_rate else {}
            if cmd == "setup_wiring":
                tc = str(c["wiring"]).upper()
                return self.apply(wiring=tc, confirmed=ok) if tc != self.cfg.terminal_config else {}
            if cmd == "setup_device":
                return self.set_device(str(c["backend"]), str(c.get("device", "")), ok)
            if cmd == "setup_devices":
                self._devices = None
                self._inputs = None
                return {}
            if cmd == "setup_save":
                return self.save()
            if cmd == "calc_set":
                return self.set_calc(c.get("k"), str(c.get("name", "")), str(c.get("formula", "")),
                                     str(c.get("unit", "")))
            if cmd == "calc_remove":
                calcs = list(self.cfg.calculations)
                gone = calcs.pop(int(c["k"]))
                self._calcs(calcs)
                self._say(f"Removed the calculated channel {gone.name}.")
                return {}
            if cmd == "calc_check":
                return {"calc_check": self._calc_problem(c.get("k"), str(c.get("formula", "")))}
            if cmd == "calc_preset":
                chans = self.cfg.channels
                name, formula, unit = dsp.preset(int(c["i"]), [ch.name for ch in chans],
                                                 {ch.name: ch.units for ch in chans})
                return {"calc_preset": {"name": name, "formula": formula, "unit": unit}}
            if cmd == "yrange":
                self.live.set_yrange(str(c["name"]), c.get("lo"), c.get("hi"))
                return {}
            if cmd == "recolor":
                return self.recolor(str(c["name"]), str(c["color"]))
        raise ValueError(f"Unknown command {cmd!r}")

    def _say(self, text: str, bad: bool = False) -> None:
        self.message, self.error = text, bad

    def _copies(self):
        return [dataclasses.replace(c) for c in self.rows]

    # -- channels --------------------------------------------------------------------------
    def set_field(self, i: int, what: str, value, confirmed: bool) -> dict:
        if what not in FIELDS or not 0 <= i < len(self.rows):
            raise ValueError(f"Can't set {what!r} of channel {i}.")
        ch = self.rows[i]
        if what == "color":
            return self.set_color(i, value)
        if what == "width":                     # only for showing: no restart, allowed while recording
            w = float(value)
            if not 0.5 <= w <= 20:
                raise ValueError("Line width must be between 0.5 and 20 px.")
            ch.width = w
            self.live._dirty = True
            return {}
        if what in ("scale", "offset"):
            value = float(value)
            if what == "scale" and value == 0:
                raise ValueError("A scale of 0 would show every sample as the offset: use a number other than 0.")
        elif what == "enabled":
            value = bool(value)
        else:
            value = str(value).strip()
            if what == "units" and not value:
                value = "V"
        if getattr(ch, what) == value:
            return {}
        was_stim = bool(self.cfg.stim_monitor) and ch.physical == self.cfg.stim_monitor
        rows = self._copies()
        setattr(rows[i], what, value)
        out = self.apply(rows, confirmed=confirmed)
        if not out and was_stim and what == "physical":
            self.cfg.stim_monitor = value             # the stimulus channel moved input
        return out

    def remove(self, i: int, confirmed: bool) -> dict:
        ch = self.rows[i]
        was_stim = bool(self.cfg.stim_monitor) and ch.physical == self.cfg.stim_monitor
        out = self.apply([c for j, c in enumerate(self._copies()) if j != i], confirmed=confirmed)
        if not out:
            if was_stim:
                self.cfg.stim_monitor = ""
            self._say(f"Removed {ch.name}.")
        return out

    def set_stim(self, i: int, on: bool) -> dict:
        """A channel's "Records the stimulus" tick: the input wired from the stimulator's output."""
        phys = self.rows[i].physical
        if on:
            self.cfg.stim_monitor = phys
        elif phys == self.cfg.stim_monitor:
            self.cfg.stim_monitor = ""
        name = next((c.name for c in self.rows if c.physical == self.cfg.stim_monitor), "")
        self._say(f"Recording the stimulus on {self.cfg.stim_monitor} ({name})." if self.cfg.stim_monitor
                  else "Not recording the stimulus.")
        self.live._dirty = True
        return {}

    def set_color(self, i: int, color: str) -> dict:
        """A trace color: only for showing, so it's allowed while recording, and the trial
        being recorded follows. Offers to recolor the session's trials that show it otherwise."""
        color = colors.normalize_color(str(color))
        ch = self.rows[i]
        ch.color = color
        self.live._dirty = True
        rec = self.live.recording
        if rec is None:
            return {}
        if rec.rec.recording:
            rec.rec.set_colors([c.color for c in self.cfg.channels])
        older = self._older(ch.name, color)
        self._say(f"{ch.name}: color {colors.color_label(color)}.")
        return {"recolor": {"name": ch.name, "color": color, "n": len(older)}} if older else {}

    def _older(self, name: str, color: str) -> list[Path]:
        """The session's recorded trials that show channel `name` in another color."""
        rec = self.live.recording
        current = rec.rec.path if rec.rec.recording else None
        out = []
        for b in list(rec.blocks):
            if b == current:
                continue
            try:
                info = trialfile.block_info(b)
            except Exception:
                continue
            names = info["channel_names"]
            if name in names and info["colors"][names.index(name)] != color:
                out.append(b)
        return out

    def recolor(self, name: str, color: str) -> dict:
        older = self._older(name, colors.normalize_color(color))
        failed = trialfile.recolor_blocks(older, {name: colors.normalize_color(color)})
        if failed:
            self._say(f"Couldn't update the color in {len(failed)} trial(s) (read-only or in use?): "
                      f"{', '.join(f.name for f in failed)}", True)
        else:
            self._say(f"{name}: color updated in {len(older)} recorded trial(s).")
        if older:
            self.live.recording._saved(older[0].parent)          # Analysis shows it
        return {}

    # -- applying a setup (channelsetup.apply_setup) ----------------------------------------
    def apply(self, rows=None, rate: float | None = None, wiring: str | None = None,
              confirmed: bool = False) -> dict:
        """Use a new channel setup, rate or wiring. {} once it's done; {"confirm": ...} if the
        session's trials were recorded with another setup and it isn't confirmed yet."""
        live, cfg, rec = self.live, self.cfg, self.live.recording
        what = "sampling rate" if rate is not None else "input wiring" if wiring is not None else "channels"
        if rec is not None and rec.busy:
            raise ValueError(f"Stop recording before changing the {what}.")
        rows = self._copies() if rows is None else rows
        changes = {}
        if rate is not None:
            changes.update(sample_rate=float(rate), chunk_size=cfgmod.auto_chunk_size(rate))
        if wiring is not None:
            if wiring not in ("RSE", "DIFF"):
                raise ValueError("Wiring is RSE or DIFF.")
            changes["terminal_config"] = wiring
            if wiring == "DIFF" and rigsetup.diff_problem(rows):
                raise ValueError(rigsetup.diff_problem(rows))
        trial = cfg.with_channels(rows, **changes)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cfgmod.validate(trial)
        settings_changed = (trial.sample_rate != cfg.sample_rate or trial.terminal_config != cfg.terminal_config)
        hardware_changed = settings_changed or (
            [(c.physical, c.enabled) for c in trial.channels] != [(c.physical, c.enabled) for c in cfg.channels])
        if rec is not None and rec.blocks and (
                settings_changed or rigsetup.layout(trial.channels) != rigsetup.layout(cfg.channels)):
            if not confirmed:
                return {"confirm": f"This session's trials were recorded with the old {what}. All trials in "
                                   f"a session must match, so changing the {what} starts a new session."}
            rec.new_session()
        running = live.backend.running
        if running and hardware_changed:
            live.run(False)
        self.rows = rows
        cfg.channels, cfg.spare_channels = trial.channels, trial.spare_channels
        cfg.sample_rate, cfg.chunk_size = trial.sample_rate, trial.chunk_size
        cfg.terminal_config = trial.terminal_config
        live.relayout()
        if running and hardware_changed:
            live.run(True)
        if what == "channels":
            self._say("Channels: " + ", ".join(f"{c.name} = {c.physical}" for c in cfg.channels) + ".")
        elif what == "sampling rate":
            self._say(f"Sampling rate: {whole(cfg.sample_rate)} per channel.")
        else:
            self._say(f"Input wiring: {cfg.terminal_config}.")
        return {}

    # -- the input device (mediacontrol.switch_input) -------------------------------------
    def set_device(self, backend: str, device: str, confirmed: bool) -> dict:
        """Use another input device. Acquiring stops (ending any trial), the input changes,
        and if it was acquiring it carries on from the new device: with Record on, in a new
        trial. Its channels come with it (an audio input's own; back from one, the DAQ's)."""
        live, cfg, rec = self.live, self.cfg, self.live.recording
        if backend not in ("simulated", "nidaqmx", "multichannelio", "audio"):
            raise ValueError(f"Unknown input device {backend!r}")
        if backend == cfg.backend and (backend != "audio" or device == cfg.audio_device):
            return {}
        if rec is not None and rec.rec.recording and not confirmed:
            return {"confirm": "A trial is being recorded: it ends here, and the next one starts on the new input."}
        saved = read_settings().get("daq_setup") or {} if cfg.backend == "audio" else {}
        trial = rigsetup.for_input(cfg, backend, device, saved)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cfgmod.validate(trial)
        setup = lambda c: (c.channels, c.spare_channels, c.sample_rate)
        new_channels = setup(trial) != setup(cfg)
        if new_channels and rec is not None and rec.blocks and not confirmed and (
                trial.sample_rate != cfg.sample_rate or rigsetup.layout(trial.channels) != rigsetup.layout(cfg.channels)):
            return {"confirm": "This session's trials were recorded with the other input's channels. All trials "
                               "in a session must match, so this input starts a new session."}
        if backend == "simulated" and cfg.backend != "simulated":    # the real device, to come back to
            update_settings(live_input={"backend": cfg.backend, "device": cfg.audio_device})
        if cfg.backend != "audio":
            update_settings(daq_setup=rigsetup.daq_setup(cfg))
        running = live.backend.running
        if running:
            live.run(False)                 # ends any trial and stimulus; Record stays on for the next
        before = (cfg.backend, cfg.audio_device)
        live.use_backend(backend, device)
        if new_channels:
            if rec is not None and rec.blocks and (trial.sample_rate != cfg.sample_rate or
                                                   rigsetup.layout(trial.channels) != rigsetup.layout(cfg.channels)):
                rec.new_session()
            self.rows = trial.channels + trial.spare_channels
            cfg.channels, cfg.spare_channels = trial.channels, trial.spare_channels
            cfg.sample_rate, cfg.chunk_size = trial.sample_rate, trial.chunk_size
            cfg.stim_monitor = trial.stim_monitor
            live.relayout()
        self._inputs = None
        name = self.input_name()
        if running:
            try:
                live.run(True)
            except Exception as e:
                why = (str(e).strip().splitlines() or [type(e).__name__])[0]
                if not new_channels:          # back to the one that worked
                    live.use_backend(*before)
                    live.run(True)
                    self._say(f"Couldn't start {name} ({why}): still on {self.input_name()}.", True)
                else:
                    self._say(f"Couldn't start {name}: {why}", True)
                return {}
            self._say(f"Input: {name}.")
            return {}
        found = backends.daq_devices() if backend == "nidaqmx" else None
        if found is not None and cfg.device not in found:
            self._say(f"Input: {name}. No NI DAQ ({cfg.device}) found: plug it in before Run.", True)
        else:
            self._say(f"Input: {name}. Press Run." + (" macOS asks once to allow the microphone."
                                                      if backend == "audio" else ""))
        return {}

    def input_name(self) -> str:
        return {"nidaqmx": "the NI DAQ", "simulated": "Simulated cells",
                "multichannelio": "the NI DAQ (MultiChannelIO)"}.get(
            self.cfg.backend, self.cfg.audio_device or "the audio input")

    def devices(self) -> list[dict]:
        """The input devices to pick from (looked for again when the page asks)."""
        if self._devices is None:
            backends.preload_daq()               # (once; on a thread of its own)
            out = [{"backend": b, "device": "", "label": label} for b, label in DEVICE_NAMES.items()]
            if not audio.available():
                out += [{"backend": "audio", "device": d["name"], "label": f"{d['name']} ({d['channels']} in)"}
                        for d in audio.devices("input")]
            self._devices = out
        found = backends.daq_devices()
        for d in self._devices:
            if d["backend"] in ("nidaqmx", "multichannelio"):
                d["note"] = ("looking…" if found is None else "none found" if not found
                             else ", ".join(found))
        return self._devices

    def _valid_inputs(self) -> list[str]:
        if self.cfg.backend != "audio":
            return valid_inputs(self.cfg)
        if self._inputs is None or self._inputs[0] != self.cfg.audio_device:
            self._inputs = (self.cfg.audio_device, valid_inputs(self.cfg))
        return self._inputs[1]

    # -- calculated channels (dsp.py) ------------------------------------------------------
    def _calc_problem(self, k, formula: str) -> str:
        calcs = list(self.cfg.calculations)
        above = calcs if k is None else calcs[:int(k)]
        chain = dsp.Chain(above + [dsp.Calculation("\0check", formula)], [c.name for c in self.cfg.channels],
                          self.cfg.sample_rate)
        return chain.problems[-1] if formula.strip() else "Type a formula, e.g. bandpass(Ch1, 300, 3000)."

    def set_calc(self, k, name: str, formula: str, unit: str) -> dict:
        name, formula, unit = name.strip(), formula.strip(), unit.strip()
        calcs = list(self.cfg.calculations)
        if not name or not formula:
            raise ValueError("A calculated channel needs a name and a formula.")
        taken = {c.name for c in self.cfg.channels} | {c.name for j, c in enumerate(calcs) if k is None or j != int(k)}
        if name in taken:
            raise ValueError(f"{name!r} is already a channel's name: give the calculation another.")
        why = self._calc_problem(k, formula)
        if why:
            raise ValueError(f"Can't calculate it: {why}")
        new = dsp.Calculation(name, formula, unit)
        if k is None:
            calcs.append(new)
        else:
            calcs[int(k)] = new
        self._calcs(calcs)
        self._say(f"{name} = {formula}.")
        return {}

    def _calcs(self, calcs: list) -> None:
        self.cfg.calculations = calcs
        self.live.relayout()

    # -- saving ------------------------------------------------------------------------------
    def save(self) -> dict:
        """The setup as a config file. In a window, saved where its Save dialog says; in a
        browser, beside the config it started from (or the data folder), never over a file."""
        cp = self.live.config_path
        default = cp.with_name(cp.stem + "_setup.toml") if cp else Path.cwd() / "channel_setup.toml"
        if self.ask_path is not None:
            got = self.ask_path(default.name, "toml")
            if not got:
                return {}
            path = Path(got)
        else:
            path, n = default, 2
            while path.exists():
                path = default.with_name(f"{default.stem} ({n}).toml")
                n += 1
        cfgmod.save(self.cfg, path)
        self._say(f"Setup saved to {path}. Start with it: python -m neusci_daq.web --config \"{path}\" "
                  f"(or neusci-daq -c \"{path}\").")
        return {}

    # -- what the page shows -----------------------------------------------------------------
    def state(self) -> dict:
        cfg = self.cfg
        is_audio = cfg.backend == "audio"
        on = [c.name for c in cfg.channels]
        return {
            "backend": cfg.backend, "device": cfg.audio_device, "name": self.input_name(),
            "devices": self.devices(), "audio_why": audio.available(),
            "rate": cfg.sample_rate, "rates": list(audio.RATES if is_audio else cfgmod.RATE_PRESETS),
            "max_rate": None if is_audio else cfgmod.max_rate(len(cfg.channels)),
            "wiring": cfg.terminal_config, "wired": not is_audio, "inputs": self._valid_inputs(),
            "rows": [{"name": c.name, "physical": c.physical, "units": c.units, "scale": c.scale,
                      "offset": c.offset, "color": c.color, "width": c.width, "enabled": c.enabled,
                      "stim": bool(cfg.stim_monitor) and c.physical == cfg.stim_monitor} for c in self.rows],
            "calcs": [{"name": c.name, "formula": c.formula, "unit": c.unit, "problem": why}
                      for c, why in zip(self.live.chain.calcs, self.live.chain.problems)],
            "presets": [p[0] for p in dsp.PRESETS], "on": on, "palette": [c.lower() for c in colors.CHOICES],
            "config": str(self.live.config_path or ""),
            "message": self.message, "error": self.error,
        }
