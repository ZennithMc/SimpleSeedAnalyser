"""tools/servercheck/viltruth116.py — writes tests/village_old_truth.json.gz from server
worlds: per start, the game's piece boxes, the SSA feature pieces the game
dropped (failed postProcess), and the loot seeds of the chests in full chunks.
    python3 viltruth116.py <pos.json> <mc>=<world> ..."""
import gzip, json, os, sys
R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, R)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anvil, jigsaw  # noqa: E402

pos = json.load(open(sys.argv[1]))
out = {}
for a in sys.argv[2:]:
    mc, W = a.split("=", 1)
    g = jigsaw._gen(int(mc))
    res = out[mc] = []
    for key, L in pos[mc].items():
        for x, z in L:
            st = {k.split(':')[-1].lower(): v for k, v in anvil.starts(W, x >> 4, z >> 4)[0].items()}
            gp = st[key]["Children"]
            lay = jigsaw.layout(12345, int(mc), key, x >> 4, z >> 4)[1]
            i = j = 0
            drop = []
            while i < len(lay):
                p = lay[i]
                b2 = jigsaw.piece_box(g, p)
                if j < len(gp) and tuple(gp[j]["BB"]) in (tuple(p.box), tuple(b2),
                                                         (b2[0], p.box[1], b2[2], b2[3], b2[4], b2[5])):
                    j += 1
                elif p.el.kind == "feature":
                    drop.append(i)
                i += 1
            chests = []
            for c in jigsaw.chests(12345, int(mc), key, x, z, "1.16.5") or []:
                if c["seed"] is None or " — " in c["name"]:
                    continue
                wx, wy, wz = c["pos"]
                ch = anvil.chunk(W, wx >> 4, wz >> 4)
                lv = None if ch is None else ch.get("Level", ch)
                if lv is None or lv.get("Status", "").split(":")[-1] != "full":
                    continue
                be = {(t["x"], t["y"], t["z"]): t for t in lv.get("TileEntities", [])}
                t = be.get((wx, wy, wz))
                if t is not None and "LootTableSeed" in t:
                    chests.append([wx, wy, wz, t["LootTableSeed"]])
            res.append(dict(key=key, x=x, z=z, bb=[q["BB"] for q in gp], dropped=drop, chests=chests))
    print(mc, len(res))
with gzip.open(os.path.join(R, "tests", "village_old_truth.json.gz"), "wt") as f:
    json.dump(out, f, separators=(",", ":"))
