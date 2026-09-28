"""
check_sisters.py — do sister seeds give DIFFERENT 128-stronghold eye profiles?

A sister seed = same low 48 bits, different high 16 bits. The first-ring stronghold
eyes depend only on the low 48 bits (so sisters share them), but the deeper rings'
positions depend on the FULL 64-bit seed (cubiomes biome generation), so the deep
strongholds — and their eyes — should DIFFER between sisters.

If they differ, sisters are a (65536x larger) source of NEW rare 128-combinations.
If they are identical, searching sisters is pointless.

    py check_sisters.py 12eye1.16_seedonly.seeds 20 [n_seeds] [n_sisters]
"""
import sys
import itertools
import cubiomes_wrap as cw
import seedlist_io
from stronghold_eyes import count_stronghold_eyes

_MASK48 = (1 << 48) - 1


def _signed(u):
    return u - (1 << 64) if u >= (1 << 63) else u


def _profile(lib, mc, full):
    lib.setup_and_seed(mc, full)
    shs = lib.find_strongholds(max_count=128)
    eyes = [count_stronghold_eyes(full, bx, bz, mc) for (bx, bz) in shs]
    n12 = sum(1 for e in eyes if e == 12)
    return shs, eyes, n12


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "12eye1.16_seedonly.seeds"
    mc = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    n_seeds = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    n_sis = int(sys.argv[4]) if len(sys.argv) > 4 else 4

    lib = cw.make_worker_lib()
    sample = list(itertools.islice(seedlist_io.iter_seedlist(path), n_seeds))

    for base in sample:
        struct = base & _MASK48
        print(f"structure seed (low48 = {struct}):")
        ring1 = None
        deep_differ = False
        for h16 in range(n_sis):
            full = _signed((h16 << 48) | struct)
            shs, eyes, n12 = _profile(lib, mc, full)
            top = sorted(eyes, reverse=True)[:6]
            r1 = (shs[0], eyes[0]) if shs else None
            if h16 == 0:
                ring1 = r1
            elif r1 != ring1:
                pass  # ring1 should be identical across sisters
            print(f"   sister h16={h16:>2}: 1st={shs[0] if shs else None} "
                  f"eyes1={eyes[0] if eyes else '-':>2}  #12-eye={n12}  top6={top}")
        print()

    print("If the deep strongholds (positions / #12-eye / top eyes) DIFFER between")
    print("h16=0,1,2,... then sisters give NEW 128-combinations (worth searching,")
    print("but 65536x more seeds). If they're identical, sisters add nothing.")


if __name__ == "__main__":
    main()
