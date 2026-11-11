"""The main window's sessions and files: live and review, New, Open, Save (one .newt file),
Save As, Append, Export, recent files, asking before anything unsaved is left, and what's
on screen for an .newt file (app_state)."""
from __future__ import annotations

import dataclasses
import shutil
import time
import warnings
from datetime import datetime
from pathlib import Path

from pyqtgraph.Qt import QtCore, QtWidgets

from . import matfile
from . import session
from . import trialfile
from . import protocols as protocolsmod
from . import simulation as sim
from . import newtfile
from .channels import ChannelRow
from .config import Channel, valid_inputs
from .export import to_labchart_mat
from .stim import Stimulus
from . import colours
from .mainparts import LIVE, REVIEW


class SessionFilesMixin:
    # ======================================================================
    # Modes
    # ======================================================================
    def _set_mode(self, mode: str):
        self.mode = mode
        self._update_toolbar()
        self.scroll.setVisible(mode == REVIEW)
        self.channel_bar.setVisible(mode == LIVE)
        self.wiring_menu.setEnabled(mode == LIVE and not self.recorder.recording)
        fw = self.focusWidget()
        if fw is not None:      # don't leave a box (e.g. Window) highlighted after switching
            fw.clearFocus()
        self._update_title()
        self._update_wiring_label()
        if hasattr(self, "comments_panel"):
            self.refresh_comments()
        if hasattr(self, "camera_panel"):
            self.stop_playback()
            self.camera_panel.set_live(mode == LIVE)
        if getattr(self, "_monitor", None) is not None and mode == REVIEW:
            self._monitor.stop()                    # review: Play plays the channel instead
            self._monitor = None
        elif mode == LIVE and getattr(self, "_listen", "") and getattr(self, "_monitor", None) is None:
            self.set_listen(self._listen)           # back to live: listening again

    def _update_title(self):
        newt = self._newt()
        if newt is not None:
            where = f"{newt.name}{'*' if self._has_unsaved() else ''} — {'Review' if self.mode == REVIEW else 'Live'}"
        elif self.mode == REVIEW and self._review_source is not None:
            where = f"{self._review_source.name} (imported copy) — Review"
        elif self.mode == REVIEW and self.review_dir is not None:
            where = f"{self.review_dir.name} — Review"
        elif self.session_dir is not None and self.session_saved:
            where = f"{self.session_dir.name} — Live"
        elif self.blocks:
            where = "Untitled session* — Live"
        else:
            where = "Untitled session — Live"
        self.setWindowTitle(f"{where} — Newt ({self.cfg.device}, {self.cfg.backend})")

    def _update_menu_state(self):
        recording = self.recorder.recording
        for a in (self.act_new, self.act_open, self.act_open_folder, self.act_open_mat, self.act_append):
            a.setEnabled(not recording)
        self.act_save.setEnabled(bool(self._export_blocks()) and not recording)
        self.act_save_folder.setEnabled(bool(self._export_blocks()) and not recording)
        self.act_save_as.setEnabled(bool(self._export_blocks()) and not recording)
        self.act_export.setEnabled(bool(self._export_blocks()) and not recording)

    def _unsaved_dir(self) -> Path:
        return Path(self.cfg.data_dir) / "unsaved"

    def _has_unsaved(self) -> bool:
        if self._newt() is not None:          # changed since it was saved in its .newt file
            return newtfile.files_of(self._export_folder()) != self._newt_files
        return self.session_dir is not None and not self.session_saved and bool(self.blocks)

    def _newt(self) -> Path | None:
        """The .newt file of the session showing, if it has one."""
        folder = self._export_folder()
        return self.newt_path if folder is not None and folder == self._newt_folder else None

    def _ask_unsaved(self) -> str:
        """Ask what to do with an unsaved session: 'save', 'discard' or 'cancel'."""
        B = QtWidgets.QMessageBox.StandardButton
        r = QtWidgets.QMessageBox.question(
            self, "Unsaved session",
            f"This session ({len(self.blocks)} trial(s)) hasn't been saved.\n\n"
            f"Save it now? If you don't, its trials stay in\n{self.session_dir}",
            B.Save | B.Discard | B.Cancel, B.Save)
        return {B.Save: "save", B.Discard: "discard"}.get(r, "cancel")

    def _ask_unsaved_analysis(self) -> str:
        """Ask what to do with analysis that isn't saved: 'save', 'discard' or 'cancel'."""
        B = QtWidgets.QMessageBox.StandardButton
        r = QtWidgets.QMessageBox.question(
            self, "Unsaved analysis", "The spikes found, units or measure windows have changed since they were saved.\n\n"
            "Save them with the session?", B.Save | B.Discard | B.Cancel, B.Save)
        return {B.Save: "save", B.Discard: "discard"}.get(r, "cancel")

    def _ask_unsaved_file(self) -> str:
        """Ask what to do with changes not in the .newt file: 'save', 'discard' or 'cancel'."""
        B = QtWidgets.QMessageBox.StandardButton
        r = QtWidgets.QMessageBox.question(
            self, "Unsaved changes", f"{self._newt().name} has changed since it was saved.\n\nSave the changes?",
            B.Save | B.Discard | B.Cancel, B.Save)
        return {B.Save: "save", B.Discard: "discard"}.get(r, "cancel")

    def _resolve_unsaved(self) -> bool:
        """True if it's OK to leave the current session."""
        if self._newt() is not None:
            if not (self._has_unsaved() or self.analysis_page.has_unsaved_analysis()):
                return True
            choice = self._ask_unsaved_file()
            return self.save() if choice == "save" else choice == "discard"
        if not self.analysis_page.resolve_unsaved():
            return False
        if not self._has_unsaved():
            return True
        choice = self._ask_unsaved()
        if choice == "save":
            return self.save()
        if choice == "discard":
            self.statusBar().showMessage(f"Unsaved trials left in {self.session_dir}", 10000)
            return True
        return False

    def _leave_live(self) -> bool:
        """Stop acquisition before switching sessions. Refuses while recording."""
        if self.recorder.recording:
            QtWidgets.QMessageBox.information(self, "Recording in progress",
                                              "Stop recording before switching sessions.")
            return False
        if self.btn_run.isChecked():
            self.btn_run.setChecked(False)
        if not self._resolve_unsaved():
            return False
        if self.session_saved:
            self._autoexport_live()
        return True

    def leave_review(self) -> bool:
        """Back to live from review. From this live session's own blocks, carry on with
        it. From a dataset that was opened, ask first: add new blocks to it, or start a
        new session (the dataset is kept either way)."""
        state = self._live_state
        if self.review_dir is None or (state is not None and self.review_dir == state[0]):
            self.back_to_live()
            return True
        path = self.review_dir
        choice = self._ask_leave_review(path)
        if choice == "new":
            self.back_to_live()
            self.statusBar().showMessage(f"New session: {path.name} is kept as it was. Press Record to start.", 10000)
            return True
        if choice == "append":
            return self.append_to_reviewed()
        return False

    def _ask_leave_review(self, path: Path) -> str | None:
        """"append", "new" or None (cancel)."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setWindowTitle("Back to live")
        box.setText(f"You're looking at {path.name}. What should new recordings do?")
        box.setInformativeText(f"Add to {path.name}: new trials go after its {len(self.review_blocks)} trial(s), "
                               "in the same session.\nNew session: start an empty one; "
                               f"{path.name} is kept as it is.")
        add = box.addButton(f"Add to {path.name}", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        new = box.addButton("New session", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(add)
        box.exec()
        return "append" if box.clickedButton() is add else "new" if box.clickedButton() is new else None

    def append_to_reviewed(self) -> bool:
        """Go live adding to the dataset being reviewed. If it was recorded with other
        channels or another sampling rate, offer to switch to its setup first."""
        path = self.review_dir
        problems, _ = session.append_check(path, self.cfg)
        if problems:
            if not self._ask_adopt_setup(path, problems):
                return False
            if not self.adopt_session_setup(path):
                return False
        try:
            self.append_session(path, force=True)
        except session.SessionError as e:
            QtWidgets.QMessageBox.critical(self, "Can't add to this session", str(e))
            return False
        if path.resolve().parent == (Path(self.cfg.data_dir) / "imported").resolve():
            self.statusBar().showMessage(
                f"Adding to {path.name}. It came from a .mat file, which isn't changed: use Save as… or "
                "Export to write the new trials to a .mat.", 15000)
        return True

    def _ask_adopt_setup(self, path: Path, problems: list[str]) -> bool:
        info = trialfile.block_info(session.list_blocks(path)[-1])
        chans = ", ".join(info["channel_names"])
        ok = QtWidgets.QMessageBox.question(
            self, "Use this dataset's setup?",
            f"{path.name} was recorded with a different setup:\n\n• " + "\n• ".join(problems)
            + f"\n\nSwitch to its setup ({chans} at {info['sample_rate']:,.0f} Hz) and add to it?")
        return ok == QtWidgets.QMessageBox.StandardButton.Yes

    def adopt_session_setup(self, path: str | Path) -> bool:
        """Take the channels (names, inputs, units, scale, colours), sampling rate and
        wiring the session was recorded with, so new blocks match it."""
        info = trialfile.block_info(session.list_blocks(path)[-1])
        self.back_to_live()
        inputs = valid_inputs(self.cfg)
        rows = []
        for k, name in enumerate(info["channel_names"]):
            phys = info["physical"][k] or (inputs[k] if k < len(inputs) else f"ai{k}")
            rows.append(ChannelRow(Channel(phys, name, units=info["units"][k], scale=info["scale"][k],
                                           offset=info["offset"][k], color=info["colors"][k])))
        return self.apply_setup(rows=rows, sample_rate=info["sample_rate"],
                                terminal_config=info["terminal_config"] or None)

    def back_to_live(self):
        """Leave review. The next Record starts a new untitled session, unless the
        review was of this live session's own blocks (Recorded blocks): then it
        carries on with that session."""
        state, self._live_state = self._live_state, None
        own = state is not None and self.review_dir == state[0]
        self._overlay_sel = []
        self._flush_review_mat()
        self._live_comments_dirty = False
        self.review_dir, self.review_blocks, self.review_block = None, [], None
        self._review_source = None
        self.session_dir, self.blocks, self._exported_n = None, [], 0
        self.session_saved = False
        self._recolor_policy = None          # a new session asks again
        if own:
            (self.session_dir, self.blocks, self.session_saved, self._exported_n,
             self._live_comments_dirty, self._recolor_policy) = state
            self._live_comments_dirty = self._live_comments_dirty or self.session_saved
        self._build_live_plots()
        self._set_mode(LIVE)
        self._fit_window()
        self.status.setText("")
        self._fill_recorded()
        self.analysis_page.source_changed()

    # ======================================================================
    # File menu: dialogs (thin wrappers around the methods below)
    # ======================================================================
    def _start_dir(self) -> str:
        Path(self.cfg.data_dir).mkdir(parents=True, exist_ok=True)
        return str(Path(self.cfg.data_dir).resolve())

    def save_as_dialog(self) -> bool:
        if self.recorder.recording:
            QtWidgets.QMessageBox.information(self, "Recording in progress", "Stop recording before saving.")
            return False
        src = self._export_folder()
        if not self._export_blocks():
            QtWidgets.QMessageBox.information(self, "Nothing to save", "Record a trial first.")
            return False
        name = src.name if (self.session_saved or self.mode == REVIEW) else \
            datetime.now().strftime("session_%Y%m%d_%H%M%S")
        default = Path(self._start_dir()) / name
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save session as", str(default), "Session folder (*)",
            options=QtWidgets.QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return False
        try:
            self.save_as(path)
            return True
        except session.SessionError as e:
            QtWidgets.QMessageBox.critical(self, "Could not save", str(e))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Could not save", repr(e))
        return False

    def open_session_dialog(self):
        if not self._leave_live():
            return
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Open session folder", self._start_dir())
        if path:
            self._try(lambda: self.open_session(path), "Could not open session")

    def open_mat_dialog(self):
        if not self._leave_live():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open .mat file", self._start_dir(), "LabChart / MATLAB export (*.mat);;All files (*)")
        if path:
            self._try(lambda: self.open_mat(path), "Could not open the .mat file")

    def append_session_dialog(self):
        if not self._leave_live():
            return
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Append to session folder", self._start_dir())
        if not path:
            return
        problems, warnings = session.append_check(path, self.cfg)
        if problems:
            QtWidgets.QMessageBox.critical(
                self, "Can't append to this session",
                "New trials wouldn't match the existing ones:\n\n• " + "\n• ".join(problems)
                + "\n\nUse New instead, or load the config this session was recorded with.")
            return
        if warnings:
            ok = QtWidgets.QMessageBox.question(
                self, "Append anyway?", "\n".join(warnings) + "\n\nAppend with the new settings?")
            if ok != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        if not self._confirm_append(Path(path)):
            return
        self._try(lambda: self.append_session(path, force=True), "Could not append")

    def _append_notes(self, path: Path) -> list[str]:
        """What happens on Append, including colours that will change."""
        notes = [
            "New trials must use the same channel names, inputs, units, sampling rate and "
            "wiring as the trials already in this session. (They do now; if you change any of "
            "them after appending, a new session is started instead.)",
            "Channels are matched by name, so don't rename a channel while appending.",
        ]
        used = session.session_colors(path)
        changed = [f"{r.ch.name} → {colours.color_label(used[r.ch.name])}" for r in self.rows
                   if r.ch.name in used and used[r.ch.name] != r.ch.color]
        if changed:
            notes.append("To match the session, these channels will switch colour: " + ", ".join(changed) + ".")
        return notes

    def _confirm_append(self, path: Path) -> bool:
        """Explain the rules for appending before doing it (can be turned off)."""
        if self.settings.value("skip_append_notice", False, type=bool):
            return True
        B = QtWidgets.QMessageBox.StandardButton
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Icon.Information, "Append to session",
            f"Appending to {path.name}.\n\n• " + "\n• ".join(self._append_notes(path)),
            B.Ok | B.Cancel, self)
        box.setDefaultButton(B.Ok)
        skip = QtWidgets.QCheckBox("Don't show this again")
        box.setCheckBox(skip)
        ok = box.exec() == B.Ok
        if ok and skip.isChecked():
            self.settings.setValue("skip_append_notice", True)
        return ok

    def export_dialog(self):
        blocks, folder = self._export_blocks(), self._export_folder()
        if not blocks:
            QtWidgets.QMessageBox.information(self, "Nothing to export", "Record a trial or open a session first.")
            return
        default = folder / f"{folder.name}.mat"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export .mat", str(default),
                                                        "MATLAB file (*.mat)")
        if path:
            self.export(path)

    def _try(self, fn, title):
        try:
            fn()
        except session.SessionError as e:
            QtWidgets.QMessageBox.critical(self, title, str(e))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, title, repr(e))

    # ======================================================================
    # File menu: actions (no dialogs, so they can be tested directly)
    # ======================================================================
    def new_session(self) -> bool:
        """Start a blank, untitled session. Asks first if the current one is unsaved."""
        if not self._leave_live():
            return False
        self.back_to_live()
        self.statusBar().showMessage("New session. Press Record to start; use Save to name it.", 10000)
        return True

    def save(self) -> bool:
        """Save everything in the session's .newt file; the first time, ask where."""
        if self.recorder.recording:
            QtWidgets.QMessageBox.information(self, "Recording in progress", "Stop recording before saving.")
            return False
        newt = self._newt()
        if newt is None:
            return self.save_file_dialog()
        return self._save_file(newt)

    def save_file_dialog(self) -> bool:
        """Ask where to save the .newt file, and save it there."""
        if self.recorder.recording:
            QtWidgets.QMessageBox.information(self, "Recording in progress", "Stop recording before saving.")
            return False
        if not self._export_blocks():
            QtWidgets.QMessageBox.information(self, "Nothing to save", "Record a trial first.")
            return False
        src = self._export_folder()
        name = self._newt().stem if self._newt() is not None else \
            src.name if (self.session_saved or self.mode == REVIEW) else datetime.now().strftime("session_%Y%m%d_%H%M%S")
        default = Path(self._start_dir()) / (name + newtfile.SUFFIX)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save as an Newt file", str(default),
                                                        f"Newt file (*{newtfile.SUFFIX})")
        if not path:
            return False
        path = Path(path)
        return self._save_file(path if path.suffix == newtfile.SUFFIX else path.with_name(path.name + newtfile.SUFFIX))

    def _save_file(self, path: Path) -> bool:
        try:
            self.save_newt(path)
            return True
        except (newtfile.NewtError, session.SessionError, OSError) as e:
            QtWidgets.QMessageBox.critical(self, "Could not save", str(e))
        return False

    def save_session_files(self) -> Path | None:
        """Write what the session keeps beside its trials that's still only in memory, so its
        folder holds all of it: the analysis (analysis.json, analysis_spikes.npz), and its
        exported .mat, if it has one, brought up to date. The groups (pages.json) and edited
        comments and colours (trial_edits.json) are written as they change. Every way of
        saving (Save, Save As, a session folder) goes through here."""
        folder = self._export_folder()
        if folder is None:
            return None
        self.analysis_page.save_analysis()
        self._flush_review_mat()
        return folder

    def save_newt(self, path: str | Path) -> Path:
        """Save everything in one .newt file: the session's recordings and the files beside
        them (analysis included), and what's on screen. Later saves add only what changed."""
        if self.recorder.recording:
            raise session.SessionError("Stop recording before saving.")
        folder = self._export_folder()
        if folder is None or not self._export_blocks():
            raise newtfile.NewtError("Nothing recorded yet: there's nothing to save.")
        path = Path(path).resolve()
        self.save_session_files()
        self._newt_files = newtfile.save(path, folder, self.app_state())
        self.newt_path, self._newt_folder = path, folder
        self._remember(path)
        self._update_title()
        self.statusBar().showMessage(f"Saved {path.name}", 8000)
        return path

    def open_file_dialog(self):
        if not self._leave_live():
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open an Newt file", self._start_dir(),
                                                        f"Newt file (*{newtfile.SUFFIX} *.argus);;All files (*)")
        if path:
            self._try(lambda: self.open_file(path), "Could not open the file")

    def open_file(self, path: str | Path) -> Path:
        """Open an .newt file: its session in review, and Newt as it was when saved."""
        path = Path(path).resolve()
        folder, state, files = newtfile.open_file(path, self.cfg.data_dir)
        self.open_session(folder, remember=False)
        self.newt_path, self._newt_folder, self._newt_files = path, folder, files
        self.restore_app_state(state)
        self._remember(path)
        self._update_title()
        self.statusBar().showMessage(f"Opened {path.name}", 8000)
        return folder

    def open_requested(self, path: str) -> None:
        """A file given to Newt to open (double-clicked, or on the command line)."""
        if self._leave_live():
            opener = self.open_file if newtfile.is_session_file(path) else \
                self.open_mat if path.lower().endswith(".mat") else self.open_session
            self._try(lambda: opener(path), "Could not open it")

    def app_state(self) -> dict:
        """What's on screen and set, for an .newt file: the tabs, Analysis, the stimulus, the
        protocol, and the simulated network."""
        d = {"version": newtfile.VERSION, "tabs": {"shown": list(self.tabs.shown), "layout": self.tabs.layout_mode},
             "analysis": self.analysis_page.app_state(), "stimulus": self.stimulus.to_dict(),
             "protocol": self.trials_page.protocol.to_dict()}
        if self.cfg.backend == "simulated":
            d["sim"] = dataclasses.asdict(self.cfg.sim_network)
        return d

    def restore_app_state(self, d: dict) -> None:
        """Back as an .newt file was saved. A part that can't be put back is skipped, and said."""
        skipped = []

        def part(name, fn):
            try:
                fn()
            except Exception as e:              # an older or damaged part: the rest still opens
                skipped.append(f"{name} ({e})")
        part("the stimulus", lambda: self.set_stimulus(Stimulus.from_dict(d["stimulus"])))

        def protocol():
            p = protocolsmod.Protocol.from_dict(d["protocol"])
            self.trials_page.protocol = p
            self.trials_page._load_protocol_into_widgets(p)
        part("the protocol", protocol)
        if "sim" in d and self.cfg.backend == "simulated" and self.sim_page is not None:
            def network():
                self.cfg.sim_network = sim.network_from_dict(d["sim"])
                self.sim_page.load()
            part("the simulation", network)
        part("Analysis", lambda: self.analysis_page.restore_app_state(d["analysis"]))

        def tabs():
            shown = [p for p in d["tabs"]["shown"] if p in self.tabs.pages]
            if len(shown) == 2:
                self.tabs.shown = shown
                self.tabs.set_layout(d["tabs"]["layout"])
        part("the tabs", tabs)
        if skipped:
            self.statusBar().showMessage("Couldn't put back " + ", ".join(skipped), 15000)

    def save_as(self, path: str | Path) -> Path:
        """Save the current session (live or reviewed) to `path` and continue there.

        An unsaved session is moved out of data/unsaved/; a saved one is copied,
        so the original stays where it was.
        """
        path = Path(path).resolve()
        if self.recorder.recording:
            raise session.SessionError("Stop recording before saving.")
        src = self._export_folder()
        if src is None or not self._export_blocks():
            raise session.SessionError("Nothing recorded yet.")
        src = src.resolve()
        self.save_session_files()
        if path == src:
            if self.mode == LIVE:
                self.export()
            return path
        if path.exists() and any(path.iterdir()):
            raise session.SessionError(f"{path} already exists and isn't empty. Choose another name.")
        if path.exists():
            path.rmdir()
        path.parent.mkdir(parents=True, exist_ok=True)
        moving = self.mode == LIVE and not self.session_saved
        if moving:
            shutil.move(str(src), str(path))
        else:
            shutil.copytree(src, path)
        stale = path / f"{src.name}.mat"  # .mat named after the old folder
        if stale.exists():
            stale.unlink()

        if self.mode == REVIEW:
            self.open_session(path)
        else:
            self.session_dir = path
            self.blocks = session.list_blocks(path)
            self.session_saved = True
            self._exported_n = 0
            self.export()
            self._remember(path)
            self._update_title()
        self.statusBar().showMessage(f"Saved to {path}", 10000)
        return path

    def open_session(self, path: str | Path, remember: bool = True):
        """Review a saved session."""
        self._live_state = None
        self._overlay_sel = []
        path = Path(path)
        blocks = session.list_blocks(path)
        if not blocks:
            raise session.SessionError(f"{path} has no block_NNN.h5 files, so it isn't a session.")
        if self.btn_run.isChecked():
            self.btn_run.setChecked(False)
        self._flush_review_mat()
        self._review_source = None
        self.review_dir, self.review_blocks = path, blocks
        self._set_mode(REVIEW)
        self.block_combo.blockSignals(True)
        self.block_combo.clear()
        for b in blocks:
            try:
                info = self.store.info(b)
                self.block_combo.addItem(f"{b.stem}  ({info['duration_s']:.1f} s)")
            except Exception:
                self.block_combo.addItem(f"{b.stem}  (unreadable)")
        self.block_combo.blockSignals(False)
        if remember:
            self._remember(path)
        self.statusBar().showMessage(f"Opened {path.name} ({len(blocks)} trial(s)) for review", 10000)
        self._show_review_block(0)
        self._fill_recorded()
        self.analysis_page.source_changed()

    def open_mat(self, path: str | Path) -> Path:
        """Review a LabChart-style .mat. It's turned into a session folder under
        <data_dir>/imported (reused if the .mat hasn't changed), so comments and
        colours can be edited without touching the original file."""
        path = Path(path)
        try:
            folder, notes = matfile.import_mat(path, Path(self.cfg.data_dir) / "imported")
        except matfile.MatError as e:
            raise session.SessionError(str(e)) from None
        self.open_session(folder)
        self._review_source = path
        recent = [r for r in self.recent_sessions() if r != str(folder.resolve())]
        self.settings.setValue("recent_sessions", session.add_recent(recent, path))
        self._update_title()
        msg = (f"Opened {path.name} ({len(self.review_blocks)} trial(s)). The .mat itself isn't changed: "
               f"comments and colours are kept in its imported copy, {folder}.")
        if notes:
            msg += " " + " ".join(notes)
        self.statusBar().showMessage(msg, 20000)
        return folder

    def append_session(self, path: str | Path, force: bool = False) -> Path:
        """Continue recording new blocks into an existing session."""
        path = Path(path)
        problems, warnings = session.append_check(path, self.cfg)
        if problems or (warnings and not force):
            raise session.SessionError("\n".join(problems + warnings))
        self.back_to_live()
        # New blocks match the colours already used in this session.
        used = session.session_colors(path)
        for r in self.rows:
            if r.ch.name in used:
                r.ch.color = used[r.ch.name]
        self._build_live_plots()
        self.session_dir = path
        self.session_saved = True
        self.blocks = session.list_blocks(path)
        self._exported_n = len(self.blocks)  # re-export only if new blocks are added
        self._remember(path)
        self._update_title()
        self.statusBar().showMessage(
            f"Appending to {path.name}: next trial is {session.next_block_path(path).name}", 10000)
        return path

    def export(self, out_path: str | Path | None = None) -> Path | None:
        """Write the current session's blocks (live or review) to a .mat file."""
        if self.recorder.recording:
            self.btn_rec.setChecked(False)
        blocks, folder = self._export_blocks(), self._export_folder()
        if not blocks:
            return None
        out = Path(out_path) if out_path else folder / f"{folder.name}.mat"
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            to_labchart_mat(blocks, out)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Export failed", repr(e))
            return None
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if self.mode == LIVE:
            self._exported_n = len(self.blocks)
            self._live_comments_dirty = False
        else:
            self._review_mat_stale = False
        self.statusBar().showMessage(f"Exported {len(blocks)} trial(s) to {out}", 10000)
        return out

    def _export_blocks(self) -> list[Path]:
        return self.review_blocks if self.mode == REVIEW else self.blocks

    def _export_folder(self) -> Path | None:
        return self.review_dir if self.mode == REVIEW else self.session_dir

    def _autoexport_live(self):
        # Don't lose the .mat if they forgot: new blocks, or comments edited since.
        if len(self.blocks) > self._exported_n or self._live_comments_dirty:
            prev_mode, self.mode = self.mode, LIVE
            if self.export() is not None:
                self._live_comments_dirty = False
            self.mode = prev_mode

    def _flush_review_mat(self):
        """After editing comments in review, update the session's .mat if it has one."""
        if self.mode == REVIEW and self._review_mat_stale and self.review_dir is not None:
            if (self.review_dir / f"{self.review_dir.name}.mat").exists():
                self.export()
        self._review_mat_stale = False

    # -- recent sessions ---------------------------------------------------
    def recent_sessions(self) -> list[str]:
        v = self.settings.value("recent_sessions", [])
        if isinstance(v, str):  # QSettings returns a bare string for 1-item lists
            v = [v]
        return list(v or [])

    def _remember(self, path: Path):
        self.settings.setValue("recent_sessions", session.add_recent(self.recent_sessions(), path))

    def _fill_recent_menu(self):
        self.recent_menu.clear()
        recent = self.recent_sessions()
        if not recent:
            a = self.recent_menu.addAction("(none yet)")
            a.setEnabled(False)
            return
        for i, p in enumerate(recent):
            self.recent_menu.addAction(f"&{i + 1}  {p}", lambda p=p: self.open_recent(p))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear List", lambda: self.settings.setValue("recent_sessions", []))

    def open_recent(self, path: str):
        if newtfile.is_session_file(path):
            if not Path(path).is_file():
                self.settings.setValue("recent_sessions", [r for r in self.recent_sessions() if r != path])
                QtWidgets.QMessageBox.warning(self, "File not found",
                                              f"{path} no longer exists. It has been removed from the list.")
            elif self._leave_live():
                self._try(lambda: self.open_file(path), "Could not open the file")
            return
        if path.lower().endswith(".mat"):
            if not Path(path).is_file():
                self.settings.setValue("recent_sessions", [r for r in self.recent_sessions() if r != path])
                QtWidgets.QMessageBox.warning(self, "File not found",
                                              f"{path} no longer exists. It has been removed from the list.")
            elif self._leave_live():
                self._try(lambda: self.open_mat(path), "Could not open the .mat file")
            return
        if not session.is_session(path):
            self.settings.setValue("recent_sessions", [r for r in self.recent_sessions() if r != path])
            QtWidgets.QMessageBox.warning(self, "Session not found",
                                          f"{path} no longer exists or has no trials. "
                                          "It has been removed from the list.")
            return
        if self._leave_live():
            self._try(lambda: self.open_session(path), "Could not open session")
