"""noise_gpu.py — GPU 1.18+ noise-biome BiomeScan prefilter (FP32), recall-safe.

The 1.18+ biome generator (climate noise + parameter tree) ported to CUDA. Each
GPU thread = 1 seed: per-seed octave init, then count grid samples whose biome is
in `allowed`, computed in FP32 (~64x the FP64 rate on consumer cards). The FP32
count is within 1 of the exact FP64 count (measured), so the GPU emits any seed
with count >= min_count-1 and the CPU validates those survivors in FP64 — 100%
recall, exact results. This is the speed unlock the "no-gain" lore misses.

Public API:
    available()           -> bool
    supported_version(mc) -> bool          (1.18+, i.e. mc >= 22)
    get_tables(mc)        -> dict | None    (flat spline + biome-tree pointers)
    biomescan(mc, seeds, xs, zs, allowed_words, y=15) -> int32 counts | None

Self-test (on a CUDA machine):  py noise_gpu.py
"""
import os, ctypes, platform, subprocess, time, threading
import numpy as np

# Serializes _load(): concurrent callers (the 12 map tile workers, the GPU search
# thread, …) must not load/bind in parallel.
_LOAD_LOCK = threading.Lock()

_HERE = os.path.dirname(os.path.abspath(__file__))
_MC_MIN = 22          # MC_1_18 (all 1.18+ supported via per-version biome tree)
_SP_CAP = 512         # spline-flat buffer (actual ~254 nodes)


class SplineFlatG(ctypes.Structure):
    # must match cubiomes_src/noise_gpu_kernel.cuh exactly
    _fields_ = [
        ("len", ctypes.c_int), ("typ", ctypes.c_int), ("fixval", ctypes.c_float),
        ("loc", ctypes.c_float * 12), ("der", ctypes.c_float * 12),
        ("val", ctypes.c_int * 12),
    ]


_LIB = None          # noise_gpu.dll (kernel/host)
_CUB = None          # libcubiomes (table extraction)
_AVAIL = None
_ERR = ""
_TBL_CACHE = {}


def _lib_name():
    s = platform.system()
    if s == "Windows":
        return "noise_gpu.dll"
    if s == "Darwin":
        return "libnoise_gpu.dylib"
    return "libnoise_gpu.so"


# Compilation runtime retirée : DLL prébuilt (tools/build_native.py), chargée et
# auto-testée par native_libs.py.


def _load():
    # Fast path: already loaded -> no lock. Otherwise serialize so exactly ONE
    # thread compiles noise_gpu.dll while the rest wait (then see _AVAIL set).
    if _AVAIL is not None:
        return
    with _LOAD_LOCK:
        _load_impl()


