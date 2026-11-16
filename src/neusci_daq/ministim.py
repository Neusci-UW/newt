"""A small stimulator floating over the Record page's plots, like LabChart's Stimulator
Panel: run or fire the stimulus, stop it, see a protocol's progress, and nudge the
stimulus's main level, without going to Acquisition › Stimulus Design.

The main level is the first segment that isn't a baseline, on the first output; with
per-trial changes it's trial 1's, and the others follow from it.
"""
from __future__ import annotations

from dataclasses import replace

from pyqtgraph.Qt import QtCore, QtWidgets

from . import stim as stimmod
from . import style

STEPS_PER_V = 20                       # the level slider moves in 0.05 V at the output


class MiniStimulator(QtWidgets.QFrame):
    def __init__(self, main, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.main = main
        self.setObjectName("miniStim")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(270)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(6)
        title = QtWidgets.QLabel("<b>Stimulator</b>")
        self.btn_edit = QtWidgets.QPushButton("Edit…")
        self.btn_edit.setToolTip("Open Acquisition › Stimulus Design")
        self.btn_edit.setStyleSheet("QPushButton { padding: 1px 8px; }")
        self.btn_close = QtWidgets.QToolButton(text="✕", autoRaise=True)
        self.btn_close.setToolTip("Hide the stimulator (the Stimulator button below brings it back)")
        self.btn_close.setAccessibleName("Hide the stimulator")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.btn_edit)
        head.addWidget(self.btn_close)
        v.addLayout(head)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        v.addWidget(self.progress)
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(6)
        self.btn_run = QtWidgets.QPushButton("Run")
        self.btn_run.setObjectName("start")
        self.btn_fire = QtWidgets.QPushButton("Fire")
        self.btn_fire.setToolTip("Send the stimulus once (trial 1's, in a protocol)")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setToolTip("Stop the stimulus or protocol and set the output to 0 V (Esc)")
        btns.addWidget(self.btn_run, 1)
        btns.addWidget(self.btn_fire)
        btns.addWidget(self.btn_stop)
        v.addLayout(btns)
        lv = QtWidgets.QHBoxLayout()
        lv.setSpacing(6)
        self.level_name = QtWidgets.QLabel("Level")
        self.level = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.level.setRange(-10 * STEPS_PER_V, 10 * STEPS_PER_V)
        self.level.setAccessibleName("Stimulus level")
        self.level_text = QtWidgets.QLabel()
        self.level_text.setMinimumWidth(64)
        self.level_text.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        lv.addWidget(self.level_name)
        lv.addWidget(self.level, 1)
        lv.addWidget(self.level_text)
        v.addLayout(lv)

        self.btn_edit.clicked.connect(lambda: main.tabs.show_tab("stim"))
        self.btn_close.clicked.connect(lambda: main.set_mini_stim(False))
        self.btn_run.clicked.connect(self._run)
        self.btn_fire.clicked.connect(lambda: main.fire_stimulus())
        self.btn_stop.clicked.connect(main.stop_stimulus)
        self.level.valueChanged.connect(self._level_moved)
        self.level.sliderReleased.connect(self.refresh)
        self.timer = QtCore.QTimer(self, interval=250, timeout=self.refresh)
        parent.installEventFilter(self)
        self.apply_theme(style.LIGHT)

    # -- placing -----------------------------------------------------------------------
    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QtCore.QEvent.Type.Resize:
            self._place()
        return False

    def _place(self) -> None:
        par = self.parentWidget()
        self.adjustSize()
        self.move(max(0, par.width() - self.width() - 24), 12)
        self.raise_()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._place()
        self.refresh()
        self.timer.start()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.timer.stop()

    def apply_theme(self, mode: str) -> None:
        t = style.tokens(mode)
        self.setStyleSheet(f"QFrame#miniStim {{ background: {t['panel']}; border: 1px solid {t['field_border']};"
                           f" border-radius: 10px; }}")
        self.status.setStyleSheet(f"color: {t['muted']}")

    # -- the stimulus's main level -------------------------------------------------------------
    def _main_part(self) -> int | None:
        s = self.main.stimulus
        if s.shape != "segments":
            return None
        return next((i for i, p in enumerate(s.parts) if p.get("kind") not in ("base", "file")), None)

    def _level_moved(self, step: int) -> None:
        i = self._main_part()
        if i is None or not self.level.isSliderDown() and not self.level.hasFocus():
            return
        v = step / STEPS_PER_V
        s = self.main.stimulus
        parts = [dict(p) for p in s.parts]
        parts[i]["level"] = v
        self.main.set_stimulus(replace(s, parts=parts))
        self._show_level(v)

    def _show_level(self, volts: float) -> None:
        cfg = self.main.cfg
        self.level_text.setText(f"{volts * cfg.stim_scale:g} {cfg.stim_units}")

    # -- state ---------------------------------------------------------------------------------
    def _run(self) -> None:
        tr = self.main.trials_page
        if tr.varying:
            if not tr.run_protocol():
                self.main.tabs.show_tab("stim")             # its message says what's missing
        else:
            self.main.fire_stimulus()

    def refresh(self) -> None:
        main = self.main
        tr = main.trials_page
        running = tr.running
        busy = main.stim_out is not None and main.stim_out.busy
        if running:
            n = min(tr.next_i, len(tr.trials))
            self.status.setText(f"{tr.protocol.name}: stimulus {n} of {len(tr.trials)}"
                                + (" · paused" if tr.paused else ""))
            self.progress.setMaximum(max(1, len(tr.trials)))
            self.progress.setValue(n)
        else:
            text = main.describe_stimuli()
            if tr.varying and tr.chk is not None and tr.chk.ok:
                text = f"{tr.protocol.name}: {tr.chk.n_trials} stimuli\n" + text
            self.status.setText(text if len(text) < 160 else text[:157] + "…")
        self.progress.setVisible(running)
        self.btn_run.setText("Run" if tr.varying else "Fire")
        self.btn_run.setEnabled(not running and not busy)
        self.btn_fire.setVisible(tr.varying)
        self.btn_fire.setEnabled(not running and not busy)
        self.btn_stop.setEnabled(running or busy or main.repeating)
        i = self._main_part()
        has = i is not None
        for w in (self.level_name, self.level, self.level_text):
            w.setVisible(has)
        if has and not self.level.isSliderDown():
            p = main.stimulus.parts[i]
            self.level_name.setText({"pulse": "Height", "sine": "Amplitude", "ramp": "To"}.get(p["kind"], "Level"))
            self.level.blockSignals(True)
            self.level.setValue(round(float(p["level"]) * STEPS_PER_V))
            self.level.blockSignals(False)
            self._show_level(float(p["level"]))
            self.level.setEnabled(not running)
            self.level.setToolTip(f"Segment {stimmod.letter(i)}'s {self.level_name.text().lower()}"
                                  + (" for trial 1" if tr.varying else ""))
        self._place()
