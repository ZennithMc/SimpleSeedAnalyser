"""
struct_crack.py — (06/11) the structures seen in a world → its structure seed (48 bits).

Every "random spread" structure is tried once per region (32×32 chunks for a village
in 1.16…): the structure seed alone picks the chunk of the try (see
cubiomes_src/structcrack_core.h). Entering the chunk where a structure STARTS —
exactly, or a few candidate chunks when unsure — gives ~9 bits per structure; ~6
structures single out the structure seed, any version from 1.14 to 26.x. Whether the
try really spawns (biome) depends on the whole world seed: that check finds the
upper 16 bits (world_seeds).

    obs = [Obs("desert_pyramid", 1234, -560), Obs("village", 300, 900, radius=1), ...]
    seeds = search(obs, mc)          # lifting on the 20 low bits, then the 28 high ones
"""
from __future__ import annotations

import ctypes
import math
import os
import threading
import time
from dataclasses import dataclass

import cubiomes_wrap as cw

_U64 = ctypes.c_uint64
_I64 = ctypes.c_int64

MAXOBS, MAXCAND, LOWBITS = 48, 25, 20
SCK_FEATURE, SCK_LARGE, SCK_OUTPOST, SCK_FORTRESS16, SCK_BASTION16, SCK_BASTION18, SCK_ENDCITY = range(7)

MC_116_1 = cw.MC_VERSIONS["Java 1.16.1"]
MC_118 = cw.MC_VERSIONS["Java 1.18"]

# key: (label, cubiomes key, dimension, what to read, default uncertainty in chunks)
# (06/11) checked on 1.16.5 / 1.21.4 servers (seed 12345): the temples' north-west corner
# IS the start chunk's corner; the other structures turn around that corner (their
# first piece has a corner — the monument / ancient city their centre — on it), so
# the middle of their main piece is in that chunk or the one west / north of it.
STRUCTS = {
    "village": ("Village", "village", 0,
                "the middle of the village's centre piece (well / meeting point with the bell)", 1),
    "pillager_outpost": ("Pillager outpost", "pillager_outpost", 0, "the middle of the tower", 1),
    "desert_pyramid": ("Desert pyramid", "desert_pyramid", 0,
                       "the pyramid's north-west corner block (lowest X and Z)", 0),
    "jungle_temple": ("Jungle temple", "jungle_temple", 0,
                      "the temple's north-west corner block (lowest X and Z)", 0),
    "swamp_hut": ("Swamp hut", "swamp_hut", 0, "the hut's north-west corner (lowest X and Z, stilts included)", 0),
    "igloo": ("Igloo", "igloo", 0, "the middle of the igloo", 1),
    "shipwreck": ("Shipwreck", "shipwreck", 0, "the middle of the wreck", 1),
    "ocean_ruin": ("Ocean ruin", "ocean_ruin", 0, "the middle of the main ruin", 1),
    "ruined_portal": ("Ruined portal", "ruined_portal", 0, "the portal frame", 1),
    "monument": ("Ocean monument", "monument", 0, "the monument's centre (the middle of its roof)", 1),
    "mansion": ("Woodland mansion", "mansion", 0, "the middle of the mansion (not checked on a server)", 2),
    "ancient_city": ("Ancient city", "ancient_city", 0, "the middle of the big portal frame at the centre", 1),
    "trail_ruins": ("Trail ruins", "trail_ruins", 0, "the middle of the ruins", 2),
    "trial_chambers": ("Trial chambers", "trial_chambers", 0, "the middle of the chambers", 2),
    "abandoned_camp": ("Abandoned camp", "abandoned_camp", 0, "the middle of the camp (not checked on a server)", 2),
    "fortress": ("Nether fortress", "fortress", -1, "the fortress' first crossing (not checked on a server)", 2),
    "bastion": ("Bastion remnant", "bastion", -1, "the middle of the bastion (not checked on a server)", 2),
    "ruined_portal_nether": ("Ruined portal (Nether)", "ruined_portal_nether", -1, "the portal frame", 1),
    "end_city": ("End city", "end_city", 1, "the base of the first tower (not checked on a server)", 2),
}


