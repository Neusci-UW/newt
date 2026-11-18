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
  base_color  One color / Shades of one color: the color ("" until picked)
  first_color, last_color  From one color to another: its two ends ("" until picked)
  trial_map how the trials of a page are drawn (colors.TRIAL_STYLES)
  gains    {channel: the amplifier gain its data is divided by when read}
  stim_fit the fit applied to the stimulus channel when it's read (analysis.FITS)
  hidden_groups, hidden_trials  the pages and block file stems left out of everything
  shown_trials  blocks you ticked yourself: they stay in, even on a page left out
  order    the pages in the order you dragged them
  sorts    [[measure, 1 smallest first or -1 biggest]...]: kept sorted by these, the first
           first (stimmeasures.MEASURES, "pc1" along the map, "trials"); [] your order.
           (Older files have sort, "frequency" or "amplitude", read as sorts.)
  alike    Group alike stimuli: {"feats": the measures (and "shape") the map is made of,
           "by": "n" (a number of groups) or "close" (how close counts as alike),
           "n": how many groups (0: each stimulus its own), "close": 0-100% of the
           farthest two apart}
  merged   [[stimulus labels]...]: the stimuli the map joins now (worked out from alike)
  color_by a measure (or "pc1") the groups are colored along, "" by the Groups setting
  avg_each a combined group's average is drawn for each stimulus in it, not all at once
  organize "time": one page, every block in the order it was recorded; "" (the default): a
           page per stimulus shape

Gains and fits are applied as data is read: the recorded blocks are never changed.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = "pages.json"
ALL = "All trials"                # the one group when there's nothing to group by
ALIKE = {"feats": [], "by": "n", "n": 0, "close": 0}
OLD_SORTS = {"frequency": [["freq", 1]], "amplitude": [["amp", 1]]}    # older files' "sort"
# what the Analysis page keeps as it's set and writes back; the rest is changed in the file itself
LOOKS = ("colors", "page_map", "trial_map", "base_color", "first_color", "last_color", "trial_opacity", "gains",
         "stim_fit", "hidden_groups", "hidden_trials", "shown_trials", "order", "sorts", "alike", "color_by",
         "avg_each")


def joined_name(names) -> str:
    """A name for groups put together: "A + B", or "A + 2 more"."""
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else " + ".join(names) if len(names) == 2 \
        else f"{names[0]} + {len(names) - 1} more"


def entries(auto: list[tuple[str, list[int]]], d: dict) -> list[tuple[str, list[int], list[str]]]:
    """The stimulus groups as you named them, with those Group alike stimuli joins (merged)
    as one: [(name, block indices, the stimulus labels in it)], in the stimuli's order."""
    renamed = d.get("renamed", {})
    labels = {label for label, _ in auto}
    sets = [[x for x in m if x in labels] for m in d.get("merged", [])]
    where = {x: k for k, m in enumerate(sets) if len(m) > 1 for x in m}
    out, at = [], {}
    for label, blocks in auto:
        k = where.get(label)
        if k is None:
            out.append((renamed.get(label, label), list(blocks), [label]))
        elif k in at:
            out[at[k]][1].extend(blocks)
            out[at[k]][2].append(label)
        else:
            at[k] = len(out)
            out.append(("", list(blocks), [label]))
    return [(joined_name(renamed.get(x, x) for x in ls) if len(ls) > 1 else name, sorted(bl), ls)
            for name, bl, ls in out]


def kinds(auto: list[tuple[str, list[int]]], d: dict) -> dict[str, str]:
    """The groups holding more than one stimulus: {name: "alike"} if Group alike stimuli
    joined them (until Keep these groups), {name: "combined"} if you did."""
    count: dict[str, int] = {}
    alike = set()
    for name, _, labels in entries(auto, d):
        count[name] = count.get(name, 0) + len(labels)
        if len(labels) > 1:                    # the map joined them
            alike.add(name)
    return {name: "alike" if name in alike else "combined" for name, n in count.items() if n > 1}


