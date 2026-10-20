"""(19/10) Recall of the GPU spiral scan (ring_gpu, host build of the device code):
every position where the C engine passes the spiral's subtree must be kept.
    python tests/ring_gpu_check.py SESSION N_SEEDS [half] [step]"""
import sys, os, random, time, copy, ctypes as ct
import numpy as np
H = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(H))
import custom_search as cs, cubiomes_session as cses, ring_gpu as rg
from cubiomes_wrap import CubiomesLib

def main():
    path, n = sys.argv[1], int(sys.argv[2])
    conds = cses.parse_session_file(path)["conditions"]
    conds = [c for c in conds if c.get("type") != "crunchiness"]      # seeds: all
    for c in conds:
        if c.get("type") == "spiral" and not int(c.get("relative", 0) or 0):
            c.setdefault("params", {})["use_gpu"] = True
            if len(sys.argv) > 3:
                h = int(sys.argv[3]); c.update(x1=-h, z1=-h, x2=h, z2=h, square_side=2 * h)
            if len(sys.argv) > 4:
                c["params"]["step"] = int(sys.argv[4])
    act = cs.active_conditions(copy.deepcopy(conds))
    spec, node, notes = rg.compile_spec(act, 35)
    print("node", node, "pt", spec.npt, "ors", spec.nor, [(spec.ors[i].kind, spec.ors[i].k, spec.ors[i].nbox, spec.ors[i].nbb) for i in range(spec.nor)], notes)
    lib = CubiomesLib(); raw = lib._lib
    arr, nc = cs._build_ssacond_array(conds, raw)
    f = raw.ssa_test_seeds_cand
    f.restype = ct.c_int64
    f.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.POINTER(ct.c_int64), ct.c_int64,
                  ct.POINTER(ct.c_int64), ct.POINTER(ct.c_int32), ct.c_int,
                  ct.POINTER(ct.c_int64), ct.c_int64, ct.c_void_p, ct.c_void_p]
    sp = act[node - 1]
    h1, step = sp["x1"], int(sp["params"]["step"]); h2 = sp["x2"]
    grid = [(x, z) for z in range((h1 // step) * step, h2 + 1, step) for x in range((h1 // step) * step, h2 + 1, step)
            if h1 <= x <= h2 and h1 <= z <= h2]
    rng = random.Random(11)
    tot_pos = tot_cpu = tot_gpu = miss = 0; t_gpu = t_cpu = 0.0; seeds_cpu = 0
    off = np.array([0, 1], np.int64); out = np.zeros(1, np.int64)
    for _ in range(n):
        sd = rng.getrandbits(63)
        t0 = time.time(); G = {tuple(p) for p in rg.host_scan(spec, sd)}; t_gpu += time.time() - t0
        t0 = time.time(); C = set()
        s64 = np.array([sd], np.int64)
        for (x, z) in grid:
            xz = np.array([x, z], np.int32)
            k = f(arr, nc, 35, 0, s64.ctypes.data_as(ct.POINTER(ct.c_int64)), 1,
                  off.ctypes.data_as(ct.POINTER(ct.c_int64)), xz.ctypes.data_as(ct.POINTER(ct.c_int32)),
                  node, out.ctypes.data_as(ct.POINTER(ct.c_int64)), 1, None, None)
            if k: C.add((x, z))
        t_cpu += time.time() - t0
        tot_pos += len(grid); tot_cpu += len(C); tot_gpu += len(G); seeds_cpu += bool(C)
        m = C - G
        miss += len(m)
        if m: print("MISS", sd, sorted(m)[:5])
    print(f"positions {tot_pos}  CPU pass {tot_cpu} (seeds {seeds_cpu})  GPU kept {tot_gpu} "
          f"({tot_gpu / max(1, tot_pos):.4%})  missed {miss}   host-scan {t_gpu:.1f}s  cpu {t_cpu:.1f}s")

main()
