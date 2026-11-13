"""
ring_gpu.py — (19/10) GPU scan of the positions of a spiral iterator.

A session « crunchiness (GPU) + spiral (GPU ticked) whose subtree starts with
climate tests » is searched like this:
  1. the GPU keeps the crunchy seeds (gaider_cuda, as before);
  2. for each of them the GPU tests EVERY position of the spiral against the
     climate-only parts of its subtree (cubiomes_src/ring_gpu_kernel.cuh) and
     keeps the positions that may pass;
  3. the C engine tests the whole session, the spiral visiting only those
     positions, in its own order (ssa_test_seeds_cand) — so the results are the
     CPU's as long as the GPU never drops a position that passes (the bounds
     are widened by margins; measured by tests/ring_gpu_check.py).

    compile_spec(active, mc, large) -> (spec, spiral_idx_1based, notes) or (None, None, why)
    available() -> bool          (noise_gpu with the ring kernel)
    scan(spec, seeds) -> (seed_index[], xz[n,2])          (GPU)
    host_scan(spec, seed) -> xz[n,2]                       (the same code on the CPU, tests)
"""
from __future__ import annotations

import ctypes as ct
import os

import numpy as np

RG_MAXPT, RG_MAXOR, RG_MAXBOX, RG_MAXBB = 12, 4, 16, 64
RG_INF = 2_000_000_000
PARA = {"Temperature": 0, "Humidity": 1, "Continentalness": 2, "Erosion": 3, "Weirdness": 5}
COST = {1: 4, 0: 4, 5: 6, 3: 8, 2: 18}           # perlin samples per value

# margins ×10000 (recall safety): FP32 point / grid of a box / widened climate boxes
M_POINT = 20
M_BOX = 400
M_BIOME = 300
GRID = 7


class RgPt(ct.Structure):
    _fields_ = [("para", ct.c_int), ("lo", ct.c_int), ("hi", ct.c_int)]


class RgBox(ct.Structure):
    _fields_ = [("x1", ct.c_int), ("z1", ct.c_int), ("x2", ct.c_int), ("z2", ct.c_int)]


class RgBB(ct.Structure):
    _fields_ = [("lo", ct.c_int * 5), ("hi", ct.c_int * 5)]


class RgOr(ct.Structure):
    _fields_ = [("kind", ct.c_int), ("k", ct.c_int), ("nbox", ct.c_int), ("grid", ct.c_int),
                ("para", ct.c_int), ("lo", ct.c_int), ("hi", ct.c_int), ("nbb", ct.c_int),
                ("box", RgBox * RG_MAXBOX), ("bb", RgBB * RG_MAXBB)]


class RgSpec(ct.Structure):
    _fields_ = [("npt", ct.c_int), ("pt", RgPt * RG_MAXPT),
                ("nor", ct.c_int), ("ors", RgOr * RG_MAXOR),
                ("x1", ct.c_int), ("z1", ct.c_int), ("x2", ct.c_int), ("z2", ct.c_int),
                ("step", ct.c_int),
                ("rx1", ct.c_int), ("rz1", ct.c_int), ("nx", ct.c_int), ("nz", ct.c_int)]


def _num(v, default):
    if v is None or v == "":
        return default
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _is_point(c):
    return (not int(c.get("rmax", 0) or 0)
            and all(int(c.get(k, 0) or 0) == 0 for k in ("x1", "z1", "x2", "z2")))


def _box_of(c):
    r = int(c.get("rmax", 0) or 0)
    if not r and c.get("area_mode") == 0 and int(c.get("radial", 0) or 0) > 0:
        r = int(c["radial"]) + 1
    if r:
        r -= 1
        return (-r, -r, r, r)                    # the disk's square: more points, safe
    return tuple(int(c.get(k, 0) or 0) for k in ("x1", "z1", "x2", "z2"))


def _biome_bbs(mc, biomes):
    import biome_climate as bc
    out = []
    for b in biomes:
        for lo, hi in bc.boxes(mc, int(b)):
            if not (lo[4] <= 0 <= hi[4] or lo[4] <= 10000 <= hi[4]):
                continue                          # cave biomes (depth below the surface)
            bb = RgBB()
            for j, k in enumerate((0, 1, 2, 3, 5)):
                bb.lo[j] = -RG_INF if lo[k] <= -10000 else lo[k] - M_BIOME
                bb.hi[j] = RG_INF if hi[k] >= 10000 else hi[k] + M_BIOME
            out.append(bb)
    return out


