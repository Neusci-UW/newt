"""The main window's review of recorded trials: the Trials browser and its groups (rename,
move, reset), overlaying trials, and showing one trial."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from .plotzoom import OverlayCurve
from . import groupsfile
from .trialsbrowser import BACK as BACK_ITEM, LIVE as LIVE_ITEM
from .mainparts import LIVE, REVIEW
from .units import whole


class ReviewMixin:
    # -- the Trials browser: groups of trials (trialsbrowser shows them) ----------------------
    def _panel_folder(self) -> Path | None:
        return self.review_dir if self.mode == REVIEW else self.session_dir

    def _panel_paths(self) -> list[Path]:
        return list(self.review_blocks) if self.mode == REVIEW else list(self.blocks)

    def _panel_channels(self) -> list[str]:
        if self.mode == REVIEW:
            return list(getattr(self, "_review_names", None) or [])
        return [c.name for c in self.cfg.channels]

    def _stim_key(self) -> str | None:
        folder = self._panel_folder()
        return f"stim_channel/{Path(folder).resolve()}" if folder else None

    def set_stim_channel(self, name: str | None) -> None:
        """The session's stimulus channel ("" or None: use the notes). Shared with Analysis."""
        key = self._stim_key()
        if key is None:
            return
        self.settings.setValue(key, name or "")
        self._fill_recorded()
        self.analysis_page.source_changed()

    def _page_state(self):
        """(pages [(name, block indices)], stimulus groups, saved changes) for the panel."""
        paths, folder = self._panel_paths(), self._panel_folder()
        auto = self.analysis_page.stimulus_groups_of(paths, folder) if paths else []
        d = groupsfile.load(folder)
        shown = groupsfile.apply(auto, [p.stem for p in paths], d)
        return self.analysis_page.arranged(shown, paths, self.analysis_page.stimulus_source(paths, folder), d), auto, d

    def _fill_recorded(self) -> None:
        """Show the session in the Blocks panel: Live (or Back to Live), then its blocks
        on their pages, each named after its page: "Baseline 1", "Baseline 2"..."""
        names, key = self._panel_channels(), self._stim_key()
        saved = self.settings.value(key) if key else None
        current = saved if saved is not None else next((n for n in names if "stim" in n.lower()), "")
        self.blocks_panel.set_stim_channels(names, current, key is not None)
        paths = self._panel_paths()
        pages, auto, d = self._page_state() if paths else ([], [], groupsfile.load(None))
        labels = {}
        for i, p in enumerate(paths):
            try:
                labels[i] = (p.stem, f"{self.store.info(p)['duration_s']:.1f} s")
            except Exception:
                labels[i] = (p.stem, "?")
        top = LIVE_ITEM if self.mode == LIVE else (BACK_ITEM if self._live_state is not None else None)
        names_of = [name for name, _ in pages]
        colors = [self.analysis_page.group_color(g, names_of, d) for g in range(max(1, len(pages)))]
        self.blocks_panel.populate(top, pages, colors, labels, lambda i: groupsfile.auto_page(auto, i, d),
                                   bool(d["renamed"] or d["moved"]), self.recorder.recording)
        self._mark_block_list()

    def _mark_block_list(self) -> None:
        """Select what's on screen: Live, or the block being reviewed."""
        want = LIVE_ITEM if self.mode == LIVE else ("block", self.block_combo.currentIndex())
        self.blocks_panel.mark(want, self._overlay_sel)

    def _activate(self, data) -> None:
        """What clicking a Blocks panel entry does: Live, Back to Live, or show a block."""
        if data in (LIVE_ITEM, BACK_ITEM):
            if self.mode == REVIEW:
                self.leave_review()
        elif isinstance(data, tuple) and data[0] == "block":
            if self.mode == LIVE:
                self.browse_recorded(int(data[1]))
            else:
                self.block_combo.setCurrentIndex(int(data[1]))

    def overlay_blocks(self, blocks: list[int]) -> None:
        """Overlay these blocks on the plots, each from its start and colored by page,
        with the block shown on top. Fewer than two: no overlay. From live mode, this
        first opens the session's blocks (as clicking one does)."""
        self._overlay_sel = list(blocks) if len(blocks) >= 2 else []
        if self._overlay_sel and self.mode == LIVE:
            self.browse_recorded(self._overlay_sel[0])
            return                               # browsing shows the block, which draws the overlay
        if self.mode == REVIEW and self._overlay_sel and self.block_combo.currentIndex() not in self._overlay_sel:
            self.block_combo.setCurrentIndex(self._overlay_sel[0])
        else:
            self._draw_overlay()

    def _draw_overlay(self) -> None:
        for plot, item in self._overlay_items:
            plot.removeItem(item)
        self._overlay_items = []
        if self.mode != REVIEW or len(self._overlay_sel) < 2 or not self.plots:
            return
        names = list(getattr(self, "_review_names", None) or [])
        pages, _, _ = self._page_state()
        page_of = {i: g for g, (_, blocks) in enumerate(pages) for i in blocks}
        by_page: dict[int, list[dict]] = {}
        for i in self._overlay_sel:
            if 0 <= i < len(self.review_blocks):
                try:
                    blk = self.store.read(self.review_blocks[i])
                except Exception:
                    continue
                by_page.setdefault(page_of.get(i, 0), []).append(blk)
        fs = (self.review_block or {}).get("sample_rate")
        for g, blks in by_page.items():
            color = QtGui.QColor(self.analysis_page.group_color(g))
            color.setAlpha(110)
            for plot, name in zip(self.plots, names):
                if self.btn_overlay_ch.isChecked():
                    plot = self.plots[0]                 # channels overlaid: everything in one plot
                rows = [b["data"][b["channel_names"].index(name)] for b in blks
                        if name in b["channel_names"] and b["sample_rate"] == fs]
                if not rows:
                    continue
                Y = np.full((len(rows), max(len(r) for r in rows)), np.nan)
                for r, y in enumerate(rows):
                    Y[r, :len(y)] = y
                item = OverlayCurve()
                item.mode = "peak"                   # spikes keep their height
                item.setPen(pg.mkPen(color, width=1))
                item.setZValue(-5)                   # the block itself stays on top
                plot.addItem(item)
                item.set_traces(Y, fs)
                self._overlay_items.append((plot, item))
        self.statusBar().showMessage(f"Overlaying {len(self._overlay_sel)} trials (Cmd/Shift-click in "
                                     "Trials to change; click one trial to stop)", 8000)

    def _rename_dialog(self, old: str) -> None:
        new, ok = QtWidgets.QInputDialog.getText(self, "Rename group", "Name for this group:", text=old)
        if ok:
            self.rename_page(old, new)

    def rename_page(self, old: str, new: str) -> None:
        """Rename a page of the Blocks panel; its blocks take the name."""
        folder = self._panel_folder()
        if folder is None:
            return
        _, auto, d = self._page_state()
        groupsfile.save(folder, groupsfile.rename(auto, d, old, new))
        self._fill_recorded()
        self.analysis_page.source_changed()

    def move_block(self, i: int, page: str) -> None:
        """Put block i on page `page` (existing or new); it takes that page's name."""
        folder = self._panel_folder()
        if folder is None:
            return
        paths = self._panel_paths()
        _, auto, d = self._page_state()
        groupsfile.save(folder, groupsfile.move(auto, [p.stem for p in paths], d, i, page))
        self._fill_recorded()
        self.analysis_page.source_changed()

    def _new_page_dialog(self, i: int) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New group", "Name for the new group:")
        if ok and name.strip():
            self.move_block(i, name)

    def _reset_pages_dialog(self) -> None:
        if QtWidgets.QMessageBox.question(
                self, "Reset groups", "Forget the group names you gave and the trials you moved, and sort "
                "the trials into groups again from the stimulus channel?") == QtWidgets.QMessageBox.StandardButton.Yes:
            self.reset_pages()

    def reset_pages(self) -> None:
        """Back to the automatic pages: no names given, no blocks moved."""
        folder = self._panel_folder()
        if folder is None:
            return
        groupsfile.save(folder, {**groupsfile.load(folder), "renamed": {}, "moved": {}})
        self._fill_recorded()
        self.analysis_page.source_changed()

    def browse_recorded(self, i: int) -> None:
        """Show block i of this live session in review. Back to Live returns to the
        session (it isn't closed), so the next Record adds to it."""
        if self.recorder.recording or not (0 <= i < len(self.blocks)) or self.session_dir is None:
            return
        state = (self.session_dir, list(self.blocks), self.session_saved, self._exported_n,
                 self._live_comments_dirty, self._recolor_policy)
        overlay = self._overlay_sel
        self.open_session(self.session_dir, remember=False)
        self._live_state, self._overlay_sel = state, overlay
        self._fill_recorded()                     # now with Back to Live
        self.block_combo.setCurrentIndex(i)
        self._draw_overlay()                      # also when block i was already showing

    # ======================================================================
    # Review mode
    # ======================================================================
    def _show_review_block(self, index: int):
        if self.mode != REVIEW or not (0 <= index < len(self.review_blocks)):
            return
        try:
            blk = self.store.read(self.review_blocks[index])
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Could not read trial", repr(e))
            return
        self.review_block = blk
        names = getattr(self, "_review_names", None)
        if self.strips or names != blk["channel_names"] or \
                len(self.plots) != len(blk["channel_names"]) + len(self.cfg.calculations):
            self._build_review_plots(blk["channel_names"], blk["units"], blk["colors"])
        for k, (btn, color) in enumerate(zip(self.review_color_btns, blk["colors"])):
            if k < len(self.curves):
                self._set_curve_color(k, color)
            btn.set_color(color)
        for curve, y in zip(self.curves, blk["data"]):
            curve.setData(blk["t"], y)
        # calculated channels: the whole block at once (zero-phase), once per file
        n, env = len(blk["channel_names"]), dict(zip(blk["channel_names"], blk["data"]))
        if self._calcs:
            ys = self.store.memo(self.review_blocks[index], ("calcs", self._chain.key()),
                                 lambda: self._chain.offline(env, blk["sample_rate"]))
            for k, y in enumerate(ys):
                if y is not None and n + k < len(self.curves):
                    self.curves[n + k].setData(blk["t"], y)
        self._curves_updated()
        self._draw_overlay()
        self.refresh_comments()
        self._fit_window()
        self._update_scroll_range()
        self._update_wiring_label()
        self.plots[0].setXRange(0, min(self.window_box.value(), max(blk["duration_s"], 1e-3)), padding=0)
        self._mark_block_list()
        self._show_block_media(index)
        self.status.setText(
            f"Reviewing {self.review_blocks[index].stem} ({index + 1}/{len(self.review_blocks)}) · "
            f"{blk['duration_s']:.2f} s · {whole(blk['sample_rate'])}/ch · "
            f"{len(blk['comments'])} comment(s)")

    def analysis_blocks(self) -> list[Path]:
        """The blocks the Analysis tab works on: those being reviewed, or recorded so far."""
        return list(self.review_blocks) if self.mode == REVIEW else list(self.blocks)

    def analysis_step(self, d: int) -> None:
        """← / →: Analysis's highlighted block, when Analysis is showing."""
        if self.tabs.is_visible("analysis"):
            self.analysis_page.step(d)

    def step_block(self, d: int) -> None:
        """↑ / ↓: the next entry in the Acquisition Blocks panel, in the order it shows
        (Live, then each page's blocks), opening it as a click would. Not while recording."""
        if self.tabs.is_visible("analysis"):          # Analysis: the next trial in the Trials list
            self.analysis_page.step_list(d)
            return
        if not self.tabs.is_visible("acq") or self.recorder.recording:
            return
        role = QtCore.Qt.ItemDataRole.UserRole
        stops = [it.data(0, role) for it in self.blocks_panel.stops()]
        here = LIVE_ITEM if self.mode == LIVE else ("block", self.block_combo.currentIndex())
        k = next((n for n, data in enumerate(stops) if data == here), 0)
        if 0 <= k + d < len(stops):
            self._activate(stops[k + d])
