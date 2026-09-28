"""Draw engine v2 — correctness tests.

  1. a drawing copied from a seed's own map scores 100 % on that seed
     (the old engine gave 58 % in its default mode);
  2. the exact score / match mask equal an INDEPENDENT reference (old C
     samplers for continentalness / temperature, cubiomes getBiomeAt for
     biomes), for random MIXED drawings (land, ocean, hot, cold, biomes,
     groups), several versions, large biomes;
  3. the search returns exactly the scores of the per-seed test, and misses
     (almost) nothing above the threshold (pre-filter recall);
  4. no slide, 1x1 drawings, pre-1.18 versions: no crash, sane scores.

    python tests/draw_engine_test.py
"""
import ctypes as ct
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import draw_engine as de
from cubiomes_wrap import MC_VERSIONS, CubiomesLib, set_large_biomes

L = CubiomesLib()._lib
L.dsearch_class_grid.argtypes = [ct.c_int64, ct.c_int, ct.c_int, ct.c_int, ct.c_int,
                                 ct.c_int, ct.c_int, ct.c_int, ct.POINTER(ct.c_int8)]
L.dsearch_temp_grid.argtypes = L.dsearch_class_grid.argtypes
L.getBiomeAt.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.c_int]
L.getBiomeAt.restype = ct.c_int
bad = 0


def check(name, ok, info=""):
    global bad
    bad += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {name:52s} {info}", flush=True)


def reference(spec, seed, ox, oz):
    """Observed class per painted cell, computed independently."""
    W, H, cell = spec.W, spec.H, spec.cell
    x0, z0 = spec.corner(ox, oz)
    n = W * H
    cont = (ct.c_int8 * n)(); temp = (ct.c_int8 * n)()
    L.dsearch_class_grid(seed, spec.mc, int(spec.large), x0, z0, W, H, cell, cont)
    L.dsearch_temp_grid(seed, spec.mc, int(spec.large), x0, z0, W, H, cell, temp)
    cont = np.frombuffer(bytes(cont), np.int8).reshape(H, W).astype(int)
    temp = np.frombuffer(bytes(temp), np.int8).reshape(H, W).astype(int)
    temp[temp == 1] = 0                              # old "neutral = land" → neither
    g = CubiomesLib()
    set_large_biomes(spec.large)
    g.setup_and_seed(spec.mc, seed, large_biomes=spec.large)
    grid = np.asarray(spec.grid)
    masks = de.group_masks()
    match = np.zeros((H, W), bool)
    for j in range(H):
        for i in range(W):
            c = int(grid[j, i])
            if c == 0:
                continue
            if c in (1, 2, 3):
                ok = cont[j, i] == c
            elif c in (4, 5):
                ok = temp[j, i] == c
            else:
                x = x0 + i * cell + cell // 2
                z = z0 + j * cell + cell // 2
                b = L.getBiomeAt(g._gen, 4, x >> 2, 320 >> 2, z >> 2)
                if de.is_group_code(c):
                    k = c - de.BIO - de.GROUP_BASE
                    ok = 0 <= b < 256 and bool((int(masks[b]) >> k) & 1)
                else:
                    ok = b == c - de.BIO
            match[j, i] = ok
    w = spec.effective_weights()
    return float(w[match].sum() / w[grid != 0].sum()), match


