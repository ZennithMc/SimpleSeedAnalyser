"""
prefeatures118.py — what the decoration of a chunk changes underground before its
stronghold, 1.18 → 26.x (24/10): amethyst geodes (step 2), mineshafts
(mineshaft118) and dungeons (monster_room, monster_room_deep) (step 3), for the
chunk itself AND its 8 neighbours, decorated first (their geodes / dungeons
reach into it), as for 1.16. Not modelled: lava lakes, icebergs, fossils,
dripstone, other structures (trial chambers, ancient cities are later steps).

Codes: mineshaft118's (0 solid, 1 air, 2 water, 3 lava, 4.. mineshaft blocks)
+ COBBLE, MOSSY, CHEST.

    blocks(seed, mc, cx, cz, bury_boxes) -> bytes [((y + 64) * 16 + z) * 16 + x] | None
"""
from __future__ import annotations

import mineshaft118 as ms

COBBLE, MOSSY, CHEST = 12, 13, 14
MIN_Y, MAX_Y = -64, 319
_LEGACY_SOLID = {ms.SOLID, ms.PLANKS, ms.LOG, ms.FENCE, ms.SPAWNER, COBBLE, MOSSY, CHEST}
_CANNOT_REPLACE = {ms.SPAWNER, CHEST}            # #features_cannot_replace (of what is here)


_TERRAIN = {}           # the terrain of a chunk (before any decoration), shared


_PREFETCH = None


def _near_boxes(bury, cx, cz):
    """The buried boxes that change chunk (cx, cz) (Region.chunk's key)."""
    x0, z0 = cx << 4, cz << 4
    return tuple(tuple(b) for b in bury if b[3] >= x0 - 12 and b[0] <= x0 + 27
                 and b[5] >= z0 - 12 and b[2] <= z0 + 27)


def prefetch_terrain(seed, mc, chunks, bury=()):
    """(30/10) the terrain of `chunks` (31/10: with the buried boxes `bury`, as
    Region keys them), computed in threads (libterrain cv_chunk releases the GIL,
    one context per thread)."""
    import os
    import terrain
    from concurrent.futures import ThreadPoolExecutor
    seed = int(seed)
    keyed = [(c, _near_boxes(bury, c[0], c[1]) if bury else ()) for c in chunks]
    todo = [(c, bx) for c, bx in keyed if (seed, mc, c[0], c[1], bx) not in _TERRAIN]
    if len(todo) < 4:
        return
    todo = todo[:600]
    if len(_TERRAIN) + len(todo) > 640:
        _TERRAIN.clear()

    def one(cb):
        c, bx = cb
        return (c, bx), terrain.chunk_underground(seed, mc, c[0], c[1], bx)
    global _PREFETCH
    if _PREFETCH is None:           # kept: each thread keeps its terrain context
        _PREFETCH = ThreadPoolExecutor(max(1, min(8, (os.cpu_count() or 2))))
    if True:
        for (c, bx), blk in _PREFETCH.map(one, todo):
            if blk is not None:
                _TERRAIN[(seed, mc, c[0], c[1], bx)] = blk


ALL_BANDS = (1 << 48) - 1
_BANDS = {}             # (31/10) (seed, mc, cx, cz, boxes) -> [bytearray, layers present]


def ensure_bands(seed, mc, cx, cz, boxes, mask):
    """(31/10) the plain terrain of chunk (cx, cz) with at least the 8-block layers
    `mask` computed: (buffer, layers present) — the buffer is shared: copy it."""
    import terrain
    key = (seed, mc, cx, cz, boxes)
    full = _TERRAIN.get(key)
    if full is not None:
        return full, ALL_BANDS
    e = _BANDS.get(key)
    if e is None:
        if len(_BANDS) > 700:
            _BANDS.clear()
        e = _BANDS[key] = [bytearray(384 * 256), 0]
    miss = mask & ~e[1]
    if miss:
        if not terrain.chunk_bands(seed, mc, cx, cz, e[0], miss, boxes):
            blk = terrain.chunk_underground(seed, mc, cx, cz, boxes)
            if blk is None:
                raise RuntimeError("no terrain")
            e[0][:] = blk
            miss = ALL_BANDS & ~e[1]
        e[1] |= miss
    if e[1] == ALL_BANDS:
        if len(_TERRAIN) > 640:
            _TERRAIN.clear()
        blk = _TERRAIN[key] = bytes(e[0])
        _BANDS.pop(key, None)
        return blk, ALL_BANDS
    return e[0], e[1]


def prefetch_bands(seed, mc, needs):
    """(31/10) ensure_bands for {(cx, cz): layers} (no buried boxes), in threads."""
    global _PREFETCH
    import os
    from concurrent.futures import ThreadPoolExecutor
    seed = int(seed)
    todo = []
    for (cx, cz), m in needs.items():
        key = (seed, mc, cx, cz, ())
        if key in _TERRAIN:
            continue
        e = _BANDS.get(key)
        if e is None or m & ~e[1]:
            todo.append(((cx, cz), m))
    if not todo:
        return
    for (cx, cz), _m in todo:                      # the entries exist before the threads
        key = (seed, mc, cx, cz, ())
        if key not in _BANDS:
            if len(_BANDS) > 700:
                _BANDS.clear()
            _BANDS[key] = [bytearray(384 * 256), 0]

    def one(cm):
        (cx, cz), m = cm
        ensure_bands(seed, mc, cx, cz, (), m)
    if _PREFETCH is None:
        _PREFETCH = ThreadPoolExecutor(max(1, min(8, (os.cpu_count() or 2))))
    list(_PREFETCH.map(one, todo))


