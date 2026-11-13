"""
ocean_ruin_y.py — (30/10) the Y of the ocean ruins' chests, 1.18+, by replaying
OceanRuinPiece.postProcess chunk by chunk on the terrain after the carvers.

In each chunk a piece reaches, postProcess starts again from the heightmap
OCEAN_FLOOR_WG at the template's corner, then getHeight (sinks the ruin to the lowest
floor when the area is uneven) — so a chunk generated after another one sees the
ruin blocks that chunk placed (cold ruins: also the brick copy before the cracked
one, the cracked before the mossy), and the chest of the same ruin can end 1 to 5
blocks higher depending on the order the chunks were generated in.
Templates underwater_ruin/* (identical 1.16 → 26.3): loot_tables/ocean_ruin_templates.
BlockRotProcessor: kept if LegacyRandomSource(Mth.getSeed(pos)).nextFloat() <= integrity.
"""
from __future__ import annotations

import gzip
import json
import os
import struct

M64 = (1 << 64) - 1
_T = None
AIR, WATER, SOLID, WSOLID = 1, 2, 10, 11          # terrain: 0 solid, 1 air, 2 water, 3 lava


def templates():
    global _T
    if _T is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables",
                         "ocean_ruin_templates.json.gz")
        with gzip.open(p, "rt") as f:
            _T = json.load(f)
    return _T


def _i32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >> 31 else v


def _s64(v):
    v &= M64
    return v - (1 << 64) if v >> 63 else v


def mth_seed(x, y, z):
    """Mth.getSeed(x, y, z)."""
    l = _s64(_i32(x * 3129871) ^ (z * 116129781) ^ y)
    l = _s64(l * l * 42317861 + l * 11)
    return l >> 16


def _f32(v):
    return struct.unpack("f", struct.pack("f", v))[0]


def integrity(name, big):
    if "cracked" in name:
        return _f32(0.7)
    if "mossy" in name:
        return _f32(0.5)
    return _f32(0.9 if big else 0.8)


def _keep(x, y, z, integ):
    s = (mth_seed(x, y, z) ^ 0x5DEECE66D) & ((1 << 48) - 1)
    s = (s * 0x5DEECE66D + 0xB) & ((1 << 48) - 1)
    return (s >> 24) * (1.0 / (1 << 24)) <= integ


def _rot(x, z, rot):
    if rot == 1:
        return -z, x
    if rot == 2:
        return -x, -z
    if rot == 3:
        return z, -x
    return x, z


_PASS = (AIR, WATER, WSOLID, 40)                  # getHeight walks down through these
_FLOOR = (0, SOLID, WSOLID, 40, 41)               # OCEAN_FLOOR_WG stops on these
NONE = 255                                        # World overlay: no ruin block


def _np():
    import numpy as np
    return np


_LUT = None


def _luts():
    """(stop, floor) lookup tables over the 256 codes"""
    global _LUT
    if _LUT is None:
        np = _np()
        st = np.ones(256, bool)
        st[list(_PASS)] = False
        fl = np.zeros(256, bool)
        fl[list(_FLOOR)] = True
        _LUT = (st, fl)
    return _LUT


class _Chunk:
    """(07/11) the terrain of a chunk as arrays: codes [y + 64, z, x], stops = where
    getHeight's walk stops, floor [z, x] = OCEAN_FLOOR_WG. The frozen-ocean ice
    (surface_ice) is added column by column when a column is first needed (`pending`)."""

    __slots__ = ("a", "stops", "floor", "pending", "blocks", "base", "cx", "cz")

    def __init__(self, a, blocks, base, cx, cz, pending):
        np = _np()
        self.a, self.blocks, self.base, self.cx, self.cz = a, blocks, base, cx, cz
        stop_lut, floor_lut = _luts()
        self.stops = stop_lut[a]
        fl = floor_lut[a][::-1]
        self.floor = np.where(fl.any(0), 320 - fl.argmax(0), -64).astype(np.int64)
        self.pending = pending

    def need(self, z0, z1, x0, x1):
        """the ice of the columns [z0, z1) × [x0, x1) (chunk-local)"""
        p = self.pending
        if not p:
            return
        for i in [i for i in p if z0 <= (i >> 4) < z1 and x0 <= (i & 15) < x1]:
            self._ice(i)

    def need_cols(self, iz, ix):
        p = self.pending
        if not p:
            return
        for i in set((iz * 16 + ix).tolist()) & p:
            self._ice(i)

    def _ice(self, i):
        np = _np()
        self.pending.discard(i)
        lz, lx = i >> 4, i & 15
        x, z = (self.cx << 4) + lx, (self.cz << 4) + lz
        blocks, base = self.blocks, self.base
        col = blocks.cols.get((x, z))
        if col is None:
            col = blocks.cols[(x, z)] = blocks.ice.column(x, z, lambda yy: base.at(x, yy, z))
        if not col:
            return
        a = self.a
        for y, v in col.items():
            if -64 <= y < 320 and a[y + 64, lz, lx] in (AIR, WATER):
                a[y + 64, lz, lx] = v
        c = a[:, lz, lx]
        stop_lut, floor_lut = _luts()
        self.stops[:, lz, lx] = stop_lut[c]
        f = np.nonzero(floor_lut[c])[0]
        self.floor[lz, lx] = int(f[-1]) - 63 if len(f) else -64


