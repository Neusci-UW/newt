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
from .units import (_PREFIX, _converter, _same_kind, _tidy_unit, parse_quantity,  # noqa: F401
                    unit_factor)

from . import stim as stimmod
from .stim import Stimulus


# Prefixes a field may show, by base unit, smallest first. Others get µ, m and none.
_SHOW_PREFIXES = {"Hz": ("", "k"), "A": ("n", "µ", "m", "")}


class UnitSpinBox(QtWidgets.QDoubleSpinBox):
    """A number box whose value is in a base unit (`unit`) but which shows it with
    whichever prefix keeps the number between 1 and 999: 10000 ms shows as "10 s",
    0.5 V as "500 mV". Typed units are converted ("1 s" in a ms box: value() is 1000)
    and the kind of unit typed is kept (mV vs µA when both are accepted). A bare
    number is in the unit shown, and the arrows step in it.
    `families` lists other units it takes: [(unit, how many base units one is)]."""

    def __init__(self, lo, hi, unit, families=None, decimals=4, step=1.0):
        super().__init__(minimum=lo, maximum=hi, decimals=decimals, singleStep=step)
        self.setKeyboardTracking(False)       # update when you finish typing, not on every digit
        self.unit = ""
        self.set_unit(unit, families)

    def set_unit(self, unit: str, families=None) -> None:
        """Base unit and accepted units. Shows the base unit again."""
        self.unit = unit
        self._families = families or [(unit, 1.0)]
        self._convert = _converter(self._families)
        self.shown = unit
        self.lineEdit().setText(self.textFromValue(self.value()))

    def _shown_factor(self) -> float:
        convert = getattr(self, "_convert", None)    # Qt asks for text before __init__ finishes
        return (convert(self.shown) if convert else None) or 1.0

    def _tidy(self, typed: str) -> str:
        for fam, _ in self._families:
            if unit_factor(typed, fam) is not None:
                return _tidy_unit(typed, fam)
        return typed

    def _best_unit(self, v: float) -> str:
        """The unit to show v in: same kind as the unit shown now, with the largest
        prefix that keeps the number at least 1 (so under 1000)."""
        fam = next((f for f, _ in self._families if unit_factor(self.shown, f) is not None), None)
        if fam is None or v == 0:
            return self.shown
        base = fam[1:] if len(fam) > 1 and fam[0] in _PREFIX else fam
        options = [u for u in (p + base for p in _SHOW_PREFIXES.get(base, ("µ", "m", ""))) if self._convert(u)]
        if not options:                       # a unit without prefixes (mmHg, /min, none)
            return self.shown
        best = options[0]
        for u in options:
            if abs(v) >= self._convert(u) * (1 - 1e-9):
                best = u
        return best

    def textFromValue(self, v: float) -> str:
        if not hasattr(self, "shown"):
            return f"{v:g}"
        self.shown = self._best_unit(v)
        shown = v / self._shown_factor()
        # as many decimals as the box keeps in base units, in the unit shown
        dec = max(0, self.decimals() + round(np.log10(self._shown_factor())))
        return f"{round(shown, dec):.10g} {self.shown}"

    def valueFromText(self, text: str) -> float:
        if not hasattr(self, "_convert"):            # still in __init__
            return super().valueFromText(text)
        if self.specialValueText() and text == self.specialValueText():
            return self.minimum()
        try:
            v, unit = parse_quantity(text, self._convert, self.shown)
        except ValueError:
            return self.value()
        self.shown = self._tidy(unit)          # show what was typed from now on
        return v

    def validate(self, text: str, pos: int):
        S = QtGui.QValidator.State
        if not hasattr(self, "_convert"):            # still in __init__
            return S.Intermediate, text, pos
        if self.specialValueText() and text == self.specialValueText():
            return S.Acceptable, text, pos
        try:
            v, _ = parse_quantity(text, self._convert, self.shown)
        except ValueError:
            return S.Intermediate, text, pos   # still typing; Qt puts the old value back if left like this
        ok = self.minimum() - 1e-9 <= v <= self.maximum() + 1e-9
        return (S.Acceptable if ok else S.Intermediate), text, pos

    def fixup(self, text: str) -> str:
        return self.textFromValue(self.value())

    def stepBy(self, steps: int) -> None:
        # The step is set for the base unit (0.2 ms); in seconds that's 0.2 s.
        base = self.singleStep()
        self.setSingleStep(base * self._shown_factor() / (self._convert(self.unit) or 1.0))
        try:
            super().stepBy(steps)
        finally:
            self.setSingleStep(base)


def _spin(lo, hi, decimals=4, unit="", step=1.0):
    return UnitSpinBox(lo, hi, unit, decimals=decimals, step=step)


class StimPage(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._loading = False
        self._units = None                 # (units, scale) the level fields currently show
        self._mode = style.LIGHT
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(16)

        # ---- left: shape and its settings -------------------------------
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
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
        self.output.setToolTip("Which analog output sends the stimulus")
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
        self._update_preview()

    # -- widgets <-> Stimulus ------------------------------------------------
    def set_stimulus(self, s: Stimulus) -> None:
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
        s.segments = self._segments() if shape == "custom" else self.main.stimulus.segments
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
        self.main._stimulus_edited(s)
        self._update_preview()

    def _update_preview(self):
        cfg, s = self.main.cfg, self.main.stimulus
        units, k = self._unit_label, self._scale
        in_units = f" ({cfg.stim_max_v * k:g} {units})" if self._converted else ""
        self.info.setText(f"Output {cfg.stim_output} · {cfg.stim_rate:,.0f} samples/s · "
                          f"limit ±{cfg.stim_max_v:g} V{in_units} (set in the config)")
        try:
            wave = s.waveform(cfg.stim_rate)
            stimmod.check_limit(wave, cfg.stim_max_v)
        except ValueError as e:
            self.curve.setData([], [])
            self._show_problem(str(e))
            return
        t = np.arange(len(wave)) / cfg.stim_rate
        # draw each sample as a flat step, which is what the output really does
        self.curve.setData(np.repeat(t, 2)[1:], np.repeat(wave * k, 2)[:-1])
        self.preview.getPlotItem().enableAutoRange()      # a new stimulus can change the scale
        peak = float(np.max(np.abs(wave))) * k
        notes = s.rounding_notes(cfg.stim_rate)
        self.show_message(f"{s.describe(units, k)}\nPeak {peak:g} {units} · {len(wave) / cfg.stim_rate * 1000:g} ms · "
                          f"{len(wave):,} samples" + ("".join("\n" + n for n in notes)))

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
            self.set_stimulus(self.main.stimulus)
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
        self.sim_note.setText(f"{what} <a href='sim'>Choose cells on the Simulation tab</a>")

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
        self.curve.setPen(pg.mkPen("#4d8dff" if mode == style.DARK else "#1a5fd0", width=2))
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
            self.main.stimulus.save(path)
            self.show_message(f"Saved to {Path(path).name}")

    def _load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load stimulus", "", "Stimulus (*.json)")
        if not path:
            return
        try:
            s = Stimulus.load(path)
        except ValueError as e:
            self.show_message(str(e), error=True)
            return
        self.set_stimulus(s)


StimWindow = StimPage   # older name
