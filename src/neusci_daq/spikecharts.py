"""The charts of the action potentials found: the stimulus and the blocks with their
spikes, a raster, the spike rate, and each unit's PCA map, waveforms, sizes and intervals.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import analysis as A
from .chartbase import Chart, dot_size
from .plotzoom import OverlayCurve, TraceCurve, plot_widget
from .unitbox import UnitSpinBox
from .viewkit import label_axis
from . import colors

MAX_WAVES = 150                    # per unit in the waveform plot
MAX_MARKED = 2000                  # spikes drawn over the blocks when a unit is highlighted


def draw_band(p: pg.PlotItem, x: np.ndarray, mean: np.ndarray, spread: np.ndarray, color: str) -> list:
    """A shaded band from mean − spread to mean + spread. Returns its items."""
    edges = []
    for sign in (1, -1):
        c = TraceCurve()
        c.setPen(pg.mkPen(None))
        c.setZValue(4)
        p.addItem(c)
        c.setData(x, mean + sign * np.nan_to_num(spread))
        edges.append(c)
    fill = QtGui.QColor(color)
    fill.setAlpha(55)
    band = pg.FillBetweenItem(edges[0], edges[1], brush=pg.mkBrush(fill))
    band.setZValue(4)
    p.addItem(band)
    return edges + [band]


class SpikeChart(Chart):
    counts_spikes = True
    help = "spike_sorting"

    def __init__(self, page, cid: str = "", title: str = ""):
        super().__init__(page, cid)
        self.plot = plot_widget()
        p = self.plot.getPlotItem()
        p.setTitle(title)
        p.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setMinimumHeight(40)
        self.body.addWidget(self.plot)

    def plots(self) -> list:
        return [self.plot]

    def _span(self, chosen: list[int]) -> float:
        """Where the longest of these blocks ends (s)."""
        if not chosen:
            return 0.0
        return max(self.data[i]["data"].shape[1] for i in chosen) / self.data[chosen[0]]["sample_rate"]

    def _clear(self) -> None:
        p = self.plot.getPlotItem()
        for item in list(p.listDataItems()):
            p.removeItem(item)


class StimulusChart(SpikeChart):
    kind = "Stimulus"
    tip = "What was the stimulus in each trial?"
    link = "time"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid)
        sp = self.plot.getPlotItem()
        sp.setLabel("bottom", "time in trial", units="s")
        self.stim_items: list[OverlayCurve] = []  # in the colors of the responses
        self.stim_onsets = pg.ScatterPlotItem(size=10, symbol="t1", pen=None, brush=pg.mkBrush("#E69F00"))
        sp.addItem(self.stim_onsets)

    def empty(self) -> bool:
        return not self.page.how() or not self.blocks()

    def listen_for_mark(self, on_drag) -> None:
        self.plot.getPlotItem().vb.on_drag_select = on_drag

    def scale_bars(self) -> list[tuple]:
        how = self.page.how()
        return [(self.plot, self.page.unit_of(how) if how and how != "notes" else None, None)]

    def draw(self) -> None:
        """The chosen blocks' stimulus channel (or the notes' times)."""
        how, chosen = self.page.how(), self.blocks()
        sp = self.plot.getPlotItem()
        for item in self.stim_items:
            sp.removeItem(item)
        self.stim_items = []
        self.stim_onsets.setData([], [])
        self.time_range(self._span(chosen))
        sp.setTitle("" if how and chosen else "Stimulus: pick the Stimulus channel in Channels" if not how else
                    "Stimulus: no trials chosen", color=self._fg())
        if not how or not chosen:
            return
        if how == "notes":                        # no trace to show: just when each one was noted
            sp.setLabel("left", "Stimulus (notes)")
            ons = [on for on in map(self.page.onsets, chosen) if on is not None and len(on)]
            if ons:
                x = np.concatenate(ons)
                self.stim_onsets.setData(x, np.zeros(len(x)))
            return
        traces = {i: self.page.trace(i, how) for i in chosen}
        traces = {i: y for i, y in traces.items() if y is not None}
        if not traces:
            return
        fs = self.data[chosen[0]]["sample_rate"]
        Y = np.full((len(traces), max(len(y) for y in traces.values())), np.nan)
        for r, y in enumerate(traces.values()):
            Y[r, :len(y)] = y
        rows = {i: r for r, i in enumerate(traces)}
        averaged, trials = self.plan(list(traces))
        sets = [(QtGui.QColor(self.page.group_color(g)), blocks) for g, _, blocks in averaged]
        sets += [(c, [i]) for i, c in self.page.trial_colors(trials).items()]
        for color, blocks in sets:
            item = OverlayCurve()
            item.mode = "peak"
            item.setPen(pg.mkPen(color, width=1.2))
            sp.addItem(item)
            item.set_traces(Y[[rows[i] for i in blocks]], fs)
            self.stim_items.append(item)
        label_axis(sp, "left", how, self.page.unit_of(how))


class BlocksChart(SpikeChart):
    kind = "Trials"
    tip = "Where are the spikes in each trial, and where's the threshold?"
    link = "time"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "Trials overlaid")
        settings = self.main.settings
        self.channel = QtWidgets.QComboBox()
        self.channel.setToolTip("The channel drawn: the one spikes are found on, or another")
        self.channel.addItem("The analyzed channel", "")
        self.field("channel", "Channel", self.channel)
        self.mark_size = dot_size(settings, "mark_point_size", 9, 20, "The size of the dots marking each spike")
        self.field("dots", "Dot size", self.mark_size, self.units_changed)
        self.show_thr = QtWidgets.QCheckBox("Show the threshold")
        self.show_thr.setChecked(settings.value("show_threshold", True, type=bool))
        self.show_thr.setToolTip("Show the threshold line. Hiding it doesn't change the search")
        self.show_thr.toggled.connect(lambda on: settings.setValue("show_threshold", on))
        self.field("threshold", "", self.show_thr, self.threshold_changed)
        p = self.plot.getPlotItem()
        p.setLabel("bottom", "time in trial", units="s")
        self.trial_items: list[OverlayCurve] = []    # the trials shown, one item per color
        self.marks = pg.ScatterPlotItem(size=self.mark_size.value(), pen=None)
        self.marks.setZValue(20)
        p.addItem(self.marks)
        # the picked units' spikes, drawn over the trace in their color
        self.event_hl = pg.PlotCurveItem(connect="finite")
        self.event_hl.setZValue(15)
        p.addItem(self.event_hl)
        self._heights: tuple | None = None           # (events, channel, each event's height on its trace)
        self.thr_line = pg.InfiniteLine(angle=0, movable=True)
        self.thr_line.setPen(pg.mkPen("#e0524f", width=1.5, style=QtCore.Qt.PenStyle.DashLine))
        self.thr_line.setZValue(30)
        self.thr_line.setToolTip("Threshold: drag to set a fixed level")
        p.addItem(self.thr_line)
        self.thr_label = pg.InfLineLabel(self.thr_line, text="threshold", position=0.97, color="#e0524f",
                                         anchors=[(1, 1), (1, 1)])
        small = QtGui.QFont()
        small.setPixelSize(10)
        self.thr_label.setFont(small)
        self.thr_line.sigPositionChangeFinished.connect(self._thr_dragged)
        self.group_avgs: list[TraceCurve] = []
        self.bands: list = []                        # ± SD / SEM shading (Spread, in the Blocks panel)
        self.overlay_legend = p.addLegend(offset=(-10, 30))
        self.apply_theme(self._mode)

    def channels_changed(self) -> None:
        self.fill(self.channel, self.page.names(), ("The analyzed channel", ""))

    def channel_name(self) -> str:
        return self.channel.currentData() or self.finder.channel.currentText()

    def _trace(self, i: int) -> np.ndarray | None:
        return self.page.trace(i, self.channel_name())

    def scale_bars(self) -> list[tuple]:
        return [(self.plot, self.page.unit_of(self.channel_name()), "s")]

    def _draw_trials(self, Y: np.ndarray, chosen: list[int], trials: list[int], fs: float) -> None:
        """The trials, one item for each color, so hundreds of them stay quick."""
        rows = {i: r for r, i in enumerate(chosen)}
        by_color: dict[str, tuple[QtGui.QColor, list[int]]] = {}
        for i, c in self.page.trial_colors(trials).items():
            by_color.setdefault(c.name(QtGui.QColor.NameFormat.HexArgb), (c, []))[1].append(rows[i])
        p = self.plot.getPlotItem()
        for color, picked in by_color.values():
            # Spikes are the point here: always draw min and max, whatever View says,
            # so they keep their height (and line up with the event marks).
            item = OverlayCurve()
            item.mode = "peak"
            item.setPen(pg.mkPen(color, width=1))
            p.addItem(item)
            item.set_traces(Y[picked], fs)
            self.trial_items.append(item)

    def draw(self) -> None:
        p = self.plot.getPlotItem()
        for item in self.group_avgs + self.bands + self.trial_items:
            p.removeItem(item)
        self.group_avgs, self.bands, self.trial_items = [], [], []
        self.overlay_legend.clear()
        chosen = [i for i in self.blocks() if self._trace(i) is not None]
        name = self.channel_name()
        if chosen:
            fs = self.data[chosen[0]]["sample_rate"]
            n = max(len(self._trace(i)) for i in chosen)
            Y = np.full((len(chosen), n), np.nan)
            for r, i in enumerate(chosen):
                y = self._trace(i)
                Y[r, :len(y)] = y
            averaged, trials = self.plan(chosen)
            self._draw_trials(Y, chosen, trials, fs)
            spread = self.page.spread()
            for g, label, mean, sd, sem, rows in self.page.averages(chosen, Y, averaged):
                x = np.arange(len(mean)) / fs
                color = self.page.group_color(g)
                item = TraceCurve(name=f"{label} (n={rows})")
                item.setPen(pg.mkPen(color, width=2.5))
                item.setZValue(6)
                p.addItem(item)
                self.group_avgs.append(item)
                item.setData(x, mean)
                if spread:
                    self.bands += draw_band(p, x, mean, sd if spread == "sd" else sem, color)
            label_axis(p, "left", name, self.page.unit_of(name))
        self.time_range(self._span(chosen))
        p.setTitle(f"{len(chosen)} trial(s)", color=self._fg())
        self.units_changed()
        self.threshold_changed()

    def events_changed(self) -> None:
        self._fill_units()
        self.units_changed()
        self.threshold_changed()

    def _event_heights(self, ev: A.Events) -> np.ndarray:
        """Where each event is on its block's trace (worked out once per search)."""
        name = self.channel_name()
        if self._heights is None or self._heights[0] is not ev or self._heights[1] != name:
            y = np.zeros(len(ev.t))
            for b in np.unique(ev.block):
                trace, m = self._trace(int(b)), ev.block == b
                if trace is not None:
                    k = np.round(ev.t[m] * self.data[int(b)]["sample_rate"]).astype(int)
                    y[m] = trace[np.clip(k, 0, len(trace) - 1)]
            self._heights = (ev, name, y)
        return self._heights[2]

    def units_changed(self) -> None:
        """Mark the spikes on the blocks: the units shown, and if those are only some of them,
        their spikes are drawn over the blocks in their color."""
        ev = self.events()
        on = self.units()
        hl = on if ev is not None and len(on) < self.page.events.n_units else set()
        self.event_hl.setData(np.empty(0), np.empty(0))
        for item in self.trial_items:                     # the picked spikes stand out from them
            item.setOpacity(0.35 if hl else 1.0)
        if ev is None or not len(ev.t):
            self.marks.setData([], [])
            return
        show = np.isin(ev.unit, list(on))
        self.marks.setData(ev.t[show], self._event_heights(ev)[show], brush=self.finder.brushes(ev.unit[show]),
                           size=self.mark_size.value(), pen=pg.mkPen(self._fg(), width=1) if hl else pg.mkPen(None))
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

    def threshold_changed(self) -> None:
        finder = self.finder
        level = finder.level()
        if level is None or self.channel_name() != finder.channel.currentText():
            self.thr_line.hide()
            return
        sign = 1 if finder.polarity.currentData() == "positive" else -1
        self.thr_line.blockSignals(True)
        self.thr_line.setValue(finder.baseline() + sign * level)
        self.thr_line.blockSignals(False)
        self.thr_label.setFormat(f"threshold {finder.fmt(level)}")
        self.thr_label.anchors = [(1, 1 if sign > 0 else 0)] * 2     # on the side away from the traces
        self.thr_label.updatePosition()
        self.thr_line.setVisible(self.show_thr.isChecked())

    def _thr_dragged(self) -> None:
        self.finder.threshold_dragged(self.thr_line.value())
        self.thr_label.setFormat(f"threshold {self.finder.fmt(abs(self.thr_line.value() - self.finder.baseline()))}")

    def apply_theme(self, mode: str) -> None:
        super().apply_theme(mode)
        if self.dock is not None:
            self.refresh()