class Region:
    """Chunks (cx, cz) -> bytearray, built on demand (terrain + their own mineshafts)."""

    def __init__(self, seed, mc, bury, bands=False):
        self.seed, self.mc, self.bury = seed, mc, bury
        self.ch = {}
        self.bands = bands          # (31/10) chunks computed by 8-block layers on demand
        self.part = {}              # (cx, cz) -> [bytearray, layers present] (bands mode)

    def layers(self, cx, cz, mask):
        """(31/10, bands mode) chunk (cx, cz) with at least the layers `mask`:
        (bytearray, layers present)."""
        c = self.ch.get((cx, cz))
        if c is not None:
            return c, ALL_BANDS
        e = self.part.get((cx, cz))
        have = e[1] if e is not None else 0
        miss = mask & ~have
        if miss:
            buf, bh = ensure_bands(self.seed, self.mc, cx, cz, _near_boxes(self.bury, cx, cz), mask | have)
            if e is None:
                e = self.part[(cx, cz)] = [bytearray(buf), bh]
            else:
                new = bh & ~have
                b = 0
                while new >> b:
                    if (new >> b) & 1:
                        e[0][b * 2048:(b + 1) * 2048] = buf[b * 2048:(b + 1) * 2048]
                    b += 1
                e[1] |= new
            if e[1] == ALL_BANDS:
                del self.part[(cx, cz)]
                self.ch[(cx, cz)] = e[0]
                return e[0], ALL_BANDS
        return e[0], e[1]

    def chunk(self, cx, cz):
        c = self.ch.get((cx, cz))
        if c is None and self.bands:
            return self.layers(cx, cz, ALL_BANDS)[0]
        if c is None:
            import terrain
            boxes = _near_boxes(self.bury, cx, cz)
            key = (self.seed, self.mc, cx, cz, boxes)
            blk = _TERRAIN.get(key)
            if blk is None:
                blk = terrain.chunk_underground(self.seed, self.mc, cx, cz, boxes)
                if blk is None:
                    raise RuntimeError("no terrain")
                if len(_TERRAIN) > 640:
                    _TERRAIN.clear()
                _TERRAIN[key] = blk
            c = self.ch[(cx, cz)] = bytearray(blk)
        return c

    def get(self, x, y, z):
        if not MIN_Y <= y <= MAX_Y:
            return ms.AIR
        c = self.chunk(x >> 4, z >> 4)
        return c[((((y + 64) << 4) | (z & 15)) << 4) | (x & 15)]

    def set(self, x, y, z, v):
        if not MIN_Y <= y <= MAX_Y:
            return
        c = self.chunk(x >> 4, z >> 4)
        c[((((y + 64) << 4) | (z & 15)) << 4) | (x & 15)] = v


def _nint(r, n):
    return ms._nint(r, n)


def _ndouble(r):
    return ((r.next(26) << 27) + r.next(27)) * (1.0 / (1 << 53))


_SOLID_LUT = None


def _box(R, x0, x1, y0, y1, z0, z1):
    """(30/10) the blocks of Region R in [x0, x1] × [y0, y1] × [z0, z1] as a numpy
    array [x, y, z] (AIR outside the world's height)."""
    import numpy as np
    out = np.full((x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1), ms.AIR, dtype=np.uint8)
    ya, yb = max(y0, MIN_Y), min(y1, MAX_Y)
    if ya > yb:
        return out
    for cx in range(x0 >> 4, (x1 >> 4) + 1):
        for cz in range(z0 >> 4, (z1 >> 4) + 1):
            a = np.frombuffer(R.chunk(cx, cz), dtype=np.uint8).reshape(384, 16, 16)  # y z x
            xa, xb = max(x0, cx << 4), min(x1, (cx << 4) + 15)
            za, zb = max(z0, cz << 4), min(z1, (cz << 4) + 15)
            sub = a[ya + 64:yb + 65, za & 15:(zb & 15) + 1, xa & 15:(xb & 15) + 1]
            out[xa - x0:xb - x0 + 1, ya - y0:yb - y0 + 1, za - z0:zb - z0 + 1] = sub.transpose(2, 0, 1)
    return out


def _dungeon_ok(R, ox, oy, oz, k, l, p, q):
    """The check of MonsterRoomFeature.place, vectorised (no side effect)."""
    import numpy as np
    global _SOLID_LUT
    if _SOLID_LUT is None:
        _SOLID_LUT = np.zeros(256, dtype=bool)
        _SOLID_LUT[list(_LEGACY_SOLID)] = True
    b = _box(R, ox + k, ox + l, oy - 1, oy + 4, oz + p, oz + q)
    sol = _SOLID_LUT[b]
    if not sol[:, 0, :].all() or not sol[:, 5, :].all():
        return False
    air = b == ms.AIR
    wall = np.zeros(sol.shape[::2], dtype=bool)
    wall[0, :] = wall[-1, :] = True
    wall[:, 0] = wall[:, -1] = True
    n = int((wall & air[:, 1, :] & air[:, 2, :]).sum())
    return 1 <= n <= 5