class _ScObs(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_int32), ("region", ctypes.c_int32), ("range", ctypes.c_int32),
                ("ncand", ctypes.c_int32), ("salt", ctypes.c_int64),
                ("rx", ctypes.c_int32 * MAXCAND), ("rz", ctypes.c_int32 * MAXCAND),
                ("ox", ctypes.c_int32 * MAXCAND), ("oz", ctypes.c_int32 * MAXCAND)]


class _ScSet(ctypes.Structure):
    _fields_ = [("n", ctypes.c_int32), ("o", _ScObs * MAXOBS)]


class _SConf(ctypes.Structure):
    _fields_ = [("salt", ctypes.c_int32), ("regionSize", ctypes.c_int8), ("chunkRange", ctypes.c_int8),
                ("structType", ctypes.c_uint8), ("dim", ctypes.c_int8), ("rarity", ctypes.c_float)]


_CL = None


def _cubiomes():
    global _CL
    if _CL is None:
        _CL = cw.CubiomesLib()
        L = _CL._lib
        L.getStructureConfig.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_SConf)]
        L.getStructureConfig.restype = ctypes.c_int
    return _CL


def config(key: str, mc: int):
    """(salt, region, range, kind) of structure `key` in version mc — None if it
    doesn't exist there (or isn't supported)"""
    st = cw.ALL_STRUCTURE_TYPES[STRUCTS[key][1]]
    sc = _SConf()
    if not _cubiomes()._lib.getStructureConfig(st, mc, ctypes.byref(sc)):
        return None
    if key == "monument" or key == "mansion":
        kind = SCK_LARGE
    elif key == "end_city":
        kind = SCK_ENDCITY
    elif key == "pillager_outpost":
        kind = SCK_OUTPOST
    elif key == "fortress":
        if mc < MC_116_1:
            return None                     # 1.15-: another placement (not supported)
        kind = SCK_FORTRESS16 if mc < MC_118 else SCK_FEATURE
    elif key == "bastion":
        kind = SCK_BASTION16 if mc < MC_118 else SCK_BASTION18
    else:
        kind = SCK_FEATURE
    return sc.salt, int(sc.regionSize), int(sc.chunkRange), kind


def available(mc: int) -> list:
    return [k for k in STRUCTS if config(k, mc) is not None]


@dataclass
class Obs:
    key: str
    x: int                      # block coordinates read in game
    z: int
    radius: int = 0             # 0: that chunk; 1: or one of the 8 around; 2: the 24 around

    def to_json(self):
        return {"key": self.key, "x": self.x, "z": self.z, "r": self.radius}

    @staticmethod
    def from_json(d):
        return Obs(d["key"], int(d["x"]), int(d["z"]), int(d.get("r", 0)))

    def chunks(self):
        cx, cz = self.x >> 4, self.z >> 4
        r = max(0, min(2, self.radius))
        return [(cx + dx, cz + dz) for dx in range(-r, r + 1) for dz in range(-r, r + 1)]


def _tri(r: int) -> list:
    """P(offset) for the large structures: (nextInt(r) + nextInt(r)) >> 1"""
    p = [0.0] * r
    for a in range(r):
        for b in range(r):
            p[(a + b) >> 1] += 1.0 / (r * r)
    return p


def candidates(o: Obs, mc: int) -> list:
    """[(rx, rz, ox, oz)] of the entered chunks where this structure can start"""
    cf = config(o.key, mc)
    if cf is None:
        return []
    _salt, reg, rng, kind = cf
    out = []
    for cx, cz in o.chunks():
        rx, rz = cx // reg, cz // reg
        ox, oz = cx - rx * reg, cz - rz * reg
        if ox < rng and oz < rng:
            if kind == SCK_ENDCITY and (cx * 16) ** 2 + (cz * 16) ** 2 < 1008 ** 2:
                continue
            out.append((rx, rz, ox, oz))
    return out


