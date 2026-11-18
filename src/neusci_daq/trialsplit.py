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

Or, for any trial (one recorded before, or driven by an outside stimulator), cut where the
stimulus channel goes back to rest (split_at_rest): a stimulus ends once it's been back at
0 for a while, so a train stays one trial or each pulse is one, as you choose.

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
from . import groupsfile
from . import stimmeasures as SM
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
    t = np.asarray(rec["t"], float)
    return _write(path, rec, [(w.lo, w.hi, {
        "stimuli": json.dumps([{**rec["stimuli"][w.stimulus], "t": w.start - float(t[w.lo])}]),
        "split_from": json.dumps({"trial": path.stem, "stimulus": w.stimulus + 1, "lined_up": w.found})})
        for w in windows(rec, stim_channel, before_ms)], next_path)


def _write(path: Path, rec: dict, cuts: list[tuple[int, int, dict]], next_path) -> list[tuple[Path, float, float]]:
    """A new trial of samples lo:hi of `path` for each cut (lo, hi, attributes put over the
    original's), with the notes that fall in it: [(its path, start, end in the original, s)]."""
    t, fs = np.asarray(rec["t"], float), float(rec["sample_rate"])
    comments = trialfile.read_comments(path)                # with your edits
    try:
        began = datetime.fromisoformat(trialfile.file_info(path)["start_time"])
    except ValueError:
        began = None
    out = []
    for lo, hi, attrs in cuts:
        t_lo, t_end = float(t[lo]), (float(t[hi]) if hi < t.size else float(t[-1]) + 1.0 / fs)
        attrs = dict(attrs)
        if began is not None:
            attrs["start_time"] = (began + timedelta(seconds=t_lo)).isoformat()
        notes = [{"t": c["t"] - t_lo, "sample_index": int(c["sample_index"]) - lo, "text": c["text"]}
                 for c in comments if t_lo <= c["t"] < t_end]
        out.append((trialfile.write_window(path, next_path(), lo, hi, attrs, notes), t_lo, t_end))
    return out


# -- cutting where the stimulus goes back to rest -----------------------------------------------
GAP_MS = 50.0          # back at 0 this long ends a stimulus (a train's gaps are shorter)
ON = SM.ON             # on: further from rest than this share of the biggest swing


@dataclass
class Cut:
    lo: int              # the new trial: samples lo:hi of the original
    hi: int
    start: int           # its stimulus: samples start:end
    end: int


def rest_cuts(y, fs: float, before_ms: float, gap_ms: float = GAP_MS, on: float = ON) -> list[Cut]:
    """Where to cut a trial whose stimulus channel is `y`: a new trial for each stimulus, a
    stimulus being where the channel is on (further from rest than `on` × its biggest swing),
    ended only once it's back at rest for `gap_ms`. Back to back, as split: each starts Before
    its stimulus (never into the one before it) and runs to where the next starts; the last
    to the end."""
    y = np.asarray(y, float)
    rest = SM.rest_level(y)
    if rest is None:
        return []
    d = np.abs(np.nan_to_num(y - rest))
    is_on = d > on * float(d.max())
    starts = np.flatnonzero(is_on & ~np.r_[False, is_on[:-1]])
    ends = np.flatnonzero(is_on & ~np.r_[is_on[1:], False]) + 1
    if starts.size > 1:                           # back at rest only briefly: the same stimulus
        apart = starts[1:] - ends[:-1] >= max(1.0, gap_ms / 1000 * fs)
        starts, ends = starts[np.r_[True, apart]], ends[np.r_[apart, True]]
    before = int(round(max(0.0, before_ms) / 1000 * fs))
    los = [max(int(ends[k - 1]) if k else 0, int(s) - before) for k, s in enumerate(starts)]
    his = los[1:] + [y.size]
    return [Cut(lo, hi, int(s), int(e)) for lo, hi, s, e in zip(los, his, starts, ends) if hi - lo >= 2]


def _sig(x: float) -> str:
    return f"{float(f'{x:.2g}'):g}"


def describe(y, fs: float, unit: str = "V") -> str:
    """A stretch of the stimulus channel in words: "Pulse of 2 V, 5 ms", "Train of 5 pulses
    of 2 V, 1 ms, every 20 ms", "Wave, 1 V, 200 ms"."""
    w, m = SM.window(y, fs), SM.measure(y, fs)
    if w is None or m is None:
        return "Nothing"
    peak = float(w[np.argmax(np.abs(w))])
    size = f"{_sig(peak)} {unit}".strip()
    if w.max() > ON * m["amp"] and w.min() < -ON * m["amp"]:
        return f"Wave, {_sig(m['amp'])} {unit}, {_sig(m['len'])} ms"
    if m["count"] <= 1:
        return f"Pulse of {size}, {_sig(m['width'])} ms"
    return (f"Train of {int(m['count'])} pulses of {size}, {_sig(m['width'])} ms, "
            f"every {_sig(1000 / m['freq'])} ms" if m["freq"] else f"Train of {int(m['count'])} pulses of {size}")


def split_at_rest(path: str | Path, stim_channel: str, before_ms: float, next_path: Callable[[], Path],
                  gap_ms: float = GAP_MS, on: float = ON) -> list[tuple[Path, float, float]]:
    """Write a new trial for each stimulus on `stim_channel` of trial `path`, cut where it
    goes back to rest (rest_cuts), at next_path() each: [(its path, where it starts and ends
    in the original, s)]. One stimulus or none: not split ([]). Stimuli the trial saved it
    sent go with the new trial they fall in."""
    path = Path(path)
    rec = trialfile.read_file(path)
    names = rec["channel_names"]
    if stim_channel not in names:
        raise ValueError(f"no channel {stim_channel}")
    t, fs = np.asarray(rec["t"], float), float(rec["sample_rate"])
    cuts = rest_cuts(rec["data"][names.index(stim_channel)], fs, before_ms, gap_ms, on)
    if len(cuts) < 2:
        return []
    out = []
    for k, c in enumerate(cuts):
        t_lo, t_end = float(t[c.lo]), (float(t[c.hi]) if c.hi < t.size else float(t[-1]) + 1.0 / fs)
        sent = [{**s, "t": float(s.get("t", 0.0)) - t_lo} for s in rec["stimuli"]
                if t_lo <= float(s.get("t", 0.0)) < t_end]
        out.append((c.lo, c.hi, {"stimuli": json.dumps(sent),
                                 "split_from": json.dumps({"trial": path.stem, "stimulus": k + 1, "at_rest": True})}))
    return _write(path, rec, out, next_path)


def hide_originals(made: list[tuple[Path, list]]) -> None:
    """After splits [(the original, its new trials from split())]: each original is hidden in
    its session's groups (pages.json), so nothing counts twice (tick it to bring it back),
    and the new trials get the channel colors you gave the original."""
    for folder in {p.parent for p, _ in made}:
        stems = [p.stem for p, _ in made if p.parent == folder]
        d = groupsfile.load(folder)
        d["hidden_trials"] = [s for s in d["hidden_trials"] if s not in stems] + stems
        d["shown_trials"] = [s for s in d["shown_trials"] if s not in stems]
        groupsfile.save(folder, d)
    for p, new in made:
        colors = trialfile.load_edits(p.parent)["colors"].get(p.stem)
        if colors:
            trialfile.recolor_blocks([q for q, _, _ in new], colors)
