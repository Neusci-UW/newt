"""Sorting a session's trials into groups by their stimulus: plain Python, shared by the
Qt Analysis tab and the HTML front end (moved here from analysispage.py, unchanged)."""
from __future__ import annotations

import json

import numpy as np

from . import analysis as A
from .stim import Stimulus, stimulus_name, to_segments


def group(blocks: list[dict | None], how: str, sig=None, units: str = "V",
           scale: float = 1.0) -> list[tuple[str, list[int]]]:
    """Blocks with the same stimulus together: [(label, block indices)]. `how` is a
    stimulus channel's name, "notes", "sent" (only what each saved it sent), or "" (no
    groups). sig(i, trace, fs) reads a block's stimulus (default:
    analysis.stimulus_signature, every time). Trials that saved what they sent are
    named for it, in the stimulator's `units` (volts × `scale`)."""
    sig = sig or (lambda i, y, fs: A.stimulus_signature(y, fs))
    if not how:
        return []
    keys, labels = {}, {}
    for i, b in enumerate(blocks):
        if b is None:
            continue
        if how == "notes":
            k = A.stimulus_notes(b["comments"]) or None
            if k is not None:
                labels[k] = " · ".join(k)
        else:
            # you said this channel is the stimulus: whatever it shows is the stimulus
            y = b["data"][b["channel_names"].index(how)] if how in b["channel_names"] else None
            k = sig(i, y, b["sample_rate"]) if y is not None else None
        keys[i] = k
    if how != "notes":
        # a block whose channel shows nothing but noise had no stimulus (judged on its own,
        # not against the others: a 0.1 V pulse beside 6 V ones is still a stimulus)
        keys = {i: (k if k is not None and abs(k[2]) > 0 else None) for i, k in keys.items()}
        # measured signatures jitter: blocks with the same stimulus share the first one's
        good = [b for b in blocks if b is not None]
        keys = A.cluster_signatures(keys, good[0]["sample_rate"] if good else 1.0)
        # Trials that saved what they sent (Simple or Complex Stimulus) group by exactly
        # that: a measured shape can't tell steps of 1 and 1.5 V apart reliably.
        for i, b in enumerate(blocks):
            k = sent_key(b)
            if k is not None and i in keys:
                keys[i] = k
        unit = next((b["units"][b["channel_names"].index(how)] for b in good if how in b["channel_names"]), "V")
        labels = A.unique_names({k: A.describe_signature(k, unit) for k in dict.fromkeys(keys.values())
                                 if k is not None and not (isinstance(k, tuple) and k[0] == "sent")})
        labels.update(sent_names(keys, units, scale))
    return A.group_blocks(keys, labels)

def sent_key(b: dict | None):
    """("sent", what was sent) for a trial that saved the stimuli it sent (Simple or
    Complex Stimulus, on one output or two): the different ones, in the order sent, each
    as segments (so an older Simple Stimulus trial matches a newer one). None if none."""
    if b is None:
        return None
    what = []
    for sent in b.get("stimuli") or ():
        st = sent.get("settings")
        if not st:
            continue
        try:
            st = to_segments(Stimulus.from_dict(st)).to_dict()
        except (ValueError, KeyError, TypeError):
            continue
        item = json.dumps([st, (sent.get("second") or {}).get("settings")], sort_keys=True)
        if item not in what:
            what.append(item)
    return ("sent", json.dumps(what)) if what else None

def sent_stimuli(k: tuple) -> list[tuple[Stimulus, Stimulus | None]]:
    """The stimuli (and what went to the other output) a ("sent", ...) key stands for."""
    out = []
    for item in json.loads(k[1]):
        st, second = json.loads(item)
        out.append((Stimulus.from_dict(st), Stimulus.from_dict(second) if second else None))
    return out

def sent_names(keys: dict, units: str, scale: float) -> dict:
    """Groups of trials that saved what they sent, named for it: "Pulse 5 V". Two
    different stimuli with the same short name get the long one (with widths and
    durations), then (2), (3)..."""
    def name(k, long: bool) -> str:
        ns = [stimulus_name(s, units, scale, long) + (f" & {stimulus_name(s2, units, scale, long)}" if s2 else "")
              for s, s2 in sent_stimuli(k)]
        return ns[0] if len(ns) == 1 else f"{ns[0]} to {ns[-1]}"     # several: a run not split
    sent = [k for k in dict.fromkeys(keys.values()) if isinstance(k, tuple) and k[0] == "sent"]
    short = {k: name(k, False) for k in sent}
    taken = list(short.values())
    return A.unique_names({k: name(k, True) if taken.count(short[k]) > 1 else short[k] for k in sent})

def stimuli_note(b: dict | None) -> tuple:
    """("20 stimuli",) for a block that holds several."""
    n = len(A.note_times(b["comments"])) if b is not None else 0
    return (f"{n} stimuli",) if n > 1 else ()

def stim_wave(b: dict | None, how: str):
    """(trace, sample rate) of a trial's stimulus: the stimulus channel as recorded, or
    (with none) the first stimulus it saved it sent, with rest around it. None if neither."""
    if b is None:
        return None
    names = b["channel_names"]
    if how in names:
        return np.asarray(b["data"][names.index(how)], float), float(b["sample_rate"])
    for sent in b.get("stimuli") or ():
        try:
            rate = float(sent["rate"])
            w = np.asarray(Stimulus.from_dict(sent["settings"]).waveform(rate), float)
        except (ValueError, KeyError, TypeError):
            continue
        return np.r_[np.zeros(w.size), w, np.zeros(w.size)], rate
    return None
