"""
ruined_portal_loot.py — (30/10) ruined portals: layout (template, rotation, mirror,
exact Y) and the loot of their chest, 1.16.1 → 26.3.

Layout (the structure start random, setLargeFeatureSeed):
  setup (weights) → air pocket → giant (5 %) / portal_1..10 → rotation → mirror
  (FRONT_BACK half the time) → Y: findSuitableY from the base height at the centre of
  the template box (WORLD_SURFACE_WG, OCEAN_FLOOR_WG on the sea floor), then down to
  where 3 of the 4 bottom corners stand on a block of that heightmap (noise terrain,
  no carvers).
Type: 1.16 / 1.17 the biome of the chunk; 1.18.2+ the structure set tries the 7 ruined
  portals in a weighted random order (WorldgenRandom setLargeFeatureSeed), the first
  whose biome tag holds the biome at its start (chunk corner, at its Y) wins.
Chest: the template's chest (loot table chests/ruined_portal). The whole portal is
  placed by the chunk holding the centre of its box (1.16: the template
  position, i.e. the start chunk): LootTableSeed = the first nextLong
  of that chunk's random for this structure (SURFACE_STRUCTURES).
"""
from __future__ import annotations

import gzip
import json
import os
import struct

import mineshaft118 as ms

_DATA = None
TYPES = ("standard", "desert", "jungle", "swamp", "mountain", "ocean", "nether")
_STRUCT = {"standard": "ruined_portal", "desert": "ruined_portal_desert",
           "jungle": "ruined_portal_jungle", "swamp": "ruined_portal_swamp",
           "mountain": "ruined_portal_mountain", "ocean": "ruined_portal_ocean",
           "nether": "ruined_portal_nether"}
UNDERGROUND, ON_LAND, PARTLY_BURIED, OCEAN_FLOOR, IN_MOUNTAIN, IN_NETHER = (
    "underground", "on_land_surface", "partly_buried", "on_ocean_floor", "in_mountain", "in_nether")
SETUPS = {"standard": ((UNDERGROUND, 0.5, 1.0), (ON_LAND, 0.5, 0.5)),
          "desert": ((PARTLY_BURIED, 1.0, 0.0),),
          "jungle": ((ON_LAND, 1.0, 0.5),),
          "swamp": ((OCEAN_FLOOR, 1.0, 0.0),),
          "mountain": ((IN_MOUNTAIN, 0.5, 1.0), (ON_LAND, 0.5, 0.5)),
          "ocean": ((OCEAN_FLOOR, 1.0, 0.0),),
          "nether": ((IN_NETHER, 1.0, 0.5),)}
ROT = ("NONE", "CLOCKWISE_90", "CLOCKWISE_180", "COUNTERCLOCKWISE_90")


def _f32(v):
    return struct.unpack("f", struct.pack("f", v))[0]


def data():
    global _DATA
    if _DATA is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "ruined_portal.json.gz")
        with gzip.open(p, "rt") as f:
            _DATA = json.load(f)
    return _DATA


def transform(x, z, mirror, rot, px, pz):
    """StructureTemplate.transform (x, z) with mirror (FRONT_BACK: x = -x), rotation,
    pivot (px, pz)."""
    if mirror:
        x = -x
    if rot == 1:
        return px + pz - z, pz - px + x
    if rot == 2:
        return px + px - x, pz + pz - z
    if rot == 3:
        return px - pz + z, px + pz - x
    return x, z


def _box(size, ox, oy, oz, rot, mirror):
    px, pz = size[0] // 2, size[2] // 2
    a = transform(0, 0, mirror, rot, px, pz)
    b = transform(size[0] - 1, size[2] - 1, mirror, rot, px, pz)
    return (ox + min(a[0], b[0]), oy, oz + min(a[1], b[1]),
            ox + max(a[0], b[0]), oy + size[1] - 1, oz + max(a[1], b[1]))


