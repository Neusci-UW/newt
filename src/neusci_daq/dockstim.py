"""The stimulator docked under Trials on the Record page: Simple Stimulus's main controls
(pulse type, a small labeled picture, amplitude and width, Stimulate / Repeat / Stop),
so students can stimulate and watch without changing tabs.

It holds no settings of its own: it shows and edits the Simple Stimulus page's (`page`),
so changing one changes the other. The ▾ folds it down to its header.
"""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from . import stim as stimmod
from . import style
from .designparts import StimPicture
from .simplestim import DIM, FIELDS, MARKED, MODES, NAMES, WAVES, PulseDiagram, _repeats_spin, _Spin

SHORT = {"single": "Pulse", "pair": "Pair", "train": "Train", "sine": "Sine", "triangle": "Tri"}


def _small_spin(lo, hi, decimals, suffix, tip):
    s = _Spin(minimum=lo, maximum=hi, decimals=decimals)
    s.setSuffix(f" {suffix}" if suffix else "")
    s.setStepType(QtWidgets.QAbstractSpinBox.StepType.AdaptiveDecimalStepType)
    s.setKeyboardTracking(False)
    s.setToolTip(tip)
    return s


class DockedStimulator(QtWidgets.QFrame):
    def __init__(self, main, page):
        super().__init__()
        self.main, self.page = main, page
        self._mode = style.LIGHT
        self._syncing = False
        self.setObjectName("dockStim")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(230)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(6)
        self.lamp = QtWidgets.QLabel()
        self.title = QtWidgets.QLabel("<b>Stimulator</b>")
        self.btn_more = QtWidgets.QPushButton("more…")
        self.btn_more.setFlat(True)
        self.btn_more.setToolTip("Open Acquisition › Simple Stimulus: presets, pulse pairs and trains in full, "
                                 "and everything sent")
        self.btn_fold = QtWidgets.QToolButton(autoRaise=True, checkable=True)
        self.btn_fold.setToolTip("Fold the stimulator away")
        self.btn_fold.setAccessibleName("Show or hide the stimulator's controls")
        head.addWidget(self.lamp)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.btn_more)
        head.addWidget(self.btn_fold)
        v.addLayout(head)

        self.body = QtWidgets.QWidget()
        b = QtWidgets.QVBoxLayout(self.body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(6)
        types = QtWidgets.QHBoxLayout()
        types.setSpacing(1)
        self.type_buttons = {}
        self.type_group = QtWidgets.QButtonGroup(self)
        for key, name in MODES:
            btn = QtWidgets.QPushButton(SHORT[key], checkable=True)
            btn.setToolTip(name)
            btn.setStyleSheet("QPushButton { padding: 3px 2px; }")
            self.type_group.addButton(btn)
            self.type_buttons[key] = btn
            types.addWidget(btn, 1)
        b.addLayout(types)
        self.diagram = PulseDiagram()
        self.diagram.setMinimumHeight(110)
        self.diagram.setMaximumHeight(110)
        self.diagram.pad = 40
        b.addWidget(self.diagram)

        units = main.cfg.stim_units or "V"
        self.level = _small_spin(-100000, 100000, 3, units, "How strong each pulse is")
        self.width = _small_spin(0.01, 10000, 3, "ms", "How long each pulse lasts")
        self.interval = _small_spin(0.1, 100000, 2, "ms", "From the start of the first pulse to the start of the second")
        self.rate = _small_spin(0.01, 5000, 2, "Hz", "Pulses per second in the train")
        self.freq = _small_spin(0.01, 2500, 2, "Hz", "Cycles per second")
        self.dur = _small_spin(1, 600000, 1, "ms", "How long the wave lasts")
        self.offset = _small_spin(-100000, 100000, 3, units, "A steady level the whole stimulus sits on (0 = none)")
        self.count = QtWidgets.QSpinBox(minimum=2, maximum=1000)
        self.count.setKeyboardTracking(False)
        self.count.setToolTip("How many pulses in the train")
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        self.labels = {}
        # (row, column) in a 2-wide grid; boxes that are never shown together share a cell
        self.boxes = {"level": self.level, "width": self.width, "interval": self.interval, "rate": self.rate,
                      "count": self.count, "freq": self.freq, "dur": self.dur, "offset": self.offset}
        cells = {"level": (0, 0), "width": (0, 1), "freq": (0, 1), "interval": (1, 0), "rate": (1, 0),
                 "dur": (1, 0), "count": (1, 1), "offset": (2, 0)}
        for key, w in self.boxes.items():
            lab = QtWidgets.QLabel()
            self.labels[key] = lab
            r, c = cells[key]
            grid.addWidget(lab, r * 2, c)
            grid.addWidget(w, r * 2 + 1, c)
        b.addLayout(grid)

        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(4)
        self.btn_fire = QtWidgets.QPushButton("Stimulate")
        self.btn_fire.setObjectName("start")
        self.btn_fire.setMinimumHeight(30)
        self.btn_fire.setToolTip("Send the stimulus once")
        self.btn_repeat = QtWidgets.QPushButton("Repeat")
        self.btn_repeat.setToolTip("Send it every few seconds, as many times as set (or until Stop)")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setToolTip("Stop sending and set the output to 0 V (Esc)")
        btns.addWidget(self.btn_fire, 2)
        btns.addWidget(self.btn_repeat, 1)
        btns.addWidget(self.btn_stop, 1)
        b.addLayout(btns)
        every = QtWidgets.QGridLayout()
        every.setHorizontalSpacing(8)
        every.setVerticalSpacing(2)
        self.every = _small_spin(0.05, 3600, 2, "s", "From the start of one stimulus to the start of the next")
        self.repeats = _repeats_spin()
        every.addWidget(QtWidgets.QLabel("Repeat every"), 0, 0)
        every.addWidget(self.every, 1, 0)
        every.addWidget(QtWidgets.QLabel("Repeats"), 0, 1)
        every.addWidget(self.repeats, 1, 1)
        b.addLayout(every)
        self.ramp = QtWidgets.QCheckBox()
        self.ramp.setToolTip("Raise the amplitude after each stimulus (the amount and the limit are on "
                             "Simple Stimulus)")
        b.addWidget(self.ramp)
        self.last = QtWidgets.QLabel()
        self.last.setWordWrap(True)
        b.addWidget(self.last)
        v.addWidget(self.body)

        # ---- signals ----------------------------------------------------------------
        self.btn_more.clicked.connect(lambda: main.tabs.show_tab("simple"))
        self.btn_fold.toggled.connect(self._fold)
        self.type_group.buttonClicked.connect(lambda *_: self._edited())
        for w in (self.level, self.width, self.interval, self.rate, self.every, self.freq, self.dur, self.offset):
            w.valueChanged.connect(self._edited)
        for w in (self.count, self.repeats):
            w.valueChanged.connect(self._edited)
        self.ramp.toggled.connect(self._edited)
        self.btn_fire.clicked.connect(page.fire)
        self.btn_repeat.clicked.connect(page.start_repeat)
        self.btn_stop.clicked.connect(main.stop_stimulus)
        page.changed.connect(self.sync)
        page.state_changed.connect(self.refresh)
        self.timer = QtCore.QTimer(self, interval=300, timeout=self.refresh)

        self.btn_fold.setChecked(not main.settings.value("dock_stim_open", True, type=bool))
        self._fold(self.btn_fold.isChecked())
        self.sync()

    # -- the page's settings, shown and edited here ------------------------------------
    def sync(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            d = self.page.values()
            for w, key in ((self.level, "level"), (self.width, "width"), (self.interval, "interval"),
                           (self.rate, "rate"), (self.count, "count"), (self.every, "every"),
                           (self.freq, "freq"), (self.dur, "dur"), (self.offset, "offset"),
                           (self.repeats, "repeats")):
                if w.value() != d[key] and not w.hasFocus():
                    w.setValue(d[key])
            self.type_buttons[d["mode"]].setChecked(True)
            self.ramp.setChecked(d["ramp"])
            units = self.main.cfg.stim_units or "V"
            self.ramp.setText(f"Raise {d['ramp_by']:+g} {units} each, to {d['ramp_to']:g} {units}")
            mode = d["mode"]
            for key, w in self.boxes.items():
                w.setVisible(key in FIELDS[mode])
                self.labels[key].setVisible(key in FIELDS[mode])
            self._draw(d)
        finally:
            self._syncing = False
        self.refresh()

    def _edited(self, *_):
        if self._syncing:
            return
        d = self.page.values()
        d.update(mode=next(k for k, b in self.type_buttons.items() if b.isChecked()),
                 level=self.level.value(), width=self.width.value(), interval=self.interval.value(),
                 rate=self.rate.value(), count=self.count.value(), every=self.every.value(),
                 ramp=self.ramp.isChecked(), freq=self.freq.value(), dur=self.dur.value(),
                 offset=self.offset.value(), repeats=self.repeats.value())
        self.page._set_values(d)
        self.page._changed()            # saves, redraws the page, and calls sync() here

    def _draw(self, d: dict) -> None:
        units, mode = self.main.cfg.stim_units or "V", d["mode"]
        labels = {"amp": f"{d['level']:g} {units}", "width": f"{d['width']:g} ms"}
        if mode == "pair":
            labels["gap"] = f"{d['interval']:g} ms"
        elif mode == "train":
            labels["gap"] = f"{d['rate']:g} Hz"
            labels["count"] = f"{d['count']} pulses"
        elif mode in WAVES:
            labels.update(freq=f"{d['freq']:g} Hz", dur=f"{d['dur']:g} ms")
        t = style.tokens(self._mode)
        self.diagram.show_values(mode, labels, (t["focus"], DIM[self._mode], t["subtle"], t["field_border"]))
        for key, lab in self.labels.items():
            lab.setText(f"<span style='color:{DIM[self._mode]}'>■</span> {NAMES[key]}" if key in MARKED[mode]
                        else NAMES[key])

    # -- state -------------------------------------------------------------------------
    def refresh(self) -> None:
        main, page = self.main, self.page
        t = style.tokens(self._mode)
        state, ok = page.state()
        busy = main.stim_out is not None and main.stim_out.busy
        state = state.replace(" on the Record page", "")
        self.lamp.setText(f"<span style='color:{'#1e8e3e' if ok else t['error']}'>●</span>")
        self.lamp.setToolTip(state)
        self.btn_fire.setEnabled(ok and not busy and not page.repeating)
        self.btn_repeat.setEnabled(ok and not page.repeating)
        self.btn_stop.setEnabled(page.repeating or busy or main.repeating)
        last = page.last_sent()
        msg = page.message.text()
        if msg and page.message.styleSheet().find(t["error"]) >= 0:
            self.last.setStyleSheet(f"color: {t['error']}")
            self.last.setText(msg)
        else:
            self.last.setStyleSheet(f"color: {t['subtle']}")
            text = (state if not ok or page.repeating else
                    (f"Last: {' · '.join(last.split('   ')[:2])}" if last else "Ready"))
            if msg and ok and not page.repeating:        # why repeating stopped, a preset loaded, …
                text = f"{msg}\n{text}"
            self.last.setText(text)

    def _fold(self, folded: bool) -> None:
        self.body.setVisible(not folded)
        self.btn_fold.setArrowType(QtCore.Qt.ArrowType.RightArrow if folded else QtCore.Qt.ArrowType.DownArrow)
        self.main.settings.setValue("dock_stim_open", not folded)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.refresh()
        self.timer.start()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.timer.stop()

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        t = style.tokens(mode)
        self.setStyleSheet(f"QFrame#dockStim {{ background: {t['ground']}; border-top: 1px solid {t['line']}; }}"
                           f"QPushButton {{ padding: 3px 6px; }}")
        self.btn_more.setStyleSheet(f"QPushButton {{ color: {t['focus']}; background: transparent; border: none;"
                                    f" padding: 0 2px; }}")
        self.sync()


class DockedDesign(QtWidgets.QFrame):
    """Complex Stimulus's stimulus, docked under Trials instead: a small picture of every
    trial, what it is, Run (or Send, for one stimulus) and Stop, and the progress. It's
    changed on Complex Stimulus ("edit…")."""

    MAX_DRAWN = 10                   # trials drawn in the small picture

    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self.setObjectName("dockStim")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(230)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        head = QtWidgets.QHBoxLayout()
        self.lamp = QtWidgets.QLabel()
        self.title = QtWidgets.QLabel("<b>Complex Stimulus</b>")
        self.btn_edit = QtWidgets.QPushButton("edit…")
        self.btn_edit.setFlat(True)
        self.btn_edit.setToolTip("Open Acquisition › Complex Stimulus to change the stimulus or the trials")
        head.addWidget(self.lamp)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.btn_edit)
        v.addLayout(head)
        self.picture = StimPicture()
        self.picture.compact = True
        self.picture.setMinimumHeight(80)
        self.picture.setMaximumHeight(80)
        v.addWidget(self.picture)
        self.what = QtWidgets.QLabel()
        self.what.setWordWrap(True)
        v.addWidget(self.what)
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(4)
        self.btn_run = QtWidgets.QPushButton("Run")
        self.btn_run.setObjectName("start")
        self.btn_run.setMinimumHeight(30)
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setToolTip("Stop the protocol or stimulus and set the output to 0 V (Esc)")
        btns.addWidget(self.btn_run, 2)
        btns.addWidget(self.btn_stop, 1)
        v.addLayout(btns)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        v.addWidget(self.progress)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.btn_edit.clicked.connect(lambda: main.tabs.show_tab("stim"))
        self.btn_run.clicked.connect(self._run)
        self.btn_stop.clicked.connect(main.stop_stimulus)
        main.trials_page.placed.connect(self.redraw)
        self.timer = QtCore.QTimer(self, interval=300, timeout=self.refresh)

    def _run(self) -> None:
        tr = self.main.trials_page
        if tr.varying:
            if not tr.run_protocol():
                self.refresh()                  # its summary says what's missing
        else:
            self.main.fire_stimulus()

    def redraw(self) -> None:
        """The small picture: every trial (up to MAX_DRAWN), the first bold."""
        if not self.isVisible():
            return
        main = self.main
        s = stimmod.to_segments(main.stimulus)
        n = main.stimulus_page.n_trials.value() if hasattr(main, "stimulus_page") else 1
        waves, ids = [], []
        for t in list(range(1, min(n, self.MAX_DRAWN))) + [0]:
            try:
                waves.append(s.at_trial(t).waveform(main.cfg.stim_rate))
                ids.append(t)
            except (ValueError, OSError):
                pass
        self.picture.set_data(waves, [], [], -1, [], main.cfg.stim_rate, "V", 1.0, trial_ids=ids, n_trials=n)
        self.refresh()

    def refresh(self) -> None:
        main, tr = self.main, self.main.trials_page
        t = style.tokens(self._mode)
        running = tr.running
        busy = main.stim_out is not None and main.stim_out.busy
        if tr.varying and tr.chk is not None:
            c = tr.chk
            what = (f"{tr.protocol.name}: {c.n_trials} stimuli" if c.ok else
                    (c.errors[0] if c.errors else "Not ready"))
        else:
            what = main.describe_stimuli()
        self.what.setText(what if len(what) < 140 else what[:137] + "…")
        if running:
            n = min(tr.next_i, len(tr.trials))
            self.progress.setMaximum(max(1, len(tr.trials)))
            self.progress.setValue(n)
            state, ok = f"Stimulus {n} of {len(tr.trials)}" + (" · paused" if tr.paused else ""), True
        elif main.mode != "live":
            state, ok = "Go back to live mode to send stimuli.", False
        elif tr.varying and tr.chk is not None and not tr.chk.ok:
            state, ok = "Fix it on Complex Stimulus (edit…).", False
        elif tr.varying and not main.cfg.stim_monitor:
            state, ok = "Record the stimulus first (Complex Stimulus › Output).", False
        else:
            state, ok = "Ready", True
        self.progress.setVisible(running)
        self.status.setText(state)
        self.status.setStyleSheet(f"color: {t['subtle'] if ok else t['error']}")
        self.lamp.setText(f"<span style='color:{'#1e8e3e' if ok else t['error']}'>●</span>")
        self.btn_run.setText("Run" if tr.varying else "Send")
        self.btn_run.setEnabled(ok and not running and not busy)
        self.btn_stop.setEnabled(running or busy or main.repeating)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.redraw()
        self.timer.start()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.timer.stop()

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        t = style.tokens(mode)
        self.setStyleSheet(f"QFrame#dockStim {{ background: {t['ground']}; border-top: 1px solid {t['line']}; }}"
                           f"QPushButton {{ padding: 3px 6px; }}")
        self.btn_edit.setStyleSheet(f"QPushButton {{ color: {t['focus']}; background: transparent; border: none;"
                                    f" padding: 0 2px; }}")
        self.what.setStyleSheet(f"color: {t['muted']}")
        self.picture.apply_theme(mode)
        self.redraw()


class DockedTimed(QtWidgets.QFrame):
    """With the run bar's Stimulus on None, under Trials: Start acquires for a set time,
    Repeats times, with an optional wait between (acquiring stops while it waits). With
    Record on, each one is a trial of its own. Acquiring stops after the last."""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self.rep = 0                      # 1..n while running, 0 when not
        self.waiting = False
        self._until = 0.0                 # when this acquisition (or wait) ends, monotonic
        self.setObjectName("dockStim")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(230)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        v.addWidget(QtWidgets.QLabel("<b>Timed acquisition</b>"))
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        st = main.settings
        self.length = _small_spin(0, 36000, 1, "s", "How long each acquisition lasts after Start. "
                                  "0 (until Stop): Start acquires until you press Stop")
        self.length.setSpecialValueText("until Stop")
        self.length.setValue(float(st.value("timed_s", 0.0)))
        self.repeats = QtWidgets.QSpinBox(minimum=1, maximum=1000)
        self.repeats.setKeyboardTracking(False)
        self.repeats.setToolTip("How many acquisitions Start makes; with Record on, each is a trial of its own")
        self.repeats.setValue(int(st.value("timed_n", 1)))
        self.wait = _small_spin(0, 36000, 1, "s", "Time between one acquisition's end and the next one's start "
                                "(acquiring stops while waiting). 0: straight after")
        self.wait.setValue(float(st.value("timed_wait", 0.0)))
        form.addRow("Acquire for", self.length)
        form.addRow("Repeats", self.repeats)
        form.addRow("Wait", self.wait)
        v.addLayout(form)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        v.addWidget(self.progress)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        for key, w in (("timed_s", self.length), ("timed_n", self.repeats), ("timed_wait", self.wait)):
            w.valueChanged.connect(lambda val, key=key: (self.main.settings.setValue(key, val), self._show()))
        self.timer = QtCore.QTimer(self, interval=100, timeout=self._tick)
        self._show()

    @property
    def running(self) -> bool:
        return self.rep > 0

    def begin(self) -> None:
        """Start was pressed (acquiring has started): the first acquisition (none to time
        with Acquire for at 0: acquiring goes on until Stop)."""
        import time
        if self.length.value() <= 0:
            return
        self.rep, self.waiting = 1, False
        self._until = time.monotonic() + self.length.value()
        self.timer.start()
        self._lock(True)
        self._show()

    def halt(self) -> None:
        """Stop, Esc, or the last one ended."""
        if not self.running:
            return
        self.rep, self.waiting = 0, False
        self.timer.stop()
        self._lock(False)
        self._show()

    def _lock(self, on: bool) -> None:
        for w in (self.length, self.repeats, self.wait):
            w.setEnabled(not on)

    def _tick(self) -> None:
        import time
        main = self.main
        if not main.btn_run.isChecked():
            self.halt()
            return
        now = time.monotonic()
        if now < self._until:
            self._show(now)
            return
        if self.waiting:                                 # the wait is over: the next one
            self.waiting = False
            self._until = now + self.length.value()
            if not main.resume_acquiring(keep_trace=True):
                self.halt()
                return
        elif self.rep >= self.repeats.value():           # the last one: done
            self.halt()
            main.btn_run.setChecked(False)
            main.statusBar().showMessage("Timed acquisition finished", 10000)
            return
        else:
            self.rep += 1
            if self.wait.value() > 0:                    # stop acquiring until the next
                self.waiting = True
                self._until = now + self.wait.value()
                main.rest_acquiring()
                main._lock_channels(True)
            else:                                        # straight on: a new trial, same stream
                self._until = now + self.length.value()
                if main.recorder.recording:
                    main._stop_block()
                    main._start_block()
        self._show(now)

    def _show(self, now: float | None = None) -> None:
        import time
        n, t, w = self.repeats.value(), self.length.value(), self.wait.value()
        self.repeats.setEnabled(t > 0 and not self.running)
        self.wait.setEnabled(t > 0 and n > 1 and not self.running)
        if not self.running:
            total = n * t + (n - 1) * w
            self.progress.setVisible(False)
            if t <= 0:
                self.status.setText("Start acquires until you press Stop")
                return
            self.status.setText(f"Start acquires {n} × {t:g} s" + (f", {w:g} s apart" if n > 1 and w else "")
                                + (f" ({total:g} s in all)" if n > 1 else ""))
            return
        now = time.monotonic() if now is None else now
        left = max(0.0, self._until - now)
        self.progress.setVisible(True)
        self.progress.setMaximum(n * 100)
        frac = 0.0 if self.waiting else 1.0 - left / max(t, 1e-9)     # of this one (none yet, waiting)
        self.progress.setValue(int((self.rep - 1 + frac) * 100))
        self.status.setText(f"Repeat {self.rep} of {n} · next in {left:.0f} s" if self.waiting
                            else f"Repeat {self.rep} of {n} · {left:.0f} s left")

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        t = style.tokens(mode)
        self.setStyleSheet(f"QFrame#dockStim {{ background: {t['ground']}; border-top: 1px solid {t['line']}; }}"
                           f"QPushButton {{ padding: 3px 6px; }}")
        self.status.setStyleSheet(f"color: {t['muted']}")
