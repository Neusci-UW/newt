"""Show the Code: right-click anywhere in NEWT and the menu has "Show the Code…", which opens
a read-only viewer of the Python behind what was clicked. The viewer lists the classes of the
widget under the pointer (and of what holds it), and shows the file each is in, at that class.
"""
from __future__ import annotations

import ast
import inspect
import keyword
import re
import sys
import textwrap
from pathlib import Path

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

ENTRY = "Show the Code…"
_PACKAGE = __name__.split(".")[0]
_MATH = {f"{_PACKAGE}.{m}" for m in ("analysis", "dsp", "fitting")}      # where the arithmetic lives
MAX_MATH = 40                                  # functions listed for one chart
_E = QtCore.QEvent.Type


def classes_of(widget: QtWidgets.QWidget | None) -> list[tuple[str, str, int]]:
    """(class name, file, line) of NEWT's own classes behind a widget: its own first, then
    its bases, then what holds it, and so on up to the window."""
    found, seen = [], set()
    while widget is not None:
        for cls in type(widget).__mro__:
            if not cls.__module__.startswith(_PACKAGE) or cls in seen:
                continue
            seen.add(cls)
            try:
                found.append((cls.__name__, inspect.getsourcefile(cls), inspect.getsourcelines(cls)[1]))
            except (OSError, TypeError):
                pass
        widget = widget.parentWidget()
    return found


def _math_called(source: str, scope: dict) -> list:
    """The functions of the math modules that `source` names, in order: through a module
    (A.derivative) or imported straight (from .dsp import smooth)."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        fn = None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            mod = scope.get(node.value.id)
            fn = getattr(mod, node.attr, None) if inspect.ismodule(mod) and mod.__name__ in _MATH else None
        elif isinstance(node, ast.Name):
            fn = scope.get(node.id)
        if inspect.isfunction(fn) and fn.__module__ in _MATH and fn not in found:
            found.append(fn)
    return sorted(found, key=lambda f: (f.__module__, f.__code__.co_firstlineno))


def math_of(widget: QtWidgets.QWidget | None) -> list:
    """The math functions behind the chart that holds `widget`: everything its drawing calls
    in analysis, dsp and fitting, and what those call in turn."""
    from .chartbase import Chart
    while widget is not None and not isinstance(widget, Chart):
        widget = widget.parentWidget()
    if widget is None:
        return []
    found, queue = [], []
    for cls in type(widget).__mro__:
        if cls.__module__.startswith(_PACKAGE) and cls is not Chart:
            try:
                queue += _math_called(inspect.getsource(cls), vars(sys.modules[cls.__module__]))
            except (OSError, TypeError):
                pass
    while queue and len(found) < MAX_MATH:
        fn = queue.pop(0)
        if fn in found:
            continue
        found.append(fn)
        queue += _math_called(inspect.getsource(fn), vars(sys.modules[fn.__module__]))
    return found


class _Highlighter(QtGui.QSyntaxHighlighter):
    """Python, coloured plainly: keywords, strings, comments, numbers, and the names after def and class."""

    def __init__(self, doc: QtGui.QTextDocument):
        super().__init__(doc)
        dark = QtWidgets.QApplication.palette().color(QtGui.QPalette.ColorRole.Base).lightness() < 128

        def fmt(light: str, night: str, bold=False, italic=False) -> QtGui.QTextCharFormat:
            f = QtGui.QTextCharFormat()
            f.setForeground(QtGui.QColor(night if dark else light))
            if bold:
                f.setFontWeight(QtGui.QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f
        words = "|".join(keyword.kwlist)
        self.rules = [(re.compile(r"\b\d+(\.\d+)?\b"), fmt("#1c6e8c", "#79c0d9"), 0),
                      (re.compile(rf"\b({words}|self)\b"), fmt("#8a2be2", "#c792ea", True), 0),
                      (re.compile(r"\b(?:def|class)\s+(\w+)"), fmt("#0b6e4f", "#7fd6a8", True), 1),
                      (re.compile(r"\"[^\"\\\n]*(\\.[^\"\\\n]*)*\"|'[^'\\\n]*(\\.[^'\\\n]*)*'"),
                       fmt("#b5532a", "#e6a37c"), 0),
                      (re.compile(r"#[^\n]*"), fmt("#7a7a7a", "#8b949e", italic=True), 0)]

    def highlightBlock(self, text: str) -> None:
        for rx, f, group in self.rules:
            for m in rx.finditer(text):
                self.setFormat(m.start(group), m.end(group) - m.start(group), f)


class _Gutter(QtWidgets.QWidget):
    def __init__(self, edit: "_Source"):
        super().__init__(edit)
        self.edit = edit

    def sizeHint(self):
        return QtCore.QSize(self.edit.gutter_width(), 0)

    def paintEvent(self, ev):
        self.edit.paint_gutter(ev)


class _Source(QtWidgets.QPlainTextEdit):
    """Read-only code with line numbers."""

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(12)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.numbers = True
        self.gutter = _Gutter(self)
        self.blockCountChanged.connect(self._room)
        self.updateRequest.connect(self._scrolled)
        self._room()
        _Highlighter(self.document())

    def set_numbers(self, on: bool) -> None:
        """Line numbers, unless what's shown is pieces of several files."""
        self.numbers = on
        self.gutter.setVisible(on)
        self._room()

    def gutter_width(self) -> int:
        if not self.numbers:
            return 0
        return 14 + self.fontMetrics().horizontalAdvance("9") * len(str(max(1, self.blockCount())))

    def _room(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _scrolled(self, rect, dy) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._room()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        r = self.contentsRect()
        self.gutter.setGeometry(QtCore.QRect(r.left(), r.top(), self.gutter_width(), r.height()))

    def paint_gutter(self, ev) -> None:
        p = QtGui.QPainter(self.gutter)
        pal = self.palette()
        p.fillRect(ev.rect(), pal.color(QtGui.QPalette.ColorRole.AlternateBase))
        p.setPen(pal.color(QtGui.QPalette.ColorRole.PlaceholderText))
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= ev.rect().bottom():
            h = round(self.blockBoundingRect(block).height())
            if block.isVisible() and top + h >= ev.rect().top():
                p.drawText(0, top, self.gutter.width() - 6, self.fontMetrics().height(),
                           QtCore.Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            block, top = block.next(), top + h

    def go_to(self, line: int) -> None:
        """Put `line` near the top, so the class opens in view."""
        block = self.document().findBlockByLineNumber(max(0, line - 1))
        cursor = QtGui.QTextCursor(block)
        self.setTextCursor(cursor)
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())                  # scroll down first, so ensureCursorVisible lands it at the top
        self.ensureCursorVisible()
        cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)
        self.setExtraSelections([self._mark(cursor)])

    def _mark(self, cursor) -> QtWidgets.QTextEdit.ExtraSelection:
        sel = QtWidgets.QTextEdit.ExtraSelection()
        color = self.palette().color(QtGui.QPalette.ColorRole.Highlight)
        color.setAlpha(60)
        sel.format.setBackground(color)
        sel.cursor = cursor
        return sel


