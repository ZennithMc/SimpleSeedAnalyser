"""tools/servercheck/mansioncheck.py — (30/10) woodland mansions on a headless server
(mkserver.py): a world of seed <seed> (copy of the server dir, world w<seed>), the 4
mansions nearest to 0 0 generated (11 × 11 chunks each), their pieces and loot chests
compared with mansion_loot, truth in tests/server_truth/mansions.json.gz.
  python3 mansioncheck.py <server dir> <mc> <label> <seed> [--no-gen]"""
import gzip, json, os, shutil, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import anvil, srv, struct_info, cubiomes_wrap as cw, mansion_loot as ml

ROT = {"NONE": 0, "CLOCKWISE_90": 1, "CLOCKWISE_180": 2, "COUNTERCLOCKWISE_90": 3}
MI = {"NONE": 0, "LEFT_RIGHT": 1, "FRONT_BACK": 2}


def main():
    base, mc, label, seed = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
    gen = "--no-gen" not in sys.argv
    if seed == 12345:
        D, wname = base, "w12345"
    else:
        D, wname = f"{base}_s{seed}", f"w{seed}"
        os.makedirs(D, exist_ok=True)
        for f in ("cp.txt", "eula.txt"):
            shutil.copy(f"{base}/{f}", D)
        open(f"{D}/start.sh", "w").write(open(f"{base}/start.sh").read().replace(base, D))
        p = open(f"{base}/server.properties").read().replace("level-seed=12345", f"level-seed={seed}") \
            .replace("level-name=w12345", f"level-name={wname}")
        open(f"{D}/server.properties", "w").write(p)
    lib = struct_info._lib(seed, mc, None, 0)
    pts = lib.find_structures_in_box(cw.Mansion, -7000, -7000, 7000, 7000, verify_biome=True, max_count=50)
    pts = sorted(pts, key=lambda p: p[0] ** 2 + p[1] ** 2)[:4]
    S = srv.Server(D)
    S.world = W = os.path.join(D, wname)
    t0 = time.time()
    if gen and pts:
        chs = {((x >> 4) + a, (z >> 4) + b) for x, z in pts for a in range(-5, 6) for b in range(-5, 6)}
        S.start()
        S.generate(sorted(chs), full=True, batch=48, timeout=3000)
        S.save()
        S.stop()
    truth, ok, bad = [], 0, 0
    for x, z in pts:
        cx, cz = x >> 4, z >> 4
        s, _st = anvil.starts(W, cx, cz)
        g = (s or {}).get("minecraft:mansion") or (s or {}).get("mansion") or (s or {}).get("Mansion")
        if not g:
            continue
        gp = [[c["Template"].split("/")[-1], [c["TPX"], c["TPY"], c["TPZ"]], ROT[c["Rot"]], MI[c.get("Mi", "NONE")]]
              for c in g["Children"]]
        lay = ml.layout(seed, mc, cx, cz)
        mp = [[n, list(p), r, m] for n, p, r, m in lay[2]] if lay else []
        if gp == mp:
            ok += 1
        else:
            bad += 1
            print("LAYOUT", x, z)
        game = {}
        for a in range(-5, 6):
            for b in range(-5, 6):
                ch = anvil.chunk(W, cx + a, cz + b)
                if anvil.status(ch) == "full":
                    for be in anvil.block_entities(ch):
                        if "woodland_mansion" in str(be.get("LootTable", "")):
                            game[(be["x"], be["y"], be["z"])] = be.get("LootTableSeed")
        mine = ml.chest_list(seed, mc, cx, cz)
        for X, Y, Z, sd, n in mine:
            if game.get((X, Y, Z)) == sd:
                ok += 1
            else:
                bad += 1
                print("CHEST", X, Y, Z, n, game.get((X, Y, Z)), sd)
        for k in game:
            if k not in {(a, b_, c) for a, b_, c, _s, _n in mine}:
                bad += 1
                print("GAME CHEST NOT IN SSA", k)
        truth.append(dict(cx=cx, cz=cz, pieces=gp, chests=[[k[0], k[1], k[2], v] for k, v in sorted(game.items())]))
    p = os.path.join(R, "tests", "server_truth", "mansions.json.gz")
    allt = json.load(gzip.open(p)) if os.path.exists(p) else {}
    allt.setdefault(label, {"mc": mc})[str(seed)] = truth
    with gzip.open(p, "wt") as f:
        json.dump(allt, f, separators=(",", ":"))
    print(f"ok {ok}  bad {bad}")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