def _chunk(blocks, cx, cz):
    """the _Chunk (cx, cz), cached on `blocks` (the terrain never changes; the ruins
    are World's overlay); None if unavailable"""
    np = _np()
    cache = blocks.__dict__.setdefault("_chunks", {})
    k = (cx, cz)
    if k in cache:
        return cache[k]
    base = blocks.b if isinstance(blocks, _IceBlocks) else blocks
    pending = set()
    if hasattr(base, "c") and hasattr(base, "seed"):
        base.at(cx << 4, 0, cz << 4)              # loads the chunk
        raw = base.c.get(k)
        if raw is None:
            cache[k] = None
            return None
        a = np.frombuffer(raw, dtype=np.uint8).reshape(384, 16, 16).copy()
        if isinstance(blocks, _IceBlocks) and blocks.ice is not None:
            import surface_ice as si
            try:
                bio = blocks.ice.lib.gen_biomes_region(cx << 4, cz << 4, 16, 16, 1, y=si.SEA)
                pending = {i for i in range(256) if bio[i] in (si.FROZEN_OCEAN, si.DEEP_FROZEN_OCEAN)}
            except Exception:                      # noqa: BLE001
                pending = set(range(256))
    else:                                         # any other terrain: block by block
        a = np.zeros((384, 16, 16), np.uint8)
        for y in range(384):
            for z in range(16):
                for x in range(16):
                    v = blocks.at((cx << 4) + x, y - 64, (cz << 4) + z)
                    if v is None:
                        cache[k] = None
                        return None
                    a[y, z, x] = v
    cache[k] = v = _Chunk(a, blocks, base, cx, cz, pending)
    return v


class World:
    """Terrain (scattered_loot._Blocks) + what the ruins placed."""

    def __init__(self, blocks, reprimed=()):
        self.b = blocks
        self.reprimed = set(reprimed)
        self.ov = {}                              # chunk → [y + 64, z, x] ruin blocks (NONE: none)
        self.top = {}                             # chunk → [z, x] highest y of a ruin block

    @property
    def w(self):
        np = _np()
        out = {}
        for (cx, cz), o in self.ov.items():
            for y, z, x in zip(*np.nonzero(o != NONE)):
                out[((cx << 4) + int(x), int(y) - 64, (cz << 4) + int(z))] = int(o[y, z, x])
        return out

    def at(self, x, y, z):
        o = self.ov.get((x >> 4, z >> 4))
        if o is not None and -64 <= y < 320:
            v = o[y + 64, z & 15, x & 15]
            if v != NONE:
                return int(v)
        return self.b.at(x, y, z)

    def _over(self, k):
        o = self.ov.get(k)
        if o is None:
            np = _np()
            o = self.ov[k] = np.full((384, 16, 16), NONE, np.uint8)
            self.top[k] = np.full((16, 16), -65, np.int64)
        return o

    def floor_wg(self, x, z):
        """OCEAN_FLOOR_WG: frozen after the carvers (a chunk's status is CARVERS while
        its features are placed: setBlock updates only the final heightmaps), so the
        ruin blocks don't count — only getHeight's walk sees them. Unless the chunk was
        saved and loaded again in between (its WG heightmaps are not saved before FULL:
        primed again from its blocks, the ruin's included) → `reprimed`."""
        k = (x >> 4, z >> 4)
        c = _chunk(self.b, *k)
        if c is None:
            return None
        c.need(z & 15, (z & 15) + 1, x & 15, (x & 15) + 1)
        f = int(c.floor[z & 15, x & 15])
        if k in self.reprimed and k in self.top:
            t = int(self.top[k][z & 15, x & 15]) + 1
            if t > f:
                f = t
        return f


