"""Newt's HTML front end (live acquisition only, so far): python -m neusci_daq.web [--run] [--browser | --serve] [--port N]

Opens a native window (pywebview: WebKit on macOS) on a page served from this process;
--browser serves it to your web browser instead (Ctrl+C to quit)."""
from __future__ import annotations

import argparse
import secrets
import threading
import webbrowser

from .live import Live
from .server import make_server


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m neusci_daq.web")
    ap.add_argument("--browser", action="store_true", help="open in your web browser, not a window")
    ap.add_argument("--serve", action="store_true", help="only serve the page (open the printed address yourself)")
    ap.add_argument("--port", type=int, default=0, help="port to serve on (default: any free one)")
    ap.add_argument("--run", action="store_true", help="start recording right away")
    ap.add_argument("--key", help=argparse.SUPPRESS)      # a fixed key (for previews); default: a new one
    args = ap.parse_args()

    live = Live()
    if args.run:
        live.run(True)
    key = args.key or secrets.token_urlsafe(16)
    srv = make_server(live, key, args.port)
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
            webview.create_window("Newt · Live (HTML trial)", url, width=1440, height=980,
                                  min_size=(900, 640), background_color="#f3f4f1")
            webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        live.close()
        srv.server_close()


if __name__ == "__main__":
    main()