class RasterChart(SpikeChart):
    kind = "Raster"
    tip = "When did each spike happen, trial by trial?"
    link = "time"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "Events by trial")
        self.point_size = dot_size(self.main.settings, "raster_point_size", 2, 12, "The size of the dots")
        self.field("dots", "Dot size", self.point_size)
        p = self.plot.getPlotItem()
        p.setLabel("left", "trial")
        p.setLabel("bottom", "time in trial", units="s")
        p.invertY(True)                                  # block 1 at the top
        p.getAxis("left").setStyle(showValues=False)
        self.raster_dots = pg.ScatterPlotItem(size=2, pen=None)
        p.addItem(self.raster_dots)
        self._rows: dict[int, int] = {}                  # block -> its row
        # each block's stimulus group, as a colored mark at the start of its row
        self.group_marks = pg.ScatterPlotItem(size=8, symbol="s", pen=None)
        self.group_marks.setZValue(5)
        p.addItem(self.group_marks)
        self.cur_band = pg.LinearRegionItem(movable=False, orientation="horizontal")
        self.cur_band.setZValue(-10)
        p.addItem(self.cur_band)
        self.apply_theme(self._mode)

    def scale_bars(self) -> list[tuple]:
        return [(self.plot, None, None)]

    def draw(self) -> None:
        chosen = self.blocks()
        p = self.plot.getPlotItem()
        rows = {i: r for r, i in enumerate(chosen, 1)}
        if len(rows) != len(self._rows):
            n = len(rows)
            p.vb.bounds[1] = (0.5, n + 0.5) if n else None
            if n:
                p.setYRange(0.5, n + 0.5, padding=0)
        self._rows = rows
        self.time_range(self._span(chosen))
        marks = [(i, self.page.group_of(i)) for i in chosen]
        marks = [(i, g) for i, g in marks if g is not None]
        self.group_marks.setData([0.0] * len(marks), [rows[i] for i, _ in marks],
                                 brush=[pg.mkBrush(self.page.group_color(g)) for _, g in marks])
        ev = self.events()
        if ev is None or not len(ev.t):
            self.raster_dots.setData([], [])
        else:
            show = np.isin(ev.unit, list(self.units()))
            at = np.array([rows.get(int(b), 0) for b in ev.block[show]])
            self.raster_dots.setData(ev.t[show], at, brush=self.finder.brushes(ev.unit[show]),
                                     size=self.point_size.value())
        self.current_changed()

    def current_changed(self) -> None:
        row = self._rows.get(self.page.index())
        self.cur_band.setVisible(row is not None)
        if row is not None:
            self.cur_band.setRegion((row - 0.5, row + 0.5))

    def rescale(self) -> None:
        if self._rows:
            self.plot.getPlotItem().setYRange(0.5, len(self._rows) + 0.5, padding=0)

    def apply_theme(self, mode: str) -> None:
        super().apply_theme(mode)
        band = QtGui.QColor(colors.PALETTE[1])
        band.setAlpha(45)
        self.cur_band.setBrush(pg.mkBrush(band))
        for line in self.cur_band.lines:
            line.setPen(pg.mkPen(None))