def obs_bits(o: Obs, mc: int) -> float:
    """information (bits) one structure gives on the structure seed"""
    cf = config(o.key, mc)
    cands = candidates(o, mc)
    if cf is None or not cands:
        return 0.0
    _s, _reg, rng, kind = cf
    if kind in (SCK_LARGE, SCK_ENDCITY):
        t = _tri(rng)
        p = sum(t[ox] * t[oz] for _rx, _rz, ox, oz in cands)
    else:
        p = len(cands) / (rng * rng)
    p *= {SCK_OUTPOST: 0.2, SCK_FORTRESS16: 0.4, SCK_BASTION16: 0.6, SCK_BASTION18: 0.6}.get(kind, 1.0)
    return -math.log2(max(p, 1e-12))


def lift_bits(o: Obs, mc: int) -> float:
    """bits it gives on the 20 low bits (lifting)"""
    cf = config(o.key, mc)
    cands = candidates(o, mc)
    if cf is None or not cands or cf[3] in (SCK_LARGE, SCK_ENDCITY):
        return 0.0
    r = cf[2]
    if r & (r - 1) == 0:
        return 0.0
    k = 0
    while r % 2 == 0:
        r //= 2
        k += 1
    k = min(k, LOWBITS - 17)
    if not k:
        return 0.0
    m = (1 << k) - 1
    res = {(ox & m, oz & m) for _rx, _rz, ox, oz in cands}
    return 2 * k - math.log2(len(res))


def effective_bits(obs, mc) -> float:
    """the information that really counts: the lifting part only fills the 20 low bits
    (beyond, it repeats what other structures said), the rest (e.g. nextInt(24) mod 3,
    the large structures, the outposts' extra draw) goes to the 28 high bits"""
    lift = sum(lift_bits(o, mc) for o in obs)
    tot = sum(obs_bits(o, mc) for o in obs)
    return min(lift, LOWBITS) + (tot - lift)


def search_seconds(obs, mc, threads=0) -> float:
    threads = threads or (os.cpu_count() or 2)
    return estimate_lift(obs, mc) * (1 << 28) * SEC_PER_SEED / threads


def problems(obs, mc) -> list:
    out = []
    for o in obs:
        lab = STRUCTS.get(o.key, ("?",))[0]
        cf = config(o.key, mc) if o.key in STRUCTS else None
        if cf is None:
            out.append(("error", f"{lab}: not generated in this version (or not supported)"))
        elif not candidates(o, mc):
            _s, reg, rng, _k = cf
            out.append(("error", f"{lab} at {o.x}, {o.z}: its chunk can't hold one — in each "
                                 f"region of {reg}×{reg} chunks it starts in the first {rng}×{rng}"))
    if len(obs) > MAXOBS:
        out.append(("error", f"At most {MAXOBS} structures"))
    return out


def _set(obs, mc) -> _ScSet:
    S = _ScSet()
    # the most selective first (fewest candidates, then rarest)
    order = sorted(obs, key=lambda o: -obs_bits(o, mc))
    S.n = len(order)
    for i, o in enumerate(order):
        salt, reg, rng, kind = config(o.key, mc)
        c = S.o[i]
        c.kind, c.region, c.range, c.salt = kind, reg, rng, salt
        cands = candidates(o, mc)[:MAXCAND]
        c.ncand = len(cands)
        for j, (rx, rz, ox, oz) in enumerate(cands):
            c.rx[j], c.rz[j], c.ox[j], c.oz[j] = rx, rz, ox, oz
    return S


def _lib():
    import tree_crack as tc
    L = tc.lib()
    if not getattr(L, "_sc_bound", False):
        PS = ctypes.POINTER(_ScSet)
        P32 = ctypes.POINTER(ctypes.c_uint32)
        PU = ctypes.POINTER(_U64)
        for name, res, args in (
                ("sc_version", ctypes.c_int, []),
                ("sc_lift", _I64, [PS, P32, _I64, ctypes.c_int]),
                ("sc_search", _I64, [PS, P32, _I64, PU, _I64, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]),
                ("sc_check", None, [PS, PU, _I64, ctypes.POINTER(ctypes.c_uint8)])):
            fn = getattr(L, name)
            fn.restype, fn.argtypes = res, args
        L._sc_bound = True
    return L


# seconds per checked seed and thread (measured: 4 – 8 ns)
SEC_PER_SEED = 7e-9


