"""
gaider_layouts.py — Layout-based seed search (Gaider10 style).

Algorithme (très efficient pour 12-eye et fortes valeurs) :

1. CPU multi-thread: pour chaque structure_seed (les 48 bits bas du
   world_seed), exécute la simulation complète des pre-pieces du
   stronghold pour calculer le portal_chunk EXACT. Pousse un Layout :
     {struct_seed, start_chunk, portal_chunk}

2. GPU: pour chaque Layout, teste les 65536 variations du high16 du
   world_seed (les 16 bits hauts du seed). Pour chaque test, applique
   la matrice Gaider à la position EXACTE du portail (1 test au lieu
   de 225 dans la grille).

3. CPU: chaque match est validé via le pipeline complet (cubiomes biome
   filter + count_stronghold_eyes) pour zéro faux positif.

Avantages vs grille 15×15 :
  - 225× moins de tests Gaider par world_seed
  - Connaît le portal_chunk EXACT, pas d'approximation
  - Permet d'utiliser une LUT pré-calculée (future opt)
"""
from __future__ import annotations
import ctypes
import os
import platform
import subprocess
import threading

# ─────────────────────────────────────────────────────────────────────
# Layout struct (must match C++)
# ─────────────────────────────────────────────────────────────────────
class Layout(ctypes.Structure):
    _fields_ = [
        ("structure_seed", ctypes.c_uint64),
        ("start_chunk_x", ctypes.c_int16),
        ("portal_chunk_x", ctypes.c_int16),
        ("start_chunk_z", ctypes.c_int16),
        ("portal_chunk_z", ctypes.c_int16),
    ]


# ─────────────────────────────────────────────────────────────────────
# Compilation + loading
# ─────────────────────────────────────────────────────────────────────
_LIB = None
_LIB_LOAD_ERROR = None
_HAS_V = False  # True si la DLL a generate_layouts_v (pre118)
_HAS_MAXY = False  # True si la DLL a max_y_batch
_HAS_FRAMES = False  # True si la DLL a spg_frames_xz (frames X,Z natives)
_HAS_FIRSTPOS = False  # True si la DLL a spg_first_pos (getFirstPosFast, 48-bit)


def _here() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _lib_name() -> str:
    return "gaider_layouts.dll" if platform.system() == "Windows" else "libgaider_layouts.so"


def _compile() -> str | None:
    """Compile the layout generator C++ lib via g++.
    Returns path or None on failure."""
    global _LIB_LOAD_ERROR
    here = _here()
    src_dir = os.path.join(here, "cubiomes_src")
    out = os.path.join(here, _lib_name())

    cpp_files = [
        os.path.join(src_dir, "gaider10_lib.cpp"),
    ]
    sg_dir = os.path.join(src_dir, "stronghold_generator")
    if getattr(__import__("sys"), "frozen", False) or not os.path.isdir(sg_dir):
        _LIB_LOAD_ERROR = f"{_lib_name()} is missing (reinstall SimpleSeedAnalyser)"
        return None
    for f in os.listdir(sg_dir):
        if f.endswith(".cpp"):
            cpp_files.append(os.path.join(sg_dir, f))

    cmd = ["g++", "-std=c++20", "-O3", "-shared", "-fPIC",
           f"-I{src_dir}",
           "-o", out] + cpp_files

    if platform.system() == "Windows":
        cmd.append("-Wl,--export-all-symbols")

    print("Compiling Gaider10 layout generator (one time, ~10s)…")
    try:
        ret = subprocess.run(cmd, capture_output=True, timeout=180)
    except FileNotFoundError:
        _LIB_LOAD_ERROR = "g++ not found. Install MinGW (w64devkit) or g++."
        return None
    except subprocess.TimeoutExpired:
        _LIB_LOAD_ERROR = "g++ compilation timed out."
        return None

    if ret.returncode != 0:
        err = ret.stderr.decode(errors="replace") if ret.stderr else "(no stderr)"
        _LIB_LOAD_ERROR = f"g++ failed: {err[:500]}"
        return None

    print(f"OK: {out}")
    return out


