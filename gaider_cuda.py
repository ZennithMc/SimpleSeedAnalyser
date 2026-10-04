"""
gaider_cuda.py — Optional CUDA acceleration for Gaider seed scanning.

Loads the prebuilt `gaider_cuda.dll` (tools/build_native.py, all RTX 20-50) and exposes the scan kernels via ctypes. Falls back gracefully if CUDA isn't
available — callers should always check `is_available()` before use.

The kernel is bit-perfect identical to the CPU `gaider_fast.c` — the same
seeds will give the same results, just much faster on GPU.
"""
from __future__ import annotations
import ctypes
import os
import platform
import subprocess
import shutil
import time

_CUDA_LIB = None  # ctypes.CDLL or None
_CUDA_AVAILABLE = None  # tri-state: None=unchecked, True/False=result
_CUDA_LOAD_ERROR = None  # human-readable error if load failed
_HAS_LCG = None  # True si la DLL chargée a le kernel LCG (MC 1.13-1.17)


def _here() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _cuda_lib_name() -> str:
    return "gaider_cuda.dll" if platform.system() == "Windows" else "libgaider_cuda.so"


# La compilation « au premier lancement » a été retirée : les DLL viennent de
# tools/build_native.py (fatbin RTX 20 → 50) et sont chargées/auto-testées par
# native_libs.py, qui ne supprime jamais une DLL.


def _load_cuda() -> None:
    """Lazy load + bind. Idempotent."""
    global _CUDA_LIB, _CUDA_AVAILABLE, _CUDA_LOAD_ERROR, _HAS_LCG
    if _CUDA_AVAILABLE is not None:
        return

    import native_libs as _nl
    lib, _msg = _nl.load_cuda("gaider_cuda")
    if lib is None:
        _CUDA_LOAD_ERROR = _msg
        _CUDA_AVAILABLE = False
        return

    try:
        lib.gaider_cuda_available.argtypes = []
        lib.gaider_cuda_available.restype = ctypes.c_int

        lib.gaider_cuda_scan_12eye.argtypes = [
            ctypes.c_int64, ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
            ctypes.POINTER(ctypes.c_int64), ctypes.c_int32,
        ]
        lib.gaider_cuda_scan_12eye.restype = ctypes.c_int

        lib.gaider_cuda_scan_min_eyes.argtypes = [
            ctypes.c_int64, ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int32),
            ctypes.c_int32,
        ]
        lib.gaider_cuda_scan_min_eyes.restype = ctypes.c_int

        # Full GPU scans (compute chunk + scan in one kernel)
        lib.gaider_cuda_full_scan_12eye.argtypes = [
            ctypes.c_int64, ctypes.c_int32, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int64), ctypes.c_int32,
        ]
        lib.gaider_cuda_full_scan_12eye.restype = ctypes.c_int

        lib.gaider_cuda_full_scan_min_eyes.argtypes = [
            ctypes.c_int64, ctypes.c_int32, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int32),
            ctypes.c_int32,
        ]
        lib.gaider_cuda_full_scan_min_eyes.restype = ctypes.c_int

        # Layout-based scans (Gaider10 architecture)
        # We don't bind LayoutInput as a Structure here — we use the same
        # one defined in gaider_layouts.py and pass the raw pointer.
        lib.gaider_cuda_layout_scan_12eye.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int64), ctypes.c_int32,
        ]
        lib.gaider_cuda_layout_scan_12eye.restype = ctypes.c_int

        lib.gaider_cuda_layout_scan_min_eyes.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int32),
            ctypes.c_int32,
        ]
        lib.gaider_cuda_layout_scan_min_eyes.restype = ctypes.c_int

        # GPU eye-count over CPU-snapped positions (seed list / all-128 finders).
        try:
            lib.gaider_cuda_eyes_arr.argtypes = [
                ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int32),
                ctypes.POINTER(ctypes.c_int32), ctypes.c_int32, ctypes.c_int,
                ctypes.c_int, ctypes.POINTER(ctypes.c_int32),
            ]
            lib.gaider_cuda_eyes_arr.restype = ctypes.c_int
        except AttributeError:
            pass

        if lib.gaider_cuda_available() == 0:
            _CUDA_LOAD_ERROR = "No CUDA-capable GPU detected"
            _CUDA_AVAILABLE = False
            return

        _CUDA_LIB = lib
        _CUDA_AVAILABLE = True
        # Bind crunchiness symbols immediately (safe: lib is loaded and available)
        _bind_crunchiness(lib)
        # Détecte le kernel LCG (MC 1.13-1.17). Absent = ancienne DLL (1.18+ only).
        try:
            lib.gaider_cuda_lcg_marker.restype = ctypes.c_int
            _HAS_LCG = (lib.gaider_cuda_lcg_marker() == 1)
        except AttributeError:
            _HAS_LCG = False
    except Exception as e:
        _CUDA_LOAD_ERROR = f"gaider_cuda.dll : liaison des fonctions impossible ({e})"
        print(f"[CUDA] {_CUDA_LOAD_ERROR}")
        _CUDA_AVAILABLE = False


