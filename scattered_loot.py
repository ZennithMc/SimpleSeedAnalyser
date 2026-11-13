"""
scattered_loot.py — chests of the single-piece structures (24/10): desert pyramid,
jungle temple (+ its two arrow dispensers) and igloo, every version 1.16 → 26.3.

The loot seed of a container = the nextLong its chunk's structure RNG gives when the
game places it (StructurePiece.createChest / createDispenser, or a template's
LootTableSeed), so each chunk replays, in order, every draw the piece makes before.

  desert pyramid  (ScatteredFeaturePiece 21×15×21): 1.18+: -random.nextInt(3) first
                  (sinking offset, drawn in EVERY chunk); then only the 4 chests
                  (N E S W around the treasure room, each only in its own chunk).
  jungle temple   (12×10×15): every MossStoneSelector box draws one nextFloat per
                  cell, inside the chunk or not (skipAir false) — 43 boxes, then
                  dispenser 1, dispenser 2, main chest, 3 boxes, hidden chest.
  igloo           (templates igloo/top, middle, bottom): the basement (bottom.nbt)
                  has the chest; StructureTemplate.placeInWorld draws one nextLong
                  per container placed in the chunk (LootTableSeed), then the "chest"
                  data marker draws the final one (setLootTable).
Read in the 26.3 jar and checked in the 1.16 (yarn), 1.17.1, 1.18 and 1.19.2 jars.
"""
from __future__ import annotations

import stronghold_eyes as se

SURFACE_STRUCTURES = 4

# SURFACE_STRUCTURES structures, in the order the game gives their index
_SURF_OLD = ["pillager_outpost", "mansion", "jungle_pyramid", "desert_pyramid", "igloo",
             "ruined_portal", "shipwreck", "swamp_hut", "monument", "ocean_ruin",
             "end_city", "village", "bastion_remnant"]                 # 1.16 – 1.18.2
_SURF_119 = ["pillager_outpost", "mansion", "jungle_pyramid", "desert_pyramid", "igloo",
             "shipwreck", "shipwreck_beached", "swamp_hut", "stronghold", "monument",
             "ocean_ruin_cold", "ocean_ruin_warm", "end_city", "bastion_remnant",
             "village_plains", "village_desert", "village_savanna", "village_snowy",
             "village_taiga",
             # (30/10) registered after the villages: ruined_portal, then the
             # BuiltinStructures order (desert, jungle, swamp, mountain, ocean, nether)
             "ruined_portal", "ruined_portal_desert", "ruined_portal_jungle",
             "ruined_portal_swamp", "ruined_portal_mountain", "ruined_portal_ocean",
             "ruined_portal_nether"]                                   # 1.19.0-2
_SURF_ALPHA = ["bastion_remnant", "desert_pyramid", "end_city", "igloo", "jungle_pyramid",
               "mansion", "monument", "ocean_ruin_cold", "ocean_ruin_warm",
               "pillager_outpost", "ruined_portal", "ruined_portal_desert",
               "ruined_portal_jungle", "ruined_portal_mountain", "ruined_portal_nether",
               "ruined_portal_ocean", "ruined_portal_swamp", "shipwreck",
               "shipwreck_beached", "stronghold", "swamp_hut", "village_desert",
               "village_plains", "village_savanna", "village_snowy", "village_taiga"]
_CAMPS = 18            # 26.3: abandoned_camp_<biome> × 18, all sorted first
_CAMP_NAMES = sorted("abandoned_camp_" + b for b in (
    "bamboo_jungle", "birch_forest", "cherry_grove", "dappled_forest", "flower_forest", "forest",
    "meadow", "old_growth_birch_forest", "old_growth_pine_taiga", "old_growth_spruce_taiga",
    "pale_garden", "savanna", "snowy_taiga", "sparse_jungle", "swamp", "taiga", "windswept_forest",
    "wooded_badlands"))


# (25/10) 1.18.2: the CONFIGURED structures in registration order (checked on the
# 1.18.2 server: villages 13-17, outposts 0) — no stronghold in the step there.
_SURF_1182 = [n for n in _SURF_119 if n != "stronghold"]


def surface_index(name: str, mc: int) -> int:
    if mc == 22:
        return _SURF_1182.index(name)
    if mc <= 21:
        return _SURF_OLD.index(name)
    if mc == 23:
        return _SURF_119.index(name)
    if name.startswith("abandoned_camp_"):            # (30/10) 26.3: sorted first
        return _CAMP_NAMES.index(name)
    return _SURF_ALPHA.index(name) + (_CAMPS if mc >= 35 else 0)


def _rng(seed, mc, cx, cz, name):
    import structure_loot as sl
    return sl.structure_rng(seed, mc, cx, cz, surface_index(name, mc), SURFACE_STRUCTURES)


def _next_int(r, n):
    """BitRandomSource.nextInt(bound) on next(31) — same for the LCG and for
    WorldgenRandom over Xoroshiro (next(bits) = nextLong() >>> 64-bits)."""
    if hasattr(r, "nextInt_bound"):
        return r.nextInt_bound(n)
    m = n - 1
    if (n & m) == 0:
        return (n * r.next(31)) >> 31
    while True:
        u = r.next(31)
        v = u % n
        if u - v + m < (1 << 31):
            return v


def _next_float(r):
    return r.next_float()


def _next_long(r):
    return r.nextLong() if hasattr(r, "nextLong") else r.next_long()


def _direction(seed, cx, cz):
    """getRandomHorizontalDirection of the structure start (LCG setLargeFeatureSeed):
    0 N, 1 E, 2 S, 3 W."""
    r = se.JavaRandom(0)
    r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
    return r.nextInt_bound(4)


class Scattered:
    """ScatteredFeaturePiece geometry (StructurePiece.getWorldX / Z, makeBoundingBox)."""

    def __init__(self, x0, y0, z0, w, h, d, ori):
        self.ori = ori
        if ori in (0, 2):
            self.box = (x0, y0, z0, x0 + w - 1, y0 + h - 1, z0 + d - 1)
        else:
            self.box = (x0, y0, z0, x0 + d - 1, y0 + h - 1, z0 + w - 1)

    def wx(self, x, z):
        b = self.box
        if self.ori in (0, 2):
            return b[0] + x
        return b[3] - z if self.ori == 3 else b[0] + z

    def wz(self, x, z):
        b = self.box
        if self.ori == 0:
            return b[5] - z
        if self.ori == 2:
            return b[2] + z
        return b[2] + x

    def wy(self, y):
        return self.box[1] + y


_T116_MAP = None
_CODES = {}


def t116_raw(seed, mc, cx, cz):
    """(31/10) the exact 1.16 / 1.17 terrain of a chunk (libcubiomes ssa_t116_blocks,
    T116 codes, [(y * 16 + z) * 16 + x], y 0..255), or None."""
    import ctypes as ct
    try:
        from cubiomes_wrap import get_lib
        f = getattr(get_lib()._lib, "ssa_t116_blocks", None)
    except Exception:
        f = None
    if f is None:
        return None
    f.argtypes = [ct.c_int, ct.c_uint64, ct.c_int32, ct.c_int32, ct.POINTER(ct.c_uint8)]
    f.restype = ct.c_int
    buf = (ct.c_uint8 * 65536)()
    if not f(int(mc), ct.c_uint64(int(seed) & ((1 << 64) - 1)), int(cx), int(cz), buf):
        return None
    return bytes(buf)


