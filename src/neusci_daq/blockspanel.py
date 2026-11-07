"""The Blocks panel: the stimulus channel, Reset pages, and a tree of Live (or Back to
Live) and the session's blocks on their pages. Acquisition has one to look at blocks;
Analysis has one to pick the blocks to analyze (`for_analysis`), with its settings.

The panel only shows things and reports what's clicked; the main window decides what
the pages are and what clicking does. Clicks, selections and menu choices are acted on
once Qt has finished with the event: acting on them inside it rebuilds the tree while
Qt is still using the clicked item, which crashes.
"""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from . import style

LIVE, BACK = "live", "back"
ROLE = QtCore.Qt.ItemDataRole.UserRole


class BlocksPanel(QtWidgets.QWidget):
    activated = QtCore.Signal(object)           # LIVE, BACK or ("block", i)
    selection = QtCore.Signal(list)              # the block indices selected (a page: all its blocks)
    rename_requested = QtCore.Signal(str)        # a page's name
    move_requested = QtCore.Signal(int, str)     # block, page ("" asks for a new page's name)
    stim_channel_picked = QtCore.Signal(str)     # "" for None
    reset_requested = QtCore.Signal()
    color_requested = QtCore.Signal(str)         # a page's name: pick its colour
    color_cleared = QtCore.Signal(str)           # a page's name: back to the default colour

    def __init__(self, for_analysis: bool = False):
        super().__init__()
        self.for_analysis = for_analysis
        self.setObjectName("blocksPanel")
        if for_analysis:      # 210 when there's room, down to 170 on a small laptop
            self.setMinimumWidth(170)
            self.setMaximumWidth(210)
        else:
            self.setFixedWidth(230)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        # The stimulus channel (also on the Analysis tab): it sorts the blocks into pages.
        lay.addWidget(QtWidgets.QLabel("Stimulus channel"))
        self.stim_ch = QtWidgets.QComboBox()
        self._stim_tip = ("The channel that recorded the stimulus, set any time: blocks with the same "
                          "stimulus go on one page. With None, the notes of each stimulus are used. "
                          "Remembered for this session (and the same setting as on Analysis)")
        self.stim_ch.activated.connect(lambda *_: self.stim_channel_picked.emit(self.stim_ch.currentData() or ""))
        lay.addWidget(self.stim_ch)
        self.btn_reset = QtWidgets.QPushButton("Reset pages")
        self.btn_reset.setToolTip("Forget the page names and moved blocks, and sort the blocks into "
                                  "pages again from the stimulus channel")
        self.btn_reset.clicked.connect(lambda: self.later(self.reset_requested.emit))
        lay.addWidget(self.btn_reset)
        self.settings = QtWidgets.QFormLayout()  # what a page adds with add_setting
        self.settings.setContentsMargins(0, 0, 0, 0)
        if for_analysis:      # all showing when there's room; scrolls on a small laptop
            holder = QtWidgets.QWidget()
            holder.setLayout(self.settings)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidget(holder)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setMinimumHeight(100)
            lay.addWidget(scroll)
        else:
            lay.addLayout(self.settings)
        lay.addSpacing(6)
        self.title = QtWidgets.QLabel("<b>Blocks</b>")
        lay.addWidget(self.title)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.setMinimumHeight(60)           # its settings above still fit a small laptop screen
        # Cmd/Shift-click several blocks (or pick a page) to overlay them
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(lambda item, *_: self.later(lambda d=item.data(0, ROLE): self.activated.emit(d)))
        self.tree.itemDoubleClicked.connect(lambda item, *_: self.later(lambda d=item.data(0, ROLE): self._double(d)))
        self.tree.itemSelectionChanged.connect(
            lambda: self.later(lambda picked=self.selected_blocks(): self.selection.emit(picked)))
        lay.addWidget(self.tree, 1)
        self.btn_all = QtWidgets.QPushButton("Analyze all blocks")
        self.btn_all.setToolTip("Clear the selection: every block is analyzed")
        self.btn_all.setVisible(for_analysis)
        lay.addWidget(self.btn_all)
        self._pages: list[tuple[str, list[int]]] = []
        self._home = lambda i: None
        self._queue: list = []
        self._timer = QtCore.QTimer(self, singleShot=True, interval=0)
        self._timer.timeout.connect(self._run)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(self.maximumWidth(), super().sizeHint().height()) if self.for_analysis \
            else super().sizeHint()

    def add_setting(self, label: str, widget: QtWidgets.QWidget) -> None:
        self.settings.addRow(label, widget)

    # -- acting once Qt is done with the event ---------------------------------------------
    def later(self, fn) -> None:
        """Run fn once Qt is done with the event in hand (the timer goes with the panel)."""
        self._queue.append(fn)
        self._timer.start()

    def _run(self) -> None:
        q, self._queue = self._queue, []
        for fn in q:
            fn()

    # -- showing -----------------------------------------------------------------------------
    def set_stim_channels(self, names: list[str], current: str, enabled: bool) -> None:
        self.stim_ch.blockSignals(True)
        self.stim_ch.clear()
        self.stim_ch.addItem("None (use the notes)", "")
        for n in names:
            self.stim_ch.addItem(n, n)
        self.stim_ch.setCurrentIndex(max(0, self.stim_ch.findData(current)))
        self.stim_ch.setEnabled(enabled)
        self.stim_ch.setToolTip(self._stim_tip if enabled else
                                "Record a block first: the setting belongs to the session")
        self.stim_ch.blockSignals(False)

    def populate(self, top: str | None, pages: list[tuple[str, list[int]]], colors: list[str],
                 labels: dict[int, tuple[str, str]], home, can_reset: bool, busy: bool) -> None:
        """top: LIVE, BACK or None; pages: [(name, block indices)]; labels: block ->
        (file stem, duration text); home(i): block i's stimulus page."""
        self._pages, self._home = pages, home
        tree = self.tree
        tree.blockSignals(True)
        tree.clear()

        def bold(item):
            f = item.font(0)
            f.setBold(True)
            item.setFont(0, f)
            return item

        if top is not None:
            item = bold(QtWidgets.QTreeWidgetItem(["● Live" if top == LIVE else "● Back to Live"]))
            item.setData(0, ROLE, top)
            tree.addTopLevelItem(item)
        n_blocks = 0
        for g, (name, blocks) in enumerate(pages):
            head = bold(QtWidgets.QTreeWidgetItem([f"{name}  ({len(blocks)})"]))
            head.setData(0, ROLE, ("page", name))
            head.setIcon(0, style.dot_icon(colors[g % len(colors)]))
            head.setToolTip(0, "Click to see this page's average; double-click to rename it; right-click "
                               "to change its colour" if self.for_analysis else
                            "Double-click to rename this page; its blocks take the new name")
            tree.addTopLevelItem(head)
            for k, i in enumerate(blocks, 1):
                stem, dur = labels.get(i, (f"block {i + 1}", "?"))
                item = QtWidgets.QTreeWidgetItem([f"{name} {k}   ({stem})"])
                item.setData(0, ROLE, ("block", i))
                item.setToolTip(0, f"{stem}, {dur}: click or drag across blocks to analyze just those; "
                                   "Cmd/Shift-click to add more; right-click to move it to another page"
                                if self.for_analysis else
                                f"{stem}, {dur}: click to look at it; Cmd/Shift-click several to overlay "
                                "them; right-click to move it to another page")
                head.addChild(item)
            head.setExpanded(True)
            n_blocks += len(blocks)
        self.title.setText(f"<b>Blocks</b> ({n_blocks})" + (f" on {len(pages)} pages" if len(pages) > 1 else ""))
        self.btn_reset.setEnabled(can_reset)
        tree.blockSignals(False)
        tree.setEnabled(not busy)
        tree.setToolTip("Pick a page to see its average, or click or drag across blocks to see those trials"
                        if self.for_analysis
                        else "Stop recording to look at other blocks" if busy else
                        "Click a block to look at it (this stops acquiring). Live goes back, and Record "
                        "carries on adding to the same session")

    def items(self):
        """Every item, pages and blocks, top to bottom."""
        for r in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(r)
            yield top
            for c in range(top.childCount()):
                yield top.child(c)

    def stops(self) -> list:
        """What ↑ ↓ step through: Live / Back to Live and the blocks, in order."""
        return [it for it in self.items() if it.data(0, ROLE) in (LIVE, BACK)
                or (isinstance(it.data(0, ROLE), tuple) and it.data(0, ROLE)[0] == "block")]

    def mark(self, want, overlay: list[int]) -> None:
        """Make `want` (LIVE or ("block", i)) current; select the overlaid blocks, if any."""
        tree = self.tree
        tree.blockSignals(True)
        for item in self.items():
            data = item.data(0, ROLE)
            if data == want:
                if overlay:                      # don't disturb a multiple selection
                    tree.setCurrentItem(item, 0, QtCore.QItemSelectionModel.SelectionFlag.NoUpdate)
                else:
                    tree.setCurrentItem(item)
                tree.scrollToItem(item)
            if isinstance(data, tuple) and data[0] == "block":
                item.setSelected(data[1] in overlay or (not overlay and data == want))
        tree.blockSignals(False)

    def select(self, blocks: list[int], pages: list[str] = ()) -> None:
        """Select just these blocks and pages, without reporting it."""
        self.tree.blockSignals(True)
        for item in self.items():
            data = item.data(0, ROLE)
            item.setSelected(isinstance(data, tuple) and
                             (data[1] in pages if data[0] == "page" else
                              data[0] == "block" and data[1] in blocks))
        self.tree.blockSignals(False)

    def selected_blocks(self) -> list[int]:
        """The blocks selected; a selected page counts as all its blocks."""
        picked = set()
        for item in self.tree.selectedItems():
            data = item.data(0, ROLE)
            if isinstance(data, tuple) and data[0] == "block":
                picked.add(int(data[1]))
            elif isinstance(data, tuple) and data[0] == "page":
                picked.update(int(item.child(c).data(0, ROLE)[1]) for c in range(item.childCount()))
        return sorted(picked)

    def selected_pages(self) -> list[str]:
        """The names of the pages selected (not those with just some blocks selected)."""
        return [item.data(0, ROLE)[1] for item in self.tree.selectedItems()
                if isinstance(item.data(0, ROLE), tuple) and item.data(0, ROLE)[0] == "page"]

    # -- page actions --------------------------------------------------------------------------
    def _double(self, data) -> None:
        if isinstance(data, tuple) and data[0] == "page":
            self.rename_requested.emit(data[1])

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        data = item.data(0, ROLE) if item else None
        if not isinstance(data, tuple) or not self.tree.isEnabled():
            return
        menu = QtWidgets.QMenu(self)
        if data[0] == "page":
            menu.addAction("Rename page…", lambda: self.later(lambda: self.rename_requested.emit(data[1])))
            menu.addAction("Colour…", lambda: self.later(lambda: self.color_requested.emit(data[1])))
            menu.addAction("Default colour", lambda: self.later(lambda: self.color_cleared.emit(data[1])))
        else:
            i = int(data[1])
            here = next((name for name, blocks in self._pages if i in blocks), None)
            to = menu.addMenu("Move to page")
            for name, _ in self._pages:
                if name != here:
                    to.addAction(name, lambda name=name: self.later(lambda: self.move_requested.emit(i, name)))
            to.addSeparator()
            to.addAction("New page…", lambda: self.later(lambda: self.move_requested.emit(i, "")))
            home = self._home(i)
            if home is not None and home != here:
                menu.addAction(f"Back to its stimulus page ({home})",
                               lambda: self.later(lambda: self.move_requested.emit(i, home)))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
