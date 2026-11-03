"""Pages: named groups of a session's blocks.

The blocks are grouped by their stimulus automatically (see analysispage), and each
group is a page named after its stimulus. You can rename a page, and move a block
to another page or a new one; each block is shown under its page's name ("Baseline
1", "Baseline 2", ...). Blocks recorded later still go to their stimulus's page.

What you change is kept in the session folder, in pages.json:
  renamed  {stimulus page's label: the name you gave it}
  moved    {block file stem: the page you put it in}
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = "pages.json"
ALL = "Blocks"                    # the one page when there's nothing to group by


def load(folder: str | Path | None) -> dict:
    out = {"renamed": {}, "moved": {}}
    if folder is None:
        return out
    try:
        d = json.loads((Path(folder) / FILE).read_text(encoding="utf-8"))
        out["renamed"] = {str(k): str(v) for k, v in d.get("renamed", {}).items()}
        out["moved"] = {str(k): str(v) for k, v in d.get("moved", {}).items()}
    except (OSError, ValueError, AttributeError):
        pass
    return out


def save(folder: str | Path, d: dict) -> None:
    (Path(folder) / FILE).write_text(json.dumps(d, indent=2), encoding="utf-8")


def apply(auto: list[tuple[str, list[int]]], stems: list[str], d: dict) -> list[tuple[str, list[int]]]:
    """The pages: [(name, block indices)], from the stimulus groups (`auto`; empty
    when there's nothing to group by, which makes one page) and what you changed.
    Pages keep their stimulus order; pages you made come after; empty ones go."""
    if not auto:
        auto = [(ALL, list(range(len(stems))))]
    renamed, moved = d.get("renamed", {}), d.get("moved", {})
    pages: dict[str, list[int]] = {}
    for label, blocks in auto:
        pages.setdefault(renamed.get(label, label), []).extend(i for i in blocks if stems[i] not in moved)
    for i, stem in enumerate(stems):
        if stem in moved:
            pages.setdefault(moved[stem], []).append(i)
    return [(name, sorted(blocks)) for name, blocks in pages.items() if blocks]


def auto_page(auto: list[tuple[str, list[int]]], i: int, d: dict) -> str:
    """The name of block i's stimulus page (where it goes if it isn't moved)."""
    label = next((label for label, blocks in auto if i in blocks), ALL)
    return d.get("renamed", {}).get(label, label)


def rename(auto: list[tuple[str, list[int]]], d: dict, old: str, new: str) -> dict:
    """Rename page `old` to `new`: its stimulus group's name, and any blocks moved into it."""
    new = new.strip()
    if not new or new == old:
        return d
    renamed = dict(d.get("renamed", {}))
    for label, _ in (auto or [(ALL, [])]):
        if renamed.get(label, label) == old:
            renamed[label] = new
    moved = {stem: (new if page == old else page) for stem, page in d.get("moved", {}).items()}
    return {"renamed": renamed, "moved": moved}


def move(auto: list[tuple[str, list[int]]], stems: list[str], d: dict, i: int, page: str) -> dict:
    """Put block i in `page` (an existing page's name or a new one). Back on its
    stimulus page, it's no longer counted as moved."""
    page = page.strip()
    moved = dict(d.get("moved", {}))
    if not page or page == auto_page(auto, i, d):
        moved.pop(stems[i], None)
    else:
        moved[stems[i]] = page
    return {"renamed": dict(d.get("renamed", {})), "moved": moved}
