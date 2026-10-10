"""
feature116.py — Java 1.16 dungeons and lava lakes (10/10).

Both are placed per chunk by the population random, which only depends on the
48 low bits of the seed (checked in Minecraft 1.16.1, see the C engine
session_engine.c « 1.16 dungeons and lava lakes »):

  dungeon    8 attempts per chunk: the spawner's X Y Z and the room's half
             sizes. Only an attempt touching a cave (1 to 5 openings, solid
             floor and ceiling) becomes a dungeon — most do not.
  lava lake  at most one per chunk: its 16×16 box, the top lava layer (Y) and
             its shape. The lake index of a chunk depends on its biome (deserts
             have no water lake): exact with the biome, a superset without it.
             Surface lakes (decorator Y ≥ 63) drop to the ground first: their
             Y is unknown here.

  features(mc, seed, kind, x1, z1, x2, z2, exact=True)   → [Feat]
             exact=True (10/10 bis): the features that REALLY generate — the C
             engine rebuilds the 1.16.1 terrain (noise, surface, caves,
             ravines), the chunk's lakes and mineshafts and runs the dungeon
             attempts in order (terrain116.c / mineshaft116.c, checked block
             for block against Minecraft). Lakes get their real Y. Features
             near a village / outpost (pieces and terrain "beards" not
             modelled) are flagged uncertain: only those still go to Minecraft.
             exact=False: the 48-bit attempts / lakes (both indices), a superset.
  lake_columns(seed, feat)                                → 256 bytes (lava columns)
  check(seed, feats, callback)                            the real Minecraft 1.16.1
  confirm_targets(conds, seed, mc)                        what a result must show
"""
from __future__ import annotations

import ctypes
import threading
from dataclasses import dataclass

VERSIONS = (19, 20)                 # cubiomes MC_1_16_1, MC_1_16_5
KINDS = ("dungeon", "lava_lake")


class _F(ctypes.Structure):
    _fields_ = [(k, ctypes.c_int32) for k in "x y z kind sx sz flags ox oy oz".split()]


@dataclass(frozen=True)
class Feat:
    kind: int            # 0 dungeon, 1 lava lake
    x: int
    y: int               # spawner / top lava layer (surface lake: decorator Y - 1)
    z: int
    sx: int              # dungeon interior half sizes / lake 16
    sz: int
    flags: int           # dungeon: attempt 0..7; lake: 1 surface, 2 desert index;
                         # 16 exact (really generated), 32 uncertain (near a village)
    ox: int
    oy: int
    oz: int

    @property
    def surface(self):
        return self.kind == 1 and bool(self.flags & 1)

    @property
    def exact(self):
        return bool(self.flags & 16)

    @property
    def uncertain(self):
        return bool(self.flags & 32)

    @property
    def real(self):
        """Known to exist without asking Minecraft."""
        return bool(self.flags & 16) and not self.flags & 32

    @property
    def name(self):
        return "Dungeon" if self.kind == 0 else "Lava lake"

    def text(self):
        if self.exact:
            tail = " — near a village: to check" if self.uncertain else ""
            if self.kind == 0:
                return (f"Dungeon · spawner {self.x} {self.y} {self.z} · room "
                        f"{2 * self.sx + 1}×{2 * self.sz + 1}{tail}")
            return (f"{'Surface lava lake' if self.surface else 'Lava lake'} · {self.x} {self.y} {self.z}"
                    f" (top lava layer){tail}")
        if self.kind == 0:
            return (f"Dungeon attempt · spawner {self.x} {self.y} {self.z} · room "
                    f"{2 * self.sx + 1}×{2 * self.sz + 1}")
        if self.surface:
            return f"Surface lava lake · {self.x} ~ {self.z} (Y = the ground)"
        return f"Lava lake · {self.x} {self.y} {self.z} (top lava layer)"

    def tp(self, seed=None):
        if self.exact:
            return f"/tp @s {self.x} {self.y + 1} {self.z}"
        y = REAL_Y.get((int(seed), self.ox, self.oz)) if (seed is not None and self.kind == 1) else None
        if y is None and self.kind == 1 and self.surface:
            return f"/tp @s {self.x} ~ {self.z}"
        return f"/tp @s {self.x} {(self.y if y is None else y) + 1} {self.z}"

    def chunk(self):
        return self.ox >> 4, self.oz >> 4


