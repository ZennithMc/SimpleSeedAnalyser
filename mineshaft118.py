"""
mineshaft118.py — abandoned mineshafts of Minecraft 1.18 → 26.x (24/10).

Layout: the 1.16 one (mineshaft116.c: room, corridors, crossings, stairs,
depth <= 8, radius 80) with the 1.18 start: setLargeFeatureSeed (legacy LCG),
one nextDouble (the frequency test) before the room, then
moveBelowSeaLevel(63, minY -64, random, 10); mesa: from the surface height
(WORLD_SURFACE_WG at the centre: randomBetweenInclusive(sea, height)).

Placement in a chunk (the air the mineshaft leaves before the strongholds):
see place_chunk.
"""
from __future__ import annotations

M64 = (1 << 64) - 1
N, S, W, E, NONE = 0, 1, 2, 3, 4
ROOM, CORRIDOR, CROSSING, STAIRS = 0, 1, 2, 3


class Lcg:
    __slots__ = ("s",)

    def __init__(self, seed=0):
        self.s = (seed ^ 0x5DEECE66D) & ((1 << 48) - 1)

    def set_seed(self, seed):
        self.s = (seed ^ 0x5DEECE66D) & ((1 << 48) - 1)

    def next(self, bits):
        self.s = (self.s * 0x5DEECE66D + 0xB) & ((1 << 48) - 1)
        v = self.s >> (48 - bits)
        if bits == 32 and v >> 31:
            v -= 1 << 32
        return v

    def next_int(self, n):
        if n & -n == n:
            return (n * (self.next(31) & 0x7FFFFFFF)) >> 31
        while True:
            b = self.next(31) & 0x7FFFFFFF
            v = b % n
            if b - v + (n - 1) < (1 << 31):
                return v

    def next_long(self):
        return ((self.next(32) << 32) + self.next(32)) & M64

    def next_double(self):
        return (((self.next(26) & ((1 << 26) - 1)) << 27) + (self.next(27) & ((1 << 27) - 1))) * (1.0 / (1 << 53))

    def next_float(self):
        return (self.next(24) & 0xFFFFFF) / float(1 << 24)

    def next_bool(self):
        return self.next(1) != 0


def _s64(v):
    v &= M64
    return v - (1 << 64) if v >> 63 else v


def large_feature_seed(r: Lcg, seed, cx, cz):
    r.set_seed(seed)
    a = r.next_long()
    b = r.next_long()
    r.set_seed((_s64(cx * _s64(a)) ^ _s64(cz * _s64(b)) ^ seed) & M64)


class Piece:
    __slots__ = ("bb", "type", "facing", "depth", "rails", "webs", "two", "dir", "length", "spawner")

    def __init__(self, t, bb, facing, depth):
        self.type, self.bb, self.facing, self.depth = t, list(bb), facing, depth
        self.rails = self.webs = self.two = False
        self.dir = facing
        self.length = 0
        self.spawner = False


def _isect(a, b):
    return a[3] >= b[0] and a[0] <= b[3] and a[5] >= b[2] and a[2] <= b[5] and a[4] >= b[1] and a[1] <= b[4]


