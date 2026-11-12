"""Lessons on the simulation pages: lesson files are valid and only use what their page
offers, and Argus saves the lesson editor's files into the lessons folder."""
import json
import re
import time

import pytest

from neusci_daq import membranepage

from test_channels import app  # noqa: F401  (fixture)

TYPES = {"mc", "num", "predict", "goal"}
MODES = {"covered", "hidden", "locked", "values"}


def read_lesson(path):
    text = path.read_text(encoding="utf-8")
    start = text.index("ArgusLesson.define(") + len("ArgusLesson.define(")
    return json.loads(text[start:text.rindex(");")])


def lesson_files():
    return sorted(membranepage.LESSONS.glob("*.js"))


def test_the_membrane_page_has_a_lesson():
    html = membranepage.PAGE.read_text(encoding="utf-8")
    assert '<script src="lesson.js"></script>' in html
    listed = re.search(r"lessons: \[([^\]]*)\]", html).group(1)
    for name in re.findall(r"'([a-z0-9_-]+)'", listed):
        assert (membranepage.LESSONS / f"{name}.js").exists(), name


@pytest.mark.parametrize("path", lesson_files(), ids=lambda p: p.stem)
def test_lesson_file_is_valid(path):
    lesson = read_lesson(path)
    assert lesson["id"] == path.stem
    qids = [q["id"] for q in lesson["questions"]]
    assert len(qids) == len(set(qids)), "question ids repeat"
    for q in lesson["questions"]:
        assert q["type"] in TYPES, q["id"]
        assert q.get("anchor"), q["id"]
        assert set(q.get("after", [])) <= set(qids), q["id"]
        if q["type"] == "mc":
            assert sum(bool(o.get("ok")) for o in q["options"]) == 1, q["id"]
        if q["type"] == "num":
            assert isinstance(q["answer"], (int, float)), q["id"]
        if q["type"] in ("predict", "goal"):
            assert q["done"]["key"], q["id"]
    for r in lesson["regions"]:
        assert r["mode"] in MODES, r["id"]
        assert set(r["until"]["ids"]) <= set(qids), r["id"]


@pytest.mark.parametrize("path", lesson_files(), ids=lambda p: p.stem)
def test_lesson_only_uses_what_its_page_offers(path):
    """Settings, readings and regions a lesson names must be in its page's lesson adapter."""
    html = membranepage.PAGE.read_text(encoding="utf-8")
    adapter = html[html.index("window.LESSON_ADAPTER"):]
    lesson = read_lesson(path)
    keys = set()
    for q in lesson["questions"]:
        keys |= set(q.get("setup", {})) | set(q.get("report", []))
        keys |= {q[k]["key"] for k in ("done", "measure") if q.get(k)}
        for place in (q["anchor"],):
            if "region" in place:
                keys.add(place["region"])
        if q.get("goTo"):
            keys.add(q["goTo"])
    for r in lesson["regions"]:
        if "region" in r["target"]:
            keys.add(r["target"]["region"])
    for k in keys:
        assert re.search(rf"\b{re.escape(k)}:", adapter), k


def test_argus_saves_lessons_into_the_lessons_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(membranepage, "LESSONS", tmp_path / "lessons")
    bridge = membranepage.LessonBridge()
    text = 'ArgusLesson.define({"id": "intro", "questions": [], "regions": []});\n'
    assert bridge.save("intro", text) == ""
    assert (tmp_path / "lessons" / "intro.js").read_text(encoding="utf-8") == text


@pytest.mark.parametrize("name", ["../escape", "Intro", "a b", "", "x" * 65, "intro.js"])
def test_argus_refuses_lesson_names_outside_the_folder(tmp_path, monkeypatch, name):
    monkeypatch.setattr(membranepage, "LESSONS", tmp_path)
    assert membranepage.LessonBridge().save(name, "ArgusLesson.define({});") != ""
    assert not any(tmp_path.iterdir())


def wait_for(app, ok, s=20.0):
    end = time.time() + s
    while time.time() < end and not ok():
        app.processEvents()
        time.sleep(0.02)
    return ok()


@pytest.mark.skipif(not membranepage.available(), reason="needs PyQt6-WebEngine")
def test_the_page_in_argus_offers_the_editor_and_saves_through_it(app, tmp_path, monkeypatch):
    monkeypatch.setattr(membranepage, "LESSONS", tmp_path)
    page = membranepage.MembranePage()
    page.resize(1200, 800)
    page.show()
    try:
        assert wait_for(app, lambda: page.view is not None and page._loaded and page.btn_lesson.isVisible()), \
            "the Edit lesson button never appeared"
        state = {}
        # the lesson file next to the page loads in Argus too (a local file, not a web server)
        page.view.page().runJavaScript("document.querySelectorAll('[data-lesson-card]').length",
                                       lambda n: state.update(cards=n))
        assert wait_for(app, lambda: state.get("cards")) and state["cards"] >= 18
        page.btn_lesson.setChecked(True)          # opens the editor in the page
        page.view.page().runJavaScript("document.documentElement.classList.contains('ls-editing')",
                                       lambda v: state.update(editing=v))
        assert wait_for(app, lambda: "editing" in state) and state["editing"]
        # the page's own Save goes through the web channel into the lessons folder
        page.view.page().runJavaScript(
            "new QWebChannel(qt.webChannelTransport, ch => ch.objects.argusLessons.save("
            "'from_page', 'ArgusLesson.define({\"id\": \"from_page\"});', r => { window.__saved = r; }));")
        assert wait_for(app, lambda: (tmp_path / "from_page.js").exists()), "nothing was saved"
        # closing the editor from inside the page un-presses the button in Argus
        page.view.page().runJavaScript("ArgusLesson.setEdit(false);")
        assert wait_for(app, lambda: not page.btn_lesson.isChecked())
    finally:
        page.close()


def test_argus_saves_only_lesson_files(tmp_path, monkeypatch):
    monkeypatch.setattr(membranepage, "LESSONS", tmp_path)
    assert membranepage.LessonBridge().save("intro", "alert('hi')") != ""
    assert not any(tmp_path.iterdir())
