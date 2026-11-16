"""Acquisition › Simple Stimulus: a stimulator box on screen, for teaching labs.

Pick a pulse type (single pulse, pulse pair, train), read the labeled picture, set the
numbers, press Stimulate. It can also send every few seconds and raise the amplitude by
a fixed amount after each stimulus (threshold and recruitment labs), and remembers named
presets. There are no protocol plans or response charts: students watch the Record page.

It keeps its own stimulus, separate from Stimulus Design's, and sends it through the
main window's send_wave, so each stimulus is still a comment and saved with the block.
"""
from __future__ import annotations

import json
import time

from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import style
from .mainparts import LIVE
from .stim import Stimulus

MODES = (("single", "Single pulse"), ("pair", "Pulse pair"), ("train", "Train"),
         ("sine", "Sine wave"), ("triangle", "Triangle wave"))
WAVES = ("sine", "triangle")
FIELDS = {"single": ("level", "width"), "pair": ("level", "width", "interval"),
          "train": ("level", "width", "rate", "count"), "sine": ("level", "freq", "dur"),
          "triangle": ("level", "freq", "dur")}
MARKED = {"single": ("level", "width"), "pair": ("level", "interval"), "train": ("level", "rate", "count"),
          "sine": ("level", "freq", "dur"), "triangle": ("level", "freq", "dur")}
NAMES = {"level": "Amplitude", "width": "Width", "interval": "Interval", "rate": "Rate", "count": "Pulses",
         "freq": "Frequency", "dur": "Duration"}
MAX_CYCLES = 500               # a triangle wave is made of ramps: 3 per cycle
DELAY_MS = 2.0                 # 0 V before the first pulse, so the recorded stimulus has a clear start
DIM = {style.LIGHT: "#d9822b", style.DARK: "#f0a35a"}
DEFAULTS = dict(mode="single", level=2.0, width=0.2, interval=20.0, rate=20.0, count=5,
                every=2.0, ramp=False, ramp_by=0.2, ramp_to=5.0, freq=10.0, dur=500.0)


class _Spin(QtWidgets.QDoubleSpinBox):
    """Shows 2.2 rather than 2.200."""

    def textFromValue(self, v: float) -> str:
        return f"{v:.{self.decimals()}f}".rstrip("0").rstrip(".") or "0"


def triangle_parts(amp_v: float, freq_hz: float, dur_ms: float) -> list[dict]:
    """A triangle wave around 0 V as ramp segments: up to +amp, down to −amp, back to 0,
    for as many whole cycles as fit in `dur_ms` (at least one)."""
    period = 1000.0 / freq_hz
    cycles = max(1, min(MAX_CYCLES, round(dur_ms / period)))
    ramp = lambda a, b, ms: {"kind": "ramp", "start": a, "level": b, "dur": ms, "d_level": "", "d_dur": ""}
    parts = [{"kind": "base", "level": 0.0, "dur": DELAY_MS, "d_level": "", "d_dur": ""}]
    for _ in range(cycles):
        parts += [ramp(0.0, amp_v, period / 4), ramp(amp_v, -amp_v, period / 2), ramp(-amp_v, 0.0, period / 4)]
    return parts


def _spin(lo, hi, decimals, suffix, tip):
    s = _Spin(minimum=lo, maximum=hi, decimals=decimals)
    s.setSuffix(f" {suffix}" if suffix else "")
    s.setStepType(QtWidgets.QAbstractSpinBox.StepType.AdaptiveDecimalStepType)   # steps scale with the value
    s.setKeyboardTracking(False)
    s.setMinimumWidth(110)
    s.setToolTip(tip)
    f = s.font()
    f.setPointSizeF(f.pointSizeF() * 1.15)
    s.setFont(f)
    return s


