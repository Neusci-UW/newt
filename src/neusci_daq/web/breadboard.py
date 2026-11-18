"""The Breadboard simulator's Python half (Simulators tab): the board, checked and solved
as on the Qt page (breadpage.py), and recorded by the simulated rig: a probe given an
input replaces that input's trace on Record (backends.SimulatedBackend._breadboard).

GET  /breadboard/state[?v=N]   JSON: the board, its preview, timing, inputs, keys (see state);
                               with ?v=N and the board still version N, only what changes
                               while recording (which MOSFETs are on, how bright the LEDs are)
POST /breadboard/cmd           JSON {"cmd": ...}, answered with the whole state (see command):
  add {kind, a, b}             a new part from hole a to hole b (b "" for a probe or ground)
  drop {kind, hole}            a part dropped on a hole (one hole, or across SPAN columns)
  edit {i, set: {field: v}}    change part i (legs to move it; value, closed, input, ...)
  delete {i} · reset · clear
  save {name, replace?}        <data>/<name>.board.toml; {dialog: true} asks where (in the window)
  open {name} | {text, name}   a .board.toml in the data folder, or a file's text from the page
  files                        the .board.toml files in the data folder (in "files")
  load {circuit}               a lesson's circuit, lessoncircuit.build's format: replaces the
                               board, its probes on the first free inputs. The Lessons tab's
                               "Build on breadboard" posts this, then opens
                               simulators.html#k=KEY&t=breadboard.

Board files are the Qt page's: a comment line, then the [[simulated.breadboard]] lines
of a config file (breadboard.to_toml_lines). The board itself is kept in ~/.newt/web.json
("breadboard", the same lines) so it's there next launch.
"""
from __future__ import annotations

import math
import re
import threading
import tomllib
from dataclasses import asdict, fields
from pathlib import Path

import numpy as np

from .. import breadboard as bb
from .. import breadlayout as bl
from .. import lessoncircuit
from .stimulator import read_settings, update_settings

HEADER = "# A NEWT breadboard circuit: Breadboard › Open board… to use it\n"
_TYPES = {f.name: f.type for f in fields(bb.Part)}
_EDITABLE = set(_TYPES) - {"kind"}


def _keys() -> dict:
    """The key (each leg's tag, name and job, and what the part does) for every kind of part,
    and the variants that change it: "vin:dc", "switch:mosfet", "switch:mosfet:p"."""
    out = {}
    for kind in bb.KINDS:
        variants = {kind: {}}
        if kind == "vin":
            variants["vin:dc"] = {"source": "dc"}
        if kind == "switch":
            variants["switch:mosfet"] = {"mode": "mosfet"}
            variants["switch:mosfet:p"] = {"mode": "mosfet", "channel": "p"}
        for name, change in variants.items():
            p = bb.new_part(kind, "c3", "" if kind in bb.ONE_HOLE else "c8")
            for k, v in change.items():
                setattr(p, k, v)
            text = bl.key_text(p).replace("the Stimulus tab sends", "the Stimulator sends")
            out[name] = {"legs": bl.key_legs(p), "text": text.replace("for Acquisition", "for Record")}
    return out


KEYS = _keys()