def compile_spec(active, mc, large=False):
    """active: the engine-format list (custom_search.active_conditions order,
    « relative » = 1-based parent). The top-level spiral ticked GPU and what the
    GPU can test of its subtree."""
    if large:
        return None, None, "large biomes are not supported by the GPU spiral scan"
    if int(mc) < 22:
        return None, None, "the GPU spiral scan needs 1.18+"
    sps = [i for i, c in enumerate(active) if c.get("type") == "spiral"
           and int(c.get("relative", 0) or 0) == 0 and (c.get("params") or {}).get("use_gpu")]
    if len(sps) != 1:
        return None, None, "one top-level spiral with « GPU » ticked is needed"
    si = sps[0]
    sc = active[si]
    kids = {}
    for j, c in enumerate(active):
        kids.setdefault(int(c.get("relative", 0) or 0), []).append(j)
    pts, ors, notes = [], [], []

    def walk(j):
        c = active[j]
        t = c.get("type")
        p = c.get("params") or {}
        if t == "climate_minmax" and _is_point(c):
            para = PARA.get(p.get("para"))
            nmax = _num(p.get("nmax"), -1)
            rng = p.get("range") or [None, None]
            lo, hi = _num(rng[0], None), _num(rng[1], None)
            if para is not None and nmax in (-1, 0):
                if not p.get("invert"):
                    pts.append((para, -RG_INF if lo is None else lo - M_POINT,
                                RG_INF if hi is None else hi + M_POINT))
                elif lo is not None and hi is not None and hi - M_POINT > lo + M_POINT:
                    # (22/10) « Invert test »: outside [lo, hi] — sent as lo' > hi'
                    # (the kernel keeps v >= lo' OR v <= hi'), the margins widen it
                    pts.append((para, hi - M_POINT, lo + M_POINT))
                elif lo is not None and hi is None:          # outside [lo, +inf) = v < lo
                    pts.append((para, -RG_INF, lo + M_POINT))
                elif hi is not None and lo is None:          # outside (-inf, hi] = v > hi
                    pts.append((para, hi - M_POINT, RG_INF))
            for k in kids.get(j + 1, ()):
                walk(k)
            return
        if t == "spiral" and _is_point(c):
            for k in kids.get(j + 1, ()):
                walk(k)
            return
        if t == "logic_or":
            need = max(1, _num(p.get("min_pass"), 1))
            ch = kids.get(j + 1, [])
            o = _compile_or([active[k] for k in ch], need, mc)
            if o is not None:
                ors.append(o)
            else:
                notes.append(f"#{j + 1} {c.get('label') or 'OR'}: left to the CPU")
            return
        if t in ("biome_sample", "climate_minmax"):
            # a box test on its own = an « at least 1 of 1 » OR
            o = _compile_or([c], 1, mc)
            if o is not None:
                ors.append(o)
            return
        # anything else: the CPU decides (the GPU just does not test it)

    for k in kids.get(si + 1, ()):
        walk(k)
    if not pts and not ors:
        return None, None, "nothing under the spiral that the GPU can test"
    s = RgSpec()
    pts.sort(key=lambda q: COST.get(q[0], 9))
    for i, (para, lo, hi) in enumerate(pts[:RG_MAXPT]):
        s.pt[i].para, s.pt[i].lo, s.pt[i].hi = para, lo, hi
    s.npt = min(len(pts), RG_MAXPT)
    # climate boxes first, then the biome boxes with the fewest climate boxes
    ors.sort(key=lambda o: (o.kind, o.nbb * o.nbox))
    for i, o in enumerate(ors[:RG_MAXOR]):
        s.ors[i] = o
    s.nor = min(len(ors), RG_MAXOR)
    x1, z1, x2, z2 = _box_of(sc)
    s.x1, s.z1, s.x2, s.z2 = x1, z1, x2, z2
    s.step = max(1, _num((sc.get("params") or {}).get("step"), 512))
    if int(sc.get("rmax", 0) or 0):
        notes.append("a round spiral is scanned as its square (the CPU keeps the disk)")
    return s, si + 1, notes