def chunk_codes(seed, mc, cx, cz):
    """(31/10) a chunk after the carvers as terrain.chunk_underground gives it
    ([((y + 64) * 16 + z) * 16 + x], 0 solid, 1 air, 2 water, 3 lava) — 1.18+ from the
    terrain library, 1.16 / 1.17 from the exact 1.16 engine (y < 0: solid, y > 255: air).
    None if unavailable."""
    k = (int(seed), int(mc), cx, cz)
    if k in _CODES:
        return _CODES[k]
    if int(mc) >= 22:
        import terrain
        v = terrain.chunk_underground(seed, mc, cx, cz)
    else:
        global _T116_MAP
        raw = t116_raw(seed, mc, cx, cz)
        if raw is None:
            v = None
        else:
            if _T116_MAP is None:
                m = bytearray([0]) * 256          # solid
                for a in (0, 25):                 # air, cave air
                    m[a] = 1
                m[2], m[3] = 2, 3                 # water, lava
                for a in (35, 36, 37):            # rail, cobweb, torch: not solid
                    m[a] = 1
                _T116_MAP = bytes(m)
            v = bytes(64 * 256) + raw.translate(_T116_MAP) + bytes([1]) * (64 * 256)
    if len(_CODES) > 256:
        _CODES.clear()
    _CODES[k] = v
    return v


def _ground(seed, mc, box, how):
    """Heightmap Y over the piece's footprint, or None (31/10: 1.16 / 1.17 too, from
    the exact 1.16 terrain: the first free block above the highest non-air one)."""
    if mc < 22:
        try:
            x0, z0, x1, z1 = box[0], box[2], box[3], box[5]
            g = []
            for xx in range(x0, x1 + 1):
                for zz in range(z0, z1 + 1):
                    c = chunk_codes(seed, mc, xx >> 4, zz >> 4)
                    if c is None:
                        return None
                    i = (zz & 15) * 16 + (xx & 15)
                    y = 255
                    while y >= 0 and c[(y + 64) * 256 + i] == 1:
                        y -= 1
                    g.append(y + 1)
            return min(g) if how == "min" else sum(g) // len(g)     # Java int division
        except Exception:
            return None
    try:
        import terrain
        if not terrain.available():
            return None
        x0, z0, x1, z1 = box[0], box[2], box[3], box[5]
        # (24/10) the per-block exact noise column (cave118.c): the first free block
        # above the highest solid / fluid one (the old exact_y was off by 1-4 blocks
        # in places: it interpolated the squeezed density)
        step = 1 if (x1 - x0) * (z1 - z0) <= 64 else 2
        g = []
        for xx in range(x0, x1 + 1, step):
            for zz in range(z0, z1 + 1, step):
                h = terrain.first_occupied(seed, mc, xx, zz)
                if h is None:
                    return None
                g.append(h + 1)
        return min(g) if how == "min" else int(round(sum(g) / len(g)))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────── desert pyramid ──
def _absent(key, seed, mc, cx, cz):
    try:
        import struct_terrain
        if struct_terrain.exists(key, int(seed), int(mc), cx, cz) is False:
            return [dict(name="Not generated", pos=(cx * 16, None, cz * 16), table="",
                         seed=None, slots=None, approx=[],
                         note="The ground under this spot is below the sea level: the game "
                              "does not build it here (1.18+).")]
    except Exception:
        pass
    return None


def desert_pyramid(seed, mc, x, z, ver, fast=False):
    import loot_engine as le
    cx, cz = x >> 4, z >> 4
    a = _absent("desert_pyramid", seed, mc, cx, cz)
    if a:
        return a
    ori = _direction(seed, cx, cz)
    p = Scattered(cx << 4, 64, cz << 4, 21, 15, 21, ori)
    note = None
    if mc >= 22 and not fast:                    # sinks to the lowest ground (1.18+)
        g = _ground(seed, mc, p.box, "min")
        if g is not None:
            p = Scattered(cx << 4, g, cz << 4, 21, 15, 21, ori)
        note = ("Y approximate (the pyramid sinks 0-2 blocks into the lowest ground "
                "under it)")
    out = []
    names = {0: "North", 1: "East", 2: "South", 3: "West"}
    steps = {0: (0, -1), 1: (1, 0), 2: (0, 1), 3: (-1, 0)}       # Direction N E S W
    pos = []
    for d in range(4):
        sx, sz = steps[d]
        lx, lz = 10 + 2 * sx, 10 + 2 * sz
        pos.append((d, p.wx(lx, lz), p.wy(-11), p.wz(lx, lz)))
    for d, wx, wy, wz in pos:
        ccx, ccz = wx >> 4, wz >> 4
        r = _rng(seed, mc, ccx, ccz, "desert_pyramid")
        if mc >= 22:
            _next_int(r, 3)
        ls = None
        for d2, x2, y2, z2 in pos:               # the chests of this chunk, N E S W
            if (x2 >> 4, z2 >> 4) != (ccx, ccz):
                continue
            v = _next_long(r)
            if d2 == d:
                ls = v
                break
        slots, approx = le.fill_chest("desert_pyramid", ls, ver)
        out.append(dict(name=f"Chest ({names[d]})", pos=(wx, wy, wz),
                        table="chests/desert_pyramid", seed=ls, slots=slots,
                        approx=approx, note=note))
    return out


# ──────────────────────────────────────────────────────────────── jungle temple ──
# the MossStoneSelector boxes in postProcess order, then the four containers
_J_BOXES_A = [
    (0, -4, 0, 11, 0, 14), (2, 1, 2, 9, 2, 2), (2, 1, 12, 9, 2, 12), (2, 1, 3, 2, 2, 11),
    (9, 1, 3, 9, 2, 11), (1, 3, 1, 10, 6, 1), (1, 3, 13, 10, 6, 13), (1, 3, 2, 1, 6, 12),
    (10, 3, 2, 10, 6, 12), (2, 3, 2, 9, 3, 12), (2, 6, 2, 9, 6, 12), (3, 7, 3, 8, 7, 11),
    (4, 8, 4, 7, 8, 10)]


