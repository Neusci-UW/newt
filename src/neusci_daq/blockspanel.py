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


class GroupTree(QtWidgets.QTreeWidget):
    """A tree whose top-level rows can be dragged to another place. Only those: dragging across
    the rows inside them still selects them."""
    moved = QtCore.Signal(str, int)              # a page's name, the row it goes to

    def dragMoveEvent(self, ev):
        ev.acceptProposedAction()

    def dropEvent(self, ev):
        at = ev.position().toPoint()
        over = self.itemAt(at)
        item = self.currentItem()
        data = item.data(0, ROLE) if item is not None else None
        ev.setDropAction(QtCore.Qt.DropAction.CopyAction)       # the rows are rebuilt: nothing to remove
        ev.accept()
        if over is None or not (isinstance(data, tuple) and data[0] == "page"):
            return
        while over.parent() is not None:
            over = over.parent()
        row = self.indexOfTopLevelItem(over)
        if at.y() > self.visualItemRect(over).center().y():
            row += 1
        self.moved.emit(data[1], row)


class BlocksPanel(QtWidgets.QWidget):
    activated = QtCore.Signal(object)           # LIVE, BACK or ("block", i)
    selection = QtCore.Signal(list)              # the block indices selected (a page: all its blocks)
    rename_requested = QtCore.Signal(str)        # a page's name
    move_requested = QtCore.Signal(int, str)     # block, page ("" asks for a new page's name)
    stim_channel_picked = QtCore.Signal(str)     # "" for None
    reset_requested = QtCore.Signal()
    color_requested = QtCore.Signal(str)         # a page's name: pick its colour
    color_cleared = QtCore.Signal(str)           # a page's name: back to the default colour
    hide_changed = QtCore.Signal(object, bool)   # ("page", name) or ("block", i), and whether it's hidden
    group_moved = QtCore.Signal(str, int)        # a page's name, the row it goes to
    sort_picked = QtCore.Signal(str)             # "frequency", "amplitude" or "" for your order

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
        # On Analysis what's above the blocks opens in a window of its own (self.extras).
        self.extras = QtWidgets.QWidget()         # the Channels window
        self.group_extras = QtWidgets.QWidget()   # the Groups window
        top = QtWidgets.QVBoxLayout(self.extras) if for_analysis else lay
        group_top = QtWidgets.QVBoxLayout(self.group_extras) if for_analysis else lay
        # The stimulus channel (also on the Analysis tab): it sorts the blocks into pages.
        top.addWidget(QtWidgets.QLabel("Stimulus channel"))
        self.stim_ch = QtWidgets.QComboBox()
        self._stim_tip = ("The channel that recorded the stimulus, set any time: trials with the same "
                          "stimulus go on one group. With None, the notes of each stimulus are used. "
                          "Remembered for this session (and the same setting as on Analysis)")
        self.stim_ch.activated.connect(lambda *_: self.stim_channel_picked.emit(self.stim_ch.currentData() or ""))
        top.addWidget(self.stim_ch)
        self.btn_reset = QtWidgets.QPushButton("Reset groups")
        self.btn_reset.setToolTip("Forget the group names and moved trials, and sort the trials into "
                                  "groups again from the stimulus channel")
        self.btn_reset.clicked.connect(lambda: self.later(self.reset_requested.emit))
        self.settings = QtWidgets.QFormLayout()  # what a page adds with add_setting
        self.settings.setContentsMargins(0, 0, 0, 0)
        top.addLayout(self.settings)
        if for_analysis:
            top.addStretch(1)
        self.group_settings = QtWidgets.QFormLayout()
        self.group_settings.setContentsMargins(0, 0, 0, 0)
        if for_analysis:
            group_top.addLayout(self.group_settings)
        group_top.addWidget(self.btn_reset)
        if for_analysis:
            group_top.addStretch(1)
        self.btn_groups = QtWidgets.QPushButton("Groups…")
        self.btn_groups.setToolTip("The colours of groups and trials, Spread, and Reset groups")
        if for_analysis:
            lay.addWidget(self.btn_groups)
        lay.addSpacing(6)
        self.title = QtWidgets.QLabel("<b>Trials</b>")
        lay.addWidget(self.title)
        self.sort_box = QtWidgets.QComboBox()
        for label, key in (("Your order", ""), ("Frequency", "frequency"), ("Amplitude", "amplitude")):
            self.sort_box.addItem(label, key)
        self.sort_box.setToolTip("The order of the groups. Dragging a group by its ⠿ goes back to your order")
        self.sort_box.activated.connect(lambda *_: self.later(lambda: self.sort_picked.emit(self.sort_box.currentData())))
        sort = QtWidgets.QHBoxLayout()
        sort.addWidget(QtWidgets.QLabel("Sort by"))
        sort.addWidget(self.sort_box, 1)
        if for_analysis:
            lay.addLayout(sort)
        else:
            self.sort_box.hide()
        self.tree = GroupTree()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.setMinimumHeight(60)           # its settings above still fit a small laptop screen
        # Cmd/Shift-click several blocks (or pick a page) to overlay them
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        if for_analysis:
            self.tree.setDragEnabled(True)
            self.tree.setAcceptDrops(True)
            self.tree.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragDrop)
            self.tree.setDefaultDropAction(QtCore.Qt.DropAction.CopyAction)
            self.tree.itemChanged.connect(self._ticked)
            self.tree.moved.connect(lambda name, row: self.later(lambda: self.group_moved.emit(name, row)))
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(lambda item, *_: self.later(lambda d=item.data(0, ROLE): self.activated.emit(d)))
        self.tree.itemDoubleClicked.connect(lambda item, *_: self.later(lambda d=item.data(0, ROLE): self._double(d)))
        self.tree.itemSelectionChanged.connect(
            lambda: self.later(lambda picked=self.selected_blocks(): self.selection.emit(picked)))
        lay.addWidget(self.tree, 1)
        self.btn_select = QtWidgets.QPushButton("Select all")
        self.btn_select.setToolTip("Tick every group and trial")
        self.btn_deselect = QtWidgets.QPushButton("Deselect all")
        self.btn_deselect.setToolTip("Untick every group and trial")
        ticks = QtWidgets.QHBoxLayout()
        ticks.addWidget(self.btn_select)
        ticks.addWidget(self.btn_deselect)
        if for_analysis:
            lay.addLayout(ticks)
        else:
            self.btn_select.hide()
            self.btn_deselect.hide()
        self.btn_all = QtWidgets.QPushButton("Analyze all trials")
        self.btn_all.setToolTip("Clear the selection: every trial is analyzed")
        self.btn_all.setVisible(for_analysis)
        lay.addWidget(self.btn_all)
        self._open: set[str] = set()              # the pages you've expanded
        self.tree.itemExpanded.connect(lambda item: self._opened(item, True))
        self.tree.itemCollapsed.connect(lambda item: self._opened(item, False))
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

    def add_group_setting(self, label: str, widget: QtWidgets.QWidget) -> None:
        self.group_settings.addRow(label, widget)

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
                                "Record a trial first: the setting belongs to the session")
        self.stim_ch.blockSignals(False)

    def populate(self, top: str | None, pages: list[tuple[str, list[int]]], colors: list[str],
                 labels: dict[int, tuple], home, can_reset: bool, busy: bool,
                 hidden_pages=(), hidden_blocks=()) -> None:
        """top: LIVE, BACK or None; pages: [(name, block indices)]; labels: block ->
        (file stem, duration text, and maybe a note); home(i): block i's stimulus page;
        hidden_pages, hidden_blocks: what's unticked, by page name and block index."""
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
            head = bold(QtWidgets.QTreeWidgetItem([("⠿ " if self.for_analysis else "") + f"{name}  ({len(blocks)})"]))
            head.setData(0, ROLE, ("page", name))
            if name not in hidden_pages:
                head.setIcon(0, style.dot_icon(colors[g % len(colors)]))
            self._tick(head, name in hidden_pages, True)
            head.setToolTip(0, "Click to see this group's average; double-click to rename it; right-click "
                               "to change its colour" if self.for_analysis else
                            "Double-click to rename this group; its trials take the new name")
            tree.addTopLevelItem(head)
            for k, i in enumerate(blocks, 1):
                stem, dur, *note = labels.get(i, (f"trial {i + 1}", "?"))
                item = QtWidgets.QTreeWidgetItem([f"{name} {k}   ({stem})" + "".join(f"   {n}" for n in note)])
                item.setData(0, ROLE, ("block", i))
                self._tick(item, i in hidden_blocks, False)
                item.setToolTip(0, f"{stem}, {dur}: click or drag across trials to analyze just those; "
                                   "Cmd/Shift-click to add more; right-click to move it to another group"
                                if self.for_analysis else
                                f"{stem}, {dur}: click to look at it; Cmd/Shift-click several to overlay "
                                "them; right-click to move it to another group")
                head.addChild(item)
            head.setExpanded(name in self._open)
            n_blocks += len(blocks)
        self.title.setText(f"<b>Trials</b> ({n_blocks})" + (f" in {len(pages)} Groups" if len(pages) > 1 else ""))
        self.btn_reset.setEnabled(can_reset)
        tree.blockSignals(False)
        tree.setEnabled(not busy)
        tree.setToolTip("Pick a group to see its average, or click or drag across trials to see those trials"
                        if self.for_analysis
                        else "Stop recording to look at other trials" if busy else
                        "Click a trial to look at it (this stops acquiring). Live goes back, and Record "
                        "carries on adding to the same session")

    def _opened(self, item: QtWidgets.QTreeWidgetItem, on: bool) -> None:
        data = item.data(0, ROLE)
        if isinstance(data, tuple) and data[0] == "page":
            self._open = self._open | {data[1]} if on else self._open - {data[1]}

    def _tick(self, item: QtWidgets.QTreeWidgetItem, hidden: bool, draggable: bool) -> None:
        """Give an item its check box, in Analysis."""
        F = QtCore.Qt.ItemFlag
        if not self.for_analysis:
            return
        flags = item.flags() | F.ItemIsUserCheckable
        item.setFlags(flags | F.ItemIsDragEnabled if draggable else flags & ~F.ItemIsDragEnabled)
        item.setCheckState(0, QtCore.Qt.CheckState.Unchecked if hidden else QtCore.Qt.CheckState.Checked)
        if hidden:
            item.setForeground(0, self.palette().color(self.palette().ColorGroup.Disabled,
                                                       self.palette().ColorRole.Text))

    def _ticked(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        data = item.data(0, ROLE)
        hidden = item.checkState(0) != QtCore.Qt.CheckState.Checked
        if isinstance(data, tuple):
            self.later(lambda: self.hide_changed.emit(data, hidden))

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
            menu.addAction("Rename group…", lambda: self.later(lambda: self.rename_requested.emit(data[1])))
            menu.addAction("Colour…", lambda: self.later(lambda: self.color_requested.emit(data[1])))
            menu.addAction("Default colour", lambda: self.later(lambda: self.color_cleared.emit(data[1])))
        else:
            i = int(data[1])
            here = next((name for name, blocks in self._pages if i in blocks), None)
            to = menu.addMenu("Move to group")
            for name, _ in self._pages:
                if name != here:
                    to.addAction(name, lambda name=name: self.later(lambda: self.move_requested.emit(i, name)))
            to.addSeparator()
            to.addAction("New group…", lambda: self.later(lambda: self.move_requested.emit(i, "")))
            home = self._home(i)
            if home is not None and home != here:
                menu.addAction(f"Back to its stimulus group ({home})",
                               lambda: self.later(lambda: self.move_requested.emit(i, home)))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