def _load() -> ctypes.CDLL | None:
    global _LIB, _LIB_LOAD_ERROR
    if _LIB is not None:
        return _LIB

    path = os.path.join(_here(), _lib_name())
    if not os.path.exists(path):
        path = _compile()
        if path is None:
            return None

    try:
        lib = ctypes.CDLL(path)
        lib.generate_layouts.argtypes = [
            ctypes.c_uint64, ctypes.c_uint64, ctypes.c_int,
            ctypes.POINTER(Layout), ctypes.c_uint32,
        ]
        lib.generate_layouts.restype = ctypes.c_uint32
        global _HAS_V
        try:
            lib.generate_layouts_v.argtypes = [
                ctypes.c_uint64, ctypes.c_uint64, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(Layout), ctypes.c_uint32,
            ]
            lib.generate_layouts_v.restype = ctypes.c_uint32
            _HAS_V = True
        except AttributeError:
            _HAS_V = False
        global _HAS_MAXY
        try:
            lib.gaider_maxy_inflate.restype = ctypes.c_int
            lib.gaider_maxy_inflate()  # AttributeError si vieille DLL sans inflate
            lib.max_y_batch.argtypes = [
                ctypes.c_int64, ctypes.c_int32, ctypes.POINTER(ctypes.c_int32),
                ctypes.POINTER(ctypes.c_int32), ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int32)]
            lib.max_y_batch.restype = ctypes.c_uint32
            _HAS_MAXY = True
        except AttributeError:
            _HAS_MAXY = False
        global _HAS_FRAMES
        try:
            lib.spg_frames_xz.argtypes = [ctypes.c_uint64, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            lib.spg_frames_xz.restype = ctypes.c_int
            _HAS_FRAMES = True
        except AttributeError:
            _HAS_FRAMES = False
        global _HAS_FIRSTPOS
        try:
            lib.spg_first_pos.argtypes = [ctypes.c_uint64, ctypes.c_int,
                ctypes.POINTER(ctypes.c_int)]
            lib.spg_first_pos.restype = None
            _HAS_FIRSTPOS = True
        except AttributeError:
            _HAS_FIRSTPOS = False
        _LIB = lib
        return lib
    except Exception as e:
        _LIB_LOAD_ERROR = f"Failed to load: {e}"
        return None


def is_available() -> bool:
    return _load() is not None


def get_load_error() -> str | None:
    return _LIB_LOAD_ERROR


def has_pre118() -> bool:
    """True si la DLL gaider_layouts supporte le pre118 (generate_layouts_v)."""
    _load()
    return _HAS_V


def has_max_y() -> bool:
    """True si la DLL gaider_layouts a max_y_batch (Max Y natif)."""
    _load()
    return _HAS_MAXY


def has_frames() -> bool:
    """True if the gaider_layouts DLL has spg_frames_xz (native frame X,Z)."""
    _load()
    return _HAS_FRAMES


def first_pos(seed, superflat=False):
    """getFirstPosFast — the biome-FREE 1st-stronghold start chunk (cx, cz), the
    ring estimate the eye finder uses. None if the native fn is missing."""
    lib = _load()
    if lib is None or not _HAS_FIRSTPOS:
        return None
    out = (ctypes.c_int * 2)()
    lib.spg_first_pos(ctypes.c_uint64(seed & ((1 << 64) - 1)),
                      1 if superflat else 0, out)
    return (out[0], out[1])


def frames_xz(seed, cx, cz, pre118=True):
    """Native end-portal frame X,Z (fast). Returns (center_x, center_z,
    [(x,z) * 12 frame columns]) or None. X,Z are exact; Y is NOT included
    (the moveBelowSeaLevel Y has a known offset in C++; Python/checker do Y)."""
    lib = _load()
    if lib is None or not _HAS_FRAMES:
        return None
    out = (ctypes.c_int * 27)()
    if not lib.spg_frames_xz(ctypes.c_uint64(seed & ((1 << 64) - 1)),
                             ctypes.c_int(cx), ctypes.c_int(cz),
                             ctypes.c_int(1 if pre118 else 0), out):
        return None
    frames = [(out[3 + 2 * k], out[4 + 2 * k]) for k in range(12)]
    return (out[1], out[2], frames)


def max_y_batch(seed_start, n, cxs, czs, pre118=True, sea=63, margin=10,
                minY=0, inflate=0, threshold=53):
    """Retourne (seeds, tops) des strongholds dont le sommet Y >= threshold.
    cxs/czs : numpy int32 (positions chunk, taille n). Tout le BFS est natif."""
    lib = _load()
    if lib is None or not _HAS_MAXY:
        return [], []
    import numpy as np
    out_seeds = np.zeros(n, dtype=np.int64)
    out_y = np.zeros(n, dtype=np.int32)
    cnt = lib.max_y_batch(
        ctypes.c_int64(seed_start), ctypes.c_int32(n),
        cxs.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        czs.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        ctypes.c_int(1 if pre118 else 0), ctypes.c_int(sea),
        ctypes.c_int(margin), ctypes.c_int(minY), ctypes.c_int(inflate),
        ctypes.c_int(threshold),
        out_seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        out_y.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)))
    return out_seeds[:cnt].tolist(), out_y[:cnt].tolist()


