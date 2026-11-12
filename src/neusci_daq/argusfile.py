"""The .argus file: a whole session in one file, to save, share or hand in, that reopens
Argus as it was left.

It's a zip. session/ holds the session folder's files (the recordings, block_NNN.h5, and
pages.json, analysis.json, analysis_spikes.npz, videos...), state.json what was on screen
(tabs, layouts, charts, windows, stimulus, protocol, simulation), and manifest.json which
files are current, with their sizes and times.

Argus never records into the zip, where a crash could lose it: it records into a working
folder, and Save packs that folder. An opened .argus is unpacked into a working folder in
data/open/. Saving again only adds what changed since the last save; a file replaced that
way leaves its old copy behind, unused, and once those take up half the file it's written
again from scratch.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import warnings
import zipfile
from pathlib import Path

SUFFIX = ".argus"
STATE, MANIFEST, FILES = "state.json", "manifest.json", "session/"
SOURCE = ".argus-source.json"      # in a working folder: the file it came from, and what was in it
STORED = {".h5", ".npz", ".mp4", ".avi", ".mov", ".mkv", ".png", ".jpg"}   # already packed: not compressed again
LEFT_OUT = {".mat"}                # remade from the recordings when needed
VERSION = 1


class ArgusError(Exception):
    pass


def files_of(folder: str | Path) -> dict[str, list[int]]:
    """The files of a session folder that go in the file: {path inside it: [size, mtime ns]}."""
    folder = Path(folder)
    out = {}
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(folder).parts) \
                and p.suffix.lower() not in LEFT_OUT:
            st = p.stat()
            out[rel] = [st.st_size, st.st_mtime_ns]
    return out


def working_folder(data_dir: str | Path, path: str | Path) -> Path:
    """Where an .argus file is unpacked: data/open/<its name>-<a tag of where it is>."""
    path = Path(path).resolve()
    tag = hashlib.sha1(str(path).encode()).hexdigest()[:8]
    return Path(data_dir) / "open" / f"{path.stem}-{tag}"


def _read_json(z: zipfile.ZipFile, name: str) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return json.loads(z.read(name).decode("utf-8"))


def read_manifest(path: str | Path) -> dict | None:
    try:
        with zipfile.ZipFile(path) as z:
            return _read_json(z, MANIFEST)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def _write(z: zipfile.ZipFile, folder: Path, rel: str) -> None:
    kind = zipfile.ZIP_STORED if Path(rel).suffix.lower() in STORED else zipfile.ZIP_DEFLATED
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")              # a name written again: the newest copy counts
        z.write(folder / rel, FILES + rel, compress_type=kind)


def _put(z: zipfile.ZipFile, name: str, d: dict) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        z.writestr(name, json.dumps(d, indent=1), compress_type=zipfile.ZIP_DEFLATED)


def save(path: str | Path, folder: str | Path, state: dict) -> dict:
    """Pack a session folder and the app's state into `path`. Returns the manifest's files,
    for telling later whether anything has changed since."""
    path, folder = Path(path), Path(folder)
    now = files_of(folder)
    if not any(r.startswith("block_") for r in now):
        raise ArgusError("Nothing recorded yet: there's nothing to save.")
    old = read_manifest(path) if path.exists() else None
    if old is not None and old.get("version") == VERSION and old.get("wasted", 0) <= path.stat().st_size / 2:
        changed = [r for r, v in now.items() if old["files"].get(r) != v]
        gone = [r for r in old["files"] if r not in now]
        wasted = old.get("wasted", 0) + sum(old["files"][r][0] for r in gone + changed if r in old["files"])
        with zipfile.ZipFile(path, "a") as z:
            for rel in changed:
                _write(z, folder, rel)
            _put(z, STATE, state)
            _put(z, MANIFEST, {"version": VERSION, "files": now, "wasted": wasted})
        return now
    tmp = path.with_name(path.name + ".saving")
    with zipfile.ZipFile(tmp, "w") as z:
        for rel in now:
            _write(z, folder, rel)
        _put(z, STATE, state)
        _put(z, MANIFEST, {"version": VERSION, "files": now, "wasted": 0})
    os.replace(tmp, path)                            # the old file stays whole until the new one is
    return now


def open_file(path: str | Path, data_dir: str | Path) -> tuple[Path, dict, dict]:
    """Unpack an .argus file into its working folder (unless it's there already, unchanged).
    Returns (the folder, the app's state, the manifest's files)."""
    path = Path(path)
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise ArgusError(f"{path.name} isn't an Argus file it can read: {e}") from None
    with z:
        try:
            manifest, state = _read_json(z, MANIFEST), _read_json(z, STATE)
        except (KeyError, ValueError):
            raise ArgusError(f"{path.name} isn't an Argus file: it has no {MANIFEST}.") from None
        if manifest.get("version", 0) > VERSION:
            raise ArgusError(f"{path.name} was saved by a newer Argus. Update Argus to open it.")
        folder = working_folder(Path(data_dir), path)
        source = folder / SOURCE
        try:
            same = json.loads(source.read_text(encoding="utf-8"))["files"] == manifest["files"] \
                and files_of(folder) == manifest["files"]
        except (OSError, ValueError, KeyError):
            same = False
        if not same:
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir(parents=True)
            for rel, (_, mtime) in manifest["files"].items():
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    data = z.read(FILES + rel)     # the newest copy of the name
                out = folder / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                os.utime(out, ns=(mtime, mtime))   # as saved, so a later save knows it's unchanged
            source.write_text(json.dumps({"path": str(path.resolve()), "files": manifest["files"]}),
                              encoding="utf-8")
    return folder, state, manifest["files"]
