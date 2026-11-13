"""
village_world.py — the blocks around a village as the game has them when it places
the village's pieces (25/10), so that the "feature" pieces (flowers, hay piles,
trees…) can be replayed draw by draw:
  * terrain: noise + aquifer + carvers + the village's Beardifier (rigid pieces with
    beard_thin, jigsaw junctions) — cave118.c cv_chunk_ex, checked block for block
    against the 26.2 server around a village;
  * then, in the chunk being decorated, every piece of the start in order: template
    blocks (air included) through the element's processors (rules, block_rot, the
    gravity of terrain_matching pieces), and the feature pieces, which use the
    chunk's structure random and write their blocks too.
Neighbour chunks are seen as terrain only (not decorated yet).
"""
from __future__ import annotations

import gzip
import json
import math
import os
import struct
from functools import lru_cache

import jigsaw
import stronghold_eyes as se

_DIR = os.path.dirname(os.path.abspath(__file__))
_ADAPT = {"none": None, "bury": 0, "beard_thin": 1, "beard_box": 2, "encapsulate": 3}


def beard_inputs(lay, cx, cz, adapt="beard_thin"):
    """(rigids, junctions) of Beardifier.forStructuresInChunk for chunk (cx, cz)."""
    t = _ADAPT.get(adapt)
    if t is None:
        return [], []
    x0, z0 = cx << 4, cz << 4
    rig, jun = [], []
    for p in lay[1]:
        b = p.box
        if not (b[0] <= x0 + 15 + 12 and b[3] >= x0 - 12 and b[2] <= z0 + 15 + 12 and b[5] >= z0 - 12):
            continue
        if p.el.proj == "rigid":
            rig.append((b[0], b[1], b[2], b[3], b[4], b[5], t, p.gld))
        for (jx, jy, jz, _d, _pr) in p.junc:
            if jx <= x0 - 12 or jz <= z0 - 12 or jx >= x0 + 15 + 12 or jz >= z0 + 15 + 12:
                continue
            jun.append((jx, jy, jz))
    return rig, jun


def terrain_chunk(seed, mc, lay, cx, cz, adapt="beard_thin"):
    import terrain
    rig, jun = beard_inputs(lay, cx, cz, adapt)
    return terrain.chunk_underground_ex(seed, mc, cx, cz, rig, jun)


@lru_cache(maxsize=2)
def _blocks(version="26.2"):
    with gzip.open(os.path.join(_DIR, "loot_tables", f"jigsaw_blocks_{version}.json.gz"),
                   "rt", encoding="utf-8") as f:
        return json.load(f)


# ── block classes ────────────────────────────────────────────────────────────────
_DIRT = {"grass_block", "dirt", "coarse_dirt", "podzol", "rooted_dirt", "mud", "moss_block",
         "mycelium", "muddy_mangrove_roots", "pale_moss_block"}
# name suffixes / names whose top face is not sturdy (isFaceSturdy UP false)
_NS_SUFFIX = ("_fence", "_fence_gate", "_wall", "_pane", "_bars", "_door", "_trapdoor", "torch",
              "lantern", "_sapling", "_carpet", "rail", "_sign", "_bed", "_button", "_pressure_plate",
              "_candle", "candle", "_head", "_skull", "_banner", "_tulip", "_orchid", "_coral",
              "_coral_fan", "_petals", "_wall_fan", "_rod", "_pot", "_bush", "_mushroom",
              "_leaf_litter", "_vine", "vines", "_roots", "_fungus", "_sprouts")
