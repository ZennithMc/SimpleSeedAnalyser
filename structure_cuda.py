"""
structure_cuda.py — GPU (CUDA) acceleration of the 48-bit structure filter.

The GPU computes ONLY structure positions (48-bit RNG, massively parallel) and
returns seeds whose structures all fall inside the requested area. The CPU then
verifies biomes and the other conditions on the survivors (mixed sessions work).
Equivalent to cubiomes' 48-bit pass. Prebuilt DLL (tools/build_native.py, RTX 20-50),
self-tested at load; falls back to CPU if the GPU is unusable.
"""
from __future__ import annotations
import ctypes, os, platform, subprocess

_CUDA_LIB = None
_CUDA_AVAILABLE = None
_CUDA_LOAD_ERROR = None


class GpuStructJob(ctypes.Structure):
    _fields_ = [
        ("algo_kind", ctypes.c_int32), ("salt", ctypes.c_int32),
        ("region_size", ctypes.c_int32), ("chunk_range", ctypes.c_int32),
        ("rx1", ctypes.c_int32), ("rz1", ctypes.c_int32),
        ("rx2", ctypes.c_int32), ("rz2", ctypes.c_int32),
        ("x1", ctypes.c_int32), ("z1", ctypes.c_int32),
        ("x2", ctypes.c_int32), ("z2", ctypes.c_int32),
        ("rmax_sq", ctypes.c_int64),
        ("cx", ctypes.c_int32), ("cz", ctypes.c_int32),
        ("min_count", ctypes.c_int32), ("exclude", ctypes.c_int32),
        ("_pad", ctypes.c_int32),
        # (10/10) 1.16 dungeons / lava lakes (structure_cuda.cu ALGO_*116)
        ("ylo", ctypes.c_int32), ("yhi", ctypes.c_int32),
        ("variant", ctypes.c_int32), ("skip_ref", ctypes.c_int32),
    ]


ALGO_DUNGEON116, ALGO_LAKE116 = 4, 5


def _feature116_job(c, mc):
    """GpuStructJob of a top-level dungeon / lava lake condition (48 bits, the
    two feature indices: what ssa_test_seed_pos_only tests), or None."""
    if int(mc) not in (19, 20):
        return None
    from custom_search import feature116_yrange
    t = c.get("type")
    p = c.get("params", {}) or {}
    need = c.get("instances", 1)
    try:
        need = int(need)
    except (TypeError, ValueError):
        need = 1
    if need <= 0:
        return None                       # exclusions: CPU only (as for structures)
    rmax = int(c.get("rmax", 0) or 0)
    if not rmax and c.get("area_mode") == 0 and int(c.get("radial", 0) or 0) > 0:
        rmax = int(c["radial"]) + 1
    if rmax > 0:
        rb = rmax - 1
        bx1, bz1, bx2, bz2, rsq = -rb, -rb, rb, rb, rb * rb + 1
    else:
        bx1, bz1, bx2, bz2 = int(c.get("x1", 0)), int(c.get("z1", 0)), int(c.get("x2", 0)), int(c.get("z2", 0))
        if bx1 > bx2: bx1, bx2 = bx2, bx1
        if bz1 > bz2: bz1, bz2 = bz2, bz1
        rsq = 0
    lake = t == "lava_lake"
    if not lake:
        # (10/10) no GPU pre-filter for dungeons: a dungeon that generates
        # shifts the later attempts of its chunk, the 48-bit attempts are not a
        # superset of the real dungeons (only of the first one per chunk)
        return None
    lo, hi = feature116_yrange(p)
    var = {"underground": 5, "surface": 6}.get(p.get("variant"), 0) if lake else 0
    return GpuStructJob(algo_kind=ALGO_LAKE116 if lake else ALGO_DUNGEON116, salt=0, region_size=1,
                        chunk_range=0,
                        rx1=((bx1 - 23) if lake else bx1) >> 4, rz1=((bz1 - 23) if lake else bz1) >> 4,
                        rx2=bx2 >> 4, rz2=bz2 >> 4, x1=bx1, z1=bz1, x2=bx2, z2=bz2,
                        rmax_sq=rsq, cx=0, cz=0, min_count=need, exclude=0, _pad=0,
                        ylo=lo if lo > 0 else -1000000, yhi=hi if hi < 255 else 1000000,
                        variant=var, skip_ref=1 if c.get("ignore_ref") else 0)


def _here(): return os.path.dirname(os.path.abspath(__file__))
def _lib_name(): return "structure_cuda.dll" if platform.system()=="Windows" else "libstructure_cuda.so"


