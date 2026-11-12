"""The Stimulator tab: design a stimulus, see it, fire it, and choose the output.

Every change applies straight away to the stimulus that Fire and Repeat send,
so there is no Apply button. The preview shows exactly the samples that will
be written to the output.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import style
from .plotzoom import plot_widget
from .unitbox import UnitSpinBox
from .units import _converter, _same_kind, parse_quantity

from . import stim as stimmod
from .stim import Stimulus


def _spin(lo, hi, decimals=4, unit="", step=1.0):
    return UnitSpinBox(lo, hi, unit, decimals=decimals, step=step)


# The preview's line colour for the first and second output: (light mode, dark mode).
OUTPUT_COLORS = {False: ("#1a5fd0", "#b36b00"), True: ("#4d8dff", "#e69f00")}


def _dot(color: str) -> QtGui.QIcon:
    pix = QtGui.QPixmap(20, 20)
    pix.setDevicePixelRatio(2.0)
    pix.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor(color))
    p.drawEllipse(QtCore.QRectF(1, 1, 8, 8))
    p.end()
    return QtGui.QIcon(pix)


class StimPage(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._colors = OUTPUT_COLORS[False]
        self._loading = False
        self._units = None                 # (units, scale) the level fields currently show
        self._mode = style.LIGHT
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(16)

        # ---- left: which output, then its shape and settings ----------------
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        self.editing = 0                   # 0: the first output's stimulus, 1: the second's
        outs = QtWidgets.QHBoxLayout()
        outs.setSpacing(8)
        self.out_tabs = QtWidgets.QTabBar()
        self.out_tabs.setDrawBase(False)
        self.out_tabs.setExpanding(False)
        self.out_tabs.setToolTip("Each output has its own stimulus; Fire sends them together")
        self.btn_add_out = QtWidgets.QPushButton("+ Add second output")
        self.btn_add_out.setToolTip("Send a second stimulus on the other analog output, from the same start")
        self.btn_remove_out = None         # the ✕ on the second tab: made with the tab (Qt deletes it with it)
        outs.addWidget(self.out_tabs)
        outs.addWidget(self.btn_add_out)
        outs.addStretch(1)
        left.addLayout(outs)
        top = QtWidgets.QFormLayout()
        self.shape = QtWidgets.QComboBox()
        self.shape.setToolTip("The kind of stimulus: its settings are shown below")
        for key, label in stimmod.SHAPES.items():
            self.shape.addItem(label, key)
        self.delay = _spin(0, 60000, unit="ms")
        self.delay.setToolTip("0 V for this long before the stimulus starts")
        top.addRow("Shape", self.shape)
        top.addRow("Delay", self.delay)
        left.addLayout(top)

        self.pages = QtWidgets.QStackedWidget()
        # pulse
        pw = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(pw); f.setContentsMargins(0, 0, 0, 0)
        self.p_amp = _spin(-10, 10, unit="V", step=0.1)
        self.p_amp.setToolTip("Height of each pulse")
        self.p_width = _spin(0, 60000, unit="ms", step=0.2)
        self.p_width.setToolTip("How long each pulse lasts")
        self.p_count = QtWidgets.QSpinBox(minimum=1, maximum=10000)
        self.p_count.setToolTip("How many pulses in the train")
        self.p_interval = _spin(0, 60000, unit="ms", step=10)
        self.p_interval.setToolTip("From the start of one pulse to the start of the next")
        self.p_bi = QtWidgets.QCheckBox("Biphasic (+ then −)")
        self.p_bi.setToolTip("Each pulse goes up, then down by the same amount, so it adds up to no net charge")
        f.addRow("Amplitude", self.p_amp); f.addRow("Width", self.p_width)
        f.addRow("Pulses", self.p_count); f.addRow("Interval", self.p_interval); f.addRow("", self.p_bi)
        self.pages.addWidget(pw)
        # step
        sw = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(sw); f.setContentsMargins(0, 0, 0, 0)
        self.s_amp = _spin(-10, 10, unit="V", step=0.1)
        self.s_amp.setToolTip("The voltage held during the step")
        self.s_dur = _spin(0, 60000, unit="ms", step=10)
        self.s_dur.setToolTip("How long the step lasts")
        f.addRow("Level", self.s_amp); f.addRow("Duration", self.s_dur)
        self.pages.addWidget(sw)
        # ramp
        rw = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(rw); f.setContentsMargins(0, 0, 0, 0)
        self.r_start = _spin(-10, 10, unit="V", step=0.1)
        self.r_start.setToolTip("The voltage the ramp starts at")
        self.r_end = _spin(-10, 10, unit="V", step=0.1)
        self.r_end.setToolTip("The voltage the ramp ends at")
        self.r_dur = _spin(0, 60000, unit="ms", step=10)
        self.r_dur.setToolTip("How long the ramp lasts")
        f.addRow("From", self.r_start); f.addRow("To", self.r_end); f.addRow("Duration", self.r_dur)
        self.pages.addWidget(rw)
        # sine
        nw = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(nw); f.setContentsMargins(0, 0, 0, 0)
        self.n_amp = _spin(0, 10, unit="V", step=0.1)
        self.n_amp.setToolTip("Height of the sine wave above and below its offset")
        self.n_off = _spin(-10, 10, unit="V", step=0.1)
        self.n_off.setToolTip("The voltage the sine wave swings around")
        self.n_freq = _spin(0.01, 2500, unit="Hz", step=1)
        self.n_freq.setToolTip("Cycles per second")
        self.n_dur = _spin(0, 60000, unit="ms", step=10)
        self.n_dur.setToolTip("How long the sine wave lasts")
        f.addRow("Amplitude", self.n_amp); f.addRow("Offset", self.n_off)
        f.addRow("Frequency", self.n_freq); f.addRow("Duration", self.n_dur)
        self.pages.addWidget(nw)
        # custom
        cw = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(cw); v.setContentsMargins(0, 0, 0, 0)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Duration (ms)", "Start (V)", "End (V)"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setToolTip("Each row is a straight line from Start to End. Same Start and End = a flat level. "
                              "Cells take units too, e.g. 0.5 s or 200 mV.")
        rowbtns = QtWidgets.QHBoxLayout()
        self.btn_add_seg = QtWidgets.QPushButton("Add segment")
        self.btn_del_seg = QtWidgets.QPushButton("Remove segment")
        rowbtns.addWidget(self.btn_add_seg); rowbtns.addWidget(self.btn_del_seg); rowbtns.addStretch(1)
        v.addWidget(self.table, 1); v.addLayout(rowbtns)
        self.pages.addWidget(cw)
        # file
        fw = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(fw); f.setContentsMargins(0, 0, 0, 0)
        frow = QtWidgets.QHBoxLayout()
        self.f_path = QtWidgets.QLineEdit(placeholderText="CSV or text file")
        self.f_path.setToolTip("The file of voltages to send: one column, or time and voltage")
        self.btn_browse = QtWidgets.QPushButton("Browse…")
        frow.addWidget(self.f_path, 1); frow.addWidget(self.btn_browse)
        self.f_rate = _spin(0, 1e6, decimals=2, unit="Hz", step=100)
        self.f_rate.setSpecialValueText("same as output")
        self.f_rate.setToolTip("Sample rate of a one-column file. Two-column files (time in s, volts) don't need it.")
        f.addRow("File", frow); f.addRow("File's rate", self.f_rate)
        self.f_hint = QtWidgets.QLabel()
        self.f_hint.setWordWrap(True)
        f.addRow("", self.f_hint)
        self.pages.addWidget(fw)
        left.addWidget(self.pages, 1)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        left.addWidget(line)
        bottom = QtWidgets.QFormLayout()
        self.output = QtWidgets.QComboBox()
        self.output.addItems(list(stimmod.AO_CHANNELS))
        self.output.setToolTip("Which analog output sends the first stimulus (a second one uses the other)")
        self.monitor = QtWidgets.QComboBox()
        self.monitor.setToolTip("Record the stimulus on a spare input (wire the output to it). "
                                "This adds a channel named Stim with exact timing.")
        self.repeat = _spin(0.05, 3600, unit="s", step=0.5)
        self.repeat.setToolTip("Repeat fires the stimulus this often")
        self.on_start = QtWidgets.QComboBox()
        for label, key in (("Do nothing", ""), ("Fire once", "once"), ("Start repeating", "repeat")):
            self.on_start.addItem(label, key)
        self.on_start.setToolTip("What the stimulator does when you press Start on the toolbar")
        bottom.addRow("Output", self.output)
        bottom.addRow("Record on", self.monitor)
        bottom.addRow("Repeat every", self.repeat)
        bottom.addRow("When Start is pressed", self.on_start)
        left.addLayout(bottom)
        self.sim_note = QtWidgets.QLabel()            # simulated backend: where the stimulus goes
        self.sim_note.setWordWrap(True)
        self.sim_note.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.sim_note.linkActivated.connect(lambda *_: self.main.tabs.show_tab("sim"))
        self.sim_note.setVisible(False)
        left.addWidget(self.sim_note)
        btns = QtWidgets.QHBoxLayout()
        self.btn_fire = QtWidgets.QPushButton("Fire")
        self.btn_fire.setObjectName("start")
        self.btn_fire.setToolTip("Send the stimulus once (Ctrl+F)")
        self.btn_repeat = QtWidgets.QPushButton("Repeat", checkable=True)
        self.btn_repeat.setToolTip("Keep sending it every few seconds until you press Stop")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setToolTip("Stop the stimulus and set the output to 0 V (Esc)")
        self.btn_save = QtWidgets.QPushButton("Save…")
        self.btn_load = QtWidgets.QPushButton("Load…")
        for b in (self.btn_fire, self.btn_repeat, self.btn_stop):
            btns.addWidget(b)
        btns.addStretch(1)
        for b in (self.btn_save, self.btn_load):
            btns.addWidget(b)
        left.addLayout(btns)
        outer.addLayout(left, 0)

        # ---- right: preview ----------------------------------------------
        right = QtWidgets.QVBoxLayout()
        self.preview = plot_widget()
        p = self.preview.getPlotItem()
        p.setLabel("left", "Output", units="V")
        p.setLabel("bottom", "time", units="s")
        p.showGrid(x=True, y=True, alpha=0.25)
        self.curve = p.plot(stepMode=None)
        self.curve2 = p.plot(stepMode=None)             # the second output's stimulus
        right.addWidget(self.preview, 1)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        right.addWidget(self.info)
        right.addWidget(self.message)
        outer.addLayout(right, 1)

        # ---- signals -------------------------------------------------------
        self._apply_units()
        self.set_stimulus(main.stimulus)
        self.repeat.setValue(main.stim_repeat_s)
        self.on_start.setCurrentIndex(max(0, self.on_start.findData(main.stim_on_start)))
        self.on_start.currentIndexChanged.connect(
            lambda *_: self.main.set_stim_on_start(self.on_start.currentData()))
        self.shape.currentIndexChanged.connect(self._shape_changed)
        for w in (self.delay, self.p_amp, self.p_width, self.p_interval, self.s_amp, self.s_dur,
                  self.r_start, self.r_end, self.r_dur, self.n_amp, self.n_off, self.n_freq, self.n_dur,
                  self.f_rate):
            w.valueChanged.connect(self._changed)
        self.p_count.valueChanged.connect(self._changed)
        self.p_bi.toggled.connect(self._changed)
        self.f_path.editingFinished.connect(self._changed)
        self.table.itemChanged.connect(self._changed)
        self.btn_add_seg.clicked.connect(self._add_segment)
        self.btn_del_seg.clicked.connect(self._remove_segment)
        self.btn_browse.clicked.connect(self._browse)
        self.repeat.valueChanged.connect(lambda v: setattr(self.main, "stim_repeat_s", float(v)))
        self.btn_fire.clicked.connect(lambda: self.main.fire_stimulus())
        self.btn_repeat.toggled.connect(self.main.set_repeat)
        self.btn_stop.clicked.connect(self.main.stop_stimulus)
        self.output.activated.connect(lambda *_: self.main.set_stim_output(self.output.currentText()))
        self.monitor.activated.connect(lambda *_: self._monitor_picked())
        self.sync()
        self.apply_theme(style.LIGHT)
        self.btn_save.clicked.connect(self._save)
        self.btn_load.clicked.connect(self._load)
        self.out_tabs.currentChanged.connect(self._output_picked)
        self.btn_add_out.clicked.connect(self._add_output)
        self.outputs_changed()

    # -- which output ----------------------------------------------------------
    def _current(self) -> Stimulus:
        """The stimulus the fields edit: the first output's, or the second's."""
        return self.main.stimulus2 if self.editing == 1 and self.main.stimulus2 is not None else self.main.stimulus

    def outputs_changed(self) -> None:
        """Show a tab per output in use, and the add / remove buttons to match."""
        main = self.main
        two = main.stimulus2 is not None
        if not two:
            self.editing = 0
        labels = [main.cfg.stim_output] + ([main.second_output] if two else [])
        self.out_tabs.blockSignals(True)
        while self.out_tabs.count():
            self.out_tabs.removeTab(0)
        for name, color in zip(labels, (self._colors[0], self._colors[1])):
            self.out_tabs.addTab(_dot(color), name)          # the colour of its line in the preview
        self.btn_remove_out = None
        if two:
            btn = QtWidgets.QToolButton(text="✕")
            btn.setAutoRaise(True)
            btn.setToolTip("Stop using the second output")
            btn.setAccessibleName("Remove the second output")
            btn.clicked.connect(lambda: QtCore.QTimer.singleShot(0, lambda: self.main.set_stimulus2(None)))
            self.out_tabs.setTabButton(1, QtWidgets.QTabBar.ButtonPosition.RightSide, btn)
            self.btn_remove_out = btn
        self._show_sim_note()
        self.out_tabs.setCurrentIndex(self.editing)
        self.out_tabs.blockSignals(False)
        self.btn_add_out.setVisible(not two)
        self.btn_add_out.setText(f"+ Add second output ({main.second_output})")
        self.btn_fire.setText("Fire both" if two else "Fire")
        self._load_fields(self._current())

    def _output_picked(self, i: int) -> None:
        if i < 0 or i == self.editing:
            return
        self.editing = i
        self._load_fields(self._current())

    def _add_output(self) -> None:
        self.editing = 1
        self.main.set_stimulus2(Stimulus())       # calls outputs_changed

    # -- widgets <-> Stimulus ------------------------------------------------
    def set_stimulus(self, s: Stimulus) -> None:
        """The first output's stimulus changed elsewhere (a protocol, Load): show it if
        that's the one being edited."""
        if self.editing == 0 or self.main.stimulus2 is None:
            self._load_fields(s)
        else:
            self._update_preview()

    def _load_fields(self, s: Stimulus) -> None:
        self._loading = True
        self.shape.setCurrentIndex(max(0, self.shape.findData(s.shape)))
        self.pages.setCurrentIndex(self.shape.currentIndex())
        self.delay.setValue(s.delay_ms)
        k = self._scale
        self.p_amp.setValue(s.amplitude_v * k); self.p_width.setValue(s.width_ms)
        self.p_count.setValue(s.count); self.p_interval.setValue(s.interval_ms); self.p_bi.setChecked(s.biphasic)
        self.s_amp.setValue(s.amplitude_v * k); self.s_dur.setValue(s.duration_ms)
        self.r_start.setValue(s.start_v * k); self.r_end.setValue(s.end_v * k); self.r_dur.setValue(s.duration_ms)
        self.n_amp.setValue(abs(s.amplitude_v) * k); self.n_off.setValue(s.offset_v * k)
        self.n_freq.setValue(s.freq_hz); self.n_dur.setValue(s.duration_ms)
        self.table.setRowCount(0)
        for seg in s.segments:
            self._append_row(seg["duration_ms"], seg["start_v"] * k, seg["end_v"] * k)
        self.f_path.setText(s.file); self.f_rate.setValue(s.file_rate)
        self._loading = False
        self._changed()          # the stimulus Fire sends and the preview both follow the fields

    def stimulus(self) -> Stimulus:
        """The stimulus the fields describe. Raises ValueError for a bad table cell."""
        shape = self.shape.currentData()
        s = Stimulus(shape=shape, delay_ms=self.delay.value())
        v = lambda box: box.value() / self._scale          # the fields show units; Stimulus keeps volts
        if shape == "pulse":
            s.amplitude_v, s.width_ms = v(self.p_amp), self.p_width.value()
            s.count, s.interval_ms, s.biphasic = self.p_count.value(), self.p_interval.value(), self.p_bi.isChecked()
        elif shape == "step":
            s.amplitude_v, s.duration_ms = v(self.s_amp), self.s_dur.value()
        elif shape == "ramp":
            s.start_v, s.end_v, s.duration_ms = v(self.r_start), v(self.r_end), self.r_dur.value()
        elif shape == "sine":
            s.amplitude_v, s.offset_v = v(self.n_amp), v(self.n_off)
            s.freq_hz, s.duration_ms = self.n_freq.value(), self.n_dur.value()
        elif shape == "file":
            s.file, s.file_rate = self.f_path.text().strip(), self.f_rate.value()
        s.segments = self._segments() if shape == "custom" else self._current().segments
        return s

    def _segments(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            vals = []
            for c, name in enumerate(("duration", "start", "end")):
                item = self.table.item(r, c)
                # Cells take units like the boxes do: "0.5 s", "200 mV".
                convert = _same_kind("ms") if c == 0 else self._level_factor
                try:
                    vals.append(parse_quantity(item.text() if item else "", convert)[0])
                except ValueError:
                    raise ValueError(f"Segment {r + 1}: {name} must be a number, optionally with "
                                     f"a unit (e.g. {'0.5 s' if c == 0 else '200 mV'}).") from None
            out.append({"duration_ms": vals[0], "start_v": vals[1] / self._scale,
                        "end_v": vals[2] / self._scale})
        return out

    # -- reacting to edits ---------------------------------------------------
    def _shape_changed(self, i):
        self.pages.setCurrentIndex(i)
        self._changed()

    def _changed(self, *_):
        if self._loading:
            return
        try:
            s = self.stimulus()
        except ValueError as e:
            self._show_problem(str(e))
            return
        # Loading fields from a stimulus gives the same stimulus back: only a real change is
        # passed on (each one re-checks the whole protocol on the Protocols page).
        if s != self._current():
            if self.editing == 1 and self.main.stimulus2 is not None:
                self.main._stimulus2_edited(s)
            else:
                self.main._stimulus_edited(s)
        self._update_preview()

    def _update_preview(self):
        cfg, main = self.main.cfg, self.main
        units, k = self._unit_label, self._scale
        in_units = f" ({cfg.stim_max_v * k:g} {units})" if self._converted else ""
        two = main.stimulus2 is not None
        where = f"{cfg.stim_output} + {main.second_output} together" if two else f"Output {cfg.stim_output}"
        self.info.setText(f"{where} · {cfg.stim_rate:,.0f} samples/s{' each' if two else ''} · "
                          f"limit ±{cfg.stim_max_v:g} V{in_units} (set in the config)")
        streams = [(cfg.stim_output, main.stimulus, self.curve)] + (
            [(main.second_output, main.stimulus2, self.curve2)] if two else [])
        if not two:
            self.curve2.setData([], [])
        lines, waves = [], []
        for name, s, curve in streams:
            try:
                wave = s.waveform(cfg.stim_rate)
                stimmod.check_limit(wave, cfg.stim_max_v)
            except ValueError as e:
                curve.setData([], [])
                self._show_problem(f"{name}: {e}" if two else str(e))
                return
            t = np.arange(len(wave)) / cfg.stim_rate
            # draw each sample as a flat step, which is what the output really does
            curve.setData(np.repeat(t, 2)[1:], np.repeat(wave * k, 2)[:-1])
            peak = float(np.max(np.abs(wave))) * k
            label = f"{name}: " if two else ""
            lines.append(f"{label}{s.describe(units, k)}" + ("" if two else
                         f"\nPeak {peak:g} {units} · {len(wave) / cfg.stim_rate * 1000:g} ms · {len(wave):,} samples"))
            lines += [f"{label}{n}" for n in s.rounding_notes(cfg.stim_rate)]
            waves.append(wave)
        self.preview.getPlotItem().enableAutoRange()      # a new stimulus can change the scale
        if two:
            n = max(len(w) for w in waves)
            lines.append(f"Together {n / cfg.stim_rate * 1000:g} ms · {n:,} samples per output")
        self.show_message("\n".join(lines))

    def _show_problem(self, text: str):
        self.show_message(text, error=True)

    def show_message(self, text: str, error: bool = False):
        t = style.tokens(self._mode)
        self.message.setStyleSheet(f"color: {t['error'] if error else t['muted']}")
        self.message.setText(text)

    # -- output, monitor, repeat (owned by the main window) --------------------
    def sync(self) -> None:
        """Show the main window's current output, monitor input and repeat state."""
        cfg = self.main.cfg
        if self._units != (cfg.stim_units, cfg.stim_scale):
            self._apply_units()
            self._load_fields(self._current())
        self.output.setCurrentText(cfg.stim_output)
        self.monitor.blockSignals(True)
        self.monitor.clear()
        for label, phys, enabled, tip in self.main.monitor_choices():
            self.monitor.addItem(label, phys)
            i = self.monitor.count() - 1
            self.monitor.setItemData(i, tip, QtCore.Qt.ItemDataRole.ToolTipRole)
            if not enabled:
                self.monitor.model().item(i).setEnabled(False)
        self.monitor.setCurrentIndex(max(0, self.monitor.findData(cfg.stim_monitor)))
        self.monitor.blockSignals(False)
        self.btn_repeat.blockSignals(True)
        self.btn_repeat.setChecked(self.main.repeating)
        self.btn_repeat.blockSignals(False)
        self._show_sim_note()
        self._update_preview()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._show_sim_note()

    def _show_sim_note(self) -> None:
        """With the simulated backend, say which cells the stimulus drives, and link to the tab."""
        cfg = self.main.cfg
        self.sim_note.setVisible(cfg.backend == "simulated")
        if cfg.backend != "simulated":
            return
        driven = [f"cell {i + 1} ({c.stimulus:g} mV)" for i, c in enumerate(cfg.sim_network.cells) if c.stimulus]
        what = ("⚡ Simulated: the stimulus is injected into " + ", ".join(driven) + "."
                if driven else "⚡ Simulated: <b>no cell</b> receives the stimulus yet.")
        if self.main.stimulus2 is not None:
            what += f" Only {cfg.stim_output}'s: {self.main.second_output} isn't simulated."
        self.sim_note.setText(f"{what} <a href='sim'>Choose cells on the Neural network simulator page</a>")

    def _monitor_picked(self):
        phys = self.monitor.currentData() or ""
        self.main.set_stim_monitor(phys)
        self.sync()

    def apply_theme(self, mode: str) -> None:
        from . import theme
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        self.preview.setBackground(th.background)
        for ax in ("left", "bottom"):
            self.preview.getPlotItem().getAxis(ax).setPen(th.foreground)
            self.preview.getPlotItem().getAxis(ax).setTextPen(th.foreground)
        self._colors = OUTPUT_COLORS[mode == style.DARK]
        self.curve.setPen(pg.mkPen(self._colors[0], width=2))
        self.curve2.setPen(pg.mkPen(self._colors[1], width=2))
        if hasattr(self, "out_tabs"):
            for i in range(self.out_tabs.count()):
                self.out_tabs.setTabIcon(i, _dot(self._colors[i]))
        self._update_preview()

    # -- units ---------------------------------------------------------------
    @property
    def _scale(self) -> float:
        return self._units[1] if self._units else 1.0

    @property
    def _unit_label(self) -> str:
        return self._units[0] if self._units else "V"

    @property
    def _converted(self) -> bool:
        return self._units not in (None, ("V", 1.0))

    def _level_families(self) -> list:
        """Units a level can be typed in: the configured one with any prefix (mA in a
        µA field), or volts at the output (mV, V), converted with the scale."""
        units, k = self._units or ("V", 1.0)
        return [(units, 1.0), ("V", k)]

    def _level_factor(self, typed: str) -> float | None:
        return _converter(self._level_families())(typed)

    def _apply_units(self) -> None:
        """Label and size the level fields for the [stimulator] units setting. The DAQ
        output is ±10 V, so the fields go to ±10 V × scale in those units."""
        cfg = self.main.cfg
        self._units = (cfg.stim_units, cfg.stim_scale)
        units, k = self._units
        # enough decimals to step 0.1 mV of output, e.g. 0.0001 V or 0.00001 mA at 0.1 mA/V
        decimals = max(4, min(8, 4 - math.floor(math.log10(k))))
        for box in (self.p_amp, self.s_amp, self.r_start, self.r_end, self.n_amp, self.n_off):
            box.blockSignals(True)
            box.setDecimals(decimals)
            box.setRange(0 if box is self.n_amp else -10 * k, 10 * k)
            box.setSingleStep(0.1 * k)
            box.set_unit(units, self._level_families())
            box.blockSignals(False)
        self.table.setHorizontalHeaderLabels(["Duration (ms)", f"Start ({units})", f"End ({units})"])
        self.f_hint.setText("One column: volts per sample. Two columns: time (s), volts."
                            + (" Files are always in volts at the output." if self._converted else ""))
        axis = self.preview.getPlotItem().getAxis("left")
        if units == "V":
            self.preview.getPlotItem().setLabel("left", "Output", units="V")
        else:
            # pyqtgraph would add a prefix to a unit that may already have one (µA -> kµA)
            self.preview.getPlotItem().setLabel("left", f"Output ({units})")
        axis.enableAutoSIPrefix(units == "V")

    # -- custom segments -----------------------------------------------------
    def _append_row(self, dur, start, end):
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate((dur, start, end)):
            self.table.setItem(r, c, QtWidgets.QTableWidgetItem(f"{float(v):g}"))

    def _add_segment(self):
        last = self.table.rowCount() - 1
        end = self.table.item(last, 2).text() if last >= 0 and self.table.item(last, 2) else "0"
        try:
            end = parse_quantity(end, self._level_factor)[0]   # e.g. "200 mV" typed in that cell
        except ValueError:
            end = 0.0
        self.table.blockSignals(True)
        self._append_row(10, end, end)
        self.table.blockSignals(False)
        self._changed()

    def _remove_segment(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()} or {self.table.rowCount() - 1})
        self.table.blockSignals(True)
        for r in reversed(rows):
            if r >= 0:
                self.table.removeRow(r)
        self.table.blockSignals(False)
        self._changed()

    # -- files -----------------------------------------------------------------
    def _browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Waveform file", "",
                                                        "Waveform (*.csv *.txt *.tsv);;All files (*)")
        if path:
            self.f_path.setText(path)
            self._changed()

    def _save(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save stimulus", "stimulus.json",
                                                        "Stimulus (*.json)")
        if path:
            self.main.stimulus.save(path, second=self.main.stimulus2)
            self.show_message(f"Saved to {Path(path).name}")

    def _load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load stimulus", "", "Stimulus (*.json)")
        if not path:
            return
        try:
            s, s2 = Stimulus.load_pair(path)      # files from before two outputs: s2 is None
        except ValueError as e:
            self.show_message(str(e), error=True)
            return
        self.editing = 0
        self.main.set_stimulus(s)
        self.main.set_stimulus2(s2)


StimWindow = StimPage   # older name
