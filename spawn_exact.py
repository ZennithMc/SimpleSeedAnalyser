"""
spawn_exact.py — (12/11) the world spawn of 1.18+ exactly as the game sets it
(MinecraftServer.setInitialSpawn, checked on real 1.21.11 servers).

1. Climate.SpawnFinder (26.3: NoiseSpawnFinder, the same search): the fittest point
   of two radial searches (radius 512 → 2048 step 512, then 32 → 512 step 32 around
   the first result). The game counts `angle` and `radius` in FLOATS
   (angle += increment / radius); cubiomes counted in doubles, which tries other
   points and often lands in another chunk.
2. The chunk of that point, then a spiral of 11 × 11 chunks around it; in each chunk
   the columns x by x, z by z (PlayerSpawnFinder.getSpawnPosInChunk): the first column
   whose top block is solid (not water, not lava) wins, the spawn is just above it.
   A swamp / mangrove swamp floor at y 62 is water where the surface_swamp noise is
   ≥ 0 (a surface rule). The ruined portals near it add their blocks and the
   netherrack they spread (it also covers water).
   Water frozen by freeze_top_layer (a cold biome above it, or a frozen ocean outside
   its warm patches) is ice, so solid. cubiomes looked at one column in 16 of an
   approximate height (> 63).
The terrain is SSA's (terrain.chunk_underground: noise, aquifers, carvers). Not
modelled: the trees / rocks / pumpkins on the column (the spawn is then on top of
them: same X, Z, a higher Y), and a tree hanging over water (rare).
"""
from __future__ import annotations

import ctypes
import math

MC_1_18 = 22
_F = None
_CACHE = {}
_DOUBT = {}
# trees standing in water: the column is then solid (leaves / roots), not water
TREES_IN_WATER = {"swamp", "mangrove_swamp"}
# biomes whose water freezes at the sea level (temperature < 0.15)
COLD = {"snowy_plains", "snowy_tundra", "ice_spikes", "snowy_taiga", "snowy_beach",
        "frozen_river", "grove", "snowy_slopes", "jagged_peaks", "frozen_peaks"}


def _f32(v):
    import struct
    return struct.unpack("f", struct.pack("f", v))[0]


def _gen(seed, mc):
    import struct_info
    g = struct_info._lib(int(seed), int(mc), None, 0)
    L = g._lib
    if not getattr(L, "_ssa_fit", False):
        L.calcFitness.restype = ctypes.c_uint64
        L.calcFitness.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        L.biome2str.restype = ctypes.c_char_p
        L.biome2str.argtypes = [ctypes.c_int, ctypes.c_int]
        L._ssa_fit = True
    return g


def find_spawn_position(seed, mc):
    """Climate.SpawnFinder: the block (x, z) the spawn search starts from"""
    g = _gen(seed, mc)
    L, G = g._lib, g._gen
    with g._lock:
        def fit(x, z):
            return L.calcFitness(G, x, z)
        best, bf = (0, 0), fit(0, 0)
        for maxr, inc in ((2048.0, 512.0), (512.0, 32.0)):
            ox, oz = best
            maxr, inc = _f32(maxr), _f32(inc)
            angle, radius = 0.0, inc
            while radius <= maxr:
                x = ox + int(math.sin(angle) * radius)
                z = oz + int(math.cos(angle) * radius)
                f = fit(x, z)
                if f < bf:
                    bf, best = f, (x, z)
                angle = _f32(angle + _f32(inc / radius))       # Java: float + float / float
                if angle > math.pi * 2:
                    angle = 0.0
                    radius = _f32(radius + inc)
    return best


def _names(g, mc):
    d = g.__dict__.setdefault("_bnames", {})
    if mc not in d:
        out = {}
        for b in range(256):
            s = g._lib.biome2str(int(mc), b)
            if s:
                out[b] = s.decode()
        d[mc] = out
    return d[mc]


