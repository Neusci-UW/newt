"""Newt's HTML front end: python -m neusci_daq.web [--config FILE] [--backend B] [--run]
[--session FOLDER] [--data DIR] [--browser | --serve] [--port N]

Without --config, Record starts on the simulated rig (three cells and the stimulus); with
one (e.g. config.toml), on its device and channels, read as the Qt app reads it.

Opens a native window (pywebview: WebKit on macOS) on pages served from this process;
--browser serves them to your web browser instead, --serve only serves (Ctrl+C to quit)."""
from __future__ import annotations

import argparse
import secrets
import threading
import webbrowser
from pathlib import Path

from .breadboard import Breadboard
from .app import App, window_dialog
from .charts import Charts
from .design import Design
from .figures import Figures
from .live import Live
from .pad import Pad
from .record import Recording
from .server import make_server
from .stimulator import Stimulator
from .trials import Trials, session_folders


def load_config(path: str | None, backend: str | None):
    """The config file's setup (as the Qt app's __main__ reads it), or None: the simulated rig."""
    if path is None and backend is None:
        return None
    from .. import config
    from .live import make_cfg
    cfg = config.load(path) if path else make_cfg()
    if backend:
        cfg.backend = backend
        if backend == "audio":
            from .. import audio
            audio.use_audio_channels(cfg)
        config.validate(cfg)
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m neusci_daq.web")
    ap.add_argument("--browser", action="store_true", help="open in your web browser, not a window")
    ap.add_argument("--serve", action="store_true", help="only serve the pages (open the printed address yourself)")
    ap.add_argument("--port", type=int, default=0, help="port to serve on (default: any free one)")
    ap.add_argument("--config", help="a config file (as the Qt app's -c, e.g. config.toml): its device and channels")
    ap.add_argument("--backend", choices=["nidaqmx", "multichannelio", "simulated", "audio"],
                    help="override the input device in the config")
    ap.add_argument("--run", action="store_true", help="start recording right away")
    ap.add_argument("--data", default="data", help="the data folder, where sessions are (default: ./data)")
    ap.add_argument("--session", help="a session folder to open on Analysis (default: the newest in --data)")
    ap.add_argument("--key", help=argparse.SUPPRESS)      # a fixed key (for previews); default: a new one
    args = ap.parse_args()

    live = Live(load_config(args.config, args.backend), args.config)
    stimulator = Stimulator(live)
    design = Design(live, stimulator)
    data = Path(args.data).expanduser().resolve()
    trials = Trials(data)
    recording = Recording(live, stimulator, data)
    recording.on_saved = trials.added          # a new trial shows on Analysis
    trials.busy_path = lambda: recording.rec.path if recording.rec.recording else None   # never deleted
    if args.run:
        live.run(True)
    first = Path(args.session) if args.session else next(iter(session_folders(trials.data)), None)
    if first is not None:           # read while the window opens
        threading.Thread(target=trials.open, args=(first,), daemon=True, name="web-open").start()
    key = args.key or secrets.token_urlsafe(16)
    charts, pad = Charts(trials), Pad(trials)
    figures = Figures(trials, pad, charts)
    breadboard = Breadboard(live, stimulator, data)
    app = App(data, live, trials, recording, stimulator, design)
    srv = make_server(live, trials, stimulator, design, recording, charts, pad, figures, key, args.port,
                      breadboard=breadboard, app=app)
    url = f"http://127.0.0.1:{srv.server_address[1]}/static/live.html#k={key}"
    print(f"serving {url}", flush=True)
    try:
        if args.browser or args.serve:
            if args.browser:
                webbrowser.open(url)
            srv.serve_forever()
        else:
            import webview
            threading.Thread(target=srv.serve_forever, daemon=True, name="web-server").start()
            window = webview.create_window("Newt (HTML)", url, width=1440, height=980,
                                           min_size=(900, 640),
                                           background_color="#111614" if app.theme == "dark" else "#f3f4f1")

            def ask_path(name: str, fmt: str):           # Save figure… and Export…: the window's own dialog
                got = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name,
                                                file_types=(f"{fmt.upper()} (*.{fmt})",))
                if isinstance(got, (list, tuple)):
                    got = got[0] if got else None
                return got
            figures.ask_path = live.rig.ask_path = ask_path
            breadboard.ask_path = ask_path
            app.dialog = window_dialog(window)              # Open, Save as, Import, Export: the window's too
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
