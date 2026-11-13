"""
qtui/seed_rows.py — the results of a session, stored compactly (22/10).

The results list held one Python tuple per seed (≈ 120 bytes each with its
int and the index dict): 10 M results took > 1 GB and the window crawled.
SeedRows keeps the seeds in a numpy int64 array (8 bytes each) and the rare
« details » text in a dict; it still behaves like the old list of
(seed, details) tuples (len, [i], [a:b], iteration, extend, del [a:b]).

RowIndex: seed -> row. A dict for up to INDEX_DICT_MAX rows, a vectorised
search beyond (a few ms on 10 M rows; only used for single seeds).
"""
from __future__ import annotations

import numpy as np

_U64 = (1 << 64) - 1


def _signed(s) -> int:
    s = int(s) & _U64
    return s - (1 << 64) if s >= (1 << 63) else s


class SeedRows:
    __slots__ = ("_a", "_n", "_det")

    def __init__(self, rows=()):
        self._a = np.empty(1024, np.int64)
        self._n = 0
        self._det = {}
        if rows:
            self.extend(rows)

    # ── list-like ────────────────────────────────────────────────────────────
    def __len__(self):
        return self._n

    def __bool__(self):
        return self._n > 0

    def _grow(self, need):
        if need <= len(self._a):
            return
        cap = max(need, int(len(self._a) * 1.6) + 1024)
        b = np.empty(cap, np.int64)
        b[:self._n] = self._a[:self._n]
        self._a = b

    def extend(self, rows):
        rows = list(rows)
        if not rows:
            return
        k = len(rows)
        self._grow(self._n + k)
        vals = [_signed(s) for s, _d in rows]
        self._a[self._n:self._n + k] = vals
        self._n += k
        for v, (_s, d) in zip(vals, rows):
            if d:
                self._det[v] = d

    def append(self, row):
        self.extend([row])

    def seed_at(self, i) -> int:
        return int(self._a[i if i >= 0 else self._n + i])

    def __getitem__(self, i):
        if isinstance(i, slice):
            idx = range(*i.indices(self._n))
            if idx.step == 1:
                vals = self._a[idx.start:idx.stop].tolist()
            else:
                vals = [int(self._a[j]) for j in idx]
            det = self._det
            return [(v, det.get(v, "")) for v in vals]
        if i < 0:
            i += self._n
        if not 0 <= i < self._n:
            raise IndexError(i)
        v = int(self._a[i])
        return (v, self._det.get(v, ""))

    def __iter__(self):
        det = self._det
        if not det:
            for v in self._a[:self._n].tolist():
                yield (v, "")
        else:
            for v in self._a[:self._n].tolist():
                yield (v, det.get(v, ""))

    def __delitem__(self, i):
        if isinstance(i, slice):
            lo, hi, st = i.indices(self._n)
            if st != 1:
                raise ValueError("only contiguous slices")
        else:
            lo, hi = (i + self._n if i < 0 else i), (i + self._n if i < 0 else i) + 1
        if hi <= lo:
            return
        gone = self._a[lo:hi].tolist()
        self._a[lo:self._n - (hi - lo)] = self._a[hi:self._n]
        self._n -= hi - lo
        if self._det:
            keep = set(self.seeds()[np.isin(self.seeds(), np.array(gone, np.int64))].tolist()) \
                if gone else set()
            for v in gone:
                if v not in keep:
                    self._det.pop(v, None)

    # ── fast paths ───────────────────────────────────────────────────────────
    def seeds(self) -> np.ndarray:
        """The seeds as an int64 array (a view: do not keep it across changes)."""
        return self._a[:self._n]

    def seeds_list(self) -> list:
        return self._a[:self._n].tolist()

    def find(self, seed):
        """First row of this seed, or None (vectorised)."""
        if not self._n:
            return None
        hit = np.flatnonzero(self._a[:self._n] == np.int64(_signed(seed)))
        return int(hit[0]) if len(hit) else None

    def contains_many(self, seeds) -> np.ndarray:
        return np.isin(np.array([_signed(s) for s in seeds], np.int64), self._a[:self._n])


class RowIndex:
    """seed -> row of a SeedRows: a dict while the list is small, a vectorised
    search beyond (no 1 GB dict for 10 M results)."""
    INDEX_DICT_MAX = 200_000

    def __init__(self, rows: SeedRows):
        self.rows = rows
        self.d = None
        if len(rows) <= self.INDEX_DICT_MAX:
            d = {}
            for i, v in enumerate(rows.seeds().tolist()):
                d.setdefault(v, i)
            self.d = d

    def add(self, start, vals):
        if self.d is None:
            return
        if len(self.rows) > self.INDEX_DICT_MAX:
            self.d = None
            return
        for i, v in enumerate(vals):
            self.d.setdefault(_signed(v), start + i)

    def get(self, seed, default=None):
        if self.d is not None:
            return self.d.get(_signed(seed), default)
        r = self.rows.find(seed)
        return default if r is None else r

    def __contains__(self, seed):
        return self.get(seed) is not None
