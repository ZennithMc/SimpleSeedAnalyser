"""Nether / End conditions (18/10): the C engine (ssa_test_seeds_batch), the
Python evaluator (evaluate_seed) and the search itself (evaluate_session, in
Incremental and 48-bit family modes) must accept the same seeds; a session
saved to a cubiomes-viewer .session and read back must give them again.

    python tests/dims_engine_check.py [n_seeds] [mc]
(cubiomes-viewer itself is the reference in tests/fuzz_dims.py.)"""
import copy, os, random, sys, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import custom_search as cs                                   # noqa: E402
import diff_engine as de                                     # noqa: E402
from cubiomes_wrap import CubiomesLib                        # noqa: E402

S = de.S

SCENARIOS = {
    "fortress":        [S("fortress", r=900)],
    "fortress_excl":   [S("fortress", r=300, inst=0)],
    "fortress_dense":  [S("fortress", r=3000, variant="dense")],
    "bastion":         [S("bastion", r=500, inst=2)],
    "bastion_starts":  [S("bastion", r=900, starts=["treasure", "bridge"])],
    "portal_nether":   [S("ruined_portal_nether", r=400, inst=2)],
    "end_city":        [S("end_city", r=1500)],
    "end_ship":        [S("end_city", r=1500, variant="with_ship")],
    "end_noship":      [S("end_city", r=2000, inst=2, variant="without_ship")],
    "gateway":         [S("end_gateway", box=(900, -800, 2500, 800))],
    "nether_b4":       [S("nether_biomes", box=(-300, -300, 300, 300), biomes_incl=[171, 172])],
    "nether_b1_any":   [S("nether_biomes", box=(-40, -40, 40, 40), biomes_incl=[170, 173],
                          match_any=True, scale=1)],
    "nether_excl":     [S("nether_biomes", box=(-200, -200, 200, 200), biomes_excl=[173], scale=16)],
    "end_b16":         [S("end_biomes", box=(-1500, -1500, 1500, 1500), biomes_incl=[42], scale=16)],
    "end_b64_any":     [S("end_biomes", box=(-3000, -3000, 3000, 3000), biomes_incl=[41, 43],
                          match_any=True, scale=64)],
    "ow_to_nether":    [S("village", r=1500), S("scale_to_nether", rel=1),
                        S("fortress", rel=2, r=400)],
    "nether_to_ow":    [S("bastion", r=400), S("scale_to_overworld", rel=1),
                        S("village", rel=2, r=800)],
    "fortress_kids":   [S("fortress", r=1200), S("nether_biomes", rel=1, box=(-48, -48, 48, 48),
                                                 biomes_incl=[170])],
    "end_chain":       [S("end_city", r=1500, variant="with_ship"),
                        S("end_gateway", rel=1, r=700)],
    "or_dims":         [S("logic_or"), S("fortress", rel=1, r=200), S("end_city", rel=1, r=400),
                        S("village", rel=1, r=300)],
    "not_nether":      [S("logic_not"), S("bastion", rel=1, r=250)],
    "spiral_nether":   [S("spiral", r=1500, step=512), S("fortress", rel=1, r=200)],
}


def session_range(conds, start, count, mc, mode):
    found = set()
    cs.evaluate_session(copy.deepcopy(conds), start, count, mc, 2, threading.Event(),
                        on_match=lambda s, d: found.add(int(s) & (2**64 - 1)),
                        on_progress=lambda d, t: None, search_mode=mode)
    return found


def roundtrip(conds, mc):
    """Save as a cubiomes-viewer session and read it back."""
    from cubiomes_session import parse_session_file
    from cubiomes_session_writer import write_session_file
    fd, path = tempfile.mkstemp(suffix=".session"); os.close(fd)
    try:
        write_session_file(path, copy.deepcopy(conds), mc_str="Java 1.21")
        # only the cubiomes-viewer part (#Cond hex), not SSA's own #SSAx copy
        lines = [l for l in open(path, encoding="utf-8") if "#SSAx:" not in l]
        open(path, "w", encoding="utf-8").writelines(lines)
        return cs.active_conditions(parse_session_file(path)["conditions"])
    finally:
        os.unlink(path)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    de.MC = int(sys.argv[2]) if len(sys.argv) > 2 else 28
    rng = random.Random(4321)
    seeds = [rng.getrandbits(64) for _ in range(n)]
    seeds = [s - (1 << 64) if s >= (1 << 63) else s for s in seeds]
    lib = CubiomesLib()
    bad = 0
    for name, conds in SCENARIOS.items():
        c = de.c_eval(copy.deepcopy(conds), seeds, lib)
        p = de.py_eval(conds, seeds, lib)
        try:
            back = roundtrip(conds, de.MC)
            r = de.c_eval(back, seeds, lib)
        except Exception as e:                                # noqa: BLE001
            r = f"ERR {e}"
        ok = c == p and r == c
        bad += not ok
        print(f"{name:16s} {'OK ' if ok else 'DIFF'} C={len(c):4d} Py={len(p):4d} "
              f"session={'=' if r == c else r if isinstance(r, str) else len(r)}", flush=True)
    # the search itself on a contiguous range: Incremental, and 48-bit family
    # (Blocks: every 64-bit seed of each family whose 48 bits pass)
    start, count = 987654321987, int(os.environ.get("DIMS_RANGE", "600"))
    for name in ("fortress", "end_ship", "ow_to_nether", "nether_b4", "or_dims"):
        conds = SCENARIOS[name]
        ref = de.c_eval(copy.deepcopy(conds), list(range(start, start + count)), lib)
        got = session_range(conds, start, count, de.MC, "inc")
        ok = got == ref
        bad += not ok
        print(f"search {name:12s} inc    {'OK ' if ok else 'DIFF'} found={len(got)} ref={len(ref)}",
              flush=True)
        lo = start & ((1 << 48) - 1)
        fam = [lo + k for k in range(2)]
        every = [(h << 48) | f for f in fam for h in range(65536)]
        every = [v - (1 << 64) if v >= (1 << 63) else v for v in every]
        ref = de.c_eval(copy.deepcopy(conds), every, lib)
        got = session_range(conds, lo, 2, de.MC, "fam48")
        ok = got == ref
        bad += not ok
        print(f"search {name:12s} fam48  {'OK ' if ok else 'DIFF'} found={len(got)} ref={len(ref)}",
              flush=True)
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
