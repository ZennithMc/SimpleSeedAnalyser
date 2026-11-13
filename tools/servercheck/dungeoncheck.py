"""tools/servercheck/dungeoncheck.py — (30/10) the dungeon chests of a headless server's
world (already generated chunks, within RADIUS blocks of 0 0): every simple_dungeon
chest of a FULL chunk against dungeon_loot (both generation orders), and the chests
SSA predicts in the chunks whose 3×3 is FULL that the game does not have. Stores
tests/server_truth/<label>.json.gz "dungeons" = {chests: [[x, y, z, seed]], inner: [[cx, cz]]}.
  python3 dungeoncheck.py <server dir> <mc> <label>"""
import gzip, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import anvil, dungeon_loot as dl

RADIUS = 640
SEED = 12345


def predicted(mc, cx, cz, nb):
    return {(a, b, c): s for d in (dl.chunk_dungeons(SEED, mc, cx, cz, nb) or []) for a, b, c, s in d[6]}


def compare(mc, chests, inner):
    """(stats, lines) — chests [[x, y, z, seed]], inner [(cx, cz)]."""
    game = {(x, y, z): s for x, y, z, s in chests}
    st = {"ok": 0, "order": 0, "miss": 0, "extra": 0}
    lines = []
    for (x, y, z), s in game.items():
        hit = {}
        for nb in (True, False):
            hit[nb] = any(predicted(mc, (x >> 4) + a, (z >> 4) + b, nb).get((x, y, z)) == s
                          for a in (-1, 0, 1) for b in (-1, 0, 1))
        if hit[True]:
            st["ok"] += 1
        elif hit[False]:
            st["order"] += 1
        else:
            st["miss"] += 1
            lines.append(f"MISS  {x} {y} {z}")
    for cx, cz in inner:
        for k, s in predicted(mc, cx, cz, True).items():
            if game.get(k) != s:
                st["extra"] += 1
                lines.append(f"EXTRA {k[0]} {k[1]} {k[2]}")
    return st, lines


def main():
    d, mc, label = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    W = os.path.join(d, "w12345")
    t0 = time.time()
    full, chests = set(), []
    rc = (RADIUS >> 9) + 1
    for rx in range(-rc, rc):
        for rz in range(-rc, rc):
            for i in range(32):
                for j in range(32):
                    cx, cz = rx * 32 + i, rz * 32 + j
                    if (cx * 16 + 8) ** 2 + (cz * 16 + 8) ** 2 > RADIUS ** 2:
                        continue
                    c = anvil.chunk(W, cx, cz)
                    if c is None or anvil.status(c) != "full":
                        continue
                    full.add((cx, cz))
                    for be in anvil.block_entities(c):
                        if "simple_dungeon" in str(be.get("LootTable", "")):
                            chests.append([be["x"], be["y"], be["z"], be.get("LootTableSeed")])
    inner = sorted(c for c in full if all((c[0] + a, c[1] + b) in full for a in (-1, 0, 1) for b in (-1, 0, 1)))
    st, lines = compare(mc, chests, inner)
    for l in lines:
        print(l)
    print(label, "full chunks", len(full), "inner", len(inner), "game chests", len(chests), st)
    p = os.path.join(R, "tests", "server_truth", label + ".json.gz")
    if os.path.exists(p):
        t = json.load(gzip.open(p))
        t["dungeons"] = dict(chests=chests, inner=[list(c) for c in inner])
        with gzip.open(p, "wt") as f:
            json.dump(t, f, separators=(",", ":"))
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