# (06/11) GPU: seeds per second (RTX-class card, conservative)
GPU_SEEDS_PER_S = 2e10
_GPU = None


def gpu():
    """(lib | None, message): tree_cuda's structure search, self-tested"""
    global _GPU
    if _GPU is not None:
        return _GPU
    import tree_crack as tc
    g, msg = tc.gpu()
    if g is not None:
        try:
            fn = g.sc_cuda_range
            fn.restype = _I64
            fn.argtypes = [ctypes.POINTER(_ScSet), ctypes.POINTER(ctypes.c_uint32), _I64, _U64, _U64,
                           ctypes.POINTER(_U64), _I64]
            if not _gpu_selftest(g):
                g, msg = None, "GPU structure search self-test failed — using the CPU"
            else:
                msg = "GPU"
        except AttributeError:
            g, msg = None, ("tree_cuda library too old for structures — rebuild it: "
                            "py tools/build_native.py --unit tree_cuda")
    _GPU = (g, msg)
    return _GPU


def _gpu_selftest(g) -> bool:
    ss, mc = 12345, cw.MC_VERSIONS["Java 1.16"]
    obs = []
    lib = _cubiomes()
    pos = cw.Pos()
    for key, rx, rz in (("desert_pyramid", 2, 0), ("swamp_hut", 0, 1), ("village", 1, 1)):
        st = cw.ALL_STRUCTURE_TYPES[STRUCTS[key][1]]
        lib._lib.getStructurePos(st, mc, ctypes.c_uint64(ss), rx, rz, ctypes.byref(pos))
        obs.append(Obs(key, pos.x, pos.z))
    S = _set(obs, mc)
    L = _lib()
    n = L.sc_lift(ctypes.byref(S), None, 0, 1)
    lows = (ctypes.c_uint32 * max(1, n))()
    L.sc_lift(ctypes.byref(S), lows, n, 1)
    low = ss & ((1 << LOWBITS) - 1)
    try:
        i = list(lows[:n]).index(low)
    except ValueError:
        return False
    g0 = (i << 28) | (ss >> LOWBITS)
    out = (_U64 * 4096)()
    k = g.sc_cuda_range(ctypes.byref(S), lows, n, max(0, g0 - 5000), 10000, out, 4096)
    return k > 0 and ss in list(out[:min(k, 4096)])


def search_seconds_device(obs, mc, device="auto", threads=0) -> float:
    """(the GPU counts once probed — gpu() — so the UI thread never loads CUDA)"""
    if device != "cpu" and _GPU is not None and _GPU[0] is not None:
        return estimate_lift(obs, mc) * (1 << 28) / GPU_SEEDS_PER_S
    return search_seconds(obs, mc, threads)


def plan(obs, mc, threads=0):
    """(survivors of the lifting, total bits, seconds on the CPU) — runs the lifting"""
    threads = threads or (os.cpu_count() or 2)
    if not obs:
        return 0, 0.0, 0.0
    L = _lib()
    S = _set(obs, mc)
    n = L.sc_lift(ctypes.byref(S), None, 0, threads)
    return n, sum(obs_bits(o, mc) for o in obs), n * (1 << 28) * SEC_PER_SEED / threads


def estimate_lift(obs, mc) -> int:
    """survivors expected after lifting, without running it"""
    b = sum(lift_bits(o, mc) for o in obs)
    return max(1, int(2 ** (LOWBITS - min(b, LOWBITS))))