def _jungle_events():
    ev = [("box", b) for b in _J_BOXES_A]
    for z2 in (0, 14):
        for xx in (2, 4, 7, 9):
            ev.append(("box", (xx, 4, z2, xx, 5, z2)))
    ev.append(("box", (5, 6, 0, 6, 6, 0)))
    for xx in (0, 11):
        for z3 in range(2, 13, 2):
            ev.append(("box", (xx, 4, z3, xx, 5, z3)))
        ev.append(("box", (xx, 6, 5, xx, 6, 5)))
        ev.append(("box", (xx, 6, 9, xx, 6, 9)))
    for b in [(2, 7, 2, 2, 9, 2), (9, 7, 2, 9, 9, 2), (2, 7, 12, 2, 9, 12), (9, 7, 12, 9, 9, 12),
              (4, 9, 4, 4, 9, 4), (7, 9, 4, 7, 9, 4), (4, 9, 10, 4, 9, 10), (7, 9, 10, 7, 9, 10),
              (5, 9, 7, 6, 9, 7), (4, 1, 9, 4, 1, 9), (7, 1, 9, 7, 1, 9), (4, 1, 10, 7, 2, 10),
              (5, 4, 5, 6, 4, 5)]:
        ev.append(("box", b))
    for zz in range(1, 14, 2):
        ev.append(("box", (1, -3, zz, 1, -2, zz)))
    for zz in range(2, 13, 2):
        ev.append(("box", (1, -1, zz, 3, -1, zz)))
    for b in [(2, -2, 1, 5, -2, 1), (7, -2, 1, 9, -2, 1), (6, -3, 1, 6, -3, 1), (6, -1, 1, 6, -1, 1)]:
        ev.append(("box", b))
    ev.append(("disp1", (3, -2, 1)))
    ev.append(("disp2", (9, -2, 3)))
    ev.append(("chest_main", (8, -3, 3)))
    ev.append(("box", (9, -1, 1, 9, -1, 5)))
    ev.append(("box", (8, -3, 8, 8, -3, 10)))
    ev.append(("box", (10, -3, 8, 10, -3, 10)))
    ev.append(("chest_hidden", (9, -3, 10)))
    return ev


_J_EVENTS = _jungle_events()
_J_NAMES = {"disp1": ("Arrow dispenser (entrance trap)", "jungle_temple_dispenser", 9),
            "disp2": ("Arrow dispenser (tripwire)", "jungle_temple_dispenser", 9),
            "chest_main": ("Chest (by the stairs)", "jungle_temple", 27),
            "chest_hidden": ("Hidden chest (lever puzzle)", "jungle_temple", 27)}


_LCG_M48 = (1 << 48) - 1
_LCG_JUMP = {}


def _skip_floats(r, n):
    """n nextFloat() at once (one step each): the LCG by its closed-form jump, the
    Xoroshiro in C (loot_native) — (07/11) the jungle temple's moss boxes"""
    if n <= 0:
        return
    if hasattr(r, "lo") and hasattr(r, "hi"):
        try:
            import loot_native
            loot_native.xoro_skip(r, n)
            return
        except Exception:                                # noqa: BLE001
            for _ in range(n):
                r.next_float()
            return
    if hasattr(r, "seed") and type(r).__name__ == "JavaRandom":
        ab = _LCG_JUMP.get(n)
        if ab is None:                                   # s → A s + C after n steps
            A, C, a, c, k = 1, 0, 0x5DEECE66D, 0xB, n
            while k:
                if k & 1:
                    A, C = (A * a) & _LCG_M48, (C * a + c) & _LCG_M48
                a, c = (a * a) & _LCG_M48, (c * a + c) & _LCG_M48
                k >>= 1
            ab = _LCG_JUMP[n] = (A, C)
        r.seed = (ab[0] * r.seed + ab[1]) & _LCG_M48
        return
    for _ in range(n):
        r.next_float()


def jungle_temple(seed, mc, x, z, ver, fast=False):
    import loot_engine as le
    cx, cz = x >> 4, z >> 4
    a = _absent("jungle_temple", seed, mc, cx, cz)
    if a:
        return a
    ori = _direction(seed, cx, cz)
    p = Scattered(cx << 4, 64, cz << 4, 12, 10, 15, ori)
    g = None if fast else _ground(seed, mc, p.box, "mean")     # (only the Y)
    if g is not None:
        p = Scattered(cx << 4, g, cz << 4, 12, 10, 15, ori)
    note = ("Y approximate (the temple sits on the average ground height)" if g is not None
            else "Y unknown here (it depends on the terrain): the /tp keeps your height")
    targets = [(k, b) for k, b in _J_EVENTS if k != "box"]
    out = []
    for key, (lx, ly, lz) in targets:
        wx, wy, wz = p.wx(lx, lz), p.wy(ly), p.wz(lx, lz)
        ccx, ccz = wx >> 4, wz >> 4
        r = _rng(seed, mc, ccx, ccz, "jungle_pyramid")
        ls = None
        for k, b in _J_EVENTS:
            if k == "box":
                n = (b[3] - b[0] + 1) * (b[4] - b[1] + 1) * (b[5] - b[2] + 1)
                _skip_floats(r, n)
                continue
            ex, ez = p.wx(b[0], b[2]), p.wz(b[0], b[2])
            if (ex >> 4, ez >> 4) != (ccx, ccz):
                continue
            v = _next_long(r)
            if k == key:
                ls = v
                break
        name, table, size = _J_NAMES[key]
        slots, approx = le.fill_chest(table, ls, ver, size=size)
        out.append(dict(name=name, pos=(wx, wy if g is not None else None, wz),
                        table=f"chests/{table}", seed=ls,
                        slots=slots, approx=approx, note=note, size=size))
    return out


# ──────────────────────────────────────────────────────────────────────── igloo ──
def _rot(x, z, rot, px, pz):
    """StructureTemplate.transform (no mirror) around the pivot (px, pz)."""
    if rot == 1:                                  # CLOCKWISE_90
        return px + pz - z, pz - px + x
    if rot == 2:                                  # CLOCKWISE_180
        return px + px - x, pz + pz - z
    if rot == 3:                                  # COUNTERCLOCKWISE_90
        return px - pz + z, px + pz - x
    return x, z


def igloo_layout(seed, cx, cz):
    """(rotation 0-3, basement depth i or None) from the structure start random."""
    r = se.JavaRandom(0)
    r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
    rot = r.nextInt_bound(4)                      # Rotation.getRandom
    if r.nextDouble() < 0.5:
        return rot, r.nextInt_bound(8) + 4
    return rot, None


def igloo(seed, mc, x, z, ver, fast=False):
    import loot_engine as le
    cx, cz = x >> 4, z >> 4
    rot, depth = igloo_layout(seed, cx, cz)
    if depth is None:
        return [dict(name="No basement", pos=(cx * 16, None, cz * 16), table="chests/igloo_chest",
                     seed=None, slots=None, approx=[],
                     note="This igloo has no basement, so no chest (50 % of igloos).")]
    # laboratory (igloo/bottom, 7×6×9): template origin = start + (0,-3,-2), pivot (3,6,7)
    ox, oz = (cx << 4) + 0, (cz << 4) - 2
    lx, lz = _rot(1, 6, rot, 3, 7)                # the chest, local (1, 1, 6)
    wx, wz = ox + lx, oz + lz
    c = (wx >> 4, wz >> 4)
    r = _rng(seed, mc, c[0], c[1], "igloo")
    _next_long(r)                                 # placeInWorld: the chest's LootTableSeed
    ls = _next_long(r)                            # the "chest" data marker: setLootTable
    wy = None
    # entrance column of the igloo: start + (3, 0, 0) rotated around (3, 5)
    ex, ez = _rot(3, 0, rot, 3, 5)
    he = None if fast else _ground(seed, mc, ((cx << 4) + ex, 0, (cz << 4) + ez,
                                              (cx << 4) + ex, 0, (cz << 4) + ez), "min")
    if he is not None:
        wy = he - 3 - 3 * depth
    # (24/10) a data marker only acts inside the chunk being generated (checked on the
    # 26.2 server): the basement spanning another chunk does not re-roll the chest.
    slots, approx = le.fill_chest("igloo_chest", ls, ver)
    return [dict(name="Basement chest", pos=(wx, wy, wz), table="chests/igloo_chest",
                 seed=ls, slots=slots, approx=approx, note=None)]