def _portal_overlay(seed, mc, sx, sz):
    """(12/11) the blocks the ruined portals near the spawn search put on the ground —
    their template and the netherrack they spread, which also covers water (seen on a
    1.21.1 server: netherrack at y 61-62 over a pond made the spawn's column solid).
    {(x, z): {y: "full" | "part" | "fluid"}}"""
    out = {}
    try:
        import copy
        import loot_finder as lf
        import ruined_portal_loot as rpl
        pts = lf.positions(seed, dict(key="ruined_portal", mc=mc, cx=sx, cz=sz, radius=260))
    except Exception:                                   # noqa: BLE001
        return out
    for (px, pz) in pts:
        try:
            g = rpl.layout(seed, mc, px >> 4, pz >> 4)
            if g is None or rpl.ghost_116(mc, g, px >> 4, pz >> 4):
                continue
            _ls, r = rpl.chest_rng(seed, mc, g, px >> 4, pz >> 4)
            W, gone = rpl.placed(seed, mc, g, copy.deepcopy(r))
        except Exception:                               # noqa: BLE001
            continue
        for k, c in W.items():
            if len(k) != 3 or c in ("a", "A"):
                continue
            x, y, z = k
            out.setdefault((x, z), {})[y] = ("fluid" if c == "l" else "part" if c == "h" else "full")
        for (x, y, z) in gone:
            out.setdefault((x, z), {})[y] = "full"          # netherrack / magma
    return out


def _column(col, over):
    """(y of the spawn | None) of a column: col = its codes (0 solid, 1 air, 2 water,
    3 lava; index y + 64), over = {y: kind} of structure blocks on it"""
    kinds = {0: "full", 1: "air", 2: "fluid", 3: "fluid"}
    cells = {}
    for y, k in over.items():
        cells[y] = k
    import numpy as np
    nz = np.nonzero(col != 1)[0]
    top = int(nz[-1]) - 64 if len(nz) else -65
    if cells:
        top = max(top, max(y for y, k in cells.items() if k != "air"))
    if top < -64:
        return None, None

    def at(y):
        if y in cells:
            return cells[y]
        return kinds[int(col[y + 64])] if -64 <= y < 320 else "air"
    if at(top) == "fluid":
        return None, top
    for y in range(top, -65, -1):
        k = at(y)
        if k == "fluid":
            return None, top
        if k == "full":
            return y + 1, top
    return None, top


def spawn(seed, mc):
    """(x, y, z) of the world spawn (1.18+), or None (older version / no terrain)"""
    seed, mc = int(seed), int(mc)
    seed = ((seed + (1 << 63)) % (1 << 64)) - (1 << 63)          # signed 64 bits
    if mc < MC_1_18:
        return None
    k = (seed, mc)
    if k in _CACHE:
        return _CACHE[k]
    import numpy as np
    import terrain
    sx, sz = find_spawn_position(seed, mc)
    cx0, cz0 = sx >> 4, sz >> 4
    g = _gen(seed, mc)
    names = _names(g, mc)
    ice = []

    def frozen(x, y, z):
        with g._lock:
            b = int(g._lib.getBiomeAt(g._gen, 1, x, y, z))
        n = names.get(b, "")
        if n in COLD:
            return True
        if n == "frozen_ocean":
            if not ice:
                import surface_ice
                ice.append(surface_ice.Ice(seed, mc))
            return ice[0].freezes(x, z)
        return False
    swamp = []

    def swamp_water(x, z):
        """SurfaceRuleData: a swamp / mangrove swamp floor at y 62 is water where the
        surface_swamp noise is ≥ 0"""
        if not swamp:
            import surface_ice as si
            root = si.Xoro.from_seed(seed).fork_positional()
            swamp.append(si.Normal(root.from_hash("minecraft:surface_swamp"), -2, [1.0]))
        return swamp[0].value(x, 0.0, z) >= 0.0
    over = _portal_overlay(seed, mc, sx, sz)
    out = None
    doubt = []                         # skipped water where a tree can stand in it
    j = k_ = 0
    dj, dk = 0, -1
    for _i in range(121):
        if -5 <= j <= 5 and -5 <= k_ <= 5:
            cx, cz = cx0 + j, cz0 + k_
            raw = terrain.chunk_underground(seed, mc, cx, cz)
            if raw is None:
                return None
            a = np.frombuffer(raw, dtype=np.uint8).reshape(384, 16, 16)    # [y + 64, z, x]
            filled = a != 1
            rev = filled[::-1]
            top = np.where(rev.any(0), 383 - rev.argmax(0), -1)              # [z, x]
            for lx in range(16):
                for lz in range(16):
                    x, z = cx * 16 + lx, cz * 16 + lz
                    ov = over.get((x, z))
                    if ov:                          # a ruined portal's blocks on it
                        col = a[:, lz, lx].copy()
                        ys, _tp = _column(col, ov)
                        if ys is not None:
                            out = (x, ys, z)
                            break
                        continue
                    t = int(top[lz, lx])
                    if t < 0:
                        continue
                    v = int(a[t, lz, lx])
                    y = t - 64
                    if v == 0 and y == 62:
                        with g._lock:
                            b = int(g._lib.getBiomeAt(g._gen, 1, x, 62, z))
                        if names.get(b, "") in ("swamp", "mangrove_swamp") and swamp_water(x, z):
                            v = 2
                    if v == 0 or (v == 2 and frozen(x, y + 1, z)):
                        out = (x, y + 1, z)
                        break
                    if v == 2 and not doubt:
                        d = _tree_near(g, names, a, top, lx, lz, x, y, z)
                        if d:
                            doubt.append(d)
                if out:
                    break
            if out:
                break
        if j == k_ or (j < 0 and j == -k_) or (j > 0 and j == 1 - k_):
            dj, dk = -dk, dj
        j += dj
        k_ += dk
    if out is None:                    # nothing solid: the centre of the chunk (its surface)
        out = (cx0 * 16 + 8, 64, cz0 * 16 + 8)       # getSpawnHeight: sea level + 1
    if len(_CACHE) > 512:
        _CACHE.clear()
    _CACHE[k] = out
    _DOUBT[k] = doubt[0] if doubt else None
    return out


