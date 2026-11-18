"""Acquisition › Complex Stimulus: build a stimulus from segments and send it as trials, in
the Simple Stimulus style.

Left: the whole stimulus drawn large (designparts.StimPicture). Click a segment on it to
edit it underneath (SegmentPanel); "+ Add segment" opens a picker of every kind (Step,
Pulse, Train, Ramp, Sine, Triangle, Square, Chirp, Noise, Exponential, Alpha, Custom).
Under that, Trials: how many, repeats, order, and the time between them. Each level and
duration can change per trial (its dashed box).

Right: Run (or Send, for one stimulus), its progress, sending one trial, and folded
Output and Save.

The Stimulator (stimwindow.py) and the protocol runner (protocolspage.py) keep their own
code and widgets; this page shows the ones it needs and keeps the rest hidden. Their
Plan / Responses / Curve views aren't shown: responses are looked at on the Record page
and in Analysis. A protocol's a is the trial number (protocols.trials_variable).
"""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from . import protocols as T
from . import stim as stimmod
from . import style
from dataclasses import replace

from .designparts import KindPicker, SegmentPanel, StimPicture
from .simplestim import _spin
from .stimdesign import MAX_FADED, seg_name, segment_spans, trials_note


def _heading(text: str, hint: str = "") -> QtWidgets.QLabel:
    return QtWidgets.QLabel(f"<b>{text}</b>" + (f"&nbsp;&nbsp;<span style='font-weight:400'>{hint}</span>"
                                                if hint else ""))