_GROW_LIMIT = 1 << 24   # au-delà : condition trop large pour un pré-filtre GPU


def _grow(total) -> int:
    """Taille de buffer suffisante pour TOUS les résultats (plus de troncature
    silencieuse). Au-delà de _GROW_LIMIT, erreur explicite plutôt qu'une
    allocation démesurée."""
    if total > _GROW_LIMIT:
        import native_libs as _nl
        raise _nl.GpuError(f"{total:,} candidats dans un seul lot GPU : condition trop "
                           f"large (augmente le seuil / le nombre d'yeux minimum)")
    return int(total)


def _unavailable():
    """Appel GPU sans GPU utilisable : erreur explicite (avant : « 0 résultat »)."""
    import native_libs as _nl
    raise _nl.GpuError(f"GPU indisponible : {get_load_error()}")


def _check(rc) -> None:
    """Lève native_libs.GpuError si la DLL signale une erreur CUDA (rc < 0)."""
    if rc is None or rc < 0:
        import native_libs as _nl
        global _CUDA_AVAILABLE, _CUDA_LOAD_ERROR
        err = _nl.last_error(_CUDA_LIB) or "erreur CUDA"
        if _nl.is_sticky(err):             # dead CUDA context: CPU from now on
            _CUDA_AVAILABLE = False
            _CUDA_LOAD_ERROR = "GPU switched off until SSA restarts (the CUDA context is lost): " + err
            print(f"[gaider_cuda] {_CUDA_LOAD_ERROR}", flush=True)
        raise _nl.GpuError(f"gaider_cuda : {err}")


def is_available() -> bool:
    """Returns True if CUDA scanning is ready to use. Triggers lazy load."""
    _load_cuda()
    return _CUDA_AVAILABLE is True


def get_load_error() -> str | None:
    """Returns the reason CUDA isn't available, if not. None if it is."""
    _load_cuda()
    return _CUDA_LOAD_ERROR


def has_lcg() -> bool:
    """True si la DLL CUDA chargée contient le kernel LCG (MC 1.13-1.17)."""
    _load_cuda()
    return bool(_HAS_LCG)


def scan_12eye(seed_start: int, n: int,
                cx_arr, cz_arr,
                max_results: int = 1024) -> tuple[int, list[int]]:
    """Scan [seed_start, seed_start+n) for 12/12 candidates on GPU.
    Returns (total_match_count, list_of_seeds). The list is capped to
    max_results."""
    if not is_available():
        _unavailable()
    import numpy as np
    cx = np.asarray(cx_arr, dtype=np.int32)
    cz = np.asarray(cz_arr, dtype=np.int32)
    out = np.zeros(max_results, dtype=np.int64)
    total = _CUDA_LIB.gaider_cuda_scan_12eye(
        ctypes.c_int64(seed_start), ctypes.c_int32(n),
        cx.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        cz.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        ctypes.c_int32(max_results),
    )
    _check(total)
    if total > max_results:   # buffer trop petit : on relance, rien n'est perdu
        return scan_12eye(seed_start, n, cx_arr, cz_arr, max_results=_grow(total))
    actual = min(total, max_results)
    return (total, out[:actual].tolist())