def _height(W, px, pz, rot, size):
    """OceanRuinPiece.getHeight from the floor at the template's corner: walks down each
    column of the footprint through air / water / waterlogged blocks / packed ice
    (stopping at -63), and sinks the ruin to the lowest floor when the area is uneven"""
    np = _np()
    y = W.floor_wg(px, pz)
    if y is None:
        return None
    a, b = _rot(size[0] - 1, size[2] - 1, rot)
    xs, zs = sorted((px, px + a)), sorted((pz, pz + b))
    t = y - 1
    min_y, area = 512, 0
    for cx in range(xs[0] >> 4, (xs[1] >> 4) + 1):
        for cz in range(zs[0] >> 4, (zs[1] >> 4) + 1):
            c = _chunk(W.b, cx, cz)
            if c is None:
                return None
            x0, x1 = max(xs[0], cx << 4) & 15, (min(xs[1], (cx << 4) + 15) & 15) + 1
            z0, z1 = max(zs[0], cz << 4) & 15, (min(zs[1], (cz << 4) + 15) & 15) + 1
            if t <= -63:
                min_y = min(min_y, t)
                continue
            c.need(z0, z1, x0, x1)
            S = c.stops[:t + 65, z0:z1, x0:x1]
            o = W.ov.get((cx, cz))
            if o is not None:
                S = S | (o[:t + 65, z0:z1, x0:x1] == SOLID)
            R = S[::-1]
            fy = np.where(R.any(0), t - R.argmax(0), -63)
            np.maximum(fy, -63, out=fy)
            min_y = min(min_y, int(fy.min()))
            area += int((fy < t - 2).sum())
    if t - min_y > 2 and area > abs(xs[1] - xs[0]) - 2:
        return min_y + 1
    return y


_TA = {}


def _tarrays(name):
    """the template's blocks as arrays (x, y, z, waterlogged)"""
    v = _TA.get(name)
    if v is None:
        np = _np()
        T = templates()[name][1]
        v = _TA[name] = tuple(np.array([e[i] for e in T], np.int64) for i in range(4))
    return v


def _keep_mask(X, Y, Z, integ):
    """_keep for arrays: RandomSource.create(Mth.getSeed(pos)).nextFloat() <= integrity"""
    np = _np()
    with np.errstate(over="ignore"):
        l = (X * 3129871).astype(np.int32).astype(np.int64) ^ (Z * 116129781) ^ Y
        l = l * l * 42317861 + l * 11
        s = (l >> 16).view(np.uint64)
        m = np.uint64((1 << 48) - 1)
        s = (s ^ np.uint64(0x5DEECE66D)) & m
        s = (s * np.uint64(0x5DEECE66D) + np.uint64(0xB)) & m
    return (s >> np.uint64(24)).astype(np.float64) * (1.0 / (1 << 24)) <= integ


