"""tools/servercheck/jigxcheck.py — (05/11) trail ruins / trial chambers: SSA's pieces
(jigsaw_extra) against the game's structure starts (server world).
    python3 jigxcheck.py <server dir> <mc> [--no-gen]"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import anvil, srv, struct_info, cubiomes_wrap as cw, jigsaw_extra as je


def main():
    d, mc = sys.argv[1], int(sys.argv[2])
    S = srv.Server(d)
    lib = struct_info._lib(12345, mc, None, 0)
    todo = []
    for key, st in (("trial_chambers", cw.Trial_Chambers), ("trail_ruins", cw.Trail_Ruins)):
        pts = sorted(lib.find_structures_in_box(st, -3000, -3000, 3000, 3000, verify_biome=True,
                                                max_count=200), key=lambda p: p[0] ** 2 + p[1] ** 2)[:6]
        todo += [(key, x, z) for x, z in pts]
    if "--no-gen" not in sys.argv:
        S.start()
        S.generate([(x >> 4, z >> 4) for _k, x, z in todo], full=False, log=print)
        S.save(); S.stop()
    ok = bad = miss = 0
    for key, x, z in todo:
        s, _st = anvil.starts(S.world, x >> 4, z >> 4) or ({}, None)
        g = None
        for k, v in (s or {}).items():
            if key in k.lower() and isinstance(v, dict) and v.get("Children"):
                g = v
        lay = je.layout(12345, mc, key, x >> 4, z >> 4)
        if g is None:
            miss += 1
            print("no start in the game", key, x, z, "SSA", None if lay is None else len(lay[0]))
            continue
        gb = [tuple(c["BB"]) for c in g["Children"]]
        mine = [tuple(p.box) for p in lay[0]] if lay else []
        if gb == mine:
            ok += 1
            print("OK", key, x, z, len(gb))
        else:
            bad += 1
            n = next((i for i, (a, b) in enumerate(zip(gb, mine)) if a != b), min(len(gb), len(mine)))
            print("DIFF", key, x, z, len(gb), len(mine), "first at", n, gb[n:n + 1], mine[n:n + 1])
    print("ok", ok, "bad", bad, "missing", miss)


if __name__ == "__main__":
    main()