def _compile_or(children, need, mc):
    if not children or len(children) > RG_MAXBOX or need > len(children):
        return None
    o = RgOr()
    o.k, o.nbox, o.grid = need, len(children), GRID
    kinds = set()
    bbs = None
    for i, c in enumerate(children):
        t = c.get("type")
        p = c.get("params") or {}
        if t == "climate_minmax" and not _is_point(c) and not p.get("invert"):
            para = PARA.get(p.get("para"))
            nmax = _num(p.get("nmax"), -1)
            rng = p.get("range") or [None, None]
            lo, hi = _num(rng[0], None), _num(rng[1], None)
            loc = _num(p.get("locate"), 0)
            if para is None or nmax not in (-1, 0):
                return None
            if loc == 1 and hi is not None and lo is None:          # min <= hi : a point <= hi
                ok = (para, -RG_INF, hi + M_BOX)
            elif loc == 0 and lo is not None and hi is None:        # max >= lo : a point >= lo
                ok = (para, lo - M_BOX, RG_INF)
            else:
                return None
            if kinds and (0 not in kinds or (o.para, o.lo, o.hi) != ok):
                return None
            kinds.add(0)
            o.kind = 0
            o.para, o.lo, o.hi = ok
        elif t == "biome_sample" and not (p.get("biomes_excl") or []) \
                and float(p.get("coverage_pct", 0) or 0) > 0 and p.get("biomes_incl"):
            key = tuple(sorted(int(b) for b in p["biomes_incl"]))
            if kinds and (1 not in kinds or key != bbs[0]):
                return None
            if bbs is None:
                lst = _biome_bbs(mc, key)
                if not lst or len(lst) > RG_MAXBB:
                    return None
                bbs = (key, lst)
            kinds.add(1)
            o.kind = 1
        else:
            return None
        x1, z1, x2, z2 = _box_of(c)
        o.box[i].x1, o.box[i].z1, o.box[i].x2, o.box[i].z2 = x1, z1, x2, z2
    if o.kind == 1:
        for i, bb in enumerate(bbs[1]):
            o.bb[i] = bb
        o.nbb = len(bbs[1])
    return o


# ── the CPU build (tests) ─────────────────────────────────────────────────────
_HOST = None


def host_lib():
    global _HOST
    if _HOST is None:
        here = os.path.dirname(os.path.abspath(__file__))
        _HOST = ct.CDLL(os.path.join(here, "tests", "libringhost.so"))
        _HOST.rg_host_scan.restype = ct.c_longlong
        _HOST.rg_host_scan.argtypes = [ct.c_uint64, ct.POINTER(RgSpec),
                                       ct.POINTER(ct.c_int32), ct.c_longlong]
        assert _HOST.rg_sizeof_spec() == ct.sizeof(RgSpec), "RgSpec layout differs"
    return _HOST


def host_scan(spec, seed, cap=1 << 20):
    L = host_lib()
    out = np.empty(2 * cap, np.int32)
    n = L.rg_host_scan(ct.c_uint64(int(seed) & (2**64 - 1)), ct.byref(spec),
                       out.ctypes.data_as(ct.POINTER(ct.c_int32)), cap)
    return out[:2 * min(n, cap)].reshape(-1, 2)


# ── the GPU (noise_gpu library) ───────────────────────────────────────────────
def _gpu():
    try:
        import noise_gpu
        noise_gpu._load()
        L = noise_gpu._LIB if noise_gpu._AVAIL else None
    except Exception:
        return None
    if not L or not hasattr(L, "noise_gpu_ringscan"):
        return None
    f = L.noise_gpu_ringscan
    f.restype = ct.c_int
    f.argtypes = [ct.c_int, ct.POINTER(RgSpec), ct.POINTER(ct.c_uint64), ct.c_int,
                  ct.POINTER(ct.c_int32), ct.POINTER(ct.c_int32), ct.c_int,
                  ct.POINTER(ct.c_int)]
    if hasattr(L, "noise_gpu_ringscan_sizeof") and L.noise_gpu_ringscan_sizeof() != ct.sizeof(RgSpec):
        return None
    return f


def available():
    return _gpu() is not None


def scan(spec, seeds, mc=35, cap=1 << 20):
    """GPU: the positions of `seeds` (uint64) that may pass.
    -> (seed_index int32[n], xz int32[n, 2]); raises on a GPU error."""
    f = _gpu()
    if f is None:
        raise RuntimeError("the GPU spiral scan is not in noise_gpu (rebuild CUDA)")
    sd = np.ascontiguousarray(np.asarray(seeds, dtype=np.uint64))
    while True:
        xz = np.empty(2 * cap, np.int32); si = np.empty(cap, np.int32); n = ct.c_int(0)
        rc = f(int(mc), ct.byref(spec), sd.ctypes.data_as(ct.POINTER(ct.c_uint64)), len(sd),
               xz.ctypes.data_as(ct.POINTER(ct.c_int32)), si.ctypes.data_as(ct.POINTER(ct.c_int32)),
               cap, ct.byref(n))
        if rc != 0:
            raise RuntimeError(f"noise_gpu_ringscan failed (code {rc})")
        if n.value <= cap:
            return si[:n.value], xz[:2 * n.value].reshape(-1, 2)
        cap = n.value + 1024                     # too many: again with room for all
