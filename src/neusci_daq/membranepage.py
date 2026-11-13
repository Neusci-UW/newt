"""The Membrane potential tab: an interactive particle simulation of ions crossing a
membrane, with the Nernst and GHK equations, a charge-density view and a GHK calculator.
The RC circuit tab uses the same class to show rc_membrane.html: a membrane's
equivalent circuit charging, then the same thing with ions when Na⁺ channels open.

The pages live in the Neusci-UW/lessons repo, a git submodule at lessons/ (clone Newt with
--recurse-submodules, or run `git submodule update --init`). The page itself is
lessons/membrane_potential.html (plain HTML and JavaScript). It is shown
in a QWebEngineView, which needs the optional PyQt6-WebEngine package:

    pip install -e ".[web]"

Without it the tab explains that and offers to open the page in the web browser instead.
The equations use MathJax: lessons/vendor/mathjax/tex-svg.js if present (see
lessons/tools/fetch_mathjax.py, for computers without internet), otherwise the online copy.
"""
from __future__ import annotations

import re
from pathlib import Path

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import style

# The lesson pages: the Neusci-UW/lessons submodule
LESSON_PAGES = Path(__file__).with_name("lessons")
PAGE = LESSON_PAGES / "membrane_potential.html"
# The RC circuit page: a capacitor charging, then the same thing done by ions
RC_PAGE = LESSON_PAGES / "rc_membrane.html"
# The action potential page: a spike travelling along an axon
AP_PAGE = LESSON_PAGES / "action_potential.html"

# QtWebEngine has to be imported before the QApplication exists. gui.py imports this
# module at the top, before run() makes the app, so importing it here is early enough.
try:
    from PyQt6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebChannel import QWebChannel
except Exception:   # not installed, or imported after the QApplication (as in some tests)
    QWebEngineView = None
    QWebEngineSettings = None
    QWebEngineScript = None
    QWebChannel = None

# Lessons: questions placed on a simulation page and parts of it locked until they are
# answered (lessons/lesson.js). Each lesson is a file in lessons/questions; the page's lesson
# editor saves into it.
LESSONS = LESSON_PAGES / "questions"
_LESSON_NAME = re.compile(r"[a-z0-9_-]{1,64}")


class LessonBridge(QtCore.QObject):
    """What a page's lesson editor can call in Argus (through Qt's web channel): saving a
    lesson file into the lessons folder, and saying when the editor opens or closes."""

    editing = QtCore.pyqtSignal(bool)

    @QtCore.pyqtSlot(bool)
    def setEditing(self, on: bool) -> None:
        self.editing.emit(on)

    @QtCore.pyqtSlot(str, str, result=str)
    def save(self, name: str, text: str) -> str:
        """Write lessons/<name>.js. Returns "" when saved, otherwise what went wrong."""
        if not _LESSON_NAME.fullmatch(name):
            return "the file name can only use lower-case letters, digits, - and _"
        if "ArgusLesson.define(" not in text:
            return "that isn't a lesson file"
        try:
            LESSONS.mkdir(exist_ok=True)
            (LESSONS / f"{name}.js").write_text(text, encoding="utf-8")
        except OSError as e:
            return str(e)
        return ""


def available() -> bool:
    """True if the page can be shown inside the app (PyQt6-WebEngine is installed)."""
    return QWebEngineView is not None


class MembranePage(QtWidgets.QWidget):
    def __init__(self, main=None, page: Path = PAGE, what: str = "membrane potential"):
        super().__init__()
        self.main = main
        self.page = page
        self.what = what      # names the page in the messages shown without WebEngine
        self._mode = style.LIGHT
        self._loaded = False
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self.view = None
        if not (available() and self.page.exists()):
            self._lay.addWidget(self._fallback())
        # Otherwise the web view is made the first time the tab is shown: starting
        # Chromium takes a moment and memory, so it waits until someone opens the page.

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.view is None and available() and self.page.exists():
            self._make_view()

    def _make_view(self) -> None:
        self.view = QWebEngineView(self)
        s = self.view.settings()
        # The page is a local file; let it fetch fonts and MathJax online when it has to.
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.view.loadFinished.connect(self._on_loaded)
        self._connect_lessons()
        self.view.setUrl(QtCore.QUrl.fromLocalFile(str(self.page)))
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_refresh.setToolTip("Reload the simulation from the start, with every setting back to its default")
        self.btn_refresh.clicked.connect(self.view.reload)
        # Shown once the page turns out to have lessons (it loads lessons/lesson.js)
        self.btn_lesson = QtWidgets.QPushButton("Edit lesson")
        self.btn_lesson.setCheckable(True)
        self.btn_lesson.setToolTip("Write or change this page's lesson: right-click the page to insert a question "
                                   "or to make a part of it a region that stays locked until questions are answered")
        self.btn_lesson.toggled.connect(self._edit_lesson)
        self.btn_lesson.hide()
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(8, 6, 8, 0)
        row.addStretch(1)
        row.addWidget(self.btn_lesson)
        row.addWidget(self.btn_refresh)
        self._lay.addLayout(row)
        self._lay.addWidget(self.view, 1)

    def _fallback(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(40, 40, 40, 40)
        v.addStretch(1)
        if not self.page.exists():
            text = f"The {self.what} page is missing:\n{self.page}"
        else:
            text = (f"The {self.what} simulation runs as a web page. To show it here, "
                    "install PyQt6-WebEngine:\n\n    pip install -e \".[web]\"\n\n"
                    "Then restart the app. Until then you can open it in your web browser.")
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(label)
        if self.page.exists():
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
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.page)))

    def _connect_lessons(self) -> None:
        """Give the page's lesson editor a way to save lesson files (Qt's web channel)."""
        if QWebChannel is None:
            return
        script = QtCore.QFile(":/qtwebchannel/qwebchannel.js")   # the page side of the channel, built into Qt
        if not script.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
            return
        source = bytes(script.readAll()).decode("utf-8")
        script.close()
        inject = QWebEngineScript()
        inject.setName("qwebchannel")
        inject.setSourceCode(source)
        inject.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        inject.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        self.view.page().scripts().insert(inject)
        self._bridge = LessonBridge(self)
        self._bridge.editing.connect(self._show_editing)
        self._channel = QWebChannel(self.view.page())
        self._channel.registerObject("argusLessons", self._bridge)
        self.view.page().setWebChannel(self._channel)

    def _edit_lesson(self, on: bool) -> None:
        if self.view is not None and self._loaded:
            self.view.page().runJavaScript(f"window.ArgusLesson && ArgusLesson.setEdit({'true' if on else 'false'});")

    def _show_editing(self, on: bool) -> None:
        """The page opened or closed its lesson editor itself: keep the button in step."""
        self.btn_lesson.blockSignals(True)
        self.btn_lesson.setChecked(on)
        self.btn_lesson.blockSignals(False)

    def _on_loaded(self, ok: bool) -> None:
        self._loaded = ok
        self._push_theme()
        # A reload starts outside the editor; offer the button only on pages that have lessons
        self._show_editing(False)
        if ok:
            self.view.page().runJavaScript("!!window.ArgusLesson", lambda has: self.btn_lesson.setVisible(bool(has)))

    def apply_theme(self, mode: str) -> None:
        """Follow the app's light/dark mode (the page reads data-theme on its root)."""
        self._mode = mode
        self._push_theme()

    def _push_theme(self) -> None:
        if self.view is None or not self._loaded:
            return
        name = "dark" if self._mode == style.DARK else "light"
        self.view.page().runJavaScript(f"document.documentElement.setAttribute('data-theme', '{name}');")
