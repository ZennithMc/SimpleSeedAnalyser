"""
check_first_ring.py — is the list "12-eye at ANY of the 3 first-ring strongholds"?

ed's list = first-ring strongholds (the 3 closest), 12-eye at ONE of them, not
necessarily the nearest. SSA's eye finder only checked the 1st stronghold. This
checks all 3 (cubiomes find_strongholds, biome-snapped, with the any-chunk rule)
and reports how often AT LEAST ONE is 12-eye.

    py check_first_ring.py 12eye1.16_seedonly.seeds 20 2000 [K]
K = how many strongholds to check (default 3 = first ring in 1.16).
"""
import sys
import cubiomes_wrap as cw
import seedlist_io
from stronghold_eyes import count_stronghold_eyes


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "12eye1.16_seedonly.seeds"
    mc = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    N = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
    K = int(sys.argv[4]) if len(sys.argv) > 4 else 3

    lib = cw.make_worker_lib()
    any12 = total = 0
    which = {}      # index (0-based) of the 12-eye stronghold -> count
    none12 = []
    for seed in seedlist_io.iter_seedlist(path):
        seed = int(seed)
        lib.setup_and_seed(mc, seed)
        shs = lib.find_strongholds(max_count=K)
        hit_idx = -1
        for i, (bx, bz) in enumerate(shs):
            if count_stronghold_eyes(seed, bx, bz, mc) == 12:
                hit_idx = i
                break
        total += 1
        if hit_idx >= 0:
            any12 += 1
            which[hit_idx] = which.get(hit_idx, 0) + 1
        elif len(none12) < 5:
            none12.append(seed)
        if total >= N:
            break

    print(f"sample {total} seeds  (mc={mc}, checked first {K} strongholds)")
    print(f"  at least ONE of the {K} is 12-eye : {any12}  ({100*any12/total:.1f}%)")
    print(f"  which stronghold is the 12-eye one (0=nearest):")
    for k in sorted(which):
        print(f"     #{k+1}: {which[k]:>5}  ({100*which[k]/total:.1f}%)")
    print()
    if any12 >= 0.97 * total:
        print(f"=> CONFIRMED: list = 12-eye at one of the first {K} strongholds.")
        print(f"   The eye finder must check the first {K} (not just #1). I'll wire that.")
    else:
        print(f"   only {100*any12/total:.0f}% — try a bigger K, or paste this.")
        if none12:
            print("   seeds with NONE of the first", K, "being 12-eye:", none12)


if __name__ == "__main__":
    main()
