"""A small web server (standard library only) between the pages and Python.

GET /                 the first page (Record)      GET /static/...  the pages and their files
GET /live/stream      never-ending binary: [u32 length][frame] for every frame (see Live._build)
GET /live/state       JSON: channels, running, rates, ...
POST /live/cmd        JSON {"cmd": ...}: run, window, cols, lag (the page's lag numbers, printed);
                      record, comment, label, new_session, add_to, sessions (Recording.command);
                      setup_..., calc_..., yrange, recolor (Rig.command); review_..., comment_...
                      (Review.command)
POST /live/review     JSON {"x0", "x1", "cols"}: the reviewed trial, packed as a live frame
POST /live/video      the reviewed trial's video (mp4), if the Qt app recorded one
GET /stim/state       JSON: Simple Stimulus's settings, what was sent, presets (see Stimulator.state)
POST /stim/cmd        JSON {"cmd": ...}: set, fire, repeat, stop, preset_save, preset_load, preset_delete
GET /design/state     JSON: Stimulus Design's segments, trials, run (see Design.state); ?pic=N leaves
                      out the picture if it is still version N
POST /design/cmd      JSON {"cmd": ..., "pic": N}: pick, set, per_trial, add, run, once, save... (Design.command)
GET /trials/state     JSON: the session's trials in their groups (see Trials.state)
POST /trials/cmd      JSON {"cmd": ...}: open, channel, tick, rename, combine, gain, fit, alike, calc,
                      delete, ... (see Trials.command)
POST /trials/preview  JSON {"i" | "avg", "cols"}: a trial's (or a group's average's) recorded channels,
                      shrunk, packed as /charts/data packs a chart (see Trials.preview)
POST /trials/calc     JSON {"kind", "source", "values", "name", "unit", "old"}: a calculated channel's
                      formula, name, unit, and why it can't be made (see Trials.calc_check)
GET /spikes/state     JSON: the spike search's settings, the units found (see Spikes.state)
POST /spikes/cmd      JSON {"cmd": ...}: set, identify, find, clear, unit, unit_color, unit_opacity
GET /network/state   JSON: the simulated network's cells, synapses, recordings, firing (see Network.state)
GET /network/rates   JSON: each cell's firing now (see Network.rates)
POST /network/cmd    JSON {"cmd": ...}: set (edits), cells, reset (see Network.command)
GET /static/lessons/ the lesson pages (the lessons submodule), with lessonbridge.js (see lessons.py)
POST /lessons/save   JSON {"name", "text"}: the lesson editor saves questions/<name>.js (see lessons.save)
GET /charts/state     JSON: the Analysis board's charts, and what they can show (see Charts.state)
POST /charts/cmd      JSON {"cmd": ...}: add, remove, set, move, spread (see Charts.command)
POST /charts/data     JSON {"id", "x0", "x1", "cols"}: what a chart draws, shrunk (see Charts.data)
GET /pad/state        JSON: the DataPad's columns, rows and their numbers, summary rows (see Pad.state)
POST /pad/cmd         JSON {"cmd": ...}: add, set, what, name, fx, move, remove, rows, segment, point,
                      stretch, baseline, by, chart_add, chart_set, chart_remove... (see Pad.command)
POST /pad/preview     JSON {"trial" | "avg", "row", "col", "stim", "resp", "cols", "x0", "x1"}: a row's
                      trial, shrunk, packed as /charts/data packs a chart (see Pad.preview)
POST /pad/chart       JSON {"id"}: a DataPad chart's dots (see Pad.chart)
GET /fig/state        JSON: Single Plot's figure, the columns and charts to make one of (see Figures.state)
POST /fig/cmd         JSON {"cmd": ...}: new, chart, set, series, look, copy_look, every, link, to_canvas,
                      edit, clear (see Figures.command)
POST /fig/png         JSON {"which": "single" | "panel", "id", "dpi", "w", "h"}: a figure drawn, as base64
                      PNG, and where its plot sits (see Figures.picture)
POST /fig/export      JSON {"what": "single" | "page", "fmt", "dpi"}: the file itself; with "dialog", saved
                      where a window's Save dialog says (see Figures.export, save_as)
GET /canvas/state     JSON: the Plot Canvas's page, figures and drawings (see Figures.canvas)
POST /canvas/cmd      JSON {"cmd": ...}: paper, letters, text, move, resize, freeze, duplicate, remove,
                      image, ann_add, ann_set (see Figures.canvas_command)
GET /breadboard/state JSON: the Breadboard simulator's board, preview, timing (see breadboard.py)
POST /breadboard/cmd  JSON {"cmd": ...}: add, drop, edit, delete, reset, clear, save, open, files, load
GET /app/state        JSON: the session showing, its file, recent files, theme (see App.state)
GET /app/files        JSON: ?dir=...&kind=newt|argus|mat|folder: a folder's contents, for a page's own
                      file dialog (see App.files)
GET /app/help         JSON: the Help page's explanations (see App.help)
GET /app/watch        never-ending: a byte every 0.5 s while the page is open (see App.watch)
POST /app/cmd         JSON {"cmd": ...}: new, open, save, save_as, export, stop, theme, forget (App.command)

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

from .app import App
from .charts import Charts
from .design import Design
from .figures import Figures
from . import lessons
from .live import Live
from .network import Network
from .pad import Pad
from .record import COMMANDS as RECORD_COMMANDS, Recording
from .review import COMMANDS as REVIEW_COMMANDS
from .rig import COMMANDS as RIG_COMMANDS
from .stimulator import Stimulator
from .trials import Trials

STATIC = Path(__file__).parent / "static"


def make_server(live: Live, trials: Trials, stimulator: Stimulator, design: Design, recording: Recording,
                charts: Charts, pad: Pad, figures: Figures, key: str,
                port: int = 0, breadboard=None, app: App | None = None) -> ThreadingHTTPServer:
    network = Network(live)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):          # quiet
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
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
            if path.startswith("/static/lessons/"):
                return self._send(*lessons.page(path[len("/static/lessons/"):]))
            if path.startswith("/static/"):
                f = (STATIC / path[len("/static/"):]).resolve()
                if STATIC.resolve() in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    body = f.read_bytes()
                    return self._send(app.page(body) if app and f.suffix == ".html" else body, ctype)
                return self._send(b"not found", "text/plain", 404)
            if not self._keyed():
                return
            if path == "/live/stream":
                return self._stream()
            if path == "/live/state":
                return self._json(live.state())
            if path in ("/network/state", "/network/rates"):
                return self._json(network.state() if path == "/network/state" else network.rates())
            if app and path.startswith("/app/"):
                return self._app_get(path, parse_qs(query))
            if path == "/trials/state":
                return self._json(trials.state())
            if path == "/spikes/state":
                return self._json(trials.spikes.state())
            if path == "/charts/state":
                return self._json(charts.state())
            if path == "/pad/state":
                return self._json(pad.state())
            if path == "/fig/state":
                return self._json(figures.state())
            if path == "/canvas/state":
                return self._json(figures.canvas())
            if path == "/stim/state":
                return self._json(stimulator.state())
            if path == "/design/state":
                return self._json(design.state(parse_qs(query).get("pic", [None])[0]))
            if path == "/breadboard/state" and breadboard is not None:
                return self._json(breadboard.state(parse_qs(query).get("v", [None])[0]))
            self._send(b"not found", "text/plain", 404)

        def do_POST(self):
            if not self._keyed():
                return
            path = self.path.split("?")[0]
            if path in ("/network/cmd", "/lessons/save"):
                return self._network_lessons(path)
            d = {}
            state = {"/live/cmd": live.state, "/stim/cmd": stimulator.state, "/charts/cmd": charts.state,
                     "/charts/data": dict, "/pad/cmd": pad.state, "/pad/chart": dict, "/pad/preview": dict,
                     "/design/cmd": lambda: design.state(d.get("pic")), "/fig/cmd": figures.state,
                     "/fig/png": dict, "/fig/export": dict, "/canvas/cmd": figures.canvas,
                     "/spikes/cmd": trials.spikes.state, "/trials/preview": dict, "/trials/calc": dict,
                     "/breadboard/cmd": breadboard.state if breadboard else dict,
                     "/live/review": dict, "/live/video": dict,
                     "/app/cmd": app.state if app else dict,
                     }.get(path, trials.state)
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                out = {}
                if path == "/charts/data":
                    return self._send(*charts.data(d))
                if path == "/pad/preview":
                    return self._send(*pad.preview(d))
                if path == "/live/review":
                    return self._send(*live.review.frame(d))
                if path == "/live/video":
                    return self._send(*live.review.video_bytes(d))
                if path == "/pad/chart":
                    return self._json(pad.chart(d))
                if path == "/trials/preview":
                    return self._send(*trials.preview(d))
                if path == "/trials/calc":
                    return self._json(trials.calc_check(d))
                if path == "/fig/png":
                    return self._json(figures.picture(d))
                if path == "/fig/export":
                    if d.get("dialog"):
                        return self._json(figures.save_as(d))
                    body, ctype, name = figures.export(d)
                    return self._send(body, ctype, extra={"Content-Disposition": f'attachment; filename="{name}"'})
                if path == "/live/cmd":
                    out = self._live(d)
                elif path == "/trials/cmd":
                    trials.command(d)
                elif path == "/charts/cmd":
                    charts.command(d)
                elif path == "/spikes/cmd":
                    trials.spikes.command(d)
                elif path == "/pad/cmd":
                    out = pad.command(d)
                elif path == "/fig/cmd":
                    out = figures.command(d)
                elif path == "/canvas/cmd":
                    out = figures.canvas_command(d)
                elif path == "/stim/cmd":
                    self._stim(d)
                elif path == "/design/cmd":
                    design.command(d)
                elif path == "/breadboard/cmd" and breadboard is not None:
                    breadboard.command(d)
                elif path == "/app/cmd" and app:
                    out = app.command(d)
                else:
                    return self._send(b"not found", "text/plain", 404)
                self._json({**state(), **out})
            except Exception as e:            # shown on the page
                self._json({**state(), "problem": str(e)}, 400)

        def _network_lessons(self, path: str) -> None:
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if path == "/lessons/save":
                    out = lessons.save(d)
                    return self._json(out, 400 if out else 200)
                network.command(d)
                self._json(network.state())
            except Exception as e:            # shown on the page
                self._json({**(network.state() if path == "/network/cmd" else {}), "problem": str(e)}, 400)

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
            elif cmd in RIG_COMMANDS:
                return live.rig.command(d)
            elif cmd in REVIEW_COMMANDS:
                return live.review.command(d)
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

        def _app_get(self, path: str, q: dict):
            if path == "/app/state":
                return self._json(app.state())
            if path == "/app/help":
                return self._json(app.help())
            if path == "/app/files":
                try:
                    return self._json(app.files(q.get("dir", [""])[0], q.get("kind", ["newt"])[0]))
                except ValueError as e:
                    return self._json({"problem": str(e)}, 400)
            if path == "/app/watch":
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return app.watch(self.wfile.write)
            self._send(b"not found", "text/plain", 404)

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
