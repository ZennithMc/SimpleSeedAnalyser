"""(24/10) « Elytra on End spawn »: the session engine (48-bit family) finds
the seeds validated in game, and the location / label of a result.

    python tests/elytra_check.py"""
import copy, os, sys, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import custom_search as cs                                   # noqa: E402
from qtui.session_model import new_condition                 # noqa: E402

M64 = (1 << 64) - 1
MC = 34
CASES = [(3122954253559898113, "in portal"),             # validated in game (24/10)
         (-5653143432252389576, "1 block off")]             # the first FOUND (ortho)


def search(conds, start, count, mode="fam48"):
    found = set()
    cs.evaluate_session(copy.deepcopy(conds), start, count, MC, 2, threading.Event(),
                        on_match=lambda s, d: found.add(int(s) & M64),
                        on_progress=lambda d, t: None, search_mode=mode)
    return found


def main():
    c = new_condition("elytra_spawn")
    bad = 0
    for seed, lab in CASES:
        lo = seed & ((1 << 48) - 1)
        got = search([c], lo, 1)
        ok = (seed & M64) in got
        bad += not ok
        print(f"{seed}: {'OK ' if ok else 'MISSING'} ({len(got)} seed(s) in its family)", flush=True)
        r = cs.check_seed([c], seed, MC)
        print("   check_seed:", r[0], r[3] if len(r) > 3 else r)
        from qtui.sessions_page import elytra_label
        l = elytra_label(seed, MC, c)
        print("   label:", l)
        bad += not (l and l.startswith(lab))
    c2 = copy.deepcopy(c); c2["params"]["ortho"] = False
    got = search([c2], CASES[1][0] & ((1 << 48) - 1), 1)
    print("ortho off:", "OK" if (CASES[1][0] & M64) not in got else "DIFF")
    bad += (CASES[1][0] & M64) in got
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