class RateChart(SpikeChart):
    kind = "Rate"
    tip = "How fast does it fire through the trial?"
    link = "time"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "Spikes per second")
        self.rate_bin = UnitSpinBox(0.01, 600, "s", step=0.5)
        self.rate_bin.setValue(0.1)
        self.rate_bin.setToolTip("Spikes per second during a trial are counted in bins this long")
        self.field("bin", "Bins", self.rate_bin)
        self.norm_rate = QtWidgets.QCheckBox("Normalize")
        self.norm_rate.setToolTip("Spikes per second during a trial as a fraction of each unit's highest")
        self.field("norm", "", self.norm_rate)
        p = self.plot.getPlotItem()
        p.setLabel("left", "spikes/s")
        p.setLabel("bottom", "time in trial", units="s")
        p.getAxis("left").enableAutoSIPrefix(False)      # fractions stay plain decimals
        self._rate_items: dict[int | None, object] = {}  # unit (None: all of them) -> its rate curve
        self.apply_theme(self._mode)

    def scale_bars(self) -> list[tuple]:
        return [(self.plot, None if self.norm_rate.isChecked() else "spikes/s", None)]

    def draw(self) -> None:
        self._clear()
        p = self.plot.getPlotItem()
        self._rate_items = {}
        chosen = self.blocks()
        end = self._span(chosen)
        self.time_range(end)
        ev = self.events()
        if ev is None or not len(ev.t):
            return
        edges, rates = A.block_rate(ev, len(chosen), end, self.rate_bin.value())
        if not len(edges):
            return
        norm = self.norm_rate.isChecked()
        scale = (lambda r: r / r.max() if r.max() > 0 else r) if norm else (lambda r: r)
        p.setLabel("left", "fraction of the highest" if norm else "spikes/s")
        on = self.units()
        every = len(on) == self.page.events.n_units
        if self.page.events.n_units > 1:
            for u in sorted(on):
                if u in rates:
                    self._rate_items[u] = p.plot(edges, scale(rates[u]), stepMode="center",
                                                 pen=pg.mkPen(self.finder.qcolor(u), width=1.5))
        if every:                                        # all units together is just one more curve
            self._rate_items[None] = p.plot(edges, scale(rates[None]), stepMode="center",
                                            pen=pg.mkPen(self._fg(), width=2))
        p.enableAutoRange(axis="y")                      # a new bin width changes the scale


