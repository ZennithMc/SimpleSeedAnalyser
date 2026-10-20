"""Pre-1.18 biomes (17/10): C engine vs Python evaluate_seed vs a brute-force
reference (genBiomes at 1:4), with the biomes that exist only before 1.18
(hills, modified, mushroom shore…), top-level, as children, excluded.
Run:  python tests/old_biomes_check.py [n_seeds] [mc]"""
import copy, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import custom_search as cs
from cubiomes_wrap import CubiomesLib
import diff_engine_shim as D

def S(t, rel=0, r=0, box=None, inst=1, **params):
    c = {"type": t, "label": "", "enabled": True, "relative": rel, "params": params, "instances": inst}
    if r:
        c.update(area_mode=0, radial=r, rmax=r + 1, x1=-r, z1=-r, x2=r, z2=r)
    elif box:
        c.update(area_mode=2, rmax=0, x1=box[0], z1=box[1], x2=box[2], z2=box[3])
    return c

B = (-384, -384, 384, 384)
SCEN = {
    "taiga_hills":      ([S("biome_filter", box=B, biomes_incl=[19])], ("all", B, [19])),
    "desert_hills+lakes": ([S("biome_filter", box=B, biomes_incl=[17, 130])], ("all", B, [17, 130])),
    "mushroom_shore":   ([S("biome_filter", box=(-2048, -2048, 2048, 2048), biomes_incl=[15])],
                         ("all", (-2048, -2048, 2048, 2048), [15])),
    "giant_tree_hills": ([S("biome_filter", box=B, biomes_incl=[33])], ("all", B, [33])),
    "modified_badlands":([S("biome_filter", box=(-1024, -1024, 1024, 1024), biomes_incl=[167, 39])],
                         ("all", (-1024, -1024, 1024, 1024), [167, 39])),
    "exclude_wooded_hills": ([S("biome_filter", box=B, biomes_incl=[18], inst=0)], ("none", B, [18])),
    "village>wooded_hills": ([S("village", r=1500), S("biome_filter", rel=1, box=(-160, -160, 160, 160),
                                                      biomes_incl=[18])], None),
    "village>tall_birch": ([S("village", r=2500), S("biome_filter", rel=1, box=(-300, -300, 300, 300),
                                                    biomes_incl=[155, 156])], None),
    "center_taiga_hills": ([S("biome_center", r=1500, biome_id=19, min_size=1, tolerance=2, count=1)], None),
    "center>outpost":   ([S("biome_center", r=2000, biome_id=18, min_size=1, tolerance=2, count=1),
                          S("outpost", rel=1, r=1200)], None),
    "sample_forestish": ([S("biome_sample", box=(-256, -256, 256, 256), biomes_incl=[4, 18, 132],
                            mode="coverage", coverage_pct=30, confidence_pct=95,
                            blocks_scale="1/4 (fast)")], None),
    "center_forest_modern": ([S("biome_center", r=1500, biome_id=4, min_size=1, tolerance=2, count=1)], None),
    "sample_forest_modern": ([S("biome_sample", box=(-256, -256, 256, 256), biomes_incl=[4, 132],
                            mode="coverage", coverage_pct=30, confidence_pct=95,
                            blocks_scale="1/4 (fast)")], None),
    "or_old":           ([S("logic_or"), S("biome_filter", rel=1, box=B, biomes_incl=[19]),
                          S("biome_filter", rel=1, box=B, biomes_incl=[22])], ("any", B, [19, 22])),
}


def truth(lib, seed, mc, spec):
    how, (x1, z1, x2, z2), ids = spec
    lib.setup_and_seed(mc, seed, False)
    gx, gz = x1 >> 2, z1 >> 2
    sx, sz = (x2 >> 2) - gx + 1, (z2 >> 2) - gz + 1
    buf = lib.gen_biomes_region(gx, gz, sx, sz, 4)
    have = set(np.frombuffer(buf, dtype=np.int32)[:sx * sz].tolist())
    if how == "all":
        return all(b in have for b in ids)
    if how == "any":
        return any(b in have for b in ids)
    return not any(b in have for b in ids)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    mc = int(sys.argv[2]) if len(sys.argv) > 2 else 19
    D.MC = mc
    rng = random.Random(77)
    seeds = [rng.getrandbits(64) for _ in range(n)]
    seeds = [s - (1 << 64) if s >= (1 << 63) else s for s in seeds]
    lib = CubiomesLib()
    bad = 0
    names = [a for a in sys.argv[3:]] or list(SCEN)
    for name in names:
        conds, spec = SCEN[name]
        t = time.perf_counter()
        c = D.c_eval(copy.deepcopy(conds), seeds, lib)
        p = D.py_eval(conds, seeds, lib)
        ok = c == p
        extra = ""
        if spec is not None:
            tr = {s & (2**64 - 1) for s in seeds if truth(lib, s, mc, spec)}
            ok = ok and tr == c
            extra = f" truth={len(tr):4d}" + ("" if tr == c else f" (C-truth {len(c - tr)}, truth-C {len(tr - c)})")
        bad += not ok
        print(f"{name:22s} {'OK ' if ok else 'DIFF'} C={len(c):4d} Py={len(p):4d}{extra}  ({time.perf_counter() - t:.1f}s)")
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
