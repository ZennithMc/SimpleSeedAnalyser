"""tools/servercheck/vilcmp116.py — SSA's 1.16 / 1.17 village / outpost layouts vs the
structure starts a server saved:  python3 vilcmp116.py <world> <mc> <pos.json>"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anvil, jigsaw  # noqa: E402

W, MC = sys.argv[1], int(sys.argv[2])
pos = json.load(open(sys.argv[3]))[str(MC)]
g = jigsaw._gen(MC)
ok = bad = miss = 0
for key, L in pos.items():
    for x, z in L:
        cx, cz = x >> 4, z >> 4
        r = anvil.starts(W, cx, cz)
        if r is None:
            miss += 1; print("no chunk", key, x, z); continue
        st = {k.split(':')[-1].lower(): v for k, v in r[0].items()}
        sname = {"village": "village", "pillager_outpost": "pillager_outpost"}[key]
        s = st.get(sname)
        lay = jigsaw.layout(12345, MC, key, cx, cz)
        if s is None or s.get("id") == "INVALID":
            print(("OK  " if lay is None else "BAD ") + f"{key} {x} {z}: none in game, SSA {None if lay is None else len(lay[1])}")
            ok += lay is None; bad += lay is not None; continue
        gp = s["Children"]
        if lay is None:
            bad += 1; print(f"BAD {key} {x} {z}: SSA none, game {len(gp)}"); continue
        # 1.16 / 1.17 StructureStart.placeInChunk drops a piece whose postProcess
        # fails (a tree / cactus patch that could not grow): extra feature pieces OK
        def m(p, q):
            b2 = jigsaw.piece_box(g, p)
            return tuple(q["BB"]) in (tuple(p.box), tuple(b2), (b2[0], p.box[1], b2[2], b2[3], b2[4], b2[5]))
        i = j = drop = 0
        L1 = lay[1]
        while i < len(L1) and j < len(gp):
            if m(L1[i], gp[j]):
                i += 1; j += 1
            elif L1[i].el.kind == "feature":
                i += 1; drop += 1
            else:
                break
        drop += sum(1 for p in L1[i:] if p.el.kind == "feature") if j == len(gp) else 0
        same = j == len(gp) and all(p.el.kind == "feature" for p in L1[i:])
        ok += same; bad += not same
        print(("OK  " if same else "BAD ") + f"{key} {x} {z}: {len(L1)} pieces, game {len(gp)}"
              + (f" ({drop} failed features)" if drop else "") + ("" if same else f", diff at {i}"))
print("ok", ok, "bad", bad, "missing", miss)