# ─────────────────────────────────────────────────────────────────────
# Layout generation (multi-threaded CPU)
# ─────────────────────────────────────────────────────────────────────
def generate_layouts(struct_seed_start: int, n: int,
                       superflat: bool = False,
                       max_layouts: int | None = None,
                       pre118: bool = False):
    """
    Génère les Layouts pour les structure seeds [start, start+n).
    Renvoie un buffer ctypes.Array of Layout et le count.
    """
    lib = _load()
    if lib is None:
        return None, 0
    # En pratique 0.7 layouts par struct seed, on alloue large.
    if max_layouts is None:
        max_layouts = max(int(n * 2), 1024)

    buf = (Layout * max_layouts)()
    if pre118 and _HAS_V:
        count = lib.generate_layouts_v(
            ctypes.c_uint64(struct_seed_start & ((1 << 64) - 1)),
            ctypes.c_uint64((struct_seed_start + n) & ((1 << 64) - 1)),
            1 if superflat else 0, 1,
            buf, ctypes.c_uint32(max_layouts))
    else:
        count = lib.generate_layouts(
            ctypes.c_uint64(struct_seed_start & ((1 << 64) - 1)),
            ctypes.c_uint64((struct_seed_start + n) & ((1 << 64) - 1)),
            1 if superflat else 0,
            buf, ctypes.c_uint32(max_layouts))
    return buf, count


def generate_layouts_parallel(struct_seed_start: int, n: int,
                                num_workers: int,
                                superflat: bool = False,
                                pre118: bool = False):
    """
    Multi-threaded generation by splitting [start, start+n) across
    `num_workers` threads. Returns a single concatenated list of Layouts.
    Note: g++ uses thread-safe StrongholdGenerator; multiple workers can
    each have their own buffer.
    """
    if num_workers <= 1:
        buf, count = generate_layouts(struct_seed_start, n, superflat, pre118=pre118)
        return buf, count

    chunk = (n + num_workers - 1) // num_workers
    results = [None] * num_workers

    def worker(i):
        start_i = struct_seed_start + i * chunk
        n_i = min(chunk, n - i * chunk)
        if n_i <= 0:
            results[i] = (None, 0)
            return
        results[i] = generate_layouts(start_i, n_i, superflat, pre118=pre118)

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(num_workers)]
    for t in threads: t.start()
    for t in threads: t.join()

    # Concatenate
    total = sum(c for _, c in results if c)
    if total == 0:
        return None, 0
    out = (Layout * total)()
    pos = 0
    for buf, c in results:
        if buf is None or c == 0:
            continue
        ctypes.memmove(ctypes.byref(out, pos * ctypes.sizeof(Layout)),
                        buf, c * ctypes.sizeof(Layout))
        pos += c
    return out, total