def random_spec(r, mc, large=False, kinds=("cont", "temp", "bio", "grp"), W=None, H=None):
    W = W or r.randint(6, 40)
    H = H or r.randint(6, 40)
    codes = []
    if "cont" in kinds: codes += [1, 2, 3]
    if "temp" in kinds: codes += [4, 5]
    if "bio" in kinds: codes += [de.biome_code(b) for b in (1, 4, 5, 21, 2, 7, 24, 35, 185)]
    if "grp" in kinds: codes += [de.group_code(k) for k in range(len(de.GROUPS))]
    grid = np.zeros((H, W), np.int32)
    for _ in range(r.randint(2, 6)):                 # blobs
        c = r.choice(codes)
        cy, cx, rad = r.randrange(H), r.randrange(W), r.randint(1, max(2, min(W, H) // 2))
        yy, xx = np.ogrid[:H, :W]
        grid[(yy - cy) ** 2 + (xx - cx) ** 2 <= rad * rad] = c
    if not grid.any():
        grid[0, 0] = codes[0]
    w = np.ones((H, W), np.float32)
    if r.random() < 0.5:
        w[: H // 2] = r.choice([0.5, 2.0, 4.0])
    return de.DrawSpec(grid, w, cell=r.choice([16, 32, 48, 64, 96, 128]),
                       center=(r.randint(-3000, 3000), r.randint(-3000, 3000)),
                       slide=r.choice([0, 2, 5, 12]), mc=mc, large=large,
                       balance=r.random() < 0.3)


def main():
    r = random.Random(3)
    mc = MC_VERSIONS["Java 1.21"]
    # 1. perfect copy
    for seed in (123456789, -4242424242, 99):
        spec = de.DrawSpec(np.ones((48, 48), np.int32), None, cell=96, slide=24, mc=mc)
        e = de.DrawEngine(spec)
        _sc, obs, _mm = e.eval_at(seed, 0, 0)          # the seed's own land / ocean map
        spec2 = de.DrawSpec(obs.astype(np.int32), None, cell=96, slide=24, mc=mc)
        e2 = de.DrawEngine(spec2)
        sc, ox, oz = e2.score_seed(seed)
        check(f"copy of seed {seed}'s map scores 100 %", abs(sc - 1.0) < 1e-9,
              f"score {sc * 100:.2f} % at ({ox},{oz})")
    # 2. exact score vs independent reference
    n_ok = n = 0
    worst = ""
    for t in range(40):
        ver = r.choice(["Java 1.21", "Java 1.18", "Java 1.20", "Java 1.21.4 +"])
        large = r.random() < 0.25
        spec = random_spec(r, MC_VERSIONS.get(ver, mc), large)
        set_large_biomes(large)
        e = de.DrawEngine(spec)
        seed = r.getrandbits(63) - (1 << 62)
        ox = r.randint(-spec.slide, spec.slide)
        oz = r.randint(-spec.slide, spec.slide)
        sc, _obs, mm = e.eval_at(seed, ox, oz)
        ref, rmatch = reference(spec, seed, ox, oz)
        painted = np.asarray(spec.grid) != 0
        same = abs(sc - ref) < 1e-6 and np.array_equal(mm[painted] == 1, rmatch[painted])
        n += 1; n_ok += same
        if not same and not worst:
            worst = f"{ver} large={large} seed={seed} C={sc:.4f} ref={ref:.4f}"
    set_large_biomes(False)
    check("exact score == independent reference (40 mixed drawings)", n_ok == n,
          f"{n_ok}/{n}  {worst}")
    # 3. search vs exhaustive per-seed test
    for kinds, cnt, side, slide in ((("cont",), 3000, 24, 6), (("cont", "temp"), 2000, 24, 6),
                                    (("bio", "grp"), 300, 24, 6), (("cont", "bio"), 400, 24, 6),
                                    (("cont",), 600, 56, 12), (("cont", "temp"), 600, 48, 16),
                                    (("temp",), 600, 40, 10)):
        spec = random_spec(r, mc, kinds=kinds, W=side, H=side)
        spec.slide = slide
        ex = de.DrawEngine(spec)
        start = r.getrandbits(62)
        allsc = {start + k: ex.score_seed(start + k)[0] for k in range(cnt)}
        thr = sorted(allsc.values(), reverse=True)[min(20, cnt - 1)]
        e = de.DrawEngine(spec, min_score=thr, top_k=1000)
        stop = ct.c_int(0)
        e.run(start, cnt, 4, stop)
        res = e.results()
        exact = all(abs(allsc[s] - sc) < 1e-9 for s, sc, _x, _z in res)
        want = {s for s, v in allsc.items() if v >= thr}
        got = {s for s, *_ in res}
        miss = want - got
        kinds = kinds + (f"{side}",)
        check(f"search {'+'.join(kinds):9s}: scores = per-seed test", exact and got <= want,
              f"{len(got)} results")
        check(f"search {'+'.join(kinds):9s}: recall above threshold", len(miss) <= max(1, len(want) // 10),
              f"{len(want) - len(miss)}/{len(want)} found (thr {thr * 100:.1f} %)")
    # 4. edge cases
    ok = True
    for ver in ("Java 1.16", "Java 1.17", "Java 1.21"):
        for W, H, slide in ((1, 1, 0), (3, 2, 0), (40, 7, 1), (64, 64, 0)):
            spec = random_spec(r, MC_VERSIONS[ver], kinds=("cont", "bio"), W=W, H=H)
            spec.slide = slide
            try:
                e = de.DrawEngine(spec, min_score=0.0, top_k=50)
                stop = ct.c_int(0)
                e.run(r.getrandbits(40), 64, 3, stop)
                for s, sc, x, z in e.results():
                    ok &= 0.0 <= sc <= 1.0 and abs(x) <= slide and abs(z) <= slide
            except ValueError:
                pass
    check("edge cases (1x1, no slide, 1.16 / 1.17 / 1.21)", ok)
    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