# ──────────────────────────────────────────────────────────────────── shipwreck ──
# The 20 templates (identical in the 1.16.1, 1.17.1, 1.18, 1.19.2, 1.21.11 and 26.2
# jars): size, containers (StructureTemplate order: y, then x, then z) and the DATA
# markers (same order; the chest is the block under the marker).
_SHIP_T = {
    "rightsideup_backhalf": ((9, 9, 16), ((5, 2, 6), (6, 4, 12)), ("map", "treasure")),
    "rightsideup_fronthalf": ((9, 9, 24), ((4, 2, 8),), ("supply",)),
    "rightsideup_full": ((9, 9, 28), ((4, 2, 8), (5, 2, 18), (6, 4, 24)), ("supply", "map", "treasure")),
    "sideways_backhalf": ((9, 9, 17), ((3, 2, 13), (6, 3, 8)), ("treasure", "map")),
    "sideways_fronthalf": ((9, 9, 24), ((5, 3, 8),), ("supply",)),
    "sideways_full": ((9, 9, 28), ((3, 2, 24), (5, 3, 8), (6, 3, 19)), ("treasure", "supply", "map")),
    "upsidedown_backhalf": ((9, 9, 16), ((2, 2, 12), (3, 5, 5)), ("treasure", "map")),
    "upsidedown_fronthalf": ((9, 9, 22), ((3, 5, 17), (4, 5, 8)), ("map", "supply")),
    "upsidedown_full": ((9, 9, 28), ((2, 2, 24), (3, 5, 17), (4, 5, 8)), ("treasure", "map", "supply")),
    "with_mast": ((9, 21, 28), ((4, 2, 9), (5, 2, 18), (6, 4, 24)), ("supply", "map", "treasure")),
}
for _k in list(_SHIP_T):
    _SHIP_T[_k + "_degraded"] = _SHIP_T[_k]
_SHIP_BEACHED = ["with_mast", "sideways_full", "sideways_fronthalf", "sideways_backhalf",
                 "rightsideup_full", "rightsideup_fronthalf", "rightsideup_backhalf",
                 "with_mast_degraded", "rightsideup_full_degraded",
                 "rightsideup_fronthalf_degraded", "rightsideup_backhalf_degraded"]
_SHIP_OCEAN = ["with_mast", "upsidedown_full", "upsidedown_fronthalf", "upsidedown_backhalf",
               "sideways_full", "sideways_fronthalf", "sideways_backhalf", "rightsideup_full",
               "rightsideup_fronthalf", "rightsideup_backhalf", "with_mast_degraded",
               "upsidedown_full_degraded", "upsidedown_fronthalf_degraded",
               "upsidedown_backhalf_degraded", "sideways_full_degraded",
               "sideways_fronthalf_degraded", "sideways_backhalf_degraded",
               "rightsideup_full_degraded", "rightsideup_fronthalf_degraded",
               "rightsideup_backhalf_degraded"]
_SHIP_PIVOT = (4, 15)
_SHIP_NAMES = {"supply": "Supply chest", "map": "Map chest", "treasure": "Treasure chest"}
MC_26_1 = 33


def shipwreck_beached(seed, mc, cx, cz):
    """Beach / snowy beach biome at the chunk centre → the beached variant."""
    try:
        import struct_info
        lib = struct_info._lib(int(seed), int(mc), None, 0)
        b = lib.get_biome_at((cx << 4) + 8, (cz << 4) + 8, scale=4, y=320)
        return int(b) in (16, 26)                 # beach, snowy_beach
    except Exception:
        return False


def shipwreck_layout(seed, cx, cz, beached):
    """(rotation, template) from the structure start random."""
    r = se.JavaRandom(0)
    r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
    rot = r.nextInt_bound(4)                      # Rotation.getRandom
    arr = _SHIP_BEACHED if beached else _SHIP_OCEAN
    return rot, arr[r.nextInt_bound(len(arr))]


def shipwreck_box(seed, mc, cx, cz, beached=None):
    """World footprint (x0, z0, x1, z1), template, rotation, beached."""
    if beached is None:
        beached = shipwreck_beached(seed, mc, cx, cz)
    rot, name = shipwreck_layout(seed, cx, cz, beached)
    (sx, _sy, sz), _c, _m = _SHIP_T[name]
    ox, oz = cx << 4, cz << 4
    pts = [_rot(a, b, rot, *_SHIP_PIVOT) for a in (0, sx - 1) for b in (0, sz - 1)]
    xs = [ox + a for a, _ in pts]
    zs = [oz + b for _, b in pts]
    return (min(xs), min(zs), max(xs), max(zs)), name, rot, beached


_ICE = {}


def _surface_ice(seed, mc):
    """surface_ice.Ice of the world (1.18+), cached; None if unavailable."""
    k = (int(seed), int(mc))
    if k not in _ICE:
        try:
            import surface_ice
            if len(_ICE) > 4:
                _ICE.clear()
            _ICE[k] = surface_ice.Ice(seed, mc)
        except Exception:
            _ICE[k] = None
    return _ICE[k]


def _heights(seed, mc, x0, z0, sx, sz, kind):
    """Heightmap values (first free block) over the UNROTATED box the game samples:
    kind 0 WORLD_SURFACE_WG (any non-air), 1 OCEAN_FLOOR_WG (solid). Taken after the
    carvers (a canyon under the wreck lowers it: seen on the 26.2 server). None when
    unavailable."""
    if not (19 <= int(mc) <= 35):
        return None
    try:
        cache = {}
        out = []
        ice = _surface_ice(seed, mc) if int(mc) >= 22 else None
        for x in range(x0, x0 + sx):
            for z in range(z0, z0 + sz):
                c = (x >> 4, z >> 4)
                if c not in cache:
                    cache[c] = chunk_codes(seed, mc, c[0], c[1])
                    if cache[c] is None:
                        return None
                blk = cache[c]
                i = (z & 15) * 16 + (x & 15)
                y = 319
                while y >= -64:
                    v = blk[(y + 64) * 256 + i]
                    if (v == 0) if kind else (v != 1):
                        break
                    y -= 1
                h = y + 1
                if ice is not None:                   # (30/10) frozen-ocean ice pillars
                    col = ice.column(x, z, lambda yy, b=blk, i=i: b[(yy + 64) * 256 + i])
                    if col:
                        h = max(h, max(col) + 1)
                out.append(h)
        return out
    except Exception:
        return None


_SHIP_TOPS = None
SEA_LEVEL = 62                                   # the water's top block


