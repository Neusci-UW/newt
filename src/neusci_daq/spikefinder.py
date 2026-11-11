"""Finding the action potentials every Analysis chart shares: the search's settings, the
units found with their ticks, colours and opacity, and the exports (see analysis.py)."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import colours
from . import style
from .channels import ColorButton
from .helpview import add_help
from .unitbox import UnitSpinBox
from .units import best_prefix
from .viewkit import View


class SpikeFinder(View):
    """Finding the action potentials every chart shares: its settings, the units found and
    their ticks and colours, and the exports."""

    def __init__(self, page: "AnalysisPage"):
        super().__init__(page)
        self.events: A.Events | None = None
        self.unit_colors: dict[int, str] = {}     # 0-based unit -> the colour picked for it
        self.unit_opacity: dict[int, float] = {}  # 0-based unit -> how solid it's drawn, if not fully
        self._unticked: set[int] = set()          # units left out of the charts
        left = QtWidgets.QVBoxLayout(self)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(6)
        self.channel = QtWidgets.QComboBox()
        self.channel.setToolTip("The channel to find action potentials on")

        box = QtWidgets.QGroupBox("Find action potentials")
        v = QtWidgets.QVBoxLayout(box)
        self.btn_find = QtWidgets.QPushButton("Find action potentials")
        self.btn_find.setObjectName("start")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_clear.setToolTip("Clear the action potentials found. Changing a setting won't search again "
                                  "until you press Find action potentials")
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.btn_find, 1)
        buttons.addWidget(self.btn_clear)
        v.addLayout(buttons)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Unit", "Count", "Rate", "Amplitude", "Width", "Colour"])
        self.table.horizontalHeaderItem(4).setToolTip("Mean waveform: trough to peak")
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.table.setToolTip("Tick the units to include: every chart shows just those, unless it has its own")
        self.table.setMinimumHeight(90)
        self.table.setMaximumHeight(170)
        v.addWidget(self.table)
        self.btn_more = QtWidgets.QToolButton(text="Search settings", checkable=True, autoRaise=True)
        self.btn_more.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_more.setToolTip("Show or hide how action potentials are found")
        v.addWidget(self.btn_more)
        self.search = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(self.search)
        f.setContentsMargins(0, 0, 0, 0)
        self.method = QtWidgets.QComboBox()
        self.method.addItem("Manual threshold", False)
        self.method.addItem("Bimodal (automatic)", True)
        self.method.setToolTip("Bimodal: the threshold goes in the valley between the many small noise "
                               "peaks and the few big spikes. If they don't form two groups, the threshold "
                               "below is used instead")
        self.threshold = QtWidgets.QLineEdit("5 SD")
        self.threshold.setToolTip("A level from the baseline, e.g. 50 mV or 0.05 V, or a number of noise "
                                  "SDs, e.g. 5 SD (a plain number means SDs). Or drag the red line")
        bip = QtWidgets.QHBoxLayout()
        self.biphasic = QtWidgets.QCheckBox("On")
        self.biphasic.setToolTip("Keep only events that also swing the other way, as a real extracellular "
                                 "spike does and many artifacts don't")
        self.bip_frac = QtWidgets.QDoubleSpinBox(minimum=0.05, maximum=1, value=0.3, singleStep=0.05, decimals=2)
        self.bip_frac.setToolTip("How far the other way, as a fraction of the threshold (0 to 1)")
        self.bip_ms = UnitSpinBox(0.1, 20, "ms", step=0.5)
        self.bip_ms.setValue(1.0)
        self.bip_ms.setToolTip("Within this long of the event's peak (before or after)")
        bip.addWidget(self.biphasic)
        bip.addWidget(self.bip_frac)
        self.polarity = QtWidgets.QComboBox()
        self.polarity.setToolTip("Which way spikes go from the baseline: down, up, or either")
        for label, key in (("Negative (extracellular)", "negative"), ("Positive (intracellular)", "positive"),
                           ("Either", "either")):
            self.polarity.addItem(label, key)
        self.dead = UnitSpinBox(0.1, 100, "ms", step=0.5)
        self.dead.setValue(1.0)
        self.dead.setToolTip("After an event, no other can start for this long")
        self.pre = UnitSpinBox(0.1, 50, "ms", step=0.5)
        self.pre.setToolTip("How much of each event's waveform to keep before its peak")
        self.pre.setValue(1.0)
        self.post = UnitSpinBox(0.1, 50, "ms", step=0.5)
        self.post.setToolTip("How much of each event's waveform to keep after its peak")
        self.post.setValue(2.0)
        self.units = QtWidgets.QComboBox()
        self.units.addItem("Auto", None)
        for k in range(1, 9):
            self.units.addItem(str(k), k)
        self.units.setToolTip("How many units to sort the events into. Auto picks however many "
                              "separate clearly by shape")
        f.addRow("Method", self.method)
        f.addRow("Threshold", self.threshold)
        f.addRow("Direction", self.polarity)
        f.addRow("Biphasic", bip)
        f.addRow("Within", self.bip_ms)
        f.addRow("Dead time", self.dead)
        f.addRow("Waveform before", self.pre)
        f.addRow("Waveform after", self.post)
        f.addRow("Units", self.units)
        v.addWidget(self.search)
        self.btn_more.toggled.connect(self._show_search)
        self.btn_more.setChecked(self.main.settings.value("analysis/search_settings", True, type=bool))
        self._show_search(self.btn_more.isChecked())
        left.addWidget(box)
        self.btn_help = add_help(box, "spike_sorting")
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        left.addWidget(self.message)
        self.btn_export_avg = QtWidgets.QPushButton("Export averages…")
        self.btn_export_avg.setToolTip("Save the average of the chosen trials (each stimulus group's, when "
                                       "grouped) with its SD, SEM and n at every point, as a CSV file")
        self.btn_export_avg.clicked.connect(self._export_averages_dialog)
        self.btn_export = QtWidgets.QPushButton("Export events…")
        self.btn_export.setToolTip("Save every event (trial, time, unit, amplitude) as a CSV file")
        self.btn_export.setEnabled(False)
        exports = QtWidgets.QHBoxLayout()
        exports.addWidget(self.btn_export)
        exports.addWidget(self.btn_export_avg)
        left.addLayout(exports)

        self.channel.currentIndexChanged.connect(lambda *_: self.settings_changed())
        self.btn_find.clicked.connect(self._find_pressed)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_export.clicked.connect(self._export_dialog)
        self.table.itemChanged.connect(self._unit_ticked)
        # Changing how events are found searches again, once the changes stop.
        self._find_timer = QtCore.QTimer(self, singleShot=True, interval=250)
        self._find_timer.timeout.connect(self._find_again)
        self._opacity_timer = QtCore.QTimer(self, singleShot=True, interval=80)   # a slider moves many steps
        self._opacity_timer.timeout.connect(lambda: self.page.canvas.units_changed())
        self.threshold.editingFinished.connect(self._detection_changed)
        for w in (self.method, self.polarity, self.units):
            w.currentIndexChanged.connect(lambda *_: self._detection_changed())
        for w in (self.dead, self.pre, self.post, self.bip_frac, self.bip_ms):
            w.valueChanged.connect(lambda *_: self._detection_changed())
        self.biphasic.toggled.connect(lambda *_: self._detection_changed())
        self._paused = False                      # Clear stops searching again until the next Find

    def state(self) -> dict:
        """What's set here, to save with the session."""
        return {"channel": self.channel.currentText(), "method": self.method.currentIndex(),
                "threshold": self.threshold.text(), "polarity": self.polarity.currentData(),
                "biphasic": self.biphasic.isChecked(), "bip_frac": self.bip_frac.value(), "bip_ms": self.bip_ms.value(),
                "dead": self.dead.value(), "pre": self.pre.value(), "post": self.post.value(),
                "units": self.units.currentIndex(), "unticked": sorted(self._unticked),
                "colors": {str(u): c for u, c in self.unit_colors.items()},
                "opacity": {str(u): o for u, o in self.unit_opacity.items()}}

    def restore(self, state: dict, events: A.Events | None) -> None:
        """Back as it was saved, without searching again."""
        self._find_timer.stop()
        widgets = (self.channel, self.method, self.threshold, self.polarity, self.biphasic, self.bip_frac,
                   self.bip_ms, self.dead, self.pre, self.post, self.units)
        for w in widgets:
            w.blockSignals(True)
        self.channel.setCurrentText(state["channel"])
        self.method.setCurrentIndex(state["method"])
        self.threshold.setText(state["threshold"])
        self.polarity.setCurrentIndex(max(0, self.polarity.findData(state["polarity"])))
        self.biphasic.setChecked(state["biphasic"])
        self.units.setCurrentIndex(state["units"])
        for w, key in ((self.bip_frac, "bip_frac"), (self.bip_ms, "bip_ms"), (self.dead, "dead"), (self.pre, "pre"),
                       (self.post, "post")):
            w.setValue(state[key])
        for w in widgets:
            w.blockSignals(False)
        self._unticked = set(state["unticked"])
        self.unit_colors = {int(u): c for u, c in state["colors"].items()}
        self.unit_opacity = {int(u): o for u, o in state["opacity"].items()}
        self.events = events
        self._show_results()
        self.page.canvas.refresh()
        self.page.events_changed()

    def _show_search(self, on: bool) -> None:
        self.main.settings.setValue("analysis/search_settings", on)
        self.search.setVisible(on)
        self.btn_more.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)

    # -- data ------------------------------------------------------------------------------
    @property
    def blocks(self):
        return self.page.blocks

    def set_channels(self, names: list[str]) -> None:
        """The channels to sort spikes on: all but the stimulus channel (it has its own chart)."""
        stim = self.page.stim_channel.currentData()
        names = [n for n in names if n != stim]
        keep = self.channel.currentText()
        self.channel.blockSignals(True)
        self.channel.clear()
        self.channel.addItems(names)
        if keep in names:
            self.channel.setCurrentText(keep)
        self.channel.blockSignals(False)
        self.settings_changed()

    def usable(self, idx: list[int], hidden_too: bool = False) -> list[int]:
        """Those of these blocks that have this channel at the first block's rate (and aren't
        left out of everything, unless hidden_too: a chart's own groups show anyway)."""
        name = self.channel.currentText()
        good = [b for b in self.blocks if b is not None]
        fs = good[0]["sample_rate"] if good else 0
        hidden = set() if hidden_too else self.page.hidden_blocks()
        return [i for i in idx if i not in hidden and self.page.has(i, name) and self.blocks[i]["sample_rate"] == fs]

    def _chosen(self) -> list[int]:
        """Indices of the chosen blocks that have this channel at the first block's rate."""
        idx = list(range(len(self.blocks)))
        group = self.page.group_blocks()
        if group is not None:
            idx = sorted(set(idx) & set(group))
        return self.usable(idx)

    def chosen_or_none(self) -> list[int]:
        return self._chosen() if self.blocks else []

    def _searched(self) -> list[int]:
        """What to search: the chosen blocks, and those charts are pinned to."""
        return sorted(set(self.chosen_or_none()) | set(self.page.canvas.pinned_blocks()))

    def _trace(self, i: int) -> np.ndarray:
        return self.page.trace(i, self.channel.currentText())

    def settings_changed(self) -> None:
        if self.events is not None:              # the same search, on what's now chosen
            self._find_timer.start()
        self.events = None
        self._show_message("")
        self._show_results()
        self.page.canvas.refresh()

    def pins_changed(self) -> None:
        """A chart was pinned, or stopped being: its blocks need spikes too."""
        if self.events is not None and not set(self._searched()) <= set(self.events.block.tolist()):
            self._find_timer.start()

    def current_changed(self) -> None:
        self.page.canvas.threshold_changed()      # the baseline is the highlighted block's

    # -- the units --------------------------------------------------------------------------------
    def shown_units(self) -> set[int]:
        """0-based units ticked in the table: these are in the charts."""
        n = self.events.n_units if self.events is not None else 0
        return set(range(n)) - self._unticked

    def show_units(self, units) -> None:
        """Tick just these units."""
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(QtCore.Qt.CheckState.Checked if r in units
                                                else QtCore.Qt.CheckState.Unchecked)

    def _unit_ticked(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        u = item.row()
        was = u in self._unticked
        now = item.checkState() != QtCore.Qt.CheckState.Checked
        if was != now:
            self._unticked ^= {u}
            self.page.canvas.units_changed()

    def color_of(self, u: int) -> str:
        """Unit u's colour (0-based): the one picked for it, else its place in the palette."""
        return self.unit_colors.get(int(u), colours.unit_color(u))

    def opacity_of(self, u: int) -> float:
        return self.unit_opacity.get(int(u), 1.0)

    def qcolor(self, u: int) -> QtGui.QColor:
        """Unit u's colour with its opacity, to draw with."""
        c = QtGui.QColor(self.color_of(u))
        c.setAlphaF(self.opacity_of(u))
        return c

    def set_unit_color(self, u: int, color: str) -> None:
        self.unit_colors[u] = color
        self._show_results()
        self.page.canvas.units_changed()

    def set_unit_opacity(self, u: int, opacity: float) -> None:
        self.unit_opacity[u] = opacity
        self._opacity_timer.start()

    def brushes(self, units: np.ndarray) -> list:
        made = {int(u): pg.mkBrush(self.qcolor(u)) for u in np.unique(units)}
        return [made[int(u)] for u in units]

    # -- the threshold ---------------------------------------------------------------------------
    def baseline(self) -> float:
        y = self._trace(self.page.index()) if self.blocks else None
        return float(np.median(y)) if y is not None else 0.0

    def unit(self) -> str:
        return self.page.unit_of(self.channel.currentText())

    def fmt(self, v: float) -> str:
        """A level in the channel's unit, with a sensible prefix: 0.0374 V -> 37.4 mV."""
        n, u = best_prefix(v, self.unit())
        return f"{n:.3g} {u}"

    def _threshold(self) -> tuple[str, float]:
        """("sd", n) or ("level", trace units), as typed. Raises ValueError."""
        return A.parse_threshold(self.threshold.text(), self.unit())

    def level(self) -> float | None:
        """The threshold in trace units from the baseline, as Find would use it. None
        before a bimodal search (it depends on every block)."""
        if self.events is not None:
            return self.events.level
        if self.method.currentData():
            return None
        try:
            kind, v = self._threshold()
        except ValueError:
            return None
        if kind == "level":
            return v
        y = self._trace(self.page.index()) if self.blocks else None
        return v * A.noise_sd(y) if y is not None else None

    def cyclic_defaults(self) -> dict:
        """These settings as the defaults for a cyclic measurement (Add analysis channel): the
        threshold as an absolute level on this channel, the direction and the dead time."""
        pol = self.polarity.currentData()
        out = {"channel": self.channel.currentText(), "dir": "below" if pol == "negative" else "above",
               "dead": self.dead.value(), "level": None}
        level = self.level() if self.blocks else None
        if level is not None:
            out["level"] = self.baseline() + (-level if pol == "negative" else level)
        return out

    def _detection_changed(self) -> None:
        try:
            self._threshold()
        except ValueError as e:
            self._show_message(str(e), error=True)
            return
        self.events = None
        self._show_results()
        self.page.canvas.threshold_changed()
        self.page.events_changed()
        if not self._paused:
            self._find_timer.start()

    def _find_again(self) -> None:
        if self._searched():
            self.find()

    def threshold_dragged(self, at: float) -> None:
        """The red line was dragged to `at`: a level you set."""
        level = abs(at - self.baseline())
        self.method.blockSignals(True)
        self.method.setCurrentIndex(0)
        self.method.blockSignals(False)
        self.threshold.setText(self.fmt(level))
        if self.polarity.currentData() != "either" and \
                (at > self.baseline()) != (self.polarity.currentData() == "positive"):
            self.polarity.blockSignals(True)
            self.polarity.setCurrentIndex(self.polarity.findData("positive" if at > self.baseline() else "negative"))
            self.polarity.blockSignals(False)
        self.events = None
        self._show_results()
        self.page.events_changed()
        if not self._paused:
            self._find_timer.start()

    # -- finding -----------------------------------------------------------------------------
    def clear(self) -> None:
        """Forget the action potentials, and don't search again until Find is pressed."""
        self._find_timer.stop()
        self._paused = True
        self.events = None
        self._unticked = set()
        self._show_results()
        self.page.events_changed()
        self._show_message("Cleared. Press Find action potentials to search again.")

    def _find_pressed(self) -> None:
        self._find_timer.stop()
        if self.find() is not None:
            self.page.canvas.rescale()

    def find(self) -> A.Events | None:
        try:
            chosen = self._searched()
            if not chosen:
                raise ValueError("No trials to search: record or open some, and check Trials and Channel.")
            self.main.statusBar().showMessage(f"Searching {len(chosen)} trial(s)…")
            QtWidgets.QApplication.processEvents()
            kind, v = self._threshold()
            if self.method.currentData() and kind == "level":
                kind, v = "sd", 5.0                      # bimodal's fallback is in SDs
            ev = A.find_events([(i, self._trace(i), self.blocks[i]["sample_rate"]) for i in chosen],
                               threshold=v, fixed=kind == "level", bimodal=bool(self.method.currentData()),
                               biphasic=self.bip_frac.value() if self.biphasic.isChecked() else None,
                               biphasic_ms=self.bip_ms.value(),
                               polarity=self.polarity.currentData(), dead_ms=self.dead.value(),
                               pre_ms=self.pre.value(), post_ms=self.post.value(),
                               n_units=self.units.currentData())
        except ValueError as e:
            self._show_message(str(e), error=True)
            return None
        finally:
            self.main.statusBar().clearMessage()
        self.events = ev
        self._paused = False
        self._show_results()
        self.page.events_changed()
        return ev

    def _show_results(self) -> None:
        """The units found, in the table."""
        ev = self.events
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.btn_export.setEnabled(ev is not None and len(ev.t) > 0)
        if ev is None:
            self.table.blockSignals(False)
            return
        for s in ev.summary():
            u = s["unit"] - 1
            r = self.table.rowCount()
            self.table.insertRow(r)
            unit_item = QtWidgets.QTableWidgetItem(f"Unit {s['unit']}")
            unit_item.setIcon(style.dot_icon(self.color_of(u)))
            unit_item.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled)
            unit_item.setCheckState(QtCore.Qt.CheckState.Unchecked if u in self._unticked
                                    else QtCore.Qt.CheckState.Checked)
            self.table.setItem(r, 0, unit_item)
            pick = ColorButton(self.color_of(u), lambda c, u=u: self.set_unit_color(u, c),
                               f"Pick Unit {s['unit']}'s colour and opacity, used on every graph",
                               opacity=self.opacity_of(u), on_opacity=lambda v, u=u: self.set_unit_opacity(u, v))
            pick.setFixedWidth(26)
            pick.setText("▾")
            self.table.setCellWidget(r, 5, pick)
            for c, text in enumerate((f"{s['count']}", f"{s['rate']:.2f} /s", self.fmt(s['amplitude']),
                                      f"{s['width_ms']:.2f} ms"), 1):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
        self.table.blockSignals(False)
        n = len(ev.t)
        msg = (f"{n} event(s) in {len(np.unique(ev.block))} trial(s), {ev.n_units} unit(s); threshold "
               f"{self.fmt(ev.level)} from the baseline." if n else
               f"No events past the threshold ({self.fmt(ev.level)}): try a lower one.")
        self._show_message(" ".join([msg] + ev.notes))

    def export(self, path: str | Path) -> Path:
        ev = self.events
        path = Path(path)
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["trial", "time_s", "unit", "amplitude", "channel"])
            for b, t, u, a in zip(ev.block, ev.t, ev.unit, ev.amplitude):
                w.writerow([self.page._paths[b].stem, f"{t:.6f}", int(u) + 1, f"{a:.6g}",
                            self.channel.currentText()])
        return path

    def export_averages(self, path: str | Path) -> Path:
        """time, then mean, SD, SEM and n for each average shown, as CSV."""
        chosen = self._chosen()
        if not chosen:
            raise ValueError("No trials chosen.")
        fs = self.blocks[chosen[0]]["sample_rate"]
        n = max(len(self._trace(i)) for i in chosen)
        Y = np.full((len(chosen), n), np.nan)
        for r, i in enumerate(chosen):
            y = self._trace(i)
            Y[r, :len(y)] = y
        unit = self.page.unit_of(self.channel.currentText())
        averaged = self.page.plan(chosen)[0] or [(None, "All chosen trials", chosen)]
        sets = self.page.averages(chosen, Y, averaged)
        path = Path(path)
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            head = ["time_s"]
            for _, label, *_rest in sets:
                head += [f"{label} mean ({unit})", f"{label} SD ({unit})", f"{label} SEM ({unit})", f"{label} n"]
            w.writerow(head)
            counts = [A.mean_spread(Y[[r for r, i in enumerate(chosen) if g is None or
                                       i in self.page.shown[g][1]]])[3] for g, *_rest in sets]
            for k in range(n):
                row = [f"{k / fs:.6f}"]
                for (g, label, mean, sd, sem, _), cnt in zip(sets, counts):
                    row += [f"{mean[k]:.6g}", f"{sd[k]:.6g}", f"{sem[k]:.6g}", int(cnt[k])]
                w.writerow(row)
        return path

    def _export_averages_dialog(self) -> None:
        start = str(Path(self.main._start_dir()) / "averages.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export averages", start, "CSV (*.csv)")
        if path:
            try:
                self.export_averages(path)
                self._show_message(f"Saved the averages to {Path(path).name}.")
            except ValueError as e:
                self._show_message(str(e), error=True)

    def _export_dialog(self) -> None:
        start = str(Path(self.main._start_dir()) / "events.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export events", start, "CSV (*.csv)")
        if path:
            self.export(path)
            self._show_message(f"Saved {len(self.events.t)} event(s) to {Path(path).name}.")

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        if self.events is not None:
            self._show_results()