def _load_impl():
    global _LIB, _CUB, _AVAIL, _ERR
    if _AVAIL is not None:          # re-check inside the lock (another thread won)
        return
    import native_libs as _nl
    lib, msg = _nl.load_cuda("noise_gpu")
    if lib is None:
        _ERR = msg
        _AVAIL = False
        return
    try:
        lib.noise_gpu_biomescan.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(SplineFlatG), ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int32), ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        lib.noise_gpu_biomescan.restype = ctypes.c_int

        try:
            lib.noise_gpu_biomemap.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(SplineFlatG), ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,
                ctypes.POINTER(ctypes.c_int32), ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                ctypes.c_int, ctypes.c_int,
                ctypes.c_uint64,
                ctypes.POINTER(ctypes.c_int),
            ]
            lib.noise_gpu_biomemap.restype = ctypes.c_int
        except AttributeError:
            pass

        try:
            lib.noise_gpu_biomemc.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(SplineFlatG), ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,
                ctypes.POINTER(ctypes.c_int32), ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
                ctypes.c_uint,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_longlong, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint64),
                ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ]
            lib.noise_gpu_biomemc.restype = ctypes.c_int
        except AttributeError:
            pass            # older noise_gpu library: biome_sample stays on the CPU

        lib.noise_gpu_climatescan.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,   # mc, para, lo10k, hi10k
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ]
        lib.noise_gpu_climatescan.restype = ctypes.c_int
        try:
            lib.noise_gpu_climatefilter.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int,                  # mc, para, nmax
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_int,
                ctypes.c_uint64, ctypes.c_longlong, ctypes.c_int,           # seed0, n, mode
                ctypes.c_longlong, ctypes.c_longlong,                       # A, B
                ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,              # out, cap
                ctypes.POINTER(ctypes.c_longlong),                          # total
            ]
            lib.noise_gpu_climatefilter.restype = ctypes.c_int
        except AttributeError:
            pass          # older library: per-seed climatescan path
        try:
            lib.noise_gpu_climatescan2.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,  # mc, para, nmax, lo, hi
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ]
            lib.noise_gpu_climatescan2.restype = ctypes.c_int
        except AttributeError:
            pass          # older library: limited octaves stay on the CPU

        try:
            lib.noise_gpu_climate_diag.argtypes = [
                ctypes.c_int, ctypes.c_int,                              # mc, para
                ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_int,
                ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
            ]
            lib.noise_gpu_climate_diag.restype = ctypes.c_int
        except AttributeError:
            pass

        import cubiomes_wrap as cw
        cub = cw.make_worker_lib()._lib
        cub.noise_gpu_get_tables.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(SplineFlatG), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32)), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_int32)), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint64)), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
        ]
        cub.noise_gpu_get_tables.restype = ctypes.c_int

        _LIB, _CUB, _AVAIL = lib, cub, True
    except Exception as e:
        _ERR = f"load/bind failed: {e}"
        _AVAIL = False


def _note_err(rc):
    """Mémorise (et affiche) l'erreur CUDA exacte : l'appelant repasse en CPU, mais
    la cause n'est plus perdue."""
    global _ERR
    try:
        import native_libs as _nl
        _ERR = f"noise_gpu rc={rc} : {_nl.last_error(_LIB) or 'erreur CUDA'}"
    except Exception:
        _ERR = f"noise_gpu rc={rc}"
    print(f"[noise_gpu] {_ERR}", flush=True)
    try:                                   # dead CUDA context: stop using the GPU
        import native_libs as _nl          # (every later call would fail too)
        if _nl.is_sticky(_ERR):
            global _AVAIL
            _AVAIL = False
            _ERR = ("GPU switched off until SSA restarts (the CUDA context is lost): " + _ERR)
            print(f"[noise_gpu] {_ERR}", flush=True)
    except Exception:
        pass


def available():
    _load()
    return bool(_AVAIL)


def last_error():
    return _ERR


def supported_version(mc):
    return int(mc) >= _MC_MIN


def get_tables(mc):
    """Returns a dict with the flat spline + biome-tree pointers for `mc`, cached.
    None if unsupported (< 1.18) or extraction failed. The btree pointers reference
    static tables inside libcubiomes (valid for the process)."""
    _load()
    if not _AVAIL or not supported_version(mc):
        return None
    if mc in _TBL_CACHE:
        return _TBL_CACHE[mc]
    spline = (SplineFlatG * _SP_CAP)()
    spn = ctypes.c_int(0)
    steps = ctypes.POINTER(ctypes.c_uint32)(); steps_n = ctypes.c_int(0)
    param = ctypes.POINTER(ctypes.c_int32)();  param_n = ctypes.c_int(0)
    nodes = ctypes.POINTER(ctypes.c_uint64)(); nodes_n = ctypes.c_int(0)
    order = ctypes.c_uint(0)
    ok = _CUB.noise_gpu_get_tables(
        int(mc), spline, ctypes.byref(spn),
        ctypes.byref(steps), ctypes.byref(steps_n),
        ctypes.byref(param), ctypes.byref(param_n),
        ctypes.byref(nodes), ctypes.byref(nodes_n),
        ctypes.byref(order))
    if not ok:
        return None
    res = dict(spline=spline, spline_n=spn.value,
               steps=steps, steps_n=steps_n.value,
               param=param, param_n=param_n.value,
               nodes=nodes, nodes_n=nodes_n.value, order=order.value)
    _TBL_CACHE[mc] = res
    return res


