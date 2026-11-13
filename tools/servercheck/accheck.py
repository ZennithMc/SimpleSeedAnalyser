"""tools/servercheck/accheck.py — (30/10) the ancient cities of a headless server
(mkserver.py): generates the chunks of the chests of the 6 nearest cities, then
compares their pieces (ancient_city_loot: jigsaw boxes, in order) and chests with
the game, and stores them in tests/server_truth/<label>.json.gz ("ancient_cities").
  python3 accheck.py <server dir> <mc> <label> [--no-gen]"""
import gzip, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import anvil, srv, struct_info, cubiomes_wrap as cw, ancient_city_loot as bl

NF = 6


def main():
    d, mc, label = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    gen = "--no-gen" not in sys.argv
    lib = struct_info._lib(12345, mc, None, 0)
    pts = lib.find_structures_in_box(cw.Ancient_City, -3000, -3000, 3000, 3000, verify_biome=True, max_count=1000)
    pts = sorted(pts, key=lambda p: p[0] ** 2 + p[1] ** 2)[:NF]
    chs = set()
    for x, z in pts:
        chs.add((x >> 4, z >> 4))
        for X, _Y, Z, _s, _t, _l, _w in [c for c in bl.chest_list(12345, mc, x, z) if c[4]]:
            chs.add((X >> 4, Z >> 4))
    S = srv.Server(d)
    W = S.world
    done = lambda c: anvil.status(anvil.chunk(W, c[0], c[1], "")) == "full"
    t0 = time.time()
    if gen:
        S.start()
        todo = [c for c in sorted(chs) if not done(c)]
        print(label, "cities", pts, "chunks", len(chs), "todo", len(todo), flush=True)
        while todo and time.time() - t0 < 3000:
            part = todo[:32]
            S.cmd(*[f"execute in minecraft:overworld run forceload add {cx * 16} {cz * 16}" for cx, cz in part])
            for _ in range(60):
                time.sleep(5)
                S.save()
                if all(done(c) for c in part):
                    break
            S.cmd("execute in minecraft:overworld run forceload remove all")
            todo = [c for c in todo if not done(c)]
            print("  left", len(todo), flush=True)
        S.save()
        S.stop()
    game = {}
    for c in chs:
        ch = anvil.chunk(W, c[0], c[1], "")
        if anvil.status(ch) == "full":
            for be in anvil.block_entities(ch):
                if "ancient_city" in str(be.get("LootTable", "")):
                    game[(be["x"], be["y"], be["z"])] = be.get("LootTableSeed")
    truth, bad, ok, lok = [], 0, 0, 0
    for x, z in pts:
        s, _st = anvil.starts(W, x >> 4, z >> 4, "")
        g = {}
        for k, v in (s or {}).items():
            if "ancient_city" in k.lower() and isinstance(v, dict) and v.get("Children"):
                g = v
        gb = [list(c["BB"]) for c in g.get("Children", [])]
        lay = bl.layout(12345, mc, x >> 4, z >> 4)
        mine = [list(p.box) for p in lay[0]] if lay else []
        if gb != mine:
            bad += 1
            i = next((i for i in range(min(len(gb), len(mine))) if gb[i] != mine[i]), min(len(gb), len(mine)))
            print("LAYOUT", x, z, len(gb), len(mine), "first diff", i, gb[i:i + 2], mine[i:i + 2])
        else:
            lok += 1
        cl = [c for c in bl.chest_list(12345, mc, x, z) if c[4]]
        for X, Y, Z, sd, _t, _l, _w in cl:
            if game.get((X, Y, Z)) == sd:
                ok += 1
            else:
                bad += 1
                print("CHEST", X, Y, Z, "game", game.get((X, Y, Z)), "SSA", sd)
        truth.append(dict(x=x, z=z, pieces=gb, chests=[[X, Y, Z, game.get((X, Y, Z))] for X, Y, Z, _s, _t, _l, _w in cl]))
    mineall = {(a, b, c) for x, z in pts for a, b, c, _s, _t, _l, _w in [c for c in bl.chest_list(12345, mc, x, z) if c[4]]}
    for k in game:
        if k not in mineall:
            bad += 1
            print("GAME CHEST NOT IN SSA", k)
    p = os.path.join(R, "tests", "server_truth", label + ".json.gz")
    if os.path.exists(p):
        t = json.load(gzip.open(p))
        t["ancient_cities"] = truth
        with gzip.open(p, "wt") as f:
            json.dump(t, f, separators=(",", ":"))
    print(f"layouts ok {lok}  chests ok {ok}  bad {bad}")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