_NS_NAME = {"air", "cave_air", "void_air", "water", "lava", "short_grass", "grass", "tall_grass", "fern",
            "large_fern", "dandelion", "poppy", "azure_bluet", "oxeye_daisy", "cornflower",
            "allium", "lily_of_the_valley", "sunflower", "lilac", "rose_bush", "peony",
            "wheat", "carrots", "potatoes", "beetroots", "sweet_berry_bush", "dirt_path",
            "farmland", "ladder", "lever", "bell", "anvil", "chipped_anvil", "damaged_anvil",
            "lectern", "cauldron", "water_cauldron", "composter", "brewing_stand", "campfire",
            "soul_campfire", "chain", "iron_chain", "cake", "cobweb", "chest", "trapped_chest",
            "ender_chest", "cactus", "cactus_flower", "stonecutter", "grindstone",
            "enchanting_table", "hopper", "sea_pickle", "kelp", "kelp_plant", "seagrass",
            "tall_seagrass", "conduit", "scaffolding", "glow_lichen", "snow", "flower_pot",
            "dead_bush", "end_rod", "lily_pad", "sugar_cane", "bamboo", "string", "tripwire",
            "tripwire_hook", "redstone_wire", "repeater", "comparator", "daylight_detector",
            "structure_void", "barrier", "light", "frogspawn", "pointed_dripstone"}
def _name(state):
    return state.split("[", 1)[0]


def is_air(state):
    return _name(state) in ("air", "cave_air", "void_air")


def sturdy_up(state):
    n = _name(state)
    if n == "snow":
        return "layers=8" in state
    if n.endswith("_slab"):
        return "type=bottom" not in state
    if n.endswith("_stairs"):
        return "half=top" in state
    if n in _NS_NAME:
        return False
    return not n.endswith(_NS_SUFFIX)


# ── the noise of NoiseThresholdProvider (seed 2345, one octave) ─────────────────
class _Improved:
    _G = [(1, 1, 0), (-1, 1, 0), (1, -1, 0), (-1, -1, 0), (1, 0, 1), (-1, 0, 1), (1, 0, -1),
          (-1, 0, -1), (0, 1, 1), (0, -1, 1), (0, 1, -1), (0, -1, -1), (1, 1, 0), (0, -1, 1),
          (-1, 1, 0), (0, -1, -1)]

    def __init__(self, r):
        self.xo, self.yo, self.zo = r.nextDouble() * 256.0, r.nextDouble() * 256.0, r.nextDouble() * 256.0
        p = list(range(256))
        for i in range(256):
            o = r.nextInt_bound(256 - i)
            p[i], p[i + o] = p[i + o], p[i]
        self.p = p

    def _g(self, h, x, y, z):
        g = self._G[h & 15]
        return g[0] * x + g[1] * y + g[2] * z

    def noise(self, x, y, z):
        xx, yy, zz = x + self.xo, y + self.yo, z + self.zo
        xf, yf, zf = math.floor(xx), math.floor(yy), math.floor(zz)
        xr, yr, zr = xx - xf, yy - yf, zz - zf
        P = lambda i: self.p[i & 255]
        x0, x1 = P(xf), P(xf + 1)
        a00, a01, a10, a11 = P(x0 + yf), P(x0 + yf + 1), P(x1 + yf), P(x1 + yf + 1)
        g = self._g
        d000 = g(P(a00 + zf), xr, yr, zr)
        d100 = g(P(a10 + zf), xr - 1.0, yr, zr)
        d010 = g(P(a01 + zf), xr, yr - 1.0, zr)
        d110 = g(P(a11 + zf), xr - 1.0, yr - 1.0, zr)
        d001 = g(P(a00 + zf + 1), xr, yr, zr - 1.0)
        d101 = g(P(a10 + zf + 1), xr - 1.0, yr, zr - 1.0)
        d011 = g(P(a01 + zf + 1), xr, yr - 1.0, zr - 1.0)
        d111 = g(P(a11 + zf + 1), xr - 1.0, yr - 1.0, zr - 1.0)
        s = lambda t: t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
        ax, ay, az = s(xr), s(yr), s(zr)
        L = lambda t, a, b: a + t * (b - a)
        return L(az, L(ay, L(ax, d000, d100), L(ax, d010, d110)),
                 L(ay, L(ax, d001, d101), L(ax, d011, d111)))