class CodeViewer(QtWidgets.QDialog):
    """The Python behind a widget: its classes on the left, their file on the right."""

    def __init__(self, widget: QtWidgets.QWidget | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("The code")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1000, 680)
        self.rows: list = []                         # per list row: None for a heading, else (path, line, text)
        self._files: dict[str, str] = {}
        self._shown: str | None = None
        row = QtWidgets.QHBoxLayout(self)
        split = QtWidgets.QSplitter()
        row.addWidget(split)
        left = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(left)
        col.setContentsMargins(0, 0, 0, 0)
        note = QtWidgets.QLabel("The math is the arithmetic that builds the chart; the chart's code arranges and draws "
                                "it. Read only.")
        note.setWordWrap(True)
        col.addWidget(note)
        self.listing = QtWidgets.QListWidget()
        col.addWidget(self.listing)
        self.source = _Source()
        split.addWidget(left)
        split.addWidget(self.source)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 740])
        self._fill(widget)
        self.listing.currentRowChanged.connect(self._pick)
        first = next((i for i, r in enumerate(self.rows) if r is not None), None)
        if first is not None:
            self.listing.setCurrentRow(first)
        else:
            self.source.setPlainText("There's no NEWT code behind what you clicked.")

    def _heading(self, text: str) -> None:
        item = QtWidgets.QListWidgetItem(text)
        item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        self.listing.addItem(item)
        self.rows.append(None)

    def _add(self, label: str, path: str, line: int, text: str | None = None) -> None:
        item = QtWidgets.QListWidgetItem(label)
        item.setToolTip(path)
        self.listing.addItem(item)
        self.rows.append((path, line, text))

    def _fill(self, widget) -> None:
        math = math_of(widget)
        if math:
            self._heading("The math behind this chart")
            together = "\n\n".join(f"# {Path(f.__code__.co_filename).name}:{f.__code__.co_firstlineno}\n"
                                    + inspect.getsource(f) for f in math)
            self._add("All of it, together", "", 0, together)
            for f in math:
                self._add(f"{f.__name__}  ({f.__module__.split('.')[-1]}.py)", f.__code__.co_filename,
                          f.__code__.co_firstlineno)
        code = classes_of(widget)
        if code:
            self._heading("The code that draws it")
            for name, path, line in code:
                self._add(f"{name}  ({Path(path).name}:{line})", path, line)

    def _pick(self, row: int) -> None:
        if not 0 <= row < len(self.rows) or self.rows[row] is None:
            return
        path, line, text = self.rows[row]
        self.source.set_numbers(text is None)
        if text is not None:                         # several functions in one document
            self._shown = None
            self.source.setPlainText(text)
            return
        if path != self._shown:
            if path not in self._files:
                self._files[path] = Path(path).read_text(encoding="utf-8", errors="replace")
            self.source.setPlainText(self._files[path])
            self._shown = path
        self.source.go_to(line)