def scan_min_eyes(seed_start: int, n: int,
                    cx_arr, cz_arr, min_eyes: int,
                    max_results: int = 4096) -> tuple[int, list[tuple[int, int]]]:
    """Scan for seeds where Gaider count >= min_eyes at given chunk.
    Returns (total_count, list of (seed, eyes))."""
    if not is_available():
        _unavailable()
    import numpy as np
    cx = np.asarray(cx_arr, dtype=np.int32)
    cz = np.asarray(cz_arr, dtype=np.int32)
    out_seeds = np.zeros(max_results, dtype=np.int64)
    out_eyes = np.zeros(max_results, dtype=np.int32)
    total = _CUDA_LIB.gaider_cuda_scan_min_eyes(
        ctypes.c_int64(seed_start), ctypes.c_int32(n),
        cx.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        cz.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        ctypes.c_int(min_eyes),
        out_seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        out_eyes.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        ctypes.c_int32(max_results),
    )
    _check(total)
    if total > max_results:   # buffer trop petit : on relance, rien n'est perdu
        return scan_min_eyes(seed_start, n, cx_arr, cz_arr, min_eyes, max_results=_grow(total))
    actual = min(total, max_results)
    return (total, list(zip(out_seeds[:actual].tolist(),
                              out_eyes[:actual].tolist())))


def full_scan_12eye(seed_start: int, n: int, mc_version: int = 28,
                     max_results: int = 1024) -> tuple[int, list[int]]:
    """ALL-GPU 12-eye scan: computes initFirstStronghold AND Gaider check
    on GPU in a single kernel. No host-side preprocessing.

    Returns (total_match_count, list_of_seeds capped to max_results)."""
    if not is_available():
        _unavailable()
    import numpy as np
    out = np.zeros(max_results, dtype=np.int64)
    total = _CUDA_LIB.gaider_cuda_full_scan_12eye(
        ctypes.c_int64(seed_start), ctypes.c_int32(n),
        ctypes.c_int(mc_version),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        ctypes.c_int32(max_results),
    )
    _check(total)
    if total > max_results:   # buffer trop petit : on relance, rien n'est perdu
        return full_scan_12eye(seed_start, n, mc_version, max_results=_grow(total))
    actual = min(total, max_results)
    return (total, out[:actual].tolist())


def full_scan_min_eyes(seed_start: int, n: int, min_eyes: int,
                         mc_version: int = 28,
                         max_results: int = 4096) -> tuple[int, list[tuple[int, int]]]:
    """ALL-GPU scan for arbitrary min_eyes."""
    if not is_available():
        _unavailable()
    import numpy as np
    out_seeds = np.zeros(max_results, dtype=np.int64)
    out_eyes = np.zeros(max_results, dtype=np.int32)
    total = _CUDA_LIB.gaider_cuda_full_scan_min_eyes(
        ctypes.c_int64(seed_start), ctypes.c_int32(n),
        ctypes.c_int(mc_version), ctypes.c_int(min_eyes),
        out_seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        out_eyes.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        ctypes.c_int32(max_results),
    )
    _check(total)
    if total > max_results:   # buffer trop petit : on relance, rien n'est perdu
        return full_scan_min_eyes(seed_start, n, min_eyes, mc_version, max_results=_grow(total))
    actual = min(total, max_results)
    return (total, list(zip(out_seeds[:actual].tolist(),
                              out_eyes[:actual].tolist())))


# ─────────────────────────────────────────────────────────────────────
# Layout-based scans (Gaider10 architecture)
# ─────────────────────────────────────────────────────────────────────

def layout_scan_12eye(layouts_buffer, n_layouts: int,
                        mc_version: int = 28,
                        max_results: int = 1024) -> tuple[int, list[int]]:
    """Scan layout buffer for 12/12 candidates.
    `layouts_buffer` is a ctypes Array of Layout from gaider_layouts.

    For each Layout, tests all 65536 high-16-bit variations of world_seed
    against the EXACT portal_chunk. ~225× more efficient than grid scan.
    """
    if not is_available():
        _unavailable()
    if n_layouts == 0:
        return (0, [])
    import numpy as np
    out = np.zeros(max_results, dtype=np.int64)
    total = _CUDA_LIB.gaider_cuda_layout_scan_12eye(
        ctypes.cast(layouts_buffer, ctypes.c_void_p),
        ctypes.c_uint32(n_layouts),
        ctypes.c_int(mc_version),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        ctypes.c_int32(max_results),
    )
    _check(total)
    if total > max_results:   # buffer trop petit : on relance, rien n'est perdu
        return layout_scan_12eye(layouts_buffer, n_layouts, mc_version, max_results=_grow(total))
    actual = min(total, max_results)
    return (total, out[:actual].tolist())