def _ship_tops():
    global _SHIP_TOPS
    if _SHIP_TOPS is None:
        import gzip
        import json
        import os
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "shipwreck_tops.json.gz")
        with gzip.open(p, "rt") as f:
            _SHIP_TOPS = {k: {tuple(map(int, c.split(","))): v for c, v in d.items()}
                          for k, d in json.load(f).items()}
    return _SHIP_TOPS


def _ship_bases(seed, mc, cx, cz, name, rot, beached, base):
    """(30/10) {chunk of a chest: {base Y}} over the generation orders. The WG
    heightmaps are frozen after the carvers, unless a chunk was saved and loaded
    again before the wreck (they are not saved): primed again from its blocks — its
    icebergs (its own, or its neighbours' too) and, before 26.1 (every chunk computes
    the height again), the part of the wreck it already holds."""
    import iceberg as ib
    import itertools
    (sx, sy, sz), cont, _m = _SHIP_T[name]
    ox, oz = cx << 4, cz << 4
    hs = _heights(seed, mc, ox, oz, sx, sz, 0 if beached else 1)
    if not hs:
        return None
    cols = [(x, z) for x in range(ox, ox + sx) for z in range(oz, oz + sz)]
    B = sorted({(x >> 4, z >> 4) for x, z in cols})
    berg = {}
    for b in B:
        for a in (-1, 0, 1):
            for c in (-1, 0, 1):
                o = (b[0] + a, b[1] + c)
                if o not in berg:
                    w = ib.chunk_icebergs(seed, mc, *o)
                    t = {}
                    for (X, Y, Z), v in w.items():
                        if v in (ib.PICE, ib.SNOWB):
                            t[(X, Z)] = max(t.get((X, Z), -999), Y + 1)
                    berg[o] = t
    tops = _ship_tops()[name]
    wt = {}
    for (lx, lz), ty in tops.items():
        a, b = _rot(lx, lz, rot, *_SHIP_PIVOT)
        wt[(ox + a, oz + b)] = base + (sy // 2 if beached else 0) + ty + 1
    own = [max(h, berg[(x >> 4, z >> 4)].get((x, z), -999)) for h, (x, z) in zip(hs, cols)]
    allb = []
    for h, (x, z) in zip(hs, cols):
        m = h
        for a in (-1, 0, 1):
            for c in (-1, 0, 1):
                m = max(m, berg[((x >> 4) + a, (z >> 4) + c)].get((x, z), -999))
        allb.append(m)
    chest_chunks = set()
    for p in cont:
        a, b = _rot(p[0], p[2], rot, *_SHIP_PIVOT)
        chest_chunks.add(((ox + a) >> 4, (oz + b) >> 4))
    import numpy as np
    H0, HO, HA = np.array(hs), np.array(own), np.array(allb)
    ice = _surface_ice(seed, mc)
    # a chunk decorated before: its top layer frozen too (freeze_top_layer, last step)
    HF = HA.copy()
    if ice is not None and not beached:
        fr = np.array([ice.freezes(x, z) for x, z in cols])
        HF = np.where(fr & (HA < SEA_LEVEL + 1), SEA_LEVEL + 1, HA)
    cmask = {b: np.array([(x >> 4, z >> 4) == b for x, z in cols]) for b in B}
    WT = np.array([wt.get(c, -999) - base - (sy // 2 if beached else 0) for c in cols])  # ty + 1
    has_w = WT > -900

    def base_of(h):
        return int(h.min()) - sy // 2 if beached else int(h.sum()) // len(h)

    if int(mc) >= MC_26_1:                      # computed once, by the first chunk
        out = set()
        for combo in itertools.product(*[(H0, HO, HA, HF)] * len(B)):
            h = H0.copy()
            for b, HB in zip(B, combo):
                h = np.where(cmask[b], HB, h)
            out.add(base_of(h))
        return {c: out for c in chest_chunks}
    # before 26.1 every chunk computes it again: the chunks of the box decorated
    # before (in some order) hold their part of the wreck at the height they found
    BF = [b for b in B if bool((cmask[b] & has_w).any())]
    res = {c: set() for c in chest_chunks}
    # a chunk: in memory (frozen heightmap), or loaded again — then with the icebergs
    # (its own / its neighbours'), and once decorated its frozen top layer + its part
    for R in itertools.chain.from_iterable(itertools.combinations(B, k) for k in range(len(B) + 1)):
        for HB in ((HA, HF) if R else (HA,)):
            for k in range(len(BF) + 1):
                for seq in itertools.permutations(BF, k):
                    placed = np.full(len(cols), -999)
                    for d in seq + (None,):
                        h = H0.copy()
                        for b in R:
                            hb = np.maximum(HB if b in seq else HA, placed) if b != d else HO
                            h = np.where(cmask[b], hb, h)
                        bd = base_of(h)
                        if d is None:
                            for c in chest_chunks:
                                if c not in seq:
                                    res[c].add(bd)
                            break
                        if d in chest_chunks:
                            res[d].add(bd)
                        m = cmask[d] & has_w
                        placed = np.where(m, bd + (sy // 2 if beached else 0) + WT, placed)
    return res


def shipwreck(seed, mc, x, z, ver, fast=False):
    """Each chest's seed comes only from the draws of its own chunk: [beached:
    nextInt(3) of the sinking], one nextLong per container of the template placed in
    that chunk, then one per DATA marker in that chunk (the chest under it) — checked
    on the 26.2 server. 26.1+: the beached nextInt(3) is drawn only by the FIRST chunk
    of the wreck that generates (heightAdjusted); before, by every chunk."""
    import loot_engine as le
    cx, cz = x >> 4, z >> 4
    box, name, rot, beached = shipwreck_box(seed, mc, cx, cz)
    (sx, sy, sz), cont, metas = _SHIP_T[name]
    ox, oz = cx << 4, cz << 4

    def W(p):
        a, b = _rot(p[0], p[2], rot, *_SHIP_PIVOT)
        return ox + a, oz + b

    chunks = {(i, j) for i in range(box[0] >> 4, (box[2] >> 4) + 1)
              for j in range(box[1] >> 4, (box[3] >> 4) + 1)}
    hs = None if fast else _heights(seed, mc, ox, oz, sx, sz, 0 if beached else 1)
    base = None
    if hs:
        base = (min(hs) - sy // 2) if beached else int(sum(hs) / len(hs))
    bases = None
    if base is not None and int(mc) >= 22:          # (31/10) icebergs etc.: 1.18+ model
        try:
            bases = _ship_bases(seed, mc, cx, cz, name, rot, beached, base)
        except Exception:
            bases = None
    kind = ("beached " if beached else "") + name.replace("_", " ")
    out = []
    for i, p in enumerate(cont):
        wx, wz = W(p)
        c = (wx >> 4, wz >> 4)
        variants = [(True, None)]
        if beached and mc >= MC_26_1 and len(chunks) > 1:
            variants = [(False, "if another chunk of the wreck generated first"),
                        (True, "if this chunk generated first")]
        for first, label in variants:
            r = _rng(seed, mc, c[0], c[1], "shipwreck_beached" if (beached and mc >= 22)
                     else "shipwreck")
            k = None
            if beached and first:
                k = _next_int(r, 3)
            for q in cont:
                if (W(q)[0] >> 4, W(q)[1] >> 4) == c:
                    _next_long(r)
            ls = None
            for q in cont:
                if (W(q)[0] >> 4, W(q)[1] >> 4) == c:
                    v = _next_long(r)
                    if q == p:
                        ls = v
            if beached and not first:
                ks = []
                for o in sorted(chunks - {c}):
                    ro = _rng(seed, mc, o[0], o[1], "shipwreck_beached")
                    ks.append(_next_int(ro, 3))
                k = max(set(ks), key=ks.count)    # the sinking the first chunk drew
            wy = None
            ys = None
            if base is not None:
                wy = base + p[1] - (k or 0)
                if bases is not None and c in bases:
                    ys = sorted({b_ + p[1] - (k or 0) for b_ in bases[c]} | {wy})
            table = "shipwreck_" + metas[i]
            slots, approx = le.fill_chest(table, ls, ver)
            nm = _SHIP_NAMES[metas[i]] + (f" — {label}" if label else "")
            note = None
            if label and not first:
                note = ("26.1+: only the first chunk of a beached wreck to generate draws its "
                        "sinking depth, so this chest depends on the order the chunks were "
                        "generated in (Y within 2 blocks).")
            if label is None or not first:
                note = (note + " " if note else "") + f"Template: {kind}."
            if ys and len(ys) > 1:
                oth = [y_ for y_ in ys if y_ != wy]
                note = (note + " " if note else "") + (
                    f"Y {wy}, or {' / '.join(map(str, oth))} depending on the order the chunks were "
                    "generated in (a chunk saved and loaded again before the wreck counts its "
                    "icebergs" + (", and the part of the wreck already placed" if int(mc) < MC_26_1
                                  else "") + ").")
            out.append(dict(name=nm, pos=(wx, wy, wz), ys=ys, table=f"chests/{table}", seed=ls,
                            slots=slots, approx=approx, note=note))
    return out


# ─────────────────────────────────────────────────────────────────── ocean ruin ──
# Templates underwater_ruin/*: size and the "chest" DATA marker (the chest is put AT
# the marker). No container in the templates; the processors (BlockRot, the
# suspicious sand / gravel) use their own positional random, not the chunk's.
_OR_SMALL = (6, 7, 7)
_OR_BIG = (16, 16, 16)
_OR_CHEST = {
    "big_brick_1": (5, 0, 4), "big_brick_2": (9, 1, 10), "big_brick_3": (12, 2, 2),
    "big_brick_8": (5, 0, 4), "big_warm_4": (11, 0, 8), "big_warm_5": (7, 0, 7),
    "big_warm_6": (10, 0, 9), "big_warm_7": (11, 0, 7),
    "brick_1": (3, 1, 5), "brick_2": (2, 0, 1), "brick_3": (1, 0, 5), "brick_4": (1, 1, 4),
    "brick_5": (4, 0, 4), "brick_6": (2, 0, 2), "brick_7": (1, 0, 3), "brick_8": (3, 1, 4),
    "warm_1": (3, 1, 1), "warm_2": (3, 1, 4), "warm_3": (3, 0, 4), "warm_4": (1, 0, 2),
    "warm_5": (3, 0, 4), "warm_6": (4, 1, 4), "warm_7": (3, 0, 3), "warm_8": (3, 0, 3),
}
for _k in list(_OR_CHEST):                       # cracked / mossy = same chest as brick
    if "brick" in _k:
        _OR_CHEST[_k.replace("brick", "cracked")] = _OR_CHEST[_k]
        _OR_CHEST[_k.replace("brick", "mossy")] = _OR_CHEST[_k]
del _OR_CHEST["mossy_1"]                         # mossy_1 has no chest marker
_OR_WARM_BIOMES = (44, 45, 47, 48)               # warm, lukewarm, deep warm, deep lukewarm


def _mth_next_int(r, a, b):
    return a if a >= b else _next_int(r, b - a + 1) + a


def ocean_ruin_warm(seed, mc, cx, cz):
    try:
        import struct_info
        lib = struct_info._lib(int(seed), int(mc), None, 0)
        return int(lib.get_biome_at((cx << 4) + 8, (cz << 4) + 8, scale=4, y=320)) in _OR_WARM_BIOMES
    except Exception:
        return False


def _box_from(px, pz, cx_, cz_, rot):
    """BoundingBox.fromCorners(pos, transform(corner, rot, pivot 0) + pos), XZ."""
    a, b = _rot(cx_, cz_, rot, 0, 0)
    return (min(px, px + a), min(pz, pz + b), max(px, px + a), max(pz, pz + b))


def ocean_ruin_layout(seed, cx, cz, warm):
    """The pieces in the start's order: [(template, x, z, rot, big)]."""
    r = se.JavaRandom(0)
    r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
    pieces = []

    def add(px, pz, rot, big):
        if warm:
            arr = ([f"big_warm_{i}" for i in (4, 5, 6, 7)] if big
                   else [f"warm_{i}" for i in range(1, 9)])
            pieces.append((arr[r.nextInt_bound(len(arr))], px, pz, rot, big))
        else:
            ids = (1, 2, 3, 8) if big else tuple(range(1, 9))
            i = ids[r.nextInt_bound(len(ids))]
            pre = "big_" if big else ""
            for kind in ("brick", "cracked", "mossy"):
                pieces.append((f"{pre}{kind}_{i}", px, pz, rot, big))

    x0, z0 = cx << 4, cz << 4
    rot = r.nextInt_bound(4)                      # Rotation.getRandom
    big = r.nextFloat() <= 0.3                    # large_probability
    add(x0, z0, rot, big)
    if big and r.nextFloat() <= 0.9:              # cluster_probability
        pbb = _box_from(x0, z0, 15, 15, rot)
        bx, bz = pbb[0], pbb[1]                   # parentBottomLeft
        m = _mth_next_int
        pos = [(bx - 16 + m(r, 1, 8), bz + 16 + m(r, 1, 7)),
               (bx - 16 + m(r, 1, 8), bz + m(r, 1, 7)),
               (bx - 16 + m(r, 1, 8), bz - 16 + m(r, 4, 8)),
               (bx + m(r, 1, 7), bz + 16 + m(r, 1, 7)),
               (bx + m(r, 1, 7), bz - 16 + m(r, 4, 6)),
               (bx + 16 + m(r, 1, 7), bz + 16 + m(r, 3, 8)),
               (bx + 16 + m(r, 1, 7), bz + m(r, 1, 7)),
               (bx + 16 + m(r, 1, 7), bz - 16 + m(r, 4, 8))]
        for _ in range(m(r, 4, 8)):
            if not pos:
                continue
            px, pz = pos.pop(r.nextInt_bound(len(pos)))
            nrot = r.nextInt_bound(4)
            nb = _box_from(px, pz, 5, 6, nrot)
            if nb[0] <= pbb[2] and nb[2] >= pbb[0] and nb[1] <= pbb[3] and nb[3] >= pbb[1]:
                continue
            add(px, pz, nrot, False)
    return pieces


def ocean_ruin_boxes(seed, mc, cx, cz):
    """[(x0, z0, x1, z1, big)] of the ruins (one per position) — for the map."""
    out, seen = [], set()
    for name, px, pz, rot, big in ocean_ruin_layout(seed, cx, cz, ocean_ruin_warm(seed, mc, cx, cz)):
        if (px, pz) in seen:
            continue
        seen.add((px, pz))
        sx, _sy, sz = _OR_BIG if big else _OR_SMALL
        out.append(_box_from(px, pz, sx - 1, sz - 1, rot) + (big,))
    return out


class _Blocks:
    """Blocks after the carvers (terrain.chunk_underground), per chunk."""

    def __init__(self, seed, mc):
        self.seed, self.mc, self.c = seed, mc, {}

    def at(self, x, y, z):
        c = (x >> 4, z >> 4)
        if c not in self.c:
            self.c[c] = chunk_codes(self.seed, self.mc, c[0], c[1])
        b = self.c[c]
        if b is None or not (-64 <= y < 320):
            return None
        return b[(y + 64) * 256 + (z & 15) * 16 + (x & 15)]

    def floor(self, x, z):                        # OCEAN_FLOOR_WG
        for y in range(319, -65, -1):
            v = self.at(x, y, z)
            if v is None:
                return None
            if v == 0:
                return y + 1
        return -64


def _ruin_y(blk, px, pz, rot, size):
    """OceanRuinPiece.postProcess: floor height at the template corner, then
    getHeight (sinks it to the lowest floor when the area is uneven)."""
    y = blk.floor(px, pz)
    if y is None:
        return None
    a, b = _rot(size[0] - 1, size[2] - 1, rot, 0, 0)
    xs, zs = sorted((px, px + a)), sorted((pz, pz + b))
    min_y, top, area = 512, y - 1, 0
    for x in range(xs[0], xs[1] + 1):
        for z in range(zs[0], zs[1] + 1):
            fy = y - 1
            while fy > -63:
                v = blk.at(x, fy, z)
                if v is None:
                    return None
                if v not in (1, 2):               # air / water (ice: not simulated)
                    break
                fy -= 1
            min_y = min(min_y, fy)
            if fy < top - 2:
                area += 1
    if top - min_y > 2 and area > abs(xs[1] - xs[0]) - 2:
        return min_y + 1
    return y


_OR_SIMS = {}


def _ocean_ruin_sim(seed, mc, cx, cz, ver):
    """(30/10) 1.18+: ocean_ruin_y replays the ruins chunk by chunk (each chunk a
    ruin covers places it again from the heightmap at its corner, on the floor left
    by what was placed before): exact Y and seeds, every generation order."""
    import loot_engine as le
    import ocean_ruin_y as oy
    k = (int(seed), int(mc))
    S = _OR_SIMS.get(k)
    if S is None:
        if len(_OR_SIMS) > 4:
            _OR_SIMS.clear()
        S = _OR_SIMS[k] = oy.Sim(seed, mc)
    _w, ps, _chs, _b = S.start(cx, cz)
    big_at = {}
    for (name, px, pz, rot, big, _i, mark) in ps:
        if mark is not None:
            a, b = oy._rot(mark[0], mark[2], rot)
            big_at[(px + a, pz + b)] = big
    out = []
    n_small = 0
    for (wx, wz), res in S.outcomes(cx, cz).items():
        big = big_at.get((wx, wz), False)
        table = "underwater_ruin_big" if big else "underwater_ruin_small"
        if not big:
            n_small += 1
        nm = "Big ruin chest" if big else f"Small ruin chest {n_small}"
        ys = {}
        for E, y, sd in res:
            ys.setdefault(sd, set()).add(y)
        main = sorted({(y, sd) for E, y, sd in res if E == ()})
        if not main and res:
            main = sorted({(y, sd) for E, y, sd in res})[:1]
        for j, (y, sd) in enumerate(main):
            slots, approx = le.fill_chest(table, sd, ver)
            other = sorted(ys[sd] - {y})
            notes = []
            if len(main) > 1:
                notes.append("Cold ruins are three copies (brick, cracked, mossy) placed one after "
                             "the other: a copy that settled higher keeps its own chest — "
                             f"{len(main)} chests here.")
            if other:
                notes.append(f"Y {y}, or {' / '.join(map(str, other))} depending on the order the "
                             "chunks were generated in (each chunk of the ruin places it again on "
                             "the floor left before).")
            out.append(dict(name=nm + (f" ({j + 1}/{len(main)})" if len(main) > 1 else ""),
                            pos=(wx, y, wz), ys=sorted(ys[sd]), table=f"chests/{table}", seed=sd,
                            slots=slots, approx=approx, note=" ".join(notes) or None))
        for sd in sorted(set(ys) - {m[1] for m in main}):
            sl2, ap2 = le.fill_chest(table, sd, ver)
            yy = sorted(ys[sd])
            out.append(dict(name=f"{nm} — if the neighbouring chunks generated first",
                            pos=(wx, yy[0], wz), ys=yy, table=f"chests/{table}", seed=sd,
                            slots=sl2, approx=ap2,
                            note="Another copy of the ruin keeps its chest when the chunks "
                                 "around were generated before this one."))
    if not out:
        return [dict(name="No chest", pos=(cx * 16, None, cz * 16), table="", seed=None,
                     slots=None, approx=[], note="This ruin has no chest.")]
    return out


_ORF = {}


def _or_start(seed, mc, scx, scz):
    """ocean_ruin_y.Sim.start without the world: (warm, pieces, their chunks, box)"""
    k = (int(seed), int(mc), scx, scz)
    v = _ORF.get(k)
    if v is None:
        import ocean_ruin_y as oy
        warm = ocean_ruin_warm(seed, mc, scx, scz)
        lay = ocean_ruin_layout(seed, scx, scz, warm)
        ps = [(n, px, pz, rot, big, oy.integrity(n, big), _OR_CHEST.get(n)) for n, px, pz, rot, big in lay]
        chs = [oy.piece_chunks(p) for p in ps]
        b = (min(min(c[0] for c in s_) for s_ in chs), min(min(c[1] for c in s_) for s_ in chs),
             max(max(c[0] for c in s_) for s_ in chs), max(max(c[1] for c in s_) for s_ in chs))
        if len(_ORF) > 4096:
            _ORF.clear()
        v = _ORF[k] = (warm, ps, chs, b)
    return v


def _or_near(seed, mc, cx, cz, r=4):
    k = ("near", int(seed), int(mc), cx, cz, r)
    v = _ORF.get(k)
    if v is None:
        import struct_info
        import cubiomes_wrap as cw
        lib = struct_info._lib(int(seed), int(mc), None, 0)
        pts = lib.find_structures_in_box(cw.Ocean_Ruin, (cx - r) * 16, (cz - r) * 16,
                                         (cx + r) * 16 + 15, (cz + r) * 16 + 15,
                                         verify_biome=True, max_count=512)
        v = _ORF[k] = sorted({(x >> 4, z >> 4) for x, z in pts})
    return v


def _ocean_ruin_fast118(seed, mc, cx, cz, ver):
    """(07/11, the loot finder) 1.18+, without the terrain: in the chunk of each of
    this ruin's chests, every seed it can get — the first K nextLong of that chunk's
    random (K = the chest markers there of every ruin of the same kind reaching it,
    as Sim.decorate draws them), with each of its tables. A superset of the exact
    chests (the finder checks its matches with the full replay)."""
    import loot_engine as le
    import ocean_ruin_y as oy
    warm, ps, _chs, _b = _or_start(seed, mc, cx, cz)
    want = {}
    for pc in ps:
        if pc[6] is None:
            continue
        a, b = oy._rot(pc[6][0], pc[6][2], pc[3])
        want.setdefault(((pc[1] + a) >> 4, (pc[2] + b) >> 4), set()).add(
            "underwater_ruin_big" if pc[4] else "underwater_ruin_small")
    out = []
    for c0, tabs in want.items():
        near = set(_or_near(seed, mc, c0[0], c0[1])) | {(cx, cz)}
        K = 0
        for k in range(c0[0] - 8, c0[0] + 9):
            for l in range(c0[1] - 8, c0[1] + 9):
                if (k, l) not in near:
                    continue
                w2, ps2, chs2, bb = _or_start(seed, mc, k, l)
                if w2 != warm or not (bb[0] <= c0[0] <= bb[2] and bb[1] <= c0[1] <= bb[3]):
                    continue
                for pc, pch in zip(ps2, chs2):
                    if pc[6] is None or c0 not in pch:
                        continue
                    a, b = oy._rot(pc[6][0], pc[6][2], pc[3])
                    if ((pc[1] + a) >> 4, (pc[2] + b) >> 4) == c0:
                        K += 1
        r = _rng(seed, mc, c0[0], c0[1], "ocean_ruin_warm" if warm else "ocean_ruin_cold")
        for _j in range(K):
            ls = _next_long(r)
            for t in sorted(tabs):
                slots, approx = le.fill_chest(t, ls, ver)
                out.append(dict(name="Ruin chest", pos=(c0[0] * 16, None, c0[1] * 16),
                                table=f"chests/{t}", seed=ls, slots=slots, approx=approx))
    return out or [dict(name="No chest", pos=(cx * 16, None, cz * 16), table="", seed=None,
                        slots=None, approx=[])]


def ocean_ruin(seed, mc, x, z, ver, fast=False):
    """Each chest = the chunk's random after one nextLong per "chest" marker of the
    pieces before it in that chunk (cold ruins: brick, cracked and mossy pieces at the
    same place → three draws on the same chest, the last one stays)."""
    import loot_engine as le
    cx, cz = x >> 4, z >> 4
    if fast and 22 <= int(mc):
        return _ocean_ruin_fast118(seed, mc, cx, cz, ver)
    if 22 <= int(mc) <= 35:
        try:
            return _ocean_ruin_sim(seed, mc, cx, cz, ver)
        except Exception:
            pass
    warm = ocean_ruin_warm(seed, mc, cx, cz)
    pieces = ocean_ruin_layout(seed, cx, cz, warm)
    marks = []                                    # (piece index, wx, wz, local y, big, piece)
    for i, (name, px, pz, rot, big) in enumerate(pieces):
        lp = _OR_CHEST.get(name)
        if lp is None:
            continue
        a, b = _rot(lp[0], lp[2], rot, 0, 0)
        marks.append((i, px + a, pz + b, lp[1], big, (px, pz, rot)))
    blk = _Blocks(seed, mc) if (19 <= int(mc) <= 35 and not fast) else None   # (only the Y)
    final = {}
    draws = {}                                    # (wx, wz) -> [(piece name, seed)] in order
    for i, wx, wz, ly, big, pc in marks:
        c = (wx >> 4, wz >> 4)
        r = _rng(seed, mc, c[0], c[1], ("ocean_ruin_warm" if warm else "ocean_ruin_cold")
                 if mc >= 22 else "ocean_ruin")
        v = None
        for j, wx2, wz2, *_ in marks:
            if (wx2 >> 4, wz2 >> 4) == c:
                t = _next_long(r)
                if j == i:
                    v = t
                    break
        final[(wx, wz)] = (v, ly, big, pc)        # a later piece at the same spot wins
        draws.setdefault((wx, wz), []).append((pieces[i][0], v))
    out = []
    n_small = 0
    for (wx, wz), (ls, ly, big, (px, pz, rot)) in final.items():
        wy = None
        if blk is not None:
            try:
                y0 = _ruin_y(blk, px, pz, rot, _OR_BIG if big else _OR_SMALL)
                wy = None if y0 is None else y0 + ly
            except Exception:
                wy = None
        table = "underwater_ruin_big" if big else "underwater_ruin_small"
        if not big:
            n_small += 1
        nm = "Big ruin chest" if big else f"Small ruin chest {n_small}"
        slots, approx = le.fill_chest(table, ls, ver)
        others = [(n, v) for n, v in draws.get((wx, wz), [])[:-1] if v is not None and v != ls]
        note = "Y approximate (the ruin sits on the sea floor)." if wy is not None else None
        if others:
            note = ((note + " ") if note else "") + (
                "Cold ruins are three copies (brick, cracked, mossy) placed one after the other, "
                "each on the sea floor left by the previous one: when a copy settles one or two "
                "blocks higher, its chest stays too — see the other cards.")
        out.append(dict(name=nm, pos=(wx, wy, wz), table=f"chests/{table}", seed=ls,
                        slots=slots, approx=approx, note=note))
        for n, v in others:
            kind = n.split("_")[-2] if n.startswith("big_") else n.split("_")[0]
            sl2, ap2 = le.fill_chest(table, v, ver)
            out.append(dict(name=f"{nm} — if the {kind} copy settled at another height",
                            pos=(wx, wy, wz), table=f"chests/{table}", seed=v, slots=sl2,
                            approx=ap2, note=None))
    if not out:
        return [dict(name="No chest", pos=(cx * 16, None, cz * 16), table="", seed=None,
                     slots=None, approx=[], note="This ruin has no chest.")]
    return out


def treasure_y(seed, mc, x, z):
    """(31/10) (chest Y or None, ground Y or None) of a buried treasure at block (x, z).
    1.16 / 1.17 (block types known): BuriedTreasurePiece — from OCEAN_FLOOR_WG down to
    the first block standing on stone / sandstone. 1.18+: the ground only."""
    if int(mc) <= 21:
        raw = t116_raw(seed, mc, x >> 4, z >> 4)
        if raw is None:
            return None, None
        i = (z & 15) * 16 + (x & 15)
        at = lambda y: raw[(y << 8) + i] if 0 <= y <= 255 else 0
        y = 255
        while y >= 0 and at(y) in (0, 25, 2, 3, 35, 36, 37):    # not motion-blocking
            y -= 1
        floor = y + 1
        y = floor
        while y > 0:
            if at(y - 1) in (1, 13):                            # stone, sandstone
                return y, floor
            y -= 1
        return None, floor
    c = chunk_codes(seed, mc, x >> 4, z >> 4)
    if c is None:
        return None, None
    i = (z & 15) * 16 + (x & 15)
    y = 319
    while y >= -64 and c[(y + 64) * 256 + i] != 0:
        y -= 1
    return None, y + 1
