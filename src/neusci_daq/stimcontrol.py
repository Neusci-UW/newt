"""The main window's stimulator: the stimulus, Fire, Repeat, Stop, the output and the
stimulus monitor."""
from __future__ import annotations

import json


from . import stim as stimmod
from .channels import ChannelRow, ChannelStrip
from .config import Channel, valid_inputs
from .stim import Stimulus
from .stimwindow import StimPage
from . import colours
from .mainparts import LIVE


class StimulatorMixin:
    # -- stimulator ----------------------------------------------------------
    def _saved_stimulus(self) -> Stimulus:
        import json
        try:
            return Stimulus.from_dict(json.loads(self.settings.value("stimulus", "") or "{}"))
        except Exception:
            return Stimulus()

    def set_stimulus(self, s: Stimulus) -> None:
        """Use stimulus `s` for Fire and Repeat, and show it in the Stimulator tab."""
        self._stimulus_edited(s)
        if hasattr(self, "stim_page"):
            self.stim_page.set_stimulus(s)

    def _stimulus_edited(self, s: Stimulus) -> None:
        """The stimulus that Fire and Repeat send (remembered between runs)."""
        import json
        self.stimulus = s
        self.settings.setValue("stimulus", json.dumps(s.to_dict()))
        if hasattr(self, "trials_page"):
            self.trials_page.stimulus_changed()

    def open_stim_window(self) -> StimPage:
        """Show the Stimulator tab."""
        self.tabs.show_tab("stim")
        return self.stim_page

    def _stim_message(self, text: str, error: bool = False) -> None:
        self.statusBar().showMessage(("⚠ " if error else "") + text, 10000)
        self.stim_page.show_message(text, error)

    def _escape(self):
        if self.trials_page.running:
            self.stop_stimulus()
            return
        if self.repeating or (self.stim_out is not None and self.stim_out.busy):
            self.stop_stimulus()
            self._stim_message("Stimulus stopped")

    def fire_stimulus(self) -> bool:
        """Send the current stimulus once. Returns False (with a message) if it can't."""
        if self.stim_out is None:
            self._stim_message("The stimulator needs the nidaqmx (or simulated) backend.", error=True)
            return False
        if self.trials_page.running:
            self._stim_message("A protocol is running (Acquisition › Protocols); stop it first.", error=True)
            return False
        try:
            wave = self.stimulus.waveform(self.cfg.stim_rate)
        except ValueError as e:
            self._stim_message(str(e), error=True)
            return False
        return self.send_wave(wave, self.stimulus.describe(self.cfg.stim_units, self.cfg.stim_scale), self.stimulus)

    def send_wave(self, wave, desc: str, stimulus: Stimulus | None = None) -> bool:
        """Send `wave` (volts at the output rate), and note it as a comment and save the
        `stimulus` it came from with the block while recording."""
        if self.stim_out is None:
            self._stim_message("The stimulator needs the nidaqmx (or simulated) backend.", error=True)
            return False
        if self.mode != LIVE or not self.backend.running:
            self._stim_message("Press Start first: stimuli are sent while acquiring.", error=True)
            return False
        try:
            stimmod.check_limit(wave, self.cfg.stim_max_v)
            self.stim_out.fire(wave, self.cfg.stim_rate)
        except (ValueError, RuntimeError) as e:
            self._stim_message(str(e), error=True)
            return False
        if self.recorder.recording:
            self._log_comment(f"Stim: {desc}" if not desc.startswith("Stimulus ") else desc)
            if stimulus is not None:
                self.recorder.add_stimulus(stimulus.to_dict(), self.cfg.stim_rate)
        self._stim_message(f"Stimulus sent on {self.cfg.stim_output}: {desc}")
        self._update_stim_chip()
        return True

    def stop_stimulus(self) -> None:
        if hasattr(self, "trials_page") and self.trials_page.running:
            self.trials_page.stop_protocol("Protocol stopped")
        self.set_repeat(False)
        if self.stim_out is not None:
            self.stim_out.stop()
        self._update_stim_chip()

    def set_repeat(self, on: bool) -> None:
        if on and hasattr(self, "trials_page") and self.trials_page.running:
            self._stim_message("A protocol is running (Acquisition › Protocols); stop it first.", error=True)
            on = False
        if on and not self.repeating:
            self.repeating = True          # set first, so the chip shows "every N s"
            if not self.fire_stimulus():
                self.repeating = False
            else:
                self.stim_timer.start(int(self.stim_repeat_s * 1000))
                self._stim_message(f"Repeating every {self.stim_repeat_s:g} s")
        elif not on:
            self.repeating = False
            self.stim_timer.stop()
        self.settings.setValue("stim_repeat_s", self.stim_repeat_s)
        b = self.stim_page.btn_repeat
        if b.isChecked() != self.repeating:
            b.blockSignals(True)
            b.setChecked(self.repeating)
            b.blockSignals(False)
        self._update_stim_chip()

    def _repeat_fire(self):
        if self.stim_out is not None and self.stim_out.busy:
            self._stim_message("Skipped one repeat: the previous stimulus was still running. "
                               "Use a longer repeat time.", error=True)
            return
        if not self.fire_stimulus():
            self.set_repeat(False)
        elif self.stim_timer.interval() != int(self.stim_repeat_s * 1000):
            self.stim_timer.setInterval(int(self.stim_repeat_s * 1000))

    def set_stim_on_start(self, what: str) -> None:
        """Stimulator tab > When Start is pressed: "" nothing, "once", or "repeat"."""
        self.stim_on_start = what if what in ("", "once", "repeat") else ""
        self.settings.setValue("stim_on_start", self.stim_on_start)

    def set_stim_output(self, ch: str) -> None:
        if self.stim_out is not None and self.stim_out.busy:
            self.stop_stimulus()
        self.cfg.stim_output = ch
        self.stim_page.sync()
        self.statusBar().showMessage(f"Stimulator output: {ch}", 6000)

    def monitor_choices(self) -> list[tuple[str, str, bool, str]]:
        """Inputs the stimulus can be recorded on: (label, input, usable, tooltip)."""
        used = {r.ch.physical: r.ch.name for r in self.rows
                if not (r.ch.physical == self.cfg.stim_monitor and r.ch.name == "Stim")}
        out = [("None (don't record it)", "", True, "")]
        for phys in valid_inputs(self.cfg):
            if phys in used:
                out.append((f"{phys} ({used[phys]})", phys, True,
                            f"{used[phys]} becomes the channel that records the stimulus"))
            else:
                pair = f" (minus input ai{int(phys[2:]) + 4})" if self.cfg.terminal_config == "DIFF" else ""
                out.append((phys, phys, True, f"Wire {self.cfg.stim_output} to {phys}{pair}, and AO GND to AI GND"))
        return out

    def set_stim_monitor(self, phys: str) -> bool:
        """Record the stimulus on input `phys` (a channel named Stim), or stop recording it ("")."""
        old = self.cfg.stim_monitor
        if phys == old:
            return True
        rows = [r for r in self._copy_rows() if not (old and r.ch.physical == old and r.ch.name == "Stim")]
        existing = next((r for r in rows if r.ch.physical == phys), None) if phys else None
        if phys and existing is None:                    # a new channel, named Stim
            if any(r.ch.name == "Stim" for r in rows):
                self._channel_message("Another channel is already named Stim; rename it first.")
                return False
            color = colours.next_color(r.ch.color for r in rows)
            rows.append(ChannelRow(Channel(phys, "Stim", units="V", color=color)))
        # an existing channel just becomes the stimulus channel: no restart unless one goes away
        same = [(r.ch.physical, r.ch.name) for r in rows] == [(r.ch.physical, r.ch.name) for r in self.rows]
        if not same and not self.apply_channels(rows):
            return False
        self.cfg.stim_monitor = phys
        self._stim_monitor_changed(phys)
        return True

    def _stim_monitor_changed(self, phys: str) -> None:
        """Show where the stimulus is recorded everywhere that cares."""
        for strip in self.strips:
            if isinstance(strip, ChannelStrip):
                strip.stim.blockSignals(True)
                strip.stim.setChecked(bool(phys) and strip.row.ch.physical == phys)
                strip.stim.blockSignals(False)
        self.stim_page.sync()
        if self.sim_page:
            self.sim_page.sync()
        self.trials_page.sync()
        name = next((r.ch.name for r in self.rows if r.ch.physical == phys), "") if phys else ""
        if name and self._stim_key() is not None:
            self.set_stim_channel(name)                  # the Blocks panel and Analysis group by it
        self.statusBar().showMessage(f"Recording the stimulus on {phys} ({name})" if phys
                                     else "Not recording the stimulus", 8000)
