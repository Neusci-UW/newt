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
from .mainparts import REVIEW


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

    def switch_input(self, backend: str, device: str = "") -> bool:
        """Use another input device: a fresh window on it (the channels change with it)."""
        import copy
        if self.recorder.recording or self.trials_page.running:
            QtWidgets.QMessageBox.information(self, "Busy", "Stop recording (and any protocol) first.")
            return False
        if backend == self.cfg.backend and (backend != "audio" or device == self.cfg.audio_device):
            return True
        if not self._leave_live():
            return False
        cfg = copy.deepcopy(self.cfg)
        if cfg.backend != "audio":                     # remembered, to come back to
            self.settings.setValue("daq_setup", json.dumps({
                "channels": [dataclasses.asdict(c) for c in cfg.channels], "sample_rate": cfg.sample_rate}))
        cfg.backend, cfg.audio_device = backend, device
        if backend == "audio":
            from . import audio
            audio.use_audio_channels(cfg)
        elif self.cfg.backend == "audio":
            saved = json.loads(self.settings.value("daq_setup", "") or "{}")
            chans = [Channel(**{k: (tuple(v) if k == "range" else v) for k, v in c.items()})
                     for c in saved.get("channels", [])]
            cfg.channels = chans or [Channel("ai0", "Ch1"), Channel("ai1", "Ch2")]
            cfg.sample_rate = float(saved.get("sample_rate", 20000))
            cfg.chunk_size = cfgmod.auto_chunk_size(cfg.sample_rate)
            colors.fill_colors(cfg.channels)
        try:
            cfgmod.validate(cfg)
        except ValueError as e:
            QtWidgets.QMessageBox.critical(self, "Can't use that input", str(e))
            return False
        from . import gui                          # the window class (this module is part of it)
        win = gui.MainWindow(cfg, settings=self.settings, config_path=self.config_path)
        win.setGeometry(self.geometry())
        gui._WINDOWS.append(win)                           # kept alive after this one closes
        self.back_to_live()
        self._switched_to = win
        win.show()
        self.close()
        win.statusBar().showMessage(
            f"Input: {device or backend}. Press Start." + (" macOS asks once to allow the microphone."
                                                            if backend == "audio" else ""), 10000)
        return True