def _java_hash(s):
    h = 0
    for ch in s:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    return h - (1 << 32) if h >= 1 << 31 else h


def _wrap(x):
    return x - math.floor(x / 3.3554432E7 + 0.5) * 3.3554432E7


class _Normal1:
    """NormalNoise.create(WorldgenRandom(LegacyRandomSource(seed)), {0, [1.0]})."""

    def __init__(self, seed):
        r = se.JavaRandom(seed)
        self.a = _Improved(se.JavaRandom((r.nextLong() ^ _java_hash("octave_0")) & ((1 << 64) - 1)))
        self.b = _Improved(se.JavaRandom((r.nextLong() ^ _java_hash("octave_0")) & ((1 << 64) - 1)))
        self.f = 0.16666666666666666 / (0.1 * (1.0 + 1.0 / 1.0))

    def value(self, x, y, z):
        k = 1.0181268882175227
        return (self.a.noise(_wrap(x), _wrap(y), _wrap(z))
                + self.b.noise(_wrap(x * k), _wrap(y * k), _wrap(z * k))) * self.f


_FLOWER_NOISE = None
_F005 = struct.unpack("f", struct.pack("f", 0.005))[0]


def _flower_low(x, y, z):
    global _FLOWER_NOISE
    if _FLOWER_NOISE is None:
        _FLOWER_NOISE = _Normal1(2345)
    return _FLOWER_NOISE.value(x * _F005, y * _F005, z * _F005) < -0.8


# ── the world ───────────────────────────────────────────────────────────────────
_TER = {}                                         # terrain chunks of the last villages


class World:
    def __init__(self, seed, mc, lay, top_block="grass_block"):
        self.seed, self.mc, self.lay = seed, mc, lay
        self.deco = mc
        self.ter = {}
        self.ov = {}
        self.top_block = top_block
        self._tops = {}

    def _t(self, cx, cz):
        k = (cx, cz)
        if k not in self.ter:
            # (07/11) shared by the replays of the same village (one World each)
            gk = (self.seed, self.mc, self.lay[0], tuple(self.lay[2]), cx, cz)
            t = _TER.get(gk)
            if t is None:
                t = terrain_chunk(self.seed, self.mc, self.lay, cx, cz)
                if t is None:
                    raise RuntimeError("terrain lib without cv_chunk_ex")
                if len(_TER) > 64:
                    _TER.clear()
                _TER[gk] = t
            self.ter[k] = t
        return self.ter[k]

    def _code(self, x, y, z):
        if y < -64 or y > 319:
            return 1
        t = self._t(x >> 4, z >> 4)
        return t[(y + 64) * 256 + (z & 15) * 16 + (x & 15)]

    def _top_solid(self, x, z):
        k = (x, z)
        if k not in self._tops:
            t = self._t(x >> 4, z >> 4)
            i = (z & 15) * 16 + (x & 15)
            y = 319
            while y >= -64 and t[(y + 64) * 256 + i] != 0:
                y -= 1
            self._tops[k] = y
        return self._tops[k]

    def get(self, x, y, z):
        v = self.ov.get((x, y, z))
        if v is not None:
            return v
        c = self._code(x, y, z)
        if c == 1:
            return "air"
        if c == 2:
            return "water[level=0]"
        if c == 3:
            return "lava[level=0]"
        # surface rule, roughly: depth below the first non-solid block above
        for d in range(0, 4):
            c2 = self._code(x, y + 1 + d, z)
            if c2 != 0:
                if d == 0:
                    return self.top_block if c2 == 1 else "dirt"
                return "dirt"
        return "stone"

    def set(self, x, y, z, state):
        self.ov[(x, y, z)] = state

    def height_wg(self, x, z):
        """WORLD_SURFACE_WG: first free block (not air) from the top."""
        for y in range(319, -65, -1):
            if not is_air(self.get(x, y, z)):
                return y + 1
        return -64


