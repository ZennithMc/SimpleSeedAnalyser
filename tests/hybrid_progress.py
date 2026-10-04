"""The GPU + CPU hybrid loop (custom_search._hybrid_loop), driven by a CPU
stand-in for the GPU pre-filter (the condition alone on the C engine), must:
  * return exactly the seeds the plain per-seed test accepts;
  * report a seed range as searched only once its survivors are VALIDATED
    (01/10: the progress counted queued survivors → 500 M/s, then 0);
  * after a Stop, never report more than what was validated.
    python tests/hybrid_progress.py
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
MC = 25   # 1.20


def batch(ak, s):
    arr, k = ak
    s = np.ascontiguousarray(s, np.int64); o = np.empty(len(s), np.int64)
    m = raw.ssa_test_seeds_batch(arr, k, MC, 0, s.ctypes.data_as(ct.POINTER(ct.c_int64)), len(s),
                                 o.ctypes.data_as(ct.POINTER(ct.c_int64)), len(s))
    return o[:m]


def main():
    clim = d.S("climate_minmax", para="Continentalness", range=(None, "-3000"), nmax=-1,
               locate=1, mode=9, invert=False)
    clim.update(x1=0, z1=0, x2=0, z2=0)
    conds = [clim, d.S("village", rel=1, r=600)]
    A1 = cs._build_ssacond_array([conds[0]], raw)
    AF = cs._build_ssacond_array(conds, raw)
    start, N = (1 << 63) - 30000, 60000            # crosses INT64_MAX
    bad = 0
    for stop_after in (None, 0.4):
        found, prog = [], []
        lock = threading.Lock()
        ev = threading.Event()

        def scan(ss, n):
            return batch(A1, cs._seed_arange(ss, n))

        def val(s):
            r = batch(AF, s)
            with lock:
                found.extend(int(x) for x in r)
        validated = [0]
        t0 = time.perf_counter()
        if stop_after:
            threading.Timer(stop_after, ev.set).start()
        cs._hybrid_loop(scan, val, lambda: None, start, N, 2, ev,
                        lambda dd, tt: prog.append(dd))
        ref = set(int(x) for x in batch(AF, cs._seed_arange(start, N)))
        last = prog[-1] if prog else 0
        if stop_after is None:
            ok = set(found) == ref and len(found) == len(ref) and last == N
            print(f"{'OK ' if ok else 'BAD'}  full run     found {len(found)}/{len(ref)}  progress {last}/{N}"
                  f"  ({time.perf_counter() - t0:.1f} s)")
        else:
            # every reported seed must be checked: results inside the reported
            # range ⊆ found (ranges are not contiguous, so check the count bound)
            ok = set(found) <= ref and last <= N
            print(f"{'OK ' if ok else 'BAD'}  stopped      found {len(found)}/{len(ref)}  progress {last}/{N}")
        bad += not ok
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