def _load():
    """Charge la DLL prébuilt (tools/build_native.py) via native_libs : auto-test
    GPU obligatoire, jamais de compilation chez l'utilisateur, jamais de suppression."""
    global _CUDA_LIB,_CUDA_AVAILABLE,_CUDA_LOAD_ERROR
    if _CUDA_AVAILABLE is not None: return
    import native_libs as _nl
    lib, msg = _nl.load_cuda("structure_cuda")
    if lib is None:
        _CUDA_LOAD_ERROR=msg; _CUDA_AVAILABLE=False; return
    try:
        lib.structure_cuda_available.argtypes=[]; lib.structure_cuda_available.restype=ctypes.c_int
        lib.structure_cuda_filter.argtypes=[ctypes.c_int64,ctypes.c_int64,
            ctypes.POINTER(GpuStructJob),ctypes.c_int,
            ctypes.POINTER(ctypes.c_int64),ctypes.c_int,ctypes.c_int]
        lib.structure_cuda_filter.restype=ctypes.c_int64
        _CUDA_LIB=lib; _CUDA_AVAILABLE=True
    except Exception as e:
        _CUDA_LOAD_ERROR=f"structure_cuda : liaison impossible ({e})"; _CUDA_AVAILABLE=False


def is_available():
    _load(); return _CUDA_AVAILABLE is True

def get_load_error():
    _load(); return _CUDA_LOAD_ERROR


LAST_JOB_CONDS = []


