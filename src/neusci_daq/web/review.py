"""Record's review on the HTML front end, as the Qt app's (reviewview.py, commentcontrol.py):
a session's trials, one at a time, each whole at first, with its calculated channels
(worked out on the whole trial: filters forwards and backwards, no delay), its comments
(edit, delete, add one at a time) and, if the Qt app recorded one with it, its video.
Back to Live leaves the session as it was: the next trial still goes where Record says.
Acquiring carries on meanwhile; the trial being recorded isn't shown.

Comments typed while recording can be changed too (the trial being recorded's: in its
file); a recorded trial's are kept beside it, in trial_edits.json, as Qt keeps them.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from .. import colors, dsp, groupsfile, session, trialfile, videofile
from .live import envelope, pack
from .trials import session_name

COMMANDS = ("review_open", "review_show", "review_close", "comment_add", "comment_set", "comment_delete")


class Review:
    def __init__(self, live):
        self.live = live
        self.active = False
        self.folder: Path | None = None
        self.blocks: list[Path] = []
        self.hidden: set[str] = set()         # trials left out of Analysis (split into others)
        self.i = -1
        self.blk: dict | None = None          # the trial shown (trialfile.read_block)
        self.calc: list = []                  # its calculated channels: [(Calculation, y or None, problem)]
        self.comments: list[dict] = []        # its comments: [{t, sample_index, text}]
        self.info: dict = {}
        self.video: tuple[Path, float] | None = None
        self._lock = threading.RLock()

    # -- commands from the page ---------------------------------------------------------
    def command(self, c: dict) -> dict:
        cmd = c.get("cmd")
        with self._lock:
            if cmd == "review_open":
                self.open(c.get("folder") or None, c.get("i"))
            elif cmd == "review_show":
                if not self.active:
                    raise ValueError("Not reviewing: press Review first.")
                self.show(int(c["i"]))
            elif cmd == "review_close":
                self.active, self.blk, self.calc = False, None, []
            elif cmd in ("comment_add", "comment_set", "comment_delete"):
                self._comment(cmd, c)
        return {}

    def open(self, folder: str | None, i=None) -> None:
        """Review a session's trials (the one Record adds to, if no folder is given)."""
        rec = self.live.recording
        folder = Path(folder) if folder else (rec.folder if rec is not None else None)
        if folder is None:
            raise ValueError("No trials yet: record one first, or pick a session.")
        blocks = session.list_blocks(folder)
        if rec is not None and rec.rec.recording:         # (being written)
            blocks = [b for b in blocks if b != rec.rec.path]
        if not blocks:
            raise ValueError(f"No trials in {folder.name} yet.")
        self.folder, self.blocks = folder, blocks
        try:
            self.hidden = set(groupsfile.load(folder)["hidden_trials"])
        except Exception:
            self.hidden = set()
        self.active = True
        self.show(len(blocks) - 1 if i is None else int(i))

    def show(self, i: int) -> None:
        i = max(0, min(len(self.blocks) - 1, i))
        path = self.blocks[i]
        blk = trialfile.read_block(path)
        names, fs = blk["channel_names"], blk["sample_rate"]
        chain = dsp.Chain(self.live.cfg.calculations, names, fs)
        ys = chain.offline(dict(zip(names, blk["data"])), fs) if chain.calcs else []
        self.calc = list(zip(chain.calcs, ys, chain.problems))
        self.blk, self.i = blk, i
        self.comments = trialfile.read_comments(path)
        try:
            self.info = trialfile.block_info(path)
        except Exception:
            self.info = {}
        self.video = videofile.video_of(path)

    # -- comments ------------------------------------------------------------------------
    def _comment(self, cmd: str, c: dict) -> None:
        """Change a comment: the live trial's (c["live"]: the one being recorded) or the
        reviewed one's."""
        rec = self.live.recording
        if c.get("live"):
            if rec is None or not rec.rec.recording:
                raise ValueError("No trial is being recorded.")
            with rec._lock:
                comments = rec.rec.get_comments()
                comments = self._changed(cmd, c, comments, self.live.backend.actual_rate)
                rec.rec.set_comments(comments)
                rec.comments = [(float(x["t"]), str(x["text"])) for x in sorted(comments, key=lambda x: x["t"])]
            return
        if not self.active or self.blk is None:
            raise ValueError("Not reviewing a trial.")
        comments = self._changed(cmd, c, [dict(x) for x in self.comments], self.blk["sample_rate"])
        path = self.blocks[self.i]
        try:
            trialfile.write_comments(path, comments)
        except OSError as e:
            raise ValueError(f"Couldn't save the comment change in {path.name} (read-only or open elsewhere?): {e}")
        self.comments = trialfile.read_comments(path)
        if rec is not None:
            rec._saved(path.parent)             # Analysis shows it

    @staticmethod
    def _changed(cmd: str, c: dict, comments: list[dict], fs: float) -> list[dict]:
        comments = sorted(comments, key=lambda x: x["t"])
        text = str(c.get("text", "")).strip()
        if cmd == "comment_add":
            if not text:
                raise ValueError("Type the comment first.")
            t = max(0.0, float(c["t"]))
            return comments + [{"t": t, "sample_index": int(round(t * fs)), "text": text}]
        row = int(c["row"])
        if not 0 <= row < len(comments):
            raise ValueError("That comment isn't there any more.")
        if cmd == "comment_delete":
            return [x for j, x in enumerate(comments) if j != row]
        if not text:
            raise ValueError("A comment can't be empty: delete it instead.")
        comments[row] = {**comments[row], "text": text}
        return comments

    # -- what the page draws ---------------------------------------------------------------
    def frame(self, d: dict) -> tuple[bytes, str]:
        """The reviewed trial from x0 to x1 (s), shrunk to `cols` screen columns, packed as a
        live frame is (live.pack), channels then calculated channels."""
        t0 = time.perf_counter()
        with self._lock:
            blk = self.blk
            if blk is None:
                raise ValueError("Not reviewing a trial.")
            t, data, calc = blk["t"], blk["data"], self.calc
        x0, x1 = float(d.get("x0", 0.0)), float(d.get("x1", blk["duration_s"]))
        cols = max(50, min(4000, int(d.get("cols", 1200))))
        a = max(0, int(np.searchsorted(t, x0)) - 1)
        b = min(len(t), int(np.searchsorted(t, x1, "right")) + 1)
        rows = [data[:, a:b]]
        if calc:
            rows.append(np.array([np.full(b - a, np.nan) if y is None else y[a:b] for _, y, _ in calc], np.float32))
        x, ys, _ = envelope(t[a:b], np.vstack(rows), cols)
        newest = np.full(ys.shape[0], np.nan, np.float32)
        return pack(0.0, x1, x1 - x0, (time.perf_counter() - t0) * 1000, x, newest, ys), "application/octet-stream"

    def video_bytes(self, d: dict) -> tuple[bytes, str]:
        """The reviewed trial's video file (the page plays it beside the traces)."""
        with self._lock:
            if self.video is None:
                raise ValueError("This trial has no video.")
            return self.video[0].read_bytes(), "video/mp4"

    def state(self) -> dict:
        with self._lock:
            if not self.active or self.blk is None:
                return {"active": False}
            blk, rec = self.blk, self.live.recording
            n = len(blk["channel_names"])
            chans = [{"name": name, "units": u, "light": colors.display_color(col, "light"),
                      "dark": colors.display_color(col, "dark")}
                     for name, u, col in zip(blk["channel_names"], blk["units"], blk["colors"])]
            for k, (c, _, why) in enumerate(self.calc):
                col = colors.PALETTE[(n + k) % len(colors.PALETTE)]
                chans.append({"name": c.name, "units": c.unit, "calc": True, "problem": why, "formula": c.formula,
                              "light": colors.display_color(col, "light"), "dark": colors.display_color(col, "dark")})
            data = rec.data if rec is not None else self.folder.parent
            return {
                "active": True, "folder": str(self.folder), "session": session_name(self.folder, data),
                "i": self.i, "trials": [{"stem": b.stem, "hidden": b.stem in self.hidden} for b in self.blocks],
                "channels": chans, "duration": blk["duration_s"], "fs": blk["sample_rate"],
                "comments": [[round(float(c["t"]), 4), str(c["text"])] for c in self.comments],
                "label": self.info.get("label", ""), "start": self.info.get("start_time", ""),
                "stimuli": len(blk.get("stimuli") or []),
                "video": None if self.video is None else {"offset": self.video[1], "name": self.video[0].name},
            }
