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
from .channels import CalcStrip, ChannelRowWidget, ChannelStrip, ColorButton
from .config import Channel, valid_inputs
from .plotzoom import MultiAxis, TraceCurve, light_grid
from .stim import Stimulus
from .ringbuffer import RingBuffer
from . import colors
from .mainparts import LIVE, REVIEW, WINDOW_MAX, _SI_BASE, _TraceViewBox
from .units import whole


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
        self.plot_widgets, self.curve_colors, self.curve_widths = [], [], []
        self.row_widgets = []
        self._unfitted.clear()
        self._overlay_items = []                 # they went with the plots
        self._mirrors = []
        self._multi = None

    def _new_plot(self, name: str, unit: str, color: str, width: float = 1.0) -> pg.PlotWidget:
        pw = pg.PlotWidget(viewBox=_TraceViewBox(self._zoom_time, self._pan_time, self._zoom_to))
        pw.getPlotItem().vb.clamp_to_data = not self.btn_run.isChecked()
        p = pw.getPlotItem()
        self.plot_widgets.append(pw)
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

    def set_auto_scale_y(self, on: bool) -> None:
        """View › Auto-scale voltage. Turning it off holds every plot's current range."""
        self.auto_scale_y = bool(on)
        self.settings.setValue("auto_scale_y", self.auto_scale_y)
        self.act_auto_y.blockSignals(True)
        self.act_auto_y.setChecked(self.auto_scale_y)
        self.act_auto_y.blockSignals(False)
        self._unfitted.clear()
        for p in self.plots:
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
        """With Overlay channels on, put a copy of each other channel's curve in the first
        plot (in its color) and hide the other plots: live, their rows shrink to just the
        channel's controls; in review, their rows go."""
        self._mirrors = []
        self._multi = None
        if not self.btn_overlay_ch.isChecked() or len(self.plots) < 2:
            return
        first = self.plots[0]
        legend = first.addLegend(offset=(10, 10))
        legend.clear()
        names = self._plot_names
        separate = self.act_separate_y.isChecked()
        multi = MultiAxis(first, separate=separate)
        multi.set_auto(self.auto_scale_y)
        for k, (pw, curve) in enumerate(zip(self.plot_widgets, self.curves)):
            name = names[k] if k < len(names) else f"Channel {k + 1}"
            ax = pw.getPlotItem().getAxis("left")
            unit = getattr(ax, "labelUnits", "") or ""
            color = curve.opts["pen"].color() if hasattr(curve.opts["pen"], "color") else None
            if k == 0:
                multi.add(curve, name, unit, color)       # already in the first plot
                legend.addItem(curve, name)
                continue
            mirror = TraceCurve()
            mirror.setPen(curve.opts["pen"])
            multi.add(mirror, name, unit, color)
            legend.addItem(mirror, name)
            self._mirrors.append((k, mirror))
            row = pw.parentWidget()
            if self.mode == LIVE and isinstance(row, ChannelRowWidget):
                pw.hide()                                # keep the channel's controls, like an "off" row
                row.setFixedHeight(row.strip.sizeHint().height())
            elif row is not None:
                row.hide()
        if not separate:
            first.setLabel("left", "all channels")
            first.getAxis("left").enableAutoSIPrefix(False)
        self._multi = multi
        self._multi_unfitted = not self.auto_scale_y
        first.getAxis("bottom").setStyle(showValues=True)      # the one plot showing: its time axis
        first.setLabel("bottom", "time", units="s")

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
            if live:
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
        self.calc_ring = RingBuffer(len(self._calcs), ring.cap if ring is not None else 2)
        if self._calcs:
            self._chain.reset()
        if self._calcs and ring is not None and len(ring):
            self.calc_ring.append(self.ring.t, self._calc_chunk(self.ring.y))

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
        elif dlg.deleted:
            del calcs[k]
        else:
            calcs[k] = dlg.calculation()
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
            else:
                content = QtWidgets.QLabel("off")
                content.setStyleSheet("color: gray; padding-left: 8px")
            row_w = ChannelRowWidget(strip, content)
            row_w.set_compact(self._compact_strips)
            self.row_widgets.append(row_w)
            if r.ch.enabled:
                self.plot_layout.addWidget(row_w, 1)
            else:
                row_w.setFixedHeight(strip.sizeHint().height())
                self.plot_layout.addWidget(row_w, 0)
        self._add_calc_rows([r.ch.name for r in self.rows if r.ch.enabled], self.cfg.sample_rate, live=True)
        self._recompute_calc_ring()
        self._finish_plots()
        self.btn_add_ch.setEnabled(not locked)
        self.rate_box.setEnabled(not locked)
        if hasattr(self, "trials_page"):
            self.trials_page.sync()

    def _build_review_plots(self, names: list[str], units: list[str], colors: list[str]):
        self._review_names = list(names)
        """Plots for a saved session: one per recorded channel, with only a color bar."""
        self._clear_plot_area()
        self.review_color_btns = []
        for i, (name, unit, color) in enumerate(zip(names, units, colors)):
            row_w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row_w)
            h.setContentsMargins(0, 0, 0, 0)
            btn = ColorButton(color, lambda c, i=i: self.set_review_color(i, c),
                              tooltip="Change this channel's color in every trial of this session")
            self.review_color_btns.append(btn)
            h.addWidget(btn, 0)
            widths = {r.ch.name: r.ch.width for r in self.rows}
            pw = self._new_plot(name, unit, color, widths.get(name, 1.0))
            pw.setToolTip("Double-click to add a comment")
            pw.scene().sigMouseClicked.connect(lambda ev, pw=pw: self._plot_clicked(pw, ev))
            h.addWidget(pw, 1)
            self.plot_layout.addWidget(row_w, 1)
        fs = (self.review_block or {}).get("sample_rate", self.cfg.sample_rate)
        self._add_calc_rows(list(names), fs, live=False)
        self._finish_plots()

    def _alloc_buffers(self, trim: bool = True):
        """Size the live display's ring buffer to Window. trim=False (stopped) only
        grows it, so zooming in and back out doesn't lose the trace on screen."""
        n = len(self.cfg.channels)
        self.cap = max(2, int(self.window_box.value() * self.cfg.sample_rate))
        ring = getattr(self, "ring", None)
        if ring is None or ring.n != n:
            self.ring = RingBuffer(n, self.cap)
        else:
            ring.resize(self.cap if trim else max(self.cap, len(ring)))
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

    def _window_changed(self):
        if not self._fitting_window:
            self._window_wanted = None           # you picked it: that's the one to keep
        if self.mode == REVIEW:
            self._update_scroll_range()
            self._scrolled(self.scroll.value())
        elif self.btn_run.isChecked():
            self._alloc_buffers()
        else:
            # Stopped: keep the last trace (Start clears it anyway) so zooming in
            # and back out doesn't lose it, and redraw since no refresh will.
            self._alloc_buffers(trim=False)
            self._show_stopped()

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
                QtWidgets.QMessageBox.critical(self, "Could not start acquisition", repr(e))
                return
            self.ring.clear()
            self._fit_window()
            self._alloc_buffers()                # back to Window's size, if stopped zooming grew it
            self._recompute_calc_ring()          # empty, filters reset
            self._clear_markers()
            self._axis_zero = 0.0
            self._block_on_screen = False
            self.btn_run.setText("Stop")
            self.btn_rec.setEnabled(True)
            self.timer.start()
            if self.stim_on_start == "once":
                self.fire_stimulus()
            elif self.stim_on_start == "repeat":
                self.set_repeat(True)
        else:
            if self.btn_rec.isChecked():
                self.btn_rec.setChecked(False)
            self.stop_stimulus()
            self.backend.stop()
            self.timer.stop()
            self.btn_run.setText("Start")
            self.btn_rec.setEnabled(False)
            self._fit_window()

    def _toggle_rec(self, on: bool):
        if on:
            if self.session_dir is None:
                # Untitled until saved; recording still goes straight to disk.
                self.session_dir = self._unsaved_dir() / datetime.now().strftime("session_%Y%m%d_%H%M%S")
                self.session_dir.mkdir(parents=True, exist_ok=True)
                self.session_saved = False
            path = session.next_block_path(self.session_dir)
            path = self.recorder.start(path)
            if self.camera_panel.start_block(path, time.monotonic()):
                self.statusBar().showMessage(f"Recording {path.name} with video", 5000)
            # Time 0 = this block's first sample. Until it arrives, use the latest
            # sample; _refresh corrects it (by at most one read) once it's known.
            self._set_axis_zero(self.recorder.last_t)
            self._block_on_screen = True
            self.blocks.append(path)
            self._update_title()
            self.statusBar().showMessage(f"Recording {path.name} in {self.session_dir}")
            self._set_rec_text(0.0)
            self.btn_rec.setIcon(style.dot_icon("#ffffff", square=True))
            self._lock_channels(True)
            self._set_comment_box(True)
            self._fill_recorded()
        else:
            self.recorder.stop()
            self.camera_panel.stop_block()
            self._lock_channels(False)
            self.statusBar().showMessage(f"Saved {self.recorder.path.name}", 10000)
            self.btn_rec.setText("Record")
            self.btn_rec.setToolTip("")
            self.btn_rec.setIcon(style.dot_icon(style.RECORDING))
            self._set_comment_box(False)
            self.analysis_page.source_changed()          # a new block to analyze
        self._fill_recorded()
        # Starting a block keeps the previous block's comment lines: its data is
        # still on screen (now at negative times), and they scroll off with it.
        self.refresh_comments(redraw_markers=not on)
        self._fit_toolbar()

    def _set_rec_text(self, secs: float) -> None:
        clock = f"{int(secs) // 60}:{int(secs) % 60:02d}"
        self.btn_rec.setText(clock if self._short_rec else f"Stop recording · {clock}")
        self.btn_rec.setToolTip("Stop recording" if self._short_rec else "")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit_toolbar()

    def _fit_toolbar(self) -> None:
        """Keep every control on the toolbar when the window is narrow (or the
        system font is wide, as on macOS): first drop the "Sampling rate" label,
        then "Window", then shorten "Stop recording · 0:12" to "0:12". Each comes
        back as soon as there's room. Otherwise Qt hides whatever doesn't fit in
        a » menu, which on a laptop was the light/dark and split buttons."""
        tb = getattr(self, "toolbar", None)
        if tb is None or not tb.isVisible():    # unshown: its width means nothing yet;
            return                              # showing it sends a resizeEvent
        lay = tb.layout()
        labels = [(self._act_lbl_rate, self.lbl_rate), (self._act_lbl_window, self.lbl_window)]
        label_acts = {act for act, _ in labels}
        others = [tb.widgetForAction(a) for a in tb.actions() if a.isVisible() and a not in label_acts]
        others = [w for w in others if w is not None]
        wanted = [(act, lbl) for act, lbl in labels if self._label_wanted(act)]
        n = len(others) + len(wanted)
        m, lm = tb.contentsMargins(), lay.contentsMargins()
        # Width with every wanted label shown and the full Record text.
        full = (sum(w.sizeHint().width() for w in others) + sum(lbl.sizeHint().width() for _, lbl in wanted)
                + lay.spacing() * max(0, n - 1) + m.left() + m.right() + lm.left() + lm.right() + 12)
        recording = self.btn_rec.isChecked()
        fm = self.btn_rec.fontMetrics()
        rec_saving = fm.horizontalAdvance("Stop recording · ") if recording else 0
        if self._short_rec:
            full += rec_saving                  # the button is short right now
        over = full - tb.width()
        for act, lbl in wanted:                 # least important first
            show = over <= 0
            if not show:
                over -= lbl.sizeHint().width() + lay.spacing()
            act.setVisible(show)
        short = recording and over > 0
        if short != self._short_rec:
            self._short_rec = short
            if recording:
                self._set_rec_text(self.recorder.samples_written / max(self.backend.actual_rate, 1e-9))

    def _label_wanted(self, act) -> bool:
        """Whether this label's group is showing at all (live mode; Acquisition visible)."""
        if act is self._act_lbl_rate:
            return self.mode == LIVE
        return hasattr(self, "tabs") and self.tabs.is_visible("acq")


    def _lock_channels(self, locked: bool):
        for st in self.strips:
            st.set_locked(locked)
        self.btn_add_ch.setEnabled(not locked)
        self.rate_box.setEnabled(not locked)
        self.wiring_menu.setEnabled(not locked and self.mode == LIVE)

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
        ts, ys = [], []
        while True:
            try:
                c = self.display_q.get_nowait()
            except queue.Empty:
                break
            ts.append(c.t)
            ys.append(c.data)
        if not ts or self.mode != LIVE:
            return
        for t, y in zip(ts, ys):                 # only the new samples are written
            self.ring.append(t, y)
            calc = self._calc_chunk(y) if self._calcs and self.calc_ring is not None else None
            if calc is not None:
                self.calc_ring.append(t, calc)
            if self._monitor is not None and self._monitor.running:
                self._feed_monitor(y, calc)
        if self.recorder.recording and self.recorder.block_started:
            self._set_axis_zero(self.recorder.block_start)   # exact block start
        if not self.plots:
            return
        x = self.buf_t - self._axis_zero
        t_end = x[-1]
        # the range first, so each TraceCurve groups its samples once for this frame; it starts
        # no earlier than the oldest sample (the views' own limits are off while acquiring: they
        # would lag a frame behind the newest data, and fight a held voltage range). Always a whole
        # Window wide: the data fills it from the left, then the window moves along with the data.
        win = self.window_box.value()
        start = max(t_end - win, x[0])
        self.plots[0].setXRange(start, start + win, padding=0)
        for ch, curve, y in zip(self.cfg.channels, self.curves, self.buf_y):
            curve.setData(x, ch.to_units(y))
        self._draw_calc_live()
        self._curves_updated()
        # drop markers that scrolled off
        t_min = x[0]
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