class StimulusPage(QtWidgets.QWidget):
    def __init__(self, main, stim, trials):
        super().__init__()
        self.main, self.stim, self.trials = main, stim, trials
        self._placing = False
        self._pushing = False
        self._mode = style.LIGHT
        self.which = 0                       # 0: the first output's stimulus, 1: the second's
        self.sel = 1
        stim.trials_fn = self._trials_for_preview

        # ---- left: the stimulus, the picked segment, Trials --------------------------
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(18, 14, 18, 14)
        lv.setSpacing(10)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(_heading("Stimulus"))
        self.summary_label = QtWidgets.QLabel()
        top.addWidget(self.summary_label)
        top.addStretch(1)
        top.addWidget(QtWidgets.QLabel("Offset"))
        self.offset = _spin(-100000, 100000, 3, stim._unit_label,
                            "A steady level the whole stimulus sits on, baselines included (0 = none). "
                            "Each segment can have its own offset too.")
        self.offset.setMinimumWidth(100)
        top.addWidget(self.offset)
        self.out_switch = QtWidgets.QWidget()
        osw = QtWidgets.QHBoxLayout(self.out_switch)
        osw.setContentsMargins(0, 0, 0, 0)
        osw.setSpacing(2)
        osw.addWidget(QtWidgets.QLabel("Editing"))
        self.out_group = QtWidgets.QButtonGroup(self)
        self.out_buttons = []
        for i in range(2):
            b = QtWidgets.QPushButton(checkable=True)
            self.out_group.addButton(b, i)
            self.out_buttons.append(b)
            osw.addWidget(b)
        self.out_buttons[0].setChecked(True)
        top.addWidget(self.out_switch)
        self.btn_add = QtWidgets.QPushButton("+ Add segment")
        self.btn_add.setObjectName("addSegment")
        self.btn_add.setToolTip("Add a segment after the one picked")
        top.addWidget(self.btn_add)
        lv.addLayout(top)

        self.picture = StimPicture()
        self.picture.setMinimumHeight(260)
        lv.addWidget(self.picture)
        trial_row = QtWidgets.QHBoxLayout()
        self.pic_hint = QtWidgets.QLabel("Click a segment on the picture to change it")
        trial_row.addWidget(self.pic_hint, 1)
        self.trial_label = QtWidgets.QLabel()
        self.trial_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, minimum=1)
        self.trial_slider.setMaximumWidth(220)
        self.trial_slider.setToolTip("Which trial the picture shows in bold")
        trial_row.addWidget(self.trial_label)
        trial_row.addWidget(self.trial_slider)
        lv.addLayout(trial_row)

        self.panel = SegmentPanel()
        lv.addWidget(self.panel)
        self.problem = QtWidgets.QLabel()
        self.problem.setWordWrap(True)
        lv.addWidget(self.problem)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        lv.addWidget(line)
        tr_row = QtWidgets.QHBoxLayout()
        tr_row.setSpacing(18)
        tr_row.addWidget(_heading("Trials"), 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        self.n_trials = QtWidgets.QSpinBox(minimum=1, maximum=T.MAX_VALUES)
        self.n_trials.setKeyboardTracking(False)
        self.n_trials.setToolTip("How many different stimuli: trial 1, 2, 3… The dashed boxes say how "
                                 "each one differs from the last.")
        self.n_trials.setAccessibleName("Number of trials")

        def col(label, w):
            box = QtWidgets.QVBoxLayout()
            box.setSpacing(4)
            box.addWidget(QtWidgets.QLabel(label))
            box.addWidget(w)
            tr_row.addLayout(box)
        col("How many", self.n_trials)
        col("Repeat each", trials.repeats)
        col("Order", trials.order)
        col("Every", trials.gap)
        tr_row.addStretch(1)
        tr_row.addWidget(trials.btn_add_b, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        lv.addLayout(tr_row)
        self.n_note = QtWidgets.QLabel()
        lv.addWidget(self.n_note)
        lv.addWidget(trials.row_b)
        lv.addStretch(1)

        self.left_scroll = QtWidgets.QScrollArea()
        self.left_scroll.setWidget(left)
        self.left_scroll.setWidgetResizable(True)
        self.left_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.left_scroll.setMinimumWidth(480)

        # ---- right, like Simple Stimulus's: the big button, Pause/Repeat and Stop, the
        # state, progress, what was sent, saving, and the output -----------------------
        side = QtWidgets.QWidget()
        side.setObjectName("designSide")
        side.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        side.setFixedWidth(310)
        rv = QtWidgets.QVBoxLayout(side)
        rv.setContentsMargins(16, 16, 16, 16)
        rv.setSpacing(10)
        big = trials.btn_run.font()
        big.setPointSizeF(big.pointSizeF() * 1.3)
        # a protocol: Run, then Pause and Stop
        self.run_box = QtWidgets.QWidget()
        rb = QtWidgets.QVBoxLayout(self.run_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(8)
        trials.btn_run.setMinimumHeight(48)
        trials.btn_run.setFont(big)
        rb.addWidget(trials.btn_run)
        ps = QtWidgets.QHBoxLayout()
        ps.addWidget(trials.btn_pause, 1)
        ps.addWidget(trials.btn_stop, 1)
        rb.addLayout(ps)
        rv.addWidget(self.run_box)
        # one stimulus: Send, then Repeat every … and Stop
        self.fire_box = QtWidgets.QWidget()
        fb = QtWidgets.QGridLayout(self.fire_box)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(6)
        stim.btn_fire.setText("Send")
        stim.btn_fire.setObjectName("start")
        stim.btn_fire.setMinimumHeight(48)
        stim.btn_fire.setFont(big)
        stim.btn_fire.style().unpolish(stim.btn_fire)
        stim.btn_fire.style().polish(stim.btn_fire)
        fb.addWidget(stim.btn_fire, 0, 0, 1, 4)
        fb.addWidget(stim.btn_repeat, 1, 0)
        fb.addWidget(QtWidgets.QLabel("every"), 1, 1)
        fb.addWidget(stim.repeat, 1, 2)
        fb.addWidget(stim.btn_stop, 1, 3)
        rv.addWidget(self.fire_box)
        self.lamp = QtWidgets.QLabel()
        self.lamp.setWordWrap(True)
        rv.addWidget(self.lamp)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        rv.addWidget(self.progress)
        rv.addWidget(trials.message)
        self.btn_once = QtWidgets.QPushButton()
        self.btn_once.setToolTip("Send the trial the picture shows, once, outside the protocol")
        rv.addWidget(self.btn_once)

        rv.addWidget(self._side_heading("Sent"))
        self.log = QtWidgets.QListWidget()
        self.log.setToolTip("Each stimulus is also a comment on the recording")
        self.log.setMinimumHeight(120)
        rv.addWidget(self.log, 1)
        self._sent = 0
        self._once = False

        rv.addWidget(self._side_heading("Saved"))
        nrow = QtWidgets.QHBoxLayout()
        nrow.addWidget(QtWidgets.QLabel("Name"))
        nrow.addWidget(trials.name, 1)
        rv.addLayout(nrow)
        srow = QtWidgets.QHBoxLayout()
        for b in (trials.btn_save, trials.btn_load, stim.btn_save, stim.btn_load):
            srow.addWidget(b)
        srow.addStretch(1)
        rv.addLayout(srow)

        self.output_note = QtWidgets.QLabel()
        self.output_note.setWordWrap(True)
        self.output_note.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.output_note.linkActivated.connect(lambda *_: self.out_body.setVisible(not self.out_body.isVisible()))
        rv.addWidget(self.output_note)
        self.out_body = QtWidgets.QWidget()
        ol = QtWidgets.QVBoxLayout(self.out_body)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.addWidget(stim.output_panel)
        orow = QtWidgets.QHBoxLayout()
        self.btn_second = QtWidgets.QPushButton()
        self.btn_second.setToolTip("Send a second stimulus on the other output, starting with the first")
        orow.addWidget(self.btn_second)
        orow.addStretch(1)
        ol.addLayout(orow)
        self.out_body.setVisible(not main.cfg.stim_monitor)     # open until the stimulus is recorded
        rv.addWidget(self.out_body)
        main.sent_hooks = getattr(main, "sent_hooks", []) + [self._sent_one]

        # what the Stimulator and the protocol have that this page doesn't show
        self._hidden = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(self._hidden)
        for w in (stim.fields_panel, stim.preview_panel, stim.buttons_panel, trials.left_panel,
                  trials.middle_panel, trials.right_panel, trials.foot):
            hl.addWidget(w)
        self._hidden.hide()

        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.left_scroll, 1)
        outer.addWidget(side)
        outer.addWidget(self._hidden)

        self.picker = KindPicker(self)

        # ---- the protocol's trial number, and signals ----------------------------------
        trials.row_a.setVisible = lambda *_: None
        trials.row_b.set_inline(False)
        trials.btn_add_b.setText("+ Also step a setting")
        trials.btn_add_b.setToolTip("Send every trial at each value of a second setting: a grid, "
                                    "e.g. 5 trials × 4 widths")
        a = trials.row_a.var()
        self.n_trials.setValue(int(a.stop) if a.key == "trial" else 1)
        if a.key != "trial":
            trials.row_a.set_var(T.trials_variable(self.n_trials.value()))
            trials._settings_changed()
        self.n_trials.valueChanged.connect(self._n_changed)
        for ed in stim.editors:
            ed.list_typed.connect(self._list_typed)
        trials.placed.connect(self.place)
        stim.monitor.currentIndexChanged.connect(lambda *_: self._output_title())
        stim.output.currentIndexChanged.connect(lambda *_: self._output_title())
        self.picture.picked.connect(self._pick)
        self.panel.edited.connect(self._edited)
        self.panel.moved.connect(self._move)
        self.panel.removed.connect(self._remove)
        self.panel.problem.connect(lambda t: self._show_problem(t))
        self.panel.list_typed.connect(self._list_typed)
        self.btn_add.clicked.connect(lambda: self.picker.popup_under(self.btn_add))
        self.picker.picked.connect(self._add)
        self.trial_slider.valueChanged.connect(lambda *_: self.refresh(editor=False))
        self.out_group.idClicked.connect(self._switch_output)
        self.btn_second.clicked.connect(self._second_output)
        self.btn_once.clicked.connect(self._send_once)
        self.offset.valueChanged.connect(
            lambda v: self._push_stimulus(replace(self._stimulus(), offset_v=v / (self.stim._scale or 1.0))))
        self.progress_timer = QtCore.QTimer(self, interval=250, timeout=self._show_progress)
        self.progress_timer.start()
        self.place()

    @staticmethod
    def _side_heading(text: str) -> QtWidgets.QLabel:
        lab = QtWidgets.QLabel(f"<b>{text.upper()}</b>")
        lab.setContentsMargins(0, 6, 0, 0)
        return lab

    def _sent_one(self, desc: str, stimulus) -> None:
        """A stimulus went out: list it if it was this page's (a protocol's trial, Send,
        or Send trial N once), not Simple Stimulus's."""
        if not (self.trials.running or self._once or stimulus is self.main.stimulus):
            return
        import time
        self._sent += 1
        self.log.insertItem(0, f"#{self._sent}   {desc}   {time.strftime('%H:%M:%S')}")
        while self.log.count() > 300:
            self.log.takeItem(self.log.count() - 1)

    def _fold(self, parent_layout, title: str) -> QtWidgets.QWidget:
        """A line that opens to show its settings."""
        btn = QtWidgets.QToolButton(checkable=True, autoRaise=True)
        btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        btn.setText(title)
        body = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        body.hide()

        def toggled(on):
            body.setVisible(on)
            btn.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)
        btn.toggled.connect(toggled)
        body.button = btn
        parent_layout.addWidget(btn)
        parent_layout.addWidget(body)
        return body

    # -- the stimulus being edited -------------------------------------------------------------
    def _stimulus(self) -> stimmod.Stimulus:
        s = self.main.stimulus2 if self.which == 1 and self.main.stimulus2 is not None else self.main.stimulus
        return stimmod.to_segments(s)

    def _push(self, parts: list[dict], editor: bool = False) -> None:
        """Use these segments for the stimulus being edited. `editor`: rebuild the segment's
        settings too (not while one of them is being typed in)."""
        self._push_stimulus(replace(self._stimulus(), parts=parts), editor)

    def _push_stimulus(self, s: stimmod.Stimulus, editor: bool = False) -> None:
        self._pushing = True
        try:
            if self.which == 1 and self.main.stimulus2 is not None:
                self.main.set_stimulus2(s)
            else:
                self.main.set_stimulus(s)
        finally:
            self._pushing = False
        self.refresh(editor=editor)

    def _pick(self, i: int) -> None:
        self.sel = i
        self.refresh(editor=True)

    def _edited(self, part: dict) -> None:
        parts = [dict(p) for p in self._stimulus().parts]
        if 0 <= self.sel < len(parts):
            kind_changed = parts[self.sel].get("kind") != part.get("kind")
            parts[self.sel] = part
            self._push(parts, editor=kind_changed)

    def _add(self, kind: str, extra: dict) -> None:
        parts = [dict(p) for p in self._stimulus().parts]
        p = {**stimmod.new_part(kind), **extra}
        at = min(self.sel + 1, len(parts))
        parts.insert(at, p)
        self.sel = at
        self._push(parts, editor=True)

    def _move(self, step: int) -> None:
        parts = [dict(p) for p in self._stimulus().parts]
        j = self.sel + step
        if 0 <= self.sel < len(parts) and 0 <= j < len(parts):
            parts[self.sel], parts[j] = parts[j], parts[self.sel]
            self.sel = j
            self._push(parts, editor=True)

    def _remove(self) -> None:
        parts = [dict(p) for p in self._stimulus().parts]
        if len(parts) > 1 and 0 <= self.sel < len(parts):
            del parts[self.sel]
            self.sel = min(self.sel, len(parts) - 1)
            self._push(parts, editor=True)

    # -- outputs -------------------------------------------------------------------------
    def _switch_output(self, i: int) -> None:
        self.which = i
        self.sel = 1
        self.refresh(editor=True)

    def _second_output(self) -> None:
        if self.main.stimulus2 is None:
            self.stim._add_output()
            self.which = 1
        else:
            self.main.set_stimulus2(None)
            self.which = 0
        self.sel = 1
        self.place()

    def _send_once(self) -> None:
        """The trial the picture shows, once."""
        main, cfg = self.main, self.main.cfg
        t = self.trial_slider.value() - 1
        try:
            s1 = main.stimulus.at_trial(t)
            s2 = main.stimulus2.at_trial(t) if main.stimulus2 is not None else None
            waves = {cfg.stim_output: s1.waveform(cfg.stim_rate)}
            if s2 is not None:
                waves[main.second_output] = s2.waveform(cfg.stim_rate)
            chans, wave = stimmod.combine(waves)
        except ValueError as e:
            self._show_problem(str(e))
            return
        self._once = True
        try:
            main.send_wave(wave, f"Trial {t + 1}: " + s1.describe(cfg.stim_units, cfg.stim_scale), s1,
                           channels=chans, stimulus2=s2)
        finally:
            self._once = False

    # -- showing it ----------------------------------------------------------------------
    def refresh(self, editor: bool = True) -> None:
        """Redraw the picture (and, with `editor`, the picked segment's settings)."""
        main, stim = self.main, self.stim
        s = self._stimulus()
        parts = s.parts
        if not parts:
            return
        self.sel = max(0, min(self.sel, len(parts) - 1))
        n = self.n_trials.value()
        self.trial_slider.blockSignals(True)
        self.trial_slider.setMaximum(max(1, n))
        self.trial_slider.blockSignals(False)
        self.trial_slider.setVisible(n > 1)
        self.trial_label.setVisible(n > 1)
        cur = min(self.trial_slider.value(), n) - 1
        self.trial_label.setText(f"Trial {cur + 1} of {n}")
        rate = main.cfg.stim_rate
        waves, ids, problem = [], [], ""
        order = [i for i in range(n) if i != cur][:MAX_FADED] + [cur]
        for t in order:
            try:
                waves.append(s.at_trial(t).waveform(rate))
                ids.append(t)
            except (ValueError, OSError) as e:
                if t == cur:
                    problem = f"Trial {t + 1}: {e}"
        try:
            spans = segment_spans(s.at_trial(cur), rate)
        except ValueError:
            spans = segment_spans(s, rate)
        names = [seg_name(p) for p in parts]
        units, k = stim._unit_label, stim._scale
        if not self.offset.hasFocus():
            self.offset.blockSignals(True)
            self.offset.setValue(float(s.offset_v) * k)
            self.offset.blockSignals(False)
        if editor:
            self.panel.show_part(self.sel, parts[self.sel], units, k, len(parts))
        else:
            self.panel.part = dict(parts[self.sel])
        self.picture.set_data(waves, spans, names, self.sel, self.panel.dims(), rate, units, k,
                              trial_ids=ids, n_trials=n)
        total = spans[-1][1] / rate * 1000 if spans else 0
        self.summary_label.setText(f"{len(parts)} segments · {total:.4g} ms")
        self._show_problem(problem)
        two = main.stimulus2 is not None
        self.out_switch.setVisible(two)
        self.out_buttons[0].setText(main.cfg.stim_output)
        self.out_buttons[1].setText(main.second_output)
        self.out_buttons[self.which if two else 0].setChecked(True)
        self.btn_second.setText(f"Remove {main.second_output}" if two else f"+ Second output ({main.second_output})")
        self.btn_once.setText(f"Send trial {cur + 1} once")

    def _show_problem(self, text: str) -> None:
        t = style.tokens(self._mode)
        self.problem.setStyleSheet(f"color: {t['error']}")
        self.problem.setText(text)
        self.problem.setVisible(bool(text))

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
        """(protocol, the trial numbers, b's value) for the Stimulator's preview, or None."""
        tr = self.trials
        p, c = tr.protocol, tr.chk
        if p is None or p.a.key != "trial":
            return None
        b = None
        if p.b is not None and c is not None and c.b_vals:
            b = c.b_vals[min(tr._plan_row, len(c.b_vals) - 1)]
        return p, [float(i) for i in range(1, self.n_trials.value() + 1)], b

    def place(self) -> None:
        """The page in the mode that goes with the trials: Send for one stimulus, Run for
        more (or for repeats)."""
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
            self.n_note.setText(trials_note(self.main.stimulus, n, self.main.stimulus2))
            self.n_note.setVisible(bool(self.n_note.text()))
            want = n > 1 or tr._has_b or tr.repeats.value() > 1
            if tr.varying != want:
                tr.set_varying(want)
            self._show_mode(want)
            stim.set_locked(tr.running)
            for w in (self.n_trials, self.panel, self.btn_add, self.picture, self.offset):
                w.setEnabled(not tr.running)
            self._output_title()
            if not self._pushing:
                self.refresh(editor=True)
        finally:
            self._placing = False

    def _show_mode(self, protocol: bool) -> None:
        tr, stim = self.trials, self.stim
        self.run_box.setVisible(protocol)
        self.fire_box.setVisible(not protocol)
        for b in (tr.btn_save, tr.btn_load):         # a protocol saves with its trials;
            b.setVisible(protocol)                   # one stimulus saves on its own
        for b in (stim.btn_save, stim.btn_load):
            b.setVisible(not protocol)
        self._show_progress()

    def _show_progress(self) -> None:
        """The state line (like Simple Stimulus's), the progress, and what can be pressed."""
        tr, main = self.trials, self.main
        t = style.tokens(self._mode)
        running = tr.running and self.isVisible()
        self.progress.setVisible(running)
        self.btn_once.setEnabled(not running)
        if running:
            n, total = min(tr.next_i, len(tr.trials)), max(1, len(tr.trials))
            self.progress.setMaximum(total)
            self.progress.setValue(n)
            state, ok = f"Stimulus {n} of {len(tr.trials)}" + (" · paused" if tr.paused else ""), True
        elif tr.varying:
            state, ok = tr.summary.text(), tr.btn_run.isEnabled()    # Run starts acquiring too
        else:
            state, ok = main.simple_page.state() if hasattr(main, "simple_page") else ("Ready", True)
        self.lamp.setText(f"<span style='color:{'#1e8e3e' if ok else t['error']}'>●</span> {state}")
        n = tr.chk.n_trials if tr.varying and tr.chk is not None and tr.chk.ok else 0
        tr.btn_run.setText(f"Run {n} stimuli" if n and not running else ("Running…" if running else "Run"))

    def _output_title(self) -> None:
        cfg = self.main.cfg
        t = style.tokens(self._mode)
        mon = next((c for c in cfg.channels if c.physical == cfg.stim_monitor), None) if cfg.stim_monitor else None
        where = f"recorded on {mon.name}" if mon is not None else "not recorded"
        self.output_note.setText(f"Output {cfg.stim_output}, {where}, limit ±{cfg.stim_max_v:g} V · "
                                 f"<a href='#' style='color:{t['focus']}'>change…</a>")

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        self.stim.apply_theme(mode)
        self.trials.apply_theme(mode)
        t = style.tokens(mode)
        self.setStyleSheet(f"QWidget#designSide {{ background: {t['ground']}; border-left: 1px solid {t['line']}; }}"
                           f"QPushButton#addSegment {{ color: {t['focus']}; font-weight: 600;"
                           f" border: 1px solid {t['focus']}; background: {t['selection']}; padding: 5px 14px; }}")
        self._output_title()
        for lab in (self.pic_hint, self.summary_label):
            lab.setStyleSheet(f"color: {t['subtle']}")
        self.picture.apply_theme(mode)
        self.panel.apply_theme(mode)
        self.picker.apply_theme(mode)
        self.refresh(editor=True)
