"""
custom_search.py — Evaluation engine for Custom Sessions.

V1: Extended Python implementation covering all major condition types from
Cubiomes Viewer's search.cpp, including:
  - Spiral iterator (with radial support)
  - First stronghold (fast 48-bit)
  - Stronghold (full biome-aware ring iterator)
  - All overworld structures (village, monument, mansion, …)
  - Nether structures: fortress, bastion
  - End structures: end_city
  - Mineshaft (getMineshafts)
  - Slime chunks
  - Spawn position
  - Scale-to-Nether / Scale-to-Overworld coordinate converters
  - logic_OR  (any child passes → parent passes)
  - logic_NOT (all children fail → parent passes)
  - Climate extreme (min/max of a climate noise parameter)

Architecture:
  - A "session" is a list of conditions in dependency order.
  - Each condition has a `relative` field (1-based, 0 = world origin).
  - Evaluation is top-down; passing conditions record their resolved position.
  - A seed matches when ALL enabled conditions pass.
"""
from __future__ import annotations

import copy
import ctypes
import math
import os
import threading
import time


def _seed_arange(start, n):
    """int64 array of the n seeds from `start`, wrapping at 2^63 like the C
    engine (np.arange cannot cross INT64_MAX: OverflowError near the top of the
    seed space)."""
    import numpy as _np
    return (_np.arange(int(n), dtype=_np.uint64)
            + _np.uint64(int(start) & 0xFFFFFFFFFFFFFFFF)).view(_np.int64)

def _nl_dbg(msg):
    try:
        import native_libs as _n
        _n.dbg(msg)
    except Exception:
        pass
from typing import Callable, List, Optional, Tuple

from cubiomes_wrap import get_large_biomes
from cubiomes_wrap import (
    CubiomesLib, StrongholdIter, Pos,
    Fortress, Bastion, End_City, End_Gateway,
    Village, Monument, Mansion, Outpost, Swamp_Hut, Igloo,
    Desert_Pyramid, Jungle_Temple, Ruined_Portal, Ruined_Portal_N,
    Shipwreck, Treasure, Ancient_City, Trail_Ruins, Trial_Chambers,
    Abandoned_Camp,
    Mineshaft, Ocean_Ruin, Desert_Well,
)

# ─────────────────────────────────────────────────────────────────────────
# Structure type map  key → cubiomes StructureType int
# ─────────────────────────────────────────────────────────────────────────
STRUCT_TYPES = {
    # Overworld
    "village":          Village,
    "monument":         Monument,
    "mansion":          Mansion,
    "outpost":          Outpost,
    "hut":              Swamp_Hut,
    "igloo":            Igloo,
    "desert_pyramid":   Desert_Pyramid,
    "jungle_temple":    Jungle_Temple,
    "ruined_portal":    Ruined_Portal,
    "shipwreck":        Shipwreck,
    "treasure":         Treasure,
    "ancient_city":     Ancient_City,
    "trial_chambers":   Trial_Chambers,
    "trail_ruins":      Trail_Ruins,
    "abandoned_camp":   Abandoned_Camp,
    "ocean_ruin":       Ocean_Ruin,
    "desert_well":      Desert_Well,
    # Nether
    "fortress":         Fortress,
    "bastion":          Bastion,
    "ruined_portal_nether": Ruined_Portal_N,
    # End
    "end_city":         End_City,
    "end_gateway":      End_Gateway,
}

# Dimension for Nether / End structures
STRUCT_DIM = {
    "fortress": -1,
    "bastion":  -1,
    "ruined_portal_nether": -1,
    "end_city":  1,
    "end_gateway": 1,
}

_U64 = (1 << 64) - 1
_U48 = (1 << 48) - 1


# ─────────────────────────────────────────────────────────────────────────
# EvalContext
# ─────────────────────────────────────────────────────────────────────────

_PRECISE_WARNED = False   # print the first precise_y unavailability/error only once

# ── coverage / confidence (Wilson) — MUST mirror terrain_bridge.c bit-for-bit so
# the GPU/numpy reduction and the C CPU batch return identical verdicts. ─────────
_FLT_EPSILON = 1.1920928955078125e-07

def _inverf(x):
    """Inverse error function via Newton's method — identical to tb_inverf() in C."""
    import math
    t = x; dt = 1.0; it = 0
    while abs(dt) > _FLT_EPSILON and it < 100:
        dt = 0.5 * math.sqrt(math.pi) * (math.erf(t) - x) / math.exp(-t * t)
        t -= dt; it += 1
    return t

def _wilson_lo(nn, p, z):
    """Lower bound of the Wilson score interval — identical to tb_wilson() in C."""
    import math
    s = z * z / nn
    t = 1.0 / (1.0 + s)
    w = t * (p + 0.5 * s)
    d = t * z * math.sqrt((p * (1.0 - p) + 0.25 * s) / nn) + _FLT_EPSILON
    return w - d

def _coverage_pass(ncount, xcount, coverage, confidence):
    """Deterministic coverage verdict over a strided disk — mirrors tb_coverage_pass."""
    import math
    if ncount <= 0:      return False
    if xcount >= ncount: return True       # every sampled cell matches
    if xcount <= 0:      return False
    c = confidence
    if c >= 1.0: c = 0.999999
    if c < 0.0:  c = 0.0
    p = xcount / ncount
    z = math.sqrt(2.0) * _inverf(c)        # c=0 -> z=0 -> exact point estimate
    return _wilson_lo(ncount, p, z) >= coverage

def active_conditions(conditions: List[dict]) -> List[dict]:
    """Snapshot of the conditions that actually take part in a search.

    * returns DEEP COPIES: the UI may edit its list while a search runs;
    * a disabled condition disables its whole subtree (its dependent conditions
      would otherwise be evaluated around a meaningless position);
    * `relative` (1-based parent position) is renumbered for the kept list, so
      every evaluator (C engine, Python, GPU pre-filters) sees the same tree;
    * a parent reference that points nowhere (or into a cycle) is detached to
      the world origin, as before.
    """
    import copy as _copy
    n = len(conditions)
    state = [None] * n                      # None unknown, True kept, False dropped

    def _kept(i, depth=0):
        if state[i] is not None:
            return state[i]
        c = conditions[i]
        if not c.get("enabled", True):
            state[i] = False
            return False
        try:
            r = int(c.get("relative", 0) or 0)
        except (TypeError, ValueError):
            r = 0
        if r <= 0 or r > n or r == i + 1 or depth > n:
            state[i] = True                 # root (or invalid link → origin)
            return True
        state[i] = _kept(r - 1, depth + 1)
        return state[i]

    kept = [i for i in range(n) if _kept(i)]
    newpos = {i + 1: k + 1 for k, i in enumerate(kept)}
    out = []
    for i in kept:
        c = _copy.deepcopy(conditions[i])
        try:
            r = int(c.get("relative", 0) or 0)
        except (TypeError, ValueError):
            r = 0
        c["relative"] = newpos.get(r, 0) if r > 0 else 0
        c["enabled"] = True
        out.append(c)
    return out


def climate_locate(params: dict) -> int:
    """Locate mode of a climate_minmax / climate_center condition: 0 = max,
    1 = min. Reads the editor's `locate`, else an imported cubiomes-viewer
    `mode` bitmask (bit0 = locate min, bit1 = locate max)."""
    if "locate" in params and params.get("locate") is not None:
        try:
            return 1 if int(params.get("locate")) == 1 else 0
        except (TypeError, ValueError):
            return 0
    try:
        m = int(params.get("mode", 2))
    except (TypeError, ValueError):
        return 0
    if m & 1:
        return 1
    return 0


class EvalContext:
    def __init__(self, lib: CubiomesLib, mc_version: int, seed: int):
        self.lib = lib
        self.mc_version = mc_version
        self.seed = seed
        self.lib.setup_and_seed(mc=mc_version, seed=seed)
        self.positions: List[Optional[Tuple[int, int]]] = [(0, 0)]
        # Position du stronghold qui satisfait le filtre eye/maxY (utile en
        # scan_128 où ce n'est pas le premier) → mémorisée pour recentrer la
        # carte instantanément au clic, sans re-scanner les 128.
        self.match_pos: Optional[Tuple[int, int]] = None

    def get_parent_pos(self, parent_idx: int) -> Tuple[int, int]:
        if 0 <= parent_idx < len(self.positions):
            p = self.positions[parent_idx]
            if p is not None:
                return p
        return (0, 0)

    def push_position(self, idx: int, pos, y=None) -> None:
        while len(self.positions) <= idx:
            self.positions.append(None)
        prev = self.positions[idx]
        self.positions[idx] = pos if pos is not None else (0, 0)
        # (10/10 ter) the Y of a position (1.16 dungeon / lava lake, stronghold
        # end portal): its children's height link. The same place pushed again
        # without a Y (a parent re-wired at its own position) keeps it.
        ys = self.__dict__.setdefault("ys", {})
        if y is not None:
            ys[idx] = int(y)
        elif prev is None or tuple(prev) != tuple(self.positions[idx]):
            ys.pop(idx, None)

    def get_parent_y(self, parent_idx: int):
        return getattr(self, "ys", {}).get(parent_idx)


# ─────────────────────────────────────────────────────────────────────────
# Spiral position generator (matches search.cpp F_SPIRAL algorithm)
# ─────────────────────────────────────────────────────────────────────────

def _spiral_positions(x1, z1, x2, z2, step, rmax_sq=0, at_x=0, at_z=0):
    rx1 = x1 // step
    rx2 = x2 // step
    rz1 = z1 // step
    rz2 = z2 // step

    rx = (rx1 + rx2) // 2
    rz = (rz1 + rz2) // 2
    i = 0; dl = 1; dx = 1; dz = 0

    while True:
        inx = (rx1 <= rx <= rx2)
        inz = (rz1 <= rz <= rz2)
        if not inx and not inz:
            break
        if inx and inz:
            px = rx * step
            pz = rz * step
            inside = True
            if rmax_sq:
                ddx = px - at_x; ddz = pz - at_z
                inside = (ddx*ddx + ddz*ddz < rmax_sq)
            elif not (x1 <= px <= x2 and z1 <= pz <= z2):
                inside = False
            if inside:
                yield (px, pz)
        rx += dx; rz += dz; i += 1
        if i == dl:
            i = 0; dx, dz = -dz, dx
            if dz == 0:
                dl += 1


# ─────────────────────────────────────────────────────────────────────────
# Slime chunk check (pure Python, matches finders.h isSlimeChunk)
# ─────────────────────────────────────────────────────────────────────────

def feature116_yrange(params) -> tuple:
    """(y_min, y_max) of a dungeon / lava lake condition (full range 0…255)."""
    try:
        lo = int(params.get("y_min", 0) or 0)
    except (TypeError, ValueError):
        lo = 0
    try:
        hi = params.get("y_max", 255)
        hi = 255 if hi is None else int(hi)
    except (TypeError, ValueError):
        hi = 255
    return max(0, lo), min(255, hi)


def _is_slime_chunk(seed: int, cx: int, cz: int) -> bool:
    rnd = seed & _U64
    rnd = (rnd + ctypes.c_int(cx * 0x5ac0db).value) & _U64
    rnd = (rnd + ctypes.c_int(cx * cx * 0x4c1906).value) & _U64
    rnd = (rnd + ctypes.c_int(cz * 0x5f24f).value) & _U64
    rnd = (rnd + ctypes.c_int(cz * cz).value * 0x4307a7) & _U64
    rnd ^= 0x3ad8025f
    rnd &= _U64
    rnd = (rnd ^ 0x5DEECE66D) & _U48
    rnd = (rnd * 0x5DEECE66D + 0xB) & _U48
    bits = (rnd >> 17) & 0xFFFFFFFF
    return (bits % 10) == 0


# ─────────────────────────────────────────────────────────────────────────
# Single condition evaluator
# ─────────────────────────────────────────────────────────────────────────

def _cond_min_count(cond: dict) -> int:
    """Instances required (<= 0 = exclusion). Same rule as the C builder:
    the editor's `instances`, else the imported `params.count`."""
    try:
        if "instances" in cond:
            return int(cond.get("instances", 1))
        return int(cond.get("params", {}).get("count", 1))
    except (TypeError, ValueError):
        return 1


def _struct_collect(ctx, cond, at, maxn=1, stop_at=1, sums=False):
    """Python mirror of session_engine.c struct_collect (cubiomes-viewer
    testCondAt order and checks) for OVERWORLD structures.
    Returns (hits, positions[:maxn]) or (hits, positions, (sx, sz)) with sums;
    hits = -1 if the structure is unknown for this version."""
    t = cond.get("type", "")
    stype = STRUCT_TYPES[t]
    px, pz = at
    x1, z1, x2, z2, rmax_sq = _get_box(cond, px, pz)
    lib = ctx.lib
    L = lib._lib
    rb = lib.region_blocks(stype, ctx.mc_version)
    if rb <= 0:
        return (-1, [], (0, 0)) if sums else (-1, [])
    skip_ref = bool(cond.get("ignore_ref"))
    variant = cond.get("params", {}).get("variant", "any") or "any"
    mc = ctx.mc_version
    seed_u = ctypes.c_uint64(int(ctx.seed) & _U64)
    pos = Pos()
    hits, out, sx, sz = 0, [], 0, 0
    for rz in range(z1 // rb, z2 // rb + 1):
        for rx in range(x1 // rb, x2 // rb + 1):
            if not L.getStructurePos(stype, mc, seed_u, rx, rz, ctypes.byref(pos)):
                continue
            bx, bz = pos.x, pos.z
            if skip_ref and bx == px and bz == pz:
                continue
            if rmax_sq:
                if (bx - px) ** 2 + (bz - pz) ** 2 >= rmax_sq:
                    continue
            elif not (x1 <= bx <= x2 and z1 <= bz <= z2):
                continue
            biome = L.isViableStructurePos(stype, lib._gen, bx, bz, 0)
            if not biome:
                continue
            if variant != "any":
                if variant in ("abandoned", "not_abandoned"):
                    ab = lib.get_variant_abandoned(stype, bx, bz, int(biome))
                    if ab != (variant == "abandoned"):
                        continue
                elif variant in ("basement", "no_basement"):
                    bs = lib.get_variant_basement(stype, bx, bz)
                    if bs != (variant == "basement"):
                        continue
            if mc >= 22 and not L.isViableStructureTerrain(stype, lib._gen, bx, bz):
                continue                                   # MC_1_18 (1.18.2) = 22
            if len(out) < maxn:
                out.append((bx, bz))
            sx += bx; sz += bz
            hits += 1
            if stop_at > 0 and hits >= stop_at:
                return (hits, out, (sx, sz)) if sums else (hits, out)
    return (hits, out, (sx, sz)) if sums else (hits, out)


_STRUCT_SPLIT_MAX = 64


def _get_box(cond: dict, px: int, pz: int):
    """Returns (x1, z1, x2, z2, rmax_sq)."""
    rmax_b = cond.get("rmax", 0)
    # backward-compat: conditions saved before rmax existed only have "radial"
    if not rmax_b and cond.get("area_mode") == 0 and int(cond.get("radial", 0) or 0) > 0:
        rmax_b = int(cond.get("radial", 0)) + 1
    if rmax_b and rmax_b > 0:
        rb = rmax_b - 1
        return (px - rb, pz - rb, px + rb, pz + rb, rb * rb + 1)
    return (
        px + cond.get("x1", 0), pz + cond.get("z1", 0),
        px + cond.get("x2", 0), pz + cond.get("z2", 0),
        0,
    )


class _RangeSeedList:
    """Expose une PLAGE [start, start+count) (ou illimitée si count<=0) comme
    seed_list lazy. Sert à router un scan_128 de plage vers le moteur NATIF 128
    de search_end_portal_seeds (cb_strongholds128_arr + seedlist_eyes_prefilter_arr)
    → vitesse C, au lieu du per-seed Python lent. count>0 : __len__ donne le total
    (barre de %). Illimité : __len__ lève TypeError (affichage sps seul)."""
    __slots__ = ("start", "count", "stop")

    def __init__(self, start, count, stop_event=None):
        self.start = int(start)
        self.count = int(count)
        self.stop = stop_event

    def __len__(self):
        if self.count > 0:
            return self.count
        raise TypeError("range illimité")

    def __iter__(self):
        s = self.start
        end = self.start + self.count if self.count > 0 else (self.start + (1 << 48))
        st = self.stop
        while s < end:
            if st is not None and st.is_set():
                break
            yield ((s + (1 << 63)) % (1 << 64)) - (1 << 63)   # signed 64-bit, wraps at 2^63
            s += 1


class _SisterList:
    """A seed list searched in "48-bit family" mode (08/10): every listed seed
    stands for its whole family — the 65 536 seeds sharing its lower 48 bits
    (its sister seeds). Lazy (len / iter): the families are expanded on the fly.
    Several listed seeds of one family are searched once."""
    __slots__ = ("lows", "stop")
    M48 = (1 << 48) - 1

    def __init__(self, seeds, stop_event=None):
        self.lows = list(dict.fromkeys(int(x) & self.M48 for x in seeds))
        self.stop = stop_event

    def __len__(self):
        return len(self.lows) << 16

    def __iter__(self):
        st = self.stop
        for low in self.lows:
            for hi in range(1 << 16):
                if (hi & 1023) == 0 and st is not None and st.is_set():
                    return
                v = low | (hi << 48)
                yield v - (1 << 64) if v >= (1 << 63) else v


def _evaluate_sisters(conditions, sl, mc_version, num_workers, stop_event,
                      on_match, on_progress):
    """The families of a _SisterList through the C engine: the 48-bit structure
    position pre-filter (ssa_test_lows48, as the Blocks engine) skips the
    families that cannot match, then all 65 536 seeds of the others are fully
    tested on every thread. Progress in families. False → not expressible in C."""
    import ctypes as ct
    import numpy as _np
    raw = CubiomesLib()._lib
    res = _build_ssacond_array(conditions, raw)
    if res is None or not hasattr(raw, "ssa_test_seeds_batch"):
        return False
    arr, n_conds = res
    raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                                         ct.POINTER(ct.c_int64), ct.c_int64,
                                         ct.POINTER(ct.c_int64), ct.c_int64]
    raw.ssa_test_seeds_batch.restype = ct.c_int64
    pre = getattr(raw, "ssa_test_lows48", None)
    if pre is not None:
        pre.argtypes = raw.ssa_test_seeds_batch.argtypes
        pre.restype = ct.c_int64
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    stop = stop_event if stop_event is not None else threading.Event()
    emit = on_match or (lambda *_: None)
    highs = _np.arange(1 << 16, dtype=_np.uint64) << _np.uint64(48)
    lows = sl.lows
    total = len(lows)
    CH = 4096
    for i in range(0, total, CH):
        if stop.is_set():
            break
        sub = _np.array(lows[i:i + CH], dtype=_np.int64)
        n = len(sub)
        if pre is not None:
            out = _np.empty(n, _np.int64)
            k = int(pre(arr, n_conds, int(mc_version), large,
                        sub.ctypes.data_as(ct.POINTER(ct.c_int64)), n,
                        out.ctypes.data_as(ct.POINTER(ct.c_int64)), n))
            keep = set(out[:k].tolist())
        else:
            keep = None
        for j, low in enumerate(sub.tolist()):
            if stop.is_set():
                break
            if keep is None or low in keep:
                fam = (highs | _np.uint64(low)).view(_np.int64)
                _parallel_validate(raw, arr, n_conds, mc_version, large, fam,
                                   num_workers, stop, emit)
                if on_progress and not stop.is_set():
                    on_progress(i + j + 1, total)
        if on_progress and not stop.is_set():
            on_progress(i + n, total)
    return True


def _exy_c_view(enabled):
    """The C engine's part of a session whose Exact Y conditions are LINKED to
    another condition (08/10): the tree without them (and without what is linked
    to them). It accepts a superset of the seeds — as long as no Exact Y sits under
    an OR / NOT — so every seed it emits is then checked with the whole tree.
    Before, such a session ran in the single-threaded Python loop: one core busy,
    no statistics, no Pause. None → not possible (Python search)."""
    n = len(enabled)
    par = [int(c.get("relative", 0) or 0) for c in enabled]
    drop = set()
    for i, c in enumerate(enabled):
        if c.get("type") != "precise_y":
            continue
        p, hops = par[i], 0
        while p and hops <= n:
            if not 1 <= p <= n or enabled[p - 1].get("type") in ("logic_or", "logic_not"):
                return None
            p, hops = par[p - 1], hops + 1
        drop.add(i)
    if not drop:
        return None
    grew = True
    while grew:
        grew = False
        for i in range(n):
            if i not in drop and par[i] and (par[i] - 1) in drop:
                drop.add(i); grew = True
    keep = [i for i in range(n) if i not in drop]
    if not keep:
        return None
    new = {old + 1: k + 1 for k, old in enumerate(keep)}
    out = []
    for i in keep:
        d = copy.deepcopy(enabled[i])
        d["relative"] = new.get(par[i], 0)
        out.append(d)
    return out


def _exy_view_rows(enabled):
    """For _exy_c_view(enabled): the session row of each row of the view."""
    n = len(enabled)
    par = [int(c.get("relative", 0) or 0) for c in enabled]
    drop = {i for i, c in enumerate(enabled) if c.get("type") == "precise_y"}
    grew = True
    while grew:
        grew = False
        for i in range(n):
            if i not in drop and par[i] and (par[i] - 1) in drop:
                drop.add(i); grew = True
    return [i for i in range(n) if i not in drop]


_TICKS_HZ = []


def _ticks_hz():
    """Ticks per second of the engine's statistics clock (rdtsc), or 0."""
    if _TICKS_HZ:
        return _TICKS_HZ[0]
    hz = 0.0
    try:
        f = CubiomesLib()._lib.ssa_ticks_now
        f.restype = ctypes.c_uint64; f.argtypes = []
        t0, k0 = time.perf_counter(), f()
        time.sleep(0.05)
        t1, k1 = time.perf_counter(), f()
        if k1 > k0 and t1 > t0:
            hz = (k1 - k0) / (t1 - t0)
    except Exception:
        hz = 0.0
    _TICKS_HZ.append(hz)
    return hz


def _inc_count(seed_count, search_mode):
    """A seed-by-seed engine run for a "48-bit family" session: its Count is in
    families → in seeds (as the C engine does when it adapts fam48 to inc)."""
    if search_mode == "fam48" and seed_count and seed_count > 0:
        return int(seed_count) * 65536
    return seed_count


# ── "End portal" position of the stronghold condition (03/10) ────────────────
# sh_anchor = "portal": the condition's position (what linked conditions are
# relative to, the result location / TP) is the centre of the end portal of the
# matched stronghold instead of the stronghold's ring point.
PORTAL_MARGIN = 256          # ring point → portal centre: ≤ ~245 blocks (snap 112 + pieces)


def _portal_anchor(params: dict) -> bool:
    return str((params or {}).get("sh_anchor", "")) == "portal"


def _portal_xz(seed, mc, sx, sz):
    """Centre (x, z) of the end portal of the stronghold starting at block (sx, sz)."""
    try:
        import ruined_portal as _rp
        r = _rp._frames_xz_native(int(seed), int(sx) >> 4, int(sz) >> 4, int(mc) < 22)
        if r is not None:
            return (int(r[0]), int(r[1]))
    except Exception:
        pass
    try:
        import stronghold_eyes as _se
        fr = _se.get_end_portal_frames(int(seed), int(sx), int(sz), int(mc))
        if fr is not None:
            return (int(fr[1]), int(fr[2]))
    except Exception:
        pass
    return None


def _loc_text(params, seed, mc, sx, sz):
    """A result's "x,z": the stronghold start, or its end portal centre (End portal mode)."""
    if _portal_anchor(params):
        pp = _portal_xz(seed, mc, sx, sz)
        if pp is not None:
            return f"{pp[0]},{pp[1]}"
    return f"{int(sx)},{int(sz)}"


def _portal_link_children(conditions) -> bool:
    """A stronghold condition in End portal mode has linked (child) conditions."""
    for i, c in enumerate(conditions):
        if (c.get("enabled", True) and c.get("type") == "first_stronghold"
                and _portal_anchor(c.get("params")) and any(
                    cc.get("enabled", True) and cc.get("relative", 0) == i + 1
                    for cc in conditions)):
            return True
    return False


def _portal_c_view(conditions):
    """What the C engine / GPU pre-filters see for a session whose stronghold is
    in End portal mode: they place the stronghold at its ring point, so each
    linked condition is widened by PORTAL_MARGIN (a structure that is near the
    portal is then always inside: a necessary test), or — when widening could
    lose results (NOT, counts of 0, inverted tests, deeper links) — left to the
    exact Python check of every candidate (_joint_seed_eval on the real session)."""
    import copy as _copy
    out = [_copy.deepcopy(c) for c in conditions]
    drop = set()

    def kids(i):
        return [j for j, c in enumerate(out) if c.get("relative", 0) == i + 1]

    def subtree(i):
        st, res = [i], set()
        while st:
            k = st.pop()
            res.add(k)
            st.extend(kids(k))
        return res
    for i, c in enumerate(out):
        if not (c.get("enabled", True) and c.get("type") == "first_stronghold"
                and _portal_anchor(c.get("params"))):
            continue
        multi = _scan_128(c.get("params") or {})   # first ring / 128: no ring point in C
        for j in kids(i):
            ch = out[j]
            p = ch.get("params") or {}
            safe = (not multi and ch.get("type") in STRUCT_TYPES and ch.get("type") not in STRUCT_DIM
                    and _cond_min_count(ch) > 0 and not p.get("invert") and not kids(j))
            if not safe:
                drop |= subtree(j)
                continue
            m = PORTAL_MARGIN
            rb = ch.get("rmax", 0) or 0
            if not rb and ch.get("area_mode") == 0 and int(ch.get("radial", 0) or 0) > 0:
                rb = int(ch.get("radial", 0)) + 1
            if rb and rb > 0:
                ch["rmax"] = int(rb) + m
            else:
                for k, d in (("x1", -m), ("z1", -m), ("x2", m), ("z2", m)):
                    ch[k] = int(ch.get(k, 0) or 0) + d
    if not drop:
        return out
    keep = [i for i in range(len(out)) if i not in drop]
    new_idx = {old + 1: n + 1 for n, old in enumerate(keep)}
    res = []
    for old in keep:
        c = out[old]
        r = c.get("relative", 0)
        if r:
            c["relative"] = new_idx.get(r, 0)
        res.append(c)
    return res


def _sh_count(params: dict) -> int:
    """How many strongholds the condition looks at (03/10): 1 (the first),
    3 (the first ring: sh_scope "Ring 3") or 128 (sh_scope "All 128")."""
    sc = params.get("sh_scope")
    if sc is not None:
        sc = str(sc)
        return 128 if sc == "All 128" else 3 if sc == "Ring 3" else 1
    return 128 if params.get("scan_128") else 1


def _scan_val(params: dict):
    """The `scan_128` argument of the engines: False, 3 or True (128)."""
    n = _sh_count(params)
    return True if n >= 128 else (3 if n == 3 else False)


def _scan_128(params: dict) -> bool:
    """True si on scanne LES 128 strongholds (au lieu du premier). Nouveau param
    UI = choix `sh_scope` (« First ring » / « All 128 ») ; rétro-compat avec
    l'ancien bool `scan_128` des sessions sauvegardées."""
    return _sh_count(params) > 1        # several strongholds (first ring or all 128)


def _eye_filter_on(params: dict) -> bool:
    """Le filtre yeux est actif si min_eyes >= 1 (0 = off). L'ancien flag
    `eye_enabled` reste supporté pour rétro-compat des sessions sauvegardées."""
    try:
        _me = int(params.get("min_eyes", 0) or 0)
    except (TypeError, ValueError):
        _me = 0
    return bool(params.get("eye_enabled")) or _me >= 1


def _stronghold_extra_enabled(params: dict) -> bool:
    return bool(_eye_filter_on(params) or params.get("maxy_enabled")
                or params.get("corrupt_enabled"))


def _stronghold_extra_ok(ctx: "EvalContext", params: dict, bx: int, bz: int) -> bool:
    """Sous-filtres eye-count / max-Y / corrupted d'une position stronghold
    (bx,bz), PAR-SEED, réutilisant EXACTEMENT stronghold_eyes /
    ruined_portal (donc identique à l'onglet Stronghold). Renvoie True si tous
    les sous-filtres ACTIVÉS passent (ou si aucun n'est activé)."""
    need_eye = _eye_filter_on(params)
    need_maxy = bool(params.get("maxy_enabled"))
    need_corrupt = bool(params.get("corrupt_enabled"))
    if not (need_eye or need_maxy or need_corrupt):
        return True
    seed = int(ctx.seed)
    mc = int(ctx.mc_version)
    if need_eye or need_maxy:
        try:
            from stronghold_eyes import get_stronghold_eyes_and_maxy
            # « Top Y » of the stronghold or of its portal room (03/10)
            eyes, max_y = get_stronghold_eyes_and_maxy(
                seed, int(bx), int(bz), mc, what=1 if params.get("maxy_target") == "room" else 0)
        except Exception:
            return False
        if need_eye:
            min_eyes = int(params.get("min_eyes", 12))
            if params.get("exact_eyes"):
                if int(eyes) != min_eyes:
                    return False
            elif int(eyes) < min_eyes:
                return False
        if need_maxy:
            if max_y is None or int(max_y) < int(params.get("min_max_y", 60)):
                return False
    if need_corrupt:
        # Broken end portal (03/10): frames predicted broken by a ruined portal
        # (footprint, 1.13-1.17) and / or a lake (1.16, experimental), ≥ min_frames
        srcs = params.get("broken_sources") or ["ruined"]
        need = max(1, int(params.get("min_frames", 1) or 1))
        n = 0
        if "ruined" in srcs and mc <= 21:
            try:
                import ruined_portal as _RP
                r = _RP.corrupt_check_at(seed, int(bx), int(bz), mc)
            except Exception:
                r = None
            if r:
                n = max(n, int(r.get("corruptible", 0) or 0))
        kinds = tuple(k for k in ("lava", "water") if k in srcs)
        if kinds and n < need:
            try:
                import lake_portals as _LP
                n = max(n, _LP.lake_frames_for(seed, int(bx), int(bz), mc, kinds))
            except Exception:
                pass
        if n < need:
            return False
    return True


def _stronghold_session_ok(lib, mc_version: int, seed: int, sh_conds: list) -> bool:
    """Post-filtre : re-évalue en Python UNIQUEMENT les conditions stronghold
    (existence + eye/maxY/corrupted) sur un candidat déjà validé par le moteur
    C/GPU pour les AUTRES conditions. Ne re-teste PAS les biomes → aucun risque
    de rejeter un candidat GPU-valide. Le moteur C ne connaît pas ces params
    Python, d'où ce filtre a posteriori."""
    try:
        ctx = EvalContext(lib, mc_version, int(seed))
        for cond in sh_conds:
            if not _evaluate_condition(ctx, cond, 1, sh_conds):
                return False, None
        return True, getattr(ctx, "match_pos", None)
    except Exception:
        return False, None


def _stronghold_match_detail(lib, mc_version: int, seed: int, sh_conds: list) -> str:
    """Pour un candidat DÉJÀ validé : renvoie une courte description du/des
    stronghold(s) qui satisfont le filtre eye/maxY. Utile surtout en mode
    scan_128 où le stronghold satisfaisant N'EST PAS le premier — l'UI affiche
    ainsi ses coords réelles pour que l'utilisateur puisse le localiser.
    Ne coûte que par RÉSULTAT (rare)."""
    try:
        from stronghold_eyes import get_stronghold_eyes_and_maxy as _ge
    except Exception:
        return ""
    try:
        ctx = EvalContext(lib, mc_version, int(seed))
    except Exception:
        return ""
    parts = []
    for cond in sh_conds:
        params = cond.get("params", {})
        if not _stronghold_extra_enabled(params):
            continue
        try:
            if _scan_128(params):
                shs = ctx.lib.find_strongholds(max_count=_sh_count(params))
            else:
                shs = ctx.lib.find_strongholds(max_count=1, radius_blocks=None)
        except Exception:
            shs = []
        for bx, bz in (shs or []):
            if _stronghold_extra_ok(ctx, params, bx, bz):
                try:
                    _ey, _my = _ge(int(seed), int(bx), int(bz), mc_version)
                except Exception:
                    _ey = "?"
                parts.append(f"{_ey} eyes @ ({int(bx)}, {int(bz)})")
                break
    return "  ·  ".join(parts)


def dy_link(cond) -> int:
    """(10/10 ter) The height link of a condition to its parent: the max |ΔY|
    in blocks (0 = the same Y), None = no link. Only the 1.16 dungeons / lava
    lakes have one, and it only acts below a parent with a Y (see y_tracking)."""
    if cond.get("type") not in Y_CHILD_TYPES or not cond.get("dy_on"):
        return None
    try:
        return max(0, int(cond.get("dy_max", 0) or 0))
    except (TypeError, ValueError):
        return None


# the conditions whose position has a Y, and the ones that can be height-linked
Y_CHILD_TYPES = ("dungeon", "lava_lake")


def y_tracking(cond) -> bool:
    """(10/10 ter) Does this condition's position carry a Y for its children:
    1.16 dungeon (spawner Y), lava lake (top lava Y), stronghold in End portal
    mode (the portal frames' Y)?"""
    t = (cond or {}).get("type")
    if t in Y_CHILD_TYPES:
        return True
    return t == "first_stronghold" and _portal_anchor((cond or {}).get("params"))


def _f116_instances(ctx, cond, at, parent_y):
    """(10/10 ter) The real 1.16 dungeons / lava lakes of a condition around
    `at`, kept as the C engine keeps them (area, Y range, lake kind, « ignore
    the reference », height link to parent_y), in its scan order. None when
    the library has no exact terrain."""
    import feature116 as f116
    if int(ctx.mc_version) not in f116.VERSIONS:
        return []
    if not f116.has_exact():
        return None
    t = cond.get("type")
    p = cond.get("params", {}) or {}
    x1, z1, x2, z2, rmax_sq = _get_box(cond, int(at[0]), int(at[1]))
    dun, lak = f116.features_exact(ctx.mc_version, ctx.seed, x1, z1, x2, z2)
    feats = dun if t == "dungeon" else lak
    lo, hi = feature116_yrange(p)
    ylo = lo if lo > 0 else -10 ** 9
    yhi = hi if hi < 255 else 10 ** 9
    var = p.get("variant", "any") if t == "lava_lake" else "any"
    dy = dy_link(cond)
    out = []
    for f in feats:
        if rmax_sq and (f.x - at[0]) ** 2 + (f.z - at[1]) ** 2 >= rmax_sq:
            continue
        if (cond.get("ignore_ref") and f.x == at[0] and f.z == at[1]
                and (parent_y is None or f.y == int(parent_y))):
            continue          # the parent itself (same Y: not a stacked one, 10/10)
        if f.kind == 1 and (var == "underground" and f.surface or var == "surface" and not f.surface):
            continue
        if not (ylo <= f.y <= yhi):
            continue
        if dy is not None and parent_y is not None and abs(f.y - int(parent_y)) > dy:
            continue
        out.append(f)
    # the C engine's order: chunk rows (z, then x), then the chunk's own order
    out.sort(key=lambda f: ((f.oz >> 4), (f.ox >> 4)) if f.kind == 1 else ((f.z >> 4), (f.x >> 4)))
    return out


def _c_eval_single_y(ctx, cond, parent_idx, px, pz):
    """(ok, x, z, y | None) of a 1.16 dungeon / lava lake condition at the
    parent position, with the parent's Y for the height link — None when the
    library is older (no ssa_eval_single_y)."""
    import ctypes as ct
    raw = ctx.lib._lib
    f = getattr(raw, "ssa_eval_single_y", None)
    if f is None:
        return None
    one = copy.deepcopy(cond)
    one["relative"] = 0
    one["enabled"] = True
    res = _build_ssacond_array([one], raw)
    if res is None:
        return None
    arr, _n = res
    f.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int64, ct.c_int32, ct.c_int32,
                  ct.c_int, ct.c_int32, ct.POINTER(ct.c_int32), ct.POINTER(ct.c_int32)]
    f.restype = ct.c_int
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    py = ctx.get_parent_y(parent_idx) if hasattr(ctx, "get_parent_y") else None
    out = (ct.c_int32 * 2)()
    oy = (ct.c_int32 * 2)()
    s64 = ((int(ctx.seed) + (1 << 63)) % (1 << 64)) - (1 << 63)
    ok = f(arr, int(ctx.mc_version), large, s64, int(px), int(pz),
           1 if py is not None else 0, int(py or 0), out, oy)
    return bool(ok), int(out[0]), int(out[1]), (int(oy[1]) if oy[0] else None)


