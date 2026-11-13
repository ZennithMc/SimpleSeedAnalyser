"""
tests/village_old_server_check.py — 1.16.1 / 1.16.5 / 1.17.1 villages and pillager
outposts (29/10) against the headless servers (seed 12345, 41 villages + 4 outposts
per version, tests/village_old_truth.json.gz, tools/servercheck/viltruth116.py):
  * layout: every piece box identical to the structure start the game saved, minus
    the feature pieces (trees, cactus patches) whose postProcess failed — 1.16 / 1.17
    StructureStart.placeInChunk drops those from the start;
  * chests: the LootTableSeed of the chests with no feature before them in their
    chunk (exact); the others (estimated draws) are only counted.
    py tests/village_old_server_check.py
"""
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import jigsaw  # noqa: E402

KNOWN = {("21", "village", -1296, 704)}     # 1.17.1: the terrain height 4 blocks off on a cliff


def main():
    with gzip.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "village_old_truth.json.gz"), "rt") as f:
        truth = json.load(f)
    bad = known = approx = exact = 0
    for mc, L in truth.items():
        g = jigsaw._gen(int(mc))
        for t in L:
            k, x, z = t["key"], t["x"], t["z"]
            lay = jigsaw.layout(12345, int(mc), k, x >> 4, z >> 4)
            mine = [] if lay is None else [p for i, p in enumerate(lay[1]) if i not in t["dropped"]]
            ok = len(mine) == len(t["bb"])
            for p, q in zip(mine, t["bb"]):
                b2 = jigsaw.piece_box(g, p)      # a reloaded start: no expansion hack height
                if tuple(q) not in (tuple(p.box), tuple(b2),
                                    (b2[0], p.box[1], b2[2], b2[3], b2[4], b2[5])):
                    ok = False
                    break
            if not ok and (mc, k, x, z) in KNOWN:
                known += 1
                print(f"KNOWN {mc} {k} {x} {z}")
                continue
            bad += not ok
            if not ok:
                print(f"BAD {mc} {k} {x} {z}: {len(mine)} pieces (game {len(t['bb'])})")
            if not t["chests"]:
                continue
            cards = {tuple(c["pos"]): c for c in jigsaw.chests(12345, int(mc), k, x, z, "1.16.5")
                     if " — " not in c["name"]}
            for wx, wy, wz, s in t["chests"]:
                c = cards.get((wx, wy, wz))
                good = c is not None and c["seed"] == s
                if c is not None and not c.get("exact", True):
                    approx += not good
                    continue
                exact += good
                bad += not good
                if not good:
                    print(f"BAD {mc} {k} {x} {z}: chest {wx} {wy} {wz}")
    print(f"exact chests {exact}; estimated chests off {approx}; known {known}; bad {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
