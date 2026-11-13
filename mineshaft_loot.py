"""
mineshaft_loot.py — abandoned mineshafts: where they are, their pieces and the
loot of their chest minecarts, 1.16.1 → 26.3 (29/10).

  1.16 / 1.17: mineshaft116.c on the exact 1.16 terrain (libcubiomes
               ssa_t116_mineshaft_*): starts (biome at the chunk), pieces,
               chests of a chunk (its neighbours decorated first, or not).
  1.18+:       mineshaft118.py (starts with the deep-dark / mesa checks, the 1.16
               layout moved below the sea level) placed block by block on the
               1.18 underground (terrain.cave118 + geodes of prefeatures118).

A chest = MineShaftCorridor.createChest: in a corridor section, nextInt(100) == 0
on either side, if the spot is air on a non-air block: rail (nextBoolean), then
the minecart's LootTableSeed = nextLong of the chunk's structure random (index of
the mineshaft in UNDERGROUND_STRUCTURES). Every draw of the pieces placed before
in that chunk counts (other mineshafts of the chunk too), and the air depends on
the caves and on what the neighbouring chunks placed if they were decorated first.
"""
from __future__ import annotations

import ctypes as ct

OLD = (19, 20, 21)
_T = ("room", "corridor", "crossing", "stairs")


def _lib():
    from cubiomes_wrap import get_lib
    return get_lib()._lib


def _s64(seed):
    return ct.c_uint64(int(seed) & ((1 << 64) - 1))


