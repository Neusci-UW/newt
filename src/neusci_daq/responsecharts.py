"""The charts of how the channels respond: traces and their derivatives, triggered averages,
what the stimulus fit did, and the charts on the phase of a repeating stimulus.
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
from .spikecharts import draw_band
from .unitbox import UnitSpinBox
from .viewkit import label_axis, style_plot, with_unit

SERIES = (("V", 0), ("dV/dt", 1), ("d²V/dt²", 2))


def _times_by_block(ev: A.Events | None, units: set[int]) -> dict[int, np.ndarray] | None:
    """(block -> spike times s) of these units, or None without events."""
    if ev is None:
        return None
    m = np.isin(ev.unit, list(units))
    return {int(b): ev.t[m & (ev.block == b)] for b in np.unique(ev.block[m])}


class TracesChart(Chart):
    kind = "Traces"
    tip = "What did each channel record through the trial?"
    link = "time"
    help = "comparison"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        self._plots: list[pg.PlotWidget] = []
        self._plot_units: list[str] = []
        self.series = QtWidgets.QTableWidget(0, len(SERIES))
        self.series.setHorizontalHeaderLabels([s for s, _ in SERIES])
        self.series.setToolTip("Tick what to show for each channel, on one time axis")
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
        self.series.itemChanged.connect(lambda *_: self._setting_changed(None))
        self._axes = AxisGroup()
        self._saved_ticks: list | None = None        # from the layout, until the channels are known

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
        return {**super().state(), "ticked": sorted(self.ticked())}

    def restore(self, state: dict) -> None:
        super().restore(state)
        self._saved_ticks = [tuple(t) for t in state.get("ticked", [])] or None

    def channels_changed(self) -> None:
        old = set(self._saved_ticks or ()) or self.ticked()
        names = self.page.names()
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

    def _draw_series(self, p: pg.PlotItem, ys: dict[int, np.ndarray], fs: float) -> None:
        """One channel of the chosen blocks: a picked page's average (with its Spread), a
        picked block's trial, in their colors."""
        page = self.page
        averaged, trials = self.plan(list(ys))
        Y = np.full((len(ys), max(len(y) for y in ys.values())), np.nan)
        rows = {b: r for r, b in enumerate(ys)}
        for b, y in ys.items():
            Y[rows[b], :len(y)] = y
        by_color: dict[str, tuple[QtGui.QColor, list[int]]] = {}
        for b, c in page.trial_colors(trials).items():
            by_color.setdefault(c.name(QtGui.QColor.NameFormat.HexArgb), (c, []))[1].append(rows[b])
        for color, picked in by_color.values():
            item = OverlayCurve()
            item.mode = "peak"
            item.setPen(pg.mkPen(color, width=1))
            p.addItem(item)
            item.set_traces(Y[picked], fs)
        x = np.arange(Y.shape[1]) / fs
        spread = page.spread()
        for g, _, blocks in averaged:
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

    def draw(self) -> None:
        self._clear_traces()
        page = self.page
        chosen = self.blocks()
        if not chosen:
            return
        fs = self.data[chosen[0]]["sample_rate"]
        series = []                                   # (label, unit, block -> y)
        for name, c in sorted(self.ticked(), key=lambda nc: (page.names().index(nc[0])
                                                             if nc[0] in page.names() else 99, nc[1])):
            ys = {b: page.trace(b, name) for b in chosen}
            ys = {b: y for b, y in ys.items() if y is not None}
            if not ys:
                continue
            unit = page.unit_of(name)
            if c == 0:
                series.append((name, unit, {b: np.asarray(y, float) for b, y in ys.items()}))
            else:
                series.append((f"{name} {SERIES[c][0]}", f"{unit}/s" if c == 1 else f"{unit}/s²",
                               {b: A.derivative(y, fs, c, self.d_smooth.value()) for b, y in ys.items()}))
        if not series:
            return
        n = max(len(y) for s in series for y in s[2].values())
        th = theme.THEMES[theme.DARK if self._mode == style.DARK else theme.LIGHT]
        series_colors = [colors.PALETTE[(k + 1) % len(colors.PALETTE)] for k in range(len(series))]
        overlaid = self.layout_mode.currentData() == "overlaid"
        if overlaid:
            pw = plot_widget()
            p = pw.getPlotItem()
            legend = p.addLegend(offset=(10, 10))
            multi = MultiAxis(p)                          # each on its own axis, fitted to the height
            for (label, unit, ys), color in zip(series, series_colors):
                Y = np.full((len(ys), n), np.nan)
                for r, y in enumerate(ys.values()):
                    Y[r, :len(y)] = y
                curve = TraceCurve()
                curve.mode = "peak"
                curve.setPen(pg.mkPen(color, width=1.5))
                curve.setData(np.arange(n) / fs, A.mean_spread(Y)[0])
                multi.add(curve, label, unit, color)
                legend.addItem(curve, label)
            multi.fit()
            self._multi = multi                           # keep it (and its axes) alive
            plots = [pw]
        else:
            plots = []
            for label, unit, ys in series:
                pw = plot_widget()
                p = pw.getPlotItem()
                p.setTitle(label, size="9pt")             # the name above, the unit beside
                label_axis(p, "left", "", unit)
                self._draw_series(p, ys, fs)
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
        plots[0].getPlotItem().setXRange(0, n / fs, padding=0)
        self._plots = plots
        self._axes.set_plots(plots)                       # the stacked traces line up
        self._plot_units = [s[1] for s in series] if len(plots) == len(series) else []
        self.page.repeats.listen()
        self.page.canvas.relink(self)

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
    tip = "How does firing change after each stimulus?"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "Stimulus-triggered spike rate", "ms from the stimulus")
        self.trigger = QtWidgets.QComboBox()
        self.trigger.setToolTip("When each stimulus happened: from the pulses on a channel that records "
                                "the stimulus, or the start of every trial (for triggered sweeps)")
        self.trigger.addItem("Start of each trial", "")
        self.field("trigger", "Stimulus from", self.trigger)
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
        triggers = {}
        for b in chosen:
            if not stim:
                triggers[b] = np.array([0.0])
            else:
                y = page.trace(b, stim)
                if y is not None:
                    triggers[b] = A.stimulus_times(y, fs)
        width = self.bin.value()
        spread = page.spread()
        tag = {"sd": ", ± SD across stimuli", "sem": ", ± SEM across stimuli"}.get(spread, "")

        def psth(trig: dict, color: str, name: str | None, filled: bool) -> int:
            """One stimulus-triggered rate: the mean over stimuli, with its spread."""
            centeres, rates = A.triggered_rates(times, trig, self.before.value(), self.after.value(), width)
            if not len(rates):
                return 0
            mean, sd, sem, _ = A.mean_spread(rates)
            edges = np.append(centeres - width / 2, centeres[-1] + width / 2)
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
            pp.setTitle(f"Stimulus-triggered spike rate by {'stimulus group' if averaged else 'block'} "
                        f"({n_trig} stimuli{tag})", color=self._fg())
        else:
            pp.setTitle(f"Stimulus-triggered spike rate ({n_trig} stimuli{tag})" if n_trig else
                        "Stimulus-triggered spike rate: no stimuli found on that channel", color=self._fg())


