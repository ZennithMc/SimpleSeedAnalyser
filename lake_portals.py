"""
lake_portals.py — end portals broken by LAKES (lava / water), Minecraft 1.16 (03/10).

Why: measured with the real 1.16.1 generation (tools/research/broken_portal), a
lake that a FRAME chunk decorates can spill into another frame chunk that is
already generated and replace frames with cave air (the lake's upper half) or
stone (the lava lake's border). In 1.16 this happens ~45x more often than with
ruined portals (about 1 first stronghold in 3,500, mostly lava lakes), up to 12/12.
From 1.17 the frames are protected against the lava border, and from 1.18 against
lakes altogether (features_cannot_replace) — so this finder is 1.16 only for now.

What is exact here: the lake position and shape (population seed, decorator seed
of the LAKES step, LakeWater/LavaPlacementDecorator, the 4–7 ellipsoids), checked
block for block against real lakes. What is NOT known here: the terrain (Minecraft
cancels a lake that touches air / liquid in the wrong place), so every candidate
is only a candidate — the Minecraft checker (mc_checker) confirms it.

Candidate rule (from the 17 real breaks among 60,000 strongholds): a frame is in
the TOP layer of the lake (u = 6, it becomes cave air) or, for lava, in the stone
border just above / beside that layer (u >= 6). Otherwise the lake's lower half
touches the portal room's air and Minecraft cancels it. This kept every break of 3
frames or more (88 candidates for 14 real breaks); a few 1–2-frame breaks are lost.

API
  lake_candidates(seed, sx, sz, mc, kinds)       -> [cand]   (one stronghold)
  search_lake_portals(start, count, callback, stop_event, progress_callback,
                      num_workers, mc_version, kinds, scan_128, seed_list)
      callback(seed, ex, ey, ez, lx, ly, lz, npred, kind, sx, sz, frames, trig)
      trig = (chunk x, chunk z) of the chunk whose decoration places the lake: the
      checker generates the frame chunks first and that chunk last.
"""
from __future__ import annotations

import ctypes
import multiprocessing as mp

import cubiomes_wrap

LAKE_VERSIONS = (19, 20)          # 1.16.1, 1.16.5 (1.16.2+ uses the same lake code)
KINDS = ("lava", "water")
_DESERT = {2, 17, 130}            # desert, desert_hills, desert_lakes: lava lake only (index 0)
_M = (1 << 48) - 1
_MUL = 0x5DEECE66D
_U64 = (1 << 64) - 1


# ── java.util.Random ─────────────────────────────────────────────────────────
class _JR:
    __slots__ = ("s",)

    def __init__(self, seed):
        self.s = (seed ^ _MUL) & _M

    def _next(self, bits):
        self.s = (self.s * _MUL + 0xB) & _M
        return self.s >> (48 - bits)

    def next_int(self, n):
        if n & (-n) == n:
            return (n * self._next(31)) >> 31
        while True:
            b = self._next(31)
            v = b % n
            if b - v + (n - 1) < (1 << 31):
                return v

    def next_long(self):
        hi = self._next(32)
        lo = self._next(32)
        if hi >= 1 << 31:
            hi -= 1 << 32
        if lo >= 1 << 31:
            lo -= 1 << 32
        return ((hi << 32) + lo) & _U64

    def next_double(self):
        return ((self._next(26) << 27) + self._next(27)) * (1.0 / (1 << 53))


def _s64(x):
    x &= _U64
    return x - (1 << 64) if x >> 63 else x


def population_seed(world_seed, bx, bz):
    """ChunkRandom.setPopulationSeed (1.13–1.17)."""
    r = _JR(world_seed)
    a = _s64(r.next_long() | 1)
    b = _s64(r.next_long() | 1)
    return _s64(bx * a + bz * b ^ world_seed)


