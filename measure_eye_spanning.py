"""
measure_eye_spanning.py — how often does the end-portal span >1 chunk?

This decides the native eye-finder design:
  - mostly single-chunk  -> a fast native single-chunk eye count is enough
    (exact for those; Python validates the few multi-chunk ones).
  - lots of multi-chunk  -> we must port the exact per-frame logic to C.

No recompile needed (pure Python, uses the existing DLLs for positions).

    py measure_eye_spanning.py <seedfile> <mc> [N]
    py measure_eye_spanning.py 12eye1.16_seedonly.seeds 20 3000
"""
import sys
import cubiomes_wrap as cw
import seedlist_io
from stronghold_eyes import get_stronghold_diagnostics


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "12eye1.16_seedonly.seeds"
    mc = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    N = int(sys.argv[3]) if len(sys.argv) > 3 else 3000

    lib = cw.make_worker_lib()
    span = {}          # n_chunks_spanned -> count
    eyes_dist = {}     # eyes -> count
    total = 0
    for seed in seedlist_io.iter_seedlist(path):
        lib.setup_and_seed(mc, int(seed))
        sh = lib.find_strongholds(max_count=1)
        if not sh:
            continue
        bx, bz = sh[0]
        d = get_stronghold_diagnostics(int(seed), bx, bz, mc)
        sp = d.get("n_chunks_spanned", 1)
        span[sp] = span.get(sp, 0) + 1
        eyes_dist[d["eyes"]] = eyes_dist.get(d["eyes"], 0) + 1
        total += 1
        if total >= N:
            break

    print(f"sample {total} seeds  (mc={mc})")
    print("portal spans N chunks:")
    for k in sorted(span):
        print(f"  {k} chunk(s): {span[k]:>6}  ({100*span[k]/total:.1f}%)")
    single = span.get(1, 0)
    print(f"=> single-chunk portals: {100*single/total:.1f}%  "
          f"(that's the max recall of a fast single-chunk native eye count)")
    print("eye counts in sample:", dict(sorted(eyes_dist.items())))


if __name__ == "__main__":
    main()
