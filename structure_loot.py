"""
structure_loot.py — the chests of a structure and their loot (25/10).

For each supported structure: where its chests are, which loot table, and the
« loot table seed » the game gives them at generation (createChest:
random.nextLong() of the structure's decoration RNG in the chest's chunk):
    decoration seed = setDecorationSeed(world seed, chunk min x, chunk min z)
    the structure's RNG = setFeatureSeed(decoration seed, index, step)
        index = the structure's rank among the structures of its step:
        1.16 – 1.18.2  StructureFeature registration order
        1.19.0–1.19.2  Structures bootstrap order (mineshafts first)
        1.19.3 +       the data pack, sorted by id (alphabetical)
    RNG family: java.util.Random up to 1.17, Xoroshiro128++ from 1.18.
The loot itself: loot_engine.fill_chest.

Supported: buried treasure; strongholds (corridor, crossing and library chests,
through the piece simulation validated for the eyes: stronghold_eyes); desert
pyramid, jungle temple (+ dispensers) and igloo (scattered_loot.py).
"""
from __future__ import annotations

import loot_engine as le
import stronghold_eyes as se

UNDERGROUND_STRUCTURES = 3


def _treasure_index(mc: int) -> int:
    if mc <= 21:        # 1.16 – 1.17: mineshaft, buried_treasure
        return 1
    if mc <= 23:        # 1.18.2 (checked on its server) – 1.19.2: mineshaft, mineshaft_mesa, buried_treasure
        return 2
    return 0            # 1.19.3+: alphabetical, buried_treasure first


def structure_rng(seed: int, mc: int, cx: int, cz: int, index: int, step: int):
    """The structure's decoration RNG in chunk (cx, cz), as setFeatureSeed leaves it."""
    if mc < 22:                                   # 1.16 – 1.17: LCG
        deco = se._lcg_get_decoration_seed(seed, cx, cz)
        return se.JavaRandom((deco + index + 10000 * step) & ((1 << 64) - 1))
    deco = se._xrsr_get_decoration_seed(int(seed) & ((1 << 64) - 1), cx, cz)
    return se.Xrsr128((deco + index + 10000 * step) & ((1 << 64) - 1))


def _next_long(r) -> int:
    return r.nextLong() if hasattr(r, "nextLong") else r.next_long()


SUPPORTED = {"treasure", "buried_treasure", "stronghold", "first_stronghold",
             "desert_pyramid", "jungle_temple", "igloo",            # (24/10) scattered_loot
             "shipwreck", "ocean_ruin", "village", "pillager_outpost",
             "mineshaft",                                           # (29/10) mineshaft_loot
             "ruined_portal",                                       # (30/10) ruined_portal_loot
             "end_city", "end_city_ship",                           # (30/10) end_city_loot
             "mansion",                                             # (30/10) mansion_loot
             "fortress",                                            # (30/10) fortress_loot
             "bastion",                                             # (30/10) bastion_loot
             "ancient_city",                                        # (30/10) ancient_city_loot
             "abandoned_camp",                                      # (30/10) 26.3, jigsaw.py
             "dungeon"}                                             # (30/10) dungeon_loot


