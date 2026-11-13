"""
dungeon_loot.py — (30/10) the dungeons (monster rooms) and their chests, 1.16.1 → 26.3.

A dungeon is a feature, not a structure: an attempt becomes a dungeon only where its
room meets a cave (1 to 5 openings, solid floor and ceiling), so the terrain of the
chunk decides, and so do the features placed before it (lakes, geodes, mineshafts,
the dungeons of the chunks decorated earlier: they reach into it).

  1.16 / 1.17   libcubiomes ssa_t116_dungeons (exact 1.16 terrain; 1.17: its carvers,
                not its geodes): the chunk's lakes, mineshafts and dungeon attempts.
  1.18 → 26.3   prefeatures118 (terrain.chunk_underground, geodes, mineshafts, the
                monster_room / monster_room_deep attempts).

Chests: MonsterRoomFeature tries 2 chests × 3 spots (an air block with exactly one
solid wall next to it); a chest placed draws its LootTableSeed (nextLong) from the
feature's random, after the moss of the floor (nextInt(4) per floor block: the
terrain again). Then the mob: nextInt(4) → skeleton, zombie, zombie, spider.

The generation order matters (a neighbour decorated first can change the dungeons
of a chunk). Two variants per chunk: its 8 neighbours decorated first (main, the
best on the servers) or the chunk alone.
"""
from __future__ import annotations

import ctypes as ct

MOBS = ("Skeleton", "Zombie", "Zombie", "Spider")
OLD = (19, 20, 21)
NB_MASK = 0x1EF                  # the 8 neighbours (structure_loot.T116_NB_MASK)


def _s64(seed):
    return ct.c_uint64(int(seed) & ((1 << 64) - 1))