# ── processors ──────────────────────────────────────────────────────────────────
def _pos_random(x, y, z):
    return se.JavaRandom(jigsaw._mth_seed(x, y, z) & ((1 << 64) - 1))


def _f32(v):
    return struct.unpack("f", struct.pack("f", v))[0]


def _match(pred, state, r):
    t = jigsaw._id(pred.get("predicate_type", ""))
    if t == "always_true":
        return True
    if t == "block_match":
        return _name(state) == jigsaw._id(pred["block"])
    if t == "blockstate_match":
        bs = pred["block_state"]
        if isinstance(bs, str):
            import nbt_read
            bs = nbt_read.block_state(bs)
        if _name(state) != jigsaw._id(bs["Name"]):
            return False
        for k, v in (bs.get("Properties") or {}).items():
            if f"{k}={v}" not in state:
                return False
        return True
    if t == "random_block_match":
        return _name(state) == jigsaw._id(pred["block"]) and r.nextFloat() < _f32(pred["probability"])
    if t == "tag_match":
        tag = jigsaw._id(pred["tag"])
        if tag == "doors":
            return _name(state).endswith("_door")
        return False
    return False


def _out_state(o):
    if isinstance(o, str):                        # 26.3: "minecraft:x[k=v]"
        return jigsaw._id(o)
    n = jigsaw._id(o["Name"])
    p = o.get("Properties")
    if p:
        n += "[" + ",".join(f"{k}={v}" for k, v in sorted(p.items())) + "]"
    return n


def _procs(g, el):
    pr = el.procs
    if isinstance(pr, str):
        pr = g.d.get("processors", {}).get(jigsaw._id(pr))
    return (pr or {}).get("processors", []) if isinstance(pr, dict) else []


def place_template(world, g, el, pos, rot, chunk, proj):
    """SinglePoolElement.place in one chunk: process then place every block."""
    data = _blocks(getattr(g, "version", "26.2")).get(el.loc)
    if data is None:
        return
    pal, flat = data["pal"], data["b"]
    procs = _procs(g, el)
    legacy = el.kind == "legacy"
    cx0, cz0 = chunk[0] << 4, chunk[1] << 4
    out = []
    for i in range(0, len(flat), 4):
        lx, ly, lz, pi = flat[i], flat[i + 1], flat[i + 2], flat[i + 3]
        x, y, z = jigsaw._rot_pos(lx, ly, lz, rot)
        x, y, z = x + pos[0], y + pos[1], z + pos[2]
        if not (cx0 <= x <= cx0 + 15 and cz0 <= z <= cz0 + 15):
            continue
        st = pal[pi]
        if st == "structure_void":
            continue
        dead = False
        for q in procs:
            typ = jigsaw._id(q.get("processor_type", ""))
            if typ == "rule":
                r = _pos_random(x, y, z)
                loc = world.get(x, y, z)
                for rule in q["rules"]:
                    if _match(rule["input_predicate"], st, r) and \
                            _match(rule.get("location_predicate", {"predicate_type": "always_true"}), loc, r):
                        st = _out_state(rule["output_state"])
                        break
            elif typ == "block_rot":
                if "rottable_blocks" in q:
                    continue
                if not (_f32(_pos_random(x, y, z).nextFloat()) <= _f32(q["integrity"])):
                    dead = True
                    break
        if dead:
            continue
        if legacy and st == "air":                # LegacySinglePoolElement: STRUCTURE_AND_AIR
            continue
        out.append((x, y, z, st, ly))
    if proj == "terrain_matching":                # GravityProcessor(WORLD_SURFACE_WG, -1)
        hs = {}
        moved = []
        for x, y, z, st, ly in out:
            k = (x, z)
            if k not in hs:
                hs[k] = world.height_wg(x, z)
            moved.append((x, hs[k] - 1 + ly, z, st, ly))
        out = moved
    for x, y, z, st, _ly in out:
        world.set(x, y, z, st)


