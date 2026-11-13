"""tools/servercheck/endcheck.py — (30/10) the End cities of a headless server
(mkserver.py): generates the chunks of the 8 nearest cities in the End, then
compares their pieces (libcubiomes getEndCityPieces) and chests (end_city_loot)
with the game, and stores them in tests/server_truth/<label>.json.gz ("end_cities").
  python3 endcheck.py <server dir> <mc> <label> [--no-gen]"""
import gzip, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import anvil, srv, struct_info, cubiomes_wrap as cw, end_city_loot as ecl


def main():
    d, mc, label = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    gen = "--no-gen" not in sys.argv
    lib = struct_info._lib(12345, mc, None, 1)
    pts = lib.find_structures_in_box(cw.End_City, -3000, -3000, 3000, 3000, verify_biome=True, max_count=1000)
    pts = sorted(pts, key=lambda p: p[0] ** 2 + p[1] ** 2)[:8]
    chs = set()
    for x, z in pts:
        for (x0, z0, x1, z1, *_r) in lib.struct_pieces("end_city", x, z):
            chs |= {(a, b) for a in range(x0 >> 4, (x1 >> 4) + 1) for b in range(z0 >> 4, (z1 >> 4) + 1)}
        chs.add((x >> 4, z >> 4))
    S = srv.Server(d)
    W = S.world
    done = lambda c: anvil.status(anvil.chunk(W, c[0], c[1], "DIM1")) == "full"
    t0 = time.time()
    if gen:
        S.start()
        todo = [c for c in sorted(chs) if not done(c)]
        while todo and time.time() - t0 < 3000:
            part = todo[:32]
            S.cmd(*[f"execute in minecraft:the_end run forceload add {cx * 16} {cz * 16}" for cx, cz in part])
            for _ in range(60):
                time.sleep(5)
                S.save()
                if all(done(c) for c in part):
                    break
            S.cmd("execute in minecraft:the_end run forceload remove all")
            todo = [c for c in todo if not done(c)]
        S.save()
        S.stop()
    game = {}
    for c in chs:
        ch = anvil.chunk(W, c[0], c[1], "DIM1")
        if anvil.status(ch) == "full":
            for be in anvil.block_entities(ch):
                if "end_city" in str(be.get("LootTable", "")):
                    game[(be["x"], be["y"], be["z"])] = be.get("LootTableSeed")
    truth, bad, ok = [], 0, 0
    for x, z in pts:
        s, _st = anvil.starts(W, x >> 4, z >> 4, "DIM1")
        g = (s or {}).get("minecraft:end_city") or (s or {}).get("endcity") or (s or {}).get("EndCity") or {}
        gb = [list(c["BB"]) for c in g.get("Children", [])]
        mine = [[p[0], p[6], p[1], p[2], p[7], p[3]] for p in ecl.pieces(12345, mc, x, z)]
        if gb != mine:
            bad += 1
            print("LAYOUT", x, z, len(gb), len(mine))
        cl = ecl.chest_list(12345, mc, x, z)
        for X, Y, Z, sd, _t in cl:
            if game.get((X, Y, Z)) == sd:
                ok += 1
            else:
                bad += 1
                print("CHEST", X, Y, Z, "game", game.get((X, Y, Z)), "SSA", sd)
        truth.append(dict(x=x, z=z, pieces=gb, chests=[[X, Y, Z, game.get((X, Y, Z))] for X, Y, Z, _s, _t in cl]))
    mineall = {(a, b, c) for x, z in pts for a, b, c, _s, _t in ecl.chest_list(12345, mc, x, z)}
    for k in game:
        if k not in mineall:
            bad += 1
            print("GAME CHEST NOT IN SSA", k)
    p = os.path.join(R, "tests", "server_truth", label + ".json.gz")
    if os.path.exists(p):
        t = json.load(gzip.open(p))
        t["end_cities"] = truth
        with gzip.open(p, "wt") as f:
            json.dump(t, f, separators=(",", ":"))
    print(f"chests ok {ok}  bad {bad}")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
