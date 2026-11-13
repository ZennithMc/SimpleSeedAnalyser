"""
bastion_loot.py — (30/10) the bastion remnants piece by piece and their chests,
1.16.1 → 26.3.

Layout: jigsaw.generate (JigsawPlacement) on the bastion pools (loot_tables/
bastion.json.gz, read in the 26.2 jar: the templates are the same in every version
from 1.16.5, the pools differ only by the processor lists' names), start pool
bastion/starts, size 6, start at y 33 (no heightmap: every piece is rigid, the
Nether terrain does not matter), max distance 80. Up to 1.18.2: fastutil shuffle; 1.16.1:
the start pool drawn first among 4 (units, hoglin stables, treasure, bridge), size 60.
Chests: chests with a LootTable in the templates; StructureTemplate.placeInWorld
draws a LootTableSeed for each (random.nextLong()). The random: the chunk of the
chest's (SURFACE_STRUCTURES, bastion_remnant index); the pieces crossing that chunk,
in order, each with its containers in the chunk (template order y, x, z).
"""
from __future__ import annotations

import gzip
import json
import os

import jigsaw as jg

_G = None
TYPES = {"bastion/units/air_base": "Housing units",           # (start piece → type)
         "bastion/hoglin_stable/air_base": "Hoglin stables",
         "bastion/treasure/big_air_full": "Treasure room",
         "bastion/bridge/starting_pieces/entrance_base": "Bridge"}
_TABLE_NAME = {"bastion_treasure": "Treasure chest", "bastion_other": "Chest",
               "bastion_hoglin_stable": "Stable chest", "bastion_bridge": "Bridge chest"}


def gen():
    global _G
    if _G is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "bastion.json.gz")
        with gzip.open(p, "rt", encoding="utf-8") as f:
            d = json.load(f)
        g = jg.Gen.__new__(jg.Gen)
        g.version, g.d, g.T = "bastion", d, d["templates"]
        g.pools = {k: jg.Pool(v, k) for k, v in d["pools"].items()}
        g._max = {}
        _G = g
    return _G


_CACHE = {}


def layout(seed, mc, cx, cz):
    """(pieces, centre) of the bastion started in chunk (cx, cz) (no biome check)."""
    k = (int(seed), int(mc), int(cx), int(cz))
    if k in _CACHE:
        return _CACHE[k]
    g = gen()
    sk = "bastion_remnant_1161" if int(mc) <= 19 else "bastion_remnant_1182" if int(mc) <= 22 \
        else "bastion_remnant"
    res = jg.generate(seed, mc, sk, int(cx), int(cz), g, lambda x, z: 0, hmode=0)
    if len(_CACHE) > 256:
        _CACHE.clear()
    _CACHE[k] = res
    return res


def kind(seed, mc, cx, cz):
    lay = layout(seed, mc, cx, cz)
    return TYPES.get(lay[0][0].loc) if lay else None


def chest_list(seed, mc, x, z):
    """[(x, y, z, loot seed, table, piece location)] of the bastion at block (x, z)."""
    import scattered_loot as sc
    lay = layout(seed, mc, int(x) >> 4, int(z) >> 4)
    if not lay:
        return []
    g = gen()
    plist = [(p, jg.containers(g, p)) for p in lay[0]]
    chests = [(w, t, p.loc) for p, cs in plist for (w, _b, t) in cs]
    seeds = {}
    for ch in sorted({(w[0] >> 4, w[2] >> 4) for w, _t, _l in chests}):
        r = sc._rng(seed, mc, ch[0], ch[1], "bastion_remnant")
        for p, cs in plist:
            b = p.box
            if not (b[0] <= (ch[0] << 4) + 15 and b[3] >= ch[0] << 4
                    and b[2] <= (ch[1] << 4) + 15 and b[5] >= ch[1] << 4):
                continue
            for (w, _b, _t) in cs:
                if (w[0] >> 4, w[2] >> 4) == ch:
                    seeds[(w, id(p))] = sc._next_long(r)
    out = []
    for p, cs in plist:
        for (w, _b, t) in cs:
            out.append((w[0], w[1], w[2], seeds.get((w, id(p))), t, p.loc))
    return out


def chests(seed, mc, x, z, ver):
    import loot_engine as le
    lst = chest_list(seed, mc, x, z)
    if not lst:
        return [dict(name="No chest", pos=(int(x), None, int(z)), table="", seed=None, slots=None,
                     approx=[], note="No chest in this bastion.")]
    out = []
    n = {}
    fac = {}                                   # (31/10) for the double chests (loot window)
    try:
        lay = layout(seed, mc, int(x) >> 4, int(z) >> 4)
        g = gen()
        for p in lay[0]:
            fac.update(jg.container_facings(g, p))
    except Exception:
        fac = {}
    for (X, Y, Z, s, t, loc) in sorted(lst, key=lambda c: (c[4] != "chests/bastion_treasure", c[1], c[0], c[2])):
        tb = (t or "chests/bastion_other").split("/")[-1]
        lab = _TABLE_NAME.get(tb, "Chest")
        n[lab] = n.get(lab, 0) + 1
        slots, approx = le.fill_chest(tb, s, ver)
        out.append(dict(name=f"{lab} {n[lab]}", pos=(X, Y, Z), table="chests/" + tb, seed=s,
                        slots=slots, approx=approx, note=f"Piece {loc.split('/', 1)[-1]}.",
                        facing=fac.get((X, Y, Z)), block="chest"))
    return out


def map_boxes(seed, mc, x, z):
    lay = layout(seed, mc, int(x) >> 4, int(z) >> 4)
    if not lay:
        return []
    g = gen()
    out = []
    for i, p in enumerate(lay[0]):
        loc = p.loc or ""
        if ("air" in loc.rsplit("/", 1)[-1] or "/mobs/" in loc or "/blocks/" in loc):
            continue                                  # air volumes, mobs, gold blocks
        b = jg.piece_box(g, p)
        if b is None or (b[0] == b[3] and b[2] == b[5]):
            continue
        if any(w in loc for w in ("/walls/", "/ramparts/", "/rampart", "/bridge/", "/legs/", "/connectors/")):
            k = "street"
        else:
            k = "house"
        out.append((b[0], b[2], b[3], b[5], k, b[1], b[4]))
    return out