# ── features ────────────────────────────────────────────────────────────────────
def _ni(r, n):
    import scattered_loot as sc
    return sc._next_int(r, n)


def _nf(r):
    return r.next_float()


def _trap(r, m):                                  # TrapezoidInt(-m, m, plateau 0)
    return _ni(r, m + 1) - _ni(r, m + 1)


def _survives_plant(world, x, y, z):
    return _name(world.get(x, y - 1, z)) in _DIRT or _name(world.get(x, y - 1, z)) == "farmland"


def f_flower_plain(world, r, o):
    for _ in range(64):
        x, y, z = o[0] + _trap(r, 6), o[1] + _trap(r, 2), o[2] + _trap(r, 6)
        if not is_air(world.get(x, y, z)):
            continue
        if _flower_low(x, y, z):
            st = ["orange_tulip", "red_tulip", "pink_tulip", "white_tulip"][_ni(r, 4)]
        elif _f32(_nf(r)) < _f32(0.33333334):
            st = ["poppy", "azure_bluet", "oxeye_daisy", "cornflower"][_ni(r, 4)]
        else:
            st = "dandelion"
        if _survives_plant(world, x, y, z):
            world.set(x, y, z, st)


def _pile(world, r, o, provider):
    if o[1] < -64 + 5:
        return
    xr, zr = 2 + _ni(r, 2), 2 + _ni(r, 2)
    # betweenClosed iterates x, then y, then z (x innermost)
    for z in range(o[2] - zr, o[2] + zr + 1):
        for y in range(o[1], o[1] + 2):
            for x in range(o[0] - xr, o[0] + xr + 1):
                xd, zd = o[0] - x, o[2] - z
                a = _f32(_f32(_nf(r)) * 10.0)
                if float(xd * xd + zd * zd) <= _f32(a - _f32(_f32(_nf(r)) * 6.0)):
                    _pile_try(world, r, x, y, z, provider)
                elif _nf(r) < 0.031:
                    _pile_try(world, r, x, y, z, provider)


def _pile_try(world, r, x, y, z, provider):
    if not is_air(world.get(x, y, z)):
        return
    below = world.get(x, y - 1, z)
    if _name(below) == "dirt_path":
        ok = r.next(1) != 0
    else:
        ok = sturdy_up(below)
    if ok:
        world.set(x, y, z, provider(r))


def _p_hay(r):
    return "hay_block[axis=" + "xyz"[_ni(r, 3)] + "]"


def _p_weighted(entries):
    tot = sum(w for _s, w in entries)

    def f(r):
        k = _ni(r, tot)
        for s, w in entries:
            k -= w
            if k < 0:
                return s
    return f


def f_cactus_patch(world, r, o):
    for _ in range(10):
        x, y, z = o[0] + _trap(r, 7), o[1] + _trap(r, 3), o[2] + _trap(r, 7)
        if not is_air(world.get(x, y, z)):
            continue
        below = _name(world.get(x, y - 1, z))
        if below not in ("sand", "red_sand", "cactus", "suspicious_sand"):
            continue
        ok = True
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = world.get(x + d[0], y, z + d[1])
            if not is_air(n) and not _name(n).endswith("water") or "lava" in n:
                ok = False
        if not ok:
            continue
        h1 = 1 + _ni(r, _ni(r, 3) + 1)             # biased_to_bottom 1-3
        h2 = 0
        if world.deco >= 29:                      # 1.21.5+: the cactus flower layer
            k = _ni(r, 4)                         # weighted list 0 (3) / 1 (1)
            h2 = 0 if k < 3 else 1
        tot = h1 + h2
        n = 0
        for i in range(tot):
            if not is_air(world.get(x, y + 1 + i, z)):
                break
            n = i + 1
        # truncate (prioritize_tip false): drop from the top layers first
        cut = tot - (n + 1) if n + 1 < tot else 0
        if cut > 0:
            d2 = min(cut, h2); h2 -= d2; cut -= d2
            h1 -= min(cut, h1)
        for i in range(h1):
            world.set(x, y + i, z, "cactus[age=0]")
        for i in range(h2):
            world.set(x, y + h1 + i, z, "cactus_flower")


