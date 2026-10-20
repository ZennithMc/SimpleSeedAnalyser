"""(19/10) End-to-end check of the GPU spiral-scan pipeline WITHOUT a GPU: the
crunchiness kernel and the ring kernel are replaced by their CPU twins
(ssa_crunch_yfrac, the host build of ring_gpu_kernel.cuh). The seeds found must
be exactly those of the plain C engine.
    python tests/ring_pipeline_check.py SESSION N_SEEDS"""
import sys, os, threading, copy, time, ctypes as ct
import numpy as np
H = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(H))
import custom_search as cs, cubiomes_session as cses, ring_gpu as rg, gaider_cuda as gc
from cubiomes_wrap import CubiomesLib

path, N = sys.argv[1], int(sys.argv[2])
conds = cses.parse_session_file(path)["conditions"]
lib = CubiomesLib(); L = lib._lib
L.ssa_crunch_yfrac.restype = ct.c_double
L.ssa_crunch_yfrac.argtypes = [ct.c_int64, ct.c_int, ct.c_int, ct.c_int, ct.c_int]
cr = next(c for c in conds if c["type"] == "crunchiness")
clim = int(cr["params"]["climate"]); thr = float(cr["params"]["threshold"])

def fake_crunch(seed_start, n, **kw):
    out = []
    for s in range(seed_start, seed_start + n):
        sc = sum(0.25 * abs(L.ssa_crunch_yfrac(ct.c_int64(s), clim, o, b, 0) - 0.5)
                 for o in (0, 1) for b in (0, 1))
        if sc <= thr + 1e-5: out.append(s)
    return len(out), out
gc.is_available = lambda: True
gc.has_exact_crunchiness = lambda: True
gc.has_crunchiness_multi = lambda: False
gc.crunchiness_filter_w = lambda ss, n, max_results=0, **kw: fake_crunch(ss, n)
def fake_scan(spec, seeds, mc=35, cap=0):
    si, xz = [], []
    for i, s in enumerate(seeds):
        p = rg.host_scan(spec, int(s))
        si += [i] * len(p); xz.append(p)
    return np.array(si, np.int32), (np.concatenate(xz) if xz else np.zeros((0, 2), np.int32))
rg.available = lambda: True
rg.scan = fake_scan

def run(cc, gpu):
    cc = copy.deepcopy(cc)
    for c in cc:
        if c["type"] in ("crunchiness", "spiral"):
            c["params"]["use_gpu"] = gpu
    found, msgs = set(), []
    t0 = time.time()
    cs.evaluate_session(cc, 0, N, 35, 2, threading.Event(), lambda s, d: found.add(int(s)),
                        lambda d, t: None, search_mode="inc",
                        on_mode=lambda m: msgs.append(m))
    return found, time.time() - t0, [m for m in msgs if isinstance(m, tuple)]

a, ta, ma = run(conds, False)
b, tb, mb = run(conds, True)
print("CPU", len(a), "%.0fs" % ta, "| GPU path", len(b), "%.0fs" % tb, mb[:3])
print("identical" if a == b else f"DIFF only CPU {sorted(a - b)[:10]} only GPU {sorted(b - a)[:10]}")
