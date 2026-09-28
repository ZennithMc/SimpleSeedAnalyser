"""biome_sample GPU pre-filter — exactness check without a GPU.

The GPU filter evaluates the SAME first sample cells as cubiomes'
monteCarloBiomes (same Java RNG seeded by the world seed) and drops a seed only
when none of them is a required biome. This test runs the CPU reference of that
kernel (custom_search.monte_prefix_emul, exact FP64 biomes) and checks, on real
seed ranges, that EVERY seed the C engine accepts is kept (lost must be 0).
Before: a fixed 21×21 grid, at y=64 instead of the condition's y → 7.5 % of the
cherry seeds lost on the RTX bench.

    python tests/biome_sample_prefix_test.py
"""
import copy, ctypes as ct, os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import custom_search as cs
import cubiomes_wrap as cw


def C(r=0, box=None, **params):
    c = {"type": "biome_sample", "label": "", "enabled": True, "relative": 0,
         "params": dict(params), "instances": 1}
    if r:
        c.update(area_mode=0, radial=r, rmax=r + 1, x1=-r, z1=-r, x2=r, z2=r)
    else:
        c.update(area_mode=2, rmax=0, x1=box[0], z1=box[1], x2=box[2], z2=box[3])
    return c


CASES = [
    ("cherry 1% r1000", 28, C(r=1000, biomes_incl=[185], coverage_pct=1.0,
                              confidence_pct=95.0, blocks_scale="1/4 (fast)"), 1500),
    ("jungle 5% r600 y=64", 28, C(r=600, biomes_incl=[21], coverage_pct=5.0,
                                  confidence_pct=90.0, blocks_scale="1/4 (fast)", y=64), 1500),
    ("plains 30% box", 28, C(box=(-300, -800, 900, 400), biomes_incl=[1], coverage_pct=30.0,
                             confidence_pct=99.0, blocks_scale="1/4 (fast)"), 800),
    ("mushroom 0.5% r1500 1.19.2", 23, C(r=1500, biomes_incl=[14], coverage_pct=0.5,
                                         confidence_pct=95.0, blocks_scale="1/4 (fast)"), 800),
]


def main():
    lib = cw.CubiomesLib()
    raw = lib._lib
    raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                                         ct.POINTER(ct.c_int64), ct.c_int64,
                                         ct.POINTER(ct.c_int64), ct.c_int64]
    raw.ssa_test_seeds_batch.restype = ct.c_int64
    raw.getBiomeAt.restype = ct.c_int
    bad = 0
    for name, mc, cond, N in CASES:
        arr, n = cs._build_ssacond_array([cond], raw)
        plan, why = cs.monte_prefix_plan(arr[0])
        if plan is None:
            print(f"{name:32s} declined: {why}")
            continue
        start = 7_000_000_000 + 1_000_003 * len(name)
        seeds = (ct.c_int64 * N)(*range(start, start + N))
        out = (ct.c_int64 * N)()
        k = raw.ssa_test_seeds_batch(arr, n, mc, 0, seeds, N, out, N)
        acc = set(out[i] for i in range(k))
        incl = set(cond["params"]["biomes_incl"])
        kept = set()
        t = time.perf_counter()
        for s in range(start, start + N):
            lib.setup_and_seed(mc, s, False)
            g = lib._gen
            f = lambda x, y, z: raw.getBiomeAt(g, 4, x, y, z)
            if cs.monte_prefix_emul(s, plan, f, incl):
                kept.add(s)
        lost = acc - kept
        bad += bool(lost)
        print(f"{name:32s} m0={plan['m0']:5d}  C accepts {len(acc):4d}/{N}  prefilter keeps "
              f"{len(kept):4d}  lost={len(lost)}  {'OK' if not lost else 'FAIL ' + str(sorted(lost)[:5])}"
              f"  ({time.perf_counter() - t:.0f} s)")
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