def _c_eval_single(ctx, cond, cond_idx, all_conds, px, pz, want_samples=False):
    """One condition evaluated by the C engine at the parent position (px, pz)
    for ctx.seed: (passed, x, z), or None when the DLL lacks ssa_eval_single."""
    import ctypes as ct
    raw = ctx.lib._lib
    f = getattr(raw, "ssa_eval_single", None)
    if f is None:
        return None
    one = copy.deepcopy(cond)
    one["relative"] = 0
    one["enabled"] = True
    try:                              # cubiomes-viewer reads full octaves here (E41)
        # all_conds = the evaluated (active) list; cond_idx is 1-based in it
        if (cond_idx - 1) in cv_full_octave_conditions(all_conds):
            one.setdefault("params", {})["nmax"] = -1
    except Exception:
        pass
    res = _build_ssacond_array([one], raw)
    if res is None:
        return None
    arr, _n = res
    f.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int64, ct.c_int32, ct.c_int32,
                  ct.POINTER(ct.c_int32), ct.POINTER(ct.c_int32), ct.c_int,
                  ct.POINTER(ct.c_int)]
    f.restype = ct.c_int
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    out = (ct.c_int32 * 2)()
    s64 = ((int(ctx.seed) + (1 << 63)) % (1 << 64)) - (1 << 63)
    cap = 4096 if want_samples else 0
    smp = (ct.c_int32 * (2 * max(1, cap)))()
    ns = ct.c_int(0)
    ok = f(arr, int(ctx.mc_version), large, s64, int(px), int(pz), out,
           smp if cap else None, cap, ct.byref(ns))
    if want_samples:
        return bool(ok), int(out[0]), int(out[1]), [(int(smp[2 * k]), int(smp[2 * k + 1]))
                                                    for k in range(ns.value)]
    return bool(ok), int(out[0]), int(out[1])