def chests(key: str, seed: int, mc: int, x: int, z: int, sub: str | None = None, fast=False):
    """[dict(name, pos (x, y|None, z), table, seed, slots, approx, note)] for the
    structure `key` at (x, z), or None if not supported.
    fast (07/11, the loot finder): skip what only gives the chests' heights or
    whether a chest survives the terrain (buried treasure, shipwreck, ruined
    portal) — the same chests and contents, possibly a chest the terrain removes:
    the finder checks its matches again without it."""
    seed = int(seed)
    mc = int(mc)
    ver = le.loot_version(mc, sub)
    if ver is None:
        return None
    if key in ("treasure", "buried_treasure"):
        cx, cz = x >> 4, z >> 4
        r = structure_rng(seed, mc, cx, cz, _treasure_index(mc), UNDERGROUND_STRUCTURES)
        ls = _next_long(r)
        slots, approx = le.fill_chest("buried_treasure", ls, ver)
        cy = gy = None
        try:                                    # (31/10) its Y (1.16 / 1.17), the ground's
            import scattered_loot as sc
            if not fast:
                cy, gy = sc.treasure_y(seed, mc, cx * 16 + 9, cz * 16 + 9)
        except Exception:
            pass
        note = ("Rarely the treasure finds no place and is missing." if cy is not None else
                "The chest is under the sand at this X Z (the /tp puts you on the ground above "
                "it: dig down). Rarely the treasure finds no place and is missing.")
        return [dict(name="Buried treasure", pos=(cx * 16 + 9, cy, cz * 16 + 9),
                     tp_y=None if cy is not None or gy is None else gy,
                     table="chests/buried_treasure", seed=ls, slots=slots, approx=approx,
                     note=note)]
    if key in ("stronghold", "first_stronghold"):
        return _stronghold_chests(seed, mc, x, z, ver)
    if key == "dungeon":                               # (30/10) x z = the spawner's
        import dungeon_loot as dl
        return dl.chests(seed, mc, int(x), int(z), ver)
    if key == "abandoned_camp":                        # (30/10) 26.3 camps: jigsaw.py
        import jigsaw
        got = jigsaw.chests(seed, mc, key, int(x), int(z), ver, fast=fast) if mc >= 35 else None
        return got or [dict(name="Not available", pos=(int(x), None, int(z)), table="", seed=None,
                            slots=None, approx=[], note="Abandoned camps exist from Java 26.3.")]
    if key in ("village", "pillager_outpost"):         # (24/10) jigsaw.py, 1.16.1 → 26.3 (29/10: 1.16 / 1.17)
        import jigsaw
        got = jigsaw.chests(seed, mc, key, int(x), int(z), ver, fast=fast)
        if got is None:
            return [dict(name="Not available", pos=(int(x), None, int(z)), table="", seed=None,
                         slots=None, approx=[],
                         note="The village / outpost layout is computed for Java 1.16.1 → 26.3 "
                              "(its pieces depend on the exact terrain).")]
        return got
    if key == "mineshaft":                             # (29/10) chest minecarts
        import mineshaft_loot as ml
        return ml.chests(seed, mc, int(x), int(z), ver)
    if key == "ancient_city":                          # (30/10) jigsaw pieces' chests
        import ancient_city_loot as acl
        return acl.chests(seed, mc, int(x), int(z), ver)
    if key == "bastion":                               # (30/10) jigsaw pieces' chests
        import bastion_loot as bl
        return bl.chests(seed, mc, int(x), int(z), ver)
    if key == "fortress":                              # (30/10) the castle corridors' chests
        import fortress_loot as fl
        return fl.chests(seed, mc, int(x), int(z), ver)
    if key == "mansion":                               # (30/10) the rooms' chests
        import mansion_loot as mnl
        return mnl.chests(seed, mc, int(x), int(z), ver)
    if key in ("end_city", "end_city_ship"):          # (30/10) towers, loot room, ship
        import end_city_loot as ecl
        return ecl.chests(seed, mc, int(x), int(z), ver)
    if key == "ruined_portal":                         # (30/10) the portal's chest
        import ruined_portal_loot as rpl
        try:
            return rpl.chests(seed, mc, int(x), int(z), ver, fast=fast)
        except Exception:
            return [dict(name="Not available", pos=(int(x), None, int(z)), table="", seed=None,
                         slots=None, approx=[],
                         note="The ruined portal's height needs the terrain library "
                              "(rebuild it: py tools/build_native.py --cubiomes).")]
    if key in ("shipwreck", "desert_pyramid", "jungle_temple", "igloo", "ocean_ruin") and fast:
        import scattered_loot as sc                    # (07/11) without the Y / the terrain
        return {"shipwreck": sc.shipwreck, "desert_pyramid": sc.desert_pyramid,
                "jungle_temple": sc.jungle_temple, "igloo": sc.igloo,
                "ocean_ruin": sc.ocean_ruin}[key](
                    seed, mc, int(x), int(z), ver, fast=True)
    if key in ("desert_pyramid", "jungle_temple", "igloo", "shipwreck", "ocean_ruin"):
        import scattered_loot as sc
        return {"desert_pyramid": sc.desert_pyramid, "jungle_temple": sc.jungle_temple,
                "igloo": sc.igloo, "shipwreck": sc.shipwreck,
                "ocean_ruin": sc.ocean_ruin}[key](seed, mc, int(x), int(z), ver)
    return None


_T116_CACHE = {}


_CV_CACHE = {}


# (24/10) 1.18+: the mineshafts (UNDERGROUND_STRUCTURES) carve and fill before the
# stronghold: mineshaft118 (layouts 251/251 and blocks 58/60 chunks as the servers)
MINESHAFTS_118 = True


