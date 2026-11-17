"""The live display's memory: the newest samples of every channel, without copying.

Each sample is written twice, at i and at i + capacity, so the newest samples are
always one contiguous slice of the storage: reading them is a view, not a copy, and
adding a chunk only touches the new samples (the old way concatenated the whole
buffer, 10 s × every channel, on every screen update).
"""
from __future__ import annotations

import numpy as np


class RingBuffer:
    def __init__(self, n_channels: int, capacity: int, dtype=np.float64):
        """dtype is the samples' (times are always float64: float32 times stop telling
        20 kHz samples apart after about 20 minutes of streaming)."""
        self.n = int(n_channels)
        self.dtype = np.dtype(dtype)
        self._alloc(max(2, int(capacity)))

    def _alloc(self, cap: int) -> None:
        self.cap = cap
        self._t = np.empty(2 * cap)            # (only the held part is ever read)
        self._y = np.empty((self.n, 2 * cap), self.dtype)
        self._w = 0              # where the next sample goes (0..cap-1)
        self._count = 0          # how many samples are held (≤ cap)

    def __len__(self) -> int:
        return self._count

    def clear(self) -> None:
        self._w = self._count = 0

    def append(self, t: np.ndarray, y: np.ndarray) -> None:
        """Add samples t (m,) and y (n_channels, m); only the newest `cap` are kept."""
        t = np.asarray(t, float)
        m = len(t)
        y = np.asarray(y, self.dtype).reshape(self.n, m)     # (works with no channels too)
        if m == 0:
            return
        if m > self.cap:
            t, y, m = t[-self.cap:], y[:, -self.cap:], self.cap
        p = (self._w + np.arange(m)) % self.cap
        for q in (p, p + self.cap):
            self._t[q] = t
            self._y[:, q] = y
        self._w = int((self._w + m) % self.cap)
        self._count = min(self.cap, self._count + m)

    def _start(self) -> int:
        return (self._w - self._count) % self.cap

    @property
    def t(self) -> np.ndarray:
        """Times of the samples held, oldest first (a view)."""
        s = self._start()
        return self._t[s:s + self._count]

    @property
    def y(self) -> np.ndarray:
        """The samples held, (n_channels, len), oldest first (a view)."""
        s = self._start()
        return self._y[:, s:s + self._count]

    def resize(self, capacity: int) -> None:
        """A new capacity, keeping the newest samples that fit."""
        capacity = max(2, int(capacity))
        if capacity == self.cap:
            return
        t, y = self.t.copy(), self.y.copy()
        self._alloc(capacity)
        self.append(t, y)
