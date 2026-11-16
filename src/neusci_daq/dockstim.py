"""The stimulator docked under Trials on the Record page: Simple Stimulus's main controls
(pulse type, a small labeled picture, amplitude and width, Stimulate / Repeat / Stop),
so students can stimulate and watch without changing tabs.

It holds no settings of its own: it shows and edits the Simple Stimulus page's (`page`),
so changing one changes the other. The ▾ folds it down to its header.
"""
from __future__ import annotations

from pyqtgraph.Qt import QtCore, QtWidgets

from . import style
from .simplestim import DIM, FIELDS, MARKED, MODES, NAMES, WAVES, PulseDiagram, _Spin

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
        self.count = QtWidgets.QSpinBox(minimum=2, maximum=1000)
        self.count.setKeyboardTracking(False)
        self.count.setToolTip("How many pulses in the train")
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        self.labels = {}
        # (row, column) in a 2-wide grid; boxes that are never shown together share a cell
        self.boxes = {"level": self.level, "width": self.width, "interval": self.interval, "rate": self.rate,
                      "count": self.count, "freq": self.freq, "dur": self.dur}
        cells = {"level": (0, 0), "width": (0, 1), "freq": (0, 1), "interval": (1, 0), "rate": (1, 0),
                 "dur": (1, 0), "count": (1, 1)}
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
        self.btn_repeat.setToolTip("Send it every few seconds until Stop")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setToolTip("Stop sending and set the output to 0 V (Esc)")
        btns.addWidget(self.btn_fire, 2)
        btns.addWidget(self.btn_repeat, 1)
        btns.addWidget(self.btn_stop, 1)
        b.addLayout(btns)
        every = QtWidgets.QHBoxLayout()
        every.setSpacing(4)
        every.addWidget(QtWidgets.QLabel("every"))
        self.every = _small_spin(0.05, 3600, 2, "s", "From the start of one stimulus to the start of the next")
        every.addWidget(self.every)
        self.ramp = QtWidgets.QCheckBox()
        self.ramp.setToolTip("Raise the amplitude after each stimulus (the amount and the limit are on "
                             "Simple Stimulus)")
        every.addWidget(self.ramp)
        every.addStretch(1)
        b.addLayout(every)
        self.last = QtWidgets.QLabel()
        self.last.setWordWrap(True)
        b.addWidget(self.last)
        v.addWidget(self.body)

        # ---- signals ----------------------------------------------------------------
        self.btn_more.clicked.connect(lambda: main.tabs.show_tab("simple"))
        self.btn_fold.toggled.connect(self._fold)
        self.type_group.buttonClicked.connect(lambda *_: self._edited())
        for w in (self.level, self.width, self.interval, self.rate, self.every, self.freq, self.dur):
            w.valueChanged.connect(self._edited)
        self.count.valueChanged.connect(self._edited)
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
                           (self.freq, "freq"), (self.dur, "dur")):
                if w.value() != d[key] and not w.hasFocus():
                    w.setValue(d[key])
            self.type_buttons[d["mode"]].setChecked(True)
            self.ramp.setChecked(d["ramp"])
            units = self.main.cfg.stim_units or "V"
            self.ramp.setText(f"{d['ramp_by']:+g} {units} each")
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
                 ramp=self.ramp.isChecked(), freq=self.freq.value(), dur=self.dur.value())
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
            self.last.setText(state if not ok or page.repeating else
                              (f"Last: {' · '.join(last.split('   ')[:2])}" if last else "Ready"))

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