_LIB = None
_LOCK = threading.Lock()


def _lib():
    global _LIB
    with _LOCK:
        if _LIB is None:
            import cubiomes_wrap as cw
            path = cw._find_lib()
            L = ctypes.CDLL(path)
            L.ssa_features116.argtypes = [ctypes.c_int, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                          ctypes.POINTER(_F), ctypes.c_int]
            L.ssa_features116.restype = ctypes.c_int
            L.ssa_lake116_shape.argtypes = [ctypes.c_int64, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_void_p, ctypes.c_void_p]
            L.ssa_lake116_shape.restype = ctypes.c_int
            L.ssa_ground116.argtypes = [ctypes.c_int, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_int, ctypes.c_void_p]
            L.ssa_ground116.restype = ctypes.c_int
            try:                                   # (10/10 bis) exact terrain
                L.ssa_features116x.argtypes = [ctypes.c_int, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                                               ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                               ctypes.POINTER(_F), ctypes.c_int, ctypes.c_int]
                L.ssa_features116x.restype = ctypes.c_int
            except AttributeError:                 # an older library: attempts only
                pass
            if L.ssa_sizeof_feat116() != ctypes.sizeof(_F):
                raise RuntimeError("libcubiomes: SsaFeat layout mismatch (rebuild it)")
            _LIB = L
        return _LIB


def available():
    try:
        _lib()
        return True
    except Exception:
        return False


def _s64(v):
    v = int(v) & ((1 << 64) - 1)
    return v - (1 << 64) if v >> 63 else v


