"""tools/servercheck/ycheck.py — (31/10) the Y of the scattered-structure chests (jungle
temple, igloo basement, shipwreck, ocean ruin, buried treasure) on a headless server:
the structures nearest to 0 0 generated, their chests (LootTableSeed) read back, the
game Y against structure_loot.chests.
  python3 ycheck.py <server dir> <mc> [N per kind] [--no-gen]"""
import os, sys, time, collections
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import anvil, srv, struct_info, cubiomes_wrap as cw, structure_loot as sl

SEED = 12345
KINDS = (("jungle_temple", cw.Jungle_Temple), ("igloo", cw.Igloo), ("shipwreck", cw.Shipwreck),
         ("ocean_ruin", cw.Ocean_Ruin), ("buried_treasure", cw.Treasure))


def main():
    d, mc = sys.argv[1], int(sys.argv[2])
    n = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 6
    lib = struct_info._lib(SEED, mc, None, 0)
    work = []
    for key, st in KINDS:
        pts = lib.find_structures_in_box(st, -5000, -5000, 5000, 5000, verify_biome=True, max_count=2000)
        pts = sorted(pts, key=lambda p: p[0] ** 2 + p[1] ** 2)
        k = 0
        for x, z in pts:
            cards = [c for c in (sl.chests(key, SEED, mc, x, z) or []) if c.get("seed") is not None]
            if not cards:
                continue
            work.append((key, x, z, cards))
            k += 1
            if k >= n:
                break
    chs = sorted({((c["pos"][0] >> 4) + a, (c["pos"][2] >> 4) + b) for _k, _x, _z, cs in work for c in cs
                  for a in (-1, 0, 1) for b in (-1, 0, 1)})
    S = srv.Server(d)
    print("structures", len(work), "chunks", len(chs), flush=True)
    if "--no-gen" not in sys.argv:
        S.start()
        S.generate(chs, full=True, batch=48, timeout=3000)
        S.save(); S.stop()
    W = S.world
    game = {}
    for cx, cz in {(c["pos"][0] >> 4, c["pos"][2] >> 4) for _k, _x, _z, cs in work for c in cs}:
        ch = anvil.chunk(W, cx, cz)
        for be in anvil.block_entities(ch) if ch else ():
            s = be.get("LootTableSeed")
            if s is not None:
                game[int(s) & ((1 << 64) - 1)] = (int(be["x"]), int(be["y"]), int(be["z"]))
    st = collections.Counter()
    for key, x, z, cards in work:
        for c in cards:
            g = game.get(int(c["seed"]) & ((1 << 64) - 1))
            if g is None:
                st[(key, "not in game")] += 1
                continue
            y = c["pos"][1]
            ok = "exact" if y == g[1] else ("none" if y is None else f"off {y - g[1]:+d}")
            st[(key, ok)] += 1
            if ok != "exact":
                print(key, "chest", g, "SSA Y", y, c["name"])
    for k, v in sorted(st.items()):
        print(" ", k, v)


if __name__ == "__main__":
    main()
