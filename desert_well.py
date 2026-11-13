"""
desert_well.py — (30/10) desert wells, 1.16.1 → 26.3.

A desert well is a feature (DesertWellFeature), placed per chunk while it is decorated:
  - the attempt (libcubiomes getStructurePos Desert_Well): 1 chunk in 1000, at a random
    spot of the chunk (1.16 / 1.17: the LCG population random, salt 40013 — the 48 low
    bits; 1.18+: Xoroshiro, salt 40002 — the whole seed);
  - the biome (1.16 / 1.17: the chunk's own biome, getNoiseBiome(cx*4+2, cz*4+2) —
    desert, desert hills or desert lakes; 1.18+: the biome filter at the spot, at the
    MOTION_BLOCKING height — desert);
  - the feature: down to the first non-air block, which must be SAND (water → nothing),
    and no hole 2 deep under its 5 × 5 base (26.3, data-driven: no air at all in the
    5 × 2 × 5 under it, volume_match).
cubiomes tests the biome only (1.18+ at y 316: it finds almost none) — SSA tests
the biome where the game does and the terrain (1.16 / 1.17: the exact 1.16 terrain,
1.18+: terrain.chunk_underground).
"""
from __future__ import annotations

from functools import lru_cache

DESERTS_116 = (2, 17, 130)            # desert, desert_hills, desert_lakes
DESERT = 2


def attempts(seed, mc, x1, z1, x2, z2):
    """[(x, z)] of the chunks' well attempts in the box (no biome)."""
    import struct_info
    import cubiomes_wrap as cw
    lib = struct_info._lib(int(seed), int(mc), None, 0)
    return lib.find_structures_in_box(cw.Desert_Well, int(x1), int(z1), int(x2), int(z2),
                                      verify_biome=False, max_count=1000000)


def _biome_ok(seed, mc, x, z, y):
    import struct_info
    lib = struct_info._lib(int(seed), int(mc), None, 0)
    if mc <= 21:
        cx, cz = x >> 4, z >> 4
        b = int(lib.get_biome_at((cx << 4) + 8, (cz << 4) + 8, scale=4, y=0))
        return b in DESERTS_116
    return int(lib.get_biome_at(x, z, scale=1, y=y)) == DESERT


@lru_cache(maxsize=4096)
def _t116(seed, mc, cx, cz):
    import ctypes as ct
    from cubiomes_wrap import get_lib
    f = getattr(get_lib()._lib, "ssa_t116_blocks", None)
    if f is None:
        return None
    f.argtypes = [ct.c_int, ct.c_uint64, ct.c_int32, ct.c_int32, ct.POINTER(ct.c_uint8)]
    f.restype = ct.c_int
    buf = (ct.c_uint8 * 65536)()
    if not f(int(mc), ct.c_uint64(int(seed) & ((1 << 64) - 1)), cx, cz, buf):
        return None
    return bytes(buf)


@lru_cache(maxsize=2048)
def _t118(seed, mc, cx, cz):
    import terrain
    return terrain.chunk_underground(int(seed), int(mc), cx, cz, ())


T116_AIR, T116_SAND, T116_CAVE_AIR = 0, 10, 25
T116_WATER = 2


def _getter(seed, mc):
    """get(x, y, z) → "air" | "sand" | "water" | "solid" | None (unknown)."""
    if mc <= 21:
        def get(x, y, z):
            if not 0 <= y <= 255:
                return "air"
            c = _t116(seed, mc, x >> 4, z >> 4)
            if c is None:
                return None
            b = c[(((y << 4) | (z & 15)) << 4) | (x & 15)]
            if b in (T116_AIR, T116_CAVE_AIR):
                return "air"
            if b == T116_SAND:
                return "sand"
            if b in (T116_WATER, 3):
                return "water"
            return "solid"
        return get

    def get(x, y, z):
        if not -64 <= y <= 319:
            return "air"
        c = _t118(seed, mc, x >> 4, z >> 4)
        if c is None:
            return None
        b = c[((((y + 64) << 4) | (z & 15)) << 4) | (x & 15)]
        if b == 1:
            return "air"
        if b in (2, 3):
            return "water"
        return "sand"                  # a desert's floor is sand (surface rule)
    return get


