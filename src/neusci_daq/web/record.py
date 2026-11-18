"""Record on the HTML front end: trials saved to a session folder the way the Qt app saves
them (recorder.py writes each one, block_001.h5, block_002.h5, ..., on its own thread), so
Analysis, here or in the Qt app, opens them.

As in Qt (liveview.py): Record is an on/off switch. Turned on while running, a trial starts
now; on while stopped, Run (or a stimulus that starts running) starts one. Stop ends the
trial and Record stays on for the next Run; turning it off ends it too. A trial that got no
samples isn't kept. Each stimulus sent while recording is saved with the trial and noted as
a comment ("Stim: Pulse 5 V"); comments typed while recording are saved at the newest
sample; the trial label is saved with each trial. A trial that sent two or more stimuli
(Repeat, Raise, Stimulus Design's trials) becomes a trial for each when it ends, the whole
one hidden (trialsplit), as Qt does. Or, with Simple Stimulus's "save just around each
stimulus" on, each stimulus sent becomes a trial of its own, Before it to After it, and
nothing in between is saved (stimwindows.py, on a thread of its own here).

A new session is a folder in data/unsaved/ named for when it started, as the Qt app makes
before it's saved; the next launch keeps adding to it, until New session. Or keep adding to
any session recorded with these same channels.

Not yet as in Qt: video, a bar on the live trace for each trial, and naming or saving the
session (the Qt app's File › Save does that).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from .. import recorder, session, trialsplit
from ..stimwindows import StimWindows
from .stimulator import read_settings, update_settings
from .trials import session_folders, session_name

COMMANDS = ("record", "comment", "label", "new_session", "add_to", "sessions")


class Recording:
    def __init__(self, live, stimulator, data: Path):
        self.live, self.cfg, self.stimulator = live, live.cfg, stimulator
        self.data = Path(data)
        self.rec = recorder.Recorder(live.backend)
        self.win = StimWindows(live)       # "save just around each stimulus"
        self.n_windows = 0                 # trials it saved this run
        self._token = None                 # its saving thread's, while it runs
        self.on = False                    # Record's switch
        self.folder: Path | None = None    # the session trials go to (made at the first)
        self.blocks: list[Path] = []       # its trials (not the whole ones that were split)
        self.comments: list[tuple[float, str]] = []    # the trial being recorded's (s, text)
        self.n_sent = 0                    # stimuli sent during it
        self.label = str(read_settings().get("trial_label", "") or "")
        self.message, self.error = "", False
        self.splitting = 0
        self.on_saved = None               # on_saved(folder): trials were added to a session
        self._lock = threading.RLock()
        live.recording = self
        self._reopen()

    def unsaved(self) -> Path:
        return self.data / "unsaved"

    def _reopen(self) -> None:
        """Carry on with the session last recorded into, if it's still there and fits these channels."""
        last = str(read_settings().get("last_live_session", "") or "")
        if not last:
            return
        p = Path(last)
        try:
            if p.resolve().parent != self.unsaved().resolve() or not session.is_session(p):
                return
            if session.append_check(p, self.cfg)[0]:
                self.message = f"Started a new session: the last one ({p.name}) was recorded with other channels."
                return
        except Exception:
            return
        self.folder, self.blocks = p, session.list_blocks(p)
        n = len(self.blocks)
        self.message = (f"Adding to your last session ({n} trial{'' if n == 1 else 's'}). "
                        "New session starts another.")

    # -- commands from the page -------------------------------------------------------
    def command(self, c: dict) -> dict:
        cmd = c.get("cmd")
        if cmd == "record":
            self.set_on(bool(c["on"]))
        elif cmd == "comment":
            self.comment(str(c["text"]))
        elif cmd == "label":
            self.set_label(str(c["text"]))
        elif cmd == "new_session":
            self.new_session()
        elif cmd == "add_to":
            self.add_to(Path(c["folder"]))
        elif cmd == "sessions":
            return {"sessions": [{"path": str(p), "name": session_name(p, self.data)} for p in session_folders(self.data)]}
        return {}

    def set_on(self, on: bool) -> None:
        with self._lock:
            self.on = on
            if on and self.live.backend.running and not self.busy:
                self._begin()
            elif not on:
                self._end()

    def comment(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            if not self.rec.recording:
                raise ValueError("Comments are saved with a trial: turn Record on first.")
            self._comment(text)

    def _comment(self, text: str) -> None:
        t = self.rec.add_comment(text)            # saved to the trial straight away
        self.comments.append((max(0.0, t - self.rec.block_start), text))

    def set_label(self, text: str) -> None:
        """The label saved with trials from now on, e.g. "Cell 1"; "" for none. A trial
        being recorded gets it too."""
        text = text.strip()
        with self._lock:
            if text == self.label:
                return
            self.label = text
            update_settings(trial_label=text)
            if self.rec.recording:
                self.rec.set_attr("label", text)

    def new_session(self) -> None:
        with self._lock:
            if self.busy:
                raise ValueError("Stop recording first.")
            self.folder, self.blocks = None, []
            update_settings(last_live_session="")
            self.message, self.error = "New session: your next trial starts it.", False

    def add_to(self, folder: Path) -> None:
        with self._lock:
            if self.busy:
                raise ValueError("Stop recording first.")
            problems, warnings = session.append_check(folder, self.cfg)
            if problems:
                raise ValueError(f"Can't add to {folder.name}: {problems[0]}")
            self.folder, self.blocks = folder, session.list_blocks(folder)
            in_unsaved = folder.resolve().parent == self.unsaved().resolve()
            update_settings(last_live_session=str(folder) if in_unsaved else "")
            self.message = " ".join([f"New trials go to {folder.name}.", *warnings])
            self.error = False

    # -- from Live and the stimulators -------------------------------------------------
    def run_started(self) -> None:
        """The stream just started: with Record on, a trial starts with it. Waits (up to 1 s)
        for its first sample, so a stimulus that started the run is in the trial, at its time;
        saving just around each stimulus, for its Before (the baseline) to be recorded first,
        as Qt does (at least 0.3 s, at most 3)."""
        with self._lock:
            if not self.on or self.busy:
                return
            self._begin()
            settle = None if self.rec.recording else max(0.3, float(self.stimulator.values.get("before", 100.0)) / 1000 + 0.1)
        if settle is not None:
            time.sleep(min(settle, 3.0))
            return
        end = time.monotonic() + 1.0
        while not self.rec.block_started and time.monotonic() < end and self.rec.recording:
            time.sleep(0.005)

    def run_stopping(self) -> None:
        with self._lock:
            self._end()

    def sent(self, desc: str, stimulus=None, wave=None) -> None:
        """A stimulus just went out: saved with the trial being recorded, and noted as a
        comment; or, saving just around each stimulus, its window kept once it's all in."""
        with self._lock:
            if self.win.running:
                if wave is not None:
                    self.win.sent(wave, self.cfg.stim_rate, desc, stimulus, None, (self.cfg.stim_output, ""))
                return
            if not self.rec.recording:
                return
            self._comment(f"Stim: {desc}" if not desc.startswith("Stimulus ") else desc)
            if stimulus is not None:
                self.rec.add_stimulus(stimulus.to_dict(), self.cfg.stim_rate, output=self.cfg.stim_output)
            self.n_sent += 1

    # -- trials ----------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        """Recording a trial, or saving just around each stimulus."""
        return self.rec.recording or self.win.running

    def _windows(self) -> bool:
        return bool(self.stimulator.values.get("windows"))

    def _begin(self) -> None:
        if self._windows():
            v = self.stimulator.values
            self.win.start(float(v.get("before", 100.0)), float(v.get("after", 500.0)))
            self.n_windows = 0
            self.message, self.error = (f"Saving just around each stimulus: {v['before']:g} ms before it to "
                                        f"{v['after']:g} ms after."), False
            self._token = token = object()
            threading.Thread(target=self._windows_loop, args=(token,), daemon=True, name="web-windows").start()
        else:
            self._start()

    def _end(self) -> None:
        if self.win.running:
            self._token = None
            for r in self.win.stop():               # the last ones, with what arrived
                self._write_window(r)
        if self.rec.recording:
            self._stop()

    def _windows_loop(self, token) -> None:
        """Every 50 ms, off any page's thread: save the windows whose After has all come in."""
        while True:
            time.sleep(0.05)
            with self._lock:
                if self._token is not token or not self.win.running:
                    return
                for r in self.win.tick():
                    self._write_window(r)

    def _write_window(self, r) -> None:
        try:
            path = self.win.write(r, self._folder, self.label)
        except OSError as e:
            self.message, self.error = f"Couldn't save the stimulus's trial: {e}", True
            return
        if path is None:
            return
        self.blocks.append(path)
        self.n_windows += 1
        n = self.n_windows
        self.message, self.error = (f"Saved {path.stem}, just around its stimulus "
                                    f"({n} trial{'' if n == 1 else 's'} this run)."), False
        self._saved(path.parent)

    def _folder(self) -> Path:
        """The session's folder, made now if there's none yet."""
        if self.folder is None:
            self.folder = self.unsaved() / datetime.now().strftime("session_%Y%m%d_%H%M%S")
            self.blocks = []
            update_settings(last_live_session=str(self.folder))
        self.folder.mkdir(parents=True, exist_ok=True)
        return self.folder

    def _start(self) -> None:
        path = self.rec.start(session.next_block_path(self._folder()))
        if self.label:
            self.rec.set_attr("label", self.label)
        self.blocks.append(path)
        self.comments, self.n_sent = [], 0
        self.message, self.error = "", False

    def _stop(self) -> None:
        rec = self.rec
        n = rec.samples_written
        secs = n / max(float(self.live.backend.actual_rate), 1e-9)
        rec.stop()
        path = rec.path
        if n == 0:                                 # no samples came: not a trial
            if path in self.blocks:
                self.blocks.remove(path)
            try:
                path.unlink()
            except OSError:
                pass
            self.message, self.error = "No data came in, so no trial was kept.", True
            return
        sent = f", {self.n_sent} stimuli" if self.n_sent > 1 else ", 1 stimulus" if self.n_sent else ""
        self.message, self.error = f"Saved {path.stem} ({secs:.1f} s{sent}).", False
        if self.n_sent > 1:          # Repeat, Raise, Stimulus Design's trials: a trial per stimulus
            self.splitting += 1
            threading.Thread(target=self._split, args=(path,), daemon=True, name="web-split").start()
        else:
            self._saved(path.parent)

    def _split(self, path: Path) -> None:
        """Off any page's thread: a trial for each stimulus sent, from Simple Stimulus's Before."""
        names = [c.name for c in self.cfg.channels]
        mon = next((c.name for c in self.cfg.channels if self.cfg.stim_monitor
                    and c.physical == self.cfg.stim_monitor), None)
        ch = mon or next((n for n in names if "stim" in n.lower()), None)
        before = float(self.stimulator.values.get("before", 100.0))
        try:
            new = trialsplit.split(path, ch, before, lambda: session.next_block_path(path.parent))
            if new:
                trialsplit.hide_originals([(path, new)])
            msg, bad = (f"Saved {path.stem} as a trial for each of its {len(new)} stimuli "
                        f"(the whole one is hidden in Analysis)."), False
        except Exception as e:
            new, msg, bad = [], f"Saved {path.stem}, but couldn't make a trial of each stimulus: {e}", True
        with self._lock:
            self.splitting -= 1
            if new and self.folder == path.parent:
                self.blocks = [b for b in self.blocks if b != path] + [q for q, _, _ in new]
            if not self.rec.recording:
                self.message, self.error = msg, bad
        self._saved(path.parent)

    def _saved(self, folder: Path) -> None:
        if self.on_saved is not None:
            threading.Thread(target=self.on_saved, args=(folder,), daemon=True, name="web-saved").start()

    # -- what the page shows -----------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            rec = self.rec
            return {
                "on": self.on, "recording": rec.recording, "windows": self.win.running,
                "trial": rec.path.stem if rec.recording and rec.path else "",
                "secs": rec.samples_written / max(float(self.live.backend.actual_rate), 1e-9) if rec.recording else 0.0,
                "session": self.folder.name if self.folder else "", "folder": str(self.folder or ""),
                "unsaved": self.folder is None or self.folder.parent == self.unsaved(),
                "n_trials": len(self.blocks), "label": self.label,
                "comments": [[round(t, 2), text] for t, text in self.comments],
                "message": self.message, "error": self.error, "splitting": self.splitting > 0,
            }

    def close(self) -> None:
        with self._lock:
            self._end()
        self.rec.close()
