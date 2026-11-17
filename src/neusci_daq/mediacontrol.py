"""The main window's audio, camera and devices: listening to a channel, the output device,
the camera panel, playing back a recorded trial with its video, and switching the input
device."""
from __future__ import annotations

import dataclasses
import json
import time

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from . import config as cfgmod
from .config import Channel
from . import colors
from .backends import daq_devices
from .channels import ChannelRow
from .mainparts import LIVE, REVIEW


class MediaMixin:
    # -- audio and video (audio.py, video.py) --------------------------------------------------
    def _media_names(self) -> list[str]:
        if self.mode == REVIEW:
            return list(self._plot_names)
        return [c.name for c in self.cfg.channels] + [c.name for c, f, _ in self._calcs if f is not None]

    def _fill_listen_menu(self) -> None:
        from . import audio
        m = self.listen_menu
        m.clear()
        group = QtGui.QActionGroup(m)
        for name in ["", *self._media_names()]:
            a = m.addAction(name or "Off", lambda name=name: self.set_listen(name))
            a.setCheckable(True)
            a.setChecked(name == self._listen)
            group.addAction(a)
        if audio.available():
            m.addSeparator()
            m.addAction(audio.available()).setEnabled(False)

    def _fill_output_menu(self) -> None:
        from . import audio
        m = self.output_menu
        m.clear()
        group = QtGui.QActionGroup(m)
        for d in [{"name": ""}] + audio.devices("output"):
            a = m.addAction(d["name"] or "The default output", lambda n=d["name"]: self.set_audio_output(n))
            a.setCheckable(True)
            a.setChecked(d["name"] == self._audio_out)
            group.addAction(a)

    def set_audio_output(self, name: str) -> None:
        self._audio_out = name
        self.settings.setValue("audio_output", name)
        if self._monitor is not None and self._monitor.running:
            self.set_listen(self._listen)              # reopen on the new device

    def set_listen(self, name: str) -> bool:
        """Listen to channel `name` live ("" stops). In review it's the one Play plays."""
        from . import audio
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None
        self._listen = name
        if not name or self.mode == REVIEW:
            self.statusBar().showMessage(f"Play (Space) plays {name}" if name else "Not listening", 5000)
            return True
        try:
            self._monitor = audio.Monitor(self._audio_out)
            self._monitor.start()
        except Exception as e:
            self._monitor = None
            self._listen = ""
            QtWidgets.QMessageBox.warning(self, "Can't listen", str(e))
            return False
        self.statusBar().showMessage(f"Listening to {name}", 5000)
        return True

    def _feed_monitor(self, y_raw: np.ndarray, calc: np.ndarray | None) -> None:
        names = [c.name for c in self.cfg.channels]
        if self._listen in names:
            k = names.index(self._listen)
            self._monitor.feed(self.cfg.channels[k].to_units(y_raw[k]), self.cfg.sample_rate)
        elif calc is not None:
            k = next((i for i, (c, _, _) in enumerate(self._calcs) if c.name == self._listen), None)
            if k is not None:
                self._monitor.feed(calc[k], self.cfg.sample_rate)

    def show_camera(self, on: bool, load: bool = True) -> None:
        self.act_camera.setChecked(on)
        self.camera_panel.setVisible(on)
        if on and load and self.mode == REVIEW and 0 <= self.block_combo.currentIndex() < len(self.review_blocks):
            self._show_block_media(self.block_combo.currentIndex())

    def _show_block_media(self, index: int) -> None:
        """Review: the block's video in the Camera panel, and a cursor to click on."""
        self.stop_playback()
        has = self.camera_panel.show_block(self.review_blocks[index])
        if has and self.camera_panel.isHidden():
            self.show_camera(True, load=False)         # a block with video: show it
        self._make_cursor()

    def _make_cursor(self) -> None:
        """A time cursor on every plot (review): click a plot to move it (and the video)."""
        self._cursor_lines = []
        for p in self.plots:
            ln = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#E69F00", width=1.5))
            ln.setZValue(50)
            ln.hide()
            p.addItem(ln)
            self._cursor_lines.append(ln)
            if not getattr(p.scene(), "_media", False):     # each plot's scene, once
                p.scene().sigMouseClicked.connect(self._media_clicked)
                p.scene()._media = True

    def _media_clicked(self, ev) -> None:
        if self.mode != REVIEW or ev.button() != QtCore.Qt.MouseButton.LeftButton or \
                ev.modifiers() & (QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.MetaModifier):
            return
        for p in self.plots:
            if p.vb.sceneBoundingRect().contains(ev.scenePos()):
                self.set_cursor(p.vb.mapSceneToView(ev.scenePos()).x())
                return

    def set_cursor(self, t: float | None) -> None:
        """Show time t (s in the block) on every plot and in the video."""
        for ln in self._cursor_lines:
            if pg.Qt.isQObjectAlive(ln):
                ln.setVisible(t is not None)
                if t is not None:
                    ln.setValue(t)
        if t is not None:
            self.camera_panel.seek(t)

    def toggle_playback(self) -> None:
        if self._player is not None and self._play_timer.isActive():
            self.stop_playback()
        else:
            self.play_shown()

    def play_shown(self) -> bool:
        """Review: play the shown part of the block's listened-to channel (or the first)."""
        from . import audio
        if self.mode != REVIEW or not self.review_block or not self.plots:
            self.statusBar().showMessage("Play works on a recorded trial: open one, or click one in Trials.", 6000)
            return False
        names = self._plot_names
        name = self._listen if self._listen in names else names[0]
        k = names.index(name)
        x, y = self.curves[k].getOriginalDataset()
        if y is None or not len(y):
            return False
        x0, x1 = self.plots[0].vb.viewRange()[0]
        m = (x >= max(x0, 0)) & (x <= x1)
        if m.sum() < 2:
            return False
        fs = self.review_block["sample_rate"]
        try:
            self._player = audio.Player(self._audio_out)
            self._player.play(y[m], fs)
        except Exception as e:
            self._player = None
            QtWidgets.QMessageBox.warning(self, "Can't play", str(e))
            return False
        self._play_start = float(x[m][0])
        self.camera_panel.play_from(self._play_start)
        self._play_timer.start()
        self.statusBar().showMessage(f"Playing {name} from {self._play_start:.2f} s (Space stops)", 5000)
        return True

    def _play_tick(self) -> None:
        pos = self._player.position() if self._player is not None else None
        if pos is None:
            self.stop_playback(keep_cursor=True)
            return
        self.set_cursor_line(self._play_start + pos)

    def set_cursor_line(self, t: float) -> None:
        for ln in self._cursor_lines:
            if pg.Qt.isQObjectAlive(ln):
                ln.show()
                ln.setValue(t)

    def stop_playback(self, keep_cursor: bool = False) -> None:
        self._play_timer.stop()
        if self._player is not None:
            self._player.stop()
            self._player = None
        if hasattr(self, "camera_panel"):
            self.camera_panel.pause()

    def _fill_input_menu(self) -> None:
        from . import audio
        m = self.input_menu
        m.clear()
        group = QtGui.QActionGroup(m)

        def add(text, backend, device="", tip=""):
            a = m.addAction(text, lambda: self.switch_input(backend, device))
            a.setCheckable(True)
            a.setChecked(self.cfg.backend == backend and (backend != "audio" or device == self.cfg.audio_device))
            a.setToolTip(tip)
            group.addAction(a)
        add("NI DAQ (USB-6003)", "nidaqmx", tip="The National Instruments DAQ, through NI-DAQmx")
        add("Simulated cells", "simulated", tip="The simulated network (Neural network simulator page): no hardware")
        m.addSeparator()
        if audio.available():
            m.addAction(audio.available()).setEnabled(False)
        for d in audio.devices("input"):
            add(f"{d['name']} ({d['channels']} in)", "audio", d["name"],
                tip="A USB audio input: an audio interface, a SpikerBox-style device or a microphone. "
                    "Its inputs are channels in1, in2, …")

    def switch_to_live(self) -> bool:
        """From Simulated cells back to the real input device used before (the NI DAQ if none)."""
        try:
            last = json.loads(self.settings.value("live_input", "") or "{}")
        except ValueError:
            last = {}
        return self.switch_input(last.get("backend", "nidaqmx"), last.get("device", ""))

    def switch_input(self, backend: str, device: str = "") -> bool:
        """Use another input device, in this window. Acquiring stops (ending any block), the
        input changes, and if it was acquiring it carries on from the new device: with Record
        on, in a new block of the same session. Everything else stays as it was."""
        import copy
        if self.trials_page.running:
            QtWidgets.QMessageBox.information(self, "Busy", "Stop the protocol first.")
            return False
        if backend == self.cfg.backend and (backend != "audio" or device == self.cfg.audio_device):
            return True
        cfg = copy.deepcopy(self.cfg)            # the setup on the new device, checked before anything changes
        cfg.backend, cfg.audio_device = backend, device
        if backend == "audio":
            from . import audio
            audio.use_audio_channels(cfg)
        elif self.cfg.backend == "audio":       # back to the DAQ's own channel setup
            saved = json.loads(self.settings.value("daq_setup", "") or "{}")
            chans = [Channel(**{k: (tuple(v) if k == "range" else v) for k, v in c.items()})
                     for c in saved.get("channels", [])] or [Channel("ai0", "Ch1"), Channel("ai1", "Ch2")]
            cfg.channels = [c for c in chans if c.enabled]
            cfg.spare_channels = [c for c in chans if not c.enabled]
            cfg.sample_rate = float(saved.get("sample_rate", 20000))
            cfg.chunk_size = cfgmod.auto_chunk_size(cfg.sample_rate)
            cfg.stim_monitor = saved.get("stim_monitor", cfg.stim_monitor)
            colors.fill_colors(cfg.channels + cfg.spare_channels)
        try:
            cfgmod.validate(cfg)
        except ValueError as e:
            QtWidgets.QMessageBox.critical(self, "Can't use that input", str(e))
            return False
        setup = lambda c: (c.channels, c.spare_channels, c.sample_rate)
        new_channels = setup(cfg) != setup(self.cfg)
        if new_channels and self.mode != LIVE:
            QtWidgets.QMessageBox.information(self, "Busy", "Go back to live first: that input has other channels.")
            return False
        if backend == "simulated" and self.cfg.backend != "simulated":   # the real device, for Switch to live
            self.settings.setValue("live_input", json.dumps({"backend": self.cfg.backend,
                                                             "device": self.cfg.audio_device}))
        if self.cfg.backend != "audio":         # remembered, to come back to
            self.settings.setValue("daq_setup", json.dumps({
                "channels": [dataclasses.asdict(c) for c in self.cfg.channels + self.cfg.spare_channels],
                "sample_rate": self.cfg.sample_rate, "stim_monitor": self.cfg.stim_monitor}))
        running = self.btn_run.isChecked()
        stimulating = bool(self.repeating or getattr(self.simple_page, "repeating", False)
                           or (self.stim_out is not None and self.stim_out.busy))
        if running:
            self.btn_run.setChecked(False)      # ends any block and stimulus; Record stays on for the next
        before, monitor = (self.cfg.backend, self.cfg.audio_device), self.cfg.stim_monitor
        self._use_backend(backend, device)
        if new_channels:
            self.cfg.stim_monitor = cfg.stim_monitor
            rows = [ChannelRow(c) for c in cfg.channels + cfg.spare_channels]
            if not self.apply_setup(rows=rows, sample_rate=cfg.sample_rate):   # (it asks about the session)
                self.cfg.stim_monitor = monitor
                self._use_backend(*before)
                if running:
                    self._carry_on()
                return False
        name = self._input_name()
        if running:
            err = self._carry_on()
            if err is not None:
                why = (str(err).strip().splitlines() or [type(err).__name__])[0]
                if not new_channels:            # back to the one that worked
                    self._use_backend(*before)
                    self._carry_on()
                    self.statusBar().showMessage(f"Couldn't start {name} ({why}): still on {self._input_name()}.",
                                                 15000)
                else:
                    self.statusBar().showMessage(f"Couldn't start {name}: {why}", 15000)
                return False
            self.statusBar().showMessage(f"Input: {name}" + (". The stimulus stopped: start it again when "
                                                             "ready." if stimulating else ""), 10000)
            return True
        found = daq_devices() if backend == "nidaqmx" else None
        if found is not None and self.cfg.device not in found:
            self.statusBar().showMessage(f"Input: {name}. No NI DAQ ({self.cfg.device}) found: plug it in "
                                         "before Start.", 15000)
        else:
            self.statusBar().showMessage(
                f"Input: {name}. Press Start." + (" macOS asks once to allow the microphone."
                                                  if backend == "audio" else ""), 10000)
        return True

    def _carry_on(self) -> BaseException | None:
        """Start acquiring again after the input device changed, recording with Record on, but
        without Start's own stimulus: nothing goes into a real preparation unless asked for.
        The error if it couldn't start (it isn't shown: the caller says what happened)."""
        self._start_error, self._quiet_start, self._starting_for_stim = None, True, True
        try:
            self.btn_run.setChecked(True)
        finally:
            self._quiet_start = self._starting_for_stim = False
        return self._start_error

    def _input_name(self) -> str:
        return {"nidaqmx": "the NI DAQ", "simulated": "Simulated cells"}.get(
            self.cfg.backend, self.cfg.audio_device or "the audio input")

    def _use_backend(self, backend: str, device: str) -> None:
        """Swap the input device under everything that reads it (acquiring is stopped)."""
        from .backends import SimulatedBackend, make_backend
        from .stimout import make_output
        self.cfg.backend, self.cfg.audio_device = backend, device
        old = self.backend
        old.stop()
        old.unsubscribe(self.display_q)
        if self.stim_out is not None and self.stim_out.busy:
            self.stim_out.stop()
        if isinstance(old, SimulatedBackend):
            self._sim_backend = old             # its cells carry on from where they were when it's back
        reuse = getattr(self, "_sim_backend", None) if backend == "simulated" else None
        self.backend = reuse or make_backend(self.cfg)
        self.backend.subscribe(self.display_q)  # the same queue, so the live view keeps reading it
        self.recorder.rebind(self.backend)
        self.stim_out = make_output(self.cfg, self.backend)
        self._input_changed()

    def _show_input_mode(self) -> None:
        """The button above Trials: to Simulated cells, or back to the real input device."""
        b = self.btn_input_mode
        if self.cfg.backend != "simulated":
            b.setText("Switch to Simulated cells")
            b.setToolTip("Record the Neural network and Breadboard simulators instead of the input device "
                         "(Acquisition carries on if it's running). Your channel setup is remembered.")
        else:
            b.setText("Switch to live recording mode")
            b.setToolTip("Record the real input device used before Simulated cells again, with its channel "
                         "setup (Acquisition carries on if it's running)")

    def _input_mode_clicked(self) -> None:
        if self.cfg.backend == "simulated":
            self.switch_to_live()
        else:
            self.switch_input("simulated")

    def _input_changed(self) -> None:
        """Everything that shows which input device records: the button above Trials, the
        simulator pages' banners, the stimulators (an audio input has no output), the title."""
        from .simpage import show_banner
        self._show_input_mode()
        for page in (self.sim_page, self.board_page):
            show_banner(page, self.cfg.backend != "simulated")
        self.set_record_stim(self.settings.value("record_stim", "simple"))
        self.stim_page._show_sim_note()     # "⚡ Simulated: …", or nothing
        self._update_title()