_REG = {}


def register_terrain(raw):
    """Give libcubiomes (raw CDLL) the terrain library's surface functions (1.18+
    wells in the C search). Once per library; harmless if not available."""
    if id(raw) in _REG:
        return _REG[id(raw)]
    ok = False
    try:
        import ctypes as ct
        import terrain
        terrain._load()
        T = terrain._LIB
        f = getattr(raw, "ssa_set_terrain_fns", None)
        if f is not None and T is not None and all(hasattr(T, n) for n in
                                                   ("cv_alloc", "cv_init", "cv_surface", "cv_solid_top",
                                                    "cv_chunk")):
            f.argtypes = [ct.c_void_p] * 5
            f.restype = None
            addr = lambda fn: ct.cast(fn, ct.c_void_p).value
            f(addr(T.cv_alloc), addr(T.cv_init), addr(T.cv_surface), addr(T.cv_solid_top),
              addr(T.cv_chunk))
            ok = True
    except Exception:
        ok = False
    _REG[id(raw)] = ok
    return ok


@lru_cache(maxsize=65536)
def exists(seed, mc, x, z):
    """(well Y or None if no well, or False if it cannot be told) at an attempt.
    libcubiomes ssa_desert_well_exists (the search's own test) when there."""
    seed, mc = int(seed), int(mc)
    try:
        import ctypes as ct
        from cubiomes_wrap import get_lib
        raw = get_lib()._lib
        f = getattr(raw, "ssa_desert_well_exists", None)
    except Exception:
        f = None
    if f is not None:
        register_terrain(raw)
        f.argtypes = [ct.c_int, ct.c_uint64, ct.c_int, ct.c_int, ct.POINTER(ct.c_int)]
        f.restype = ct.c_int
        y = ct.c_int(0)
        if not f(mc, ct.c_uint64(seed & ((1 << 64) - 1)), int(x), int(z), ct.byref(y)):
            return None
        return y.value
    return _exists_py(seed, mc, x, z)


def _exists_py(seed, mc, x, z):
    """The same in Python (a library without ssa_desert_well_exists)."""
    import struct_info
    lib = struct_info._lib(seed, mc, None, 0)
    if mc <= 21:                                  # the chunk's biome first (cheap, exact)
        if not _biome_ok(seed, mc, x, z, 0):
            return None
    elif not any(int(lib.get_biome_at(x + a, z + b, scale=4, y=16)) == DESERT
                 for a in (-8, 0, 8) for b in (-8, 0, 8)):
        return None                               # no desert around: no terrain needed
    get = _getter(seed, mc)
    top = 255 if mc <= 21 else 319
    lo = 0 if mc <= 21 else -64
    y = top
    while y > lo + 2:
        v = get(x, y, z)
        if v is None:
            return False
        if v != "air":
            break
        y -= 1
    if not _biome_ok(seed, mc, x, z, y + 1):
        return None
    if get(x, y, z) != "sand":
        return None
    for i in range(-2, 3):
        for j in range(-2, 3):
            a, b = get(x + i, y - 1, z + j) == "air", get(x + i, y - 2, z + j) == "air"
            if (a or b) if mc >= 35 else (a and b):      # 26.3: no air at all under it
                return None
    return y


def wells(seed, mc, x1, z1, x2, z2):
    """[(x, y, z)] of the wells in the box."""
    out = []
    for x, z in attempts(seed, mc, x1, z1, x2, z2):
        y = exists(seed, mc, x, z)
        if y is not None and y is not False:
            out.append((x, y, z))
    return out


def filter_points(seed, mc, pts):
    """The map / search points (well attempts that passed cubiomes or not) that
    really are wells (kept when it cannot be told)."""
    out = []
    for p in pts:
        y = exists(int(seed), int(mc), int(p[0]), int(p[1]))
        if y is not None:
            out.append(p)
    return out
