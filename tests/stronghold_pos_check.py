"""
tests/stronghold_pos_check.py — first strongholds of seed 12345 as the headless
servers place them (portal frames found in the region files), 24/10.
    py tests/stronghold_pos_check.py
Cherry groves only count for the biome search from 1.21.9 (#stronghold_biased_to
of the 1.20.6 / 1.21.1 / 1.21.3 / 1.21.4 jars has no cherry_grove; 25w31a, MC-278965):
the 2nd stronghold moves one chunk in 1.20.6 → 1.21.8 (29/10: was 1.21.2).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cubiomes_wrap as cw  # noqa: E402

GAME = {
    24: [(-1676, 1988), (-556, -1644), (1764, 372)],     # 1.19.4
    25: [(-1676, 1988), (-540, -1644), (1764, 372)],     # 1.20.6
    26: [(-1676, 1988), (-540, -1644), (1764, 372)],     # 1.21.1
    28: [(-1676, 1988), (-540, -1644), (1764, 372)],     # 1.21.4 (29/10)
    32: [(-1676, 1988), (-556, -1644), (1764, 372)],     # 1.21.11
    34: [(-1676, 1988), (-556, -1644), (1764, 372)],     # 26.2
}


def main():
    bad = 0
    for mc, want in GAME.items():
        lib = cw.CubiomesLib()
        lib.setup_and_seed(mc, 12345)
        mine = [tuple(p) for p in lib.find_strongholds(max_count=len(want))]
        ok = mine == want
        bad += not ok
        print(("OK  " if ok else "BAD ") + f"mc {mc}: {mine}" + ("" if ok else f" (game {want})"))
    print("bad", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