def chunk_lakes(world_seed, cx, cz, desert):
    """The lakes chunk (cx, cz) places in 1.16: [(kind, x, y, z, rng)], where
    (x, y, z) is the decorator position (before the air drop and the down(4)) and
    rng is the Random positioned for LakeFeature.generate."""
    pop = population_seed(world_seed, cx * 16, cz * 16)
    out = []
    idx = 0
    if not desert:                                # water lake: index 0 of LAKES (step 1)
        r = _JR(pop + 10000)
        if r.next_int(4) == 0:
            x = r.next_int(16) + cx * 16
            z = r.next_int(16) + cz * 16
            y = r.next_int(256)
            out.append(("water", x, y, z, r))
        idx = 1
    r = _JR(pop + idx + 10000)                    # lava lake
    if r.next_int(8) == 0:
        x = r.next_int(16) + cx * 16
        z = r.next_int(16) + cz * 16
        y = r.next_int(r.next_int(248) + 8)
        if y < 63 or r.next_int(10) == 0:
            out.append(("lava", x, y, z, r))
    return out


def _could_hit(world_seed, cx, cz, cols, kinds):
    """Cheap pre-test of chunk_lakes for both biome cases: can a lake of `kinds`
    at a usable height cover one of the frame columns?"""
    pop = population_seed(world_seed, cx * 16, cz * 16)

    def covers(x, z):
        return any(x <= fx <= x + 15 and z <= fz <= z + 15 for fx, fz in cols)
    if "water" in kinds:                          # not desert: water lake, index 0
        r = _JR(pop + 10000)
        if r.next_int(4) == 0:
            x = r.next_int(16) + cx * 16
            z = r.next_int(16) + cz * 16
            y = r.next_int(256)
            if 5 <= y <= 64 and covers(x, z):
                return True
    if "lava" in kinds:
        for idx in (0, 1):                        # desert: index 0; otherwise 1
            r = _JR(pop + idx + 10000)
            if r.next_int(8) == 0:
                x = r.next_int(16) + cx * 16
                z = r.next_int(16) + cz * 16
                y = r.next_int(r.next_int(248) + 8)
                if 5 <= y <= 64 and covers(x, z):
                    return True
    return False


def lake_blobs(r):
    """The ellipsoids of LakeFeature (consumes the same randoms)."""
    blobs = []
    for _ in range(r.next_int(4) + 4):
        d = r.next_double() * 6.0 + 3.0
        e = r.next_double() * 4.0 + 2.0
        f = r.next_double() * 6.0 + 3.0
        g = r.next_double() * (16.0 - d - 2.0) + 1.0 + d / 2.0
        h = r.next_double() * (8.0 - e - 4.0) + 2.0 + e / 2.0
        k = r.next_double() * (16.0 - f - 2.0) + 1.0 + f / 2.0
        blobs.append((g, h, k, d / 2.0, e / 2.0, f / 2.0))
    return blobs


def _inside(blobs, l, n, m):
    if not (1 <= l <= 14 and 1 <= m <= 14 and 1 <= n <= 6):
        return False
    for g, h, k, rd, re, rf in blobs:
        o = (l - g) / rd
        p = (n - h) / re
        q = (m - k) / rf
        if o * o + p * p + q * q < 1.0:
            return True
    return False


def _border(blobs, l, n, m):
    if not (0 <= l < 16 and 0 <= m < 16 and 0 <= n < 8) or _inside(blobs, l, n, m):
        return False
    return any(_inside(blobs, l + a, n + b, m + c) for a, b, c in
               ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)))


def lake_hits(kind, x, y, blobs, z, frames):
    """Frames the lake (ellipsoids `blobs`) can break under the candidate rule."""
    y0 = y - 4
    n = 0
    for fx, fy, fz in frames:
        l, u, m = fx - x, fy - y0, fz - z
        if u == 6 and _inside(blobs, l, u, m):
            n += 1
        elif kind == "lava" and u >= 6 and _border(blobs, l, u, m):
            n += 1
    return n