def surface_grid(mc, seed, x0, z0, w, h, step=1, caves=True):
    """(10/10 bis) Exact Y of 1.16: the highest solid block (the sea floor
    under water) on a grid, int32[h, w] — exact terrain, with the caves /
    ravines (caves=True) or without."""
    import numpy as np
    L = _lib()
    f = L.ssa_surface116
    f.argtypes = [ctypes.c_int, ctypes.c_int64, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                  ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    f.restype = ctypes.c_int
    out = np.zeros(int(w) * int(h), dtype=np.int32)
    f(int(mc), _s64(seed), int(x0), int(z0), int(w), int(h), int(step), 1 if caves else 0,
      out.ctypes.data)
    return out.reshape(int(h), int(w))


def has_exact():
    """Is the exact terrain in the library (rebuilt after 10/10 bis)?"""
    try:
        return hasattr(_lib(), "ssa_features116x")
    except Exception:
        return False


_XCACHE: dict = {}          # (mc, seed, box) → (dungeons, lakes) (exact, the last few)
_XLOCK = threading.Lock()


def features_exact(mc, seed, x1, z1, x2, z2, threads=0):
    """(dungeons, lava lakes) that really generate in a box — one pass of the
    exact terrain for both (lakes by their centre). threads 1: this thread only
    (the map's workers), 0: every core."""
    key = (int(mc), int(seed), int(x1), int(z1), int(x2), int(z2))
    with _XLOCK:
        v = _XCACHE.get(key)
    if v is not None:
        return v
    L = _lib()
    nch = ((int(x2) >> 4) - (int(x1) >> 4) + 4) * ((int(z2) >> 4) - (int(z1) >> 4) + 4)
    n_max = max(64, nch * 9)
    buf = (_F * n_max)()
    n = L.ssa_features116x(int(mc), _s64(seed), 2, int(x1), int(z1), int(x2), int(z2),
                           buf, n_max, int(threads))
    allf = [Feat(f.kind, f.x, f.y, f.z, f.sx, f.sz, f.flags, f.ox, f.oy, f.oz)
            for f in buf[:min(n, n_max)]]
    allf.sort(key=lambda f: (f.z, f.x, f.y))
    v = (tuple(f for f in allf if f.kind == 0), tuple(f for f in allf if f.kind == 1))
    with _XLOCK:
        if len(_XCACHE) > 256:
            _XCACHE.clear()
        _XCACHE[key] = v
    return v


def features(mc, seed, kind, x1, z1, x2, z2, exact=True, cap=200000, threads=0):
    """The features of a box ([] outside 1.16). exact: the ones that really
    generate (exact terrain, see the module doc; the 48-bit attempts if the
    library is older); else what the 48 bits give (attempts, both lake
    indices)."""
    if int(mc) not in VERSIONS:
        return []
    k = 1 if kind in (1, "lava_lake") else 0
    L = _lib()
    if exact and hasattr(L, "ssa_features116x"):
        return list(features_exact(mc, seed, x1, z1, x2, z2, threads)[k])
    # up to 16 dungeon attempts (both indices when not exact) / 2 lakes a chunk
    nch = ((int(x2) >> 4) - (int(x1) >> 4) + 3) * ((int(z2) >> 4) - (int(z1) >> 4) + 3)
    n_max = min(cap, max(64, nch * (16 if k == 0 else 2)))
    buf = (_F * n_max)()
    n = L.ssa_features116(int(mc), _s64(seed), k, int(x1), int(z1), int(x2), int(z2),
                          1 if exact else 0, buf, n_max)
    return [Feat(k, f.x, f.y, f.z, f.sx, f.sz, f.flags, f.ox, f.oy, f.oz)
            for f in buf[:min(n, n_max)]]


ABOVE = 10              # C F116_ABOVE: attempts higher above the ground are dropped


def ground(mc, seed, pts):
    """Approximate ground Y (cubiomes, 1:4 cells) at [(x, z)] — 1.16."""
    n = len(pts)
    if not n or int(mc) not in VERSIONS:
        return [0] * n
    xs = (ctypes.c_int32 * n)(*[int(p[0]) for p in pts])
    zs = (ctypes.c_int32 * n)(*[int(p[1]) for p in pts])
    out = (ctypes.c_int32 * n)()
    _lib().ssa_ground116(int(mc), _s64(seed), xs, zs, n, out)
    return list(out)


def below_ground(mc, seed, feats):
    """The dungeon attempts at most ABOVE blocks above the approximate ground
    (others kept as they are), as the condition's "below the ground". Exact
    (real) dungeons are kept: they are in the rock by definition."""
    d = [f for f in feats if f.kind == 0 and not f.exact]
    if not d:
        return list(feats)
    gy = dict(zip(((f.x >> 2, f.z >> 2) for f in d), ground(mc, seed, [(f.x, f.z) for f in d])))
    return [f for f in feats if f.kind != 0 or f.y <= gy[(f.x >> 2, f.z >> 2)] + ABOVE]


def lake_columns(seed, f: Feat):
    """bytes(256): 1 where the lake has lava (any layer), index (dx * 16 + dz)
    from its box origin; b'' if not found."""
    cols = (ctypes.c_uint8 * 256)()
    n = _lib().ssa_lake116_shape(_s64(seed), f.ox, f.oz, 1 if f.flags & 2 else 0, None, cols)
    return bytes(cols) if n > 0 else b""


def lake_cells(seed, f: Feat):
    """[(x, y, z)] of the lava blocks LakeFeature would place (solid rock)."""
    cells = (ctypes.c_uint8 * 2048)()
    if _lib().ssa_lake116_shape(_s64(seed), f.ox, f.oz, 1 if f.flags & 2 else 0, cells, None) <= 0:
        return []
    return [(f.ox + a, f.oy + c, f.oz + b) for a in range(16) for b in range(16) for c in range(1, 4)
            if cells[(a * 16 + b) * 8 + c]]


# ── the real Minecraft 1.16.1 (SpawnWorker « FEAT ») ──────────────────────────
# (10/10) the real top lava Y Minecraft gave a lake (after its drop):
# {(seed, box origin x, box origin z): Y}; surface lakes get their Y this way
REAL_Y: dict = {}


def check(seed, feats, callback):
    """callback(list of (Feat, ok, detail) | None, error | None) on a worker
    thread. Dungeon: a spawner at its X Y Z; lava lake: ≥ half of its lava
    cells are lava, the lake dropped as in the game (surface ones to the
    ground) — its real Y goes to REAL_Y."""
    import spawn_check as sc
    req = []
    for f in feats:
        if f.kind == 0:
            req.append(f"0 {f.x} {f.y} {f.z}")
        else:
            cx, cz = f.chunk()
            req.append(f"1 {cx} {cz} {0 if f.flags & 2 else 1}")
    if not req:
        callback([], None)
        return
    line = f"FEAT {_s64(seed)} {len(req)} " + " ".join(req)

    def cb(res, err):
        if res is None or not res.get("feat"):
            callback(None, err or "no answer")
            return
        out = []
        for j, f in enumerate(feats):
            v = res["res"][j] if j < len(res["res"]) else 0
            if f.kind == 0:
                out.append((f, bool(v), "spawner found" if v else "no dungeon (no cave next to it)"))
            else:
                pm, y = v if isinstance(v, tuple) else (v, f.y)
                if pm >= 500:
                    REAL_Y[(int(seed), f.ox, f.oz)] = y
                out.append((f, pm >= 500, f"{pm / 10:.0f} % of its lava is there"
                            + (f" · top lava Y {y}" if pm >= 500 else "")))
        callback(out, None)
    sc.pool().submit_line(line, cb)


def real_y(seed, f):
    """The lake's real Y (exact terrain or confirmed by Minecraft; None if
    unknown / dungeon)."""
    if f.kind == 1 and f.exact:
        return f.y
    return REAL_Y.get((int(seed), f.ox, f.oz)) if f.kind == 1 else None


def check_many(seed, feats, timeout=120.0):
    """Blocking check (tests / tools)."""
    ev, box = threading.Event(), {}

    def cb(r, e):
        box["r"], box["e"] = r, e
        ev.set()
    check(seed, feats, cb)
    ev.wait(timeout)
    if box.get("r") is None:
        raise RuntimeError(box.get("e") or "timeout")
    return box["r"]


def condition_features(cond, seed, mc, at=(0, 0), exact=True):
    """The features a dungeon / lava lake condition accepts around `at` (its
    parent's position): the area, the lake kind and the Y range."""
    from custom_search import feature116_yrange
    t = cond.get("type")
    p = cond.get("params", {}) or {}
    ax, az = int(at[0]), int(at[1])
    rmax = int(cond.get("rmax", 0) or 0)
    if rmax > 0:
        rb = rmax - 1
        box = (ax - rb, az - rb, ax + rb, az + rb)
    else:
        box = (ax + int(cond.get("x1", 0)), az + int(cond.get("z1", 0)),
               ax + int(cond.get("x2", 0)), az + int(cond.get("z2", 0)))
    lo, hi = feature116_yrange(p)
    full = lo <= 0 and hi >= 255
    var = p.get("variant", "any")
    out = []
    for f in features(mc, seed, t, *box, exact=exact):
        if rmax > 0 and (f.x - ax) ** 2 + (f.z - az) ** 2 >= (rmax - 1) ** 2 + 1:
            continue
        if f.kind == 1:
            if var == "underground" and f.surface or var == "surface" and not f.surface:
                continue
            if f.surface and not f.exact:
                if full:
                    out.append(f)
                continue
        if lo <= f.y <= hi:
            out.append(f)
    if t == "dungeon" and p.get("below_ground", True):
        out = below_ground(mc, seed, out)
    return out


def confirm_targets(conds, seed, mc):
    """[(condition, needed count, [Feat])] for the enabled TOP-LEVEL dungeon /
    lava lake conditions that ask for some (instances ≥ 1) — what a result
    must really have in Minecraft. Relative ones (their area follows a parent)
    and exclusions are not checked."""
    if int(mc) not in VERSIONS:
        return []
    out = []
    for c in conds:
        if not c.get("enabled", True) or c.get("type") not in KINDS:
            continue
        if int(c.get("relative", 0) or 0):
            continue
        need = int(c.get("instances", 1) or 0)
        if need <= 0:
            continue
        out.append((c, need, condition_features(c, seed, mc)))
    return out


def uncertain(feats):
    """The features Minecraft still has to confirm (48-bit ones, or exact
    ones near a village)."""
    return [f for f in feats if not f.real]
