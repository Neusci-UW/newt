"""The charts of how the channels respond: the recording and its derivatives, with the
spikes found, triggered averages, what the stimulus fit did, and the charts on the phase of
a repeating stimulus.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from . import colors
from . import style, theme
from .chartbase import Chart, dot_size
from .plotzoom import AxisGroup, MultiAxis, OverlayCurve, TraceCurve, plot_widget
from .spikecharts import draw_band, draw_step_band
from .unitbox import UnitSpinBox
from .viewkit import label_axis, style_plot, with_unit

SERIES = (("V", 0), ("dV/dt", 1), ("d²V/dt²", 2))
MAX_MARKED = 2000                  # spikes drawn over the trace when a unit is highlighted


def _times_by_block(ev: A.Events | None, units: set[int]) -> dict[int, np.ndarray] | None:
    """(block -> spike times s) of these units, or None without events."""
    if ev is None:
        return None
    m = np.isin(ev.unit, list(units))
    return {int(b): ev.t[m & (ev.block == b)] for b in np.unique(ev.block[m])}


class ResponseChart(Chart):
    kind = "Response"
    y = "signal"
    tip = "What did each channel record through the trial, and where are the spikes?"
    link = "time"
    help = "comparison"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self._plots: list[pg.PlotWidget] = []
        self._plot_units: list[str] = []
        self._plot_keys: list[tuple[str, int]] = []      # what each stacked plot shows: (channel, series)
        self._bar_gaps: dict[tuple[str, int], tuple] = {}  # how far you dragged each one's scale bar
        self.series = QtWidgets.QTableWidget(0, len(SERIES))
        self.series.setHorizontalHeaderLabels([s for s, _ in SERIES])
        self.series.setToolTip("Tick what to show for each channel, on one time axis. The stimulus channel "
                               "has a chart of its own, Stimulus")
        self.series.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.series.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.series.setMinimumHeight(110)
        self.form.addRow(self.series)
        self.settings_box.show()
        self.d_smooth = UnitSpinBox(0, 50, "ms", step=0.1)
        self.d_smooth.setValue(0.2)
        self.d_smooth.setToolTip("Differentiating amplifies noise: average over this long first (0 = none)")
        self.field("smooth", "Smooth for d/dt", self.d_smooth)
        self.layout_mode = QtWidgets.QComboBox()
        self.layout_mode.setToolTip("Show the channels on separate axes, in group and trial colors, or the "
                                    "average of each on one plot, scaled to fit")
        self.layout_mode.addItem("Stacked, each on its own axis", "stacked")
        self.layout_mode.addItem("Overlaid, each scaled to fit", "overlaid")
        self.field("layout", "Layout", self.layout_mode)
        self.show_trials = QtWidgets.QCheckBox("Show each trial")
        self.show_trials.setToolTip("Draw every trial of a group, faint, in the group's color")
        self.show_average = QtWidgets.QCheckBox("Show the average")
        self.show_average.setToolTip("Draw each group's average (and its Spread). Tick both for faint trials "
                                     "behind a bold average; one stays ticked")
        self.show_average.setChecked(True)
        self.field("show_trials", "", self.show_trials, self._view_changed)
        self.field("show_average", "", self.show_average, self._view_changed)
        self.series.itemChanged.connect(lambda *_: self._setting_changed(None))
        self._axes = AxisGroup()
        self._saved_ticks: list | None = None        # from the layout, until the channels are known
        # the spikes, on the channel the Spikes panel sorts
        box = QtWidgets.QGroupBox("Spikes")
        spikes = QtWidgets.QFormLayout(box)
        self._side.insertWidget(self._side.count() - 1, box)       # above the stretch
        self.show_thr = QtWidgets.QCheckBox("Show the spike threshold")
        self.show_thr.setToolTip("A red line at the threshold: drag it to set the level. Hiding it doesn't "
                                 "change the search")
        self.field("threshold", "", self.show_thr, self.threshold_changed, spikes)
        self.show_dots = QtWidgets.QCheckBox("Mark each spike with a dot")
        self.show_dots.setToolTip("A dot on each spike found, in its unit's color")
        self.field("dots", "", self.show_dots, self._show_spikes, spikes)
        self.mark_size = dot_size(self.main.settings, "mark_point_size", 9, 20, "The size of the dots marking "
                                                                                 "each spike")
        self.field("dot_size", "Dot size", self.mark_size, self._show_spikes, spikes)
        for b in (self.show_thr, self.show_dots):
            b.toggled.connect(self._spikes_set_by_you)
        self.spikes_note = QtWidgets.QLabel()
        self.spikes_note.setWordWrap(True)
        spikes.addRow(self.spikes_note)
        self._spikes_set = False      # both boxes turn on when spikes are first found; after that, as you set them
        self._spike_channel = ""      # the channel they're drawn on, the one Spikes sorts
        self._faded: list | None = None   # its curves, faded when some units stand out; None: not drawn
        self._heights: tuple | None = None   # (events, channel, each event's height on its trace)
        self.marks = self.event_hl = self.thr_line = self.thr_label = None

    def _view_changed(self) -> None:
        if not (self.show_trials.isChecked() or self.show_average.isChecked()):
            self.show_average.blockSignals(True)          # something has to show
            self.show_average.setChecked(True)
            self.show_average.blockSignals(False)
        self.refresh()

    def plots(self) -> list:
        return self._plots

    def measure_channel(self, k: int) -> str:
        """Plot k's channel (stacked), or the first one ticked (overlaid)."""
        names = self.page.names()
        ticked = sorted(self.ticked(), key=lambda nc: (names.index(nc[0]) if nc[0] in names else 99, nc[1]))
        if not ticked:
            return super().measure_channel(k)
        return ticked[min(k, len(ticked) - 1)][0]

    def state(self) -> dict:
        return {**super().state(), "ticked": sorted(self.ticked()), "spikes_set": self._spikes_set}

    def restore(self, state: dict) -> None:
        super().restore(state)
        self._saved_ticks = [tuple(t) for t in state.get("ticked", [])] or None
        self._spikes_set = state.get("spikes_set", False)

    def names(self) -> list[str]:
        """The channels it lists: all but the stimulus, which has its own chart."""
        stim = self.page.stim_channel.currentData()
        return [n for n in self.page.names() if n != stim]

    def channels_changed(self) -> None:
        names = self.names()
        old = {nc for nc in set(self._saved_ticks or ()) or self.ticked() if nc[0] in names}
        self.series.blockSignals(True)
        self.series.setRowCount(len(names))
        self.series.setVerticalHeaderLabels(names)
        for r, name in enumerate(names):
            for c in range(len(SERIES)):
                item = QtWidgets.QTableWidgetItem()
                item.setFlags(QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled)
                on = (name, c) in old if old else c == 0                # every channel's V to start
                item.setCheckState(QtCore.Qt.CheckState.Checked if on else QtCore.Qt.CheckState.Unchecked)
                self.series.setItem(r, c, item)
        self.series.blockSignals(False)
        self._saved_ticks = None if names else self._saved_ticks

    def stimulus_changed(self) -> None:
        self.channels_changed()
        self.refresh()

    def ticked(self) -> set[tuple[str, int]]:
        out = set()
        for r in range(self.series.rowCount()):
            head = self.series.verticalHeaderItem(r)
            for c in range(self.series.columnCount()):
                item = self.series.item(r, c)
                if head is not None and item is not None and item.checkState() == QtCore.Qt.CheckState.Checked:
                    out.add((head.text(), c))
        return out

    def _clear_traces(self) -> None:
        for pw in self._plots:
            pw.setParent(None)
            pw.deleteLater()
        self._plots = []
        self._plot_units = []
        self._plot_keys = []
        self._faded = None
        self.marks = self.event_hl = self.thr_line = self.thr_label = None

    def _draw_series(self, p: pg.PlotItem, ys: dict[int, np.ndarray], fs: float) -> list:
        """One channel of the chosen blocks: a picked page's average (with its Spread), a
        picked block's trial, in their colors. Returns the curves drawn."""
        page = self.page
        averaged, trials = self.plan(list(ys))
        Y = np.full((len(ys), max(len(y) for y in ys.values())), np.nan)
        rows = {b: r for r, b in enumerate(ys)}
        for b, y in ys.items():
            Y[rows[b], :len(y)] = y
        by_color: dict[str, tuple[QtGui.QColor, list[int]]] = {}
        for b, c in page.trial_colors(trials).items():
            by_color.setdefault(c.name(QtGui.QColor.NameFormat.HexArgb), (c, []))[1].append(rows[b])
        drawn = []
        if self.show_trials.isChecked():                  # every trial of a group, faint behind its average
            for g, _, blocks in averaged:
                faint = QtGui.QColor(page.group_color(g))
                faint.setAlpha(70)
                item = OverlayCurve()
                item.mode = "peak"
                item.setPen(pg.mkPen(faint, width=1))
                p.addItem(item)
                item.set_traces(Y[[rows[b] for b in blocks]], fs)
                drawn.append(item)
        for color, picked in by_color.values():
            item = OverlayCurve()
            item.mode = "peak"
            item.setPen(pg.mkPen(color, width=1))
            p.addItem(item)
            item.set_traces(Y[picked], fs)
            drawn.append(item)
        x = np.arange(Y.shape[1]) / fs
        spread = page.spread()
        for g, _, blocks in averaged if self.show_average.isChecked() else []:
            mean, sd, sem, _ = A.mean_spread(Y[[rows[b] for b in blocks]])
            color = page.group_color(g)
            if spread:
                draw_band(p, x, mean, sd if spread == "sd" else sem, color)
            item = TraceCurve()
            item.mode = "peak"
            item.setPen(pg.mkPen(color, width=2))
            item.setZValue(6)
            p.addItem(item)
            item.setData(x, mean)
            drawn.append(item)
        return drawn

    def show_bars(self) -> None:
        """Scale bars, each where it was dragged before the plots were drawn again."""
        super().show_bars()
        for pw, key in zip(self._plots, self._plot_keys):
            bar = getattr(pw.getPlotItem(), "_scale_bar", None)
            if bar is not None and key in self._bar_gaps:
                bar.gap = self._bar_gaps[key]
                bar.refresh()

    def _zoom(self) -> tuple[dict, list | None]:
        """What you zoomed to, to keep through drawing again: the y range of each stacked plot
        not fitting itself (by the series it shows), and the time shown."""
        ys = {}
        for pw, key in zip(self._plots, self._plot_keys):
            vb = pw.getPlotItem().vb
            if not vb.autoRangeEnabled()[1]:
                ys[key] = vb.viewRange()[1]
        return ys, (self._plots[0].getPlotItem().vb.viewRange()[0] if self._plots else None)

    def draw(self) -> None:
        kept_y, kept_x = self._zoom()
        for pw, key in zip(self._plots, self._plot_keys):
            bar = getattr(pw.getPlotItem(), "_scale_bar", None)
            if bar is not None:
                self._bar_gaps[key] = bar.gap
        self._clear_traces()
        self._spike_channel = self.finder.channel.currentText()
        self._spikes_found()
        page = self.page
        chosen = self.blocks()
        if not chosen:
            self._note_spikes()
            return
        fs = self.data[chosen[0]]["sample_rate"]
        series = []                                   # (label, unit, block -> y, (channel, series))
        for name, c in sorted(self.ticked(), key=lambda nc: (page.names().index(nc[0])
                                                             if nc[0] in page.names() else 99, nc[1])):
            ys = {b: page.trace(b, name) for b in chosen}
            ys = {b: y for b, y in ys.items() if y is not None}
            if not ys:
                continue
            unit = page.unit_of(name)
            if c == 0:
                series.append((name, unit, {b: np.asarray(y, float) for b, y in ys.items()}, (name, c)))
            else:
                series.append((f"{name} {SERIES[c][0]}", f"{unit}/s" if c == 1 else f"{unit}/s²",
                               {b: A.derivative(y, fs, c, self.d_smooth.value()) for b, y in ys.items()}, (name, c)))
        if not series:
            self._note_spikes()
            return
        n = max(len(y) for s in series for y in s[2].values())
        th = theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT]
        series_colors = [colors.PALETTE[(k + 1) % len(colors.PALETTE)] for k in range(len(series))]
        overlaid = self.layout_mode.currentData() == "overlaid"
        spiked = (self._spike_channel, 0)
        if overlaid:
            pw = plot_widget()
            p = pw.getPlotItem()
            legend = p.addLegend(offset=(10, 10))
            multi = MultiAxis(p)                          # each on its own axis, fitted to the height
            spread = page.spread()
            for (label, unit, ys, key), color in zip(series, series_colors):
                Y = np.full((len(ys), n), np.nan)
                for r, y in enumerate(ys.values()):
                    Y[r, :len(y)] = y
                mean, sd, sem, _ = A.mean_spread(Y)
                curve = TraceCurve()
                curve.mode = "peak"
                curve.setPen(pg.mkPen(color, width=1.5))
                curve.setData(np.arange(n) / fs, mean)
                vb = multi.add(curve, label, unit, color)
                where = p if vb is p.vb else vb
                if spread:                                # across the chosen trials, on its own axis
                    draw_band(where, np.arange(n) / fs, mean, sd if spread == "sd" else sem, color)
                legend.addItem(curve, label)
                if key == spiked:
                    self._put_spikes(where, [curve])
            multi.fit()
            self._multi = multi                           # keep it (and its axes) alive
            plots = [pw]
        else:
            plots = []
            for label, unit, ys, key in series:
                pw = plot_widget()
                p = pw.getPlotItem()
                p.setTitle(label, size="9pt")             # the name above, the unit beside
                p.layout.setRowFixedHeight(0, 16)         # a small title, so a short row has room for its numbers
                p.titleLabel.setMaximumHeight(16)
                label_axis(p, "left", "", unit)
                drawn = self._draw_series(p, ys, fs)
                if key == spiked:
                    self._put_spikes(p, drawn)
                if plots:
                    p.setXLink(plots[0])
                plots.append(pw)
        for pw in plots:
            p = pw.getPlotItem()
            p.showGrid(x=True, y=True, alpha=0.2)
            p.vb.bounds[0] = (0.0, n / fs)
            style_plot(pw, th)
            pw.setMinimumHeight(40)
            self.body.addWidget(pw, 1)
        for pw in plots:                                  # plain seconds on every row, so they read the same
            pw.getPlotItem().getAxis("bottom").enableAutoSIPrefix(False)
        if overlaid:
            self._multi.recolor()                         # the theme reset the left axis's color
        plots[-1].getPlotItem().setLabel("bottom", "time in trial (s)")
        same = kept_x is not None and getattr(self, "_end", None) == n / fs     # else show all of it
        plots[0].getPlotItem().setXRange(*(kept_x if same else (0, n / fs)), padding=0)
        self._end = n / fs
        self._plot_keys = [] if overlaid else [s[3] for s in series]
        for pw, key in zip(plots, self._plot_keys):
            if key in kept_y:
                pw.getPlotItem().setYRange(*kept_y[key], padding=0)
        self._plots = plots
        self._axes.set_plots(plots)                       # the stacked traces line up
        self._plot_units = [s[1] for s in series] if len(plots) == len(series) else []
        self._show_spikes()
        self.threshold_changed()
        self.page.repeats.listen()
        self.page.canvas.relink(self)

    # -- the spikes, on the channel the Spikes panel sorts ------------------------------------------
    def _put_spikes(self, where, faded: list) -> None:
        """The threshold line, the dots and the picked units' spikes, on this plot (or view)."""
        self._faded = faded
        self.marks = pg.ScatterPlotItem(size=self.mark_size.value(), pen=None)
        self.marks.setZValue(20)
        # the picked units' spikes, drawn over the trace in their color
        self.event_hl = pg.PlotCurveItem(connect="finite")
        self.event_hl.setZValue(15)
        self.thr_line = pg.InfiniteLine(angle=0, movable=True)
        self.thr_line.setPen(pg.mkPen("#e0524f", width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.thr_line.setZValue(30)
        self.thr_line.setToolTip("Threshold: drag to set a fixed level")
        self.thr_line.hide()
        for item in (self.marks, self.event_hl, self.thr_line):
            where.addItem(item)
        self.thr_label = pg.InfLineLabel(self.thr_line, text="threshold", position=0.97, color="#e0524f",
                                         anchors=[(1, 1), (1, 1)])
        small = QtGui.QFont()
        small.setPixelSize(10)
        self.thr_label.setFont(small)
        self.thr_line.sigPositionChangeFinished.connect(self._thr_dragged)

    def _spikes_set_by_you(self) -> None:
        self._spikes_set = True

    def _spikes_found(self) -> None:
        """The first time spikes are found, show the threshold and the dots."""
        ev = self.page.events
        if self._spikes_set or ev is None or not len(ev.t):
            return
        self._spikes_set = True
        for b in (self.show_thr, self.show_dots):
            b.blockSignals(True)
            b.setChecked(True)
            b.blockSignals(False)
        self.saved()

    def _note_spikes(self) -> None:
        ch = self.finder.channel.currentText()
        if not ch:
            text = "Spikes are found on the channel picked in Spikes (left)."
        elif (ch, 0) not in self.ticked():
            text = f"On {ch}, the channel Spikes sorts: tick its V above to see them."
        else:
            text = f"On {ch}, the channel Spikes sorts."
        self.spikes_note.setText(text)

    def _trace(self, i: int) -> np.ndarray | None:
        return self.page.trace(i, self._spike_channel)

    def _event_heights(self, ev: A.Events) -> np.ndarray:
        """Where each event is on its block's trace (worked out once per search)."""
        name = self._spike_channel
        if self._heights is None or self._heights[0] is not ev or self._heights[1] != name:
            y = np.zeros(len(ev.t))
            for b in np.unique(ev.block):
                trace, m = self._trace(int(b)), ev.block == b
                if trace is not None:
                    k = np.round(ev.t[m] * self.data[int(b)]["sample_rate"]).astype(int)
                    y[m] = trace[np.clip(k, 0, len(trace) - 1)]
            self._heights = (ev, name, y)
        return self._heights[2]

    def _show_spikes(self) -> None:
        """A dot on each spike, and if only some units are shown, their spikes drawn over the
        recording in their color while the rest of it fades."""
        self._note_spikes()
        if self._faded is None:
            return
        ev = self.events()
        on = self.units()
        hl = on if ev is not None and len(on) < self.page.events.n_units else set()
        self.event_hl.setData(np.empty(0), np.empty(0))
        for item in self._faded:                          # the picked spikes stand out from them
            item.setOpacity(0.35 if hl else 1.0)
        if ev is None or not len(ev.t):
            self.marks.setData([], [])
            return
        show = np.isin(ev.unit, list(on))
        if self.show_dots.isChecked():
            self.marks.setData(ev.t[show], self._event_heights(ev)[show], brush=self.finder.brushes(ev.unit[show]),
                               size=self.mark_size.value(),
                               pen=pg.mkPen(self._fg(), width=1) if hl else pg.mkPen(None))
        else:
            self.marks.setData([], [])
        if hl:
            self._draw_spikes(ev, np.flatnonzero(show), hl)

    def _draw_spikes(self, ev: A.Events, which: np.ndarray, hl: set[int]) -> None:
        """Events `which`, drawn over their blocks' traces."""
        which = which[::max(1, -(-len(which) // MAX_MARKED))]
        xs, ys = [], []
        for b in np.unique(ev.block[which]):
            y, fs = self._trace(int(b)), self.data[int(b)]["sample_rate"]
            if y is None:
                continue
            k = np.round(ev.t[which[ev.block[which] == b]] * fs).astype(int)
            span = np.arange(-int(round(self.finder.pre.value() / 1000 * fs)),
                             int(round(self.finder.post.value() / 1000 * fs)) + 1)
            at = k[:, None] + span
            cut = np.where((at >= 0) & (at < len(y)), y[np.clip(at, 0, len(y) - 1)], np.nan)
            gap = np.full((len(k), 1), np.nan)
            xs.append(np.hstack([at / fs, gap]).ravel())
            ys.append(np.hstack([cut, gap]).ravel())
        if xs:
            color = self.finder.qcolor(next(iter(hl))) if len(hl) == 1 else self._fg()
            self.event_hl.setData(np.concatenate(xs), np.concatenate(ys), pen=pg.mkPen(color, width=2.5))

    def events_changed(self) -> None:
        self._fill_units()
        if self._spike_channel != self.finder.channel.currentText():
            self.refresh()                                # spikes on another channel now
            return
        self._spikes_found()
        self._show_spikes()
        self.threshold_changed()

    def units_changed(self) -> None:
        if self.own_units is None:
            self._fill_units()
        self._show_spikes()

    def threshold_changed(self) -> None:
        if self.thr_line is None:
            return
        finder = self.finder
        level = finder.level()
        if level is None or self._spike_channel != finder.channel.currentText():
            self.thr_line.hide()
            return
        sign = 1 if finder.polarity.currentData() == "positive" else -1
        self.thr_line.setVisible(self.show_thr.isChecked())       # first: a hidden label keeps its old text
        self.thr_line.blockSignals(True)
        self.thr_line.setValue(finder.baseline() + sign * level)
        self.thr_line.blockSignals(False)
        self.thr_label.setFormat(f"threshold {finder.fmt(level)}")
        self.thr_label.anchors = [(1, 1 if sign > 0 else 0)] * 2     # on the side away from the traces
        self.thr_label.updatePosition()

    def _thr_dragged(self) -> None:
        self.finder.threshold_dragged(self.thr_line.value())
        self.thr_label.setFormat(f"threshold {self.finder.fmt(abs(self.thr_line.value() - self.finder.baseline()))}")

    def listen_for_mark(self, on_drag) -> None:
        for pw in self._plots:
            pw.getPlotItem().vb.on_drag_select = on_drag

    def scale_bars(self) -> list[tuple]:
        """The stacked traces, each with its own unit and the last with the time too."""
        return [(pw, unit, "s" if k == len(self._plots) - 1 else None)
                for k, (pw, unit) in enumerate(zip(self._plots, self._plot_units))]

    def apply_theme(self, mode: str) -> None:
        super().apply_theme(mode)
        if getattr(self, "_multi", None) is not None and self.layout_mode.currentData() == "overlaid":
            self._multi.recolor()
        if self.dock is not None:
            self._show_spikes()                           # the picked spikes' outline is the ink color


class _AverageChart(Chart):
    help = "comparison"

    def __init__(self, page, cid: str, title: str, bottom: str):
        super().__init__(page, cid)
        self.plot = plot_widget()
        p = self.plot.getPlotItem()
        p.setTitle(title)
        p.setLabel("bottom", bottom)
        p.showGrid(x=True, y=True, alpha=0.2)
        p.addItem(pg.InfiniteLine(0, angle=90, pen=pg.mkPen("#888", style=QtCore.Qt.PenStyle.DashLine)))
        self.plot.setMinimumHeight(40)
        self.body.addWidget(self.plot)

    def plots(self) -> list:
        return [self.plot]

    def _times(self) -> dict[int, np.ndarray] | None:
        return _times_by_block(self.events(), self.units())

    def _clear(self) -> None:
        p = self.plot.getPlotItem()
        p.enableAutoRange()                           # a new window or bin can change the scale
        for item in list(p.listDataItems()):
            p.removeItem(item)
        for item in [x for x in p.items if isinstance(x, pg.FillBetweenItem)]:
            p.removeItem(item)


class PsthChart(_AverageChart):
    kind = "Stimulus-triggered rate"
    measure = ("event", "stimulus")
    y = "spikes/s"
    tip = "How does firing change after each stimulus?"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "Stimulus-triggered spike rate", "ms from the stimulus")
        self.trigger = QtWidgets.QComboBox()
        self.trigger.setToolTip("When each stimulus happened: from the pulses on a channel that records "
                                "the stimulus, or the start of every trial (for triggered sweeps)")
        self.trigger.addItem("Start of each trial", "")
        self.field("trigger", "Stimulus from", self.trigger)
        self.align = QtWidgets.QComboBox()
        self.align.addItem("Start of each train or burst", "train")
        self.align.addItem("Every pulse", "pulse")
        self.align.setToolTip("Start of each train or burst: one stimulus per train of pulses or burst of a "
                              "wave, at its first pulse or peak (or when the notes say it was sent). Every "
                              "pulse: each pulse of a train, or each cycle of a wave (at its peak). To see "
                              "firing through each cycle of a sine, Spikes per phase and Spikes by cycle show "
                              "it best")
        self.field("align", "Align to", self.align)
        self.before = UnitSpinBox(0, 10000, "ms", step=5)
        self.before.setToolTip("How long before each stimulus to show")
        self.before.setValue(50)
        self.field("before", "Before stimulus", self.before)
        self.after = UnitSpinBox(1, 10000, "ms", step=5)
        self.after.setToolTip("How long after each stimulus to show")
        self.after.setValue(200)
        self.field("after", "After stimulus", self.after)
        self.bin = UnitSpinBox(0.1, 1000, "ms", step=1)
        self.bin.setValue(5)
        self.bin.setToolTip("Bin width of the stimulus-triggered spike rate")
        self.field("bin", "Bin", self.bin)
        p = self.plot.getPlotItem()
        self.psth_legend = p.addLegend(offset=(-10, 10))           # one entry per stimulus group
        p.setLabel("left", "spikes/s")
        self.apply_theme(self._mode)

    def channels_changed(self) -> None:
        keep = self.trigger.currentData()
        names = self.page.names()
        self.trigger.blockSignals(True)
        self.trigger.clear()
        self.trigger.addItem("Start of each trial", "")
        for name in names:
            self.trigger.addItem(f"Pulses on {name}", name)
        i = self.trigger.findData(keep)
        if i < 0 or not keep:                         # the Stimulus channel, or one that looks like it
            stim = self.page.stim_channel.currentData()
            i = self.trigger.findData(stim) if stim else next(
                (k for k in range(self.trigger.count()) if "stim" in str(self.trigger.itemData(k)).lower()), 0)
        self.trigger.setCurrentIndex(max(0, i))
        self.trigger.blockSignals(False)
        self._apply_wanted()

    def stimulus_changed(self) -> None:
        ch = self.page.stim_channel.currentData()
        i = self.trigger.findData(ch) if ch else 0
        if i >= 0:
            self.trigger.setCurrentIndex(i)

    def draw(self) -> None:
        pp = self.plot.getPlotItem()
        page = self.page
        self._clear()
        times = self._times()
        chosen = self.blocks()
        if times is None or not chosen:
            pp.setTitle("Stimulus-triggered spike rate: find action potentials first", color=self._fg())
            return
        fs = self.data[chosen[0]]["sample_rate"]
        stim = self.trigger.currentData()
        self.align.setEnabled(bool(stim))
        every = self.align.currentData() == "pulse"
        triggers = {}
        for b in chosen:
            if not stim:
                triggers[b] = np.array([0.0])
            else:
                y = page.trace(b, stim)
                if y is not None:               # each train's start as page.onsets finds it, or every pulse
                    triggers[b] = A.stimulus_times(y, fs, one_way=True) if every else \
                        A.train_starts(A.stimulus_times(y, fs))
        width = self.bin.value()
        spread = page.spread()
        tag = {"sd": ", ± SD across stimuli", "sem": ", ± SEM across stimuli"}.get(spread, "")

        def psth(trig: dict, color: str, name: str | None, filled: bool) -> int:
            """One stimulus-triggered rate: the mean over stimuli, with its spread."""
            centers, rates = A.triggered_rates(times, trig, self.before.value(), self.after.value(), width)
            if not len(rates):
                return 0
            mean, sd, sem, _ = A.mean_spread(rates)
            edges = np.append(centers - width / 2, centers[-1] + width / 2)
            c = QtGui.QColor(color)
            if spread:
                s_ = np.nan_to_num(sd if spread == "sd" else sem)
                hi = pp.plot(edges, mean + s_, stepMode="center", pen=pg.mkPen(None))
                lo = pp.plot(edges, np.maximum(mean - s_, 0), stepMode="center", pen=pg.mkPen(None))
                c.setAlpha(60)
                pp.addItem(pg.FillBetweenItem(hi, lo, brush=pg.mkBrush(c)))
                c.setAlpha(255)
            fill = QtGui.QColor(color)
            fill.setAlpha(90)
            pp.plot(edges, mean, stepMode="center", pen=pg.mkPen(c, width=2 if name else 1.5), name=name,
                    **({"fillLevel": 0, "brush": pg.mkBrush(fill)} if filled and not spread else {}))
            return len(rates)

        # the same as the blocks: a picked page's average, a picked block's trial
        averaged, trials = self.plan([k for k in chosen if k in triggers])
        colors = page.trial_colors(trials)
        sets = [(label, page.group_color(g), blocks) for g, label, blocks in averaged]
        sets += [(None, colors[k], [k]) for k in trials]
        self.psth_legend.clear()
        n_trig = 0
        for label, color, blocks in sets:
            n = len([t for k in blocks for t in triggers[k]])
            n_trig += psth({k: triggers[k] for k in blocks}, color, f"{label} (n={n})" if label and len(sets) > 1
                           else None, len(sets) == 1)
        if len(sets) > 1:
            pp.setTitle(f"Stimulus-triggered spike rate by {'stimulus group' if averaged else 'trial'} "
                        f"({n_trig} stimuli{tag})", color=self._fg())
        else:
            pp.setTitle(f"Stimulus-triggered spike rate ({n_trig} stimuli{tag})" if n_trig else
                        "Stimulus-triggered spike rate: no stimuli found on that channel", color=self._fg())


class StaChart(_AverageChart):
    kind = "Spike-triggered average"
    measure = ("event", "spike")
    y = "signal"
    tip = "What does a channel do just before and after a spike?"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "Spike-triggered average", "ms from the spike")
        self.sta_channel = QtWidgets.QComboBox()
        self.sta_channel.setToolTip("The channel averaged around each spike")
        self.field("channel", "Average", self.sta_channel)
        self.sta_before = UnitSpinBox(0, 10000, "ms", step=1)
        self.sta_before.setToolTip("How long before each spike to average")
        self.sta_before.setValue(20)
        self.field("before", "Before spike", self.sta_before)
        self.sta_after = UnitSpinBox(0, 10000, "ms", step=1)
        self.sta_after.setToolTip("How long after each spike to average")
        self.sta_after.setValue(5)
        self.field("after", "After spike", self.sta_after)
        self.apply_theme(self._mode)

    def channels_changed(self) -> None:
        self.fill(self.sta_channel, self.page.names())

    def draw(self) -> None:
        sp = self.plot.getPlotItem()
        page = self.page
        self._clear()
        times = self._times()
        chosen = self.blocks()
        if times is None or not chosen:
            sp.setTitle("Spike-triggered average: find action potentials first", color=self._fg())
            return
        fs = self.data[chosen[0]]["sample_rate"]
        spread = page.spread()
        name = self.sta_channel.currentData()
        traces = {k: page.trace(k, name) for k in chosen} if name else {}
        traces = {k: v for k, v in traces.items() if v is not None}
        averaged, trials = self.plan(list(traces))
        colors = page.trial_colors(trials)
        total = 0
        for color, blocks in ([(page.group_color(g), blocks) for g, _, blocks in averaged]
                              + [(colors[k], [k]) for k in trials]):
            t, mean, sd, n = A.triggered_average({k: traces[k] for k in blocks}, fs,
                                                 {k: v for k, v in times.items() if k in blocks},
                                                 self.sta_before.value(), self.sta_after.value())
            if n:
                if spread:
                    draw_band(sp, t, mean, sd if spread == "sd" else sd / np.sqrt(n), color)
                sp.plot(t, mean, pen=pg.mkPen(color, width=2))
                total += n
        if total:
            label_axis(sp, "left", name, page.unit_of(name))
        tag = {"sd": ", ± SD", "sem": ", ± SEM"}.get(spread, "")
        sp.setTitle(f"Spike-triggered average of {name} ({total} spikes{tag})" if total else
                    "Spike-triggered average: no spikes far enough from the trial ends", color=self._fg())


class FitChart(Chart):
    kind = "Stimulus fit"
    y = "signal"
    tip = "How well does the Stim fit match the recording, and what's left over?"
    link = "time"
    uses_units = False

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.fit_plot, self.resid_plot = plot_widget(), plot_widget()
        self.fit_rec, self.fit_line, self.resid_line = TraceCurve(), TraceCurve(), TraceCurve()
        self.fit_plot.getPlotItem().addItem(self.fit_rec)
        self.fit_plot.getPlotItem().addItem(self.fit_line)
        self.resid_plot.getPlotItem().addItem(self.resid_line)
        self.resid_plot.getPlotItem().setXLink(self.fit_plot)
        for pw in (self.fit_plot, self.resid_plot):
            pw.getPlotItem().showGrid(x=True, y=True, alpha=0.2)
            pw.setMinimumHeight(40)
            self.body.addWidget(pw)
        for curve in (self.fit_rec, self.fit_line, self.resid_line):
            curve.mode = "peak"
        self.apply_theme(self._mode)

    def plots(self) -> list:
        return [self.fit_plot, self.resid_plot]

    def empty(self) -> bool:
        return not self.page._looks["stim_fit"]

    def draw(self) -> None:
        """The stimulus fit of the first chosen block over the recording, and what it left out."""
        page, stim = self.page, self.page.stim_channel.currentData()
        chosen = self.blocks()
        fit = page._looks["stim_fit"]
        if not chosen or not stim or not fit:
            self.fit_plot.getPlotItem().setTitle("Stimulus fit: pick the Stimulus channel and a Stim fit in Channels",
                                                 color=self._fg())
            for curve in (self.fit_rec, self.fit_line, self.resid_line):
                curve.setData([], [])
            return
        b = chosen[0]
        y, line = page.recorded(b, stim), page.trace(b, stim)
        if y is None:
            return
        t = np.arange(len(y)) / self.data[b]["sample_rate"]
        faint = QtGui.QColor(self._fg())
        faint.setAlpha(110)
        self.fit_rec.setPen(pg.mkPen(faint, width=1))
        self.fit_line.setPen(pg.mkPen("#e0524f", width=1.5))
        self.resid_line.setPen(pg.mkPen(self._fg(), width=1))
        self.fit_rec.setData(t, np.asarray(y, float))
        self.fit_line.setData(t, line)
        self.resid_line.setData(t, y - line)
        unit = page.unit_of(stim)
        rms = float(np.sqrt(np.nanmean((y - line) ** 2)))
        self.fit_plot.getPlotItem().setTitle(f"Stimulus fit: {fit}, trial {b + 1} (red, over the recording)",
                                             color=self._fg())
        self.resid_plot.getPlotItem().setTitle(f"What the fit left out (RMS {with_unit(rms, unit)})", color=self._fg())
        label_axis(self.fit_plot.getPlotItem(), "left", stim, unit)
        label_axis(self.resid_plot.getPlotItem(), "left", "", unit)
        for pw in (self.fit_plot, self.resid_plot):
            pw.getPlotItem().enableAutoRange()


class CycleChart(Chart):
    """A chart on the phase of the repeats found on the stimulus."""
    link = "phase"
    help = "cycles"
    title = ""
    phased = True                  # along the stimulus's phase

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.plot = plot_widget()
        p = self.plot.getPlotItem()
        p.setTitle(self.title)
        p.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setMinimumHeight(40)
        if self.phased:
            p.vb.bounds[0] = (0.0, 360.0)
            p.setXRange(0, 360, padding=0)
            p.getAxis("bottom").setTicks([[(d, f"{d}°") for d in range(0, 361, 90)]])
            p.setLabel("bottom", "stimulus phase")
        self.body.addWidget(self.plot)

    def plots(self) -> list:
        return [self.plot]

    def measure_channel(self, k: int) -> str:
        """The channel cut into cycles, if this chart has one, else the stimulus."""
        box = getattr(self, "cycle_resp", None)
        return box.currentText() if box is not None and box.currentText() else self.page.repeats.channel()

    def _add_cycle_settings(self) -> None:
        self.cycle_resp = QtWidgets.QComboBox()
        self.cycle_resp.setToolTip("The channel to cut into cycles")
        self.field("response", "Response", self.cycle_resp)
        self.cycle_each = QtWidgets.QCheckBox("Each cycle")
        self.cycle_each.setChecked(True)
        self.cycle_each.setToolTip("Draw every cycle behind the average")
        self.field("each", "", self.cycle_each)
        self.cycle_map = QtWidgets.QComboBox()
        self.cycle_map.addItems(colors.TRIAL_STYLES)
        self.cycle_map.setCurrentText("Viridis")
        self.cycle_map.setToolTip("How to color the cycles, from the first of a trial to the last: a color "
                                  "map shows how the response changes through the stimulus")
        self.field("colours", "Cycle colors", self.cycle_map)    # the key layouts were saved with

    def _fill_response(self) -> None:
        names = self.page.names()
        stim = next((k for k, n in enumerate(names) if "stim" in n.lower()), 0)
        picked = self.page.stim_channel.currentData()
        rest = [n for n in names if n != picked] or names
        default = rest[0] if picked else (names[1 if stim == 0 else 0] if len(names) > 1 else None)
        self.fill(self.cycle_resp, names, default=default)

    def cycle_sets(self) -> tuple[dict[int, np.ndarray], list[tuple[str, QtGui.QColor, list[int]]]]:
        """Each block's cycles, and what to draw of them: (block -> cycles, [(name, color, its
        blocks)]), the same pages and trials as the other charts."""
        page = self.page
        skip = page.repeats.cycle_skip.value()
        cycles = {}
        for b in self.blocks():
            found = page.repeats.found(b).bounds[skip:]
            if len(found):
                cycles[b] = found
        averaged, trials = self.plan(list(cycles))
        colors = page.trial_colors(trials)
        sets = [(name, QtGui.QColor(page.group_color(g)), blocks) for g, name, blocks in averaged]
        return cycles, sets + [(f"Trial {k + 1}", QtGui.QColor(colors[k].rgb()), [k]) for k in trials]

    def _shades(self, color: QtGui.QColor, number: np.ndarray) -> list:
        shades = colors.spread(self.cycle_map.currentText(), color, int(number.max()) + 1)
        for c in shades:
            c.setAlpha(min(c.alpha(), 150))
        return shades

    def _responses(self, cycles: dict, blocks: list[int]):
        """The stimulus and response cut into these blocks' cycles: (S, R, cycle numbers of
        R, which rows of S have R). R is None if no block has the response."""
        page = self.page
        stim, resp = page.repeats.channel(), self.cycle_resp.currentText()
        S = np.vstack([A.cycle_rows(page.trace(b, stim), cycles[b]) for b in blocks])
        number = np.concatenate([np.arange(len(cycles[b])) for b in blocks])
        traces = {b: page.trace(b, resp) for b in blocks}
        have = [b for b in blocks if traces[b] is not None]
        if not have:
            return S, None, number, None
        R = np.vstack([A.cycle_rows(traces[b], cycles[b]) for b in have])
        rows = np.concatenate([np.full(len(cycles[b]), b in have) for b in blocks])
        return S, R, number[rows], rows

    @staticmethod
    def _draw_cycles(p: pg.PlotItem, X: np.ndarray, Y: np.ndarray, number: np.ndarray, shades: list) -> None:
        """Rows of X against Y, row r in shades[number[r]]. Rows of one shade are one item."""
        same: dict[int, list[int]] = {}
        for r, k in enumerate(number):
            same.setdefault(shades[k].rgba(), []).append(r)
        for rgba, rows in same.items():
            gap = np.full((len(rows), 1), np.nan)
            p.addItem(pg.PlotCurveItem(np.hstack((X[rows], gap)).ravel(), np.hstack((Y[rows], gap)).ravel(),
                                       connect="finite", pen=pg.mkPen(QtGui.QColor.fromRgba(rgba), width=1)))

    def draw(self) -> None:
        p = self.plot.getPlotItem()
        p.clear()
        p.setTitle(self.title, color=self._fg())
        self.start()
        cycles, sets = self.cycle_sets()
        if cycles:
            self.draw_cycles(p, cycles, sets)
        else:
            self.no_cycles(p)
        self.page.repeats.show_found(list(cycles))

    def start(self) -> None:
        """Before drawing."""

    def no_cycles(self, p: pg.PlotItem) -> None:
        stim = self.page.repeats.channel()
        p.setTitle(f"{self.title}: no repeats on {stim}. For a one-off stimulus, use the stimulus-triggered rate" if stim
                   else f"{self.title}: pick the Stimulus channel in Channels", color=self._fg())

    def draw_cycles(self, p: pg.PlotItem, cycles: dict, sets: list) -> None:
        raise NotImplementedError


class StimulusCycleChart(CycleChart):
    kind = "Stimulus cycle"
    y = "signal"
    tip = "What does one cycle of the stimulus look like?"
    title = "Stimulus"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.plot.setMaximumHeight(16777215)
        self.apply_theme(self._mode)

    def draw_cycles(self, p: pg.PlotItem, cycles: dict, sets: list) -> None:
        page = self.page
        stim = page.repeats.channel()
        x = np.arange(A.CYCLE_POINTS) * 360 / A.CYCLE_POINTS
        total = 0
        spread = page.spread()
        for _, color, blocks in sets:
            S = np.vstack([A.cycle_rows(page.trace(b, stim), cycles[b]) for b in blocks])
            mean, sd, sem, _ = A.mean_spread(S)              # across the cycles
            if spread:
                draw_band(p, x, mean, sd if spread == "sd" else sem, color)
            p.plot(x, mean, pen=pg.mkPen(color, width=1.5))
            total += len(S)
        lengths = np.concatenate([np.diff(c, axis=1).ravel() for c in cycles.values()])
        hz = self.data[next(iter(cycles))]["sample_rate"] / np.median(lengths)
        found = [page.repeats.found(b) for b in cycles]
        how = {"known": "from what was sent", "shape": "by shape", "crossing": "by rises",
               "hand": "from your repeat"}.get(found[0].method, "")
        left = sum(r.left_out for r in found)
        p.setTitle(f"Stimulus ({total} cycles {how}, about {hz:.3g} Hz" + (f", {left} left out)" if left else ")"),
                   color=self._fg())
        label_axis(p, "left", stim, page.unit_of(stim))
        p.enableAutoRange(axis="y")


class ResponseCycleChart(CycleChart):
    kind = "Response by cycle"
    y = "signal"
    tip = "Is the response the same in every cycle?"
    title = "Response, each cycle"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self._add_cycle_settings()
        self.cycle_legend = self.plot.getPlotItem().addLegend(offset=(-10, 10))
        self.apply_theme(self._mode)

    def channels_changed(self) -> None:
        self._fill_response()

    def start(self) -> None:
        self.cycle_legend.clear()

    def draw_cycles(self, p: pg.PlotItem, cycles: dict, sets: list) -> None:
        resp = self.cycle_resp.currentText()
        x = np.arange(A.CYCLE_POINTS) * 360 / A.CYCLE_POINTS
        spread = self.page.spread()
        for name, color, blocks in sets:
            _, R, number, _ = self._responses(cycles, blocks)
            if R is None:
                continue
            mean, sd, sem, _ = A.mean_spread(R)
            if self.cycle_each.isChecked():
                self._draw_cycles(p, np.broadcast_to(x, R.shape), R, number, self._shades(color, number))
            if spread:
                draw_band(p, x, mean, sd if spread == "sd" else sem, color)
            p.plot(x, mean, pen=pg.mkPen(color, width=2.4), name=f"{name} (n={len(R)})" if len(sets) > 1 else None)
        label_axis(p, "left", resp, self.page.unit_of(resp))
        p.enableAutoRange(axis="y")


class LoopChart(CycleChart):
    kind = "Response against stimulus"
    measure = ("range", "loop")
    y = "response"
    tip = "Does the response follow the stimulus, or lag it (a loop)?"
    title = "Response against stimulus"
    link = ""
    phased = False

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self._add_cycle_settings()
        self.apply_theme(self._mode)

    def channels_changed(self) -> None:
        self._fill_response()

    def no_cycles(self, p: pg.PlotItem) -> None:
        pass

    def draw_cycles(self, p: pg.PlotItem, cycles: dict, sets: list) -> None:
        page = self.page
        stim, resp = page.repeats.channel(), self.cycle_resp.currentText()
        for _, color, blocks in sets:
            S, R, number, rows = self._responses(cycles, blocks)
            if R is None:
                continue
            if self.cycle_each.isChecked():
                self._draw_cycles(p, np.hstack((S[rows], S[rows][:, :1])), np.hstack((R, R[:, :1])), number,
                                  self._shades(color, number))
            mean = A.mean_spread(R)[0]
            loop_x = S[rows].mean(0)
            p.plot(np.r_[loop_x, loop_x[0]], np.r_[mean, mean[0]], pen=pg.mkPen(color, width=2.4))
        label_axis(p, "left", resp, page.unit_of(resp))
        label_axis(p, "bottom", stim, page.unit_of(stim))
        p.enableAutoRange()


class SpikeCycleChart(CycleChart):
    kind = "Spikes by cycle"
    y = "cycle"
    tip = "When in each cycle did the spikes happen?"
    title = "Spikes by cycle"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.point_size = dot_size(self.main.settings, "raster_point_size", 2, 12, "The size of the dots")
        self.field("dots", "Dot size", self.point_size)
        p = self.plot.getPlotItem()
        p.invertY(True)                                  # the first cycle at the top
        p.setLabel("left", "cycle")
        self.apply_theme(self._mode)

    def draw_cycles(self, p: pg.PlotItem, cycles: dict, sets: list) -> None:
        ev = self.events()
        if ev is None:
            p.setTitle(f"{self.title}: find action potentials first", color=self._fg())
            return
        row = 0
        size = self.point_size.value()
        for _, _, blocks in sets:
            for b in blocks:
                for u in sorted(self.units()):           # in the units' colors
                    k, deg = A.spike_phases(ev.t[(ev.block == b) & (ev.unit == u)], cycles[b],
                                            self.data[b]["sample_rate"])
                    p.addItem(pg.ScatterPlotItem(deg, row + k + 1, size=size, pen=None,
                                                 brush=pg.mkBrush(self.finder.qcolor(u))))
                row += len(cycles[b])
        p.vb.bounds[1] = (0.5, row + 0.5) if row else None
        if row:
            p.setYRange(0.5, row + 0.5, padding=0)


class PhaseChart(CycleChart):
    kind = "Spikes per phase"
    y = "spikes/cycle"
    tip = "Which part of the cycle does it fire in most?"
    title = "Spikes per phase"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self.phase_bin = QtWidgets.QSpinBox(minimum=1, maximum=90, suffix="°")
        self.phase_bin.setValue(10)
        self.phase_bin.setToolTip("Bin width of Spikes per phase")
        self.field("bin", "Phase bin", self.phase_bin)
        p = self.plot.getPlotItem()
        p.setLabel("left", "spikes/cycle")
        self.phase_legend = p.addLegend(offset=(-10, 10))
        self.apply_theme(self._mode)

    def start(self) -> None:
        self.phase_legend.clear()

    def draw_cycles(self, p: pg.PlotItem, cycles: dict, sets: list) -> None:
        ev = self.events()
        if ev is None:
            p.setTitle(f"{self.title}: find action potentials first", color=self._fg())
            return
        edges = np.linspace(0, 360, round(360 / self.phase_bin.value()) + 1)
        spikes = 0
        locks = []
        spread = self.page.spread()
        for name, color, blocks in sets:
            phases, rows = [], []                        # rows: each cycle's spikes in each bin
            for b in blocks:
                counts = np.zeros((len(cycles[b]), len(edges) - 1))
                for u in sorted(self.units()):
                    k, deg = A.spike_phases(ev.t[(ev.block == b) & (ev.unit == u)], cycles[b],
                                            self.data[b]["sample_rate"])
                    np.add.at(counts, (k, np.clip(np.searchsorted(edges, deg, side="right") - 1, 0,
                                                  len(edges) - 2)), 1)
                    phases.append(deg)
                rows.append(counts)
            phases = np.concatenate(phases) if phases else np.empty(0)
            spikes += len(phases)
            strength, at = A.vector_strength(phases)
            locks.append((strength, at))
            fill = QtGui.QColor(color)
            fill.setAlpha(90)
            # spikes per cycle, across the cycles
            mean, sd, sem, _ = A.mean_spread(np.vstack(rows) if rows else np.zeros((0, len(edges) - 1)))
            if spread:
                draw_step_band(p, edges, mean, sd if spread == "sd" else sem, color)
            p.plot(edges, mean, stepMode="center", pen=pg.mkPen(color, width=1.5),
                   name=f"{name}: {strength:.2f} at {at:.0f}°" if len(sets) > 1 else None,
                   **({"fillLevel": 0, "brush": pg.mkBrush(fill)} if len(sets) == 1 else {}))
        if spikes and len(sets) == 1:
            p.setTitle(f"Spikes per phase ({spikes} spikes, vector strength {locks[0][0]:.2f} at "
                       f"{locks[0][1]:.0f}°)", color=self._fg())
        elif spikes:
            p.setTitle(f"Spikes per phase ({spikes} spikes; vector strength by group)", color=self._fg())
        p.enableAutoRange(axis="y")