class PcaChart(SpikeChart):
    kind = "PCA map"
    tip = "Do the spikes fall into separate units?"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "PCA map of the units")
        for side in ("left", "bottom"):
            self.plot.getPlotItem().getAxis(side).enableAutoSIPrefix(False)
        self.apply_theme(self._mode)

    def draw(self) -> None:
        self._clear()
        ev = self.events()
        if ev is None or len(ev.t) <= 2:
            return
        cp = self.plot.getPlotItem()
        points, share = A.principal_components(ev.waves, 2)
        if points is None:
            return
        cp.setLabel("bottom", f"PC1 ({share[0]:.0%} of the differences)")
        cp.setLabel("left", f"PC2 ({share[1]:.0%})")
        for u in range(self.page.events.n_units):        # always every unit: this map is for comparing them
            m = ev.unit == u
            cp.plot(points[m, 0], points[m, 1], pen=None, symbol="o", symbolSize=5, symbolPen=None,
                    symbolBrush=self.finder.qcolor(u))


class WaveformsChart(SpikeChart):
    kind = "Waveforms"
    tip = "What does each unit's spike look like?"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "Waveforms by unit")
        self.norm_waves = QtWidgets.QCheckBox("Normalize")
        self.norm_waves.setToolTip("Each unit's spikes divided by the average size of their peaks (the "
                                   "unit's average peak = ±1), so units compare by shape and spikes keep "
                                   "their sizes within a unit")
        self.field("norm", "", self.norm_waves)
        self.plot.getPlotItem().setLabel("bottom", "ms from the peak")
        self.apply_theme(self._mode)

    def draw(self) -> None:
        self._clear()
        ev = self.events()
        if ev is None or not len(ev.t):
            return
        wp = self.plot.getPlotItem()
        if self.norm_waves.isChecked():
            wp.setLabel("left", "÷ the unit's average peak")
            wp.getAxis("left").enableAutoSIPrefix(False)
        else:
            label_axis(wp, "left", self.finder.channel.currentText(), self.finder.unit())
        peak = int(np.argmin(np.abs(ev.wave_t_ms))) if len(ev.wave_t_ms) else 0
        rng = np.random.default_rng(0)
        for u in sorted(self.units()):
            w = ev.waves[ev.unit == u]
            if not len(w):
                continue
            if self.norm_waves.isChecked():
                size = float(np.abs(w[:, peak]).mean())
                w = w / size if size > 0 else w
            shown = rng.choice(len(w), min(MAX_WAVES, len(w)), replace=False)
            color = self.finder.qcolor(u)
            xs = np.tile(np.append(ev.wave_t_ms, ev.wave_t_ms[-1]), len(shown))
            ys = np.column_stack([w[shown], np.full(len(shown), np.nan)]).ravel()
            wp.plot(xs, ys, pen=pg.mkPen(self.faded(color, 40), width=1), connect="finite")
            wp.plot(ev.wave_t_ms, w.mean(0), pen=pg.mkPen(color, width=3))