def _dungeon(R, r, ox, oy, oz):
    """MonsterRoomFeature.place (1.18+): True if placed."""
    get, put = R.get, R.set
    j = _nint(r, 2) + 2
    k, l = -j - 1, j + 1
    o = _nint(r, 2) + 2
    p, q = -o - 1, o + 1
    if isinstance(R, Region):
        if not _dungeon_ok(R, ox, oy, oz, k, l, p, q):
            return False
        n = 1
    else:
        n = 0
    for x in (range(k, l + 1) if not n else ()):
        for y in range(-1, 5):
            for z in range(p, q + 1):
                b = get(ox + x, oy + y, oz + z)
                solid = b in _LEGACY_SOLID
                if y == -1 and not solid:
                    return False
                if y == 4 and not solid:
                    return False
                if (x in (k, l) or z in (p, q)) and y == 0 and b == ms.AIR \
                        and get(ox + x, oy + y + 1, oz + z) == ms.AIR:
                    n += 1
    if n < 1 or n > 5:
        return False
    for x in range(k, l + 1):
        for y in range(3, -2, -1):
            for z in range(p, q + 1):
                X, Y, Z = ox + x, oy + y, oz + z
                b = get(X, Y, Z)
                if x in (k, l) or y in (-1, 4) or z in (p, q):
                    if Y >= MIN_Y and get(X, Y - 1, Z) not in _LEGACY_SOLID:
                        put(X, Y, Z, ms.AIR)
                    elif b in _LEGACY_SOLID and b != CHEST:
                        if y == -1 and _nint(r, 4) != 0:
                            if b not in _CANNOT_REPLACE:
                                put(X, Y, Z, MOSSY)
                        elif b not in _CANNOT_REPLACE:
                            put(X, Y, Z, COBBLE)
                elif b not in (CHEST, ms.SPAWNER):
                    if b not in _CANNOT_REPLACE:
                        put(X, Y, Z, ms.AIR)
    chests = []
    for _i in range(2):
        for _j in range(3):
            X = ox + _nint(r, j * 2 + 1) - j
            Z = oz + _nint(r, o * 2 + 1) - o
            if get(X, oy, Z) != ms.AIR:
                continue
            c = sum(1 for dx, dz in ((0, -1), (1, 0), (0, 1), (-1, 0))
                    if get(X + dx, oy, Z + dz) in _LEGACY_SOLID)
            if c != 1:
                continue
            if get(X, oy, Z) not in _CANNOT_REPLACE:
                put(X, oy, Z, CHEST)
            ls = ms._nlong(r)                    # the chest's loot table seed
            chests.append((X, oy, Z, ls))
            break
    if get(ox, oy, oz) not in _CANNOT_REPLACE:
        put(ox, oy, oz, ms.SPAWNER)
    mob = _nint(r, 4)                            # the mob
    if DCAP is not None:                         # (30/10) dungeon_loot: the dungeons placed
        DCAP.append((ox, oy, oz, j, o, mob, chests))
    return True


DCAP = None             # (30/10) a list: _dungeon appends (x, y, z, j, o, mob, [(cx, cy, cz, seed)])


# global feature indices of UNDERGROUND_STRUCTURES (step 3): fossil_upper,
# fossil_lower, monster_room, monster_room_deep (FeatureSorter over the biomes)
_ROOM_INDEX = (2, 3)


def _dungeons(R, seed, mc, cx, cz):
    if USE_C and isinstance(R, Region) and _dungeons_c(R, seed, mc, cx, cz):
        return
    import structure_loot as sl
    x0, z0 = cx << 4, cz << 4
    for idx, (cnt, lo, hi) in zip(_ROOM_INDEX, ((10, 0, 319), (4, -58, -1))):
        r = sl.structure_rng(seed, mc, cx, cz, idx, 3)
        for _a in range(cnt):
            x = _nint(r, 16) + x0
            z = _nint(r, 16) + z0
            y = _nint(r, hi - lo + 1) + lo
            _dungeon(R, r, x, y, z)


_DG_SOLID = sum(1 << v for v in _LEGACY_SOLID)
_DG_CANNOT = sum(1 << v for v in _CANNOT_REPLACE)


def _dungeons_c(R, seed, mc, cx, cz):
    """(31/10) _dungeons in C (libterrain cv_dungeons118) on the 3 × 3 chunks of R;
    False: no library."""
    import ctypes as ct
    try:
        import terrain
        terrain._load()
        f = getattr(terrain._LIB, "cv_dungeons118", None)
    except Exception:
        f = None
    if f is None:
        return False
    if not getattr(f, "_ssa_set", False):
        f.argtypes = [ct.c_uint64, ct.c_int, ct.c_int, ct.c_void_p, ct.c_uint32, ct.c_uint32,
                      ct.c_void_p, ct.c_int]
        f.restype = ct.c_int
        f._ssa_set = True
    out = (ct.c_int64 * (15 * 14))()
    if R.bands:
        return _dungeons_bands(R, seed, cx, cz, out)
    bufs = []
    ptrs = (ct.c_void_p * 9)()
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            b = (ct.c_uint8 * (384 * 256)).from_buffer(R.chunk(cx + dx, cz + dz))
            bufs.append(b)
            ptrs[(dz + 1) * 3 + dx + 1] = ct.addressof(b)
    n = f(ct.c_uint64(int(seed) & ms.M64), cx, cz, ptrs, _DG_SOLID, _DG_CANNOT, out, 14)
    del bufs
    _dcap(out, n)
    return True


