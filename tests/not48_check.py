"""(18/10) A top-level NOT over a Nether fortress / ruined portal is now
decided in the 48-bit pass when the 48 bits are enough. The 48-bit family
search (Blocks) must still give exactly the brute-force seeds.

    python tests/not48_check.py [n_lows]"""
import copy, os, random, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import custom_search as cs                                   # noqa: E402
import diff_engine as de                                     # noqa: E402
from cubiomes_wrap import CubiomesLib                        # noqa: E402

S = de.S
SC = {
    "not_fortress":      [S("logic_not"), S("fortress", rel=1, r=300)],
    "not_fortress_box":  [S("logic_not"), S("fortress", rel=1, box=(-600, -100, 200, 500))],
    "not_fortress_2":    [S("logic_not"), S("fortress", rel=1, r=900, inst=2)],
    "not_fortress_excl": [S("logic_not"), S("fortress", rel=1, r=250, inst=0)],
    "not_portal":        [S("logic_not"), S("ruined_portal_nether", rel=1, r=200)],
    "not_two":           [S("logic_not"), S("fortress", rel=1, r=150), S("bastion", rel=1, r=150)],
    "not_and_village":   [S("logic_not"), S("fortress", rel=1, r=300), S("village", r=600)],
    # (a NOT holding a 64-bit condition makes the session run Incremental:
    #  no 48-bit pass to check there)
}


def search(conds, start, count, mc, mode):
    found = set()
    cs.evaluate_session(copy.deepcopy(conds), start, count, mc, 2, threading.Event(),
                        on_match=lambda s, d: found.add(int(s) & (2**64 - 1)),
                        on_progress=lambda d, t: None, search_mode=mode)
    return found


def main():
    nl = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    lib = CubiomesLib()
    rng = random.Random(77)
    bad = 0
    for mc in (19, 20, 21, 34):
        de.MC = mc
        for name, conds in SC.items():
            lo = rng.getrandbits(48)
            every = [(h << 48) | (lo + k) for k in range(nl) for h in range(65536)]
            every = [v - (1 << 64) if v >= (1 << 63) else v for v in every]
            ref = {v & (2**64 - 1) for v in de.c_eval(copy.deepcopy(conds), every, lib)}
            got = search(conds, lo, nl, mc, "fam48")
            ok = got == ref
            bad += not ok
            print(f"mc={mc:2d} {name:18s} {'OK ' if ok else 'DIFF'} found={len(got)} ref={len(ref)}",
                  flush=True)
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
