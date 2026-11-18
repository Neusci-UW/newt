"""A small web server (standard library only) between the pages and Python.

GET /                 the first page (Record)      GET /static/...  the pages and their files
GET /live/stream      never-ending binary: [u32 length][frame] for every frame (see Live._build)
GET /live/state       JSON: channels, running, rates, ...
POST /live/cmd        JSON {"cmd": ...}: run, window, cols, lag (the page's lag numbers, printed);
                      record, comment, label, new_session, add_to, sessions (Recording.command)
GET /stim/state       JSON: Simple Stimulus's settings, what was sent, presets (see Stimulator.state)
POST /stim/cmd        JSON {"cmd": ...}: set, fire, repeat, stop, preset_save, preset_load, preset_delete
GET /design/state     JSON: Stimulus Design's segments, trials, run (see Design.state); ?pic=N leaves
                      out the picture if it is still version N
POST /design/cmd      JSON {"cmd": ..., "pic": N}: pick, set, per_trial, add, run, once, save... (Design.command)
GET /trials/state     JSON: the session's trials in their groups (see Trials.state)
POST /trials/cmd      JSON {"cmd": ...}: open, channel, tick, rename, combine, ... (see Trials.command)

Only 127.0.0.1 is served, and everything but the pages' files needs this launch's key
(header X-Newt-Key): any program on the computer can reach a local server, and
/live/cmd fires the stimulator's outputs.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import struct
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .design import Design
from .live import Live
from .record import COMMANDS as RECORD_COMMANDS, Recording
from .stimulator import Stimulator
from .trials import Trials

STATIC = Path(__file__).parent / "static"


def make_server(live: Live, trials: Trials, stimulator: Stimulator, design: Design, recording: Recording,
                key: str, port: int = 0) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):          # quiet
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, d: dict, code: int = 200) -> None:
            self._send(json.dumps(d).encode(), "application/json", code)

        def _keyed(self) -> bool:
            if secrets.compare_digest(self.headers.get("X-Newt-Key", ""), key):
                return True
            self._send(b"no key", "text/plain", 403)
            return False

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/":
                path = "/static/live.html"
            if path.startswith("/static/"):
                f = (STATIC / path[len("/static/"):]).resolve()
                if STATIC.resolve() in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    return self._send(f.read_bytes(), ctype)
                return self._send(b"not found", "text/plain", 404)
            if not self._keyed():
                return
            if path == "/live/stream":
                return self._stream()
            if path == "/live/state":
                return self._json(live.state())
            if path == "/trials/state":
                return self._json(trials.state())
            if path == "/stim/state":
                return self._json(stimulator.state())
            if path == "/design/state":
                return self._json(design.state(parse_qs(query).get("pic", [None])[0]))
            self._send(b"not found", "text/plain", 404)

        def do_POST(self):
            if not self._keyed():
                return
            path = self.path.split("?")[0]
            d = {}
            state = {"/live/cmd": live.state, "/stim/cmd": stimulator.state,
                     "/design/cmd": lambda: design.state(d.get("pic"))}.get(path, trials.state)
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                out = {}
                if path == "/live/cmd":
                    out = self._live(d)
                elif path == "/trials/cmd":
                    trials.command(d)
                elif path == "/stim/cmd":
                    self._stim(d)
                elif path == "/design/cmd":
                    design.command(d)
                else:
                    return self._send(b"not found", "text/plain", 404)
                self._json({**state(), **out})
            except Exception as e:            # shown on the page
                self._json({**state(), "problem": str(e)}, 400)

        def _live(self, d: dict) -> dict:
            cmd = d.get("cmd")
            if cmd == "run":
                live.run(bool(d["on"]))
            elif cmd == "window":
                live.set_window(d["s"])
            elif cmd == "cols":
                live.set_cols(d["n"])
            elif cmd in RECORD_COMMANDS:
                return recording.command(d)
            elif cmd == "lag":
                print("lag ({engine}, {vis}): Python→screen {age_med:.0f} ms median, {age_p95:.0f} ms p95 "
                      "(→page {net_med:.0f}, →canvas {canvas_med:.0f}) · draw {draw_ms:.2f} ms · {fps:.0f} frames/s "
                      "· screen {refresh:.0f}/s · Python frame {build_ms:.2f} ms".format(**d), flush=True)
            return {}

        def _stim(self, d: dict) -> None:
            cmd = d.get("cmd")
            if cmd == "set":
                stimulator.set(d["values"])
            elif cmd == "fire":
                stimulator.fire()
            elif cmd == "repeat":
                stimulator.start_repeat()
            elif cmd == "stop":
                stimulator.stop()
            elif cmd == "preset_save":
                stimulator.save_preset(d["name"])
            elif cmd == "preset_load":
                stimulator.load_preset(d["name"])
            elif cmd == "preset_delete":
                stimulator.delete_preset(d["name"])
            else:
                raise ValueError(f"Unknown command {cmd!r}")

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = -1
            try:
                while True:
                    fid, frame = live.wait_frame(last)
                    if frame is not None:
                        last = fid
                        self.wfile.write(struct.pack("<I", len(frame)) + frame)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    return srv
