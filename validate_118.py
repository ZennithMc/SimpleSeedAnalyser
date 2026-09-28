"""
validate_118.py — foundation check for the 1.18+ corrupted-portal work.

For a few normal 1.18 seeds, it computes the 1st stronghold's 12 end-portal frame
positions (cubiomes for the biome-accurate stronghold + gaider/Python BFS for the
frames, mc=22) and sends them to the REAL Minecraft 1.18.1 seed-checker.

Expected for normal seeds: every frame is END_PORTAL_FRAME → NATURAL=0, FORCED=0.
If instead frames come back "broken", SSA's 1.18+ frame X,Z,Y is off (gaider
moveBelowSeaLevel / minY) and we fix that BEFORE doing the ruined portal.

Run from the SimpleSeedAnalyser folder:  python validate_118.py
Needs: the seed-checker-1.18.1 project + JDK 17 (same setup as the 1.16 checker).
"""
import os
import cubiomes_wrap as cw
from stronghold_eyes import get_end_portal_frames
import checker_client

MC = 22  # cubiomes id for Java 1.18

# point the checker at the 1.18.1 project (in-memory, does NOT touch the 1.16 config)
_here = os.path.dirname(os.path.abspath(__file__))
checker_client._CHECKER_DIR = os.path.normpath(os.path.join(
    _here, "..", "Sources to relie on",
    "seed-checker-1.18.1", "seed-checker-1.18.1"))

# a handful of arbitrary seeds (no corruption expected — just frame correctness)
SEEDS = [1, 2, 12, 123, 1000, 8040347553, -4242, 777777]


def frames_for(lib, seed):
    lib.setup_and_seed(MC, seed)
    shs = lib.find_strongholds(max_count=1)
    if not shs:
        return None
    bx, bz = shs[0]
    fr = get_end_portal_frames(seed, bx, bz, MC)
    if fr is None:
        return None
    frames, ex, ez, fy = fr
    return frames, bx, bz


def main():
    print("Checker dir :", checker_client._CHECKER_DIR)
    print("Available   :", checker_client.is_available(),
          "  (gradlew + JDK17 found)")
    lib = cw.make_worker_lib()
    reqs = []
    for s in SEEDS:
        f = frames_for(lib, s)
        if f is None:
            print(f"seed {s}: no stronghold/frames")
            continue
        frames, bx, bz = f
        reqs.append((s, frames, bx >> 4, bz >> 4))
        print(f"seed {s}: stronghold ({bx},{bz})  frame0={frames[0]}")
    print("\nVerifying against real Minecraft 1.18.1 (first run ~25 s warmup)...\n")
    res = checker_client.verify(reqs, timeout=300.0)
    ok = True
    for (s, frames, rox, roz) in reqs:
        r = res.get(s)
        if r is None:
            print(f"seed {s}: NO RESULT"); ok = False; continue
        nat, forced = r.get("natural", -1), r.get("forced", -1)
        tag = "OK" if (nat == 0 and forced == 0) else "!! FRAMES WRONG"
        if nat or forced:
            ok = False
        print(f"seed {s}: NATURAL(broken)={nat}  FORCED={forced}   {tag}")
    print("\n==>", "All frames correct — gaider 1.18+ frames VALID."
          if ok else "Some frames wrong — SSA 1.18+ frame X,Z,Y needs a fix.")


if __name__ == "__main__":
    main()
