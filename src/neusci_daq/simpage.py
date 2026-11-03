"""The Simulation tab (only with --backend simulated): what each input records.

Pick an input, then set its noise and sine, the EPSP each stimulus pulse gives,
and the cells near it: their spike shape and how their firing rate rises with
depolarization. Changes apply straight away, while recording too, and Setup ›
Save channel setup writes them to the config's [simulated] section.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import config as cfgmod
from . import simulation as sim
from . import style, theme
from .plotzoom import plot_widget
from .stimwindow import UnitSpinBox, _same_kind, parse_quantity

# Cells table: (heading, SimCell field, unit or "" for a plain number, tooltip)
_COLUMNS = (
    ("Spike size", "depth_v", "V", "Depth of the spike's negative trough on the trace"),
    ("Width", "width_ms", "ms", "Spike trough to repolarization peak: narrow ~0.3 ms, broad ~0.8 ms"),
    ("Peak ratio", "peak_ratio", "", "Repolarization peak as a fraction of the trough"),
    ("Max rate", "max_rate", "Hz", "Firing rate when fully depolarized"),
    ("Threshold", "threshold_v", "V", "Depolarization (EPSP size on the trace) giving half the max rate"),
    ("Slope", "slope_v", "V", "How sharply the rate rises around threshold: smaller is sharper"),
    ("Burst chance", "burst", "", "Chance (0-1) that a spike starts a burst of 2-4 spikes, 3-6 ms apart"),
)


def _fmt(v: float, unit: str) -> str:
    return f"{v:.4g} {unit}".strip()


class SimPage(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self._loading = False
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(16)

        # ---- left: settings for one input -----------------------------------
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        intro = QtWidgets.QLabel(
            "What each simulated input records: noise, a sine, an EPSP after each stimulus pulse, "
            "and cells that fire faster as the EPSPs depolarize them. Changes apply straight away.")
        intro.setWordWrap(True)
        left.addWidget(intro)
        pick = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QComboBox()
        self.input.setToolTip("The input these settings are for")
        self.btn_reset = QtWidgets.QPushButton("Reset to defaults")
        self.btn_reset.setToolTip("Put this input back to the default noise, sine, EPSP and three cells")
        pick.addWidget(QtWidgets.QLabel("Input"))
        pick.addWidget(self.input, 1)
        pick.addWidget(self.btn_reset)
        left.addLayout(pick)

        sig = QtWidgets.QGroupBox("Signal")
        f = QtWidgets.QFormLayout(sig)
        self.noise = UnitSpinBox(0, 5, "V", step=0.01)
        self.sine_v = UnitSpinBox(0, 10, "V", step=0.1)
        self.sine_v.setToolTip("0 = no sine")
        self.sine_hz = UnitSpinBox(0, 1000, "Hz", step=1)
        f.addRow("Noise (RMS)", self.noise)
        f.addRow("Sine amplitude", self.sine_v)
        f.addRow("Sine frequency", self.sine_hz)
        left.addWidget(sig)

        ep = QtWidgets.QGroupBox("EPSP after each stimulus pulse")
        f = QtWidgets.QFormLayout(ep)
        self.epsp = UnitSpinBox(0, 10, "V", step=0.05)
        self.epsp.setToolTip("EPSP peak for a 1 V pulse; it scales with the pulse. 0 = no EPSPs")
        self.epsp_delay = UnitSpinBox(0, 1000, "ms", step=0.5)
        self.epsp_peak = UnitSpinBox(0.05, 1000, "ms", step=0.5)
        self.epsp_peak.setToolTip("From the EPSP's start to its peak; it lasts about 8× this")
        f.addRow("Size for a 1 V pulse", self.epsp)
        f.addRow("Delay", self.epsp_delay)
        f.addRow("Time to peak", self.epsp_peak)
        left.addWidget(ep)

        cells = QtWidgets.QGroupBox("Cells")
        v = QtWidgets.QVBoxLayout(cells)
        self.table = QtWidgets.QTableWidget(0, len(_COLUMNS) + 1)
        self.table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS] + ["At rest"])
        for i, (_, _, _, tip) in enumerate(_COLUMNS):
            self.table.horizontalHeaderItem(i).setToolTip(tip)
        self.table.horizontalHeaderItem(len(_COLUMNS)).setToolTip("Firing rate with no EPSP (from the settings)")
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(True)
        self.table.setToolTip("Cells take units, e.g. 400 mV or 0.5 ms")
        rowbtns = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add cell")
        self.btn_del = QtWidgets.QPushButton("Remove cell")
        rowbtns.addWidget(self.btn_add)
        rowbtns.addWidget(self.btn_del)
        rowbtns.addStretch(1)
        v.addWidget(self.table, 1)
        v.addLayout(rowbtns)
        left.addWidget(cells, 1)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        outer.addLayout(left, 3)

        # ---- right: what the cells look like --------------------------------
        right = QtWidgets.QVBoxLayout()
        self.shape_plot = plot_widget()
        sp = self.shape_plot.getPlotItem()
        sp.setTitle("Spike shapes")
        sp.setLabel("left", "V")
        sp.setLabel("bottom", "ms from the trough")
        self.rate_plot = plot_widget()
        rp = self.rate_plot.getPlotItem()
        rp.setTitle("Firing rate vs depolarization")
        rp.setLabel("left", "spikes/s")
        rp.setLabel("bottom", "depolarization (V)")
        for p in (sp, rp):
            p.showGrid(x=True, y=True, alpha=0.25)
            p.getAxis("left").enableAutoSIPrefix(False)
            p.getAxis("bottom").enableAutoSIPrefix(False)
        self.epsp_line = pg.InfiniteLine(0, angle=90, pen=pg.mkPen("#888", style=QtCore.Qt.PenStyle.DashLine),
                                         label="1 V pulse EPSP", labelOpts={"position": 0.9})
        rp.addItem(self.epsp_line)
        right.addWidget(self.shape_plot, 1)
        right.addWidget(self.rate_plot, 1)
        outer.addLayout(right, 2)

        # ---- signals -------------------------------------------------------------
        self.sync()
        self.input.currentIndexChanged.connect(lambda *_: self._show_input())
        for w in (self.noise, self.sine_v, self.sine_hz, self.epsp, self.epsp_delay, self.epsp_peak):
            w.valueChanged.connect(self._changed)
        self.table.itemChanged.connect(self._changed)
        self.btn_add.clicked.connect(self._add_cell)
        self.btn_del.clicked.connect(self._remove_cell)
        self.btn_reset.clicked.connect(self._reset)
        self.apply_theme(style.LIGHT)

    # -- which input --------------------------------------------------------------
    def sync(self) -> None:
        """List the inputs (after a channel or wiring change) and show the picked one."""
        cfg = self.main.cfg
        cur = self.input.currentData() or (cfg.channels[0].physical if cfg.channels else "ai0")
        names = {c.physical: c.name for c in cfg.channels}
        self.input.blockSignals(True)
        self.input.clear()
        for phys in cfgmod.valid_inputs(cfg):
            label = phys + (f" ({names[phys]})" if phys in names else " (not used)")
            if phys == cfg.stim_monitor:
                label += " · records the stimulus"
            self.input.addItem(label, phys)
        self.input.setCurrentIndex(max(0, self.input.findData(cur)))
        self.input.blockSignals(False)
        self._show_input()

    def current(self) -> sim.SimInput:
        return sim.input_for(self.main.cfg.sim_inputs, self.input.currentData() or "ai0")

    def _show_input(self) -> None:
        inp = self.current()
        self._loading = True
        self.noise.setValue(inp.noise_v)
        self.sine_v.setValue(inp.sine_v)
        self.sine_hz.setValue(inp.sine_hz)
        self.epsp.setValue(inp.epsp_v_per_v)
        self.epsp_delay.setValue(inp.epsp_delay_ms)
        self.epsp_peak.setValue(inp.epsp_peak_ms)
        self.table.setRowCount(0)
        for c in inp.cells:
            self._append_row(c)
        self._loading = False
        mon = self.input.currentData() == self.main.cfg.stim_monitor
        self._show_message("This input is wired to the stimulator (Stimulator tab › Record on): it records "
                           "the stimulus, not these settings." if mon else "")
        self._redraw()

    # -- table ----------------------------------------------------------------------
    def _append_row(self, c: sim.SimCell) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for col, (_, attr, unit, _) in enumerate(_COLUMNS):
            self.table.setItem(r, col, QtWidgets.QTableWidgetItem(_fmt(getattr(c, attr), unit)))
        rest = QtWidgets.QTableWidgetItem(f"{float(c.rate(0.0)):.2g} Hz")
        rest.setFlags(rest.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, len(_COLUMNS), rest)

    def _cells(self) -> list[sim.SimCell]:
        out = []
        for r in range(self.table.rowCount()):
            c = sim.SimCell()
            for col, (head, attr, unit, _) in enumerate(_COLUMNS):
                item = self.table.item(r, col)
                text = item.text() if item else ""
                try:
                    v = parse_quantity(text, _same_kind(unit))[0] if unit else float(text)
                except ValueError:
                    raise ValueError(f"Cell {r + 1}: {head.lower()} must be a number"
                                     + (f", optionally with a unit (e.g. {_fmt(getattr(c, attr), unit)})."
                                        if unit else ".")) from None
                setattr(c, attr, v)
            out.append(c)
        return out

    def _add_cell(self) -> None:
        cells = self._cells_or_none() or []
        self.table.blockSignals(True)
        self._append_row(sim.SimCell(**vars(cells[-1])) if cells else sim.SimCell())
        self.table.blockSignals(False)
        self._changed()

    def _remove_cell(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()} or {self.table.rowCount() - 1})
        self.table.blockSignals(True)
        for r in reversed(rows):
            if r >= 0:
                self.table.removeRow(r)
        self.table.blockSignals(False)
        self._changed()

    def _cells_or_none(self):
        try:
            return self._cells()
        except ValueError:
            return None

    # -- edits ----------------------------------------------------------------------
    def _changed(self, *_) -> None:
        if self._loading:
            return
        try:
            cells = self._cells()
        except ValueError as e:
            self._show_message(str(e), error=True)
            return
        inp = sim.SimInput(self.input.currentData(), self.noise.value(), self.sine_v.value(),
                           self.sine_hz.value(), self.epsp.value(), self.epsp_delay.value(),
                           self.epsp_peak.value(), cells)
        try:
            sim.validate([inp])
        except ValueError as e:
            self._show_message(str(e), error=True)
            return
        self._store(inp)
        # refresh the "At rest" column without re-entering this
        self.table.blockSignals(True)
        for r, c in enumerate(cells):
            self.table.item(r, len(_COLUMNS)).setText(f"{float(c.rate(0.0)):.2g} Hz")
        self.table.blockSignals(False)
        self._show_message("")
        self._redraw()

    def _store(self, inp: sim.SimInput) -> None:
        """Put `inp` in the config. A new list, so the acquisition thread never sees
        it half changed."""
        others = [i for i in self.main.cfg.sim_inputs if i.physical != inp.physical]
        self.main.cfg.sim_inputs = sorted(others + [inp], key=lambda i: i.physical)

    def _reset(self) -> None:
        phys = self.input.currentData()
        self.main.cfg.sim_inputs = [i for i in self.main.cfg.sim_inputs if i.physical != phys]
        self._show_input()

    def _show_message(self, text: str, error: bool = False) -> None:
        t = style.tokens(self._mode)
        self.message.setStyleSheet(f"color: {t['error'] if error else t['muted']}")
        self.message.setText(text)

    # -- plots ----------------------------------------------------------------------
    def _redraw(self) -> None:
        inp = self.current()
        sp, rp = self.shape_plot.getPlotItem(), self.rate_plot.getPlotItem()
        for p in (sp, rp):
            for item in list(p.listDataItems()):
                p.removeItem(item)
        fs = 100_000.0                                    # smooth curves, whatever the sample rate
        top = max([0.6, 2.0 * inp.epsp_v_per_v] + [c.threshold_v + 6 * c.slope_v for c in inp.cells])
        d = np.linspace(0, top, 300)
        for n, c in enumerate(inp.cells):
            color = cfgmod.PALETTE[n % len(cfgmod.PALETTE)]
            w, trough = sim.spike_shape(c, fs)
            sp.plot((np.arange(len(w)) - trough) / fs * 1e3, w, pen=pg.mkPen(color, width=2))
            rp.plot(d, c.rate(d), pen=pg.mkPen(color, width=2))
        self.epsp_line.setValue(inp.epsp_v_per_v)
        self.epsp_line.setVisible(inp.epsp_v_per_v > 0)

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        for pw in (self.shape_plot, self.rate_plot):
            pw.setBackground(th.background)
            p = pw.getPlotItem()
            p.setTitle(p.titleLabel.text, color=th.foreground)
            for ax in ("left", "bottom"):
                p.getAxis(ax).setPen(th.foreground)
                p.getAxis(ax).setTextPen(th.foreground)
        self.epsp_line.setPen(pg.mkPen(th.foreground, style=QtCore.Qt.PenStyle.DashLine))
        self.epsp_line.label.setColor(th.foreground)
        self._redraw()
