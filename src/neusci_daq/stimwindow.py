"""The Stimulator tab: design a stimulus, see it, fire it, and choose the output.

Every change applies straight away to the stimulus that Fire and Repeat send,
so there is no Apply button. The preview shows exactly the samples that will
be written to the output.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import style
from .plotzoom import plot_widget
from .unitbox import UnitSpinBox
from .units import _converter

from . import stim as stimmod
from .stim import Stimulus
from .segmenteditor import SegmentEditor


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
        # In panels, so the Stimulus page (stimuluspage.py) can lay them out with the protocol's.
        self.fields_panel = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(self.fields_panel)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        self.editing = 0                   # kept for older callers: both outputs show at once now
        self.editors: list[SegmentEditor] = []
        self.out_heads: list[QtWidgets.QWidget] = []
        self.out_dots: list[QtWidgets.QLabel] = []
        for i in range(2):
            head = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(head)
            hl.setContentsMargins(0, 4 if i else 0, 0, 0)
            hl.setSpacing(6)
            dot = QtWidgets.QLabel()
            name = QtWidgets.QLabel()
            name.setObjectName("outName")
            hl.addWidget(dot)
            hl.addWidget(name)
            if i == 1:
                rm = QtWidgets.QToolButton(text="✕", autoRaise=True)
                rm.setToolTip("Stop using the second output")
                rm.setAccessibleName("Remove the second output")
                rm.clicked.connect(lambda: QtCore.QTimer.singleShot(0, lambda: self.main.set_stimulus2(None)))
                hl.addWidget(rm)
                self.btn_remove_out = rm
            hl.addStretch(1)
            ed = SegmentEditor(self._unit_info)
            ed.edited.connect(self._changed)
            ed.problem.connect(self._show_problem)
            left.addWidget(head)
            left.addWidget(ed)
            self.out_heads.append(head)
            self.out_dots.append(dot)
            self.editors.append(ed)
        self.btn_add_out = QtWidgets.QPushButton("+ Add second output")
        self.btn_add_out.setToolTip("Send a second stimulus on the other analog output, on the same timeline")
        left.addWidget(self.btn_add_out, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        left.addStretch(1)

        self.output_panel = QtWidgets.QWidget()
        ov = QtWidgets.QVBoxLayout(self.output_panel)
        ov.setContentsMargins(0, 0, 0, 0)
        bottom = QtWidgets.QFormLayout()
        self.output = QtWidgets.QComboBox()
        self.output.addItems(list(stimmod.AO_CHANNELS))
        self.output.setToolTip("Which analog output sends the first stimulus (a second one uses the other)")
        self.monitor = QtWidgets.QComboBox()
        self.monitor.setToolTip("Record the stimulus on a spare input (wire the output to it). "
                                "This adds a channel named Stim with exact timing.")
        self.repeat = _spin(0.05, 3600, unit="s", step=0.5)
        self.repeat.setToolTip("Repeat fires the stimulus this often")
        bottom.addRow("Output", self.output)
        bottom.addRow("Record on", self.monitor)
        ov.addLayout(bottom)
        self.sim_note = QtWidgets.QLabel()            # simulated backend: where the stimulus goes
        self.sim_note.setWordWrap(True)
        self.sim_note.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.sim_note.linkActivated.connect(lambda *_: self.main.tabs.show_tab("sim"))
        self.sim_note.setVisible(False)
        ov.addWidget(self.sim_note)
        self.buttons_panel = QtWidgets.QWidget()
        btns = QtWidgets.QHBoxLayout(self.buttons_panel)
        btns.setContentsMargins(0, 0, 0, 0)
        self.btn_fire = QtWidgets.QPushButton("Fire")
        self.btn_fire.setObjectName("start")
        self.btn_fire.setToolTip("Send the stimulus once (Ctrl+F)")
        self.btn_repeat = QtWidgets.QPushButton("Repeat", checkable=True)
        self.btn_repeat.setToolTip("Keep sending it every few seconds until you press Stop")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setToolTip("Stop the stimulus and set the output to 0 V (Esc)")
        self.btn_save = QtWidgets.QPushButton("Save…")
        self.btn_load = QtWidgets.QPushButton("Load…")
        for b in (self.btn_fire, self.btn_repeat):
            btns.addWidget(b)
        btns.addWidget(QtWidgets.QLabel("every"))
        btns.addWidget(self.repeat)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)
        for b in (self.btn_save, self.btn_load):
            btns.addWidget(b)
        column = QtWidgets.QVBoxLayout()
        column.setSpacing(10)
        column.addWidget(self.fields_panel, 1)
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        column.addWidget(line)
        column.addWidget(self.output_panel)
        column.addWidget(self.buttons_panel)
        outer.addLayout(column, 0)

        # ---- right: preview ----------------------------------------------
        self.preview_panel = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(self.preview_panel)
        right.setContentsMargins(0, 0, 0, 0)
        trial_row = QtWidgets.QHBoxLayout()
        self.preview_title = QtWidgets.QLabel("<b>Preview</b>")
        trial_row.addWidget(self.preview_title)
        trial_row.addStretch(1)
        self.trial_label = QtWidgets.QLabel()
        self.trial_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal, minimum=1, maximum=1)
        self.trial_slider.setFixedWidth(160)
        self.trial_slider.setToolTip("Which trial is drawn bold; the others are faded")
        self.trial_slider.valueChanged.connect(lambda *_: self._update_preview())
        trial_row.addWidget(self.trial_label)
        trial_row.addWidget(self.trial_slider)
        right.addLayout(trial_row)
        # One plot per output, on the same time axis.
        self.preview = plot_widget()
        self.preview2 = plot_widget()
        for i, pw in enumerate((self.preview, self.preview2)):
            p = pw.getPlotItem()
            p.setLabel("left", "Output", units="V")
            p.showGrid(x=True, y=True, alpha=0.25)
        self.preview.getPlotItem().getAxis("bottom").setStyle(showValues=False)
        self.preview2.getPlotItem().setLabel("bottom", "time", units="s")
        self.preview2.setXLink(self.preview)
        self.curve = self.preview.getPlotItem().plot()        # kept for older callers
        self.curve2 = self.preview2.getPlotItem().plot()
        # Trials to draw: f() -> (protocol, a values, b value) or None for just the stimulus
        # (the Stimulus page sets it).
        self.trials_fn = None
        right.addWidget(self.preview, 1)
        right.addWidget(self.preview2, 1)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        right.addWidget(self.info)
        right.addWidget(self.message)
        outer.addWidget(self.preview_panel, 1)

        # ---- signals -------------------------------------------------------
        self._apply_units()
        self.set_stimulus(main.stimulus)
        self.repeat.setValue(main.stim_repeat_s)
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
        self.btn_add_out.clicked.connect(self._add_output)
        self.outputs_changed()

    # -- which output ----------------------------------------------------------
    def _current(self) -> Stimulus:
        """The stimulus the fields edit: the first output's, or the second's."""
        return self.main.stimulus2 if self.editing == 1 and self.main.stimulus2 is not None else self.main.stimulus

    def outputs_changed(self) -> None:
        """Show a segment table per output in use, and the add button to match."""
        main = self.main
        two = main.stimulus2 is not None
        names = [main.cfg.stim_output, main.second_output]
        for i, (head, dot, ed) in enumerate(zip(self.out_heads, self.out_dots, self.editors)):
            on = i == 0 or two
            head.setVisible(on)
            ed.setVisible(on)
            dot.setPixmap(_dot(self._colors[i]).pixmap(10, 10))
            head.findChild(QtWidgets.QLabel, "outName").setText(f"<b>{names[i]}</b>")
        self.btn_add_out.setVisible(not two)
        self.btn_add_out.setText(f"+ Add second output ({main.second_output})")
        self.btn_fire.setText("Fire both" if two else "Fire")
        self._show_sim_note()
        self._load_fields()

    def _add_output(self) -> None:
        self.main.set_stimulus2(Stimulus(parts=[stimmod.new_part("base", dur=10.0),
                                                stimmod.new_part("step"),
                                                stimmod.new_part("base", dur=30.0)]))

    # -- widgets <-> Stimulus ------------------------------------------------
    def set_stimulus(self, s: Stimulus) -> None:
        """The stimulus changed elsewhere (a protocol, Load): show it."""
        self._load_fields()

    def _load_fields(self, *_) -> None:
        self._loading = True
        self.editors[0].set_stimulus(self.main.stimulus)
        if self.main.stimulus2 is not None:
            self.editors[1].set_stimulus(self.main.stimulus2)
        self._loading = False
        self._changed()          # the stimulus Fire sends and the preview both follow the fields

    def stimulus(self) -> Stimulus:
        return self.editors[0].stimulus()

    def set_locked(self, locked: bool) -> None:
        for ed in self.editors:
            ed.set_locked(locked)
        self.btn_add_out.setEnabled(not locked)
        if self.btn_remove_out is not None:
            self.btn_remove_out.setEnabled(not locked)

    def _unit_info(self):
        """(units, volts → units scale, accepted units) for the level boxes."""
        return self._unit_label, self._scale, self._level_families()

    # -- reacting to edits ---------------------------------------------------
    def _changed(self, *_):
        if self._loading:
            return
        # Loading fields from a stimulus gives the same stimulus back: only a real change is
        # passed on (each one re-checks the whole protocol).
        s = self.editors[0].stimulus()
        if s != self.main.stimulus:
            self.main._stimulus_edited(s)
        if self.main.stimulus2 is not None:
            s2 = self.editors[1].stimulus()
            if s2 != self.main.stimulus2:
                self.main._stimulus2_edited(s2)
        self._update_preview()

    def _update_preview(self):
        from . import protocols as T
        cfg, main = self.main.cfg, self.main
        units, k = self._unit_label, self._scale
        in_units = f" ({cfg.stim_max_v * k:g} {units})" if self._converted else ""
        two = main.stimulus2 is not None
        where = f"{cfg.stim_output} + {main.second_output} together" if two else f"Output {cfg.stim_output}"
        self.info.setText(f"{where} · {cfg.stim_rate:,.0f} samples/s{' each' if two else ''} · "
                          f"limit ±{cfg.stim_max_v:g} V{in_units} (set in the config)")
        self.preview2.setVisible(two)
        self.preview.getPlotItem().getAxis("bottom").setStyle(showValues=not two)
        self.preview.getPlotItem().setLabel("bottom", "" if two else "time", units="" if two else "s")
        got = self.trials_fn() if self.trials_fn is not None else None
        p, a_vals, b = got if got else (None, [None], None)
        n = len(a_vals)
        self.trial_slider.blockSignals(True)
        self.trial_slider.setMaximum(max(1, n))
        self.trial_slider.blockSignals(False)
        self.trial_slider.setVisible(n > 1)
        self.trial_label.setVisible(n > 1)
        cur = min(self.trial_slider.value(), n) - 1
        self.trial_label.setText(f"Trial {cur + 1} of {n}")
        names = [cfg.stim_output, main.second_output]
        plots = [self.preview.getPlotItem(), self.preview2.getPlotItem()]
        for pi in plots:
            pi.clear()
        lines, err = [], ""
        # every trial (at most 60 of them), faded, then the one picked, bold
        order = [i for i in range(n) if i != cur][:59] + [cur]
        longest = 0
        for ia in order:
            a = a_vals[ia]
            try:
                if p is None:
                    s1, k1, s2, k2 = main.stimulus, 1.0, main.stimulus2, 1.0
                else:
                    s1, k1, s2, k2 = T.applied(main.stimulus, p, a, b, main.stimulus2)
                streams = [(s1, k1)] + ([(s2, k2)] if two else [])
                for lane, (st, kk) in enumerate(streams):
                    wave = st.waveform(cfg.stim_rate) * kk
                    stimmod.check_limit(wave, cfg.stim_max_v)
                    t = np.arange(len(wave)) / cfg.stim_rate
                    col = QtGui.QColor(self._colors[lane])
                    bold = ia == cur
                    if not bold:
                        col.setAlpha(80)
                    plots[lane].plot(np.repeat(t, 2)[1:], np.repeat(wave * k, 2)[:-1],
                                     pen=pg.mkPen(col, width=2.4 if bold else 1))
                    longest = max(longest, len(wave))
                    if bold:
                        label = f"{names[lane]}: " if two else ""
                        lines.append(f"{label}{st.describe(units, k)}")
                        lines += [f"{label}{x}" for x in st.rounding_notes(cfg.stim_rate)]
            except ValueError as e:
                if ia == cur or not err:
                    err = (f"Trial {ia + 1}: " if n > 1 else "") + str(e)
        for pi in plots:
            pi.enableAutoRange()                       # a new stimulus can change the scale
        if err:
            self._show_problem(err)
            return
        lines.append(f"{longest / cfg.stim_rate * 1000:g} ms · {longest:,} samples"
                     + (" per output" if two else "") + (" (the longest trial)" if n > 1 else ""))
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
            for ed in self.editors:
                ed.refresh_units()
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
        for pw in (self.preview, self.preview2):
            pw.setBackground(th.background)
            for ax in ("left", "bottom"):
                pw.getPlotItem().getAxis(ax).setPen(th.foreground)
                pw.getPlotItem().getAxis(ax).setTextPen(th.foreground)
        self._colors = OUTPUT_COLORS[mode == style.DARK]
        for i, dot in enumerate(getattr(self, "out_dots", [])):
            dot.setPixmap(_dot(self._colors[i]).pixmap(10, 10))
        for ed in getattr(self, "editors", []):
            ed.apply_theme(mode)
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
        for pw in (self.preview, self.preview2):
            pi = pw.getPlotItem()
            if units == "V":
                pi.setLabel("left", "Output", units="V")
            else:
                # pyqtgraph would add a prefix to a unit that may already have one (µA -> kµA)
                pi.setLabel("left", f"Output ({units})")
            pi.getAxis("left").enableAutoSIPrefix(units == "V")

    # -- files -----------------------------------------------------------------
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
        self.main.set_stimulus2(s2)
        self.main.set_stimulus(s)


StimWindow = StimPage   # older name
