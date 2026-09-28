"""biome_gpu.py — GPU 128-stronghold biome snap (1.13-1.17), bit-exact vs cubiomes.

Computes the 128 stronghold positions per seed on the GPU — the biome-snap that is
the bottleneck of the 128-stronghold mode on CPU. Pairs with gaider_cuda (which
counts eyes at given positions). 1.18+ is NOT supported here (noise-based biomes,
a different engine) — callers must fall back to CPU for >= 1.18.

Public API:
    available()            -> bool          (CUDA + biome_gpu.dll usable)
    supported_version(mc)  -> bool          (True for 1.13-1.17 == mc 16..21)
    get_template(mc)       -> (LayerG[64], oceanmix, validB, validM) | None
    strongholds128(mc, seeds) -> (xs[n,128], zs[n,128]) int32 | None

Self-test:  py biome_gpu.py
"""
import os, ctypes, platform, subprocess, time, shutil
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
GL_MAX = 64

# mc codes (cubiomes): 1.13=16, 1.14=17, 1.15=18, 1.16=20, 1.17=21
_MC_MIN, _MC_MAX = 16, 21


class LayerG(ctypes.Structure):
    # must match cubiomes_src/gtypes.h exactly
    _fields_ = [
        ("type", ctypes.c_int), ("mc", ctypes.c_int),
        ("zoom", ctypes.c_int), ("edge", ctypes.c_int),
        ("layerSalt", ctypes.c_uint64),
        ("startSalt", ctypes.c_uint64),
        ("startSeed", ctypes.c_uint64),
        ("p", ctypes.c_int), ("p2", ctypes.c_int),
    ]


_LIB = None          # biome_gpu.dll (kernel/host)
_CUB = None          # libcubiomes  (template extraction)
_AVAIL = None        # tri-state: None=unprobed, True/False
_ERR = ""
_TMPL_CACHE = {}


def _lib_name():
    s = platform.system()
    if s == "Windows":
        return "biome_gpu.dll"
    if s == "Darwin":
        return "libbiome_gpu.dylib"
    return "libbiome_gpu.so"


def _find_lib():
    p = os.path.join(_HERE, _lib_name())
    return p if os.path.exists(p) else None


def _compile():
    """Compile cubiomes_src/biome_gpu.cu -> biome_gpu.dll via nvcc. Mirrors
    gaider_cuda.py's arch detection. Returns path or None (caller falls back to CPU)."""
    global _ERR
    src = os.path.join(_HERE, "cubiomes_src", "biome_gpu.cu")
    if not os.path.exists(src):
        _ERR = f"Missing {src}"
        return None
    out = os.path.join(_HERE, _lib_name())
    is_win = (platform.system() == "Windows")

    # don't recompile if the .dll is newer than the .cu (+ the shared .cuh)
    cuh = os.path.join(_HERE, "cubiomes_src", "biome_gpu_kernel.cuh")
    newest_src = max(os.path.getmtime(src),
                     os.path.getmtime(cuh) if os.path.exists(cuh) else 0)
    if os.path.exists(out) and os.path.getmtime(out) >= newest_src:
        return out

    def _detect_sm():
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=compute_cap",
                                "--format=csv,noheader"], capture_output=True, timeout=10)
            cap = r.stdout.decode().strip().split("\n")[0].strip().replace(".", "")
            if cap.isdigit() and len(cap) >= 2:
                return f"-arch=sm_{cap}"
        except Exception:
            pass
        return None

    archs = []
    if not is_win:
        archs.append("-arch=native")
    d = _detect_sm()
    if d:
        archs.append(d)
    for sm in ["sm_120", "sm_100", "sm_90", "sm_89", "sm_86", "sm_80", "sm_75", "sm_70", "sm_61"]:
        if f"-arch={sm}" not in archs:
            archs.append(f"-arch={sm}")

    print("[biome_gpu] Compiling biome_gpu.cu (one time, ~30s)…")
    ret, last = None, ""
    for a in archs:
        cmd = ["nvcc", "-O3", "-shared", a]
        if not is_win:
            cmd += ["-Xcompiler", "-fPIC"]
        cmd += [src, "-o", out]
        try:
            ret = subprocess.run(cmd, capture_output=True, timeout=180)
        except FileNotFoundError:
            _ERR = ("nvcc not found. Install the CUDA Toolkit and put nvcc on PATH.")
            return None
        except subprocess.TimeoutExpired:
            _ERR = "nvcc timed out (>3 min)."
            return None
        if ret.returncode == 0:
            print(f"[biome_gpu] OK: {out}")
            return out
        last = ((ret.stderr or b"").decode(errors="replace") +
                (ret.stdout or b"").decode(errors="replace")).strip()
        print(f"[biome_gpu] {a} failed: {last[:300]}")
    _ERR = f"nvcc failed:\n{last[:2000]}"
    return None


