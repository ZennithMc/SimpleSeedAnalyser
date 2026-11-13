"""
tests/shipwreck_server_check.py — shipwreck chests read on the headless 26.2 server
(seed 12345, region files: position + LootTableSeed), 24/10.
    py tests/shipwreck_server_check.py
Ocean wrecks: one card per chest, seed and Y exact (the 560 1696 one sits on an
iceberg in game: seed exact, Y not checked). Beached ones (26.1+): the seed is one
of the two cards (which chunk generated first).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scattered_loot as sc  # noqa: E402

SEED, MC = 12345, 34
# (wreck x, z): [(chest x, y, z, LootTableSeed)]   y None = not checked
GAME = {
    (-1904, 1536): [(-1909, 51, 1549, 3794908547604281807), (-1902, 54, 1550, -3431707044562448604),
                    (-1893, 54, 1551, -4542775065930366083)],
    (-128, 32): [(-131, 58, 46, 8658717388762826234)],
    (176, 160): [(181, 47, 168, 2561330585687988488)],
    (688, -1920): [(695, 44, -1907, 8818496181961779220), (702, 47, -1906, 6082368270976800088)],
    (1296, 816): [(1309, 50, 833, -4773086392172655047), (1302, 53, 832, 4034553710122954489),
                  (1293, 53, 831, -3859562552695887675)],
    (-1264, 2208): [(-1254, 49, 2223, 5059413838891043720), (-1263, 49, 2224, -35153855253965320),
                    (-1269, 51, 2225, -439252855027964994)],
    (144, 2160): [(155, 58, 2175, -1262200643009732227)],
    (560, 1696): [(555, None, 1710, -8354161754150336818), (561, None, 1709, -4226792007241181305)],
    # beached
    (-2288, 1456): [(-2293, 59, 1470, -4682660218803542111), (-2287, 61, 1469, 3858875878455126033)],
    (1824, -2128): [(1828, 53, -2107, -5209449946167302536), (1827, 53, -2116, 3493660618573327962),
                    (1826, 55, -2122, 7122560186590135051)],
    (1360, -3776): [(1363, 60, -3752, 6762688048777965466), (1365, 61, -3768, -6506930525857742710),
                    (1366, 61, -3757, -5210042680424973110)],
    (2336, -2832): [(2334, 53, -2817, -4040066386561099088), (2343, 53, -2818, 2385088463132088075),
                    (2349, 55, -2819, -6888997494980022405)],
}


def main():
    bad = 0
    for (x, z), want in GAME.items():
        cards = sc.shipwreck(SEED, MC, x, z, "26.2")
        for gx, gy, gz, gs in want:
            mine = [c for c in cards if (c["pos"][0], c["pos"][2]) == (gx, gz)]
            hit = [c for c in mine if c["seed"] == gs]
            ok = bool(hit)
            yok = gy is None or any(c["pos"][1] in (gy, None) for c in hit) or \
                any(c["pos"][1] == gy for c in mine)
            bad += not (ok and yok)
            print(("OK  " if ok and yok else "BAD ") + f"wreck {x} {z}: chest {gx} {gy} {gz}"
                  + ("" if ok else " seed") + ("" if yok else f" Y {[c['pos'][1] for c in mine]}"))
    print("bad", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
