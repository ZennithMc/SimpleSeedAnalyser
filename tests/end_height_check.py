"""(18/10) The fast End surface (ssa_end_height_grid: shared island grid,
lazy noise columns) must give EXACTLY cubiomes' mapEndSurfaceHeight +
mapEndIslandHeight (ssa_end_height_ref), at every scale, near and far.

    python tests/end_height_check.py [n_cases]"""
import ctypes, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np                                            # noqa: E402
import cubiomes_wrap as cw                                    # noqa: E402


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    L = cw.make_worker_lib()._lib
    for fn in ("ssa_end_height_grid", "ssa_end_height_ref"):
        f = getattr(L, fn)
        f.argtypes = [ctypes.c_int, ctypes.c_uint64] + [ctypes.c_int32] * 5 + [ctypes.POINTER(ctypes.c_float)]
        f.restype = ctypes.c_int

    def run(fn, mc, seed, x, z, w, h, s):
        o = np.zeros((h, w), np.float32)
        getattr(L, fn)(mc, seed, x, z, w, h, s, o.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
        return np.nan_to_num(o, nan=-9.0)

    vers = [v for k, v in cw.MC_VERSIONS.items() if k in ("Java 1.21", "Java 1.16", "Java 1.13",
                                                            "Java 1.19", "Java 26.3")]
    rng = random.Random(11)
    bad, tn, tr = 0, 0.0, 0.0
    for _ in range(n):
        mc = rng.choice(vers); seed = rng.getrandbits(64)
        s = rng.choice([1, 2, 4, 8, 16, 64, 256])
        w = h = rng.choice([17, 64, 96]) if s <= 8 else 25
        r = rng.choice([200, 1500, 3000, 20000, 1 << 21])
        x = rng.randint(-r, r) // s; z = rng.randint(-r, r) // s
        t = time.time(); a = run("ssa_end_height_grid", mc, seed, x, z, w, h, s); tn += time.time() - t
        t = time.time(); b = run("ssa_end_height_ref", mc, seed, x, z, w, h, s); tr += time.time() - t
        if not np.array_equal(a, b):
            bad += 1
            print("DIFF", mc, seed, x, z, w, s, float(np.abs(a - b).max()), int((a != b).sum()))
    print(f"FAILURES: {bad}/{n}   fast {tn:.2f}s   cubiomes {tr:.2f}s   (×{tr / max(tn, 1e-9):.1f})")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
