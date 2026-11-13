"""tools/servercheck/wellcheck.py — (30/10) desert wells on a headless server
(mkserver.py): the well attempts of the box near a desert (SSA says a well, cubiomes
says one, or desert around), the 3×3 chunks of each generated, the wells found in the
game (a sandstone block with 8 sandstone slabs around it: the roof, 4 above the sand)
against desert_well.exists and cubiomes (biome check). Stores
tests/server_truth/<label>.json.gz "wells" = [[x, z, game Y or None]].
  python3 wellcheck.py <server dir> <mc> <label> [R] [--no-gen]"""
import gzip, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import anvil, srv, struct_info, cubiomes_wrap as cw, desert_well as dw

N = 30
SEED = 12345


def game_wells(W, chunks):
    S, T = set(), set()
    for cx, cz in chunks:
        c = anvil.chunk(W, cx, cz)
        if anvil.status(c) != "full":
            continue
        for x, y, z, n, _p in anvil.blocks(c, ("sandstone_slab", "sandstone")):
            n = n.split(":")[-1]
            if n == "sandstone_slab":
                S.add((x, y, z))
            elif n == "sandstone":
                T.add((x, y, z))
    return {(x, z): y - 4 for (x, y, z) in T
            if all((x + a, y, z + b) in S for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b)}


def main():
    d, mc, label = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    rad = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else (6000 if mc <= 21 else 12000)
    gen = "--no-gen" not in sys.argv
    lib = struct_info._lib(SEED, mc, None, 0)
    att = dw.attempts(SEED, mc, -rad, -rad, rad, rad)
    cv = set(map(tuple, lib.find_structures_in_box(cw.Desert_Well, -rad, -rad, rad, rad, verify_biome=True,
                                                    max_count=100000)))
    deserts = set(dw.DESERTS_116)
    pick = []
    for x, z in att:
        y = dw.exists(SEED, mc, x, z)
        near = any(int(lib.get_biome_at(x + a, z + b, scale=4, y=16)) in deserts
                   for a in (-24, 0, 24) for b in (-24, 0, 24))
        if (y not in (None, False)) or (x, z) in cv or near:
            pick.append((x, z))
    pick = sorted(pick, key=lambda p: p[0] ** 2 + p[1] ** 2)[:N]
    chs = sorted({((x >> 4) + a, (z >> 4) + b) for x, z in pick for a in (-1, 0, 1) for b in (-1, 0, 1)})
    S = srv.Server(d)
    W = S.world
    done = lambda c: anvil.status(anvil.chunk(W, c[0], c[1], "")) == "full"
    t0 = time.time()
    print(label, "attempts", len(pick), "chunks", len(chs), flush=True)
    if gen:
        S.start()
        todo = [c for c in chs if not done(c)]
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
    game = game_wells(W, chs)
    st = {"ok": 0, "bad": 0, "cubiomes ok": 0}
    truth = []
    for x, z in pick:
        g = game.get((x, z))
        y = dw.exists(SEED, mc, x, z)
        mine = None if y in (None, False) else y
        ok = (g is None) == (mine is None)
        st["ok" if ok else "bad"] += 1
        st["cubiomes ok"] += ((x, z) in cv) == (g is not None)
        if not ok:
            print("WELL", x, z, "game", g, "SSA", y, "py", dw._exists_py(SEED, mc, x, z), "cubiomes", (x, z) in cv)
        truth.append([x, z, g])
    extra = [k for k in game if k not in set(pick)]
    print("game wells", sum(1 for t in truth if t[2] is not None), "other wells found", extra)
    p = os.path.join(R, "tests", "server_truth", label + ".json.gz")
    if os.path.exists(p):
        t = json.load(gzip.open(p))
        t["wells"] = truth
        with gzip.open(p, "wt") as f:
            json.dump(t, f, separators=(",", ":"))
    print(st)
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
