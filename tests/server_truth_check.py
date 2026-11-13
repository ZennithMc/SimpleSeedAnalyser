"""
tests/server_truth_check.py — SSA against what the headless servers saved (29/10),
one file per version in tests/server_truth/ (tools/servercheck/svcheck.py):
  * positions: the starts the game saved / the candidate chunks it left empty;
  * exact footprints (pyramids, temples, huts, igloos, shipwrecks, ruins,
    monuments, treasures);
  * chests of the scattered structures, villages / outposts (pieces + chests),
    strongholds (frame Y, 12 eyes, chests), mineshafts (1.18+).
    py tests/server_truth_check.py [label ...]
"Alternative card" = a second card SSA shows (neighbour generation order);
"estimated" = a village chest after decorations SSA cannot replay exactly.
"""
import glob
import gzip
import hashlib
import json
import os
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)
sys.path.insert(0, os.path.join(R, "qtui"))
SEED = 12345
# misses not modelled, per label: {(category, x, z)}
#  * strongholds: chests by cave / aquifer edges (generation-order dependent), a
#    dungeon inside a buried stronghold piece;
#  * igloo 320 1152: 5 blocks higher (the surface it read included something a
#    neighbour chunk decorated first);
#  * 1.18.2: a mineshaft chest by a cave edge.
_SH118 = {("chest", 2186, 332), ("chest", 2884, 4314), ("chest", -3115, -4753)}
_IGLOO = {("chest", 321, 1156)}
KNOWN = {"1.16.1": {("chest", -540, 246), ("chest", 374, -1203)},   # ruined portals: chest under netherrack
         "1.17.1": {("chest", -556, -1589), ("chest", -560, -1598)},
         "1.18.2": _SH118 | {("chest", 2214, 311), ("chest", -15, 231)},
         "1.19.2": _SH118 | {("chest", 2205, 307), ("desert_well", -5759, 11356)},   # (30/10) mangrove
         "1.19.4": {("chest", -586, -1668), ("desert_well", -5759, 11356)},       # leaves over it
         "1.20.6": _IGLOO, "1.21.1": _IGLOO, "1.21.3": _IGLOO,
         "1.21.4": {("chest", -557, -1645)},
         "26.3": {("chest", -586, -1668), ("chest", 1828, 412), ("chest", 1833, 406)}}  # + obsidian


def _igloo_top(bbs):
    """The dome (igloo/top, 7 × 8 × 5) among the igloo's pieces."""
    for b in bbs:
        if sorted((b[3] - b[0] + 1, b[5] - b[2] + 1)) == [7, 8] and b[4] - b[1] + 1 == 5:
            return b
    return max(bbs, key=lambda b: b[1])


