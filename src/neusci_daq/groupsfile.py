"""Groups: named groups of a session's trials (called pages in the code and in pages.json,
the file they've always been saved in).

The blocks are grouped by their stimulus automatically (see analysispage), and each
group is a page named after its stimulus. You can rename a page, and move a block
to another page or a new one; each block is shown under its page's name ("Baseline
1", "Baseline 2", ...). Blocks recorded later still go to their stimulus's page.

What you change is kept in the session folder, in pages.json:
  renamed  {stimulus page's label: the name you gave it}
  moved    {block file stem: the page you put it in}
  colors   {page's name: the color you gave it}
  page_map the color map across the pages (colors.PAGE_STYLES)
  trial_map how the trials of a page are drawn (colors.TRIAL_STYLES)
  gains    {channel: the amplifier gain its data is divided by when read}
  stim_fit the fit applied to the stimulus channel when it's read (analysis.FITS)
  hidden_groups, hidden_trials  the pages and block file stems left out of everything
  shown_trials  blocks you ticked yourself: they stay in, even on a page left out
  order    the pages in the order you dragged them
  sort     "frequency" or "amplitude" to keep them sorted, "" for your order

Gains and fits are applied as data is read: the recorded blocks are never changed.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = "pages.json"
ALL = "All trials"                # the one group when there's nothing to group by


def load(folder: str | Path | None) -> dict:
    out = {"renamed": {}, "moved": {}, "colors": {}, "page_map": "", "trial_map": "", "gains": {}, "stim_fit": "",
           "hidden_groups": [], "hidden_trials": [], "shown_trials": [], "order": [], "sort": ""}
    if folder is None:
        return out
    try:
        d = json.loads((Path(folder) / FILE).read_text(encoding="utf-8"))
        out["renamed"] = {(ALL if k == "Blocks" else str(k)): str(v) for k, v in d.get("renamed", {}).items()}
        out["moved"] = {str(k): str(v) for k, v in d.get("moved", {}).items()}
        out["colors"] = {str(k): str(v) for k, v in d.get("colors", {}).items()}
        out["page_map"], out["trial_map"] = str(d.get("page_map", "")), str(d.get("trial_map", ""))
        out["gains"] = {str(k): float(v) for k, v in d.get("gains", {}).items()}
        out["stim_fit"] = str(d.get("stim_fit", ""))
        for key in ("hidden_groups", "hidden_trials", "shown_trials", "order"):
            out[key] = [str(x) for x in d.get(key, [])]
        out["sort"] = str(d.get("sort", ""))
    except (OSError, ValueError, AttributeError, TypeError):
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


def arrange(shown: list[tuple[str, list[int]]], d: dict, measure) -> list[tuple[str, list[int]]]:
    """The pages in the order asked for: sorted, if `sort` says so, with measure(blocks) giving
    (frequency, amplitude); otherwise your order, and any you haven't placed after those."""
    if d.get("sort") in ("frequency", "amplitude"):
        k = 0 if d["sort"] == "frequency" else 1
        return sorted(shown, key=lambda page: measure(page[1])[k])
    order = {name: r for r, name in enumerate(d.get("order", []))}
    return sorted(shown, key=lambda page: order.get(page[0], len(order)))


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
    colors = {(new if page == old else page): c for page, c in d.get("colors", {}).items()}
    same = lambda names: [new if n == old else n for n in names]
    return {**d, "renamed": renamed, "moved": moved, "colors": colors, "order": same(d.get("order", [])),
            "hidden_groups": same(d.get("hidden_groups", []))}


def move(auto: list[tuple[str, list[int]]], stems: list[str], d: dict, i: int, page: str) -> dict:
    """Put block i in `page` (an existing page's name or a new one). Back on its
    stimulus page, it's no longer counted as moved."""
    page = page.strip()
    moved = dict(d.get("moved", {}))
    if not page or page == auto_page(auto, i, d):
        moved.pop(stems[i], None)
    else:
        moved[stems[i]] = page
    return {**d, "renamed": dict(d.get("renamed", {})), "moved": moved}