def load(folder: str | Path | None) -> dict:
    out = {"renamed": {}, "moved": {}, "colors": {}, "page_map": "", "trial_map": "", "base_color": "",
           "first_color": "", "last_color": "", "trial_opacity": 0,
           "gains": {}, "stim_fit": "",
           "hidden_groups": [], "hidden_trials": [], "shown_trials": [], "order": [],
           "sorts": [], "alike": dict(ALIKE), "merged": [], "color_by": "", "avg_each": False,
           "organize": ""}
    if folder is None:
        return out
    try:
        d = json.loads((Path(folder) / FILE).read_text(encoding="utf-8"))
        out["renamed"] = {(ALL if k == "Blocks" else str(k)): str(v) for k, v in d.get("renamed", {}).items()}
        out["moved"] = {str(k): str(v) for k, v in d.get("moved", {}).items()}
        out["colors"] = {str(k): str(v) for k, v in d.get("colors", {}).items()}
        out["page_map"], out["trial_map"] = str(d.get("page_map", "")), str(d.get("trial_map", ""))
        for key in ("base_color", "first_color", "last_color"):
            out[key] = str(d.get(key, ""))
        out["trial_opacity"] = int(d.get("trial_opacity", 0) or 0)
        out["gains"] = {str(k): float(v) for k, v in d.get("gains", {}).items()}
        out["stim_fit"] = str(d.get("stim_fit", ""))
        for key in ("hidden_groups", "hidden_trials", "shown_trials", "order"):
            out[key] = [str(x) for x in d.get(key, [])]
        out["sorts"] = [[str(k), -1 if s == -1 else 1] for k, s in d.get("sorts", [])] \
            if "sorts" in d else list(OLD_SORTS.get(str(d.get("sort", "")), []))
        a = d.get("alike") or {}
        out["alike"] = {"feats": [str(f) for f in a.get("feats", [])], "by": "close" if a.get("by") == "close" else "n",
                        "n": int(a.get("n", 0) or 0), "close": int(a.get("close", 0) or 0)}
        out["merged"] = [[str(x) for x in m] for m in d.get("merged", [])]
        out["color_by"] = str(d.get("color_by", ""))
        out["avg_each"] = bool(d.get("avg_each", False))
        out["organize"] = "time" if d.get("organize") == "time" else ""
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return out


def save(folder: str | Path, d: dict) -> None:
    (Path(folder) / FILE).write_text(json.dumps(d, indent=2), encoding="utf-8")


def apply(auto: list[tuple[str, list[int]]], stems: list[str], d: dict) -> list[tuple[str, list[int]]]:
    """The pages: [(name, block indices)], from the stimulus groups (`auto`; empty
    when there's nothing to group by, which makes one page) and what you changed.
    Pages keep their stimulus order; pages you made come after; empty ones go."""
    if not auto or d.get("organize") == "time":    # by start time: all on one page, in order
        auto = [(ALL, list(range(len(stems))))]
    moved = d.get("moved", {})
    pages: dict[str, list[int]] = {}
    for name, blocks, _ in entries(auto, d):
        pages.setdefault(name, []).extend(i for i in blocks if stems[i] not in moved)
    for i, stem in enumerate(stems):
        if stem in moved:
            pages.setdefault(moved[stem], []).append(i)
    return [(name, sorted(blocks)) for name, blocks in pages.items() if blocks]


def arrange(shown: list[tuple[str, list[int]]], d: dict, measure) -> list[tuple[str, list[int]]]:
    """The pages in the order asked for: sorted, if `sorts` says so, by measure(blocks) (a
    dict of measures), ties by the next sort, pages without that measure last; otherwise
    your order, and any you haven't placed after those."""
    sorts = d.get("sorts")
    if sorts:
        values = {name: measure(blocks) or {} for name, blocks in shown}

        def key(page):
            out = []
            for k, way in sorts:
                v = values[page[0]].get(k)
                missing = v is None or v != v
                out.append((missing, 0.0 if missing else way * float(v)))
            return out
        return sorted(shown, key=key)
    order = {name: r for r, name in enumerate(d.get("order", []))}
    return sorted(shown, key=lambda page: order.get(page[0], len(order)))


def auto_page(auto: list[tuple[str, list[int]]], i: int, d: dict) -> str:
    """The name of block i's stimulus page (where it goes if it isn't moved)."""
    return next((name for name, blocks, _ in entries(auto, d) if i in blocks), d.get("renamed", {}).get(ALL, ALL))


def rename(auto: list[tuple[str, list[int]]], d: dict, old: str, new: str) -> dict:
    """Rename page `old` to `new`: its stimulus group's name, and any blocks moved into it."""
    new = new.strip()
    if not new or new == old:
        return d
    renamed = dict(d.get("renamed", {}))
    for name, _, labels in entries(auto or [(ALL, [])], d):
        if name == old:
            for label in labels:
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


def combine(auto: list[tuple[str, list[int]]], d: dict, names: list[str], new: str = "") -> dict:
    """Make these pages one, named `new` (or after them all): each takes the name, the trials
    moved into them too. Split puts them back."""
    names = list(dict.fromkeys(names))
    if len(names) < 2:
        return d
    new = new.strip() or joined_name(names)
    for name in names:
        d = rename(auto, d, name, new)
    return d


def split(auto: list[tuple[str, list[int]]], d: dict, name: str) -> dict:
    """A page you combined back into its stimuli, each under its stimulus's own name."""
    labels = [ls[0] for n, _, ls in entries(auto, d) if n == name and len(ls) == 1]
    if len(labels) < 2:
        return d
    return {**d, "renamed": {k: v for k, v in d.get("renamed", {}).items() if k not in labels}}


def keep(auto: list[tuple[str, list[int]]], d: dict) -> dict:
    """The groups Group alike stimuli made, kept as groups you made (rename, split or drag
    them; they stay when the measures change), and the map joins nothing more for now."""
    renamed = dict(d.get("renamed", {}))
    for name, _, labels in entries(auto, d):
        if len(labels) > 1:
            renamed.update({label: name for label in labels})
    return {**d, "renamed": renamed, "merged": [], "alike": {**ALIKE, **d.get("alike", {}), "n": 0, "close": 0}}
