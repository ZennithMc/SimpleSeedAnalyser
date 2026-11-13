"""
jigsaw_extra.py — (05/11) trail ruins (1.20+) and trial chambers (1.21+): their jigsaw
layout (jigsaw.generate on their own data: loot_tables/trail_ruins.json.gz,
trial_chambers.json.gz, tools/build_jigsaw_extra.py).

Trail ruins: start 15 blocks under the WORLD_SURFACE_WG height at the centre, 7 deep.
Trial chambers: start Y drawn uniformly in [-40, -20] (first draw of the structure
random), no heightmap, 20 deep, 116 blocks around, 10 blocks from the world's bottom
and top; the pool aliases only pick the spawners' mobs (their own random): each alias
is read as its first target (one template each: no draw).
"""
from __future__ import annotations

import gzip
import json
import os

import jigsaw as jg

_G = {}
_CACHE = {}


def gen(key):
    g = _G.get(key)
    if g is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", key + ".json.gz")
        with gzip.open(p, "rt", encoding="utf-8") as f:
            d = json.load(f)
        g = jg.Gen.__new__(jg.Gen)
        g.version, g.d, g.T = key, d, d["templates"]
        g.pools = {k: jg.Pool(v, k) for k, v in d["pools"].items()}
        st = d["structures"][key]
        for a in st.get("pool_aliases", ()):          # alias → its first target
            t = a.get("type", "")
            pairs = []
            if t.endswith("random_group"):
                pairs = [(x["alias"], x["target"]) for x in a["groups"][0]["data"]]
            elif t.endswith("random"):
                tg = a["targets"][0]["data"]
                pairs = [(a["alias"], tg)]
            elif t.endswith("direct"):
                pairs = [(a["alias"], a["target"])]
            for al, tg in pairs:
                al, tg = jg._id(al), jg._id(tg)
                if tg in g.pools:
                    g.pools[al] = g.pools[tg]
        if key == "trial_chambers":
            st["no_projection"] = True
        g._max = {}
        _G[key] = g
    return g


def layout(seed, mc, key, cx, cz):
    """(pieces, centre) of the trail ruins / trial chambers started in chunk (cx, cz)."""
    mc = int(mc)
    if (key == "trail_ruins" and mc < 24) or (key == "trial_chambers" and mc < 26):
        return None
    k = (int(seed), mc, key, int(cx), int(cz))
    if k in _CACHE:
        return _CACHE[k]
    g = gen(key)
    if key == "trial_chambers":
        res = jg.generate(seed, mc, key, int(cx), int(cz), g, lambda x, z: 0, hmode=None)
    else:
        res = jg.generate(seed, mc, key, int(cx), int(cz), g)
    if len(_CACHE) > 128:
        _CACHE.clear()
    _CACHE[k] = res
    return res
