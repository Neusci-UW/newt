"""The main window's channels: adding, editing and removing them, their colors (and the
recorded trials'), applying a setup, the sampling rate and the input wiring."""
from __future__ import annotations

import dataclasses
import warnings
from pathlib import Path

from pyqtgraph.Qt import QtWidgets

from . import config as cfgmod
from . import session
from . import trialfile
from .channels import ChannelRow, ChannelStrip
from .config import Channel, Config, valid_inputs
from . import colors
from .mainparts import LIVE, REVIEW
from .units import whole


class ChannelSetupMixin:
    # -- changing channels -------------------------------------------------
    def _copy_rows(self) -> list[ChannelRow]:
        return [ChannelRow(dataclasses.replace(r.ch)) for r in self.rows]

    def _channel_edited(self, strip: ChannelStrip, what: str, value):
        rows = self._copy_rows()
        was_stim = bool(self.cfg.stim_monitor) and strip.row.ch.physical == self.cfg.stim_monitor
        setattr(rows[self.rows.index(strip.row)].ch, what, value)
        if self.apply_channels(rows) and was_stim and what == "physical":
            self._stim_monitor_moved(value)             # the stimulus channel moved input

    def _channel_removed(self, strip: ChannelStrip):
        i = self.rows.index(strip.row)
        was_stim = bool(self.cfg.stim_monitor) and strip.row.ch.physical == self.cfg.stim_monitor
        if self.apply_channels([r for j, r in enumerate(self._copy_rows()) if j != i]) and was_stim:
            self._stim_monitor_moved("")

    def _channel_stim_ticked(self, strip: ChannelStrip, on: bool) -> None:
        """Acquisition: a channel's "Records the stimulus" box."""
        phys = strip.row.ch.physical
        if on:
            self.set_stim_monitor(phys)
        elif phys == self.cfg.stim_monitor:
            self._stim_monitor_moved("")

    def _stim_monitor_moved(self, phys: str) -> None:
        """The stimulus is now recorded on `phys` ("" = nowhere), no channels added or removed."""
        self.cfg.stim_monitor = phys
        self._stim_monitor_changed(phys)

    def add_channel(self) -> bool:
        used = {r.ch.physical for r in self.rows}
        free = [p for p in valid_inputs(self.cfg) if p not in used]
        if not free:
            self._channel_message("All inputs are already in use.")
            return False
        names = {r.ch.name for r in self.rows}
        n = len(self.rows) + 1
        while f"Ch{n}" in names:
            n += 1
        color = colors.next_color(r.ch.color for r in self.rows)
        return self.apply_channels(self._copy_rows() + [ChannelRow(Channel(free[0], f"Ch{n}", color=color))])

    # -- colors (display only: allowed while recording, never restarts) ----
    def _channel_recolored(self, strip: ChannelStrip, color: str):
        self.set_channel_color(self.rows.index(strip.row), color)

    def set_channel_color(self, index: int, color: str, recolor_recorded: bool | None = None) -> None:
        """Change a live channel's trace color.

        If the session already has recorded blocks with a different color for
        this channel, asks whether to recolor them too (or uses
        `recolor_recorded` if given). The block being recorded always follows.
        """
        color = colors.normalize_color(color)
        ch = self.rows[index].ch
        ch.color = color
        strip = self.strips[index] if index < len(self.strips) else None
        if strip is not None:
            strip.color_btn.set_color(color, ch.enabled)
        if ch in self.cfg.channels:
            k = self.cfg.channels.index(ch)
            if k < len(self.curves):
                self._set_curve_color(k, color)
        if self.recorder.recording:
            self.recorder.set_colors([c.color for c in self.cfg.channels])
        current = self.recorder.path if self.recorder.recording else None
        older = [b for b in self.blocks if b != current and self._block_color(b, ch.name) not in (None, color)]
        if older:
            if recolor_recorded is None:
                recolor_recorded = self._recolor_policy
            if recolor_recorded is None:
                recolor_recorded, remember = self._ask_recolor(ch.name, len(older))
                if remember:
                    self._recolor_policy = recolor_recorded
            if recolor_recorded:
                self._recolor(older, {ch.name: color})
                return
        self.statusBar().showMessage(f"{ch.name}: color {colors.color_label(color)}", 6000)

    def _block_color(self, block: Path, name: str) -> str | None:
        """Color of channel `name` in a recorded block, or None if it isn't there."""
        try:
            info = self.store.info(block)
        except Exception:
            return None
        names = info["channel_names"]
        return info["colors"][names.index(name)] if name in names else None

    def _ask_recolor(self, name: str, n: int) -> tuple[bool, bool]:
        """Ask whether to recolor recorded blocks. Returns (recolor, remember for this session)."""
        B = QtWidgets.QMessageBox.StandardButton
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Icon.Question, "Recolor recorded trials?",
            f"{n} trial(s) already recorded in this session show {name} in another color.\n\n"
            "Use the new color for them too? (Only the display color changes, not the data.)",
            B.Yes | B.No, self)
        box.setDefaultButton(B.Yes)
        remember = QtWidgets.QCheckBox("Do this for every color change in this session")
        box.setCheckBox(remember)
        return box.exec() == B.Yes, remember.isChecked()

    def _recolor(self, blocks: list[Path], colors: dict[str, str]) -> None:
        failed = trialfile.recolor_blocks(blocks, colors)
        names = ", ".join(colors)
        if failed:
            self._channel_message(f"Couldn't update the color in {len(failed)} trial(s) "
                                  f"(read-only or in use?): {', '.join(f.name for f in failed)}")
        else:
            self.statusBar().showMessage(f"{names}: color updated in {len(blocks)} recorded trial(s)", 8000)

    def set_review_color(self, index: int, color: str) -> None:
        """Recolor one channel in every block of the session being reviewed."""
        if self.mode != REVIEW or self.review_block is None:
            return
        color = colors.normalize_color(color)
        name = self.review_block["channel_names"][index]
        self.review_block["colors"][index] = color
        self._set_curve_color(index, color)
        self.review_color_btns[index].set_color(color)
        self._recolor(self.review_blocks, {name: color})

    @staticmethod
    def _layout(channels: list[Channel]) -> list[tuple]:
        return [(c.physical, c.name, c.units) for c in channels]

    def apply_channels(self, rows: list[ChannelRow]) -> bool:
        """Switch to a new channel setup (see apply_setup)."""
        return self.apply_setup(rows=rows)

    def apply_setup(self, rows: list[ChannelRow] | None = None, sample_rate: float | None = None,
                    terminal_config: str | None = None) -> bool:
        """Change channels, sampling rate and/or input wiring. Restarts acquisition if running.

        Refused while recording. If the session already has blocks recorded with
        a different setup, a new session is started, because every block in a
        session must match to export as one .mat file.
        """
        what = ("sampling rate" if sample_rate is not None else
                "input wiring" if terminal_config is not None else "channels")

        def refuse(msg):
            self._channel_message(msg)
            self._build_live_plots()   # put the controls back as they were
            self._sync_setup_controls()
            return False

        if self.recorder.recording:
            return refuse(f"Stop recording before changing the {what}.")
        if hasattr(self, "trials_page") and self.trials_page.running:
            return refuse(f"Stop the protocol before changing the {what}.")
        if self.mode != LIVE:
            return refuse(f"Go back to live mode to change the {what}.")
        rows = self._copy_rows() if rows is None else rows
        changes = {}
        if sample_rate is not None:
            changes["sample_rate"] = float(sample_rate)
            changes["chunk_size"] = cfgmod.auto_chunk_size(sample_rate)
        if terminal_config is not None:
            changes["terminal_config"] = terminal_config
            if terminal_config == "DIFF":
                # Say which channel to move, rather than a generic "ai5 isn't available".
                stuck = [r.ch for r in rows if r.ch.physical not in ("ai0", "ai1", "ai2", "ai3")]
                if stuck:
                    names = ", ".join(f"{c.name} ({c.physical})" for c in stuck)
                    return refuse(f"Can't switch to DIFF: {names} must move to ai0–ai3 or be removed "
                                  "first, because DIFF uses ai4–ai7 as the minus inputs.")
        trial = self.cfg.with_channels([r.ch for r in rows], **changes)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                cfgmod.validate(trial)
        except ValueError as e:
            return refuse(str(e))

        running = self.btn_run.isChecked()
        settings_changed = (trial.sample_rate != self.cfg.sample_rate
                            or trial.terminal_config != self.cfg.terminal_config)
        hardware_changed = settings_changed or (
            [(c.physical, c.enabled) for c in trial.channels]
            != [(c.physical, c.enabled) for c in self.cfg.channels])
        if self.blocks and (settings_changed
                            or self._layout(trial.channels) != self._layout(self.cfg.channels)):
            if not self._confirm_new_session(what):
                return refuse(f"{what.capitalize()} unchanged.")
            if not self.new_session():          # asks Save / Don't Save if unsaved
                return refuse(f"{what.capitalize()} unchanged.")
        if running and hardware_changed:
            self.btn_run.setChecked(False)

        self.rows = rows
        self.cfg.channels, self.cfg.spare_channels = trial.channels, trial.spare_channels
        self.cfg.sample_rate, self.cfg.chunk_size = trial.sample_rate, trial.chunk_size
        self.cfg.terminal_config = trial.terminal_config
        if hardware_changed:
            self.ring = None                     # new channels or rate: start the display afresh
            self._alloc_buffers()
        self._build_live_plots()
        self._redraw_live()
        self._sync_setup_controls()
        if self.sim_page:
            self.sim_page.sync()          # inputs and their channel names
        if getattr(self, "board_page", None):
            self.board_page.sync()
        if running and hardware_changed and not self.btn_run.isChecked():
            self.btn_run.setChecked(True)
        if what == "channels":
            on = ", ".join(f"{c.name} = {c.physical}" for c in self.cfg.channels)
            self.statusBar().showMessage(f"Channels: {on}", 8000)
        elif what == "sampling rate":
            self.statusBar().showMessage(f"Sampling rate: {whole(self.cfg.sample_rate)} per channel", 8000)
        else:
            self.statusBar().showMessage(f"Input wiring: {self.cfg.terminal_config}", 8000)
        return True

    def _confirm_new_session(self, what: str) -> bool:
        B = QtWidgets.QMessageBox.StandardButton
        r = QtWidgets.QMessageBox.question(
            self, f"{what.capitalize()} changed",
            f"This session's trials were recorded with the old {what}. All trials in a session "
            f"must match, so changing the {what} starts a new session.\n\nContinue?",
            B.Ok | B.Cancel, B.Ok)
        return r == B.Ok

    # -- sampling rate and wiring ------------------------------------------
    @staticmethod
    def _parse_rate(text: str) -> float | None:
        t = text.lower().replace(",", "").replace("hz", "").strip()
        mult = 1.0
        if t.endswith("k"):
            t, mult = t[:-1].strip(), 1000.0
        try:
            v = float(t) * mult
        except ValueError:
            return None
        return v if v > 0 else None

    def _rate_entered(self):
        # Selecting a preset fires both activated and editingFinished, and a
        # dialog taking focus fires editingFinished again: handle only one.
        if self._rate_busy:
            return
        self._rate_busy = True
        try:
            self._apply_entered_rate()
        finally:
            self._rate_busy = False

    def _apply_entered_rate(self):
        rate = self._parse_rate(self.rate_box.currentText())
        if rate is None:
            self._channel_message(f"{self.rate_box.currentText()!r} isn't a sampling rate. "
                                  "Type a number of Hz, e.g. 1000 or 20k.")
            self._sync_setup_controls()
            return
        if rate != self.cfg.sample_rate:
            self.apply_setup(sample_rate=rate)
        else:
            self._sync_setup_controls()   # tidy the text, e.g. "1000" -> "1 kHz"

    def _sync_setup_controls(self):
        """Show the current rate and wiring in the toolbar, menu and status bar."""
        self.rate_box.blockSignals(True)
        self.rate_box.setEditText(whole(self.cfg.sample_rate))
        self.rate_box.blockSignals(False)
        n = len(self.cfg.channels)
        self.rate_box.setToolTip(f"Samples per second, per channel. With {n} channel(s) on, "
                                 f"the USB-6003 allows up to {whole(cfgmod.max_rate(n))}.")
        self.act_wiring[self.cfg.terminal_config].setChecked(True)
        self._update_wiring_label()

    def _update_wiring_label(self):
        if self.mode == REVIEW:
            tc = (self.review_block or {}).get("terminal_config")
            self.wiring_label.setText(f"  recorded in {tc}  " if tc else "")
        else:
            self.wiring_label.setText(f"  {self.cfg.terminal_config} wiring  ")

    def _channel_message(self, text: str):
        self.statusBar().showMessage(f"⚠ {text}", 10000)

    def save_setup_dialog(self):
        if self.config_path:
            default = self.config_path.with_name(self.config_path.stem + "_setup.toml")
        else:
            default = Path(self._start_dir()) / "channel_setup.toml"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save channel setup", str(default),
                                                        "Config file (*.toml)")
        if path:
            cfgmod.save(self.cfg, path)
            self.statusBar().showMessage(f"Channel setup saved to {path}. "
                                         f"Start with: neusci-daq -c \"{path}\"", 15000)