def f_berry_patch(world, r, o):
    for _ in range(96):
        x, y, z = o[0] + _trap(r, 7), o[1] + _trap(r, 3), o[2] + _trap(r, 7)
        if is_air(world.get(x, y, z)) and _name(world.get(x, y - 1, z)) == "grass_block":
            world.set(x, y, z, "sweet_berry_bush[age=3]")


def f_taiga_grass(world, r, o):
    for _ in range(32):
        x, y, z = o[0] + _trap(r, 7), o[1] + _trap(r, 3), o[2] + _trap(r, 7)
        if not is_air(world.get(x, y, z)):
            continue
        st = ("short_grass" if world.mc >= 28 else "grass") if _ni(r, 5) < 1 else "fern"
        if _survives_plant(world, x, y, z):
            world.set(x, y, z, st)


# ── trees (TreeFeature with the village configs of the 26.2 jar) ─────────────────
_SMALL_FLOWERS = {"dandelion", "poppy", "blue_orchid", "allium", "azure_bluet", "red_tulip",
                  "orange_tulip", "white_tulip", "pink_tulip", "oxeye_daisy", "cornflower",
                  "lily_of_the_valley", "wither_rose", "torchflower", "open_eyeblossom",
                  "closed_eyeblossom", "golden_dandelion"}
_REPL_TREES = {"pale_moss_carpet", "short_grass", "grass", "fern", "dead_bush", "vine", "glow_lichen",
               "sunflower", "lilac", "rose_bush", "peony", "tall_grass", "large_fern",
               "hanging_roots", "pitcher_plant", "water", "seagrass", "tall_seagrass", "bush",
               "firefly_bush", "warped_roots", "nether_sprouts", "crimson_roots", "leaf_litter",
               "short_dry_grass", "tall_dry_grass"}


def _valid_tree_pos(st):
    n = _name(st)
    return is_air(st) or n.endswith("_leaves") or n in _SMALL_FLOWERS or n in _REPL_TREES


def _is_free(st):
    return _valid_tree_pos(st) or _name(st).endswith(("_log", "_wood", "_stem", "_hyphae"))


_TREES = {  # trunk (kind, base, a, b), size (limit, lower, upper), log, leaves
    "oak": (("straight", 4, 2, 0), (1, 0, 1), "oak_log[axis=y]", "oak_leaves"),
    "spruce": (("straight", 5, 2, 1), (2, 0, 2), "spruce_log[axis=y]", "spruce_leaves"),
    "pine": (("straight", 6, 4, 0), (2, 0, 2), "spruce_log[axis=y]", "spruce_leaves"),
    "acacia": (("forking", 5, 2, 2), (1, 0, 2), "acacia_log[axis=y]", "acacia_leaves"),
}
_HDIR = [(0, -1), (1, 0), (0, 1), (-1, 0)]       # north east south west


def _leaf(world, x, y, z, leaves):
    if _valid_tree_pos(world.get(x, y, z)):
        world.set(x, y, z, leaves)


def _row(world, o, radius, y, skip, leaves, r=None):
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            if skip(abs(dx), y, abs(dz), radius):
                continue
            _leaf(world, o[0] + dx, o[1] + y, o[2] + dz, leaves)