class StaChart(_AverageChart):
    kind = "Spike-triggered average"
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
                    s_ = sd if spread == "sd" else sd / np.sqrt(n)
                    hi = sp.plot(t, mean + s_, pen=pg.mkPen(None))
                    lo = sp.plot(t, mean - s_, pen=pg.mkPen(None))
                    fill = QtGui.QColor(color)
                    fill.setAlpha(50)
                    sp.addItem(pg.FillBetweenItem(hi, lo, brush=pg.mkBrush(fill)))
                sp.plot(t, mean, pen=pg.mkPen(color, width=2))
                total += n
        if total:
            label_axis(sp, "left", name, page.unit_of(name))
        tag = {"sd": ", ± SD", "sem": ", ± SEM"}.get(spread, "")
        sp.setTitle(f"Spike-triggered average of {name} ({total} spikes{tag})" if total else
                    "Spike-triggered average: no spikes far enough from the trial ends", color=self._fg())


class FitChart(Chart):
    kind = "Stimulus fit"
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
        for _, color, blocks in sets:
            S = np.vstack([A.cycle_rows(page.trace(b, stim), cycles[b]) for b in blocks])
            p.plot(x, S.mean(0), pen=pg.mkPen(color, width=1.5))
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
                s_ = np.nan_to_num(sd if spread == "sd" else sem)
                hi, lo = p.plot(x, mean + s_, pen=pg.mkPen(None)), p.plot(x, mean - s_, pen=pg.mkPen(None))
                fill = QtGui.QColor(color)
                fill.setAlpha(50)
                p.addItem(pg.FillBetweenItem(hi, lo, brush=pg.mkBrush(fill)))
            p.plot(x, mean, pen=pg.mkPen(color, width=2.4), name=f"{name} (n={len(R)})" if len(sets) > 1 else None)
        label_axis(p, "left", resp, self.page.unit_of(resp))
        p.enableAutoRange(axis="y")


class LoopChart(CycleChart):
    kind = "Response against stimulus"
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
        for name, color, blocks in sets:
            phases = []
            for b in blocks:
                for u in sorted(self.units()):
                    phases.append(A.spike_phases(ev.t[(ev.block == b) & (ev.unit == u)], cycles[b],
                                                 self.data[b]["sample_rate"])[1])
            phases = np.concatenate(phases) if phases else np.empty(0)
            spikes += len(phases)
            strength, at = A.vector_strength(phases)
            locks.append((strength, at))
            fill = QtGui.QColor(color)
            fill.setAlpha(90)
            count = sum(len(cycles[b]) for b in blocks)
            p.plot(edges, np.histogram(phases, edges)[0] / count, stepMode="center", pen=pg.mkPen(color, width=1.5),
                   name=f"{name}: {strength:.2f} at {at:.0f}°" if len(sets) > 1 else None,
                   **({"fillLevel": 0, "brush": pg.mkBrush(fill)} if len(sets) == 1 else {}))
        if spikes and len(sets) == 1:
            p.setTitle(f"Spikes per phase ({spikes} spikes, vector strength {locks[0][0]:.2f} at "
                       f"{locks[0][1]:.0f}°)", color=self._fg())
        elif spikes:
            p.setTitle(f"Spikes per phase ({spikes} spikes; vector strength by group)", color=self._fg())
        p.enableAutoRange(axis="y")

