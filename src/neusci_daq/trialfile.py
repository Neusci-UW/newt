"""One trial's file, block_NNN.h5: everything that knows its layout.

  /raw_volts   (n_channels, N) float32   -- unscaled ADC volts
  /t           (N,) float64              -- only for software-timed backends
  /comments    compound(t, sample_index, text)  -- the comments typed while recording
  attrs: sample_rate, hardware_timed, device, backend, start_time,
         channel_names, physical, units, scale, offset, colors, stimuli

A trial file is written once, while it's recorded (recorder.py) or imported (matfile.py),
and never opened for writing again: the original recording doesn't change. What you change
afterwards, a trial's comments and its channels' colors, is kept beside it in
trial_edits.json, and reading a trial puts those over what the file holds:

  {"comments": {"block_003": [{"t", "sample_index", "text"}]},
   "colors":   {"block_003": {"Channel 1": "#e69f00"}}}
"""
from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

from .colors import PALETTE, normalize_color

EDITS = "trial_edits.json"
COMMENT_DT = np.dtype([("t", "f8"), ("sample_index", "i8"), ("text", h5py.string_dtype())])


# -- the comments dataset --------------------------------------------------------------------
def decode_comments(rows) -> list[dict]:
    out = []
    for c in rows:
        text = c["text"]
        out.append({"t": float(c["t"]), "sample_index": int(c["sample_index"]),
                    "text": text.decode() if isinstance(text, bytes) else str(text)})
    return out


def write_comments_dataset(f: h5py.File, comments: list[dict]) -> None:
    """Rewrite the comments of a file being written (sorted by time)."""
    rows = sorted(comments, key=lambda c: c["t"])
    if "comments" in f:
        del f["comments"]
    ds = f.create_dataset("comments", shape=(len(rows),), maxshape=(None,), dtype=COMMENT_DT)
    for i, c in enumerate(rows):
        ds[i] = (float(c["t"]), int(c["sample_index"]), str(c["text"]))


def comment_rows(comments: list[dict]) -> np.ndarray:
    rows = np.zeros(len(comments), dtype=COMMENT_DT)
    for k, c in enumerate(sorted(comments, key=lambda c: c["t"])):
        rows[k] = (float(c["t"]), int(c["sample_index"]), str(c["text"]))
    return rows


# -- edits kept beside the trials -------------------------------------------------------------
def _edits_path(path: Path) -> Path:
    return Path(path).with_name(EDITS)


