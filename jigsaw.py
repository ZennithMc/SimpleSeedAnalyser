"""
jigsaw.py — the jigsaw structures piece by piece (24/10): villages and pillager
outposts, as JigsawPlacement builds them (26.2 code, read in the jar).

  layout(seed, mc, key, cx, cz) -> Layout | None
      key: "village" (the type is chosen like ChunkGenerator.createStructures: weighted
      order of the "villages" set, the first whose start centre is in one of its biomes)
      or "pillager_outpost".
      Layout.pieces: [Piece(loc, kind, rot, box=(x0, y0, z0, x1, y1, z1), pos, proj)]

Random: the structure start WorldgenRandom (LCG setLargeFeatureSeed). Every draw is
replayed: Rotation.getRandom, pool.getRandomTemplate, then for each piece in placement
order (SequencedPriorityIterator: highest placement priority first, FIFO) its jigsaws
(Util.shuffle then stable sort by selection priority), and per jigsaw the shuffled
pool (+ fallback), Rotation.getShuffled per element, the target's shuffled jigsaws.
Heights: getFirstFreeHeight(WORLD_SURFACE_WG) = terrain.first_occupied + 1 (1.18+).
Free space: VoxelShape region = outer box minus the placed boxes (ONLY_SECOND test on
the box deflated by 0.25 = "fully inside the outer box, sharing no block with a hole").
"""
from __future__ import annotations

import gzip
import json
import os
from collections import deque
from functools import lru_cache

import stronghold_eyes as se

_DIR = os.path.dirname(os.path.abspath(__file__))
MIN_Y, MAX_Y = -64, 319
MC_FIRST, MC_LAST = 19, 35            # 1.16.1 → 26.3 (data per version: data_version)
MC_OLD = 22                           # below: the 1.16 / 1.17 village system (29/10)

# Direction: horizontal index for rotation (north 0, east 1, south 2, west 3)
_H = ["north", "east", "south", "west"]
_STEP = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0),
         "up": (0, 1, 0), "down": (0, -1, 0)}
_OPP = {"north": "south", "south": "north", "east": "west", "west": "east", "up": "down",
        "down": "up"}


def _rot_dir(d, rot):
    if d in ("up", "down"):
        return d
    return _H[(_H.index(d) + rot) & 3]


def _rot_pos(x, y, z, rot):
    """StructureTemplate.transform, pivot 0, no mirror."""
    if rot == 1:
        return -z, y, x
    if rot == 2:
        return -x, y, -z
    if rot == 3:
        return z, y, -x
    return x, y, z


