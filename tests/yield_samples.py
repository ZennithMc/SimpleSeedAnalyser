"""« Yield individual samples » (cubiomes-viewer count == 1): the children of a
biome / noise sample are tested around EACH accepted sample position.
Checks with children whose answer is known at an accepted position:
  child = the same test at that exact point  → always passes → same seeds as alone
  child = the opposite test at that point     → never passes → no seed
Run:  python tests/yield_samples.py"""
import copy, ctypes as ct, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import custom_search as cs
from cubiomes_wrap import CubiomesLib

raw = CubiomesLib()._lib
raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.POINTER(ct.c_int64),
                                     ct.c_int64, ct.POINTER(ct.c_int64), ct.c_int64]


def run(conds, seeds):
    arr, k = cs._build_ssacond_array(conds, raw)
    n = len(seeds)
    ins = (ct.c_int64 * n)(*seeds); outs = (ct.c_int64 * n)()
    m = raw.ssa_test_seeds_batch(arr, k, 28, 0, ins, n, outs, n)
    return set(outs[:m])


NS = {"type": "noise_sample", "label": "", "enabled": True, "relative": 0, "rmax": 1025,
      "x1": 0, "z1": 0, "x2": 0, "z2": 0, "area_mode": 0,
      "params": {"para": "Weirdness", "range": [3000.0, None], "nmax": 4, "invert": False,
                 "coverage_pct": 20.0, "confidence_pct": 95.0, "yield_samples": True}}


def point(lo, hi):
    return {"type": "climate_minmax", "label": "", "enabled": True, "relative": 1, "rmax": 0,
            "x1": 0, "z1": 0, "x2": 0, "z2": 0, "area_mode": 1,
            "params": {"para": "Weirdness", "mode": (4 if lo is not None else 0) | (8 if hi is not None else 0) | 1,
                       "locate": 1, "range": [lo, hi], "nmax": 4, "invert": False}}


random.seed(5)
seeds = [random.getrandbits(63) for _ in range(1500)]
alone = run([NS], seeds)
same = run([NS, point("3000", None)], seeds)
opp = run([NS, point(None, "2999")], seeds)
nov = copy.deepcopy(NS); nov["params"]["yield_samples"] = False
print(f"alone {len(alone)}  same-test child {len(same)}  opposite child {len(opp)}  "
      f"(centre, no yield, opposite: {len(run([nov, point(None, '2999')], seeds))})")
ok = same == alone and not opp and alone
print("OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