class _HistChart(SpikeChart):
    """A histogram of each unit, on bins shared by all of them."""
    norm_tip = ""

    def __init__(self, page, cid: str = "", title: str = ""):
        super().__init__(page, cid, title)
        self.norm = QtWidgets.QCheckBox("Normalize")
        self.norm.setToolTip(self.norm_tip)
        self.field("norm", "", self.norm)
        self.plot.getPlotItem().getAxis("left").enableAutoSIPrefix(False)
        self.apply_theme(self._mode)

    def _bars(self, edges: np.ndarray, counts: np.ndarray, u: int) -> None:
        color = self.finder.qcolor(u)
        y = counts / counts.sum() if self.norm.isChecked() and counts.sum() else counts
        self.plot.getPlotItem().plot(edges, y, stepMode="center", fillLevel=0, brush=self.faded(color, 90),
                                     pen=pg.mkPen(color, width=1.5))


class SizesChart(_HistChart):
    kind = "AP sizes"
    tip = "How big are each unit's spikes?"
    norm_tip = "Each unit's histogram as a fraction of its own events, so units with different counts compare by shape"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "AP sizes")

    def draw(self) -> None:
        self._clear()
        ev = self.events()
        if ev is None or not len(ev.t):
            return
        hp = self.plot.getPlotItem()
        label_axis(hp, "bottom", "size from the baseline", self.finder.unit())
        hp.setLabel("left", "fraction of the unit's events" if self.norm.isChecked() else "events")
        sizes = np.abs(ev.amplitude)
        edges = np.histogram_bin_edges(sizes, bins=40)
        for u in sorted(self.units()):
            self._bars(edges, np.histogram(sizes[ev.unit == u], edges)[0], u)


class IntervalsChart(_HistChart):
    kind = "Intervals"
    tip = "How long between one spike and the next?"
    norm_tip = "Each unit's intervals as a fraction of its own intervals"

    def __init__(self, page, cid: str = ""):
        super().__init__(page, cid, "Inter-spike intervals")
        self.plot.getPlotItem().setLabel("bottom", "ms between spikes of a unit")

    def draw(self) -> None:
        self._clear()
        ev = self.events()
        if ev is None or not len(ev.t):
            return
        ip = self.plot.getPlotItem()
        ip.setLabel("left", "fraction of the unit's intervals" if self.norm.isChecked() else "count")
        every = A.intervals_ms(ev)
        # up to the 98th percentile, so a few long gaps don't squash the rest
        top = float(np.percentile(every, 98)) if len(every) else 100.0
        edges = np.linspace(0, max(top, 1.0), 61)
        for u in sorted(self.units()):
            self._bars(edges, np.histogram(A.intervals_ms(ev, u), edges)[0], u)