def _evaluate_condition(ctx: EvalContext, cond: dict, cond_idx: int,
                         all_conds: List[dict]) -> bool:
    t = cond.get("type", "")
    params = cond.get("params", {})
    parent_idx = cond.get("relative", 0)
    px, pz = ctx.get_parent_pos(parent_idx)
    x1, z1, x2, z2, rmax_sq = _get_box(cond, px, pz)

    # ── Coordinate converters ─────────────────────────────────────────
    if t == "scale_to_nether":
        ctx.push_position(cond_idx, (px // 8, pz // 8))
        return True

    if t == "scale_to_overworld":
        ctx.push_position(cond_idx, (px * 8, pz * 8))
        return True

    # ── Spiral (handled by _eval_from, but record center if reached here) ─
    if t == "spiral":
        ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
        return True

    # ── Logic gates ───────────────────────────────────────────────────
    if t == "logic_or":
        children = [(i + 1) for i, c in enumerate(all_conds)
                    if c.get("enabled", True) and c.get("relative", 0) == cond_idx]
        if not children:
            ctx.push_position(cond_idx, (px, pz))
            return True
        # each alternative is a full subtree evaluated at this node's position
        # (as in C and cubiomes-viewer); before, the children were read around
        # a stale position and their own dependent conditions were skipped.
        ctx.push_position(cond_idx, (px, pz))
        for child_idx in children:
            if _eval_node(ctx, all_conds, child_idx, (px, pz)):
                ctx.push_position(cond_idx, (px, pz))
                return True
        return False

    if t == "logic_not":
        children = [(i + 1) for i, c in enumerate(all_conds)
                    if c.get("enabled", True) and c.get("relative", 0) == cond_idx]
        if not children:
            return False
        ctx.push_position(cond_idx, (px, pz))
        for child_idx in children:
            if _eval_node(ctx, all_conds, child_idx, (px, pz)):
                return False
        ctx.push_position(cond_idx, (px, pz))
        return True

    # ── Spawn ─────────────────────────────────────────────────────────
    if t == "spawn":
        # exact spawn (getSpawn), as the C engine and cubiomes-viewer — the
        # map's get_spawn() uses the fast estimate, which can differ.
        try:
            with ctx.lib._lock:
                _p = ctx.lib._lib.getSpawn(ctx.lib._gen)
            sp = (_p.x, _p.z)
        except Exception:
            sp = ctx.lib.get_spawn()
        if sp is None:
            return False
        sx, sz = sp
        if rmax_sq:
            if (sx - px)**2 + (sz - pz)**2 >= rmax_sq:
                return False
        elif not (x1 <= sx <= x2 and z1 <= sz <= z2):
            return False
        ctx.push_position(cond_idx, (sx, sz))
        return True

    # ── First stronghold ──────────────────────────────────────────────
    if t == "first_stronghold":
        it = StrongholdIter()
        pos = ctx.lib._lib.initFirstStronghold(
            ctypes.byref(it), ctx.mc_version,
            ctypes.c_uint64(ctx.seed & _U64))
        if rmax_sq:
            if (pos.x - px)**2 + (pos.z - pz)**2 >= rmax_sq:
                return False
        elif not (x1 <= pos.x <= x2 and z1 <= pos.z <= z2):
            return False
        # Sous-filtres eye/maxY/corrupted : ils DOIVENT être comptés à la
        # position RÉELLE du stronghold — biome-snappée via find_strongholds —
        # PAS à la position idéale d'initFirstStronghold (pré-snap). Sinon les
        # yeux sont comptés au mauvais chunk (ex. 5 yeux à la pos idéale vs 1 œil
        # à la pos réelle). L'onglet Stronghold + le chemin single-condition
        # validé utilisent find_strongholds : on fait pareil pour être exact.
        if _stronghold_extra_enabled(params):
            if _scan_128(params):
                # « Check all 128 strongholds » : on passe si AU MOINS un des 128
                # strongholds satisfait le filtre yeux/maxY/corrupted (≈128x plus
                # lourd/seed, mais par CANDIDAT donc rare). Positions réelles.
                try:
                    _shs = ctx.lib.find_strongholds(max_count=_sh_count(params))
                except Exception:
                    _shs = []
                if not _shs:
                    return False
                _hit = None
                for bx, bz in _shs:
                    if _stronghold_extra_ok(ctx, params, bx, bz):
                        _hit = (int(bx), int(bz))
                        break
                if _hit is None:
                    return False
                ctx.match_pos = _hit          # stronghold satisfaisant (≠ premier)
            else:
                try:
                    _sh1 = ctx.lib.find_strongholds(max_count=1, radius_blocks=None)
                    _ex, _ez = _sh1[0] if _sh1 else (pos.x, pos.z)
                except Exception:
                    _ex, _ez = pos.x, pos.z
                if not _stronghold_extra_ok(ctx, params, _ex, _ez):
                    return False
                ctx.match_pos = (int(_ex), int(_ez))
        else:
            ctx.match_pos = (int(pos.x), int(pos.z))
        if _portal_anchor(params):
            # End portal mode: the position is the portal centre of the matched
            # stronghold (the first one, snapped, without a filter)
            base = ctx.match_pos if _stronghold_extra_enabled(params) else None
            if base is None:
                try:
                    _sh1 = ctx.lib.find_strongholds(max_count=1, radius_blocks=None)
                    base = _sh1[0] if _sh1 else None
                except Exception:
                    base = None
            pp = _portal_xz(ctx.seed, ctx.mc_version, *base) if base else None
            if pp is None:
                return False
            ctx.match_pos = pp
            # (10/10 ter) the portal's Y, when a child is height-linked to it
            py = None
            if any(cc.get("relative", 0) == cond_idx and dy_link(cc) is not None for cc in all_conds):
                try:
                    import stronghold_eyes as _se
                    fr = _se.get_end_portal_frames(int(ctx.seed), int(base[0]), int(base[1]),
                                                   int(ctx.mc_version))
                    py = int(fr[3]) if fr is not None else None
                except Exception:
                    py = None
            ctx.push_position(cond_idx, pp, py)
            return True
        ctx.push_position(cond_idx, (pos.x, pos.z))
        return True

    # ── Stronghold (full ring iterator) ──────────────────────────────
    if t == "stronghold":
        min_count = params.get("count", 1)
        exclude = (min_count <= 0)
        sh = ctx.lib.find_strongholds(max_count=128)
        hits = []
        for bx, bz in sh:
            if rmax_sq:
                if (bx - px)**2 + (bz - pz)**2 < rmax_sq:
                    hits.append((bx, bz))
            elif x1 <= bx <= x2 and z1 <= bz <= z2:
                hits.append((bx, bz))
        if exclude:
            if hits:
                return False
            ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
            return True
        # Sous-filtres eye/maxY/corrupted : ne garde que les strongholds qui
        # passent (par-seed). Requiert au moins `min_count` strongholds valides.
        if _stronghold_extra_enabled(params):
            hits = [(bx, bz) for (bx, bz) in hits
                    if _stronghold_extra_ok(ctx, params, bx, bz)]
        if len(hits) < min_count:
            return False
        ctx.push_position(cond_idx, hits[0])
        return True

    # ── (09/10) 1.16 fallback-point feature: the C engine's prediction ─
    # (10/10) and the 1.16 dungeons / lava lakes of an area
    if t in ("dungeon", "lava_lake"):
        # (10/10 ter) with the parent's Y (height link) and its own Y back
        r = _c_eval_single_y(ctx, cond, parent_idx, px, pz)
        if r is None:
            r = _c_eval_single(ctx, cond, cond_idx, all_conds, px, pz)
            r = (r[0], r[1], r[2], None) if r else None
        if not r or not r[0]:
            return False
        ctx.push_position(cond_idx, (r[1], r[2]), r[3])
        return True
    if t == "spawn_feature116":
        r = _c_eval_single(ctx, cond, cond_idx, all_conds, px, pz)
        if not r or not r[0]:
            return False
        ctx.push_position(cond_idx, (r[1], r[2]))
        return True

    # ── Slime chunks ──────────────────────────────────────────────────
    if t == "slime":
        min_count = params.get("count", 1)
        exclude = (min_count <= 0)
        seed = ctx.seed
        cx1 = x1 >> 4; cz1 = z1 >> 4
        cx2 = x2 >> 4; cz2 = z2 >> 4
        hits = []
        for cz in range(cz1, cz2 + 1):
            for cxc in range(cx1, cx2 + 1):
                if not _is_slime_chunk(seed, cxc, cz):
                    continue
                bx = cxc << 4; bz = cz << 4
                if rmax_sq and (bx - px)**2 + (bz - pz)**2 >= rmax_sq:
                    continue
                hits.append((bx, bz))
                if not exclude and len(hits) >= min_count:
                    break
            else:
                continue
            break
        if exclude:
            if hits:
                return False
            ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
            return True
        if len(hits) < min_count:
            return False
        avg_x = sum(h[0] for h in hits) // len(hits)
        avg_z = sum(h[1] for h in hits) // len(hits)
        ctx.push_position(cond_idx, (avg_x, avg_z))
        return True

    # ── Mineshaft ─────────────────────────────────────────────────────
    if t == "mineshaft":
        min_count = params.get("count", 1)
        exclude = (min_count <= 0)
        try:
            positions = ctx.lib.find_mineshafts(x1, z1, x2, z2, max_count=64)
        except Exception:
            positions = []
        if rmax_sq:
            positions = [(bx, bz) for bx, bz in positions
                         if (bx - px)**2 + (bz - pz)**2 < rmax_sq]
        if exclude:
            if positions:
                return False
            ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
            return True
        if len(positions) < min_count:
            return False
        avg_x = sum(p[0] for p in positions) // len(positions)
        avg_z = sum(p[1] for p in positions) // len(positions)
        ctx.push_position(cond_idx, (avg_x, avg_z))
        return True

    # ── Nether / End structures ───────────────────────────────────────
    if t in STRUCT_DIM:
        dim = STRUCT_DIM[t]
        struct_id = STRUCT_TYPES[t]
        min_count = _cond_min_count(cond)
        exclude = (min_count <= 0)
        try:
            positions = ctx.lib.find_structures_in_box_dim(
                struct_type=struct_id,
                x1=x1, z1=z1, x2=x2, z2=z2,
                dim=dim, verify_biome=True, max_count=64)
        except Exception:
            return False
        if rmax_sq:
            positions = [(bx, bz) for bx, bz in positions
                         if (bx - px)**2 + (bz - pz)**2 < rmax_sq]
        if exclude:
            if positions:
                return False
            ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
            return True
        if len(positions) < min_count:
            return False
        ctx.push_position(cond_idx, positions[0])
        return True

    # ── Overworld structures ──────────────────────────────────────────
    if t in STRUCT_TYPES:
        min_count = _cond_min_count(cond)
        exclude = (min_count <= 0)
        hits, positions = _struct_collect(ctx, cond, (px, pz), maxn=1,
                                          stop_at=1 if exclude else min_count)
        if hits < 0:
            return False
        if exclude:
            if hits:
                return False
            ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
            return True
        if hits < min_count:
            return False
        ctx.push_position(cond_idx, positions[0])
        return True

    # ── Climate extreme (1.18+) ───────────────────────────────────────
    # ── Biome filter — C-native checkForBiomes (F_BIOME) ────────────────
    if t == "biome_filter":
        biomes_incl = params.get("biomes_incl", [])
        biomes_excl = params.get("biomes_excl", [])
        scale       = params.get("scale", 4)
        dim_key     = params.get("dim", "overworld")
        dim         = {"overworld": 0, "nether": -1, "end": 1}.get(dim_key, 0)
        y           = params.get("y", 64)
        min_count   = params.get("count", 1)
        exclude     = (min_count <= 0)
        ok = ctx.lib.check_biomes(biomes_incl, biomes_excl,
                                   x1, z1, x2, z2,
                                   scale=scale, dim=dim, y=y)
        if exclude:
            if ok:
                return False
            ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
            return True
        if not ok:
            return False
        ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
        return True

    # ── Biome sample — C-native monteCarloBiomes (F_BIOME_SAMPLE) ────────
    if t == "biome_sample":
        biomes_incl = params.get("biomes_incl", [])
        biomes_excl = params.get("biomes_excl", [])
        coverage    = float(params.get("coverage", 0.5))
        confidence  = float(params.get("confidence", 0.9))
        scale       = params.get("scale", 4)
        y           = params.get("y", 64)
        passed, cx, cz = ctx.lib.monte_carlo_biomes(
            biomes_incl, biomes_excl,
            x1, z1, x2, z2,
            coverage=coverage, confidence=confidence,
            scale=scale, y=y)
        if not passed:
            return False
        ctx.push_position(cond_idx, (cx, cz))
        return True

    # ── Biome center — C-native getBiomeCenters (F_BIOME_CENTER) ─────────
    if t == "biome_center":
        biome_id  = params.get("biome_id", 1)
        min_size  = params.get("min_size", 1)
        tolerance = params.get("tolerance", 4)
        min_count = params.get("count", 1)
        exclude   = (min_count <= 0)
        y         = params.get("y", 64)
        centers = ctx.lib.get_biome_centers(
            biome_id, x1, z1, x2, z2,
            min_size=min_size, tolerance=tolerance,
            y=y, nmax=32)
        if rmax_sq:
            centers = [(bx, bz) for bx, bz in centers
                       if (bx - px)**2 + (bz - pz)**2 < rmax_sq]
        if exclude:
            if centers:
                return False
            ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
            return True
        if len(centers) < min_count:
            return False
        ctx.push_position(cond_idx, centers[0])
        return True

    # ── Locate climate center — coarse flood fill, evaluated by the C engine
    # (one implementation: the search and this path give the same region)
    if t == "climate_center":
        if ctx.mc_version < 22:
            return False
        one = copy.deepcopy(cond)
        one.setdefault("params", {})["_need_center"] = True   # children may use it
        r = _c_eval_single(ctx, one, cond_idx, all_conds, px, pz)
        if r is None:
            raise RuntimeError("Locate climate center needs the C engine (rebuild "
                               "libcubiomes: py tools/build_native.py --cubiomes)")
        ok, wx, wz = r
        if not ok:
            return False
        ctx.push_position(cond_idx, (wx, wz))
        return True

    # ── Climate min/max — C-native F_CLIMATE_MINMAX ─────────────────────
    if t in ("climate_minmax",):
        if ctx.mc_version < 22:
            return False
        para_map = {"Temperature": 0, "Humidity": 1,
                    "Continentalness": 2, "Erosion": 3, "Weirdness": 5}
        para   = para_map.get(params.get("para", "Temperature"), 0)
        # Parse range (empty / None = ±inf)
        def _pb(v, di):
            if v is None: return di
            if isinstance(v, str):
                ss = v.strip()
                if ss == "" or ss in ("-Infinity", "+Infinity",
                                        "-inf", "+inf", "inf"):
                    return di
                try: return float(ss)
                except ValueError: return di
            try: return float(v)
            except (TypeError, ValueError): return di
        rng = params.get("range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            vmin = _pb(rng[0], float("-inf"))
            vmax = _pb(rng[1], float("+inf"))
        else:
            vmin = _pb(params.get("vmin"), float("-inf"))
            vmax = _pb(params.get("vmax"), float("+inf"))
        # same mode for climate_minmax and climate_center, same rule as the
        # C engine (before: climate_minmax ignored "Locate min" here)
        mode = (1 if climate_locate(params) == 1 else 2)   # E_LOCATE_MIN/MAX
        if vmin != float("-inf"): mode |= 4   # E_TEST_LOWER
        if vmax != float("+inf"): mode |= 8   # E_TEST_UPPER
        nmax   = int(params.get("nmax", -1))
        invert = bool(params.get("invert", False))
        passed, wx, wz, _ = ctx.lib.climate_minmax(
            para, x1, z1, x2, z2,
            mode=mode, vmin=vmin, vmax=vmax,
            nmax=nmax, invert=invert)
        if not passed:
            return False
        ctx.push_position(cond_idx, (wx, wz))
        return True

    # ── Climate noise — C-native exact port of F_CLIMATE_NOISE ───────────
    if t == "climate_noise":
        if ctx.mc_version < 22:
            return False
        INT_MIN = -2147483648; INT_MAX = 2147483647
        NP_MAX  = 6
        ok_lo   = params.get("ok_lo", [INT_MIN] * NP_MAX)
        ok_hi   = params.get("ok_hi", [INT_MAX] * NP_MAX)
        ex_lo   = params.get("ex_lo", [INT_MIN] * NP_MAX)
        ex_hi   = params.get("ex_hi", [INT_MAX] * NP_MAX)
        nmax    = int(params.get("nmax", -1))
        passed = ctx.lib.climate_noise(
            x1, z1, x2, z2,
            ok_lo=ok_lo, ok_hi=ok_hi,
            ex_lo=ex_lo, ex_hi=ex_hi,
            nmax=nmax)
        if not passed:
            return False
        ctx.push_position(cond_idx, ((x1 + x2) // 2, (z1 + z2) // 2))
        return True

    # ── Noise sample — F_NOISE_SAMPLE ───────────────────────────────────────
    # Monte-Carlo over the area (coverage / confidence, cubiomes-viewer's
    # random sample order): evaluated by the C engine itself (ssa_eval_single)
    # so the Python path gives exactly the search's answer and position.
    # (Before: "one point / the area's min-max is in range" — another test,
    # other results and another position for the children.)
    if t == "noise_sample":
        if ctx.mc_version < 22:
            return False
        r = _c_eval_single(ctx, cond, cond_idx, all_conds, px, pz)
        if r is None:
            raise RuntimeError("noise_sample needs the C engine (rebuild libcubiomes: "
                               "py tools/build_native.py --cubiomes)")
        ok, wx, wz = r
        if not ok:
            return False
        ctx.push_position(cond_idx, (wx, wz))
        return True

    # ── Approx Y — fast noise heightmap, sampled over the radial zone ─────────
    # Passes if ANY sampled block in the disk of radius r around (at_x, at_z) meets
    # the test (in-range, or out-of-range when "Outside range"). The box (x1..z2)
    # was recentred on "at: x z" by the editor; r = half its width.
    if t == "height":
        if ctx.mc_version < 22:
            return False
        y_min   = float(params.get("y_min", float("-inf")))
        y_max   = float(params.get("y_max", float("+inf")))
        if y_min <= -64: y_min = float("-inf")       # world limits = no bound
        if y_max >= 320: y_max = float("+inf")
        invert  = bool(params.get("invert", False))
        # Read the ABSOLUTE "at: x z" + radius straight from the condition. (_get_box
        # collapses a radial box onto the parent position, losing "at".)
        at_x = int(cond.get("at_x", (x1 + x2) // 2))
        at_z = int(cond.get("at_z", (z1 + z2) // 2))
        r = max(0, int(cond.get("radial", (x2 - x1) // 2)))
        step = 1 if r <= 0 else max(1, (2 * r) // 24 + 1)   # capped grid (≤ ~25/axis)
        r2 = r * r
        found = False; hit = (at_x, at_z)
        sz = at_z - r
        while sz <= at_z + r and not found:
            sx = at_x - r
            while sx <= at_x + r:
                if r == 0 or (sx - at_x) ** 2 + (sz - at_z) ** 2 <= r2:
                    h = ctx.lib.approx_height(sx, sz)
                    in_range = (y_min <= h <= y_max)
                    if ((not in_range) if invert else in_range):
                        found = True; hit = (sx, sz); break
                sx += step
            sz += step
        if not found:
            return False
        ctx.push_position(cond_idx, hit)
        return True

    # ── Precise Y — block-exact terrain (C + GPU), sampled over the radial zone ─
    if t == "precise_y":
        if ctx.mc_version < 22:
            return False
        y_min   = float(params.get("y_min", float("-inf")))
        y_max   = float(params.get("y_max", float("+inf")))
        invert  = bool(params.get("invert", False))
        caves   = bool(params.get("caves", True))
        coverage   = float(params.get("coverage", 0.0) or 0.0)
        confidence = float(params.get("confidence", 0.95) or 0.95)
        # "related to": the disk centre = parent position (px,pz) + the stored offset.
        # For an absolute Exact Y (relative=0) px,pz=(0,0), so at_x == the stored
        # absolute coordinate — identical to before.
        at_x = px + int(cond.get("at_x", 0))
        at_z = pz + int(cond.get("at_z", 0))
        r = max(0, int(cond.get("radial", (x2 - x1) // 2)))
        try:
            import terrain as _terr, numpy as _np
            global _PRECISE_WARNED
            mc = int(getattr(ctx, "mc_version", 22))
            _samp = params.get("sampling", None)
            if r <= 0:
                W = H = 1; step = 1; bx0 = at_x; bz0 = at_z
            else:
                # explicit sampling stride (1/1, 1/4, 1/16 → 1, 2, 4); legacy
                # conditions without it fall back to the capped auto grid.
                step = max(1, int(_samp)) if _samp else max(1, (2 * r) // 64 + 1)
                W = (2 * r) // step + 1; H = W
                bx0 = at_x - r; bz0 = at_z - r
            # CPU EXACT (FP64), un contexte terrain par thread de recherche. Avant :
            # grille GPU FP32 (approchée) utilisée comme verdict final, et « False »
            # pour TOUTES les graines sur une machine sans GPU NVIDIA (0 résultat
            # silencieux). Même grille que le batch C → mêmes verdicts.
            grid = _terr.surface_grid_exact(mc, ctx.seed, bx0, bz0, W, H, step=step, caves=caves)
            m = (grid >= y_min) & (grid <= y_max)   # NB: not `cond` (that's the dict!)
            if invert:
                m = ~m
            ZZ, XX = _np.mgrid[0:H, 0:W]
            WX = bx0 + XX * step; WZ = bz0 + ZZ * step
            if r > 0:
                disk = ((WX - at_x) ** 2 + (WZ - at_z) ** 2) <= (r * r)
                m = m & disk
            else:
                disk = _np.ones_like(m, dtype=bool)
            if coverage > 0.0 and r > 0:
                # deterministic coverage test (Wilson) over the strided disk — the
                # in-disk grid is identical to the C batch, so verdicts match.
                if not _coverage_pass(int(disk.sum()), int(m.sum()), coverage, confidence):
                    return False
                hit = (at_x, at_z)          # the whole zone validated → its centre
            else:
                if not m.any():
                    return False
                fidx = int(_np.argmax(m)); jz, ix = divmod(fidx, W)
                hit = (int(WX[jz, ix]), int(WZ[jz, ix]))
        except Exception as _ex:
            if not _PRECISE_WARNED:
                _PRECISE_WARNED = True
                import traceback as _tb
                print("[precise_y] eval error:", repr(_ex)); _tb.print_exc()
            return False
        ctx.push_position(cond_idx, hit)
        return True

    if t == "climate_extreme":
        if ctx.mc_version < 22:
            return False
        try:
            from cubiomes_wrap import (NoiseMapLib, NP_TEMPERATURE, NP_HUMIDITY,
                                         NP_CONTINENTALNESS, NP_EROSION, NP_WEIRDNESS)
        except ImportError:
            return False
        para_map = {
            "Temperature": NP_TEMPERATURE, "Humidity": NP_HUMIDITY,
            "Continentalness": NP_CONTINENTALNESS, "Erosion": NP_EROSION,
            "Weirdness": NP_WEIRDNESS,
        }
        nptype = para_map.get(params.get("para", "Temperature"), NP_TEMPERATURE)
        find_max = str(params.get("yield_of", "Maximum")).lower() == "maximum"

        def _pf(s, d):
            try:
                s = str(s).strip()
                if not s or "inf" in s.lower() or "∞" in s:
                    return d
                return float(s)
            except Exception:
                return d

        vr = params.get("value_range", ("-Infinity", "+Infinity"))
        lo, hi = vr if isinstance(vr, (list, tuple)) and len(vr) == 2 else ("-Infinity", "+Infinity")
        v_min = _pf(lo, float("-inf"))
        v_max = _pf(hi, float("+inf"))
        invert = bool(params.get("invert_range", False))

        octave_str = str(params.get("octave", "all")).strip()
        if octave_str in ("all", ""):
            nmax = -1
        else:
            try:
                nmax = int(octave_str[:-1]) * 2 + (1 if octave_str[-1].upper() == "A" else 2)
            except Exception:
                nmax = -1

        if x1 >= x2 or z1 >= z2:
            return False
        gx = x1 // 4; gz = z1 // 4
        W = max(1, (x2 - x1) // 4); H = max(1, (z2 - z1) // 4)
        if W * H > 16_000_000:
            return False
        try:
            nm = NoiseMapLib(ctx.seed, nptype=nptype, large=get_large_biomes(), nmax=nmax)
            rng = nm.get_para_range(gx, gz, W, H)
            arr = nm.gen_noise_grid(gx, gz, W, H, scale=4)
            import numpy as np
            idx_flat = int(np.argmax(arr)) if find_max else int(np.argmin(arr))
            ext_val = float(arr.flat[idx_flat])
            iz, ix = divmod(idx_flat, W)
            world_x = (gx + ix) * 4 + 2
            world_z = (gz + iz) * 4 + 2
        except Exception:
            return False

        in_range = (v_min <= ext_val <= v_max)
        if invert:
            if in_range:
                return False
        else:
            if not in_range:
                return False
        ctx.push_position(cond_idx, (world_x, world_z))
        return True

    # ── Crunchiness ──────────────────────────────────────────────────────────
    if t == "crunchiness":
        try:
            import ctypes as _ct
            _lib = ctx.lib._lib
            # Pull params
            climate   = int(params.get("climate", 2))
            threshold = float(params.get("threshold", 0.10))
            mode      = int(params.get("mode", 0))   # 0=crunchy (<=), 1=bland (>=)
            octs      = params.get("octaves", {}) or {}
            weighting = params.get("weighting", "Equal")
            custom_w  = params.get("custom_weights", {}) or {}

            # Build (slot, pair_idx, is_b) list
            checked = []  # (slot_name, flat_weight_index, is_b_stream)
            for i in range(6):
                for sub_i, s_letter in enumerate("AB"):
                    slot = f"{i}{s_letter}"
                    if octs.get(slot):
                        checked.append((slot, 2 * i + sub_i, sub_i == 1))

            if not checked:
                # No octaves selected: score = 0 always → passes crunchy mode
                passes = (mode == 0)
                if passes:
                    ctx.push_position(cond_idx, (px, pz))
                    return True
                return False

            # Compute per-slot weights
            weights = {}
            if weighting.startswith("Equal"):
                w = 1.0 / len(checked)
                for slot, idx, _ in checked:
                    weights[slot] = w
            elif weighting.startswith("Commission"):
                cm = {"0A": 0.35, "0B": 0.35,
                      "1A": 0.11, "1B": 0.11,
                      "2A": 0.04, "2B": 0.04}
                for slot, idx, _ in checked:
                    weights[slot] = cm.get(slot, 0.0)
            elif weighting.startswith("First octave"):
                first_w = {"0A": 0.4, "0B": 0.4}
                rest = [(s, i, b) for s, i, b in checked if s not in first_w]
                rest_w = (1.0 - sum(first_w.get(s, 0.0)
                                    for s, _, _ in checked if s in first_w)
                          ) / max(1, len(rest))
                for slot, idx, _ in checked:
                    weights[slot] = first_w.get(slot, rest_w if rest else 0.0)
            elif weighting.startswith("Custom"):
                for slot, idx, _ in checked:
                    try:
                        weights[slot] = float(custom_w.get(slot, 0.0))
                    except (TypeError, ValueError):
                        weights[slot] = 0.0
            else:
                w = 1.0 / len(checked)
                for slot, idx, _ in checked:
                    weights[slot] = w

            # Compute score: sum of weight * |y_frac - 0.5|
            # NOTE: no *2 — matches test_crunchiness() in session_engine.c
            # and the GPU kernel exactly. Thresholds are consistent across
            # Python evaluator / C engine / GPU kernel.
            score = 0.0
            seed64 = _ct.c_int64(ctx.seed).value
            for slot, idx, is_b in checked:
                octave_pair_idx = idx // 2  # 0..5
                y = _lib.ssa_crunch_yfrac(
                    _ct.c_int64(seed64),
                    _ct.c_int(climate),
                    _ct.c_int(octave_pair_idx),
                    _ct.c_int(1 if is_b else 0),
                    _ct.c_int(1 if get_large_biomes() else 0),  # world type
                )
                if y < 0:
                    continue  # octave doesn't exist for this climate
                score += weights.get(slot, 0.0) * abs(y - 0.5)

            passes = (score <= threshold) if mode == 0 else (score >= threshold)
            if passes:
                ctx.push_position(cond_idx, (px, pz))
                return True
            return False
        except Exception:
            return False

    # Unknown type → fail safe
    return False


# ─────────────────────────────────────────────────────────────────────────
# Top-level evaluator with Spiral sub-iteration
# ─────────────────────────────────────────────────────────────────────────

def _eval_node(ctx: EvalContext, conditions: List[dict],
                cond_1idx: int, at: Tuple[int, int]) -> bool:
    """Evaluate node cond_1idx (1-based) with parent position `at`.
    Recursively evaluates children. Spiral nodes iterate all positions."""
    idx0 = cond_1idx - 1
    if idx0 < 0 or idx0 >= len(conditions):
        return True
    cond = conditions[idx0]
    t = cond.get("type", "")
    params = cond.get("params", {})

    # Temporarily wire parent position so _get_box works correctly
    parent_idx = cond.get("relative", 0)
    saved = ctx.get_parent_pos(parent_idx)
    ctx.push_position(parent_idx, at)

    try:
        direct_children = [
            (i + 1) for i, c in enumerate(conditions)
            if c.get("enabled", True) and c.get("relative", 0) == cond_1idx
        ]

        if (t == "noise_sample" and params.get("yield_samples") and direct_children
                and ctx.mc_version >= 22):
            # « Yield individual samples »: the children must all pass around ONE
            # accepted sample (in the search's order) — as the C engine does
            r = _c_eval_single(ctx, cond, cond_1idx, conditions, at[0], at[1],
                               want_samples=True)
            if r is None:
                raise RuntimeError("noise_sample needs the C engine (rebuild libcubiomes)")
            ok, _wx, _wz, samples = r
            if not ok:
                return False
            for sp in samples:
                ctx.push_position(cond_1idx, sp)
                if all(_eval_node(ctx, conditions, ch, sp) for ch in direct_children):
                    return True
            return False

        if t == "spiral":
            step = int(params.get("step", 16) or 0)
            if step < 1:
                step = 512          # cubiomes-viewer: « c.step ? c.step : 512 » (= the C engine)
            rmax_b = cond.get("rmax", 0)
            if rmax_b and rmax_b > 0:
                rb = rmax_b - 1
                x1, z1, x2, z2 = at[0] - rb, at[1] - rb, at[0] + rb, at[1] + rb
                rmax_sq = rb * rb + 1
            else:
                x1 = at[0] + cond.get("x1", 0); z1 = at[1] + cond.get("z1", 0)
                x2 = at[0] + cond.get("x2", 0); z2 = at[1] + cond.get("z2", 0)
                rmax_sq = 0

            for (gx, gz) in _spiral_positions(x1, z1, x2, z2, step,
                                               rmax_sq, at[0], at[1]):
                ctx.push_position(cond_1idx, (gx, gz))
                all_ok = all(
                    _eval_node(ctx, conditions, ch, (gx, gz))
                    for ch in direct_children
                )
                if all_ok:
                    return True
            return False

        elif (t in ("first_stronghold", "stronghold")
              and _scan_128(params)
              and (_stronghold_extra_enabled(params) or _portal_anchor(params))
              and direct_children):
            # ── Linking JOINT scan_128 ────────────────────────────────────────
            # « check all 128 strongholds » AVEC des conditions reliées : on veut
            # que les enfants (ex. village) soient près du MÊME stronghold qui a
            # les N yeux — pas du premier. On itère donc les 128 strongholds et,
            # pour chacun, on teste les enfants RELATIVEMENT à lui, puis le filtre
            # yeux. Optimisation (le « sens inverse » demandé) : les enfants sont
            # souvent CHEAP (village à côté) et le filtre yeux coûte un BFS ; on
            # teste donc les enfants D'ABORD et on ne fait le BFS des yeux que sur
            # les rares strongholds qui ont déjà le village. Pass si UN stronghold
            # satisfait tout ; on mémorise sa position (TP + carte).
            try:
                shs = ctx.lib.find_strongholds(max_count=_sh_count(params))
            except Exception:
                shs = []
            for bx, bz in (shs or []):
                sp = (int(bx), int(bz))
                if _portal_anchor(params):           # linked to the end portal (03/10)
                    sp = _portal_xz(ctx.seed, ctx.mc_version, bx, bz)
                    if sp is None:
                        continue
                ctx.push_position(cond_1idx, sp)
                if direct_children and not all(
                        _eval_node(ctx, conditions, ch, sp)
                        for ch in direct_children):
                    continue
                if not _stronghold_extra_ok(ctx, params, bx, bz):
                    continue
                ctx.match_pos = sp
                return True
            return False

        elif t in Y_CHILD_TYPES and direct_children and _cond_min_count(cond) > 0:
            # (10/10 ter) 1.16 dungeons / lava lakes with linked conditions, as
            # the C engine (clust_node): instances == 1 → OR over every real
            # one (its X Y Z for the children), > 1 → their centroid
            insts = _f116_instances(ctx, cond, at, ctx.get_parent_y(parent_idx))
            mc_ = _cond_min_count(cond)
            if insts is None:                          # older library: the old way
                ok = _evaluate_condition(ctx, cond, cond_1idx, conditions)
                cp = ctx.get_parent_pos(cond_1idx)
                return ok and all(_eval_node(ctx, conditions, ch, cp) for ch in direct_children)
            if mc_ == 1:
                for f in insts:
                    ctx.push_position(cond_1idx, (f.x, f.z), f.y)
                    if all(_eval_node(ctx, conditions, ch, (f.x, f.z)) for ch in direct_children):
                        return True
                return False
            if len(insts) < mc_:
                return False
            n = len(insts)

            def _tdiv(a, b):                          # C integer division (toward 0)
                q = abs(a) // b
                return q if a >= 0 else -q
            cpos = (_tdiv(sum(f.x for f in insts), n), _tdiv(sum(f.z for f in insts), n))
            ctx.push_position(cond_1idx, cpos, _tdiv(sum(f.y for f in insts), n))
            return all(_eval_node(ctx, conditions, ch, cpos) for ch in direct_children)

        elif (t in STRUCT_TYPES and t not in STRUCT_DIM and direct_children
              and _cond_min_count(cond) > 0):
            # cubiomes-viewer BR_CLUST (see eval_node in session_engine.c):
            # instances == 1 → OR over every instance; > 1 → centroid.
            mc_ = _cond_min_count(cond)
            if mc_ == 1:
                hits, insts = _struct_collect(ctx, cond, at, maxn=_STRUCT_SPLIT_MAX,
                                              stop_at=_STRUCT_SPLIT_MAX)
                for ip in insts:
                    ctx.push_position(cond_1idx, ip)
                    if all(_eval_node(ctx, conditions, ch, ip) for ch in direct_children):
                        return True
                return False
            hits, _insts, (sx, sz) = _struct_collect(ctx, cond, at, maxn=1, stop_at=0, sums=True)
            if hits < mc_:
                return False
            cpos = (int(sx / hits), int(sz / hits))        # C truncation toward 0
            ctx.push_position(cond_1idx, cpos)
            return all(_eval_node(ctx, conditions, ch, cpos) for ch in direct_children)

        else:
            # Evaluate this condition
            ok = _evaluate_condition(ctx, cond, cond_1idx, conditions)
            if not ok:
                return False
            # Recurse into children (AND logic), unless they are OR/NOT
            # (which handle their own children internally)
            child_pos = ctx.get_parent_pos(cond_1idx)
            skip_children_in = {"logic_or", "logic_not"}
            if t not in skip_children_in:
                for ch in direct_children:
                    if not _eval_node(ctx, conditions, ch, child_pos):
                        return False
            return True

    finally:
        ctx.push_position(parent_idx, saved)


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────

def evaluate_seed(conditions: List[dict], seed: int, mc_version: int,
                   lib: CubiomesLib) -> Tuple[bool, str]:
    """Evaluate all enabled conditions for one seed."""
    enabled_indices = [i for i, c in enumerate(conditions)
                       if c.get("enabled", True)]
    if not enabled_indices:
        return (False, "")

    ctx = EvalContext(lib, mc_version, seed)

    # Top-level nodes: those whose parent is world origin (relative == 0)
    # and that are NOT children of another enabled condition
    top_level = [
        (i + 1) for i in enabled_indices
        if conditions[i].get("relative", 0) == 0
    ]
    # Nodes referenced as children of a top-level spiral are evaluated
    # recursively from _eval_node, not from here. Nodes with relative > 0
    # that are NOT children of any other enabled condition are also top-level.
    referenced_as_children = set()
    for i in enabled_indices:
        rel = conditions[i].get("relative", 0)
        if rel > 0 and (rel - 1) in enabled_indices:
            referenced_as_children.add(i + 1)  # 1-based

    for cond_1idx in top_level:
        if not _eval_node(ctx, conditions, cond_1idx, (0, 0)):
            return (False, "")

    # Also evaluate any enabled top-level nodes not yet covered
    # (e.g. conditions with relative=0 that should be AND-chained)
    # → all already handled above since top_level = all with relative==0

    # Build summary string
    parts = []
    for i in enabled_indices[:4]:
        p = ctx.positions[i + 1] if (i + 1) < len(ctx.positions) else None
        if p is not None:
            parts.append(f"{conditions[i].get('type', '?')}@({p[0]},{p[1]})")
    return (True, "  ".join(parts))


def _explain_seed_c(conditions, seed, mc_version, lib):
    """explain_seed through the C engine (ssa_explain_seed): the SAME evaluation
    as the search, so a result is always located where the search found it.
    None when the library is too old or the tree can't be serialised."""
    import ctypes as ct
    raw = lib._lib
    if not hasattr(raw, "ssa_explain_seed"):
        return None
    active = active_conditions(conditions)
    res = _build_ssacond_array(conditions, raw)
    if res is None:
        return None
    arr, n = res
    raw.ssa_explain_seed.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.c_int64,
                                     ct.POINTER(ct.c_int32), ct.POINTER(ct.c_uint8)]
    raw.ssa_explain_seed.restype = ct.c_int
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    xz = (ct.c_int32 * (2 * n))()
    valid = (ct.c_uint8 * n)()
    s64 = int(seed) & _U64
    s64 = s64 - (1 << 64) if s64 >= (1 << 63) else s64
    reached = passed = None
    if hasattr(raw, "ssa_explain_seed2"):
        raw.ssa_explain_seed2.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.c_int64,
                                          ct.POINTER(ct.c_int32), ct.POINTER(ct.c_uint8),
                                          ct.POINTER(ct.c_uint8), ct.POINTER(ct.c_uint8)]
        raw.ssa_explain_seed2.restype = ct.c_int
        rb, pb = (ct.c_uint8 * n)(), (ct.c_uint8 * n)()
        ok = bool(raw.ssa_explain_seed2(arr, n, int(mc_version), large, s64, xz, valid, rb, pb))
        reached, passed = [bool(x) for x in rb], [bool(x) for x in pb]
    else:
        ok = bool(raw.ssa_explain_seed(arr, n, int(mc_version), large, s64, xz, valid))
    pos = [(int(xz[2 * i]), int(xz[2 * i + 1])) if valid[i] else None for i in range(n)]
    anchors = []
    for c in active:
        r = int(c.get("relative", 0) or 0)
        anchors.append((0, 0) if r == 0 else (pos[r - 1] if 1 <= r <= n else None))
    if reached is not None:
        _LAST_CHECK[int(seed)] = (reached, passed)
    return ok, active, anchors, pos


_LAST_CHECK = {}


def check_seed(conditions, seed, mc_version, lib=None):
    """explain_seed + for every active condition: reached? branch passed?
    → (ok, active, anchors, pos, reached, passed); reached/passed None with an
    old library."""
    if lib is None:
        lib = CubiomesLib()
    r = explain_seed(conditions, seed, mc_version, lib)
    rp = _LAST_CHECK.pop(int(seed), (None, None))
    return tuple(r) + tuple(rp)


def explain_seed(conditions: List[dict], seed: int, mc_version: int, lib: CubiomesLib = None):
    if lib is None:
        lib = CubiomesLib()
    if any(c.get("type") == "first_stronghold" and _portal_anchor(c.get("params"))
           for c in active_conditions(conditions)):
        # End portal mode: the C engine only knows the ring point (03/10)
        return _explain_seed_py(conditions, seed, mc_version, lib)
    try:
        r = _explain_seed_c(conditions, seed, mc_version, lib)
        if r is not None:
            return r
    except Exception:
        import traceback
        traceback.print_exc()
    return _explain_seed_py(conditions, seed, mc_version, lib)


def _explain_seed_py(conditions: List[dict], seed: int, mc_version: int, lib: CubiomesLib = None):
    """Why does `seed` match? Evaluates the ACTIVE tree (see active_conditions)
    with the Python evaluator and returns (ok, active, anchors, positions):
      active[i]    : the i-th active condition (engine format, deep copy)
      anchors[i]   : (x, z) of its parent when it was evaluated (zone centre)
      positions[i] : (x, z) where it was satisfied, or None.
    Used by the Qt workspace to draw every condition's zone and hit on the map."""
    active = active_conditions(conditions)
    if lib is None:
        lib = CubiomesLib()
    ctx = EvalContext(lib, mc_version, int(seed))
    anchors = [None] * len(active)
    orig = _eval_node

    kids = {}
    for j, c in enumerate(active):
        kids.setdefault(int(c.get("relative", 0) or 0), []).append(j + 1)

    def clear(idx):
        # only the positions of the evaluation that succeeded (08/10): an
        # earlier spiral cell / instance left stale ones in the subtree, and
        # the map was centred on them
        st = [idx]
        while st:
            k = st.pop()
            if 0 < k < len(ctx.positions):
                ctx.positions[k] = None
            if 0 < k <= len(anchors):
                anchors[k - 1] = None
            st.extend(kids.get(k, ()))

    def spy(ctx_, conds_, idx, at):
        clear(idx)
        if 1 <= idx <= len(anchors):
            anchors[idx - 1] = (int(at[0]), int(at[1]))
        r = orig(ctx_, conds_, idx, at)
        if not r:
            clear(idx)
        return r
    ok = True
    g = globals()
    g["_eval_node"] = spy
    try:
        for i, c in enumerate(active):
            if int(c.get("relative", 0) or 0) == 0 and not spy(ctx, active, i + 1, (0, 0)):
                ok = False
                break
    finally:
        g["_eval_node"] = orig
    pos = []
    for i in range(len(active)):
        p_ = ctx.positions[i + 1] if i + 1 < len(ctx.positions) else None
        pos.append((int(p_[0]), int(p_[1])) if p_ is not None else None)
    return ok, active, anchors, pos


def find_match_position(conditions: List[dict], seed: int, mc_version: int,
                        lib: CubiomesLib):
    """Renvoie une position (x, z) représentative de l'endroit où la session
    MATCHE pour cette seed — pour recentrer la carte au clic sur un résultat.
    Priorité : le stronghold qui satisfait un filtre yeux/maxY (utile en
    scan_128 où ce n'est PAS le premier), sinon la position de la 1ʳᵉ condition
    concrète matchée (structure/spawn/…). Renvoie None si rien de pertinent."""
    enabled_indices = [i for i, c in enumerate(conditions)
                       if c.get("enabled", True)]
    if not enabled_indices:
        return None
    try:
        ctx = EvalContext(lib, mc_version, int(seed))
    except Exception:
        return None
    top_level = [(i + 1) for i in enabled_indices
                 if conditions[i].get("relative", 0) == 0]
    try:
        for cond_1idx in top_level:
            _eval_node(ctx, conditions, cond_1idx, (0, 0))
    except Exception:
        pass
    # Priorité 0 : position exacte posée par l'éval (linking joint scan_128 :
    # le stronghold qui a À LA FOIS les yeux ET le village à côté).
    _mp = getattr(ctx, "match_pos", None)
    if _mp is not None:
        return (int(_mp[0]), int(_mp[1]))
    # Priorité 1 : stronghold satisfaisant un filtre eye/maxY/corrupt.
    for i in enabled_indices:
        c = conditions[i]
        if (c.get("type") in ("first_stronghold", "stronghold")
                and _stronghold_extra_enabled(c.get("params", {}))):
            params = c.get("params", {})
            try:
                if _scan_128(params):
                    shs = ctx.lib.find_strongholds(max_count=_sh_count(params))
                else:
                    shs = ctx.lib.find_strongholds(max_count=1, radius_blocks=None)
                for bx, bz in (shs or []):
                    if _stronghold_extra_ok(ctx, params, bx, bz):
                        return (int(bx), int(bz))
            except Exception:
                pass
    # Priorité 2 : 1ʳᵉ position concrète poussée (≠ origine).
    for i in enabled_indices:
        p = ctx.positions[i + 1] if (i + 1) < len(ctx.positions) else None
        if p is not None and (int(p[0]), int(p[1])) != (0, 0):
            return (int(p[0]), int(p[1]))
    # Repli : 1ʳᵉ position poussée même si origine.
    for i in enabled_indices:
        p = ctx.positions[i + 1] if (i + 1) < len(ctx.positions) else None
        if p is not None:
            return (int(p[0]), int(p[1]))
    return None


def _joint_seed_eval(conditions: List[dict], seed: int, mc_version: int,
                     lib: CubiomesLib):
    """Évalue TOUTE la session pour une seed (via _eval_node, qui gère le linking
    joint scan_128) et renvoie (ok, match_pos). match_pos = le stronghold qui a
    satisfait la contrainte jointe (yeux + enfants), sinon None."""
    enabled_indices = [i for i, c in enumerate(conditions)
                       if c.get("enabled", True)]
    if not enabled_indices:
        return (False, None)
    try:
        ctx = EvalContext(lib, mc_version, int(seed))
    except Exception:
        return (False, None)
    top_level = [(i + 1) for i in enabled_indices
                 if conditions[i].get("relative", 0) == 0]
    try:
        for cond_1idx in top_level:
            if not _eval_node(ctx, conditions, cond_1idx, (0, 0)):
                return (False, None)
    except Exception:
        return (False, None)
    return (True, getattr(ctx, "match_pos", None))


def _session_joint_scan128_children(conditions: List[dict]) -> bool:
    """True si un stronghold scan_128 (filtre eye/maxY) a AU MOINS une condition
    ENFANT reliée → linking joint : le moteur C le sérialise en anneau et émet
    « enfant près d'UN stronghold », puis le post-filtre Python vérifie les yeux
    du MÊME stronghold (candidats rares → rapide)."""
    for i, c in enumerate(conditions):
        if not c.get("enabled", True):
            continue
        if (c.get("type") in ("first_stronghold", "stronghold")
                and _scan_128(c.get("params", {}))
                and _stronghold_extra_enabled(c.get("params", {}))):
            cidx = i + 1
            if any(cc.get("enabled", True) and cc.get("relative", 0) == cidx
                   for cc in conditions):
                return True
    return False


def _run_scan128_range(min_eyes, min_max_y, seed_start, seed_count, mc,
                       num_workers, stop_event, on_match, on_progress, on_mode):
    """Scan « un des 128 strongholds a N yeux (et/ou Max Y) » sur une PLAGE, en
    réutilisant EXACTEMENT le worker natif de l'onglet Stronghold seed-list
    (`_seed_list_128_worker` : cb_strongholds128_arr + prefilter natif + valid.
    des rares survivants), via ProcessPool multi-cœurs. Différence clé avec le
    chemin qui bloquait : `fut.result(timeout=...)` → le STOP répond toujours.
    Émet (seed, "x,z") du stronghold satisfaisant. Renvoie True si géré."""
    from stronghold_eyes import _seed_list_128_worker, _sl_native_init

    if not _sl_native_init():
        print("[SCAN128] natif indisponible → repli", flush=True)
        return False
    print(f"[SCAN128] start min_eyes={min_eyes} maxY={min_max_y} "
          f"count={seed_count}", flush=True)

    if on_mode is not None:
        try: on_mode(0)
        except Exception: pass

    total = seed_count if seed_count > 0 else 0
    end = seed_count if seed_count > 0 else (1 << 48)
    BLOCK = 200
    done = 0
    i = 0
    while i < end:
        if stop_event is not None and stop_event.is_set():
            break
        b = min(i + BLOCK, end)
        seeds = list(range(seed_start + i, seed_start + b))
        try:
            # _seed_list_128_worker : worker EXACT de l'onglet Stronghold seed-list
            # (cb_strongholds128_arr natif + prefilter + valid. des survivants).
            # Appelé IN-PROCESS (pas de ProcessPool → pas de bug spawn Windows
            # depuis un thread). h = (seed, eyes, bx, bz, maxy).
            for h in _seed_list_128_worker((seeds, min_eyes, min_max_y, mc)):
                on_match(int(h[0]), f"{int(h[2])},{int(h[3])}")
        except Exception:
            import traceback
            traceback.print_exc()
        done += (b - i)
        i = b
        if on_progress is not None:
            on_progress(done, total)
    print(f"[SCAN128] fini, scanned={done}", flush=True)
    return True


def _run_stronghold_joint_search(conditions, seed_start, seed_count, mc_version,
                                 num_workers, stop_event, on_match, on_progress,
                                 on_mode):
    """Scan multi-thread d'une PLAGE pour les sessions 'scan_128 + condition
    reliée' (contrainte jointe : le MÊME stronghold parmi les 128 a les yeux ET
    les enfants à côté). Per-seed Python, mais les appels cubiomes (ctypes/C)
    libèrent le GIL → les threads parallélisent. Émet (seed, "x,z") pour le TP.
    Plus lent que le moteur C, mais c'est le seul chemin correct pour ce cas."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from collections import deque

    if on_mode is not None:
        try: on_mode(0)
        except Exception: pass

    nw = max(1, int(num_workers))
    _tls = threading.local()

    def _get_lib():
        lib = getattr(_tls, "lib", None)
        if lib is None:
            lib = CubiomesLib()
            _tls.lib = lib
        return lib

    total = seed_count if seed_count > 0 else (1 << 62)
    BLOCK = 500

    def _work(a, b):
        lib = _get_lib()
        hits = []
        for s in range(a, b):
            if stop_event is not None and stop_event.is_set():
                break
            ws = seed_start + s
            ok, mp = _joint_seed_eval(conditions, ws, mc_version, lib)
            if ok:
                hits.append((int(ws), f"{mp[0]},{mp[1]}" if mp else ""))
        return (b - a, hits)

    done = 0
    with ThreadPoolExecutor(max_workers=nw) as ex:
        in_flight = deque()
        i = 0
        IN_FLIGHT = nw * 3

        def _submit():
            nonlocal i
            if i >= total:
                return False
            b = min(i + BLOCK, total)
            in_flight.append(ex.submit(_work, i, b))
            i = b
            return True

        for _ in range(IN_FLIGHT):
            if not _submit():
                break
        while in_flight:
            if stop_event is not None and stop_event.is_set():
                for f in in_flight:
                    f.cancel()
                break
            fut = in_flight.popleft()
            try:
                n, hits = fut.result()
                for ws, det in hits:
                    on_match(ws, det)
                done += n
                if on_progress:
                    on_progress(done, seed_count if seed_count > 0 else 0)
            except Exception:
                pass
            _submit()



# ─────────────────────────────────────────────────────────────────────────────
# C-native SsaCond builder
# ─────────────────────────────────────────────────────────────────────────────

# Map Python condition type strings → CTYPE_* integers (filled at first call)
_CTYPE_MAP: dict = {}

def _get_ctype_map(lib) -> dict:
    """Lazily build the Python-key → CTYPE_int mapping from the C library."""
    global _CTYPE_MAP
    if _CTYPE_MAP:
        return _CTYPE_MAP
    _key_to_c = {
        "spiral":              "spiral",
        "scale_to_nether":     "scale_to_nether",
        "scale_to_overworld":  "scale_to_overworld",
        "first_stronghold":    "first_stronghold",
        "stronghold":          "stronghold",
        "fortress":            "structure_nether",
        "ruined_portal_nether": "structure_nether",
        "end_gateway":         "structure_end",
        "bastion":             "structure_nether",
        "end_city":            "structure_end",
        "mineshaft":           "mineshaft",
        "slime":               "slime",
        "spawn":               "spawn",
        "biome_filter":        "biome_filter",
        "biome_sample":        "biome_sample",
        "biome_center":        "biome_center",
        "climate_minmax":      "climate_minmax",
        "climate_center":      "climate_area",     # coarse flood fill (01/10)
        "climate_extreme":     "climate_minmax",   # legacy alias
        "climate_noise":       "climate_noise",
        "noise_sample":        "noise_sample",   # Monte-Carlo climat (coverage/confidence)
        "height":              "height",
        "logic_or":            "logic_or",
        "logic_not":           "logic_not",
        "crunchiness":         "crunchiness",
        "spawn_feature116":    "spawn_feature",     # (09/10) 1.16 fallback point
        "dungeon":             "feature116",        # (10/10) 1.16 per-chunk features
        "lava_lake":           "feature116",
    }
    # All standard overworld structures
    for k in ("village","monument","mansion","outpost","hut","igloo",
              "desert_pyramid","jungle_temple","ruined_portal","shipwreck",
              "treasure","ancient_city","trial_chambers","trail_ruins",
              "abandoned_camp",
              "ocean_ruin","desert_well"):
        _key_to_c[k] = "structure_ow"

    try:
        for py_key, c_suffix in _key_to_c.items():
            fn = getattr(lib, f"ssa_ctype_{c_suffix}", None)
            if fn:
                _CTYPE_MAP[py_key] = int(fn())
    except Exception:
        pass
    return _CTYPE_MAP


_OW_STRUCTS_CV = {"village", "monument", "mansion", "outpost", "hut", "igloo", "desert_pyramid",
                  "jungle_temple", "ruined_portal", "shipwreck", "treasure", "ancient_city",
                  "trial_chambers", "trail_ruins", "ocean_ruin", "desert_well"}


def cv_full_octave_conditions(enabled: List[dict]) -> set:
    """Indices (in `enabled`) of the climate / noise-sample conditions that
    cubiomes-viewer evaluates with ALL octaves despite their « up to octave »
    setting.

    cubiomes-viewer (SearchThreadEnv::init4Noise) only seeds the reduced-octave
    climate noise when the seed's biome generator is not yet set up; once a
    condition has applied the full generator for the seed (biome filter /
    sample / centre, spawn, height, climate noise, stronghold, an overworld
    structure found in its area), later climate conditions read the full
    noise. Which conditions come « later » is fixed by CV's evaluation order:
    parents before children, siblings in list order. SSA evaluates in its own
    (cheapest-first) order, so this is decided here, statically, from CV's."""
    n = len(enabled)
    kids = {i: [] for i in range(-1, n)}
    parent = []
    for i, c in enumerate(enabled):
        try:
            r = int(c.get("relative", 0) or 0)
        except (TypeError, ValueError):
            r = 0
        p = r - 1 if 1 <= r <= n and r != i + 1 else -1
        parent.append(p)
        kids[p].append(i)

    def t(i):
        return enabled[i].get("type", "")

    def uncond(i):          # applies the generator whenever it is evaluated
        c = enabled[i]
        tt = t(i)
        if tt == "biome_filter":
            return (c.get("params", {}).get("dim", "overworld") or "overworld") == "overworld"
        return tt in ("biome_sample", "biome_center", "spawn", "height", "climate_noise")

    def covers_region(i):   # an overworld structure area holding a whole region
        c = enabled[i]      # (≤ 40 chunks) always has a candidate position in it,
        if t(i) not in _OW_STRUCTS_CV or t(i) in ("treasure", "desert_well"):
            return False    # and CV sets the generator up at the first candidate
        try:
            rm = int(c.get("rmax", 0) or 0)
            if rm > 0:
                w = h = int((rm - 1) * 1.414)
            else:
                w = abs(int(c.get("x2", 0)) - int(c.get("x1", 0)))
                h = abs(int(c.get("z2", 0)) - int(c.get("z1", 0)))
        except (TypeError, ValueError):
            return False
        return w >= 2 * 40 * 16 and h >= 2 * 40 * 16

    def cond_applier(i):    # applies it when it passes
        c = enabled[i]
        if t(i) in _OW_STRUCTS_CV:
            return _cond_min_count(c) > 0 or covers_region(i)
        return t(i) == "stronghold" and _cond_min_count(c) > 0

    def evaluated_applies(i):   # evaluating it applies the generator, pass or fail
        return uncond(i) or covers_region(i)

    memo = {}

    def guarantees(i, depth=0):
        """passing node i implies the full generator was applied"""
        if i in memo:
            return memo[i]
        if depth > n:
            return False
        memo[i] = False
        tt = t(i)
        if uncond(i) or cond_applier(i):
            res = True
        elif tt == "logic_or":
            ks = kids[i]
            res = bool(ks) and all(guarantees(k, depth + 1) for k in ks)
        elif tt == "logic_not":
            ks = kids[i]
            res = bool(ks) and evaluated_applies(ks[0])
        else:
            res = any(guarantees(k, depth + 1) for k in kids[i])
        memo[i] = res
        return res

    out = set()
    for y, c in enumerate(enabled):
        if t(y) not in ("climate_minmax", "noise_sample"):   # (cubiomes-viewer quirk; climate_center is SSA-only)
            continue
        applied = False
        node, guard = y, 0
        while node != -1 and not applied and guard <= n:
            guard += 1
            p = parent[node]
            if p != -1 and (uncond(p) or cond_applier(p)):
                applied = True
                break
            sibs = kids[p]
            earlier = sibs[:sibs.index(node)] if node in sibs else []
            pt = t(p) if p != -1 else ""
            for sb in earlier:
                if pt in ("logic_or", "logic_not"):
                    if evaluated_applies(sb):   # evaluated (and failed) before
                        applied = True
                elif guarantees(sb):            # AND: evaluated and passed before
                    applied = True
                if applied:
                    break
            node = p
        if applied:
            out.add(y)
    return out


def _build_ssacond_array(conditions: List[dict], lib) -> Optional[object]:
    """
    Serialise enabled conditions into a ctypes array of SsaCond structs.
    Returns (array, n) or None if the engine is not available.
    """
    import ctypes as ct
    try:
        sz = int(lib.ssa_sizeof_ssacond())
    except AttributeError:
        return None

    ctype_map = _get_ctype_map(lib)
    if not ctype_map:
        return None

    enabled = active_conditions(conditions)
    n = len(enabled)
    if n == 0 or n > 128:
        return None

    buf = (ct.c_uint8 * (sz * n))()
    try:
        _cv_full_oct = cv_full_octave_conditions(enabled)
    except Exception:
        _cv_full_oct = set()

    INT_MIN = -2147483648
    INT_MAX  =  2147483647
    NP_MAX   = 6

    def _write_i32(base, off, val):
        v = ct.c_int32(int(val))
        ct.memmove(ct.addressof(base) + off, ct.addressof(v), 4)
    def _write_f32(base, off, val):
        v = ct.c_float(float(val))
        ct.memmove(ct.addressof(base) + off, ct.addressof(v), 4)
    def _write_f64(base, off, val):
        v = ct.c_double(float(val))
        ct.memmove(ct.addressof(base) + off, ct.addressof(v), 8)

    # Field offsets inside SsaCond (must match session_engine.c layout exactly)
    # We use a ctypes Structure to compute them automatically.
    class SsaCond(ct.Structure):
        _fields_ = [
            ("ctype",      ct.c_int32),
            ("cond_idx",   ct.c_int32),
            ("parent_idx", ct.c_int32),
            ("x1",ct.c_int32),("z1",ct.c_int32),("x2",ct.c_int32),("z2",ct.c_int32),
            ("rmax",       ct.c_int32),
            ("min_count",  ct.c_int32),
            ("exclude",    ct.c_int32),
            ("struct_type",   ct.c_int32),
            ("variant_filter",ct.c_int32),
            ("step",          ct.c_int32),
            # biomes_incl[64]
            ("biomes_incl", ct.c_int32 * 64),
            ("n_incl",     ct.c_int32),
            # biomes_excl[64]
            ("biomes_excl", ct.c_int32 * 64),
            ("n_excl",     ct.c_int32),
            ("biome_scale",ct.c_int32),
            ("biome_dim",  ct.c_int32),
            ("biome_y",    ct.c_int32),
            ("biome_match_any", ct.c_int32),
            ("coverage",   ct.c_double),
            ("confidence", ct.c_double),
            ("biome_id",   ct.c_int32),
            ("biome_min_size",  ct.c_int32),
            ("biome_tolerance", ct.c_int32),
            ("climate_para",    ct.c_int32),
            ("climate_mode",    ct.c_int32),
            ("climate_vmin",    ct.c_double),
            ("climate_vmax",    ct.c_double),
            ("climate_nmax",    ct.c_int32),
            ("climate_invert",  ct.c_int32),
            ("cn_ok_lo",  ct.c_int32 * 6),
            ("cn_ok_hi",  ct.c_int32 * 6),
            ("cn_ex_lo",  ct.c_int32 * 6),
            ("cn_ex_hi",  ct.c_int32 * 6),
            ("height_ymin",  ct.c_float),
            ("height_ymax",  ct.c_float),
            ("height_invert",ct.c_int32),
            # Crunchiness fields
            ("crunch_climate",      ct.c_int32),
            ("crunch_octave_mask",  ct.c_uint32),
            ("crunch_weights",      ct.c_float * 12),
            ("crunch_mode",         ct.c_int32),
            ("crunch_threshold",    ct.c_float),
            ("skip_ref",            ct.c_int32),
            ("yield_samples",       ct.c_int32),
            ("dy_max",              ct.c_int32),     # (10/10 ter) height link to the parent
        ]

    # Verify size matches C
    if ct.sizeof(SsaCond) != sz:
        # Size mismatch — layout changed, fall back to Python
        return None

    arr = (SsaCond * n)()

    para_map = {"Temperature":0,"Humidity":1,"Continentalness":2,
                "Erosion":3,"Weirdness":5}

    for i, cond in enumerate(enabled):
        s = arr[i]
        t = cond.get("type", "")
        params = cond.get("params", {})

        ctype_int = ctype_map.get(t, -1)
        if ctype_int < 0:
            return None   # unknown type → use Python fallback

        # Linking joint scan_128 : un first_stronghold scan_128 AVEC un enfant
        # relié devient un CTYPE_STRONGHOLD (anneau) côté moteur C, pour qu'il
        # évalue l'enfant PAR stronghold (village près d'UN des 128), à vitesse C.
        # Le filtre yeux reste vérifié par le post-filtre Python (rare candidats).
        if (t == "first_stronghold" and _scan_128(params)
                and (_stronghold_extra_enabled(params) or _portal_anchor(params))):
            _my1 = i + 1
            if any(cc.get("relative", 0) == _my1
                   for j, cc in enumerate(enabled) if j != i):
                _sh_ct = ctype_map.get("stronghold")
                if _sh_ct is not None:
                    ctype_int = _sh_ct

        s.ctype      = ctype_int
        # cond_idx = 1-based position in the enabled array (i+1).
        # `relative` from cubiomes_session.py is already a 1-based position
        # in the original conditions list, remapped above to enabled positions.
        s.cond_idx   = i + 1
        s.parent_idx = cond.get("relative", 0)
        s.x1 = cond.get("x1", 0); s.z1 = cond.get("z1", 0)
        s.x2 = cond.get("x2", 0); s.z2 = cond.get("z2", 0)
        s.rmax       = cond.get("rmax", 0)
        if not s.rmax and cond.get("area_mode") == 0 and int(cond.get("radial", 0) or 0) > 0:
            s.rmax = int(cond.get("radial", 0)) + 1   # backward-compat (pre-rmax saves)

        # Nombre minimum d'instances dans la zone. La source canonique est le
        # champ "Instances within area" de l'éditeur, stocké dans cond["instances"]
        # (comme cubiomes). On retombe sur params["count"] pour compatibilité
        # avec d'anciennes sessions / le format hex importé.
        try:
            if "instances" in cond:
                min_count = int(cond.get("instances", 1))
            else:
                min_count = int(params.get("count", 1))
        except (TypeError, ValueError):
            min_count = 1
        s.min_count  = max(1, min_count) if min_count > 0 else 1
        s.exclude    = 1 if min_count <= 0 else 0
        s.skip_ref   = 1 if cond.get("ignore_ref") else 0
        # biome / noise sample « Yield individual samples » (cubiomes-viewer count == 1)
        s.yield_samples = 1 if (t in ("biome_sample", "noise_sample")
                                and params.get("yield_samples")) else 0
        _dy = dy_link(cond)
        s.dy_max = _dy + 1 if _dy is not None else 0          # C: limit + 1, 0 = none

        # struct_type
        s.struct_type = STRUCT_TYPES.get(t, 0)
        if t == "spawn_feature116":          # (09/10) kind: 0 dungeon, 1 lava lake
            s.struct_type = 1 if params.get("kind") == "lava_lake" else 0
            s.min_count, s.exclude = 1, 0
        if t in ("dungeon", "lava_lake"):     # (10/10) kind + lake variant + Y range
            s.struct_type = 1 if t == "lava_lake" else 0
            s.variant_filter = {"underground": 5, "surface": 6}.get(params.get("variant"), 0)
            ylo, yhi = feature116_yrange(params)
            s.height_ymin = ylo if ylo > 0 else -1e9
            s.height_ymax = yhi if yhi < 255 else 1e9

        # variant_filter (village abandoned, igloo basement)
        _variant_str = params.get("variant", "any")
        _variant_int = {"any":0, "abandoned":1, "not_abandoned":2,
                         "basement":3, "no_basement":4}.get(_variant_str, 0)
        s.variant_filter = _variant_int
        if t == "lava_lake":                  # (10/10) underground 5 / surface 6
            s.variant_filter = {"underground": 5, "surface": 6}.get(params.get("variant"), 0)
        elif t == "dungeon":                  # (10/10 ter) the exact terrain decides: no
            s.variant_filter = 0              # "below the approximate ground" any more

        # spiral step. Default 0 → C uses 512 (cubiomes-viewer convention).
        try:    s.step = int(params.get("step", 0) or 0)
        except: s.step = 0

        # biome lists
        def _fill_biomes(field, lst):
            n_b = min(len(lst), 64)
            for j in range(n_b): field[j] = int(lst[j])
            return n_b

        biomes_incl = params.get("biomes_incl", [])
        biomes_excl = params.get("biomes_excl", [])
        s.n_incl = _fill_biomes(s.biomes_incl, biomes_incl)
        s.n_excl = _fill_biomes(s.biomes_excl, biomes_excl)

        dim_str = params.get("dim", "overworld")
        s.biome_dim   = {"overworld":0,"nether":-1,"end":1}.get(dim_str,0)
        # Cubiomes-viewer convention: y=256 (real coord) which maps to r.y=64
        # (sea level) when scale=4. This is what monteCarloBiomes expects to
        # sample at the surface.
        s.biome_y     = params.get("y", 256)
        s.biome_match_any = (1 if params.get("match_any", False) else 0) \
            | (2 if params.get("approx", False) else 0)       # CV FLG_APPROX

        # Sampling step: "1/4 (fast)" → scale=4, "1/1 (precise)" → scale=1
        blocks_scale = params.get("blocks_scale", "1/4 (fast)")
        if blocks_scale.startswith("1/1"):
            s.biome_scale = 1
        else:
            s.biome_scale = params.get("scale", 4)

        # Coverage / confidence: percent UI → fraction for the C engine
        coverage_pct   = params.get("coverage_pct", None)
        confidence_pct = params.get("confidence_pct", None)
        # If percent values exist (Biome samples), use them; else fall back
        if confidence_pct is not None:
            try:
                conf = float(confidence_pct) / 100.0
            except (TypeError, ValueError):
                conf = 0.0
            # Clamp into a sane range
            if conf <= 0.0: conf = 0.5
            if conf >= 1.0: conf = 0.999
            s.confidence = conf
        else:
            s.confidence = params.get("confidence", 0.9)

        # Compute coverage: either from coverage_pct, or from blocks_count/area
        mode = params.get("mode", None)
        if isinstance(mode, str) and mode == "min_count":
            try:
                blocks_count = int(params.get("blocks_count", 0))
            except (TypeError, ValueError):
                blocks_count = 0
            area_blocks = max(1, (cond.get("x2", 0) - cond.get("x1", 0))
                                  * (cond.get("z2", 0) - cond.get("z1", 0)))
            cov = blocks_count / area_blocks if area_blocks > 0 else 0.0
            if cov < 0.0001: cov = 0.0001
            if cov > 1.0:    cov = 1.0
            s.coverage = cov
        elif coverage_pct is not None:
            try:
                cov = float(coverage_pct) / 100.0
            except (TypeError, ValueError):
                cov = 0.5
            if cov <= 0: cov = 0.0001
            if cov > 1: cov = 1.0
            s.coverage = cov
        else:
            s.coverage = params.get("coverage", 0.5)

        # biome id 0 is valid (ocean): before, `0 or 1` turned it into plains
        try:
            _bid = params.get("biome_id", 1)
            s.biome_id = int(1 if _bid is None or _bid == "" else _bid)
        except (TypeError, ValueError):
            s.biome_id = 1
        try:    s.biome_min_size  = int(params.get("min_size", 1) or 1)
        except: s.biome_min_size  = 1
        # tolerance 0 is valid (cubiomes-viewer's default): before, `0 or 4`
        # turned it into 4 → another sampling step in getBiomeCenters
        try:
            _tol = params.get("tolerance", 4)
            s.biome_tolerance = int(4 if _tol is None or _tol == "" else _tol)
        except (TypeError, ValueError):
            s.biome_tolerance = 4

        # climate_minmax / climate_center — climate conditions
        if t in ("climate_minmax", "climate_center", "climate_extreme",
                 "climate_noise", "noise_sample"):
            s.climate_para = para_map.get(params.get("para", "Temperature"), 0)

            # Read the range (tuple) if present, else fall back to vmin/vmax
            def _parse_bound(v, default_inf):
                """Parse a range endpoint. None/empty → ±inf; else float."""
                if v is None:
                    return default_inf
                if isinstance(v, str):
                    s = v.strip()
                    if s == "" or s in ("-Infinity", "+Infinity",
                                          "-inf", "+inf", "inf"):
                        return default_inf
                    try:
                        return float(s)
                    except ValueError:
                        return default_inf
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return default_inf

            rng = params.get("range")
            if isinstance(rng, (list, tuple)) and len(rng) == 2:
                vmin = _parse_bound(rng[0], float("-inf"))
                vmax = _parse_bound(rng[1], float("+inf"))
            else:
                # Legacy: separate vmin/vmax floats
                vmin = _parse_bound(params.get("vmin"), float("-inf"))
                vmax = _parse_bound(params.get("vmax"), float("+inf"))

            s.climate_vmin = vmin if vmin != float("-inf") else -1e30
            s.climate_vmax = vmax if vmax != float("+inf") else  1e30

            # Mode bitmask (bit 0=loc_min, bit 1=loc_max, bit 2=test_lower,
            # bit 3=test_upper). For climate_minmax we read the user choice;
            # for climate_center we derive from 'locate' and the range.
            if t == "noise_sample":
                # F_NOISE_SAMPLE (Monte-Carlo) : le mode sert seulement à
                # indiquer quelles bornes sont actives. monte_noise_eval teste
                # vmin < v < vmax sur chaque point ; monteCarloBiomes exige
                # qu'une fraction `coverage` satisfasse. Pas de locate min/max.
                mode = 0
                if vmin != float("-inf"):
                    mode |= 4   # E_TEST_LOWER (vmin actif)
                if vmax != float("+inf"):
                    mode |= 8   # E_TEST_UPPER (vmax actif)
                s.climate_mode = mode
            elif t == "climate_center" or t in ("climate_minmax", "climate_extreme"):
                # locate=0 → max, locate=1 → min
                # For climate_minmax the param is called "locate" (new UI) or
                # may still be a legacy "mode" integer from old saves — handle both.
                # (before: an imported mode=2 "locate max" became locate MIN)
                locate = climate_locate(params)

                mode = 0
                if locate == 0:  # max
                    mode |= 2     # E_LOCATE_MAX
                else:             # min
                    mode |= 1     # E_LOCATE_MIN
                # Activate range tests only when bounds are finite
                if vmin != float("-inf"):
                    mode |= 4   # E_TEST_LOWER
                if vmax != float("+inf"):
                    mode |= 8   # E_TEST_UPPER
                s.climate_mode = mode
            try:
                s.climate_nmax = int(params.get("nmax", -1))
            except (TypeError, ValueError):
                s.climate_nmax = -1
            if i in _cv_full_oct:               # cubiomes-viewer reads the full noise here
                s.climate_nmax = -1
            s.climate_invert = 1 if params.get("invert", False) else 0
        else:
            # Safe defaults for non-climate conditions (avoids crashes when
            # a "mode" param exists with a non-int value, e.g. biome_sample)
            s.climate_para   = 0
            s.climate_mode   = 0
            s.climate_vmin   = 0.0
            s.climate_vmax   = 0.0
            s.climate_nmax   = -1
            s.climate_invert = 0

        # climate noise ranges
        def _fill_np(field, lst):
            for j in range(min(len(lst),6)): field[j] = int(lst[j])
        _fill_np(s.cn_ok_lo, params.get("ok_lo",[INT_MIN]*NP_MAX))
        _fill_np(s.cn_ok_hi, params.get("ok_hi",[INT_MAX]*NP_MAX))
        _fill_np(s.cn_ex_lo, params.get("ex_lo",[INT_MIN]*NP_MAX))
        _fill_np(s.cn_ex_hi, params.get("ex_hi",[INT_MAX]*NP_MAX))
        # ── Crunchiness ──
        if t == "crunchiness":
            # Climate: stored as int (NP_TEMPERATURE=0, NP_HUMIDITY=1,
            # NP_CONTINENTALNESS=2, NP_EROSION=3, NP_WEIRDNESS=5)
            try:
                s.crunch_climate = int(params.get("climate", 2))
            except (TypeError, ValueError):
                s.crunch_climate = 2

            # Build octave mask. A: bits 0..5, B: bits 16..21
            octs = params.get("octaves", {}) or {}
            mask = 0
            for i in range(6):
                if octs.get(f"{i}A"): mask |= (1 << i)
                if octs.get(f"{i}B"): mask |= (1 << (16 + i))
            s.crunch_octave_mask = mask

            # Build weights: indices 2*i = A, 2*i+1 = B
            # Note: even if user picks Custom, we always pass actual weights
            # to the C engine, not "Equal" label.
            weighting = params.get("weighting", "Equal")
            custom_w = params.get("custom_weights", {}) or {}
            weights = [0.0] * 12
            checked_slots = []
            for i in range(6):
                for sub_i, s_letter in enumerate("AB"):
                    slot = f"{i}{s_letter}"
                    if octs.get(slot):
                        checked_slots.append((slot, 2*i + sub_i))

            if not checked_slots:
                pass  # all zeros → score always 0 → seed passes if mode=crunchy
            elif weighting.startswith("Equal"):
                w = 1.0 / len(checked_slots)
                for slot, idx in checked_slots:
                    weights[idx] = w
            elif weighting.startswith("Commission"):
                cm = {"0A": 0.35, "0B": 0.35,
                       "1A": 0.11, "1B": 0.11,
                       "2A": 0.04, "2B": 0.04}
                for slot, idx in checked_slots:
                    weights[idx] = cm.get(slot, 0.0)
            elif weighting.startswith("First octave"):
                # 0.4 on 0A, 0.4 on 0B if checked, rest spread
                first_w = {"0A": 0.4, "0B": 0.4}
                rest_idxs = [(s, i) for s, i in checked_slots
                              if s not in first_w]
                rest_w = (1.0 - sum(first_w.get(s, 0.0) for s, _ in checked_slots
                                      if s in first_w)) / max(1, len(rest_idxs))
                for slot, idx in checked_slots:
                    weights[idx] = first_w.get(slot, rest_w if rest_idxs else 0.0)
            elif weighting.startswith("Custom"):
                for slot, idx in checked_slots:
                    try:
                        weights[idx] = float(custom_w.get(slot, 0.0))
                    except (TypeError, ValueError):
                        weights[idx] = 0.0
            else:
                # Default to Equal
                w = 1.0 / len(checked_slots)
                for slot, idx in checked_slots:
                    weights[idx] = w

            for i, w in enumerate(weights):
                s.crunch_weights[i] = w

            # Mode (0 = crunchy / below, 1 = bland / above)
            try:
                s.crunch_mode = int(params.get("mode", 0))
            except (TypeError, ValueError):
                s.crunch_mode = 0
            # Threshold
            try:
                s.crunch_threshold = float(params.get("threshold", 0.10))
            except (TypeError, ValueError):
                s.crunch_threshold = 0.10
        else:
            s.crunch_climate     = 0
            s.crunch_octave_mask = 0
            for i in range(12):
                s.crunch_weights[i] = 0.0
            s.crunch_mode = 0
            s.crunch_threshold = 0.0


        # height — only populated for the height condition
        if t == "height":
            try:
                ymin = float(params.get("y_min", 0))
            except (TypeError, ValueError):
                ymin = 0.0
            try:
                ymax = float(params.get("y_max", 0))
            except (TypeError, ValueError):
                ymax = 0.0
            # -64 / 320 = the world limits = no bound (an imported cubiomes-viewer
            # condition without bound is stored as -64 / 320, 01/10)
            s.height_ymin   = ymin if ymin > -64 else -1e9
            s.height_ymax   = ymax if ymax < 320 else  1e9
            s.height_invert = 1 if params.get("invert", False) else 0
        elif t not in ("dungeon", "lava_lake"):      # (10/10) set above for those
            s.height_ymin   = 0.0
            s.height_ymax   = 0.0
            s.height_invert = 0

        # Locate climate center — coarse flood fill (01/10). Fields reused:
        # biome_scale = cell size in blocks, biome_min_size / biome_tolerance =
        # min / max region size in cells (0 = no max), min_count = 1 when the
        # region's centre is needed (children, or the Python path asking for
        # the position), climate_mode = range bounds only (no locate).
        if t == "climate_center":
            try:
                s.biome_scale = max(4, min(1024, int(params.get("scale", 16) or 16)))
            except (TypeError, ValueError):
                s.biome_scale = 16
            try:
                s.biome_min_size = max(1, int(params.get("min_size", 1) or 1))
            except (TypeError, ValueError):
                s.biome_min_size = 1
            try:
                s.biome_tolerance = max(0, int(params.get("max_size", 0) or 0))
            except (TypeError, ValueError):
                s.biome_tolerance = 0
            _me = int(s.cond_idx)
            _kids = any(int(o.get("relative", 0) or 0) == _me for o in enabled)
            s.min_count = 1 if (_kids or params.get("_need_center")) else 0
            s.exclude = 0
            s.climate_mode = (4 if s.climate_vmin > -1e29 else 0) | (8 if s.climate_vmax < 1e29 else 0)

    return arr, n


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_seed(conditions: List[dict], seed: int, mc_version: int,
                   lib: CubiomesLib) -> Tuple[bool, str]:
    """Evaluate all enabled conditions for one seed (Python path)."""
    conditions = active_conditions(conditions)
    enabled_indices = list(range(len(conditions)))
    if not enabled_indices:
        return (False, "")
    ctx = EvalContext(lib, mc_version, seed)
    top_level = [i + 1 for i in enabled_indices
                 if conditions[i].get("relative", 0) == 0]
    for cond_1idx in top_level:
        if not _eval_node(ctx, conditions, cond_1idx, (0, 0)):
            return (False, "")
    parts = []
    for i in enabled_indices[:4]:
        p = ctx.positions[i + 1] if (i + 1) < len(ctx.positions) else None
        if p is not None:
            parts.append(f"{conditions[i].get('type','?')}@({p[0]},{p[1]})")
    return (True, "  ".join(parts))


def _structure_type_keys():
    """Set of condition 'type' keys that are structures (GPU-eligible subset)."""
    return set(STRUCT_TYPES.keys()) | {"dungeon", "lava_lake"}     # (10/10) 1.16 features


# ═════════════════════════════════════════════════════════════════════════════
#  Biome GPU prefilter (1.18+ noise) — task #71
#  The 1.18+ noise biome generator ported to CUDA (FP32, recall-safe). The GPU
#  counts grid samples in the target biomes per seed; the CPU validates EVERY
#  survivor with the exact condition, so results are always exact — the GPU only
#  accelerates. Validated: GPU FP32 biome == cubiomes 99.9997%, FP32 scan count
#  within 1 of FP64 (=> margin makes it recall-safe). See noise_gpu.py.
# ═════════════════════════════════════════════════════════════════════════════

def _parallel_validate(raw, arr, n_conds, mc, large, seeds_i64,
                       num_workers, stop_event, on_match):
    """Validate GPU survivors with the C engine across N threads (ctypes releases
    the GIL during ssa_test_seeds_batch -> true parallelism). A one-time warm-up
    builds g_child_order single-threaded; the worker threads then only READ it
    (build_child_order is idempotent), so it's thread-safe. Matches are emitted
    on the calling thread. Uses every thread the user chose."""
    import ctypes as ct, numpy as _np, concurrent.futures as _cf
    if seeds_i64 is None or len(seeds_i64) == 0:
        return
    seeds_i64 = _np.ascontiguousarray(seeds_i64, _np.int64)
    nn = len(seeds_i64)
    nw = max(1, int(num_workers or 1))

    def _run_batch(chunk):
        chunk = _np.ascontiguousarray(chunk, _np.int64)
        m = len(chunk)
        if m == 0:
            return _np.empty(0, _np.int64)
        out = _np.empty(m, _np.int64)
        kept = _tsb(raw, 
            arr, ct.c_int(n_conds), ct.c_int(mc), ct.c_int(large),
            chunk.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(m),
            out.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(m))
        return out[:int(kept)]

    if nw == 1 or nn < 512:
        for s in _run_batch(seeds_i64).tolist():
            if stop_event.is_set():
                break
            on_match(int(s), "")
        return
    # warm-up: build g_child_order once (single-threaded) so threads only read it
    _run_batch(seeds_i64[:1])
    chunks = _np.array_split(seeds_i64, nw)
    with _cf.ThreadPoolExecutor(max_workers=nw) as ex:
        for res in ex.map(_run_batch, chunks):
            for s in res.tolist():
                if stop_event.is_set():
                    break
                on_match(int(s), "")



# ── statistics of the GPU pipelines (01/10) ─────────────────────────────────
# The GPU pre-filters validate their survivors with ssa_test_seeds_batch, which
# kept no per-condition counters: those sessions had no stats at all. While a
# search with on_profile runs, _GPU_PROF holds a counter buffer (the layout of
# ssa_engine_profile2) that the validators fill through _tsb(), plus what the
# GPU itself filtered out, credited to the pre-filtered condition.
_GPU_PROF = {"buf": None, "n": 0, "pos": None, "scanned": 0, "kept": 0,
             "lock": threading.Lock()}


def _tsb(raw, arr, n, mc, large, pin, nin, pout, cap):
    """ssa_test_seeds_batch, counted into the running search's statistics."""
    buf = _GPU_PROF["buf"]
    nn = int(getattr(n, "value", n))
    f = getattr(raw, "ssa_test_seeds_batch_prof", None)
    if buf is not None and f is not None and nn == _GPU_PROF["n"]:
        f.restype = ctypes.c_int64
        f.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int64),
                      ctypes.c_int64, ctypes.POINTER(ctypes.c_int64), ctypes.c_int64, ctypes.POINTER(ctypes.c_int64)]
        return f(arr, nn, mc, large, pin, nin, pout, cap, buf)
    return raw.ssa_test_seeds_batch(arr, n, mc, large, pin, nin, pout, cap)


def _gpu_prof_target(enabled, cond):
    """The condition a GPU pipeline pre-filters (its position in the C array)."""
    _GPU_PROF["pos"] = next((i for i, c in enumerate(enabled) if c is cond), None)


def _gpu_prof_note(scanned, kept):
    with _GPU_PROF["lock"]:
        _GPU_PROF["scanned"] += int(scanned)
        _GPU_PROF["kept"] += int(kept)


def _gpu_prof_rows():
    """(rows, total) like the engine's on_profile, or None (nothing counted)."""
    buf, n = _GPU_PROF["buf"], _GPU_PROF["n"]
    if buf is None or n <= 0:
        return None
    rows = [[int(buf[5 * i + j]) for j in range(5)] for i in range(n)]
    pos = _GPU_PROF["pos"]
    with _GPU_PROF["lock"]:
        dropped = _GPU_PROF["scanned"] - _GPU_PROF["kept"]
    if pos is not None and 0 <= pos < n and dropped > 0:
        # the seeds the GPU rejected reached this condition and failed its test
        rows[pos][0] += dropped
        rows[pos][3] += dropped
    if not any(r[0] for r in rows):
        return None
    return [tuple(r) for r in rows], int(buf[5 * n])


def _hybrid_loop(gpu_scan, cpu_test, warmup, seed_start, total, num_workers,
                 stop_event, on_progress, gpu_chunk=1 << 17, cpu_chunk=1 << 14):
    """Adaptive CPU+GPU co-processing over one seed range (work-stealing).

    A single GPU thread pulls big chunks, runs the FP32 prefilter, and pushes the
    survivors onto a bounded queue. The CPU worker threads PRIORITISE validating
    those GPU survivors (complementary: the CPU does the exact checks the GPU
    can't); only when that queue is empty do they pull their own raw chunks and
    full-test them on the C engine (additive: idle CPU adds throughput). This
    auto-adapts per condition:
      * slow GPU / few survivors (biome_sample)  -> CPU mostly full-tests extra
        seeds            => throughput ~ GPU + CPU
      * fast GPU / many survivors (climate)      -> CPU mostly validates the GPU
        output           => GPU-bound, stays exact
    Each seed is processed exactly once (GPU and CPU pull DISJOINT chunks from the
    same dispenser). Results are identical to a pure-CPU search; only speed
    changes. The GPU thread never validates, so it stays ~100% busy; the only hot
    CPU threads are the `num_workers` validators (ed's 12-thread CPU rule). If the
    GPU fails mid-run, its current chunk is handed to the CPU and the CPU workers
    finish the whole range -> still correct, just CPU-speed."""
    import threading, queue as _q, concurrent.futures as _cf, numpy as _np
    warmup()                       # build g_child_order single-threaded (then thread-safe)
    # Pause / Resume (08/10): a chunk counts once ALL its seeds are checked (its
    # GPU survivors validated, or its raw seeds full-tested) without a Stop in
    # between; the resume point is the first seed not covered that way.
    _rcb = getattr(_HYB_TLS, "on_resume", None)
    trk = tcb = None
    if _rcb is not None:
        from broken_search import ResumeTracker
        trk = ResumeTracker(seed_start, ["h"])
        tcb = trk.chunk_cb("h")
        _rmode = getattr(_HYB_TLS, "on_mode", None)
        if _rmode is not None:
            try: _rmode(("resumable", True))
            except Exception: pass
    nw = max(1, int(num_workers or 0))   # all chosen threads (>=1 validator)
    take_lock = threading.Lock(); prog_lock = threading.Lock()
    st = {"next": int(seed_start), "end": int(seed_start) + int(total), "done": 0}
    survq = _q.Queue(maxsize=2 * nw + 4)          # bounded -> backpressure throttles GPU
    gpu_done = threading.Event()

    def _take(chunk):
        with take_lock:
            s = st["next"]
            if s >= st["end"]:
                return None
            n = min(chunk, st["end"] - s); st["next"] = s + n
            return (s, n)

    def _bump(s0, n):
        if tcb is not None and not stop_event.is_set():
            tcb(s0, n)
        with prog_lock:
            st["done"] += n; d = st["done"]
        on_progress(min(d, total), total)

    def _put(surv):                                # stop-aware blocking put
        while not stop_event.is_set():
            try:
                survq.put(surv, timeout=0.1); return
            except _q.Full:
                continue

    # chunk sizes ADAPT to ~0.3 s (CPU) / ~0.5 s (GPU) per piece: slow sessions
    # (biomes: ms per seed) used to wait for 16 384 seeds per CPU piece —
    # tens of seconds without any progress, so the engine analysis saw no
    # speed at all and showed "GPU not engaged" although the GPU was running.
    import time as _time
    # the GPU starts with SMALL pieces too: one piece of 16 384 seeds on a large
    # biome grid took minutes, and Stop / Pause waited for it (seen 27/09)
    gsz = {"n": 64}

    def _gpu_worker():
        try:
            while not stop_event.is_set():
                c = _take(gsz["n"])
                if c is None:
                    break
                _t0 = _time.perf_counter()
                try:
                    surv = gpu_scan(c[0], c[1])
                except Exception:
                    import traceback; traceback.print_exc(); surv = None
                if surv is None:                   # GPU failed/raised -> hand raw chunk to CPU
                    _put((_seed_arange(c[0], c[1]), [1, c[1], c[0]]))
                    break
                _gpu_prof_note(c[1], len(surv))                          # stop the GPU; CPU finishes the rest
                _dt = _time.perf_counter() - _t0
                # a chunk also has to be VALIDATED quickly: it counts as searched
                # only once its survivors are checked, and 8 M seeds with 1 000+
                # costly survivors came as one lump every second or two → the
                # rate swung between 0 and peaks (MIRRORS V6, 01/10). At most
                # about one piece (~0.3 s) per validator per chunk.
                _cap = max(1, nw) * max(8, int(vcost["piece"]))
                if len(surv) > _cap:
                    gsz["n"] = max(16, int(c[1] * _cap / len(surv)))
                elif _dt < 0.25 and gsz["n"] < (1 << 23) and len(surv) * 2 <= _cap:
                    gsz["n"] *= 2
                elif _dt > 1.0 and gsz["n"] > 16:
                    gsz["n"] = max(16, int(gsz["n"] * 0.5 / _dt))
                if len(surv):
                    # handed out in pieces the validators finish in ~0.3 s: one
                    # array of 200 000 survivors (a fast GPU, a loose pre-filter)
                    # went to ONE validator thread for minutes, and Stop waited
                    # for it (biome sample bench, 27/09)
                    step = max(8, int(vcost["piece"]))
                    # the chunk counts as searched once its LAST piece is
                    # validated: counted when queued, the rate showed the GPU
                    # speed (500 M/s), then 0 while the queue drained, and a
                    # Stop counted seeds never checked (REMIRRORS V6, 01/10)
                    rec = [-(-len(surv) // step), c[1], c[0]]
                    for _o in range(0, len(surv), step):
                        _put((surv[_o:_o + step], rec))   # blocks if full (backpressure)
                else:
                    _bump(c[0], c[1])
        finally:
            gpu_done.set()

    vcost = {"piece": 64}                          # survivors per validation piece (~0.3 s)

    def _safe_test(item):                          # a bad chunk must not kill a validator
        if isinstance(item, tuple):                # (survivors, [pieces left, chunk size])
            seeds, rec = item
        else:
            seeds, rec = item, None
        _t = _time.perf_counter()
        try:                                       # (would shrink the pool & could stall
            cpu_test(seeds)                        # the GPU on a full queue in long runs)
        except Exception:
            import traceback; traceback.print_exc()
        _d = _time.perf_counter() - _t
        if len(seeds) >= 8 and _d > 0.01:          # measured validation cost per seed
            vcost["piece"] = max(8, min(1 << 16, int(0.3 * len(seeds) / _d)))
        if rec is not None and not stop_event.is_set():
            with prog_lock:
                rec[0] -= 1
                last = rec[0] == 0
            if last:
                _bump(rec[2], rec[1])

    def _cpu_worker():
        csz = 64                                   # per worker, adapts to ~0.3 s
        while not stop_event.is_set():
            try:
                surv = survq.get_nowait()          # priority: validate GPU output
            except _q.Empty:
                surv = None
            if surv is not None:
                _safe_test(surv)
                continue
            c = _take(csz)                         # else: full-test a raw chunk
            if c is not None:
                _t0 = _time.perf_counter()
                _safe_test(_seed_arange(c[0], c[1]))
                _dt = _time.perf_counter() - _t0
                if _dt < 0.15 and csz < int(cpu_chunk):
                    csz *= 2
                elif _dt > 0.6 and csz > 16:
                    csz //= 2
                _bump(c[0], c[1])
                continue
            # no raw chunks left to steal
            if gpu_done.is_set():
                try:
                    surv = survq.get_nowait()
                except _q.Empty:
                    break                          # GPU done + queue drained -> finished
                _safe_test(surv)
            else:
                try:
                    item = survq.get(timeout=0.05)      # wait for GPU stragglers
                except _q.Empty:
                    continue
                _safe_test(item)

    with _cf.ThreadPoolExecutor(max_workers=nw + 1) as ex:
        futs = [ex.submit(_gpu_worker)] + [ex.submit(_cpu_worker) for _ in range(nw)]
        for f in futs:
            f.result()
    if trk is not None and trk.used:
        try:
            _rcb(trk.point())
        except Exception:
            pass
    return True


# Largest scale-4 grid a GPU pre-filter scans per seed (4096 × 4096 blocks). Bigger
# grids are slower per seed, but the engine analysis MEASURES and keeps the CPU
# when the GPU does not win (before: 1<<16 = 1024 × 1024 blocks → most biome /
# climate sessions were silently refused → "GPU not engaged").
GPU_GRID_CELLS = 1 << 20
_GPU_DECLINE = {}                      # thread id -> why the last GPU pipeline declined


def _gpu_decline(why):
    """A GPU pipeline refuses this session (→ the C engine runs instead); the
    reason is reported to the engine analysis ('GPU not engaged: …')."""
    _GPU_DECLINE[threading.get_ident()] = why
    return False


def _biome_gpu_eligible(enabled):
    """Return a biome_sample condition to GPU-prefilter on, or None.
    Eligible = a biome_sample condition with use_gpu=True and a non-empty include
    list. (Other conditions are still CPU-validated on every survivor.)"""
    for c in enabled:
        if (c.get("type") == "biome_sample"
                and c.get("relative", 0) == 0          # top-level AND only: a child/
                and c.get("params", {}).get("use_gpu") # linked condition has a DYNAMIC
                and c.get("params", {}).get("biomes_incl")):  # area (parent-relative) ->
            return c                                   # fixed-box GPU prefilter is unsafe
    return None


def _mc_wilson(n, p, z):
    """cubiomes finders.c wilson(), bit for bit (FLT_EPSILON included)."""
    s = z * z / n
    t = 1.0 / (1.0 + s)
    w = t * (p + 0.5 * s)
    d = t * z * math.sqrt((p * (1.0 - p) + 0.25 * s) / n) + 1.1920928955078125e-07
    return w - d, w + d


def monte_prefix_plan(sc):
    """GPU prefilter plan for a biome_sample, from its C SsaCond `sc` (the same
    values the C engine uses). cubiomes' monteCarloBiomes draws its sample cells
    from a Java RNG seeded with the world seed — the positions depend on the seed
    only, never on the biomes. It can only accept after at least one success,
    and with zero successes it always stops (rejects) after the same number m0
    of counted samples. So: evaluate the SAME first m0 cells as the CPU; no
    required biome among them -> the CPU rejects the seed too (exact, not a
    grid estimate). Returns (plan dict, None) or (None, reason)."""
    import statistics
    scale = sc.biome_scale if sc.biome_scale > 0 else 4
    if scale != 4:
        return None, "only the 1:4 sampling scale is computed by the GPU"
    cov, conf = float(sc.coverage), float(sc.confidence)
    if not (0.0 < conf < 1.0) or not (0.0 < cov <= 1.0):
        return None, "coverage / confidence out of range"
    if sc.rmax > 0:
        rb = int(sc.rmax) - 1
        x1, z1, x2, z2 = -rb, -rb, rb, rb
        rmax_sq = rb * rb + 1
    else:
        x1, z1, x2, z2 = int(sc.x1), int(sc.z1), int(sc.x2), int(sc.z2)
        rmax_sq = 0
    rx, rz = x1 >> 2, z1 >> 2
    sx = max(1, (x2 >> 2) - rx + 1)
    sz = max(1, (z2 >> 2) - rz + 1)
    n = sx * sz
    z = statistics.NormalDist().inv_cdf((1.0 + conf) / 2.0)    # sqrt(2)·erfinv(conf)
    wn = z * math.sqrt(n)
    if n < 4 * wn:
        return None, ("small area: the C engine samples it without repeats "
                      "(a shuffled list the GPU does not reproduce)")
    wlo, whi = _mc_wilson(wn, cov, z)
    m0 = None
    for m in range(1, n + 1):
        per = 1.0 / m
        lo, hi = _mc_wilson(float(m), 0.0, z)
        if hi + per < cov or hi - lo < whi - wlo:
            m0 = m
            break
    if m0 is None:
        m0 = n
    m0 += 4 + m0 // 50          # margin: inverf() rounding in C vs Python
    if m0 > 4096:                # NG_MC_MAXCELLS (noise_gpu.cu): cells kept in shared memory
        return None, "the coverage asked needs too many samples for the GPU filter"
    if sx > 65536 or sz > 65536:
        return None, "area too large for the GPU filter"
    return {"rx": rx, "rz": rz, "sx": sx, "sz": sz, "y": int(sc.biome_y) >> 2,
            "rmax_sq": rmax_sq, "m0": m0, "max_iter": 8 * m0 + 4096}, None


def monte_prefix_emul(seed, plan, biome_at, incl):
    """CPU reference of the GPU kernel (tests): True = the seed is kept."""
    M = (1 << 48) - 1
    st = [(seed ^ 0x5DEECE66D) & M]

    def nxt(bits):
        st[0] = (st[0] * 0x5DEECE66D + 0xB) & M
        return st[0] >> (48 - bits)         # bits <= 31: always >= 0

    def nint(nn):
        m = nn - 1
        if (m & nn) == 0:
            return (nn * nxt(31)) >> 31
        while True:
            bits = nxt(31)
            val = bits % nn
            if ((bits - val + m) & 0xFFFFFFFF) < 0x80000000:
                return val
    cnt = it = 0
    while cnt < plan["m0"]:
        if it >= plan["max_iter"]:
            return True
        tx = nint(plan["sx"]); nint(1); tz = nint(plan["sz"]); it += 1
        X, Z = plan["rx"] + tx, plan["rz"] + tz
        if plan["rmax_sq"] and (X * 4) ** 2 + (Z * 4) ** 2 >= plan["rmax_sq"]:
            continue
        cnt += 1
        if biome_at(X, plan["y"], Z) in incl:
            return True
    return False


def _run_biome_gpu_search(enabled, bcond, seed_start, seed_count, mc_version,
                          lib, stop_event, on_match, on_progress, num_workers=0):
    """GPU FP32 noise-biome prefilter (1.18+), double-buffered: the GPU scans
    batch N+1 (counting grid samples in biomes_incl, FP32) while the C engine
    validates batch N's survivors with the EXACT condition tree (ssa_test_seeds_
    batch) — identical results to the normal search, just faster. The GPU only
    prunes; correctness comes from the C validation. Returns True if it ran on
    the GPU, False to fall back to the CPU engine."""
    import ctypes as ct
    try:
        import noise_gpu as _ng
        import numpy as _np
    except Exception:
        return _gpu_decline("the noise_gpu module could not be imported")
    if not _ng.available() or not _ng.supported_version(mc_version):
        return _gpu_decline("noise_gpu is not available (no CUDA library) or does not support this version")

    params = bcond.get("params", {})
    incl = params.get("biomes_incl", [])
    if not incl:
        return _gpu_decline("no required biome to look for")
    # GPU grid samples at scale 4; the "precise" (1/1) sampling mode checks every
    # block, so stay recall-safe by leaving that case to the CPU engine.
    if str(params.get("blocks_scale", "")).startswith("1/1"):
        return _gpu_decline("1:1 block scale is checked on the CPU")

    # Full condition tree for exact C validation (same engine as the normal search)
    raw = lib._lib
    res = _build_ssacond_array(enabled, raw)
    if res is None:
        return _gpu_decline("this session's tree is not supported by the C engine")
    arr, n_conds = res
    _gpu_prof_target(enabled, bcond)
    try:
        raw.ssa_test_seeds_batch.argtypes = [
            ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
            ct.POINTER(ct.c_int64), ct.c_int64,
            ct.POINTER(ct.c_int64), ct.c_int64]
        raw.ssa_test_seeds_batch.restype = ct.c_int64
    except Exception:
        return _gpu_decline("the C engine is too old (rebuild libcubiomes)")
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0

    if large:
        return _gpu_decline("large biomes are not computed by the GPU (C engine on the CPU)")
    # The SAME sample cells as the C engine's monteCarloBiomes (seeded by the
    # world seed) — exact, instead of the old 21×21 grid at y=64 that lost 7.5 %
    # of the valid seeds (cherry 1 % in r1000, RTX bench 27/09): a small patch
    # between two grid points, and the condition's y (default 256) ignored.
    if os.environ.get("SSA_GPU_BIOMEMC") == "0":      # manual off switch
        return _gpu_decline("biome samples on the GPU switched off (SSA_GPU_BIOMEMC=0)")
    if not _ng.has_biomemc():
        return _gpu_decline("the GPU library is too old for biome samples (rebuild: "
                            "py tools/build_native.py --unit noise_gpu)")
    try:
        _one = _build_ssacond_array([dict(bcond, relative=0)], raw)
        plan, why = monte_prefix_plan(_one[0][0]) if _one else (None, "C engine unavailable")
    except Exception as e:
        plan, why = None, f"{type(e).__name__}: {e}"
    if plan is None:
        return _gpu_decline(why)
    mask = _ng.allowed_bitmask(incl)

    def _scan(ss, n):
        seeds = _seed_arange(ss, n)
        keep = _ng.biomemc(mc_version, seeds, plan, mask)
        if keep is None:
            return None
        return seeds[keep != 0]

    import threading as _th                    # the hybrid runs N validators -> make
    _emit_lock = _th.Lock(); _emit_raw = on_match    # on_match thread-safe (rare calls)
    def on_match(_s, _summary=""):
        with _emit_lock:
            _emit_raw(_s, _summary)

    def _validate(seeds_i64):                  # single-thread C validation + emit;
        if seeds_i64 is None or len(seeds_i64) == 0:   # the hybrid loop runs N of
            return                             # these in parallel + 1 GPU scan thread
        seeds_i64 = _np.ascontiguousarray(seeds_i64, _np.int64)
        nn = len(seeds_i64)
        out = _np.empty(nn, _np.int64)
        kept = _tsb(raw, 
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            seeds_i64.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn),
            out.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn))
        for k in range(int(kept)):
            if stop_event.is_set():
                break
            on_match(int(out[k]), "")

    def _warmup():                             # build g_child_order single-threaded
        s0 = _np.zeros(1, _np.int64); o0 = _np.empty(1, _np.int64)
        raw.ssa_test_seeds_batch(
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            s0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1),
            o0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1))

    total = seed_count if seed_count > 0 else (1 << 48)
    return _hybrid_loop(_scan, _validate, _warmup, seed_start, total,
                        num_workers, stop_event, on_progress)


# ═════════════════════════════════════════════════════════════════════════════
#  Climate GPU prefilter (1.18+ noise) — "Locate climate extreme" / climate min-max
#  The GPU computes the min and max of the chosen climate parameter over the area
#  (raw position, no shift — matches cubiomes sampleClimatePara). A seed can only
#  pass if the area's [min,max] overlaps the test range [vmin,vmax] (necessary for
#  the located extreme to be in range, for ANY locate mode) -> recall-safe prefilter.
#  The C engine validates survivors exactly. Validated: GPU min/max == cubiomes
#  within <1 unit (x10000). ~1.2 G samples/s.
# ═════════════════════════════════════════════════════════════════════════════

_CLIMATE_PARA = {"Temperature": 0, "Humidity": 1, "Continentalness": 2,
                 "Erosion": 3, "Weirdness": 5}


def _climate_gpu_eligible(enabled):
    """Return a climate_minmax / climate_center condition to GPU-prefilter, or None.
    Both map to the same CTYPE_CLIMATE_MINMAX C backend (locate min/max position +
    test value vs [vmin,vmax]); the overlap prefilter is recall-safe for either."""
    for c in enabled:
        p = c.get("params", {})
        if (c.get("type") == "climate_minmax"      # not climate_center: a flood fill
                and (c.get("relative", 0) == 0 or p.get("gpu_box"))   # child: only with its
                                                      # zone englobante (gpu_planner)
                and p.get("use_gpu")):   # limited octaves too (noise_gpu climatescan2)
            return c
    return None


def _gpu_nmax(enabled, cond, arr):
    """Octave limit the C engine really uses for `cond` (its SsaCond, where a
    cubiomes-viewer full-octave read is already applied), -1 = all octaves."""
    for i, c in enumerate(enabled):
        if c is cond:
            try:
                v = int(arr[i].climate_nmax)
                return v if v > 0 else -1
            except Exception:
                break
    try:
        v = int(cond.get("params", {}).get("nmax", -1))
        return v if v > 0 else -1
    except (TypeError, ValueError):
        return -1


def _run_climate_gpu_search(enabled, ccond, seed_start, seed_count, mc_version,
                            lib, stop_event, on_match, on_progress, num_workers=0):
    """GPU climate min/max prefilter, double-buffered + exact C validation."""
    import ctypes as ct
    try:
        import noise_gpu as _ng
        import numpy as _np
    except Exception:
        return _gpu_decline("the noise_gpu module could not be imported")
    if not _ng.available() or not _ng.supported_version(mc_version):
        return _gpu_decline("noise_gpu is not available (no CUDA library) or does not support this version")

    params = ccond.get("params", {})
    para = _CLIMATE_PARA.get(params.get("para", "Temperature"), 0)

    # range -> x10000 bounds (None / ±inf = unconstrained)
    def _bound(v):
        if v is None:
            return None
        try:
            s = str(v).strip()
            if s == "" or "inf" in s.lower():
                return None
            return float(s)
        except (TypeError, ValueError):
            return None
    rng = params.get("range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        vmin_raw, vmax_raw = _bound(rng[0]), _bound(rng[1])
    else:
        vmin_raw, vmax_raw = _bound(params.get("vmin")), _bound(params.get("vmax"))
    if vmin_raw is None and vmax_raw is None:
        return _gpu_decline("no bound to filter on")    # nothing to filter on -> let CPU handle
    invert = bool(params.get("invert", False))
    MARGIN = 3          # x10000 units, recall-safe (FP32 err < 1)
    # The range is ALREADY in the x10000 domain (same as the GPU min/max output
    # and the C engine's climate_vmin, set with no scaling). Do NOT multiply by
    # 10000 again — that was a recall bug: on INVERTED conditions it pushed the
    # bounds to +-1.7e8 so the keepmask dropped EVERY seed in GPU-scanned chunks.
    vmin10k = vmin_raw if vmin_raw is not None else None
    vmax10k = vmax_raw if vmax_raw is not None else None

    def _keepmask(mn, mx):
        if not invert:
            # pass requires the located extreme in [vmin,vmax] -> area [min,max]
            # must overlap [vmin,vmax] (necessary cond, any locate mode).
            lo = (vmin10k - MARGIN) if vmin10k is not None else -1e18
            hi = (vmax10k + MARGIN) if vmax10k is not None else 1e18
            return (mx >= lo) & (mn <= hi)
        # invert: pass requires the located extreme OUTSIDE [vmin,vmax] -> some
        # sample below vmin or above vmax (necessary cond).
        m = (mn < -1e17)   # all-False array of the right shape
        if vmin10k is not None:
            m = m | (mn < vmin10k + MARGIN)
        if vmax10k is not None:
            m = m | (mx > vmax10k - MARGIN)
        return m

    # root box -> full scale-4 grid (matches cubiomes; subsampling would be recall-unsafe)
    rmax = ccond.get("rmax", 0)
    if rmax and rmax > 0:
        rb = rmax - 1; x1, z1, x2, z2 = -rb, -rb, rb, rb
    else:
        x1 = ccond.get("x1", -2048); z1 = ccond.get("z1", -2048)
        x2 = ccond.get("x2",  2048); z2 = ccond.get("z2",  2048)
    _gb = params.get("gpu_box")          # zone englobante of a DEPENDENT condition
    if _gb:                              # (gpu_planner): the union of every place its
        x1, z1, x2, z2 = (int(v) for v in _gb)   # area can be — a necessary test
    if x2 < x1: x1, x2 = x2, x1
    if z2 < z1: z1, z2 = z2, z1
    rx1, rz1 = x1 >> 2, z1 >> 2
    # cubiomes-viewer / C engine extent: (x2>>2)-(x1>>2)+1 (was up to one cell
    # short → a necessary pre-filter could miss the last row / column)
    rw, rh = max(1, (x2 >> 2) - rx1 + 1), max(1, (z2 >> 2) - rz1 + 1)
    if rw * rh > GPU_GRID_CELLS:   # widened zones (planner measures)      # area too large for GPU grid -> CPU (stays exact)
        return _gpu_decline(f"its area ({rw}×{rh} cells of 4 blocks) is above the GPU grid limit")
    gx = _np.repeat(_np.arange(rx1, rx1 + rw, dtype=_np.int32), rh)
    gz = _np.tile(_np.arange(rz1, rz1 + rh, dtype=_np.int32), rw)
    xs = _np.ascontiguousarray(gx); zs = _np.ascontiguousarray(gz)

    # full condition tree for exact C validation
    raw = lib._lib
    res = _build_ssacond_array(enabled, raw)
    if res is None:
        return _gpu_decline("this session's tree is not supported by the C engine")
    arr, n_conds = res
    _gpu_prof_target(enabled, ccond)
    try:
        raw.ssa_test_seeds_batch.argtypes = [
            ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
            ct.POINTER(ct.c_int64), ct.c_int64,
            ct.POINTER(ct.c_int64), ct.c_int64]
        raw.ssa_test_seeds_batch.restype = ct.c_int64
    except Exception:
        return _gpu_decline("the C engine is too old (rebuild libcubiomes)")
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    if large:          # the GPU noise kernels compute the normal world type only
        return _gpu_decline("large biomes are not computed by the GPU (C engine on the CPU)")
    nmax = _gpu_nmax(enabled, ccond, arr)
    if nmax > 0 and not _ng.has_climate_nmax():
        return _gpu_decline("limited octaves need the newer GPU library "
                            "(py tools/build_native.py --unit noise_gpu)")

    # fused GPU filter (seeds made on the GPU, only survivors come back): the
    # same keep rule as _keepmask, in integers
    import math as _m
    _BIG = 1 << 62
    if not invert:
        _mode = 0
        _A = _m.ceil(vmin10k - MARGIN) if vmin10k is not None else -_BIG
        _B = _m.floor(vmax10k + MARGIN) if vmax10k is not None else _BIG
    else:
        _mode = 1
        _A = (_m.ceil(vmin10k + MARGIN) - 1) if vmin10k is not None else -_BIG
        _B = (_m.floor(vmax10k - MARGIN) + 1) if vmax10k is not None else _BIG
    _fused = [_ng.has_climatefilter()]

    def _scan(ss, n):
        if _fused[0]:
            r = _ng.climatefilter(mc_version, ss, n, xs, zs, para, nmax, _mode, _A, _B)
            if r is not None:
                return r
            _fused[0] = False                  # not for this parameter: per-seed scan
        seeds = _seed_arange(ss, n)
        r = _ng.climatescan(mc_version, seeds, xs, zs, para, nmax=nmax)
        if r is None:
            return None
        mn, mx, _cnt = r
        return seeds[_keepmask(mn, mx)]

    import threading as _th                    # the hybrid runs N validators -> make
    _emit_lock = _th.Lock(); _emit_raw = on_match    # on_match thread-safe (rare calls)
    def on_match(_s, _summary=""):
        with _emit_lock:
            _emit_raw(_s, _summary)

    def _validate(seeds_i64):
        if seeds_i64 is None or len(seeds_i64) == 0:
            return
        seeds_i64 = _np.ascontiguousarray(seeds_i64, _np.int64)
        nn = len(seeds_i64)
        out = _np.empty(nn, _np.int64)
        kept = _tsb(raw, 
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            seeds_i64.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn),
            out.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn))
        for k in range(int(kept)):
            if stop_event.is_set():
                break
            on_match(int(out[k]), "")

    def _warmup():                             # build g_child_order single-threaded
        s0 = _np.zeros(1, _np.int64); o0 = _np.empty(1, _np.int64)
        raw.ssa_test_seeds_batch(
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            s0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1),
            o0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1))

    total = seed_count if seed_count > 0 else (1 << 48)
    return _hybrid_loop(_scan, _validate, _warmup, seed_start, total,
                        num_workers, stop_event, on_progress)


# ═════════════════════════════════════════════════════════════════════════════
#  Climate noise samples GPU prefilter (1.18+) — F_NOISE_SAMPLE
#  GPU counts grid samples whose climate param is in [vmin,vmax]; pass if the
#  coverage (count/nsamp) meets the threshold. Reuses the climate kernel's count
#  output. Recall-safe via a generous margin; the C engine validates exactly.
# ═════════════════════════════════════════════════════════════════════════════

def _noise_gpu_eligible(enabled):
    for c in enabled:
        p = c.get("params", {})
        if (c.get("type") == "noise_sample" and c.get("relative", 0) == 0
                and p.get("use_gpu")):                # top-level AND only (child = unsafe)
            return c
    return None


def _run_noise_gpu_search(enabled, ncond, seed_start, seed_count, mc_version,
                          lib, stop_event, on_match, on_progress, num_workers=0):
    """GPU climate-coverage prefilter (Climate noise samples), exact C validation."""
    import ctypes as ct
    try:
        import noise_gpu as _ng
        import numpy as _np
    except Exception:
        return _gpu_decline("the noise_gpu module could not be imported")
    if not _ng.available() or not _ng.supported_version(mc_version):
        return _gpu_decline("noise_gpu is not available (no CUDA library) or does not support this version")

    params = ncond.get("params", {})
    para = _CLIMATE_PARA.get(params.get("para", "Continentalness"), 2)
    invert = bool(params.get("invert", False))
    try:
        cov = float(params.get("coverage_pct", 50)) / 100.0
    except (TypeError, ValueError):
        cov = 0.5
    cov = min(max(cov, 0.0), 1.0)

    def _bound(v):
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or "inf" in s.lower():
            return None
        try:
            return float(s)
        except ValueError:
            return None
    rng = params.get("range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        vmin_raw, vmax_raw = _bound(rng[0]), _bound(rng[1])
    else:
        vmin_raw, vmax_raw = _bound(params.get("vmin")), _bound(params.get("vmax"))
    # bounds are ALREADY in the x10000 domain (C engine: "bornes en unités x10000",
    # value tested = sampleDoublePerlin*10000). Do NOT scale again (same units bug
    # as climate_minmax — it broke inverted/coverage counts on GPU-scanned chunks).
    lo10k = int(vmin_raw) if vmin_raw is not None else -2147483648
    hi10k = int(vmax_raw) if vmax_raw is not None else  2147483647

    # box -> full scale-4 grid (matches the area cubiomes samples; cap size)
    rmax = ncond.get("rmax", 0)
    if not rmax and ncond.get("area_mode") == 0 and int(ncond.get("radial", 0) or 0) > 0:
        rmax = int(ncond.get("radial", 0)) + 1
    if rmax and rmax > 0:
        rb = rmax - 1; x1, z1, x2, z2 = -rb, -rb, rb, rb
    else:
        x1 = ncond.get("x1", -2048); z1 = ncond.get("z1", -2048)
        x2 = ncond.get("x2",  2048); z2 = ncond.get("z2",  2048)
    if x2 < x1: x1, x2 = x2, x1
    if z2 < z1: z1, z2 = z2, z1
    rx1, rz1 = x1 >> 2, z1 >> 2
    # cubiomes-viewer / C engine extent: (x2>>2)-(x1>>2)+1 (was up to one cell
    # short → a necessary pre-filter could miss the last row / column)
    rw, rh = max(1, (x2 >> 2) - rx1 + 1), max(1, (z2 >> 2) - rz1 + 1)
    if rw * rh > GPU_GRID_CELLS:
        return _gpu_decline(f"its area ({rw}×{rh} cells of 4 blocks) is above the GPU grid limit")
    xs = _np.ascontiguousarray(_np.repeat(_np.arange(rx1, rx1 + rw, dtype=_np.int32), rh))
    zs = _np.ascontiguousarray(_np.tile(_np.arange(rz1, rz1 + rh, dtype=_np.int32), rw))
    nsamp = rw * rh
    # recall-safe coverage threshold in sample count (generous margin for the
    # grid-vs-MonteCarlo + FP32 gap). non-invert: count_in >= cov*n ; invert:
    # count_out >= cov*n  <=>  count_in <= (1-cov)*n.

    raw = lib._lib
    res = _build_ssacond_array(enabled, raw)
    if res is None:
        return _gpu_decline("this session's tree is not supported by the C engine")
    arr, n_conds = res
    _gpu_prof_target(enabled, ncond)
    try:
        raw.ssa_test_seeds_batch.argtypes = [
            ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
            ct.POINTER(ct.c_int64), ct.c_int64,
            ct.POINTER(ct.c_int64), ct.c_int64]
        raw.ssa_test_seeds_batch.restype = ct.c_int64
    except Exception:
        return _gpu_decline("the C engine is too old (rebuild libcubiomes)")
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    if large:          # the GPU noise kernels compute the normal world type only
        return _gpu_decline("large biomes are not computed by the GPU (C engine on the CPU)")
    nmax = _gpu_nmax(enabled, ncond, arr)
    if nmax > 0 and not _ng.has_climate_nmax():
        return _gpu_decline("limited octaves need the newer GPU library "
                            "(py tools/build_native.py --unit noise_gpu)")

    M = 3
    if invert:
        _mode, _A, _B = 1, lo10k + M, hi10k - M
    else:
        _mode, _A, _B = 0, lo10k - M + 1, hi10k + M - 1
    _fused = [_ng.has_climatefilter()]

    def _scan(ss, n):
        if _fused[0]:
            r = _ng.climatefilter(mc_version, ss, n, xs, zs, para, nmax, _mode, _A, _B)
            if r is not None:
                return r
            _fused[0] = False
        seeds = _seed_arange(ss, n)
        r = _ng.climatescan(mc_version, seeds, xs, zs, para, lo10k, hi10k, nmax=nmax)
        if r is None:
            return None
        mn, mx, _cnt = r
        mn = mn.astype(_np.int64); mx = mx.astype(_np.int64)   # ±2^31 bounds ± margin
        # EXACT necessary test: cubiomes' Monte-Carlo can only accept after at
        # least one sample in range (outside it when inverted), so the area must
        # hold such a cell (FP32 margin). The old test — a share of the square
        # grid vs the coverage asked, with a 10 % margin — was only statistical,
        # and wrong on a disk area (the grid counted the corners too: a seed with
        # 60 % in the disk but 47 % in the square was dropped).
        M = 3
        if invert:
            keep = (mn <= lo10k + M) | (mx >= hi10k - M)
        else:
            keep = (mx > lo10k - M) & (mn < hi10k + M)
        return seeds[keep]

    import threading as _th                    # the hybrid runs N validators -> make
    _emit_lock = _th.Lock(); _emit_raw = on_match    # on_match thread-safe (rare calls)
    def on_match(_s, _summary=""):
        with _emit_lock:
            _emit_raw(_s, _summary)

    def _validate(seeds_i64):
        if seeds_i64 is None or len(seeds_i64) == 0:
            return
        seeds_i64 = _np.ascontiguousarray(seeds_i64, _np.int64)
        nn = len(seeds_i64)
        out = _np.empty(nn, _np.int64)
        kept = _tsb(raw, 
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            seeds_i64.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn),
            out.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn))
        for k in range(int(kept)):
            if stop_event.is_set():
                break
            on_match(int(out[k]), "")

    def _warmup():                             # build g_child_order single-threaded
        s0 = _np.zeros(1, _np.int64); o0 = _np.empty(1, _np.int64)
        raw.ssa_test_seeds_batch(
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            s0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1),
            o0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1))

    total = seed_count if seed_count > 0 else (1 << 48)
    return _hybrid_loop(_scan, _validate, _warmup, seed_start, total,
                        num_workers, stop_event, on_progress)


# ═════════════════════════════════════════════════════════════════════════════
#  Biome filter GPU prefilter (1.18+ "Overworld at scale" / F_BIOME / checkForBiomes)
#  The GPU classifies EVERY cell of the area (full grid at scale 4 — subsampling
#  would lose recall on presence) and keeps seeds where each REQUIRED biome is
#  present (count>=1). Recall-safe except a rare single-cell biome misclassified
#  by FP32 (~1e-5); exclusions / inverted / scale-1 / huge boxes -> exact C engine.
# ═════════════════════════════════════════════════════════════════════════════

def _biome_filter_gpu_eligible(enabled):
    """Return a biome_filter ('Overworld at scale') cond to GPU-prefilter, or None.
    Only the non-inverted, scale-4, top-level case with a required include list."""
    for c in enabled:
        p = c.get("params", {})
        if not (c.get("type") == "biome_filter"
                and (c.get("relative", 0) == 0 or p.get("gpu_box"))
                and p.get("use_gpu")
                and p.get("biomes_incl")
                and not str(p.get("blocks_scale", "")).startswith("1/1")):
            continue
        try:                                       # mirror _build_ssacond_array: count<=0
            mn = int(c.get("instances", p.get("count", 1)))   # => exclude (inverted) -> CPU
        except (TypeError, ValueError):
            mn = 1
        if mn > 0:
            return c
    return None


def _run_biome_filter_gpu_search(enabled, fcond, seed_start, seed_count, mc_version,
                                 lib, stop_event, on_match, on_progress, num_workers=0):
    """GPU 'Overworld at scale' prefilter: keep seeds where every required biome is
    present over the full area grid; the C engine validates survivors exactly."""
    import ctypes as ct
    try:
        import noise_gpu as _ng
        import numpy as _np
    except Exception:
        return _gpu_decline("the noise_gpu module could not be imported")
    if not _ng.available() or not _ng.supported_version(mc_version):
        return _gpu_decline("noise_gpu is not available (no CUDA library) or does not support this version")

    params = fcond.get("params", {})
    incl = [int(b) for b in params.get("biomes_incl", [])]
    if not incl:
        return _gpu_decline("no required biome to look for")
    match_any = bool(params.get("match_any", False))

    # box -> FULL scale-4 grid (every cell; subsampling would lose presence recall)
    rmax = fcond.get("rmax", 0)
    if not rmax and fcond.get("area_mode") == 0 and int(fcond.get("radial", 0) or 0) > 0:
        rmax = int(fcond.get("radial", 0)) + 1
    if rmax and rmax > 0:
        rb = rmax - 1; x1, z1, x2, z2 = -rb, -rb, rb, rb
    else:
        x1 = fcond.get("x1", -2048); z1 = fcond.get("z1", -2048)
        x2 = fcond.get("x2",  2048); z2 = fcond.get("z2",  2048)
    _gb = params.get("gpu_box")          # zone englobante of a DEPENDENT condition
    if _gb:                              # (gpu_planner): the union of every place its
        x1, z1, x2, z2 = (int(v) for v in _gb)   # area can be — a necessary test
    if x2 < x1: x1, x2 = x2, x1
    if z2 < z1: z1, z2 = z2, z1
    rx1, rz1 = x1 >> 2, z1 >> 2
    rw = max(1, (x2 >> 2) - (x1 >> 2) + 1)     # C / cubiomes-viewer extent
    rh = max(1, (z2 >> 2) - (z1 >> 2) + 1)
    if rw * rh > GPU_GRID_CELLS:   # widened zones (planner measures)                        # too large for full-grid GPU -> CPU
        return _gpu_decline(f"its area ({rw}×{rh} cells of 4 blocks) is above the GPU grid limit")
    xs = _np.ascontiguousarray(_np.repeat(_np.arange(rx1, rx1 + rw, dtype=_np.int32), rh))
    zs = _np.ascontiguousarray(_np.tile(_np.arange(rz1, rz1 + rh, dtype=_np.int32), rw))
    # y EXACTLY as the C biome_filter handler: r.y = biome_y >> 2 at scale 4
    y4 = int(params.get("y", 256)) >> 2

    raw = lib._lib
    res = _build_ssacond_array(enabled, raw)
    if res is None:
        return _gpu_decline("this session's tree is not supported by the C engine")
    arr, n_conds = res
    _gpu_prof_target(enabled, fcond)
    try:
        raw.ssa_test_seeds_batch.argtypes = [
            ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
            ct.POINTER(ct.c_int64), ct.c_int64,
            ct.POINTER(ct.c_int64), ct.c_int64]
        raw.ssa_test_seeds_batch.restype = ct.c_int64
    except Exception:
        return _gpu_decline("the C engine is too old (rebuild libcubiomes)")
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    if large:          # the GPU noise kernels compute the normal world type only
        return _gpu_decline("large biomes are not computed by the GPU (C engine on the CPU)")

    # match-any -> one union mask (any required present); match-all -> one mask per
    # required biome, keep only if ALL present.
    if match_any:
        masks = [_ng.allowed_bitmask(incl)]
    else:
        masks = [_ng.allowed_bitmask([b]) for b in incl]

    def _scan(ss, n):
        seeds = _seed_arange(ss, n)
        keep = None
        for m in masks:
            counts = _ng.biomescan(mc_version, seeds, xs, zs, m, y=y4)
            if counts is None:
                return None
            present = counts >= 1
            keep = present if keep is None else (keep & present)
        return seeds[keep]

    import threading as _th                    # the hybrid runs N validators -> make
    _emit_lock = _th.Lock(); _emit_raw = on_match    # on_match thread-safe (rare calls)
    def on_match(_s, _summary=""):
        with _emit_lock:
            _emit_raw(_s, _summary)

    def _validate(seeds_i64):
        if seeds_i64 is None or len(seeds_i64) == 0:
            return
        seeds_i64 = _np.ascontiguousarray(seeds_i64, _np.int64)
        nn = len(seeds_i64)
        out = _np.empty(nn, _np.int64)
        kept = _tsb(raw, 
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            seeds_i64.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn),
            out.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn))
        for k in range(int(kept)):
            if stop_event.is_set():
                break
            on_match(int(out[k]), "")

    def _warmup():                             # build g_child_order single-threaded
        s0 = _np.zeros(1, _np.int64); o0 = _np.empty(1, _np.int64)
        raw.ssa_test_seeds_batch(
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            s0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1),
            o0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1))

    total = seed_count if seed_count > 0 else (1 << 48)
    return _hybrid_loop(_scan, _validate, _warmup, seed_start, total,
                        num_workers, stop_event, on_progress)


# ═════════════════════════════════════════════════════════════════════════════
#  Biome center GPU prefilter (1.18+ "Locate biome center" / F_BIOME_CENTER)
#  A center of biome B can only exist if B is PRESENT in the area. The GPU scans
#  the full area grid for B (count>=1) and keeps such seeds; the C engine
#  (getBiomeCenters: min-size + tolerance + min-count) validates exactly. Same
#  recall profile as biome_filter. Inverted (count<=0 = "no center") -> CPU.
# ═════════════════════════════════════════════════════════════════════════════

def _biome_center_gpu_eligible(enabled):
    """Return a biome_center cond to GPU-prefilter on, or None."""
    for c in enabled:
        p = c.get("params", {})
        if not (c.get("type") == "biome_center"
                and (c.get("relative", 0) == 0 or p.get("gpu_box"))
                and p.get("use_gpu")):
            continue
        try:
            mn = int(c.get("instances", p.get("count", 1)))    # count<=0 -> exclude -> CPU
        except (TypeError, ValueError):
            mn = 1
        if mn > 0:
            return c
    return None


def _run_biome_center_gpu_search(enabled, ccond, seed_start, seed_count, mc_version,
                                 lib, stop_event, on_match, on_progress, num_workers=0):
    """GPU 'Locate biome center' prefilter: keep seeds where the target biome is
    present over the full area grid; the C engine validates centers exactly."""
    import ctypes as ct
    try:
        import noise_gpu as _ng
        import numpy as _np
    except Exception:
        return _gpu_decline("the noise_gpu module could not be imported")
    if not _ng.available() or not _ng.supported_version(mc_version):
        return _gpu_decline("noise_gpu is not available (no CUDA library) or does not support this version")

    params = ccond.get("params", {})
    try:
        biome_id = int(params.get("biome_id", 1) or 1)
    except (TypeError, ValueError):
        biome_id = 1

    rmax = ccond.get("rmax", 0)
    if not rmax and ccond.get("area_mode") == 0 and int(ccond.get("radial", 0) or 0) > 0:
        rmax = int(ccond.get("radial", 0)) + 1
    if rmax and rmax > 0:
        rb = rmax - 1; x1, z1, x2, z2 = -rb, -rb, rb, rb
    else:
        x1 = ccond.get("x1", -2048); z1 = ccond.get("z1", -2048)
        x2 = ccond.get("x2",  2048); z2 = ccond.get("z2",  2048)
    _gb = params.get("gpu_box")          # zone englobante of a DEPENDENT condition
    if _gb:                              # (gpu_planner): the union of every place its
        x1, z1, x2, z2 = (int(v) for v in _gb)   # area can be — a necessary test
    if x2 < x1: x1, x2 = x2, x1
    if z2 < z1: z1, z2 = z2, z1
    rx1, rz1 = x1 >> 2, z1 >> 2
    rw = max(1, (x2 >> 2) - (x1 >> 2) + 1)     # C / cubiomes-viewer extent
    rh = max(1, (z2 >> 2) - (z1 >> 2) + 1)
    if rw * rh > GPU_GRID_CELLS:   # widened zones (planner measures)                        # too large for full-grid GPU -> CPU
        return _gpu_decline(f"its area ({rw}×{rh} cells of 4 blocks) is above the GPU grid limit")
    xs = _np.ascontiguousarray(_np.repeat(_np.arange(rx1, rx1 + rw, dtype=_np.int32), rh))
    zs = _np.ascontiguousarray(_np.tile(_np.arange(rz1, rz1 + rh, dtype=_np.int32), rw))
    y4 = int(params.get("y", 256)) >> 2            # mirror C: r.y = biome_y >> 2 (scale 4)

    raw = lib._lib
    res = _build_ssacond_array(enabled, raw)
    if res is None:
        return _gpu_decline("this session's tree is not supported by the C engine")
    arr, n_conds = res
    _gpu_prof_target(enabled, ccond)
    try:
        raw.ssa_test_seeds_batch.argtypes = [
            ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
            ct.POINTER(ct.c_int64), ct.c_int64,
            ct.POINTER(ct.c_int64), ct.c_int64]
        raw.ssa_test_seeds_batch.restype = ct.c_int64
    except Exception:
        return _gpu_decline("the C engine is too old (rebuild libcubiomes)")
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    if large:          # the GPU noise kernels compute the normal world type only
        return _gpu_decline("large biomes are not computed by the GPU (C engine on the CPU)")

    mask = _ng.allowed_bitmask([biome_id])

    def _scan(ss, n):
        seeds = _seed_arange(ss, n)
        counts = _ng.biomescan(mc_version, seeds, xs, zs, mask, y=y4)
        if counts is None:
            return None
        return seeds[counts >= 1]

    import threading as _th
    _emit_lock = _th.Lock(); _emit_raw = on_match
    def on_match(_s, _summary=""):
        with _emit_lock:
            _emit_raw(_s, _summary)

    def _validate(seeds_i64):
        if seeds_i64 is None or len(seeds_i64) == 0:
            return
        seeds_i64 = _np.ascontiguousarray(seeds_i64, _np.int64)
        nn = len(seeds_i64)
        out = _np.empty(nn, _np.int64)
        kept = _tsb(raw, 
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            seeds_i64.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn),
            out.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn))
        for k in range(int(kept)):
            if stop_event.is_set():
                break
            on_match(int(out[k]), "")

    def _warmup():
        s0 = _np.zeros(1, _np.int64); o0 = _np.empty(1, _np.int64)
        raw.ssa_test_seeds_batch(
            arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(large),
            s0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1),
            o0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1))

    total = seed_count if seed_count > 0 else (1 << 48)
    return _hybrid_loop(_scan, _validate, _warmup, seed_start, total,
                        num_workers, stop_event, on_progress)