def layout_scan_min_eyes(layouts_buffer, n_layouts: int, min_eyes: int,
                           mc_version: int = 28,
                           max_results: int = 4096) -> tuple[int, list[tuple[int, int]]]:
    """Same as layout_scan_12eye but for arbitrary min_eyes (6-11)."""
    if not is_available():
        _unavailable()
    if n_layouts == 0:
        return (0, [])
    import numpy as np
    out_seeds = np.zeros(max_results, dtype=np.int64)
    out_eyes = np.zeros(max_results, dtype=np.int32)
    total = _CUDA_LIB.gaider_cuda_layout_scan_min_eyes(
        ctypes.cast(layouts_buffer, ctypes.c_void_p),
        ctypes.c_uint32(n_layouts),
        ctypes.c_int(min_eyes),
        ctypes.c_int(mc_version),
        out_seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        out_eyes.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        ctypes.c_int32(max_results),
    )
    _check(total)
    if total > max_results:   # buffer trop petit : on relance, rien n'est perdu
        return layout_scan_min_eyes(layouts_buffer, n_layouts, min_eyes, mc_version, max_results=_grow(total))
    actual = min(total, max_results)
    return (total, list(zip(out_seeds[:actual].tolist(),
                              out_eyes[:actual].tolist())))


def has_eyes_arr() -> bool:
    """True if the loaded CUDA lib has gaider_cuda_eyes_arr (newer build)."""
    _load_cuda()
    return _CUDA_AVAILABLE is True and hasattr(_CUDA_LIB, "gaider_cuda_eyes_arr")


def eyes_arr(seeds, cxs, czs, mc_version: int, grid: int = 7):
    """GPU grid-max eye count for parallel arrays (seeds, cxs, czs) of CPU-snapped
    stronghold positions. Returns an int32 numpy array (eye count per entry) or
    None if CUDA / the symbol is unavailable. The caller validates survivors on CPU."""
    if not has_eyes_arr():
        return None
    import numpy as np
    s = np.ascontiguousarray(seeds, dtype=np.int64)
    cx = np.ascontiguousarray(cxs, dtype=np.int32)
    cz = np.ascontiguousarray(czs, dtype=np.int32)
    n = int(s.shape[0])
    out = np.zeros(n, dtype=np.int32)
    if n == 0:
        return out
    _rc = _CUDA_LIB.gaider_cuda_eyes_arr(
        s.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        cx.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        cz.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        ctypes.c_int32(n), ctypes.c_int(int(mc_version)),
        ctypes.c_int(int(grid)),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)))
    _check(_rc)
    return out


# ═════════════════════════════════════════════════════════════════════════════
# CRUNCHINESS FILTER — yo-score pre-filter for mushroom island seeds
#
# Wraps gaider_cuda_crunchiness_filter() from the CUDA library.
# This is a port of COMMISSION's KernelFilterSeeds stage.
#
# Usage:
#   total, seeds = crunchiness_filter(seed_start=0, n=2**28, threshold=0.035)
#
# `threshold` controls the pass rate:
#   0.045 → ~1 in 2 700  (loose)
#   0.035 → ~1 in 9 400  (default)
#   0.030 → ~1 in 26 000 (tight)
#   0.025 → ~1 in 54 000 (very tight)
#
# Returns (total_matches, list_of_seeds).  The list is capped to max_results
# (default 65536); increase it if you expect more hits in a single batch.
# ═════════════════════════════════════════════════════════════════════════════

