"""One place that reads recorded blocks, shared by review, the Blocks panel and the
Analysis tab, so a block is read from disk once rather than by each of them.

Everything kept for a block is tied to its file's modification time and size: add a
comment or recolour a channel (which rewrite the file) and the next read is fresh.
Block data is kept up to a memory budget, least recently used first out; the small
facts about a block (session.block_info) and values worked out from it (memo, e.g.
its stimulus) are kept while the file is unchanged.
"""
from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

from . import session

DEFAULT_BUDGET = 1_000_000_000      # bytes of block data kept in memory


class BlockStore:
    def __init__(self, budget: int = DEFAULT_BUDGET):
        self.budget = budget
        self._data: OrderedDict[Path, tuple[tuple, dict, int]] = OrderedDict()   # path -> (stamp, block, bytes)
        self._info: dict[Path, tuple[tuple, dict]] = {}
        self._memo: dict[tuple[Path, object], tuple[tuple, object]] = {}
        self.used = 0

    @staticmethod
    def _stamp(path: Path) -> tuple:
        st = os.stat(path)
        return st.st_mtime_ns, st.st_size

    def read(self, path: str | Path) -> dict:
        """session.read_block(path), from memory when the file hasn't changed."""
        path = Path(path)
        stamp = self._stamp(path)
        hit = self._data.get(path)
        if hit is not None and hit[0] == stamp:
            self._data.move_to_end(path)
            return hit[1]
        if hit is not None:
            self.used -= hit[2]
        block = session.read_block(path)
        size = int(block["data"].nbytes + block["t"].nbytes)
        self._data[path] = (stamp, block, size)
        self.used += size
        while self.used > self.budget and len(self._data) > 1:
            _, (_, _, n) = self._data.popitem(last=False)
            self.used -= n
        return block

    def info(self, path: str | Path) -> dict:
        """session.block_info(path): the small facts (duration, channels...), cached."""
        path = Path(path)
        stamp = self._stamp(path)
        hit = self._info.get(path)
        if hit is None or hit[0] != stamp:
            hit = (stamp, session.block_info(path))
            self._info[path] = hit
        return hit[1]

    def memo(self, path: str | Path, key, make):
        """make(), remembered for (path, key) until the file changes."""
        path = Path(path)
        stamp = self._stamp(path)
        hit = self._memo.get((path, key))
        if hit is None or hit[0] != stamp:
            hit = (stamp, make())
            self._memo[(path, key)] = hit
        return hit[1]

    def forget(self, path: str | Path | None = None) -> None:
        """Drop what's kept for one block, or everything."""
        if path is None:
            self._data.clear()
            self._info.clear()
            self._memo.clear()
            self.used = 0
            return
        path = Path(path)
        hit = self._data.pop(path, None)
        if hit is not None:
            self.used -= hit[2]
        self._info.pop(path, None)
        for k in [k for k in self._memo if k[0] == path]:
            del self._memo[k]
