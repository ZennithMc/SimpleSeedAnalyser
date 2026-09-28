"""
terrain.py — block-perfect 1.18+ terrain surface Y (xpple/cubiomes terrainnoise),
exposed to SSA via a self-contained shared lib (terrain.dll / .so).

API:
    available()                         -> bool
    TerrainEngine(seed, mc=MC_1_18)
        .exact_y(x, z)                  -> int   (world Y of highest SOLID block)
        .surface_grid(x0, z0, w, h, step) -> int32[h,w]  (highest-solid Y grid)
        .column(x, z)                   -> int32[384]     (block ids, idx0 = y -64)

Convention: the engine returns the highest SOLID block (what you read standing on
the surface in-game). NOISE caves are included; world carvers (ravines/canyons) are
NOT yet (Phase 2). Terrain is block-perfect everywhere except carver-breached spots.
"""
import os, sys, ctypes, platform, subprocess, threading
import numpy as np

MC_1_18 = 22   # cubiomes enum (== MC_1_18_2; 1.18.x overworld terrain is seed-identical)

# Diagnostic: print every GPU surface_grid call (count / points / ms). Lets us see
# whether the Exact-Y map is GPU-bound (few big fast calls) or orchestration-bound
# (many tiny calls = overhead, GPU idle). Set False to silence.
_SG_DEBUG = True

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_HERE, "cubiomes_terrain")
_SRC = ["terrain_bridge.c", "terrainnoise.c", "biomenoise.c", "noise.c",
        "biomes.c", "layers.c", "generator.c", "util.c"]

_LIB = None
_AVAIL = None
_ERR = ""
_LOCK = threading.Lock()


def _lib_name():
    s = platform.system()
    if s == "Windows":
        return "terrain.dll"
    if s == "Darwin":
        return "libterrain.dylib"
    return "libterrain.so"          # not "terrain.so": Python would import it as the terrain module


def _compile():
    """Compile cubiomes_terrain -> shared lib (once; rebuilt only if a source is
    newer). Returns the lib path or None. Tries gcc, then clang, then MSVC cl."""
    global _ERR
    out = os.path.join(_HERE, _lib_name())
    srcs = [os.path.join(_SRC_DIR, s) for s in _SRC]
    # installed / frozen app: no sources, never compile — use the shipped library
    # (before: missing sources returned None even with terrain.dll present, so
    # Exact Y never worked in the .exe)
    if getattr(sys, "frozen", False) or not all(os.path.exists(s) for s in srcs):
        if os.path.exists(out):
            return out
        _ERR = f"{os.path.basename(out)} missing"
        return None
    newest = max(os.path.getmtime(s) for s in srcs)
    if os.path.exists(out) and os.path.getmtime(out) >= newest:
        return out

    is_win = (platform.system() == "Windows")
    print("[terrain] Compiling terrain lib (one time)…")
    # GCC / Clang
    for cc in ("gcc", "clang", "cc"):
        cmd = [cc, "-O3", "-fwrapv", "-fPIC", "-shared", "-I", _SRC_DIR,
               *srcs, "-lm", "-o", out]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=180)
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            _ERR = f"{cc} timed out"; return None
        if r.returncode == 0 and os.path.exists(out):
            print(f"[terrain] OK: {out}")
            return out
        _ERR = (r.stderr or b"").decode(errors="replace")[:2000]
    # nvcc — guaranteed present on this machine (used to build noise_gpu) and it
    # drives the MSVC host compiler itself, so no Visual Studio dev-prompt needed.
    nvcc_cmd = ["nvcc", "-O3", "--shared", "-I", _SRC_DIR, *srcs, "-o", out]
    if not is_win:
        nvcc_cmd = ["nvcc", "-O3", "-Xcompiler", "-fPIC", "--shared", "-I", _SRC_DIR,
                    *srcs, "-lm", "-o", out]
    try:
        r = subprocess.run(nvcc_cmd, capture_output=True, timeout=180)
        if r.returncode == 0 and os.path.exists(out):
            print(f"[terrain] OK (nvcc): {out}")
            return out
        _ERR = ((r.stderr or b"") + (r.stdout or b"")).decode(errors="replace")[:2000]
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        _ERR = "nvcc timed out"; return None
    # MSVC cl (available wherever the CUDA/nvcc toolchain is set up)
    if is_win:
        cmd = ["cl", "/O2", "/LD", "/nologo", f"/I{_SRC_DIR}", *srcs,
               f"/Fe:{out}"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(out):
                print(f"[terrain] OK (cl): {out}")
                return out
            _ERR = (r.stdout or b"").decode(errors="replace")[:2000]
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            _ERR = "cl timed out"; return None
    _ERR = ("No C compiler found (gcc/clang/cl). To build manually:\n"
            "  gcc -O3 -fwrapv -fPIC -shared -I cubiomes_terrain "
            + " ".join("cubiomes_terrain/" + s for s in _SRC)
            + " -lm -o " + _lib_name() + "\n[last error]\n" + _ERR)
    return None


def _load():
    global _LIB, _AVAIL, _ERR
    if _AVAIL is not None:
        return
    with _LOCK:
        if _AVAIL is not None:
            return
        path = _compile()
        if path is None:
            _AVAIL = False
            return
        try:
            lib = ctypes.CDLL(path)
            lib.tb_alloc.restype = ctypes.c_void_p
            lib.tb_free.argtypes = [ctypes.c_void_p]
            lib.tb_init.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint64]
            lib.tb_init.restype = ctypes.c_int
            lib.tb_surface_y.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int]   # +caves
            lib.tb_surface_y.restype = ctypes.c_int
            lib.tb_surface_grid.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int,                    # +caves
                                            ctypes.POINTER(ctypes.c_int)]
            lib.tb_column.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                      ctypes.POINTER(ctypes.c_int)]
            try:   # batch Precise-Y search filter (newer terrain.dll)
                lib.tb_precise_y_filter.argtypes = [
                    ctypes.POINTER(ctypes.c_uint64), ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_double, ctypes.c_double,           # coverage, confidence
                    ctypes.POINTER(ctypes.c_uint8)]
                lib.tb_precise_y_filter.restype = ctypes.c_int
                # caller-owned context API (thread-safe multithreaded path)
                lib.tb_precise_y_alloc.argtypes = [ctypes.c_int]
                lib.tb_precise_y_alloc.restype  = ctypes.c_void_p
                lib.tb_precise_y_free.argtypes  = [ctypes.c_void_p]
                lib.tb_precise_y_filter_ctx.argtypes = [
                    ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_double, ctypes.c_double,           # coverage, confidence
                    ctypes.POINTER(ctypes.c_uint8)]
                lib.tb_precise_y_filter_ctx.restype = ctypes.c_int
            except AttributeError:
                pass
            _LIB, _AVAIL = lib, True
        except Exception as e:
            _ERR = f"load failed: {e}"
            _AVAIL = False