_LAST_GPU_FAIL = {"msg": None}


def _set_gpu_fail(msg):
    _LAST_GPU_FAIL["msg"] = msg
    print(f"[GPU] {msg}", flush=True)


def _run_structure_gpu_search(conditions, jobs, seed_start, seed_count,
                              mc_version, lib, stop_event, on_match,
                              on_progress, fam48=True, num_workers=0, inc=False):
    """GPU 48-bit structure pre-filter + CPU full validation, double-buffered.

    The GPU scans large batches of 48-bit lows, keeping only seeds whose
    structures all fall in the requested area. The CPU then validates biomes
    and all other conditions on the survivors via ssa_test_seeds_batch.
    Returns True if the search ran on GPU, False to signal CPU fallback.
    """
    import ctypes as ct
    import structure_cuda as _sc
    import concurrent.futures as _cf

    raw = lib._lib
    import sys as _dbgsys
    _nl_dbg(f"[GPU-DBG] STEP1: entering, hasattr(expand)={hasattr(raw, 'ssa_expand_lows')}")

    # Bind ssa_expand_lows
    try:
        raw.ssa_expand_lows.argtypes = [
            ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
            ct.POINTER(ct.c_int64), ct.c_int64,
            ct.POINTER(ct.c_int64), ct.c_int64, ct.c_int,
            ct.POINTER(ct.c_int)]
        raw.ssa_expand_lows.restype = ct.c_int64
        _nl_dbg(f"[GPU-DBG] STEP2: ssa_expand_lows bound OK")
    except AttributeError as _e:
        _nl_dbg(f"[GPU-DBG] STEP2: ssa_expand_lows MISSING (AttributeError): {_e}")
        return False
    except Exception as _e:
        _nl_dbg(f"[GPU-DBG] STEP2: ssa_expand_lows MISSING (other {type(_e).__name__}): {_e}")
        return False

    # Construire le SsaCond array
    _nl_dbg(f"[GPU-DBG] STEP3: calling _build_ssacond_array (n_conds={len(conditions)})")
    res = _build_ssacond_array(conditions, raw)
    _nl_dbg(f"[GPU-DBG] STEP4: _build_ssacond_array returned {res is not None}")
    if res is None:
        return False
    arr, n_conds = res

    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    _nl_dbg(f"[GPU-DBG] STEP5: large={large}")

    _struct_keys = _structure_type_keys()
    # has_dep64 is ALWAYS True for structure conditions.
    #
    # BUG-FIX: The previous code set has_dep64=False when all conditions were
    # structure types (reasoning: structure positions are 48-bit only, so all
    # 65536 high-bit variants are valid). This was WRONG for two reasons:
    #
    # 1. Structure GENERATION requires a biome check (64-bit) — e.g. mansions
    #    only spawn in dark forest. A 48-bit position hit does NOT mean the
    #    structure actually generates. Skipping ssa_expand_lows skips the biome
    #    check → all GPU survivors are reported as results → millions of false
    #    positives.
    #
    # 2. The GPU position filter is often trivially satisfied (e.g. count=4
    #    over a large radius: 50+ out of 64 regions are in range, so every seed
    #    has 50 mansions > 4 → 100% pass rate). Without CPU validation the user
    #    sees 30M+ bogus results.
    #
    # Fix: always expand GPU survivors through ssa_expand_lows (CPU), which
    # runs ALL conditions including biome checks. The GPU 48-bit filter always
    # runs and reduces the CPU workload (even a 15% survival rate means the CPU
    # only validates 15% of structure-seeds instead of all of them).
    n_enabled = len(conditions)  # caller already filtered to enabled
    has_dep64 = True  # ALWAYS — biome/generation checks require full 64-bit seed
    _nl_dbg(f"[GPU-DBG] STEP6: has_dep64={has_dep64} (forced)  n_jobs={len(jobs)}  n_enabled={n_enabled}")

    # Batch GPU large : une carte récente (RTX 50xx) avale 1 M graines en
    # quelques millisecondes puis attend le CPU (GPU à ~10% d'utilisation). Un
    # batch de 16 M donne au GPU un vrai gros bloc de travail à chaque appel et
    # le maintient occupé. out_cap est dimensionné en conséquence.
    BATCH   = 1 << 28          # 256 M lows par batch GPU (amortit overhead Python/ctypes)
    OUT_CAP = 1 << 22          # 4 M survivants max par batch
    MASK48 = (1 << 48) - 1
    if inc:
        # INCREMENTAL: the range is [seed_start, seed_start+count) of 64-bit
        # seeds. Each seed has ONE 48-bit low; the GPU pre-filters those lows
        # and every survivor is validated as THAT exact seed (no expansion).
        # Before: the GPU path always expanded each low to its 65 536 upper
        # bits (Blocks semantics) → an Incremental range of 4 M seeds became
        # 2.7e11 seeds, and not the requested ones.
        low_start = seed_start & MASK48
        total = seed_count if seed_count > 0 else (1 << 63)
    elif seed_count > 0:
        low_start = seed_start & MASK48
        total = min(seed_count, (1 << 48) - low_start)
    else:
        low_start = seed_start & MASK48
        total = (1 << 48) - low_start
    done = 0
    _nl_dbg(f"[GPU-DBG] STEP7: low_start={low_start}  total={total}  BATCH={BATCH}")



    import threading as _th
    try:
        import queue as _queue
    except ImportError:
        import Queue as _queue

    out_buf = (ct.c_int64 * OUT_CAP)()

    # Flag d'arrêt partagé avec le C : ssa_expand_lows le teste pendant sa boucle
    # OpenMP → interruption en ~ms au lieu d'attendre la fin du batch entier
    # (sinon un seul appel sur 2 lows = 131072 graines bloquait l'arrêt ~5s).
    _cstop = ct.c_int(0)
    def _stop_sync():
        stop_event.wait()       # bloque jusqu'au stop
        _cstop.value = 1
    _stopper = _th.Thread(target=_stop_sync, daemon=True)
    _stopper.start()

    # ─────────────────────────────────────────────────────────────────────
    # PIPELINE DOUBLE-BUFFER (producteur GPU / consommateur CPU)
    #
    # Problème de l'ancienne boucle séquentielle : GPU filtre un batch → on
    # ATTEND → CPU valide les survivants → on attend → GPU batch suivant. Le
    # GPU dort pendant la validation CPU et inversement (GPU à 10-20%, 1s on /
    # 1s off ; CPU < 10%).
    #
    # Ici le GPU tourne dans un thread dédié et enchaîne les batches SANS
    # attendre le CPU : il dépose les survivants dans une file. Le thread
    # principal consomme la file et lance la validation CPU (ssa_expand_lows,
    # elle-même multi-thread OpenMP). Ainsi, pendant que le CPU valide le batch
    # N, le GPU calcule déjà le batch N+1 → les deux travaillent en continu.
    #
    # La file est bornée (maxsize) : si le CPU prend du retard, le thread GPU
    # bloque sur put() au lieu d'accumuler en mémoire. Si au contraire le filtre
    # est très sélectif (peu de survivants), le GPU enchaîne à pleine vitesse.
    # ─────────────────────────────────────────────────────────────────────
    # (INC: a short queue, so a new measured batch size applies quickly)
    _gpu_queue = _queue.Queue(maxsize=3 if inc else 16)  # (batch_done_lows, survivors) ou sentinelles
    _gpu_err = {}

    class _TooBroad(Exception):
        pass

    def _put_item(item):
        # Déposer (peut bloquer si le CPU est en retard → régule la mémoire)
        while not stop_event.is_set():
            try:
                _gpu_queue.put(item, timeout=0.2)
                return
            except _queue.Full:
                continue

    def _run_range(start, n):
        """Filtre [start, start+n) et dépose TOUS les survivants. Si le buffer de
        sortie (OUT_CAP) déborde, l'ancien code gardait les OUT_CAP premiers et
        perdait le reste sans rien dire (jusqu'à 98 % des graines valides mesurés
        sur une condition large). Désormais : on re-découpe la plage jusqu'à ce
        que chaque morceau tienne ; si plus de 5 % des lows passent, le
        pré-filtre GPU n'apporte rien → abandon explicite vers le moteur CPU."""
        if stop_event.is_set():
            return
        _survivors, _tot = _sc.filter_seeds(jobs, start, n, fam48=fam48, out_cap=OUT_CAP)
        if _tot > OUT_CAP:
            if _tot > max(OUT_CAP, n // 20):
                raise _TooBroad(f"{_tot:,} survivants sur {n:,} lows")
            half = n // 2
            _run_range(start, half)
            _run_range(start + half, n - half)
            return
        if not _so or inc:          # (structure-seed-only output: no stats)
            _k = 1 if inc else 0x10000
            _gpu_prof_note(n * _k, len(_survivors) * _k)
        if inc:
            # survivors are lows of [start, start+n) (no 2^48 wrap inside a
            # call, see the producer) → back to the exact 64-bit seeds
            base = _inc_seed_of[0] - start
            _survivors = [((l + base + (1 << 63)) % (1 << 64)) - (1 << 63) for l in _survivors]
            _put_item((_inc_done_at[0] + n, _survivors))
            _inc_done_at[0] += n
            _inc_seed_of[0] += n
            return
        _put_item(((start + n) - low_start, _survivors))

    _inc_done_at = [0]            # INC: seeds already handed to the queue
    _inc_seed_of = [seed_start]   # INC: 64-bit seed of the next low to filter
    # INC: batch size MEASURED on the validation speed (the consumer updates it,
    # aiming at ~0.5 s per batch). A fixed 2^28-seed batch took minutes on a
    # slow validation (mansion ≈ 500 k/s → ~8 min) before the first progress:
    # 0 seeds/s shown, and the engine analysis measured "no progress in 22 s".
    _bsz = [(1 << 18) if inc else BATCH]
    import time as _time_mod

    def _gpu_producer():
        """Tourne en continu : filtre chaque batch sur GPU, dépose les
        survivants dans la file. Ne fait AUCUNE validation CPU."""
        _pdone = 0
        try:
            while _pdone < total:
                if stop_event.is_set():
                    break
                _nn = min(_bsz[0], total - _pdone)
                if inc:          # never cross a 2^48 boundary inside one call
                    _lo = (seed_start + _pdone) & MASK48
                    _nn = min(_nn, (1 << 48) - _lo)
                try:
                    _run_range(((seed_start + _pdone) & MASK48) if inc else low_start + _pdone, _nn)
                except _TooBroad as _e:
                    _gpu_err["too_broad"] = str(_e)
                    break
                except Exception as _e:
                    import traceback as _tb
                    _tb.print_exc(file=_dbgsys.stderr)
                    _gpu_err["err"] = _e
                    break
                _pdone += _nn
        finally:
            # Sentinelle de fin (None) pour débloquer le consommateur
            while not stop_event.is_set():
                try:
                    _gpu_queue.put(None, timeout=0.2)
                    break
                except _queue.Full:
                    continue

    # Hide sister seeds / Structure seed only (08/10)
    _hs, _so = _fam_opts("fam48") if fam48 and not inc else (False, False)
    _x_large = int(large) | ((1 << 16) if _hs else 0)
    _pre48 = getattr(raw, "ssa_test_lows48", None) if _so else None
    if _pre48 is not None:
        _pre48.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                           ct.POINTER(ct.c_int64), ct.c_int64,
                           ct.POINTER(ct.c_int64), ct.c_int64]
        _pre48.restype = ct.c_int64
    # statistics (10/10): the Blocks validation counts into _GPU_PROF, and what
    # the GPU filtered out is credited to the first pre-filtered condition
    # (seeds: one low = 65 536 seeds in Blocks mode)
    _xprof = None
    if _GPU_PROF["buf"] is not None and _GPU_PROF["n"] == n_conds:
        _xprof = getattr(raw, "ssa_expand_lows_prof", None)
        if _xprof is not None:
            _xprof.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                               ct.POINTER(ct.c_int64), ct.c_int64,
                               ct.POINTER(ct.c_int64), ct.c_int64, ct.c_int,
                               ct.POINTER(ct.c_int), ct.POINTER(ct.c_int64)]
            _xprof.restype = ct.c_int64
        _jc = getattr(_sc, "LAST_JOB_CONDS", None) or []
        if _jc:
            _gpu_prof_target(conditions, _jc[0])
    _producer = _th.Thread(target=_gpu_producer, daemon=True)
    _producer.start()
    _nl_dbg(f"[GPU-DBG] producer started (double-buffer pipeline)")

    # ── Consommateur (thread principal) : validation CPU des survivants ──
    EXPAND_CHUNK = 64   # 64 lows × 65536 = ~4 M tests par tranche CPU
    while not stop_event.is_set():
        try:
            item = _gpu_queue.get(timeout=0.2)
        except _queue.Empty:
            if not _producer.is_alive() and _gpu_queue.empty():
                break
            continue
        if item is None:        # sentinelle de fin du producteur
            break
        batch_done, survivors = item
        _prev_done = done
        done = batch_done

        if inc:
            _tv0 = _time_mod.perf_counter()
            if survivors:
                import numpy as _np
                _parallel_validate(raw, arr, n_conds, mc_version, large,
                                   _np.array(survivors, dtype=_np.int64),
                                   num_workers, stop_event, on_match)
            on_progress(done, total if seed_count > 0 else 0)
            _dtv = _time_mod.perf_counter() - _tv0
            _nb = max(1, done - _prev_done)
            if _dtv > 0.02:          # validation-bound: aim at ~0.5 s per batch
                _bsz[0] = int(max(1 << 16, min(BATCH, _nb * 0.5 / _dtv, _bsz[0] * 4)))
            else:                    # validation is instant: let the GPU batch grow
                _bsz[0] = int(min(BATCH, _bsz[0] * 4))
            if "err" in _gpu_err or "too_broad" in _gpu_err:
                break
            continue

        if survivors:
            # Progress INSIDE the batch: a batch is 2^28 lows = 1.8e13 seeds,
            # which the CPU validation can take many seconds to go through.
            # Reporting the batch end at once made the progress jump then stay
            # flat (and the speed unmeasurable by the engine analysis): report
            # the low reached instead, survivors being sorted.
            survivors = sorted(survivors)
            for off in range(0, len(survivors), EXPAND_CHUNK):
                if stop_event.is_set():
                    break
                chunk = survivors[off:off + EXPAND_CHUNK]
                in_arr = (ct.c_int64 * len(chunk))(*chunk)
                if _pre48 is not None:
                    # structure seed only: every top-level structure position
                    # (the GPU checked those it supports), no biome, no expansion
                    kept = _pre48(arr, n_conds, int(mc_version), int(large),
                                  in_arr, len(chunk), out_buf, OUT_CAP)
                elif _xprof is not None:      # statistics on (10/10)
                    kept = _xprof(arr, n_conds, int(mc_version), _x_large,
                                  in_arr, len(chunk), out_buf, OUT_CAP,
                                  int(num_workers), ct.byref(_cstop), _GPU_PROF["buf"])
                else:
                    kept = raw.ssa_expand_lows(
                        arr, ct.c_int(n_conds), ct.c_int(mc_version), ct.c_int(_x_large),
                        in_arr, ct.c_int64(len(chunk)),
                        out_buf, ct.c_int64(OUT_CAP), ct.c_int(num_workers),
                        ct.byref(_cstop))
                ncopy = min(int(kept), OUT_CAP)
                for i in range(ncopy):
                    if stop_event.is_set():
                        break
                    on_match(int(out_buf[i]), "")
                # Progress en GRAINES réelles : chaque low couvre 65536 graines.
                reached = min(done, int(chunk[-1]) + 1 - low_start)
                on_progress(reached << 16, total << 16)
        on_progress(done << 16, total << 16)

        if "err" in _gpu_err or "too_broad" in _gpu_err:
            break

    # Attendre proprement la fin du producteur
    stop_event_was_set = stop_event.is_set()
    _producer.join(timeout=2.0)

    if "too_broad" in _gpu_err:
        _set_gpu_fail("GPU : condition trop large pour le pré-filtre ("
                      + _gpu_err["too_broad"] + ") — recherche relancée sur le CPU.")
        return False
    if "err" in _gpu_err:
        import traceback
        traceback.print_exception(type(_gpu_err["err"]), _gpu_err["err"],
                                  _gpu_err["err"].__traceback__)
        _set_gpu_fail(f"Erreur GPU : {_gpu_err['err']} — recherche relancée sur le CPU.")
        return False

    if inc:
        on_progress(done, total if seed_count > 0 else 0)
    else:
        on_progress(done << 16, total << 16)
    return True


