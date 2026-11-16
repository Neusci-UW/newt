"""Acquisition › Stimulus Design: the Stimulator and its protocols in one page, in three
sections.

1 Stimulus: each output's segments, left to right (segmenteditor.py). Any segment's level
  or duration can change per trial.
2 Trials: how many trials (1, 2, 3…), repeats, order, the time between them, and
  optionally b, one setting stepped against the trials (every trial × every b).
3 Output: which output, and the input that records it.

With one trial sent once it's the plain stimulator (Fire, Repeat). With more, it's a
protocol: Run, with its progress under the buttons. The preview always shows every
trial, faded, with the one picked in bold. The protocol's Plan, Responses and Curve
views aren't shown: responses are looked at on the Record and Analysis pages. For a
quick pulse, pair or train there's Simple Stimulus (simplestim.py).

The halves keep their own code (stimwindow.py, protocolspage.py): this page lays out
their panels. A protocol's a is the trial number (protocols.trials_variable).
"""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from . import protocols as T


def _heading(number: str, title: str, hint: str = "") -> QtWidgets.QLabel:
    lab = QtWidgets.QLabel(f"<b>{number}&nbsp;&nbsp;{title}</b>"
                           + (f"&nbsp;&nbsp;<span style='font-weight:400'>{hint}</span>" if hint else ""))
    lab.setContentsMargins(0, 6, 0, 0)
    return lab