def available():
    _load()
    return bool(_AVAIL)


def last_error():
    return _ERR


# ── GPU (CUDA) path: terrain_gpu.cu → terrain_gpu.dll (block-perfect, validated
# bit-exact vs CPU on the RTX 5080). Falls back to the CPU bridge if nvcc/GPU absent.
_GPU = None                          # None=untried, False=unavailable, lib=loaded
_GPU_LOCK = threading.Lock()
_GPU_SRC = ["terrain_gpu.cu"] + _SRC[1:]   # the .cu + the cubiomes .c (no terrain_bridge.c)


def _gpu_name():
    s = platform.system()
    if s == "Windows":
        return "terrain_gpu.dll"
    if s == "Darwin":
        return "libterrain_gpu.dylib"
    return "terrain_gpu.so"


def _compile_load_gpu():
    out = os.path.join(_HERE, _gpu_name())
    srcs = [os.path.join(_SRC_DIR, s) for s in _GPU_SRC]
    shipped = getattr(sys, "frozen", False) or not all(os.path.exists(s) for s in srcs)
    if shipped and not os.path.exists(out):
        return None
    if not shipped and not (os.path.exists(out) and
                            os.path.getmtime(out) >= max(os.path.getmtime(s) for s in srcs)):
        is_win = (platform.system() == "Windows")
        # -DTGPU_FP32: the MAP uses the float density chain (≈10-60× faster on any
        # consumer NVIDIA GPU, whose FP64 is throttled). The per-block hover read-out
        # uses the FP64 CPU lib (terrain.dll) and stays exact. No -arch is set, so
        # nvcc embeds PTX → JITs for whatever NVIDIA GPU the user has (broad compat).
        cmd = ["nvcc", "-O3", "--shared", "-DTGPU_FP32", "-I", _SRC_DIR]
        if not is_win:
            cmd += ["-Xcompiler", "-fPIC"]
        cmd += srcs + ["-o", out]
        print("[terrain] Compiling terrain_gpu (CUDA, one time ~1min)…")
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=300)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0 or not os.path.exists(out):
            return None
        print(f"[terrain] OK (GPU): {out}")
    try:
        lib = ctypes.CDLL(out)
        lib.tgpu_surface_grid.argtypes = [
            ctypes.c_int, ctypes.c_uint64,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int,                                   # +caves
            ctypes.POINTER(ctypes.c_int)]
        lib.tgpu_surface_grid.restype = ctypes.c_int
        return lib
    except Exception:
        return None


