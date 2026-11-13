"""
end_city_loot.py — (30/10) the chests of the End cities (and their ship), 1.16.1 → 26.3.

Pieces: libcubiomes getEndCityPieces (same order as the game's start, checked on the
servers), the city on the island height isViableEndCityTerrain finds.
Chests: fat_tower_top (2), third_floor_2 (1), ship (2) — a chest block with an NBT
(StructureTemplate.placeInWorld draws a LootTableSeed for it: random.nextLong()),
then the « Chest » data marker above it sets the end_city_treasure table with a new
nextLong. The random: the chunk of the chest's (SURFACE_STRUCTURES, end_city index);
every piece of the city crossing that chunk, in order: its chests in the chunk
(template order y, x, z), then its « Chest » markers whose chest is in the chunk.
"""
from __future__ import annotations

import gzip
import json
import os

_T = None
_NAMES = ("base_floor", "base_roof", "bridge_end", "bridge_gentle_stairs", "bridge_piece",
          "bridge_steep_stairs", "fat_tower_base", "fat_tower_middle", "fat_tower_top",
          "second_floor_1", "second_floor_2", "second_roof", "ship", "third_floor_1",
          "third_floor_2", "third_roof", "tower_base", "tower_floor", "tower_piece", "tower_top")


def templates():
    global _T
    if _T is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "end_city.json.gz")
        with gzip.open(p, "rt") as f:
            _T = json.load(f)
    return _T


def local_to_world(piece, lx, ly, lz):
    """The template's local block → world, from the piece box and rotation
    (0 none, 1 cw 90, 2 180, 3 ccw 90; no pivot: the template position is the box
    corner the rotation leaves at its origin)."""
    x0, z0, x1, z1, _t, rot, y0, _y1 = piece
    rot &= 3
    if rot == 0:
        return x0 + lx, y0 + ly, z0 + lz
    if rot == 1:
        return x1 - lz, y0 + ly, z0 + lx
    if rot == 2:
        return x1 - lx, y0 + ly, z1 - lz
    return x0 + lz, y0 + ly, z1 - lx


def pieces(seed, mc, x, z):
    import struct_info
    lib = struct_info._lib(int(seed), int(mc), None, 1)
    return lib.struct_pieces("end_city", int(x), int(z))


def _in(piece, cx, cz):
    x0, z0, x1, z1 = piece[0], piece[1], piece[2], piece[3]
    return x1 >= cx << 4 and x0 <= (cx << 4) + 15 and z1 >= cz << 4 and z0 <= (cz << 4) + 15


def chest_list(seed, mc, x, z):
    """[(x, y, z, loot seed, template)] of the city at (x, z)."""
    import scattered_loot as sc
    ps = pieces(seed, mc, x, z)
    T = templates()
    chests = []
    for p in ps:
        t = _NAMES[p[4]] if 0 <= p[4] < len(_NAMES) else ""
        for c in T.get(t, {}).get("chests", ()):
            chests.append((local_to_world(p, *c), t))
    out = []
    seeds = {}
    for ch in sorted({(w[0] >> 4, w[2] >> 4) for w, _t in chests}):
        r = sc._rng(seed, mc, ch[0], ch[1], "end_city")
        for p in ps:
            if not _in(p, *ch):
                continue
            t = _NAMES[p[4]] if 0 <= p[4] < len(_NAMES) else ""
            d = T.get(t)
            if not d:
                continue
            for c in d["chests"]:                       # placeInWorld: the chests' NBT
                w = local_to_world(p, *c)
                if (w[0] >> 4, w[2] >> 4) == ch:
                    sc._next_long(r)
            for m in d["marks"]:                        # handleDataMarker « Chest »
                w = local_to_world(p, m[0], m[1] - 1, m[2])
                if (w[0] >> 4, w[2] >> 4) == ch:
                    seeds[w] = sc._next_long(r)
    for w, t in chests:
        out.append((w[0], w[1], w[2], seeds.get(w), t))
    return out


_LABEL = {"fat_tower_top": "Tower top chest", "third_floor_2": "City chest", "ship": "Ship chest"}


def chests(seed, mc, x, z, ver):
    import loot_engine as le
    lst = chest_list(seed, mc, x, z)
    if not lst:
        return [dict(name="No chest", pos=(int(x), None, int(z)), table="", seed=None, slots=None,
                     approx=[], note="No chest in this End city (no tower top, loot room or ship).")]
    out = []
    n = {}
    for (X, Y, Z, s, t) in lst:
        n[t] = n.get(t, 0) + 1
        slots, approx = le.fill_chest("end_city_treasure", s, ver)
        note = None
        if t == "ship":
            note = "In the ship's hold, next to the elytra."
        out.append(dict(name=f"{_LABEL.get(t, 'Chest')} {n[t]}", pos=(X, Y, Z),
                        table="chests/end_city_treasure", seed=s, slots=slots, approx=approx, note=note))
    return out
