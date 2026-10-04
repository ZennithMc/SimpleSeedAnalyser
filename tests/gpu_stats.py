"""Statistics of the GPU pipelines (01/10): the validators count through
_tsb() / ssa_test_seeds_batch_prof, and the seeds the GPU pre-filter rejected
are credited to the pre-filtered condition. On a session whose root is that
condition, the counters (seeds reached, branch passed, checks, checks passed)
must equal the C engine's own statistics for the same seeds.
A CPU stand-in plays the GPU (the root condition alone on the C engine).
    python tests/gpu_stats.py
"""
import ctypes as ct, os, sys, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import custom_search as cs
from cubiomes_wrap import CubiomesLib
import diff_engine as d

MC = 25
raw = CubiomesLib()._lib


def main():
    clim = d.S("climate_minmax", para="Continentalness", range=(None, "-2000"), nmax=-1,
               locate=1, invert=False)
    clim.update(x1=0, z1=0, x2=0, z2=0)
    tree = cs.active_conditions([clim, d.S("village", rel=1, r=800),
                                 d.S("outpost", rel=2, r=400), d.S("logic_or", rel=1),
                                 d.S("monument", rel=4, r=1500), d.S("mansion", rel=4, r=3000)])
    start, N = 987654321, 40000
    # 1. the C engine's own statistics
    eng = {}
    cs.evaluate_session(tree, start, N, MC, 2, threading.Event(), lambda s, x="": None,
                        lambda a, b: None, on_profile=lambda rows, tot: eng.update(r=rows))
    # 2. the GPU pipeline path: stand-in GPU scan + validators through _tsb
    n = len(tree)
    buf = (ct.c_int64 * (5 * n + 1))()
    cs._GPU_PROF.update(buf=buf, n=n, pos=None, scanned=0, kept=0)
    cs._gpu_prof_target(tree, tree[0])
    a1, k1 = cs._build_ssacond_array([tree[0]], raw)
    af, kf = cs._build_ssacond_array(tree, raw)
    raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                                         ct.POINTER(ct.c_int64), ct.c_int64,
                                         ct.POINTER(ct.c_int64), ct.c_int64]
    raw.ssa_test_seeds_batch.restype = ct.c_int64

    def scan(ss, m):
        s = cs._seed_arange(ss, m); o = np.empty(m, np.int64)
        k = raw.ssa_test_seeds_batch(a1, k1, MC, 0, s.ctypes.data_as(ct.POINTER(ct.c_int64)), m,
                                     o.ctypes.data_as(ct.POINTER(ct.c_int64)), m)
        return o[:k]

    def val(s):
        for k in range(0, len(s), 4):        # tiny pieces, like the real validators
            _val(s[k:k + 4])

    def _val(s):
        s = np.ascontiguousarray(s, np.int64); o = np.empty(len(s), np.int64)
        cs._tsb(raw, af, ct.c_int(kf), ct.c_int(MC), ct.c_int(0),
                s.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(len(s)),
                o.ctypes.data_as(ct.POINTER(ct.c_int64)), ct.c_int64(len(s)))
    cs._hybrid_loop(scan, val, lambda: None, start, N, 2, threading.Event(), lambda a, b: None)
    rows, _tot = cs._gpu_prof_rows()
    cs._GPU_PROF.update(buf=None, n=0, pos=None)
    bad = 0
    for i, (e, g) in enumerate(zip(eng["r"], rows)):
        same = [e[j] == g[j] for j in (0, 1, 3, 4)]
        ok = all(same) and (g[2] > 0 or g[3] == 0)      # a checked condition has a time (02/10)
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'}  #{i + 1} {tree[i]['type']:15s} engine reached {e[0]:6d} "
              f"passed {e[1]:5d} checks {e[3]:6d}/{e[4]:6d}   GPU path {g[0]:6d} {g[1]:5d} "
              f"{g[3]:6d}/{g[4]:6d}  time {'yes' if g[2] else 'no'}")
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