def test_single_seed(conditions, seed, mc_version, search_mode="inc"):
    """Tests ONE exact seed against the whole session, through the same
    dispatcher as a search (C engine + stronghold eye / max-Y / corrupted
    post-filters + Exact Y). Returns True / False; errors are RAISED so the UI
    can say "error" instead of "does not match" (before: the C engine alone,
    no stronghold post-filter, and any exception became False)."""
    import threading
    hit = []
    evaluate_session(conditions, int(seed), 1, mc_version, 1, threading.Event(),
                     on_match=lambda s, d="": hit.append(int(s)),
                     on_progress=lambda d, t: None, search_mode="inc",
                     seed_list=[int(seed)])
    return bool(hit)


def _seed_list_eval_worker(args):
    """Worker: evaluate a sublist of EXACT seeds against the conditions (Python
    path). Returns the matching (seed, details). Used by SEED-LIST mode."""
    conditions, sublist, mc_version = args
    out = []
    try:
        lib = CubiomesLib()
    except Exception:
        return out
    for seed in sublist:
        try:
            ok, details = evaluate_seed(conditions, seed, mc_version, lib)
        except Exception:
            continue
        if ok:
            out.append((seed, details))
    return out


def _seed_list_eval_c(conditions, seed_list, mc_version, num_workers,
                      stop_event, on_match, on_progress) -> bool:
    """Seed list through the C engine (ssa_test_seeds_batch, parallel threads).
    Returns False when the tree cannot be serialised for C (→ Python path)."""
    import ctypes as ct
    import numpy as _np
    lib = CubiomesLib()
    raw = lib._lib
    res = _build_ssacond_array(conditions, raw)
    if res is None or not hasattr(raw, "ssa_test_seeds_batch"):
        return False
    arr, n_conds = res
    raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                                         ct.POINTER(ct.c_int64), ct.c_int64,
                                         ct.POINTER(ct.c_int64), ct.c_int64]
    raw.ssa_test_seeds_batch.restype = ct.c_int64
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    total = len(seed_list)
    stop = stop_event if stop_event is not None else __import__("threading").Event()
    CH = 4096
    done = 0
    for i in range(0, total, CH):
        if stop.is_set():
            break
        sub = [((int(x) + (1 << 63)) % (1 << 64)) - (1 << 63) for x in seed_list[i:i + CH]]
        chunk = _np.array(sub, dtype=_np.int64)
        _parallel_validate(raw, arr, n_conds, mc_version, large, chunk,
                           num_workers, stop, on_match or (lambda *_: None))
        done += len(sub)
        if on_progress:
            on_progress(done, total)
    return True