def _jdiv2(a):
    """Java int division by 2 (toward zero)."""
    return -((-a) // 2) if a < 0 else a // 2


@lru_cache(maxsize=4)
def data(version="26.2"):
    p = os.path.join(_DIR, "loot_tables", f"jigsaw_{version}.json.gz")
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def _id(s):
    return s.split(":", 1)[1] if ":" in s else s


# ── pool elements ───────────────────────────────────────────────────────────────
class Elem:
    __slots__ = ("kind", "loc", "proj", "elems", "feature", "procs")

    def __init__(self, d):
        t = _id(d["element_type"])
        self.kind = {"single_pool_element": "single", "legacy_single_pool_element": "legacy",
                     "feature_pool_element": "feature", "empty_pool_element": "empty",
                     "list_pool_element": "list"}[t]
        self.loc = _id(d["location"]) if "location" in d else None
        self.proj = d.get("projection", "rigid")
        self.feature = _id(d["feature"]) if "feature" in d else None
        self.elems = [Elem(e) for e in d.get("elements", [])]
        self.procs = d.get("processors")
        if self.kind == "list":
            for e in self.elems:
                e.proj = self.proj


class Pool:
    def __init__(self, d, name):
        self.name = name
        self.fallback = _id(d.get("fallback", "minecraft:empty"))
        self.templates = []
        for e in d.get("elements", []):
            el = Elem(e["element"])
            self.templates += [el] * int(e["weight"])


class Jig:
    __slots__ = ("pos", "front", "top", "name", "target", "pool", "joint", "pp", "sp")


def data_version(mc):
    """The jigsaw data file that serves SSA version `mc` (cubiomes MC_*)."""
    mc = int(mc)
    if mc >= 35:                 # (30/10) 26.3: + the abandoned camps (same villages / outposts)
        return "26.3"
    if mc >= 25:                 # 1.20.6 → 26.2: identical pools / templates (checked)
        return "26.2"
    if mc == 24:
        return "1.19.4"
    if mc == 23:
        return "1.19.2"
    if mc == 22:
        return "1.18.2"
    if mc == 21:                 # pools dumped from the 1.17.1 jar's registries
        return "1.17.1"
    return "1.16.5"              # 1.16.1 → 1.16.5 (same layouts, checked on 1.16.1)


class Gen:
    def __init__(self, version="26.2"):
        d = data(version)
        self.version = version
        self.d = d
        self.T = d["templates"]
        self.pools = {k: Pool(v, k) for k, v in d["pools"].items()}
        self._max = {}

    # sizes, boxes, jigsaws
    def bbox(self, el, pos, rot):
        if el.kind in ("single", "legacy"):
            sx, sy, sz = self.T[el.loc]["size"]
            a = _rot_pos(0, 0, 0, rot)
            b = _rot_pos(sx - 1, sy - 1, sz - 1, rot)
            return (min(a[0], b[0]) + pos[0], min(a[1], b[1]) + pos[1], min(a[2], b[2]) + pos[2],
                    max(a[0], b[0]) + pos[0], max(a[1], b[1]) + pos[1], max(a[2], b[2]) + pos[2])
        if el.kind == "feature":
            return (pos[0], pos[1], pos[2], pos[0], pos[1], pos[2])
        if el.kind == "list":
            bs = [self.bbox(e, pos, rot) for e in el.elems if e.kind != "empty"]
            return (min(b[0] for b in bs), min(b[1] for b in bs), min(b[2] for b in bs),
                    max(b[3] for b in bs), max(b[4] for b in bs), max(b[5] for b in bs))
        return None

    def jigsaws(self, el, pos, rot, r):
        """getShuffledJigsawBlocks (draws from r for single / legacy)."""
        if el.kind == "list":
            return self.jigsaws(el.elems[0], pos, rot, r)
        if el.kind == "feature":
            j = Jig()
            j.pos, j.front, j.top = tuple(pos), "down", "south"
            j.name, j.target, j.pool, j.joint, j.pp, j.sp = "bottom", "empty", "empty", "rollable", 0, 0
            if self.version == "26.3":                # (30/10) 26.3: no name, attaches to any target
                j.name = None
            return [j]
        if el.kind == "empty":
            return []
        out = []
        for t in self.T[el.loc]["jig"]:
            j = Jig()
            x, y, z = _rot_pos(*t["pos"], rot)
            j.pos = (x + pos[0], y + pos[1], z + pos[2])
            f, tp = t["ori"].split("_")
            j.front, j.top = _rot_dir(f, rot), _rot_dir(tp, rot)
            j.name, j.target, j.pool = t["name"], t["target"], t["pool"]
            j.joint = t["joint"] or ("aligned" if f not in ("up", "down") else "rollable")
            j.pp, j.sp = t["pp"], t["sp"]
            out.append(j)
        _shuffle(out, r)
        out.sort(key=lambda j: -j.sp)               # stable
        return out

    def max_size(self, pool_name):
        if pool_name not in self._max:
            p = self.pools.get(pool_name)
            m = 0
            if p is not None:
                for el in p.templates:
                    if el.kind != "empty":
                        b = self.bbox(el, (0, 0, 0), 0)
                        m = max(m, b[4] - b[1] + 1)
            self._max[pool_name] = m
        return self._max[pool_name]


def _shuffle(lst, r):
    for i in range(len(lst), 1, -1):
        k = r.nextInt_bound(i)
        lst[i - 1], lst[k] = lst[k], lst[i - 1]


def _can_attach(s, t):
    return (s.front == _OPP[t.front] and (s.joint == "rollable" or s.top == t.top)
            and (t.name is None or s.target == t.name))


class Free:
    """outer box [x0, x1+1) … minus holes (inclusive block boxes)."""
    __slots__ = ("outer", "holes")

    def __init__(self, outer, holes=None):
        self.outer, self.holes = outer, list(holes or [])

    def fits(self, b):
        o = self.outer
        if not (b[0] >= o[0] and b[3] + 1 <= o[3] and b[1] >= o[1] and b[4] + 1 <= o[4]
                and b[2] >= o[2] and b[5] + 1 <= o[5]):
            return False
        for h in self.holes:
            if b[0] <= h[3] and h[0] <= b[3] and b[1] <= h[4] and h[1] <= b[4] \
                    and b[2] <= h[5] and h[2] <= b[5]:
                return False
        return True


class Piece:
    __slots__ = ("el", "pos", "rot", "box", "gld", "junc")

    def __init__(self, el, pos, rot, box, gld=1):
        self.el, self.pos, self.rot, self.box, self.gld = el, pos, rot, box, gld
        self.junc = []                    # JigsawJunction: (x, groundY, z, deltaY, proj)

    @property
    def loc(self):
        return self.el.loc if self.el.kind != "list" else self.el.elems[0].loc

    def __repr__(self):
        return f"Piece({self.el.kind}:{self.loc or self.el.feature}, rot {self.rot}, {self.box})"


def _ytop(seed, mc):
    import terrain
    ctx = terrain._cv_ctx(seed, mc)
    if ctx is None:
        return None
    lib = terrain._LIB
    cache = {}

    def first_free(x, z):
        k = (x, z)
        v = cache.get(k)
        if v is None:
            v = cache[k] = int(lib.cv_surface(ctx, int(x), int(z))) + 1
        return v
    first_free._hmode = 1                      # (31/10) the C engine's height mode
    return first_free


def _ytop116(seed, mc):
    """1.16 / 1.17 getFirstFreeHeight(WORLD_SURFACE_WG): the noise terrain (C t116)."""
    import ctypes as ct
    from cubiomes_wrap import get_lib
    f = getattr(get_lib()._lib, "ssa_t116_base_height", None)
    if f is None:                            # library not rebuilt
        return None
    f.argtypes = [ct.c_int, ct.c_uint64, ct.c_int32, ct.c_int32]
    f.restype = ct.c_int
    s64 = ct.c_uint64(int(seed) & ((1 << 64) - 1))
    if f(int(mc), s64, 0, 0) < 0:
        return None
    cache = {}

    def first_free(x, z):
        k = (x, z)
        v = cache.get(k)
        if v is None:
            v = cache[k] = int(f(int(mc), s64, int(x), int(z)))
        return v
    first_free._hmode = 2
    return first_free


def _type116(seed, mc, key, cx, cz, g, large=None):
    """1.16 / 1.17: the structure of the set listed by the biome at the chunk's
    centre (getNoiseBiome((cx << 2) + 2, 0, (cz << 2) + 2)), or None."""
    try:
        import struct_info
        from colormaps import BIOME_NAME_TO_ID
        lib = struct_info._lib(int(seed), int(mc), large, 0)
        b = int(lib.get_biome_at((cx << 4) + 8, (cz << 4) + 8, scale=4, y=0))
    except Exception:
        return None
    keys = [k for k in g.d["biomes"] if (k == key if key != "village" else k.startswith("village_"))]
    for sk in keys:
        if b in {BIOME_NAME_TO_ID.get(n, -1) for n in g.d["biomes"][sk]}:
            return sk
    return None


# ── (31/10) the C engine (cubiomes_src/jigsaw_engine.c) ─────────────────────────
USE_C = True
_DIRS = {"north": 0, "east": 1, "south": 2, "west": 3, "up": 4, "down": 5}


def _c_lib():
    try:
        from cubiomes_wrap import get_lib
        raw = get_lib()._lib
        return raw if hasattr(raw, "ssa_jig_generate") else None
    except Exception:
        return None


def _c_data(g, raw):
    """The data set of Gen g loaded in C once: (handle, elements, pool ids, names)."""
    h = getattr(g, "_c", None)
    if h is not None:
        return h
    import ctypes as ct
    names = {}

    def nid(n):
        if n is None:
            return -1
        if n not in names:
            names[n] = len(names)
        return names[n]
    pool_names = list(g.pools)
    pid = {n: i for i, n in enumerate(pool_names)}
    tnames = list(g.T)
    tid = {n: i for i, n in enumerate(tnames)}
    tpl, jig = [], []
    for n in tnames:
        t = g.T[n]
        j0 = len(jig) // 11
        for j in t["jig"]:
            f, tp = j["ori"].split("_")
            joint = j["joint"] or ("aligned" if f not in ("up", "down") else "rollable")
            jig += [j["pos"][0], j["pos"][1], j["pos"][2], _DIRS[f], _DIRS[tp], nid(j["name"]),
                    nid(j["target"]), pid.get(j["pool"], -1), 1 if joint == "rollable" else 0,
                    int(j["pp"]), int(j["sp"])]
        tpl += [t["size"][0], t["size"][1], t["size"][2], j0, len(t["jig"])]
    elems, eid, listel = [], {}, []

    def reg(e):
        k = id(e)
        if k in eid:
            return eid[k]
        i = eid[k] = len(elems)
        elems.append(e)
        return i
    erec = {}

    def build(e):
        i = reg(e)
        if i in erec:
            return i
        erec[i] = None
        kids = [build(c) for c in e.elems] if e.kind == "list" else []
        c0 = len(listel)
        listel.extend(kids)
        kind = {"single": 0, "legacy": 0, "feature": 1, "empty": 2, "list": 3}[e.kind]
        t = tid.get(e.loc, -1) if kind == 0 else -1
        if kind == 0 and t < 0:
            kind = 2
        erec[i] = [kind, t, 1 if e.proj == "rigid" else 0, c0, len(kids)]
        return i
    pool, poolel = [], []
    for n in pool_names:
        P = g.pools[n]
        e0 = len(poolel)
        poolel += [build(e) for e in P.templates]
        pool.append([e0, len(P.templates), pid.get(P.fallback, -1),
                     1 if (P.templates or n == "empty") else 0, g.max_size(n)])
    el = [v for i in range(len(elems)) for v in erec[i]]
    A = lambda xs: (ct.c_int32 * max(1, len(xs)))(*[int(v) for v in xs])
    flat = lambda xs: [v for x in xs for v in x]
    f = raw.ssa_jig_load
    f.restype = ct.c_void_p
    feat_name = -1 if g.version == "26.3" else nid("bottom")
    hd = f(len(tnames), A(tpl), len(jig) // 11, A(jig), len(elems), A(el), len(listel), A(listel),
           len(pool_names), A(flat(pool)), len(poolel), A(poolel),
           feat_name, nid("empty"), pid.get("empty", -1))
    if not hd:
        return None
    g._c = (hd, elems, pid, names)
    return g._c


def _generate_c(seed, mc, st, cx, cz, g, hmode):
    """generate() in C, or None when it cannot (old library, no terrain, data)."""
    import ctypes as ct
    raw = _c_lib()
    if raw is None:
        return None
    if hmode == 1:
        try:
            import desert_well
            desert_well.register_terrain(raw)
            if not raw.ssa_tf_ready():
                return None
        except Exception:
            return None
    cd = _c_data(g, raw)
    if cd is None:
        return None
    hd, elems, pid, names = cd
    sps = st.get("start_pools_1161") or [st["start_pool"]]
    starts = (ct.c_int32 * len(sps))(*[pid.get(_id(n), -1) for n in sps])
    md = st.get("max_distance_from_center", 80)
    mh = md if isinstance(md, int) else md["horizontal"]
    mv = md if isinstance(md, int) else md.get("vertical", 4064)
    sname = names.get(_id(st["start_jigsaw_name"]), -3) if st.get("start_jigsaw_name") else -2
    if sname == -3:
        return None
    cap, capj = 8192, 65536
    out = (ct.c_int32 * (13 * cap))()
    jout = (ct.c_int32 * (5 * capj))()
    cen = (ct.c_int32 * 3)()
    f = raw.ssa_jig_generate
    f.argtypes = [ct.c_void_p, ct.c_int, ct.c_uint64, ct.c_int, ct.c_int, ct.POINTER(ct.c_int32), ct.c_int,
                  ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_int, ct.c_int,
                  ct.POINTER(ct.c_int32), ct.c_int, ct.POINTER(ct.c_int32), ct.c_int, ct.POINTER(ct.c_int32)]
    f.restype = ct.c_int
    n = f(hd, int(mc), ct.c_uint64(int(seed) & ((1 << 64) - 1)), int(cx), int(cz), starts, len(sps),
          int(st.get("start_height", {}).get("absolute", 0)), int(st["size"]), int(mh), int(mv),
          int(sname), 1 if st.get("use_expansion_hack") else 0, 1 if st.get("legacy_1182") else 0,
          int(hmode), out, cap, jout, capj, cen)
    if n < 0:
        return None
    if n == 0:
        return False
    pieces = []
    k = 0
    for i in range(n):
        o = out[13 * i:13 * i + 13]
        p = Piece(elems[o[0]], (o[1], o[2], o[3]), o[4], tuple(o[5:11]), o[11])
        for _ in range(o[12]):
            jx, jy, jz, dy, rig = jout[5 * k:5 * k + 5]
            p.junc.append((jx, jy, jz, dy, "rigid" if rig else "terrain_matching"))
            k += 1
        pieces.append(p)
    return pieces, (cen[0], cen[1], cen[2])


def generate(seed, mc, struct_key, cx, cz, gen=None, height=None, hmode=None, size=None):
    """JigsawPlacement.addPieces for one structure (no biome check). Returns
    (pieces, centre (x, y, z)) or None. (31/10) In C when possible (hmode: 0 no
    height, 1 the 1.18+ terrain, 2 the 1.16 terrain; from the height function)."""
    g = gen or Gen()
    st = g.d["structures"][struct_key]
    if size is not None:                          # (05/11) size 0: the start piece and its
        st = dict(st, size=int(size))             # centre only (same draws until then)
    if USE_C:
        hm = getattr(height, "_hmode", None) if height is not None else (1 if int(mc) >= 22 else None)
        if hm is None:
            hm = hmode
        if hm is not None:
            got = _generate_c(seed, mc, st, cx, cz, g, hm)
            if got is False:
                return None
            if got is not None:
                return got
    height = height or _ytop(seed, mc)
    if height is None:
        return None
    r = se.JavaRandom(0)
    r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
    sh = st.get("start_height", {})
    if str(sh.get("type", "")).endswith("uniform"):  # (05/11) trial chambers: Y drawn first
        lo, hi = int(sh["min_inclusive"]["absolute"]), int(sh["max_inclusive"]["absolute"])
        y0 = r.nextInt_bound(hi - lo + 1) + lo
    else:
        y0 = int(sh.get("absolute", 0))
    pos = (cx << 4, y0, cz << 4)
    sp = st["start_pool"]
    if st.get("start_pools_1161"):                # (30/10) 1.16.1 bastions: the start pool first
        sps = st["start_pools_1161"]
        sp = sps[r.nextInt_bound(len(sps))]
    crot = r.nextInt_bound(4)                     # Rotation.getRandom
    pool = g.pools[_id(sp)]
    if not pool.templates:
        return None
    cel = pool.templates[r.nextInt_bound(len(pool.templates))]
    if cel.kind == "empty":
        return None
    vy = 0
    if st.get("start_jigsaw_name"):               # (30/10) ancient cities: the named jigsaw
        name = _id(st["start_jigsaw_name"])       # (getRandomNamedJigsaw) sits on the start
        jp = next((j.pos for j in g.jigsaws(cel, pos, crot, r) if j.name == name), None)
        if jp is None:
            return None
        vx, vy, vz = jp[0] - pos[0], jp[1] - pos[1], jp[2] - pos[2]
        pos = (pos[0] - vx, pos[1] - vy, pos[2] - vz)
    box = g.bbox(cel, pos, crot)
    centerX = _jdiv2(box[3] + box[0])
    centerZ = _jdiv2(box[5] + box[2])
    if st.get("start_jigsaw_name") or st.get("no_projection"):   # no heightmap: k = its Y
        bottomY = pos[1]
    else:
        bottomY = pos[1] + height(centerX, centerZ)
    dy = bottomY - (box[1] + 1)
    box = (box[0], box[1] + dy, box[2], box[3], box[4] + dy, box[5])
    cpiece = Piece(cel, (pos[0], pos[1] + dy, pos[2]), crot, box, 1)
    centerY = bottomY + vy
    pieces = [cpiece]
    depth_max = int(st["size"])
    if depth_max <= 0:
        return pieces, (centerX, centerY, centerZ)
    md = st.get("max_distance_from_center", 80)
    mh = md if isinstance(md, int) else md["horizontal"]
    mv = md if isinstance(md, int) else md.get("vertical", 4064)
    pad = int(st.get("dimension_padding", 0) or 0)       # (05/11) trial chambers: 10
    outer = (centerX - mh, max(centerY - mv, MIN_Y + pad), centerZ - mh,
             centerX + mh + 1, min(centerY + mv + 1, MAX_Y + 1 - pad), centerZ + mh + 1)
    free0 = Free(outer, [box])
    hack = bool(st.get("use_expansion_hack", False))
    legacy = bool(st.get("legacy_1182"))
    queues = {}                                   # priority -> deque

    def place_children(src, ctx_free, depth):
        el = src.el
        srigid = el.proj == "rigid"
        sbb = src.box
        sbox_y = sbb[1]
        src_free = None
        for sj in g.jigsaws(el, src.pos, src.rot, r):
            fx, fy, fz = _STEP[sj.front]
            tpos = (sj.pos[0] + fx, sj.pos[1] + fy, sj.pos[2] + fz)
            s_local_y = sj.pos[1] - sbox_y
            base_h = None
            tp = g.pools.get(sj.pool)
            if tp is None or (not tp.templates and sj.pool != "empty"):
                continue
            fb = g.pools.get(tp.fallback)
            if fb is None or (not fb.templates and tp.fallback != "empty"):
                continue
            inside = (sbb[0] <= tpos[0] <= sbb[3] and sbb[1] <= tpos[1] <= sbb[4]
                      and sbb[2] <= tpos[2] <= sbb[5])
            if inside:
                if src_free is None:
                    src_free = Free((sbb[0], sbb[1], sbb[2], sbb[3] + 1, sbb[4] + 1, sbb[5] + 1))
                cfree = src_free
            else:
                cfree = ctx_free
            targets = []
            shuf = _shuffled_fastutil if legacy else _shuffled
            if depth != depth_max:
                targets += shuf(tp.templates, r)
            targets += shuf(fb.templates, r)
            placed = False
            for tel in targets:
                if tel.kind == "empty":
                    break
                for trot in _shuffled([0, 1, 2, 3], r):
                    tjigs = g.jigsaws(tel, (0, 0, 0), trot, r)
                    hb = g.bbox(tel, (0, 0, 0), trot)
                    expand = 0
                    if hack and hb[4] - hb[1] + 1 <= 16:
                        for tj in tjigs:
                            sx, sy, sz = _STEP[tj.front]
                            q = (tj.pos[0] + sx, tj.pos[1] + sy, tj.pos[2] + sz)
                            if not (hb[0] <= q[0] <= hb[3] and hb[1] <= q[1] <= hb[4]
                                    and hb[2] <= q[2] <= hb[5]):
                                continue
                            cp = g.pools.get(tj.pool)
                            a = g.max_size(tj.pool) if cp is not None else 0
                            b = g.max_size(cp.fallback) if cp is not None else 0
                            expand = max(expand, a, b)
                    for tj in tjigs:
                        if not _can_attach(sj, tj):
                            continue
                        raw = (tpos[0] - tj.pos[0], tpos[1] - tj.pos[1], tpos[2] - tj.pos[2])
                        rbb = g.bbox(tel, raw, trot)
                        trigid = tel.proj == "rigid"
                        t_local_y = tj.pos[1]
                        dY = s_local_y - t_local_y + _STEP[sj.front][1]
                        if srigid and trigid:
                            tby = sbox_y + dY
                        else:
                            if base_h is None:
                                base_h = height(sj.pos[0], sj.pos[2])
                            tby = base_h - t_local_y
                        yo = tby - rbb[1]
                        tbb = (rbb[0], rbb[1] + yo, rbb[2], rbb[3], rbb[4] + yo, rbb[5])
                        tposn = (raw[0], raw[1] + yo, raw[2])
                        if expand > 0:
                            ns = max(expand + 1, tbb[4] - tbb[1])
                            if tbb[1] + ns > tbb[4]:
                                tbb = (tbb[0], tbb[1], tbb[2], tbb[3], tbb[1] + ns, tbb[5])
                        if not cfree.fits(tbb):
                            continue
                        cfree.holes.append(tbb)
                        sgld = src.gld
                        tgld = sgld - dY if trigid else 1
                        tp_ = Piece(tel, tposn, trot, tbb, tgld)
                        if srigid:
                            jy = sbox_y + s_local_y
                        elif trigid:
                            jy = tby + t_local_y
                        else:
                            if base_h is None:
                                base_h = height(sj.pos[0], sj.pos[2])
                            jy = base_h + int(dY / 2)
                        src.junc.append((tpos[0], jy - s_local_y + sgld, tpos[2], dY, tel.proj))
                        tp_.junc.append((sj.pos[0], jy - t_local_y + tgld, sj.pos[2], -dY, el.proj))
                        pieces.append(tp_)
                        if depth + 1 <= depth_max:
                            queues.setdefault(sj.pp, deque()).append((tp_, cfree, depth + 1))
                        placed = True
                        break
                    if placed:
                        break
                if placed:
                    break

    place_children(cpiece, free0, 0)
    while queues:
        p = max(k for k, q in queues.items() if q)
        piece, fr, dep = queues[p].popleft()
        if not queues[p]:
            del queues[p]
        place_children(piece, fr, dep)
    return pieces, (centerX, centerY, centerZ)


def _shuffled_fastutil(lst, r):
    """1.18.2 getShuffledTemplates: fastutil ObjectArrays.shuffle (i from n-1 down to
    0, nextInt(i + 1))."""
    c = list(lst)
    for i in range(len(c) - 1, -1, -1):
        k = r.nextInt_bound(i + 1)
        c[i], c[k] = c[k], c[i]
    return c


def _shuffled(lst, r):
    c = list(lst)
    _shuffle(c, r)
    return c


# ── which structure of the set, and does it exist ──────────────────────────────
_SET_OF = {"village": "villages", "pillager_outpost": "pillager_outposts",
           "abandoned_camp": "abandoned_camp"}                    # (30/10) 26.3


def _biome_ok(seed, mc, struct_key, centre, g, large=None):
    try:
        import struct_info
        from colormaps import BIOME_NAME_TO_ID
        lib = struct_info._lib(int(seed), int(mc), large, 0)
        b = int(lib.get_biome_at(centre[0], centre[2], scale=4, y=centre[1]))
        return b in {BIOME_NAME_TO_ID.get(n, -1) for n in g.d["biomes"][struct_key]}
    except Exception:
        return True


_LAYOUTS = {}


def _check_pos(seed, mc, g, sk, cx, cz, centre):
    """Where the game tests the biome: the start piece's centre at its ground height
    (1.18.2 too: JigsawPlacement tests getNoiseBiome(centerX, y, centerZ))."""
    return centre


def layout(seed, mc, key, cx, cz, large=None):
    """(struct_key, pieces, centre) of the village / outpost started in chunk (cx, cz),
    or None (no valid biome at the start centre for any type). Versions with the
    same pools / templates / code as the 26.2 jar: 1.21.11, 26.1, 26.2 (checked)."""
    if not (MC_FIRST <= int(mc) <= MC_LAST):
        return None
    k = (int(seed), int(mc), key, int(cx), int(cz), large)
    if k in _LAYOUTS:
        return _LAYOUTS[k]
    g = _gen(mc)
    if int(mc) < MC_OLD:
        res = None
        sk = _type116(seed, mc, key, cx, cz, g, large)
        height = _ytop116(seed, mc) if sk else None
        if height is not None:
            got = generate(seed, mc, sk, cx, cz, g, height)
            if got:
                res = (sk, got[0], got[1])
        if len(_LAYOUTS) > 256:
            _LAYOUTS.clear()
        _LAYOUTS[k] = res
        return res
    height = _ytop(seed, mc)
    if height is None:
        return None
    entries = list(g.d["sets"][_SET_OF[key]]["structures"])
    res = None
    if len(entries) == 1:
        sk = _id(entries[0]["structure"])
        got = generate(seed, mc, sk, cx, cz, g, height)
        if got and _biome_ok(seed, mc, sk, _check_pos(seed, mc, g, sk, cx, cz, got[1]), g, large):
            res = (sk, got[0], got[1])
    else:
        r = se.JavaRandom(0)
        r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
        total = sum(int(e["weight"]) for e in entries)
        while entries:
            choice = r.nextInt_bound(total)
            idx = 0
            for e in entries:
                choice -= int(e["weight"])
                if choice < 0:
                    break
                idx += 1
            sel = entries[idx]
            sk = _id(sel["structure"])
            # (05/11) the biome is tested at the start piece's centre: find it alone
            # first (size 0), the whole village only for the type that is kept
            got = generate(seed, mc, sk, cx, cz, g, height, size=0)
            if got and _biome_ok(seed, mc, sk, _check_pos(seed, mc, g, sk, cx, cz, got[1]), g, large):
                got = generate(seed, mc, sk, cx, cz, g, height)
                if got:
                    res = (sk, got[0], got[1])
                    break
            entries.pop(idx)
            total -= int(sel["weight"])
    if len(_LAYOUTS) > 256:
        _LAYOUTS.clear()
    _LAYOUTS[k] = res
    return res


def layout_type(seed, mc, key, cx, cz, large=None):
    """(05/11) the structure key layout() would keep ("village_desert"…) or None: the
    same draws and biome tests, the start piece only (generate size 0)."""
    if not (MC_OLD <= int(mc) <= MC_LAST):
        return None
    g = _gen(mc)
    height = _ytop(seed, mc)
    if height is None:
        return None
    entries = list(g.d["sets"][_SET_OF[key]]["structures"])
    if len(entries) == 1:
        sk = _id(entries[0]["structure"])
        got = generate(seed, mc, sk, cx, cz, g, height, size=0)
        return sk if got and _biome_ok(seed, mc, sk, _check_pos(seed, mc, g, sk, cx, cz, got[1]),
                                       g, large) else None
    r = se.JavaRandom(0)
    r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
    total = sum(int(e["weight"]) for e in entries)
    while entries:
        choice = r.nextInt_bound(total)
        idx = 0
        for e in entries:
            choice -= int(e["weight"])
            if choice < 0:
                break
            idx += 1
        sel = entries[idx]
        sk = _id(sel["structure"])
        got = generate(seed, mc, sk, cx, cz, g, height, size=0)
        if got and _biome_ok(seed, mc, sk, _check_pos(seed, mc, g, sk, cx, cz, got[1]), g, large):
            return sk
        entries.pop(idx)
        total -= int(sel["weight"])
    return None


_GEN = {}


def _gen(mc=None):
    v = "26.2" if mc is None else data_version(mc)
    if v not in _GEN:
        _GEN[v] = Gen(v)
    return _GEN[v]


def piece_box(g, p):
    """The template's own box (without the expansion hack's extra height)."""
    return g.bbox(p.el, p.pos, p.rot)


def _kind(loc):
    if not loc:
        return "deco"
    if "/streets/" in loc or "/terminators/" in loc:
        return "street"
    if "town_centers" in loc or "base_plates" in loc:
        return "center"
    if "/decor" in loc or "lamp" in loc or "/trees" in loc:
        return "deco"
    return "house"


def map_boxes(seed, mc, key, cx, cz, large=None):
    """[(x0, z0, x1, z1, kind, y0, y1)] of the pieces worth drawing (no 1-block
    feature / entity pieces), kind: street, center, house, deco. [] if none."""
    lay = layout(seed, mc, key, cx, cz, large)
    if lay is None:
        return []
    g = _gen(mc)
    out = []
    for p in lay[1]:
        if p.el.kind == "feature":
            continue
        b = piece_box(g, p)
        if b is None or (b[0] == b[3] and b[2] == b[5]):
            continue
        out.append((b[0], b[2], b[3], b[5], _kind(p.loc), b[1], b[4]))
    return out


# ── loot (24/10) ─────────────────────────────────────────────────────────────────
def _elements(el):
    return el.elems if el.kind == "list" else [el]


def _mth_seed(x, y, z):
    def i32(v):
        v &= 0xFFFFFFFF
        return v - (1 << 32) if v >= 1 << 31 else v
    s = (i32(x * 3129871) ^ (z * 116129781) ^ y)
    s = (s * s * 42317861 + s * 11)
    s = ((s + (1 << 63)) % (1 << 64)) - (1 << 63)
    return s >> 16


def _rot_integrity(g, e):
    """block_rot integrity of the element's processors that applies to containers
    (no rottable_blocks tag), or None."""
    pr = e.procs
    if isinstance(pr, str):
        pr = g.d.get("processors", {}).get(_id(pr))
    if not isinstance(pr, dict):
        return None
    for q in pr.get("processors", []):
        if _id(q.get("processor_type", "")) == "block_rot" and "rottable_blocks" not in q:
            return float(q["integrity"])
    return None


def _kept(integrity, pos):
    """BlockRotProcessor: RandomSource.create(Mth.getSeed(pos)).nextFloat() <= integrity."""
    r = se.JavaRandom(_mth_seed(*pos) & ((1 << 64) - 1))
    import struct
    f = struct.unpack("f", struct.pack("f", r.nextFloat()))[0]
    return f <= struct.unpack("f", struct.pack("f", integrity))[0]


def containers(g, p):
    """[(world (x, y, z), block, table)] of a piece, in placeInWorld order (a
    container its block_rot processor removes is left out: no draw)."""
    out = []
    for e in _elements(p.el):
        if e.kind not in ("single", "legacy"):
            continue
        integ = _rot_integrity(g, e)
        for c in g.T[e.loc]["cont"]:
            x, y, z = _rot_pos(*c["pos"], p.rot)
            w = (x + p.pos[0], y + p.pos[1], z + p.pos[2])
            if integ is not None and not _kept(integ, w):
                continue
            out.append((w, c["block"], c["table"]))
    return out


_DIRS4 = ("north", "east", "south", "west")


def container_facings(g, p):
    """(31/10) {world (x, y, z): facing after the piece's rotation} of a piece's
    containers (the double chests: two halves side by side, same facing)."""
    out = {}
    for e in _elements(p.el):
        if e.kind not in ("single", "legacy"):
            continue
        for c in g.T[e.loc]["cont"]:
            f = c.get("facing")
            x, y, z = _rot_pos(*c["pos"], p.rot)
            w = (x + p.pos[0], y + p.pos[1], z + p.pos[2])
            if f in _DIRS4:
                f = _DIRS4[(_DIRS4.index(f) + (p.rot or 0)) % 4]
            out[w] = f
    return out


_NAMES = {"pillager_outpost": "Outpost chest",
          "abandoned_camp_common_chest": "Camp chest",        # (30/10) 26.3 camps
          "abandoned_camp_barrel": "Camp barrel",
          "abandoned_camp_secret_chest": "Secret copper chest"}


def _simple_seed(seed, mc, sk, key, plist, pi, ci, ch):
    """Without the world replay: containers + trees only (assumes the trees grow,
    skips the other features)."""
    import scattered_loot as sc
    r = sc._rng(seed, mc, ch[0], ch[1], sk if mc >= 22 else
                ("village" if key == "village" else "pillager_outpost"))
    for pj, (q, qcs) in enumerate(plist):
        b = q.box
        if not (b[0] <= (ch[0] << 4) + 15 and b[3] >= ch[0] << 4
                and b[2] <= (ch[1] << 4) + 15 and b[5] >= ch[1] << 4):
            continue
        if q.el.kind == "feature":
            sim = _FEATURE_DRAWS.get(q.el.feature)
            if sim is not None:
                sim(r)
            continue
        for cj, ((qx, qy, qz), _b, _t) in enumerate(qcs):
            if (qx >> 4, qz >> 4) != ch:
                continue
            v = sc._next_long(r)
            if pj == pi and cj == ci:
                return v
    return None


# The village trees (TreeFeature, 26.2 configs): the draws they make from the chunk
# random, assuming the tree grows (sapling can survive, room for its full height).
def _ni(r, n):
    import scattered_loot as sc
    return sc._next_int(r, n)


def _oak(r):            # straight 4+[0,2]+[0,0], blob foliage r2 h3: 4 rows × 4 corners
    _ni(r, 3); _ni(r, 1)
    for _ in range(16):
        _ni(r, 2)


def _spruce(r):         # straight 5+[0,2]+[0,1], spruce foliage
    _ni(r, 3); _ni(r, 2)                          # height
    _ni(r, 2)                                     # foliageHeight: trunk_height uniform 1-2
    _ni(r, 2)                                     # radius uniform 2-3
    _ni(r, 3)                                     # offset uniform 0-2
    _ni(r, 2)                                     # first row radius


def _pine(r):           # straight 6+[0,4]+[0,0], pine foliage
    h = 6 + _ni(r, 5) + _ni(r, 1)
    fh = 3 + _ni(r, 2)                            # height uniform 3-4
    _ni(r, max(h - fh + 1, 1))                    # radius 1 + nextInt(trunkHeight + 1)


def _acacia(r):         # forking 5+[0,2]+[0,2], acacia foliage (no draws)
    _ni(r, 3); _ni(r, 3)
    lean = _ni(r, 4)
    _ni(r, 4); _ni(r, 3)
    if _ni(r, 4) != lean:
        _ni(r, 2); _ni(r, 3)


_FEATURE_DRAWS = {"oak": _oak, "spruce": _spruce, "pine": _pine, "acacia": _acacia}


def _no_feature_before(plist, pi, ch):
    """no FEATURE piece before piece pi reaches chunk ch: the container's seed needs no
    world replay (_simple_seed = the replay's: checked on 26.3 / 1.18 – 1.21 villages)"""
    for q, _qcs in plist[:pi]:
        b = q.box
        if q.el.kind == "feature" and (b[0] <= (ch[0] << 4) + 15 and b[3] >= ch[0] << 4
                                       and b[2] <= (ch[1] << 4) + 15 and b[5] >= ch[1] << 4):
            return False
    return True


def chests(seed, mc, key, x, z, ver, fast=False):
    """Loot cards of the village / outpost at (x, z). The seed of a container =
    the chunk's structure random after every draw of the pieces before it in that
    chunk (pieces in start order; one nextLong per RandomizableContainer of a
    template placed in the chunk, barrels without loot included). The FEATURE
    pieces (trees, hay piles, flowers…) placed before in the same chunk use that
    random too: village_world.py replays them on the terrain + village blocks."""
    import loot_engine as le
    cx, cz = int(x) >> 4, int(z) >> 4
    lay = layout(seed, mc, key, cx, cz)
    if lay is None:
        return None
    sk, pieces, _c = lay
    g = _gen(mc)
    plist = [(p, containers(g, p)) for p in pieces]
    import village_world as vw
    reps = {}
    # (29/10) "Java 1.21" = 1.21.4 → 1.21.8: the loot sub-version tells the decorations
    # (1.21.5+: cactus flowers on the village cactus patches)
    deco = {"1.21.4": 28, "1.21.5": 29, "1.21.8": 30, "1.21.10": 31, "1.21.11": 32}.get(ver) \
        if int(mc) in (28, 32) else None

    def replay(ch, done=()):
        k = (ch, done)
        if int(mc) < MC_OLD:                      # 1.16 / 1.17: no world replay
            return None
        if k not in reps:
            try:
                reps[k] = vw.replay_chunk(seed, mc, lay, ch, done=done, deco_mc=deco)[:2]
            except Exception:
                reps[k] = None
        return reps[k]

    if fast and int(mc) >= MC_OLD:           # (07/11) the loot finder: no world replay
        out = []
        for pi, (p, cs) in enumerate(plist):
            for ci, ((wx, wy, wz), blk, table) in enumerate(cs):
                if not table:
                    continue
                ch = (wx >> 4, wz >> 4)
                tname = table.replace("chests/", "").replace("barrels/", "")
                if _no_feature_before(plist, pi, ch):
                    ls = _simple_seed(seed, mc, sk, key, plist, pi, ci, ch)
                    slots, approx = le.fill_chest(tname, ls, ver)
                else:                            # its seed needs the replay: unknown here
                    ls, slots, approx = None, le.UNKNOWN, []
                out.append(dict(name="Chest", pos=(wx, wy, wz), table=table, seed=ls,
                                slots=slots, approx=approx, _tname=tname))
        return out or [dict(name="No chest", pos=(cx * 16, None, cz * 16), table="", seed=None,
                            slots=None, approx=[])]
    cards = {}
    for pi, (p, cs) in enumerate(plist):
        for ci, ((wx, wy, wz), blk, table) in enumerate(cs):
            if not table:
                continue
            ch = (wx >> 4, wz >> 4)
            rp = replay(ch)
            ls = None if rp is None else rp[0].get((pi, ci))
            if rp is None:                        # no cv_chunk_ex (old terrain lib)
                ls = _simple_seed(seed, mc, sk, key, plist, pi, ci, ch)
            feats = [] if rp is None else [f for f, _ok in rp[1]]
            # the features before it in its chunk (the replay met them all)
            before = []
            for pj, q in enumerate(pieces[:pi]):
                b = q.box
                if q.el.kind == "feature" and b[0] >> 4 == ch[0] and b[2] >> 4 == ch[1]:
                    before.append(q.el.feature)
            tname = table.replace("chests/", "").replace("barrels/", "")
            slots, approx = le.fill_chest(tname, ls, ver)
            base = tname.split("/")[-1].replace("village_", "").replace("_", " ")
            nm = _NAMES.get(tname) or _NAMES.get(key) or (base.capitalize() + " chest")
            note = None
            alts = []
            if before and int(mc) < MC_OLD:
                note = ("Generated after " + ", ".join(sorted(set(before))).replace("_", " ")
                        + " in its chunk: their draws are estimated (trees assumed to grow, "
                          "other decorations skipped) — often wrong in 1.16 / 1.17 "
                          "(≈ 1 chest in 3 exact on the servers).")
            elif before:
                note = ("Generated after " + ", ".join(sorted(set(before))).replace("_", " ")
                        + " in its chunk: their draws are replayed on the terrain and the "
                          "structure blocks (≈ 90 % exact on the 26.2 server; the decorations "
                          "of the chunks around, if generated first, can change it).")
                for a_ in (-1, 0, 1):
                    for b_ in (-1, 0, 1):
                        if (a_, b_) == (0, 0):
                            continue
                        nb = (ch[0] + a_, ch[1] + b_)
                        v = replay(ch, (nb,))
                        s2 = None if v is None else v[0].get((pi, ci))
                        if s2 is not None and s2 != ls and s2 not in [x_[1] for x_ in alts]:
                            alts.append((nb, s2))
            # two templates of a list element at the same spot: the last one stays
            cards[(wx, wy, wz)] = dict(name=nm, pos=(wx, wy, wz), table=table, seed=ls,
                                       slots=slots, approx=approx, note=note,
                                       exact=not before, _alts=alts, _tname=tname)
    out = []
    for c in cards.values():
        alts = c.pop("_alts")
        tname = c.pop("_tname")
        out.append(c)
        for nb, s2 in alts:
            sl2, ap2 = le.fill_chest(tname, s2, ver)
            out.append(dict(name=c["name"] + f" — if chunk {nb[0]} {nb[1]} was generated first",
                            pos=c["pos"], table=c["table"], seed=s2, slots=sl2, approx=ap2,
                            note=None, exact=False))
    if not out:
        return [dict(name="No chest", pos=(cx * 16, None, cz * 16), table="", seed=None,
                     slots=None, approx=[], note="No loot chest in this " +
                     {"pillager_outpost": "outpost.", "abandoned_camp": "camp."}.get(key, "village."))]
    return out
