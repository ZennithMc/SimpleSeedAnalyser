"""Locate climate center (coarse flood fill, 01/10) — C engine against an
independent Python reference.

The reference samples every cell with cubiomes' own climate path
(bfb_climate_minmax: setClimateParaSeed + getParaRange on ONE 1:4 cell), not
the engine's shared-octave cache, and runs its own breadth-first fill with the
same definition:
  cells of s blocks, sampled at their centre block (>> 2 = the 1:4 cell),
  4-connected, start = the cell of the reference position, window = the area
  (area 0 = ±128 cells), pass when the region has ≥ min cells (and ≤ max cells,
  not cut by the window, when a max is set); the pushed position is the centre
  block of the mean cell (or the start cell when no centre is needed).
Checked: pass / fail AND position, for many seeds, scales, ranges, octave
limits, invert, windows, max sizes; then the search engine against the
per-seed test on a tree with a child placed at the region's centre.
    python tests/climate_area.py [n_seeds]
"""
import copy, ctypes as ct, math, os, random, sys, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import custom_search as cs
from cubiomes_wrap import CubiomesLib
import diff_engine as d

MC = 25            # 1.20
PARA = {"Temperature": 0, "Humidity": 1, "Continentalness": 2, "Erosion": 3, "Weirdness": 5}
lib = CubiomesLib()
raw = lib._lib
d.MC = MC