class _Lay:
    def __init__(self, r):
        self.r = r
        self.p = []
        self.ents = []

    def overlaps(self, b):
        return any(_isect(q.bb, b) for q in self.p)

    def random_jigsaw(self, x, y, z, d, depth):
        r = self.r
        m = r.next_int(100)
        if m >= 80:
            b = [x, y, z, x, y + 2, z]
            if r.next_int(4) == 0:
                b[4] += 4
            if d == S:
                b[0], b[3], b[5] = x - 1, x + 3, z + 4
            elif d == W:
                b[0], b[2], b[5] = x - 4, z - 1, z + 3
            elif d == E:
                b[3], b[2], b[5] = x + 4, z - 1, z + 3
            else:
                b[0], b[3], b[2] = x - 1, x + 3, z - 4
            if self.overlaps(b):
                return None
            pc = Piece(CROSSING, b, NONE, depth)
            pc.dir = d
            pc.two = (b[4] - b[1] + 1) > 3
        elif m >= 70:
            b = [x, y - 5, z, x, y + 2, z]
            if d == S:
                b[3], b[5] = x + 2, z + 8
            elif d == W:
                b[0], b[5] = x - 8, z + 2
            elif d == E:
                b[3], b[5] = x + 8, z + 2
            else:
                b[3], b[2] = x + 2, z - 8
            if self.overlaps(b):
                return None
            pc = Piece(STAIRS, b, d, depth)
        else:
            b = [x, y, z, x, y + 2, z]
            l = r.next_int(3) + 2
            while l > 0:
                mm = l * 5
                if d == S:
                    b[3], b[5] = x + 2, z + mm - 1
                elif d == W:
                    b[0], b[5] = x - (mm - 1), z + 2
                elif d == E:
                    b[3], b[5] = x + mm - 1, z + 2
                else:
                    b[3], b[2] = x + 2, z - (mm - 1)
                if not self.overlaps(b):
                    break
                l -= 1
            if l <= 0:
                return None
            pc = Piece(CORRIDOR, b, d, depth)
            pc.rails = r.next_int(3) == 0
            pc.webs = not pc.rails and r.next_int(23) == 0
            pc.length = (b[5] - b[2] + 1) // 5 if d in (N, S) else (b[3] - b[0] + 1) // 5
        self.p.append(pc)
        return pc

    def try_jigsaw(self, x, y, z, d, depth):
        if depth > 8:
            return None
        rb = self.p[0].bb
        if abs(x - rb[0]) > 80 or abs(z - rb[2]) > 80:
            return None
        pc = self.random_jigsaw(x, y, z, d, depth + 1)
        if pc is not None:
            self.place(pc)
        return pc

    def place(self, pc):
        if pc.type == CORRIDOR:
            self.place_corridor(pc)
        elif pc.type == CROSSING:
            self.place_crossing(pc)
        elif pc.type == STAIRS:
            self.place_stairs(pc)

    def place_room(self):
        r = self.r
        room = self.p[0]
        rb = room.bb
        i = room.depth
        cX, cY, cZ = rb[3] - rb[0] + 1, rb[4] - rb[1] + 1, rb[5] - rb[2] + 1
        j = cY - 3 - 1
        if j <= 0:
            j = 1
        for side in range(4):
            span = cX if side < 2 else cZ
            k = 0
            while k < span:
                k += r.next_int(span)
                if k + 3 > span:
                    break
                y = rb[1] + r.next_int(j) + 1
                if side == 0:
                    c = self.try_jigsaw(rb[0] + k, y, rb[2] - 1, N, i)
                    e = c and [c.bb[0], c.bb[1], rb[2], c.bb[3], c.bb[4], rb[2] + 1]
                elif side == 1:
                    c = self.try_jigsaw(rb[0] + k, y, rb[5] + 1, S, i)
                    e = c and [c.bb[0], c.bb[1], rb[5] - 1, c.bb[3], c.bb[4], rb[5]]
                elif side == 2:
                    c = self.try_jigsaw(rb[0] - 1, y, rb[2] + k, W, i)
                    e = c and [rb[0], c.bb[1], c.bb[2], rb[0] + 1, c.bb[4], c.bb[5]]
                else:
                    c = self.try_jigsaw(rb[3] + 1, y, rb[2] + k, E, i)
                    e = c and [rb[3] - 1, c.bb[1], c.bb[2], rb[3], c.bb[4], c.bb[5]]
                if e:
                    self.ents.append(e)
                k += 4

    def place_corridor(self, pc):
        r = self.r
        b = list(pc.bb)
        i = pc.depth
        j = r.next_int(4)
        f = pc.facing
        y = b[1] - 1 + r.next_int(3)
        if f == S:
            if j <= 1:
                self.try_jigsaw(b[0], y, b[5] + 1, f, i)
            elif j == 2:
                self.try_jigsaw(b[0] - 1, y, b[5] - 3, W, i)
            else:
                self.try_jigsaw(b[3] + 1, y, b[5] - 3, E, i)
        elif f == W:
            if j <= 1:
                self.try_jigsaw(b[0] - 1, y, b[2], f, i)
            elif j == 2:
                self.try_jigsaw(b[0], y, b[2] - 1, N, i)
            else:
                self.try_jigsaw(b[0], y, b[5] + 1, S, i)
        elif f == E:
            if j <= 1:
                self.try_jigsaw(b[3] + 1, y, b[2], f, i)
            elif j == 2:
                self.try_jigsaw(b[3] - 3, y, b[2] - 1, N, i)
            else:
                self.try_jigsaw(b[3] - 3, y, b[5] + 1, S, i)
        else:
            if j <= 1:
                self.try_jigsaw(b[0], y, b[2] - 1, f, i)
            elif j == 2:
                self.try_jigsaw(b[0] - 1, y, b[2], W, i)
            else:
                self.try_jigsaw(b[3] + 1, y, b[2], E, i)
        if i >= 8:
            return
        if f in (N, S):
            k = b[2] + 3
            while k + 3 <= b[5]:
                l = r.next_int(5)
                if l == 0:
                    self.try_jigsaw(b[0] - 1, b[1], k, W, i + 1)
                elif l == 1:
                    self.try_jigsaw(b[3] + 1, b[1], k, E, i + 1)
                k += 5
        else:
            m = b[0] + 3
            while m + 3 <= b[3]:
                n = r.next_int(5)
                if n == 0:
                    self.try_jigsaw(m, b[1], b[2] - 1, N, i + 1)
                elif n == 1:
                    self.try_jigsaw(m, b[1], b[5] + 1, S, i + 1)
                m += 5

    def place_crossing(self, pc):
        b = list(pc.bb)
        i = pc.depth
        d = pc.dir
        if d == S:
            self.try_jigsaw(b[0] + 1, b[1], b[5] + 1, S, i)
            self.try_jigsaw(b[0] - 1, b[1], b[2] + 1, W, i)
            self.try_jigsaw(b[3] + 1, b[1], b[2] + 1, E, i)
        elif d == W:
            self.try_jigsaw(b[0] + 1, b[1], b[2] - 1, N, i)
            self.try_jigsaw(b[0] + 1, b[1], b[5] + 1, S, i)
            self.try_jigsaw(b[0] - 1, b[1], b[2] + 1, W, i)
        elif d == E:
            self.try_jigsaw(b[0] + 1, b[1], b[2] - 1, N, i)
            self.try_jigsaw(b[0] + 1, b[1], b[5] + 1, S, i)
            self.try_jigsaw(b[3] + 1, b[1], b[2] + 1, E, i)
        else:
            self.try_jigsaw(b[0] + 1, b[1], b[2] - 1, N, i)
            self.try_jigsaw(b[0] - 1, b[1], b[2] + 1, W, i)
            self.try_jigsaw(b[3] + 1, b[1], b[2] + 1, E, i)
        if pc.two:
            r = self.r
            if r.next_bool():
                self.try_jigsaw(b[0] + 1, b[1] + 4, b[2] - 1, N, i)
            if r.next_bool():
                self.try_jigsaw(b[0] - 1, b[1] + 4, b[2] + 1, W, i)
            if r.next_bool():
                self.try_jigsaw(b[3] + 1, b[1] + 4, b[2] + 1, E, i)
            if r.next_bool():
                self.try_jigsaw(b[0] + 1, b[1] + 4, b[5] + 1, S, i)

    def place_stairs(self, pc):
        b = pc.bb
        i = pc.depth
        f = pc.facing
        if f == S:
            self.try_jigsaw(b[0], b[1], b[5] + 1, S, i)
        elif f == W:
            self.try_jigsaw(b[0] - 1, b[1], b[2], W, i)
        elif f == E:
            self.try_jigsaw(b[3] + 1, b[1], b[2], E, i)
        else:
            self.try_jigsaw(b[0], b[1], b[2] - 1, N, i)