def biomescan(mc, seeds, xs, zs, allowed_words, y=15):
    """GPU FP32 BiomeScan count per seed (recall-safe prefilter).

    mc: 1.18+ (>= 22). seeds: array-like int64. xs, zs: sample-point arrays (block
    coords, scale-4). allowed_words: 4 uint64 (256-bit biome bitmask; bit b set =>
    biome id b allowed). y: scale-4 y (15 == y60, cubiomes default).

    Returns int32 array [n] of FP32 match counts (compare to min_count-1 to keep
    candidates, then CPU-validate in FP64). None on failure / unsupported version."""
    _load()
    if not _AVAIL or not supported_version(mc):
        return None
    t = get_tables(mc)
    if t is None:
        return None
    seeds = np.ascontiguousarray(seeds, dtype=np.uint64)
    n = int(len(seeds))
    if n == 0:
        return np.zeros(0, np.int32)
    xs = np.ascontiguousarray(xs, dtype=np.int32)
    zs = np.ascontiguousarray(zs, dtype=np.int32)
    nsamp = int(len(xs))
    allowed = np.ascontiguousarray(allowed_words, dtype=np.uint64)
    if len(allowed) != 4:
        raise ValueError("allowed_words must have 4 uint64")
    out = np.zeros(n, dtype=np.int32)
    rc = _LIB.noise_gpu_biomescan(
        int(mc),
        t["spline"], int(t["spline_n"]),
        t["steps"], int(t["steps_n"]),
        t["param"], int(t["param_n"]),
        t["nodes"], int(t["nodes_n"]),
        ctypes.c_uint(int(t["order"])),
        xs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        zs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        nsamp, int(y),
        allowed.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
        seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)), n,
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
    if rc != 0:
        _note_err(rc)
        return None
    return out


def has_biomemc():
    """True if the loaded library has the biome_sample Monte-Carlo kernel."""
    _load()
    return bool(_AVAIL) and hasattr(_LIB, "noise_gpu_biomemc")


def biomemc(mc, seeds, plan, allowed_words):
    """biome_sample pre-filter on the GPU: walks, for every seed, the SAME sample
    cells as cubiomes' monteCarloBiomes (plan from custom_search.monte_prefix_plan)
    and returns an int32 array, 1 = keep (the C engine decides), 0 = the C engine
    rejects it. None on failure / old library."""
    _load()
    if not _AVAIL or not supported_version(mc) or not hasattr(_LIB, "noise_gpu_biomemc"):
        return None
    t = get_tables(mc)
    if t is None:
        return None
    seeds = np.ascontiguousarray(seeds, dtype=np.uint64)
    n = int(len(seeds))
    if n == 0:
        return np.zeros(0, np.int32)
    allowed = np.ascontiguousarray(allowed_words, dtype=np.uint64)
    out = np.zeros(n, dtype=np.int32)
    rc = _LIB.noise_gpu_biomemc(
        int(mc),
        t["spline"], int(t["spline_n"]),
        t["steps"], int(t["steps_n"]),
        t["param"], int(t["param_n"]),
        t["nodes"], int(t["nodes_n"]),
        ctypes.c_uint(int(t["order"])),
        int(plan["rx"]), int(plan["rz"]), int(plan["sx"]), int(plan["sz"]), int(plan["y"]),
        int(plan["rmax_sq"]), int(plan["m0"]), int(plan["max_iter"]),
        allowed.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
        seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)), n,
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
    if rc != 0:
        _note_err(rc)
        return None
    return out