def _evaluate_seed_list(conditions, seed_list, mc_version, num_workers,
                        stop_event, on_match, on_progress):
    """SEED-LIST mode for custom sessions: test EXACTLY the given seeds against
    the conditions and stop when all are done. Parallelised over <=12 workers."""
    seed_list = list(seed_list)
    total = len(seed_list)
    nw = max(1, int(num_workers))
    # C engine first (same evaluator as a range search, ~1000x faster than the
    # Python path). Python is only used for trees the C engine cannot express.
    if _seed_list_eval_c(conditions, seed_list, mc_version, nw,
                         stop_event, on_match, on_progress):
        return
    CS = 256
    subs = [seed_list[i:i + CS] for i in range(0, total, CS)]
    done = 0
    if nw == 1:
        lib = CubiomesLib()
        for sub in subs:
            if stop_event is not None and stop_event.is_set():
                break
            for seed in sub:
                # errors are raised (the UI reports them) instead of being
                # silently counted as "no match"
                ok, details = evaluate_seed(conditions, seed, mc_version, lib)
                if ok and on_match:
                    on_match(seed, details)
            done += len(sub)
            if on_progress:
                on_progress(done, total)
    else:
        import concurrent.futures as _cf
        ex = _cf.ProcessPoolExecutor(max_workers=min(nw, 61))   # Windows limit: 61 workers
        futs = [ex.submit(_seed_list_eval_worker, (conditions, sub, mc_version))
                for sub in subs]
        try:
            for sub, fut in zip(subs, futs):
                if stop_event is not None and stop_event.is_set():
                    break
                for (seed, details) in fut.result():
                    if on_match:
                        on_match(seed, details)
                done += len(sub)
                if on_progress:
                    on_progress(done, total)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
    if on_progress:
        on_progress(total, total)