def layout(seed, cx, cz, mesa=False, min_y=-64, sea=63, mc=26):
    return layout_ex(seed, cx, cz, mesa, min_y, sea, mc)[0]


_LAYOUT_CACHE = {}


def layout_ex(seed, cx, cz, mesa=False, min_y=-64, sea=63, mc=26, height=None):
    """The pieces of the start of chunk (cx, cz) (Piece list, p[0] the room),
    moved vertically (mesa: not moved, Y unknown). (29/10) cached: treat as
    read-only (the spawners placed: SPIDER_DONE)."""
    k = (int(seed) & M64, cx, cz, bool(mesa), min_y, sea, mc >= 23, height is not None and mesa)
    v = _LAYOUT_CACHE.get(k)
    if v is None:
        v = _layout_ex(seed, cx, cz, mesa, min_y, sea, mc, height)
        if len(_LAYOUT_CACHE) > 2048:
            _LAYOUT_CACHE.clear()
        _LAYOUT_CACHE[k] = v
    return v


def _layout_ex(seed, cx, cz, mesa=False, min_y=-64, sea=63, mc=26, height=None):
    r = Lcg()
    large_feature_seed(r, seed & M64, cx, cz)
    if mc >= 23:
        r.next_double()                              # 1.19+: the frequency test, same random
    x, z = (cx << 4) + 2, (cz << 4) + 2
    ax, ay, az = r.next_int(6), r.next_int(6), r.next_int(6)
    L = _Lay(r)
    L.p.append(Piece(ROOM, (x, 50, z, x + 7 + ax, 54 + ay, z + 7 + az), NONE, 0))
    L.place_room()
    if mesa:
        # offset from the surface: WORLD_SURFACE_WG at the centre of the whole box
        x0 = min(q.bb[0] for q in L.p); x1 = max(q.bb[3] for q in L.p)
        y0 = min(q.bb[1] for q in L.p); y1 = max(q.bb[4] for q in L.p)
        z0 = min(q.bb[2] for q in L.p); z1 = max(q.bb[5] for q in L.p)
        cx_, cy_, cz_ = x0 + (x1 - x0 + 1) // 2, y0 + (y1 - y0 + 1) // 2, z0 + (z1 - z0 + 1) // 2
        k = height(cx_, cz_) if height else None
        if k is None:
            return L.p, None                     # Y unknown
        l = sea if k <= sea else r.next_int(k - sea + 1) + sea
        dy = l - cy_
        for q in L.p:
            q.bb[1] += dy
            q.bb[4] += dy
        for e in L.ents:
            e[1] += dy
            e[4] += dy
    else:
        y0 = min(q.bb[1] for q in L.p)
        y1 = max(q.bb[4] for q in L.p)
        i = sea - 10
        j = (y1 - y0 + 1) + min_y + 1
        if j < i:
            j += r.next_int(i - j)
        dy = j - y1
        for q in L.p:
            q.bb[1] += dy
            q.bb[4] += dy
        for e in L.ents:
            e[1] += dy
            e[4] += dy
    return L.p, L.ents


# ───────────────────────── placement in one chunk ─────────────────────────
# block codes of the chunk buffer (terrain.chunk_underground: 0 solid, 1 air,
# 2 water, 3 lava) + the mineshaft's own blocks
SOLID, AIR, WATER, LAVA, PLANKS, LOG, FENCE, CHAIN, RAIL, COBWEB, TORCH, SPAWNER = range(12)
_STURDY = {SOLID, PLANKS, LOG, SPAWNER}          # isFaceSturdy (every face)
_SOLID_RENDER = {SOLID, PLANKS, LOG}
_WOOD = {PLANKS, LOG, FENCE, CHAIN}              # MineShaftPiece.canBeReplaced: not these
_SUPPORT_DOWN = {SOLID, PLANKS, LOG, FENCE, CHAIN, SPAWNER}
MIN_Y, MAX_Y = -64, 319


def _tops(b):
    """(29/10) highest sturdy block + 1 of each column [z * 16 + x] (numpy)."""
    try:
        import numpy as np
        a = np.frombuffer(bytes(b), np.uint8).reshape(384, 16, 16)
        st = np.isin(a, np.fromiter(_STURDY, np.uint8))
        any_ = st.any(axis=0)
        last = 383 - np.argmax(st[::-1], axis=0)
        t = np.where(any_, last + 1 + MIN_Y, MIN_Y)
        return [int(v) for v in t.reshape(256)]
    except ImportError:
        out = [None] * 256
        for z in range(16):
            for x in range(16):
                y = MAX_Y
                while y >= MIN_Y and b[(((y + 64) << 4) | z) << 4 | x] not in _STURDY:
                    y -= 1
                out[z * 16 + x] = y + 1
        return out


