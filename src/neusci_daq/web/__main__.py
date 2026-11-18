"""Newt's HTML front end: python -m neusci_daq.web [--run] [--session FOLDER] [--data DIR]
[--browser | --serve] [--port N]

Opens a native window (pywebview: WebKit on macOS) on pages served from this process;
--browser serves them to your web browser instead, --serve only serves (Ctrl+C to quit)."""
from __future__ import annotations

import argparse
import secrets
import threading
import webbrowser
from pathlib import Path

from .design import Design
from .live import Live
from .record import Recording
from .server import make_server
from .stimulator import Stimulator
from .trials import Trials, session_folders


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m neusci_daq.web")
    ap.add_argument("--browser", action="store_true", help="open in your web browser, not a window")
    ap.add_argument("--serve", action="store_true", help="only serve the pages (open the printed address yourself)")
    ap.add_argument("--port", type=int, default=0, help="port to serve on (default: any free one)")
    ap.add_argument("--run", action="store_true", help="start recording right away")
    ap.add_argument("--data", default="data", help="the data folder, where sessions are (default: ./data)")
    ap.add_argument("--session", help="a session folder to open on Analysis (default: the newest in --data)")
    ap.add_argument("--key", help=argparse.SUPPRESS)      # a fixed key (for previews); default: a new one
    args = ap.parse_args()

    live = Live()
    stimulator = Stimulator(live)
    design = Design(live, stimulator)
    data = Path(args.data).expanduser().resolve()
    trials = Trials(data)
    recording = Recording(live, stimulator, data)
    recording.on_saved = trials.added          # a new trial shows on Analysis
    if args.run:
        live.run(True)
    first = Path(args.session) if args.session else next(iter(session_folders(trials.data)), None)
    if first is not None:           # read while the window opens
        threading.Thread(target=trials.open, args=(first,), daemon=True, name="web-open").start()
    key = args.key or secrets.token_urlsafe(16)
    srv = make_server(live, trials, stimulator, design, recording, key, args.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/#k={key}"
    print(f"serving {url}", flush=True)
    try:
        if args.browser or args.serve:
            if args.browser:
                webbrowser.open(url)
            srv.serve_forever()
        else:
            import webview
            threading.Thread(target=srv.serve_forever, daemon=True, name="web-server").start()
            webview.create_window("Newt (HTML)", url, width=1440, height=980,
                                  min_size=(900, 640), background_color="#f3f4f1")
            webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        design.stop()
        stimulator.stop()
        live.close()                   # (ends the trial being recorded)
        recording.close()
        srv.server_close()


if __name__ == "__main__":
    main()