def _signed(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


_CACHE = {}


def chunk_dungeons(seed, mc, cx, cz, neighbours=True, bands=False):
    """[(x, y, z, half x, half z, mob, [(chest x, y, z, loot seed)])] of the dungeons
    chunk (cx, cz) places (None: not available). bands (31/10, 1.18+, area): the
    terrain computed only in the 8-block layers that are read (same result)."""
    seed, mc = int(seed), int(mc)
    k = (seed, mc, cx, cz, bool(neighbours))
    if k in _CACHE:
        return _CACHE[k]
    if mc in OLD:
        from cubiomes_wrap import get_lib
        f = getattr(get_lib()._lib, "ssa_t116_dungeons", None)
        if f is None:                                 # library not rebuilt
            return None
        f.argtypes = [ct.c_int, ct.c_uint64, ct.c_int32, ct.c_int32, ct.c_int,
                      ct.POINTER(ct.c_int64), ct.c_int]
        f.restype = ct.c_int
        buf = (ct.c_int64 * (16 * 16))()
        n = f(mc, _s64(seed), cx, cz, NB_MASK if neighbours else 0, buf, 16)
        if n < 0:
            return None
        out = []
        for i in range(min(n, 16)):
            r = buf[16 * i:16 * i + 16]
            ch = [tuple(r[7 + 4 * j:10 + 4 * j]) + (r[10 + 4 * j],) for j in range(r[6])]
            out.append((r[0], r[1], r[2], r[3], r[4], r[5], ch))
    elif 22 <= mc <= 35:
        import prefeatures118 as pf
        import mineshaft118 as ms
        try:
            if not bands:
                pf.prefetch_terrain(seed, mc, [(cx + a, cz + b) for a in range(-2, 3) for b in range(-2, 3)])
            ms.SPIDER_DONE.clear()
            R = pf.Region(seed, mc, [], bands=bands and not neighbours and pf.USE_C)
            if neighbours:
                for dz in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx or dz:
                            pf._decorate(R, seed, mc, cx + dx, cz + dz)
            pf._geodes(R, seed, mc, cx, cz)
            pf._mineshafts(R, seed, mc, cx, cz)
            pf.DCAP = cap = []
            try:
                pf._dungeons(R, seed, mc, cx, cz)
            finally:
                pf.DCAP = None
        except Exception:
            return None
        out = [(x, y, z, j, o, mob, [(a, b, c, _signed(s)) for a, b, c, s in chs])
               for x, y, z, j, o, mob, chs in cap]
    else:
        return None
    if len(_CACHE) > 512:
        _CACHE.clear()
    _CACHE[k] = out
    return out


def find(seed, mc, x, y, z):
    """The dungeon whose spawner is at (x, y, z) (y None: any) in both variants:
    {variant (neighbours first?): dungeon or None}."""
    out = {}
    for nb in (True, False):
        ds = chunk_dungeons(seed, mc, int(x) >> 4, int(z) >> 4, nb) or []
        out[nb] = next((d for d in ds if d[0] == x and d[2] == z and (y is None or d[1] == y)), None)
    return out


def chests(seed, mc, x, z, ver, y=None):
    """Loot cards of the dungeon whose spawner is at (x, [y,] z)."""
    import loot_engine as le
    v = find(seed, mc, int(x), y, int(z))
    main, alone = v[True], v[False]
    d = main or alone
    if d is None:
        return [dict(name="No dungeon", pos=(int(x), y, int(z)), table="", seed=None, slots=None,
                     approx=[], note="No dungeon at this spawner in either generation order "
                                     "(or loot not available for this version / library).")]
    out = []
    mob = MOBS[d[5] & 3]
    room = f"{2 * d[3] + 1}×{2 * d[4] + 1}"
    if not d[6]:
        out.append(dict(name="No chest", pos=(d[0], d[1], d[2]), table="", seed=None, slots=None,
                        approx=[], note=f"{mob} spawner, room {room}: no chest found a place "
                                        "(an air block with exactly one wall next to it)."))
    for i, (cxp, cyp, czp, s) in enumerate(d[6]):
        slots, approx = le.fill_chest("simple_dungeon", s, ver)
        note = f"{mob} spawner, room {room}."
        if main is None:
            note += (" Only if this chunk is generated before the chunks around it "
                     "(else this dungeon is not there).")
        elif alone is not None and alone != main:
            note += " If this chunk is generated before the chunks around it: see the other card."
        out.append(dict(name=f"Dungeon chest {i + 1}", pos=(cxp, cyp, czp), table="chests/simple_dungeon",
                        seed=s, slots=slots, approx=approx, note=note))
    if main is not None and alone is not None and alone != main:
        for i, (cxp, cyp, czp, s) in enumerate(alone[6]):
            slots, approx = le.fill_chest("simple_dungeon", s, ver)
            out.append(dict(name=f"Dungeon chest {i + 1} — if this chunk was generated first",
                            pos=(cxp, cyp, czp), table="chests/simple_dungeon", seed=s, slots=slots,
                            approx=approx, note=None, exact=False))
    if main is None:
        for c in out:
            c["exact"] = False
    return out


def area(seed, mc, x1, z1, x2, z2):
    """(1.18+) the dungeons of the chunks of a block box, each chunk decorated alone
    (the map: as the 1.16 one): [(x, y, z, j, o, mob, chests)]."""
    seed, mc = int(seed), int(mc)
    if not 22 <= mc <= 35:
        return []
    import prefeatures118 as pf
    chunks = [(cx, cz) for cz in range(z1 >> 4, (z2 >> 4) + 1) for cx in range(x1 >> 4, (x2 >> 4) + 1)]
    bands = BANDS and _bands_ok()
    if bands:                         # (31/10) only the layers the attempts read, in threads
        pf.prefetch_bands(seed, mc, _area_needs(seed, mc, chunks))
    else:
        pf.prefetch_terrain(seed, mc, [(cx, cz) for cz in range((z1 >> 4) - 2, (z2 >> 4) + 3)
                                       for cx in range((x1 >> 4) - 2, (x2 >> 4) + 3)])   # + what geodes reach
    out = []
    for cx, cz in chunks:
        ds = chunk_dungeons(seed, mc, cx, cz, False, bands)
        for d in ds or ():
            if x1 <= d[0] <= x2 and z1 <= d[2] <= z2:
                out.append(d)
    return out


BANDS = True


def _bands_ok():
    try:
        import terrain
        import prefeatures118 as pf
        terrain._load()
        return pf.USE_C and all(hasattr(terrain._LIB, n) for n in ("cv_chunk_bands", "cv_dungeons118b"))
    except Exception:
        return False


def _area_needs(seed, mc, chunks):
    """{(cx, cz): 8-block layers} the chunks of `chunks` decorated alone read: the
    layers of their dungeon attempts (if they fail), whole chunks where a mineshaft
    piece or a geode is."""
    import prefeatures118 as pf
    import mineshaft118 as ms
    import structure_loot as sl
    ALL = pf.ALL_BANDS
    needs = {}
    bio, deep = ms.biome_fns(seed, mc)
    h = ms._height_fn(seed, mc)
    for cx, cz in chunks:
        for k, m in pf.dungeon_needs(seed, cx, cz).items():
            needs[k] = needs.get(k, 0) | m
        if ms._reaching(seed, mc, cx, cz, bio, h)[3]:
            needs[(cx, cz)] = ALL
        r = sl.structure_rng(seed, mc, cx, cz, pf._GEODE_INDEX, 2)
        if r.next_float() < 0.0416666679084301:            # a geode: its 33³ cube
            x = pf._nint(r, 16) + (cx << 4)
            z = pf._nint(r, 16) + (cz << 4)
            for X in range((x - 16) >> 4, ((x + 16) >> 4) + 1):
                for Z in range((z - 16) >> 4, ((z + 16) >> 4) + 1):
                    needs[(X, Z)] = ALL
    return needs