def f_tree(kind):
    (tk, base, ha, hb), (lim, lo, up), log, leaves = _TREES[kind]

    def f(world, r, o):
        if _name(world.get(o[0], o[1] - 1, o[2])) not in _DIRT and \
                _name(world.get(o[0], o[1] - 1, o[2])) != "farmland":
            return                                # would_survive(sapling) filter
        h = base + _ni(r, ha + 1) + _ni(r, hb + 1)
        if kind == "oak":
            fh, rad = 3, 2
        elif kind == "spruce":
            fh = max(4, h - (1 + _ni(r, 2)))
            rad = 2 + _ni(r, 2)
        elif kind == "pine":
            fh = 3 + _ni(r, 2)
            rad = 1 + _ni(r, max(h - fh + 1, 1))
        else:
            fh, rad = 0, 2
        if o[1] < -64 + 1 or o[1] + h + 1 > 319 + 1:
            return
        clip = h
        for yo in range(0, h + 2):
            rr = lo if yo < lim else up
            bad = False
            for xo in range(-rr, rr + 1):
                for zo in range(-rr, rr + 1):
                    if not _is_free(world.get(o[0] + xo, o[1] + yo, o[2] + zo)):
                        bad = True
                        break
                if bad:
                    break
            if bad:
                clip = yo - 2
                break
        if clip < h:
            return
        below = world.get(o[0], o[1] - 1, o[2])
        if _name(below) not in _DIRT and _name(below) not in ("podzol", "mud"):
            world.set(o[0], o[1] - 1, o[2], "dirt")
        atts = []
        if tk == "straight":
            for i in range(h):
                if _valid_tree_pos(world.get(o[0], o[1] + i, o[2])):
                    world.set(o[0], o[1] + i, o[2], log)
            atts.append(((o[0], o[1] + h, o[2]), 0))
        else:                                     # ForkingTrunkPlacer
            lean = _ni(r, 4)
            lean_h = h - _ni(r, 4) - 1
            steps = 3 - _ni(r, 3)
            tx, tz, ey = o[0], o[2], None
            for yo in range(h):
                if yo >= lean_h and steps > 0:
                    tx += _HDIR[lean][0]; tz += _HDIR[lean][1]; steps -= 1
                if _valid_tree_pos(world.get(tx, o[1] + yo, tz)):
                    world.set(tx, o[1] + yo, tz, log)
                    ey = o[1] + yo + 1
            if ey is not None:
                atts.append(((tx, ey, tz), 1))
            tx, tz = o[0], o[2]
            br = _ni(r, 4)
            if br != lean:
                bpos = lean_h - _ni(r, 2) - 1
                bsteps = 1 + _ni(r, 3)
                ey = None
                yo = bpos
                while yo < h and bsteps > 0:
                    if yo >= 1:
                        tx += _HDIR[br][0]; tz += _HDIR[br][1]
                        if _valid_tree_pos(world.get(tx, o[1] + yo, tz)):
                            world.set(tx, o[1] + yo, tz, log)
                            ey = o[1] + yo + 1
                    yo += 1; bsteps -= 1
                if ey is not None:
                    atts.append(((tx, ey, tz), 0))
        for ap, roff in atts:
            if kind == "oak":
                for yo in range(0, -fh - 1, -1):
                    cur = max(rad + roff - 1 - int(yo / 2), 0)
                    for dx in range(-cur, cur + 1):
                        for dz in range(-cur, cur + 1):
                            if abs(dx) == cur and abs(dz) == cur and (_ni(r, 2) == 0 or yo == 0):
                                continue
                            _leaf(world, ap[0] + dx, ap[1] + yo, ap[2] + dz, leaves)
            elif kind == "spruce":
                off = _ni(r, 3)
                cur = _ni(r, 2)
                mx, mn = 1, 0
                for yo in range(off, -fh - 1, -1):
                    _row(world, ap, cur, yo, lambda a, y, b, c: a == c and b == c and c > 0, leaves)
                    if cur >= mx:
                        cur = mn; mn = 1; mx = min(mx + 1, rad + roff)
                    else:
                        cur += 1
            elif kind == "pine":
                off = 1
                cur = 0
                for yo in range(off, off - fh - 1, -1):
                    _row(world, ap, cur, yo, lambda a, y, b, c: a == c and b == c and c > 0, leaves)
                    if cur >= 1 and yo == off - fh + 1:
                        cur -= 1
                    elif cur < rad + roff:
                        cur += 1
            else:                                 # acacia
                def sk(a, y, b, c):
                    if y == 0:
                        return (a > 1 or b > 1) and a != 0 and b != 0
                    return a == c and b == c and c > 0
                _row(world, ap, rad + roff, -1 - fh, sk, leaves)
                _row(world, ap, rad - 1, -fh, sk, leaves)
                _row(world, ap, rad + roff - 1, 0, sk, leaves)
    return f