def biomemap(mc, seed, xs, zs, y=15):
    """GPU biome ID at each (xs[i],zs[i]) cell for ONE seed — instant map tiles.

    mc: 1.18+ (>= 22). seed: int. xs, zs: scale-4 cell coords (world>>2). y: scale-4
    y (15 == y60, cubiomes default). Returns an int32 array [ncells] of biome IDs,
    or None on failure / unsupported version (caller falls back to the CPU)."""
    _load()
    if not _AVAIL or not supported_version(mc):
        return None
    t = get_tables(mc)
    if t is None:
        return None
    xs = np.ascontiguousarray(xs, dtype=np.int32)
    zs = np.ascontiguousarray(zs, dtype=np.int32)
    ncells = int(len(xs))
    if ncells == 0:
        return np.zeros(0, np.int32)
    out = np.empty(ncells, dtype=np.int32)
    rc = _LIB.noise_gpu_biomemap(
        int(mc),
        t["spline"], int(t["spline_n"]),
        t["steps"], int(t["steps_n"]),
        t["param"], int(t["param_n"]),
        t["nodes"], int(t["nodes_n"]),
        ctypes.c_uint(int(t["order"])),
        xs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        zs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        ncells, int(y),
        ctypes.c_uint64(int(seed) & 0xFFFFFFFFFFFFFFFF),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
    if rc != 0:
        _note_err(rc)
        return None
    return out


def has_climate_nmax():
    """True if the loaded library computes limited octaves ("up to octave")."""
    _load()
    return bool(_AVAIL) and hasattr(_LIB, "noise_gpu_climatescan2")


def climatescan(mc, seeds, xs, zs, para, lo10k=None, hi10k=None, nmax=-1):
    """GPU per-seed min, max AND count-in-range of one climate parameter over the
    grid (1.18+, raw position, no shift — matches cubiomes sampleClimatePara).

    mc: >= 22. para: NP index (0=temp 1=humid 2=cont 3=eros 5=weird). lo10k/hi10k:
    range bounds scaled x10000 for the count (None = ±2^31, count then meaningless).
    Returns (mins, maxs, counts) int32 arrays: min & max value (x10000) and the
    number of samples in [lo10k, hi10k]. None on failure / unsupported version."""
    _load()
    if not _AVAIL or not supported_version(mc):
        return None
    seeds = np.ascontiguousarray(seeds, dtype=np.uint64)
    n = int(len(seeds))
    if n == 0:
        z = np.zeros(0, np.int32); return (z, z.copy(), z.copy())
    xs = np.ascontiguousarray(xs, dtype=np.int32)
    zs = np.ascontiguousarray(zs, dtype=np.int32)
    nsamp = int(len(xs))
    LO = int(lo10k) if lo10k is not None else -2147483648
    HI = int(hi10k) if hi10k is not None else  2147483647
    omin = np.zeros(n, dtype=np.int32)
    omax = np.zeros(n, dtype=np.int32)
    ocount = np.zeros(n, dtype=np.int32)
    nmax = int(nmax) if nmax is not None else -1
    if nmax > 0:                       # limited octaves: needs the newer library
        if not hasattr(_LIB, "noise_gpu_climatescan2"):
            return None
        fn, head = _LIB.noise_gpu_climatescan2, (int(mc), int(para), nmax, LO, HI)
    else:
        fn, head = _LIB.noise_gpu_climatescan, (int(mc), int(para), LO, HI)
    rc = fn(
        *head,
        xs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        zs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        nsamp,
        seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)), n,
        omin.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        omax.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        ocount.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
    if rc != 0:
        _note_err(rc)
        return None
    return omin, omax, ocount


def has_climatefilter():
    """True if the loaded library has the fused climate filter (seeds made on
    the GPU, only the survivors come back)."""
    _load()
    return bool(_AVAIL) and hasattr(_LIB, "noise_gpu_climatefilter")


_CF_CAP = 1 << 22


