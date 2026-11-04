"""Tabs across the top (Acquisition, Analysis, Simulation), some with sub-pages
under them (Acquisition: Record, Stimulator, Trials), and an optional split: two
pages side by side or one above the other.

A page is one widget, so it can only be in one half at a time. Any page can go in
either half, two sub-pages of the same tab included. Picking the page that's
already open in the other half goes back to one page, showing it full screen.
Picking a tab shows the sub-page last used under it (one the other half isn't
showing, if there is one).
"""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

ONE, SIDE, STACKED = "off", "h", "v"


def _tab_bar(name: str = "") -> QtWidgets.QTabBar:
    bar = QtWidgets.QTabBar()
    if name:
        bar.setObjectName(name)
    bar.setDrawBase(False)
    bar.setExpanding(False)
    # No ◀ ▶ scroll arrows when the tabs don't fit; shorten the names ("Analy…") instead.
    bar.setUsesScrollButtons(False)
    bar.setElideMode(QtCore.Qt.TextElideMode.ElideRight)
    bar.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
    return bar


class _Pane(QtWidgets.QWidget):
    picked = QtCore.Signal(int, str)        # pane index, page id
    group_picked = QtCore.Signal(int, str)  # pane index, tab (group) id

    def __init__(self, index: int, groups: list[tuple[str, str, list[str]]], names: dict[str, str]):
        super().__init__()
        self.index, self.groups, self.names = index, groups, names
        self.group_of = {pid: gid for gid, _, kids in groups for pid in kids}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QtWidgets.QWidget()
        head.setObjectName("paneHead")
        head.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        hl = QtWidgets.QHBoxLayout(head)
        hl.setContentsMargins(8, 0, 8, 0)
        self.bar = _tab_bar()
        for _, gname, _ in groups:
            self.bar.addTab(gname)
        self.bar.tabBarClicked.connect(lambda k: k >= 0 and self.group_picked.emit(self.index, self.groups[k][0]))
        hl.addWidget(self.bar)
        hl.addStretch(1)
        self.head_layout = hl
        lay.addWidget(head)
        # The sub-pages of the tab that's open, when it has more than one.
        self.sub_head = QtWidgets.QWidget()
        self.sub_head.setObjectName("subHead")
        self.sub_head.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        sl = QtWidgets.QHBoxLayout(self.sub_head)
        sl.setContentsMargins(8, 0, 8, 0)
        self.sub_bar = _tab_bar("subTabs")
        self.sub_bar.tabBarClicked.connect(self._sub_clicked)
        sl.addWidget(self.sub_bar)
        sl.addStretch(1)
        self.sub_head.hide()
        lay.addWidget(self.sub_head)
        self._sub_ids: list[str] = []
        self.body = QtWidgets.QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self.body, 1)
        self.page_id: str | None = None

    def _sub_clicked(self, k: int) -> None:
        if 0 <= k < len(self._sub_ids):
            self.picked.emit(self.index, self._sub_ids[k])

    def _set_heads(self, pid: str) -> None:
        gid = self.group_of[pid]
        gi = next(i for i, g in enumerate(self.groups) if g[0] == gid)
        kids = self.groups[gi][2]
        self.bar.blockSignals(True)
        self.bar.setCurrentIndex(gi)
        self.bar.blockSignals(False)
        self.sub_bar.blockSignals(True)
        if kids != self._sub_ids:
            while self.sub_bar.count():
                self.sub_bar.removeTab(0)
            for k in kids:
                self.sub_bar.addTab(self.names[k])
            self._sub_ids = list(kids)
        self.sub_bar.setCurrentIndex(kids.index(pid))
        self.sub_bar.blockSignals(False)
        self.sub_head.setVisible(len(kids) > 1)

    def show_page(self, pid: str, widget: QtWidgets.QWidget):
        self._set_heads(pid)
        if self.page_id == pid and widget.parent() is self:
            return
        while self.body.count():
            w = self.body.takeAt(0).widget()
            if w is not None:
                w.hide()
        self.body.addWidget(widget)
        widget.show()
        self.page_id = pid


