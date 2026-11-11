"""The main window's comments: typing them while recording, the Comments panel (edit,
delete, add at a time), and their marks on the plots."""
from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui

from . import trialfile
from . import theme
from .comments import CommentPopup
from .mainparts import LIVE, REVIEW


class CommentsMixin:
    def _add_comment(self):
        text = self.comment_edit.text().strip()
        if not text or not self.recorder.recording:
            return
        self._log_comment(text)
        self.comment_edit.clear()

    def _log_comment(self, text: str) -> None:
        t = self.recorder.add_comment(text)      # saved to the block straight away
        self._add_marker(t - self._axis_zero, text)
        self.refresh_comments(redraw_markers=False)

    def _set_comment_box(self, recording: bool):
        self.comment_edit.setEnabled(recording)
        self.comment_edit.setPlaceholderText("Press Enter to add comment" if recording
                                             else "Press Record to add comments")

    def _focus_comment(self):
        if self.mode == LIVE and self.comment_edit.isEnabled():
            self.comment_edit.setFocus()
            self.comment_edit.selectAll()

    # -- comments panel ----------------------------------------------------
    def _toggle_comments(self, on: bool):
        self.comments_panel.setVisible(on)
        self.settings.setValue("comments_open", on)

    def _comment_block(self) -> Path | None:
        """The block whose comments the panel shows: the reviewed block in review;
        in live, the block being recorded, else the last one recorded."""
        if self.mode == REVIEW:
            i = self.block_combo.currentIndex()
            return self.review_blocks[i] if 0 <= i < len(self.review_blocks) else None
        if self.recorder.recording:
            return self.recorder.path
        return self.blocks[-1] if self.blocks else None

    def _read_block_comments(self, block: Path) -> list[dict]:
        if self.recorder.recording and block == self.recorder.path:
            return self.recorder.get_comments()
        try:
            return trialfile.read_comments(block)
        except Exception:
            return []

    def refresh_comments(self, redraw_markers: bool = True) -> None:
        """Reload the panel (and the comment lines on the plots) from the block file."""
        block = self._comment_block()
        self._shown_comments = self._read_block_comments(block) if block else []
        self.comments_panel.set_comments(f"Comments · {block.stem}" if block else "Comments",
                                         [(c["t"], c["text"]) for c in self._shown_comments],
                                         editable=block is not None)
        self.btn_comments.setText(f"Comments ({len(self._shown_comments)})")
        if redraw_markers:
            self._redraw_comment_markers()

    def _redraw_comment_markers(self):
        self._clear_markers()
        if self.mode == REVIEW:
            offset = 0.0
        elif (self._block_on_screen and self.recorder.path is not None
              and self._comment_block() == self.recorder.path):
            offset = self.recorder.block_start - self._axis_zero
        else:
            return                               # that block isn't on screen
        for c in self._shown_comments:
            self._add_marker(c["t"] + offset, c["text"])

    def _save_comments(self, comments: list[dict]) -> None:
        block = self._comment_block()
        if block is None:
            return
        try:
            if self.recorder.recording and block == self.recorder.path:
                self.recorder.set_comments(comments)
            else:
                trialfile.write_comments(block, comments)
        except Exception as e:
            self._channel_message(f"Couldn't save the comment change in {block.name} "
                                  f"(read-only or open elsewhere?): {e}")
        if self.mode == REVIEW:
            self._review_mat_stale = True
        elif self.session_saved:
            self._live_comments_dirty = True
        self.refresh_comments()

    def edit_comment(self, row: int, text: str) -> None:
        comments = [dict(c) for c in self._shown_comments]
        if 0 <= row < len(comments) and text.strip():
            comments[row]["text"] = text.strip()
            self._save_comments(comments)

    def delete_comment(self, row: int) -> None:
        if 0 <= row < len(self._shown_comments):
            self._save_comments([c for i, c in enumerate(self._shown_comments) if i != row])

    def add_comment_at(self, t: float, text: str) -> None:
        """Add a comment at time t (s from the block's start), e.g. from a double-click in review."""
        if not text.strip() or self._comment_block() is None:
            return
        fs = (self.review_block or {}).get("sample_rate") if self.mode == REVIEW else self.backend.actual_rate
        fs = fs or self.cfg.sample_rate
        self._save_comments(self._shown_comments + [
            {"t": float(t), "sample_index": int(round(t * fs)), "text": text.strip()}])

    def jump_to(self, t: float) -> None:
        """Centre the review plots on a comment."""
        if self.mode != REVIEW or not self.plots:
            return
        win = self.window_box.value()
        start = max(0.0, t - win / 2)
        self.plots[0].setXRange(start, start + win, padding=0)

    def _plot_clicked(self, pw, ev):
        if self.mode != REVIEW or not ev.double() or self.review_block is None:
            return
        vb = pw.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        t = min(max(0.0, vb.mapSceneToView(ev.scenePos()).x()), self.review_block["duration_s"])
        self._popup = CommentPopup(self, t)
        self._popup.submitted.connect(self.add_comment_at)
        self._popup.show_at(QtGui.QCursor.pos())

    def _add_marker(self, t: float, text: str):
        lines = []
        # Stagger labels over three heights so nearby comments don't print on top of each
        # other, and keep them short on the plot (the Comments list has the full text).
        self._marker_count = getattr(self, "_marker_count", 0) + 1
        label_y = 0.92 - 0.09 * ((self._marker_count - 1) % 3)
        short = text if len(text) <= 32 else text[:31].rstrip() + "…"
        for p in self.plots:
            mc = theme.THEMES[self.plot_mode].marker
            ln = pg.InfiniteLine(t, angle=90, pen=pg.mkPen(mc, style=QtCore.Qt.PenStyle.DashLine),
                                 label=short if p is self.plots[0] else None,
                                 labelOpts={"position": label_y, "color": mc})
            p.addItem(ln)
            lines.append(ln)
        self.markers.append((t, lines))

    def _clear_markers(self):
        self._marker_count = 0
        for _, lines in self.markers:
            for p, ln in zip(getattr(self, "plots", []), lines):
                p.removeItem(ln)
        self.markers = []