FEATURES = {
    "flower_plain": f_flower_plain,
    "pile_hay": lambda w, r, o: _pile(w, r, o, _p_hay),
    "pile_melon": lambda w, r, o: _pile(w, r, o, lambda r_: "melon"),
    "pile_snow": lambda w, r, o: _pile(w, r, o, lambda r_: "snow[layers=1]"),
    "pile_ice": lambda w, r, o: _pile(w, r, o, _p_weighted([("blue_ice", 1), ("packed_ice", 5)])),
    "pile_pumpkin": lambda w, r, o: _pile(w, r, o, _p_weighted([("pumpkin", 19),
                                                                ("jack_o_lantern", 1)])),
    "patch_cactus": f_cactus_patch,
    "patch_berry_bush": f_berry_patch,
    "patch_taiga_grass": f_taiga_grass,
    "oak": f_tree("oak"), "spruce": f_tree("spruce"), "pine": f_tree("pine"),
    "acacia": f_tree("acacia"),
}


# ── the chunk, piece after piece ────────────────────────────────────────────────
def decorate_templates(world, g, lay, chunk):
    """Places every template piece of the start in `chunk` (a neighbour chunk
    already decorated): blocks only, no random."""
    x0, z0 = chunk[0] << 4, chunk[1] << 4
    for p in lay[1]:
        b = p.box
        if not (b[0] <= x0 + 15 and b[3] >= x0 and b[2] <= z0 + 15 and b[5] >= z0):
            continue
        for e in jigsaw._elements(p.el):
            if e.kind in ("single", "legacy"):
                place_template(world, g, e, p.pos, p.rot, chunk, p.el.proj)


def replay_chunk(seed, mc, lay, chunk, stop=None, done=(), deco_mc=None):
    """Replays the structure random of `chunk` through the start's pieces. Returns
    {(piece index, container index): loot seed} for the containers placed in it,
    and the features met (name, handled?)."""
    import scattered_loot as sc
    g = jigsaw._gen(mc)
    sk, pieces = lay[0], lay[1]
    top = "sand" if "desert" in sk else "grass_block"
    world = World(seed, mc, lay, top)
    world.deco = deco_mc or mc      # (29/10) the game version of the decorations (1.21.4 +)
    for nb in done:
        decorate_templates(world, g, lay, nb)
    r = sc._rng(seed, mc, chunk[0], chunk[1], sk if mc >= 22 else
                ("village" if sk.startswith("village") else "pillager_outpost"))
    seeds, feats = {}, []
    x0, z0 = chunk[0] << 4, chunk[1] << 4
    for pi, p in enumerate(pieces):
        b = p.box
        if not (b[0] <= x0 + 15 and b[3] >= x0 and b[2] <= z0 + 15 and b[5] >= z0):
            continue
        if p.el.kind == "feature":
            f = FEATURES.get(p.el.feature)
            feats.append((p.el.feature, f is not None))
            if f is not None:
                f(world, r, p.pos)
            continue
        for ci, (w, _b, _t) in enumerate(jigsaw.containers(g, p)):
            if (w[0] >> 4, w[2] >> 4) == chunk:
                seeds[(pi, ci)] = sc._next_long(r)
        for e in jigsaw._elements(p.el):
            if e.kind in ("single", "legacy"):
                place_template(world, g, e, p.pos, p.rot, chunk, p.el.proj)
        if stop is not None and pi >= stop:
            break
    return seeds, feats, world
