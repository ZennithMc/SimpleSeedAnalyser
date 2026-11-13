"""
ancient_city_loot.py — (30/10) the ancient cities piece by piece and their chests,
1.19.2 → 26.3.

Layout: jigsaw.generate (JigsawPlacement) on the ancient_city pools (loot_tables/
ancient_city.json.gz, read in the 26.2 jar: the same pools and templates from 1.19.2 —
the 1.19.2 pools dumped from its registries), start pool ancient_city/city_center,
size 7, max distance 116, start y -27 with the « city_anchor » jigsaw of the start
piece on it (getRandomNamedJigsaw: the start's shuffled jigsaws). Rigid: the terrain
does not matter. A template the jar lacks (walls/intact_horizontal_wall_stairs_5) is
an empty one, as StructureTemplateManager.getOrCreate makes it.
Chests: chests with a LootTable in the templates (placeInWorld: a nextLong for each
container). The random: the chunk of the chest's (UNDERGROUND_DECORATION, ancient_city
index); the pieces crossing that chunk, in order. The sculk patch features also draw
from it (and how much depends on the blocks): flagged.
"""
from __future__ import annotations

import gzip
import json
import os

import jigsaw as jg

UNDERGROUND_DECORATION = 7

_G = None
_TABLE_NAME = {"ancient_city": "Chest", "ancient_city_ice_box": "Ice box"}


def gen():
    global _G
    if _G is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "ancient_city.json.gz")
        with gzip.open(p, "rt", encoding="utf-8") as f:
            d = json.load(f)
        g = jg.Gen.__new__(jg.Gen)
        g.version, g.d, g.T = "ancient_city", d, d["templates"]
        g.pools = {k: jg.Pool(v, k) for k, v in d["pools"].items()}
        g._max = {}
        _G = g
    return _G


_CACHE = {}


def layout(seed, mc, cx, cz):
    """(pieces, centre) of the ancient city started in chunk (cx, cz) (no biome check)."""
    if int(mc) < 23:
        return None
    k = (int(seed), int(mc), int(cx), int(cz))
    if k in _CACHE:
        return _CACHE[k]
    res = jg.generate(seed, mc, "ancient_city", int(cx), int(cz), gen(), lambda x, z: 0, hmode=0)
    if len(_CACHE) > 256:
        _CACHE.clear()
    _CACHE[k] = res
    return res


def city_index(mc):
    """ancient_city's index in UNDERGROUND_DECORATION: registered after fortress and
    nether_fossil in 1.19.2, first by name from 1.19.4."""
    return 2 if int(mc) == 23 else 0


def chest_list(seed, mc, x, z):
    """[(x, y, z, loot seed, table, piece location, sculk)] of the city at block (x, z);
    sculk: a sculk patch feature of the city draws from the chunk's random before
    the chest (its draws depend on the blocks around: the seed may be off)."""
    import structure_loot as sl
    lay = layout(seed, mc, int(x) >> 4, int(z) >> 4)
    if not lay:
        return []
    g = gen()
    plist = [(p, jg.containers(g, p)) for p in lay[0]]
    chests = [(w, t, p.loc) for p, cs in plist for (w, _b, t) in cs]
    seeds, warn = {}, {}
    for ch in sorted({(w[0] >> 4, w[2] >> 4) for w, _t, _l in chests}):
        r = sl.structure_rng(seed, mc, ch[0], ch[1], city_index(mc), UNDERGROUND_DECORATION)
        feat = False
        for p, cs in plist:
            b = p.box
            if not (b[0] <= (ch[0] << 4) + 15 and b[3] >= ch[0] << 4
                    and b[2] <= (ch[1] << 4) + 15 and b[5] >= ch[1] << 4):
                continue
            if p.el.kind == "feature":
                if (p.pos[0] >> 4, p.pos[2] >> 4) == ch:
                    feat = True
                continue
            for (w, _b, _t) in cs:
                if (w[0] >> 4, w[2] >> 4) == ch:
                    seeds[(w, id(p))] = sl._next_long(r)
                    warn[(w, id(p))] = feat
    out = []
    for p, cs in plist:
        for (w, _b, t) in cs:
            out.append((w[0], w[1], w[2], seeds.get((w, id(p))), t, p.loc, warn.get((w, id(p)), False)))
    return out


def chests(seed, mc, x, z, ver):
    import loot_engine as le
    lst = [c for c in chest_list(seed, mc, x, z) if c[4]]
    if not lst:
        return [dict(name="No chest", pos=(int(x), None, int(z)), table="", seed=None, slots=None,
                     approx=[], note="No loot chest in this ancient city (or not 1.19+).")]
    out = []
    n = {}
    for (X, Y, Z, s, t, loc, sculk) in sorted(lst, key=lambda c: (c[1], c[0], c[2])):
        tb = t.split("/")[-1]
        lab = _TABLE_NAME.get(tb, "Chest")
        n[lab] = n.get(lab, 0) + 1
        slots, approx = le.fill_chest(tb, s, ver)
        note = f"Piece {loc.split('/', 1)[-1]}."
        if sculk:
            note += (" A sculk patch is placed in this chunk before the chest: its draws depend"
                     " on the blocks around, the contents may differ.")
        out.append(dict(name=f"{lab} {n[lab]}", pos=(X, Y, Z), table="chests/" + tb, seed=s,
                        slots=slots, approx=approx, note=note))
    return out


def map_boxes(seed, mc, x, z):
    lay = layout(seed, mc, int(x) >> 4, int(z) >> 4)
    if not lay:
        return []
    g = gen()
    out = []
    for p in lay[0]:
        if p.el.kind in ("feature", "empty"):
            continue
        loc = p.loc or ""
        b = jg.piece_box(g, p)
        if b is None or (b[0] == b[3] and b[2] == b[5]):
            continue
        k = "center" if "city_center" in loc else "street" if "/walls" in loc else "house"
        out.append((b[0], b[2], b[3], b[5], k, b[1], b[4]))
    return out
