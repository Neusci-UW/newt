"""The Membrane potential tab: an interactive particle simulation of ions crossing a
membrane, with the Nernst and GHK equations, a charge-density view and a GHK calculator.

The page itself is web/membrane_potential.html (plain HTML and JavaScript). It is shown
in a QWebEngineView, which needs the optional PyQt6-WebEngine package:

    pip install -e ".[web]"

Without it the tab explains that and offers to open the page in the web browser instead.
The equations use MathJax: web/vendor/mathjax/tex-svg.js if present (see
tools/fetch_mathjax.py, for computers without internet), otherwise the online copy.
"""
from __future__ import annotations

from pathlib import Path

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import style

PAGE = Path(__file__).with_name("web") / "membrane_potential.html"

# QtWebEngine has to be imported before the QApplication exists. gui.py imports this
# module at the top, before run() makes the app, so importing it here is early enough.
try:
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except Exception:   # not installed, or imported after the QApplication (as in some tests)
    QWebEngineView = None
    QWebEngineSettings = None


def available() -> bool:
    """True if the page can be shown inside the app (PyQt6-WebEngine is installed)."""
    return QWebEngineView is not None


class MembranePage(QtWidgets.QWidget):
    def __init__(self, main=None):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self._loaded = False
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self.view = None
        if not (available() and PAGE.exists()):
            self._lay.addWidget(self._fallback())
        # Otherwise the web view is made the first time the tab is shown: starting
        # Chromium takes a moment and memory, so it waits until someone opens the page.

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.view is None and available() and PAGE.exists():
            self._make_view()

    def _make_view(self) -> None:
        self.view = QWebEngineView(self)
        s = self.view.settings()
        # The page is a local file; let it fetch fonts and MathJax online when it has to.
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.view.loadFinished.connect(self._on_loaded)
        self.view.setUrl(QtCore.QUrl.fromLocalFile(str(PAGE)))
        self._lay.addWidget(self.view)

    def _fallback(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(40, 40, 40, 40)
        v.addStretch(1)
        if not PAGE.exists():
            text = f"The membrane potential page is missing:\n{PAGE}"
        else:
            text = ("The membrane potential simulation runs as a web page. To show it here, "
                    "install PyQt6-WebEngine:\n\n    pip install -e \".[web]\"\n\n"
                    "Then restart the app. Until then you can open it in your web browser.")
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(label)
        if PAGE.exists():
            btn = QtWidgets.QPushButton("Open in web browser")
            btn.clicked.connect(self.open_in_browser)
            row = QtWidgets.QHBoxLayout()
            row.addStretch(1)
            row.addWidget(btn)
            row.addStretch(1)
            v.addLayout(row)
        v.addStretch(1)
        return box

    def open_in_browser(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(PAGE)))

    def _on_loaded(self, ok: bool) -> None:
        self._loaded = ok
        self._push_theme()

    def apply_theme(self, mode: str) -> None:
        """Follow the app's light/dark mode (the page reads data-theme on its root)."""
        self._mode = mode
        self._push_theme()

    def _push_theme(self) -> None:
        if self.view is None or not self._loaded:
            return
        name = "dark" if self._mode == style.DARK else "light"
        self.view.page().runJavaScript(f"document.documentElement.setAttribute('data-theme', '{name}');")
