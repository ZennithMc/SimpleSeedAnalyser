"""tools/servercheck/scatcat.py — (05/11) the top view of the single-piece structures
built in code (desert pyramid, jungle temple, swamp hut, ocean monument), read from a
headless server's world (mkserver.py): per column of the piece (its own frame, width ×
depth), the highest block of the structure and its height above the piece's lowest
top; the most frequent over the instances. → loot_tables/scattered_tops.json.gz

    python3 scatcat.py <server dir> <mc> [--no-gen] [--n N]"""
import collections, gzip, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import anvil, srv, struct_info, cubiomes_wrap as cw, scattered_loot as sc
from piececat import World, _gen

KINDS = {
    # key: (cubiomes type, (w, h, d), origin offset, block whitelist, y range)
    "desert_pyramid": (cw.Desert_Pyramid, (21, 15, 21), 0,
                       ("sandstone", "terracotta", "tnt", "stone_pressure_plate"), (40, 140)),
    "jungle_temple": (cw.Jungle_Pyramid if hasattr(cw, "Jungle_Pyramid") else cw.Jungle_Temple,
                      (12, 10, 15), 0,
                      ("cobblestone", "mossy_cobblestone", "chiseled_stone_bricks", "lever",
                       "dispenser", "sticky_piston", "repeater", "redstone_wire", "tripwire_hook",
                       "chest"), (40, 160)),
    "swamp_hut": (cw.Swamp_Hut, (7, 7, 9), 0,
                  ("spruce_planks", "spruce_stairs", "oak_fence", "oak_log", "potted_red_mushroom",
                   "cauldron", "crafting_table", "spruce_slab"), (50, 90)),
    "monument": (cw.Monument, (58, 23, 58), -29,
                 ("prismarine", "sea_lantern", "gold_block", "wet_sponge", "sponge"), (35, 64)),
}


def main():
    d, mc = sys.argv[1], int(sys.argv[2])
    a = sys.argv
    n = int(a[a.index("--n") + 1]) if "--n" in a else 6
    seed = 12345
    log = lambda *m: print(*m, flush=True)
    S = srv.Server(d)
    lib = struct_info._lib(seed, mc, None, 0)
    inst = {}
    ch = set()
    for k, (st, (w, h, dd), off, _wl, _yr) in KINDS.items():
        R_ = 20000 if k == "desert_pyramid" else 5000
        pts = sorted(lib.find_structures_in_box(st, -R_, -R_, R_, R_, verify_biome=True,
                                                max_count=400), key=lambda p: p[0] ** 2 + p[1] ** 2)[:n * (4 if k == "desert_pyramid" else 1)]
        inst[k] = []
        for x, z in pts:
            cx, cz = x >> 4, z >> 4
            ori = sc._direction(seed, cx, cz)
            p = sc.Scattered((cx << 4) + off, 64, (cz << 4) + off, w, h, dd, ori)
            inst[k].append(p)
            b = p.box
            for qx in range(b[0] >> 4, (b[3] >> 4) + 1):
                for qz in range(b[2] >> 4, (b[5] >> 4) + 1):
                    ch.add((qx, qz))
        log(k, pts)
    if "--no-gen" not in a:
        S.start()
        _gen(S, ch, "", log)
        S.save()
        S.stop()
    W = World(S.world, "")
    sid = {"desert_pyramid": "desert_pyramid", "jungle_temple": "jungle_pyramid",
           "swamp_hut": "swamp_hut", "monument": "monument"}
    for k in inst:                                # only the ones the game really built
        keep = []
        for p in inst[k]:
            cx, cz = (p.box[0] - KINDS[k][2]) >> 4, (p.box[2] - KINDS[k][2]) >> 4
            st, _s = anvil.starts(S.world, cx, cz) or ({}, None)
            if any(sid[k] in kk.lower() and isinstance(v, dict) and v.get("Children")
                   for kk, v in (st or {}).items()):
                keep.append(p)
        log(k, "built", len(keep), "of", len(inst[k]))
        inst[k] = keep
    out, names, idx = {}, [], {}
    for k, (st, (w, h, dd), off, wl, (ylo, yhi)) in KINDS.items():
        votes = collections.defaultdict(collections.Counter)
        hs = collections.defaultdict(list)
        for p in inst[k]:
            tops = {}
            for lz in range(dd):
                for lx in range(w):
                    X, Z = p.wx(lx, lz), p.wz(lx, lz)
                    for y in range(yhi, ylo - 1, -1):
                        nm = W.block(X, y, Z)
                        if nm is None:
                            break
                        if any(s in nm for s in wl):
                            tops[(lx, lz)] = (nm, y)
                            break
            if not tops:
                continue
            base = min(v[1] for v in tops.values())
            for c, (nm, y) in tops.items():
                votes[c][nm] += 1
                hs[c].append(y - base)
        cells = []
        for lz in range(dd):
            for lx in range(w):
                v = votes.get((lx, lz))
                if not v or sum(v.values()) * 2 < len(inst[k]):
                    cells.append("")
                    continue
                nm = v.most_common(1)[0][0]
                if nm not in idx:
                    idx[nm] = len(names); names.append(nm)
                hh = sorted(hs[(lx, lz)])[len(hs[(lx, lz)]) // 2]
                cells.append("%x:%d" % (idx[nm], hh))
        out[k] = [w, dd, ",".join(cells)]
        log(k, "instances", len(inst[k]), "cells", sum(1 for c in cells if c))
    p = os.path.join(R, "loot_tables", "scattered_tops.json.gz")
    with gzip.open(p, "wt") as f:
        json.dump({"mc": mc, "names": names, "tops": out}, f, separators=(",", ":"))
    log(p, os.path.getsize(p), names)


if __name__ == "__main__":
    main()
