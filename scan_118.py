"""
scan_118.py — find the REAL end-portal-frame Y for a 1.18 seed, to measure the
offset between SSA's predicted frames and reality.

For each of SSA's 12 predicted frame (x,z), it asks the real 1.18.1 checker for
the block at every Y in a full column. The positions that come back as
END_PORTAL_FRAME are the real frames → we print them + the (dx,dy,dz) offset.
"""
import os
import cubiomes_wrap as cw
from stronghold_eyes import get_end_portal_frames
import checker_client

MC = 22
SEED = 1
YLO, YHI = -64, 100

_here = os.path.dirname(os.path.abspath(__file__))
checker_client._CHECKER_DIR = os.path.normpath(os.path.join(
    _here, "..", "Sources to relie on",
    "seed-checker-1.18.1", "seed-checker-1.18.1"))


def main():
    lib = cw.make_worker_lib()
    lib.setup_and_seed(MC, SEED)
    bx, bz = lib.find_strongholds(max_count=1)[0]
    frames, ex, ez, fy = get_end_portal_frames(SEED, bx, bz, MC)
    print(f"seed {SEED}: stronghold ({bx},{bz})  SSA portal center ~({ex},{fy},{ez})")
    print("SSA frames:", frames)

    # build a full-column scan at each frame (x,z)
    xz = sorted({(x, z) for (x, y, z) in frames})
    sent = [(x, y, z) for (x, z) in xz for y in range(YLO, YHI + 1)]
    res = checker_client.verify([(SEED, sent, bx >> 4, bz >> 4)], timeout=400.0)
    r = res.get(SEED, {})
    broken = {(x, y, z) for (x, y, z, b) in r.get("broken", [])}
    real = [p for p in sent if p not in broken]   # END_PORTAL_FRAME positions

    print(f"\nReal END_PORTAL_FRAME positions found: {len(real)}")
    for p in sorted(real):
        print("   ", p)
    if real:
        ry = {p[1] for p in real}
        print(f"\nReal frame Y level(s): {sorted(ry)}   |  SSA frame Y: {fy}")
        print(f"=> Y offset (real - SSA): {sorted(y - fy for y in ry)}")
        # match X,Z to see if X,Z is correct
        ssa_xz = {(x, z) for (x, y, z) in frames}
        real_xz = {(x, z) for (x, y, z) in real}
        print(f"X,Z match: SSA∩real = {len(ssa_xz & real_xz)}/12  "
              f"(12 = X,Z correct, only Y off)")
    else:
        print("\nNo frames in these columns → X,Z is ALSO off (not just Y).")


if __name__ == "__main__":
    main()