def climatefilter(mc, seed0, n, xs, zs, para, nmax, mode, A, B):
    """Fused climate pre-filter over seeds seed0 .. seed0+n-1 (64-bit wrap).
    mode 0 keeps max >= A and min <= B; mode 1 keeps min <= A or max >= B
    (values x10000 over the xs/zs cells, FP32). Returns an int64 array of the
    survivors (sorted), None on failure (-4: parameter not supported → None)."""
    _load()
    if not _AVAIL or not supported_version(mc) or not hasattr(_LIB, "noise_gpu_climatefilter"):
        return None
    n = int(n)
    if n <= 0:
        return np.zeros(0, np.int64)
    xs = np.ascontiguousarray(xs, dtype=np.int32)
    zs = np.ascontiguousarray(zs, dtype=np.int32)
    cap = min(n, _CF_CAP)
    out = np.empty(cap, dtype=np.uint64)
    tot = ctypes.c_longlong(0)
    s0 = int(seed0) & 0xFFFFFFFFFFFFFFFF
    rc = _LIB.noise_gpu_climatefilter(
        int(mc), int(para), int(nmax if nmax is not None else -1),
        xs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        zs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), int(len(xs)),
        ctypes.c_uint64(s0), ctypes.c_longlong(n), int(mode),
        ctypes.c_longlong(int(A)), ctypes.c_longlong(int(B)),
        out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)), cap, ctypes.byref(tot))
    if rc != 0:
        if rc != -4:
            _note_err(rc)
        return None
    if tot.value > cap:                     # too many survivors: two halves
        h = n // 2
        a = climatefilter(mc, seed0, h, xs, zs, para, nmax, mode, A, B)
        b = climatefilter(mc, s0 + h, n - h, xs, zs, para, nmax, mode, A, B)
        if a is None or b is None:
            return None
        return np.concatenate([a, b])
    r = out[:tot.value].view(np.int64).copy()
    r.sort()
    return r


def allowed_bitmask(biome_ids):
    """Pack biome ids (0..255) into the 4-word 256-bit mask the kernel expects."""
    words = [0, 0, 0, 0]
    for b in biome_ids:
        b = int(b)
        if 0 <= b < 256:
            words[(b >> 6) & 3] |= (1 << (b & 63))
    return np.array(words, dtype=np.uint64)