def _place(W, piece, chunk, rng_next, chests):
    """postProcess of one piece in chunk (cx, cz): its blocks there, its chest (the
    chunk's next nextLong if the marker is in it)."""
    np = _np()
    name, px, pz, rot, big, integ, mark = piece
    size = templates()[name][0]
    y0 = _height(W, px, pz, rot, size)
    if y0 is None:
        return False
    cx, cz = chunk
    x0, z0 = cx << 4, cz << 4
    tx, ty, tz, twl = _tarrays(name)
    if rot == 1:
        a, b = -tz, tx
    elif rot == 2:
        a, b = -tx, -tz
    elif rot == 3:
        a, b = tz, -tx
    else:
        a, b = tx, tz
    X, Y, Z = px + a, y0 + ty, pz + b
    m = (X >= x0) & (X < x0 + 16) & (Z >= z0) & (Z < z0 + 16) & (Y >= -64) & (Y < 320)
    X, Y, Z, wl = X[m], Y[m], Z[m], twl[m]
    if len(X):
        k = _keep_mask(X, Y, Z, integ)
        X, Y, Z, wl = X[k], Y[k], Z[k], wl[k]
    if len(X):
        if chests:                                 # a block over a chest of this chunk
            for key in list(chests):
                if ((X == key[0]) & (Y == key[1]) & (Z == key[2])).any():
                    del chests[key]
        o = W._over(chunk)
        c = _chunk(W.b, cx, cz)
        iy, iz, ix = Y + 64, Z & 15, X & 15
        cur = o[iy, iz, ix]
        if c is not None:
            c.need_cols(iz, ix)
            cur = np.where(cur != NONE, cur, c.a[iy, iz, ix])
        o[iy, iz, ix] = np.where((wl != 0) & ((cur == WATER) | (cur == WSOLID)), WSOLID, SOLID)
        np.maximum.at(W.top[chunk], (iz, ix), Y)
    if mark is not None:
        a, b = _rot(mark[0], mark[2], rot)
        X, Y, Z = px + a, y0 + mark[1], pz + b
        if x0 <= X < x0 + 16 and z0 <= Z < z0 + 16 and -64 <= Y < 320:
            cur = W.at(X, Y, Z)
            o = W._over(chunk)
            o[Y + 64, Z & 15, X & 15] = WSOLID if cur in (WATER, WSOLID) else SOLID
            if Y > W.top[chunk][Z & 15, X & 15]:
                W.top[chunk][Z & 15, X & 15] = Y
            chests[(X, Y, Z)] = rng_next()
    return True


def piece_chunks(piece):
    name, px, pz, rot = piece[:4]
    size = templates()[name][0]
    a, b = _rot(size[0] - 1, size[2] - 1, rot)
    xs, zs = sorted((px, px + a)), sorted((pz, pz + b))
    return {(x, z) for x in range(xs[0] >> 4, (xs[1] >> 4) + 1)
            for z in range(zs[0] >> 4, (zs[1] >> 4) + 1)}


def simulate(blocks, pieces, order, rng_for):
    """Chunks in `order`: [chunk] → {chunk: {(x, y, z): loot seed}} of the chests left.
    pieces: [(name, px, pz, rot, big, integrity, chest marker or None)];
    rng_for(chunk) → a function giving the chunk's next nextLong."""
    W = World(blocks)
    out = {}
    for ch in order:
        nxt = rng_for(ch)
        chests = {}
        for pc in pieces:
            if ch in piece_chunks(pc):
                _place(W, pc, ch, nxt, chests)
        out[ch] = chests
    return out, W


# ───────────────── whole chunks: every ocean ruin start reaching them, game order ─────────────────
class _IceBlocks:
    """The terrain + the frozen-ocean ice pillars of the SURFACE step (surface_ice)."""

    def __init__(self, b, ice):
        self.b, self.ice, self.cols = b, ice, {}

    def at(self, x, y, z):
        v = self.b.at(x, y, z)
        if self.ice is None or v not in (AIR, WATER):
            return v
        col = self.cols.get((x, z))
        if col is None:
            col = self.cols[(x, z)] = self.ice.column(x, z, lambda yy: self.b.at(x, yy, z))
        return col.get(y, v)


