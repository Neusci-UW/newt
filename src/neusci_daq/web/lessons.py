"""The Lessons tab's Python half: the lesson pages (the Neusci-UW/lessons submodule, at
neusci_daq/lessons), served at /static/lessons/, and the lesson editor's Save.

Each lesson page is served as it is, except that its <head> first loads
/static/lessonbridge.js (what the Qt app's web channel gives a page: Save for the
lesson editor, "Build on breadboard") and Newt's own copy of its fonts, so it needs
no internet. The files themselves are never changed, except the lesson files the
lesson editor saves into questions/, as the Qt app does (membranepage.LessonBridge).
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path

LESSONS = Path(__file__).resolve().parents[1] / "lessons"
QUESTIONS = LESSONS / "questions"
_NAME = re.compile(r"[a-z0-9_-]{1,64}")
_HEAD = re.compile(rb"<head[^>]*>", re.I)
# the Google Fonts links (Plex Sans and Mono, the same weights as static/fonts/plex.css)
_GOOGLE_FONTS = re.compile(rb'<link[^>]*fonts\.(?:googleapis|gstatic)\.com[^>]*>\s*', re.I)
_INJECT = (b'\n<script src="/static/lessonbridge.js"></script>'
           b'\n<link rel="stylesheet" href="/static/fonts/plex.css">\n')


def page(rel: str) -> tuple[bytes, str, int]:
    """A file of the lessons folder, as (body, content type, status)."""
    f = (LESSONS / rel).resolve()
    if LESSONS not in f.parents or not f.is_file():
        missing = "" if LESSONS.is_dir() and any(LESSONS.iterdir()) else \
            " (the lessons submodule is empty: run git submodule update --init)"
        return f"not found{missing}".encode(), "text/plain; charset=utf-8", 404
    body = f.read_bytes()
    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    if f.suffix.lower() == ".html":
        body = _GOOGLE_FONTS.sub(b"", body)
        m = _HEAD.search(body)
        at = m.end() if m else 0
        body = body[:at] + _INJECT + body[at:]
        ctype = "text/html; charset=utf-8"
    elif ctype.startswith("text/") or ctype == "application/javascript":
        ctype += "; charset=utf-8"
    return body, ctype, 200


def save(d: dict) -> dict:
    """The lesson editor's Save: write questions/<name>.js. {"problem"} if it can't."""
    name, text = str(d.get("name", "")), str(d.get("text", ""))
    if not _NAME.fullmatch(name):
        return {"problem": "the file name can only use lower-case letters, digits, - and _"}
    if "ArgusLesson.define(" not in text:
        return {"problem": "that isn't a lesson file"}
    try:
        QUESTIONS.mkdir(exist_ok=True)
        (QUESTIONS / f"{name}.js").write_text(text, encoding="utf-8")
    except OSError as e:
        return {"problem": str(e)}
    return {}
