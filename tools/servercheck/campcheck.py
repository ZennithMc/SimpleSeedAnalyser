"""tools/servercheck/campcheck.py — (30/10) the abandoned camps of a 26.3 headless server
(mkserver.py): generates the chunks of the 30 nearest camps, then compares their pieces
(jigsaw.layout "abandoned_camp": type, boxes in order) and containers (chests, copper
chests, barrels: position + LootTableSeed) with the game, and stores them in
tests/server_truth/<label>.json.gz ("camps").
  python3 campcheck.py <server dir> <mc> <label> [--no-gen]"""
import gzip, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import anvil, srv, struct_info, cubiomes_wrap as cw, jigsaw as jg

NC = 30


def mine(mc, x, z):
    """{(x, y, z): seed} of the camp's loot containers (SSA)."""
    out = {}
    for c in jg.chests(12345, mc, "abandoned_camp", x, z, "26.3") or []:
        if c.get("seed") is not None and " — if chunk" not in c["name"]:
            out[tuple(c["pos"])] = c["seed"]
    return out


def main():
    d, mc, label = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    gen = "--no-gen" not in sys.argv
    lib = struct_info._lib(12345, mc, None, 0)
    pts = lib.find_structures_in_box(cw.Abandoned_Camp, -3000, -3000, 3000, 3000, verify_biome=True,
                                     max_count=1000)
    pts = sorted(pts, key=lambda p: p[0] ** 2 + p[1] ** 2)[:NC]
    chs = set()
    for x, z in pts:
        lay = jg.layout(12345, mc, "abandoned_camp", x >> 4, z >> 4)
        for p in (lay[1] if lay else []):
            b = p.box
            chs |= {(a, c) for a in range(b[0] >> 4, (b[3] >> 4) + 1) for c in range(b[2] >> 4, (b[5] >> 4) + 1)}
        chs.add((x >> 4, z >> 4))
    S = srv.Server(d)
    W = S.world
    done = lambda c: anvil.status(anvil.chunk(W, c[0], c[1], "")) == "full"
    t0 = time.time()
    if gen:
        S.start()
        todo = [c for c in sorted(chs) if not done(c)]
        print(label, "camps", pts, "chunks", len(chs), "todo", len(todo), flush=True)
        while todo and time.time() - t0 < 3000:
            part = todo[:32]
            S.cmd(*[f"forceload add {cx * 16} {cz * 16}" for cx, cz in part])
            for _ in range(60):
                time.sleep(5)
                S.save()
                if all(done(c) for c in part):
                    break
            S.cmd("forceload remove all")
            todo = [c for c in todo if not done(c)]
            print("  left", len(todo), flush=True)
        S.save()
        S.stop()
    game = {}
    for c in chs:
        ch = anvil.chunk(W, c[0], c[1], "")
        if anvil.status(ch) == "full":
            for be in anvil.block_entities(ch):
                if "abandoned_camp" in str(be.get("LootTable", "")):
                    game[(be["x"], be["y"], be["z"])] = be.get("LootTableSeed")
    truth, bad, ok, lok = [], 0, 0, 0
    allmine = {}
    for x, z in pts:
        s, _st = anvil.starts(W, x >> 4, z >> 4, "")
        g, gk = {}, None
        for k, v in (s or {}).items():
            if "abandoned_camp" in k.lower() and isinstance(v, dict) and v.get("Children"):
                g, gk = v, k
        gb = [list(c["BB"]) for c in g.get("Children", [])]
        lay = jg.layout(12345, mc, "abandoned_camp", x >> 4, z >> 4)
        mb = [list(p.box) for p in lay[1]] if lay else []
        mk = lay[0] if lay else None
        if gb != mb or (gk or "").split(":")[-1] != mk:
            bad += 1
            print("LAYOUT", x, z, gk, mk, len(gb), len(mb), gb[:2], mb[:2])
        else:
            lok += 1
        m = mine(mc, x, z)
        allmine.update(m)
        for k, sd in m.items():
            if game.get(k) == sd:
                ok += 1
            else:
                bad += 1
                print("CHEST", k, "game", game.get(k), "SSA", sd)
        truth.append(dict(x=x, z=z, type=gk, pieces=gb, chests=[[*k, game.get(k)] for k in m]))
    for k in game:
        if k not in allmine:
            bad += 1
            print("GAME CHEST NOT IN SSA", k, game[k])
    p = os.path.join(R, "tests", "server_truth", label + ".json.gz")
    if os.path.exists(p):
        t = json.load(gzip.open(p))
        t["camps"] = truth
        with gzip.open(p, "wt") as f:
            json.dump(t, f, separators=(",", ":"))
    print(f"layouts ok {lok}  chests ok {ok}  bad {bad}")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