def _fmt_s(sec: float) -> str:
    for f, p in ((1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n")):
        if abs(sec) >= f * 0.999:
            return f"{sec / f:.3g} {p}s"
    return f"{sec:.3g} s"


class Breadboard:
    def __init__(self, live, stimulator, data: Path):
        self.live = live
        self.stimulator = stimulator
        self.data = Path(data)
        self.ask_path = None            # (name, fmt) -> path or None: the window's Save dialog
        self._lock = threading.Lock()
        self.version = 0
        self.message = ""
        self._rest = ({}, {})            # (MOSFETs on, LED currents) with the stimulator at 0 V
        self._extra: dict = {}           # this one answer's extras (a file list...)
        self._pv = (-1, None)            # (version, its preview)
        board = None
        text = read_settings().get("breadboard")
        if isinstance(text, str) and text.strip():
            try:
                board = bb.from_toml(tomllib.loads(text).get("simulated", {}))
            except (ValueError, TypeError, tomllib.TOMLDecodeError):
                board = None
        self._set(board or bb.default_board(), save=False)

    @property
    def board(self) -> bb.Board:
        return self.live.cfg.sim_breadboard

    # -- what the page shows -----------------------------------------------------------
    def inputs(self) -> list[dict]:
        cfg, out = self.live.cfg, []
        for c, (light, dark) in zip(cfg.channels, self.live.trace_colors):
            if c.physical != cfg.stim_monitor:
                out.append({"physical": c.physical, "name": c.name, "units": c.units, "scale": c.scale,
                            "light": light, "dark": dark})
        return out

    def live_now(self) -> dict:
        """Which MOSFETs are on and each LED's current: from the recording while it steps this
        board, otherwise at rest."""
        be = self.live.backend
        state = getattr(be, "_board", None)
        running = bool(getattr(be, "running", False))
        if running and state is not None and getattr(state, "board", None) is self.board:
            on, lit, now = state.closed_now(), state.lit_now(), True
        else:
            (on, lit), now = self._rest, False
        return {"on": {str(k): bool(v) for k, v in on.items()},
                "lit": {str(k): float(v) for k, v in lit.items()},
                "live": now, "running": running}

    def state(self, v=None) -> dict:
        with self._lock:
            if v is not None and str(v) == str(self.version):
                return {"version": self.version, **self.live_now()}
            board = self.board
            out = {"version": self.version, "parts": [asdict(p) for p in board.parts],
                   "message": self.message, "inputs": self.inputs(), "keys": KEYS,
                   "names": bb.NAMES, "led_vf": bb.LED_VF, "cols": bb.COLS,
                   "preview": self._preview_now(board), "timing": self._timing(board),
                   "folder": str(self.data), "dialog": self.ask_path is not None,
                   **self.live_now(), **self._extra}
            self._extra = {}
            return out

    # -- commands ------------------------------------------------------------------------
    def command(self, d: dict) -> None:
        cmd = d.get("cmd")
        with self._lock:
            parts = self.board.parts
            if cmd == "add":
                part = bl.placed_part(d["kind"], d["a"], d.get("b", ""))
                if part is None:
                    raise ValueError("No free hole beside it for the wiper: pick other ends.")
                self._apply(bb.Board([*map(_copy, parts), part]))
            elif cmd == "drop":
                part = bl.dropped_part(d["kind"], d["hole"])
                if part is None:
                    raise ValueError("No free hole beside it for the wiper: drop it somewhere else.")
                self._apply(bb.Board([*map(_copy, parts), part]))
            elif cmd == "edit":
                self._edit(int(d["i"]), d.get("set") or {})
            elif cmd == "delete":
                i = int(d["i"])
                self._apply(bb.Board([_copy(p) for k, p in enumerate(parts) if k != i]))
            elif cmd == "reset":
                self._apply(bb.default_board(), "")
            elif cmd == "clear":
                self._apply(bb.Board(), "")
            elif cmd == "save":
                self._save(d)
            elif cmd == "open":
                self._open(d)
            elif cmd == "files":
                self._extra["files"] = self._files()
            elif cmd == "load":
                self._load(d.get("circuit") or {})
            else:
                raise ValueError(f"Unknown command {cmd!r}")

    def _edit(self, i: int, changes: dict) -> None:
        board = bb.copy_board(self.board)
        if not 0 <= i < len(board.parts):
            raise ValueError("That part is gone.")
        part = board.parts[i]
        for k, v in changes.items():
            if k not in _EDITABLE:
                raise ValueError(f"A part has no {k!r}")
            t = _TYPES[k]
            setattr(part, k, bool(v) if t == "bool" else float(v) if t == "float" else str(v))
        if "color" in changes and "vf" not in changes and part.kind == "led":
            part.vf = bb.LED_VF.get(part.color, part.vf)          # a colour brings its usual forward voltage
        if part.kind == "switch" and part.mode == "mosfet" and not part.gate:
            part.gate = bl.gate_hole(part.a, part.b) or ""
            if not part.gate:
                raise ValueError("No free hole beside the switch for its gate: move it first.")
        legs = part.holes()
        if len(set(legs)) != len(legs):
            raise ValueError("Two legs can't share a hole.")
        self._apply(board)

    def _apply(self, board: bb.Board, message: str | None = "") -> None:
        """Check a changed board and, if it can be solved, make it the one recorded."""
        bb.validate(board)
        bb.BoardState(board, 1e-4)                  # can the circuit be solved? (ValueError if not)
        self._set(board)
        if message is not None:
            self.message = message

    def _set(self, board: bb.Board, save: bool = True) -> None:
        # A new object, so the acquisition thread picks up the change all at once.
        self.live.cfg.sim_breadboard = board
        self._rest = _at_rest(board)
        self.version += 1
        if save:
            update_settings(breadboard="\n".join(bb.to_toml_lines(board)).lstrip() + "\n")

    # -- files ---------------------------------------------------------------------------
    def _files(self) -> list[dict]:
        found = []
        if self.data.is_dir():
            for f in sorted([*self.data.glob("*.board.toml"), *self.data.glob("*/*.board.toml")]):
                found.append({"name": str(f.relative_to(self.data)), "mtime": f.stat().st_mtime})
        return found

    def _save(self, d: dict) -> None:
        text = HEADER + "\n".join(bb.to_toml_lines(self.board)) + "\n"
        if d.get("dialog") and self.ask_path is not None:
            got = self.ask_path("circuit.board.toml", "toml")
            if not got:
                return
            path = Path(got)
            if not path.name.endswith(".toml"):
                path = path.with_name(path.name + ".board.toml")
        else:
            name = re.sub(r"[/\\:]+", "-", str(d.get("name", "")).strip())
            name = re.sub(r"(\.board)?\.toml$", "", name)
            if not name:
                raise ValueError("Give the board a name.")
            path = self.data / f"{name}.board.toml"
            if path.exists() and not d.get("replace"):
                self._extra["exists"] = path.name
                return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as e:
            raise ValueError(f"Couldn't save the board: {e}") from None
        self.message = f"Saved {path.name}" + ("" if path.parent == self.data else f" in {path.parent}")
        self.version += 1

    def _open(self, d: dict) -> None:
        if "text" in d:
            text, name = str(d["text"]), str(d.get("name") or "the file")
        else:
            name = str(d.get("name", ""))
            path = (self.data / name).resolve()
            if self.data.resolve() not in path.parents or not path.is_file():
                raise ValueError(f"There's no {name} in the data folder.")
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as e:
                raise ValueError(f"Couldn't open {name}: {e}") from None
            name = path.name
        try:
            sim = tomllib.loads(text).get("simulated", {})
            if "breadboard" not in sim:
                raise ValueError("there's no breadboard circuit in it")
            board = bb.from_toml(sim)
        except (ValueError, TypeError, tomllib.TOMLDecodeError) as e:
            raise ValueError(f"Couldn't open {name}: {e}") from None
        self._apply(board, f"Opened {name}")

    def _load(self, spec: dict) -> None:
        """A lesson's circuit (lessoncircuit.build), its probes on the first free inputs."""
        try:
            board, notes = lessoncircuit.build(spec)
        except (KeyError, TypeError) as e:
            raise ValueError(f"The lesson's circuit is missing something: {e}") from None
        for probe, inp in zip(board.probes(), self.inputs()):
            probe.input = inp["physical"]
        title = spec.get("title", "the lesson's circuit")
        self._apply(board, f"Built from {title}. " + ". ".join(notes) + ("." if notes else ""))

    # -- the preview and timing ----------------------------------------------------------
    def _preview_now(self, board: bb.Board) -> dict | None:
        if self._pv[0] != self.version:
            self._pv = (self.version, self._preview(board))
        return self._pv[1]

    @staticmethod
    def _preview(board: bb.Board) -> dict | None:
        """What the probes would record for a 1 V, 100 ms pulse from the stimulator (bb.preview),
        every other sample."""
        try:
            t, stim, traces = bb.preview(board)
        except ValueError:
            return None
        r = lambda a: np.round(a[::2], 5).tolist()
        return {"t": r(t), "stim": r(stim) if any(p.kind == "vin" and p.source == "stim" for p in board.parts) else None,
                "traces": [r(y) for y in traces]}

    def _timing(self, board: bb.Board) -> dict:
        """The circuit's time constant, what to set to see it charge, and warnings when the
        Simple Stimulus pulse or Record's window would hide it (as the Qt page's Timing box)."""
        try:
            taus = bb.time_constants(board)
        except ValueError:
            return {"lines": [], "warnings": []}
        if not taus:
            return {"lines": ["No capacitor charges through a resistor here, so the probes follow the input at once."],
                    "warnings": []}
        tau = taus[0]
        rs = [p for p in board.parts if p.kind == "resistor"]
        cs = [p for p in board.parts if p.kind == "capacitor"]
        parts = ""
        if len(rs) == 1 and len(cs) == 1:
            parts = f" ({lessoncircuit.fmt(rs[0].value, 'Ω')} × {lessoncircuit.fmt(cs[0].value, 'F')})"
        t = _fmt_s
        lines = [f"Time constant <b>τ = {t(tau)}</b>{parts}" + (" (the slowest of several)" if len(taus) > 1 else ""),
                 f"The capacitor charges 63% in τ and almost fully in 5τ = {t(5 * tau)}. To see it, send a pulse "
                 f"of at least {t(5 * tau)} and set Record's window to about {t(10 * tau)}–{t(50 * tau)}."]
        warnings = []
        v = getattr(self.stimulator, "values", {}) or {}
        if any(p.kind == "vin" and p.source == "stim" for p in board.parts) and v.get("mode") in ("single", "pair", "train"):
            width = float(v.get("width", 0)) / 1000
            if 0 < width < 5 * tau:
                reach = 1 - math.exp(-width / tau)
                warnings.append(f"The Stimulator's pulse is {t(width)}, shorter than 5τ: the capacitor "
                                f"only reaches about {reach:.0%} of the input.")
        window = float(self.live.window_s)
        if window > 200 * tau:
            warnings.append(f"Record's window is {t(window)}, so a {t(5 * tau)} charge will look like a thin spike.")
        return {"lines": lines, "warnings": warnings}


def _copy(p: bb.Part) -> bb.Part:
    return bb.Part(**asdict(p))


def _at_rest(board: bb.Board) -> tuple[dict, dict]:
    """Which MOSFETs are on and each LED's current with the stimulator at 0 V, once the
    circuit has settled."""
    if not any(p.kind == "led" or (p.kind == "switch" and p.mode == "mosfet") for p in board.parts):
        return {}, {}
    try:
        state = bb.BoardState(board, 1e-4)
        state.run(np.zeros(2000), [])
        return state.closed_now(), state.lit_now()
    except ValueError:
        return {}, {}
