"""The Simulation tab (only with --backend simulated): the network of cells the
simulated backend records from, the synapses between them, and which cell each
input records.

Changes apply straight away, while recording too, and Setup › Save channel setup
writes them to the config's [simulated] section. See simulation.py for the model.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import config as cfgmod
from . import simulation as sim
from . import style, theme
from .stimwindow import UnitSpinBox, _same_kind, parse_quantity
from .viewkit import scroll_column

# Cells table: (heading, Cell field, unit, tooltip)
_CELL_COLUMNS = (
    ("Rest", "rest_mv", "mV", "Resting membrane potential"),
    ("Threshold", "threshold_mv", "mV", "The cell fires when its membrane potential reaches this"),
    ("Reset", "reset_mv", "mV", "Where the membrane potential goes after a spike (below threshold)"),
    ("Time const.", "tau_ms", "ms", "Membrane time constant: how quickly PSPs fade"),
    ("Refractory", "refractory_ms", "ms", "No spikes for this long after one"),
    ("Drive", "drive_mv", "mV", "Steady depolarization above rest; with noise, sets how often it fires on its own"),
    ("Noise", "noise_mv", "mV", "Membrane potential fluctuation (SD)"),
    ("Stimulus", "stimulus", "mV", "How strongly the stimulus drives this cell: the depolarization from a 1 V, "
                                   "1 ms pulse. The stimulus is injected as a current, so longer or stronger "
                                   "pulses depolarize more; 0 = not driven"),
    ("Spike size", "spike_depth_v", "V", "Extracellular recordings: depth of this cell's spike trough"),
    ("Spike width", "spike_width_ms", "ms", "Extracellular recordings: spike trough to repolarization peak"),
)
_LIVE_COL = len(_CELL_COLUMNS)
_KINDS = (("Intracellular", "intracellular"), ("Extracellular", "extracellular"))


def _fmt(v: float, unit: str) -> str:
    return f"{v:.4g} {unit}".strip()


def _readonly(text: str) -> QtWidgets.QTableWidgetItem:
    item = QtWidgets.QTableWidgetItem(text)
    item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
    return item


class SimPage(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._mode = style.LIGHT
        self._loading = False
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(16)

        # ---- left: the network ---------------------------------------------------
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        intro = QtWidgets.QLabel(
            "A network of integrate-and-fire cells. Each fires when its membrane potential reaches "
            "threshold; its spikes give the cells it connects to a PSP after the synaptic delay. "
            "The stimulus is injected as a current into the cells it drives, following its waveform: "
            "pulse width, trains and sines all matter. Changes apply straight away.")
        intro.setWordWrap(True)
        left.addWidget(intro)

        top = QtWidgets.QHBoxLayout()
        f = QtWidgets.QFormLayout()
        self.n_cells = QtWidgets.QSpinBox(minimum=1, maximum=sim.MAX_CELLS)
        self.n_cells.setToolTip(f"1 to {sim.MAX_CELLS} cells")
        self.delay = UnitSpinBox(0, 100, "ms", step=0.5)
        self.delay.setToolTip("From a spike (or stimulus pulse) to the start of the PSP it causes")
        self.syn_tau = UnitSpinBox(0.1, 100, "ms", step=0.5)
        self.syn_tau.setToolTip("How long the synaptic current lasts: longer gives slower, broader PSPs")
        f.addRow("Cells", self.n_cells)
        f.addRow("Synaptic delay", self.delay)
        f.addRow("Synaptic time const.", self.syn_tau)
        top.addLayout(f)
        top.addStretch(1)
        self.btn_reset = QtWidgets.QPushButton("Reset to the default network")
        self.btn_reset.setToolTip("Three cells in a chain: the stimulus drives 1, 1 excites 2, "
                                  "2 excites 3, and 3 inhibits 1")
        top.addWidget(self.btn_reset, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        left.addLayout(top)

        box = QtWidgets.QGroupBox("⚡ Stimulator → network: which cells the stimulus drives")
        v = QtWidgets.QVBoxLayout(box)
        hint = QtWidgets.QLabel("Whatever the Stimulator or Protocols tab sends is injected as a current into the "
                                "ticked cells. Strength: the depolarization from a 1 V, 1 ms pulse.")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.stim_grid = QtWidgets.QGridLayout()
        self.stim_grid.setHorizontalSpacing(14)
        v.addLayout(self.stim_grid)
        self.stim_boxes: list[tuple[QtWidgets.QCheckBox, UnitSpinBox]] = []
        self._strength: dict[int, float] = {}      # the last strength of an unticked cell
        wire = QtWidgets.QHBoxLayout()
        self.stim_wire = QtWidgets.QLabel()
        self.stim_wire.setWordWrap(True)
        self.btn_record_stim = QtWidgets.QPushButton()
        self.btn_record_stim.setToolTip("Record the stimulus on a spare input (a channel named Stim). "
                                        "Protocols needs it to line the responses up with each stimulus")
        self.btn_open_stim = QtWidgets.QPushButton("Open the Stimulator")
        wire.addWidget(self.stim_wire, 1)
        wire.addWidget(self.btn_record_stim)
        wire.addWidget(self.btn_open_stim)
        v.addLayout(wire)
        left.addWidget(box)

        box = QtWidgets.QGroupBox("Cells")
        v = QtWidgets.QVBoxLayout(box)
        self.cells = QtWidgets.QTableWidget(0, len(_CELL_COLUMNS) + 1)
        self.cells.setHorizontalHeaderLabels([c[0] for c in _CELL_COLUMNS] + ["Firing now"])
        for i, (_, _, _, tip) in enumerate(_CELL_COLUMNS):
            self.cells.horizontalHeaderItem(i).setToolTip(tip)
        self.cells.horizontalHeaderItem(_LIVE_COL).setToolTip("Spikes/s over the last 10 s, while acquiring")
        self.cells.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.cells.setToolTip("Cells take units, e.g. -55 mV or 0.5 ms")
        v.addWidget(self.cells)
        left.addWidget(box, 3)

        box = QtWidgets.QGroupBox("Synapses: PSP (mV) from each spike of the row's cell onto the column's cell")
        v = QtWidgets.QVBoxLayout(box)
        self.syn = QtWidgets.QTableWidget(0, 0)
        self.syn.setToolTip("Positive excites (EPSP), negative inhibits (IPSP), 0 = no synapse")
        self.syn.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.syn)
        left.addWidget(box, 3)

        box = QtWidgets.QGroupBox("Recordings: the cell each input records")
        v = QtWidgets.QVBoxLayout(box)
        self.recs = QtWidgets.QTableWidget(0, 6)
        self.recs.setHorizontalHeaderLabels(["Cell", "Recording", "Gain", "Noise", "Sine", "Sine freq."])
        tips = ("Which cell this input records", "Intracellular: membrane potential with PSPs and action "
                "potentials. Extracellular: just the spikes", "Intracellular: trace V per V of membrane "
                "potential (10 shows -70 mV as -0.7 V)", "Recording noise on the trace (RMS)",
                "Amplitude of a sine added to the trace, e.g. mains hum; 0 = none", "Frequency of that sine")
        for i, tip in enumerate(tips):
            self.recs.horizontalHeaderItem(i).setToolTip(tip)
        self.recs.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.recs)
        left.addWidget(box, 2)

        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        left_box = QtWidgets.QWidget()
        left_box.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_column(left_box), 3)

        # ---- right: a picture of the network ---------------------------------------
        right = QtWidgets.QVBoxLayout()
        self.diagram = pg.PlotWidget()
        self.diagram.setMenuEnabled(False)
        self.diagram.setMouseEnabled(False, False)
        self.diagram.hideButtons()
        d = self.diagram.getPlotItem()
        d.hideAxis("left")
        d.hideAxis("bottom")
        d.setAspectLocked(True)
        d.setTitle("Network")
        right.addWidget(self.diagram, 1)
        key = QtWidgets.QLabel("Green arrows excite, red inhibit; thicker is stronger. "
                               "⚡ = the stimulator and the cells it drives. Labels show the inputs "
                               "recording each cell.")
        key.setWordWrap(True)
        right.addWidget(key)
        outer.addLayout(right, 2)

        # ---- signals ---------------------------------------------------------------
        self.load()
        self.n_cells.valueChanged.connect(self._resized)
        for w in (self.delay, self.syn_tau):
            w.valueChanged.connect(self._changed)
        self.cells.itemChanged.connect(self._changed)
        self.syn.itemChanged.connect(self._changed)
        self.recs.itemChanged.connect(self._changed)
        self.btn_reset.clicked.connect(self._reset)
        self.btn_record_stim.clicked.connect(self._record_stimulus)
        self.btn_open_stim.clicked.connect(lambda: self.main.tabs.show_tab("stim"))
        self._timer = QtCore.QTimer(self, interval=1000, timeout=self._show_rates)
        self._timer.start()
        self.apply_theme(style.LIGHT)

    # -- config -> widgets ------------------------------------------------------------
    @property
    def net(self) -> sim.Network:
        return self.main.cfg.sim_network

    def sync(self) -> None:
        """After a channel, wiring or stimulus-wire change: the inputs listed may differ."""
        self.load()

    def load(self) -> None:
        net = self.net
        n = len(net.cells)
        self._loading = True
        self.n_cells.setValue(n)
        self.delay.setValue(net.synapse_delay_ms)
        self.syn_tau.setValue(net.synapse_tau_ms)
        self.cells.setRowCount(n)
        self.cells.setVerticalHeaderLabels([f"Cell {i + 1}" for i in range(n)])
        for r, c in enumerate(net.cells):
            for col, (_, attr, unit, _) in enumerate(_CELL_COLUMNS):
                self.cells.setItem(r, col, QtWidgets.QTableWidgetItem(_fmt(getattr(c, attr), unit)))
            self.cells.setItem(r, _LIVE_COL, _readonly("—"))
        w = net.weights()
        self.syn.setRowCount(n)
        self.syn.setColumnCount(n)
        self.syn.setVerticalHeaderLabels([f"From {i + 1}" for i in range(n)])
        self.syn.setHorizontalHeaderLabels([f"To {i + 1}" for i in range(n)])
        for i in range(n):
            for j in range(n):
                item = _readonly("—") if i == j else QtWidgets.QTableWidgetItem(f"{w[i, j]:g}")
                if i == j:
                    item.setToolTip("A cell has no synapse onto itself")
                self.syn.setItem(i, j, item)
        self._tint_synapses()
        self._load_recordings()
        self._load_stimulus()
        self._loading = False
        self._show_message("")
        self._redraw()

    def _load_recordings(self) -> None:
        cfg = self.main.cfg
        inputs = [c for c in cfg.channels]
        self.recs.setRowCount(len(inputs))
        self.recs.setVerticalHeaderLabels([f"{c.physical} ({c.name})" for c in inputs])
        n = len(self.net.cells)
        for r, ch in enumerate(inputs):
            rec = sim.recording_for(self.net, ch.physical)
            monitor = ch.physical == cfg.stim_monitor
            cell = QtWidgets.QComboBox()
            cell.addItems([f"Cell {i + 1}" for i in range(n)])
            cell.setToolTip("The cell this input records")
            cell.setCurrentIndex(min(rec.cell, n - 1))
            kind = QtWidgets.QComboBox()
            kind.setToolTip("How this input records the cell")
            for label, key in _KINDS:
                kind.addItem(label, key)
            kind.setCurrentIndex(max(0, kind.findData(rec.kind)))
            for w in (cell, kind):
                w.setEnabled(not monitor)
                w.currentIndexChanged.connect(self._changed)
            self.recs.setCellWidget(r, 0, cell)
            self.recs.setCellWidget(r, 1, kind)
            for col, (attr, unit) in enumerate((("gain", ""), ("noise_v", "V"), ("sine_v", "V"), ("sine_hz", "Hz")), 2):
                item = QtWidgets.QTableWidgetItem(_fmt(getattr(rec, attr), unit))
                if monitor:
                    item = _readonly("")
                self.recs.setItem(r, col, item)
            if monitor:
                self.recs.cellWidget(r, 0).setToolTip("Wired to the stimulator (Acquisition › Stimulator › Record on): "
                                                      "it records the stimulus")
                self.recs.item(r, 2).setText("records the stimulus")

    def _load_stimulus(self) -> None:
        """The ⚡ box: a tick and a strength per cell, and where the stimulus is recorded."""
        while self.stim_grid.count():
            w = self.stim_grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.stim_boxes = []
        for i, c in enumerate(self.net.cells):
            tick = QtWidgets.QCheckBox(f"Cell {i + 1}")
            tick.setToolTip("Whether the stimulus reaches this cell")
            tick.setChecked(c.stimulus != 0)
            strength = UnitSpinBox(-1000, 1000, "mV", step=1)
            strength.setValue(c.stimulus if c.stimulus else self._strength.get(i, 25.0))
            strength.setEnabled(c.stimulus != 0)
            strength.setToolTip("Depolarization from a 1 V, 1 ms pulse; negative hyperpolarizes")
            tick.toggled.connect(lambda on, i=i: self._stimulus_edited(i))
            strength.valueChanged.connect(lambda *_, i=i: self._stimulus_edited(i))
            row, col = divmod(i, 5)
            self.stim_grid.addWidget(tick, row, 2 * col)
            self.stim_grid.addWidget(strength, row, 2 * col + 1)
            self.stim_boxes.append((tick, strength))
        self._show_wire()

    def _show_wire(self) -> None:
        cfg = self.main.cfg
        ch = next((c for c in cfg.channels if c.physical == cfg.stim_monitor), None) if cfg.stim_monitor else None
        driven = [i + 1 for i, c in enumerate(self.net.cells) if c.stimulus]
        drives = (f"The stimulus drives cell{'s' if len(driven) > 1 else ''} "
                  f"{', '.join(map(str, driven))}." if driven else
                  "<span style='color:#e0524f'>No cell is ticked: stimuli won't affect the network.</span>")
        if ch is not None:
            self.stim_wire.setText(f"{drives} It's recorded on {ch.physical} ({ch.name}).")
            self.btn_record_stim.setVisible(False)
        else:
            spare = self._spare_input()
            self.stim_wire.setText(f"{drives} It isn't recorded on an input yet: Protocols needs that.")
            self.btn_record_stim.setText(f"Record it on {spare}" if spare else "No spare input")
            self.btn_record_stim.setEnabled(bool(spare))
            self.btn_record_stim.setVisible(True)

    def _spare_input(self) -> str:
        return next((phys for _, phys, ok, _ in self.main.monitor_choices() if phys and ok), "")

    def _record_stimulus(self) -> None:
        spare = self._spare_input()
        if spare and self.main.set_stim_monitor(spare):      # syncs this tab too
            self._show_message(f"The stimulus is now recorded on {spare}, as a channel named Stim.")

    def _stimulus_edited(self, i: int) -> None:
        """A tick or strength changed: write it into the cells table, which drives the network."""
        if self._loading:
            return
        tick, strength = self.stim_boxes[i]
        strength.setEnabled(tick.isChecked())
        if not tick.isChecked():
            self._strength[i] = strength.value()
        value = strength.value() if tick.isChecked() else 0.0
        col = next(k for k, c in enumerate(_CELL_COLUMNS) if c[1] == "stimulus")
        self.cells.item(i, col).setText(_fmt(value, "mV"))       # → _changed
        self._show_wire()

    def sync_stimulus(self) -> None:
        """The cells table's Stimulus column changed: show it in the ⚡ box."""
        for i, c in enumerate(self.net.cells[:len(self.stim_boxes)]):
            tick, strength = self.stim_boxes[i]
            for w in (tick, strength):
                w.blockSignals(True)
            tick.setChecked(c.stimulus != 0)
            if c.stimulus:
                strength.setValue(c.stimulus)
            strength.setEnabled(c.stimulus != 0)
            for w in (tick, strength):
                w.blockSignals(False)
        self._show_wire()

    # -- widgets -> config ------------------------------------------------------------
    def _read(self) -> sim.Network:
        """The network the widgets describe. Raises ValueError for a bad cell."""
        n = self.cells.rowCount()
        cells = []
        for r in range(n):
            c = sim.Cell()
            for col, (head, attr, unit, _) in enumerate(_CELL_COLUMNS):
                setattr(c, attr, self._number(self.cells, r, col, unit, f"Cell {r + 1}: {head.lower()}"))
            cells.append(c)
        w = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                if i != j:
                    w[i, j] = self._number(self.syn, i, j, "mV", f"Synapse {i + 1}→{j + 1}")
        net = sim.Network(cells, self.delay.value(), self.syn_tau.value())
        net.set_weights(w)
        cfg = self.main.cfg
        for r, ch in enumerate(cfg.channels):
            if ch.physical == cfg.stim_monitor or self.recs.cellWidget(r, 0) is None:
                continue
            rec = sim.Recording(ch.physical, self.recs.cellWidget(r, 0).currentIndex(),
                                self.recs.cellWidget(r, 1).currentData())
            for col, (attr, unit, name) in enumerate((("gain", "", "gain"), ("noise_v", "V", "noise"),
                                                      ("sine_v", "V", "sine"), ("sine_hz", "Hz", "sine freq.")), 2):
                setattr(rec, attr, self._number(self.recs, r, col, unit, f"{ch.physical} {name}"))
            net.recordings.append(rec)
        # inputs not shown here (not used by a channel) keep their settings
        shown = {r.physical for r in net.recordings}
        net.recordings += [sim.Recording(**vars(r)) for r in self.net.recordings if r.physical not in shown]
        for r in net.recordings:
            r.cell = min(r.cell, n - 1)
        return net

    @staticmethod
    def _number(table, r, c, unit, what) -> float:
        item = table.item(r, c)
        text = item.text() if item else ""
        try:
            return parse_quantity(text, _same_kind(unit))[0] if unit else float(text)
        except ValueError:
            raise ValueError(f"{what} must be a number" + (f", optionally with a unit (e.g. 5 {unit})."
                                                           if unit else ".")) from None

    def _changed(self, *_) -> None:
        if self._loading:
            return
        try:
            net = self._read()
            sim.validate(net)
        except ValueError as e:
            self._show_message(str(e), error=True)
            return
        # A new object, so the acquisition thread picks up the change all at once.
        self.main.cfg.sim_network = net
        self._tint_synapses()
        self._show_message("")
        self.sync_stimulus()
        self._redraw()

    def _resized(self, n: int) -> None:
        if self._loading:
            return
        try:
            net = self._read()
        except ValueError:
            net = sim.copy_network(self.net)
        sim.resize(net, n)
        self.main.cfg.sim_network = net
        self.load()

    def _reset(self) -> None:
        self.main.cfg.sim_network = sim.default_network()
        self.load()

    def _show_message(self, text: str, error: bool = False) -> None:
        t = style.tokens(self._mode)
        self.message.setStyleSheet(f"color: {t['error'] if error else t['muted']}")
        self.message.setText(text)

    def _show_rates(self) -> None:
        be = self.main.backend
        if not self.isVisible() or not hasattr(be, "rates"):
            return
        rates = be.rates() if be.running else None
        self.cells.blockSignals(True)
        for r in range(self.cells.rowCount()):
            item = self.cells.item(r, _LIVE_COL)
            if item is not None:
                item.setText(f"{rates[r]:.1f} /s" if rates and r < len(rates) else "—")
        self.cells.blockSignals(False)

    # -- looks --------------------------------------------------------------------------
    def _tint_synapses(self) -> None:
        self.syn.blockSignals(True)
        for i in range(self.syn.rowCount()):
            for j in range(self.syn.columnCount()):
                item = self.syn.item(i, j)
                if item is None or i == j:
                    continue
                try:
                    v = float(parse_quantity(item.text(), _same_kind("mV"))[0])
                except ValueError:
                    v = 0.0
                a = int(min(110, 25 + 6 * abs(v)))
                item.setBackground(QtGui.QColor(0, 160, 80, a) if v > 0 else
                                   QtGui.QColor(220, 50, 50, a) if v < 0 else QtGui.QBrush())
        self.syn.blockSignals(False)

    def _redraw(self) -> None:
        d = self.diagram.getPlotItem()
        d.clear()
        net = self.net
        n = len(net.cells)
        th = theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT]
        ang = np.pi / 2 - 2 * np.pi * np.arange(n) / n
        pos = np.column_stack([np.cos(ang), np.sin(ang)]) * (0 if n == 1 else 1.0)
        r_cell = 0.18
        w = net.weights()
        biggest = max(1e-9, np.abs(w).max())
        for i in range(n):
            for j in range(n):
                if i == j or not w[i, j]:
                    continue
                a, b = pos[i], pos[j]
                u = (b - a) / np.linalg.norm(b - a)
                side = np.array([-u[1], u[0]]) * 0.06           # two-way synapses side by side
                start, end = a + u * r_cell + side, b - u * (r_cell + 0.02) + side
                color = "#2fae6a" if w[i, j] > 0 else "#e0524f"
                width = 1.5 + 4 * abs(w[i, j]) / biggest
                d.addItem(pg.PlotCurveItem([start[0], end[0]], [start[1], end[1]], pen=pg.mkPen(color, width=width)))
                arrow = pg.ArrowItem(pos=tuple(end), angle=180 - np.degrees(np.arctan2(u[1], u[0])),
                                     headLen=14, tipAngle=35, pen=pg.mkPen(color), brush=color)
                d.addItem(arrow)
        recorded: dict[int, list[str]] = {}
        for ch in self.main.cfg.channels:
            if ch.physical != self.main.cfg.stim_monitor:
                rec = sim.recording_for(net, ch.physical)
                recorded.setdefault(min(rec.cell, n - 1), []).append(
                    ch.physical + (" (in)" if rec.kind == "intracellular" else " (ex)"))
        stim_at = np.array([-1.45, 1.35])
        driven = [i for i in range(n) if net.cells[i].stimulus]
        for i in driven:
            u = (pos[i] - stim_at) / max(np.linalg.norm(pos[i] - stim_at), 1e-9)
            start, end = stim_at + u * 0.16, pos[i] - u * (r_cell + 0.02)
            color = "#E69F00"
            d.addItem(pg.PlotCurveItem([start[0], end[0]], [start[1], end[1]],
                                       pen=pg.mkPen(color, width=2, style=QtCore.Qt.PenStyle.DashLine)))
            d.addItem(pg.ArrowItem(pos=tuple(end), angle=180 - np.degrees(np.arctan2(u[1], u[0])), headLen=12,
                                   tipAngle=35, pen=pg.mkPen(color), brush=color))
        d.addItem(pg.ScatterPlotItem([stim_at[0]], [stim_at[1]], size=30, symbol="s",
                                     pen=pg.mkPen("#E69F00", width=2), brush=pg.mkBrush(th.background)))
        bolt = pg.TextItem("⚡", color="#E69F00", anchor=(0.5, 0.5))
        bolt.setPos(*stim_at)
        d.addItem(bolt)
        cap = pg.TextItem("Stimulator", color=th.foreground, anchor=(0.5, 0))
        cap.setPos(stim_at[0], stim_at[1] - 0.14)
        d.addItem(cap)
        d.addItem(pg.ScatterPlotItem(pos[:, 0], pos[:, 1], size=46, pen=pg.mkPen(th.foreground, width=2),
                                     brush=pg.mkBrush(cfgmod.PALETTE[1])))
        for i in range(n):
            label = pg.TextItem(str(i + 1), color="#000000", anchor=(0.5, 0.5))
            label.setPos(*pos[i])
            d.addItem(label)
            out = pos[i] * 1.38 if n > 1 else np.array([0, -0.35])
            extra = recorded.get(i, [])
            if extra:
                t = pg.TextItem("  ".join(extra), color=th.foreground, anchor=(0.5, 0.5))
                t.setPos(*out)
                d.addItem(t)
        d.setXRange(-1.7, 1.7, padding=0)
        d.setYRange(-1.6, 1.6, padding=0)

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        th = theme.THEMES[theme.DARK if mode == style.DARK else theme.LIGHT]
        self.diagram.setBackground(th.background)
        self.diagram.getPlotItem().setTitle("Network", color=th.foreground)
        self._redraw()
