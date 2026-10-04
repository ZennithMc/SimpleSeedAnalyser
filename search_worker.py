"""
search_worker.py — Module ULTRA-LÉGER dédié aux workers `multiprocessing`
pour la recherche de seeds.

Sur Windows, `multiprocessing` utilise `spawn` qui re-importe le module
contenant le worker dans chaque sous-processus. En isolant le worker dans
un fichier qui n'importe **que** stronghold_eyes + cubiomes (et rien de
la GUI), on évite de relancer Tkinter et tout l'arbre d'imports lourds
dans chaque sous-processus → démarrage instantané et pas de hang Windows.

NE PAS importer ce module depuis gui.py (sauf via stronghold_eyes).
"""
from __future__ import annotations

import ctypes as _ctypes

# Cache du CubiomesLib par worker (un par sous-processus). Évite de
# recharger la lib à chaque seed.
_CUBIOMES_LIB = None
# MC version par défaut = 1.21.4 (matche MC_VERSIONS["Java 1.21.4 +"] = 28).
_DEFAULT_MC = 28   # MC_1_21_WD (1.21.4+)

# Native C functions for the Gaider fast path. Bound lazily on first use.
_GAIDER_C = None


_WARNED = set()


def _warn_once(e):
    """A failed check of one seed is reported once per error type (it used to
    be swallowed and counted as "no match"), without stopping the search."""
    k = (type(e).__name__, str(e)[:120])
    if k in _WARNED:
        return
    _WARNED.add(k)
    import sys, traceback
    try:
        print(f"[search_worker] seed check failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        traceback.print_exc()
    except Exception:
        pass

def _get_cubiomes():
    global _CUBIOMES_LIB
    if _CUBIOMES_LIB is None:
        from cubiomes_wrap import CubiomesLib
        _CUBIOMES_LIB = CubiomesLib()
    return _CUBIOMES_LIB


def _get_gaider_c():
    """Bind native C gaider functions on first call. Returns the lib or
    None if symbols not available (older lib build)."""
    global _GAIDER_C
    if _GAIDER_C is not None:
        return _GAIDER_C if _GAIDER_C is not False else None

    lib = _get_cubiomes()._lib
    try:
        lib.gaider_count_eyes_at_chunk.argtypes = [
            _ctypes.c_uint64, _ctypes.c_int32, _ctypes.c_int32]
        lib.gaider_count_eyes_at_chunk.restype = _ctypes.c_int

        lib.gaider_check_12eye_fast.argtypes = [
            _ctypes.c_uint64, _ctypes.c_int32, _ctypes.c_int32]
        lib.gaider_check_12eye_fast.restype = _ctypes.c_int

        lib.gaider_batch_scan.argtypes = [
            _ctypes.POINTER(_ctypes.c_uint64), _ctypes.c_int32,
            _ctypes.POINTER(_ctypes.c_int32), _ctypes.POINTER(_ctypes.c_int32),
            _ctypes.c_int,
            _ctypes.POINTER(_ctypes.c_int32),
            _ctypes.POINTER(_ctypes.c_int32),
        ]
        lib.gaider_batch_scan.restype = _ctypes.c_int

        # Batch with internal initFirstStronghold call (fastest path)
        lib.gaider_batch_12eye_with_init.argtypes = [
            _ctypes.c_int64, _ctypes.c_int32, _ctypes.c_int,
            _ctypes.POINTER(_ctypes.c_int64),
            _ctypes.POINTER(_ctypes.c_int32),
        ]
        lib.gaider_batch_12eye_with_init.restype = _ctypes.c_int

        lib.gaider_batch_prefilter_with_init.argtypes = [
            _ctypes.c_int64, _ctypes.c_int32, _ctypes.c_int, _ctypes.c_int,
            _ctypes.POINTER(_ctypes.c_int64),
            _ctypes.POINTER(_ctypes.c_int32),
        ]
        lib.gaider_batch_prefilter_with_init.restype = _ctypes.c_int

        lib.gaider_batch_strict_with_init.argtypes = [
            _ctypes.c_int64, _ctypes.c_int32, _ctypes.c_int, _ctypes.c_int,
            _ctypes.POINTER(_ctypes.c_int64),
            _ctypes.POINTER(_ctypes.c_int32),
        ]
        lib.gaider_batch_strict_with_init.restype = _ctypes.c_int
        _GAIDER_C = lib
    except (AttributeError, OSError):
        # Lib was compiled without gaider_fast.c (old version)
        _GAIDER_C = False
        return None
    return _GAIDER_C


_GAIDER_LCG = None

def _get_gaider_lcg():
    """Bind les fonctions LCG (MC 1.13-1.17) de gaider_fast.c, SEPAREMENT du
    binding xoroshiro : une DLL ancienne (sans LCG) ne doit pas desactiver le
    chemin natif 1.18+. Retourne la lib si dispo, sinon None (fallback Python)."""
    global _GAIDER_LCG
    if _GAIDER_LCG is not None:
        return _GAIDER_LCG if _GAIDER_LCG is not False else None
    g = _get_gaider_c()
    if g is None:
        _GAIDER_LCG = False
        return None
    try:
        g.gaider_count_eyes_at_chunk_lcg.argtypes = [
            _ctypes.c_uint64, _ctypes.c_int32, _ctypes.c_int32]
        g.gaider_count_eyes_at_chunk_lcg.restype = _ctypes.c_int
        g.gaider_batch_12eye_with_init_lcg.argtypes = [
            _ctypes.c_int64, _ctypes.c_int32, _ctypes.c_int,
            _ctypes.POINTER(_ctypes.c_int64), _ctypes.POINTER(_ctypes.c_int32)]
        g.gaider_batch_12eye_with_init_lcg.restype = _ctypes.c_int
        g.gaider_batch_strict_with_init_lcg.argtypes = [
            _ctypes.c_int64, _ctypes.c_int32, _ctypes.c_int, _ctypes.c_int,
            _ctypes.POINTER(_ctypes.c_int64), _ctypes.POINTER(_ctypes.c_int32)]
        g.gaider_batch_strict_with_init_lcg.restype = _ctypes.c_int
        g.gaider_batch_grid_with_init_lcg.argtypes = [
            _ctypes.c_int64, _ctypes.c_int32, _ctypes.c_int, _ctypes.c_int,
            _ctypes.c_int,
            _ctypes.POINTER(_ctypes.c_int64), _ctypes.POINTER(_ctypes.c_int32)]
        g.gaider_batch_grid_with_init_lcg.restype = _ctypes.c_int
        g.gaider_batch_compute_chunks.argtypes = [
            _ctypes.c_int64, _ctypes.c_int32, _ctypes.c_int,
            _ctypes.POINTER(_ctypes.c_int32), _ctypes.POINTER(_ctypes.c_int32)]
        g.gaider_batch_compute_chunks.restype = None
        _GAIDER_LCG = g
    except (AttributeError, OSError):
        _GAIDER_LCG = False
        return None
    return _GAIDER_LCG


def _gaider_chunk_eyes(seed: int, cx: int, cz: int) -> int:
    """Calcule combien des 12 nextFloat seraient > 0.9 si on plaçait un
    stronghold à ce chunk précis. Utilise le C natif si dispo (~10× plus
    rapide que le Python pur), fallback Python sinon."""
    g = _get_gaider_c()
    if g is not None:
        return g.gaider_count_eyes_at_chunk(
            _ctypes.c_uint64(seed & ((1 << 64) - 1)), cx, cz)
    # Fallback pur Python
    from stronghold_eyes import (Xrsr128, _xrsr_get_decoration_seed, _U64,
                                  _skip_portal_room)
    deco = _xrsr_get_decoration_seed(seed & _U64, cx, cz)
    rng = Xrsr128((deco + 40019) & _U64)
    _skip_portal_room(rng)
    eyes = 0
    for _ in range(12):
        if rng.next_float() > 0.9:
            eyes += 1
    return eyes


def _count_eyes_gaider_only(seed: int, bx: int, bz: int,
                            mc_version: int = 22) -> int:
    """
    Calcule le nombre d'yeux avec **uniquement** la matrice Gaider
    `_skip_portal_room`, SANS simuler les pre-pieces.

    Cette méthode est connue pour être correcte quand la PortalRoom est
    seule dans son chunk physique (cas mono-chunk validés) ; elle peut
    être imprécise pour les cas multi-chunks complexes.

    Xoroshiro (1.18+) only. The decoration-seed offset is version-aware
    (1.18.1 = +50000, 1.19+ = +40000+index) via _eye_feature_offset.
    """
    from stronghold_eyes import (Xrsr128, _xrsr_get_decoration_seed, _U64,
                                  _skip_portal_room, StrongholdGenerator,
                                  _frame_world_pos, _eye_feature_offset)
    off = _eye_feature_offset(mc_version)
    cx, cz = bx >> 4, bz >> 4
    gen = StrongholdGenerator()
    gen.generate(seed, cx, cz)
    p = gen.portal_room_piece
    if p is None:
        return 0

    cache = {}
    def get_chunk_frames(ccx, ccz):
        if (ccx, ccz) in cache:
            return cache[(ccx, ccz)]
        deco = _xrsr_get_decoration_seed(seed & _U64, ccx, ccz)
        rng = Xrsr128((deco + off) & _U64)
        _skip_portal_room(rng)
        f = [rng.next_float() > 0.9 for _ in range(12)]
        cache[(ccx, ccz)] = f
        return f

    eyes = 0
    for i in range(12):
        wx, wz = _frame_world_pos(p, i)
        if get_chunk_frames(wx >> 4, wz >> 4)[i]:
            eyes += 1
    return eyes


def _gaider_12eye_check(seed: int, lib, mc_version: int,
                          _it_buffer, _ctypes_local) -> bool:
    """Retourne True si la seed donne 12/12 selon Gaider matrix au chunk
    approximatif fourni par initFirstStronghold."""
    pos = lib._lib.initFirstStronghold(
        _ctypes_local.byref(_it_buffer), mc_version, _ctypes_local.c_uint64(seed & ((1<<64)-1)))
    cx = pos.x >> 4
    cz = pos.z >> 4

    from stronghold_eyes import (Xrsr128, _xrsr_get_decoration_seed, _U64,
                                  _skip_portal_room, _eye_feature_offset,
                                  EYE_OFFSET_LEGACY)
    off = _eye_feature_offset(mc_version)
    g = _get_gaider_c()
    if g is not None and off == EYE_OFFSET_LEGACY:   # native quick check = +40019 only
        return bool(g.gaider_check_12eye_fast(
            _ctypes_local.c_uint64(seed & ((1 << 64) - 1)), cx, cz))

    # Python (any version's offset)
    deco = _xrsr_get_decoration_seed(seed & _U64, cx, cz)
    rng = Xrsr128((deco + off) & _U64)
    _skip_portal_room(rng)
    for _ in range(12):
        if rng.next_float() <= 0.9:
            return False
    return True


_LCG_GRID = 7      # 1.13-1.17 fast prefilter window (chunks) — lossy, see Stronghold page
_XRSR_GRID = 15    # 1.18+ prefilter window: snap (≤7 chunks) + portal-room offset (≤12 seen)


def search_chunk(args):
    """
    Worker exécuté dans un sous-processus séparé.

    args = (seed_start, i_start, i_end, min_eyes)
       OR (seed_start, i_start, i_end, min_eyes, mc_version)
       OR (seed_start, i_start, i_end, min_eyes, mc_version, fast_mode)

    Modes :
      - min_eyes == 12 : batch C natif (Gaider 12-eye check), ~1.6M seeds/s
                         single thread. Précis pour 12/12.
      - fast_mode (et min_eyes < 12) : batch C natif (Gaider prefilter
                         9 chunks), candidats validés par pipeline complet.
                         Ratio ~50% pour 6-11 mais on scanne 25 000× plus.
      - sinon : pipeline lent (cubiomes + simulation full Python).

    Renvoie (i_start, i_end, list[(seed, eyes, bx, bz)])
    """
    fast_mode = False
    min_max_y = None
    if len(args) == 7:
        seed_start, i_start, i_end, min_eyes, mc_version, fast_mode, min_max_y = args
    elif len(args) == 6:
        seed_start, i_start, i_end, min_eyes, mc_version, fast_mode = args
    elif len(args) == 5:
        seed_start, i_start, i_end, min_eyes, mc_version = args
    else:
        seed_start, i_start, i_end, min_eyes = args
        mc_version = _DEFAULT_MC
    _U64 = (1 << 64) - 1
    _S64 = (1 << 63)

    from stronghold_eyes import count_stronghold_eyes, get_stronghold_portal_full
    from stronghold_eyes import _inflate_arg, topy_of
    from cubiomes_wrap import StrongholdIter as _SHIter

    lib = _get_cubiomes()
    g = _get_gaider_c()
    g_lcg = _get_gaider_lcg() if (mc_version < 22) else None

    # 1.13-1.17 (mc<22) : prefiltres natifs Gaider ET double-check
    # _count_eyes_gaider_only sont xoroshiro (1.18+). Pour 1.16 il faut rester
    # sur le chemin version-correct (count_stronghold_eyes(mc)).
    is_pre118 = (mc_version < 22)
    twelve_mode = (min_eyes >= 12)
    use_doublecheck = False   # gaider-only under-counts (see path B) — disabled 26/09

    n_total = i_end - i_start
    if n_total <= 0:
        return (i_start, i_end, [])
    chunk_seed_start = seed_start + i_start

    results = []

    def _emit(seed, eyes, bx, bz):
        # Max Y : on ne fait la generation complete (couteuse) QUE si on filtre
        # par Max Y. En eye-finder pur (min_max_y=None) -> zero surcout.
        _maxy = None
        if min_max_y is not None:
            _info = get_stronghold_portal_full(seed, bx, bz, mc_version)
            _maxy = topy_of(_info, min_max_y)
            if _maxy is None or _maxy < min_max_y:
                return
        results.append((seed, eyes, bx, bz, _maxy))

    # ── MAX Y search (1.13-1.17) : top Y EXACT en natif (gaider10_lib) ──
    # compute_chunks (cubiomes, natif) -> max_y_batch (BFS natif) -> candidats,
    # puis coords + yeux exacts sur le petit nombre retenu. ~10-50x le Python.
    if min_max_y is not None and is_pre118 and g_lcg is not None:
        try:
            import gaider_layouts as _gl
            if _gl.has_max_y():
                import numpy as np
                cxs = np.zeros(n_total, dtype=np.int32)
                czs = np.zeros(n_total, dtype=np.int32)
                # VRAIES positions (biome-snappées) via cubiomes : indispensable,
                # initFirstStronghold (anneau) donne une structure décalée -> faux.
                valid = bytearray(n_total)
                for i in range(n_total):
                    seed = chunk_seed_start + i
                    try:
                        lib.setup_and_seed(mc=mc_version, seed=seed)
                        sh = lib.find_strongholds(max_count=1, radius_blocks=None)
                        if sh:
                            bx, bz = sh[0]
                            cxs[i] = bx >> 4
                            czs[i] = bz >> 4
                            valid[i] = 1
                    except Exception as _e:
                        _warn_once(_e)   # (was silently "no match")
                # BFS native au VRAI chunk -> top Y exact -> pré-filtre par seuil
                cseeds, _ctops = _gl.max_y_batch(
                    chunk_seed_start, n_total, cxs, czs,
                    pre118=(mc_version <= 20), sea=63, margin=10,
                    minY=(-64 if mc_version >= 22 else 0),  # 1.18+ = deepslate world
                    inflate=_inflate_arg(mc_version, min_max_y), threshold=int(min_max_y))
                for seed in cseeds:
                    seed = int(seed)
                    i = seed - chunk_seed_start
                    if i < 0 or i >= n_total or not valid[i]:
                        continue
                    bx = int(cxs[i]) << 4
                    bz = int(czs[i]) << 4
                    try:
                        info = get_stronghold_portal_full(seed, bx, bz, mc_version)
                        if info is None or topy_of(info, min_max_y) < min_max_y:
                            continue
                        eyes = count_stronghold_eyes(seed, bx, bz, mc_version)
                        # BUG FIX : ce chemin (Max Y natif pré-118) oubliait le
                        # filtre yeux → on combinait mal Max Y + eye count.
                        if eyes < min_eyes:
                            continue
                        results.append((seed, eyes, info["center_x"],
                                        info["center_z"], topy_of(info, min_max_y)))
                    except Exception as _e:
                        _warn_once(_e)   # (was silently "no match")
                return (i_start, i_end, results)
        except Exception as _e:
            _warn_once(_e)   # (was silently "no match")

    # ── PATH A-LCG / B-LCG : MC 1.13-1.17 natif (gaider_fast.c) ──────
    # Même structure que les chemins xoroshiro mais avec le compteur LCG.
    # Position via initFirstStronghold (dans le C), validation CPU exacte.
    if is_pre118 and g_lcg is not None and (twelve_mode or fast_mode):
        import numpy as np
        out_seeds = np.zeros(n_total, dtype=np.int64)
        out_eyes  = np.zeros(n_total, dtype=np.int32)
        # Grille ±7 autour de la vraie position : le portail est décalé par la
        # BFS, un single-chunk donne ~0% recall. ±7 le couvre (recall élevé).
        n_cand = g_lcg.gaider_batch_grid_with_init_lcg(
            _ctypes.c_int64(chunk_seed_start), n_total, mc_version,
            (12 if twelve_mode else min_eyes), _LCG_GRID,
            out_seeds.ctypes.data_as(_ctypes.POINTER(_ctypes.c_int64)),
            out_eyes.ctypes.data_as(_ctypes.POINTER(_ctypes.c_int32)))
        for k in range(n_cand):
            seed = int(out_seeds[k])
            try:
                lib.setup_and_seed(mc=mc_version, seed=seed)
                sh_list = lib.find_strongholds(max_count=1, radius_blocks=None)
                if not sh_list:
                    continue
                bx, bz = sh_list[0]
                eyes = count_stronghold_eyes(seed, bx, bz, mc_version)
                if eyes >= min_eyes:
                    _emit(seed, eyes, bx, bz)
            except Exception as _e:
                _warn_once(_e)   # (was silently "no match")
        return (i_start, i_end, results)

    # ── PATH A: 12-eye fast batch (C native) ─────────────────────────
    if twelve_mode and g is not None and not is_pre118:
        import numpy as np
        out_seeds = np.zeros(n_total, dtype=np.int64)
        out_eyes  = np.zeros(n_total, dtype=np.int32)
        n_match = g.gaider_batch_12eye_with_init(
            _ctypes.c_int64(chunk_seed_start), n_total, mc_version,
            out_seeds.ctypes.data_as(_ctypes.POINTER(_ctypes.c_int64)),
            out_eyes.ctypes.data_as(_ctypes.POINTER(_ctypes.c_int32)),
        )
        # Pour chaque candidat 12/12 selon Gaider, on valide via pipeline
        # complet pour avoir la VRAIE position cubiomes et le compte exact.
        for k in range(n_match):
            seed = int(out_seeds[k])
            try:
                lib.setup_and_seed(mc=mc_version, seed=seed)
                sh_list = lib.find_strongholds(max_count=1, radius_blocks=None)
                if not sh_list:
                    continue
                bx, bz = sh_list[0]
                eyes = count_stronghold_eyes(seed, bx, bz, mc_version)
                if eyes >= min_eyes:
                    _emit(seed, eyes, bx, bz)
            except Exception as _e:
                _warn_once(_e)   # (was silently "no match")
        return (i_start, i_end, results)

    # ── PATH B: fast_mode pour 6-11/12 (C native batch prefilter) ───
    # (below 6 eyes nearly every seed passes the grid: plain exact path C instead)
    if (fast_mode and g is not None and not twelve_mode and not is_pre118 and min_eyes >= 6
            and hasattr(g, "seedlist_eyes_prefilter_arr")
            and hasattr(g, "gaider_batch_compute_chunks")):
        import numpy as np
        # BUG fixed 26/09: the old STRICT prefilter looked at ONE chunk (the
        # un-snapped ring position) while the portal room is usually 1-12
        # chunks away (biome snap + stronghold layout) -> it found 0 of 20
        # six-eye seeds in 60 000 (exhaustive search: 20). Now: grid max
        # single-chunk count over ±_XRSR_GRID chunks around the un-snapped
        # position (covers snap ≤7 + portal offset), the same prefilter as the
        # seed-list path; survivors are validated exactly below.
        seeds_arr = (np.arange(n_total, dtype=np.uint64)          # wraps at 2^63 (np.arange
                     + np.uint64(int(chunk_seed_start) & 0xFFFFFFFFFFFFFFFF)).view(np.int64)  # overflowed)
        cxs = np.zeros(n_total, dtype=np.int32)
        czs = np.zeros(n_total, dtype=np.int32)
        P32 = _ctypes.POINTER(_ctypes.c_int32)
        P64 = _ctypes.POINTER(_ctypes.c_int64)
        g.gaider_batch_compute_chunks.argtypes = [_ctypes.c_int64, _ctypes.c_int32, _ctypes.c_int,
                                                  P32, P32]
        g.gaider_batch_compute_chunks.restype = None
        g.seedlist_eyes_prefilter_arr.argtypes = [P64, P32, P32, _ctypes.c_int32, _ctypes.c_int,
                                                  _ctypes.c_int, _ctypes.c_int, P64, P32]
        g.seedlist_eyes_prefilter_arr.restype = _ctypes.c_int
        g.gaider_batch_compute_chunks(_ctypes.c_int64(chunk_seed_start), n_total, mc_version,
                                      cxs.ctypes.data_as(P32), czs.ctypes.data_as(P32))
        out_seeds = np.zeros(n_total, dtype=np.int64)
        out_eyes  = np.zeros(n_total, dtype=np.int32)
        n_cand = g.seedlist_eyes_prefilter_arr(
            seeds_arr.ctypes.data_as(P64), cxs.ctypes.data_as(P32), czs.ctypes.data_as(P32),
            n_total, mc_version, min_eyes, _XRSR_GRID,
            out_seeds.ctypes.data_as(P64), out_eyes.ctypes.data_as(P32))
        # Validation CPU complète pour chaque candidat
        for k in range(n_cand):
            seed = int(out_seeds[k])
            try:
                lib.setup_and_seed(mc=mc_version, seed=seed)
                sh_list = lib.find_strongholds(max_count=1, radius_blocks=None)
                if not sh_list:
                    continue
                bx, bz = sh_list[0]
                eyes = count_stronghold_eyes(seed, bx, bz, mc_version)
                if eyes < min_eyes:
                    continue
                # 26/09: no more "Gaider double-check" min(exact, gaider-only):
                # gaider-only ignores the pieces sharing the portal chunk and
                # UNDER-counted 17 of 32 seeds with >= 5 eyes (exact count =
                # the in-game-verified simulation) -> real hits were dropped.
                _emit(seed, eyes, bx, bz)
            except Exception as _e:
                _warn_once(_e)   # (was silently "no match")
        return (i_start, i_end, results)

    # ── PATH C: SLOW (mode normal sans optimisation) ────────────────
    for j in range(i_start, i_end):
        raw  = (seed_start + j) & _U64
        seed = raw - (1 << 64) if raw >= _S64 else raw
        try:
            lib.setup_and_seed(mc=mc_version, seed=seed)
            sh_list = lib.find_strongholds(max_count=1, radius_blocks=None)
            if not sh_list:
                continue
            bx, bz = sh_list[0]
            eyes = count_stronghold_eyes(seed, bx, bz, mc_version)
            if eyes < min_eyes:
                continue
            if use_doublecheck:
                eyes_gaider = _count_eyes_gaider_only(seed, bx, bz, mc_version)
                if eyes_gaider < min_eyes:
                    continue
                eyes = min(eyes, eyes_gaider)
            _emit(seed, eyes, bx, bz)
        except Exception as _e:
            _warn_once(_e)   # (was silently "no match")
    return (i_start, i_end, results)