def build_jobs(conditions, lib, mc):
    """Build GpuStructJob list for top-level GPU-supported structure conditions.
    Returns (jobs, all_structures_supported)."""
    raw = lib._lib if hasattr(lib,"_lib") else lib
    raw.ssa_get_struct_config.argtypes=[ctypes.c_int,ctypes.c_int,
        ctypes.POINTER(ctypes.c_int32),ctypes.POINTER(ctypes.c_int32),ctypes.POINTER(ctypes.c_int32)]
    raw.ssa_get_struct_config.restype=ctypes.c_int
    raw.ssa_struct_gpu_algo.argtypes=[ctypes.c_int]; raw.ssa_struct_gpu_algo.restype=ctypes.c_int
    from custom_search import STRUCT_TYPES
    import sys as _dbg
    jobs=[]; all_sup=True; n_struct=0
    global LAST_JOB_CONDS
    LAST_JOB_CONDS=[]                  # the condition of each job (statistics)
    for c in conditions:
        if not c.get("enabled",True): continue
        rel=c.get("relative",c.get("parent_idx",0)) or 0
        if rel!=0: continue
        t=c.get("type","")
        if t in ("dungeon","lava_lake"):          # (10/10) 1.16 features
            n_struct+=1
            fj=_feature116_job(c,mc)
            if fj is None: all_sup=False
            else: jobs.append(fj); LAST_JOB_CONDS.append(c)
            continue
        if t not in STRUCT_TYPES: continue
        n_struct+=1
        stype=STRUCT_TYPES[t]
        algo=raw.ssa_struct_gpu_algo(stype)
        __import__("native_libs").dbg(f"[GPU-DBG] build_jobs: type={t!r} stype={stype} algo={algo} use_gpu={c.get('params',{}).get('use_gpu')}")
        if algo<0: all_sup=False; continue
        salt=ctypes.c_int32(); rs=ctypes.c_int32(); cr=ctypes.c_int32()
        if not raw.ssa_get_struct_config(stype,mc,ctypes.byref(salt),ctypes.byref(rs),ctypes.byref(cr)):
            all_sup=False; continue
        params=c.get("params",{})
        # rmax is stored at the condition root (c["rmax"]), NOT inside params.
        # params.get("rmax") would always return 0 for imported session conditions.
        rmax=int(c.get("rmax",0) or 0)
        if not rmax and c.get("area_mode")==0 and int(c.get("radial",0) or 0)>0:
            rmax=int(c["radial"])+1
        x1=int(c.get("x1",0)); z1=int(c.get("z1",0)); x2=int(c.get("x2",0)); z2=int(c.get("z2",0))
        # top-level condition → its reference point is the world origin (0, 0).
        # BUG fixed 26/09: the circle used to be centred on (x1, z1) — i.e. on
        # (-r, -r) for a radial condition — so the GPU dropped most valid seeds
        # of small radii (mansion r=300: 19 found instead of 1725).
        if rmax>0:
            rb=rmax-1; bx1,bz1,bx2,bz2=-rb,-rb,rb,rb
            rmax_sq=rb*rb+1; cx,cz=0,0
        else:
            bx1,bz1,bx2,bz2=x1,z1,x2,z2; rmax_sq=0; cx,cz=0,0
            if bx1>bx2: bx1,bx2=bx2,bx1
            if bz1>bz2: bz1,bz2=bz2,bz1
        region_size=rs.value
        if region_size<=0: all_sup=False; continue
        if region_size==32:
            rx1,rz1,rx2_,rz2_=bx1>>9,bz1>>9,bx2>>9,bz2>>9
        elif region_size==1:
            rx1,rz1,rx2_,rz2_=bx1>>4,bz1>>4,bx2>>4,bz2>>4
        else:
            csz=region_size<<4
            def _rd(v): return (v//csz) if v>=0 else -((-v+csz-1)//csz)
            rx1,rz1,rx2_,rz2_=_rd(bx1),_rd(bz1),_rd(bx2),_rd(bz2)
        # min_count = nombre d'instances requises. L'UI le stocke à la RACINE de
        # la condition (c["instances"]), PAS dans params["count"] — qui n'existe
        # plus depuis le refactor instances. Lire params["count"] renvoyait
        # toujours 1, donc le GPU ne cherchait qu'UNE structure au lieu de N
        # (ex. « 4 manoirs » filtrait comme « 1 manoir » → 15% de survie au lieu
        # de ~1/300 milliards). On lit instances en priorité, avec repli.
        _min_count = c.get("instances", None)
        if _min_count is None:
            _min_count = params.get("count", 1)
        try:
            _min_count = int(_min_count)
        except (TypeError, ValueError):
            _min_count = 1
        # instances 0 = exclusion ("no such structure here"). The GPU only sees
        # CANDIDATE positions (no biome check): a candidate that would not
        # generate must not reject the seed, so an exclusion cannot be
        # pre-filtered safely → left to the CPU validation (before: it was
        # forced to "at least 1", i.e. the opposite condition).
        if _min_count <= 0:
            continue
        _exclude = 0
        jobs.append(GpuStructJob(algo_kind=algo,salt=salt.value,region_size=region_size,
            chunk_range=cr.value,rx1=rx1,rz1=rz1,rx2=rx2_,rz2=rz2_,x1=bx1,z1=bz1,x2=bx2,z2=bz2,
            rmax_sq=rmax_sq,cx=cx,cz=cz,min_count=_min_count,
            exclude=_exclude,_pad=0))
        LAST_JOB_CONDS.append(c)
    return jobs, (all_sup and n_struct>0)




_OUT_BUF = None
_OUT_BUF_CAP = 0

def filter_seeds(jobs, seed_start, n, fam48=False, out_cap=1<<20):
    """Run the GPU filter. Returns (seeds_list, total_found).
    Raises RuntimeError if the CUDA kernel reports an error (so the caller can
    fall back to CPU instead of looping forever with 0 results)."""
    global _OUT_BUF, _OUT_BUF_CAP
    if not is_available() or not jobs:
        return [], 0
    arr = (GpuStructJob * len(jobs))(*jobs)
    # Buffer de sortie reutilise entre batches (sinon 32 Mo alloues+zero a chaque
    # appel = des centaines de Mo/s de churn a ~150 batches/s).
    if _OUT_BUF is None or _OUT_BUF_CAP < out_cap:
        _OUT_BUF = (ctypes.c_int64 * out_cap)()
        _OUT_BUF_CAP = out_cap
    out = _OUT_BUF
    total = _CUDA_LIB.structure_cuda_filter(
        ctypes.c_int64(seed_start), ctypes.c_int64(n),
        arr, ctypes.c_int(len(jobs)), out, ctypes.c_int(out_cap),
        ctypes.c_int(1 if fam48 else 0))
    if total < 0:
        import native_libs as _nl
        global _CUDA_AVAILABLE, _CUDA_LOAD_ERROR
        err = _nl.last_error(_CUDA_LIB) or "erreur CUDA"
        if _nl.is_sticky(err):             # dead CUDA context: CPU from now on
            _CUDA_AVAILABLE = False
            _CUDA_LOAD_ERROR = "GPU switched off until SSA restarts (the CUDA context is lost): " + err
            print(f"[structure_cuda] {_CUDA_LOAD_ERROR}", flush=True)
        raise _nl.GpuError("filtre GPU des structures : " + err)
    ncopy = min(int(total), out_cap)
    return [int(out[i]) for i in range(ncopy)], int(total)
