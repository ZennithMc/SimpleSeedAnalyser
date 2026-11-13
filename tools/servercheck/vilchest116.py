"""tools/servercheck/vilchest116.py — SSA's 1.16 / 1.17 village / outpost chest seeds vs
the LootTableSeed a server wrote (full chunks only):
    python3 vilchest116.py <world> <mc> <pos.json> <ver>"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anvil, jigsaw  # noqa: E402

W, MC, VER = sys.argv[1], int(sys.argv[2]), sys.argv[4]
pos = json.load(open(sys.argv[3]))[str(MC)]
ok = bad = skip = approx_ok = approx_bad = 0
for key, L in pos.items():
    for x, z in L:
        cards = jigsaw.chests(12345, MC, key, x, z, VER) or []
        for c in cards:
            if c["seed"] is None or " — " in c["name"]:
                continue
            wx, wy, wz = c["pos"]
            ch = anvil.chunk(W, wx >> 4, wz >> 4)
            lv = None if ch is None else ch.get("Level", ch)
            if lv is None or lv.get("Status", "").split(":")[-1] != "full":
                skip += 1
                continue
            be = {(t["x"], t["y"], t["z"]): t for t in lv.get("TileEntities", lv.get("block_entities", []))}
            t = be.get((wx, wy, wz))
            got = None if t is None else t.get("LootTableSeed")
            good = got == c["seed"]
            if c.get("exact", True):
                ok += good; bad += not good
            else:
                approx_ok += good; approx_bad += not good
            if not good:
                print(("BAD " if c.get("exact", True) else "APPROX ") +
                      f"{key} {x} {z}: chest {wx} {wy} {wz} ssa {c['seed']} game {got}")
print("exact ok", ok, "bad", bad, "| after features ok", approx_ok, "bad", approx_bad, "| not generated", skip)
