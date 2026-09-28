"""Differential test: C engine (ssa_test_seeds_batch) vs Python evaluate_seed.
Each scenario is a condition list; both evaluators must accept exactly the same
seeds. Run:  python tests/diff_engine.py [n_seeds] [scenario...]"""
import copy, ctypes as ct, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import custom_search as cs
from cubiomes_wrap import CubiomesLib

MC = 28  # 1.21


def S(t, rel=0, r=0, box=None, en=True, inst=1, **params):
    c = {"type": t, "label": "", "enabled": en, "relative": rel, "params": params,
         "instances": inst}
    if r:
        c.update(area_mode=0, radial=r, rmax=r + 1, x1=-r, z1=-r, x2=r, z2=r)
    elif box:
        c.update(area_mode=2, rmax=0, x1=box[0], z1=box[1], x2=box[2], z2=box[3])
    return c


SCENARIOS = {
    "parent_child":   [S("village", r=1500), S("outpost", rel=1, r=400)],
    "disabled_first": [S("mansion", r=5000, en=False), S("village", r=1500), S("outpost", rel=2, r=400)],
    "disabled_parent":[S("village", r=1500, en=False), S("outpost", rel=1, r=400), S("hut", r=3000)],
    "or_basic":       [S("logic_or"), S("mansion", rel=1, r=2500), S("monument", rel=1, r=1200)],
    "or_grandchild":  [S("logic_or"), S("village", rel=1, r=1200), S("outpost", rel=2, r=500),
                       S("desert_pyramid", rel=1, r=800)],
    "or_under_struct":[S("village", r=1500), S("logic_or", rel=1), S("outpost", rel=2, r=600),
                       S("hut", rel=2, r=1500)],
    "not_basic":      [S("logic_not"), S("village", rel=1, r=700)],
    "not_under":      [S("village", r=1200), S("logic_not", rel=1), S("outpost", rel=2, r=700)],
    "spiral":         [S("spiral", r=2000, step=512), S("village", rel=1, r=300), S("outpost", rel=1, r=300)],
    "instances":      [S("village", r=2000, inst=3)],
    "nether_scale":   [S("village", r=1500), S("scale_to_nether", rel=1), S("scale_to_overworld", rel=2),
                       S("outpost", rel=3, r=500)],
    "spawn_slime":    [S("spawn", r=300), S("slime", rel=1, box=(-32, -32, 32, 32))],
    "climate_max":    [S("climate_minmax", box=(-2000, -2000, 2000, 2000), para=2, locate=0,
                         range=(None, None), nmax=-1), S("village", rel=1, r=800)],
    "climate_min":    [S("climate_minmax", box=(-2000, -2000, 2000, 2000), para=2, locate=1,
                         range=(None, None), nmax=-1), S("village", rel=1, r=800)],
    "biome_filter":   [S("biome_filter", box=(-512, -512, 512, 512), biomes_incl=[4])],  # forest
}


def c_eval(conds, seeds, lib):
    raw = lib._lib
    res = cs._build_ssacond_array(conds, raw)
    if res is None:
        raise RuntimeError("C engine unavailable")
    arr, n = res
    raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                                         ct.POINTER(ct.c_int64), ct.c_int64,
                                         ct.POINTER(ct.c_int64), ct.c_int64]
    raw.ssa_test_seeds_batch.restype = ct.c_int64
    s = np.ascontiguousarray(np.array(seeds, dtype=np.int64))
    out = np.empty(len(s), np.int64)
    k = raw.ssa_test_seeds_batch(arr, n, MC, 0, s.ctypes.data_as(ct.POINTER(ct.c_int64)), len(s),
                                 out.ctypes.data_as(ct.POINTER(ct.c_int64)), len(s))
    return {int(x) & (2**64 - 1) for x in out[:k]}


def py_eval(conds, seeds, lib):
    ok = set()
    for sd in seeds:
        if cs.evaluate_seed(copy.deepcopy(conds), sd, MC, lib)[0]:
            ok.add(sd & (2**64 - 1))
    return ok


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 200
    names = [a for a in sys.argv[1:] if not a.isdigit()] or list(SCENARIOS)
    rng = random.Random(1234)
    seeds = [rng.getrandbits(64) for _ in range(n)]
    seeds = [s - (1 << 64) if s >= (1 << 63) else s for s in seeds]
    lib = CubiomesLib()
    bad = 0
    for name in names:
        conds = SCENARIOS[name]
        t = time.perf_counter()
        try:
            c = c_eval(copy.deepcopy(conds), seeds, lib)
        except Exception as e:
            print(f"{name:16s} C ERROR {e}"); bad += 1; continue
        p = py_eval(conds, seeds, lib)
        dt = time.perf_counter() - t
        status = "OK " if c == p else "DIFF"
        bad += c != p
        print(f"{name:16s} {status} C={len(c):4d} Py={len(p):4d} onlyC={len(c-p):3d} onlyPy={len(p-c):3d}  ({dt:.1f}s)")
    bad += stale_order_check(seeds, lib)
    print("FAILURES:", bad)
    return bad


def stale_order_check(seeds, lib):
    """Regression (found on Windows 26/09): two searches in a row whose condition
    arrays share the same address and size must not share the evaluation order."""
    raw = lib._lib
    a = copy.deepcopy(SCENARIOS["or_grandchild"]); b = copy.deepcopy(SCENARIOS["or_under_struct"])
    ra = cs._build_ssacond_array(a, raw); rb = cs._build_ssacond_array(b, raw)
    arr, n = ra
    run = lambda: c_eval_arr(arr, n, seeds, raw)
    run()                                                    # tree A at this address
    ct.memmove(ct.addressof(arr), ct.addressof(rb[0]), ct.sizeof(arr))   # tree B, same address
    got = run()
    ref = c_eval(copy.deepcopy(b), seeds, lib)
    ok = got == ref
    print(f"{'stale_order':16s} {'OK ' if ok else 'DIFF'} reused-address={len(got)} fresh={len(ref)}")
    return 0 if ok else 1


def c_eval_arr(arr, n, seeds, raw):
    s = np.ascontiguousarray(np.array(seeds, dtype=np.int64))
    out = np.empty(len(s), np.int64)
    k = raw.ssa_test_seeds_batch(arr, n, MC, 0, s.ctypes.data_as(ct.POINTER(ct.c_int64)), len(s),
                                 out.ctypes.data_as(ct.POINTER(ct.c_int64)), len(s))
    return {int(x) & (2**64 - 1) for x in out[:k]}


if __name__ == "__main__":
    sys.exit(1 if main() else 0)


def engine_range(conds, start, count, workers=2):
    """Main search path: evaluate_session over a contiguous INC range."""
    import threading
    found = set()
    cs.evaluate_session(copy.deepcopy(conds), start, count, MC, workers, threading.Event(),
                        on_match=lambda s, d: found.add(int(s) & (2**64 - 1)),
                        on_progress=lambda d, t: None, search_mode="inc")
    return found


def main_range(count=300, start=987654321987):
    lib = CubiomesLib()
    seeds = list(range(start, start + count))
    bad = 0
    for name, conds in SCENARIOS.items():
        if name == "biome_filter":
            continue
        e = engine_range(conds, start, count)
        p = py_eval(conds, seeds, lib)
        bad += e != p
        print(f"{name:16s} {'OK ' if e == p else 'DIFF'} engine={len(e):4d} Py={len(p):4d}")
    print("RANGE FAILURES:", bad)
    return bad