def _load():
    global _LIB, _CUB, _AVAIL, _ERR
    if _AVAIL is not None:
        return
    # RETIRÉ (26/09/2026, décision d'Édouard) : biome_gpu.cu ne compile pas en moins
    # de 15 min pour les 5 familles de cartes, et la compilation intégrée (limite
    # 3 min) échouait donc toujours → ce mode n'a jamais tourné. Le scan « 128
    # strongholds » 1.13-1.17 passe par le moteur CPU (déjà utilisé en pratique).
    _ERR = ("GPU « 128 strongholds » 1.13-1.17 retiré — calcul CPU")
    _AVAIL = False
    return
    path = _find_lib()
    # recompile if missing or stale
    np_ = _compile()
    if np_:
        path = np_
    if path is None:
        _AVAIL = False
        return
    try:
        lib = ctypes.CDLL(path)
        lib.biome_gpu_strongholds128.argtypes = [
            ctypes.c_int, ctypes.POINTER(LayerG), ctypes.c_int,
            ctypes.c_uint64, ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_int64), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
            ctypes.POINTER(ctypes.c_int32),   # out_eyes (fused GPU eye prefilter)
        ]
        lib.biome_gpu_strongholds128.restype = ctypes.c_int

        import cubiomes_wrap as cw
        cub = cw.make_worker_lib()._lib
        cub.biome_gpu_get_template.argtypes = [
            ctypes.c_int, ctypes.POINTER(LayerG), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
        ]
        cub.biome_gpu_get_template.restype = ctypes.c_int

        _LIB, _CUB, _AVAIL = lib, cub, True
    except Exception as e:
        _ERR = f"load/bind failed: {e}"
        _AVAIL = False


def available():
    _load()
    return bool(_AVAIL)


def last_error():
    return _ERR


def supported_version(mc):
    return _MC_MIN <= int(mc) <= _MC_MAX


def get_template(mc):
    """Returns (LayerG*64 array, oceanmix, validB, validM) for `mc`, cached.
    None if the version is unsupported (>= 1.18) or extraction failed."""
    _load()
    if not _AVAIL or not supported_version(mc):
        return None
    if mc in _TMPL_CACHE:
        return _TMPL_CACHE[mc]
    tmpl = (LayerG * GL_MAX)()
    omix = ctypes.c_int(0)
    vB = ctypes.c_uint64(0)
    vM = ctypes.c_uint64(0)
    ok = _CUB.biome_gpu_get_template(int(mc), tmpl, ctypes.byref(omix),
                                     ctypes.byref(vB), ctypes.byref(vM))
    if not ok:
        return None
    res = (tmpl, omix.value, vB.value, vM.value)
    _TMPL_CACHE[mc] = res
    return res


def strongholds128(mc, seeds):
    """Compute the 128 stronghold positions + a fused eye prefilter on the GPU.

    mc: 16..21 (1.13-1.17). seeds: array-like of int64 world seeds.
    Returns (xs, zs, eyes), each numpy int32 [n, 128]: block-x, block-z, and the
    grid-max eye prefilter (0..12) per stronghold (an UPPER BOUND — the caller must
    exact-validate survivors on CPU). None on failure / unsupported version."""
    _load()
    if not _AVAIL or not supported_version(mc):
        return None
    t = get_template(mc)
    if t is None:
        return None
    tmpl, omix, vB, vM = t
    seeds = np.ascontiguousarray(seeds, dtype=np.int64)
    n = int(len(seeds))
    if n == 0:
        z = np.zeros((0, 128), np.int32)
        return (z, z.copy(), z.copy())
    ox = np.zeros(n * 128, dtype=np.int32)
    oz = np.zeros(n * 128, dtype=np.int32)
    oe = np.zeros(n * 128, dtype=np.int32)
    rc = _LIB.biome_gpu_strongholds128(
        int(mc), tmpl, int(omix), ctypes.c_uint64(vB), ctypes.c_uint64(vM),
        seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)), n,
        ox.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        oz.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        oe.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)))
    if rc != 0:
        return None
    return ox.reshape(n, 128), oz.reshape(n, 128), oe.reshape(n, 128)


if __name__ == "__main__":
    # self-test: GPU vs cubiomes (a few seeds, all supported versions) + throughput
    if not available():
        print("biome_gpu NOT available:", last_error())
        raise SystemExit(1)
    import cubiomes_wrap as cw
    lib = cw.make_worker_lib()
    vers = [(16, "1.13"), (17, "1.14"), (18, "1.15"), (20, "1.16"), (21, "1.17")]
    seeds = np.array([1000, 1234567, 8675309], dtype=np.int64)
    allok = True
    for mc, name in vers:
        res = strongholds128(mc, seeds)
        if res is None:
            print(f"{name}: strongholds128 returned None"); allok = False; continue
        xs, zs, es = res
        mism = 0
        for si, s in enumerate(seeds.tolist()):
            lib.setup_and_seed(mc, int(s))
            ref = lib.find_strongholds(max_count=128)
            for i, (rx, rz) in enumerate(ref):
                if xs[si, i] != rx or zs[si, i] != rz:
                    mism += 1
        print(f"{name} (mc={mc}): {128*len(seeds)-mism}/{128*len(seeds)} "
              f"{'OK' if mism == 0 else '*** MISMATCH ***'}")
        allok = allok and (mism == 0)
    # throughput
    N = 200000
    bs = np.arange(1000000, 1000000 + N, dtype=np.int64)
    t = time.perf_counter()
    strongholds128(20, bs)
    dt = time.perf_counter() - t
    print(f"\nthroughput (1.16): {N} seeds in {dt:.1f}s => {N/dt:,.0f} seeds/s")
    print("ALL BIT-EXACT" if allok else "MISMATCH — do not use")
