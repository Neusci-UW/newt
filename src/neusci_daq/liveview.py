"""The main window's live view: the plots of the acquisition, calculated channels, the
buffers behind them, the time axis (Window, scrolling, zoom), Start and Record, and the
screen refresh."""
from __future__ import annotations

import queue
import time
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from . import session
from . import style
from . import dsp
from .channels import CalcStrip, ChannelRow, ChannelRowWidget, ChannelStrip, format_y
from .config import Channel, valid_inputs
from .plotzoom import MultiAxis, TraceCurve, light_grid
from .stim import Stimulus
from .ringbuffer import RingBuffer
from . import colors, theme, trialfile
from .mainparts import LIVE, REVIEW, WINDOW_MAX, _SI_BASE, _TraceViewBox
from .units import whole

KEEP_S = 60.0          # seconds the live display always holds, however far you zoom in
# Live: a bar where each acquisition starts (Start, or carrying on after a wait) and one
# where each trial starts (a recorded block, or a saved stimulus window)
BOUND_COLORS = {"acq": "#1fa39a", "trial": "#e8a33d", "trial_end": "#e8a33d"}
GAP_SHOWN_MIN_S, GAP_SHOWN_FRAC = 0.2, 0.05   # a wait is shown this wide at most: 0.2 s, or 5% of Window
STIM_TAIL_S = 0.5      # acquiring this long after the last stimulus ends, then stopping (Stimulus not None)


def _time(s: float) -> str:
    """A time for the readout: 33.512 s, or 120 ms / 450 µs under a second."""
    a = abs(s)
    if a >= 1 or a == 0:
        return f"{s:.4g} s" if a < 1000 else f"{s:.0f} s"
    if a >= 1e-3:
        return f"{s * 1e3:.4g} ms"
    return f"{s * 1e6:.4g} µs"