def ref_eval(seed, cond, at=(0, 0), need_center=True):
    p = cond["params"]
    para = PARA[p["para"]]
    s = int(p.get("scale", 16))
    lo, hi = p["range"]
    vmin = float(lo) if lo is not None else -math.inf
    vmax = float(hi) if hi is not None else math.inf
    inv = bool(p.get("invert"))
    nmax = int(p.get("nmax", -1))
    need = max(1, int(p.get("min_size", 1)))
    maxc = int(p.get("max_size", 0) or 0)
    want = need_center or maxc > 0
    lib.setup_and_seed(MC, seed)
    cache = {}

    def ok(i, j):
        k = (i, j)
        if k not in cache:
            qx = (i * s + s // 2) >> 2; qz = (j * s + s // 2) >> 2
            _p, _x, _z, v = lib.climate_minmax(para, qx * 4, qz * 4, qx * 4 + 3, qz * 4 + 3,
                                               mode=2, nmax=nmax)
            cache[k] = (not (vmin < v < vmax)) if inv else (vmin <= v <= vmax)
        return cache[k]
    ax, az = at
    i0, j0 = ax // s, az // s
    rmax = int(cond.get("rmax", 0) or 0)
    if rmax:
        rb = rmax - 1
        gx1, gx2, gz1, gz2 = (ax - rb) // s, (ax + rb) // s, (az - rb) // s, (az + rb) // s
        r2 = rb * rb + 1
    elif cond["x1"] == cond["x2"] and cond["z1"] == cond["z2"]:
        gx1, gx2, gz1, gz2 = i0 - 128, i0 + 128, j0 - 128, j0 + 128
        r2 = 0
    else:
        a, b = ax + cond["x1"], ax + cond["x2"]; c_, e = az + cond["z1"], az + cond["z2"]
        gx1, gx2 = min(a, b) // s, max(a, b) // s
        gz1, gz2 = min(c_, e) // s, max(c_, e) // s
        r2 = 0

    def inwin(i, j):
        if not (gx1 <= i <= gx2 and gz1 <= j <= gz2):
            return False
        if r2:
            dx = i * s + s // 2 - ax; dz = j * s + s // 2 - az
            return dx * dx + dz * dz < r2
        return True
    if not ok(i0, j0):
        return False, None
    if need <= 1 and not want:
        return True, (i0 * s + s // 2, j0 * s + s // 2)
    seen = {(i0, j0)}; q = [(i0, j0)]; h = 0
    cnt = sx = sz = 0; border = False
    while h < len(q):
        i, j = q[h]; h += 1
        cnt += 1; sx += i; sz += j
        if not want and cnt >= need:
            return True, (i0 * s + s // 2, j0 * s + s // 2)
        if maxc and cnt > maxc:
            return False, None
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (i + di, j + dj)
            if not inwin(*n):
                border = True; continue
            if n in seen:
                continue
            seen.add(n)
            if ok(*n):
                q.append(n)
    if cnt < need or (maxc and (cnt > maxc or border)):
        return False, None
    if want:
        mi, mj = sx // cnt, sz // cnt
        return True, (mi * s + s // 2, mj * s + s // 2)
    return True, (i0 * s + s // 2, j0 * s + s // 2)


class Ctx:                     # what _c_eval_single needs
    def __init__(self, seed):
        self.lib = lib; self.seed = seed; self.mc_version = MC


def c_eval(seed, cond, at, need_center):
    one = copy.deepcopy(cond)
    if need_center:
        one["params"]["_need_center"] = True
    r = cs._c_eval_single(Ctx(seed), one, 1, [one], at[0], at[1])
    return r[0], ((r[1], r[2]) if r[0] else None)


def cc(para, rng, scale, mn, mx=0, inv=False, nmax=-1, r=0, box=None):
    c = d.S("climate_center", r=r, box=box, para=para, range=rng, scale=scale,
            min_size=mn, max_size=mx, invert=inv, nmax=nmax)
    c.setdefault("x1", 0); c.setdefault("z1", 0); c.setdefault("x2", 0); c.setdefault("z2", 0)
    return c


CASES = [
    cc("Continentalness", (None, "-1000"), 256, 30),
    cc("Continentalness", ("3000", None), 64, 40, box=(-1500, -1500, 1500, 1500)),
    cc("Temperature", ("2000", None), 64, 25, nmax=2, r=1200),
    cc("Erosion", ("-2000", "2000"), 256, 10, mx=60),
    cc("Humidity", ("-1000", "1000"), 64, 15, inv=True, box=(-1000, -800, 1200, 900)),
    cc("Weirdness", (None, "0"), 256, 50, nmax=1),
    cc("Continentalness", (None, "-4500"), 64, 1),
    cc("Temperature", ("-4500", None), 256, 8, mx=40, r=6000),
    cc("Continentalness", (None, "-4500"), 64, 1, mx=400),
    cc("Weirdness", (None, "-5000"), 64, 2, mx=60, r=5000),
    cc("Temperature", ("4000", None), 16, 30, mx=3000, box=(-600, -600, 600, 600)),
]


def main():
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    rng = random.Random(7)
    bad = 0
    for k, cond in enumerate(CASES):
        good = passed = 0
        for _ in range(n_seeds):
            seed = rng.getrandbits(64) - (1 << 63)
            at = (rng.randint(-3000, 3000), rng.randint(-3000, 3000))
            for need_center in (True, False):
                a = c_eval(seed, cond, at, need_center)
                b = ref_eval(seed & 0xFFFFFFFFFFFFFFFF, cond, at, need_center)
                if a == b:
                    good += 1
                elif bad < 6:
                    print("   DIFF seed", seed, "at", at, "center", need_center, "C", a, "ref", b)
                passed += a[0]
        n = 2 * n_seeds
        ok = good == n
        bad += not ok
        p = cond["params"]
        print(f"{'OK ' if ok else 'BAD'}  case {k}: {p['para'][:5]} {p['range']} 1:{p['scale']} "
              f"min {p['min_size']} max {p['max_size']} inv {int(p['invert'])} nmax {p['nmax']}"
              f"  {good}/{n} equal, {passed} passes")
    # the search engine (threads, pause logic, child at the centre) = per-seed test
    tree = [cc("Continentalness", (None, "-1500"), 256, 12), d.S("village", rel=1, r=1500)]
    seeds = [rng.getrandbits(63) for _ in range(300)]
    e1 = d.c_eval(tree, seeds, lib); e2 = d.py_eval(tree, seeds, lib)
    ok = e1 == e2
    bad += not ok
    print(f"{'OK ' if ok else 'BAD'}  engine vs Python path (child at the centre): "
          f"{len(e1)} / {len(e2)} seeds")
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
