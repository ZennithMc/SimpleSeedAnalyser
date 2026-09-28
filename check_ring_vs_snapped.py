"""
check_ring_vs_snapped.py — is the 12-eye list defined at the biome-FREE ring
position or at the real biome-SNAPPED (in-game) stronghold?

For each sample seed it counts eyes at BOTH:
  - ring  : get_first_stronghold_pos (getFirstPos / ring, biome-free)
  - snap  : cubiomes find_strongholds (biome-snapped, the real in-game stronghold)
both with the verified any-chunk 12-eye rule.

If ~100% are 12-eye at the RING but far fewer at the SNAP, the list was made with
the ring shortcut, and SSA's snapped eye count is the right tool to filter it.

    py check_ring_vs_snapped.py 12eye1.16_seedonly.seeds 20 2000
"""
import sys
import cubiomes_wrap as cw
import seedlist_io
from stronghold_eyes import count_stronghold_eyes, get_first_stronghold_pos


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "12eye1.16_seedonly.seeds"
    mc = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    N = int(sys.argv[3]) if len(sys.argv) > 3 else 2000

    lib = cw.make_worker_lib()
    ring12 = snap12 = both12 = ringeqsnap = total = 0
    examples_ring_only = []
    for seed in seedlist_io.iter_seedlist(path):
        seed = int(seed)
        rbx, rbz = get_first_stronghold_pos(seed)        # ring (biome-free)
        lib.setup_and_seed(mc, seed)
        sh = lib.find_strongholds(max_count=1)
        if not sh:
            continue
        sbx, sbz = sh[0]                                 # snapped (in-game)
        er = count_stronghold_eyes(seed, rbx, rbz, mc)
        es = count_stronghold_eyes(seed, sbx, sbz, mc)
        total += 1
        if er == 12:
            ring12 += 1
        if es == 12:
            snap12 += 1
        if er == 12 and es == 12:
            both12 += 1
        if (rbx >> 4, rbz >> 4) == (sbx >> 4, sbz >> 4):
            ringeqsnap += 1
        if er == 12 and es != 12 and len(examples_ring_only) < 5:
            examples_ring_only.append((seed, (sbx, sbz), es))
        if total >= N:
            break

    print(f"sample {total} seeds  (mc={mc})")
    print(f"  12-eye at RING (biome-free) : {ring12:>5}  ({100*ring12/total:.1f}%)")
    print(f"  12-eye at SNAP (in-game)    : {snap12:>5}  ({100*snap12/total:.1f}%)")
    print(f"  12-eye at BOTH              : {both12:>5}  ({100*both12/total:.1f}%)")
    print(f"  ring chunk == snap chunk    : {ringeqsnap:>5}  ({100*ringeqsnap/total:.1f}%)")
    print()
    if ring12 >= 0.95 * total > snap12:
        print("=> your list is 12-eye at the RING position (biome-free shortcut).")
        print("   At the REAL in-game stronghold only", f"{100*snap12/total:.0f}%",
              "are truly 12-eye. SSA's eye finder filters to those.")
        print("   A few seeds that are 12 at ring but NOT in-game (tp to check):")
        for s, (bx, bz), es in examples_ring_only:
            print(f"     seed {s}  stronghold ({bx},{bz})  in-game eyes={es}")
    elif snap12 >= 0.95 * total:
        print("=> your list IS ~all 12-eye in game. The 34.5% earlier was a bug; paste this.")
    else:
        print("=> neither ~100%. Paste this output so we dig further.")


if __name__ == "__main__":
    main()