def check(t, out):
    import cubiomes_wrap as cw
    import struct_boxes as sb
    import structure_loot as sl
    import jigsaw
    import loot_engine as le
    import stronghold_eyes as se
    mc, area, lab = t["mc"], t["area"], t["label"]
    sub = t.get("loot")              # the loot / decoration sub-version (1.21.4 +)
    known = KNOWN.get(lab, set())
    lib = cw.make_worker_lib()
    lib.setup_and_seed(mc, SEED)

    def add(cat, ok, msg, state=None, x=None, z=None):
        st = state or ("ok" if ok else "bad")
        if st == "bad" and (cat.split()[0], x, z) in known:
            st = "known"
        d = out.setdefault(cat, {})
        d[st] = d.get(st, 0) + 1
        if st not in ("ok", "known"):
            print(f"{st.upper():6} {lab} {cat}: {msg}")

    for key in set(t["starts"]) | set(t["empty"]):
        ssa = set(lib.find_structures_in_box(cw.STRUCTURE_TYPES[key], -area, -area, area - 1, area - 1,
                                             verify_biome=True, max_count=100000))
        if key in ("desert_pyramid", "jungle_temple", "trail_ruins"):
            import struct_terrain
            ssa = set(struct_terrain.filter_points(key, SEED, mc, list(ssa)))
        ssa = {(x >> 4, z >> 4) for x, z in ssa}
        for cx, cz, bbs in t["starts"].get(key, []):
            add("position " + key, (cx, cz) in ssa, f"{cx * 16} {cz * 16}: game yes, SSA no",
                x=cx * 16, z=cz * 16)
            fp = sb.footprint(key, cx * 16, cz * 16, SEED, mc)
            if isinstance(fp, list):
                if fp and fp[0][4]:
                    gb = {(b[0], b[2], b[3], b[5]) for b in bbs}
                    mb = {tuple(b[:4]) for b in fp}
                    add("footprint " + key, mb == gb, f"{cx * 16} {cz * 16}")
            elif fp is not None and fp[4]:
                u = _igloo_top(bbs) if key == "igloo" else [min(b[0] for b in bbs), 0, min(b[2] for b in bbs),
                                                   max(b[3] for b in bbs), 0, max(b[5] for b in bbs)]
                add("footprint " + key, tuple(fp[:4]) == (u[0], u[2], u[3], u[5]),
                    f"{cx * 16} {cz * 16}: SSA {fp[:4]}")
        for cx, cz in t["empty"].get(key, []):
            add("position " + key, (cx, cz) not in ssa, f"{cx * 16} {cz * 16}: game no, SSA yes",
                x=cx * 16, z=cz * 16)
    by = {}
    for key, x, y, z, s in t["chests"]:
        by.setdefault(key, []).append((x, y, z, s))
    for key, L in by.items():
        done = {}
        for x, y, z, s in L:
            # the structure: the start whose pieces hold the chest
            st = None
            for cx, cz, bbs in t["starts"].get(key, []):
                if any(b[0] <= x <= b[3] and b[2] <= z <= b[5] for b in bbs):
                    st = (cx, cz)
                    break
            if st is None:
                continue
            if st not in done:
                done[st] = sl.chests(key, SEED, mc, st[0] * 16, st[1] * 16, sub) or []
            cards = [c for c in done[st] if c.get("seed") is not None and (c["pos"][0], c["pos"][2]) == (x, z)]
            main_ = [c for c in cards if c["seed"] == s and " — " not in c["name"]]
            if main_:
                yok = main_[0]["pos"][1] in (None, y)
                order = not yok and y in (main_[0].get("ys") or ())
                add("chest " + key, yok, f"{x} {y} {z}: seed OK, SSA Y {main_[0]['pos'][1]}",
                    "order" if order else
                    None if yok or not any(w in (main_[0].get("note") or "") for w in ("Y approximate", "Y can be")) else "y_approx",
                    x=x, z=z)
            elif any(c["seed"] == s for c in cards):
                ok_y = any(c["seed"] == s and y in (c.get("ys") or (c["pos"][1],)) for c in cards)
                add("chest " + key, False, f"{x} {y} {z}", "alt" if ok_y or not any(
                    c.get("ys") for c in cards) else None)
            else:
                add("chest " + key, False, f"{x} {y} {z}", x=x, z=z)
    ver = le.loot_version(mc, sub)
    gv = jigsaw._gen(mc)
    for v in t["villages"]:
        k, x, z, gb = v["key"], v["x"], v["z"], v["bb"]
        lay = jigsaw.layout(SEED, mc, k, x >> 4, z >> 4)
        ok = lay is not None
        if ok:
            i = j = 0
            L1 = lay[1]
            while i < len(L1) and j < len(gb):
                p = L1[i]
                b2 = jigsaw.piece_box(gv, p)
                if tuple(gb[j]) in (tuple(p.box), tuple(b2), (b2[0], p.box[1], b2[2], b2[3], b2[4], b2[5])):
                    i += 1
                    j += 1
                elif p.el.kind == "feature" and mc < 22:      # 1.16 / 1.17: failed features dropped
                    i += 1
                else:
                    break
            ok = j == len(gb) and (i == len(L1) or mc < 22)
        add("layout " + k, ok, f"{x} {z}", x=x, z=z)
        if not v["chests"]:
            continue
        cards = jigsaw.chests(SEED, mc, k, x, z, ver) or []
        for px, py, pz, s in v["chests"]:
            here = [c for c in cards if tuple(c["pos"]) == (px, py, pz)]
            main_ = [c for c in here if " — " not in c["name"]]
            if any(c["seed"] == s for c in main_):
                add("chest " + k, True, "")
            elif any(c["seed"] == s for c in here):
                add("chest " + k, False, f"{px} {py} {pz}", "alt")
            elif main_ and not main_[0].get("exact", True):
                add("chest " + k, False, f"{px} {py} {pz}", "estimated")
            else:
                add("chest " + k, False, f"{px} {py} {pz}", x=px, z=pz)
    if t.get("ms_chests"):                          # (29/10) chest minecarts
        import mineshaft_loot as ml
        by_start = {}
        for mcx, mcz, X, Y, Z, gs in t["ms_chests"]:
            by_start.setdefault((mcx, mcz), []).append((X, Y, Z, gs))
        for (mcx, mcz), L in by_start.items():
            cards = ml.chests(SEED, mc, mcx * 16, mcz * 16, ver)
            main_ = {tuple(c["pos"]): c["seed"] for c in cards if c.get("seed") is not None and " — " not in c["name"]}
            alt = {tuple(c["pos"]): c["seed"] for c in cards if c.get("seed") is not None and " — " in c["name"]}
            for X, Y, Z, gs in L:
                if main_.get((X, Y, Z)) == gs or gs in main_.values():   # (moved by water)
                    add("chest mineshaft", True, "")
                elif alt.get((X, Y, Z)) == gs or gs in alt.values():
                    add("chest mineshaft", False, f"{X} {Y} {Z}", "alt")
                else:
                    add("chest mineshaft", False, f"{X} {Y} {Z}", x=X, z=Z)
            gseeds = {g[3] for g in L}             # (30/10) SSA's chests the game has not
            for c in cards:
                if c.get("seed") is not None and " — " not in c["name"] and c["seed"] not in gseeds:
                    add("ms extra", False, f"{c['pos']}", "order" if c.get("cond") else None,
                        x=c["pos"][0], z=c["pos"][2])
    if t.get("end_cities"):                         # (30/10) End cities: pieces + chests
        import end_city_loot as ecl
        for e in t["end_cities"]:
            mine = [[p[0], p[6], p[1], p[2], p[7], p[3]] for p in ecl.pieces(SEED, mc, e["x"], e["z"])]
            add("layout end_city", mine == e["pieces"], f"{e['x']} {e['z']}", x=e["x"], z=e["z"])
            cl = {(X, Y, Z): sd for X, Y, Z, sd, _t in ecl.chest_list(SEED, mc, e["x"], e["z"])}
            for X, Y, Z, gs in e["chests"]:
                add("chest end_city", cl.get((X, Y, Z)) == gs, f"{X} {Y} {Z}", x=X, z=Z)
    if t.get("fortresses"):                         # (30/10) fortresses: pieces (final Y) + chests
        import fortress_loot as fl
        for e in t["fortresses"]:
            mine = [[p.b0[0], p.b0[1], p.b0[2], p.b1[0], p.b1[1], p.b1[2]]
                    for p in fl.pieces(SEED, mc, e["x"] >> 4, e["z"] >> 4)]
            add("layout fortress", mine == e["pieces"], f"{e['x']} {e['z']}", x=e["x"], z=e["z"])
            cl = {(X, Y, Z): sd for X, Y, Z, sd, _p in fl.chest_list(SEED, mc, e["x"], e["z"])}
            for X, Y, Z, gs in e["chests"]:
                add("chest fortress", cl.get((X, Y, Z)) == gs, f"{X} {Y} {Z}", x=X, z=Z)
    if t.get("wells"):                              # (30/10) desert wells: the attempts near deserts
        import desert_well as dwl
        for x, z, gy in t["wells"]:
            y = dwl.exists(SEED, mc, x, z)
            mine = None if y in (None, False) else y
            add("desert_well", (mine is None) == (gy is None), f"{x} {z}: game {gy} SSA {mine}", x=x, z=z)
    if t.get("dungeons"):                           # (30/10) dungeon chests (known worlds, r 640)
        sys.path.insert(0, os.path.join(R, "tools", "servercheck"))
        import dungeoncheck as dgc
        dg = t["dungeons"]
        st_, _lines = dgc.compare(mc, dg["chests"], [tuple(c) for c in dg["inner"]])
        d_ = out.setdefault("chest dungeon", {})
        for k_, v_ in st_.items():
            if v_:
                d_[k_] = d_.get(k_, 0) + v_
    if t.get("camps"):                              # (30/10) 26.3 abandoned camps: type, boxes, containers
        import jigsaw as jgc
        for e in t["camps"]:
            lay = jgc.layout(SEED, mc, "abandoned_camp", e["x"] >> 4, e["z"] >> 4)
            ok_ = lay is not None and [list(p.box) for p in lay[1]] == e["pieces"] \
                and (e.get("type") or "").split(":")[-1] == lay[0]
            add("layout abandoned_camp", ok_, f"{e['x']} {e['z']}", x=e["x"], z=e["z"])
            cards = {tuple(c["pos"]): c["seed"] for c in jgc.chests(SEED, mc, "abandoned_camp", e["x"], e["z"], "26.3") or []
                     if c.get("seed") is not None and " — if chunk" not in c["name"]}
            for X, Y, Z, gs in e["chests"]:
                add("chest abandoned_camp", cards.get((X, Y, Z)) == gs, f"{X} {Y} {Z}", x=X, z=Z)
    if t.get("ancient_cities"):                     # (30/10) ancient cities: jigsaw boxes + chests
        import ancient_city_loot as acl
        for e in t["ancient_cities"]:
            lay = acl.layout(SEED, mc, e["x"] >> 4, e["z"] >> 4)
            mine = [list(p.box) for p in lay[0]] if lay else []
            add("layout ancient_city", mine == e["pieces"], f"{e['x']} {e['z']}", x=e["x"], z=e["z"])
            cl = {(X, Y, Z): (sd, w) for X, Y, Z, sd, tb, _l, w in acl.chest_list(SEED, mc, e["x"], e["z"]) if tb}
            for X, Y, Z, gs in e["chests"]:
                sd, w = cl.get((X, Y, Z), (None, False))
                add("chest ancient_city", sd == gs, f"{X} {Y} {Z}: after a sculk patch",
                    state="sculk" if sd != gs and w else None, x=X, z=Z)
    if t.get("bastions"):                           # (30/10) bastions: jigsaw boxes + chests
        import bastion_loot as bl
        for e in t["bastions"]:
            lay = bl.layout(SEED, mc, e["x"] >> 4, e["z"] >> 4)
            mine = [list(p.box) for p in lay[0]] if lay else []
            add("layout bastion", mine == e["pieces"], f"{e['x']} {e['z']}", x=e["x"], z=e["z"])
            cl = {(X, Y, Z): sd for X, Y, Z, sd, _t, _l in bl.chest_list(SEED, mc, e["x"], e["z"])}
            for X, Y, Z, gs in e["chests"]:
                add("chest bastion", cl.get((X, Y, Z)) == gs, f"{X} {Y} {Z}", x=X, z=Z)
    if t.get("ruined_portals"):                     # (30/10) layout + the portal chest
        import ruined_portal_loot as rpl
        for e in t["ruined_portals"]:
            g = rpl.layout(SEED, mc, e["cx"], e["cz"])
            mine = None if g is None else ("minecraft:ruined_portal/" + g["template"], rpl.ROT[g["rot"]],
                                           "FRONT_BACK" if g["mirror"] else "NONE", list(g["origin"]),
                                           g["placement"])
            game = (e["tpl"], e["rot"], e["mir"], e["tp"], e["vp"])
            add("layout ruined_portal", mine is not None and tuple(mine) == tuple(game),
                f"{e['cx']} {e['cz']}: game {game} SSA {mine}", x=e["cx"] * 16, z=e["cz"] * 16)
            if not e.get("full", True):
                continue
            cards = [c for c in rpl.chests(SEED, mc, e["cx"] * 16, e["cz"] * 16, ver) if c.get("seed") is not None]
            for X, Y, Z, gs in e["chests"]:
                add("chest ruined_portal", any(tuple(c["pos"]) == (X, Y, Z) and c["seed"] == gs
                                               for c in cards), f"{X} {Y} {Z}", x=X, z=Z)
            if not e["chests"] and cards:              # SSA's chest, none in the game
                add("chest ruined_portal", False, f"{cards[0]['pos']}: no chest in the game",
                    "order" if cards[0].get("cond") else None, x=cards[0]["pos"][0], z=cards[0]["pos"][2])
    for (x, z), fy, eyes, chests in t["strongholds"]:
        pf = se.get_stronghold_portal_full(SEED, x, z, mc)
        mine = "".join("1" if e else "0" for e in se.count_stronghold_frames(SEED, x, z, mc))
        add("stronghold frames", pf is not None and pf["frame_y"] == fy and mine == eyes,
            f"{x} {z}: game Y {fy} eyes {eyes}, SSA Y {pf and pf['frame_y']} eyes {mine}", x=x, z=z)
        cards = sl.chests("stronghold", SEED, mc, x, z, sub) or []
        for cx, cy, cz, gs in chests:
            here = [c for c in cards if (c["pos"][0], c["pos"][2]) == (cx, cz) and c.get("seed") is not None]
            if any(c["seed"] == gs and c["pos"][1] == cy and " — " not in c["name"] for c in here):
                add("chest stronghold", True, "")
            elif any(c["seed"] == gs for c in here):
                add("chest stronghold", False, f"{cx} {cy} {cz}", "alt")
            else:
                add("chest stronghold", False, f"{cx} {cy} {cz}", x=cx, z=cz)
    if t.get("mineshafts_old") or (mc < 22 and t["no_mineshaft"]):
        import mineshaft_loot as ml                  # (29/10) 1.16 / 1.17 (mineshaft116.c)
        mine = {(x, z) for x, z, _m in ml.starts(SEED, mc, -24, -24, 23, 23)}
        for k, gb in t.get("mineshafts_old", {}).items():
            cx, cz = map(int, k.split(","))
            ok = (cx, cz) in mine and {tuple(b) for b in gb} <= {tuple(b[:6]) for b in ml.pieces(SEED, mc, cx, cz)}
            add("mineshaft", ok, f"start {cx} {cz}", x=cx * 16, z=cz * 16)
        for cx, cz in t["no_mineshaft"]:
            add("mineshaft", (cx, cz) not in mine, f"no start in game at chunk {cx} {cz}",
                x=cx * 16, z=cz * 16)
    elif t["mineshafts"] or t["no_mineshaft"]:
        import mineshaft118 as ms
        bio, _deep = ms.biome_fns(SEED, mc)
        hfn = ms._height_fn(SEED, mc)
        for k, (h, mesa) in t["mineshafts"].items():
            cx, cz = map(int, k.split(","))
            ty = ms.has_start(SEED, mc, cx, cz, bio, hfn)
            ok = ty == (2 if mesa else 1)
            if ok:
                pcs = ms.layout(SEED, cx, cz, mc=mc) if not mesa else \
                    ms.layout_ex(SEED, cx, cz, mesa=True, mc=mc, height=hfn)[0]
                ok = hashlib.md5(repr([tuple(p.bb) for p in pcs]).encode()).hexdigest()[:12] == h
            add("mineshaft", ok, f"start {cx} {cz}", x=cx * 16, z=cz * 16)
        for cx, cz in t["no_mineshaft"]:
            ty = ms.has_start(SEED, mc, cx, cz, bio, hfn)
            add("mineshaft", not ty, f"no start in game at chunk {cx} {cz}", x=cx * 16, z=cz * 16)