if __name__ == "__main__":
    # self-test on a CUDA machine: GPU FP32 count vs cubiomes FP64 count.
    if not available():
        print("noise_gpu NOT available:", last_error())
        raise SystemExit(1)
    import cubiomes_wrap as cw
    lib = cw.make_worker_lib()
    mc = 25  # 1.20
    N = 9
    # sample on a SCALE-4 grid (the kernel works in scale-4 coords, like getBiomeAt)
    base = [-750 + 1500 * i // (N - 1) for i in range(N)]
    xs = np.array([i for i in base for _ in base], np.int32)
    zs = np.array([j for _ in base for j in base], np.int32)
    allow = [4, 27, 29, 5, 30, 21, 1, 3]
    mask = allowed_bitmask(allow)
    aset = set(allow)
    seeds = np.arange(1, 2001, dtype=np.uint64)
    counts = biomescan(mc, seeds, xs, zs, mask, y=15)
    if counts is None:
        print("biomescan returned None:", last_error()); raise SystemExit(1)
    worst = 0
    # CPU ref: kernel(X,15,Z) == getBiomeAt(4,X,15,Z); get_biome_at(wx,wz,scale=4,y)
    # internally divides by scale, so feed world coords X*4 and y=60 (60//4 == 15).
    for si, s in enumerate(seeds.tolist()[:300]):   # spot-check first 300 vs cubiomes
        lib.setup_and_seed(mc, int(s))
        cpu = sum(1 for k in range(len(xs))
                  if lib.get_biome_at(int(xs[k]) * 4, int(zs[k]) * 4, scale=4, y=60) in aset)
        worst = max(worst, abs(int(counts[si]) - cpu))
    print(f"GPU vs cubiomes (300 seeds): max |count diff| = {worst} (recall-safe if <= 1)")
    # throughput
    M = 200000
    bs = np.arange(1_000_000, 1_000_000 + M, dtype=np.uint64)
    t = time.perf_counter()
    biomescan(mc, bs, xs, zs, mask, y=15)
    dt = time.perf_counter() - t
    print(f"throughput (1.20, {len(xs)} samples/seed): {M} seeds in {dt:.1f}s => {M/dt:,.0f} seeds/s")

    # ── climate min/max self-test (continentalness over a 1024-block box) ──
    print("\n[climate]")
    para = 2  # continentalness
    bx1, bz1, bx2, bz2 = -128, -128, 128, 128   # -> 64x64 scale-4 cells = 4096 samples
    rx1, rz1 = bx1 >> 2, bz1 >> 2
    rw, rh = max(1, (bx2 - bx1) >> 2), max(1, (bz2 - bz1) >> 2)
    cxs = np.array([gx for gx in range(rx1, rx1 + rw) for _ in range(rh)], np.int32)
    czs = np.array([gz for _ in range(rw) for gz in range(rz1, rz1 + rh)], np.int32)
    cseeds = np.arange(1, 51, dtype=np.uint64)
    cres = climatescan(mc, cseeds, cxs, czs, para)
    if cres is None:
        print("climatescan returned None:", last_error())
    else:
        cmin, cmax, _ccnt = cres
        worst = 0.0
        for si, s in enumerate(cseeds.tolist()):
            lib.setup_and_seed(mc, int(s))
            _, _, _, vmax = lib.climate_minmax(para, bx1, bz1, bx2, bz2, mode=2 | 4, vmin=-1e9, vmax=float("inf"))
            _, _, _, vmin = lib.climate_minmax(para, bx1, bz1, bx2, bz2, mode=1 | 8, vmin=float("-inf"), vmax=1e9)
            # cubiomes returns the value already scaled x10000 (same domain as the GPU output)
            worst = max(worst, abs(cmax[si] - vmax), abs(cmin[si] - vmin))
        print(f"GPU vs cubiomes climate min/max (50 seeds, cont, {len(cxs)} samples, x10000 domain): "
              f"worst err = {worst:.4g} (recall-safe if <= a few)")
    M2 = 20000
    cbs = np.arange(1_000_000, 1_000_000 + M2, dtype=np.uint64)
    t = time.perf_counter()
    climatescan(mc, cbs, cxs, czs, para)
    dt = time.perf_counter() - t
    print(f"climate throughput ({len(cxs)} samples/seed): {M2} seeds in {dt:.1f}s => {M2/dt:,.0f} seeds/s")

    # ── multi-seed kernel DIAGNOSTIC (finds the recall bug) ──────────────────
    # Runs BOTH the single-seed and multi-seed kernels and reports where they
    # disagree (+ position within the block). 0 mismatches = multi-kernel correct.
    print("\n[diag] multi-seed vs single-seed kernel (0 mismatch = correct)")
    if hasattr(_LIB, "noise_gpu_climate_diag"):
        pt = np.array([0], np.int32)   # single point (0,0)
        diag_cfgs = [
            ("weirdness, single point (0,0)", 5, pt, pt),
            ("weirdness, 64x64 area",         5, cxs, czs),
            ("continentalness, 64x64 area",   2, cxs, czs),
        ]
        for label, dpara, dxs, dzs in diag_cfgs:
            dn = 8192
            dseeds = np.arange(1, 1 + dn, dtype=np.uint64)
            print(f"  -- {label} --")
            _LIB.noise_gpu_climate_diag(
                mc, dpara,
                dxs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
                dzs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
                len(dxs),
                dseeds.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
                dn)
    else:
        print("  noise_gpu_climate_diag unavailable — rebuild noise_gpu.dll")
