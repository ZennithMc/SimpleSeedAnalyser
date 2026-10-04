"""
ruined_portal.py — Recherche "Corrupted End Portal" (portail en ruine qui casse
un end portal de stronghold).

Pipeline (validé in-game, seed 3399 en 1.17.1) :
  1. PRE-FILTRE 48-bit PUR (rapide, sans biome, sans BFS) :
     anneau du 1er stronghold (initFirstStronghold, ±112) + portail en ruine le
     plus proche en X,Z (getStructurePos). On garde si distance < prefilter_thr.
  2. VALIDATION (sur les ~25% de candidats seulement) :
     - vraie position snappée du stronghold (avec biome, via nextStronghold)
     - centre EXACT du end portal (BFS Python, get_stronghold_portal_full)
     - centre EXACT du portail en ruine (variant RNG + Y surface + box, miroir
       corrigé : cubiomes mir==0 => MC FRONT_BACK)
     - distance 3D end portal <-> ruined portal ; hit si <= max_dist (~25).

Les fonctions C (cb_*) sont dans corrupted_bridge.c, compilé dans libcubiomes.
ATTENTION : supprimer libcubiomes.dll pour recompiler (sinon cb_* absent ->
has_corrupted() renvoie False et la recherche est indisponible).
"""
import os
import ctypes
import math
import multiprocessing as mp

import cubiomes_wrap
from stronghold_eyes import get_stronghold_portal_full, get_end_portal_frames

_U64 = (1 << 64) - 1
_REG = 40 * 16  # taille de région des portails en ruine (40 chunks) en blocs
_i32 = ctypes.POINTER(ctypes.c_int)

# Tailles des 13 templates (index 1..13) ; empreintes (lx,lz) chargées du JSON.
_PSX = [0, 6, 9, 8, 8, 10, 5, 9, 14, 10, 12, 11, 11, 16]
_PSY = [0, 10, 12, 9, 9, 10, 7, 7, 9, 8, 8, 17, 16, 16]
_PSZ = [0, 6, 9, 9, 9, 7, 7, 9, 9, 9, 10, 16, 16, 16]
_LAVA_REACH = 8  # portée verticale de la coulée de lave sous le portail en ruine
_GATE = 80  # porte X,Z (Chebyshev) centre ruine <-> début stronghold avant la BFS
_SCAN_GATE = 96  # looser ORIGIN-proximity gate used by the native batch pre-screen
_S128_CAP = 65536  # survivor-stronghold buffer for the 128-strongholds scan
_S128_CHUNK = 250  # seeds per chunk in 128 mode: small = live SPS + fast Stop
                   # (generating 128 biome-snapped strongholds/seed is heavy, so
                   #  short chunks keep progress updating and Stop responsive)
try:
    import json as _json
    _FOOTPRINT = {int(k): [tuple(c) for c in v] for k, v in _json.load(
        open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "ruined_footprints.json"))).items()}
except Exception:
    _FOOTPRINT = {}


def _xf(lx, lz, mFB, rot, px, pz, ox, oz):
    """Transforme un (lx,lz) local du template en (x,z) monde (rotation+miroir)."""
    n = lx
    n3 = lz
    if mFB:
        n = -n
    if rot == 2:
        tx = px + px - n
        tz = pz + pz - n3
    elif rot == 1:
        tx = px + pz - n3
        tz = pz - px + n
    elif rot == 3:
        tx = px - pz + n3
        tz = px + pz - n
    else:
        tx = n
        tz = n3
    return (tx + ox, tz + oz)


