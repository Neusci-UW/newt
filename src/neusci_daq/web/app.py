"""The app's own parts on the HTML front end, the same on every page (static/app.js):

- the session's file: New session, Open, Save, Save as. One .newt file holds a whole session
  (newtfile.py); an opened one is unpacked into data/open/, as the Qt app does, and Analysis
  shows it. Old .argus files open the same way.
- importing a LabChart or MATLAB .mat (matfile.py: made into a session in data/imported/), or a
  session folder; exporting the session's trials to a .mat (export.py).
- the window's own file dialogs (pywebview), set as `dialog` in __main__.py; with none
  (--browser, --serve), the page asks with its own (files()).
- the light/dark switch, kept in ~/.newt/web.json and written into each page as it's served.
- the outputs kept safe: Esc stops any stimulus, from any page; and once no page has been
  open for GRACE seconds (the window or the browser tab closed, or the page lost the
  server), any stimulus stops and the outputs go to 0 V. Every page holds /app/watch open
  for that; going from one tab to another is quicker than GRACE.

The .newt files are the Qt app's: what it keeps of a session opened from one (its tabs,
Analysis, protocol...) is written back as it was, and a new one gets what the Qt app needs to
open it. The HTML front end's own part is under "web".
"""
from __future__ import annotations

import dataclasses
import json
import threading
import warnings
import zipfile
import time
from pathlib import Path

from .. import helptopics, matfile, newtfile, session
from .. import protocols
from .. import simplestimulus as SS
from ..export import to_labchart_mat
from .stimulator import read_settings, update_settings

GRACE = 2.5                 # s with no page open before the outputs go to 0 V
THEMES = ("light", "dark")
KINDS = {                   # what a file dialog lists: (its name, the endings)
    "newt": ("Newt file", (".newt", ".argus")),
    "argus": ("Argus file", (".argus",)),
    "mat": ("LabChart or MATLAB file", (".mat",)),
    "folder": ("Session folder", ()),
}


def window_dialog(window):
    """The native window's own file dialogs (pywebview), for App.dialog."""
    import webview

    def dialog(mode: str, kind: str, folder: str, name: str = "") -> str | None:
        label, ends = KINDS.get(kind, KINDS["newt"])
        types = (f"{label} ({';'.join('*' + e for e in ends)})",) if ends else ()
        if mode == "folder":
            got = window.create_file_dialog(webview.FileDialog.FOLDER, directory=folder)
        elif mode == "open":
            got = window.create_file_dialog(webview.FileDialog.OPEN, directory=folder,
                                            file_types=types + ("All files (*.*)",))
        else:
            got = window.create_file_dialog(webview.FileDialog.SAVE, directory=folder, save_filename=name,
                                            file_types=types)
        if isinstance(got, (list, tuple)):
            got = got[0] if got else None
        return got or None
    return dialog


