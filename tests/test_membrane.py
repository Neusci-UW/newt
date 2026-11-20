"""The Membrane potential tab: the page ships with the package and the tab works with or
without PyQt6-WebEngine."""
from pyqtgraph.Qt import QtWidgets

from neusci_daq import membranepage, style

from test_channels import app, pump, win  # noqa: F401  (fixtures)


def test_page_ships_with_the_package():
    page = membranepage.PAGE
    assert page.exists(), page
    html = page.read_text(encoding="utf-8")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "<title>Exploring the membrane potential</title>" in html
    assert "vendor/mathjax/tex-svg.js" in html          # local MathJax first, online copy as fallback


def test_tab_is_under_simulation(win):
    assert win.tabs.pages["membrane"] is win.lesson_pages["membrane"]
    assert win.tabs.group_of["membrane"] == "lessons"


def test_tab_shows_the_page_or_explains_what_to_install(app, win):
    win.show()
    win.tabs.pick(0, "membrane")
    pump(app, 0.3)
    page = win.lesson_pages["membrane"]
    if membranepage.available():
        assert page.view is not None and page.view.url().toLocalFile().endswith("membrane_potential.html")
        assert page.btn_refresh.isVisible()
    else:
        texts = " ".join(lbl.text() for lbl in page.findChildren(QtWidgets.QLabel))
        assert "PyQt6-WebEngine" in texts
        assert any(b.text() == "Open in web browser" for b in page.findChildren(QtWidgets.QPushButton))


def test_follows_the_app_theme(win):
    win.lesson_pages["membrane"].apply_theme(style.DARK)
    assert win.lesson_pages["membrane"]._mode == style.DARK
    win.lesson_pages["membrane"].apply_theme(style.LIGHT)
    assert win.lesson_pages["membrane"]._mode == style.LIGHT


def test_rc_page_ships_and_is_under_simulation(app, win):
    html = membranepage.RC_PAGE.read_text(encoding="utf-8")
    assert "<title>Passive Properties: Time</title>" in html
    assert win.tabs.pages["rc"] is win.lesson_pages["rc"]
    assert win.tabs.group_of["rc"] == "lessons"
    win.show()
    win.tabs.pick(0, "rc")
    pump(app, 0.3)
    if membranepage.available():
        assert win.lesson_pages["rc"].view.url().toLocalFile().endswith("rc_membrane.html")
    else:
        texts = " ".join(lbl.text() for lbl in win.lesson_pages["rc"].findChildren(QtWidgets.QLabel))
        assert "Passive properties: time" in texts
