"""
struct_terrain.py — terrain conditions of the structures (24/10).

Desert pyramids and jungle temples (SinglePieceStructure, 1.18+) only generate when
the LOWEST of the four corner heights of their footprint (getFirstOccupiedHeight,
WORLD_SURFACE_WG, at (minX, minZ), (minX, minZ+d), (minX+w, minZ), (minX+w, minZ+d)
of the start chunk, unrotated width/depth) is at least the sea level (63). cubiomes
does not test it, so the map showed phantom ones. Checked against /locate on the
26.2 server: 20 / 20 (5 of them do not exist in game).
Heights: terrain.first_occupied (cave118.c, per-block exact noise + aquifer).
"""
from __future__ import annotations

from functools import lru_cache

SINGLE_PIECE = {"desert_pyramid": (21, 21), "jungle_temple": (12, 15)}
SEA_LEVEL = 63


@lru_cache(maxsize=65536)
def exists(key, seed, mc, cx, cz):
    """True / False, or None when it cannot be told (version, terrain lib)."""
    wd = SINGLE_PIECE.get(key)
    if wd is None or not (22 <= int(mc) <= 35):
        return None
    try:
        import terrain
    except Exception:
        return None
    w, d = wd
    x0, z0 = int(cx) << 4, int(cz) << 4
    low = None
    for x, z in ((x0, z0), (x0, z0 + d), (x0 + w, z0), (x0 + w, z0 + d)):
        h = terrain.first_occupied(seed, mc, x, z)
        if h is None:
            return None
        low = h if low is None else min(low, h)
        if low < SEA_LEVEL:
            return False
    return True


# (29/10) trail ruins (JigsawStructure, start_height -15 under WORLD_SURFACE_WG): the
# game tests the biome at the start piece's centre, 15 blocks under the surface —
# lush caves there = no ruins (server 1.21.4: 608 32). Pool trail_ruins/tower: 5
# towers, weight 1 each, 5 × 5 (tower_1, 2) or 7 × 7 (tower_3, 4, 5) — same in every
# jar 1.20 → 26.3.
_TRAIL_SIZES = (5, 5, 7, 7, 7)
_TRAIL_BIOMES = {"taiga", "snowy_taiga", "old_growth_pine_taiga", "old_growth_spruce_taiga",
                 "old_growth_birch_forest", "jungle"}


@lru_cache(maxsize=65536)
def trail_ruins_exists(seed, mc, cx, cz):
    if not (25 <= int(mc) <= 35):
        return None
    try:
        import terrain
        import stronghold_eyes as se
        import struct_info
        from colormaps import BIOME_NAME_TO_ID
    except Exception:
        return None
    r = se.JavaRandom(0)
    r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
    rot = r.nextInt_bound(4)
    s = _TRAIL_SIZES[r.nextInt_bound(5)] - 1
    x0, z0 = int(cx) << 4, int(cz) << 4
    a, b = ((s, s), (-s, s), (-s, -s), (s, -s))[rot]      # rotated far corner
    xa, xb = sorted((x0, x0 + a))
    za, zb = sorted((z0, z0 + b))
    jd = lambda v: v // 2 if v >= 0 else -((-v) // 2)       # Java int division
    mx, mz = jd(xa + xb), jd(za + zb)
    h = terrain.first_occupied(seed, mc, mx, mz)
    if h is None:
        return None
    y = h + 1 - 15
    try:
        lib = struct_info._lib(int(seed), int(mc), None, 0)
        bid = int(lib.get_biome_at(mx, mz, scale=4, y=y))
    except Exception:
        return None
    return bid in {BIOME_NAME_TO_ID.get(n, -1) for n in _TRAIL_BIOMES}


def filter_points(key, seed, mc, pts):
    if key == "ruined_portal" and int(mc) <= 20:      # (30/10) 1.16: portals never placed
        import ruined_portal_loot as rpl
        out = []
        for p in pts:
            try:
                ok = rpl.exists_116(int(seed), int(mc), p[0] >> 4, p[1] >> 4)
            except Exception:
                ok = True
            if ok:
                out.append(p)
        return out
    if key == "trail_ruins":
        return [p for p in pts if trail_ruins_exists(int(seed), int(mc), p[0] >> 4, p[1] >> 4)
                is not False]
    if key not in SINGLE_PIECE:
        return pts
    return [p for p in pts if exists(key, int(seed), int(mc), p[0] >> 4, p[1] >> 4) is not False]
