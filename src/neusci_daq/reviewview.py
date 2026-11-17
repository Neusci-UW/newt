"""The main window's review of recorded trials: the Trials browser and its groups (rename,
move, reset), overlaying trials, and showing one trial."""
from __future__ import annotations

import threading
from pathlib import Path

import warnings

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from .plotzoom import OverlayCurve
from . import groupsfile, session, trialfile, trialsplit
from .trialsbrowser import BACK as BACK_ITEM, LIVE as LIVE_ITEM
from .mainparts import LIVE, REVIEW
from .units import whole



class _SplitRelay(QtCore.QObject):
    """Brings a split worked out on another thread back to the window's thread."""
    done = QtCore.Signal(object)

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

    def default_stim_channel(self, names: list[str]) -> str:
        """The stimulus channel when none was picked for a session: the one the stimulus is
        recorded on (Complex Stimulus › Record on), if the trials have it; else one called Stim."""
        mon = next((r.ch.name for r in self.rows if self.cfg.stim_monitor
                    and r.ch.physical == self.cfg.stim_monitor), None)
        if mon is not None and mon in names:
            return mon
        return next((n for n in names if "stim" in n.lower()), "")

    def _stim_key(self) -> str | None:
        folder = self._panel_folder()
        return f"stim_channel/{Path(folder).resolve()}" if folder else None

    def set_stim_channel(self, name: str | None, from_monitor: bool = False) -> None:
        """The session's stimulus channel ("" or None: use the notes). Shared with Analysis.
        Live, it's also the channel ticked ⚡ Stimulus (what records the stimulator's
        output), each following the other; from_monitor: that box changed it."""
        name = name or ""
        key = self._stim_key()
        if key is not None:
            self.settings.setValue(key, name)
        if not from_monitor and self.mode == LIVE:
            phys = next((r.ch.physical for r in self.rows if r.ch.name == name), "") if name else ""
            locked = self.recorder.recording or (hasattr(self, "trials_page") and self.trials_page.running)
            if phys != (self.cfg.stim_monitor or "") and not locked:
                if phys:
                    if not self.set_stim_monitor(phys):  # an existing channel: no restart
                        self._fill_recorded()            # (couldn't: show what it still is)
                else:
                    self._stim_monitor_moved("")         # None: no channel records it
                return                                   # (which came back here to refresh)
        self._fill_recorded()
        if key is not None:
            self.analysis_page.source_changed()

    def step_trials(self, d: int, extend: bool = False) -> None:
        """Up and Down in the Record page's Trials list: on to the entry before or after (each
        group's Average, then its trials), highlighting just it, opening its group and closing
        the one left. With Shift (extend): on to the next trial, keeping the ones highlighted
        and overlaying them. Down from Live opens the first; Up from the first goes to Live."""
        paths = self._panel_paths()
        if not paths:
            return
        stops = []
        for name, blocks in self._page_state()[0]:
            if len(blocks) >= 2 and not extend:
                stops.append(("avg", name))
            stops += [("block", i) for i in blocks]
        here = None
        if self.mode == REVIEW:
            avg = getattr(self, "_showing_average", None)
            here = ("avg", avg) if avg else ("block", self.block_combo.currentIndex())
        if here is None or here not in stops:
            if d < 0 or self.recorder.recording or here is not None:
                return
            new = stops[0]
        else:
            pos = stops.index(here) + d
            if pos < 0:
                if self._live_state is not None and not extend:   # this session's own trials: Live is above
                    self.leave_review()
                return
            new = stops[min(len(stops) - 1, pos)]
            if new == here:
                return
        if extend and new[0] == "block":
            had = set(self._overlay_sel) or ({here[1]} if here and here[0] == "block" else set())
            picked = sorted(had | {new[1]})
            self._show_entry(new)
            self.blocks_panel.select(picked)
            self.overlay_blocks(picked)
            self.blocks_panel.mark(new, picked)
            return
        self._overlay_sel = []                       # a plain step: just the one it lands on
        self._show_entry(new)
        if self.mode == REVIEW:
            self.blocks_panel.reveal(new, here)

    def _show_entry(self, entry) -> None:
        """Show a Trials list entry: ("block", i) or ("avg", group)."""
        if entry[0] == "avg":
            self.show_group_average(entry[1])
            return
        i = entry[1]
        if self.mode == LIVE:
            self.browse_recorded(i)
        elif self.block_combo.currentIndex() == i:
            self._show_review_block(i)
        else:
            self.block_combo.setCurrentIndex(i)

    def delete_trials(self, paths: list[Path]) -> int:
        """Move these trials' files to the Trash (asked first; they can be put back from
        there). Not the one being recorded. Returns how many went."""
        busy = self.recorder.path if self.recorder.recording else None
        paths = [Path(p) for p in paths if Path(p) != busy and Path(p).exists()]
        if not paths:
            if busy is not None:
                self.statusBar().showMessage("That trial is being recorded: stop recording to delete it", 8000)
            return 0
        names = ", ".join(p.stem for p in paths[:6]) + (f" and {len(paths) - 6} more" if len(paths) > 6 else "")
        n = len(paths)
        ask = QtWidgets.QMessageBox.question(
            self, "Delete trials",
            f"Move {n} trial{'' if n == 1 else 's'} to the Trash?\n\n{names}\n\n"
            "You can put them back from the Trash.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel)
        if ask != QtWidgets.QMessageBox.StandardButton.Yes:
            return 0
        gone, failed = [], []
        for p in paths:
            extras = [x for x in p.parent.glob(p.stem + ".*") if x != p]    # its video, edits...
            if QtCore.QFile.moveToTrash(str(p)):
                gone.append(p)
                for x in extras:
                    QtCore.QFile.moveToTrash(str(x))
                self.store.forget(p)
            else:
                failed.append(p.name)
        self.blocks = [b for b in self.blocks if b not in gone]
        if self.mode == REVIEW and self.review_dir is not None:
            left = [b for b in self.review_blocks if b not in gone]
            if not left:
                self.back_to_live()
            else:
                state, overlay = self._live_state, []
                shown = self.block_combo.currentIndex()
                self.open_session(self.review_dir, remember=False, keep_acquiring=True)
                self._live_state, self._overlay_sel = state, overlay
                self._fill_recorded()
                self.block_combo.setCurrentIndex(min(max(0, shown), len(self.review_blocks) - 1))
        self._update_title()
        self._fill_recorded()
        self.analysis_page.source_changed()
        msg = f"Moved {len(gone)} trial{'' if len(gone) == 1 else 's'} to the Trash"
        if failed:
            msg += f"; couldn't move {', '.join(failed)}"
        self.statusBar().showMessage(msg, 10000)
        return len(gone)

    def split_trials(self, paths: list[Path]) -> None:
        """Right-click › Split into a trial per stimulus: what Record does by itself when it
        stops, for trials recorded before (see split_in_background)."""
        busy = self.recorder.path if self.recorder.recording else None
        todo = [Path(p) for p in paths
                if Path(p) != busy and Path(p).exists() and trialfile.stimuli_count(p) > 1]
        if not todo:
            self.statusBar().showMessage("Nothing to split: each trial highlighted sent one stimulus or none", 8000)
            return
        self.split_in_background(todo)

    def split_at_rest_dialog(self, paths: list[Path]) -> None:
        """Right-click › Split where the stimulus goes back to 0…: shows the first trial with
        the cuts as you set them (SplitAtRestDialog), then splits it, or every one highlighted."""
        from .splitdialog import SplitAtRestDialog
        busy = self.recorder.path if self.recorder.recording else None
        todo = [Path(p) for p in paths if Path(p) != busy and Path(p).exists()]
        if not todo:
            return
        ch = self._split_channel(todo[0])
        if ch is None:
            self.statusBar().showMessage("Pick the Stimulus channel first: the cuts are found on it", 8000)
            return
        try:
            rec = trialfile.read_file(todo[0])
        except Exception as e:
            self.statusBar().showMessage(f"Couldn't read {todo[0].name}: {e}", 8000)
            return
        sp = getattr(self, "simple_page", None)
        dlg = SplitAtRestDialog(self, rec, ch, len(todo), sp.before.value() if sp is not None else 100.0,
                                todo[0].stem)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        gap, on, before, every = dlg.settings()
        self.split_in_background(todo if every else todo[:1], at_rest=(gap, on, before))

    def split_in_background(self, paths: list[Path], marks: dict | None = None,
                            at_rest: tuple[float, float, float] | None = None) -> None:
        """Make a trial of each stimulus sent during these trials (Repeat, Raise, Complex
        Stimulus's Repeat), back to back from Simple Stimulus's Before (trialsplit), named
        for what was sent by Groups. Worked out on another thread, so acquiring carries on
        smoothly; then the originals are hidden (tick one to bring it back) and the new
        trials listed. marks: {path: where that trial starts on the live history}, to draw
        each new trial's start and end there. at_rest: (back at 0 for, ms; on; Before, ms) to
        cut where the stimulus channel goes back to 0 instead (trialsplit.split_at_rest)."""
        if not hasattr(self, "_split_relay"):
            self._split_relay = _SplitRelay(self)
            self._split_relay.done.connect(self._split_done)
        sp = getattr(self, "simple_page", None)
        before = sp.before.value() if sp is not None else 100.0
        jobs = [(Path(p), self._split_channel(Path(p))) for p in paths]
        relay = self._split_relay

        def work():
            out = []
            for p, ch in jobs:
                try:
                    nxt = lambda f=p.parent: session.next_block_path(f)
                    if at_rest is not None:
                        if ch is None:
                            raise ValueError("no stimulus channel")
                        gap, on, before_ms = at_rest
                        out.append((p, trialsplit.split_at_rest(p, ch, before_ms, nxt, gap, on), None))
                    else:
                        out.append((p, trialsplit.split(p, ch, before, nxt), None))
                except Exception as e:             # (said in the status bar)
                    out.append((p, [], e))
            relay.done.emit((out, dict(marks or {})))
        threading.Thread(target=work, name="split trials").start()

    def _split_done(self, got) -> None:
        results, marks = got
        made = [(p, new) for p, new, _ in results if new]
        failed = [p.name for p, _, err in results if err is not None]
        for folder in {p.parent for p, _ in made}:          # the originals: hidden, so nothing counts twice
            stems = [p.stem for p, _ in made if p.parent == folder]
            d = groupsfile.load(folder)
            d["hidden_trials"] = [s for s in d["hidden_trials"] if s not in stems] + stems
            d["shown_trials"] = [s for s in d["shown_trials"] if s not in stems]
            groupsfile.save(folder, d)
        for p, new in made:                                 # the channel colors you gave the original
            colors = trialfile.load_edits(p.parent)["colors"].get(p.stem)
            if colors:
                trialfile.recolor_blocks([q for q, _, _ in new], colors)
        paths = [q for _, new in made for q, _, _ in new]
        for q in paths:
            if self.session_dir is not None and q.parent == Path(self.session_dir):
                self.session_block_added(q)
        if self.mode == REVIEW and self.review_dir is not None and paths and \
                (self.session_dir is None or Path(self.review_dir) != Path(self.session_dir)):
            state, shown = self._live_state, self.block_combo.currentIndex()
            self.open_session(self.review_dir, remember=False, keep_acquiring=True)
            self._live_state, self._overlay_sel = state, []
            self._fill_recorded()
            self.block_combo.setCurrentIndex(min(max(0, shown), len(self.review_blocks) - 1))
        for p, new in made:                                 # each trial's start and end on the live history
            base = marks.get(p)
            if base is not None:
                for _, a, b in new:
                    self._mark_boundary(base + a, "trial")
                    self._mark_boundary(base + b, "trial_end")
        self._update_title()
        self._fill_recorded()
        self.analysis_page.source_changed()
        if made or failed:
            msg = "; ".join(f"{p.stem}: a trial for each of its {len(new)} stimuli (it's hidden)" for p, new in made)
            if failed:
                msg += ("; " if msg else "") + f"couldn't split {', '.join(failed)}"
            self.statusBar().showMessage(msg, 10000)

    def _split_channel(self, path: Path) -> str | None:
        """The channel a split finds the stimuli on: the session's stimulus channel, else the
        one the stimulus is recorded on. None: their notes are used."""
        names = trialfile.file_info(path)["channel_names"]
        saved = self.settings.value(f"stim_channel/{path.parent.resolve()}")
        ch = saved if saved is not None else self.default_stim_channel(names)
        return ch if ch in names else None

    def set_trial_label(self, text: str) -> None:
        """The label saved with trials from now on (Record page), e.g. "Cell 1"; "" for none.
        A trial being recorded gets it too."""
        if text == self.trial_label:
            return
        self.trial_label = text
        self.settings.setValue("trial_label", text)
        if self.recorder.recording:
            self.recorder.set_attr("label", text)
            self._fill_recorded()

    def set_organize(self, key: str, folder: Path | None) -> None:
        """Organize a session's trials by stimulus shape ("") or start time ("time"),
        kept with its groups (pages.json). Shared by this panel and Analysis's."""
        if folder is None:
            return
        d = groupsfile.load(folder)
        d["organize"] = key
        groupsfile.save(folder, d)
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
        monitor = next((r.ch.name for r in self.rows if self.cfg.stim_monitor
                        and r.ch.physical == self.cfg.stim_monitor), None) if self.mode == LIVE else None
        current = (monitor if monitor is not None else saved if saved is not None    # live: the ticked one
                   else self.default_stim_channel(names))
        self.blocks_panel.set_stim_channels(names, current, key is not None or self.mode == LIVE)
        self.blocks_panel.set_organize(groupsfile.load(self._panel_folder())["organize"],
                                       self._panel_folder() is not None)
        paths = self._panel_paths()
        pages, auto, d = self._page_state() if paths else ([], [], groupsfile.load(None))
        labels = {}
        for i, p in enumerate(paths):
            try:
                info = self.store.info(p)
                labels[i] = (p.stem, f"{info['duration_s']:.1f} s") + ((info["label"],) if info.get("label") else ())
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
        want = (LIVE_ITEM if self.mode == LIVE else ("avg", self._showing_average)
                if getattr(self, "_showing_average", None) else ("block", self.block_combo.currentIndex()))
        self.blocks_panel.mark(want, self._overlay_sel)

    def _activate(self, data) -> None:
        """What clicking a Blocks panel entry does: Live, Back to Live, or show a block."""
        if data in (LIVE_ITEM, BACK_ITEM):
            if self.mode == REVIEW:
                self.leave_review()
        elif isinstance(data, tuple) and data[0] == "avg":
            self.show_group_average(data[1])
        elif isinstance(data, tuple) and data[0] == "block":
            if self.mode == LIVE:
                self.browse_recorded(int(data[1]))
            else:
                if self.block_combo.currentIndex() == int(data[1]):   # (e.g. back from an average)
                    self._show_review_block(int(data[1]))
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
            for k, (plot, name) in enumerate(zip(self.plots, names)):
                # every highlighted trial drawn alike: the channel's own color and line width,
                # the same as the trial being looked at
                color = QtGui.QColor(self.curve_colors[k] if k < len(self.curve_colors) else "#888888")
                width = self.curve_widths[k] if k < len(self.curve_widths) else 1.0
                rows = [b["data"][b["channel_names"].index(name)] for b in blks
                        if name in b["channel_names"] and b["sample_rate"] == fs]
                if not rows:
                    continue
                Y = np.full((len(rows), max(len(r) for r in rows)), np.nan)
                for r, y in enumerate(rows):
                    Y[r, :len(y)] = y
                item = OverlayCurve()
                item.mode = getattr(self.curves[k], "mode", "peak") if k < len(self.curves) else "peak"
                item.setPen(pg.mkPen(color, width=width))
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

    def regroup(self) -> None:
        """Sort every trial into groups again from scratch: what was measured of each
        trial's stimulus is forgotten and measured again. Names and moves stay."""
        self.store.forget()
        self._fill_recorded()
        self.analysis_page.source_changed()
        n = len(self._page_state()[0]) if self._panel_paths() else 0
        self.statusBar().showMessage(f"Regrouped: {n} group{'' if n == 1 else 's'}", 8000)

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
        self.open_session(self.session_dir, remember=False, keep_acquiring=True)
        self._live_state, self._overlay_sel = state, overlay
        self._fill_recorded()                     # now with Back to Live
        self.block_combo.setCurrentIndex(i)
        self._draw_overlay()                      # also when block i was already showing

    # ======================================================================
    # Review mode
    # ======================================================================
    def _show_whole_trial(self) -> None:
        """Each trial opens fitted to its whole length (zoom in from there). Window's own
        value comes back for live."""
        wb = self.window_box
        if self._window_wanted is None:
            self._window_wanted = wb.value()
        self._fitting_window = True
        wb.setValue(wb.maximum())                  # (_fit_window capped it at the trial's length)
        self._fitting_window = False
        self._sync_time_bar()

    def _show_review_block(self, index: int):
        if self.mode != REVIEW or not (0 <= index < len(self.review_blocks)):
            return
        try:
            blk = self.store.read(self.review_blocks[index])
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Could not read trial", repr(e))
            return
        self.review_block = blk
        self._showing_average = None
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
        self._show_whole_trial()
        self._update_scroll_range()
        self._update_wiring_label()
        self.plots[0].setXRange(0, min(self.window_box.value(), max(blk["duration_s"], 1e-3)), padding=0)
        for p in self.plots:                       # and each channel fitted to this trial's data
            self._autoscale_view(p.vb)
        self._mark_block_list()
        self._show_block_media(index)
        self.status.setText(
            f"Reviewing {self.review_blocks[index].stem} ({index + 1}/{len(self.review_blocks)}) · "
            f"{blk['duration_s']:.2f} s · {whole(blk['sample_rate'])}/ch · "
            f"{len(blk['comments'])} comment(s)")

    def show_group_average(self, name: str) -> None:
        """A group's average: every channel averaged sample by sample over its trials, each
        from its start (a saved trial window starts the same time before its stimulus), so
        the average stimulus and the average response. Shown like a trial, but not a file."""
        pages = dict(self._page_state()[0]) if self._panel_paths() else {}
        idx = pages.get(name, [])
        if len(idx) < 2:
            return
        if self.mode == LIVE:
            if self.recorder.recording:
                return
            self.browse_recorded(idx[0])
            if self.mode != REVIEW:
                return
        blks = []
        for i in idx:
            try:
                blks.append(self.store.read(self.review_blocks[i]))
            except Exception:
                pass
        first = blks[0] if blks else None
        blks = [b for b in blks if b["channel_names"] == first["channel_names"]
                and b["sample_rate"] == first["sample_rate"]] if first else []
        if len(blks) < 2:
            self.statusBar().showMessage("These trials can't be averaged: their channels or rates differ", 8000)
            return
        fs, n = first["sample_rate"], max(len(b["t"]) for b in blks)
        data = []
        for k in range(len(first["channel_names"])):
            Y = np.full((len(blks), n), np.nan)
            for r, b in enumerate(blks):
                Y[r, :len(b["data"][k])] = b["data"][k]
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                data.append(np.nanmean(Y, axis=0))
        t = np.arange(n) / fs
        self._overlay_sel = []
        self._draw_overlay()
        self._showing_average = name
        self.review_block = {**first, "data": data, "t": t, "duration_s": n / fs, "comments": []}
        for curve, y in zip(self.curves, data):
            curve.setData(t, y)
        if self._calcs:                             # the calculated channels, of the average
            ys = self._chain.offline(dict(zip(first["channel_names"], data)), fs)
            for k, y in enumerate(ys):
                if y is not None and len(data) + k < len(self.curves):
                    self.curves[len(data) + k].setData(t, y)
        self._curves_updated()
        self._clear_markers()
        self._fit_window()
        self._show_whole_trial()
        self._update_scroll_range()
        self.plots[0].setXRange(0, min(self.window_box.value(), n / fs), padding=0)
        for p in self.plots:
            self._autoscale_view(p.vb)
        self.status.setText(f"Average of {len(blks)} trials · {name} · {n / fs:.2f} s · {whole(fs)}/ch")
        self.blocks_panel.reveal(("avg", name))     # the list shows it highlighted (opening Live's trials rebuilt it)

    def analysis_blocks(self) -> list[Path]:
        """The blocks the Analysis tab works on: those being reviewed, or recorded so far."""
        return list(self.review_blocks) if self.mode == REVIEW else list(self.blocks)

    def analysis_step(self, d: int) -> None:
        """← / →: Analysis's highlighted block, when Analysis is showing."""
        if self.tabs.is_visible("analysis"):
            self.analysis_page.step(d)

    def step_block(self, d: int, extend: bool = False) -> None:
        """↑ / ↓ anywhere in the window (Shift: extend): the same as in the Trials list itself,
        on Analysis or the Record page, Averages included. Not while recording."""
        if self.tabs.is_visible("analysis"):          # Analysis: the next entry in its Trials list
            self.analysis_page.step_list(d, extend=extend)
            return
        if not self.tabs.is_visible("acq") or self.recorder.recording:
            return
        self.step_trials(d, extend=extend)