def _symbol_exists(lib: ctypes.CDLL, name: str) -> bool:
    """Return True only if *name* is actually exported by the loaded DLL/SO.

    ctypes does NOT raise when you set argtypes / restype on a missing symbol —
    it raises only at call-time.  To detect the symbol early we probe the OS
    loader directly:
      • Windows  : GetProcAddress via kernel32
      • Linux    : dlsym via libdl (handle 0 = RTLD_DEFAULT)
    Returns False on any error so the caller can skip the bind gracefully.
    """
    try:
        if platform.system() == "Windows":
            import ctypes.util as _cu
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # lib._handle is the HMODULE (an int on Windows ctypes)
            addr = kernel32.GetProcAddress(lib._handle, name.encode())
            return addr != 0
        else:
            # POSIX: use dlsym to probe the already-loaded handle
            libdl = ctypes.CDLL("libdl.so.2", use_errno=True)
            libdl.dlsym.restype = ctypes.c_void_p
            addr = libdl.dlsym(lib._handle, name.encode())
            return addr is not None and addr != 0
    except Exception:
        return False


def _bind_crunchiness(lib: ctypes.CDLL) -> None:
    """Bind gaider_cuda_crunchiness_filter and _ex only if they exist in the DLL.

    NOTE: ctypes does NOT raise AttributeError when setting argtypes on a
    missing symbol — the error only surfaces at call time, causing silent
    failures (0 results, no exception).  We probe the OS loader first.
    """
    if _symbol_exists(lib, "gaider_cuda_crunchiness_filter"):
        lib.gaider_cuda_crunchiness_filter.argtypes = [
            ctypes.c_int64,                    # seed_start
            ctypes.c_int32,                    # n
            ctypes.c_float,                    # threshold
            ctypes.POINTER(ctypes.c_int64),    # out_seeds
            ctypes.c_int32,                    # max_results
        ]
        lib.gaider_cuda_crunchiness_filter.restype = ctypes.c_int
    else:
        # Remove the attribute so crunchiness_filter() will raise a clear error
        try:
            delattr(lib, "gaider_cuda_crunchiness_filter")
        except AttributeError:
            pass

    if _symbol_exists(lib, "gaider_cuda_crunchiness_filter_ex"):
        lib.gaider_cuda_crunchiness_filter_ex.argtypes = [
            ctypes.c_int64,                    # seed_start
            ctypes.c_int32,                    # n
            ctypes.c_int32,                    # climate_id
            ctypes.c_float,                    # threshold
            ctypes.c_int32,                    # mode_flag (0=crunchy, 1=bland)
            ctypes.POINTER(ctypes.c_int64),    # out_seeds
            ctypes.c_int32,                    # max_results
        ]
        lib.gaider_cuda_crunchiness_filter_ex.restype = ctypes.c_int
    else:
        try:
            delattr(lib, "gaider_cuda_crunchiness_filter_ex")
        except AttributeError:
            pass


# _bind_crunchiness is now called directly inside _load_cuda above.


def crunchiness_filter(
        seed_start: int,
        n: int,
        threshold: float = 0.035,
        max_results: int = 65536,
) -> "tuple[int, list[int]]":
    """GPU yo-score pre-filter: scan `n` seeds from `seed_start` and keep
    those whose crunchiness score is below `threshold`.

    Returns ``(total_match_count, list_of_seeds)``.  ``list_of_seeds`` is
    capped to ``max_results``; ``total_match_count`` may exceed it.

    Raises ``RuntimeError`` if CUDA is not available or if the compiled
    library does not export ``gaider_cuda_crunchiness_filter`` (recompile
    from the updated ``gaider_cuda.cu`` to get this function).
    """
    if not is_available():
        raise RuntimeError(
            f"CUDA not available: {get_load_error()}"
        )

    fn = getattr(_CUDA_LIB, "gaider_cuda_crunchiness_filter", None)
    if fn is None:
        raise RuntimeError(
            "gaider_cuda_crunchiness_filter not found in the compiled library.\n"
            "Please recompile cubiomes_src/gaider_cuda.cu (the updated version\n"
            "now includes the crunchiness kernel from COMMISSION)."
        )

    import numpy as np
    out = np.zeros(max_results, dtype=np.int64)
    total = fn(
        ctypes.c_int64(seed_start),
        ctypes.c_int32(n),
        ctypes.c_float(threshold),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        ctypes.c_int32(max_results),
    )
    _check(total)
    if total > max_results:   # buffer trop petit : on relance, rien n'est perdu
        return crunchiness_filter(seed_start, n, threshold, max_results=_grow(total))
    actual = min(total, max_results)
    return (total, out[:actual].tolist())