def _air_test_118(seed, mc, cx, cz, dy, pieces):
    """1.18 → 26.2: the chunk's underground after the carvers (terrain.cave118),
    the stronghold pieces burying the terrain around them (beardifier)."""
    key = (seed, mc, cx, cz, NEIGHBOURS_FIRST)
    blk = _CV_CACHE.get(key)
    if blk is None:
        try:
            import terrain
        except Exception:
            return None
        x0, z0 = cx << 4, cz << 4
        boxes = [(b.minX, b.minY + dy, b.minZ, b.maxX, b.maxY + dy, b.maxZ)
                 for b in (q.bbox for q in pieces)
                 if b.maxX >= x0 - 12 and b.minX <= x0 + 27
                 and b.maxZ >= z0 - 12 and b.minZ <= z0 + 27]       # isCloseToChunk(12)
        blk = None
        if MINESHAFTS_118:
            try:                                 # (24/10) mineshafts and dungeons come before
                import prefeatures118
                allb = [(b.minX, b.minY + dy, b.minZ, b.maxX, b.maxY + dy, b.maxZ)
                        for b in (q.bbox for q in pieces)]
                blk = prefeatures118.blocks(seed, mc, cx, cz, allb, NEIGHBOURS_FIRST)
            except Exception:
                blk = None
        if blk is None:
            blk = terrain.chunk_underground(seed, mc, cx, cz, boxes)
        if blk is None:
            return None
        if len(_CV_CACHE) > 64:
            _CV_CACHE.clear()
        _CV_CACHE[key] = blk
    x0, z0 = cx << 4, cz << 4

    def air(wx, wy, wz, blk=blk):
        y = wy + dy
        if not -64 <= y < 320:
            return True
        return blk[(((y + 64) << 4 | (wz - z0)) << 4) | (wx - x0)] == 1   # CV_AIR
    return air


# the neighbouring chunks decorated before the stronghold's chunk (their lakes,
# dungeons, geodes reach into it); False: the chunk alone (the other order)
NEIGHBOURS_FIRST = True

# 1.16: the neighbours decorated before the chunk (bit k = (dz+1)*3 + (dx+1));
# 0: the chunk alone. (24/10) all 8 first: their lakes / dungeons reach into the
# chunk before its stronghold (1.16.1 server, 16 strongholds: 142 / 143 chests
# and every eye right, against 139 / 143 for the chunk alone)
T116_NB_MASK = 0x1EF


def _air_test(seed, mc, cx, cz, dy, pieces=()):
    """(wx, wy, wz) -> air before the stronghold, in chunk (cx, cz); wy is the
    piece's Y before moveBelowSeaLevel (+ dy). None: no terrain for this version."""
    if 22 <= mc <= 35:                           # 1.18 → 26.3 (26.3: same terrain, checked)
        return _air_test_118(seed, mc, cx, cz, dy, pieces)
    if mc not in (19, 20, 21):                   # the exact terrain: 1.16
        return None
    # (24/10) 1.17: the 1.16 terrain and features up to the dungeons, with the
    # 1.17 carvers (floor -0.7f, the canyons' float providers); not the geodes
    tmc = mc
    mask = T116_NB_MASK if NEIGHBOURS_FIRST else 0
    key = (seed, mc, cx, cz, mask)
    blk = _T116_CACHE.get(key)
    if blk is None:
        import ctypes as ct
        from cubiomes_wrap import get_lib
        buf = (ct.c_uint8 * 65536)()
        s64 = ct.c_uint64(seed & ((1 << 64) - 1))
        f = getattr(get_lib()._lib, "ssa_t116_blocks_nb", None) if mask else None
        if f is not None:                    # (a library not rebuilt: the chunk alone)
            f.argtypes = [ct.c_int, ct.c_uint64, ct.c_int32, ct.c_int32, ct.c_int,
                          ct.POINTER(ct.c_uint8)]
            f.restype = ct.c_int
            ok = f(tmc, s64, cx, cz, mask, buf)
        else:
            f = getattr(get_lib()._lib, "ssa_t116_blocks", None)
            if f is None:
                return None
            f.argtypes = [ct.c_int, ct.c_uint64, ct.c_int32, ct.c_int32, ct.POINTER(ct.c_uint8)]
            f.restype = ct.c_int
            ok = f(tmc, s64, cx, cz, buf)
        if not ok:
            return None
        blk = bytes(buf)
        if mc == 21:
            try:                                 # (24/10) 1.17: the amethyst geodes
                import prefeatures118
                if prefeatures118.GEODES_117:
                    blk = prefeatures118.geodes_117(seed, mc, cx, cz, blk, NEIGHBOURS_FIRST)
            except Exception:
                pass
        if len(_T116_CACHE) > 64:
            _T116_CACHE.clear()
        _T116_CACHE[key] = blk
    x0, z0 = cx << 4, cz << 4

    def air(wx, wy, wz, blk=blk):
        y = wy + dy
        if not 0 <= y < 256:
            return True
        b = blk[(((y << 4) | (wz - z0)) << 4) | (wx - x0)]
        return b == 0 or b == 25                 # T116_AIR, T116_CAVE_AIR
    return air