def _gpu():
    """Lazily compile + load the CUDA terrain lib. Returns the lib, or None (→ CPU)."""
    global _GPU
    if _GPU is not None:
        return _GPU or None
    with _GPU_LOCK:
        if _GPU is None:
            _GPU = _compile_load_gpu() or False
        return _GPU or None


def gpu_available():
    return _gpu() is not None


def surface_grid_gpu(mc, seed, x0, z0, w, h, step=1, caves=True):
    """Block-exact surface-Y grid via the GPU ONLY, with no per-seed CPU engine.
    The GPU re-seeds itself internally (tgpu_upload), so this is the right call for
    a per-seed search filter (creating a TerrainEngine per seed is wasteful — its
    CPU column cache is never used on the GPU path — and churns memory).
    Returns int32[h, w] (top solid Y) or None if no GPU / the call failed."""
    g = _gpu()
    if g is None:
        return None
    out = np.empty(int(w) * int(h), dtype=np.int32)
    rc = g.tgpu_surface_grid(
        int(mc), ctypes.c_uint64(int(seed) & 0xFFFFFFFFFFFFFFFF),
        int(x0), int(z0), int(w), int(h), int(step), 1 if caves else 0,
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
    if rc == 0:
        return (out - 1).reshape(int(h), int(w))
    return None


# ── Grille de surface EXACTE (CPU FP64) pour la recherche ─────────────────────
# La condition Exact-Y « par graine » utilisait le GPU FP32 comme verdict final
# (approché, et « False » systématique sans GPU NVIDIA). Elle utilise désormais ce
# calcul CPU exact : un contexte C par thread (vrai parallélisme entre les workers
# de recherche, ctypes relâche le GIL pendant l'appel).
_TLS_GRID = threading.local()


def _thread_ctx(mc, seed):
    _load()
    if not _AVAIL:
        raise RuntimeError("terrain lib unavailable: " + _ERR)
    ctx = getattr(_TLS_GRID, "ctx", None)
    if not ctx:
        ctx = _LIB.tb_alloc()
        if not ctx:
            raise RuntimeError("tb_alloc failed")
        _TLS_GRID.ctx = ctx
    if not _LIB.tb_init(ctx, int(mc), ctypes.c_uint64(int(seed) & 0xFFFFFFFFFFFFFFFF)):
        raise RuntimeError("tb_init failed (unsupported version?)")
    return ctx


def surface_grid_exact(mc, seed, x0, z0, w, h, step=1, caves=True):
    """Grille de surface Y EXACTE (bloc solide le plus haut), int32[h, w], CPU."""
    ctx = _thread_ctx(mc, seed)
    w, h = int(w), int(h)
    out = np.empty(w * h, dtype=np.int32)
    _LIB.tb_surface_grid(ctx, int(x0), int(z0), w, h, int(step), 1 if caves else 0,
                         out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
    return (out - 1).reshape(h, w)


def precise_y_filter(seeds, mc, at_x, at_z, r, y_min, y_max,
                     caves=True, invert=False, sample_step=None,
                     coverage=0.0, confidence=0.95):
    """C-native batch Precise-Y filter for ONE chunk of seeds (the caller runs
    several chunks in parallel threads — this releases the GIL). `seeds` is a
    uint64 array; returns a uint8 mask (1 = passes), or None if the lib lacks the
    symbol. coverage<=0 → pass if ANY sample's Y meets the (optional inverted)
    range (early-exit). coverage>0 → pass if the Wilson lower bound (at the given
    confidence) of the in-range fraction over the strided disk ≥ coverage."""
    _load()
    if not _AVAIL or not hasattr(_LIB, "tb_precise_y_filter"):
        return None
    seeds = np.ascontiguousarray(seeds, dtype=np.uint64)
    n = int(len(seeds))
    mask = np.zeros(n, dtype=np.uint8)
    if n == 0:
        return mask
    if sample_step is None:
        sample_step = 1 if r <= 0 else max(1, (2 * r) // 64 + 1)
    _LIB.tb_precise_y_filter(
        seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)), n, int(mc),
        int(at_x), int(at_z), int(r), int(y_min), int(y_max),
        1 if caves else 0, 1 if invert else 0, int(sample_step),
        float(coverage), float(confidence),
        mask.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)))
    return mask


# Per-OS-thread terrain context (real thread-safety; no compiler TLS in the DLL).
_tls = threading.local()

