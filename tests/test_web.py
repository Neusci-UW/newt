"""The HTML front end, end to end without a browser: the app wired as `python -m neusci_daq.web`
wires it (web.__main__.build), on a real local server, asked what the pages ask.

A smoke test: the window's own address and every tab from it, every page and what it loads,
the key, every route the server's notes list, and one run, stimulate, record, analyze pass on
the simulated rig. It is here for the kind of thing found by hand so far: tabs that led to
"no key" (the window opened at /, so its links pointed beside the pages), and a page's state
failing after two branches were merged.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

import pytest

from neusci_daq import trialfile
from neusci_daq.web import server

KEY = "smoke"
STATIC = Path(server.STATIC)
LESSONS = STATIC.parents[1] / "lessons"          # a submodule: empty in a checkout that didn't fetch it


class Client:
    def __init__(self, base: str):
        self.base = base

    def _ask(self, req) -> tuple[int, str, bytes]:
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.url, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.url, e.read()
        except (urllib.error.URLError, ConnectionError) as e:      # the server dropped it: a crash there
            return 599, req.full_url, str(e).encode()

    def get(self, path: str, key: str | None = KEY) -> tuple[int, str, bytes]:
        """(status, the address it ended at, body)."""
        return self._ask(urllib.request.Request(urljoin(self.base, path), headers={"X-Newt-Key": key} if key else {}))

    def post(self, path: str, d: dict, key: str | None = KEY) -> tuple[int, str, bytes]:
        return self._ask(urllib.request.Request(urljoin(self.base, path), data=json.dumps(d).encode(),
                                                headers={"X-Newt-Key": key} if key else {}))

    def json(self, path: str):
        status, _, body = self.get(path)
        assert status == 200, f"{path}: {status} {body[:300]!r}"
        return json.loads(body)

    def do(self, path: str, d: dict) -> dict:
        """A command that should work: its reply."""
        status, _, body = self.post(path, d)
        assert status == 200, f"{path} {d}: {status} {body[:300]!r}"
        return json.loads(body)


@pytest.fixture
def web(tmp_path, monkeypatch):
    from pyqtgraph.Qt import QtCore
    from neusci_daq.web import __main__ as app_main, stimulator, trials
    monkeypatch.setattr(stimulator, "SETTINGS", tmp_path / "web.json")        # not your own settings
    st = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.Format.IniFormat)
    monkeypatch.setattr(trials, "_qt_settings", lambda: st)                   # nor the Qt app's
    srv, parts = app_main.build(tmp_path / "data", KEY)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = Client(f"http://127.0.0.1:{srv.server_address[1]}")
    c.parts, c.address = parts, app_main.address(srv, KEY)
    yield c
    srv.shutdown()
    app_main.close(srv, parts)


def wait(ok, secs: float = 10.0) -> bool:
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if ok():
            return True
        time.sleep(0.05)
    return False


def routes() -> tuple[list, list]:
    """([(GET route, what the notes say)], [(POST route, ...)]) from the server's own notes,
    without the pages themselves (they need no key)."""
    gets, posts = [], []
    for how, path, about in re.findall(r"^(GET|POST) (/\S*)\s+(.*)$", server.__doc__, re.M):
        if path != "/" and not path.startswith("/static"):
            (gets if how == "GET" else posts).append((path, about))
    return gets, posts


# ---------------------------------------------------------------- the pages
def test_the_windows_address_opens_record_and_every_tab_leads_to_a_page(web):
    status, at, body = web.get(web.address.split("#")[0], key=None)
    assert status == 200 and at.endswith("/static/live.html") and b"<title>" in body
    status, at, _ = web.get("/", key=None)                    # the bare address ends up there too
    assert status == 200 and at.endswith("/static/live.html")
    tabs = re.findall(r"\['[^']+', '([a-z]+\.html)'\]", (STATIC / "nav.js").read_text())
    assert len(tabs) >= 8
    for page in tabs:                                         # as a browser follows a tab's link
        status, _, body = web.get(urljoin(at, page), key=None)
        assert status == 200 and b"<html" in body, f"the {page} tab: {status} {body[:60]!r}"


def test_every_page_loads_only_files_the_app_has_and_nothing_from_the_internet(web):
    pages = sorted(p.name for p in STATIC.glob("*.html"))
    assert len(pages) >= 9
    for page in pages:
        status, at, body = web.get("/static/" + page, key=None)
        assert status == 200, page
        refs = re.findall(r'<(?:script|link|img|iframe)\b[^>]*?\b(?:src|href)="([^"]+)"', body.decode())
        assert refs, f"{page} loads nothing?"
        for ref in refs:
            assert not re.match(r"(https?:)?//", ref), f"{page} loads {ref}: Newt runs without internet"
            if ref.startswith(("data:", "about:", "#")) or (ref.startswith("/static/lessons/") and not any(LESSONS.glob("*.html"))):
                continue
            status, _, _ = web.get(urljoin(at, ref.split("#")[0]), key=None)
            assert status == 200, f"{page} asks for {ref}: {status}"


def test_scripts_and_pages_only_name_files_the_app_has():
    for f in [*STATIC.glob("*.js"), *STATIC.glob("*.html")]:
        text = f.read_text()
        for ref in re.findall(r"""['"`](/static/[\w./-]+\.(?:js|css|html|woff2|png|svg))""", text):
            if not ref.startswith("/static/lessons/"):
                assert (STATIC / ref[len("/static/"):]).is_file(), f"{f.name} names {ref}"
        # links to another tab, which carry the key along
        for page in re.findall(r"""([a-z]+\.html)(?:\?[\w=&]*)?(?:['"]\s*\+\s*[\w.]*location\.hash|\$\{location\.hash\})""", text):
            assert (STATIC / page).is_file(), f"{f.name} links to {page}"


# ---------------------------------------------------------------- the key and the routes
def test_nothing_but_the_pages_answers_without_this_launchs_key(web):
    gets, posts = routes()
    assert len(gets) >= 15 and len(posts) >= 15
    for path, _ in gets:
        assert web.get(path, key=None)[0] == 403, path
        assert web.get(path, key="not-it")[0] == 403, path
    for path, _ in posts:
        assert web.post(path, {}, key=None)[0] == 403, path


def test_every_route_in_the_servers_notes_answers(web):
    gets, posts = routes()
    for path, about in gets:
        if "never-ending" in about or path.endswith("/"):
            continue
        status, _, body = web.get(path)
        assert status == 200, f"GET {path}: {status} {body[:300]!r}"
        d = json.loads(body)
        assert not (isinstance(d, dict) and d.get("problem")), f"GET {path}: {d.get('problem')}"
    for path, _ in posts:
        if path == "/lessons/save":               # (it writes into the lessons)
            continue
        status, _, body = web.post(path, {})      # nonsense is refused with a reason, not a crash or a 404
        assert status in (200, 400), f"POST {path} {{}}: {status} {body[:300]!r}"


# ---------------------------------------------------------------- one pass through the app
def test_run_stimulate_and_record_then_the_trial_is_on_analysis(web):
    rec = web.parts.recording
    web.do("/live/cmd", {"cmd": "run", "on": True})
    web.do("/live/cmd", {"cmd": "record", "on": True})
    assert wait(lambda: rec.state()["recording"]), rec.state()

    req = urllib.request.Request(urljoin(web.base, "/live/stream"), headers={"X-Newt-Key": KEY})
    with urllib.request.urlopen(req, timeout=10) as stream:       # a frame of the live traces
        n = int.from_bytes(stream.read(4), "little")
        assert 32 < n < 5_000_000 and len(stream.read(n)) == n

    sent = web.do("/stim/cmd", {"cmd": "fire"})
    assert not sent.get("problem") and not sent["error"], sent["message"]
    assert sent["sent"] == 1
    for path in ("/live/state", "/breadboard/state", "/network/rates", "/stim/state", "/design/state"):
        web.json(path)                                            # each page's state, while it runs
    time.sleep(0.6)

    web.do("/live/cmd", {"cmd": "record", "on": False})
    web.do("/live/cmd", {"cmd": "run", "on": False})
    assert wait(lambda: not rec.state()["recording"] and rec.state()["n_trials"] >= 1), rec.state()
    folder = rec.state()["folder"]
    blocks = sorted(Path(folder).glob("block_*.h5"))
    assert blocks, f"no trial saved in {folder}"
    b = trialfile.read_block(blocks[0])
    assert b["data"].shape[0] == len(b["channel_names"]) and b["data"].shape[1] > 0

    st = web.do("/trials/cmd", {"cmd": "open", "folder": folder})    # the session, on Analysis
    assert not st.get("problem") and st["n_trials"] >= 1 and st["groups"], st.get("problem")
    for path in ("/trials/state", "/charts/state", "/pad/state", "/fig/state", "/canvas/state", "/spikes/state"):
        web.json(path)