def _run_precise_y_cpu_batch(precise_conds, seed_start, seed_count, mc_version,
                             num_workers, stop_event, on_match, on_progress,
                             on_mode=None):
    """Fast CPU path for a search whose ONLY conditions are Precise Y (GPU toggle
    off): scan the seed range in chunks, each batch-filtered by tb_precise_y_filter
    across up to 12 threads (the C call releases the GIL → real parallelism, no
    per-seed CUDA overhead). Returns True if it handled the search, else False
    (caller falls back to the per-seed path)."""
    try:
        import terrain as _terr, numpy as _np, math
        import concurrent.futures as _cf
    except Exception:
        return False
    if not hasattr(_terr, "precise_y_filter"):
        print("[exactY] terrain module has no precise_y_filter()", flush=True)
        return False
    # availability probe (also triggers the one-time terrain.dll compile)
    if _terr.precise_y_filter(_np.zeros(1, dtype=_np.int64), mc_version,
                              0, 0, 0, -(1 << 30), (1 << 30)) is None:
        print("[exactY] probe=None → terrain.dll lacks tb_precise_y_filter "
              "(recompile needed):", _terr.last_error()[:200], flush=True)
        return False

    filt = [_precise_filter_params(c) for c in precise_conds]

    def _do(arr):                 # arr int64 chunk -> seeds passing ALL precise_y
        keep = arr
        for (ax, az, r, ymn, ymx, cv, inv, cov, conf, step) in filt:
            if len(keep) == 0:
                break
            # ctx path: each worker thread owns its own terrain ctx → thread-safe
            m = _terr.precise_y_filter_ctx(keep, mc_version, ax, az, r, ymn, ymx,
                                           caves=cv, invert=inv, sample_step=step,
                                           coverage=cov, confidence=conf)
            if m is None:
                return arr[:0]
            keep = keep[m.astype(bool)]
        return keep

    nw = max(1, int(num_workers or 1))   # every thread the user chose (no cap)
    if on_mode is not None:
        on_mode(0)                  # report "inc": fam48 is meaningless for terrain
    total = seed_count if seed_count > 0 else 0
    # Adaptive chunk: target ~0.2 s of work per chunk regardless of radius. That's long
    # enough that each worker stays in the GIL-released C call (no GIL thrashing → full
    # real throughput) yet short enough that completions are frequent → `done` rises
    # smoothly → the 2.5 s GUI window reads STEADY (no 10k↔80k wave aliasing). Cost is
    # estimated from the disk point count (re-seed floor ~130 µs + ~0.5 µs/point).
    _npts = 1
    for f in filt:
        _r = f[2]; _stp = f[9] if (len(f) > 9 and f[9]) else (max(1, (2 * f[2]) // 64 + 1) if f[2] else 1)
        if _r and _r > 0:
            _npts = max(_npts, int(math.pi * _r * _r / (_stp * _stp)) + 1)
    # Chunk size MEASURED, not guessed: the first chunks are tiny, then each
    # finished chunk updates the per-seed cost and the next ones aim at ~0.3 s.
    # (Before: a static estimate ~250× too optimistic with caves → chunks of
    # ~50 s → 0 seeds/s for most of the run, and Stop left minutes of work
    # running in the background.)
    cost = {"per_seed": None}
    CHUNK_MIN, CHUNK_MAX, TARGET = 1, 8192, 0.3
    seed = int(seed_start); submitted = 0; done = 0
    import time as _time
    _t0 = _time.time(); _last_log = _t0; _last_log_done = 0

    def _chunk_len():
        ps = cost["per_seed"]
        if ps is None:
            return 2
        return max(CHUNK_MIN, min(CHUNK_MAX, int(TARGET / max(ps, 1e-7))))

    def _make_chunk():
        nonlocal seed, submitted
        if seed_count > 0 and submitted >= seed_count:
            return None
        cl = _chunk_len()
        nn = cl if seed_count <= 0 else min(cl, seed_count - submitted)
        if nn <= 0:
            return None
        arr = _seed_arange(seed, nn)
        seed += nn; submitted += nn
        return arr

    def _timed(arr):
        t = _time.perf_counter()
        keep = _do(arr)
        return keep, (_time.perf_counter() - t) / max(1, len(arr))

    ex = _cf.ThreadPoolExecutor(max_workers=nw)
    futures = {}
    try:
        # Keep the pipeline FULL (~3× chunks in flight) so the worker threads never
        # idle while the main thread reports matches — that gap caused the 90k→9k dip.
        for _ in range(nw * 2):
            c = _make_chunk()
            if c is None:
                break
            futures[ex.submit(_timed, c)] = len(c)
        while futures and not stop_event.is_set():
            # TIMEOUT so the loop wakes to re-check stop_event even if no chunk has
            # finished yet — otherwise a long chunk would block Stop indefinitely.
            doneset, _pend = _cf.wait(list(futures), timeout=0.25,
                                      return_when=_cf.FIRST_COMPLETED)
            _now = _time.time()      # ground-truth throughput log (every ~2 s)
            if _now - _last_log >= 2.0:
                _avg = done / max(1e-6, _now - _t0)
                _win = (done - _last_log_done) / max(1e-6, _now - _last_log)
                _nl_dbg(f"[exactY] REAL sps avg={_avg:,.0f}  last2s={_win:,.0f}  "
                        f"done={done:,}  inflight={len(futures)}")
                _last_log = _now; _last_log_done = done
            if not doneset:
                continue            # pure timeout: re-check stop, no 0-delta sps tick
            for fut in doneset:
                nn = futures.pop(fut)
                try:
                    keep, per = fut.result()
                    ps = cost["per_seed"]
                    cost["per_seed"] = per if ps is None else 0.7 * ps + 0.3 * per
                except Exception:
                    keep = None
                if keep is not None and len(keep):
                    for s in keep.tolist():
                        on_match(int(s), "")
                done += nn
                c = _make_chunk()
                if c is not None and not stop_event.is_set():
                    futures[ex.submit(_timed, c)] = len(c)
            on_progress(done, total)
    finally:
        for fut in list(futures):   # cancel queued (not-yet-started) chunks
            fut.cancel()
        # Don't block the GUI waiting for in-flight chunks: return now, let the few
        # running ones drain in the background (cancel_futures needs Py 3.9+).
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)
    on_progress(done, total)
    return True


def _precise_filter_params(c):
    """(at_x, at_z, r, y_min, y_max, caves, invert, coverage, confidence, step) for
    one Exact-Y condition. coverage<=0 → any-match; >0 → Wilson coverage. step is the
    sampling stride (1/1·1/4·1/16 → 1·2·4); None → C auto grid (legacy conditions)."""
    import math
    p = c.get("params", {})
    x1 = c.get("x1", 0); x2 = c.get("x2", 0); z1 = c.get("z1", 0); z2 = c.get("z2", 0)
    ax = int(c.get("at_x", (x1 + x2) // 2)); az = int(c.get("at_z", (z1 + z2) // 2))
    r = max(0, int(c.get("radial", (x2 - x1) // 2)))
    ymn = p.get("y_min", None); ymx = p.get("y_max", None)
    ymn = -(1 << 30) if (ymn is None or not math.isfinite(float(ymn))) else int(ymn)
    ymx = (1 << 30) if (ymx is None or not math.isfinite(float(ymx))) else int(ymx)
    cov  = float(p.get("coverage", 0.0) or 0.0)
    conf = float(p.get("confidence", 0.95) or 0.95)
    _samp = p.get("sampling", None)
    step = None if _samp is None else max(1, int(_samp))
    return (ax, az, r, ymn, ymx, bool(p.get("caves", True)),
            bool(p.get("invert", False)), cov, conf, step)


def _run_precise_y_postfilter(precise_conds, other_conds, seed_start, seed_count,
                              mc_version, num_workers, stop_event, on_match,
                              on_progress, on_mode=None):
    """Mixed search (Exact Y + cheaper conditions, GPU toggle off): STREAM the cheaper
    conditions' candidate seeds through the Exact Y CPU batch in chunks, emitting the
    passers immediately.

    BUG THIS FIXES: the old design collected EVERY candidate into a list first, then
    filtered. A high-yield first condition (e.g. "a pillager outpost exists in the
    area" matches a large fraction of seeds) over a big range never finished
    collecting → the Exact Y filter never ran → ZERO results (and millions of ints
    piled up in RAM). Streaming filters as candidates arrive → results appear live and
    memory stays bounded. Returns True if handled, else False."""
    try:
        import terrain as _terr, numpy as _np
        import threading as _th
    except Exception:
        return False
    if not hasattr(_terr, "precise_y_filter"):
        return False
    if _terr.precise_y_filter(_np.zeros(1, _np.int64), mc_version,
                              0, 0, 0, -(1 << 30), (1 << 30)) is None:
        return False

    filt = [_precise_filter_params(c) for c in precise_conds]
    def _filter_batch(arr):
        keep = arr
        for (ax, az, r, ymn, ymx, cv, inv, cov, conf, step) in filt:
            if len(keep) == 0:
                break
            # each worker thread owns its own terrain ctx (thread-local) → thread-safe
            m = _terr.precise_y_filter_ctx(keep, mc_version, ax, az, r, ymn, ymx,
                                           caves=cv, invert=inv, sample_step=step,
                                           coverage=cov, confidence=conf)
            if m is None:
                return arr[:0]
            keep = keep[m.astype(bool)]
        return keep

    # The cheaper conditions' candidates are QUEUED and Exact-Y-filtered by a
    # pool of worker threads (the C call releases the GIL). Before, each 4 096
    # candidates were filtered inline in the search's own result loop, on one
    # thread: minutes per batch → 0 seeds/s most of the time, and Stop / Pause
    # waited for the batch to finish.
    import queue as _q, time as _time
    nw = max(1, int(num_workers or 1))       # every thread the user chose
    work = _q.Queue(maxsize=nw * 4)                   # bounded → back-pressure
    _lock = _th.Lock()
    _stats = {"collected": 0, "passed": 0}
    cost = {"per_seed": None}
    buf = []
    feeding = {"on": True}

    def _chunk_len():
        ps = cost["per_seed"]
        return 4 if ps is None else max(1, min(4096, int(0.3 / max(ps, 1e-7))))

    def _put(batch):
        while not stop_event.is_set():
            try:
                work.put(batch, timeout=0.1)
                return
            except _q.Full:
                continue

    # Progress = seeds whose candidates have all been Exact-Y-checked: each
    # batch carries the cheap search's position when it was cut; a finished
    # batch reports it. With nothing queued, the cheap search's own progress
    # goes straight through (sessions whose cheap conditions reject a lot).
    prog = {"cheap": (0, 0), "reported": 0, "inflight": 0}

    def _report(d, total):
        with _lock:
            if d <= prog["reported"]:
                return
            prog["reported"] = d
        on_progress(d, total)

    def _cheap_progress(d, total):
        with _lock:
            prog["cheap"] = (d, total)
            idle = not buf and work.empty() and prog["inflight"] == 0
        if idle:
            _report(d, total)

    def _collect(seed, _summary=""):
        batch = None
        with _lock:
            buf.append(int(seed))
            _stats["collected"] += 1
            if len(buf) >= _chunk_len():
                # position of this batch in the scan (Incremental: seeds ascend)
                tot = prog["cheap"][1] or (seed_count if seed_count > 0 else 0)
                pos = max(prog["cheap"][0], max(buf) - int(seed_start) + 1)
                batch = (buf[:], (min(pos, tot) if tot else pos, tot)); buf.clear()
        if batch is not None:
            _put(batch)                    # blocks the search only when the pool is full

    def _worker():
        while True:
            if stop_event.is_set():
                return
            try:
                batch, tag = work.get(timeout=0.1)
            except _q.Empty:
                if not feeding["on"]:
                    return
                continue
            with _lock:
                prog["inflight"] += 1
            t = _time.perf_counter()
            keep = _filter_batch(_np.array(batch, dtype=_np.int64))
            per = (_time.perf_counter() - t) / max(1, len(batch))
            with _lock:
                ps = cost["per_seed"]
                cost["per_seed"] = per if ps is None else 0.7 * ps + 0.3 * per
            n = 0
            if not stop_event.is_set():
                for s_ in keep.tolist():
                    on_match(int(s_), ""); n += 1
            with _lock:
                if n:
                    _stats["passed"] += n
                prog["inflight"] -= 1
            _report(*tag)

    pool = [_th.Thread(target=_worker, daemon=True, name=f"ssa-exacty-{k}") for k in range(nw)]
    for th in pool:
        th.start()
    try:
        evaluate_session(other_conds, seed_start, seed_count, mc_version, num_workers,
                         stop_event, _collect, _cheap_progress, "inc", on_mode)
        with _lock:                       # the tail
            rest = buf[:]; buf.clear()
        if rest and not stop_event.is_set():
            _put((rest, prog["cheap"]))
    finally:
        feeding["on"] = False
        for th in pool:                   # finish the queued work (or stop at once)
            while th.is_alive():
                th.join(0.1)
                if stop_event.is_set():
                    break
        if not stop_event.is_set():
            _report(*prog["cheap"])
    _nl_dbg(f"[exactY] post-filter streamed: collected={_stats['collected']} "
            f"passed={_stats['passed']}  filt(at_x,at_z,r,ymin,ymax,cov)="
            f"{[(f[0], f[1], f[2], f[3], f[4], f[7]) for f in filt]}")
    return True


def evaluate_session(conditions, seed_start, seed_count, mc_version, num_workers,
                     stop_event, on_match, on_progress, search_mode="inc", on_mode=None,
                     seed_list=None, on_resume=None, on_profile=None, max_found=0,
                     options=None):
    """The search. options (08/10, "48-bit family"): hide_sisters = one seed per
    family; struct_only = the structure seed alone decides. A nested call (an
    engine running part of a session) keeps the options of the outer one."""
    depth = getattr(_OPT_TLS, "depth", 0)
    if depth == 0:
        _OPT_TLS.opts = dict(options or {})
        if _OPT_TLS.opts.get("struct_only") and struct_only_blocker(conditions):
            # (10/10) the structure seed cannot decide this session: exact
            # search instead
            _OPT_TLS.opts["struct_only"] = False
    _OPT_TLS.depth = depth + 1
    try:
        return _evaluate_session_outer(conditions, seed_start, seed_count, mc_version,
                                       num_workers, stop_event, on_match, on_progress,
                                       search_mode, on_mode, seed_list, on_resume,
                                       on_profile, max_found)
    finally:
        _OPT_TLS.depth = depth
        if depth == 0:
            _OPT_TLS.opts = {}


def _evaluate_session_outer(conditions, seed_start, seed_count, mc_version, num_workers,
                            stop_event, on_match, on_progress, search_mode="inc",
                            on_mode=None, seed_list=None, on_resume=None, on_profile=None,
                            max_found=0):
    """The search (see _evaluate_session_impl). With on_profile, the GPU
    pipelines report per-condition statistics too (01/10): their validators
    count into _GPU_PROF, reported every 0.5 s and at the end. The C engine
    keeps reporting its own; nothing is reported while the buffer is empty."""
    args = (conditions, seed_start, seed_count, mc_version, num_workers, stop_event,
            on_match, on_progress, search_mode, on_mode, seed_list, on_resume,
            on_profile, max_found)
    if on_profile is None or seed_list is not None:
        return _evaluate_session_impl(*args)
    n = len(active_conditions(conditions))
    if n == 0 or n > 128:
        return _evaluate_session_impl(*args)
    try:                        # a libcubiomes older than p101 has no counters: no
        if not hasattr(CubiomesLib()._lib, "ssa_test_seeds_batch_prof"):   # empty rows
            return _evaluate_session_impl(*args)
    except Exception:
        return _evaluate_session_impl(*args)
    buf = (ctypes.c_int64 * (5 * n + 1))()
    _GPU_PROF.update(buf=buf, n=n, pos=None, scanned=0, kept=0)
    done = threading.Event()

    def _report():
        r = _gpu_prof_rows()
        if r is not None:
            try:
                on_profile(*r)
            except Exception:
                pass

    def _loop():
        while not done.wait(0.5):
            _report()
    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    try:
        return _evaluate_session_impl(*args)
    finally:
        done.set()
        th.join(timeout=2)
        _report()
        _GPU_PROF.update(buf=None, n=0, pos=None)


# ── Parallel post-filter (03/10) ──────────────────────────────────────────────
# The exact Python check of the C engine's candidates (stronghold filters, End
# portal links, all-128 joint links) ran in ONE thread inside the drain: with
# common candidates the C engine filled its buffer and waited → 500 k/s then
# 1 k/s, in waves. Now batches of candidates go to worker processes (every core).
_PF_W = {}
_PPF_TLS = threading.local()
# the running search's Pause / Resume callbacks for _hybrid_loop (GPU pipelines,
# 08/10): set by _evaluate_session_impl0 for a seed-by-seed range search
_HYB_TLS = threading.local()
_OPT_TLS = threading.local()          # evaluate_session(options=…) of this thread


def _fam_opts(search_mode):
    """(hide_sisters, struct_only) of the running search ("48-bit family" only)."""
    o = getattr(_OPT_TLS, "opts", None) or {}
    if search_mode != "fam48":
        return False, False
    so = bool(o.get("struct_only"))
    return bool(o.get("hide_sisters")) or so, so


def struct_only_blocker(conditions):
    """Why "Structure seed only" cannot decide this session (10/10), or None.
    That mode only checks the 48-bit POSITIONS of the top-level structures: a
    linked condition (village near a village, dungeon near a dungeon) was never
    tested, nor was a 1.16 dungeon / lava lake real (it depends on the biomes,
    i.e. on the 64 bits) -> results with one dungeon only."""
    try:
        act = active_conditions(conditions)
    except Exception:
        act = [c for c in conditions if c.get("enabled", True)]
    for c in act:
        if c.get("type") in ("dungeon", "lava_lake"):
            return "1.16 dungeons / lava lakes depend on the biomes (the whole seed)"
    for c in act:
        if int(c.get("relative", 0) or 0):
            return "it has linked conditions, which only a full test checks"
    return None


def struct_only_seed(low):
    """The one seed shown for a structure seed (Structure seed only): its lower
    48 bits with fixed pseudo-random upper bits (the same for the same structure
    seed, so a resumed search does not list it twice)."""
    low = int(low) & ((1 << 48) - 1)
    hi = ((low * 0x9E3779B97F4A7C15) >> 40) & 0xFFFF
    v = low | (hi << 48)
    return v - (1 << 64) if v >= (1 << 63) else v


def _pf_worker_batch(args):
    kind, payload, mc, items = args
    lib = _PF_W.get("lib")
    if lib is None:
        lib = _PF_W["lib"] = CubiomesLib()
    out = []
    for s, det in items:
        try:
            if kind == "joint":
                ok, mp = _joint_seed_eval(payload, int(s), mc, lib)
            elif kind == "full":            # the whole tree, Python evaluator (08/10)
                ok, summ = evaluate_seed(payload, int(s), mc, lib)
                if ok:
                    out.append((int(s), summ or det))
                continue
            elif kind == "exy":             # absolute Exact Y conditions: batched below
                break
            else:
                ok, mp = _stronghold_session_ok(lib, mc, int(s), payload)
        except Exception:
            ok, mp = False, None
        if ok:
            out.append((int(s), f"{mp[0]},{mp[1]}" if mp else det))
    if kind == "exy":
        out = _exy_abs_check(payload, mc, items)
    return out


def _exy_abs_check(filt, mc, items):
    """The ABSOLUTE Exact Y conditions (filt = _precise_filter_params of each) on
    a batch of candidates, block-exact terrain in C (one context per thread)."""
    import numpy as _np
    import terrain as _terr
    if not items:
        return []
    arr = _np.array([int(s) for s, _d in items], dtype=_np.int64)
    det = {int(s): d for s, d in items}
    keep = arr
    for (ax, az, r, ymn, ymx, cv, inv, cov, conf, step) in filt:
        if len(keep) == 0:
            break
        m = _terr.precise_y_filter_ctx(keep, mc, ax, az, r, ymn, ymx, caves=cv, invert=inv,
                                       sample_step=step, coverage=cov, confidence=conf)
        if m is None:
            raise RuntimeError("the terrain library has no Exact Y filter (rebuild it)")
        keep = keep[m.astype(bool)]
    return [(int(s), det.get(int(s), "")) for s in keep.tolist()]


def _pf_worker_batch2(args):
    """_pf_worker_batch + (seeds checked, seconds) for the statistics (08/10)."""
    t = time.perf_counter()
    out = _pf_worker_batch(args)
    return out, len(args[3]), time.perf_counter() - t


class _ParallelPostFilter:
    BATCH = 128
    MAX_WAIT = 0.25                     # a lone candidate is checked within ~¼ s

    def __init__(self, kind, payload, mc, nworkers, emit):
        self.kind, self.payload, self.mc = kind, payload, int(mc)
        self.nw = max(1, int(nworkers))
        self.emit = emit
        self.items, self.t_first = [], None
        self.futs = []
        self.lock = threading.Lock()
        self.ex = None
        self.failed = False
        self.closed = threading.Event()
        self.n_checked = self.n_passed = 0     # statistics (08/10)
        self.after_close = None
        self.secs = 0.0
        self.th = threading.Thread(target=self._loop, name="ssa-postfilter", daemon=True)
        self.th.start()

    def _note(self, n, k, dt):
        with self.lock:
            self.n_checked += int(n); self.n_passed += int(k); self.secs += float(dt)

    def _sync(self, items):             # no worker processes: in this thread
        res, n, dt = _pf_worker_batch2((self.kind, self.payload, self.mc, items))
        self._note(n, len(res), dt)
        for s, d in res:
            self.emit(s, d)

    def _submit_locked(self):
        items, self.items, self.t_first = self.items, [], None
        if not items:
            return
        if self.ex is None and not self.failed:
            try:
                import concurrent.futures as _cf
                self.ex = _cf.ProcessPoolExecutor(max_workers=min(self.nw, 61))
            except Exception:
                self.failed = True
        if self.failed:
            self._sync(items)
            return
        try:
            self.futs.append((self.ex.submit(_pf_worker_batch2,
                                             (self.kind, self.payload, self.mc, items)), items))
        except Exception:
            self.failed = True
            self._sync(items)

    def _collect(self, block=False):
        with self.lock:
            futs = list(self.futs)
        for f, items in futs:
            if not block and not f.done():
                continue
            try:
                res, n, dt = f.result()
            except Exception:           # a worker died: check these here
                res, n, dt = _pf_worker_batch2((self.kind, self.payload, self.mc, items))
            with self.lock:
                try:
                    self.futs.remove((f, items))
                except ValueError:
                    continue
            self._note(n, len(res), dt)
            for s, d in res:
                self.emit(s, d)
            if block:
                return

    def add(self, seed, details):
        with self.lock:
            self.items.append((int(seed), details))
            if self.t_first is None:
                self.t_first = time.time()
            if len(self.items) >= self.BATCH:
                self._submit_locked()
            backlog = len(self.futs)
        if backlog > 4 * self.nw:       # the engine waits for the checks (honest speed)
            self._collect(block=True)

    def _loop(self):
        while not self.closed.wait(0.05):
            with self.lock:
                if self.items and time.time() - self.t_first > self.MAX_WAIT:
                    self._submit_locked()
            self._collect()

    def close(self):
        """Everything handed in is checked and reported (pause: nothing lost)."""
        self.closed.set()
        self.th.join(timeout=5)
        with self.lock:
            self._submit_locked()
        while True:
            with self.lock:
                left = len(self.futs)
            if not left:
                break
            self._collect(block=True)
        if self.ex is not None:
            try:
                self.ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass


def _evaluate_session_impl(*args):
    try:
        return _evaluate_session_impl0(*args)
    finally:
        _HYB_TLS.on_resume = None
        _HYB_TLS.on_mode = None
        ppf = getattr(_PPF_TLS, "obj", None)
        if ppf is not None:
            _PPF_TLS.obj = None
            ppf.close()
            _ac = getattr(ppf, "after_close", None)
            if _ac is not None:
                try:
                    _ac()
                except Exception:
                    pass


def _evaluate_session_impl0(
        conditions: List[dict],
        seed_start: int,
        seed_count: int,
        mc_version: int,
        num_workers: int,
        stop_event: threading.Event,
        on_match: Callable[[int, str], None],
        on_progress: Callable[[int, int], None],
        search_mode: str = "inc",
        on_mode: Callable[[int], None] = None,
        seed_list=None,
        on_resume: Callable[[int], None] = None,
        on_profile: Callable = None,
        max_found: int = 0,
) -> None:
    """
    max_found : > 0 → the C engine stops by itself after that many matches
    (result limit / safety cap applied at the source, p96).
    Main search loop — tries the C-native multithreaded engine first,
    falls back to single-threaded Python if the engine is not compiled yet.
    seed_list : if given, test EXACTLY those seeds (Python per-seed path) and
    stop when all are done (ignores seed_start/seed_count).
    """
    import ctypes as ct
    import time

    # Snapshot: deep copies of the active conditions, parent links renumbered.
    # From here on the search never sees the UI's live list (edits during a
    # search can no longer change what is evaluated) and every path — C, GPU,
    # Python, seed list — works on the same tree.
    conditions = active_conditions(conditions)
    enabled = conditions
    if not enabled:
        return
    # GPU pipelines (_hybrid_loop) scan seed by seed: they can give a resume
    # point only for an Incremental range search (08/10)
    _HYB_TLS.on_resume = (on_resume if (seed_list is None and search_mode == "inc")
                          else None)
    _HYB_TLS.on_mode = on_mode
    # "48-bit family" on a seed list (08/10): the list's families, i.e. every
    # sister seed of each listed seed (before: only the listed seeds)
    if seed_list is not None and search_mode == "fam48" and not isinstance(seed_list, _SisterList):
        seed_list = _SisterList(seed_list, stop_event)
    _hide_sis, _struct_only = _fam_opts(search_mode)
    if _hide_sis:
        # one result per 48-bit family; the engines also skip the rest of a
        # family once it matched (flags below), this keeps it exact
        _fam_seen = set()
        _fam_lock = threading.Lock()
        _fam_base = on_match

        def on_match(_seed, _details="", _b=_fam_base):
            lo = int(_seed) & ((1 << 48) - 1)
            with _fam_lock:
                if lo in _fam_seen:
                    return
                _fam_seen.add(lo)
            if _struct_only:
                _seed = struct_only_seed(lo)
                _details = _details or "structure seed only"
            _b(_seed, _details)

    # ── Fast path : first_stronghold eye/maxY SEUL → moteur de l'onglet ──────
    # Si la seule condition est un first_stronghold avec eye/maxY (pas corrupted),
    # on route vers search_end_portal_seeds : MÊME moteur que l'onglet Stronghold
    # (pré-filtre Gaider C natif → ~3M sps en CPU, encore plus en GPU), au lieu
    # du post-filtre BFS lent. C'est "strictement la même chose".
    if (len(enabled) == 1
            and enabled[0].get("type") in ("first_stronghold", "stronghold")):
        _p = enabled[0].get("params", {})
        _eye_on = _eye_filter_on(_p)
        if (_eye_on or _p.get("maxy_enabled")) and not _p.get("corrupt_enabled"):
            _min_eyes = int(_p.get("min_eyes", 0)) if _eye_on else 0
            _mmy = int(_p.get("min_max_y", 60)) if _p.get("maxy_enabled") else None
            if _mmy is not None and _p.get("maxy_target") == "room":
                from stronghold_eyes import RoomY
                _mmy = RoomY(_mmy)                 # top of the portal room (03/10)
            # ── Top Y 1.16 by 48-bit families (03/10): the Stronghold page's engine
            # (topy_family, exact, CPU + GPU hybrid). Only in the session's 48-bit
            # family mode (its Start / Count / resume are in families).
            import topy_family as _tf
            _thr_ok = (_mmy is not None and int(mc_version) in _tf.VERSIONS
                       and int(_mmy) >= (_tf.MIN_FAST_Y_ROOM if _p.get("maxy_target") == "room"
                                         else _tf.MIN_FAST_Y))
            if (_thr_ok and search_mode == "fam48" and seed_list is None
                    and not _scan_128(_p)):
                _exact = bool(_p.get("exact_eyes"))

                def _fcb(seed, eyes, bx, bz, *_a):
                    if _min_eyes:
                        if (int(eyes) != _min_eyes) if _exact else (int(eyes) < _min_eyes):
                            return
                    on_match(int(seed), _loc_text(_p, seed, mc_version, bx, bz))

                def _fprog(done, total):
                    if on_progress is not None:     # families (the runner's fam48 unit)
                        on_progress(int(done) // _tf.FAMILY, int(total) // _tf.FAMILY)

                def _fstatus(msg):
                    if on_mode is not None:
                        on_mode(("note", str(msg)))
                if on_mode is not None:
                    on_mode(1)
                _tf.search_topy_families(
                    int(seed_start) & _tf.M48, int(seed_count) * _tf.FAMILY if seed_count else 0,
                    _mmy, _fcb, stop_event, _fprog, max(1, num_workers), int(mc_version),
                    # "Hide sister seeds": one seed per tall stronghold, found sooner
                    one_per_family=bool(_p.get("hide_sisters", True)),
                    use_gpu=bool(_p.get("topy_gpu", True)),
                    status_callback=_fstatus,
                    resume_callback=(on_resume if on_resume is not None else None))
                return
            from stronghold_eyes import search_end_portal_seeds
            # ── All 128 sur une PLAGE ────────────────────────────────────────
            # Le chemin range de search_end_portal_seeds ignore scan_128 : seul le
            # chemin seed_list a le moteur natif 128 (cb_strongholds128_arr) +
            # ProcessPool MULTI-CŒURS (le même que l'onglet Stronghold, PROUVÉ).
            # On expose donc la plage comme seed_list lazy → 128 en C natif parallèle.
            _sl = seed_list
            if _sl is None and _scan_128(_p):
                _sl = _RangeSeedList(seed_start, seed_count, stop_event)
            # scan_128 = CPU natif 128 (le pipeline GPU 128 est 1.13-1.17 only).
            _use_gpu = (bool(_p.get("eye_gpu", True)) and _mmy is None
                        and not _scan_128(_p))
            if on_mode is not None:
                on_mode(0)
            from broken_search import ResumeTracker
            _etrk = (ResumeTracker(seed_start, ["e"])
                     if (_sl is None and on_resume is not None) else None)
            search_end_portal_seeds(
                seed_start, _inc_count(seed_count, search_mode), _min_eyes,
                # callback(seed, eyes, bx, bz) → on garde bx,bz comme "x,z" dans
                # details pour un TP instantané au clic sur le résultat.
                callback=lambda s, *a, **k: on_match(
                    int(s), (_loc_text(_p, s, mc_version, a[1], a[2]) if len(a) >= 3 else "")),
                stop_event=stop_event, progress_callback=on_progress,
                num_workers=max(1, num_workers), mc_version=mc_version,
                # 1.18+: ±15-chunk prefilter (lossless); 1.13-1.17: the LCG
                # prefilter lost about half the results (E21) → exact path
                fast_mode=(int(mc_version) >= 22), use_gpu=_use_gpu, min_max_y=_mmy,
                seed_list=_sl, scan_128=_scan_val(_p),
                exact_eyes=bool(_p.get("exact_eyes")),
                chunk_callback=_etrk.chunk_cb("e") if _etrk is not None else None,
            )
            if _etrk is not None and _etrk.used:     # Pause / Resume (03/10)
                on_resume(_etrk.point())
            return

    # ── Fast path : first_stronghold « Broken portal » SEUL → moteur de l'onglet
    # Stronghold (03/10). Avant : le moteur C émettait CHAQUE seed (toutes ont un
    # stronghold) et le post-filtre Python les testait une par une dans un seul
    # fil (< 1 000 sps, CPU presque au repos). Ici : la même recherche que la page
    # Stronghold (porte native ruined portal + lacs, processus sur tous les cœurs),
    # même règle (≥ min_frames prédits), donc les mêmes résultats.
    if (len(enabled) == 1
            and enabled[0].get("type") in ("first_stronghold", "stronghold")):
        _p = enabled[0].get("params", {})
        if _p.get("corrupt_enabled"):
            import lake_portals as _LP
            _mc = int(mc_version)
            _srcs = [k for k in (_p.get("broken_sources") or ["ruined"])
                     if (k == "ruined" and _mc <= 21)
                     or (k in ("lava", "water") and _mc in _LP.LAKE_VERSIONS)]
            if _srcs:
                from broken_search import run_broken_sources
                _need = max(1, int(_p.get("min_frames", 1) or 1))
                if on_mode is not None:
                    on_mode(0)

                # eyes / Top Y with it (03/10): checked on the stronghold of each
                # broken-portal hit (rare) instead of every seed in Python
                _others = dict(_p, corrupt_enabled=False)
                _extra = _eye_filter_on(_others) or bool(_others.get("maxy_enabled"))
                _tls = threading.local()

                def _sh_ok(seed, sx, sz):
                    if not _extra:
                        return True
                    if sx is None or sz is None:          # the first stronghold
                        lib = getattr(_tls, "lib", None)
                        if lib is None:
                            lib = _tls.lib = CubiomesLib()
                        lib.setup_and_seed(mc=_mc, seed=int(seed))
                        shs = lib.find_strongholds(max_count=1, radius_blocks=None)
                        if not shs:
                            return False
                        sx, sz = shs[0]
                    import types as _ty
                    try:
                        return _stronghold_extra_ok(_ty.SimpleNamespace(seed=int(seed), mc_version=_mc),
                                                    _others, int(sx), int(sz))
                    except Exception:
                        return False

                def _rcb(seed, ex, ey, ez, rx, ry, rz, under, *_a):
                    if int(under) >= _need and _sh_ok(seed, *(_a[1:3] if len(_a) >= 3
                                                             else (None, None))):
                        on_match(int(seed), f"{int(ex)},{int(ez)}")

                def _lcb(seed, ex, ey, ez, lx, ly, lz, npred, kind, sx, sz, *_a):
                    if int(npred) >= _need and _sh_ok(seed, sx, sz):
                        on_match(int(seed), f"{int(ex)},{int(ez)}")
                _stop = stop_event if stop_event is not None else threading.Event()
                from broken_search import ResumeTracker
                _trk = (ResumeTracker(seed_start, ["ruined", "lakes"])
                        if (seed_list is None and on_resume is not None) else None)
                run_broken_sources(_srcs, seed_start, _inc_count(seed_count, search_mode),
                                   max(1, num_workers), _stop,
                                   on_progress or (lambda *_x: None), _mc,
                                   _scan_val(_p), seed_list, _rcb, _lcb, tracker=_trk)
                if _trk is not None:                 # Pause / Resume (03/10)
                    on_resume(_trk.point())
                return

    # ── Post-filtre stronghold eye/maxY/corrupted ────────────────────────────
    # Le moteur C/GPU ignore ces params (logique Python). On enveloppe on_match :
    # chaque candidat émis (déjà validé pour les autres conditions) est re-vérifié
    # côté Python sur les conditions stronghold uniquement. Coût par RÉSULTAT (rare).
    _joint_children = _session_joint_scan128_children(conditions)
    # stronghold in End portal mode with linked conditions (03/10): the engines
    # pre-filter with widened links, every candidate is checked exactly here
    if _portal_link_children(conditions):
        _joint_children = True
        enabled = _portal_c_view(enabled)
    # candidates the C engine emitted but the Python post-filter did not check
    # because Stop / Pause came first: the resume point is moved back to them
    # (before, they were dropped and the resume point went past them → seeds
    # never tested; audit 28/09)
    _pf_pending = []
    _sh_pf = [c for c in enabled
              if c.get("type") in ("stronghold", "first_stronghold")
              and _stronghold_extra_enabled(c.get("params", {}))]
    # Exact Y linked to another condition (08/10): the C engine runs the rest of
    # the tree on every thread, each of its seeds is checked with the whole tree
    # by worker processes (statistics, Pause / Resume as a C search)
    # (absolute Exact Y too: the old streaming post-filter had no statistics and
    # no Pause, and kept a single thread on the candidates)
    _exy_view = None
    if (not _joint_children and not _sh_pf
            and any(c.get("type") == "precise_y" for c in enabled)):
        _exy_view = _exy_c_view(enabled)
        if _exy_view is not None:
            try:
                if _build_ssacond_array(_exy_view, CubiomesLib()._lib) is None:
                    _exy_view = None
            except Exception:
                _exy_view = None
    if _exy_view is not None:
        _x_full = list(enabled)
        _x_abs = all(int(c.get("relative", 0) or 0) == 0 for c in _x_full
                     if c.get("type") == "precise_y")
        _x_kind, _x_pay = "full", _x_full
        if _x_abs:
            # only absolute Exact Y: the candidates need just those (batched C)
            try:
                import terrain as _terr_x, numpy as _np_x
                if _terr_x.precise_y_filter_ctx(_np_x.zeros(1, _np_x.int64), mc_version,
                                                0, 0, 0, -(1 << 30), 1 << 30) is not None:
                    _x_kind = "exy"
                    _x_pay = [_precise_filter_params(c) for c in _x_full
                              if c.get("type") == "precise_y"]
            except Exception:
                pass
        _x_base = on_match
        max_found = 0       # the engine's matches are candidates, not results
        _ppf_x = _ParallelPostFilter(_x_kind, _x_pay, mc_version, num_workers, _x_base)
        _PPF_TLS.obj = _ppf_x
        _x_rows = _exy_view_rows(_x_full)          # view row -> session row
        enabled = _exy_view
        if on_mode is not None:
            try: on_mode(("note", "Exact Y checked on the C engine's candidates"))
            except Exception: pass
        if on_profile is not None:
            _x_prof = on_profile
            _x_hz = _ticks_hz()

            _x_last = []

            def on_profile(rows, total, _n=len(_x_full)):
                _x_last[:] = [(rows, total)]
                full = [None] * _n
                for k, r in enumerate(rows):
                    if k < len(_x_rows):
                        full[_x_rows[k]] = r
                with _ppf_x.lock:
                    nc, npass, secs = _ppf_x.n_checked, _ppf_x.n_passed, _ppf_x.secs
                # the engine times 1 seed in 32: the same scale for the post-filter
                tk = int(secs * _x_hz / 32) if _x_hz else 0
                ex = [i for i, c in enumerate(_x_full) if c.get("type") == "precise_y"]
                for j, i in enumerate(ex):
                    full[i] = (nc, npass, tk if j == 0 else 0, nc, npass)
                for i in range(_n):
                    if full[i] is None:
                        full[i] = (0, 0, 0, 0, 0)
                _x_prof(full, int(total) + tk)
            # once every candidate is checked (end / pause): the final numbers
            _ppf_x.after_close = lambda: _x_last and on_profile(*_x_last[0])

        def _exy_postfilter_on_match(_seed, _details=""):
            if stop_event is not None and stop_event.is_set():
                _pf_pending.append(int(_seed))
                return
            _ppf_x.add(_seed, _details)
        on_match = _exy_postfilter_on_match
    elif _joint_children:
        # ── Linking JOINT scan_128 ────────────────────────────────────────────
        # Le moteur C a sérialisé le stronghold scan_128 en ANNEAU et émet donc
        # « enfant (village) près d'UN stronghold » — candidats RARES. Ici on
        # vérifie que ce stronghold a bien les yeux/maxY, via l'éval jointe
        # COMPLÈTE (village + yeux sur le MÊME stronghold). Le BFS des yeux ne
        # tourne que sur ces rares candidats → rapide. match_pos = le bon stronghold.
        import threading as _th_j
        _j_tls = _th_j.local()
        _j_base = on_match
        max_found = 0       # the engine's matches are candidates, not results (03/10)
        _ppf_j = _ParallelPostFilter("joint", conditions, mc_version, num_workers, _j_base)
        _PPF_TLS.obj = _ppf_j

        def _joint_postfilter_on_match(_seed, _details=""):
            if stop_event is not None and stop_event.is_set():
                _pf_pending.append(int(_seed))
                return
            _ppf_j.add(_seed, _details)
            return
            _lib = getattr(_j_tls, "lib", None)
            if _lib is None:
                _lib = CubiomesLib()
                _j_tls.lib = _lib
            _ok, _mp = _joint_seed_eval(conditions, int(_seed), mc_version, _lib)
            if _ok:
                _j_base(_seed, f"{_mp[0]},{_mp[1]}" if _mp else _details)
        on_match = _joint_postfilter_on_match
    elif _sh_pf:
        import threading as _th_pf
        # Lib PAR THREAD (pas de lock global) → le BFS des yeux tourne en
        # parallèle sur tous les workers. Respecte stop_event pour rester réactif.
        _pf_tls = _th_pf.local()
        _pf_base = on_match
        max_found = 0       # the engine's matches are candidates, not results (03/10)
        _ppf_s = _ParallelPostFilter("sh", _sh_pf, mc_version, num_workers, _pf_base)
        _PPF_TLS.obj = _ppf_s

        def _sh_postfilter_on_match(_seed, _details=""):
            if stop_event is not None and stop_event.is_set():
                _pf_pending.append(int(_seed))
                return
            _ppf_s.add(_seed, _details)
            return
            _lib = getattr(_pf_tls, "lib", None)
            if _lib is None:
                _lib = CubiomesLib()
                _pf_tls.lib = _lib
            _ok, _mp = _stronghold_session_ok(_lib, mc_version, int(_seed), _sh_pf)
            if _ok:
                # details = "x,z" du stronghold satisfaisant → TP instantané au
                # clic (pas de re-scan des 128).
                _det = f"{_mp[0]},{_mp[1]}" if _mp else _details
                _pf_base(_seed, _det)
        on_match = _sh_postfilter_on_match

    if seed_list is not None:
        if isinstance(seed_list, _SisterList):
            if on_mode is not None:
                on_mode(1)                     # progress in families of 65 536 seeds
            if _evaluate_sisters(enabled, seed_list, mc_version, num_workers,
                                 stop_event, on_match, on_progress):
                return
            if on_mode is not None:
                on_mode(0)                     # Python: the expanded seeds one by one
            for _k, _low in enumerate(seed_list.lows):
                if stop_event is not None and stop_event.is_set():
                    break
                _evaluate_seed_list(enabled, list(_SisterList([_low])), mc_version,
                                    num_workers, stop_event, on_match, None)
                if on_progress:
                    on_progress((_k + 1) << 16, len(seed_list))
            return
        _evaluate_seed_list(enabled, seed_list, mc_version, num_workers,
                            stop_event, on_match, on_progress)
        return

    # ── Fast Precise-Y batch path ────────────────────────────────────────────
    # If the search is Precise Y only (no other conditions) and the GPU toggle is
    # off, scan the range with the C multithreaded batch filter (≈ orders of
    # magnitude faster than per-seed GPU/Python). Anything else falls through.
    _precise = [c for c in enabled if c.get("type") == "precise_y"]
    if _precise:
        # Terrain depends on all 64 seed bits → force inc (fam48 is for structures
        # only). This holds for BOTH the CPU batch and the GPU per-seed fall-through.
        search_mode = "inc"
        if on_mode is not None:
            on_mode(0)
        # always scan the range with the batch when Exact Y is the only condition
        # and the GPU toggle is off.
        _others  = [c for c in enabled if c.get("type") != "precise_y"]
        # Le GPU Exact-Y est retiré (approché et plus lent que le CPU) : l'option
        # use_gpu des anciennes sessions est ignorée → chemins CPU exacts.
        _all_cpu = True
        # A "related to" (relative != 0) Exact Y has a PER-SEED centre (the parent's
        # position), so the fixed-centre CPU batch / post-filter can't handle it. When
        # any Exact Y is relative, fall through to the per-seed eval (which offsets the
        # disk by the parent position). The fast batch only fires when every Exact Y is
        # absolute.
        _all_absolute = all(int(c.get("relative", 0) or 0) == 0 for c in _precise)
        _nl_dbg(f"[exactY] dispatch: n_precise={len(_precise)} "
                f"others={[c.get('type') for c in _others]} all_cpu={_all_cpu} "
                f"all_abs={_all_absolute} mode={search_mode}")
        if _all_cpu and _all_absolute and not _others:
            if _run_precise_y_cpu_batch(_precise, seed_start, seed_count, mc_version,
                                        num_workers, stop_event, on_match, on_progress,
                                        on_mode):
                _nl_dbg("[exactY] CPU batch USED")
                return
            print("[exactY] CPU batch returned False (terrain.dll symbol missing?)",
                  flush=True)
        elif _all_cpu and _all_absolute and _others:
            # Exact Y + cheaper conditions, GPU toggle off → cheap conds first, then
            # CPU-batch-filter the candidates with Exact Y (no GPU).
            if _run_precise_y_postfilter(_precise, _others, seed_start, seed_count,
                                         mc_version, num_workers, stop_event,
                                         on_match, on_progress, on_mode):
                _nl_dbg("[exactY] CPU post-filter USED (cheap conds → batch Exact Y)")
                return

    # ── Crunchiness GPU pre-filter path ─────────────────────────────────────
    # Si la condition crunchiness a use_gpu=True avec weighting Commission,
    # on court-circuite le moteur C et on lance directement un batch CUDA
    # (gaider_cuda.crunchiness_filter_ex) → validation CPU pour les autres
    # conditions, exactement comme _run_crunch_search dans stronghold_eyes.py.
    #
    # POURQUOI : le moteur C-natif (ssa_engine_create) n'appelle jamais le
    # code Python de criteria.py — écrire dans _CRUNCH_CONFIG n'avait donc
    # aucun effet quand le moteur C prenait la main.
    # 08/10: the GPU scores every seed and the survivors go to the hybrid loop
    # (_hybrid_loop): ALL the CPU threads validate them with the C engine and
    # full-test raw seeds when the GPU is ahead, with Pause / Resume. Before, a
    # session with a spiral iterator never took this path (one CPU thread
    # validated: too slow), so a ticked "GPU" did nothing; and only the FIRST
    # crunchiness condition was looked at, even when it was not the GPU one.
    _crunch_cond = next((c for c in enabled if c.get("type") == "crunchiness"
                         and int(c.get("relative", 0) or 0) == 0
                         and (c.get("params") or {}).get("use_gpu")), None)
    if _crunch_cond is not None:
        if True:
            try:
                import gaider_cuda as _gc
                import numpy as _np
                if not _gc.is_available():
                    raise RuntimeError("the CUDA library is not available")
                if not _gc.has_exact_crunchiness():
                    raise RuntimeError("the GPU library is older than the exact "
                                       "crunchiness filter — rebuild it "
                                       "(py tools/build_native.py)")
                _raw_v = CubiomesLib()._lib
                _v_res = _build_ssacond_array(enabled, _raw_v)
                if _v_res is None:
                    raise RuntimeError("this session's tree is not supported by the C engine")
                _v_arr, _v_nc = _v_res
                _raw_v.ssa_test_seeds_batch.argtypes = [
                    ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                    ct.POINTER(ct.c_int64), ct.c_int64,
                    ct.POINTER(ct.c_int64), ct.c_int64]
                _raw_v.ssa_test_seeds_batch.restype = ct.c_int64
                try:
                    from cubiomes_wrap import get_large_biomes
                    _v_large = 1 if get_large_biomes() else 0
                except Exception:
                    _v_large = 0
                # the crunchiness entry of the C array (same order as `enabled`),
                # so the GPU uses the very parameters the CPU re-test uses (p96)
                _pos = next(i for i, c in enumerate(enabled) if c is _crunch_cond)
                _cc = _v_arr[_pos]
                if int(_cc.ctype) != 19:
                    raise RuntimeError("the crunchiness condition was not found in the C array")
                if not hasattr(_raw_v, "ssa_crunch_gpu_params"):
                    raise RuntimeError("libcubiomes is older than the exact crunchiness "
                                       "filter — rebuild it (py tools/build_native.py --cubiomes)")
                _ch = (ct.c_uint64 * 2)(); _ol = (ct.c_uint64 * 6)(); _oh = (ct.c_uint64 * 6)()
                _wa = (ct.c_float * 6)(); _wb = (ct.c_float * 6)()
                _raw_v.ssa_crunch_gpu_params.restype = ct.c_int
                _raw_v.ssa_crunch_gpu_params.argtypes = [
                    ct.c_int, ct.c_int, ct.c_uint32, ct.POINTER(ct.c_float),
                    ct.POINTER(ct.c_uint64), ct.POINTER(ct.c_uint64), ct.POINTER(ct.c_uint64),
                    ct.POINTER(ct.c_float), ct.POINTER(ct.c_float), ct.c_int]
                _kp = _raw_v.ssa_crunch_gpu_params(
                    int(_cc.crunch_climate), int(_v_large), int(_cc.crunch_octave_mask),
                    _cc.crunch_weights, _ch, _ol, _oh, _wa, _wb, 6)
                _gp = dict(clim_hash=(_ch[0], _ch[1]), oct_lo=list(_ol[:_kp]),
                           oct_hi=list(_oh[:_kp]), w_a=list(_wa[:_kp]), w_b=list(_wb[:_kp]),
                           threshold=float(_cc.crunch_threshold), mode_flag=int(_cc.crunch_mode))

                def _scan(ss, n):
                    ss = ((int(ss) + (1 << 63)) % (1 << 64)) - (1 << 63)
                    _t, _c = _gc.crunchiness_filter_w(ss, int(n), max_results=int(n), **_gp)
                    return _np.asarray(_c, _np.int64)

                # a threshold that keeps most seeds: the GPU would not help
                _probe = 1 << 20
                _kept0 = len(_scan(seed_start, _probe))
                if _kept0 > _probe // 20:
                    raise RuntimeError(f"this crunchiness threshold keeps {_kept0 / _probe:.0%} "
                                       f"of the seeds: the GPU pre-filter would not help")

                _emit_lock = threading.Lock()

                def _validate(seeds_i64):
                    if seeds_i64 is None or len(seeds_i64) == 0:
                        return
                    seeds_i64 = _np.ascontiguousarray(seeds_i64, _np.int64)
                    nn = len(seeds_i64)
                    out = _np.empty(nn, _np.int64)
                    kept = _tsb(_raw_v, _v_arr, ct.c_int(_v_nc), ct.c_int(mc_version),
                                ct.c_int(_v_large),
                                seeds_i64.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn),
                                out.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(nn))
                    for k in range(int(kept)):
                        if stop_event.is_set():
                            break
                        with _emit_lock:
                            on_match(int(out[k]), "")

                def _warmup():                 # build g_child_order single-threaded
                    s0 = _np.zeros(1, _np.int64); o0 = _np.empty(1, _np.int64)
                    _raw_v.ssa_test_seeds_batch(
                        _v_arr, ct.c_int(_v_nc), ct.c_int(mc_version), ct.c_int(_v_large),
                        s0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1),
                        o0.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(1))

                if on_mode is not None:
                    try: on_mode(3)            # 3 = GPU (CUDA) + C validation
                    except Exception: pass
                _gpu_prof_target(enabled, _crunch_cond)
                _total_seeds = seed_count if seed_count > 0 else (1 << 48)
                _hybrid_loop(_scan, _validate, _warmup, seed_start, _total_seeds,
                             num_workers, stop_event, on_progress)
                return   # chemin CUDA terminé, on ne passe pas au moteur C
            except Exception as _gpu_ex:
                import traceback; traceback.print_exc()
                if on_mode is not None:
                    try:
                        on_mode(("gpu_status", f"Crunchiness GPU: {_gpu_ex} — "
                                               f"searching on the CPU instead."))
                    except Exception:
                        pass
    # ────────────────────────────────────────────────────────────────────────

    # ── Structure GPU 48-bit pre-filter path ───────────────────────────────
    # Si le mode est 48-bit family ET qu'au moins une condition de structure a
    # use_gpu=True, on déporte le filtre 48-bit des positions de structures sur
    # GPU (massivement parallèle), puis le CPU valide biomes + autres conditions
    # sur les seeds survivantes via ssa_test_seeds_batch. Fonctionne en session
    # MIXTE (structures + biomes). Repli silencieux si CUDA indisponible.
    _gpu_struct = any(c.get("params", {}).get("use_gpu")
                      for c in enabled
                      if c.get("type") in _structure_type_keys())
    import sys as _sys
    _nl_dbg(f"[GPU-DBG] search_mode={search_mode!r}  _gpu_struct={_gpu_struct}")
    # Dès qu'une condition de structure a use_gpu=True, on emprunte le pipeline
    # GPU — INDÉPENDAMMENT du mode affiché dans l'UI. Cocher « GPU CUDA » EST la
    # demande explicite d'utiliser le GPU 48-bit ; on ne veut pas que le mode
    # (Incremental / Blocks / 48-bit) puisse l'annuler silencieusement.
    if _gpu_struct:
        _gpu_msg = None
        try:
            import structure_cuda as _sc
            avail = _sc.is_available()
            _nl_dbg(f"[GPU-DBG] is_available={avail}  err={_sc.get_load_error()!r}")
            if avail:
                _lib = CubiomesLib()
                _jobs, _all_sup = _sc.build_jobs(enabled, _lib, mc_version)
                _nl_dbg(f"[GPU-DBG] n_jobs={len(_jobs)}  all_sup={_all_sup}")
                if _jobs:
                    if on_mode is not None:
                        try: on_mode(3)  # 3 = GPU actif (code spécial)
                        except Exception: pass
                    _LAST_GPU_FAIL["msg"] = None
                    _ok = _run_structure_gpu_search(
                        enabled, _jobs, seed_start, seed_count, mc_version,
                        _lib, stop_event, on_match, on_progress,
                        fam48=True, num_workers=num_workers,
                        inc=(search_mode == "inc"))
                    _nl_dbg(f"[GPU-DBG] _run returned {_ok}")
                    if _ok:
                        # (no trailing on_mode(1): the LAST mode reported must
                        # stay 3, it is how callers know the GPU really ran)
                        return  # pipeline GPU terminé
                    else:
                        _gpu_msg = (_LAST_GPU_FAIL["msg"]
                                    or "GPU : échec du pré-filtre — recherche relancée sur le CPU.")
                else:
                    _gpu_msg = ("GPU: no eligible structure (build_jobs empty). "
                                "This structure may not be supported by the kernel.")
                    _nl_dbg(f"[GPU-DBG] all_sup={_all_sup}")
            else:
                _gpu_msg = f"GPU unavailable: {_sc.get_load_error()}"
        except Exception as _ge:
            import traceback; traceback.print_exc()
            _gpu_msg = f"GPU error: {type(_ge).__name__}: {_ge}"
        # Remonter le statut GPU à l'appelant (affichage UI) via on_mode codes 10+
        if _gpu_msg is not None and on_mode is not None:
            try: on_mode(("gpu_status", _gpu_msg))
            except Exception: pass
    # ────────────────────────────────────────────────────────────────────────

    # ── Biome GPU prefilter path (1.18+ noise biomes) ──────────────────────────
    # Si une condition biome_sample a use_gpu=True et qu'on est en 1.18+, on
    # déporte le comptage des biomes (bruit FP32) sur GPU ; le CPU valide chaque
    # survivante avec la condition EXACTE (résultats toujours exacts). Repli
    # silencieux vers le moteur C si noise_gpu est indisponible.
    _bcond = _biome_gpu_eligible(enabled)
    if _bcond is not None and int(mc_version) >= 22:
        try:
            _lib_b = CubiomesLib()
            if on_mode is not None:
                try: on_mode(3)        # 3 = GPU actif
                except Exception: pass
            if _run_biome_gpu_search(enabled, _bcond, seed_start, seed_count,
                                     mc_version, _lib_b, stop_event,
                                     on_match, on_progress, num_workers):
                if on_mode is not None:
                    try: on_mode(3)        # 3 = GPU (CUDA), keep label
                    except Exception: pass
                return                 # pipeline GPU terminé
            _why = _GPU_DECLINE.pop(threading.get_ident(), None)
            if _why and on_mode is not None:
                try: on_mode(("gpu_status", "GPU pre-filter not used: " + _why))
                except Exception: pass
        except Exception:
            import traceback; traceback.print_exc()
    # ────────────────────────────────────────────────────────────────────────

    # ── Biome filter GPU prefilter (1.18+ "Overworld at scale") ─────────────────
    _fcond = _biome_filter_gpu_eligible(enabled)
    if _fcond is not None and int(mc_version) >= 22:
        try:
            _lib_f = CubiomesLib()
            if on_mode is not None:
                try: on_mode(3)
                except Exception: pass
            if _run_biome_filter_gpu_search(enabled, _fcond, seed_start, seed_count,
                                            mc_version, _lib_f, stop_event,
                                            on_match, on_progress, num_workers):
                if on_mode is not None:
                    try: on_mode(3)        # 3 = GPU (CUDA), keep label
                    except Exception: pass
                return
            _why = _GPU_DECLINE.pop(threading.get_ident(), None)
            if _why and on_mode is not None:
                try: on_mode(("gpu_status", "GPU pre-filter not used: " + _why))
                except Exception: pass
        except Exception:
            import traceback; traceback.print_exc()
    # ────────────────────────────────────────────────────────────────────────

    # ── Biome center GPU prefilter (1.18+ "Locate biome center") ────────────────
    _qcond = _biome_center_gpu_eligible(enabled)
    if _qcond is not None and int(mc_version) >= 22:
        try:
            _lib_q = CubiomesLib()
            if on_mode is not None:
                try: on_mode(3)
                except Exception: pass
            if _run_biome_center_gpu_search(enabled, _qcond, seed_start, seed_count,
                                            mc_version, _lib_q, stop_event,
                                            on_match, on_progress, num_workers):
                if on_mode is not None:
                    try: on_mode(3)        # 3 = GPU (CUDA), keep label
                    except Exception: pass
                return
            _why = _GPU_DECLINE.pop(threading.get_ident(), None)
            if _why and on_mode is not None:
                try: on_mode(("gpu_status", "GPU pre-filter not used: " + _why))
                except Exception: pass
        except Exception:
            import traceback; traceback.print_exc()
    # ────────────────────────────────────────────────────────────────────────

    # ── Climate GPU prefilter path (1.18+ "Locate climate extreme") ─────────────
    _ccond = _climate_gpu_eligible(enabled)
    if _ccond is not None and int(mc_version) >= 22:
        try:
            _lib_c = CubiomesLib()
            if on_mode is not None:
                try: on_mode(3)
                except Exception: pass
            if _run_climate_gpu_search(enabled, _ccond, seed_start, seed_count,
                                       mc_version, _lib_c, stop_event,
                                       on_match, on_progress, num_workers):
                if on_mode is not None:
                    try: on_mode(3)        # 3 = GPU (CUDA), keep label
                    except Exception: pass
                return
            _why = _GPU_DECLINE.pop(threading.get_ident(), None)
            if _why and on_mode is not None:
                try: on_mode(("gpu_status", "GPU pre-filter not used: " + _why))
                except Exception: pass
        except Exception:
            import traceback; traceback.print_exc()
    # ────────────────────────────────────────────────────────────────────────

    # ── Climate noise samples GPU prefilter (1.18+) ─────────────────────────────
    _ncond = _noise_gpu_eligible(enabled)
    if _ncond is not None and int(mc_version) >= 22:
        try:
            _lib_n = CubiomesLib()
            if on_mode is not None:
                try: on_mode(3)
                except Exception: pass
            if _run_noise_gpu_search(enabled, _ncond, seed_start, seed_count,
                                     mc_version, _lib_n, stop_event,
                                     on_match, on_progress, num_workers):
                if on_mode is not None:
                    try: on_mode(3)        # 3 = GPU (CUDA), keep label
                    except Exception: pass
                return
            _why = _GPU_DECLINE.pop(threading.get_ident(), None)
            if _why and on_mode is not None:
                try: on_mode(("gpu_status", "GPU pre-filter not used: " + _why))
                except Exception: pass
        except Exception:
            import traceback; traceback.print_exc()
    # ────────────────────────────────────────────────────────────────────────

    total = seed_count if seed_count > 0 else 0

    # ── Try C-native engine ──────────────────────────────────────────────────
    lib = CubiomesLib()
    result = _build_ssacond_array(enabled, lib._lib)

    if result is not None:
        arr, n_conds = result

        n_threads = max(1, num_workers)
        mode_codes = {"inc": 0, "fam48": 1, "blocks": 2}
        mode_code = mode_codes.get(search_mode, 0)
        try:
            from cubiomes_wrap import get_large_biomes
            large_bit = 1 if get_large_biomes() else 0
        except Exception:
            large_bit = 0
        large_encoded = large_bit | ((mode_code & 0xFF) << 8)
        _hs, _so = _fam_opts(search_mode)
        large_encoded |= ((1 if _hs else 0) | (2 if _so else 0)) << 16

        # Pass NULL callback — matches are delivered via ring buffer drain instead.
        # This eliminates the GIL bottleneck: the C threads never need to acquire
        # the GIL per match, so they scale linearly with thread count.
        eng = lib._lib.ssa_engine_create(
            arr, ct.c_int(n_conds),
            ct.c_int(mc_version), ct.c_int(large_encoded),
            ct.c_int64(seed_start),
            ct.c_int64(seed_count if seed_count > 0 else 0),
            ct.c_int(n_threads),
            None, None,   # no callback — use drain_matches instead
        )
        if eng is None:
            result = None
        else:
            if max_found and max_found > 0 and hasattr(lib._lib, "ssa_engine_set_max_found"):
                try:
                    lib._lib.ssa_engine_set_max_found.argtypes = [ct.c_void_p, ct.c_int64]
                    lib._lib.ssa_engine_set_max_found.restype = None
                    lib._lib.ssa_engine_set_max_found(eng, ct.c_int64(int(max_found)))
                except Exception:
                    pass
            # Mode réellement choisi par l'auto-adaptation (0=INC,1=FAM48,2=BLOCKS)
            _eff_mode = 0
            if hasattr(lib._lib, "ssa_engine_effective_mode"):
                try:
                    _eff_mode = int(lib._lib.ssa_engine_effective_mode(eng))
                    if on_mode is not None:
                        on_mode(_eff_mode)
                except Exception:
                    pass
            # Units: the engine counts 64-bit seeds in INC / BLOCKS (one low =
            # 65 536 seeds) and families in FAM48; the range is in iteration
            # units (lows in BLOCKS) → report the total in the SAME units.
            if _eff_mode == 2 or (_eff_mode == 0 and mode_code != 0):
                # BLOCKS counts seeds; a 48-bit request run as Incremental (no
                # structure) had its family count scaled to seeds by the engine
                total = total * 65536
            done_c  = ct.c_int64(0)
            found_c = ct.c_int64(0)
            last_prog_report = 0
            # Drain buffer — 64K seeds at a time
            DRAIN_CAP = 65536
            _drain_buf = (ct.c_int64 * DRAIN_CAP)()
            _has_drain = hasattr(lib._lib, "ssa_engine_drain_matches")
            # per-condition profile (seeds reached / passed, own CPU time)
            _has_prof = on_profile is not None and hasattr(lib._lib, "ssa_engine_profile")
            _prof5 = hasattr(lib._lib, "ssa_engine_profile2")     # + per-check counts
            _prof_buf = (ct.c_int64 * ((5 if _prof5 else 3) * n_conds + 1))()
            _last_prof = [0.0]

            def _profile(force=False):
                if not _has_prof:
                    return
                now = time.time()
                if not force and now - _last_prof[0] < 0.5:
                    return
                _last_prof[0] = now
                try:
                    fn = lib._lib.ssa_engine_profile2 if _prof5 else lib._lib.ssa_engine_profile
                    fn.restype = ct.c_int
                    fn.argtypes = [ct.c_void_p, ct.POINTER(ct.c_int64), ct.c_int]
                    k = fn(eng, _prof_buf, n_conds)
                    w = 5 if _prof5 else 3
                    if k > 0:
                        # rows: [reached, branch passed, ticks(, checks, checks passed)]
                        on_profile([tuple(int(_prof_buf[w * i + j]) for j in range(w))
                                    for i in range(k)], int(_prof_buf[w * k]))
                except Exception:
                    pass

            _waited = False
            try:
                while True:
                    if stop_event.is_set():
                        lib._lib.ssa_engine_stop(eng)
                        break

                    # Drain ring buffer → forward matches to Python UI
                    if _has_drain:
                        while True:
                            n_drained = lib._lib.ssa_engine_drain_matches(
                                eng, _drain_buf, DRAIN_CAP)
                            for k in range(n_drained):
                                on_match(int(_drain_buf[k]), "")
                            if n_drained < DRAIN_CAP or stop_event.is_set():
                                break
                    if stop_event.is_set():          # e.g. result limit hit while draining
                        lib._lib.ssa_engine_stop(eng)
                        break

                    still_running = lib._lib.ssa_engine_progress(
                        eng, ct.byref(done_c), ct.byref(found_c))

                    done = int(done_c.value)
                    # Report on every poll (~20 Hz; the GUI throttles the on-screen
                    # refresh to ~2 Hz). The old gate only reported once `done` had
                    # advanced by 5000, so a slow session (heavy spiral scan, a few
                    # seeds/s) could run a long time showing "0 seeds checked / 0 s/s"
                    # while real results were already being found — because `done`
                    # climbed toward 5000 far too slowly to ever trigger a report.
                    on_progress(done, total)
                    _profile()

                    if not still_running:
                        break

                    stop_event.wait(0.05)            # wakes at once on Stop / Pause

                # the workers are really finished before the final reads (a stop
                # left them finishing their chunk: results pushed after the last
                # drain were lost, now they are drained below)
                if hasattr(lib._lib, "ssa_engine_join"):
                    lib._lib.ssa_engine_join.argtypes = [ct.c_void_p]
                    lib._lib.ssa_engine_join(eng)

                # Resume point (pause): first seed not fully tested, in the
                # engine's iteration units. Read before wait() frees the engine;
                # reported after the final drain (post-filter backlog, below).
                _rp = None
                if on_resume is not None and hasattr(lib._lib, "ssa_engine_resume_point"):
                    try:
                        lib._lib.ssa_engine_resume_point.restype = ct.c_int64
                        lib._lib.ssa_engine_resume_point.argtypes = [ct.c_void_p]
                        _rp = int(lib._lib.ssa_engine_resume_point(eng))
                    except Exception:
                        _rp = None

                # Final drain
                if _has_drain:
                    while True:
                        n_drained = lib._lib.ssa_engine_drain_matches(
                            eng, _drain_buf, DRAIN_CAP)
                        for k in range(n_drained):
                            on_match(int(_drain_buf[k]), "")
                        if n_drained < DRAIN_CAP:
                            break

                if _rp is not None:
                    if _pf_pending:
                        # the unchecked candidates come back on Resume: resume at the
                        # earliest of them (iteration order from the start seed; lows
                        # in 48-bit family / blocks modes)
                        if _eff_mode in (1, 2):             # effective mode: lows
                            _M, _unit, _base = (1 << 48), (lambda v: v & ((1 << 48) - 1)), seed_start & ((1 << 48) - 1)
                        else:
                            _M, _unit, _base = (1 << 64), (lambda v: v), seed_start
                        _cands = [_rp] + [_unit(v) for v in _pf_pending]
                        _best = min(_cands, key=lambda v: (v - _base) % _M)
                        if _M == (1 << 64):
                            _best = ((_best + (1 << 63)) % (1 << 64)) - (1 << 63)
                        _rp = _best
                    try:
                        on_resume(_rp)
                    except Exception:
                        pass

                _profile(force=True)
                try:
                    lib._lib.ssa_engine_progress(eng, ct.byref(done_c), ct.byref(found_c))
                except Exception:
                    pass
                lib._lib.ssa_engine_wait(eng)
                _waited = True
                on_progress(int(done_c.value), total)
                return
            except BaseException:
                if _waited:                    # the engine is already freed
                    raise
                # a callback raised (UI closing, bad result…): the C workers must
                # not keep running in the background (audit 28/09)
                try:
                    lib._lib.ssa_engine_stop(eng)
                    if hasattr(lib._lib, "ssa_engine_join"):
                        lib._lib.ssa_engine_join.argtypes = [ct.c_void_p]
                        lib._lib.ssa_engine_join(eng)
                    lib._lib.ssa_engine_wait(eng)
                except Exception:
                    pass
                raise

    # ── Python fallback ──────────────────────────────────────────────────────
    # (08/10) the seeds are evaluated by worker processes (every thread chosen),
    # not one by one in this thread; Pause gives the exact resume point (every
    # seed handed in is checked before the search returns)
    seed = seed_start
    done = 0
    progress_every = 500
    last_prog = 0
    if on_mode is not None:
        try: on_mode(0)
        except Exception: pass
    _ppf_py = _ParallelPostFilter("full", list(enabled), mc_version, num_workers, on_match)
    try:
        while True:
            if stop_event.is_set():
                break
            if seed_count > 0 and done >= seed_count:
                break
            _ppf_py.add(((seed + (1 << 63)) % (1 << 64)) - (1 << 63), "")
            done += 1
            seed += 1
            if done - last_prog >= progress_every:
                on_progress(done, total)
                last_prog = done
    finally:
        _ppf_py.close()
    on_progress(done, total)
    if on_resume is not None and search_mode == "inc":
        on_resume(((seed + (1 << 63)) % (1 << 64)) - (1 << 63))