def _bind(lib):
    lib.cb_create.restype = ctypes.c_void_p
    lib.cb_setup.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint64]
    lib.cb_first_stronghold.argtypes = [ctypes.c_void_p, ctypes.c_int,
        ctypes.c_uint64, _i32, _i32, _i32, _i32]
    lib.cb_first_stronghold.restype = ctypes.c_int
    lib.cb_ruined_portal.argtypes = [ctypes.c_void_p, ctypes.c_int,
        ctypes.c_uint64, ctypes.c_int, ctypes.c_int,
        _i32, _i32, _i32, _i32, _i32]
    lib.cb_ruined_portal.restype = ctypes.c_int
    lib.cb_prefilter.argtypes = [ctypes.c_int, ctypes.c_uint64,
        _i32, _i32, _i32, _i32]
    lib.cb_prefilter.restype = ctypes.c_int
    lib.cb_ruined_detail.argtypes = [ctypes.c_void_p, ctypes.c_int,
        ctypes.c_uint64, ctypes.c_int, ctypes.c_int, _i32]
    lib.cb_ruined_detail.restype = ctypes.c_int
    # Native batch pre-screen (only in the recompiled DLL). Optional: if absent we
    # fall back to the per-seed Python prefilter loop.
    if hasattr(lib, "cb_corrupt_scan"):
        lib.cb_corrupt_scan.argtypes = [ctypes.c_void_p, ctypes.c_int,
            ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint64), ctypes.c_int]
        lib.cb_corrupt_scan.restype = ctypes.c_int
    if hasattr(lib, "cb_ruined_y"):
        lib.cb_ruined_y.argtypes = [ctypes.c_void_p, ctypes.c_int,
            ctypes.c_uint64, ctypes.c_int, ctypes.c_int]
        lib.cb_ruined_y.restype = ctypes.c_int
    # All-128 stronghold positions (for the "scan all strongholds" search mode).
    if hasattr(lib, "cb_strongholds128"):
        lib.cb_strongholds128.argtypes = [ctypes.c_void_p, ctypes.c_int,
            ctypes.c_uint64, ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        lib.cb_strongholds128.restype = ctypes.c_int
    # 1.18+ ruined variant (LCG setLargeFeatureSeed). Optional (recompiled DLL).
    if hasattr(lib, "cb_ruined_118"):
        lib.cb_ruined_118.argtypes = [ctypes.c_void_p, ctypes.c_int,
            ctypes.c_uint64, ctypes.c_int, ctypes.c_int, _i32]
        lib.cb_ruined_118.restype = ctypes.c_int
    # 128-strongholds corrupted gate (survivors only). Optional (recompiled DLL).
    if hasattr(lib, "cb_corrupt_scan128"):
        lib.cb_corrupt_scan128.argtypes = [ctypes.c_void_p, ctypes.c_int,
            ctypes.c_uint64, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        lib.cb_corrupt_scan128.restype = ctypes.c_int
    # same gate, first N strongholds per seed (3 = first ring, 03/10)
    if hasattr(lib, "cb_corrupt_scan_n"):
        lib.cb_corrupt_scan_n.argtypes = [ctypes.c_void_p, ctypes.c_int,
            ctypes.c_uint64, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        lib.cb_corrupt_scan_n.restype = ctypes.c_int


def has_corrupted() -> bool:
    """True si libcubiomes contient le pont corrupted (cb_prefilter)."""
    try:
        path = cubiomes_wrap._find_lib()
        if not path:
            return False
        lib = ctypes.CDLL(path)
        return hasattr(lib, "cb_prefilter") and hasattr(lib, "cb_ruined_portal")
    except Exception:
        return False


def has_118() -> bool:
    """True if libcubiomes has the 1.18+ ruined variant (cb_ruined_118)."""
    try:
        path = cubiomes_wrap._find_lib()
        if not path:
            return False
        return hasattr(ctypes.CDLL(path), "cb_ruined_118")
    except Exception:
        return False


# ── état par worker (process) ────────────────────────────────────────────────
_W = {}


def _winit(mc):
    path = cubiomes_wrap._find_lib() or cubiomes_wrap._compile_cubiomes()
    lib = ctypes.CDLL(path)
    _bind(lib)
    _W["lib"] = lib
    _W["ctx"] = lib.cb_create()
    cubiomes_wrap.apply_large_ctx(lib, _W["ctx"])
    _W["mc"] = mc
    if hasattr(lib, "cb_corrupt_scan"):
        _W["scan_buf"] = (ctypes.c_uint64 * 200000)()
    if hasattr(lib, "cb_corrupt_scan128"):
        _W["s128_seed"] = (ctypes.c_uint64 * _S128_CAP)()
        _W["s128_x"] = (ctypes.c_int * _S128_CAP)()
        _W["s128_z"] = (ctypes.c_int * _S128_CAP)()
    if hasattr(lib, "cb_strongholds128"):
        _W["s128_pos"] = (ctypes.c_int * 256)()  # 128 (x,z) for seed-list 128 mode
    # one-time diagnostic of the active fast path (helps debug throughput)
    try:
        nf = _frames_xz_native(0, 0, 0, True) is not None
    except Exception:
        nf = False
    try:
        if os.environ.get("SSA_DEBUG", "") in ("", "0"):   # developer trace only
            raise OSError
        import ssa_paths
        _d = ssa_paths.log_dir()
        dbg = os.path.join(_d, "corrupt_debug.txt")   # (was written next to the code)
        with open(dbg, "w") as f:
            f.write("cb_corrupt_scan=%s  cb_ruined_y=%s  native_frames=%s\n"
                    % (hasattr(lib, "cb_corrupt_scan"),
                       hasattr(lib, "cb_ruined_y"), nf))
    except Exception:
        pass


def _prefilter(seed):
    """Distance Chebyshev anneau-stronghold <-> portail en ruine le plus proche
    (X,Z). Pur 48-bit, sans biome."""
    lib = _W["lib"]
    a, b, c, d = (ctypes.c_int(0) for _ in range(4))
    return lib.cb_prefilter(_W["mc"], seed & _U64,
                            ctypes.byref(a), ctypes.byref(b),
                            ctypes.byref(c), ctypes.byref(d))


_GAIDER = None  # cached gaider_layouts module (or False if unavailable)


def _frames_xz_native(seed, cx, cz, pre118):
    """Fast native end-portal frame X,Z via gaider_layouts (C++ BFS). Returns
    (center_x, center_z, [(x,z) * 12]) or None (then caller falls back to the
    Python BFS). Much faster than get_end_portal_frames for the hot scan path."""
    global _GAIDER
    if _GAIDER is None:
        try:
            import gaider_layouts as _g
            _GAIDER = _g if _g.has_frames() else False
        except Exception:
            _GAIDER = False
    if not _GAIDER:
        return None
    try:
        return _GAIDER.frames_xz(seed, cx, cz, pre118)
    except Exception:
        return None


def _validate_at(seed, spx, spz):
    """CORRUPTION filter for ONE stronghold whose end-portal start chunk is
    (spx, spz) (block coords). A ruined portal whose X,Z footprint covers end
    portal frames, low enough for its lava/netherrack to reach them. This is the
    NECESSARY condition for a frame to be broken (the final result depends on the
    non-deterministic chunk generation order; the checker confirms exactly).
    Returns (ep, (corruptible, ruined_center, mirFB, templ)) or None.
    Shared by the 1st-stronghold search, the 128-strongholds search, and the
    single-seed per-stronghold corrupted check. Assumes the generator is set up
    (cheap cached cb_setup is re-called here for safety)."""
    lib = _W["lib"]
    mc = _W["mc"]
    ctx = _W["ctx"]
    cubiomes_wrap.apply_large_ctx(_W["lib"], ctx)   # large biomes world
    lib.cb_setup(ctx, mc, seed & _U64)  # cached (no-op if already set for this mc)
    # ── fast gate (C only): only run the (costly) Python BFS if a ruined portal
    # is geometrically close enough to the stronghold START to cover a frame. The
    # end portal sits ~3 chunks from the start; the footprint radiates ~8 blocks
    # → ruined center within _GATE of (spx, spz).
    rgx, rgz = spx // _REG, spz // _REG
    cand = []
    for ddx in (-1, 0, 1):
        for ddz in (-1, 0, 1):
            out = (ctypes.c_int * 10)()
            if not lib.cb_ruined_detail(ctx, mc, seed & _U64,
                    rgx + ddx, rgz + ddz, out):
                continue
            ti = out[1]
            if ti not in _FOOTPRINT:
                continue
            if max(abs(out[7] - spx), abs(out[9] - spz)) > _GATE:
                continue
            cand.append((out[1], out[2], out[3], out[4], out[5], out[6],
                         out[7], out[8], out[9], rgx + ddx, rgz + ddz))
    if not cand:
        return None
    # Frames X,Z: native (fast C++ BFS) if available, else the Python BFS. The
    # native path avoids the ~4 ms Python BFS on every gate survivor; the Python
    # BFS then runs only on the rare footprint+chunk candidates (to get the Y).
    pre118 = (mc <= 20)  # old FillerCorridor for 1.13-1.16 (== _use_old_filler)
    fy = None  # frame Y — filled lazily (Python BFS) only when a candidate exists
    fxz = _frames_xz_native(seed, spx >> 4, spz >> 4, pre118)
    if fxz is not None:
        ex, ez, fxy = fxz
        frames = [(x, 0, z) for (x, z) in fxy]
    else:
        fr = get_end_portal_frames(seed, spx, spz, mc)
        if fr is None:
            return None
        frames, ex, ez, fy = fr
    has_y = hasattr(lib, "cb_ruined_y")
    best = None
    for (ti, rot, mFB, yb, ox, oz, rcx, rcy, rcz, regX, regZ) in cand:
        pvx, pvz = _PSX[ti] // 2, _PSZ[ti] // 2
        # ruined "trigger" chunk: the chunk whose generation writes the whole
        # ruined template (1.16 = the template-origin chunk). A frame can only be
        # overwritten by the ruined if it sits in a DIFFERENT chunk than this one
        # (otherwise the stronghold room is written after the ruined and re-wins).
        trig = (ox >> 4, oz >> 4)
        fpw = {_xf(lx, lz, mFB, rot, pvx, pvz, ox, oz)
               for (lx, lz) in _FOOTPRINT[ti]}
        corruptible = sum(1 for f in frames
                          if (f[0], f[2]) in fpw
                          and (f[0] >> 4, f[2] >> 4) != trig)
        if corruptible < 1:
            continue
        # Now (and only now) get the real frame Y via the Python BFS, once.
        if fy is None:
            fr = get_end_portal_frames(seed, spx, spz, mc)
            if fr is None:
                return None
            _, ex, ez, fy = fr
        # Y filter: the ruined must vertically reach the frames. Surface ruined
        # portals (Y~70) far above a Y~30 end portal can never break it. cubiomes
        # Y is approximate -> generous window; the checker confirms exactly.
        if has_y:
            ryb = lib.cb_ruined_y(ctx, mc, seed & _U64, regX, regZ)
            if ryb != -999 and not (ryb - _LAVA_REACH) <= fy <= (ryb + _PSY[ti] - 1):
                continue
        if best is None or corruptible > best[0]:
            best = (corruptible, (rcx, rcy, rcz), mFB, ti)
    if best is not None:
        return (ex, fy if fy is not None else 0, ez), best
    return None


def _validate(seed, max_dist):
    """CORRUPTION filter for the FIRST stronghold of `seed` (hot search path)."""
    lib = _W["lib"]
    mc = _W["mc"]
    ctx = _W["ctx"]
    cubiomes_wrap.apply_large_ctx(_W["lib"], ctx)   # large biomes world
    lib.cb_setup(ctx, mc, seed & _U64)
    rx, rz, sx, sz = (ctypes.c_int(0) for _ in range(4))
    lib.cb_first_stronghold(ctx, mc, seed & _U64, ctypes.byref(rx),
                            ctypes.byref(rz), ctypes.byref(sx), ctypes.byref(sz))
    return _validate_at(seed, sx.value, sz.value)


def _validate_118(seed):
    """CORRUPTION filter for 1.18+ (mc 22+). Native frames (gaider, pre118=False)
    + native 1.18+ ruined variant (cb_ruined_118, LCG setLargeFeatureSeed) + the
    EXACT footprint overlap + cross-chunk + underground setup. NO Y filter — the
    Y depends on the terrain column scan (cubiomes can't get it exactly), so the
    checker confirms the Y. Returns (ep, (corruptible, ruined_center, mFB, ti))."""
    lib = _W["lib"]
    mc = _W["mc"]
    ctx = _W["ctx"]
    cubiomes_wrap.apply_large_ctx(_W["lib"], ctx)   # large biomes world
    if not hasattr(lib, "cb_ruined_118"):
        return None
    lib.cb_setup(ctx, mc, seed & _U64)
    rx, rz, sx, sz = (ctypes.c_int(0) for _ in range(4))
    lib.cb_first_stronghold(ctx, mc, seed & _U64, ctypes.byref(rx),
                            ctypes.byref(rz), ctypes.byref(sx), ctypes.byref(sz))
    spx, spz = sx.value, sz.value
    fxz = _frames_xz_native(seed, spx >> 4, spz >> 4, False)  # 1.18+ frames X,Z
    if fxz is None:
        return None
    ex, ez, fxy = fxz
    rgx, rgz = spx // _REG, spz // _REG
    out = (ctypes.c_int * 7)()
    best = None
    for ddx in (-1, 0, 1):
        for ddz in (-1, 0, 1):
            if not lib.cb_ruined_118(ctx, mc, seed & _U64, rgx + ddx, rgz + ddz, out):
                continue
            if out[6] != 0:        # setup: only UNDERGROUND reaches a buried portal
                continue
            cx, cz, ti, rot, mFB = out[1], out[2], out[3], out[4], out[5]
            if ti < 1 or ti > 13 or ti not in _FOOTPRINT:
                continue
            pvx, pvz = _PSX[ti] // 2, _PSZ[ti] // 2
            ox, oz = cx << 4, cz << 4
            fpw = {_xf(lx, lz, mFB, rot, pvx, pvz, ox, oz)
                   for (lx, lz) in _FOOTPRINT[ti]}
            corruptible = sum(1 for (fx, fz) in fxy
                              if (fx, fz) in fpw and (fx >> 4, fz >> 4) != (cx, cz))
            if corruptible < 1:
                continue
            if best is None or corruptible > best[0]:
                best = (corruptible, (ox, 0, oz), mFB, ti)
    if best is None:
        return None
    fr = get_end_portal_frames(seed, spx, spz, mc)  # lazy: real EP Y for display
    ey = fr[3] if fr else 0
    return (ex, ey, ez), best


class _Req(tuple):
    """(frames, epx, epz, rox, roz) + .sh = the stronghold's start chunk, which the
    Minecraft checker pins (its 1.16.1 stronghold search can differ, 03/10)."""
    def __new__(cls, t, sh):
        o = tuple.__new__(cls, t)
        o.sh = sh
        return o


def corrupt_request_for(seed, mc=20, sx=None, sz=None, _ctxlib=None):
    """For the checker VERIFY: returns (frames, epx, epz, rox, roz) where `frames`
    is the list of the 12 exact (x, y, z) end-portal frame positions, (epx,epz) is
    the end-portal center, and (rox,roz) is the origin (trigger) chunk of the
    nearest ruined portal. Returns None if there is no usable portal/ruined portal.
    (sx, sz) = a SPECIFIC stronghold start (block coords) to verify; if None, the
    1st stronghold is used (default). Pass the result's stronghold for 128 mode.
    _ctxlib = optional (lib, ctx) to reuse the context (batch)."""
    if _ctxlib is not None:
        lib, ctx = _ctxlib
    else:
        path = cubiomes_wrap._find_lib() or cubiomes_wrap._compile_cubiomes()
        lib = ctypes.CDLL(path)
        _bind(lib)
        ctx = lib.cb_create()
        cubiomes_wrap.apply_large_ctx(lib, ctx)
    lib.cb_setup(ctx, mc, seed & _U64)
    if sx is None or sz is None:
        rx, rz, sxc, szc = (ctypes.c_int(0) for _ in range(4))
        lib.cb_first_stronghold(ctx, mc, seed & _U64, ctypes.byref(rx),
                                ctypes.byref(rz), ctypes.byref(sxc), ctypes.byref(szc))
        sx, sz = sxc.value, szc.value
    fr = get_end_portal_frames(seed, sx, sz, mc)
    if fr is None:
        return None
    frames, ex, ez, fy = fr
    rgx, rgz = ex // _REG, ez // _REG
    best = None
    for ddx in (-1, 0, 1):
        for ddz in (-1, 0, 1):
            out = (ctypes.c_int * 10)()
            if not lib.cb_ruined_detail(ctx, mc, seed & _U64,
                    rgx + ddx, rgz + ddz, out):
                continue
            ti = out[1]
            if ti < 1 or ti > 13:
                continue
            cx, cz = out[7], out[9]
            d = max(abs(cx - ex), abs(cz - ez))
            if best is None or d < best[0]:
                best = (d, out[5] >> 4, out[6] >> 4)
    if best is None:
        return None
    return _Req((frames, ex, ez, best[1], best[2]), (sx >> 4, sz >> 4))


def corrupt_request_auto(seed, mc=20):
    """Like corrupt_request_for but targets a CORRUPTIBLE stronghold: the 1st if it
    is corruptible, otherwise scans the 128 strongholds and uses the first
    corruptible one. Keeps 'Verify all results' correct for 128 mode (where a
    result may be on a non-1st stronghold). Returns (frames,epx,epz,rox,roz)/None."""
    _ensure_local_ctx(mc)
    lib, ctx = _W["lib"], _W["ctx"]
    cubiomes_wrap.apply_large_ctx(_W["lib"], ctx)   # large biomes world
    lib.cb_setup(ctx, mc, seed & _U64)
    rx, rz, sxc, szc = (ctypes.c_int(0) for _ in range(4))
    lib.cb_first_stronghold(ctx, mc, seed & _U64, ctypes.byref(rx),
                            ctypes.byref(rz), ctypes.byref(sxc), ctypes.byref(szc))
    if corrupt_check_at(seed, sxc.value, szc.value, mc) is not None:
        return corrupt_request_for(seed, mc, sx=sxc.value, sz=szc.value,
                                   _ctxlib=(lib, ctx))
    if hasattr(lib, "cb_strongholds128"):
        buf = (ctypes.c_int * 256)()
        k = lib.cb_strongholds128(ctx, mc, seed & _U64, buf, 128)
        for i in range(k):
            sx, sz = buf[2 * i], buf[2 * i + 1]
            if corrupt_check_at(seed, sx, sz, mc) is not None:
                return corrupt_request_for(seed, mc, sx=sx, sz=sz, _ctxlib=(lib, ctx))
    return None


def corrupt_request_118(seed, mc=22):
    """Checker request for a 1.18+ corrupted candidate: the 12 exact end-portal
    frame positions (with Y) + the ruined ORIGIN chunk of the corruptible ruined.
    Returns (frames, epx, epz, rox, roz) or None."""
    _ensure_local_ctx(mc)
    r = _validate_118(seed)
    if r is None:
        return None
    (ex, ey, ez), (corruptible, (rox, roy, roz), mFB, ti) = r
    lib = _W["lib"]
    ctx = _W["ctx"]
    cubiomes_wrap.apply_large_ctx(_W["lib"], ctx)   # large biomes world
    lib.cb_setup(ctx, mc, seed & _U64)
    rx, rz, sx, sz = (ctypes.c_int(0) for _ in range(4))
    lib.cb_first_stronghold(ctx, mc, seed & _U64, ctypes.byref(rx),
                            ctypes.byref(rz), ctypes.byref(sx), ctypes.byref(sz))
    fr = get_end_portal_frames(seed, sx.value, sz.value, mc)
    if fr is None:
        return None
    frames, fex, fez, fy = fr
    return (frames, fex, fez, rox >> 4, roz >> 4)   # ruined origin chunk


def verify_seed(seed, mc=20, timeout=150.0, sx=None, sz=None):
    """EXACT verification via the perfect checker (real Minecraft 1.16.1). Returns
    {"natural":int, "forced":int, "broken":[(x,y,z,block),...], "ep":(epx,epz),
    "ruined_chunk":(rox,roz)} or None. mc 20 = 1.16 only (the checker is 1.16.1).
    (sx, sz) = a specific stronghold to verify (128 mode); None = 1st stronghold."""
    import checker_client
    req = corrupt_request_for(seed, mc, sx=sx, sz=sz)
    if req is None:
        return None
    frames, epx, epz, rox, roz = req
    out = checker_client.verify_one(seed, frames, rox, roz, timeout=timeout)
    if out is None:
        return None
    out["ep"] = (epx, epz)
    out["ruined_chunk"] = (rox, roz)
    return out


def verify_corruptible_strongholds(seed, mc=20, timeout=150.0):
    """Single-seed AUTO-VERIFY: run the search method on the 128 strongholds, then
    send the survivors (corruptible) to the EXACT checker. Returns a dict
    {stronghold_index: {"natural","forced","broken","ep","ruined_chunk","sx","sz"}}
    for the corruptible strongholds that the checker confirms (forced > 0).
    Needs the checker (1.16.1). Heavy (~3s/survivor) -> run in a background thread."""
    import checker_client
    _ensure_local_ctx(mc)
    lib, ctx = _W["lib"], _W["ctx"]
    cubiomes_wrap.apply_large_ctx(_W["lib"], ctx)   # large biomes world
    # 1) all 128 stronghold positions (native if available, else the 1st only)
    if hasattr(lib, "cb_strongholds128"):
        buf = (ctypes.c_int * 256)()
        k = lib.cb_strongholds128(ctx, mc, seed & _U64, buf, 128)
        shs = [(buf[2 * i], buf[2 * i + 1]) for i in range(k)]
    else:
        lib.cb_setup(ctx, mc, seed & _U64)
        rx, rz, sxc, szc = (ctypes.c_int(0) for _ in range(4))
        lib.cb_first_stronghold(ctx, mc, seed & _U64, ctypes.byref(rx),
                                ctypes.byref(rz), ctypes.byref(sxc), ctypes.byref(szc))
        shs = [(sxc.value, szc.value)]
    # 2) search-method survivors + build their exact checker requests
    reqs, meta = [], {}
    for i, (sx, sz) in enumerate(shs):
        if corrupt_check_at(seed, sx, sz, mc) is None:
            continue
        rq = corrupt_request_for(seed, mc, sx=sx, sz=sz)
        if rq is None:
            continue
        frames, epx, epz, rox, roz = rq
        # tag the seed per index so the checker results map back (offset trick)
        meta[i] = (sx, sz, epx, epz, rox, roz)
        reqs.append((i, frames, rox, roz))
    if not reqs:
        return {}
    # 3) verify each survivor (the checker keys by seed, so call one at a time)
    res = {}
    for (i, frames, rox, roz) in reqs:
        out = checker_client.verify_one(seed, frames, rox, roz, timeout=timeout)
        if out is None:
            continue
        sx, sz, epx, epz, _rox, _roz = meta[i]
        out.update({"ep": (epx, epz), "ruined_chunk": (rox, roz),
                    "sx": sx, "sz": sz})
        res[i] = out
    return res


def _ensure_local_ctx(mc):
    """Populate the module worker-state (_W) for DIRECT calls outside the search
    pool (e.g. the GUI single-seed analysis). Idempotent per mc."""
    if _W.get("lib") is not None and _W.get("mc") == mc:
        return
    path = cubiomes_wrap._find_lib() or cubiomes_wrap._compile_cubiomes()
    lib = ctypes.CDLL(path)
    _bind(lib)
    _W["lib"] = lib
    _W["ctx"] = lib.cb_create()
    cubiomes_wrap.apply_large_ctx(lib, _W["ctx"])
    _W["mc"] = mc
    if hasattr(lib, "cb_corrupt_scan"):
        _W["scan_buf"] = (ctypes.c_uint64 * 200000)()


def corrupt_check_at(seed, sx, sz, mc=20):
    """Single-seed per-stronghold CORRUPTED check using the SEARCH method (the
    fast geometric filter, NOT the slow Java checker). (sx, sz) = the stronghold
    start block coords (as returned by find_strongholds / cb_first_stronghold).
    Returns a dict {corruptible, ep:(x,y,z), ruined:(x,y,z), templ, mirFB} when
    this stronghold's end portal CAN be corrupted (necessary condition met), else
    None. For an EXACT yes/no, confirm with the checker (verify_seed).
    1.13-1.17 only (mc 20/21); returns None for newer versions."""
    if mc > 21:
        return None
    _ensure_local_ctx(mc)
    r = _validate_at(seed, sx, sz)
    if r is None:
        return None
    (ex, ey, ez), (corruptible, (rcx, rcy, rcz), mFB, ti) = r
    return {"corruptible": corruptible, "ep": (ex, ey, ez),
            "ruined": (rcx, rcy, rcz), "templ": ti, "mirFB": mFB}


def corrupt_check_all_strongholds(seed, strongholds, mc=20):
    """Run corrupt_check_at on a list of strongholds [(sx, sz), ...] for ONE seed
    (the single-seed 128-strongholds analysis). Returns a dict
    {index: result_dict} only for the strongholds that CAN be corrupted."""
    _ensure_local_ctx(mc)
    res = {}
    for i, (sx, sz) in enumerate(strongholds):
        r = corrupt_check_at(seed, sx, sz, mc)
        if r is not None:
            res[i] = r
    return res


def _chunk(args):
    """Traite [seed0, seed0+n) : pré-filtre puis validation corruption. Retourne
    la liste des hits (seed, ex,ey,ez, rx,ry,rz, under, template). `under` = nb de
    frames de l'end portal couvertes par l'empreinte X,Z du portail en ruine."""
    seed0, n, max_dist, thr, scan_128, sublist = args
    from stronghold_eyes import sh_count
    nsh = sh_count(scan_128)               # 1, 3 (first ring) or 128 (03/10)
    lib = _W["lib"]
    mc = _W["mc"]
    ctx = _W["ctx"]
    cubiomes_wrap.apply_large_ctx(_W["lib"], ctx)   # large biomes world
    hits = []
    is_118 = mc >= 22   # 1.18+ : different ruined mechanism (native cb_ruined_118)

    def _val(seed):
        return _validate_118(seed) if is_118 else _validate(seed, max_dist)

    if sublist is not None:
        # SEED-LIST mode: validate exactly the given (arbitrary, non-contiguous)
        # seeds. No native contiguous batch here (cb_corrupt_scan needs a range);
        # each seed is validated directly (1st stronghold, or all 128 if scan_128).
        use_128 = (scan_128 and not is_118
                   and hasattr(lib, "cb_strongholds128") and "s128_pos" in _W)
        pos = _W.get("s128_pos")
        for seed in sublist:
            if use_128:
                k = lib.cb_strongholds128(ctx, mc, seed & _U64, pos, nsh)
                for j in range(k):
                    r = _validate_at(seed, pos[2 * j], pos[2 * j + 1])
                    if r is None:
                        continue
                    (ex, ey, ez), (under, rc, mfb, ti) = r
                    hits.append((seed, ex, ey, ez, rc[0], rc[1], rc[2], under, ti,
                                 int(pos[2 * j]), int(pos[2 * j + 1])))
            else:
                r = _val(seed)
                if r is None:
                    continue
                (ex, ey, ez), (under, rc, mfb, ti) = r
                hits.append((seed, ex, ey, ez, rc[0], rc[1], rc[2], under, ti))
        return (seed0, len(sublist), hits)
    if (scan_128 and nsh < 128 and not is_118 and "s128_seed" in _W
            and not hasattr(lib, "cb_corrupt_scan_n") and "s128_pos" in _W):
        # first ring with an older library: the per-seed path (slower, exact)
        sub = [((x + (1 << 63)) % (1 << 64)) - (1 << 63) for x in range(seed0, seed0 + n)]
        return (seed0, n, _chunk((seed0, n, max_dist, thr, scan_128, sub))[2])
    if (scan_128 and not is_118 and "s128_seed" in _W
            and hasattr(lib, "cb_corrupt_scan128")):
        # 128-STRONGHOLDS mode: the native gate screens EVERY stronghold of each
        # seed (cheap, position-only); Python validates only the survivors. A seed
        # can produce several hits (one per corruptible stronghold).
        sb, xb, zb = _W["s128_seed"], _W["s128_x"], _W["s128_z"]
        cap = _S128_CAP
        off = 0
        while off < n:
            m = min(_S128_CHUNK, n - off)
            if nsh < 128:
                k = lib.cb_corrupt_scan_n(ctx, mc, (seed0 + off) & _U64, m,
                                          _SCAN_GATE, nsh, sb, xb, zb, cap)
            else:
                k = lib.cb_corrupt_scan128(ctx, mc, (seed0 + off) & _U64, m,
                                           _SCAN_GATE, sb, xb, zb, cap)
            for j in range(min(k, cap)):
                su = int(sb[j])
                seed = su if su < (1 << 63) else su - (1 << 64)
                r = _validate_at(seed, xb[j], zb[j])
                if r is None:
                    continue
                (ex, ey, ez), (under, rc, mfb, ti) = r
                hits.append((seed, ex, ey, ez, rc[0], rc[1], rc[2], under, ti,
                             int(xb[j]), int(zb[j])))
            off += m
        return (seed0, n, hits)
    if "scan_buf" in _W and hasattr(lib, "cb_corrupt_scan"):
        # FAST path: native batch does prefilter + origin gate in C (no biome on
        # the ruined, no surface noise); Python only validates the survivors.
        buf = _W["scan_buf"]
        cap = len(buf)
        k = lib.cb_corrupt_scan(ctx, mc, seed0 & _U64, n, thr, _SCAN_GATE, buf, cap)
        for j in range(min(k, cap)):
            su = int(buf[j])
            seed = su if su < (1 << 63) else su - (1 << 64)
            r = _val(seed)
            if r is None:
                continue
            (ex, ey, ez), (under, rc, mfb, ti) = r
            hits.append((seed, ex, ey, ez, rc[0], rc[1], rc[2], under, ti))
    else:
        # Fallback (old DLL without cb_corrupt_scan): per-seed Python prefilter.
        for i in range(n):
            seed = seed0 + i
            if _prefilter(seed) >= thr:
                continue
            r = _val(seed)
            if r is None:
                continue
            (ex, ey, ez), (under, rc, mfb, ti) = r
            hits.append((seed, ex, ey, ez, rc[0], rc[1], rc[2], under, ti))
    return (seed0, n, hits)


def search_corrupted_portals(seed_start, seed_count, callback,
                             stop_event=None, progress_callback=None,
                             num_workers=1, mc_version=21,
                             max_dist=25, prefilter_thr=180,
                             chunk_size=20000, scan_128=True, seed_list=None,
                             chunk_callback=None):
    """Cherche les seeds ou un portail en ruine corrompt le end portal d'un
    stronghold. callback(seed, ex, ey, ez, rx, ry, rz, under, template[, sx, sz]) par hit
    (sx, sz = début du stronghold pour les hits « 128 strongholds » ; absent = le 1er).
    seed_count <= 0 => illimite (jusqu'a stop_event).
    scan_128=True (default) : check ALL 128 strongholds of each seed (more hits
    per seed); False : only the 1st stronghold (the original fast mode).
    seed_list : if given, test EXACTLY those seeds (any order) and stop when all
    are done (ignores seed_start/seed_count).
    """
    if not has_corrupted():
        raise RuntimeError("libcubiomes ne contient pas le pont corrupted — "
                           "supprime libcubiomes.dll pour recompiler.")
    if seed_list is not None:
        # Stream the list — do NOT materialise 43M ints. SeedListSource / list
        # expose len() for the progress denominator; a bare generator gives 0
        # (unknown total -> the UI shows a running count instead of a %).
        try:
            seed_count = len(seed_list)
        except TypeError:
            seed_count = 0
    # Pre-build / load the native frame lib ONCE here (main process) so the
    # spawned workers don't each race to compile it (slow startup warmup).
    try:
        import gaider_layouts as _g
        _g._load()
    except Exception:
        pass
    nwork = max(1, int(num_workers))
    # 128-strongholds mode is much heavier per seed -> smaller chunks for a
    # responsive progress bar (the gate is batched in C inside cb_corrupt_scan128).
    from stronghold_eyes import sh_count
    use_128 = sh_count(scan_128) if scan_128 else False     # the count goes to the workers
    if use_128:
        chunk_size = min(chunk_size, _S128_CHUNK if use_128 >= 128 else 4 * _S128_CHUNK)
    # 1.18+ : the biome-accurate stronghold snap (locateBiome on the noise biome
    # system) is ~8 ms/seed (vs µs in 1.16), so a 20k chunk takes ~50 s. Small
    # chunks -> live SPS + responsive Stop.
    if mc_version >= 22:
        chunk_size = min(chunk_size, 400)
    done = [0]

    def _emit(res):
        seed0, n, hits = res
        for h in hits:
            callback(*h)
        if chunk_callback is not None:     # this block is fully checked (resume, 03/10)
            chunk_callback(seed0, n)
        done[0] += n
        if progress_callback:
            progress_callback(done[0], seed_count)

    # generateur de chunks
    def _chunks():
        if seed_list is not None:
            from itertools import islice
            cs = min(chunk_size, _S128_CHUNK) if use_128 else chunk_size
            src = iter(seed_list)        # SeedListSource/list/generator -> stream
            idx = 0
            while True:
                if stop_event is not None and stop_event.is_set():
                    return
                sub = list(islice(src, cs))
                if not sub:
                    return
                yield (idx, len(sub), max_dist, prefilter_thr, use_128, sub)
                idx += len(sub)
            return
        s = seed_start
        end = seed_start + seed_count if seed_count and seed_count > 0 else None
        while end is None or s < end:
            if stop_event is not None and stop_event.is_set():
                return
            n = chunk_size if end is None else min(chunk_size, end - s)
            yield (s, n, max_dist, prefilter_thr, use_128, None)
            s += n

    if nwork == 1:
        _winit(mc_version)
        for ch in _chunks():
            if stop_event is not None and stop_event.is_set():
                break
            _emit(_chunk(ch))
        return

    ctx = mp.get_context("spawn")
    with ctx.Pool(nwork, initializer=_winit, initargs=(mc_version,)) as pool:
        try:
            for res in pool.imap_unordered(_chunk, _chunks()):
                _emit(res)
                if stop_event is not None and stop_event.is_set():
                    break
        finally:
            pool.terminate()
            pool.join()
# corruption-filter v2 (footprint overlap)