def search(obs, mc, threads=0, stop: threading.Event | None = None, on_progress=None,
           cap=1 << 20, device="auto") -> list:
    """every structure seed placing every structure entered (sorted). device: "auto"
    (GPU when tree_cuda has the structure kernel) or "cpu"."""
    probs = [p for p in problems(obs, mc) if p[0] == "error"]
    if probs:
        raise ValueError(probs[0][1])
    if not obs:
        raise ValueError("No structure")
    threads = threads or (os.cpu_count() or 2)
    L = _lib()
    S = _set(obs, mc)
    n = L.sc_lift(ctypes.byref(S), None, 0, threads)
    lows = (ctypes.c_uint32 * max(1, n))()
    L.sc_lift(ctypes.byref(S), lows, n, threads)
    out = (_U64 * cap)()
    g = gpu()[0] if device != "cpu" else None
    if g is not None:
        return _search_gpu(g, S, lows, n, out, cap, stop, on_progress)
    stop_flag = ctypes.c_int(0)
    prog = ctypes.c_int64(0)
    res = {}

    def run():
        res["n"] = L.sc_search(ctypes.byref(S), lows, n, out, cap, threads,
                               ctypes.addressof(stop_flag), ctypes.addressof(prog))
    th = threading.Thread(target=run, daemon=True, name="ssa-struct-crack")
    th.start()
    total = max(1, n * 16)
    t0 = time.time()
    while th.is_alive():
        th.join(0.25)
        if stop is not None and stop.is_set():
            stop_flag.value = 1
        if on_progress:
            f = prog.value / total
            el = time.time() - t0
            on_progress(f, el / f - el if f > 0.001 else None)
    k = min(res.get("n", 0), cap)
    return sorted(set(out[:k]))


def _search_gpu(g, S, lows, n, out, cap, stop, on_progress) -> list:
    total = n << 28
    piece = 1 << 34                          # ~1 s per call on a fast card: progress / stop
    found = 0
    t0 = time.time()
    g0 = 0
    while g0 < total:
        if stop is not None and stop.is_set():
            break
        m = min(piece, total - g0)
        room = max(0, cap - found)
        buf = (_U64 * max(1, min(room, 1 << 20)))()
        k = g.sc_cuda_range(ctypes.byref(S), lows, n, g0, m, buf, min(room, 1 << 20))
        if k < 0:
            import native_libs as nl
            raise RuntimeError("GPU error: " + nl.last_error(g))
        take = min(k, room, 1 << 20)
        for i in range(take):
            out[found + i] = buf[i]
        found += take
        g0 += m
        if on_progress:
            f = g0 / total
            el = time.time() - t0
            on_progress(f, el / f - el if f > 0.001 else None)
    return sorted(set(out[:found]))


def check(obs, mc, seeds) -> list:
    """the seeds placing every structure (filter)"""
    seeds = list(seeds)
    if not obs or not seeds:
        return seeds
    L = _lib()
    S = _set(obs, mc)
    arr = (_U64 * len(seeds))(*seeds)
    ok = (ctypes.c_uint8 * len(seeds))()
    L.sc_check(ctypes.byref(S), arr, len(seeds), ok)
    return [s for s, k in zip(seeds, ok) if k]


def positions(ss: int, obs, mc) -> list:
    """block position (cubiomes getStructurePos) of each entered structure for seed ss,
    or None when its try is elsewhere"""
    g = _cubiomes()
    out = []
    pos = cw.Pos()
    for o in obs:
        cf = config(o.key, mc)
        st = cw.ALL_STRUCTURE_TYPES[STRUCTS[o.key][1]]
        hit = None
        for rx, rz, ox, oz in candidates(o, mc):
            if g._lib.getStructurePos(st, mc, ctypes.c_uint64(ss), rx, rz, ctypes.byref(pos)) and \
                    (pos.x >> 4, pos.z >> 4) == (rx * cf[1] + ox, rz * cf[1] + oz):
                hit = (pos.x, pos.z)
                break
        out.append(hit)
    return out


def viable_check(obs, mc):
    """ok(world_seed, positions) → every structure can spawn there (biomes): finds the
    upper 16 bits of the world seed"""
    gens = {}

    def gen(dim):
        if dim not in gens:
            gens[dim] = cw.CubiomesLib()
        return gens[dim]

    def ok(seed, pos):
        for dim in sorted({STRUCTS[o.key][2] for o in obs}):
            g = gen(dim)
            g.setup_and_seed(mc, seed, large_biomes=False, dim=dim)
            for o, p in zip(obs, pos):
                if STRUCTS[o.key][2] != dim or p is None:
                    continue
                st = cw.ALL_STRUCTURE_TYPES[STRUCTS[o.key][1]]
                if not g._lib.isViableStructurePos(st, g._gen, p[0], p[1], 0):
                    return False
        return True
    return ok