class App:
    def __init__(self, data: Path, live, trials, recording, stimulator, design):
        self.data = Path(data)
        self.live, self.trials, self.recording = live, trials, recording
        self.stimulator, self.design = stimulator, design
        self.dialog = None          # dialog(mode "open" | "save" | "folder", kind, folder, name) -> path or None
        self.newt: Path | None = None           # the .newt file of the session showing (if it has one),
        self.newt_folder: Path | None = None    # the folder it's unpacked in,
        self.newt_files: dict | None = None     # what that held when last saved or opened,
        self.newt_state: dict = {}              # and its state.json (the Qt app's parts are kept)
        self.source: Path | None = None         # the .mat the session showing was imported from
        self.message, self.error = "", False
        self.theme = str(read_settings().get("theme", "") or "")
        self._seen: Path | None = None          # the session last looked at (_follow)
        self.watchers = 0
        self._gone = 0                          # counts the times the last page went
        self._lock = threading.RLock()

    # -- what the pages show ---------------------------------------------------------------
    def folder(self) -> Path | None:
        """The session showing: Analysis's, else the one Record adds to."""
        return self.trials.folder or self.recording.folder

    def _linked(self, folder: Path | None) -> bool:
        return folder is not None and self.newt is not None and self.newt_folder == folder

    def _follow(self, folder: Path | None) -> None:
        """A session that came to show some other way (the newest at launch, or picked on
        Analysis): if it was unpacked from a .newt file or imported from a .mat that's still
        there, it's that file's again."""
        if folder == self._seen:
            return
        self._seen = folder
        if folder is None or folder.parent != self.data / "imported":
            self.source = None
        if folder is None or self._linked(folder):
            return
        try:
            src = json.loads((folder / newtfile.SOURCE).read_text(encoding="utf-8"))
            path = Path(src["path"])
            if path.is_file() and newtfile.working_folder(self.data, path) == folder:
                with zipfile.ZipFile(path) as z, warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    manifest = json.loads(z.read(newtfile.MANIFEST))
                    state = json.loads(z.read(newtfile.STATE))
                self.newt, self.newt_folder, self.newt_files, self.newt_state = path, folder, manifest["files"], state
                return
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            pass
        try:
            src = json.loads((folder / matfile.MARKER).read_text(encoding="utf-8"))
            if Path(src["path"]).is_file():
                self.source = Path(src["path"])
        except (OSError, ValueError, KeyError):
            pass

    def state(self) -> dict:
        with self._lock:
            folder = self.folder()
            self._follow(folder)
            linked = self._linked(folder)
            in_unsaved = folder is not None and folder.parent == self.data / "unsaved"
            if linked:
                name, kind = self.newt.name, "newt"
                unsaved = newtfile.files_of(folder) != self.newt_files
            elif folder is not None and self.source is not None and folder.parent == self.data / "imported":
                name, kind, unsaved = f"{self.source.name} (imported copy)", "mat", False
            elif folder is not None:
                name, kind = ("Untitled session", "unsaved") if in_unsaved else (folder.name, "folder")
                unsaved = in_unsaved and session.is_session(folder)
            else:
                name, kind, unsaved = "Untitled session", "", False
            return {
                "name": name, "kind": kind, "unsaved": unsaved, "folder": str(folder or ""),
                "file": str(self.newt) if linked else "", "recording": self.recording.busy,
                "recent": [{"path": p, "name": Path(p).name} for p in read_settings().get("recent_files", [])],
                "theme": self.theme, "native": self.dialog is not None, "data": str(self.data),
                "message": self.message, "error": self.error,
            }

    def page(self, html: bytes) -> bytes:
        """A page as it's served: in the theme picked, from its first paint."""
        if self.theme in THEMES:
            return html.replace(b'<html lang="en">', f'<html lang="en" data-theme="{self.theme}">'.encode(), 1)
        return html

    def files(self, where: str, kind: str) -> dict:
        """For the page's own file dialog (no window's): a folder's folders, and its files of `kind`."""
        start = self.data if self.data.is_dir() else Path.home()
        here = Path(where).expanduser() if where else start
        if not here.is_dir():
            here = start
        here = here.resolve()
        while not here.is_dir() and here.parent != here:      # (gone since): the nearest folder that's there
            here = here.parent
        ends = KINDS.get(kind, KINDS["newt"])[1]
        out = []
        try:
            items = sorted(here.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            raise ValueError(f"Can't look in {here}: {e.strerror or e}") from None
        for p in items:
            if p.name.startswith("."):
                continue
            try:
                if p.is_dir():
                    out.append({"name": p.name, "path": str(p), "dir": True, "session": session.is_session(p)})
                elif kind != "folder" and p.suffix.lower() in ends:
                    st = p.stat()
                    out.append({"name": p.name, "path": str(p), "dir": False, "size": st.st_size,
                                "mtime": st.st_mtime})
            except OSError:
                continue
        places = [("Data folder", start), ("Home", Path.home()), ("Desktop", Path.home() / "Desktop"),
                  ("Documents", Path.home() / "Documents")]
        return {"dir": str(here), "parent": str(here.parent) if here.parent != here else "",
                "session": session.is_session(here), "entries": out,
                "places": [{"name": n, "path": str(p)} for n, p in places if p.is_dir()]}

    @staticmethod
    def help() -> dict:
        """The Help page's explanations (helptopics.py, the Qt app's ? buttons' words)."""
        return {"topics": helptopics.TOPICS}

    # -- commands from the pages -----------------------------------------------------------
    def command(self, c: dict) -> dict:
        cmd = c.get("cmd")
        if cmd == "stop":
            return {"stopped": self.stop_all()}
        if cmd == "theme":
            theme = str(c.get("theme") or "")
            self.theme = theme if theme in THEMES else ""
            update_settings(theme=self.theme)
            return {}
        if cmd == "forget":
            update_settings(recent_files=[])
            return {}
        if cmd == "new":
            return self.new()
        if cmd in ("open", "save", "save_as", "export"):
            return self._file_command(cmd, c)
        raise ValueError(f"Unknown command {cmd!r}")

    def _file_command(self, cmd: str, c: dict) -> dict:
        self._follow(self.folder())
        if cmd == "save":
            folder = self.folder()
            if self._linked(folder):
                return self.save(self.newt)
        kind = c.get("kind") or ("mat" if cmd == "export" else "newt")
        path = c.get("path")
        if not path:
            if self.recording.busy:
                raise ValueError("Stop recording first.")
            if cmd == "open":
                ask = {"mode": "folder" if kind == "folder" else "open", "kind": kind, "dir": str(self._start())}
            else:
                ask = {"mode": "save", "kind": kind, "dir": str(self._start()), "name": self._default_name(kind)}
            if self.dialog is None:
                return {"pick": ask}            # the page asks, then sends the path
            path = self.dialog(ask["mode"], kind, ask["dir"], ask.get("name", ""))
            if not path:
                return {"cancelled": True}
        if cmd == "open":
            return self.open(path)
        if cmd == "export":
            return self.export(path)
        return self.save(path)

    def _start(self) -> Path:
        if self.newt is not None and self.newt.parent.is_dir():
            return self.newt.parent
        return self.data if self.data.is_dir() else Path.home()

    def _default_name(self, kind: str) -> str:
        folder = self.folder()
        if kind == "mat":
            if self.source is not None:
                return self.source.stem + " (from Newt).mat"
            return (self.newt.stem if self._linked(folder) else folder.name if folder else "session") + ".mat"
        if self._linked(folder):
            return self.newt.name
        if folder is None or folder.parent in (self.data / "unsaved", self.data / "imported"):
            name = folder.name if folder is not None and folder.parent == self.data / "unsaved" \
                else self.source.stem if self.source is not None else time.strftime("session_%Y%m%d_%H%M%S")
            return name + newtfile.SUFFIX
        return folder.name + newtfile.SUFFIX

    def _say(self, text: str, error: bool = False) -> None:
        self.message, self.error = text, error

    def new(self) -> dict:
        """An empty session, as the Qt app's File › New: the next trial recorded starts it.
        What was showing stays where it is."""
        with self._lock:
            if self.recording.busy:
                raise ValueError("Stop recording first.")
            self.recording.new_session()
            t = self.trials
            with t._lock:                     # Analysis shows nothing until a trial comes in
                t.folder, t.paths, t.blocks, t.how = None, [], [], ""
                t._state = t._auto = None
            self.newt = self.newt_folder = self.newt_files = self.source = None
            self.newt_state = {}
            self._say("New session: press Record to start it.")
            return {"opened": "new"}

    def open(self, path: str | Path) -> dict:
        """A .newt (or .argus) file, a .mat, or a session folder, shown on Analysis."""
        p = Path(path).expanduser()
        if self.recording.busy:
            raise ValueError("Stop recording first.")
        if not p.exists():
            raise ValueError(f"{p} isn't there any more.")
        with self._lock:
            if newtfile.is_session_file(p):
                p = p.resolve()
                folder, state, files = newtfile.open_file(p, self.data)
                self.trials.open(folder)
                self.newt, self.newt_folder, self.newt_files, self.newt_state = p, folder, files, state
                self.source = None
                skipped = self._restore(state.get("web") or {})
                if not session.is_session(folder) and not self.recording.busy:
                    try:                       # a file with no trials yet: Record adds to it, as in Qt
                        self.recording.add_to(folder)
                    except ValueError:
                        pass
                n = len(self.trials.paths)
                msg = f"Opened {p.name} ({n} trial{'' if n == 1 else 's'})."
                if skipped:
                    msg += " Couldn't put back " + ", ".join(skipped) + "."
                self._say(msg)
            elif p.suffix.lower() == ".mat":
                p = p.resolve()
                try:
                    folder, notes = matfile.import_mat(p, self.data / "imported")
                except matfile.MatError as e:
                    raise ValueError(str(e)) from None
                self.trials.open(folder)
                self.newt = self.newt_folder = self.newt_files = None
                self.newt_state, self.source = {}, p
                n = len(self.trials.paths)
                self._say(" ".join([f"Opened {p.name} ({n} trial{'' if n == 1 else 's'}). The .mat itself isn't "
                                    "changed: what you change is kept in its imported copy.", *notes]))
            elif p.is_dir():
                if not session.is_session(p):
                    raise ValueError(f"{p.name} has no trials (block_001.h5, ...), so it isn't a session.")
                p = p.resolve()
                self.trials.open(p)
                self.source = None
                n = len(self.trials.paths)
                self._say(f"Opened {p.name} ({n} trial{'' if n == 1 else 's'}).")
            else:
                raise ValueError(f"Newt can't open {p.name}: pick a .newt, .argus or .mat file, or a session folder.")
            self._remember(p)
            return {"opened": str(p)}

    def _restore(self, w: dict) -> list[str]:
        """The HTML front end's part of a .newt file: Simple Stimulus, Stimulus Design, the
        stimulus channel picked on Analysis. A part that can't be put back is skipped, and said."""
        skipped = []
        if isinstance(w.get("simple_stim"), dict):
            try:
                self.stimulator.set(w["simple_stim"])
            except (ValueError, TypeError) as e:
                skipped.append(f"Simple Stimulus ({e})")
        d, des = w.get("design"), self.design
        if isinstance(d, dict) and not des.running:
            try:
                with des._lock:
                    des._take(d)
                    des.sel, des.cur = min(1, len(des.stim.parts) - 1), 0
                    des._changed()
            except (ValueError, TypeError, KeyError) as e:
                skipped.append(f"Stimulus Design ({e})")
        how = w.get("how")
        if isinstance(how, str) and how and (how in self.trials.names() or how in ("sent", "notes")):
            self.trials.command({"cmd": "channel", "how": how})
        return skipped

    def save(self, path: str | Path) -> dict:
        """Everything in one .newt file: the session's trials and what's kept beside them (Analysis
        saves as it changes), and the app's state. Saving to it again adds only what changed."""
        with self._lock:
            if self.recording.busy:
                raise ValueError("Stop recording before saving.")
            folder = self.folder()
            if folder is None:
                raise ValueError("There's no session to save yet: record a trial, or open one.")
            path = Path(path).expanduser()
            if path.suffix.lower() not in newtfile.SUFFIXES:
                path = path.with_name(path.name + newtfile.SUFFIX)
            path = path.resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            state = self._app_state(folder)
            self.newt_files = newtfile.save(path, folder, state)
            self.newt, self.newt_folder, self.newt_state = path, folder, state
            self._remember(path)
            self._say(f"Saved {path.name}.")
            return {"saved": str(path)}

    def _app_state(self, folder: Path) -> dict:
        """state.json: the Qt app's parts as they were opened (or what it needs, for a new file),
        and the HTML front end's."""
        d = dict(self.newt_state) if self.newt_folder == folder and self.newt_state else self._qt_parts()
        d["version"] = newtfile.VERSION
        with self.design._lock:
            design = self.design._as_dict()
        d["web"] = {"simple_stim": dict(self.stimulator.values), "design": design, "how": self.trials.how}
        return d

    def _qt_parts(self) -> dict:
        """What the Qt app puts back from a .newt file (sessionfiles.restore_app_state)."""
        cfg, how = self.live.cfg, self.trials.how
        return {
            "tabs": {"shown": ["acq", "simple"], "layout": "off"},
            "analysis": {"stim_channel": how if how in self.trials.names() else "", "current": 0, "picked": [],
                         "picked_pages": [], "panel": "trials",
                         "canvas": {"layout": "Board", "layouts": {}, "own": [], "lock": False}},
            "stimulus": SS.stimulus(self.stimulator.values, cfg.stim_scale or 1.0).to_dict(),
            "protocol": protocols.Protocol().to_dict(),
            "sim": dataclasses.asdict(cfg.sim_network),
        }

    def export(self, path: str | Path) -> dict:
        """The session's trials as a LabChart-style .mat (export.py), as the Qt app's Export."""
        if self.recording.busy:
            raise ValueError("Stop recording before exporting.")
        folder = self.folder()
        blocks = session.list_blocks(folder) if folder is not None else []
        if not blocks:
            raise ValueError("There's nothing to export yet: record a trial, or open a session.")
        path = Path(path).expanduser()
        if path.suffix.lower() != ".mat":
            path = path.with_name(path.name + ".mat")
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        to_labchart_mat(blocks, path)
        self._say(f"Exported {len(blocks)} trial{'' if len(blocks) == 1 else 's'} to {path.name}.")
        return {"exported": str(path)}

    def _remember(self, path: Path) -> None:
        update_settings(recent_files=session.add_recent(read_settings().get("recent_files", []), path))

    # -- keeping the outputs safe ----------------------------------------------------------
    def stop_all(self, why: str = "Stopped (Esc)") -> bool:
        """Stop any stimulus: Stimulus Design's trials, Simple Stimulus's repeats, and what's
        being sent; the outputs go to 0 V. True if anything was going."""
        out = self.live.output
        going = self.design.running or self.stimulator.repeating or (out is not None and out.busy)
        if going:
            self.design.stop(why)
            self.stimulator.stop()
            self.stimulator._say(f"{why}: the stimulus stopped and the outputs are at 0 V.")
        return going

    def watch(self, write) -> None:
        """A page's /app/watch: held open while the page is, a byte every half second."""
        with self._lock:
            self.watchers += 1
        try:
            while True:
                write(b".")
                time.sleep(0.5)
        except OSError:                       # (BrokenPipe, ConnectionReset): the page went
            pass
        finally:
            with self._lock:
                self.watchers -= 1
                if self.watchers == 0:
                    self._gone += 1
                    t = threading.Timer(GRACE, self._none_left, args=(self._gone,))
                    t.daemon = True
                    t.start()

    def _none_left(self, gone: int) -> None:
        with self._lock:
            if self.watchers or gone != self._gone:
                return                        # a page came back (the next tab)
        self.stop_all("No Newt page was open")
        out = self.live.output
        if out is not None:
            try:
                out.zero()                    # whatever was or wasn't going: 0 V
            except Exception:
                pass