class PulseDiagram(QtWidgets.QWidget):
    """The pulse type drawn with its settings labeled, in the colors of the boxes below."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(150)
        self.mode, self.labels, self.colors = "single", {}, ("#1a5fd0", "#d9822b", "#5c6370", "#c9ced6")
        self.pad = 0          # px kept clear on the left for the amplitude label (narrow copies)

    def show_values(self, mode: str, labels: dict, colors: tuple) -> None:
        self.mode, self.labels, self.colors = mode, labels, colors
        self.update()

    def paintEvent(self, ev):
        wave, dim, text, axis = (QtGui.QColor(c) for c in self.colors)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        base, top = h - 42, 34
        lab = self.labels
        if self.mode in WAVES:
            self._paint_wave(p, w, top, base, wave, dim, axis)
            p.end()
            return
        p.setPen(QtGui.QPen(axis, 1))
        p.drawLine(16, base, w - 16, base)
        # where the pulses are, as (x0, x1) in pixels
        X = lambda f: self.pad + f * (w - self.pad)
        if self.mode == "single":
            xs = [(X(0.42), X(0.55))]
        elif self.mode == "pair":
            xs = [(X(0.28), X(0.36)), (X(0.62), X(0.70))]
        else:
            xs = [(X(0.2 + i * 0.13), X(0.2 + i * 0.13 + 0.03)) for i in range(5)]
        path = QtGui.QPainterPath(QtCore.QPointF(16, base))
        for x0, x1 in xs:
            for x, y in ((x0, base), (x0, top), (x1, top), (x1, base)):
                path.lineTo(x, y)
        path.lineTo(w - 16, base)
        p.setPen(QtGui.QPen(wave, 3))
        p.drawPath(path)

        bold = QtGui.QFont(self.font())
        bold.setBold(True)
        p.setFont(bold)

        def arrow(x0, y0, x1, y1):
            p.setPen(QtGui.QPen(dim, 2))
            p.drawLine(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1))
            line = QtCore.QLineF(x0, y0, x1, y1)
            for a, b in ((line.p1(), line.p2()), (line.p2(), line.p1())):
                v = QtCore.QLineF(a, b).unitVector()
                d = QtCore.QPointF(v.dx() * 7, v.dy() * 7)
                n = QtCore.QPointF(-d.y() * 0.5, d.x() * 0.5)
                p.setBrush(dim)
                p.setPen(QtCore.Qt.PenStyle.NoPen)
                p.drawPolygon(QtGui.QPolygonF([a, a + d + n, a + d - n]))

        def label(x, y, s, align):
            p.setPen(dim)
            r = QtCore.QRectF(x - 200, y - 10, 400, 20)
            if align == "right":
                r = QtCore.QRectF(x - 400, y - 10, 400, 20)
            flag = {"right": QtCore.Qt.AlignmentFlag.AlignRight, "center": QtCore.Qt.AlignmentFlag.AlignHCenter}[align]
            p.drawText(r, flag | QtCore.Qt.AlignmentFlag.AlignVCenter, s)

        x0, x1 = xs[0]
        ax = x0 - 18
        arrow(ax, top, ax, base)
        label(ax - 8, (top + base) / 2, lab.get("amp", ""), "right")
        if self.mode == "single":
            arrow(x0, base + 16, x1, base + 16)
            label((x0 + x1) / 2, base + 31, lab.get("width", ""), "center")
        else:
            nx0 = xs[1][0]
            arrow(x0, base + 16, nx0, base + 16)
            label((x0 + nx0) / 2, base + 31, lab.get("gap", ""), "center")
            p.setPen(QtGui.QPen(text))
            p.setFont(self.font())
            p.drawText(QtCore.QRectF(x0 - 60, top - 22, x1 - x0 + 120, 18),
                       QtCore.Qt.AlignmentFlag.AlignHCenter, lab.get("width", ""))
        if self.mode == "train":
            p.setFont(bold)
            p.setPen(QtGui.QPen(dim, 2))
            y = top - 10
            p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(xs[1][0], y + 5), QtCore.QPointF(xs[1][0], y),
                                            QtCore.QPointF(xs[-1][1], y), QtCore.QPointF(xs[-1][1], y + 5)]))
            label((xs[1][0] + xs[-1][1]) / 2, y - 12, lab.get("count", ""), "center")
        p.end()


def _arrow(p, dim, x0, y0, x1, y1):
    p.setPen(QtGui.QPen(dim, 2))
    p.drawLine(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1))
    line = QtCore.QLineF(x0, y0, x1, y1)
    for a, b in ((line.p1(), line.p2()), (line.p2(), line.p1())):
        v = QtCore.QLineF(a, b).unitVector()
        d = QtCore.QPointF(v.dx() * 7, v.dy() * 7)
        n = QtCore.QPointF(-d.y() * 0.5, d.x() * 0.5)
        p.setBrush(dim)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawPolygon(QtGui.QPolygonF([a, a + d + n, a + d - n]))


def _paint_wave(self, p, w, top, base, wave, dim, axis):
    """A sine or triangle wave: 3 cycles drawn, amplitude from the middle line to a peak,
    the frequency over one cycle, the duration under the whole wave."""
    import math
    mid = (top + base) / 2
    X = lambda f: self.pad + f * (w - self.pad)
    x0, x1 = X(0.18), X(0.88)
    cyc = (x1 - x0) / 3
    p.setPen(QtGui.QPen(axis, 1))
    p.drawLine(QtCore.QPointF(16, mid), QtCore.QPointF(w - 16, mid))
    amp = mid - top
    path = QtGui.QPainterPath(QtCore.QPointF(16, mid))
    path.lineTo(x0, mid)
    for i in range(121):
        ph = i / 120 * 3                       # cycles
        if self.mode == "sine":
            y = math.sin(2 * math.pi * ph)
        else:
            f = ph % 1
            y = 4 * f if f < 0.25 else (2 - 4 * f if f < 0.75 else 4 * f - 4)
        path.lineTo(x0 + ph * cyc, mid - y * amp)
    path.lineTo(w - 16, mid)
    p.setPen(QtGui.QPen(wave, 3))
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    bold = QtGui.QFont(self.font())
    bold.setBold(True)
    p.setFont(bold)
    lab = self.labels
    ax = x0 + cyc / 4                          # the first peak
    _arrow(p, dim, ax, top, ax, mid)
    p.setPen(dim)
    p.drawText(QtCore.QRectF(x0 - 410, (top + mid) / 2 - 10, 400, 20),
               QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter, lab.get("amp", ""))
    # one cycle, peak to peak, above the wave
    y = top - 10
    p.setPen(QtGui.QPen(dim, 2))
    a, b = x0 + cyc * 1.25, x0 + cyc * 2.25
    p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(a, y + 5), QtCore.QPointF(a, y),
                                    QtCore.QPointF(b, y), QtCore.QPointF(b, y + 5)]))
    p.setPen(dim)
    p.drawText(QtCore.QRectF((a + b) / 2 - 200, y - 22, 400, 18), QtCore.Qt.AlignmentFlag.AlignHCenter,
               lab.get("freq", ""))
    _arrow(p, dim, x0, base + 16, x1, base + 16)
    p.setPen(dim)
    p.drawText(QtCore.QRectF((x0 + x1) / 2 - 200, base + 21, 400, 20), QtCore.Qt.AlignmentFlag.AlignHCenter,
               lab.get("dur", ""))


PulseDiagram._paint_wave = _paint_wave


class SimpleStimPage(QtWidgets.QWidget):
    changed = QtCore.Signal()        # a setting changed (the Record page's docked stimulator follows)
    state_changed = QtCore.Signal()  # sent one, or started or stopped repeating

    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self.sent = 0
        self.repeating = False
        self.timer = QtCore.QTimer(self, timeout=self._tick)
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.state_timer = QtCore.QTimer(self, interval=300, timeout=self._refresh)
        saved = self._load_settings()

        # ---- left: type, picture, numbers, sending ----------------------------------
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(20, 16, 20, 16)
        lv.setSpacing(14)
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setSpacing(2)
        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_buttons = {}
        for key, name in MODES:
            b = QtWidgets.QPushButton(name, checkable=True)
            b.setMinimumWidth(110)
            self.mode_group.addButton(b)
            self.mode_buttons[key] = b
            mode_row.addWidget(b)
        mode_row.addStretch(1)
        lv.addLayout(mode_row)

        self.diagram = PulseDiagram()
        self.diagram_box = QtWidgets.QFrame()
        self.diagram_box.setObjectName("simpleDiagram")
        db = QtWidgets.QVBoxLayout(self.diagram_box)
        db.setContentsMargins(8, 8, 8, 4)
        db.addWidget(self.diagram)
        lv.addWidget(self.diagram_box)

        units = main.cfg.stim_units or "V"
        self.level = _spin(-100000, 100000, 3, units, "How strong each pulse is")
        self.width = _spin(0.01, 10000, 3, "ms", "How long each pulse lasts")
        self.interval = _spin(0.1, 100000, 2, "ms", "From the start of the first pulse to the start of the second")
        self.rate = _spin(0.01, 5000, 2, "Hz", "Pulses per second in the train")
        self.freq = _spin(0.01, 2500, 2, "Hz", "Cycles per second")
        self.dur = _spin(1, 600000, 1, "ms", "How long the wave lasts")
        self.count = QtWidgets.QSpinBox(minimum=2, maximum=1000)
        self.count.setKeyboardTracking(False)
        self.count.setMinimumWidth(110)
        self.count.setFont(self.level.font())
        self.count.setToolTip("How many pulses in the train")
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        self.field_labels = {}
        for col, (key, name, w) in enumerate((("level", "Amplitude", self.level), ("width", "Width", self.width),
                                               ("interval", "Interval", self.interval), ("rate", "Rate", self.rate),
                                               ("count", "Pulses", self.count), ("freq", "Frequency", self.freq),
                                               ("dur", "Duration", self.dur))):
            lab = QtWidgets.QLabel(name)
            self.field_labels[key] = lab
            grid.addWidget(lab, 0, col)
            grid.addWidget(w, 1, col)
        grid.setColumnStretch(7, 1)
        lv.addLayout(grid)

        send = QtWidgets.QHBoxLayout()
        send.setSpacing(10)
        send.addWidget(QtWidgets.QLabel("<b>Start repeating</b> sends one every"))
        self.every = _spin(0.05, 3600, 2, "s", "From the start of one stimulus to the start of the next")
        self.every.setMinimumWidth(90)
        send.addWidget(self.every)
        send.addWidget(QtWidgets.QLabel("until Stop."))
        send.addStretch(1)
        lv.addLayout(send)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        lv.addWidget(line)
        ramp = QtWidgets.QHBoxLayout()
        ramp.setSpacing(8)
        self.ramp = QtWidgets.QCheckBox("After each stimulus, raise the amplitude by")
        self.ramp_by = _spin(-100000, 100000, 3, units, "Added to the amplitude after each stimulus")
        self.ramp_by.setMinimumWidth(90)
        self.ramp_to = _spin(-100000, 100000, 3, units, "Repeating stops once the amplitude would go past this")
        self.ramp_to.setMinimumWidth(90)
        ramp.addWidget(self.ramp)
        ramp.addWidget(self.ramp_by)
        ramp.addWidget(QtWidgets.QLabel("up to"))
        ramp.addWidget(self.ramp_to)
        ramp.addStretch(1)
        lv.addLayout(ramp)
        self.ramp_note = QtWidgets.QLabel("For threshold and recruitment: press Start repeating, watch the "
                                          "Record page, and press Stop when the response stops growing.")
        self.ramp_note.setWordWrap(True)
        lv.addWidget(self.ramp_note)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        lv.addWidget(self.message)
        lv.addStretch(1)

        # ---- right: buttons, state, what was sent, presets -----------------------------
        right = QtWidgets.QWidget()
        right.setObjectName("simpleSide")
        right.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        right.setFixedWidth(310)
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(16, 16, 16, 16)
        rv.setSpacing(10)
        self.btn_fire = QtWidgets.QPushButton("Stimulate")
        self.btn_fire.setObjectName("start")
        self.btn_fire.setMinimumHeight(48)
        f = self.btn_fire.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        self.btn_fire.setFont(f)
        self.btn_fire.setToolTip("Send the stimulus once")
        rv.addWidget(self.btn_fire)
        row = QtWidgets.QHBoxLayout()
        self.btn_repeat = QtWidgets.QPushButton("Start repeating")
        self.btn_repeat.setToolTip("Send the stimulus every few seconds until Stop")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setToolTip("Stop sending and set the output to 0 V (Esc)")
        row.addWidget(self.btn_repeat, 1)
        row.addWidget(self.btn_stop, 1)
        rv.addLayout(row)
        self.lamp = QtWidgets.QLabel()
        self.lamp.setWordWrap(True)
        rv.addWidget(self.lamp)

        rv.addWidget(self._heading("Sent"))
        self.log = QtWidgets.QListWidget()
        self.log.setToolTip("Each stimulus is also a comment on the recording")
        self.log.setMinimumHeight(120)
        rv.addWidget(self.log, 1)

        rv.addWidget(self._heading("Presets"))
        self.presets = QtWidgets.QListWidget()
        self.presets.setToolTip("Click a preset to load its settings")
        self.presets.setMaximumHeight(130)
        rv.addWidget(self.presets)
        prow = QtWidgets.QHBoxLayout()
        self.btn_save_preset = QtWidgets.QPushButton("Save as preset…")
        self.btn_del_preset = QtWidgets.QPushButton("Delete")
        prow.addWidget(self.btn_save_preset)
        prow.addWidget(self.btn_del_preset)
        prow.addStretch(1)
        rv.addLayout(prow)
        self.output_note = QtWidgets.QLabel()
        self.output_note.setWordWrap(True)
        self.output_note.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.output_note.linkActivated.connect(lambda *_: main.tabs.show_tab("stim"))
        rv.addWidget(self.output_note)

        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(left, 1)
        outer.addWidget(right)

        # ---- values and signals -----------------------------------------------------
        self._set_values(saved)
        self._fill_presets()
        self.mode_group.buttonClicked.connect(lambda *_: self._changed())
        for w in (self.level, self.width, self.interval, self.rate, self.every, self.ramp_by, self.ramp_to,
                  self.freq, self.dur):
            w.valueChanged.connect(self._changed)
        self.count.valueChanged.connect(self._changed)
        self.ramp.toggled.connect(self._changed)
        self.btn_fire.clicked.connect(self.fire)
        self.btn_repeat.clicked.connect(self.start_repeat)
        self.btn_stop.clicked.connect(main.stop_stimulus)       # stops this page's repeating too (halt)
        self.presets.itemClicked.connect(self._preset_picked)
        self.btn_save_preset.clicked.connect(self._save_preset)
        self.btn_del_preset.clicked.connect(self._delete_preset)
        self._changed()

    @staticmethod
    def _heading(text: str) -> QtWidgets.QLabel:
        lab = QtWidgets.QLabel(f"<b>{text.upper()}</b>")
        lab.setContentsMargins(0, 6, 0, 0)
        return lab

    # -- settings ------------------------------------------------------------------------
    def _load_settings(self) -> dict:
        try:
            d = json.loads(self.main.settings.value("simple_stim", "") or "{}")
        except (ValueError, TypeError):
            d = {}
        return {**DEFAULTS, **{k: v for k, v in d.items() if k in DEFAULTS}}

    def values(self) -> dict:
        return dict(mode=self.mode(), level=self.level.value(), width=self.width.value(),
                    interval=self.interval.value(), rate=self.rate.value(), count=self.count.value(),
                    every=self.every.value(), ramp=self.ramp.isChecked(), ramp_by=self.ramp_by.value(),
                    ramp_to=self.ramp_to.value(), freq=self.freq.value(), dur=self.dur.value())

    def _set_values(self, d: dict) -> None:
        d = {**DEFAULTS, **d}
        widgets = (self.level, self.width, self.interval, self.rate, self.count, self.every,
                   self.ramp_by, self.ramp_to, self.ramp, self.freq, self.dur)
        for w in widgets:
            w.blockSignals(True)
        self.mode_buttons.get(d["mode"], self.mode_buttons["single"]).setChecked(True)
        self.level.setValue(float(d["level"]))
        self.width.setValue(float(d["width"]))
        self.interval.setValue(float(d["interval"]))
        self.rate.setValue(float(d["rate"]))
        self.count.setValue(int(d["count"]))
        self.every.setValue(float(d["every"]))
        self.ramp.setChecked(bool(d["ramp"]))
        self.ramp_by.setValue(float(d["ramp_by"]))
        self.ramp_to.setValue(float(d["ramp_to"]))
        self.freq.setValue(float(d["freq"]))
        self.dur.setValue(float(d["dur"]))
        for w in widgets:
            w.blockSignals(False)

    def mode(self) -> str:
        return next((k for k, b in self.mode_buttons.items() if b.isChecked()), "single")

    # -- the stimulus ----------------------------------------------------------------------
    def stimulus(self) -> Stimulus:
        """What Stimulate sends: a pulse, two, or a train, in volts at the output."""
        cfg, mode = self.main.cfg, self.mode()
        scale = cfg.stim_scale or 1.0
        amp, freq, dur = self.level.value() / scale, self.freq.value(), self.dur.value()
        if mode == "sine":
            return Stimulus(shape="sine", delay_ms=DELAY_MS, amplitude_v=amp, freq_hz=freq, duration_ms=dur)
        if mode == "triangle":
            return Stimulus(shape="segments", parts=triangle_parts(amp, freq, dur))
        count = {"single": 1, "pair": 2}.get(mode, self.count.value())
        interval = {"single": 100.0, "pair": self.interval.value()}.get(mode, 1000.0 / self.rate.value())
        return Stimulus(shape="pulse", delay_ms=DELAY_MS, amplitude_v=self.level.value() / scale,
                        width_ms=self.width.value(), count=count, interval_ms=interval)

    def describe(self) -> str:
        units, mode = self.main.cfg.stim_units or "V", self.mode()
        if mode in WAVES:
            return (f"{'Sine' if mode == 'sine' else 'Triangle'} {self.level.value():g} {units} · "
                    f"{self.freq.value():g} Hz · {self.dur.value():g} ms")
        s = f"{self.level.value():g} {units} · {self.width.value():g} ms"
        if mode == "pair":
            s += f" · pair {self.interval.value():g} ms apart"
        elif mode == "train":
            s += f" · {self.count.value()} at {self.rate.value():g} Hz"
        return s

    def _changed(self, *_):
        mode = self.mode()
        widgets = {"level": self.level, "width": self.width, "interval": self.interval, "rate": self.rate,
                   "count": self.count, "freq": self.freq, "dur": self.dur}
        for key, w in widgets.items():
            w.setVisible(key in FIELDS[mode])
            self.field_labels[key].setVisible(key in FIELDS[mode])
        for w in (self.ramp_by, self.ramp_to):
            w.setEnabled(self.ramp.isChecked())
        self.main.settings.setValue("simple_stim", json.dumps(self.values()))
        self._draw()
        self.changed.emit()
        try:
            self.stimulus().waveform(self.main.cfg.stim_rate)
            self.show_message("")
        except ValueError as e:
            self.show_message(str(e), error=True)

    def _draw(self) -> None:
        units, mode = self.main.cfg.stim_units or "V", self.mode()
        labels = {"amp": f"Amplitude {self.level.value():g} {units}", "width": f"Width {self.width.value():g} ms"}
        if mode == "pair":
            labels["gap"] = f"Interval {self.interval.value():g} ms (start to start)"
        elif mode == "train":
            labels["gap"] = f"{1000 / self.rate.value():.4g} ms → {self.rate.value():g} Hz"
            labels["count"] = f"{self.count.value()} pulses"
        elif mode in WAVES:
            labels.update(freq=f"{self.freq.value():g} Hz", dur=f"Duration {self.dur.value():g} ms")
        t = style.tokens(self._mode)
        self.diagram.show_values(mode, labels, (t["focus"], DIM[self._mode], t["subtle"], t["field_border"]))
        # the boxes whose values are labeled in orange on the picture get an orange mark
        for key, lab in self.field_labels.items():
            lab.setText(f"<span style='color:{DIM[self._mode]}'>■</span> {NAMES[key]}" if key in MARKED[mode]
                        else NAMES[key])

    # -- sending -------------------------------------------------------------------------
    def fire(self) -> bool:
        main = self.main
        if main.trials_page.running:
            self.show_message("A protocol is running on Stimulus Design; stop it first.", error=True)
            return False
        if main.repeating:
            main.set_repeat(False)
        if main.stim_out is not None and main.stim_out.busy:
            self.show_message("The previous stimulus is still running.", error=True)
            return False
        s = self.stimulus()
        try:
            wave = s.waveform(main.cfg.stim_rate)
        except ValueError as e:
            self.show_message(str(e), error=True)
            return False
        desc = self.describe()
        if not main.send_wave(wave, desc, s):
            self.show_message(main.statusBar().currentMessage().lstrip("⚠ "), error=True)
            return False
        self.sent += 1
        self.log.insertItem(0, f"#{self.sent}   {desc}   {time.strftime('%H:%M:%S')}")
        while self.log.count() > 200:
            self.log.takeItem(self.log.count() - 1)
        self.show_message("")
        if self.ramp.isChecked():
            self._raise_amplitude()
        self.state_changed.emit()
        return True

    def _raise_amplitude(self) -> None:
        by, to = self.ramp_by.value(), self.ramp_to.value()
        nxt = self.level.value() + by
        if (by > 0 and nxt > to + 1e-9) or (by < 0 and nxt < to - 1e-9) or by == 0:
            if self.repeating:
                self.halt()
                self.show_message(f"Reached {self.level.value():g} {self.main.cfg.stim_units or 'V'}: stopped.")
            return
        self.level.setValue(nxt)

    def start_repeat(self) -> None:
        if self.repeating:
            return
        self.repeating = True
        if not self.fire():
            self.repeating = False
            return
        if self.repeating:            # the ramp may have stopped it already
            self.timer.start(int(self.every.value() * 1000))
        self._refresh()
        self.state_changed.emit()

    def _tick(self) -> None:
        if self.main.stim_out is not None and self.main.stim_out.busy:
            self.show_message("Skipped one: the previous stimulus was still running. "
                              "Send them further apart.", error=True)
            return
        if not self.fire():
            self.halt()
        elif self.timer.interval() != int(self.every.value() * 1000):
            self.timer.setInterval(int(self.every.value() * 1000))

    def halt(self) -> None:
        """Stop repeating (the main window's stop_stimulus calls this, so Esc works here)."""
        self.repeating = False
        self.timer.stop()
        self._refresh()
        self.state_changed.emit()

    # -- presets -------------------------------------------------------------------------
    def _saved_presets(self) -> list[dict]:
        try:
            out = json.loads(self.main.settings.value("simple_stim_presets", "") or "[]")
            return [p for p in out if isinstance(p, dict) and p.get("name")]
        except (ValueError, TypeError):
            return []

    def _fill_presets(self) -> None:
        self.presets.clear()
        for p in self._saved_presets():
            self.presets.addItem(p["name"])
        if not self.presets.count():
            item = QtWidgets.QListWidgetItem("No presets yet")
            item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            self.presets.addItem(item)
        self.btn_del_preset.setEnabled(bool(self._saved_presets()))

    def _preset_picked(self, item) -> None:
        p = next((p for p in self._saved_presets() if p["name"] == item.text()), None)
        if p is not None:
            self.halt()
            self._set_values(p.get("values", {}))
            self._changed()
            self.show_message(f"Loaded “{p['name']}”")

    def _save_preset(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Save preset", "Name:",
                                                  text=f"{dict(MODES)[self.mode()]} {self.describe()}")
        name = name.strip()
        if not ok or not name:
            return
        presets = [p for p in self._saved_presets() if p["name"] != name]
        presets.append({"name": name, "values": self.values()})
        self.main.settings.setValue("simple_stim_presets", json.dumps(presets))
        self._fill_presets()

    def _delete_preset(self) -> None:
        item = self.presets.currentItem()
        if item is None or not item.flags() & QtCore.Qt.ItemFlag.ItemIsEnabled:
            self.show_message("Click a preset first, then Delete.")
            return
        presets = [p for p in self._saved_presets() if p["name"] != item.text()]
        self.main.settings.setValue("simple_stim_presets", json.dumps(presets))
        self._fill_presets()

    # -- state ---------------------------------------------------------------------------
    def show_message(self, text: str, error: bool = False) -> None:
        t = style.tokens(self._mode)
        self.message.setStyleSheet(f"color: {t['error'] if error else t['muted']}")
        self.message.setText(text)

    def last_sent(self) -> str:
        return self.log.item(0).text() if self.log.count() else ""

    def state(self) -> tuple[str, bool]:
        """(what the stimulator is doing or what's missing, whether it can send now)."""
        main = self.main
        backend = getattr(main, "backend", None)
        live = main.mode == LIVE and backend is not None and backend.running
        protocol = main.trials_page.running
        if main.stim_out is None:
            state, ok = "No stimulator: it needs the nidaqmx (or simulated) backend.", False
        elif protocol:
            state, ok = "A protocol is running on Stimulus Design.", False
        elif not live:
            state, ok = "Press Start on the Record page first.", False
        elif self.repeating:
            state, ok = f"Sending every {self.every.value():g} s…", True
        else:
            state, ok = "Ready", True
        return state, ok

    def _refresh(self) -> None:
        main, cfg = self.main, self.main.cfg
        t = style.tokens(self._mode)
        busy = main.stim_out is not None and main.stim_out.busy
        state, ok = self.state()
        color = "#1e8e3e" if ok else t["error"]
        self.lamp.setText(f"<span style='color:{color}'>●</span> {state}")
        self.btn_fire.setEnabled(ok and not busy and not self.repeating)
        self.btn_repeat.setEnabled(ok and not self.repeating)
        self.btn_stop.setEnabled(self.repeating or busy or main.repeating)
        mon = next((c for c in cfg.channels if c.physical == cfg.stim_monitor), None) if cfg.stim_monitor else None
        where = f"recorded on {mon.name}" if mon is not None else "not recorded"
        self.output_note.setText(f"Output {cfg.stim_output}, {where}, limit ±{cfg.stim_max_v:g} V · "
                                 f"<a href='#' style='color:{t['focus']}'>change on Stimulus Design</a>")

    def showEvent(self, ev):
        super().showEvent(ev)
        self._refresh()
        self.state_timer.start()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.state_timer.stop()

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        t = style.tokens(mode)
        self.setStyleSheet(f"QWidget#simpleSide {{ background: {t['ground']}; border-left: 1px solid {t['line']}; }}"
                           f"QFrame#simpleDiagram {{ background: {t['panel']}; border: 1px solid {t['line']};"
                           f" border-radius: 8px; }}")
        for lab in (self.ramp_note,):
            lab.setStyleSheet(f"color: {t['subtle']}")
        self._draw()
        self._refresh()