def starts(seed, mc, cx0, cz0, cx1, cz1):
    """[(cx, cz, mesa)] of the mineshaft starts in the chunk rectangle."""
    mc = int(mc)
    L = _lib()
    n_max = max(16, (cx1 - cx0 + 1) * (cz1 - cz0 + 1) // 64 + 64)
    if mc in OLD:
        f = L.ssa_t116_mineshaft_starts
        f.restype = ct.c_int
        buf = (ct.c_int32 * (3 * n_max))()
        n = f(mc, _s64(seed), cx0, cz0, cx1, cz1, buf, n_max)
        return [(buf[3 * i], buf[3 * i + 1], buf[3 * i + 2] == 2) for i in range(max(0, n))]
    if mc < 22:
        return []
    import mineshaft118 as ms
    f = L.ssa_mineshaft_candidates
    f.restype = ct.c_int
    buf = (ct.c_int32 * (2 * n_max))()
    n = f(_s64(seed), cx0, cz0, cx1, cz1, buf, n_max)
    bio, _deep = ms.biome_fns(seed, mc)
    h = ms._height_fn(seed, mc)
    out = []
    for i in range(max(0, n)):
        x, z = buf[2 * i], buf[2 * i + 1]
        # mineshaft118.has_start, its normal layout in C (same boxes, 38 × faster)
        room = _c118(seed, mc, x, z, 1)
        b = bio((x << 4) + 8, room[0][1], z << 4) if room else ""
        if b not in ("badlands", "eroded_badlands", "wooded_badlands"):
            if not room or (b == "deep_dark" and mc >= 23):
                continue
            out.append((x, z, False))
            continue
        t = ms.has_start(seed, mc, x, z, bio, h)
        if t:
            out.append((x, z, t == 2))
    return out


def _c118(seed, mc, cx, cz, n_max=1024):
    """1.18+ normal layout (libcubiomes ssa_mineshaft118_pieces): [(x0, y0, z0, x1,
    y1, z1, type)]."""
    f = _lib().ssa_mineshaft118_pieces
    f.restype = ct.c_int
    buf = (ct.c_int32 * (7 * n_max))()
    n = f(int(mc), _s64(seed), cx, cz, buf, n_max)
    return [tuple(buf[7 * i:7 * i + 7]) for i in range(max(0, n))]


def pieces(seed, mc, cx, cz, mesa=None):
    """[(x0, y0, z0, x1, y1, z1, kind)] of the start of chunk (cx, cz) at its final
    place ([] if none / Y unknown)."""
    mc = int(mc)
    if mc in OLD:
        f = _lib().ssa_t116_mineshaft_pieces
        f.restype = ct.c_int
        buf = (ct.c_int32 * (7 * 1024))()
        n = f(mc, _s64(seed), cx, cz, buf, 1024)
        return [tuple(buf[7 * i:7 * i + 6]) + (_T[buf[7 * i + 6]],) for i in range(max(0, n))]
    import mineshaft118 as ms
    if mesa is None:
        hit = [m for x, z, m in starts(seed, mc, cx, cz, cx, cz) if (x, z) == (cx, cz)]
        if not hit:
            return []
        mesa = hit[0]
    if not mesa:
        return [b[:6] + (_T[b[6]],) for b in _c118(seed, mc, cx, cz)]
    ps, ents = ms.layout_ex(seed, cx, cz, mesa=mesa, mc=mc, height=ms._height_fn(seed, mc))
    if ents is None:
        return []
    return [tuple(p.bb) + (_T[p.type],) for p in ps]


def map_points(seed, mc, bx, bz, B=4096, seed_view=False):
    """The map's points of a B-block block: (cx * 16 + 8, cz * 16 + 8) per start
    (map helper process). Structure-seed view: every chunk of the frequency test."""
    c0x, c0z = (bx * B) >> 4, (bz * B) >> 4
    c1x, c1z = c0x + (B >> 4) - 1, c0z + (B >> 4) - 1
    if seed_view:
        L = _lib()
        f = L.ssa_mineshaft_candidates
        f.restype = ct.c_int
        n_max = (B >> 4) * (B >> 4) // 64 + 256
        buf = (ct.c_int32 * (2 * n_max))()
        n = f(_s64(seed), c0x, c0z, c1x, c1z, buf, n_max)
        return [(buf[2 * i] * 16 + 8, buf[2 * i + 1] * 16 + 8) for i in range(max(0, n))]
    return [(x * 16 + 8, z * 16 + 8) for x, z, _m in starts(seed, mc, c0x, c0z, c1x, c1z)]


def map_boxes(seed, mc, cx, cz, large=None):
    """For the map: [(x0, z0, x1, z1, kind, y0, y1)]."""
    return [(b[0], b[2], b[3], b[5], b[6], b[1], b[4]) for b in pieces(seed, mc, cx, cz)]


def _chunk_chests(seed, mc, cx, cz, neighbours, reloaded=False):
    """([(x, y, z, seed, start)], several starts of a kind reach the chunk?)."""
    mc = int(mc)
    if mc in OLD:
        import structure_loot as sl
        f = _lib().ssa_t116_mineshaft_chests
        f.restype = ct.c_int
        buf = (ct.c_int64 * (6 * 64))()
        n = f(mc, _s64(seed), cx, cz, sl.T116_NB_MASK if neighbours else 0, buf, 64)
        return [(buf[6 * i], buf[6 * i + 1], buf[6 * i + 2], buf[6 * i + 3],
                 (buf[6 * i + 4], buf[6 * i + 5])) for i in range(max(0, n))], False
    import prefeatures118 as pf
    import mineshaft118 as ms
    out = pf.mineshaft_chests(seed, mc, cx, cz, neighbours, reloaded)
    return out, ms.LAST_MULTI[0]


_CACHE = {}

# the generation-order variants of a chunk: its 8 neighbours decorated before it or
# not; its reference sets as built (in memory) or rebuilt from disk (another order of
# the starts when two of a kind reach it)
VARIANTS = ((True, False), (False, False), (True, True), (False, True))
_VNOTE = {(True, False): "the chunks around generated first",
          (False, False): "the chunks around generated after it",
          (True, True): "the chunks around generated first, this chunk saved and loaded again before",
          (False, True): "this chunk saved and loaded again before, the chunks around generated after"}


def chest_list(seed, mc, cx, cz):
    """[(x, y, z, {variant: loot seed})] of the minecarts of the start of chunk (cx, cz)
    (variants: VARIANTS; main = the first, the 8 neighbours decorated first, as the
    strongholds)."""
    k = (int(seed), int(mc), cx, cz)
    if k in _CACHE:
        return _CACHE[k]
    ps = pieces(seed, mc, cx, cz)
    chunks = sorted({(x, z) for b in ps if b[6] == "corridor"
                     for x in range(b[0] >> 4, (b[3] >> 4) + 1)
                     for z in range(b[2] >> 4, (b[5] >> 4) + 1)})
    if int(mc) not in OLD and chunks:
        import prefeatures118 as pf
        pf.prefetch_terrain(seed, int(mc), sorted({(x + a, z + b) for x, z in chunks
                                                   for a in range(-2, 3) for b in range(-2, 3)}))
    out = {}
    tried = {}
    for ch in chunks:
        # every corridor chunk every way: a cave-spider spawner placed by a neighbour
        # decorated first skips a draw, the order of two starts changes the draws
        multi = False
        for v in VARIANTS:
            if v[1] and not multi:
                continue
            got, m = _chunk_chests(seed, mc, ch[0], ch[1], v[0], v[1])
            multi = multi or m
            tried.setdefault(ch, []).append(v)
            for c in got:
                if c[4] == (cx, cz):
                    out.setdefault((c[0], c[1], c[2]), {})[v] = c[3]
    res = [(p[0], p[1], p[2], d, tuple(tried[(p[0] >> 4, p[2] >> 4)])) for p, d in sorted(out.items())]
    if len(_CACHE) > 64:
        _CACHE.clear()
    _CACHE[k] = res
    return res


def _biome_fn(seed, mc, ver):
    """(06/11) the biome at a minecart (26.2+: a music disc only in Sulfur Caves —
    location_check on the loot's origin, the game's 1:1 biome)"""
    import loot_engine as le
    tab = le.get_table(ver, "abandoned_mineshaft") if ver else None
    if tab is None or "location_check" not in str(tab):
        return lambda X, Y, Z: None
    try:
        import cubiomes_wrap as cw
        g = cw.CubiomesLib()
        g.setup_and_seed(int(mc), int(seed), large_biomes=False)
    except Exception:                                    # noqa: BLE001
        return lambda X, Y, Z: None

    def f(X, Y, Z):
        try:
            b = g.get_biome_at(int(X), int(Z), 1, int(Y))
            return {"biome": cw.BIOME_NAMES.get(b, str(b)).lower().replace(" ", "_")}
        except Exception:                                # noqa: BLE001
            return None
    return f


def chests(seed, mc, x, z, ver):
    """Loot cards of the mineshaft started in the chunk of (x, z)."""
    import loot_engine as le
    cx, cz = int(x) >> 4, int(z) >> 4
    lst = chest_list(seed, mc, cx, cz)
    out = []
    n = 0
    world_of = _biome_fn(seed, mc, ver)
    for (X, Y, Z, d, tried) in lst:
        n += 1
        s = d.get(VARIANTS[0])
        others = {}
        for v in VARIANTS[1:]:
            if v in d and d[v] != s:
                others.setdefault(d[v], v)
        missing = [v for v in tried if v not in d]
        if s is not None:
            slots, approx = le.fill_chest("abandoned_mineshaft", s, ver, world=world_of(X, Y, Z))
            note = "A chest minecart on a rail, in a corridor (water flowing through can push it " \
                   "a few blocks)."
            if others:
                note = ("A chest minecart on a rail. The loot depends on the order the chunks were "
                        "generated: see the other card(s).")
            if missing:
                note += " Not there if " + " / ".join(_VNOTE[v] for v in missing) + "."
            out.append(dict(name=f"Minecart chest {n}", pos=(X, Y, Z), table="chests/abandoned_mineshaft",
                            seed=s, slots=slots, approx=approx, note=note,
                            cond=bool(missing)))
        for o, v in others.items():
            slots, approx = le.fill_chest("abandoned_mineshaft", o, ver, world=world_of(X, Y, Z))
            out.append(dict(name=f"Minecart chest {n} — if {_VNOTE[v]}", pos=(X, Y, Z),
                            table="chests/abandoned_mineshaft", seed=o, slots=slots, approx=approx,
                            note=None if s is not None else "Only there in that generation order.",
                            cond=True))
    if not out:
        return [dict(name="No chest", pos=(cx * 16, None, cz * 16), table="", seed=None, slots=None,
                     approx=[], note="No chest minecart in this mineshaft (its corridors draw "
                                     "one in about 50 sections).")]
    return out