def _dcap(out, n):
    if DCAP is not None:
        for i in range(n):
            v = out[15 * i:15 * i + 15]
            DCAP.append((v[0], v[1], v[2], v[3], v[4], v[5],
                         [(v[7 + 4 * c], v[8 + 4 * c], v[9 + 4 * c], v[10 + 4 * c])
                          for c in range(v[6])]))


def _fb():
    import ctypes as ct
    import terrain
    terrain._load()
    f = terrain._LIB.cv_dungeons118b
    if not getattr(f, "_ssa_set", False):
        f.argtypes = [ct.c_uint64, ct.c_int, ct.c_int, ct.c_void_p, ct.c_void_p, ct.c_uint32, ct.c_uint32,
                      ct.c_void_p, ct.c_int, ct.c_void_p, ct.c_void_p, ct.c_int]
        f.restype = ct.c_int
        f._ssa_set = True
    return f


def dungeon_needs(seed, cx, cz):
    """(31/10) the 8-block layers the dungeon attempts of chunk (cx, cz) read in its
    3 × 3 chunks if they all fail (the usual case): {(cx', cz'): layers}."""
    import ctypes as ct
    f = _fb()
    ptrs = (ct.c_void_p * 9)()
    have = (ct.c_uint64 * 9)()
    st = (ct.c_uint64 * 4)()
    need = (ct.c_uint64 * 9)()
    f(ct.c_uint64(int(seed) & ms.M64), cx, cz, ptrs, have, 0, 0, st, 1, need, None, 0)
    return {(cx + i % 3 - 1, cz + i // 3 - 1): need[i] for i in range(9) if need[i]}


def _dungeons_bands(R, seed, cx, cz, out):
    """The dungeons on a bands-mode Region: the missing layers computed when an
    attempt reaches them (the C stops before it and resumes there)."""
    import ctypes as ct
    f = _fb()
    st = (ct.c_uint64 * 4)()
    need = (ct.c_uint64 * 9)()
    while True:
        ptrs = (ct.c_void_p * 9)()
        have = (ct.c_uint64 * 9)()
        bufs = []
        for i in range(9):
            X, Z = cx + i % 3 - 1, cz + i // 3 - 1
            c = R.ch.get((X, Z))
            h = ALL_BANDS
            if c is None:
                e = R.part.get((X, Z))
                c, h = (e[0], e[1]) if e is not None else (None, 0)
            if c is not None:
                b = (ct.c_uint8 * (384 * 256)).from_buffer(c)
                bufs.append(b)
                ptrs[i] = ct.addressof(b)
            have[i] = h
        n = f(ct.c_uint64(int(seed) & ms.M64), cx, cz, ptrs, have, _DG_SOLID, _DG_CANNOT, st, 0, need, out, 14)
        del bufs
        _dcap(out, n)
        if st[0] == 2:
            return True
        for i in range(9):
            if need[i]:
                R.layers(cx + i % 3 - 1, cz + i // 3 - 1, need[i])


def _mineshafts(R, seed, mc, cx, cz):
    bio, deep = ms.biome_fns(seed, mc)
    if R.bands and not ms._reaching(seed, mc, cx, cz, bio, ms._height_fn(seed, mc))[3]:
        return                                # (31/10) no piece: the chunk is not needed
    c = R.chunk(cx, cz)
    out, _n, _m = ms.place_chunk(seed, mc, cx, cz, bytes(c), bio, deep, height=ms._height_fn(seed, mc))
    c[:] = out


def _decorate(R, seed, mc, cx, cz):
    _geodes(R, seed, mc, cx, cz)              # step 2 LOCAL_MODIFICATIONS
    _mineshafts(R, seed, mc, cx, cz)          # step 3: the structures, then
    _dungeons(R, seed, mc, cx, cz)            # its features


DUNGEONS = True


def mineshaft_chests(seed, mc, cx, cz, neighbours=True, reloaded=False):
    """(29/10) the chest minecarts chunk (cx, cz) places: [(x, y, z, loot seed,
    (start cx, start cz))] — its 8 neighbours decorated first if `neighbours`,
    then its own geodes, then its mineshafts (step 3, before its dungeons)."""
    seed = int(seed)
    ms.SPIDER_DONE.clear()
    R = Region(seed, mc, [])
    r = 2 if neighbours else 1                # (31/10) their terrain first, in threads
    prefetch_terrain(seed, mc, [(cx + a, cz + b) for b in range(-r, r + 1) for a in range(-r, r + 1)])
    if neighbours:
        for dz in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx or dz:
                    _decorate(R, seed, mc, cx + dx, cz + dz)
    _geodes(R, seed, mc, cx, cz)
    c = R.chunk(cx, cz)
    bio, deep = ms.biome_fns(seed, mc)
    out = []
    del ms.CHEST_TRIES[:]
    ms.place_chunk(seed, mc, cx, cz, bytes(c), bio, deep, chests=out, height=ms._height_fn(seed, mc),
                   reloaded=reloaded)
    return out


def mineshaft_chest_tries(seed, mc, cx, cz):
    """(29/10) the chest attempts of chunk (cx, cz) alone (placed or not: the spot
    may be air only once the neighbours are decorated): [(x, y, z, start)]."""
    mineshaft_chests(seed, mc, cx, cz, False)
    return list(ms.CHEST_TRIES)


def blocks(seed, mc, cx, cz, bury=(), neighbours=True):
    """The chunk (cx, cz) when its stronghold is placed: the 8 neighbours (if
    `neighbours`: decorated before it) then the chunk itself decorated."""
    seed = int(seed)
    ms.SPIDER_DONE.clear()
    R = Region(seed, mc, list(bury))
    if DUNGEONS:                              # (31/10) their terrain first, in threads
        prefetch_terrain(seed, mc, [(cx + a, cz + b) for b in range(-2, 3) for a in range(-2, 3)], R.bury)

        for dz in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if (dx or dz) and neighbours:
                    _decorate(R, seed, mc, cx + dx, cz + dz)
        _decorate(R, seed, mc, cx, cz)
    else:
        _mineshafts(R, seed, mc, cx, cz)
    return bytes(R.chunk(cx, cz))


# ───────────────────────── amethyst geodes (LOCAL_MODIFICATIONS) ─────────────────────────
AMETHYST, BUDDING, CALCITE, BASALT, BUD = 15, 16, 17, 18, 19
_GRAD = ((1, 1, 0), (-1, 1, 0), (1, -1, 0), (-1, -1, 0), (1, 0, 1), (-1, 0, 1), (1, 0, -1), (-1, 0, -1),
         (0, 1, 1), (0, -1, 1), (0, 1, -1), (0, -1, -1), (1, 1, 0), (0, -1, 1), (-1, 1, 0), (0, -1, -1))


def _jhash(s):
    h = 0
    for ch in s:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    return h - (1 << 32) if h >> 31 else h


class _Improved:
    def __init__(self, r):
        self.xo = r.next_double() * 256.0
        self.yo = r.next_double() * 256.0
        self.zo = r.next_double() * 256.0
        p = list(range(256))
        for i in range(256):
            j = r.next_int(256 - i)
            p[i], p[i + j] = p[i + j], p[i]
        self.p = p

    def noise(self, x, y, z):
        import math
        d, e, f = x + self.xo, y + self.yo, z + self.zo
        i, j, k = math.floor(d), math.floor(e), math.floor(f)
        g, h, l = d - i, e - j, f - k
        P = self.p
        a = P[i & 255]
        b = P[(i + 1) & 255]
        c0 = P[(a + j) & 255]
        c1 = P[(a + j + 1) & 255]
        c2 = P[(b + j) & 255]
        c3 = P[(b + j + 1) & 255]
        k &= 255

        def gd(hh, X, Y, Z):
            gg = _GRAD[hh & 15]
            return gg[0] * X + gg[1] * Y + gg[2] * Z
        v000 = gd(P[(c0 + k) & 255], g, h, l)
        v100 = gd(P[(c2 + k) & 255], g - 1, h, l)
        v010 = gd(P[(c1 + k) & 255], g, h - 1, l)
        v110 = gd(P[(c3 + k) & 255], g - 1, h - 1, l)
        v001 = gd(P[(c0 + k + 1) & 255], g, h, l - 1)
        v101 = gd(P[(c2 + k + 1) & 255], g - 1, h, l - 1)
        v011 = gd(P[(c1 + k + 1) & 255], g, h - 1, l - 1)
        v111 = gd(P[(c3 + k + 1) & 255], g - 1, h - 1, l - 1)

        def ss(t):
            return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

        def lerp(t, a_, b_):
            return a_ + t * (b_ - a_)
        s, t, u = ss(g), ss(h), ss(l)
        return lerp(u, lerp(t, lerp(s, v000, v100), lerp(s, v010, v110)),
                    lerp(t, lerp(s, v001, v101), lerp(s, v011, v111)))


    def noise_v(self, x, y, z):
        """noise on numpy arrays (same IEEE operations, same order)."""
        import numpy as np
        G = np.array(_GRAD, dtype=np.float64)
        P = np.array(self.p + self.p, dtype=np.int64)
        d, e, f = x + self.xo, y + self.yo, z + self.zo
        fi, fj, fk = np.floor(d), np.floor(e), np.floor(f)
        g, h, l = d - fi, e - fj, f - fk
        i, j, k = fi.astype(np.int64), fj.astype(np.int64), fk.astype(np.int64)
        a = P[i & 255]
        b = P[(i + 1) & 255]
        c0 = P[(a + j) & 255]
        c1 = P[(a + j + 1) & 255]
        c2 = P[(b + j) & 255]
        c3 = P[(b + j + 1) & 255]
        k &= 255

        def gd(hh, X, Y, Z):
            gg = G[hh & 15]
            return gg[..., 0] * X + gg[..., 1] * Y + gg[..., 2] * Z
        v000 = gd(P[(c0 + k) & 255], g, h, l)
        v100 = gd(P[(c2 + k) & 255], g - 1, h, l)
        v010 = gd(P[(c1 + k) & 255], g, h - 1, l)
        v110 = gd(P[(c3 + k) & 255], g - 1, h - 1, l)
        v001 = gd(P[(c0 + k + 1) & 255], g, h, l - 1)
        v101 = gd(P[(c2 + k + 1) & 255], g - 1, h, l - 1)
        v011 = gd(P[(c1 + k + 1) & 255], g, h - 1, l - 1)
        v111 = gd(P[(c3 + k + 1) & 255], g - 1, h - 1, l - 1)

        def ss(t):
            return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

        def lerp(t, a_, b_):
            return a_ + t * (b_ - a_)
        s, t, u = ss(g), ss(h), ss(l)
        return lerp(u, lerp(t, lerp(s, v000, v100), lerp(s, v010, v110)),
                    lerp(t, lerp(s, v001, v101), lerp(s, v011, v111)))


def _wrap_v(d):
    import numpy as np
    return d - np.floor(d / 3.3554432E7 + 0.5) * 3.3554432E7


def _value_v(self, x, y, z):
    """value() on numpy arrays."""
    q = 0.0625
    a = self.n[0].noise_v(_wrap_v(x * q), _wrap_v(y * q), _wrap_v(z * q))
    x2, y2, z2 = x * 1.0181268882175227, y * 1.0181268882175227, z * 1.0181268882175227
    b = self.n[1].noise_v(_wrap_v(x2 * q), _wrap_v(y2 * q), _wrap_v(z2 * q))
    return (a + b) * (0.16666666666666666 / 0.2)


def _fast_inv_sqrt_v(d):
    import numpy as np
    e = 0.5 * d
    l = d.view(np.int64)
    l = np.int64(6910469410427058090) - (l >> 1)
    d = l.view(np.float64)
    return d * (1.5 - e * d * d)


def _wrap(d):
    import math
    return d - math.floor(d / 3.3554432E7 + 0.5) * 3.3554432E7


class _GeodeNoise:
    """NormalNoise.create(WorldgenRandom(LegacyRandomSource(seed)), -4, 1.0): two
    PerlinNoise of one octave (positional: fromHashOf("octave_-4"))."""

    def __init__(self, seed):
        r = ms.Lcg(seed & ms.M64)
        h = _jhash("octave_-4")
        f1 = r.next_long()
        n1 = _Improved(ms.Lcg((h ^ f1) & ms.M64))
        f2 = r.next_long()
        n2 = _Improved(ms.Lcg((h ^ f2) & ms.M64))
        self.n = (n1, n2)

    def value(self, x, y, z):
        q = 0.0625
        a = self.n[0].noise(_wrap(x * q), _wrap(y * q), _wrap(z * q))
        x2, y2, z2 = x * 1.0181268882175227, y * 1.0181268882175227, z * 1.0181268882175227
        b = self.n[1].noise(_wrap(x2 * q), _wrap(y2 * q), _wrap(z2 * q))
        return (a + b) * (0.16666666666666666 / 0.2)

    value_v = _value_v


_GN = {}
_GEO_CANNOT = {ms.SPAWNER, CHEST}
_GEO_INVALID_AIRLIKE = {ms.AIR, ms.WATER, ms.LAVA}      # isAir() or #geode_invalid_blocks (fluids, ice…)


def _fast_inv_sqrt(d):
    """Mth.fastInvSqrt (one Newton step), the geodes' 1/sqrt up to 1.19.2."""
    import struct
    e = 0.5 * d
    l = struct.unpack("<q", struct.pack("<d", d))[0]
    l = 6910469410427058090 - (l >> 1)
    d = struct.unpack("<d", struct.pack("<q", l))[0]
    return d * (1.5 - e * d * d)


def _geode(R, r, seed, ox, oy, oz, only_chunk=None, fast=False, centred=False):
    """GeodeFeature.place (default layer / crack settings, amethyst config).
    fast: Mth.fastInvSqrt for the distances (≤ 1.19.2), else 1 / sqrt."""
    import math
    isq = _fast_inv_sqrt if fast else (lambda v: 1.0 / math.sqrt(v))
    c5 = 0.5 if centred else 0.0                 # 1.17 Vec3i.distSqr: from the block's centre
    nz = _GN.get(seed)
    if nz is None:
        nz = _GN[seed] = _GeodeNoise(seed)
    get, put = R.get, R.set
    npts = _nint(r, 2) + 3                      # distributionPoints uniform(3, 4)
    d13 = npts / 6.0
    d18 = 1.0 / math.sqrt(1.7)
    d20 = 1.0 / math.sqrt(2.2 + d13)
    d22 = 1.0 / math.sqrt(3.2 + d13)
    d24 = 1.0 / math.sqrt(4.2 + d13)
    d26 = 1.0 / math.sqrt(2.0 + _ndouble(r) / 2.0 + (d13 if npts > 3 else 0.0))
    crack = r.next_float() < 0.95
    pts = []
    bad = 0
    for _i in range(npts):
        a = _nint(r, 3) + 4
        b = _nint(r, 3) + 4
        c = _nint(r, 3) + 4
        P = (ox + a, oy + b, oz + c)
        st = get(*P)
        if st in _GEO_INVALID_AIRLIKE:
            bad += 1
            if bad > 1:
                return False
        pts.append((P, _nint(r, 2) + 1))
    cps = []
    if crack:
        k = _nint(r, 4)
        m = npts * 2 + 1
        if k == 0:
            cps = [(ox + m, oy + 7, oz), (ox + m, oy + 5, oz), (ox + m, oy + 1, oz)]
        elif k == 1:
            cps = [(ox, oy + 7, oz + m), (ox, oy + 5, oz + m), (ox, oy + 1, oz + m)]
        elif k == 2:
            cps = [(ox + m, oy + 7, oz + m), (ox + m, oy + 5, oz + m), (ox + m, oy + 1, oz + m)]
        else:
            cps = [(ox, oy + 7, oz), (ox, oy + 5, oz), (ox, oy + 1, oz)]
    placements = []
    res = _geode_eval_c(nz, ox, oy, oz, pts, cps, fast, centred, d24)
    if res is not None:
        hit, Sf, CSf = res
    else:
        hit, Sf, CSf = _geode_eval_np(nz, ox, oy, oz, pts, cps, fast, c5, d24)
    for n in hit:
        iz, rem = divmod(n, 33 * 33)
        iy, ix = divmod(rem, 33)
        X, Y, Z = ox - 16 + ix, oy - 16 + iy, oz - 16 + iz
        s = Sf[n]
        cs = CSf[n]
        if True:
                cur = get(X, Y, Z)
                ok = cur not in _GEO_CANNOT
                if crack and cs >= d26 and s < d18:
                    if ok:
                        put(X, Y, Z, ms.AIR)
                elif s >= d18:
                    if ok:
                        put(X, Y, Z, ms.AIR)
                elif s >= d20:
                    alt = r.next_float() < 0.083
                    if ok:
                        put(X, Y, Z, BUDDING if alt else AMETHYST)
                    if alt and r.next_float() < 0.35:
                        placements.append((X, Y, Z))
                elif s >= d22:
                    if ok:
                        put(X, Y, Z, CALCITE)
                else:
                    if ok:
                        put(X, Y, Z, BASALT)
    for (X, Y, Z) in placements:
        _nint(r, 4)                                  # the bud size
        for dx, dy, dz in ((0, -1, 0), (0, 1, 0), (0, 0, -1), (0, 0, 1), (-1, 0, 0), (1, 0, 0)):
            nb = get(X + dx, Y + dy, Z + dz)
            if nb == ms.AIR or nb == ms.WATER:       # canClusterGrowAtState (air / water source)
                if nb not in _GEO_CANNOT:
                    put(X + dx, Y + dy, Z + dz, BUD)
                break
    return True


def _geode_eval_c(nz, ox, oy, oz, pts, cps, fast, centred, d24):
    """(31/10) the 33³ distance field in C (libterrain cv_geode_eval): ({index: S},
    {index: CS}) of the cells with S >= d24, in the game's order; None: no library."""
    import ctypes as ct
    try:
        import terrain
        terrain._load()
        f = getattr(terrain._LIB, "cv_geode_eval", None)
    except Exception:
        f = None
    if f is None or not USE_C:
        return None
    c = getattr(nz, "_c", None)
    if c is None:
        perm = (ct.c_int32 * 512)(*(list(nz.n[0].p) + list(nz.n[1].p)))
        off = (ct.c_double * 6)(nz.n[0].xo, nz.n[0].yo, nz.n[0].zo, nz.n[1].xo, nz.n[1].yo, nz.n[1].zo)
        c = nz._c = (perm, off)
        f.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_void_p,
                      ct.c_int, ct.c_void_p, ct.c_int, ct.c_int, ct.c_double, ct.c_void_p, ct.c_void_p,
                      ct.c_void_p, ct.c_int]
        f.restype = ct.c_int
        f._ssa_set = True
    fp = [v for (P, o) in pts for v in (P[0], P[1], P[2], o)]
    fc = [v for P in cps for v in P]
    ap = (ct.c_int * max(1, len(fp)))(*fp)
    ac = (ct.c_int * max(1, len(fc)))(*fc)
    cap = 33 * 33 * 33
    hits = (ct.c_int32 * cap)()
    sv = (ct.c_double * cap)()
    cv = (ct.c_double * cap)()
    n = f(c[0], c[1], ox, oy, oz, len(pts), ap, len(cps), ac, int(bool(fast)), int(bool(centred)),
          d24, hits, sv, cv, cap)
    idx = hits[:n]
    return idx, dict(zip(idx, sv[:n])), dict(zip(idx, cv[:n]))


USE_C = True


def _geode_eval_np(nz, ox, oy, oz, pts, cps, fast, c5, d24):
    # (30/10) numpy over the 33³ cube (same IEEE operations and order as the Java
    # loop), then the cells of the shell in the game's order (Z, Y, X: the draws)
    import numpy as np
    Zs, Ys, Xs = np.meshgrid(np.arange(oz - 16, oz + 17, dtype=np.float64),
                             np.arange(oy - 16, oy + 17, dtype=np.float64),
                             np.arange(ox - 16, ox + 17, dtype=np.float64), indexing="ij")
    isv = _fast_inv_sqrt_v if fast else (lambda v: 1.0 / np.sqrt(v))
    nv = nz.value_v(Xs, Ys, Zs) * 0.05
    Xc, Yc, Zc = Xs + c5, Ys + c5, Zs + c5
    S = np.zeros_like(Xs)
    for (px, py, pz), off in pts:
        dx, dy, dz = Xc - px, Yc - py, Zc - pz
        S = S + (isv(dx * dx + dy * dy + dz * dz + off) + nv)
    CS = np.zeros_like(Xs)
    for (px, py, pz) in cps:
        dx, dy, dz = Xc - px, Yc - py, Zc - pz
        CS = CS + (isv(dx * dx + dy * dy + dz * dz + 2) + nv)
    hit = np.nonzero((S >= d24).ravel())[0].tolist()
    return hit, S.ravel().tolist(), CS.ravel().tolist()


_GEODE_INDEX = 2           # LOCAL_MODIFICATIONS: iceberg_packed, iceberg_blue, amethyst_geode, …


_GEO_CACHE = {}


class _Rec:
    """A region that records the writes of one geode (on the terrain alone)."""

    def __init__(self, R):
        self.R, self.w = R, {}

    def get(self, x, y, z):
        v = self.w.get((x, y, z))
        return self.R.get(x, y, z) if v is None else v

    def set(self, x, y, z, v):
        self.w[(x, y, z)] = v


def _geodes(R, seed, mc, cx, cz):
    """(29/10) the geode's writes are computed once per chunk (on the terrain, the
    expensive noise), then replayed: blocks not chest / spawner are replaced."""
    import structure_loot as sl
    k = (int(seed), int(mc), cx, cz)
    wr = _GEO_CACHE.get(k)
    if wr is None:
        r = sl.structure_rng(seed, mc, cx, cz, _GEODE_INDEX, 2)
        wr = ()
        if r.next_float() < 0.0416666679084301:            # 1.0F / 24
            x = _nint(r, 16) + (cx << 4)
            z = _nint(r, 16) + (cz << 4)
            y = _nint(r, 30 - (-58) + 1) - 58
            rec = _Rec(Region(int(seed), mc, list(R.bury)))
            _geode(rec, r, seed, x, y, z, fast=mc <= 23)
            wr = tuple((a, b, c, v) for (a, b, c), v in rec.w.items())
        if len(_GEO_CACHE) > 4096:
            _GEO_CACHE.clear()
        _GEO_CACHE[k] = wr
    for (a, b, c, v) in wr:
        if R.get(a, b, c) not in _GEO_CANNOT:
            R.set(a, b, c, v)


# ───────────────────────── 1.17 geodes (on the 1.16 terrain engine) ─────────────────────────
class _LegacyGeodeNoise:
    """1.17 NormalNoise.create(WorldgenRandom(seed), -4, 1.0), legacy octaves: an
    ImprovedNoise made and dropped (octave 0 → index 4 > size), 3 skipped octaves
    (262 draws each), the kept one; twice."""

    def __init__(self, seed):
        r = ms.Lcg(seed & ms.M64)
        self.n = []
        for _k in range(2):
            _Improved(r)
            for _s in range(3):
                for _i in range(262):
                    r.next(1)
            self.n.append(_Improved(r))

    value = _GeodeNoise.value
    value_v = _value_v


_LGN = {}


class T116Region:
    """1.16 / 1.17 world from ssa_t116_blocks (codes T116_*), mapped to the
    mineshaft118 codes for reading; writes kept for the target chunk only."""

    def __init__(self, seed, mc, cx, cz, target):
        self.seed, self.mc, self.cx, self.cz = seed, mc, cx, cz
        self.t = bytearray(target)                   # the target chunk (T116 codes)
        self.cache = {}

    def _chunk(self, X, Z):
        if (X, Z) == (self.cx, self.cz):
            return self.t
        c = self.cache.get((X, Z))
        if c is None:
            import ctypes as ct
            from cubiomes_wrap import get_lib
            f = get_lib()._lib.ssa_t116_blocks
            f.argtypes = [ct.c_int, ct.c_uint64, ct.c_int32, ct.c_int32, ct.POINTER(ct.c_uint8)]
            buf = (ct.c_uint8 * 65536)()
            f(self.mc, ct.c_uint64(self.seed & ms.M64), X, Z, buf)
            c = self.cache[(X, Z)] = bytes(buf)
        return c

    def get(self, x, y, z):
        if not 0 <= y <= 255:
            return ms.AIR
        b = self._chunk(x >> 4, z >> 4)[(((y << 4) | (z & 15)) << 4) | (x & 15)]
        if b in (0, 25):
            return ms.AIR
        if b == 2:
            return ms.WATER
        if b == 3:
            return ms.LAVA
        if b in (15, 16):                            # ice, packed ice: #geode_invalid_blocks
            return ms.WATER
        return ms.SOLID

    def set(self, x, y, z, v):
        if (x >> 4, z >> 4) != (self.cx, self.cz) or not 0 <= y <= 255:
            return
        self.t[(((y << 4) | (z & 15)) << 4) | (x & 15)] = 25 if v == ms.AIR else 28   # cave air / other


GEODES_117 = True
_GEODE_117_INDEX = 0       # LOCAL_MODIFICATIONS of most biomes: the geode alone


def geodes_117(seed, mc, cx, cz, target, neighbours=True):
    """The 1.17 target chunk (T116 codes) once the amethyst geodes of it and of
    its neighbours are placed (rarity 1/53, square, uniform(6, 46))."""
    seed = int(seed)
    R = T116Region(seed, mc, cx, cz, target)
    nz = _LGN.get(seed)
    if nz is None:
        nz = _LGN[seed] = _LegacyGeodeNoise(seed)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (dx or dz) and not neighbours:
                continue
            X, Z = cx + dx, cz + dz
            import structure_loot as sl
            r = sl.structure_rng(seed, mc, X, Z, _GEODE_117_INDEX, 2)
            if not r.next_float() < 0.01886792480945587:      # 1.0F / 53
                continue
            x = _nint(r, 16) + (X << 4)
            z = _nint(r, 16) + (Z << 4)
            y = _nint(r, 41) + 6
            _GN[("117", seed)] = nz
            _geode_with(R, r, nz, x, y, z, fast=True)
    return bytes(R.t)


def _geode_with(R, r, nz, ox, oy, oz, fast):
    key = ("tmp",)
    _GN[key] = nz
    try:
        return _geode(R, r, key, ox, oy, oz, fast=fast, centred=True)
    finally:
        _GN.pop(key, None)
