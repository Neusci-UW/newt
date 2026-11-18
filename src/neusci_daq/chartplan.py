"""What the Analysis charts draw, without Qt, for both front ends: the trials left out of
everything (hidden_blocks), which ticked trials show as their group's average and which on
their own (ticked, plan), each trial's color (trial_colors), and a trace shrunk to two points
per screen column, its min and max (envelope), so a page never gets the raw samples.
"""
from __future__ import annotations

import numpy as np

from . import colors


def hidden_blocks(shown: list, stems: list[str], looks: dict) -> set[int]:
    """The trials left out of everything: those of unticked groups, and those you unticked,
    but not those you ticked yourself. shown: [(group name, its trials)]."""
    out = {i for name, blocks in shown if name in looks["hidden_groups"] for i in blocks}
    for i, s in enumerate(stems):
        if s in looks["hidden_trials"]:
            out.add(i)
        elif s in looks["shown_trials"]:
            out.discard(i)
    return out


def ticked(shown: list, hidden: set[int], hidden_groups) -> tuple[list[int], list[str]]:
    """(the trials ticked, the groups shown as averages), or ([], []) when everything is: a
    group ticked shows its average of the trials still ticked in it (unticking a trial leaves
    it out of the average, not the average out), as does one whose trials are all ticked; a
    trial ticked on its own, in a group left unticked, shows its trace."""
    every = sorted(i for _, blocks in shown for i in blocks)
    picked = [i for i in every if i not in hidden]
    if picked == every:
        return [], []
    off = set(hidden_groups)
    return picked, [n for n, blocks in shown if blocks and
                    (not set(blocks) & hidden or (n not in off and set(blocks) - hidden))]


def plan(shown: list, chosen: list[int], picked: list[int], pages_picked: list[str], each=None
         ) -> tuple[list[tuple[int, str, list[int]]], list[int]]:
    """What to draw of the chosen trials: ([(group number, name, its trials)] to average, the
    trials to show on their own). A group picked shows its average, a trial picked on its own
    its trace, and with nothing picked every group's average. each(entry), when given, splits
    one group's entry into several (a combined group: an average for each stimulus in it)."""
    wanted = set(chosen)
    averaged = [(g, name, [i for i in blocks if i in wanted]) for g, (name, blocks) in enumerate(shown)
                if not picked or name in pages_picked]
    averaged = [a for a in averaged if a[2]]
    if each is not None:
        averaged = [part for a in averaged for part in each(a)]
    done = {i for _, _, blocks in averaged for i in blocks}
    return averaged, [i for i in chosen if i not in done]


def trial_colors(shown: list, blocks: list[int], group_color, style: str, opacity: float | None) -> dict:
    """How each of these trials is drawn (QColors): in order within its group, by the Trials
    setting `style`, from its group's color (group_color(g))."""
    out: dict = {}
    for g, (_, members) in enumerate(shown):
        rows = [i for i in blocks if i in members]
        out.update(zip(rows, colors.spread(style, group_color(g), len(rows), opacity)))
    rest = [i for i in blocks if i not in out]
    out.update(zip(rest, colors.spread(style, group_color(0), len(rest), opacity)))
    return out


# ------------------------------------------------------------------------- shrinking traces
def columns(i0: int, i1: int, cols: int) -> np.ndarray | None:
    """Where each screen column starts, in samples, for samples i0 to i1 on `cols` columns;
    None when there are few enough samples to send them all."""
    m = i1 - i0
    if m <= 2 * cols:
        return None
    return i0 + np.arange(cols) * m // cols


def envelope_x(i0: int, i1: int, cols: int, fs: float) -> np.ndarray:
    """The times (s) of envelope()'s points: two per column, at its start and its middle."""
    edges = columns(i0, i1, cols)
    if edges is None:
        return np.arange(i0, i1) / fs
    x = np.empty(2 * len(edges))
    x[0::2] = edges
    x[1::2] = edges + max(1, (i1 - i0) // cols // 2)
    return x / fs


def envelope(y: np.ndarray, i0: int, i1: int, cols: int, how: str = "zigzag") -> np.ndarray:
    """Samples i0 to i1 of y (NaN past its end) as two points per screen column (float32).
    how: "zigzag", the column's min and max, alternating so one line runs through every
    column (as live.py draws); "low" or "high", its min (or max) twice, for a band's edges."""
    edges = columns(i0, i1, cols)
    y = np.asarray(y)
    if edges is None:
        out = np.full(i1 - i0, np.nan, np.float32)
        part = y[i0:min(i1, len(y))]
        out[:len(part)] = part
        return out
    out = np.full(2 * len(edges), np.nan, np.float32)
    k = int(np.searchsorted(edges, len(y)))          # the columns that start inside the trace
    if k == 0:
        return out
    seg = y[i0:min(i1, len(y))]
    at = edges[:k] - i0
    with np.errstate(invalid="ignore"):
        lo = np.fmin.reduceat(seg, at) if how != "high" else None
        hi = np.fmax.reduceat(seg, at) if how != "low" else None
    if how == "low":
        out[0:2 * k:2] = out[1:2 * k:2] = lo
    elif how == "high":
        out[0:2 * k:2] = out[1:2 * k:2] = hi
    else:
        out[0:2 * k:2], out[1:2 * k:2] = lo, hi
        a = out[2::4].copy()                         # odd columns go max then min: one zigzag
        out[2::4], out[3::4] = out[3::4], a
    return out
