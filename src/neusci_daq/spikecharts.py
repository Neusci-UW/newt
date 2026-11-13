"""The charts of the action potentials found: the stimulus, a raster, the spike rate, and
each unit's PCA map, waveforms, sizes and intervals. The recording with its spikes and the
threshold is on Response (responsecharts.py).
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtGui, QtWidgets

from . import analysis as A
from .chartbase import Chart, dot_size
from .plotzoom import OverlayCurve, TraceCurve, plot_widget
from .unitbox import UnitSpinBox
from .viewkit import label_axis
from . import colors

MAX_WAVES = 150                    # per unit in the waveform plot


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


def draw_step_band(p: pg.PlotItem, edges: np.ndarray, mean: np.ndarray, spread: np.ndarray, color: str) -> list:
    """draw_band for a stepped curve: one value per bin, between `edges`."""
    return draw_band(p, np.repeat(edges, 2)[1:-1], np.repeat(mean, 2), np.repeat(spread, 2), color)


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
        for item in list(p.listDataItems()) + [x for x in p.items if isinstance(x, pg.FillBetweenItem)]:
            p.removeItem(item)


class StimulusChart(SpikeChart):
    kind = "Stimulus"
    y = "signal"
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


class RasterChart(SpikeChart):
    kind = "Raster"
    y = "trial"
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
    y = "spikes/s"
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
        lengths = {i: self.data[i]["data"].shape[1] / self.data[i]["sample_rate"] for i in chosen}
        edges, rates = A.block_rate(ev, lengths, self.rate_bin.value())
        if not len(edges):
            return
        norm = self.norm_rate.isChecked()
        p.setLabel("left", "fraction of the highest" if norm else "spikes/s")
        spread = self.page.spread()
        on = self.units()
        every = len(on) == self.page.events.n_units
        curves = [(u, self.finder.qcolor(u), 1.5) for u in sorted(on) if u in rates] \
            if self.page.events.n_units > 1 else []
        if every:                                        # all units together is just one more curve
            curves.append((None, QtGui.QColor(self._fg()), 2))
        for u, color, width in curves:
            mean, sd, sem, _ = A.mean_spread(rates[u])   # across the trials, in each bin
            k = 1 / np.nanmax(mean) if norm and np.nanmax(mean) > 0 else 1
            if spread:
                draw_step_band(p, edges, mean * k, (sd if spread == "sd" else sem) * k, color)
            self._rate_items[u] = p.plot(edges, mean * k, stepMode="center", pen=pg.mkPen(color, width=width))
        p.enableAutoRange(axis="y")                      # a new bin width changes the scale


class PcaChart(SpikeChart):
    kind = "PCA map"
    measure = ("shape", "pca")
    y = "PC2"
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
    measure = ("event", "spike")
    y = "signal"
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
        spread = self.page.spread()
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
            mean, sd, sem, _ = A.mean_spread(w)              # across the unit's spikes
            if spread:
                draw_band(wp, ev.wave_t_ms, mean, sd if spread == "sd" else sem, color)
            wp.plot(ev.wave_t_ms, mean, pen=pg.mkPen(color, width=3))


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
    measure = ("values", "sizes")
    y = "events"
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
    measure = ("values", "intervals")
    y = "count"
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