def _center(bb):
    return (bb[0] + (bb[3] - bb[0] + 1) // 2, bb[1] + (bb[4] - bb[1] + 1) // 2,
            bb[2] + (bb[5] - bb[2] + 1) // 2)


# ── terrain columns (noise, no carvers) ──
class _Cols:
    def __init__(self, seed, mc):
        self.seed, self.mc, self.c = int(seed), int(mc), {}

    def _chunk(self, cx, cz):
        k = (cx, cz)
        v = self.c.get(k)
        if v is None:
            if self.mc >= 22:
                import terrain
                v = terrain.chunk_underground_ex(self.seed, self.mc, cx, cz, (), (), flags=3)
            else:
                v = _t116_noise(self.seed, self.mc, cx, cz)
            if v is None:
                raise RuntimeError("no terrain")
            self.c[k] = v
        return v

    def at(self, x, y, z):
        """0 solid, 1 air, 2 water, 3 lava."""
        if self.mc >= 22:
            if not -64 <= y < 320:
                return 1
            return self._chunk(x >> 4, z >> 4)[(y + 64) * 256 + (z & 15) * 16 + (x & 15)]
        if not 0 <= y < 256:
            return 1
        return self._chunk(x >> 4, z >> 4)[(((y << 4) | (z & 15)) << 4) | (x & 15)]

    def base_height(self, x, z, ocean_floor):
        top = 319 if self.mc >= 22 else 255
        low = -64 if self.mc >= 22 else 0
        for y in range(top, low - 1, -1):
            v = self.at(x, y, z)
            if (v == 0) if ocean_floor else (v != 1):
                return y + 1
        return low


def _t116_noise(seed, mc, cx, cz):
    """1.16 / 1.17 noise chunk (libcubiomes ssa_t116_noise_chunk), as T116 codes →
    0 solid, 1 air, 2 water, 3 lava."""
    import ctypes as ct
    from cubiomes_wrap import get_lib
    L = get_lib()._lib
    if not hasattr(L, "ssa_t116_noise_chunk"):
        return None
    f = L.ssa_t116_noise_chunk
    f.restype = ct.c_int
    buf = (ct.c_uint8 * 65536)()
    if not f(int(mc), ct.c_uint64(int(seed) & ((1 << 64) - 1)), cx, cz, buf):
        return None
    lut = bytes(_T116_LUT)
    return bytes(buf).translate(lut)


_T116_LUT = [0] * 256
_T116_LUT[0] = _T116_LUT[25] = 1          # air, cave air
_T116_LUT[2] = 2
_T116_LUT[3] = 3


def _rand_between(r, a, b):
    return r.next_int(b - a + 1) + a


def _within(r, a, b):
    return _rand_between(r, a, b) if a < b else b


def _find_y(r, cols, vp, air, surface, yspan, bb, min_y):
    if vp == IN_NETHER:
        if air:
            ny = _rand_between(r, 32, 100)
        elif r.next_float() < 0.5:
            ny = _rand_between(r, 27, 29)
        else:
            ny = _rand_between(r, 29, 100)
    elif vp == IN_MOUNTAIN:
        ny = _within(r, 70, surface - yspan)
    elif vp == UNDERGROUND:
        ny = _within(r, min_y, surface - yspan)
    elif vp == PARTLY_BURIED:
        ny = surface - yspan + _rand_between(r, 2, 8)
    else:
        ny = surface
    corners = ((bb[0], bb[2]), (bb[3], bb[2]), (bb[0], bb[5]), (bb[3], bb[5]))
    floor = vp == OCEAN_FLOOR
    y = ny
    while y > min_y:
        n = 0
        hit = False
        for (x, z) in corners:
            v = cols.at(x, y, z)
            if (v == 0) if floor else (v != 1):
                n += 1
                if n == 3:
                    hit = True
                    break
        if hit:
            break
        y -= 1
    return y


def _prefix(seed, mc, cx, cz, kind):
    """gen's draws before the terrain: (random, placement, air, template, rotation,
    mirror, size, chest, box at y 0)"""
    mc = int(mc)
    r = ms.Lcg()
    ms.large_feature_seed(r, int(seed) & ms.M64, cx, cz)
    st = data().get("setups", {}).get(str(mc))       # (26.3: no air pocket on land)
    setups = tuple(tuple(x) for x in st[kind]) if st else SETUPS[kind]
    if len(setups) > 1:
        total = _f32(0.0)
        for s in setups:
            total = _f32(total + _f32(s[1]))
        pick = _f32(r.next_float())
        chosen = None
        for s in setups:
            pick = _f32(pick - _f32(_f32(s[1]) / total))
            if pick < 0.0:
                chosen = s
                break
    else:
        chosen = setups[0]
    vp, _w, ap = chosen
    air = False if ap == 0.0 else True if ap == 1.0 else r.next_float() < _f32(ap)
    if r.next_float() < _f32(0.05):
        tpl = f"giant_portal_{r.next_int(3) + 1}"
    else:
        tpl = f"portal_{r.next_int(10) + 1}"
    rot = r.next_int(4)
    mirror = not (r.next_float() < 0.5)
    size, chest = data()["templates"][tpl]
    x0, z0 = cx << 4, cz << 4
    bb = _box(size, x0, 0, z0, rot, mirror)
    return r, vp, air, tpl, rot, mirror, size, chest, bb


def gen(seed, mc, cx, cz, kind, cols=None):
    """One ruined portal type's generation point: dict(template, rot, mirror, placement,
    air, origin (x, y, z), box)."""
    mc = int(mc)
    cols = cols or _Cols(seed, mc)
    r, vp, air, tpl, rot, mirror, size, chest, bb = _prefix(seed, mc, cx, cz, kind)
    x0, z0 = cx << 4, cz << 4
    c = _center(bb)
    surface = cols.base_height(c[0], c[2], vp == OCEAN_FLOOR) - 1
    min_y = (-64 if mc >= 22 else 0) + 15
    y = _find_y(r, cols, vp, air, surface, bb[4] - bb[1] + 1, bb, min_y)
    return dict(kind=kind, template=tpl, rot=rot, mirror=mirror, placement=vp, air=air,
                origin=(x0, y, z0), box=_box(size, x0, y, z0, rot, mirror), size=size, chest=chest)


# 1.16 / 1.17: the type of the biome (cubiomes ids)
_OLD_TYPE = {}
for _b in (2, 17, 130):                                   # desert, desert hills, desert lakes
    _OLD_TYPE[_b] = "desert"
for _b in (21, 22, 23, 149, 151, 168, 169):               # jungles, bamboo jungles
    _OLD_TYPE[_b] = "jungle"
for _b in (6, 134):
    _OLD_TYPE[_b] = "swamp"
for _b in (3, 20, 34, 131, 162, 163, 164, 37, 38, 39, 165, 166, 167, 133, 158, 25):
    _OLD_TYPE[_b] = "mountain"                            # mountains, savanna plateaus, badlands, stone shore…
for _b in (0, 24, 10, 50, 44, 45, 46, 47, 48, 49):
    _OLD_TYPE[_b] = "ocean"
_NETHER = (8, 170, 171, 172, 173)


def _biome_lib(seed, mc):
    import struct_info
    return struct_info._lib(int(seed), int(mc), None, 0)


_NAME_IDS = {}


def _tag_ids(mc, kind):
    """cubiomes ids of the biome tag has_structure/ruined_portal_<kind> (1.18.2+)."""
    k = (int(mc), kind)
    v = _NAME_IDS.get(k)
    if v is None:
        import ctypes as ct
        from cubiomes_wrap import get_lib
        L = get_lib()._lib
        f = L.biome2str
        f.restype = ct.c_char_p
        f.argtypes = [ct.c_int, ct.c_int]
        tags = data()["tags"]
        key = str(int(mc)) if str(int(mc)) in tags else max(tags, key=int)
        names = set(tags[key].get(kind, []))
        v = set()
        for i in range(256):
            s = f(int(mc), i)
            if s and s.decode() in names:
                v.add(i)
        _NAME_IDS[k] = v
    return v


def layout(seed, mc, cx, cz, cols=None):
    """The ruined portal started in chunk (cx, cz) (overworld), or None.
    cols: a _Cols to reuse (its noise chunks)."""
    mc = int(mc)
    cols = cols or _Cols(seed, mc)
    lib = _biome_lib(seed, mc)
    if mc < 22:
        b = int(lib.get_biome_at((cx << 4) + 8, (cz << 4) + 8, scale=4, y=63))
        if b in _NETHER:
            return None
        return gen(seed, mc, cx, cz, _OLD_TYPE.get(b, "standard"), cols)
    # the structure set: weighted random order, the first valid biome wins
    r = ms.Lcg()
    ms.large_feature_seed(r, int(seed) & ms.M64, cx, cz)
    left = list(TYPES)
    total = len(left)
    while left:
        j = r.next_int(total)
        k = j                                   # weights 1: the j-th entry
        kind = left[k]
        if kind != "nether":
            g = gen(seed, mc, cx, cz, kind, cols)
            ox, oy, oz = g["origin"]
            b = int(lib.get_biome_at(ox, oz, scale=4, y=oy))
            if b in _tag_ids(mc, kind):
                return g
        left.pop(k)
        total -= 1
    return None


def _column(lib, x, z):
    """the biomes of the 1:4 cells of column (x, z), y -64 → 319 (one genBiomes)"""
    import ctypes as ct
    from cubiomes_wrap import Range
    try:
        with lib._lock:
            L = lib._lib
            n = max(int(L.getMinCacheSize(lib._gen, 4, 1, 96, 1)), 96)
            buf = (ct.c_int * n)()
            if L.genBiomes(lib._gen, buf, Range(4, x // 4, z // 4, 1, 1, -16, 96)) == 0:
                return {int(v) for v in buf[:96]}
    except Exception:                                   # noqa: BLE001
        pass
    return {int(lib.get_biome_at(x, z, scale=4, y=y)) for y in range(-64, 320, 4)}


def candidates(seed, mc, cx, cz):
    """(07/11, the loot finder) every portal the start in chunk (cx, cz) can be,
    without the terrain: [dict(kind, template, rot, mirror, box (y 0), size, chest,
    placement, air)]. 1.16 / 1.17: the one of its biome (exact). 1.18.2+: the type
    depends on the biome AT the portal's height (terrain): the types of the structure
    set in their (terrain-free) random order, without those whose biome tag holds no
    biome of the column, and none after one whose tag holds them all — a superset."""
    mc = int(mc)
    lib = _biome_lib(seed, mc)

    def one(kind):
        _r, vp, air, tpl, rot, mirror, size, chest, bb = _prefix(seed, mc, cx, cz, kind)
        return dict(kind=kind, template=tpl, rot=rot, mirror=mirror, box=bb, size=size,
                    chest=chest, placement=vp, air=air, origin=(cx << 4, 0, cz << 4))
    if mc < 22:
        b = int(lib.get_biome_at((cx << 4) + 8, (cz << 4) + 8, scale=4, y=63))
        if b in _NETHER:
            return []
        return [one(_OLD_TYPE.get(b, "standard"))]
    ox, oz = cx << 4, cz << 4
    col = _column(lib, ox, oz)
    r = ms.Lcg()
    ms.large_feature_seed(r, int(seed) & ms.M64, cx, cz)
    left = list(TYPES)
    total = len(left)
    out = []
    while left:
        j = r.next_int(total)
        kind = left[j]
        if kind != "nether":
            tag = _tag_ids(mc, kind)
            if col & tag:
                out.append(one(kind))
                if col <= tag:
                    break                       # this one wins whatever the height
        left.pop(j)
        total -= 1
    return out


# ── (30/10) does the portal keep its chest? ─────────────────────────────────────
# 1.16: a portal whose template box misses its start chunk has no reference there:
# the chunk of its template position never places it (no portal at all).
# All versions: spreadNetherrack (chunk random, after the chest's LootTableSeed) puts a
# netherrack drip column under each surface spot it covers — with no check, so a chest
# under such a spot is replaced. LavaSubmergedBlockProcessor: a chest in lava → lava.
_OLD_TEMPS = {
    "ocean": 0.5, "plains": 0.8, "desert": 2.0, "mountains": 0.2, "forest": 0.7, "taiga": 0.25,
    "swamp": 0.8, "river": 0.5, "frozen_ocean": 0.0, "frozen_river": 0.0, "snowy_tundra": 0.0,
    "snowy_mountains": 0.0, "mushroom_fields": 0.9, "mushroom_field_shore": 0.9, "beach": 0.8,
    "desert_hills": 2.0, "wooded_hills": 0.7, "taiga_hills": 0.25, "mountain_edge": 0.2,
    "jungle": 0.95, "jungle_hills": 0.95, "jungle_edge": 0.95, "deep_ocean": 0.5, "stone_shore": 0.2,
    "snowy_beach": 0.05, "birch_forest": 0.6, "birch_forest_hills": 0.6, "dark_forest": 0.7,
    "snowy_taiga": -0.5, "snowy_taiga_hills": -0.5, "giant_tree_taiga": 0.3,
    "giant_tree_taiga_hills": 0.3, "wooded_mountains": 0.2, "savanna": 1.2, "savanna_plateau": 1.0,
    "badlands": 2.0, "wooded_badlands_plateau": 2.0, "badlands_plateau": 2.0, "warm_ocean": 0.5,
    "lukewarm_ocean": 0.5, "cold_ocean": 0.5, "deep_warm_ocean": 0.5, "deep_lukewarm_ocean": 0.5,
    "deep_cold_ocean": 0.5, "deep_frozen_ocean": 0.5, "sunflower_plains": 0.8, "desert_lakes": 2.0,
    "gravelly_mountains": 0.2, "flower_forest": 0.7, "taiga_mountains": 0.25, "swamp_hills": 0.8,
    "ice_spikes": 0.0, "modified_jungle": 0.95, "modified_jungle_edge": 0.95,
    "tall_birch_forest": 0.6, "tall_birch_hills": 0.6, "dark_forest_hills": 0.7,
    "snowy_taiga_mountains": -0.5, "giant_spruce_taiga": 0.25, "giant_spruce_taiga_hills": 0.25,
    "modified_gravelly_mountains": 0.2, "shattered_savanna": 1.1, "shattered_savanna_plateau": 1.0,
    "eroded_badlands": 2.0, "modified_wooded_badlands_plateau": 2.0, "modified_badlands_plateau": 2.0,
    "bamboo_jungle": 0.95, "bamboo_jungle_hills": 0.95}
_TNOISE = None


def _bname(mc, b):
    import ctypes as ct
    from cubiomes_wrap import get_lib
    f = get_lib()._lib.biome2str
    f.restype = ct.c_char_p
    f.argtypes = [ct.c_int, ct.c_int]
    v = f(int(mc), int(b))
    return v.decode() if v else ""


def _temperature(mc, name, x, y, z):
    """Biome.getTemperature at (x, y, z) (height-adjusted, FROZEN modifier)."""
    global _TNOISE
    import surface_ice as si
    f32 = _f32
    if _TNOISE is None:
        _TNOISE = si._PerlinSimplex(1234, [0])
    t = data()["temps"].get(name)
    if t is None:
        base, mod = _OLD_TEMPS.get(name, 0.8), ("frozen" if name in ("frozen_ocean", "deep_frozen_ocean")
                                                else "none")
    else:
        base, mod = t
    v = f32(base)
    if mod == "frozen":
        v = f32(si._temp(x, z, f32(base)))
    if mc >= 22:
        if y > 80:
            n = f32(_TNOISE.value(float(f32(f32(x) / f32(8.0))), float(f32(f32(z) / f32(8.0)))) * 8.0)
            v = f32(v - f32(f32(f32(f32(n + f32(y)) - f32(80.0)) * f32(0.05)) / f32(40.0)))
    elif y > 64:
        n = f32(_TNOISE.value(float(f32(f32(x) / f32(8.0))), float(f32(f32(z) / f32(8.0)))) * 4.0)
        v = f32(v - f32(f32(f32(f32(n + f32(y)) - f32(64.0)) * f32(0.05)) / f32(30.0)))
    return v


def properties(seed, mc, g):
    """(cold, overgrown, vines) of the portal piece."""
    k = g["kind"]
    mc = int(mc)
    if mc >= 22:
        pr = data()["props"][k]
        idx = 0
        for i, s_ in enumerate(SETUPS[k]):
            if s_[0] == g["placement"]:
                idx = i
                break
        can_cold, over, vines = pr[idx][0], pr[idx][1], pr[idx][2]
    else:
        can_cold = k in ("mountain", "ocean", "standard")
        over, vines = k == "jungle", k in ("jungle", "swamp")
    cold = False
    if can_cold:
        lib = _biome_lib(seed, mc)
        ox, oy, oz = g["origin"]
        if mc >= 22:
            b = int(lib.get_biome_at(ox, oz, scale=4, y=oy))
        else:
            b = int(lib.get_biome_at(ox + 8, oz + 8, scale=4, y=63))
        cold = _temperature(mc, _bname(mc, b), ox, oy, oz) < _f32(0.15)
    return cold, over, vines


def _blocks(g):
    out = []
    size = g["size"]
    px, pz = size[0] // 2, size[2] // 2
    ox, oy, oz = g["origin"]
    for e in data()["blocks"][g["template"]].split(";"):
        x, y, z, c = e.split(",")
        a, b = transform(int(x), int(z), g["mirror"], g["rot"], px, pz)
        out.append((ox + a, oy + int(y), oz + b, c))
    return out


def _pos_float(x, y, z):
    """RandomSource.create(Mth.getSeed(pos)).nextFloat()."""
    import ocean_ruin_y as oy
    s_ = (oy.mth_seed(x, y, z) ^ 0x5DEECE66D) & ((1 << 48) - 1)
    s_ = (s_ * 0x5DEECE66D + 0xB) & ((1 << 48) - 1)
    return (s_ >> 24) * (1.0 / (1 << 24))


class _Carved:
    """The terrain after the carvers (frozen WG heightmaps, the blocks the portal meets)."""

    def __init__(self, seed, mc):
        self.seed, self.mc, self.c = int(seed), int(mc), {}

    def _chunk(self, cx, cz):
        v = self.c.get((cx, cz))
        if v is None:
            if self.mc >= 22:
                import terrain
                v = terrain.chunk_underground(self.seed, self.mc, cx, cz)
            else:
                import ctypes as ct
                from cubiomes_wrap import get_lib
                f = get_lib()._lib.ssa_t116_carved
                f.restype = ct.c_int
                buf = (ct.c_uint8 * 65536)()
                v = bytes(buf).translate(bytes(_T116_LUT)) if f(self.mc, ct.c_uint64(self.seed & ((1 << 64) - 1)),
                                                               cx, cz, buf) else None
                if v is not None:
                    v = bytes(buf).translate(bytes(_T116_LUT))
            if v is None:
                raise RuntimeError("no terrain")
            self.c[(cx, cz)] = v
        return v

    def at(self, x, y, z):
        if self.mc >= 22:
            if not -64 <= y < 320:
                return 1
            return self._chunk(x >> 4, z >> 4)[(y + 64) * 256 + (z & 15) * 16 + (x & 15)]
        if not 0 <= y < 256:
            return 1
        return self._chunk(x >> 4, z >> 4)[(((y << 4) | (z & 15)) << 4) | (x & 15)]

    def surface(self, x, z, ocean_floor):
        # (07/11) per chunk, both heightmaps at once (numpy): the same answer as the
        # walk down from the top (the block where (v == 0) / (v != 1) first holds)
        k = (x >> 4, z >> 4, bool(ocean_floor))
        hm = self.__dict__.setdefault("_hm", {}).get(k)
        if hm is None:
            import numpy as np
            raw = np.frombuffer(self._chunk(x >> 4, z >> 4), dtype=np.uint8)
            if self.mc >= 22:
                a, low = raw.reshape(384, 16, 16), -64
            else:
                a, low = raw.reshape(256, 16, 16), 0
            m = (a == 0) if ocean_floor else (a != 1)
            rev = m[::-1]
            hm = np.where(rev.any(0), low + a.shape[0] - 1 - rev.argmax(0), low - 1)
            self._hm[k] = hm
        return int(hm[z & 15, x & 15])


_PROB = (1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9, 0.9, 0.8, 0.7, 0.6, 0.4, 0.2)


def chest_fate(seed, mc, g, r, reprimed=False):
    """None if the chest stays, else why it is gone. r: the placing chunk's random just
    after the chest's LootTableSeed. reprimed: the chunk's WG heightmaps primed again
    after the template was placed (chunk saved and loaded again)."""
    import scattered_loot as sc
    mc = int(mc)
    cold, over, _vines = properties(seed, mc, g)
    T = _Carved(seed, mc)
    X, Y, Z = chest_pos(g)
    floor_ = g["placement"] == OCEAN_FLOOR
    # the blocks of the template as placed (processors that matter here)
    W = {}
    for (x, y, z, c) in _blocks(g):
        world = T.at(x, y, z)
        if c == "a" and not g["air"]:
            continue                                   # STRUCTURE_AND_AIR: terrain kept
        if c == "g" and _pos_float(x, y, z) < _f32(0.3):
            c = "a"
        elif c == "l":
            if g["placement"] == OCEAN_FLOOR or cold or _pos_float(x, y, z) < _f32(0.2):
                c = "s"
        elif c == "o" and _pos_float(x, y, z) < _f32(0.15):
            c = "s"                                    # crying obsidian
        if world == 3 and c in ("a", "h", "c", "l"):  # LavaSubmergedBlockProcessor
            c = "l"
        if c == "A":
            c = "a"
        W[(x, y, z)] = c
    if W.get((X, Y, Z)) != "c":
        return "lava"
    tops_w = {}
    if reprimed:
        for (x, y, z), c in W.items():
            if c != "a" and (not floor_ or c in ("o", "s", "h", "c", "g")):
                if y > tops_w.get((x, z), -999):
                    tops_w[(x, z)] = y
    box = g["box"]
    cxx, _cy, czz = _center(box)
    follow = g["placement"] in (ON_LAND, OCEAN_FLOOR)
    floor = g["placement"] == OCEAN_FLOOR
    avg = ((box[3] - box[0] + 1) + (box[5] - box[2] + 1)) // 2
    adj = sc._next_int(r, max(1, 8 - avg // 2))
    nf = (lambda: r.next_float()) if hasattr(r, "next_float") else (lambda: r.nextFloat())

    def nd():
        return ((r.next(26) << 27) + r.next(27)) * (1.0 / (1 << 53))

    def place():
        if not cold:
            nf()
    md = len(_PROB)
    for x in range(cxx - md, cxx + md + 1):
        for z in range(czz - md, czz + md + 1):
            ad = max(0, abs(x - cxx) + abs(z - czz) + adj)
            if ad >= md:
                continue
            if not nd() < _PROB[ad]:
                continue
            sy = T.surface(x, z, floor)
            if reprimed:
                tops = [yy for (xx, zz), yy in tops_w.items() if (xx, zz) == (x, z)]
                if tops:
                    sy = max(sy, tops[0])
            y = sy if follow else min(box[1], sy)
            if abs(y - box[1]) > 3:
                continue
            c = W.get((x, y, z))
            if c is None:
                v = T.at(x, y, z)
                ok = v not in (1, 3)                   # air, lava
            else:
                ok = c not in ("a", "o", "c", "l")
            if not ok:
                continue
            place()
            if over:
                nf()
            # the drip column below: no check
            lo = y - 1
            place()
            k = 8
            while k > 0 and nf() < 0.5:
                lo -= 1
                place()
                k -= 1
            if (x, z) == (X, Z) and lo <= Y <= y - 1:
                return "netherrack"
    return None


def at_risk(seed, mc, g):
    """The chest could meet a netherrack drip in another generation order: a portal
    following the ground, the chest's column in the spread, the chest at most 9 blocks
    under the ground there."""
    if g["placement"] not in (ON_LAND, OCEAN_FLOOR):
        return False
    X, Y, Z = chest_pos(g)
    cxx, _cy, czz = _center(g["box"])
    if abs(X - cxx) + abs(Z - czz) >= len(_PROB):
        return False
    sy = _Carved(seed, mc).surface(X, Z, g["placement"] == OCEAN_FLOOR)
    return Y >= sy - 9


def exists_116(seed, mc, cx, cz):
    """1.16: False when the portal started in chunk (cx, cz) is never placed (its
    template box misses the start chunk). Draws only, no terrain."""
    lib = _biome_lib(seed, mc)
    b = int(lib.get_biome_at((cx << 4) + 8, (cz << 4) + 8, scale=4, y=63))
    kind = _OLD_TYPE.get(b, "standard")
    r = ms.Lcg()
    ms.large_feature_seed(r, int(seed) & ms.M64, cx, cz)
    setups = SETUPS[kind]
    if len(setups) > 1:
        pick = _f32(r.next_float())
        chosen = setups[0] if pick < 0.5 else setups[1]
    else:
        chosen = setups[0]
    ap = chosen[2]
    if ap not in (0.0, 1.0):
        r.next_float()
    if r.next_float() < _f32(0.05):
        tpl = f"giant_portal_{r.next_int(3) + 1}"
    else:
        tpl = f"portal_{r.next_int(10) + 1}"
    rot = r.next_int(4)
    mirror = not (r.next_float() < 0.5)
    size = data()["templates"][tpl][0]
    bb = _box(size, cx << 4, 0, cz << 4, rot, mirror)
    return not ghost_116(mc, dict(box=bb), cx, cz)


def ghost_116(mc, g, cx, cz):
    """1.16: the template box misses the start chunk → never placed."""
    if int(mc) > 20:
        return False
    b = g["box"]
    return not (b[0] <= (cx << 4) + 15 and b[3] >= cx << 4 and b[2] <= (cz << 4) + 15 and b[5] >= cz << 4)


def chest_pos(g):
    lx, ly, lz = g["chest"][0]
    size = g["size"]
    a, b = transform(lx, lz, g["mirror"], g["rot"], size[0] // 2, size[2] // 2)
    ox, oy, oz = g["origin"]
    return ox + a, oy + ly, oz + b


def chests(seed, mc, x, z, ver, fast=False):
    import loot_engine as le
    import scattered_loot as sc
    cx, cz = int(x) >> 4, int(z) >> 4
    if fast:                        # (07/11) the finder: every portal it can be, no terrain
        out = []
        for g in candidates(seed, mc, cx, cz):
            if ghost_116(mc, g, cx, cz):
                continue
            c = _center(g["box"])
            ch = (cx, cz) if int(mc) <= 20 else (c[0] >> 4, c[2] >> 4)
            name = _STRUCT[g["kind"]] if int(mc) >= 22 else "ruined_portal"
            ls = sc._next_long(sc._rng(seed, mc, ch[0], ch[1], name))
            slots, approx = le.fill_chest("ruined_portal", ls, ver)
            out.append(dict(name="Portal chest", pos=(chest_pos(g)[0], None, chest_pos(g)[2]),
                            table="chests/ruined_portal", seed=ls, slots=slots, approx=approx))
        return out or [dict(name="No ruined portal", pos=(x, None, z), table="", seed=None,
                            slots=None, approx=[])]
    g = layout(seed, mc, cx, cz)
    if g is None:
        return [dict(name="No ruined portal", pos=(x, None, z), table="", seed=None, slots=None,
                     approx=[], note="SSA finds no ruined portal start here.")]
    c = _center(g["box"])
    ch = (c[0] >> 4, c[2] >> 4)
    if int(mc) <= 20:           # 1.16: placed by the chunk of the template position
        ch = (cx, cz)
    name = _STRUCT[g["kind"]] if int(mc) >= 22 else "ruined_portal"
    kind = g["kind"] if g["kind"] != "standard" else "overworld"
    desc = (f"{kind.capitalize()} ruined portal, {g['template'].replace('_', ' ')}, "
            f"{g['placement'].replace('_', ' ')}" + (", air pocket" if g["air"] else "") + ".")
    if ghost_116(mc, g, cx, cz):
        return [dict(name="No portal", pos=chest_pos(g), table="", seed=None, slots=None, approx=[],
                     note="1.16: this portal's template lies outside its start chunk, so the game never "
                          "places it (no blocks, no chest). " + desc)]
    r = sc._rng(seed, mc, ch[0], ch[1], name)
    ls = sc._next_long(r)
    import copy
    try:
        r2 = copy.deepcopy(r)
        fate = chest_fate(seed, mc, g, r)
        fate2 = chest_fate(seed, mc, g, r2, reprimed=True) if fate is None else fate
    except Exception:
        fate = fate2 = None
    if fate == "lava":
        return [dict(name="No chest", pos=chest_pos(g), table="", seed=None, slots=None, approx=[],
                     note="The template's chest is gone: it was placed in lava. " + desc)]
    slots, approx = le.fill_chest("ruined_portal", ls, ver)
    if fate == "netherrack":
        return [dict(name="Portal chest — usually gone", pos=chest_pos(g), table="chests/ruined_portal",
                     seed=ls, slots=slots, approx=approx, cond=True,
                     note="The netherrack the portal spreads around drips down over the chest and "
                          "replaces it — unless the chunks around were generated first (another ground "
                          "height there). " + desc)]
    if fate2 == "netherrack":
        return [dict(name="Portal chest", pos=chest_pos(g), table="chests/ruined_portal", seed=ls,
                     slots=slots, approx=approx, cond=True,
                     note="Near the ground under the portal's netherrack: depending on the order the "
                          "chunks were generated, a netherrack drip can replace it. " + desc)]
    return [dict(name="Portal chest", pos=chest_pos(g), table="chests/ruined_portal", seed=ls,
                 slots=slots, approx=approx, note=desc)]


_MAP = {}


def map_boxes(seed, mc, cx, cz):
    """For the map: [(x0, z0, x1, z1, "portal", y0, y1)] (cached; [] if unknown)."""
    k = (int(seed), int(mc), cx, cz)
    v = _MAP.get(k)
    if v is None:
        try:
            g = layout(seed, mc, cx, cz)
            if g is not None and ghost_116(mc, g, cx, cz):
                g = None
            v = [] if g is None else [(g["box"][0], g["box"][2], g["box"][3], g["box"][5], "portal",
                                       g["box"][1], g["box"][4])]
        except Exception:
            v = []
        if len(_MAP) > 4096:
            _MAP.clear()
        _MAP[k] = v
    return v


# ── (07/11) the portal frame as placed (ruined_portal_filter: completable portals) ──
def placed(seed, mc, g, r, reprimed=False, spots=None, decide=None, pending=None, T=None):
    """chest_fate's world, whole: (W {(x, y, z): code} of the template as placed —
    "o" obsidian, "s" crying obsidian among others —, the cells spreadNetherrack and
    its drip columns replaced). r: the placing chunk's random just after the chest's
    LootTableSeed (it is consumed). spots: a set that receives the ground spots the
    spread tries (a crying obsidian there is replaced, an obsidian is not).
    (07/11, the odds model) decide: {obsidian (x, y, z): crying?} that overrides the
    game's draw, every other obsidian is NOT crying; pending: a list that receives the
    obsidian met at a ground spot whose fate was not decided. T: a _Carved to reuse."""
    import scattered_loot as sc
    mc = int(mc)
    cold, over, _vines = properties(seed, mc, g)
    T = T or _Carved(seed, mc)
    W = {}
    for (x, y, z, c) in _blocks(g):
        world = T.at(x, y, z)
        if c == "a" and not g["air"]:
            continue
        if c == "g" and _pos_float(x, y, z) < _f32(0.3):
            c = "a"
        elif c == "l":
            if g["placement"] == OCEAN_FLOOR or cold or _pos_float(x, y, z) < _f32(0.2):
                c = "s"
        elif c == "o" and ((decide.get((x, y, z), False)) if decide is not None
                           else _pos_float(x, y, z) < _f32(0.15)):
            c = "s"
            W[("cry", x, y, z)] = True                 # crying obsidian (BlockAgeProcessor)
        if world == 3 and c in ("a", "h", "c", "l"):
            c = "l"
        if c == "A":
            c = "a"
        W[(x, y, z)] = c
    floor_ = g["placement"] == OCEAN_FLOOR
    tops_w = {}
    if reprimed:
        for k, c in W.items():
            if k[0] == "cry":
                continue
            x, y, z = k
            if c != "a" and (not floor_ or c in ("o", "s", "h", "c", "g")):
                if y > tops_w.get((x, z), -999):
                    tops_w[(x, z)] = y
    box = g["box"]
    cxx, _cy, czz = _center(box)
    follow = g["placement"] in (ON_LAND, OCEAN_FLOOR)
    avg = ((box[3] - box[0] + 1) + (box[5] - box[2] + 1)) // 2
    adj = sc._next_int(r, max(1, 8 - avg // 2))
    nf = (lambda: r.next_float()) if hasattr(r, "next_float") else (lambda: r.nextFloat())

    def nd():
        return ((r.next(26) << 27) + r.next(27)) * (1.0 / (1 << 53))

    def place():
        if not cold:
            nf()
    gone = set()
    md = len(_PROB)
    for x in range(cxx - md, cxx + md + 1):
        for z in range(czz - md, czz + md + 1):
            ad = max(0, abs(x - cxx) + abs(z - czz) + adj)
            if ad >= md:
                continue
            if not nd() < _PROB[ad]:
                continue
            sy = T.surface(x, z, floor_)
            if reprimed and (x, z) in tops_w:
                sy = max(sy, tops_w[(x, z)])
            y = sy if follow else min(box[1], sy)
            if abs(y - box[1]) > 3:
                continue
            if spots is not None:
                spots.add((x, y, z))
            c = W.get((x, y, z))
            if (pending is not None and c == "o" and (x, y, z) not in decide
                    and (x, y, z) not in pending):
                pending.append((x, y, z))
            if c is None:
                ok = T.at(x, y, z) not in (1, 3)
            else:
                ok = c not in ("a", "o", "c", "l")
            if not ok:
                continue
            place()
            gone.add((x, y, z))
            if over:
                nf()
            lo = y - 1
            place()
            gone.add((x, lo, z))
            k = 8
            while k > 0 and nf() < 0.5:
                lo -= 1
                place()
                gone.add((x, lo, z))
                k -= 1
    return W, gone


def chest_rng(seed, mc, g, cx, cz):
    """(loot seed, the random after it) of the portal started in chunk (cx, cz)"""
    import scattered_loot as sc
    c = _center(g["box"])
    ch = (cx, cz) if int(mc) <= 20 else (c[0] >> 4, c[2] >> 4)
    name = _STRUCT[g["kind"]] if int(mc) >= 22 else "ruined_portal"
    r = sc._rng(seed, mc, ch[0], ch[1], name)
    return sc._next_long(r), r