def check_mansions(allt, out, labels):
    """(30/10) woodland mansions (worlds of other seeds: mansions are rare)."""
    import mansion_loot as mnl
    for lab, d in allt.items():
        if labels and lab not in labels:
            continue
        o = out.setdefault(lab, {})
        mc = d["mc"]
        for sd, L in d.items():
            if sd == "mc":
                continue
            seed = int(sd)
            for e in L:
                lay = mnl.layout(seed, mc, e["cx"], e["cz"])
                mp = [[n, list(p), r, m] for n, p, r, m in lay[2]] if lay else []
                st = "ok" if mp == e["pieces"] else "bad"
                o.setdefault("layout mansion", {})[st] = o.get("layout mansion", {}).get(st, 0) + 1
                if st == "bad":
                    print(f"BAD    {lab} layout mansion: seed {seed} {e['cx']} {e['cz']}")
                mine = {(a, b, c): v for a, b, c, v, _n in mnl.chest_list(seed, mc, e["cx"], e["cz"])}
                game = {(a, b, c): v for a, b, c, v in e["chests"]}
                for k in set(mine) | set(game):
                    st = "ok" if mine.get(k) == game.get(k) else "bad"
                    o.setdefault("chest mansion", {})[st] = o.get("chest mansion", {}).get(st, 0) + 1
                    if st == "bad":
                        print(f"BAD    {lab} chest mansion: seed {seed} {k} game {game.get(k)} SSA {mine.get(k)}")


def main(labels=None):
    out = {}
    for f in sorted(glob.glob(os.path.join(R, "tests", "server_truth", "*.json.gz"))):
        with gzip.open(f, "rt") as fh:
            t = json.load(fh)
        if "label" not in t:                         # (30/10) mansions.json.gz: other seeds
            check_mansions(t, out, labels)
            continue
        if labels and t["label"] not in labels:
            continue
        o = out.setdefault(t["label"], {})
        check(t, o)
    bad = 0
    for lab, o in out.items():
        print(f"── {lab}")
        for cat, d in sorted(o.items()):
            bad += d.get("bad", 0)
            print(f"   {cat:26} " + "  ".join(f"{a} {b}" for a, b in sorted(d.items())))
    print("bad", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(set(sys.argv[1:]) or None))
