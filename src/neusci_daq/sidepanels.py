"""The panels beside the Analysis whiteboard.

Left, a strip of buttons that fold panels out: Trials (the Trials browser), Groups (which
groups every chart shows and how they're coloured), Channels (the stimulus and analyzed
channels, gains, the stimulus fit) and Spikes (finding action potentials, and the units with
their colours). Right, the settings of the chart you picked: its Groups (the page's, or its
own), then its own settings. Pick several and the panel changes them together.
"""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from . import style
from .channels import ColorButton
from .viewkit import scroll_column

PANELS = (("trials", "Trials", "Every trial, in its group: pick the ones the charts show"),
          ("groups", "Groups", "Which groups every chart shows, and their colours"),
          ("channels", "Channels", "The stimulus and analyzed channels, amplifier gains and the stimulus fit"),
          ("spikes", "Spikes", "Find action potentials, and tick and colour the units"))


class Rail(QtWidgets.QWidget):
    """The strip of buttons; each folds its panel out, or back in."""
    picked = QtCore.Signal(str)               # a panel's key, or "" for none

    def __init__(self):
        super().__init__()
        self.setObjectName("rail")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(4)
        self.buttons: dict[str, QtWidgets.QToolButton] = {}
        for key, text, tip in PANELS:
            b = QtWidgets.QToolButton(text=text, checkable=True, autoRaise=True)
            b.setToolTip(tip)
            b.setMinimumWidth(64)
            b.clicked.connect(lambda on, k=key: self._clicked(k, on))
            lay.addWidget(b)
            self.buttons[key] = b
        lay.addStretch(1)

    def _clicked(self, key: str, on: bool) -> None:
        for k, b in self.buttons.items():
            if k != key:
                b.setChecked(False)
        self.picked.emit(key if on else "")

    def set_open(self, key: str) -> None:
        for k, b in self.buttons.items():
            b.setChecked(k == key)

    def restyle(self, mode: str) -> None:
        t = style.tokens(mode)
        self.setStyleSheet(f"#rail {{ background: {t['panel']}; border-right: 1px solid {t['line']}; }}"
                           f"#rail QToolButton {{ padding: 8px 4px; color: {t['ink']}; }}"
                           f"#rail QToolButton:checked {{ background: {t['selection']}; color: {t['focus']}; "
                           f"border-radius: 6px; }}")


