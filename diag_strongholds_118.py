"""
diag_strongholds_118.py — pin down the "1.18 strongholds are wrong" report.

Runs the SAME code path the GUI uses (cubiomes find_strongholds for the
position, then get_stronghold_diagnostics for the portal /tp), but in a FRESH
Python process. So:

  - if the coords printed here are CORRECT in-game  -> the GUI was running stale
    Python (just fully restart SSA);
  - if the coords printed here are ALSO wrong in-game -> a real bug remains, and
    we know whether it's the X,Z (cubiomes position) or only the Y (offset).

Usage:
    python diag_strongholds_118.py            # default seed 1000, version 1.18
    python diag_strongholds_118.py 17713 22   # seed 17713, mc 22 (1.18)
    python diag_strongholds_118.py 17713 28   # seed 17713, mc 28 (1.21.4)

mc enum: 1.16=20, 1.17.1=21, 1.18=22, 1.19=24, 1.20=25, 1.21.1=26, 1.21.4+=28.
"""
import sys
import cubiomes_wrap as cw
import stronghold_eyes as se


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    mc = int(sys.argv[2]) if len(sys.argv) > 2 else 22

    lib = cw.make_worker_lib()
    lib.setup_and_seed(mc, seed)
    shs = lib.find_strongholds(max_count=16, radius_blocks=None)

    print(f"seed={seed}  mc={mc}")
    print(f"cubiomes find_strongholds (first {len(shs)}): {shs}")
    print("-" * 64)
    worst = 0.0
    for (sx, sz) in shs:
        d = se.get_stronghold_diagnostics(seed, sx, sz, mc)
        cx, fy, cz = d["portal_pos"]
        dist = ((cx - sx) ** 2 + (cz - sz) ** 2) ** 0.5
        worst = max(worst, dist)
        flag = "   <<< BUG (>200)" if dist > 200 else ""
        print(f"stronghold origin (cubiomes) : X={sx}  Z={sz}{flag}")
        print(f"  portal room chunk          : {d['portal_room_chunk']}")
        print(f"  direction                  : {d['direction']}")
        print(f"  portal centre /tp          : /tp @s {cx}.5 {fy} {cz}.5")
        print(f"  stronghold -> portal dist  : {dist:.0f} blocks "
              f"(normal: ~10-120; >200 = BUG)")
        print(f"  frame Y                     : {d.get('frame_y')}")
        print(f"  dungeon low / high Y        : {d.get('lowest_y')} / {d.get('highest_y')}")
        print(f"  eyes                        : {d['eyes']} / 12")
        print("-" * 64)

    print(f"WORST stronghold->portal distance: {worst:.0f} blocks")
    if worst > 200:
        print(">>> The shared get_stronghold_diagnostics ITSELF produces a far portal")
        print("    for this seed -> the bug is in the generation/cubiomes position.")
    else:
        print(">>> All portals are near their stronghold here. If SSA's GUI shows a")
        print("    far portal for the same seed, the bug is in the GUI plumbing")
        print("    (wrong seed/position passed to get_stronghold_diagnostics).")
    print()
    print("In game: /tp to a 'portal centre' above. You should land on the end")
    print("portal frames (the 5x5 ring).")


if __name__ == "__main__":
    main()