# ── one stronghold ───────────────────────────────────────────────────────────
_W = {}


def _winit(mc):
    """Own generator context (never ruined_portal's: both searches may run in the
    same process, in two threads, when each has a single worker)."""
    import ruined_portal as rp
    path = cubiomes_wrap._find_lib() or cubiomes_wrap._compile_cubiomes()
    lib = ctypes.CDLL(path)
    rp._bind(lib)
    ctx = lib.cb_create()
    cubiomes_wrap.apply_large_ctx(lib, ctx)
    _W.update(lib=lib, ctx=ctx, mc=mc)
    lib.getBiomeAt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int]
    lib.getBiomeAt.restype = ctypes.c_int
    if hasattr(lib, "cb_strongholds128"):
        _W["pos128"] = (ctypes.c_int * 256)()


def lake_frames_for(seed, sx, sz, mc, kinds=KINDS):
    """Frames predicted broken by a lake for the stronghold starting at block
    (sx, sz) (0 if none / not 1.16) — for the session condition (03/10)."""
    if mc not in LAKE_VERSIONS:
        return 0
    if not _W or _W.get("mc") != mc:
        _winit(mc)
    if _W.get("seed") != seed:
        _W["lib"].cb_setup(_W["ctx"], mc, int(seed) & ((1 << 64) - 1))
        _W["seed"] = seed
    cands = lake_candidates(int(seed), int(sx), int(sz), mc, kinds)
    return max((int(d["npred"]) for d in cands), default=0)


_NY = {}


def _frame_y_native(seed, sx, sz):
    """Y of the end portal frames from the native generator (03/10): the top of
    the portal room (gaider topY, TOPY_ROOM flag) − 4 (the room is 8 high, the
    frames stand on its local y 3). Checked equal to the Python BFS on 4 140
    strongholds (1.16.1 and 1.16.5). ~100× faster: the Python BFS was 60 % of
    the lake search. None when the native library cannot (then: Python BFS)."""
    if "lib" not in _NY:
        _NY["lib"] = None
        try:
            import gaider_layouts as gl
            import stronghold_eyes as se
            gl._load()
            if gl._LIB is not None and getattr(gl, "_HAS_MAXY", False) and se.native_room_ok():
                _NY.update(lib=gl._LIB, flag=se.TOPY_ROOM,
                           cx=(ctypes.c_int32 * 1)(), cz=(ctypes.c_int32 * 1)(),
                           os=(ctypes.c_int64 * 1)(), oy=(ctypes.c_int32 * 1)())
        except Exception:
            _NY["lib"] = None
    lib = _NY["lib"]
    if lib is None or _W.get("mc") not in LAKE_VERSIONS:
        return None
    _NY["cx"][0], _NY["cz"][0] = int(sx) >> 4, int(sz) >> 4
    n = lib.max_y_batch(ctypes.c_int64(_s64(seed)), ctypes.c_int32(1), _NY["cx"], _NY["cz"],
                        ctypes.c_int(1), ctypes.c_int(63), ctypes.c_int(10), ctypes.c_int(0),
                        ctypes.c_int(_NY["flag"]), ctypes.c_int(-1000000), _NY["os"], _NY["oy"])
    if n != 1 or _NY["oy"][0] <= -100000:          # TOPY_OVERFLOW: let Python decide
        return None
    return int(_NY["oy"][0]) - 4


def _frames_xz(seed, sx, sz):
    import ruined_portal as rp
    fxz = rp._frames_xz_native(seed, sx >> 4, sz >> 4, True)
    if fxz is not None:
        ex, ez, cols = fxz
        return ex, ez, list(cols), None
    import stronghold_eyes as se
    fr = se.get_end_portal_frames(seed, sx, sz, _W["mc"])
    if fr is None:
        return None
    frames, ex, ez, fy = fr
    return ex, ez, [(f[0], f[2]) for f in frames], frames