class LiveMixin:
    # -- plots -------------------------------------------------------------
    def _clear_plot_area(self):
        self._clear_markers()
        while self.plot_layout.count():
            w = self.plot_layout.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.plots, self.curves, self.strips = [], [], []
        self.review_strips = []
        self._joins = []                         # (they went with the plots)
        self.plot_units = []
        self._readout_items, self._readout_at = [], None
        self.plot_widgets, self.curve_colors, self.curve_widths = [], [], []
        self.row_widgets = []
        self._unfitted.clear()
        self._overlay_items = []                 # they went with the plots
        self._mirrors = []
        self._multi = None

    def _new_plot(self, name: str, unit: str, color: str, width: float = 1.0) -> pg.PlotWidget:
        pw = pg.PlotWidget(viewBox=_TraceViewBox(self._zoom_time, self._pan_time, self._zoom_to,
                                                      autoscale=self._autoscale_view))
        vb = pw.getPlotItem().vb
        vb.clamp_to_data = not self.btn_run.isChecked()
        vb.set_box_tool(getattr(self, "btn_box_zoom", None) is not None and self.btn_box_zoom.isChecked())
        vb.on_fit = lambda vb=vb: self._fit_view(vb)
        vb.on_zoomed = lambda vb: self._zoom_stack.append(vb)
        vb.on_cursor = lambda x: self._readout(x, None, True)      # click: the value there
        vb.on_select = self._readout                               # drag: the average over it
        p = pw.getPlotItem()
        self.plot_widgets.append(pw)
        self.plot_units.append(unit)
        if unit in _SI_BASE:
            p.setLabel("left", name, units=unit)       # pyqtgraph adds m, µ, k as needed
        else:
            # A prefix on a unit that already has one (mV, µA) or isn't SI (mmHg)
            # would read "kmV", so show it as it is.
            p.setLabel("left", f"{name} ({unit})" if unit else name)
            p.getAxis("left").enableAutoSIPrefix(False)
        p.getAxis("left").setWidth(70)   # same width on every plot, so time axes line up
        p.showGrid(x=True, y=True, alpha=0.2)
        light_grid(p)
        # No pyqtgraph downsampling: TraceCurve does its own, which doesn't alias.
        p.enableAutoRange(axis="y", enable=self.auto_scale_y)
        p.setAutoVisible(y=True)
        if not self.auto_scale_y:
            self._unfitted.add(p)
        if self.plots:
            p.setXLink(self.plots[0])
        self.plots.append(p)
        curve = TraceCurve()
        p.addItem(curve)
        self.curves.append(curve)
        self.curve_colors.append(color)
        self.curve_widths.append(float(width))
        self._set_curve_color(len(self.curves) - 1, color)
        self._style_plot(pw)
        return pw

    def _fit_strips(self) -> None:
        """Fold the channels' settings to their small form (they open on hover) when the
        rows wouldn't all fit with them open, or when Acquisition shares the screen."""
        if not hasattr(self, "tabs"):
            return
        rows = self.row_widgets
        need = sum(rw.full_height for rw in rows) + self.plot_layout.spacing() * max(0, len(rows) - 1)
        compact = self.tabs.shares_screen("acq") or need > self.plot_area.height()
        if compact != self._compact_strips:
            self._compact_strips = compact
            for rw in rows:
                rw.set_compact(compact or getattr(rw, "overlaid", False))    # overlaid rows stay folded

    def _autoscale_view(self, vb) -> None:
        """Right-click › Autoscale: fit this plot's y axis to its data now. With View ›
        Auto-scale on it keeps fitting; off, it holds the new range."""
        vb._zoom_history.clear()
        vb.y_free = False                                # back within the data
        vb.enableAutoRange(axis="y", enable=True)
        vb.updateAutoRange()
        vb.enableAutoRange(axis="y", enable=self.auto_scale_y)
        if self._multi is not None and self.plots and vb is self.plots[0].vb:
            self._multi.fit()                            # the overlaid traces too

    def set_box_tool(self, on: bool) -> None:
        """Time bar › Zoom box: a plain drag on any plot draws a box and zooms to it."""
        for p in self.plots:
            p.vb.set_box_tool(on)

    def _zoom_back(self) -> None:
        """Time bar › Back: undo the last box zoom, on whichever plot it was drawn."""
        live = {p.vb for p in self.plots}
        while self._zoom_stack:
            vb = self._zoom_stack.pop()
            if vb in live and vb._zoom_history:
                vb.zoom_back()
                return

    def _fit_view(self, vb) -> None:
        """Double-click with the zoom box tool: all the data in time, and this plot's
        y axis fitted to it."""
        self._time_fit_all()
        self._autoscale_view(vb)

    def set_auto_scale_y(self, on: bool) -> None:
        """View › Auto-scale voltage. Turning it off holds every plot's current range."""
        self.auto_scale_y = bool(on)
        self.settings.setValue("auto_scale_y", self.auto_scale_y)
        self.act_auto_y.blockSignals(True)
        self.act_auto_y.setChecked(self.auto_scale_y)
        self.act_auto_y.blockSignals(False)
        self._unfitted.clear()
        for p in self.plots:
            p.vb.y_free = False
            p.enableAutoRange(axis="y", enable=self.auto_scale_y)
        if self._multi is not None:
            self._multi.set_auto(self.auto_scale_y)

    def set_overlay_axes(self, separate: bool) -> None:
        """View › Overlaid channels on separate axes."""
        self.settings.setValue("overlay_separate_axes", bool(separate))
        if self.act_separate_y.isChecked() != bool(separate):
            self.act_separate_y.blockSignals(True)
            self.act_separate_y.setChecked(bool(separate))
            self.act_separate_y.blockSignals(False)
        if self.btn_overlay_ch.isChecked():
            self.set_channel_overlay(True)              # rebuild with the new axes

    def set_trace_mode(self, mode: str) -> None:
        """View › Zoomed-out traces: "mean" (moving average) or "peak" (min and max)."""
        TraceCurve.mode = mode if mode in ("mean", "peak") else "mean"
        self.settings.setValue("trace_mode", TraceCurve.mode)
        self.act_trace_mode[TraceCurve.mode].setChecked(True)
        for c in self.curves:
            c.refresh()

    def _curves_updated(self) -> None:
        """After new data went into the curves."""
        self._fit_new_plots()
        if self._readout_at is not None:         # the cursor's or highlight's values, for the new data
            self._update_readout()
        for k, mirror in self._mirrors:
            if k < len(self.curves):
                mirror.setData(*self.curves[k].getOriginalDataset())
        if self._multi is not None and self._multi_unfitted and self._mirrors \
                and all(m.yData is not None and len(m.yData) for _, m in self._mirrors):
            self._multi.fit()                            # auto-scale off: fit once, then hold
            self._multi_unfitted = False

    def set_channel_overlay(self, on: bool) -> None:
        """Overlay channels: every channel in the first plot."""
        self.settings.setValue("overlay_channels", bool(on))
        if self.btn_overlay_ch.isChecked() != bool(on):
            self.btn_overlay_ch.blockSignals(True)
            self.btn_overlay_ch.setChecked(bool(on))
            self.btn_overlay_ch.blockSignals(False)
        if self.mode == LIVE:
            self._build_live_plots()
            self._redraw_live()
        elif self.review_blocks:
            self._review_names = None               # rebuild the plots for this block
            self._show_review_block(self.block_combo.currentIndex())

    def _apply_channel_overlay(self) -> None:
        """With Overlay channels on, put a copy of each overlaid channel's curve in the
        first overlaid one's plot (in its color) and hide their plots: live, their rows
        shrink to just the channel's controls; in review, their rows go. Channels taken
        out of the overlay (the tool's menu) keep their own plots."""
        self._mirrors = []
        self._multi = None
        names = self._plot_names
        name_of = lambda k: names[k] if k < len(names) else f"Channel {k + 1}"
        inside = [k for k in range(len(self.plots)) if name_of(k) not in self.overlay_out]
        if not self.btn_overlay_ch.isChecked() or len(inside) < 2:
            return
        host = inside[0]
        first = self.plots[host]
        legend = first.addLegend(offset=(10, 10))
        legend.clear()
        separate = self.act_separate_y.isChecked()
        multi = MultiAxis(first, separate=separate)
        multi.set_auto(self.auto_scale_y)
        for k, (pw, curve) in enumerate(zip(self.plot_widgets, self.curves)):
            if k not in inside:
                continue
            name = name_of(k)
            ax = pw.getPlotItem().getAxis("left")
            unit = getattr(ax, "labelUnits", "") or ""
            color = curve.opts["pen"].color() if hasattr(curve.opts["pen"], "color") else None
            if k == host:
                multi.add(curve, name, unit, color)       # already in the host plot
                legend.addItem(curve, name)
                continue
            mirror = TraceCurve()
            mirror.setPen(curve.opts["pen"])
            multi.add(mirror, name, unit, color)
            legend.addItem(mirror, name)
            self._mirrors.append((k, mirror))
            row = pw.parentWidget()
            if self.mode == LIVE and isinstance(row, ChannelRowWidget):
                pw.hide()                                # keep the channel's controls, folded (they open on hover)
                row.fit_strip = row.overlaid = True     # so the overlay plot gets the height
                row.set_compact(True)
            elif row is not None:
                row.hide()
        if not separate:
            first.setLabel("left", "all channels")
            first.getAxis("left").enableAutoSIPrefix(False)
        self._multi = multi
        self._multi_unfitted = not self.auto_scale_y
        # Time numbers on the lowest plot still showing.
        showing = [k for k in range(len(self.plots)) if k not in inside[1:]]
        for k in showing:
            self.plots[k].getAxis("bottom").setStyle(showValues=k == showing[-1])
        self.plots[showing[-1]].setLabel("bottom", "time", units="s")
        # The overlay's right-hand axes make its plot narrower: give the plots still
        # showing the same room on the right, so their times line up.
        others = [self.plots[k] for k in showing if k != host]

        def align(*_):
            for p in others:
                l, t, r, b = p.layout.getContentsMargins()
                r = max(0.0, r + p.vb.width() - first.vb.width())
                if abs(r - p.layout.getContentsMargins()[2]) > 0.5:
                    p.layout.setContentsMargins(l, t, r, b)
        if others:
            first.vb.sigResized.connect(align)
            for p in others:
                p.vb.sigResized.connect(align)
            align()

    def overlay_menu(self, menu: QtWidgets.QMenu) -> None:
        """The overlay tool's menu: a check for each channel, ticked if it's overlaid."""
        menu.clear()
        names = self._plot_names[:len(self.plots)] or [c.name for c in self.cfg.channels]
        for name in names:
            act = menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name not in self.overlay_out)
            act.toggled.connect(lambda on, name=name: self.set_overlaid(name, on))

    def set_overlaid(self, name: str, on: bool) -> None:
        """Put a channel in the overlay or take it out (back to its own plot). Ticking one
        turns the overlay on."""
        (self.overlay_out.discard if on else self.overlay_out.add)(name)
        self.settings.setValue("overlay_excluded", "\n".join(sorted(self.overlay_out)))
        if on and not self.btn_overlay_ch.isChecked():
            self.btn_overlay_ch.setChecked(True)              # rebuilds the plots
        elif self.btn_overlay_ch.isChecked():
            self.set_channel_overlay(True)

    def _fit_new_plots(self) -> None:
        """With auto-scale off, give a plot that has just got its first data a range
        that fits it, then leave it alone."""
        for p, curve in zip(self.plots, self.curves):
            if p not in self._unfitted:
                continue
            y = curve.yData
            if y is None or not len(y) or not np.isfinite(y).any():
                continue
            lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
            p.setYRange(lo, hi if hi > lo else lo + 1, padding=0.05)
            self._unfitted.discard(p)

    # -- calculated channels (dsp.py) ---------------------------------------------------------
    def _add_calc_rows(self, names: list[str], fs: float, live: bool) -> None:
        """A row for each calculated channel, below the channels' rows."""
        self._chain = dsp.Chain(self.cfg.calculations, names, fs)   # each may use the ones above it
        self._calcs = [(c, f, f"Can't calculate: {why}" if why else "")
                       for c, f, why in zip(self._chain.calcs, self._chain.formulas, self._chain.problems)]
        for k, (c, f, problem) in enumerate(self._calcs):
            color = colors.PALETTE[(len(names) + k) % len(colors.PALETTE)]
            pw = self._new_plot(c.name, c.unit, color, 1.0)
            if live or getattr(self, "review_strips", None):     # lined up under the channels' strips
                row = ChannelRowWidget(CalcStrip(c.name, c.formula, problem,
                                                 lambda *_, k=k: self.analysis_channel_dialog(k)), pw)
                row.set_compact(self._compact_strips)
                self.row_widgets.append(row)
            else:
                row = QtWidgets.QWidget()
                h = QtWidgets.QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                pad = QtWidgets.QLabel("ƒ")
                pad.setToolTip(f"{c.name} = {c.formula}" + (f"\n{problem}" if problem else ""))
                pad.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                pad.setFixedWidth(self.review_color_btns[0].sizeHint().width() if self.review_color_btns else 24)
                h.addWidget(pad, 0)
                h.addWidget(pw, 1)
            if problem:
                pw.getPlotItem().setTitle(problem, color="#e0524f", size="9pt")
            self.plot_layout.addWidget(row, 1)
        self._plot_names = list(names) + [c.name for c, _, _ in self._calcs]

    def _calc_chunk(self, y_raw: np.ndarray) -> np.ndarray:
        """The calculated channels for a chunk of live data (raw volts, one row a channel)."""
        env = {ch.name: ch.to_units(row) for ch, row in zip(self.cfg.channels, y_raw)}
        m = y_raw.shape[1]
        out = self._chain.stream(env, self.cfg.sample_rate) if self._calcs else []
        return np.array([y if y is not None else np.full(m, np.nan) for y in out]).reshape(len(self._calcs), m)

    def _recompute_calc_ring(self) -> None:
        """Calculated values for the live data held, e.g. after the calculations change."""
        ring = getattr(self, "ring", None)
        self.calc_ring = RingBuffer(len(self._calcs), ring.cap if ring is not None else 2, np.float32)
        if self._calcs:
            self._chain.reset()
        if self._calcs and ring is not None and len(ring):
            # stretch by stretch: the history breaks (a NaN sample) where nothing was acquired,
            # and filters start again after each break rather than carry the NaN on
            t, y = self.ring.t, self.ring.y
            breaks = np.flatnonzero(np.isnan(y).all(axis=0)) if y.size else np.empty(0, int)
            start = 0
            for b in list(breaks) + [len(t)]:
                if b > start:
                    self.calc_ring.append(t[start:b], self._calc_chunk(y[:, start:b]))
                if b < len(t):
                    self.calc_ring.append(t[b:b + 1], np.full((len(self._calcs), 1), np.nan))
                    self._chain.reset()
                start = b + 1

    def _draw_calc_live(self) -> None:
        if not self._calcs or self.calc_ring is None or not len(self.calc_ring):
            return
        n = len(self.cfg.channels)
        x = self.calc_ring.t - self._axis_zero
        for k in range(len(self._calcs)):
            if n + k < len(self.curves):
                self.curves[n + k].setData(x, self.calc_ring.y[k])

    def _calc_sources(self) -> tuple[list[str], dict[str, str], float]:
        """(recorded channel names, their units, sample rate) of what's shown."""
        if self.mode == REVIEW and self.review_block:
            blk = self.review_block
            return list(blk["channel_names"]), dict(zip(blk["channel_names"], blk["units"])), blk["sample_rate"]
        return ([c.name for c in self.cfg.channels], {c.name: c.units for c in self.cfg.channels},
                self.cfg.sample_rate)

    def analysis_channel_dialog(self, k: int | None = None) -> bool:
        """Add an analysis channel (k None) or change analysis channel k, LabChart-style."""
        from .analysischannel import AnalysisChannelDialog
        names, units, fs = self._calc_sources()
        calcs = list(self.cfg.calculations)
        above = calcs if k is None else calcs[:k]
        units = {**units, **{c.name: c.unit for c in above}}
        sources = names + [c.name for c in above]

        def preview(formula):
            if self.mode == REVIEW and self.review_block:
                blk = self.review_block
                env, t = dict(zip(blk["channel_names"], blk["data"])), blk["t"]
            elif getattr(self, "ring", None) is not None and len(self.ring):
                env = {ch.name: ch.to_units(row) for ch, row in zip(self.cfg.channels, self.ring.y)}
                t = self.ring.t - self.ring.t[0]
            else:
                raise ValueError("press Start to see it on live data")
            y = dsp.Chain(above + [dsp.Calculation("\0preview", formula)], names, fs).offline(env, fs)[-1]
            if y is None:
                raise ValueError("it uses a channel that can't be calculated")
            return t, y

        dlg = AnalysisChannelDialog(self, sources, units, fs, calc=None if k is None else calcs[k],
                                    taken={c.name for j, c in enumerate(calcs) if j != k}, preview=preview,
                                    sorter=self.analysis_page.raw.cyclic_defaults())
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return False
        if k is None:
            calcs.append(dlg.calculation())
            # a new analysis channel gets a channel row of its own, not a right axis in the
            # channel overlay (its tool's menu can put it in)
            self.overlay_out.add(calcs[-1].name)
        elif dlg.deleted:
            del calcs[k]
        else:
            old, calcs[k] = calcs[k], dlg.calculation()
            if old.name in self.overlay_out and calcs[k].name != old.name:   # renamed: still on its own
                self.overlay_out.discard(old.name)
                self.overlay_out.add(calcs[k].name)
        self.settings.setValue("overlay_excluded", "\n".join(sorted(self.overlay_out)))
        self.set_calculations(calcs)
        return True

    def set_calculations(self, calcs: list) -> None:
        self.cfg.calculations = list(calcs)
        if self.mode == LIVE:
            self._build_live_plots()
            self._redraw_live()
        elif self.review_blocks:
            self._review_names = None
            self._show_review_block(self.block_combo.currentIndex())
        self.analysis_page.source_changed()

    def _finish_plots(self):
        if self.plots:
            # Time numbers on the bottom plot only, like LabChart: the others keep their
            # ticks and grid. It also saves redrawing every plot's axis text each frame,
            # which was most of a live screen update's time.
            for p in self.plots[:-1]:
                p.getAxis("bottom").setStyle(showValues=False)
            self.plots[-1].setLabel("bottom", "time", units="s")
            self.plots[0].sigXRangeChanged.connect(self._xrange_changed)
            if getattr(self, "mode", LIVE) == LIVE:
                self._redraw_bounds()             # the acquisition and trial bars
            self._axis_group.set_plots(self.plots)     # every plot's area starts at the same x
            if getattr(self, "mode", LIVE) == LIVE and not self.btn_run.isChecked():
                self._show_stopped()     # new plots (e.g. after a rate change) start at Window
        self._apply_channel_overlay()

    def _build_live_plots(self):
        """One row per channel: its controls on the left, its plot on the right.

        A switched-off channel keeps its controls, so it can be switched back on,
        but has no plot.
        """
        self._clear_plot_area()
        inputs = valid_inputs(self.cfg)
        locked = self.recorder.recording or (hasattr(self, "trials_page") and self.trials_page.running)
        for r in self.rows:
            strip = ChannelStrip(r, inputs, self.cfg.terminal_config == "DIFF",
                                 self._channel_edited, self._channel_removed, self._channel_recolored,
                                 can_remove=len(self.rows) > 1, on_width=self._channel_width,
                                 is_stim=bool(self.cfg.stim_monitor) and r.ch.physical == self.cfg.stim_monitor,
                                 on_stim=self._channel_stim_ticked)
            strip.set_locked(locked)
            self.strips.append(strip)
            if r.ch.enabled:
                content = self._new_plot(r.ch.name, r.ch.units, r.ch.color, r.ch.width)
                p = content.getPlotItem()
                strip.attach_plot(p.vb, r.ch.units, r.ch.units in _SI_BASE, self._autoscale_view,
                                  on_hold=lambda p=p: self._unfitted.discard(p))
            else:
                content = QtWidgets.QLabel("off")
                content.setStyleSheet("color: gray; padding-left: 8px")
            row_w = ChannelRowWidget(strip, content)
            row_w.fit_strip = not r.ch.enabled
            row_w.set_compact(self._compact_strips)
            self.row_widgets.append(row_w)
            if r.ch.enabled:
                self.plot_layout.addWidget(row_w, 1)
            else:
                self.plot_layout.addWidget(row_w, 0)
        self._add_calc_rows([r.ch.name for r in self.rows if r.ch.enabled], self.cfg.sample_rate, live=True)
        self._recompute_calc_ring()
        self._finish_plots()
        self._fit_strips()
        self.btn_add_ch.setEnabled(not locked)
        self.rate_box.setEnabled(not locked)
        if hasattr(self, "trials_page"):
            self.trials_page.sync()

    def _build_review_plots(self, names: list[str], units: list[str], colors: list[str]):
        """Plots for a saved session: one per recorded channel, each with its strip as
        in live mode (color, line thickness, Max/Min and scale); what a saved trial
        can't change (on/off, name, input) is locked."""
        self._review_names = list(names)
        self._clear_plot_area()
        self.review_color_btns = []
        self.review_strips = []
        widths = {r.ch.name: r.ch.width for r in self.rows}
        for i, (name, unit, color) in enumerate(zip(names, units, colors)):
            ch = Channel(physical=name, name=name, units=unit, color=color, width=widths.get(name, 1.0))
            strip = ChannelStrip(ChannelRow(ch), [name], False, lambda *_: None, lambda *_: None,
                                 lambda st, c, i=i: self.set_review_color(i, c), can_remove=False,
                                 on_width=lambda st, w, i=i: self.set_review_width(i, w))
            strip.set_locked(True)
            strip.input.setToolTip("Recorded as this channel (can't be changed in a saved trial)")
            strip.color_btn.setToolTip("Change this channel's color in every trial of this session")
            self.review_color_btns.append(strip.color_btn)
            self.review_strips.append(strip)
            pw = self._new_plot(name, unit, color, ch.width)
            pw.setToolTip("Double-click to add a comment")
            pw.scene().sigMouseClicked.connect(lambda ev, pw=pw: self._plot_clicked(pw, ev))
            p = pw.getPlotItem()
            strip.attach_plot(p.vb, unit, unit in _SI_BASE, self._autoscale_view,
                              on_hold=lambda p=p: self._unfitted.discard(p))
            row_w = ChannelRowWidget(strip, pw)
            row_w.set_compact(self._compact_strips)
            self.row_widgets.append(row_w)
            self.plot_layout.addWidget(row_w, 1)
        fs = (self.review_block or {}).get("sample_rate", self.cfg.sample_rate)
        self._add_calc_rows(list(names), fs, live=False)
        self._finish_plots()
        self._fit_strips()

    def set_review_width(self, index: int, width: float) -> None:
        """Line thickness of a channel while browsing trials (display only). The live
        channel of the same name takes it too, so it's the same either way."""
        from .channels import LINE_MAX, LINE_MIN
        width = min(LINE_MAX, max(LINE_MIN, float(width)))
        if index < len(self.curve_widths):
            self.curve_widths[index] = width
            self._set_curve_color(index, self.curve_colors[index])
        name = self._review_names[index] if index < len(self._review_names) else None
        for r in self.rows:
            if r.ch.name == name:
                r.ch.width = width
        if getattr(self, "_overlay_sel", None):
            self._draw_overlay()                 # the overlaid trials match it

    def _alloc_buffers(self, trim: bool = True):
        """Size the live display's ring buffer to Window (but never under KEEP_S). trim=False (stopped) only
        grows it, so zooming in and back out doesn't lose the trace on screen."""
        n = len(self.cfg.channels)
        # At least the last minute, whatever Window shows, so zooming in and back out
        # always finds it (float32: about 10 MB per channel at 20 kHz).
        self.cap = max(2, int(max(self.window_box.value(), KEEP_S) * self.cfg.sample_rate))
        ring = getattr(self, "ring", None)
        if ring is None or ring.n != n:
            self.ring = RingBuffer(n, self.cap, np.float32)   # half the memory; plenty for 16-bit DAQ readings
        elif trim:
            ring.resize(self.cap)
        elif self.cap > ring.cap:
            # Grow only, and in doubling steps (up to the longest Window): each resize copies
            # the whole buffer, and doing that on every wheel tick froze zooming while streaming.
            ring.resize(min(max(self.cap, 2 * ring.cap), int(WINDOW_MAX * self.cfg.sample_rate)))
        cr = getattr(self, "calc_ring", None)
        if cr is not None and cr.cap != self.ring.cap:
            cr.resize(self.ring.cap)             # the calculated values keep pace

    @property
    def buf_t(self) -> np.ndarray:
        """Times of the live samples held (a view of the ring buffer)."""
        ring = getattr(self, "ring", None)
        return ring.t if ring is not None else np.empty(0)

    @property
    def buf_y(self) -> np.ndarray:
        """The live samples held, one row per channel (a view of the ring buffer)."""
        ring = getattr(self, "ring", None)
        return ring.y if ring is not None else np.empty((len(self.cfg.channels), 0))

    def _update_scroll_range(self):
        if self.review_block is None:
            return
        win = self.window_box.value()
        span = max(0.0, self.review_block["duration_s"] - win)
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, int(span * 1000))
        self.scroll.setPageStep(int(win * 1000))
        self.scroll.setSingleStep(max(1, int(win * 100)))
        self.scroll.blockSignals(False)

    def _scrolled(self, value_ms: int):
        if self.mode != REVIEW:
            return
        start = value_ms / 1000
        self.plots[0].setXRange(start, start + self.window_box.value(), padding=0)

    def _xrange_changed(self, _vb, rng):
        """Keep the scrollbar in step when the user pans or zooms with the mouse."""
        if self.mode != REVIEW:
            return
        self.scroll.blockSignals(True)
        self.scroll.setValue(int(max(0.0, rng[0]) * 1000))
        self.scroll.blockSignals(False)

    def _zoom_time(self, s: float, x: float | None = None):
        """Mouse wheel over a plot: scale Window by s. In review and while acquisition
        is stopped, the time under the mouse (x) stays put; while running, the newest
        data stays at the right edge."""
        wb = self.window_box
        old = wb.value()
        # Three significant figures keeps the box readable ("13.5 s"). Always move at
        # least one step, or small scrolls (a trackpad, or 0.01 s × 1.35 before) round
        # back to the same value and zoom gets stuck.
        new = float(f"{old * s:.3g}")
        if new == old and s != 1:
            step = 10.0 ** (np.floor(np.log10(old)) - 2)
            new = old + (step if s > 1 else -step)
        new = min(max(new, wb.minimum()), wb.maximum())
        if not self.plots or (self.mode == LIVE and self.btn_run.isChecked()):
            wb.setValue(new)
            return
        x0 = self.plots[0].vb.viewRange()[0][0]
        x = x0 + old / 2 if x is None else x
        wb.setValue(new)
        start = x - (x - x0) * wb.value() / old
        if self.mode == REVIEW:
            self.scroll.setValue(int(max(0.0, start) * 1000))
        else:
            self._show_stopped(start)

    def _zoom_to(self, x0: float, x1: float) -> None:
        """Ctrl+drag box zoom: Window becomes the box's width and the plots start at its
        left edge (while acquiring, only Window changes: the newest data stays at the right)."""
        wb = self.window_box
        wb.setValue(min(max(float(f"{x1 - x0:.3g}"), wb.minimum()), wb.maximum()))
        if not self.plots or (self.mode == LIVE and self.btn_run.isChecked()):
            return
        if self.mode == REVIEW:
            self.scroll.setValue(int(max(0.0, x0) * 1000))
        else:
            self._show_stopped(x0)

    def _pan_time(self, delta: float):
        """Shift+wheel over a plot: move a tenth of Window per wheel notch, earlier for
        wheel up and later for wheel down. Not while acquiring, where the newest data
        stays at the right edge."""
        if not self.plots or (self.mode == LIVE and self.btn_run.isChecked()):
            return
        start = self.plots[0].vb.viewRange()[0][0] - delta / 120 * 0.1 * self.window_box.value()
        if self.mode == REVIEW:
            self.scroll.setValue(int(max(0.0, start) * 1000))     # the scrollbar keeps it in the block
        else:
            self._show_stopped(start)

    def _time_fit_all(self) -> None:
        """Time bar › Show all the data: Window becomes the data's length."""
        span = self._data_span()
        if span is None or not self.plots:
            return
        wb = self.window_box
        wb.setValue(min(span, wb.maximum()))
        if self.mode == REVIEW:
            self.scroll.setValue(0)
        else:
            self._show_stopped(-np.inf)

    def _time_to_end(self) -> None:
        """Time bar › Show the newest data: scroll to the end. (While acquiring it's already there.)"""
        if not self.plots or (self.mode == LIVE and self.btn_run.isChecked()):
            return
        if self.mode == REVIEW:
            self.scroll.setValue(self.scroll.maximum())
        else:
            self._show_stopped(np.inf)

    def _sync_time_bar(self) -> None:
        """The time bar's Window follows the toolbar's; its newest/all buttons only do
        something when there's a stopped trace or a recorded block to move about in."""
        wb, ro = self.window_box, getattr(self, "window_readout", None)
        if ro is None:
            return
        ro.blockSignals(True)
        ro.setRange(wb.minimum(), wb.maximum())
        ro.setValue(wb.value())
        ro.blockSignals(False)
        still = self._data_span() is not None
        for kind in ("end", "fit_x"):
            self._time_buttons[kind].setEnabled(still)

    def _data_span(self) -> float | None:
        """How many seconds of data there are to show, or None if Window isn't limited:
        while acquiring, or with no trace."""
        if self.mode == REVIEW:
            return self.review_block["duration_s"] if self.review_block is not None else None
        buf_t = self.buf_t
        return None if self.btn_run.isChecked() or len(buf_t) < 2 else float(buf_t[-1] - buf_t[0])

    def _fit_window(self) -> None:
        """Window is what the plots show, so it can't be longer than the data. When the
        data cuts it short, the old value comes back for the next run."""
        wb, span = self.window_box, self._data_span()
        self._fitting_window = True
        if span is None:
            wb.setMaximum(WINDOW_MAX)
            if self._window_wanted is not None:
                wb.setValue(self._window_wanted)
                self._window_wanted = None
        else:
            limit = max(wb.minimum(), np.floor(span * 1e4) / 1e4)
            if wb.value() > limit and self._window_wanted is None:
                self._window_wanted = wb.value()
            wb.setMaximum(limit)
        self._fitting_window = False
        self._sync_time_bar()

    def _window_changed(self):
        if not self._fitting_window:
            self._window_wanted = None           # you picked it: that's the one to keep
        if self.mode == REVIEW:
            self._update_scroll_range()
            self._scrolled(self.scroll.value())
        elif self.btn_run.isChecked():
            # Grow only: zooming in and back out while acquiring keeps what's already
            # held (the refresh draws just the last Window of it). Once zooming has
            # settled, the buffer shrinks back to the Window, so a long Window left
            # behind doesn't keep its memory.
            self._alloc_buffers(trim=False)
            timer = getattr(self, "_trim_timer", None)
            if timer is None:
                timer = self._trim_timer = QtCore.QTimer(self, singleShot=True, interval=2000)
                timer.timeout.connect(self._trim_after_zoom)
            timer.start()
        else:
            # Stopped: keep the last trace (Start clears it anyway) so zooming in
            # and back out doesn't lose it, and redraw since no refresh will.
            self._alloc_buffers(trim=False)
            self._show_stopped()

    def _trim_after_zoom(self) -> None:
        """Zooming stopped a while ago while acquiring: give back what's beyond Window (or KEEP_S)."""
        if self.mode == LIVE and self.btn_run.isChecked() and self.ring is not None \
                and self.ring.cap > self.cap:
            self._alloc_buffers(trim=True)

    def _show_stopped(self, start: float | None = None) -> None:
        """Acquisition stopped: show Window seconds of the last trace from `start`
        (default: keep the right edge where it is), staying within the data."""
        if not self.plots:
            return
        win = self.window_box.value()
        buf_t = getattr(self, "buf_t", np.empty(0))
        if not len(buf_t):
            # No trace (e.g. a rate change cleared it): show Window from 0 s,
            # where the next run starts, so zooming still visibly does something.
            self.plots[0].setXRange(0, win, padding=0)
            if not self._fitting_window:
                self._fit_window()
            return
        x0, x1 = buf_t[0] - self._axis_zero, buf_t[-1] - self._axis_zero
        if start is None:
            start = self.plots[0].vb.viewRange()[0][1] - win
        start = min(max(start, x0), max(x0, x1 - win))
        self.plots[0].setXRange(start, start + win, padding=0)

    # ======================================================================
    # Live mode
    # ======================================================================
    def _toggle_run(self, on: bool):
        for p in self.plots:
            p.vb.clamp_to_data = not on
        if on:
            self._stim_sent, self._stim_idle_since = False, None
            windows = self._simple_windows()
            tp = getattr(self, "trials_page", None)
            protocol = (self.settings.value("record_stim", "simple") == "complex" and tp is not None
                        and tp.varying and self.stim_out is not None)   # it saves each stimulus's window
            if not self.resume_acquiring(record=getattr(self, "_start_records", True)
                                         and not windows and not protocol, keep_trace=True, new_run=True):
                return
            if windows:                          # each stimulus saved as a trial of its own
                sp = self.simple_page
                self.window_saver.start(sp.before.value(), sp.after.value())
            self.btn_run.setText("Stop")
            self.timer.start()
            if windows and not getattr(self, "_starting_for_stim", False):
                # record its Before (the baseline) first
                QtCore.QTimer.singleShot(int(self.simple_page.before.value()) + 100,
                                         lambda: self.btn_run.isChecked() and self._stim_on_start())
            else:
                self._stim_on_start()
        else:
            if self.recorder.recording:          # ends the block; Record stays on for the next Start
                self._stop_block()
            self.stop_stimulus()
            if hasattr(self, "window_saver"):
                self.window_saver.stop()         # the last stimulus's trial, with what arrived
            self.backend.stop()
            self._end_stretch()
            self._stopped_at = time.monotonic()  # for the shaded band where the next run joins on
            self.timer.stop()
            self.btn_run.setText("Start")
            self._show_rec_armed()
            self._fit_window()
            if self.mode == LIVE:
                self._redraw_live()              # all of the trace held, not just the last Window drawn while running

    def resume_acquiring(self, record: bool = True, keep_trace: bool = False, new_run: bool = False) -> bool:
        """Start the stream afresh (Start, or a run carrying on after a wait) and, with
        Record on (and `record`), a new block. The display starts empty at 0 s, or with
        keep_trace (the same run) carries on from the end of what's shown: the run's
        stretches end to end, a dashed line where each joins, the waits left out."""
        # Drop chunks left over from the previous run; their timestamps
        # restart from 0, so drawing them would join old and new data.
        while True:
            try:
                self.display_q.get_nowait()
            except queue.Empty:
                break
        try:
            self.backend.start()
        except Exception as e:
            self.btn_run.setChecked(False)
            if getattr(self, "_quiet_start", False):    # switching input devices: it says so itself
                self._start_error = e
            else:
                QtWidgets.QMessageBox.critical(self, "Could not start acquisition", repr(e))
            return False
        rate = float(self.backend.actual_rate)
        same = (len(self.ring) and self.ring.n == len(self.cfg.channels)
                and abs(rate - getattr(self, "_ring_rate", rate)) < 1e-6 * rate)
        if keep_trace and same:
            # the new stream's clock starts at 0 again: put it after what's shown, past a short
            # shaded band for the time nothing was acquired (the real time is its label)
            self._end_stretch()                  # (samples that came in after it stopped, too)
            gap0 = float(self.ring.t[-1]) + 1.0 / max(rate, 1e-9)
            off = time.monotonic() - getattr(self, "_stopped_at", time.monotonic())
            shown = min(off, max(GAP_SHOWN_MIN_S, GAP_SHOWN_FRAC * self.window_box.value()))
            self.ring.append(np.array([gap0]), np.full((self.ring.n, 1), np.nan))   # the trace breaks
            if self.calc_ring is not None and len(self.calc_ring):
                self.calc_ring.append(np.array([gap0]), np.full((self.calc_ring.n, 1), np.nan))
            join = gap0 + shown + 1.0 / max(rate, 1e-9)
            self._t_shift = join
            self._stretch_start = join
            if new_run:
                self._saved_until = None
            if self.mode == LIVE:
                self._fit_window()
            if off > 0.05:
                self._mark_boundary(gap0, "gap", (join, off))
            if new_run:
                self._mark_boundary(join, "acq")
            if record and self.btn_rec.isChecked():
                self._start_block()
            return True
        self._t_shift, self._ring_rate = 0.0, rate
        self._stretch_start, self._saved_until = 0.0, None
        self._clear_bounds()
        self._mark_boundary(0.0, "acq")
        self.ring.clear()
        if self.mode == LIVE:                # (looking at a block: Back to Live redraws)
            self._fit_window()
            self._alloc_buffers()            # back to Window's size, if stopped zooming grew it
            self._clear_markers()
        self._recompute_calc_ring()          # empty, filters reset
        self._axis_zero = 0.0
        self._block_on_screen = False
        if record and self.btn_rec.isChecked():   # Record is on: this stretch is a block of its own
            self._start_block()
        return True

    def ensure_acquiring(self, record: bool = True) -> bool:
        """Start, for a stimulus sent from a stimulus page: acquiring (and recording, with
        Record on) as Start does, but without Start's own run-bar stimulus, since the page
        sends its own. True if acquiring."""
        if self.backend.running:
            return True
        if self.mode != LIVE:
            return False
        self._starting_for_stim, self._start_records = True, record
        try:
            self.btn_run.setChecked(True)
        finally:
            self._starting_for_stim, self._start_records = False, True
        return self.backend.running

    def _mark_boundary(self, t: float, kind: str, gap: tuple | None = None) -> None:
        """A bar on the live history at stream time `t` (as the history holds it): where a
        run ("acq") or a trial ("trial") starts, or a trial ends ("trial_end"); or ("gap", with gap = (end, seconds)) a
        shaded band where nothing was acquired. Kept with the history, so it's drawn again
        after Back to Live, and goes when that part of the history does."""
        self._bounds.append((float(t), kind, gap))
        if self.mode == LIVE and self.plots:
            self._draw_boundary(float(t), kind, gap)

    def mark_trial_start(self, stream_t: float, length_s: float | None = None) -> None:
        """A trial (a saved stimulus window) starts at this time of the current stream and
        lasts length_s. What was acquired since the last saved part, before it, wasn't saved."""
        start = stream_t + self._t_shift
        self._mark_boundary(start, "trial")
        if length_s is not None:
            self._mark_unsaved(start)
            self._saved_until = start + length_s
            self._mark_boundary(start + length_s, "trial_end")

    def _mark_unsaved(self, upto: float) -> None:
        """A run saving just its trials' windows: shade what was acquired since the last
        saved part (or this stretch's start) up to `upto` as not saved."""
        frm = max(getattr(self, "_stretch_start", 0.0), getattr(self, "_saved_until", None) or -np.inf)
        if upto - frm > 0.005:
            last = self._bounds[-1] if self._bounds else None
            if last is not None and last[1] == "unsaved" and abs(last[2][0] - frm) < 1e-6:
                self._bounds[-1] = (last[0], "unsaved", (upto, upto - last[0]))   # one band, longer
                if self.mode == LIVE and self.plots:
                    self._redraw_bounds()
            else:
                self._mark_boundary(frm, "unsaved", (upto, upto - frm))
            if getattr(self, "_saved_until", None) is not None:
                self._saved_until = max(self._saved_until, upto)    # accounted for up to here

    def _end_stretch(self) -> None:
        """Acquiring stops (a wait, or Stop): its tail after the last saved trial wasn't saved."""
        if getattr(self, "_saved_until", None) is not None and len(self.ring):
            self._mark_unsaved(float(self.ring.t[-1]))

    def _draw_boundary(self, t: float, kind: str, gap: tuple | None = None) -> None:
        items = []
        if kind in ("gap", "unsaved"):
            end, secs = gap
            if kind == "gap":
                text = f"not acquiring · {secs:.3g} s" if secs < 60 else f"not acquiring · {secs / 60:.3g} min"
                brush, tip = pg.mkBrush(128, 128, 128, 60), f"Nothing was acquired here: {text.split('· ')[1]} (shown shorter)"
            else:                                # acquired, but outside every saved trial
                text = "not saved"
                brush, tip = pg.mkBrush(232, 163, 61, 28), ("Acquired but not saved: only each trial's "
                                                            "window is saved (this is warm-up or tail)")
            for k, p in enumerate(self.plots):
                band = pg.LinearRegionItem((t - self._axis_zero, end - self._axis_zero), movable=False,
                                           brush=brush, pen=pg.mkPen(None))
                band.setToolTip(tip)
                band.setZValue(-10)
                p.addItem(band)
                items.append(band)
                if k == 0:                       # its label, on the top plot
                    lab = pg.InfiniteLine(t - self._axis_zero, angle=90, pen=pg.mkPen(None),
                                          label=text, labelOpts={"position": 0.97, "color": "#8a8f98",
                                                                 "anchors": [(0, 0), (0, 0)]})
                    p.addItem(lab)
                    items.append(lab)
            self._joins.append((t, kind, items, end))
            return
        # thick: easy to see at a glance; a trial's start and end thickest
        pen = pg.mkPen(BOUND_COLORS[kind], width=3 if kind == "acq" else 5)
        tip = {"acq": "A run starts here", "trial_end": "A trial ends here"}.get(kind, "A trial starts here")
        for p in self.plots:
            ln = pg.InfiniteLine(t - self._axis_zero, angle=90, pen=pen)
            ln.setToolTip(tip)
            ln.setZValue(5)
            p.addItem(ln)
            items.append(ln)
        self._joins.append((t, kind, items, None))

    def _remove_bound_items(self) -> None:
        for _, _, items, _ in self._joins:
            for it in items:
                vb = it.getViewBox()
                if vb is not None:
                    vb.removeItem(it)
        self._joins = []

    def _redraw_bounds(self) -> None:
        """The bars and bands again, on new live plots."""
        self._remove_bound_items()
        ring = getattr(self, "ring", None)                # (none yet while the window is built)
        t0 = float(ring.t[0]) if ring is not None and len(ring) else None
        self._bounds = [b for b in self._bounds if t0 is None or b[0] >= t0 - 1e-9]
        for t, kind, gap in sorted(self._bounds, key=lambda b: b[0]):   # (oldest first)
            self._draw_boundary(t, kind, gap)

    def _clear_bounds(self) -> None:
        self._remove_bound_items()
        self._bounds = []

    def _mark_block_start(self) -> None:
        """A recorded block's first sample arrived: a trial bar there (once a block)."""
        rec = self.recorder
        if rec.recording and rec.block_started and getattr(self, "_trial_marked", None) != rec.path:
            self._trial_marked = rec.path
            self._mark_boundary(rec.block_start + self._t_shift, "trial")

    def rest_acquiring(self) -> None:
        """Stop the stream for a while without leaving Start on: a protocol waiting a
        long time between stimuli. Ends the block being recorded (Record stays on, so
        resume_acquiring starts the next)."""
        if self.recorder.recording:
            self._stop_block()
        self.backend.stop()
        self._end_stretch()
        self._stopped_at = time.monotonic()      # for the shaded band where it carries on

    def _simple_windows(self, record_on: bool = False) -> bool:
        """Simple Stimulus is on the run bar, set to save just around each stimulus, and
        Record is on (or `record_on`: whatever Record is)."""
        sp = getattr(self, "simple_page", None)
        return (sp is not None and sp.windows.isChecked() and hasattr(self, "window_saver")
                and self.settings.value("record_stim", "simple") == "simple"
                and (record_on or self.btn_rec.isChecked()))

    def _stop_when_stimuli_end(self) -> None:
        """With the run bar's Stimulus on Simple or Complex, acquiring stops by itself
        STIM_TAIL_S after the last stimulus: once one has been sent and nothing is
        sending or still to come (no protocol, no Repeat)."""
        if (not self.btn_run.isChecked() or not getattr(self, "_stim_sent", False)
                or self.settings.value("record_stim", "simple") == "none"):
            self._stim_idle_since = None
            return
        tp, sp = getattr(self, "trials_page", None), getattr(self, "simple_page", None)
        ws = getattr(self, "window_saver", None)
        if ((self.stim_out is not None and self.stim_out.busy) or self.repeating
                or (tp is not None and tp.running) or (sp is not None and sp.repeating)
                or (ws is not None and ws.pending)):          # (its After isn't recorded yet)
            self._stim_idle_since = None
            return
        now = time.monotonic()
        if getattr(self, "_stim_idle_since", None) is None:
            self._stim_idle_since = now
        elif now - self._stim_idle_since >= STIM_TAIL_S:
            self.btn_run.setChecked(False)
            self.statusBar().showMessage("Stimulus finished: stopped acquiring", 10000)

    def _stim_on_start(self) -> None:
        """Start runs the stimulus picked on the run bar: None sends nothing, Simple
        repeats Simple Stimulus's stimulus, Complex runs Complex Stimulus's trials."""
        if getattr(self, "_starting_for_stim", False):
            return
        mode = self.settings.value("record_stim", "simple")
        if mode == "none" and hasattr(self, "dock_timed"):
            self.dock_timed.begin()             # Acquire for, Repeats, Wait
            return
        if self.stim_out is None:
            return
        if mode == "simple" and hasattr(self, "simple_page"):
            self.simple_page.start_repeat()
        elif mode == "complex" and hasattr(self, "dock_design"):
            self.dock_design._run()

    def _toggle_rec(self, on: bool):
        """Record is an on/off switch. Turned on while acquiring, a block starts now;
        on while stopped, Start begins recording straight away. Off ends the block."""
        tp = getattr(self, "trials_page", None)
        if tp is not None and tp.running:    # a protocol saves each stimulus's window instead
            tp.set_saving_windows(on)
        elif self._simple_windows(record_on=True):   # so does Simple Stimulus, if it's set to
            if on and self.btn_run.isChecked() and self.backend.running:
                sp = self.simple_page
                self.window_saver.start(sp.before.value(), sp.after.value())
            elif not on:
                self.window_saver.stop()
        elif on and self.btn_run.isChecked() and not self.recorder.recording:
            self._start_block()
        elif not on and self.recorder.recording:
            self._stop_block()
        self._show_rec_armed()

    def _show_rec_armed(self) -> None:
        """Record's look while it isn't counting a block's time: on (armed) or off."""
        if self.recorder.recording:
            return
        on = self.btn_rec.isChecked()
        self.btn_rec.setText("Record: On" if on else "Record: Off")
        if not on:
            self.btn_rec.setToolTip("Turn on to record: now while acquiring, or as soon as you press Start")
        elif self.btn_run.isChecked():
            self.btn_rec.setToolTip("Click to turn recording off")
        else:
            self.btn_rec.setToolTip("Start will record too. Click to turn recording off.")
        self.btn_rec.setIcon(style.dot_icon(style.RECORDING))
        self._fit_toolbar()

    def _ensure_session_dir(self) -> Path:
        """The live session's folder, made now if it has none yet."""
        if self.session_dir is None:
            # Untitled until saved; recording still goes straight to disk.
            self.session_dir = self._unsaved_dir() / datetime.now().strftime("session_%Y%m%d_%H%M%S")
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.session_saved = False
            self.settings.setValue("last_live_session", str(self.session_dir))   # reopened on next start
        return self.session_dir

    def _start_block(self) -> None:
        self._ensure_session_dir()
        path = session.next_block_path(self.session_dir)
        path = self.recorder.start(path)
        if self.trial_label:
            self.recorder.set_attr("label", self.trial_label)
        if self.camera_panel.start_block(path, time.monotonic()):
            self.statusBar().showMessage(f"Recording {path.name} with video", 5000)
        # Time 0 = this block's first sample. Until it arrives, use the latest
        # sample; _refresh corrects it (by at most one read) once it's known.
        self._set_axis_zero(float(self.ring.t[-1]) if len(self.ring) else self._t_shift)
        self._block_on_screen = True
        self.blocks.append(path)
        self._update_title()
        self.statusBar().showMessage(f"Recording {path.name} in {self.session_dir}")
        self._set_rec_text(0.0)
        self.btn_rec.setIcon(style.dot_icon("#ffffff", square=True))
        self._lock_channels(True)
        self._set_comment_box(True)
        self._block_changed(started=True)

    def _stop_block(self) -> None:
        empty = self.recorder.samples_written == 0
        start, end = self.recorder.block_start + self._t_shift, self.recorder.last_t + self._t_shift
        self.recorder.stop()
        path = self.recorder.path
        if empty and path is not None and path in self.blocks:   # no samples came: not a trial
            self.blocks.remove(path)
            try:
                path.unlink()
            except OSError:
                pass
        self.camera_panel.stop_block()
        self._lock_channels(False)
        if not empty:
            self.statusBar().showMessage(f"Saved {self.recorder.path.name}", 10000)
            self._mark_boundary(end, "trial_end")
        self._set_comment_box(False)
        self.analysis_page.source_changed()          # a new block to analyze
        self._block_changed(started=False)
        if not empty and path is not None and trialfile.stimuli_count(path) > 1:
            # Repeat, Raise, Complex Stimulus's Repeat: a trial per stimulus, by itself
            self.split_in_background([path], {path: start})

    def _block_changed(self, started: bool) -> None:
        self._update_menu_state()           # so Cmd+S / Export match without opening File first
        self._fill_recorded()
        # Starting a block keeps the previous block's comment lines: its data is
        # still on screen (now at negative times), and they scroll off with it.
        self.refresh_comments(redraw_markers=not started)
        self._fit_toolbar()

    def _set_rec_text(self, secs: float) -> None:
        clock = f"{int(secs) // 60}:{int(secs) % 60:02d}"
        self.btn_rec.setText(clock if self._short_rec else f"Stop recording · {clock}")
        self.btn_rec.setToolTip("Stop recording" if self._short_rec else "")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit_toolbar()

    def _fit_toolbar(self) -> None:
        """Record's text is always the full "Stop recording · 0:12": it's on the run bar
        under the channels now, with room to spare."""
        self._short_rec = False



    def _lock_channels(self, locked: bool):
        for st in self.strips:
            st.set_locked(locked)
        self.btn_add_ch.setEnabled(not locked)
        self.rate_box.setEnabled(not locked)
        self.wiring_menu.setEnabled(not locked and self.mode == LIVE)

    # -- click and highlight (as in LabChart) ------------------------------------------------
    def _readout(self, x0: float, x1: float | None, finished: bool) -> None:
        """A click at x0 (x1 None): a cursor on every channel, each showing its value there.
        A drag from x0 to x1: that stretch highlighted on every channel, each showing its
        average over it. Esc clears it."""
        if x1 is not None and abs(x1 - x0) < 1e-9:
            return
        self._readout_at = (x0, x1)
        if not self._readout_items or (x1 is None) != isinstance(self._readout_items[0][0], pg.InfiniteLine):
            self.clear_readout()
            self._readout_at = (x0, x1)
            for p in self.plots:
                if x1 is None:
                    mark = pg.InfiniteLine(x0, angle=90, pen=pg.mkPen("#6e7681", width=1.5))
                else:
                    mark = pg.LinearRegionItem((x0, x1), movable=False, brush=pg.mkBrush(70, 130, 220, 45),
                                               pen=pg.mkPen(70, 130, 220, 140))
                mark.setZValue(4)
                p.addItem(mark, ignoreBounds=True)
                label = pg.TextItem(anchor=(1, 0), color="#5c6370", fill=pg.mkBrush(255, 255, 255, 200))
                label.setParentItem(p.vb)          # fixed in the plot's top-right corner
                p.vb.sigResized.connect(lambda vb=p.vb, lab=label: lab.setPos(vb.width() - 4, 2))
                label.setPos(p.vb.width() - 4, 2)
                self._readout_items.append((mark, label, p))
        for mark, label, p in self._readout_items:
            if x1 is None:
                mark.setValue(x0)
            else:
                mark.setRegion((x0, x1))
        self._update_readout()

    def _update_readout(self) -> None:
        if self._readout_at is None:
            return
        x0, x1 = self._readout_at
        for k, (mark, label, p) in enumerate(self._readout_items):
            curve = self.curves[k] if k < len(self.curves) else None
            unit = self.plot_units[k] if k < len(self.plot_units) else ""
            x, y = curve.getOriginalDataset() if curve is not None else (None, None)
            fmt = lambda v: format_y(float(v), unit, unit in _SI_BASE)
            text = ""
            if x is not None and len(x):
                if x1 is None:
                    i = int(np.clip(np.searchsorted(x, x0), 1, len(x) - 1))
                    i = i - 1 if abs(x[i - 1] - x0) <= abs(x[i] - x0) else i
                    if abs(x[i] - x0) <= 2 * (x[-1] - x[0]) / max(1, len(x) - 1) and np.isfinite(y[i]):
                        text = f"{_time(x[i])} · {fmt(y[i])}"
                else:
                    sel = (x >= x0) & (x <= x1)
                    if sel.any() and np.isfinite(y[sel]).any():
                        text = (f"{_time(x0)}–{_time(x1)} · Δ {_time(x1 - x0)} · "
                                f"mean {fmt(np.nanmean(y[sel]))}")
            label.setText(text or ("no data here" if x1 is None else "no data in the highlight"))

    def clear_readout(self) -> bool:
        """Take the cursor or highlight away. True if there was one."""
        had = bool(self._readout_items)
        for mark, label, p in self._readout_items:
            try:
                p.removeItem(mark)
                label.setParentItem(None)
                if label.scene() is not None:
                    label.scene().removeItem(label)
            except RuntimeError:                  # its plot already went
                pass
        self._readout_items, self._readout_at = [], None
        return had

    def _redraw_live(self):
        if self.buf_t.size and self.buf_y.shape[0] == len(self.cfg.channels) <= len(self.curves):
            for ch, curve, y in zip(self.cfg.channels, self.curves, self.buf_y):
                curve.setData(self.buf_t - self._axis_zero, ch.to_units(y))
            self._draw_calc_live()
            self._curves_updated()

    def _set_axis_zero(self, zero: float) -> None:
        """Change which stream time the live axis shows as 0; comment lines move with it."""
        delta = self._axis_zero - zero
        if delta:
            for i, (t, lines) in enumerate(self.markers):
                for ln in lines:
                    ln.setValue(ln.value() + delta)
                self.markers[i] = (t + delta, lines)
            for t, _, items, end in self._joins:  # (bars keep history times: only redrawn)
                for it in items:
                    if isinstance(it, pg.LinearRegionItem):
                        it.setRegion((t - zero, end - zero))
                    else:
                        it.setValue(t - zero)
        self._axis_zero = zero

    def _refresh(self):
        if self.backend.error is not None:
            err, self.backend.error = self.backend.error, None
            self.btn_run.setChecked(False)
            QtWidgets.QMessageBox.critical(self, "Acquisition stopped", repr(err))
            return
        if self.stim_out is not None and self.stim_out.error is not None:
            err, self.stim_out.error = self.stim_out.error, None
            self.set_repeat(False)
            self._stim_message(f"Stimulus failed: {err}", error=True)
        self._update_stim_chip()
        self._stop_when_stimuli_end()
        ts, ys = [], []
        while True:
            try:
                c = self.display_q.get_nowait()
            except queue.Empty:
                break
            ts.append(c.t + self._t_shift)     # (shifted: a run's stretches end to end)
            ys.append(c.data)
        if not ts:
            return
        if self.mode != LIVE:                    # looking at a trial: keep the history going
            for t, y in zip(ts, ys):
                self.ring.append(t, y)
            self._mark_block_start()
            return
        for t, y in zip(ts, ys):                 # only the new samples are written
            self.ring.append(t, y)
            calc = self._calc_chunk(y) if self._calcs and self.calc_ring is not None else None
            if calc is not None:
                self.calc_ring.append(t, calc)
            if self._monitor is not None and self._monitor.running:
                self._feed_monitor(y, calc)
        if self.recorder.recording and self.recorder.block_started:
            self._set_axis_zero(self.recorder.block_start + self._t_shift)   # exact block start
            self._mark_block_start()
        if not self.plots:
            return
        buf_t, buf_y = self.buf_t, self.buf_y
        t_min = buf_t[0] - self._axis_zero
        win = self.window_box.value()
        i0 = int(np.searchsorted(buf_t, buf_t[-1] - win - 1.0 / self.cfg.sample_rate))
        buf_t, buf_y = buf_t[i0:], buf_y[:, i0:]   # only the Window: the buffer may hold more
        x = buf_t - self._axis_zero
        t_end = x[-1]
        # the range first, so each TraceCurve groups its samples once for this frame (the views'
        # own limits are off while acquiring: they would lag a frame behind the newest data, and
        # fight a held voltage range). Always a whole Window wide, never starting before 0 s:
        # until the Window fills, the trace grows from the left; after that the newest
        # sample stays at the right edge.
        start = max(t_end - win, min(0.0, t_min))
        self.plots[0].setXRange(start, start + win, padding=0)
        for ch, curve, y in zip(self.cfg.channels, self.curves, buf_y):
            curve.setData(x, ch.to_units(y))
        self._draw_calc_live()
        self._curves_updated()
        if self._joins and self._joins[0][0] < float(self.ring.t[0]) - 1e-9:
            self._redraw_bounds()                # bars whose part of the history is gone
        # drop markers that scrolled off (out of the buffer)
        while self.markers and self.markers[0][0] < t_min:
            _, lines = self.markers.pop(0)
            for p, ln in zip(self.plots, lines):
                p.removeItem(ln)
        rate = self.backend.actual_rate
        lag = self.display_q.qsize()
        msg = f"rate {whole(round(rate, 1))}/ch" + ("" if self.backend.hardware_timed else " (software-timed!)")
        if self.recorder.recording and self.backend.hardware_timed:
            secs = self.recorder.samples_written / max(rate, 1e-9)
            msg += f" | recorded {secs:,.1f} s"
            self._set_rec_text(secs)
        if lag > 5:
            msg += f" | display lagging ({lag} chunks queued)"
        self.status.setText(msg)
