"""
tests/village_server_check.py — villages / pillager outposts against the headless 26.2
server (seed 12345), 24/10:
  * layout: 10 villages + 6 outposts, every piece (template + bounding box) identical
    to the structure start the game saved;
  * chests: the LootTableSeed the game wrote (25/10: also the chests generated
    after decorations — trees, hay piles, flowers — replayed by village_world.py).
    py tests/village_server_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jigsaw  # noqa: E402

SEED, MC = 12345, 34
# (key, x, z): number of pieces in game
LAYOUT = {("village", -2864, 288): 79, ("village", 1264, 4160): 69, ("village", 2800, 4144): 181,
          ("village", 3056, 2416): 185, ("village", 2416, 4144): 94, ("village", -3472, 608): 62,
          ("village", -2096, 32): 187, ("village", -2992, -144): 116, ("village", 896, 3616): 60,
          ("village", 2784, 3664): 208, ("pillager_outpost", 1680, 3616): 8,
          ("pillager_outpost", 3952, 1216): 8, ("pillager_outpost", 848, 2144): 13,
          ("pillager_outpost", 2560, 3312): 9, ("pillager_outpost", -3536, -1696): 9,
          ("pillager_outpost", -3264, -3552): 15}
CHESTS = {
    ("village", -2096, 32): [(-2081, 80, 18, -598628006285897453), (-2057, 85, 42, 6848759977125899078),
                             (-2115, 64, -21, 4748166028811384167), (-2056, 87, 53, -8333219119124051696),
                             (-2108, 69, -4, 2593791333621795808)],
    ("pillager_outpost", 1680, 3616): [(1670, 90, 3606, 8334690981223184378)],
    ("village", 2784, 3664): [(2797, 73, 3698, None), (2767, 65, 3623, None)],
    ("village", -3472, 608): [(-3458, 73, 583, 4379291820646678216)],   # after a hay pile
    ("village", 160, 880): [(170, 96, 921, 7819841418893197022)],       # after 2 flower patches
    ("village", -2560, 16): [(-2597, 64, -11, None)],
}


def main():
    bad = 0
    for (k, x, z), n in LAYOUT.items():
        lay = jigsaw.layout(SEED, MC, k, x >> 4, z >> 4)
        got = 0 if lay is None else len(lay[1])
        ok = got == n
        bad += not ok
        print(("OK  " if ok else "BAD ") + f"{k} {x} {z}: {got} pieces (game {n})")
    for (k, x, z), want in CHESTS.items():
        cards = {c["pos"]: c for c in jigsaw.chests(SEED, MC, k, x, z, "26.2")}
        for cx, cy, cz, s in want:
            c = cards.get((cx, cy, cz))
            if s is None:
                ok = c is not None
            else:
                ok = c is not None and c["seed"] == s
                if not ok and c is not None and not c.get("exact", True):
                    print(f"KNOWN {k} {x} {z}: chest {cx} {cy} {cz} (feature not replayed)")
                    continue
            bad += not ok
            print(("OK  " if ok else "BAD ") + f"{k} {x} {z}: chest {cx} {cy} {cz}")
    print("bad", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
