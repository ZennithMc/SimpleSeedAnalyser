"""
draw_engine.py — Python side of the Draw search engine v2 (cubiomes_src/draw_engine.c).

A drawing is a grid of class codes (see CLASSES / GROUPS / biome_code) plus
per-cell weights. One exact score is used everywhere (search, "test a seed",
overlays): the weighted share of painted cells whose world cell belongs to
the painted class, at the best position within the slide radius.

    spec = DrawSpec(grid, weights, cell=64, center=(0, 0), slide=24, mc=..., large=False)
    eng = DrawEngine(spec, min_score=0.6, top_k=500)
    eng.score_seed(seed)          -> (score, ox, oz)
    eng.eval_at(seed, ox, oz)     -> (score, observed codes, match mask)
    eng.run(start, count, threads, stop)   (blocking; see DrawSearch)
    eng.results()                 -> [(seed, score, ox, oz), …] best first
"""
from __future__ import annotations

import ctypes as ct
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

# ── classes ──────────────────────────────────────────────────────────────────
ANY, LAND, OCEAN, MUSHROOM, HOT, COLD = 0, 1, 2, 3, 4, 5
BIO = 1000                          # biome b  -> 1000 + b ; group k -> 1000 + 500 + k

CLASSES = [  # code, key, label, colour, layer, tooltip
    (LAND, "land", "Land", (118, 170, 72), "Continentalness",
     "Continentalness ≥ −0.19 (everything that is not ocean: coasts, rivers included)"),
    (OCEAN, "ocean", "Ocean", (46, 88, 178), "Continentalness",
     "Continentalness between −1.05 and −0.19 (ocean and deep ocean)"),
    (MUSHROOM, "mushroom", "Mushroom", (182, 70, 200), "Continentalness",
     "Continentalness < −1.05 (mushroom fields)"),
    (HOT, "hot", "Hot", (232, 118, 44), "Temperature",
     "Temperature ≥ 0.55 (desert, badlands, savanna… band)"),
    (COLD, "cold", "Cold", (150, 205, 240), "Temperature",
     "Temperature ≤ −0.45 (frozen band)"),
]
CLASS_BY_CODE = {c[0]: c for c in CLASSES}

GROUPS = [  # key, label, colour, member biome ids
    ("mountains", "Mountains", (132, 132, 142), (177, 178, 179, 180, 181, 182)),
    ("forests", "Forests", (58, 136, 58), (4, 132, 27, 155, 29, 5, 32, 160, 30, 185)),
    ("jungle", "Jungle", (48, 158, 40), (21, 168, 23)),
    ("desert", "Desert & badlands", (214, 184, 110), (2, 37, 38, 39)),
    ("snowy", "Snowy", (220, 236, 245), (12, 140, 26, 10, 50, 46, 30, 178, 180, 181)),
    ("swamp", "Swamps", (70, 110, 80), (6, 184)),
    ("plains", "Plains & meadows", (140, 190, 90), (1, 129, 177, 35, 36, 163)),
    ("river", "Rivers", (70, 110, 220), (7, 11)),
]
GROUP_BASE = 500


def biome_code(b: int) -> int:
    return BIO + int(b)


def group_code(k: int) -> int:
    return BIO + GROUP_BASE + int(k)


def is_biome_code(c: int) -> bool:
    return BIO <= c < BIO + GROUP_BASE


def is_group_code(c: int) -> bool:
    return c >= BIO + GROUP_BASE


def group_masks() -> np.ndarray:
    """biome id -> bit k set when the biome belongs to group k. A biome painted
    for itself keeps matching itself, a group matches any of its members
    (before: groups REPLACED their members, so a painted Jungle / Forest /
    peak biome could never match)."""
    t = np.zeros(256, dtype=np.uint32)
    for k, g in enumerate(GROUPS):
        for b in g[3]:
            if 0 <= b < 256:
                t[b] |= np.uint32(1 << k)
    return t


def code_color(c: int):
    if c in CLASS_BY_CODE:
        return CLASS_BY_CODE[c][3]
    if is_group_code(c):
        k = c - BIO - GROUP_BASE
        return GROUPS[k][2] if 0 <= k < len(GROUPS) else (128, 128, 128)
    if is_biome_code(c):
        try:
            from cubiomes_wrap import BIOME_COLORS, DEFAULT_COLOR
            return BIOME_COLORS.get(c - BIO, DEFAULT_COLOR)
        except Exception:
            return (128, 128, 128)
    return (0, 0, 0)


