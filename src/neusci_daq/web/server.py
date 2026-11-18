"""A small web server (standard library only) between the page and Live.

GET /           the page            GET /static/... its files (no key needed: nothing secret)
GET /stream     never-ending binary: [u32 length][frame] for every frame (see Live._build)
GET /state      JSON: channels, running, rates, ...
POST /cmd       JSON {"cmd": ...}: run, window, cols, stim, lag (the page's lag numbers, printed)

Only 127.0.0.1 is served, and /stream, /state and /cmd need this launch's key (header
X-Newt-Key): any program on the computer can reach a local server, and /cmd fires the
stimulator's outputs."""
from __future__ import annotations

import json
import mimetypes
import secrets
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .live import Live

STATIC = Path(__file__).parent / "static"


def make_server(live: Live, key: str, port: int = 0) -> ThreadingHTTPServer:
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
            path = self.path.split("?")[0]
            if path == "/":
                path = "/static/live.html"
            if path == "/stream":
                return self._keyed() and self._stream()
            if path == "/state":
                return self._keyed() and self._json(live.state())
            if path.startswith("/static/"):
                f = (STATIC / path[len("/static/"):]).resolve()
                if STATIC.resolve() in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    return self._send(f.read_bytes(), ctype)
            self._send(b"not found", "text/plain", 404)

        def do_POST(self):
            if not self._keyed():
                return
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                cmd, out = d.get("cmd"), {}
                if cmd == "run":
                    live.run(bool(d["on"]))
                elif cmd == "window":
                    live.set_window(d["s"])
                elif cmd == "cols":
                    live.set_cols(d["n"])
                elif cmd == "stim":
                    out["sent"] = live.stimulate(d["amp"], d["width"], d["count"], d["every"])
                elif cmd == "lag":
                    print("lag ({engine}, {vis}): Python→screen {age_med:.0f} ms median, {age_p95:.0f} ms p95 "
                          "(→page {net_med:.0f}, →canvas {canvas_med:.0f}) · draw {draw_ms:.2f} ms · {fps:.0f} frames/s "
                          "· screen {refresh:.0f}/s · Python frame {build_ms:.2f} ms".format(**d), flush=True)
                self._json({**live.state(), **out})
            except Exception as e:            # shown on the page
                self._json({**live.state(), "problem": str(e)}, 400)

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
