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
    assert "<title>Membrane Potential, Ion by Ion</title>" in html
    assert "vendor/mathjax/tex-svg.js" in html          # local MathJax first, online copy as fallback


def test_tab_is_under_simulation(win):
    assert win.tabs.pages["membrane"] is win.membrane_page
    assert win.tabs.group_of["membrane"] == "lessons"


def test_tab_shows_the_page_or_explains_what_to_install(app, win):
    win.show()
    win.tabs.pick(0, "membrane")
    pump(app, 0.3)
    page = win.membrane_page
    if membranepage.available():
        assert page.view is not None and page.view.url().toLocalFile().endswith("membrane_potential.html")
        assert page.btn_refresh.isVisible()
    else:
        texts = " ".join(lbl.text() for lbl in page.findChildren(QtWidgets.QLabel))
        assert "PyQt6-WebEngine" in texts
        assert any(b.text() == "Open in web browser" for b in page.findChildren(QtWidgets.QPushButton))


def test_follows_the_app_theme(win):
    win.membrane_page.apply_theme(style.DARK)
    assert win.membrane_page._mode == style.DARK
    win.membrane_page.apply_theme(style.LIGHT)
    assert win.membrane_page._mode == style.LIGHT


def test_rc_page_ships_and_is_under_simulation(app, win):
    html = membranepage.RC_PAGE.read_text(encoding="utf-8")
    assert "<title>RC Circuit and Membrane</title>" in html
    assert win.tabs.pages["rc"] is win.rc_page
    assert win.tabs.group_of["rc"] == "lessons"
    win.show()
    win.tabs.pick(0, "rc")
    pump(app, 0.3)
    if membranepage.available():
        assert win.rc_page.view.url().toLocalFile().endswith("rc_membrane.html")
    else:
        texts = " ".join(lbl.text() for lbl in win.rc_page.findChildren(QtWidgets.QLabel))
        assert "RC circuit" in texts