def has_exact_crunchiness() -> bool:
    """True when the GPU library has the exact crunchiness kernel (p96)."""
    return is_available() and getattr(_CUDA_LIB, "gaider_cuda_crunchiness_filter_w", None) is not None


def crunchiness_filter_w(seed_start: int, n: int, clim_hash, oct_lo, oct_hi, w_a, w_b,
                         threshold: float, mode_flag: int, max_results: int,
                         eps: float = 1e-5) -> "tuple[int, list[int]]":
    """EXACT GPU crunchiness pre-filter (p96): same score as the CPU engine
    (octave hashes and A/B weights from libcubiomes ssa_crunch_gpu_params),
    kept within eps of passing → a superset of the CPU result, which re-tests
    every candidate. Returns (total, candidates); when total > max_results only
    the first max_results are returned (the caller splits the batch)."""
    if not is_available():
        _unavailable()
    fn = _CUDA_LIB.gaider_cuda_crunchiness_filter_w
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.c_int64, ctypes.c_int32, ctypes.c_uint64, ctypes.c_uint64,
                   ctypes.c_int32, ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
                   ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
                   ctypes.c_float, ctypes.c_float, ctypes.c_int32,
                   ctypes.POINTER(ctypes.c_int64), ctypes.c_int32]
    k = len(oct_lo)
    a_lo = (ctypes.c_uint64 * 6)(*oct_lo); a_hi = (ctypes.c_uint64 * 6)(*oct_hi)
    a_wa = (ctypes.c_float * 6)(*w_a); a_wb = (ctypes.c_float * 6)(*w_b)
    import numpy as np
    out = np.zeros(max(1, max_results), dtype=np.int64)
    total = fn(seed_start, n, clim_hash[0], clim_hash[1], k, a_lo, a_hi, a_wa, a_wb,
               float(threshold), float(eps), int(mode_flag),
               out.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)), int(max_results))
    _check(total)
    return (int(total), out[:min(total, max_results)].tolist())


def crunchiness_filter_ex(
        seed_start: int,
        n: int,
        climate_id: int = 2,
        threshold: float = 0.035,
        mode_flag: int = 0,
        max_results: int = 65536,
) -> "tuple[int, list[int]]":
    """Extended GPU yo-score pre-filter: like crunchiness_filter but for any
    climate noise selected by climate_id.

    climate_id:
      0 = Temperature
      1 = Humidity
      2 = Continentalness  (same as crunchiness_filter)
      3 = Erosion
      5 = Weirdness

    mode_flag:
      0 = crunchy (keep seeds where score < threshold)
      1 = bland   (keep seeds where score >= threshold)

    Uses Commission weighting (35/35/11/11/4/4) on the 3 dominant octave pairs.

    Falls back to the old crunchiness_filter (continentalness, mode_flag ignored)
    if the extended symbol is not in the compiled library (pre-update build).
    """
    if not is_available():
        raise RuntimeError(f"CUDA not available: {get_load_error()}")

    fn = getattr(_CUDA_LIB, "gaider_cuda_crunchiness_filter_ex", None)
    if fn is None:
        # Fallback: old library without _ex — only works correctly for cont.
        return crunchiness_filter(seed_start, n, threshold, max_results)

    import numpy as np
    out = np.zeros(max_results, dtype=np.int64)
    total = fn(
        ctypes.c_int64(seed_start),
        ctypes.c_int32(n),
        ctypes.c_int32(climate_id),
        ctypes.c_float(threshold),
        ctypes.c_int32(mode_flag),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        ctypes.c_int32(max_results),
    )
    _check(total)
    if total > max_results:   # buffer trop petit : on relance, rien n'est perdu
        return crunchiness_filter_ex(seed_start, n, climate_id, threshold, mode_flag, max_results=_grow(total))
    actual = min(total, max_results)
    return (total, out[:actual].tolist())