class GroupColours(QtWidgets.QWidget):
    """Each group: ticked to show it everywhere, and its colour (click to change)."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(2)

    def fill(self) -> None:
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        page = self.page
        hidden = set(page._looks["hidden_groups"])
        if not page.shown:
            note = QtWidgets.QLabel("No groups yet: pick the stimulus channel in Channels.")
            note.setWordWrap(True)
            self.lay.addWidget(note)
            return
        for g, (name, blocks) in enumerate(page.shown):
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            box = QtWidgets.QCheckBox(name)
            box.setToolTip(f"Show {name} in every chart that follows the page ({len(blocks)} trial(s))")
            box.setChecked(name not in hidden)
            box.toggled.connect(lambda on, n=name: page.set_hidden(("page", n), not on))
            pick = ColorButton(page.group_color(g), lambda c, n=name: page.set_page_color(n, c),
                               f"Pick {name}'s colour, used in every chart")
            pick.setFixedWidth(34)
            h.addWidget(box, 1)
            h.addWidget(pick)
            self.lay.addWidget(row)


class ChartGroups(QtWidgets.QGroupBox):
    """The picked charts' groups: the page's, or ticked for these charts alone."""

    def __init__(self, page):
        super().__init__("Groups")
        self.page = page
        self.charts: list = []
        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.same = QtWidgets.QRadioButton("Same as the page")
        self.same.setToolTip("Show the groups ticked in Groups (left), and the trials picked in Trials")
        self.own = QtWidgets.QRadioButton("Choose")
        self.own.setToolTip("Show just the groups ticked here, whatever the page shows: say Group 3 here and "
                            "Group 5 on another chart, to compare")
        row.addWidget(self.same)
        row.addWidget(self.own)
        row.addStretch(1)
        lay.addLayout(row)
        self.mixed = QtWidgets.QLabel("Mixed: some of these follow the page, some choose their own.")
        self.mixed.setWordWrap(True)
        lay.addWidget(self.mixed)
        self.ticks = QtWidgets.QWidget()
        self._ticks = QtWidgets.QVBoxLayout(self.ticks)
        self._ticks.setContentsMargins(12, 0, 0, 0)
        self._ticks.setSpacing(1)
        lay.addWidget(self.ticks)
        self.same.toggled.connect(lambda on: self._mode(not on) if on else None)
        self.own.toggled.connect(lambda on: self._mode(True) if on else None)

    def show_for(self, charts: list) -> None:
        self.charts = [c for c in charts if c.uses_blocks]
        self.setVisible(bool(self.charts))
        if not self.charts:
            return
        own = [c.pin is not None for c in self.charts]
        for b, on in ((self.same, not any(own)), (self.own, all(own))):
            b.blockSignals(True)
            b.setAutoExclusive(False)
            b.setChecked(on)
            b.setAutoExclusive(True)
            b.blockSignals(False)
        self.mixed.setVisible(any(own) and not all(own))
        self._fill()

    def _fill(self) -> None:
        while self._ticks.count():
            w = self._ticks.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        own = all(c.pin is not None for c in self.charts)
        for g, (name, blocks) in enumerate(self.page.shown):
            box = QtWidgets.QCheckBox(name)
            box.setIcon(style.dot_icon(self.page.group_color(g)))
            ticked = [own and name in c.pin.get("pages", []) for c in self.charts] if own else \
                [name not in self.page._looks["hidden_groups"]] * len(self.charts)
            box.setTristate(False)
            box.setChecked(all(ticked))
            box.setEnabled(own)
            box.toggled.connect(lambda on, n=name: self._tick(n, on))
            self._ticks.addWidget(box)

    def _mode(self, own: bool) -> None:
        for c in self.charts:
            if own and c.pin is None:
                shown = [n for n, _ in self.page.shown if n not in self.page._looks["hidden_groups"]]
                c.set_own_groups(shown)
            elif not own and c.pin is not None:
                c.set_own_groups(None)
        self.show_for(self.charts)

    def _tick(self, name: str, on: bool) -> None:
        for c in self.charts:
            have = list(c.pin.get("pages", [])) if c.pin is not None else []
            names = [n for n, _ in self.page.shown if (n in have and n != name) or (n == name and on)]
            c.set_own_groups(names)


class SettingsPanel(QtWidgets.QWidget):
    """Beside the board, on the right: the picked chart's settings, or several charts'."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setObjectName("settingsPanel")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        self.heading = QtWidgets.QLabel()
        self.heading.setWordWrap(True)
        lay.addWidget(self.heading)
        self.groups = ChartGroups(page)
        lay.addWidget(self.groups)
        self.multi = QtWidgets.QWidget()
        m = QtWidgets.QVBoxLayout(self.multi)
        m.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.btn_size = QtWidgets.QPushButton("Same size")
        self.btn_size.setToolTip("Make them all the size of the first picked")
        self.btn_line = QtWidgets.QPushButton("Line up in a row")
        self.btn_line.setToolTip("Put them side by side, left to right, along the top one")
        row.addWidget(self.btn_size)
        row.addWidget(self.btn_line)
        m.addLayout(row)
        self.btn_remove = QtWidgets.QPushButton("Remove")
        m.addWidget(self.btn_remove)
        lay.addWidget(self.multi)
        lay.addWidget(page.canvas.options, 1)
        self.empty = QtWidgets.QLabel("Click a chart to see its settings here. Shift-click, or drag a box over "
                                      "empty space, to change several together.")
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)
        lay.addStretch(0)
        self.btn_size.clicked.connect(self._same_size)
        self.btn_line.clicked.connect(self._line_up)
        self.btn_remove.clicked.connect(page.canvas.close_picked)
        page.canvas.picked_changed.connect(self.update_for_picked)
        self.update_for_picked()

    def update_for_picked(self) -> None:
        charts = self.page.canvas.picked()
        n = len(charts)
        self.heading.setText(f"<b>{charts[0].name}</b>" if n == 1 else
                             f"<b>{n} charts picked</b><br>{', '.join(c.name for c in charts)}" if n else "")
        self.heading.setVisible(bool(n))
        self.groups.show_for(charts)
        self.multi.setVisible(n > 1)
        self.btn_remove.setText(f"Remove {n} charts")
        self.page.canvas.options.setVisible(n == 1)
        self.empty.setVisible(n == 0)

    def _same_size(self) -> None:
        charts = self.page.canvas.picked()
        if len(charts) > 1:
            size = charts[0].dock.place.size()
            for c in charts[1:]:
                c.dock.place.setSize(size)
            self.page.canvas.space.place_all()
            self.page.canvas.changed()

    def _line_up(self) -> None:
        charts = sorted(self.page.canvas.picked(), key=lambda c: c.dock.place.left())
        if len(charts) > 1:
            x, y = charts[0].dock.place.left(), min(c.dock.place.top() for c in charts)
            for c in charts:
                c.dock.place.moveTo(x, y)
                x += c.dock.place.width() + 2 * 20
            self.page.canvas.space.place_all()
            self.page.canvas.changed()

    def restyle(self, mode: str) -> None:
        t = style.tokens(mode)
        self.setStyleSheet(f"#settingsPanel {{ background: {t['panel']}; }}")


def drawer(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    """A panel as it folds out: scrolling up and down when it's taller than the window."""
    widget.setMaximumWidth(300)
    return scroll_column(widget)
