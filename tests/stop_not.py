"""Stop during a seed's test must never publish that seed (01/10).

A long spiral cut short by Stop returned « fail »; under a NOT this became a
PASS, so every seed in flight at Stop came out as a result (CoffeeAddicted's
session: crunchiness + NOT(spiral ±16 384 → T ≥ 0.55), runs of consecutive
false seeds, one per thread). The engine is stopped several times in the middle
of such seeds; every published seed must pass the plain per-seed test.
    python tests/stop_not.py
"""
import ctypes as ct, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import custom_search as cs
from cubiomes_wrap import CubiomesLib
import diff_engine as d

raw = CubiomesLib()._lib
raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.POINTER(ct.c_int64),
                                     ct.c_int64, ct.POINTER(ct.c_int64), ct.c_int64]
raw.ssa_test_seeds_batch.restype = ct.c_int64
MC = 25


def batch(conds, seeds):
    arr, k = cs._build_ssacond_array(conds, raw)
    s = np.ascontiguousarray(seeds, np.int64); o = np.empty(len(s), np.int64)
    m = raw.ssa_test_seeds_batch(arr, k, MC, 0, s.ctypes.data_as(ct.POINTER(ct.c_int64)), len(s),
                                 o.ctypes.data_as(ct.POINTER(ct.c_int64)), len(s))
    return set(int(x) for x in o[:m])


def main():
    clim = d.S("climate_minmax", rel=2, para="Temperature", range=("5500", None), nmax=2,
               locate=0, invert=False)
    clim.update(x1=0, z1=0, x2=0, z2=0)
    sp = d.S("spiral", rel=1, r=16384, step=64)
    conds = [d.S("logic_not"), sp, clim]
    bad = 0
    start = 23453569473714
    for k, delay in enumerate((0.15, 0.3, 0.5, 0.8)):
        found = set()
        ev = threading.Event()
        threading.Timer(delay, ev.set).start()
        t = time.perf_counter()
        cs.evaluate_session(conds, start + k * 100000, 100000, MC, 2, ev,
                            on_match=lambda s, dd="": found.add(int(s)),
                            on_progress=lambda a, b: None, search_mode="inc")
        false = found - batch(conds, sorted(found)) if found else set()
        ok = not false
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'}  stop after {delay:.2f} s: {len(found)} published, "
              f"{len(false)} false  ({time.perf_counter() - t:.2f} s)")
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