class World:
    """The chunk (cx, cz) as a buffer [((y + 64) * 16 + z) * 16 + x]; outside: air."""

    def __init__(self, blk, cx, cz, deep_dark=None):
        self.b = bytearray(blk)
        self.x0, self.z0 = cx << 4, cz << 4
        self.box = (self.x0, MIN_Y, self.z0, self.x0 + 15, MAX_Y, self.z0 + 15)
        self.deep_dark = deep_dark               # (x, y, z) -> bool, or None
        self._orig = bytes(blk)                  # (31/10) the tops computed when first asked
        self.top = None                          # OCEAN_FLOOR_WG (frozen before the writes)

    def inside(self, x, y, z):
        return self.x0 <= x <= self.x0 + 15 and self.z0 <= z <= self.z0 + 15 and MIN_Y <= y <= MAX_Y

    def get(self, x, y, z):
        if not self.inside(x, y, z):
            return AIR
        return self.b[((((y + 64) << 4) | (z - self.z0)) << 4) | (x - self.x0)]

    def set(self, x, y, z, v):
        if self.inside(x, y, z):
            self.b[((((y + 64) << 4) | (z - self.z0)) << 4) | (x - self.x0)] = v

    def height(self, x, z):
        if self.top is None:
            self.top = _tops(self._orig)
        return self.top[(z - self.z0) * 16 + (x - self.x0)]


def _nint(r, n):
    m = n - 1
    if (n & m) == 0:
        return (n * r.next(31)) >> 31
    while True:
        u = r.next(31)
        v = u % n
        if u - v + m < (1 << 31):
            return v


def _nfloat(r):
    return r.next_float()


def _nbool(r):
    return r.next(1) != 0


def _nlong(r):
    return r.nextLong() if hasattr(r, "nextLong") else r.next_long()