class Sim:
    """The ocean ruins of the area, placed chunk by chunk as applyBiomeDecoration does:
    per structure (ocean_ruin_cold, ocean_ruin_warm: step order, one random per chunk
    and structure shared by its starts), the starts in the order of the chunk's
    reference set (LongOpenHashSet), their pieces in order."""

    def __init__(self, seed, mc):
        import scattered_loot as sc
        self.seed, self.mc = int(seed), int(mc)
        self.blocks = _IceBlocks(sc._Blocks(self.seed, self.mc), sc._surface_ice(self.seed, self.mc))
        self._st = {}
        self._near = {}
        self.force = set()           # the clicked start, even if the finder disagrees

    def start(self, scx, scz):
        k = (scx, scz)
        v = self._st.get(k)
        if v is None:
            import scattered_loot as sc
            warm = sc.ocean_ruin_warm(self.seed, self.mc, scx, scz)
            lay = sc.ocean_ruin_layout(self.seed, scx, scz, warm)
            ps = [(n, px, pz, rot, big, integrity(n, big), sc._OR_CHEST.get(n))
                  for n, px, pz, rot, big in lay]
            chs = [piece_chunks(p) for p in ps]
            x0 = min(min(c[0] for c in s) for s in chs)
            x1 = max(max(c[0] for c in s) for s in chs)
            z0 = min(min(c[1] for c in s) for s in chs)
            z1 = max(max(c[1] for c in s) for s in chs)
            v = self._st[k] = (warm, ps, chs, (x0, z0, x1, z1))
        return v

    def starts_near(self, cx, cz, r=4):
        k = (cx, cz, r)
        v = self._near.get(k)
        if v is None:
            import struct_info
            import cubiomes_wrap as cw
            lib = struct_info._lib(self.seed, self.mc, None, 0)
            pts = lib.find_structures_in_box(cw.Ocean_Ruin, (cx - r) * 16, (cz - r) * 16,
                                             (cx + r) * 16 + 15, (cz + r) * 16 + 15,
                                             verify_biome=True, max_count=512)
            v = self._near[k] = sorted({(x >> 4, z >> 4) for x, z in pts})
        return v

    def decorate(self, W, ch):
        """Place what the ruins put in chunk ch; {(x, y, z): loot seed} of its chests."""
        import scattered_loot as sc
        import mineshaft118 as ms
        cx, cz = ch
        refs = []                                     # createReferences: x then z, ±8
        near = set(self.starts_near(cx, cz)) | self.force
        for k in range(cx - 8, cx + 9):
            for l in range(cz - 8, cz + 9):
                if (k, l) in near:
                    b = self.start(k, l)[3]
                    if b[0] <= cx <= b[2] and b[1] <= cz <= b[3]:
                        refs.append((k, l))
        chests = {}
        for warm in sorted({False, True}, key=lambda w: sc.surface_index(
                "ocean_ruin_warm" if w else "ocean_ruin_cold", self.mc)):
            group = [s for s in refs if self.start(*s)[0] == warm]
            if not group:
                continue
            keys = [(x & 0xFFFFFFFF) | ((z & 0xFFFFFFFF) << 32) for x, z in group]
            r = sc._rng(self.seed, self.mc, cx, cz, "ocean_ruin_warm" if warm else "ocean_ruin_cold")

            def nxt():
                return sc._next_long(r)
            for key in ms._lset_order(keys):
                x, z = key & 0xFFFFFFFF, key >> 32
                x = x - (1 << 32) if x >> 31 else x
                z = z - (1 << 32) if z >> 31 else z
                _w, ps, chs, _b = self.start(x, z)
                for pc, pch in zip(ps, chs):
                    if ch in pch:
                        _place(W, pc, ch, nxt, chests)
        return chests

    def outcomes(self, scx, scz):
        """{(wx, wz) of a chest marker of start (scx, scz): [(scenario, y, seed)]} with
        scenario = the other chunks of that ruin generated before (tuple)."""
        import itertools
        _w, ps, chs, _b = self.start(scx, scz)
        self.force = {(scx, scz)}
        out = {}
        done = set()
        for pc, pch in zip(ps, chs):
            if pc[6] is None:
                continue
            a, b = _rot(pc[6][0], pc[6][2], pc[3])
            wx, wz = pc[1] + a, pc[2] + b
            c0 = (wx >> 4, wz >> 4)
            if (wx, wz) in done:
                continue
            done.add((wx, wz))
            fam = set().union(*[c for p2, c in zip(ps, chs) if (p2[1], p2[2]) == (pc[1], pc[2])])
            O = sorted(fam - {c0})
            corner = {(p2[1] >> 4, p2[2] >> 4) for p2 in ps if (p2[1], p2[2]) == (pc[1], pc[2])}
            res = []
            for k in range(len(O) + 1):
                for E in itertools.combinations(O, k):
                    for rp in ((), tuple(c for c in E if c in corner)):
                        W = World(self.blocks, rp)
                        for c in E:
                            self.decorate(W, c)
                        got = self.decorate(W, c0)
                        for (x, y, z), s in got.items():
                            if (x, z) == (wx, wz):
                                res.append((E + (("reloaded",) if rp else ()), y, s))
                        if not any(c in corner for c in E):
                            break
            out[(wx, wz)] = res
        return out