def precise_y_filter_ctx(seeds, mc, at_x, at_z, r, y_min, y_max,
                         caves=True, invert=False, sample_step=None,
                         coverage=0.0, confidence=0.95):
    """Thread-safe batch Precise-Y filter: each calling OS thread owns its OWN C
    terrain context (allocated once, reused). This is the hot multithreaded path —
    unlike precise_y_filter() it never relies on the DLL's (unreliable) thread-local
    statics. coverage<=0 → any-match; coverage>0 → Wilson coverage test (see
    precise_y_filter). Returns a uint8 mask, or None if the lib lacks the symbols."""
    _load()
    if not _AVAIL or not hasattr(_LIB, "tb_precise_y_filter_ctx"):
        return None
    ctx = getattr(_tls, "ctx", None)
    if ctx is None or getattr(_tls, "ctx_mc", None) != mc:
        if ctx:
            _LIB.tb_precise_y_free(ctx)
        ctx = _LIB.tb_precise_y_alloc(int(mc))
        if not ctx:
            _tls.ctx = None
            return None
        _tls.ctx = ctx
        _tls.ctx_mc = mc
    seeds = np.ascontiguousarray(seeds, dtype=np.uint64)
    n = int(len(seeds))
    mask = np.zeros(n, dtype=np.uint8)
    if n == 0:
        return mask
    if sample_step is None:
        sample_step = 1 if r <= 0 else max(1, (2 * r) // 64 + 1)
    _LIB.tb_precise_y_filter_ctx(
        ctypes.c_void_p(ctx),
        seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)), n,
        int(at_x), int(at_z), int(r), int(y_min), int(y_max),
        1 if caves else 0, 1 if invert else 0, int(sample_step),
        float(coverage), float(confidence),
        mask.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)))
    return mask


class TerrainEngine:
    """One engine per (seed, mc). Holds a per-seed noise-column cache (in C)."""
    def __init__(self, seed, mc=MC_1_18):
        _load()
        if not _AVAIL:
            raise RuntimeError("terrain lib unavailable: " + _ERR)
        self._ctx = _LIB.tb_alloc()
        if not self._ctx:
            raise RuntimeError("tb_alloc failed")
        if not _LIB.tb_init(self._ctx, int(mc), ctypes.c_uint64(int(seed) & 0xFFFFFFFFFFFFFFFF)):
            raise RuntimeError("tb_init failed (unsupported version?)")
        self.seed = int(seed)
        self.mc = int(mc)

    def exact_y(self, x, z, caves=True):
        """World Y of the highest SOLID block at (x, z).
        caves=True -> full exact surface; caves=False -> surface with no cave carving."""
        return int(_LIB.tb_surface_y(self._ctx, int(x), int(z), 1 if caves else 0)) - 1

    def surface_grid(self, x0, z0, w, h, step=1, caves=True):
        """Highest-solid Y over a (w x h) grid, `step` blocks apart. int32[h, w].
        Uses the CUDA path when available (much faster), else the CPU bridge.
        Both return the air-above-solid Y; the -1 gives the top solid block.
        caves=True -> full exact; caves=False -> terrain shape with no caves."""
        _cv = 1 if caves else 0
        out = np.empty(int(w) * int(h), dtype=np.int32)
        g = _gpu()
        if g is not None:
            import time as _t
            _t0 = _t.perf_counter()
            rc = g.tgpu_surface_grid(
                int(self.mc), ctypes.c_uint64(int(self.seed) & 0xFFFFFFFFFFFFFFFF),
                int(x0), int(z0), int(w), int(h), int(step), _cv,
                out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
            if _SG_DEBUG:
                _dt = (_t.perf_counter() - _t0) * 1000.0
                TerrainEngine._sg_n = getattr(TerrainEngine, "_sg_n", 0) + 1
                TerrainEngine._sg_ms = getattr(TerrainEngine, "_sg_ms", 0.0) + _dt
                print(f"[sg] #{TerrainEngine._sg_n} pts={int(w)*int(h):>8} "
                      f"caves={_cv} call={_dt:6.1f}ms  (cumul {TerrainEngine._sg_ms:7.0f}ms)")
            if rc == 0:
                return (out - 1).reshape(int(h), int(w))
        _LIB.tb_surface_grid(self._ctx, int(x0), int(z0), int(w), int(h), int(step), _cv,
                             out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
        return (out - 1).reshape(int(h), int(w))

    def column(self, x, z):
        """Block ids for the full 384-tall column (index 0 = world y -64)."""
        out = np.empty(384, dtype=np.int32)
        _LIB.tb_column(self._ctx, int(x), int(z),
                       out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
        return out

    def __del__(self):
        try:
            if getattr(self, "_ctx", None) and _LIB is not None:
                _LIB.tb_free(self._ctx)
                self._ctx = None
        except Exception:
            pass