# biomes without trees (their land cannot hang leaves over the water)
NO_TREES = {"ocean", "deep_ocean", "cold_ocean", "deep_cold_ocean", "lukewarm_ocean",
            "deep_lukewarm_ocean", "warm_ocean", "frozen_ocean", "deep_frozen_ocean", "river",
            "frozen_river", "beach", "snowy_beach", "stony_shore", "desert", "badlands",
            "eroded_badlands", "ice_spikes", "snowy_plains", "stony_peaks", "jagged_peaks",
            "frozen_peaks", "snowy_slopes", "mushroom_fields", "dripstone_caves", "lush_caves",
            "deep_dark"}


def _tree_near(g, names, a, top, lx, lz, x, y, z):
    """A skipped water column where a tree can reach: one standing in the water (swamp,
    mangrove swamp) or on land ≤ 4 blocks away (its leaves over the water) → (x, z,
    biome) — the game's spawn can then be on those leaves, before SSA's"""
    with g._lock:
        b = int(g._lib.getBiomeAt(g._gen, 1, x, y + 1, z))
    n = names.get(b, "")
    if n in TREES_IN_WATER:
        return (x, z, n)
    for dx in range(-4, 5):
        for dz in range(-4, 5):
            ax, az = lx + dx, lz + dz
            if not (0 <= ax < 16 and 0 <= az < 16):
                continue
            t = int(top[az, ax])
            if t < 0 or a[t, az, ax] != 0 or t - 64 < y:
                continue
            with g._lock:
                b2 = int(g._lib.getBiomeAt(g._gen, 1, x + dx, t - 63, z + dz))
            n2 = names.get(b2, "")
            if n2 and n2 not in NO_TREES:
                return (x, z, n2)
    return None


def doubt(seed, mc):
    """(x, z, biome) of the first water column skipped before the spawn where a tree
    can reach (standing in the water, or on land ≤ 4 blocks away): the game's spawn can
    be there, on its leaves. None: SSA's X Z are the game's."""
    k = ((((int(seed) + (1 << 63)) % (1 << 64)) - (1 << 63)), int(mc))
    if k not in _DOUBT:
        spawn(seed, mc)
    return _DOUBT.get(k)