class StimulusPage(QtWidgets.QWidget):
    def __init__(self, main, stim, trials):
        super().__init__()
        self.main, self.stim, self.trials = main, stim, trials
        self._placing = False
        stim.trials_fn = self._trials_for_preview

        # ---- left: 1 Stimulus, 2 Trials, 3 Output, then Fire or Run -------------------
        left = QtWidgets.QWidget()
        left.setMinimumWidth(360)
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(14, 10, 10, 10)
        lv.setSpacing(6)
        lv.addWidget(_heading("1", "Stimulus", "segments, left to right"))
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(stim.fields_panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        lv.addWidget(scroll, 3)

        lv.addWidget(_heading("2", "Trials"))
        n_row = QtWidgets.QHBoxLayout()
        n_row.setContentsMargins(14, 0, 14, 0)
        n_row.setSpacing(8)
        self.n_trials = QtWidgets.QSpinBox(minimum=1, maximum=T.MAX_VALUES)
        self.n_trials.setKeyboardTracking(False)
        self.n_trials.setToolTip("How many different stimuli: trial 1, 2, 3… Each segment's per-trial "
                                 "boxes say how it changes from one to the next.")
        self.n_trials.setAccessibleName("Number of trials")
        n_row.addWidget(QtWidgets.QLabel("Trials"))
        n_row.addWidget(self.n_trials)
        self.n_note = QtWidgets.QLabel()
        n_row.addWidget(self.n_note, 1)
        lv.addLayout(n_row)
        lv.addWidget(trials.left_panel, 2)

        self.btn_output = QtWidgets.QToolButton(checkable=True, autoRaise=True)
        self.btn_output.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_output.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.btn_output.setToolTip("Which output sends the stimulus, and the input it's recorded on")
        self.btn_output.toggled.connect(self._show_output)
        lv.addWidget(self.btn_output)
        lv.addWidget(stim.output_panel)
        lv.addWidget(stim.buttons_panel)
        lv.addWidget(trials.foot)
        # The protocol's progress and messages, here now its results views are hidden.
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress_text = QtWidgets.QLabel()
        for w in (self.progress, self.progress_text, trials.message):
            w.setContentsMargins(14, 0, 14, 0)
            lv.addWidget(w)
        self.progress_timer = QtCore.QTimer(self, interval=250, timeout=self._show_progress)
        self.progress_timer.start()

        # ---- middle: the preview. The protocol's Plan / Responses / Curve and its measuring
        # panel stay in the page, hidden (the protocol still uses their widgets).
        middle = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(middle)
        tv.setContentsMargins(10, 12, 10, 4)
        tv.addWidget(stim.preview_panel)
        tv.addWidget(trials.middle_panel)

        self.split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        for w in (left, middle, trials.right_panel):
            self.split.addWidget(w)
        self.split.setStretchFactor(1, 1)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.split)

        # the protocol's own copies of what's now in the Stimulus section
        for w in (trials.shape_label, trials.shape, trials.btn_edit_stim, trials.stim_info, trials.row_a,
                  trials.preview_title, trials.preview):
            w.hide()
            w.setEnabled(False)
        trials.row_a.setVisible = lambda *_: None
        trials.row_b.set_inline(False)
        trials.btn_add_b.setText("+ Also step a setting against the trials (b)")
        trials.btn_add_b.setToolTip("Every trial is sent at each value of b: a grid, e.g. 5 trials × 4 widths")
        trials.btn_monitor.clicked.disconnect()
        trials.btn_monitor.clicked.connect(lambda: self.btn_output.setChecked(True))
        stim.btn_save.setToolTip("Save this stimulus to a file")

        a = trials.row_a.var()
        self.n_trials.setValue(int(a.stop) if a.key == "trial" else 1)
        if a.key != "trial":
            trials.row_a.set_var(T.trials_variable(self.n_trials.value()))
            trials._settings_changed()
        self.n_trials.valueChanged.connect(self._n_changed)
        for ed in stim.editors:
            ed.list_typed.connect(self._list_typed)
        trials.placed.connect(self.place)
        trials.grid.itemSelectionChanged.connect(lambda: stim._update_preview())
        stim.monitor.currentIndexChanged.connect(lambda *_: self._output_title())
        stim.output.currentIndexChanged.connect(lambda *_: self._output_title())
        self.btn_output.setChecked(not main.cfg.stim_monitor)     # open until the stimulus is recorded
        self._show_output(self.btn_output.isChecked())
        self.place()

    # -- trials ------------------------------------------------------------------------
    def _n_changed(self, n: int) -> None:
        tr = self.trials
        tr.row_a.set_var(T.trials_variable(n))
        tr._settings_changed()
        self.place()

    def _list_typed(self, n: int) -> None:
        """A per-trial list was typed: one trial per value, if there were fewer."""
        if self.n_trials.value() < n and not self.trials.running:
            self.n_trials.setValue(n)

    def _trials_for_preview(self):
        """(protocol, the trial numbers, b's value) for the preview, or None."""
        tr = self.trials
        p, c = tr.protocol, tr.chk
        if p is None or p.a.key != "trial":
            return None
        b = None
        if p.b is not None and c is not None and c.b_vals:
            b = c.b_vals[min(tr._plan_row, len(c.b_vals) - 1)]
        return p, [float(i) for i in range(1, self.n_trials.value() + 1)], b

    def place(self) -> None:
        """The page in the mode that goes with the trials: the plain stimulator for one
        stimulus, a protocol for more."""
        if self._placing:
            return
        self._placing = True
        try:
            tr, stim = self.trials, self.stim
            a = tr.row_a.var()
            if a.key == "trial" and int(a.stop) != self.n_trials.value():      # e.g. after Load…
                self.n_trials.blockSignals(True)
                self.n_trials.setValue(int(a.stop))
                self.n_trials.blockSignals(False)
            n = self.n_trials.value()
            lists = self.main.stimulus.list_lengths() + (
                self.main.stimulus2.list_lengths() if self.main.stimulus2 is not None else [])
            steps = self.main.stimulus.steps or (self.main.stimulus2 is not None and self.main.stimulus2.steps)
            if n == 1 and steps:
                self.n_note.setText("Set more than 1 to use the per-trial changes")
            elif lists and min(lists) < n:
                self.n_note.setText(f"A per-trial list has only {min(lists)} values")
            else:
                self.n_note.setText("different stimuli, numbered 1, 2, 3…" if n > 1 else "")
            want = n > 1 or tr._has_b or tr.repeats.value() > 1
            if tr.varying != want:
                tr.set_varying(want)
            self._show_mode(want)
            stim.set_locked(tr.running)
            self.n_trials.setEnabled(not tr.running)
            self._output_title()
            stim._update_preview()
        finally:
            self._placing = False

    def _show_mode(self, protocol: bool) -> None:
        tr, stim = self.trials, self.stim
        stim.buttons_panel.setVisible(not protocol)
        tr.foot.setVisible(protocol)
        tr.middle_panel.setVisible(False)
        tr.right_panel.setVisible(False)
        self._show_progress()

    def _show_progress(self) -> None:
        tr = self.trials
        running = tr.running and self.isVisible()
        self.progress.setVisible(running)
        self.progress_text.setVisible(running)
        if running:
            n, total = min(tr.next_i, len(tr.trials)), max(1, len(tr.trials))
            self.progress.setMaximum(total)
            self.progress.setValue(n)
            self.progress_text.setText(f"Stimulus {n} of {len(tr.trials)}" + (" · paused" if tr.paused else ""))

    # -- the output section ---------------------------------------------------------------
    def _show_output(self, on: bool) -> None:
        self.stim.output_panel.setVisible(on)
        self.btn_output.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)

    def _output_title(self) -> None:
        cfg = self.main.cfg
        mon = next((c for c in cfg.channels if c.physical == cfg.stim_monitor), None) if cfg.stim_monitor else None
        where = f"recorded on {mon.name}" if mon is not None else "not recorded"
        self.btn_output.setText(f"3  Output: {cfg.stim_output}, {where}")

    def apply_theme(self, mode: str) -> None:
        self.stim.apply_theme(mode)
        self.trials.apply_theme(mode)
