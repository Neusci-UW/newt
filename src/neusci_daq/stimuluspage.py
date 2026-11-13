"""The Stimulus tab: the Stimulator and Protocols in one page.

A protocol is a stimulus with one or two of its settings stepped, so the stepping lives on the
settings themselves: each has a small toggle (↕) beside it. Turn one on and it becomes a, its box
becomes a Range or List of values, and the page turns into a protocol: the preview becomes the
Plan (with Responses and Curve), the trial table shows on the right, and Run protocol takes the
place of Fire. A second toggle is b. With nothing toggled it's the plain stimulator.

The two halves keep their own code (stimwindow.py, protocolspage.py); this page lays out their
panels and keeps the toggles and the protocol's a and b in step.
"""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from . import protocols as T

# The stimulator's boxes that can be stepped: (its attribute, the shape it belongs to, the
# protocol's key). None: every shape has it. "scale" has no box of its own: see _scale_cells.
FIELDS = (("delay", None, "delay_ms"),
          ("p_amp", "pulse", "amplitude_v"), ("p_width", "pulse", "width_ms"), ("p_count", "pulse", "count"),
          ("p_interval", "pulse", "interval_ms"),
          ("s_amp", "step", "amplitude_v"), ("s_dur", "step", "duration_ms"),
          ("r_start", "ramp", "start_v"), ("r_end", "ramp", "end_v"), ("r_dur", "ramp", "duration_ms"),
          ("n_amp", "sine", "amplitude_v"), ("n_off", "sine", "offset_v"), ("n_freq", "sine", "freq_hz"),
          ("n_dur", "sine", "duration_ms"))
CUSTOM_PAGE, FILE_PAGE = 4, 5                          # the stimulator's pages without a box to scale


class _Cell(QtWidgets.QWidget):
    """A setting's box and its vary toggle. While varied, the box gives way to a or b's values."""

    def __init__(self, field: QtWidgets.QWidget, key: str, label: str):
        super().__init__()
        self.field, self.key = field, key
        self.row = QtWidgets.QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(6)
        self.row.addWidget(field, 1)
        self.toggle = QtWidgets.QToolButton(text="↕", checkable=True, autoRaise=True)
        self.toggle.setFixedWidth(26)
        self.toggle.setToolTip(f"Vary {label.lower()}: step it through values, sending the stimulus once for each")
        self.toggle.setAccessibleName(f"Vary {label}")
        self.row.addWidget(self.toggle, 0, QtCore.Qt.AlignmentFlag.AlignTop)

    def show_varied(self, letter: str | None, row: QtWidgets.QWidget | None = None) -> None:
        on = letter is not None
        self.field.setVisible(not on)
        self.toggle.blockSignals(True)
        self.toggle.setChecked(on)
        self.toggle.setText(letter or "↕")
        self.toggle.blockSignals(False)
        if row is not None and row.parentWidget() is not self:
            self.row.insertWidget(1, row, 1)
        if row is not None:
            row.show()


