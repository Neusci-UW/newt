"""Analysis work saved with a session, beside its recordings (which it never changes):
analysis.json has the settings, and analysis_spikes.npz the spikes found. Blocks are kept by
file stem, so the file still means the same if blocks are added or removed.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import analysis as A

FILE, SPIKES = "analysis.json", "analysis_spikes.npz"
BOARD = "board"         # the HTML front end's charts (web/charts.py): kept when the Qt app saves
PAD_CHARTS = "pad_charts"   # the HTML DataPad's charts of one column against another (web/pad.py): kept too
KEPT = (BOARD, PAD_CHARTS)


def exists(folder: str | Path) -> bool:
    return (Path(folder) / FILE).exists()


def save(folder: str | Path, state: dict, events: A.Events | None, stems: list[str]) -> None:
    folder = Path(folder)
    saved = read_state(folder)
    state = {**{k: saved[k] for k in KEPT if k in saved and k not in state}, **state}
    (folder / FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")
    spikes = folder / SPIKES
    if events is None:
        spikes.unlink(missing_ok=True)
    else:
        with spikes.open("wb") as f:
            np.savez_compressed(f, stem=np.array(stems)[events.block], t=events.t, unit=events.unit,
                                amplitude=events.amplitude, waves=events.waves, wave_t_ms=events.wave_t_ms,
                                level=events.level, duration_s=events.duration_s,
                                notes=np.array(events.notes, dtype=str))


def load(folder: str | Path, stems: list[str]) -> tuple[dict, A.Events | None]:
    """(the settings, the spikes) saved in this folder, without any in blocks that are gone."""
    folder = Path(folder)
    state = json.loads((folder / FILE).read_text(encoding="utf-8"))
    if not (folder / SPIKES).exists():
        return state, None
    where = {s: i for i, s in enumerate(stems)}
    with np.load(folder / SPIKES) as d:
        keep = np.array([s in where for s in d["stem"]], bool)
        return state, A.Events(block=np.array([where[s] for s in d["stem"][keep]], int), t=d["t"][keep],
                               unit=d["unit"][keep], amplitude=d["amplitude"][keep], waves=d["waves"][keep],
                               wave_t_ms=d["wave_t_ms"], level=float(d["level"]), duration_s=float(d["duration_s"]),
                               notes=[str(n) for n in d["notes"]])


def read_state(folder: str | Path) -> dict:
    """The settings saved in this folder, without the spikes; {} if there are none (or they
    can't be read)."""
    try:
        state = json.loads((Path(folder) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_part(folder: str | Path, key: str, value) -> None:
    """Change one part of the saved settings (as the HTML front end saves its charts),
    leaving the rest, and the spikes, as they are."""
    folder = Path(folder)
    state = read_state(folder)
    state[key] = value
    tmp = folder / (FILE + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(folder / FILE)
