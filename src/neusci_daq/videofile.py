"""A trial's video on disk: block_NNN.mp4 beside block_NNN.h5, lined up by
block_NNN.video.json (time in the block = time in the video + offset_s + nudge_s).
The Qt camera panel (video.py) writes them; it and the HTML front end play them."""
from __future__ import annotations

import json
from pathlib import Path


def sidecar(block: Path) -> Path:
    return Path(block).with_suffix(".video.json")


def video_of(block: Path) -> tuple[Path, float] | None:
    """(video file, offset_s) recorded with this block, or None."""
    meta = sidecar(block)
    if not meta.exists():
        return None
    try:
        d = json.loads(meta.read_text())
        f = Path(block).parent / d["file"]
        return (f, float(d.get("offset_s", 0.0)) + float(d.get("nudge_s", 0.0))) if f.exists() else None
    except (ValueError, KeyError, OSError):
        return None


def save_nudge(block: Path, nudge_s: float) -> None:
    meta = sidecar(block)
    d = json.loads(meta.read_text())
    d["nudge_s"] = float(nudge_s)
    meta.write_text(json.dumps(d, indent=1))


def video_ms(t_block: float, offset_s: float) -> int:
    """Where in the video (ms) a time in the block (s) is."""
    return max(0, int(round((t_block - offset_s) * 1000)))