class StimulusPage(QtWidgets.QWidget):
    def __init__(self, main, stim, trials):
        super().__init__()
        self.main, self.stim, self.trials = main, stim, trials
        self._placing = False
        self.cells: dict[tuple[str | None, str], _Cell] = {}
        for attr, shape, key in FIELDS:
            self._wrap(getattr(stim, attr), shape, key)
        self._scale_cells()

        # ---- left: the stimulus, its output, and how it's sent -------------------------
        left = QtWidgets.QWidget()
        left.setMinimumWidth(320)
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(14, 12, 10, 10)
        lv.setSpacing(8)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(stim.fields_panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        lv.addWidget(scroll, 1)
        self.btn_output = QtWidgets.QToolButton(checkable=True, autoRaise=True)
        self.btn_output.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_output.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.btn_output.setToolTip("Which output sends the stimulus, and the input it's recorded on")
        self.btn_output.toggled.connect(self._show_output)
        lv.addWidget(self.btn_output)
        lv.addWidget(stim.output_panel)
        lv.addWidget(stim.buttons_panel)
        lv.addWidget(trials.left_panel, 2)

        # ---- middle: the preview, or the protocol's Plan / Responses / Curve ------------
        middle = QtWidgets.QWidget()
        mv = QtWidgets.QVBoxLayout(middle)
        mv.setContentsMargins(10, 12, 10, 10)
        mv.addWidget(stim.preview_panel, 1)
        mv.addWidget(trials.middle_panel, 1)

        self.split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        for w in (left, middle, trials.right_panel):
            self.split.addWidget(w)
        self.split.setStretchFactor(1, 1)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.split)

        # the protocol's own copies of what's now on the stimulus side
        for w in (trials.shape_label, trials.shape, trials.btn_edit_stim, trials.stim_info, trials.btn_add_b):
            w.hide()
            w.setEnabled(False)
        trials.btn_add_b.setVisible = lambda *_: None          # b comes from a second toggle now
        for row in (trials.row_a, trials.row_b):
            row.set_inline(True)
        trials.btn_monitor.clicked.disconnect()
        trials.btn_monitor.clicked.connect(lambda: self.btn_output.setChecked(True))
        stim.btn_save.setToolTip("Save this stimulus to a file")

        trials.placed.connect(self.place)
        stim.out_tabs.currentChanged.connect(lambda *_: self.place())
        stim.monitor.currentIndexChanged.connect(lambda *_: self._output_title())
        stim.output.currentIndexChanged.connect(lambda *_: self._output_title())
        self.btn_output.setChecked(not main.cfg.stim_monitor)     # open until the stimulus is recorded
        self._show_output(self.btn_output.isChecked())
        self.place()

    # -- the toggles -----------------------------------------------------------------
    def _wrap(self, field: QtWidgets.QWidget, shape: str | None, key: str) -> None:
        form = field.parentWidget().layout()
        label = next((lab for k, lab, _ in T.SETTINGS.get(shape or "pulse", []) if k == key), key)
        cell = _Cell(field, key, label)
        form.replaceWidget(field, cell)
        cell.row.insertWidget(0, field, 1)
        cell.toggle.clicked.connect(lambda *_, c=cell: self._toggled(c))
        self.cells[(shape, key)] = cell

    def _scale_cells(self) -> None:
        """Custom and file stimuli step their whole shape, × a scale: a row for it under each."""
        for page, shape in ((CUSTOM_PAGE, "custom"), (FILE_PAGE, "file")):
            box = QtWidgets.QLabel("× 1, the shape as it is")
            box.setEnabled(False)
            holder = QtWidgets.QWidget()
            f = QtWidgets.QFormLayout(holder)
            f.setContentsMargins(0, 0, 0, 0)
            f.addRow("Scale", box)
            lay = self.stim.pages.widget(page).layout()
            if isinstance(lay, QtWidgets.QFormLayout):
                lay.addRow(holder)
            else:
                lay.addWidget(holder)
            self._wrap(box, shape, "scale")

    def _key(self, cell: _Cell) -> str:
        """Its protocol key: with a second output being edited, named after that output."""
        main = self.main
        if self.stim.editing == 1 and main.stimulus2 is not None:
            return f"{main.second_output}:{cell.key}"
        return cell.key

    def _varied(self) -> list[tuple[QtWidgets.QWidget, T.Variable, str]]:
        tr = self.trials
        if not tr.varying:
            return []
        return [(tr.row_a, tr.row_a.var(), "a")] + ([(tr.row_b, tr.row_b.var(), "b")] if tr._has_b else [])

    def _toggled(self, cell: _Cell) -> None:
        tr, key = self.trials, self._key(cell)
        fresh = T.Variable(key, "range", *T.default_range(key))
        varied = [v.key for _, v, _ in self._varied()]
        self._placing = True                             # place once, at the end
        try:
            if not varied:
                tr.row_a.set_var(fresh)
                tr.set_varying(True)
            elif key == varied[0]:
                if len(varied) > 1:                      # b becomes a
                    tr.row_a.set_var(tr.row_b.var())
                    tr._set_b(False)
                else:
                    tr.set_varying(False)
            elif len(varied) > 1 and key == varied[1]:
                tr._set_b(False)
            elif len(varied) == 1:
                tr._set_b(True)
                tr.row_b.set_var(fresh)
            else:
                self.stim.show_message("Two settings are varied already (a and b): turn one off first", True)
            tr._settings_changed()
        finally:
            self._placing = False
        self.place()

    def place(self) -> None:
        """Each varied setting's values where its box was, and the page in the mode that goes with it."""
        if self._placing:
            return
        self._placing = True
        try:
            tr, stim = self.trials, self.stim
            shape = stim.shape.currentData()
            placed = {}
            for row, var, letter in self._varied():
                out, base = T.split_key(var.key)
                if (1 if out else 0) == stim.editing:    # on the output being edited
                    cell = self.cells.get((shape, base)) or self.cells.get((None, base))
                    if cell is not None:
                        placed[cell] = (letter, row)
            for row in (tr.row_a, tr.row_b):
                if all(r is not row for _, r in placed.values()):
                    row.hide()
            for cell in self.cells.values():
                cell.show_varied(*placed.get(cell, (None, None)))
                cell.toggle.setEnabled(not tr.running)
            self._show_mode(tr.varying)
            self._output_title()
        finally:
            self._placing = False

    def _show_mode(self, protocol: bool) -> None:
        tr, stim = self.trials, self.stim
        stim.preview_panel.setVisible(not protocol)
        stim.buttons_panel.setVisible(not protocol)
        tr.middle_panel.setVisible(protocol)
        tr.left_panel.setVisible(protocol)
        tr.right_panel.setVisible(protocol)

    # -- the output section ---------------------------------------------------------------
    def _show_output(self, on: bool) -> None:
        self.stim.output_panel.setVisible(on)
        self.btn_output.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)

    def _output_title(self) -> None:
        cfg = self.main.cfg
        mon = next((c for c in cfg.channels if c.physical == cfg.stim_monitor), None) if cfg.stim_monitor else None
        where = f"recorded on {mon.name}" if mon is not None else "not recorded"
        self.btn_output.setText(f"Output: {cfg.stim_output}, {where}")

    def apply_theme(self, mode: str) -> None:
        self.stim.apply_theme(mode)
        self.trials.apply_theme(mode)