class _P:
    """StructurePiece helpers over one Piece, in the chunk box."""

    def __init__(self, pc, w, kind_wood=PLANKS):
        self.p, self.w = pc, w

    def wpos(self, x, y, z):
        b, f = self.p.bb, self.p.facing
        if f == N:
            X, Z = b[0] + x, b[5] - z
        elif f == S:
            X, Z = b[0] + x, b[2] + z
        elif f == W:
            X, Z = b[3] - z, b[2] + x
        elif f == E:
            X, Z = b[0] + z, b[2] + x
        else:
            X, Z = x, z
        Y = y if f == NONE else y + b[1]
        return X, Y, Z

    def getb(self, x, y, z):
        X, Y, Z = self.wpos(x, y, z)
        return self.w.get(X, Y, Z) if self.w.inside(X, Y, Z) else AIR

    def place(self, v, x, y, z):
        X, Y, Z = self.wpos(x, y, z)
        if not self.w.inside(X, Y, Z):
            return
        if self.w.get(X, Y, Z) in _WOOD:                 # canBeReplaced
            return
        self.w.set(X, Y, Z, v)

    def interior(self, x, y, z):
        X, Y, Z = self.wpos(x, y + 1, z)
        if not self.w.inside(X, Y, Z):
            return False
        return Y < self.w.height(X, Z)

    def box(self, x0, y0, z0, x1, y1, z1, edge, inner, skip_air=False):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                for z in range(z0, z1 + 1):
                    if skip_air and self.getb(x, y, z) == AIR:
                        continue
                    e = y in (y0, y1) or x in (x0, x1) or z in (z0, z1)
                    self.place(edge if e else inner, x, y, z)

    def maybe_box(self, r, f, x0, y0, z0, x1, y1, z1, edge, inner, need_solid, need_interior):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                for z in range(z0, z1 + 1):
                    if _nfloat(r) > f:
                        continue
                    if need_solid and self.getb(x, y, z) == AIR:
                        continue
                    if need_interior and not self.interior(x, y, z):
                        continue
                    e = y in (y0, y1) or x in (x0, x1) or z in (z0, z1)
                    self.place(edge if e else inner, x, y, z)

    def maybe_block(self, r, f, x, y, z, v):
        if _nfloat(r) < f:
            self.place(v, x, y, z)

    def invalid(self, deep_check):
        b, c, w = self.p.bb, self.w.box, self.w
        i, j, k = max(b[0] - 1, c[0]), max(b[1] - 1, c[1]), max(b[2] - 1, c[2])
        l, m, n = min(b[3] + 1, c[3]), min(b[4] + 1, c[4]), min(b[5] + 1, c[5])
        if deep_check and w.deep_dark is not None and w.deep_dark((i + l) // 2, (j + m) // 2, (k + n) // 2):
            return True
        liq = (WATER, LAVA)
        for x in range(i, l + 1):
            for z in range(k, n + 1):
                if w.get(x, j, z) in liq or w.get(x, m, z) in liq:
                    return True
        for x in range(i, l + 1):
            for y in range(j, m + 1):
                if w.get(x, y, k) in liq or w.get(x, y, n) in liq:
                    return True
        for z in range(k, n + 1):
            for y in range(j, m + 1):
                if w.get(i, y, z) in liq or w.get(l, y, z) in liq:
                    return True
        return False


def _room(pp, ents):
    b = pp.p.bb
    pp.box(b[0], b[1] + 1, b[2], b[3], min(b[1] + 3, b[4]), b[5], AIR, AIR)
    for e in ents:
        pp.box(e[0], e[4] - 2, e[2], e[3], e[4], e[5], AIR, AIR)
    # generateUpperHalfSphere(minX, minY + 4, minZ, maxX, maxY, maxZ, CAVE_AIR, false)
    import struct as _st

    def f32(v):
        return _st.unpack("f", _st.pack("f", v))[0]
    x0, y0, z0, x1, y1, z1 = b[0], b[1] + 4, b[2], b[3], b[4], b[5]
    fw, fh, fd = f32(float(x1 - x0 + 1)), f32(float(y1 - y0 + 1)), f32(float(z1 - z0 + 1))
    cxf = f32(x0 + f32(fw / 2.0))
    czf = f32(z0 + f32(fd / 2.0))
    for y in range(y0, y1 + 1):
        fy = f32(f32(float(y - y0)) / fh)
        for x in range(x0, x1 + 1):
            fx = f32(f32(f32(float(x)) - cxf) / f32(fw * 0.5))
            for z in range(z0, z1 + 1):
                fz = f32(f32(f32(float(z)) - czf) / f32(fd * 0.5))
                s = f32(f32(f32(fx * fx) + f32(fy * fy)) + f32(fz * fz))
                if s <= f32(1.05):
                    pp.place(AIR, x, y, z)


def _stairs(pp):
    pp.box(0, 5, 0, 2, 7, 1, AIR, AIR)
    pp.box(0, 0, 7, 2, 2, 8, AIR, AIR)
    for i in range(5):
        pp.box(0, 5 - i - (1 if i < 4 else 0), 2 + i, 2, 7 - i, 2 + i, AIR, AIR)


def _set_planks(pp, x, y, z):
    if not pp.interior(x, y, z):
        return
    X, Y, Z = pp.wpos(x, y, z)
    if pp.w.get(X, Y, Z) not in _STURDY:
        pp.w.set(X, Y, Z, PLANKS)


def _crossing(pp):
    b = pp.p.bb
    A = AIR
    if pp.p.two:
        pp.box(b[0] + 1, b[1], b[2], b[3] - 1, b[1] + 3 - 1, b[5], A, A)
        pp.box(b[0], b[1], b[2] + 1, b[3], b[1] + 3 - 1, b[5] - 1, A, A)
        pp.box(b[0] + 1, b[4] - 2, b[2], b[3] - 1, b[4], b[5], A, A)
        pp.box(b[0], b[4] - 2, b[2] + 1, b[3], b[4], b[5] - 1, A, A)
        pp.box(b[0] + 1, b[1] + 3, b[2] + 1, b[3] - 1, b[1] + 3, b[5] - 1, A, A)
    else:
        pp.box(b[0] + 1, b[1], b[2], b[3] - 1, b[4], b[5], A, A)
        pp.box(b[0], b[1], b[2] + 1, b[3], b[4], b[5] - 1, A, A)
    for (x, z) in ((b[0] + 1, b[2] + 1), (b[0] + 1, b[5] - 1), (b[3] - 1, b[2] + 1), (b[3] - 1, b[5] - 1)):
        if pp.getb(x, b[4] + 1, z) != AIR:
            pp.box(x, b[1], z, x, b[4], z, PLANKS, A)
    y = b[1] - 1
    for x in range(b[0], b[3] + 1):
        for z in range(b[2], b[5] + 1):
            _set_planks(pp, x, y, z)


def _fill_pillar_or_chain(pp, x, y, z):
    """fillPillarDownOrChainUp with the log."""
    w = pp.w
    X, Y, Z = pp.wpos(x, y, z)
    if not w.inside(X, Y, Z):
        return
    i = 1
    down = up = True
    while down or up:
        if down:
            st = w.get(X, Y - i, Z)
            repl = st in (AIR, WATER, LAVA) and st != LAVA
            if not repl and st in _STURDY:
                for yy in range(Y - i + 1, Y):
                    w.set(X, yy, Z, LOG)
                return
            down = i <= 20 and repl and (Y - i) > MIN_Y + 1
        if up:
            st = w.get(X, Y + i, Z)
            repl = st in (AIR, WATER, LAVA)
            if not repl and st in _SUPPORT_DOWN:      # canHangChainBelow (not a falling block)
                w.set(X, Y + 1, Z, FENCE)
                for yy in range(Y + 2, Y + i):
                    w.set(X, yy, Z, CHAIN)
                return
            up = i <= 50 and repl and (Y + i) < MAX_Y
        i += 1


def _double_support(pp, x, y, z):
    if pp.getb(x, y, z) == PLANKS:
        _fill_pillar_or_chain(pp, x, y, z)
    if pp.getb(x + 2, y, z) == PLANKS:
        _fill_pillar_or_chain(pp, x + 2, y, z)


def _support(pp, r, x0, y0, z, y1, x1):
    for x in range(x0, x1 + 1):                          # isSupportingBox
        if pp.getb(x, y1 + 1, z) == AIR:
            return
    pp.box(x0, y0, z, x0, y1 - 1, z, FENCE, AIR)
    pp.box(x1, y0, z, x1, y1 - 1, z, FENCE, AIR)
    if _nint(r, 4) == 0:
        pp.box(x0, y1, z, x0, y1, z, PLANKS, AIR)
        pp.box(x1, y1, z, x1, y1, z, PLANKS, AIR)
    else:
        pp.box(x0, y1, z, x1, y1, z, PLANKS, AIR)
        pp.maybe_block(r, 0.05, x0 + 1, y1, z - 1, TORCH)
        pp.maybe_block(r, 0.05, x0 + 1, y1, z + 1, TORCH)


def _sturdy_neighbours(pp, x, y, z, need):
    X, Y, Z = pp.wpos(x, y, z)
    n = 0
    for dx, dy, dz in ((0, -1, 0), (0, 1, 0), (0, 0, -1), (0, 0, 1), (-1, 0, 0), (1, 0, 0)):
        if pp.w.inside(X + dx, Y + dy, Z + dz) and pp.w.get(X + dx, Y + dy, Z + dz) in _STURDY:
            n += 1
            if n >= need:
                return True
    return False


def _cobweb(pp, r, f, x, y, z):
    if pp.interior(x, y, z) and _nfloat(r) < f and _sturdy_neighbours(pp, x, y, z, 2):
        pp.place(COBWEB, x, y, z)


CHEST_TRIES = []          # (29/10) every chest attempt (placed or not): (x, y, z, start)


def _chest(pp, r, x, y, z, out):
    X, Y, Z = pp.wpos(x, y, z)
    if out is not None and pp.w.inside(X, Y, Z):
        CHEST_TRIES.append((X, Y, Z, pp.start))
    if pp.w.inside(X, Y, Z) and pp.w.get(X, Y, Z) == AIR and pp.w.get(X, Y - 1, Z) != AIR:
        _nbool(r)                                        # rail shape
        pp.place(RAIL, x, y, z)
        s = _nlong(r)
        if out is not None:
            out.append((X, Y, Z, s, pp.start))
        return True
    return False


def _corridor(pp, r, chests):
    p = pp.p
    m = p.length * 5 - 1
    pp.box(0, 0, 0, 2, 1, m, AIR, AIR)
    pp.maybe_box(r, 0.8, 0, 2, 0, 2, 2, m, AIR, AIR, False, False)
    if p.webs:
        pp.maybe_box(r, 0.6, 0, 0, 0, 2, 1, m, COBWEB, AIR, False, True)
    for i in range(p.length):
        o = 2 + i * 5
        _support(pp, r, 0, 0, o, 2, 2)
        for f, x, dz in ((0.1, 0, -1), (0.1, 2, -1), (0.1, 0, 1), (0.1, 2, 1),
                         (0.05, 0, -2), (0.05, 2, -2), (0.05, 0, 2), (0.05, 2, 2)):
            _cobweb(pp, r, f, x, 2, o + dz)
        if _nint(r, 100) == 0:
            _chest(pp, r, 2, 0, o - 1, chests)
        if _nint(r, 100) == 0:
            _chest(pp, r, 0, 0, o + 1, chests)
        if p.webs and (pp.start, tuple(p.bb)) not in SPIDER_DONE:
            q = o - 1 + _nint(r, 3)
            X, Y, Z = pp.wpos(1, 0, q)
            if pp.w.inside(X, Y, Z) and pp.interior(1, 0, q):
                SPIDER_DONE.add((pp.start, tuple(p.bb)))
                pp.w.set(X, Y, Z, SPAWNER)
    for x in range(3):
        for z in range(m + 1):
            _set_planks(pp, x, -1, z)
    _double_support(pp, 0, -1, 2)
    if p.length > 1:
        _double_support(pp, 0, -1, m - 2)
    if p.rails:
        for z in range(m + 1):
            s = pp.getb(1, -1, z)
            if s != AIR and s in _SOLID_RENDER:
                pp.maybe_block(r, 0.7 if pp.interior(1, 0, z) else 0.9, 1, 0, z, RAIL)


# (29/10) MineShaftCorridor.hasPlacedSpider is a field of the start's piece: the
# chunks generated after the one that placed the spawner skip its nextInt(3).
# The spawners placed during one computation (the neighbours decorated first, then
# the chunk): cleared by its caller (prefeatures118).
SPIDER_DONE = set()


def _room_entrances(room_bb, pieces):
    """MineShaftRoom.childEntranceBoxes, rebuilt from the children's boxes (the
    room adds one for each child it created directly)."""
    return []          # filled by layout_ex


# ── fastutil LongOpenHashSet (references) ──
def _fu_mix(x):
    h = (x * 0x9E3779B97F4A7C15) & M64
    h ^= h >> 32
    return (h ^ (h >> 16)) & 0xFFFFFFFF


def lset_reloaded(keys):
    """The iteration order of the reference set once saved and loaded again:
    new LongOpenHashSet(saved array) — a table sized for the array (arraySize(n, .75)),
    filled in the saved (= first iteration) order."""
    a = _lset_order(keys)
    need = max(2, int(len(a) / 0.75 + 0.999999))
    n = 1
    while n < need:
        n <<= 1
    return _lset_order(a, n)


def _lset_order(keys, n0=32):
    n, mask, size = n0, n0 - 1, 0
    maxfill = min(int(n0 * 0.75 + 0.999999), n0 - 1)
    tab = [0] * (n + 1)
    has_null = False
    for k in keys:
        if k == 0:
            if has_null:
                continue
            has_null = True
        else:
            pos = _fu_mix(k) & mask
            dup = False
            while tab[pos] != 0:
                if tab[pos] == k:
                    dup = True
                    break
                pos = (pos + 1) & mask
            if dup:
                continue
            tab[pos] = k
        size += 1
        if size - 1 >= maxfill:
            need = int((size + 1) / 0.75 + 0.999999)
            nn = 1
            while nn < need:
                nn <<= 1
            nn = max(nn, 2)
            nt = [0] * (nn + 1)
            for kk in tab[:n]:
                if kk:
                    pos = _fu_mix(kk) & (nn - 1)
                    while nt[pos] != 0:
                        pos = (pos + 1) & (nn - 1)
                    nt[pos] = kk
            tab, n, mask = nt, nn, nn - 1
            maxfill = min(int(nn * 0.75 + 0.999999), nn - 1)
    out = [0] if has_null else []
    for pos in range(n - 1, -1, -1):
        if tab[pos]:
            out.append(tab[pos])
    return out


def has_start(seed, mc, cx, cz, biome_at=None, height=None):
    """0 none / 1 normal / 2 mesa. biome_at(x, y, z) -> name (the stub position
    after the vertical move); None: every candidate is normal."""
    r = Lcg()
    large_feature_seed(r, seed & M64, cx, cz)
    if not r.next_double() < 0.004000000189989805:
        return 0
    if biome_at is None:
        return 1
    # the type comes from the biome of the stub (middle x, min z) after the move:
    # try normal, then mesa (the biome at the moved Y must agree)
    p = layout(seed, cx, cz, mc=mc)
    b = biome_at((cx << 4) + 8, p[0].bb[1], cz << 4)
    if b not in ("badlands", "eroded_badlands", "wooded_badlands"):
        if b == "deep_dark" and mc >= 23:
            return 0
        return 1
    pm, ents = layout_ex(seed, cx, cz, mesa=True, mc=mc, height=height)
    if ents is None:
        return 2                                         # mesa, Y unknown
    bm = biome_at((cx << 4) + 8, pm[0].bb[1], cz << 4)
    return 2 if bm in ("badlands", "eroded_badlands", "wooded_badlands") else 0


def underground_index(mc, mesa=False):
    """setFeatureSeed index of the mineshaft in UNDERGROUND_STRUCTURES (step 3)."""
    if mc <= 23:            # 1.18.2 / 1.19.0-2: configured structures in registration order
        return 1 if mesa else 0
    return 2 if mesa else 1     # 1.19.3+: alphabetical (buried_treasure, mineshaft, mineshaft_mesa)


_HAS = {}                 # (29/10) has_start per chunk, cached


_PC_CACHE = {}
PC_STATS = [0, 0]


LAST_MULTI = [False]       # (30/10) the last place_chunk met 2+ starts of one kind


def place_chunk(seed, mc, cx, cz, blk, biome_at=None, deep_dark=None, chests=None, height=None,
                reloaded=False):
    """(30/10) reloaded: the chunk was saved and loaded again before its decoration —
    its reference sets rebuilt from the saved arrays (another iteration order when
    two starts of a kind reach it). Memoised on the input chunk (its bytes), the spiders already placed
    and the kind of call; the side effects (chests, CHEST_TRIES, SPIDER_DONE) are
    replayed."""
    import hashlib
    blk = bytes(blk)
    if not _reaching(seed, mc, cx, cz, biome_at, height)[3]:     # (31/10) no piece here
        LAST_MULTI[0] = False
        return bytearray(blk), 0, _reaching(seed, mc, cx, cz, biome_at, height)[2]
    k = (int(seed) & M64, mc, cx, cz, hashlib.blake2b(blk, digest_size=16).digest(),
         biome_at is None, deep_dark is None, height is None, chests is None,
         frozenset(SPIDER_DONE), bool(reloaded))
    v = _PC_CACHE.get(k)
    if v is None:
        PC_STATS[1] += 1
        n_ct, sp0 = len(CHEST_TRIES), set(SPIDER_DONE)
        ch = [] if chests is not None else None
        LAST_MULTI[0] = False
        b, n, unk = _place_chunk(seed, mc, cx, cz, blk, biome_at, deep_dark, ch, height, reloaded)
        v = (bytes(b), n, unk, tuple(ch or ()), tuple(CHEST_TRIES[n_ct:]),
             tuple(SPIDER_DONE - sp0), LAST_MULTI[0])
        if len(_PC_CACHE) > 256:
            _PC_CACHE.clear()
        _PC_CACHE[k] = v
        del CHEST_TRIES[n_ct:]
    else:
        PC_STATS[0] += 1
    b, n, unk, ch, ct, sp, LAST_MULTI[0] = v
    if chests is not None:
        chests.extend(ch)
    CHEST_TRIES.extend(ct)
    SPIDER_DONE.update(sp)
    return bytearray(b), n, unk


def _place_chunk(seed, mc, cx, cz, blk, biome_at=None, deep_dark=None, chests=None, height=None,
                 reloaded=False):
    """The chunk buffer (codes above) once the mineshafts reaching chunk (cx, cz)
    are placed, as UNDERGROUND_STRUCTURES does (before the strongholds): the
    normal ones, then the mesa ones (their own structure: own references, own
    random). Returns (bytearray, number of pieces placed, mesa starts of unknown Y)."""
    import structure_loot as sl
    w = World(blk, cx, cz, deep_dark)
    found, keys, unknown, _any = _reaching(seed, mc, cx, cz, biome_at, height)
    cfn = _c_piece_fn()
    n = 0
    for t in (1, 2):
        if not keys[t]:
            continue
        r = sl.structure_rng(seed, mc, cx, cz, underground_index(mc, t == 2), 3)
        if len(keys[t]) > 1:
            LAST_MULTI[0] = True
        for k in (lset_reloaded(keys[t]) if reloaded else _lset_order(keys[t])):
            x, z = k & 0xFFFFFFFF, k >> 32
            x = x - (1 << 32) if x >> 31 else x
            z = z - (1 << 32) if z >> 31 else z
            ps, ents = found[t][(x, z)]
            for pc in ps:
                b = pc.bb
                if not (b[3] >= w.box[0] and b[0] <= w.box[3] and b[5] >= w.box[2] and b[2] <= w.box[5]):
                    continue
                pp = _P(pc, w)
                pp.start = (x, z)                        # (29/10) for the chests
                if cfn is not None:
                    v = _piece_c(cfn, w, pc, ents, r, chests, (x, z), mc >= 23, cx, cz)
                    n += v
                    continue
                if pp.invalid(mc >= 23):
                    continue
                n += 1
                if pc.type == ROOM:
                    _room(pp, ents)
                elif pc.type == CORRIDOR:
                    _corridor(pp, r, chests)
                elif pc.type == CROSSING:
                    _crossing(pp)
                else:
                    _stairs(pp)
    return w.b, n, unknown


_REACH = {}
USE_C = True
_CFN = []


def _c_piece_fn():
    """(31/10) libterrain cv_ms118_piece, or None."""
    if not USE_C:
        return None
    if not _CFN:
        f = None
        try:
            import ctypes as ct
            import terrain
            terrain._load()
            f = getattr(terrain._LIB, "cv_ms118_piece", None)
            if f is not None:
                P = ct.c_void_p
                f.argtypes = [P, P, P, ct.c_int, ct.c_int, ct.c_int, P, ct.c_int, ct.c_int, ct.c_int, P,
                              ct.c_int, P, P, P, P, P, ct.c_int]
                f.restype = ct.c_int
        except Exception:
            f = None
        _CFN.append(f)
    return _CFN[0]


def _piece_c(f, w, pc, ents, r, chests, start, deep_check, cx, cz):
    """One piece in C (same as _P.invalid + _room / _corridor / _crossing / _stairs):
    1 placed, 0 not."""
    import ctypes as ct
    if deep_check and w.deep_dark is not None:
        b, c = pc.bb, w.box
        i, j, k = max(b[0] - 1, c[0]), max(b[1] - 1, c[1]), max(b[2] - 1, c[2])
        l, m, nn = min(b[3] + 1, c[3]), min(b[4] + 1, c[4]), min(b[5] + 1, c[5])
        if w.deep_dark((i + l) // 2, (j + m) // 2, (k + nn) // 2):
            return 0
    cc = getattr(w, "_c", None)
    if cc is None:
        cc = w._c = ((ct.c_uint8 * len(w.b)).from_buffer(w.b), ct.c_char_p(w._orig),
                     (ct.c_int16 * 256)(*([-32768] * 256)))
    key = (start, tuple(pc.bb))
    flags = (1 if pc.webs else 0) | (2 if pc.rails else 0) | (4 if pc.two else 0) | \
            (8 if key in SPIDER_DONE else 0) | (16 if chests is not None else 0)
    bb = (ct.c_int * 6)(*pc.bb)
    fe = [v for e in (ents or ()) for v in e[:6]] if pc.type == ROOM else []
    ea = (ct.c_int * max(1, len(fe)))(*fe)
    rng = (ct.c_uint64 * 2)(r.lo, r.hi)
    cap = 64
    ch = (ct.c_int64 * (4 * cap))()
    tr = (ct.c_int * (3 * cap))()
    nch, ntr = ct.c_int(0), ct.c_int(0)
    v = f(cc[0], cc[1], cc[2], cx, cz, pc.type, bb, pc.facing, pc.length, flags, ea, len(fe) // 6, rng, ch, ct.byref(nch),
          tr, ct.byref(ntr), cap)
    r.lo, r.hi = rng[0], rng[1]
    if not v:
        return 0
    if v & 2:
        SPIDER_DONE.add(key)
    if chests is not None:
        for t in range(min(ntr.value, cap)):
            CHEST_TRIES.append((tr[3 * t], tr[3 * t + 1], tr[3 * t + 2], start))
        for t in range(min(nch.value, cap)):
            chests.append((ch[4 * t], ch[4 * t + 1], ch[4 * t + 2], ch[4 * t + 3], start))
    return 1


def _reaching(seed, mc, cx, cz, biome_at=None, height=None):
    """(31/10) the mineshaft starts whose box reaches chunk (cx, cz): (found {kind:
    {(x, z): (pieces, entrances)}}, keys {kind: [start keys]}, mesa starts of unknown Y,
    any?) — no terrain needed, cached."""
    rk = (int(seed) & M64, mc, cx, cz, biome_at is None, height is None)
    v = _REACH.get(rk)
    if v is not None:
        return v
    x0w, z0w = cx << 4, cz << 4
    found = {1: {}, 2: {}}
    keys = {1: [], 2: []}
    unknown = 0
    for x in range(cx - 8, cx + 9):
        for z in range(cz - 8, cz + 9):
            hk = (int(seed) & M64, mc, x, z, biome_at is None, height is None)
            t = _HAS.get(hk)
            if t is None:
                t = _HAS[hk] = has_start(seed, mc, x, z, biome_at, height)
                if len(_HAS) > 200000:
                    _HAS.clear()
            if not t:
                continue
            ps, ents = layout_ex(seed, x, z, mesa=(t == 2), mc=mc, height=height)
            if ents is None:
                unknown += 1
                continue
            x0 = min(q.bb[0] for q in ps); x1 = max(q.bb[3] for q in ps)
            z0 = min(q.bb[2] for q in ps); z1 = max(q.bb[5] for q in ps)
            if not (x1 >= x0w and x0 <= x0w + 15 and z1 >= z0w and z0 <= z0w + 15):
                continue
            found[t][(x, z)] = (ps, ents)
            keys[t].append((x & 0xFFFFFFFF) | ((z & 0xFFFFFFFF) << 32))
    anyp = any(b[3] >= x0w and b[0] <= x0w + 15 and b[5] >= z0w and b[2] <= z0w + 15
               for t in (1, 2) for ps, _e in found[t].values() for b in (q.bb for q in ps))
    v = (found, keys, unknown, anyp)          # any: a piece's box meets the chunk
    if len(_REACH) > 100000:
        _REACH.clear()
    _REACH[rk] = v
    return v


_BIO = {}


def biome_fns(seed, mc):
    """(biome_at for the starts, deep_dark at 1:1) from cubiomes, cached."""
    key = (int(seed), int(mc))
    v = _BIO.get(key)
    if v is None:
        import cubiomes_wrap as cw
        lib = cw.CubiomesLib()
        lib.setup_and_seed(int(mc), int(seed))
        names = {183: "deep_dark", 37: "badlands", 38: "wooded_badlands", 165: "eroded_badlands"}

        def biome_at(x, y, z, lib=lib):
            return names.get(lib.get_biome_at(x, z, 4, y), "")

        def deep(x, y, z, lib=lib):
            return lib.get_biome_at(x, z, 1, y) == 183
        if len(_BIO) > 8:
            _BIO.clear()
        v = _BIO[key] = (biome_at, deep)
    return v


def _height_fn(seed, mc):
    import terrain

    def h(x, z):
        v = terrain.first_occupied(seed, mc, x, z)
        return None if v is None else v + 1
    return h


def apply(seed, mc, cx, cz, blk):
    """chunk_underground buffer -> the same with the mineshafts of the chunk (1.18+)."""
    bio, deep = biome_fns(seed, mc)
    out, _n, _mesa = place_chunk(seed, mc, cx, cz, blk, bio, deep, height=_height_fn(seed, mc))
    return bytes(out)