def code_name(c: int) -> str:
    if c == ANY:
        return "Any"
    if c in CLASS_BY_CODE:
        return CLASS_BY_CODE[c][2]
    if is_group_code(c):
        k = c - BIO - GROUP_BASE
        return GROUPS[k][1] if 0 <= k < len(GROUPS) else f"Group {k}"
    if is_biome_code(c):
        try:
            from cubiomes_wrap import BIOME_NAMES
            return BIOME_NAMES.get(c - BIO, f"Biome {c - BIO}")
        except Exception:
            return f"Biome {c - BIO}"
    return f"#{c}"


# ── the drawing ──────────────────────────────────────────────────────────────
@dataclass
class DrawSpec:
    grid: np.ndarray                    # (H, W) int32 codes, 0 = any
    weights: Optional[np.ndarray] = None  # (H, W) float, None = 1
    cell: int = 64                      # blocks per cell
    center: Tuple[int, int] = (0, 0)
    slide: int = 0                      # slide radius in cells
    mc: int = 0
    large: bool = False
    balance: bool = False               # every painted class weighs the same

    def effective_weights(self) -> np.ndarray:
        g = np.asarray(self.grid, dtype=np.int32)
        w = (np.ones(g.shape, np.float32) if self.weights is None
             else np.asarray(self.weights, np.float32).copy())
        w[g == ANY] = 0
        if self.balance:
            classes = [c for c in np.unique(g) if c != ANY]
            tot = float(w.sum())
            if classes and tot > 0:
                for c in classes:
                    m = g == c
                    s = float(w[m].sum())
                    if s > 0:
                        w[m] *= (tot / len(classes)) / s
        return w

    def used_groups(self):
        g = np.asarray(self.grid)
        return {int(c) - BIO - GROUP_BASE for c in np.unique(g) if is_group_code(int(c))}

    @property
    def W(self):
        return int(np.asarray(self.grid).shape[1])

    @property
    def H(self):
        return int(np.asarray(self.grid).shape[0])

    def corner(self, ox=0, oz=0):
        """World (x, z) of the drawing's top-left corner at offset (ox, oz)."""
        return (self.center[0] - (self.W * self.cell) // 2 + ox * self.cell,
                self.center[1] - (self.H * self.cell) // 2 + oz * self.cell)


# ── ctypes binding ───────────────────────────────────────────────────────────
_LIB = None


def lib():
    global _LIB
    if _LIB is None:
        from cubiomes_wrap import CubiomesLib
        L = CubiomesLib()._lib
        if not hasattr(L, "dengine_create"):
            raise RuntimeError("The Draw engine is missing from libcubiomes: rebuild it "
                               "(py tools/build_native.py --cubiomes).")
        P = ct.POINTER
        L.dengine_create.restype = ct.c_void_p
        L.dengine_create.argtypes = [P(ct.c_int32), P(ct.c_float), ct.c_int, ct.c_int, ct.c_int,
                                     ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_int,
                                     P(ct.c_uint32), ct.c_double, ct.c_int, ct.c_int, ct.c_int]
        L.dengine_free.argtypes = [ct.c_void_p]
        L.dengine_run.restype = ct.c_int
        L.dengine_run.argtypes = [ct.c_void_p, ct.c_int64, ct.c_int64, ct.c_int, P(ct.c_int)]
        L.dengine_score_seed.restype = ct.c_double
        L.dengine_score_seed.argtypes = [ct.c_void_p, ct.c_int64, P(ct.c_int), P(ct.c_int)]
        L.dengine_eval_at.restype = ct.c_double
        L.dengine_eval_at.argtypes = [ct.c_void_p, ct.c_int64, ct.c_int, ct.c_int,
                                      P(ct.c_int32), P(ct.c_uint8)]
        L.dengine_results.restype = ct.c_int
        L.dengine_results.argtypes = [ct.c_void_p, P(ct.c_int64), P(ct.c_double),
                                      P(ct.c_int), P(ct.c_int), ct.c_int]
        L.dengine_seed_results.argtypes = [ct.c_void_p, P(ct.c_int64), P(ct.c_double),
                                           P(ct.c_int), P(ct.c_int), ct.c_int]
        L.dengine_counters.argtypes = [ct.c_void_p, P(ct.c_int64), P(ct.c_int64)]
        _LIB = L
    return _LIB


def _s64(x):
    x = int(x) & ((1 << 64) - 1)
    return x - (1 << 64) if x >> 63 else x


class DrawEngine:
    def __init__(self, spec: DrawSpec, min_score: float = 0.0, top_k: int = 1000,
                 pre_step: int = 0, pre_oct: int = 8):
        L = lib()
        self.spec = spec
        g = np.ascontiguousarray(spec.grid, dtype=np.int32)
        w = np.ascontiguousarray(spec.effective_weights(), dtype=np.float32)
        rm = np.ascontiguousarray(group_masks(), dtype=np.uint32)
        self._keep = (g, w, rm)
        self.top_k = int(top_k)
        self.h = L.dengine_create(g.ctypes.data_as(ct.POINTER(ct.c_int32)),
                                  w.ctypes.data_as(ct.POINTER(ct.c_float)),
                                  spec.W, spec.H, int(spec.cell), int(spec.center[0]),
                                  int(spec.center[1]), int(spec.slide), int(spec.mc),
                                  1 if spec.large else 0,
                                  rm.ctypes.data_as(ct.POINTER(ct.c_uint32)),
                                  float(min_score), self.top_k, int(pre_step), int(pre_oct))
        if not self.h:
            raise ValueError("Nothing painted (or invalid drawing).")

    def close(self):
        if self.h:
            lib().dengine_free(self.h)
            self.h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def score_seed(self, seed):
        ox, oz = ct.c_int(0), ct.c_int(0)
        sc = lib().dengine_score_seed(self.h, _s64(seed), ct.byref(ox), ct.byref(oz))
        return float(sc), ox.value, oz.value

    def eval_at(self, seed, ox, oz):
        W, H = self.spec.W, self.spec.H
        obs = np.zeros((H, W), np.int32)
        mm = np.zeros((H, W), np.uint8)
        sc = lib().dengine_eval_at(self.h, _s64(seed), int(ox), int(oz),
                                   obs.ctypes.data_as(ct.POINTER(ct.c_int32)),
                                   mm.ctypes.data_as(ct.POINTER(ct.c_uint8)))
        return float(sc), obs, mm

    def run(self, start, count, threads, stop_flag: ct.c_int) -> bool:
        return bool(lib().dengine_run(self.h, _s64(start), int(count), int(threads),
                                      ct.byref(stop_flag)))

    def results(self):
        n = self.top_k
        s = (ct.c_int64 * n)(); sc = (ct.c_double * n)()
        ox = (ct.c_int * n)(); oz = (ct.c_int * n)()
        k = lib().dengine_results(self.h, s, sc, ox, oz, n)
        return [(int(s[i]), float(sc[i]), int(ox[i]), int(oz[i])) for i in range(k)]

    def put_results(self, rows):
        n = len(rows)
        if not n:
            return
        s = (ct.c_int64 * n)(*[_s64(r[0]) for r in rows])
        sc = (ct.c_double * n)(*[float(r[1]) for r in rows])
        ox = (ct.c_int * n)(*[int(r[2]) for r in rows])
        oz = (ct.c_int * n)(*[int(r[3]) for r in rows])
        lib().dengine_seed_results(self.h, s, sc, ox, oz, n)

    def counters(self):
        t, r = ct.c_int64(0), ct.c_int64(0)
        lib().dengine_counters(self.h, ct.byref(t), ct.byref(r))
        return int(t.value), int(r.value)


class DrawSearch:
    """Runs a DrawEngine over a seed range in a background thread, in batches
    of ~0.3 s: Pause stops at a batch boundary → the resume point is exact.
    Stop interrupts at once (the current batch will be re-tested on resume)."""

    def __init__(self, engine: DrawEngine, start: int, count: int = 0, threads: int = 4):
        self.e = engine
        self.start_seed = int(start)
        self.count = int(count)               # 0 = unlimited
        self.threads = max(1, int(threads))
        self.done = 0                         # seeds fully tested (from start)
        self._stop = ct.c_int(0)
        self._pause = threading.Event()
        self._thread = None
        self.finished = False
        self.error = None
        self.t_run = 0.0

    @property
    def resume_point(self):
        return self.start_seed + self.done

    def start(self):
        self._stop.value = 0
        self._pause.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ssa-draw")
        self._thread.start()

    def pause(self):
        self._pause.set()

    def stop(self):
        self._pause.set()
        self._stop.value = 1

    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self):
        batch = 256
        t0 = time.perf_counter()
        try:
            while not self._pause.is_set():
                n = batch if self.count <= 0 else min(batch, self.count - self.done)
                if n <= 0:
                    self.finished = True
                    break
                tb = time.perf_counter()
                complete = self.e.run(self.start_seed + self.done, n, self.threads, self._stop)
                dt = time.perf_counter() - tb
                if not complete:
                    break                               # stopped inside the batch
                self.done += n
                # aim at ~0.3 s per batch (quick pause, little overhead)
                if dt < 0.15:
                    batch = min(batch * 2, 1 << 22)
                elif dt > 0.6 and batch > 16:
                    batch //= 2
        except Exception as ex:                          # reported, never swallowed
            self.error = f"{type(ex).__name__}: {ex}"
        self.t_run += time.perf_counter() - t0
