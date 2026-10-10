"""The GPU filter of the 1.16 dungeons / lava lakes (structure_cuda.cu,
ALGO_DUNGEON116 / ALGO_LAKE116) must keep EXACTLY the 48-bit lows the CPU
pre-filter keeps (session_engine.c ssa_test_seed_pos_only → ssa_test_lows48).

    py tests/feature116_gpu_check.py [n_lows]
(needs the CUDA libs: py tools/build_native.py, and libcubiomes: --cubiomes)
"""
import ctypes as ct
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import custom_search as cs
import structure_cuda as sc
from cubiomes_wrap import CubiomesLib
from qtui.session_model import SessionModel, new_condition

MC = 19


def cond(t, box, inst, y=(0, 255), var="any", rmax=0):
    c = new_condition(t)
    c.update(area_mode=2, x1=box[0], z1=box[1], x2=box[2], z2=box[3], instances=inst, rmax=rmax)
    c["params"]["y_min"], c["params"]["y_max"] = y
    if t == "lava_lake":
        c["params"]["variant"] = var
    return c


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2_000_000
    if not sc.is_available():
        print("structure_cuda not available:", sc.get_load_error())
        return 1
    lib = CubiomesLib()
    raw = lib._lib
    raw.ssa_test_lows48.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.POINTER(ct.c_int64),
                                    ct.c_int64, ct.POINTER(ct.c_int64), ct.c_int64]
    raw.ssa_test_lows48.restype = ct.c_int64
    sessions = [
        [cond("lava_lake", (-16, -16, 16, 16), 1, (10, 40))],
        [cond("lava_lake", (-200, -200, 200, 200), 6, var="underground")],
        [cond("lava_lake", (-64, -64, 64, 64), 1, var="surface")],
        [cond("dungeon", (0, 0, 31, 31), 3, (20, 30))],
        [cond("dungeon", (-8, -8, 8, 8), 1, (62, 66))],
        [cond("dungeon", (0, 0, 0, 0), 2, (10, 50), rmax=40)],
        [cond("dungeon", (-32, -32, 32, 32), 4, (5, 40)), cond("lava_lake", (-48, -48, 48, 48), 1)],
    ]
    bad = 0
    r = random.Random(1)
    for k, cl in enumerate(sessions):
        m = SessionModel()
        for c in cl:
            m.add(c)
        conds = m.export()
        jobs, _ok = sc.build_jobs(conds, lib, MC)
        start = r.getrandbits(48) - n
        gpu, total = sc.filter_seeds(jobs, start, n, fam48=True, out_cap=1 << 22)
        arr, kk = cs._build_ssacond_array(conds, raw)
        ins = (ct.c_int64 * n)(*[(start + i) & ((1 << 48) - 1) for i in range(n)])
        outs = (ct.c_int64 * n)()
        m_ = raw.ssa_test_lows48(arr, kk, MC, 0, ins, n, outs, n)
        cpu = set(outs[:min(m_, n)])
        same = set(gpu) == cpu
        bad += not same
        print(f"session {k}: jobs {len(jobs)}  GPU {len(set(gpu))}  CPU {len(cpu)}  "
              + ("SAME" if same else f"DIFF gpu-only {sorted(set(gpu) - cpu)[:3]} cpu-only {sorted(cpu - set(gpu))[:3]}"))
    print("MISMATCHES:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
