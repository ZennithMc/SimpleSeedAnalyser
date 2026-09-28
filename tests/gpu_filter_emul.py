"""CPU emulation of the GPU structure pre-filter (structure_cuda d_test_job),
driven by the SAME jobs as the real GPU (structure_cuda.build_jobs).
Checks the property the GPU path relies on: every seed the full C engine
accepts must survive the pre-filter (the pre-filter may keep extra seeds —
the CPU validates them — but must never drop a valid one).
Runs without a GPU:  python tests/gpu_filter_emul.py"""
import copy, ctypes as ct, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structure_cuda as sc
import custom_search as cs
from cubiomes_wrap import CubiomesLib, Pos
import diff_engine as d

S = d.S
CASES = {
    "mansion r300":        [S("mansion", r=300)],
    "village>outpost":     [S("village", r=300), S("outpost", rel=1, r=250)],
    "monument>village":    [S("monument", r=200), S("village", rel=1, r=250)],
    "village box":         [S("village", box=(100, -400, 700, 200))],
    "2 villages r800":     [S("village", r=800, inst=2)],
    "no mansion r2000":    [S("mansion", r=2000, inst=0), S("village", r=300)],
    "outpost r500":        [S("outpost", r=500)],
}


def emul_pass(jobs, stypes, seed, lib):
    L = lib._lib
    for j, st in zip(jobs, stypes):
        hits = 0
        ok = None
        for rz in range(j.rz1, j.rz2 + 1):
            for rx in range(j.rx1, j.rx2 + 1):
                p = Pos()
                if not L.getStructurePos(st, 28, ct.c_uint64(seed & (2**64 - 1)), rx, rz, ct.byref(p)):
                    continue
                if j.rmax_sq:
                    inside = (p.x - j.cx) ** 2 + (p.z - j.cz) ** 2 < j.rmax_sq
                else:
                    inside = j.x1 <= p.x <= j.x2 and j.z1 <= p.z <= j.z2
                if inside:
                    hits += 1
                    if hits >= j.min_count:
                        ok = not j.exclude
                        break
            if ok is not None:
                break
        if ok is None:
            ok = bool(j.exclude)
        if not ok:
            return False
    return True


def main(n=400):
    lib = CubiomesLib()
    rng = random.Random(7)
    seeds = [rng.getrandbits(64) - (1 << 63) for _ in range(n)]
    bad = 0
    for name, conds in CASES.items():
        jobs, _ = sc.build_jobs(copy.deepcopy(conds), lib, 28)
        stypes = [cs.STRUCT_TYPES[c["type"]] for c in conds
                  if not c.get("relative") and c["type"] in cs.STRUCT_TYPES
                  and int(c.get("instances", 1)) > 0]
        valid = d.c_eval(copy.deepcopy(conds), seeds, lib)
        kept = {s & (2**64 - 1) for s in seeds if emul_pass(jobs, stypes, s, lib)}
        lost = valid - kept
        bad += bool(lost)
        print(f"{name:20s} {'OK ' if not lost else 'LOST'} valid={len(valid):4d} "
              f"prefilter kept={len(kept):4d} lost={len(lost)}")
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