def lake_candidates(seed, sx, sz, mc=None, kinds=KINDS):
    """Lake candidates for the stronghold starting at block (sx, sz). The
    generator in _W must be set up for `seed` (cb_setup). Returns
    [dict(kind, lake=(x, y, z), trig=(cx, cz), npred, frames, ex, ey, ez)]."""
    if not _W:
        _winit(mc or 20)
    lib, ctx = _W["lib"], _W["ctx"]
    got = _frames_xz(seed, sx, sz)
    if got is None:
        return []
    ex, ez, cols, frames = got
    fcs = {(x >> 4, z >> 4) for x, z in cols}
    ring = {(a + dx, b + dz) for a, b in fcs for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
    out = []
    for cx, cz in sorted(ring):                    # a lake reaches +15 blocks: ring of 1
        # the biome (desert or not: which lakes the chunk can place) costs more
        # than the lakes themselves: asked only when a lake of either case could
        # cover a frame column (03/10; same result, ~40 % faster)
        if not _could_hit(seed, cx, cz, cols, kinds):
            continue
        desert = lib.getBiomeAt(ctx, 4, cx * 4 + 2, 0, cz * 4 + 2) in _DESERT
        for kind, x, y, z, rng in chunk_lakes(seed, cx, cz, desert):
            if kind not in kinds:
                continue
            if not 5 <= y <= 64:                       # frames lie between y ~10 and ~60
                continue
            if not any(x <= fx <= x + 15 and z <= fz <= z + 15 for fx, fz in cols):
                continue
            blobs = lake_blobs(rng)
            # the rule fixes the frame layer relative to the lake (u = 6 / 7): the
            # columns can be tested before the (costly) exact frame Y
            if not any(_inside(blobs, fx - x, 6, fz - z) or
                       (kind == "lava" and (_border(blobs, fx - x, 6, fz - z) or
                                            _border(blobs, fx - x, 7, fz - z)))
                       for fx, fz in cols):
                continue
            if frames is None:                      # the exact Y, once
                fy0 = _frame_y_native(seed, sx, sz)
                if fy0 is not None:                 # native (gaider topY, portal room)
                    frames = [(fx, fy0, fz) for fx, fz in cols]
                else:                               # Python BFS
                    import stronghold_eyes as se
                    fr = se.get_end_portal_frames(seed, sx, sz, _W["mc"])
                    if fr is None:
                        return out
                    frames, ex, ez, _fy = fr
            fy = frames[0][1]
            if not (fy - 7 <= y - 4 <= fy - 6):     # rule: frames at u = 6 or 7
                continue
            n = lake_hits(kind, x, y, blobs, z, frames)
            if n > 0:
                out.append(dict(kind=kind, lake=(x, y, z), trig=(cx, cz), npred=n,
                                frames=list(frames), ex=ex, ey=fy, ez=ez))
    return out


def _strongholds(seed, scan_128):
    lib, ctx, mc = _W["lib"], _W["ctx"], _W["mc"]
    lib.cb_setup(ctx, mc, seed & _U64)
    if scan_128 and "pos128" in _W:
        from stronghold_eyes import sh_count
        pos = _W["pos128"]
        k = lib.cb_strongholds128(ctx, mc, seed & _U64, pos, sh_count(scan_128))
        return [(pos[2 * j], pos[2 * j + 1]) for j in range(k)]
    a = [ctypes.c_int(0) for _ in range(4)]
    lib.cb_first_stronghold(ctx, mc, seed & _U64, *[ctypes.byref(v) for v in a])
    return [(a[2].value, a[3].value)]


def _chunk(args):
    seed0, n, kinds, scan_128, sub = args
    import cubiomes_wrap as cw
    cw.apply_large_ctx(_W["lib"], _W["ctx"])
    seeds = sub if sub is not None else range(seed0, seed0 + n)
    hits = []
    for seed in seeds:
        seed = _s64(seed)
        for sx, sz in _strongholds(seed, scan_128):
            for c in lake_candidates(seed, sx, sz, kinds=kinds):
                lx, ly, lz = c["lake"]
                hits.append((seed, c["ex"], c["ey"], c["ez"], lx, ly, lz, c["npred"], c["kind"],
                             int(sx), int(sz), c["frames"], c["trig"]))
    return (seed0, len(seeds), hits)


def search_lake_portals(seed_start, seed_count, callback, stop_event=None,
                        progress_callback=None, num_workers=1, mc_version=20,
                        kinds=KINDS, scan_128=False, seed_list=None, chunk_size=2000,
                        throttle=None, chunk_callback=None):
    """Lake-broken end portal candidates (1.16). Same conventions as
    ruined_portal.search_corrupted_portals (seed_count <= 0 = until stop_event).
    throttle(): called before each new block of seeds is handed out; it may block
    (the Minecraft checks are much slower than this search). Only a few blocks
    are in flight at a time, so a blocked throttle really pauses the search."""
    if mc_version not in LAKE_VERSIONS:
        raise ValueError("lake-broken portals are searched in 1.16 only")
    kinds = tuple(k for k in kinds if k in KINDS)
    if scan_128:
        from stronghold_eyes import sh_count
        chunk_size = min(chunk_size, 50 if sh_count(scan_128) >= 128 else 500)
    if seed_list is not None:
        try:
            seed_count = len(seed_list)
        except TypeError:
            seed_count = 0
    done = [0]

    def stopped():
        return stop_event is not None and stop_event.is_set()

    def _emit(res):
        _s0, k, hits = res
        for h in hits:
            callback(*h)
        if chunk_callback is not None:     # this block is fully checked (resume, 03/10)
            chunk_callback(_s0, k)
        done[0] += k
        if progress_callback:
            progress_callback(done[0], seed_count)

    def _chunks():
        if seed_list is not None:
            from itertools import islice
            src = iter(seed_list)
            idx = 0
            while not stopped():
                sub = list(islice(src, chunk_size))
                if not sub:
                    return
                yield (idx, len(sub), kinds, scan_128, sub)
                idx += len(sub)
            return
        s = seed_start
        end = seed_start + seed_count if seed_count and seed_count > 0 else None
        while end is None or s < end:
            if stopped():
                return
            k = chunk_size if end is None else min(chunk_size, end - s)
            yield (s, k, kinds, scan_128, None)
            s += k

    def _next(it):
        if throttle is not None:
            throttle()
        if stopped():
            return None
        return next(it, None)

    nwork = max(1, int(num_workers))
    it = _chunks()
    if nwork == 1:
        _winit(mc_version)
        while True:
            ch = _next(it)
            if ch is None:
                break
            _emit(_chunk(ch))
        return
    import time
    ctx = mp.get_context("spawn")
    with ctx.Pool(nwork, initializer=_winit, initargs=(mc_version,)) as pool:
        try:
            flight = []
            more = True
            while True:
                while more and len(flight) < 2 * nwork:
                    ch = _next(it)
                    if ch is None:
                        more = False
                        break
                    flight.append(pool.apply_async(_chunk, (ch,)))
                if not flight:
                    break
                ready = [f for f in flight if f.ready()]
                if not ready:
                    time.sleep(0.02)
                    if stopped():
                        break
                    continue
                for f in ready:
                    flight.remove(f)
                    _emit(f.get())
                if stopped():
                    break
        finally:
            pool.terminate()
            pool.join()


if __name__ == "__main__":                      # py lake_portals.py SEED…  (first stronghold, 1.16.5)
    import sys
    _winit(20)
    for a in sys.argv[1:]:
        s = int(a)
        for sx, sz in _strongholds(s, False):
            for c in lake_candidates(s, sx, sz):
                print(s, c["kind"], c["lake"], "chunk", c["trig"], "frames", c["npred"],
                      "portal", (c["ex"], c["ey"], c["ez"]))