def load_edits(folder: str | Path) -> dict:
    try:
        d = json.loads((Path(folder) / EDITS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"comments": dict(d.get("comments", {})), "colors": dict(d.get("colors", {}))}


def _save_edits(folder: Path, d: dict) -> None:
    d = {k: v for k, v in d.items() if v}
    p = Path(folder) / EDITS
    if not d:
        p.unlink(missing_ok=True)
        return
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    tmp.replace(p)


def edits_stamp(path: str | Path) -> tuple:
    """Changes when this trial's folder's edits do."""
    try:
        st = _edits_path(Path(path)).stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return ()


def with_edits(path: str | Path, block: dict) -> dict:
    """A trial as read (read_block or block_info), with its edited comments and colors."""
    path = Path(path)
    edits = load_edits(path.parent)
    comments = edits["comments"].get(path.stem)
    colors = edits["colors"].get(path.stem)
    if comments is None and not colors:
        return block
    out = dict(block)
    if comments is not None:
        if "comments" in out:
            out["comments"] = [(float(c["t"]), str(c["text"])) for c in sorted(comments, key=lambda c: c["t"])]
        if "n_comments" in out:
            out["n_comments"] = len(comments)
    if colors:
        out["colors"] = [colors.get(n, c) for n, c in zip(out["channel_names"], out["colors"])]
    return out


# -- reading ------------------------------------------------------------------------------------
def _colors(attrs, n: int) -> list[str]:
    """Saved trace colors, or palette colors for trials recorded before colors were saved."""
    saved = [str(c) for c in attrs["colors"]] if "colors" in attrs else []
    out = []
    for i in range(n):
        try:
            out.append(normalize_color(saved[i]))
        except (IndexError, ValueError):
            out.append(normalize_color(PALETTE[i % len(PALETTE)]))
    return out


def file_info(path: str | Path) -> dict:
    """What the file says of itself, without loading its data (no edits)."""
    with h5py.File(path, "r") as f:
        a = f.attrs
        n = f["raw_volts"].shape[1]
        fs = float(a["sample_rate"])
        return {
            "channel_names": [str(x) for x in a["channel_names"]],
            "physical": [str(x) for x in a["physical"]],
            "units": [str(x) for x in a["units"]],
            "scale": [float(x) for x in a["scale"]],
            "offset": [float(x) for x in a["offset"]],
            "colors": _colors(a, len(a["channel_names"])),
            "sample_rate": fs,
            "n_samples": int(n),
            "duration_s": n / fs if fs > 0 else float("nan"),
            "n_comments": int(f["comments"].shape[0]),
            "start_time": str(a["start_time"]),
            "device": str(a["device"]),
            "backend": str(a["backend"]),
            "terminal_config": str(a["terminal_config"]) if "terminal_config" in a else None,
        }


def block_info(path: str | Path) -> dict:
    """Metadata of one trial, without loading its data, with your edits."""
    return with_edits(path, file_info(path))


def read_file(path: str | Path) -> dict:
    """One trial as the file holds it, scaled, as float32 to keep memory down (no edits)."""
    with h5py.File(path, "r") as f:
        a = f.attrs
        fs = float(a["sample_rate"])
        raw = f["raw_volts"][:].astype(np.float32, copy=False)
        scale = np.asarray(a["scale"], dtype=np.float32)[:, None]
        offset = np.asarray(a["offset"], dtype=np.float32)[:, None]
        data = raw * scale + offset
        t = f["t"][:] if "t" in f else np.arange(raw.shape[1], dtype=np.float64) / fs
        comments = [(float(c["t"]), c["text"].decode() if isinstance(c["text"], bytes) else str(c["text"]))
                    for c in f["comments"][:]]
        return {
            "t": t, "data": data, "comments": comments, "sample_rate": fs,
            "channel_names": [str(x) for x in a["channel_names"]],
            "units": [str(x) for x in a["units"]],
            "colors": _colors(a, len(a["channel_names"])),
            "terminal_config": str(a["terminal_config"]) if "terminal_config" in a else None,
            "duration_s": float(t[-1]) if t.size else 0.0,
            "stimuli": json.loads(a.get("stimuli", "[]")),      # what was sent (Recorder.add_stimulus)
        }


def read_block(path: str | Path) -> dict:
    """One trial for review, with your edits."""
    return with_edits(path, read_file(path))


def load_recording(path: str | Path) -> dict:
    """A trial in full precision, with scaling applied, for export: arrays, the comments
    (as rows of COMMENT_DT, with your edits) and the file's attributes."""
    with h5py.File(path, "r") as f:
        a = dict(f.attrs)
        raw = f["raw_volts"][:].astype("f8")
        scale = np.asarray(a["scale"])[:, None]
        offset = np.asarray(a["offset"])[:, None]
        t = f["t"][:] if "t" in f else np.arange(raw.shape[1]) / a["sample_rate"]
        rows = f["comments"][:]
    edited = load_edits(Path(path).parent)["comments"].get(Path(path).stem)
    return {"t": t, "data": raw * scale + offset, "raw_volts": raw,
            "comments": comment_rows(edited) if edited is not None else rows, "attrs": a}


def read_comments(path: str | Path) -> list[dict]:
    """A trial's comments as [{t, sample_index, text}], in time order, with your edits."""
    path = Path(path)
    edited = load_edits(path.parent)["comments"].get(path.stem)
    if edited is not None:
        return sorted((dict(c) for c in edited), key=lambda c: c["t"])
    with h5py.File(path, "r") as f:
        return sorted(decode_comments(f["comments"][:]), key=lambda c: c["t"])


# -- changing (beside the file) ------------------------------------------------------------------
def write_comments(path: str | Path, comments: list[dict]) -> None:
    """Set a trial's comments. The file keeps the ones typed while recording; these are
    kept beside it, in trial_edits.json."""
    path = Path(path)
    d = load_edits(path.parent)
    d["comments"][path.stem] = [{"t": float(c["t"]), "sample_index": int(c["sample_index"]), "text": str(c["text"])}
                                for c in sorted(comments, key=lambda c: c["t"])]
    _save_edits(path.parent, d)


def recolor_blocks(blocks, colors: dict[str, str]) -> list[Path]:
    """Set the color of channels (by name) of these trials, beside their files. Returns the
    trials that couldn't be read."""
    failed, by_folder = [], {}
    for b in blocks:
        b = Path(b)
        try:
            names = file_info(b)["channel_names"]
        except Exception:
            failed.append(b)
            continue
        d = by_folder.setdefault(b.parent, load_edits(b.parent))
        mine = dict(d["colors"].get(b.stem, {}))
        mine.update({n: normalize_color(colors[n]) for n in names if n in colors})
        d["colors"][b.stem] = mine
    for folder, d in by_folder.items():
        _save_edits(folder, d)
    return failed


# -- writing a whole trial at once (imports) -----------------------------------------------------
def write_block(path: Path, data: np.ndarray, rate: float, names: list[str], units: list[str], colors: list[str],
                comments: list[tuple[float, str]], start_time: str, **attrs) -> None:
    """One trial in the layout the recorder writes, with the data already in its units."""
    n = len(names)
    with h5py.File(path, "w") as f:
        f.create_dataset("raw_volts", data=data.astype(np.float32), maxshape=(n, None),
                         chunks=(n, max(1, min(4096, data.shape[1]))))
        rows = np.zeros(len(comments), dtype=COMMENT_DT)
        for k, (t, text) in enumerate(comments):
            rows[k] = (float(t), int(round(t * rate)), text)
        f.create_dataset("comments", data=rows, maxshape=(None,))
        a = f.attrs
        a["sample_rate"] = rate
        a["hardware_timed"] = True
        a["device"], a["backend"] = "", "imported"
        a["start_time"] = start_time
        a["channel_names"] = names
        a["physical"] = [""] * n
        a["units"] = units
        a["scale"] = [1.0] * n                 # the values are already in their units
        a["offset"] = [0.0] * n
        a["colors"] = colors
        for k, v in attrs.items():
            a[k] = v
