"""Splitting a trial into a trial per stimulus sent.

A trial recorded straight through while Repeat (or Raise) sent stimulus after stimulus
becomes one trial for each, as if it had been recorded with Simple Stimulus's "save just
around each stimulus". Groups then put the trials of the same stimulus together, from
every run, and can sort them by amplitude.

Each new trial runs from Before its stimulus to After it (always the whole stimulus, as
Record keeps it), and stops short of the stimuli either side. Where each stimulus really
starts is found on the recorded stimulus channel, since its note can be up to a second
off; without one, its note is used. The new trials are new files: the original doesn't
change, and each new one says where it came from ("split_from").
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from . import analysis as A
from . import trialfile
from .stim import Stimulus


@dataclass
class Window:
    stimulus: int        # which stimulus sent (0 = the first)
    lo: int              # samples lo:hi of the trial
    hi: int
    start: float         # where the stimulus starts, s from the trial's start
    found: bool          # found on the stimulus channel (else its note)


def windows(rec: dict, stim_channel: str | None, before_ms: float, after_ms: float) -> list[Window]:
    """Where each stimulus sent in a trial (as trialfile.read_file reads it) is cut out."""
    t, fs, names = np.asarray(rec["t"], float), float(rec["sample_rate"]), rec["channel_names"]
    y = np.asarray(rec["data"][names.index(stim_channel)], float) if stim_channel in names else None
    sent = rec["stimuli"]
    notes = [float(s.get("t", 0.0)) for s in sent]
    spans: list[tuple[float, float, float, bool] | None] = []     # (start, delay, length s, found)
    for k, s in enumerate(sent):
        try:
            st = Stimulus.from_dict(s["settings"])
            rate = float(s["rate"])
            wave = st.waveform(rate)
        except (ValueError, KeyError, TypeError, OSError):
            spans.append(None)
            continue
        at = None
        if y is not None:
            at = A.sent_onset(y, fs, A.Sent(notes[k], wave, rate), notes[k + 1] if k + 1 < len(notes) else None)
        spans.append((notes[k] if at is None else at, st.onset_samples(rate) / rate, len(wave) / rate, at is not None))
    end = float(t[-1]) if t.size else 0.0
    out = []
    for k, sp in enumerate(spans):
        if sp is None:
            continue
        start, delay, length, found = sp
        t0 = start + delay                                  # time 0: where the stimulus itself starts
        a = t0 - max(before_ms / 1000.0, delay)
        b = t0 + max(after_ms / 1000.0, length - delay)
        prev = next((spans[j] for j in range(k - 1, -1, -1) if spans[j] is not None), None)
        nxt = next((spans[j] for j in range(k + 1, len(spans)) if spans[j] is not None), None)
        if prev is not None:                                # not into the stimuli either side
            a = max(a, prev[0] + prev[2])
        if nxt is not None:
            b = min(b, nxt[0])
        lo, hi = int(np.searchsorted(t, max(a, 0.0), "left")), int(np.searchsorted(t, min(b, end), "right"))
        if hi - lo >= 2:
            out.append(Window(k, lo, hi, start, found))
    return out


def split(path: str | Path, stim_channel: str | None, before_ms: float, after_ms: float,
          next_path: Callable[[], Path]) -> list[Path]:
    """Write a new trial for each stimulus sent during trial `path`, at next_path() each;
    their paths. A trial that sent fewer than two stimuli isn't split ([])."""
    path = Path(path)
    rec = trialfile.read_file(path)
    if len(rec["stimuli"]) < 2:
        return []
    t, fs = np.asarray(rec["t"], float), float(rec["sample_rate"])
    comments = trialfile.read_comments(path)                # with your edits
    info = trialfile.file_info(path)
    try:
        began = datetime.fromisoformat(info["start_time"])
    except ValueError:
        began = None
    colors = trialfile.load_edits(path.parent)["colors"].get(path.stem)
    out = []
    for w in windows(rec, stim_channel, before_ms, after_ms):
        t_lo, t_hi = float(t[w.lo]), float(t[w.hi - 1]) + 1.0 / fs
        attrs = {"stimuli": json.dumps([{**rec["stimuli"][w.stimulus], "t": w.start - t_lo}]),
                 "split_from": json.dumps({"trial": path.stem, "stimulus": w.stimulus + 1,
                                           "lined_up": w.found})}
        if began is not None:
            attrs["start_time"] = (began + timedelta(seconds=t_lo)).isoformat()
        notes = [{"t": c["t"] - t_lo, "sample_index": int(c["sample_index"]) - w.lo, "text": c["text"]}
                 for c in comments if t_lo <= c["t"] < t_hi]
        dest = trialfile.write_window(path, next_path(), w.lo, w.hi, attrs, notes)
        out.append(dest)
    if colors and out:
        trialfile.recolor_blocks(out, colors)               # the channel colors you gave the original
    return out
