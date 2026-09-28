"""Python evaluator == C engine on noise_sample sessions (Monte-Carlo climate
samples: top-level, child of a structure, parent of a structure, « yield
individual samples », limited octaves, invert).

    python tests/noise_sample_py_vs_c.py [n_seeds]
"""
import copy
import ctypes as ct
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import custom_search as cs                 # noqa: E402
from cubiomes_wrap import CubiomesLib      # noqa: E402

MC = 28


def NS(rel=0, r=600, para="Weirdness", lo=3000.0, hi=None, cov=20.0, conf=95.0, nmax=-1,
       invert=False, yld=False):
    return {"type": "noise_sample", "label": "", "enabled": True, "relative": rel,
            "rmax": r + 1, "radial": r, "x1": -r, "z1": -r, "x2": r, "z2": r, "area_mode": 0,
            "params": {"para": para, "range": [lo, hi], "nmax": nmax, "invert": invert,
                       "coverage_pct": cov, "confidence_pct": conf, "yield_samples": yld}}


def ST(t, rel=0, r=800):
    return {"type": t, "label": "", "enabled": True, "relative": rel, "instances": 1,
            "rmax": r + 1, "radial": r, "x1": -r, "z1": -r, "x2": r, "z2": r, "area_mode": 0,
            "params": {}}


SESSIONS = {
    "top weirdness ≥ 0.3": [NS()],
    "top continentalness < −0.2 (40 %)": [NS(para="Continentalness", lo=None, hi=-2000.0, cov=40.0)],
    "invert, 4 octaves": [NS(para="Temperature", lo=-2000.0, hi=2000.0, invert=True, nmax=4)],
    "village → noise sample": [ST("village", r=1500), NS(rel=1, r=300, cov=15.0)],
    "noise sample → outpost": [NS(r=1000, cov=10.0), ST("outpost", rel=1, r=500)],
    "yield samples → village": [NS(r=1200, cov=5.0, yld=True), ST("village", rel=1, r=250)],
}


def c_batch(conds, seeds, lib):
    raw = lib._lib
    arr, n = cs._build_ssacond_array(conds, raw)
    raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                                         ct.POINTER(ct.c_int64), ct.c_int64,
                                         ct.POINTER(ct.c_int64), ct.c_int64]
    raw.ssa_test_seeds_batch.restype = ct.c_int64
    ins = (ct.c_int64 * len(seeds))(*seeds)
    outs = (ct.c_int64 * len(seeds))()
    k = raw.ssa_test_seeds_batch(arr, n, MC, 0, ins, len(seeds), outs, len(seeds))
    return set(outs[:k])


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rnd = random.Random(7)
    seeds = [rnd.getrandbits(63) - (1 << 62) for _ in range(n)]
    lib = CubiomesLib()
    bad = 0
    for name, conds in SESSIONS.items():
        cset = c_batch(conds, seeds, lib)
        pset = {s for s in seeds if cs.evaluate_seed(copy.deepcopy(conds), s, MC, lib)[0]}
        ok = cset == pset
        bad += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {name:38s} C {len(cset):4d}  Python {len(pset):4d}"
              + ("" if ok else f"  only C {len(cset - pset)}, only Py {len(pset - cset)}"))
    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