def _stronghold_chests(seed, mc, x, z, ver):
    """Every chest of the stronghold at (x, z): for each, the pieces of ITS chunk
    are replayed in order (their RNG draws, as for the eyes) up to that chest."""
    # the WHOLE stronghold (the game generates every piece; the eye generator
    # stops at the portal room)
    gen, _rng = se._generate_full_bfs(seed, x >> 4, z >> 4, se._use_old_filler(mc))
    dy = 0
    try:
        pf = se.get_stronghold_portal_full(seed, x, z, mc)
        if pf is not None:
            dy = int(pf.get("offset", 0))
    except Exception:
        pass
    want = []                       # (piece, lx, ly, lz, table, name)
    for p in gen.pieces:
        if p.type == se.CHEST_CORRIDOR:
            want.append((p, 3, 2, 3, "stronghold_corridor", "Corridor chest"))
        elif p.type == se.ROOM_CROSSING and p.additionalData == 2:
            want.append((p, 3, 4, 8, "stronghold_crossing", "Crossing (storeroom) chest"))
        elif p.type == se.LIBRARY:
            want.append((p, 3, 3, 5, "stronghold_library", "Library chest"))
            if (p.bbox.maxY - p.bbox.minY) > 6:
                want.append((p, 12, 8, 1, "stronghold_library", "Library chest (upper floor)"))
    out = []
    for p, lx, ly, lz, table, name in want:
        wx, wy, wz = p.get_world_x(lx, lz), p.get_world_y(ly), p.get_world_z(lx, lz)
        global NEIGHBOURS_FIRST
        ls, note = _stronghold_chest_seed(seed, mc, gen, dy, p, lx, ly, lz)
        if ls is None:
            continue
        alt = None
        if note is None:
            # (24/10) the other decoration order: the chunk before its neighbours
            NEIGHBOURS_FIRST = False
            try:
                alt, _n = _stronghold_chest_seed(seed, mc, gen, dy, p, lx, ly, lz)
            finally:
                NEIGHBOURS_FIRST = True
        slots, approx = le.fill_chest(table, ls, ver)
        if note:
            approx = list(approx) + [note]
        out.append(dict(name=name, pos=(wx, wy + dy, wz), table=f"chests/{table}", seed=ls,
                        slots=slots, approx=approx, note=None))
        if alt is not None and alt != ls:
            slots2, approx2 = le.fill_chest(table, alt, ver)
            out.append(dict(name=name + " — if the neighbouring chunks were generated after it",
                            pos=(wx, wy + dy, wz), table=f"chests/{table}", seed=alt,
                            slots=slots2, approx=approx2,
                            note="a dungeon, geode or lake of a neighbouring chunk reaches this "
                                 "chest's chunk: its contents depend on which was generated first"))
    return out


def _stronghold_chest_seed(seed, mc, gen, dy, p, lx, ly, lz):
    """(LootTableSeed, note) of the chest (lx, ly, lz) of piece p: the pieces of
    its chunk replayed in order up to it, the walls skipping the air there."""
    orig = se._create_chest_rng
    if True:
        wx, wz = p.get_world_x(lx, lz), p.get_world_z(lx, lz)
        cx, cz = wx >> 4, wz >> 4
        rng = se._eye_rng_for_chunk(seed, cx, cz, mc)
        bb = se.BoundingBox(cx << 4, -32768, cz << 4, (cx << 4) + 15, 32767, (cz << 4) + 15)
        got = {}
        note = None
        air = _air_test(seed, mc, cx, cz, dy, gen.pieces)
        if air is None:
            note = ("no cave data for this version yet: exact only if no cave crosses "
                    "the stronghold's walls in this chunk")
        se.AIR_TEST = air

        def rec(r, piece, chunkBB, a, b, c, _got=got):
            if piece is p and (a, b, c) == (lx, ly, lz):
                _got["seed"] = r.next_long()
                raise StopIteration
            orig(r, piece, chunkBB, a, b, c)
        se._create_chest_rng = rec
        try:
            for q in gen.pieces:
                if not q.bbox.intersects(bb):
                    continue
                if q is gen.portal_room_piece:
                    # the portal room before this chest in its chunk: its boxes, the
                    # 12 eyes; the portal and the silverfish spawner draw nothing more
                    se._simulate_portal_room_prefix(rng, q, bb)
                    for _ in range(12):
                        rng.next_float()
                    continue
                se._simulate_piece_postprocess(rng, q, bb)
        except StopIteration:
            pass
        finally:
            se._create_chest_rng = orig
            se.AIR_TEST = None
        return got.get("seed"), note