class TabArea(QtWidgets.QWidget):
    """The pages under the tab bars. `changed` fires whenever what's visible changes.

    `pages` are the pages themselves, [(id, name, widget)]. `groups` puts them under
    tabs, [(tab id, tab name, [page ids])]; without it every page is its own tab."""

    changed = QtCore.Signal()

    def __init__(self, pages: list[tuple[str, str, QtWidgets.QWidget]],
                 groups: list[tuple[str, str, list[str]]] | None = None):
        super().__init__()
        self.ids = [pid for pid, _, _ in pages]
        self.names = {pid: name for pid, name, _ in pages}
        self.pages = {pid: w for pid, _, w in pages}
        if groups is None:
            groups = [(pid, name, [pid]) for pid, name, _ in pages]
        groups = [(g, n, [k for k in kids if k in self.pages]) for g, n, kids in groups]
        self.groups = [g for g in groups if g[2]]
        grouped = [k for _, _, kids in self.groups for k in kids]
        if sorted(grouped) != sorted(self.ids):
            raise ValueError("every page must be under exactly one tab")
        self.group_of = {pid: gid for gid, _, kids in self.groups for pid in kids}
        self.last = {gid: kids[0] for gid, _, kids in self.groups}   # last sub-page used, per tab
        self.layout_mode = ONE
        self.shown = [self.ids[0], self._second(self.ids[0])]           # Record | Stimulator
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(7)
        self.panes = [_Pane(i, self.groups, self.names) for i in range(2)]
        for p in self.panes:
            p.picked.connect(self.pick)
            p.group_picked.connect(self.pick_group)
            self.splitter.addWidget(p)
        lay.addWidget(self.splitter)
        self._hold = QtWidgets.QWidget()          # parks pages that aren't shown
        self._hold.hide()
        for w in self.pages.values():
            w.setParent(self._hold)
        self._place()

    def _second(self, first: str) -> str:
        """What a new split puts in the other half when both would show the same page:
        the first other page (Record and Stimulator side by side, by default)."""
        return next(i for i in self.ids if i != first)

    # -- what's visible --------------------------------------------------------
    @property
    def split(self) -> bool:
        return self.layout_mode != ONE

    def visible(self) -> list[str]:
        return self.shown[:2] if self.split else self.shown[:1]

    def is_visible(self, pid: str) -> bool:
        return pid in self.visible()

    def shares_screen(self, pid: str) -> bool:
        """True if `pid` is showing in one half of a split (not full screen)."""
        return self.split and self.is_visible(pid)

    # -- changing it -------------------------------------------------------------
    def pick(self, pane: int, pid: str) -> None:
        """A page was picked in a pane (a sub-page, or a tab with no sub-pages)."""
        if self.split and self.shown[1 - pane] == pid:
            # Already open in the other half: show it full screen instead.
            other = self.shown[pane]
            self.shown = [pid, other]
            self.layout_mode = ONE
        else:
            self.shown[pane] = pid
        self._place()

    def pick_group(self, pane: int, gid: str) -> None:
        """A tab was clicked: show its last-used sub-page, or another of its sub-pages
        if that one is in the other half."""
        kids = next(k for g, _, k in self.groups if g == gid)
        if self.shown[pane] in kids and self.is_visible(self.shown[pane]):
            return                                   # already on this tab
        order = [self.last[gid]] + [k for k in kids if k != self.last[gid]]
        if self.split:
            free = [k for k in order if k != self.shown[1 - pane]]
            if free:
                order = free
        self.pick(pane, order[0])

    def set_layout(self, mode: str) -> None:
        if mode not in (ONE, SIDE, STACKED):
            raise ValueError(mode)
        if mode != ONE and self.shown[0] == self.shown[1]:
            self.shown[1] = self._second(self.shown[0])
        self.layout_mode = mode
        self._place()

    def show_tab(self, pid: str) -> None:
        """Make page `pid` visible (used by shortcuts and menu items)."""
        if self.is_visible(pid):
            return
        if self.split:
            self.shown[1] = pid
        else:
            self.shown[0] = pid
        self._place()

    def _place(self):
        self.splitter.setOrientation(QtCore.Qt.Orientation.Vertical if self.layout_mode == STACKED
                                     else QtCore.Qt.Orientation.Horizontal)
        vis = self.visible()
        for pid in vis:
            self.last[self.group_of[pid]] = pid
        for pid, w in self.pages.items():
            if pid not in vis:
                w.hide()
                w.setParent(self._hold)
        for i, pane in enumerate(self.panes):
            if i < len(vis):
                pane.show_page(vis[i], self.pages[vis[i]])
                pane.show()
            else:
                pane.hide()
                pane.page_id = None
        if self.split:
            total = max(2, sum(self.splitter.sizes()) or 2)
            self.splitter.setSizes([total // 2, total - total // 2])
        self.changed.emit()


def layout_icon(mode: str, ink: str, size: int = 16) -> QtGui.QIcon:
    """One box (one tab), a box split left/right, or a box split top/bottom."""
    scale = 2.0
    pix = QtGui.QPixmap(int(size * scale), int(size * scale))
    pix.setDevicePixelRatio(scale)
    pix.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setPen(QtGui.QPen(QtGui.QColor(ink), 1.3))
    r = QtCore.QRectF(1.5, 2.5, size - 3, size - 5)
    p.drawRoundedRect(r, 1.5, 1.5)
    if mode == SIDE:
        p.drawLine(QtCore.QPointF(r.center().x(), r.top()), QtCore.QPointF(r.center().x(), r.bottom()))
    elif mode == STACKED:
        p.drawLine(QtCore.QPointF(r.left(), r.center().y()), QtCore.QPointF(r.right(), r.center().y()))
    p.end()
    return QtGui.QIcon(pix)
