"""Splitting a trial into a trial per stimulus sent.

A trial recorded straight through while Repeat (or Raise, or Complex Stimulus's Repeat)
sent stimulus after stimulus becomes one trial for each, by itself when Record stops (or
from the Trials list's right-click menu). Groups then put the trials of the same stimulus
together, from every run, named for what was sent ("Pulse 5 V"), and can sort them by
amplitude.

The trials sit back to back, so nothing recorded after the first is lost: each starts
Before its stimulus (never into the stimulus before it) and runs up to where the next one
starts; the last runs to the end of the recording. Where each stimulus really starts is
found on the recorded stimulus channel, since its note can be up to a second off; without
one, its note is used. The trials are new files: the original doesn't change, and each
new one says where it came from ("split_from").

Plain Python, numpy and h5py, so it runs off the GUI thread.
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


def windows(rec: dict, stim_channel: str | None, before_ms: float) -> list[Window]:
    """Where each stimulus sent in a trial (as trialfile.read_file reads it) is cut out:
    back to back, each from Before its stimulus to the next one's start."""
    t, fs, names = np.asarray(rec["t"], float), float(rec["sample_rate"]), rec["channel_names"]
    y = np.asarray(rec["data"][names.index(stim_channel)], float) if stim_channel in names else None
    sent = rec["stimuli"]
    notes = [float(s.get("t", 0.0)) for s in sent]
    spans = []                                    # (stimulus, start, delay, length s, found)
    for k, s in enumerate(sent):
        try:
            st = Stimulus.from_dict(s["settings"])
            rate = float(s["rate"])
            wave = st.waveform(rate)
        except (ValueError, KeyError, TypeError, OSError):
            continue
        at = None
        if y is not None:
            at = A.sent_onset(y, fs, A.Sent(notes[k], wave, rate), notes[k + 1] if k + 1 < len(notes) else None)
        spans.append((k, notes[k] if at is None else at, st.onset_samples(rate) / rate, len(wave) / rate,
                      at is not None))
    starts = []
    for j, (k, start, delay, length, found) in enumerate(spans):
        a = start + delay - max(before_ms / 1000.0, delay)   # Before time 0, and always the whole stimulus
        if j:                                                 # not into the stimulus before it
            a = max(a, spans[j - 1][1] + spans[j - 1][3])
        starts.append(int(np.searchsorted(t, max(a, 0.0), "left")))
    out = []
    for j, (k, start, _d, _l, found) in enumerate(spans):
        lo, hi = starts[j], (starts[j + 1] if j + 1 < len(starts) else t.size)
        if hi - lo >= 2:
            out.append(Window(k, lo, hi, start, found))
    return out


def split(path: str | Path, stim_channel: str | None, before_ms: float,
          next_path: Callable[[], Path]) -> list[tuple[Path, float, float]]:
    """Write a new trial for each stimulus sent during trial `path`, at next_path() each:
    [(its path, where it starts and ends in the original, s)]. A trial that sent fewer
    than two stimuli isn't split ([])."""
    path = Path(path)
    rec = trialfile.read_file(path)
    if len(rec["stimuli"]) < 2:
        return []
    t, fs = np.asarray(rec["t"], float), float(rec["sample_rate"])
    comments = trialfile.read_comments(path)                # with your edits
    try:
        began = datetime.fromisoformat(trialfile.file_info(path)["start_time"])
    except ValueError:
        began = None
    out = []
    for w in windows(rec, stim_channel, before_ms):
        t_lo, t_end = float(t[w.lo]), (float(t[w.hi]) if w.hi < t.size else float(t[-1]) + 1.0 / fs)
        attrs = {"stimuli": json.dumps([{**rec["stimuli"][w.stimulus], "t": w.start - t_lo}]),
                 "split_from": json.dumps({"trial": path.stem, "stimulus": w.stimulus + 1,
                                           "lined_up": w.found})}
        if began is not None:
            attrs["start_time"] = (began + timedelta(seconds=t_lo)).isoformat()
        notes = [{"t": c["t"] - t_lo, "sample_index": int(c["sample_index"]) - w.lo, "text": c["text"]}
                 for c in comments if t_lo <= c["t"] < t_end]
        out.append((trialfile.write_window(path, next_path(), w.lo, w.hi, attrs, notes), t_lo, t_end))
    return out