class ShowTheCode(QtCore.QObject):
    """Adds Show the Code… to every right-click menu, and gives a menu of its own to a
    right-click that has none. Install once, on the application."""

    def __init__(self, app: QtWidgets.QApplication):
        super().__init__(app)
        self.app = app
        self._widget: QtWidgets.QWidget | None = None
        self._pending = False
        self._turn = 0
        self._viewers: list[CodeViewer] = []
        app.installEventFilter(self)

    def eventFilter(self, obj, ev):
        kind = ev.type()
        if kind == _E.ContextMenu:
            self._note(fallback=True)
        elif kind in (_E.MouseButtonPress, _E.MouseButtonRelease) and ev.button() == QtCore.Qt.MouseButton.RightButton:
            self._note()                                 # pyqtgraph's menus come from the mouse, not ContextMenu
        elif kind == _E.Show and self._pending and isinstance(obj, QtWidgets.QMenu) \
                and not obj.property("codeShown") and obj.isWindow():
            self._add_to(obj)
        return False

    def _note(self, fallback: bool = False) -> None:
        """A right-click is being handled: the menus that open until it's done get the entry."""
        if not self._pending:
            self._pending = True
            self._widget = self.app.widgetAt(QtGui.QCursor.pos())
            self._turn += 1
            turn = self._turn
            QtCore.QTimer.singleShot(0, lambda: self._done(turn, fallback))
        elif fallback:
            QtCore.QTimer.singleShot(0, self._lonely)

    def _done(self, turn: int, fallback: bool) -> None:
        if turn == self._turn:
            self._pending = False
        if fallback:
            self._lonely()

    def _lonely(self) -> None:
        """No menu opened for this right-click: offer just Show the Code…"""
        if self.app.activePopupWidget() is None and self._widget is not None:
            menu = QtWidgets.QMenu()
            menu.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
            self._add_to(menu, force=True)
            menu.popup(QtGui.QCursor.pos())

    def _add_to(self, menu: QtWidgets.QMenu, force: bool = False) -> None:
        if self._widget is None and not force:
            return
        menu.setProperty("codeShown", True)
        widget = self._widget
        if menu.actions() and not menu.actions()[-1].isSeparator():
            menu.addSeparator()
        menu.addAction(ENTRY).triggered.connect(lambda: self.open(widget))
        menu.adjustSize()

    def open(self, widget) -> None:
        try:
            widget.objectName()                           # gone since the menu opened?
        except RuntimeError:
            widget = None
        viewer = CodeViewer(widget, self.app.activeWindow())
        viewer.setWindowFlag(QtCore.Qt.WindowType.Window)
        viewer.destroyed.connect(lambda *_: self._viewers.remove(viewer) if viewer in self._viewers else None)
        self._viewers.append(viewer)
        viewer.show()
