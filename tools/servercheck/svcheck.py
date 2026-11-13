"""tools/servercheck/svcheck.py — one Minecraft version against SSA, on a headless
server built by mkserver.py (seed 12345). Cloud-side verification tool.

    python3 svcheck.py <server dir> <mc> <label> [--area R] [--step N] [--loot VER] [--skip-gen]

1. generates the structure starts over ±R blocks (a grid of force-loaded chunks),
   then the chunks the checks need (chests, strongholds, mineshafts), and saves;
2. compares with SSA:
   * positions: every candidate chunk of each overworld structure (cubiomes'
     attempt of its region) — the game has a start there ⇔ SSA shows it;
   * footprints: SSA's exact footprints = the x/z of the start's pieces;
   * scattered-structure chests (desert pyramid, jungle temple, igloo, shipwreck,
     ocean ruin, buried treasure): LootTableSeed (+ Y when SSA gives one);
   * villages / outposts: every piece box, chest seeds;
   * strongholds (8 first): end-portal frame Y, the 12 eyes, every chest;
   * mineshafts (1.18+): start ⇔ mineshaft118.has_start, md5 of the piece boxes;
3. writes tests/server_truth/<label>.json.gz (what the game saved) for
   tests/server_truth_check.py.
"""
import gzip
import math
import hashlib
import json
import os
import sys
import time

R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, R)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anvil  # noqa: E402
import srv  # noqa: E402

SEED = 12345
SCATTERED = ("desert_pyramid", "jungle_temple", "igloo", "shipwreck", "ocean_ruin", "buried_treasure")
N_PER_TYPE = 8           # structures per type whose chests are generated / checked
N_MS = 6                 # mineshafts whose chest minecarts are checked


def ssa_key(gid):
    g = gid.split(":")[-1].lower()
    if g.startswith("village"):
        return "village"
    for pre, k in (("pillager_outpost", "pillager_outpost"), ("jungle_pyramid", "jungle_temple"),
                   ("jungle_temple", "jungle_temple"), ("desert_pyramid", "desert_pyramid"),
                   ("swamp_hut", "swamp_hut"), ("igloo", "igloo"), ("ocean_monument", "monument"),
                   ("monument", "monument"), ("woodland_mansion", "mansion"), ("mansion", "mansion"),
                   ("ruined_portal_nether", None), ("ruined_portal", "ruined_portal"),
                   ("ancient_city", "ancient_city"), ("buried_treasure", "buried_treasure"),
                   ("shipwreck", "shipwreck"), ("ocean_ruin", "ocean_ruin"),
                   ("trail_ruins", "trail_ruins"), ("trial_chambers", "trial_chambers"),
                   ("mineshaft", "mineshaft"), ("stronghold", "stronghold"),
                   ("abandoned_camp", "abandoned_camp")):
        if g.startswith(pre):
            return k
    return g


def starts_of(world, cx, cz):
    """{ssa key: [start dict]} of the chunk, or None (chunk not generated)."""
    c = anvil.chunk(world, cx, cz)
    if c is None:
        return None
    lv = c.get("Level", c)
    st = lv.get("structures", lv.get("Structures", {}))
    out = {}
    for k, v in st.get("starts", st.get("Starts", {})).items():
        if v.get("id") == "INVALID" or not v.get("Children"):
            continue
        out.setdefault(ssa_key(k), []).append(dict(v, _gid=k))
    return out


def bb_union(start):
    bs = [q["BB"] for q in start["Children"]]
    return [min(b[0] for b in bs), min(b[1] for b in bs), min(b[2] for b in bs),
            max(b[3] for b in bs), max(b[4] for b in bs), max(b[5] for b in bs)]


def chunks_of(b, pad=0):
    return [(x, z) for x in range((b[0] - pad) >> 4, ((b[3] + pad) >> 4) + 1)
            for z in range((b[2] - pad) >> 4, ((b[5] + pad) >> 4) + 1)]


class Report:
    def __init__(self):
        self.n = {}
        self.lines = []

    def add(self, cat, ok, msg=None, state=None):
        st = state or ("ok" if ok else "bad")
        d = self.n.setdefault(cat, {})
        d[st] = d.get(st, 0) + 1
        if st != "ok" and msg:
            self.lines.append(f"{st.upper():6} {cat}: {msg}")

    def show(self):
        for l in self.lines:
            print(l)
        for k, d in self.n.items():
            print(f"{k:22} " + "  ".join(f"{a} {b}" for a, b in sorted(d.items())))


def main():
    a = sys.argv[1:]
    D, MC, LABEL = a[0], int(a[1]), a[2]
    area = int(a[a.index("--area") + 1]) if "--area" in a else 1536
    gen = "--skip-gen" not in a
    step = int(a[a.index("--step") + 1]) if "--step" in a else 16
    import loot_engine as le
    LOOT = a[a.index("--loot") + 1] if "--loot" in a else le.loot_version(MC)   # sub-version
    S = srv.Server(D)
    W = S.world
    import cubiomes_wrap as cw
    lib = cw.make_worker_lib()
    lib.setup_and_seed(MC, SEED)
    t0 = time.time()
    lo, hi = (-area) >> 4, (area - 1) >> 4
    if gen:
        S.start()
        grid = [(x, z) for x in range(lo + step // 2, hi + 1, step)
                for z in range(lo + step // 2, hi + 1, step)]
        print("phase 1: structure starts,", len(grid), "force-loaded chunks")
        S.generate(grid)
    rep = Report()
    truth = dict(label=LABEL, mc=MC, loot=LOOT, area=area, starts={}, empty={}, chests=[], villages=[],
                 strongholds=[], mineshafts={}, no_mineshaft=[])

    # ── positions ──
    want_full = set()
    found = {}
    for key, stype in cw.STRUCTURE_TYPES.items():
        cand = lib.find_structures_in_box(stype, -area, -area, area - 1, area - 1,
                                          verify_biome=False, max_count=100000)
        if not cand:
            continue
        ssa = set(lib.find_structures_in_box(stype, -area, -area, area - 1, area - 1,
                                             verify_biome=True, max_count=100000))
        if key in ("desert_pyramid", "jungle_temple", "trail_ruins"):
            import struct_terrain
            ssa = set(struct_terrain.filter_points(key, SEED, MC, list(ssa)))
        if key == "village":                    # the zombie villages are villages too
            pass
        for (x, z) in cand:
            cx, cz = x >> 4, z >> 4
            st = starts_of(W, cx, cz)
            if st is None:
                continue
            g = st.get(key, [])
            mine = (x, z) in ssa
            rep.add("position " + key, bool(g) == mine,
                    f"{x} {z}: game {'yes' if g else 'no'}, SSA {'yes' if mine else 'no'}")
            if g:
                truth["starts"].setdefault(key, []).append([cx, cz, [q["BB"] for q in g[0]["Children"]]])
                found.setdefault(key, []).append((x, z, g[0]))
            else:
                truth["empty"].setdefault(key, []).append([cx, cz])
    # ── footprints ──
    sys.path.insert(0, os.path.join(R, "qtui"))
    import struct_boxes as sb
    for key, L in found.items():
        for x, z, s in L:
            fp = sb.footprint(key, x, z, SEED, MC)
            if fp is None:
                continue
            if isinstance(fp, list):
                gb = sorted((q["BB"][0], q["BB"][2], q["BB"][3], q["BB"][5]) for q in s["Children"])
                mb = sorted((b[0], b[1], b[2], b[3]) for b in fp)
                if fp[0][4]:
                    rep.add("footprint " + key, set(mb) <= set(gb) and len(set(gb)) == len(set(mb)),
                            f"{x} {z}: SSA {mb} game {gb}")
                continue
            if not fp[4]:
                continue
            u = bb_union(s) if key != "igloo" else next((q["BB"] for q in s["Children"] if str(q.get("Template", "")).endswith("igloo/top")),
                                                                    max((q["BB"] for q in s["Children"]), key=lambda b: b[1]))
            g4 = (u[0], u[2], u[3], u[5])
            rep.add("footprint " + key, tuple(fp[:4]) == g4, f"{x} {z}: SSA {fp[:4]} game {g4}")
    # ── chunks for the chests / strongholds / mineshafts ──
    import structure_loot as sl
    import jigsaw
    near = lambda L: sorted(L, key=lambda t: t[0] * t[0] + t[1] * t[1])[:N_PER_TYPE]
    for key in SCATTERED:
        for x, z, s in near(found.get(key, [])):
            want_full.update(chunks_of(bb_union(s)))
    vil = near(found.get("village", [])) + near(found.get("pillager_outpost", []))[:4]
    vcards = {}
    for x, z, s in vil:
        k = "village" if ssa_key(s["_gid"]) == "village" else "pillager_outpost"
        try:
            import loot_engine as le
            cards = jigsaw.chests(SEED, MC, k, x, z, LOOT) or []
        except Exception as e:
            cards = []
            print("jigsaw.chests failed", k, x, z, e)
        vcards[(k, x, z)] = cards
        for c in cards:
            if c.get("seed") is not None:
                want_full.add((c["pos"][0] >> 4, c["pos"][2] >> 4))
    shs = lib.find_strongholds(8)
    if gen:
        S.generate([(x >> 4, z >> 4) for x, z in shs], full=False)
    sh_starts = []
    for x, z in shs:
        st = starts_of(W, x >> 4, z >> 4) or {}
        s = (st.get("stronghold") or [None])[0]
        sh_starts.append((x, z, s))
        if s is not None:
            want_full.update(chunks_of(bb_union(s)))
    ms_area = (-24, -24, 23, 23) if MC >= 22 else None      # chunks
    # (29/10) the mineshafts whose chest minecarts are checked: the nearest ones
    import mineshaft_loot as ml
    ms_loot = sorted(ml.starts(SEED, MC, -24, -24, 23, 23), key=lambda t: t[0] ** 2 + t[1] ** 2)[:N_MS]
    ms_cards = {}
    for mcx, mcz, _mesa in ms_loot:
        for b in ml.pieces(SEED, MC, mcx, mcz):
            if b[6] == "corridor":
                want_full.update(chunks_of((b[0], b[1], b[2], b[3], b[4], b[5])))
    # (30/10) ruined portals: every one found, its start chunk and its box's centre
    for x, z, st_ in found.get("ruined_portal", []):
        b = st_["Children"][0]["BB"]
        want_full.add(((b[0] + b[3]) // 2 >> 4, (b[2] + b[5]) // 2 >> 4))
    if gen:
        print("phase 2:", len(want_full), "full chunks")
        S.generate(sorted(want_full))
        S.stop()

    def be_at(x, y, z):
        c = anvil.chunk(W, x >> 4, z >> 4)
        if anvil.status(c) != "full":
            return None, False
        for t in anvil.block_entities(c):
            if t["x"] == x and t["z"] == z and (y is None or t["y"] == y) and "LootTableSeed" in t:
                return t, True
        return None, True

    # ── scattered chests ──
    for key in SCATTERED:
        for x, z, s in near(found.get(key, [])):
            cards = sl.chests(key, SEED, MC, x, z, LOOT) or []
            mains = [c for c in cards if " — " not in c["name"] and c.get("seed") is not None]
            alts = [c for c in cards if " — " in c["name"] and c.get("seed") is not None]
            for c in mains:
                px, py, pz = c["pos"]
                ch = anvil.chunk(W, px >> 4, pz >> 4)
                if anvil.status(ch) != "full":
                    continue
                # every container with a loot seed at this X Z (cold ocean ruins: one
                # chest per copy that settled at its own height)
                tb = (c.get("table") or "").split("/")[-1]
                gl = [t for t in anvil.block_entities(ch)
                      if t["x"] == px and t["z"] == pz and "LootTableSeed" in t
                      and (not tb or str(t.get("LootTable", "")).endswith(tb))]
                for t in gl:
                    truth["chests"].append([key, px, t["y"], pz, t["LootTableSeed"]])
                if not gl:
                    rep.add("chest " + key, False, f"{x} {z} chest {px} {py} {pz}: no chest in game")
                    continue
                for t in gl:
                    gs = t["LootTableSeed"]
                    if gs == c["seed"]:
                        yok = py is None or py == t["y"]
                        rep.add("chest " + key, yok, f"{px} {py} {pz}: seed OK, game Y {t['y']}",
                                "order" if not yok and t["y"] in (c.get("ys") or ()) else
                                None if yok or not any(w in (c.get("note") or "") for w in ("Y approximate", "Y can be")) else "y_approx")
                    elif any(a["seed"] == gs and tuple(a["pos"][::2]) == (px, pz) for a in alts):
                        rep.add("chest " + key, False, f"{px} {t['y']} {pz}: an alternative card", "alt")
                    else:
                        rep.add("chest " + key, False, f"{x} {z} chest {px} {t['y']} {pz}: SSA "
                                f"{c['seed']} game {gs}")
    # ── villages / outposts ──
    gv = jigsaw._gen(MC) if jigsaw.MC_FIRST <= MC <= jigsaw.MC_LAST else None
    for x, z, s in vil:
        k = "village" if ssa_key(s["_gid"]) == "village" else "pillager_outpost"
        gp = s["Children"]
        lay = jigsaw.layout(SEED, MC, k, x >> 4, z >> 4) if gv else None
        ok = lay is not None
        if ok:
            L1 = lay[1]
            i = j = 0
            while i < len(L1) and j < len(gp):
                p = L1[i]
                b2 = jigsaw.piece_box(gv, p)
                if tuple(gp[j]["BB"]) in (tuple(p.box), tuple(b2), (b2[0], p.box[1], b2[2], b2[3], b2[4], b2[5])):
                    i += 1; j += 1
                elif p.el.kind == "feature" and MC < 22:
                    i += 1
                else:
                    break
            ok = j == len(gp) and i == len(L1) or (MC < 22 and j == len(gp))
        rep.add("layout " + k, ok, f"{x} {z}: SSA {len(lay[1]) if lay else None} pieces, game {len(gp)}")
        vt = dict(key=k, x=x, z=z, bb=[q["BB"] for q in gp], chests=[])
        for c in vcards.get((k, x, z), []):
            if c.get("seed") is None or " — " in c["name"]:
                continue
            px, py, pz = c["pos"]
            t, gen_ok = be_at(px, py, pz)
            if not gen_ok or t is None:
                continue
            vt["chests"].append([px, py, pz, t["LootTableSeed"]])
            good = t["LootTableSeed"] == c["seed"]
            if not good:
                alt = any(a["seed"] == t["LootTableSeed"] for a in vcards[(k, x, z)]
                          if " — " in a["name"] and tuple(a["pos"]) == (px, py, pz))
                st = "alt" if alt else ("approx" if not c.get("exact", True) else "bad")
                rep.add("chest " + k, False, f"{x} {z} chest {px} {py} {pz}", st)
            else:
                rep.add("chest " + k, True)
        truth["villages"].append(vt)
    # ── strongholds ──
    import stronghold_eyes as se
    for x, z, s in sh_starts:
        if s is None:
            rep.add("stronghold", False, f"{x} {z}: no start in the game")
            continue
        frames = []
        chests = []
        for c in chunks_of(bb_union(s)):
            ch = anvil.chunk(W, *c)
            if anvil.status(ch) != "full":
                continue
            frames += [b for b in anvil.blocks(ch, ("end_portal_frame",))]
            for t in anvil.block_entities(ch):
                if "stronghold" in str(t.get("LootTable", "")) and "LootTableSeed" in t:
                    chests.append((t["x"], t["y"], t["z"], t["LootTableSeed"]))
        if len(frames) != 12:
            rep.add("stronghold", False, f"{x} {z}: {len(frames)} frames read", "skip")
            continue
        fy = frames[0][1]
        g = se.StrongholdGenerator()
        g.pre118 = se._use_old_filler(MC)
        g.generate(SEED, x >> 4, z >> 4)
        pr = g.portal_room_piece
        at = {(b[0], b[2]): b[4].get("eye") == "true" for b in frames}
        eyes = "".join("1" if at.get(se._frame_world_pos(pr, i)) else "0" for i in range(12))
        truth["strongholds"].append([[x, z], fy, eyes, sorted(chests)])
        pf = se.get_stronghold_portal_full(SEED, x, z, MC)
        mine = "".join("1" if e else "0" for e in se.count_stronghold_frames(SEED, x, z, MC))
        rep.add("stronghold frames", pf is not None and pf["frame_y"] == fy and mine == eyes,
                f"{x} {z}: game Y {fy} eyes {eyes}, SSA Y {pf and pf['frame_y']} eyes {mine}")
        cards = sl.chests("stronghold", SEED, MC, x, z, LOOT) or []
        for cx, cy, cz, gs in chests:
            here = [c for c in cards if (c["pos"][0], c["pos"][2]) == (cx, cz) and c.get("seed") is not None]
            main_ = [c for c in here if " — " not in c["name"]]
            if any(c["seed"] == gs and c["pos"][1] == cy for c in main_):
                rep.add("chest stronghold", True)
            elif any(c["seed"] == gs for c in here):
                rep.add("chest stronghold", False, f"{cx} {cy} {cz}", "alt")
            else:
                rep.add("chest stronghold", False, f"{x} {z} chest {cx} {cy} {cz}")
    # ── mineshafts (starts in the chunks already generated) ──
    if ms_area is not None:
        import mineshaft118 as ms
        bio, _deep = ms.biome_fns(SEED, MC)
        hfn = ms._height_fn(SEED, MC)
        for cx in range(ms_area[0], ms_area[2] + 1):
            for cz in range(ms_area[1], ms_area[3] + 1):
                st = starts_of(W, cx, cz)
                if st is None:
                    continue
                g = st.get("mineshaft")
                t = ms.has_start(SEED, MC, cx, cz, bio, hfn)
                if not g:
                    if t:
                        rep.add("mineshaft start", False, f"{cx} {cz}: SSA has one, not the game")
                    else:
                        truth["no_mineshaft"].append([cx, cz])
                    continue
                gb = [tuple(q["BB"]) for q in g[0]["Children"]]
                h = hashlib.md5(repr(gb).encode()).hexdigest()[:12]
                truth["mineshafts"][f"{cx},{cz}"] = [h, "mesa" in g[0]["_gid"]]
                if not t:
                    rep.add("mineshaft start", False, f"{cx} {cz}: game has one, not SSA")
                    continue
                mine = [tuple(p.bb) for p in (ms.layout(SEED, cx, cz, mc=MC) if t == 1 else
                                              ms.layout_ex(SEED, cx, cz, mesa=True, mc=MC, height=hfn)[0])]
                rep.add("mineshaft start", hashlib.md5(repr(mine).encode()).hexdigest()[:12] == h,
                        f"{cx} {cz}: layout differs")
    # ── 1.16 / 1.17 mineshafts (mineshaft116.c): starts and pieces ──
    if MC < 22:
        mine = {(x, z) for x, z, _m in ml.starts(SEED, MC, -24, -24, 23, 23)}
        for cx in range(-24, 24):
            for cz in range(-24, 24):
                st = starts_of(W, cx, cz)
                if st is None:
                    continue
                g = st.get("mineshaft")
                if not g:
                    if (cx, cz) in mine:
                        rep.add("mineshaft start", False, f"{cx} {cz}: SSA has one, not the game")
                    else:
                        truth["no_mineshaft"].append([cx, cz])
                    continue
                gb = sorted(tuple(q["BB"]) for q in g[0]["Children"])
                truth.setdefault("mineshafts_old", {})[f"{cx},{cz}"] = gb
                if (cx, cz) not in mine:
                    rep.add("mineshaft start", False, f"{cx} {cz}: game has one, not SSA")
                    continue
                # 1.16 / 1.17 drop the pieces that met a liquid: the game's ⊆ SSA's
                mb = {tuple(b[:6]) for b in ml.pieces(SEED, MC, cx, cz)}
                rep.add("mineshaft start", set(gb) <= mb,
                        f"{cx} {cz}: layout differs ({len(mb)} / {len(gb)} pieces)")
    # ── mineshaft chest minecarts (entities) ──
    truth["ms_chests"] = []
    for mcx, mcz, _mesa in ms_loot:
        ps = ml.pieces(SEED, MC, mcx, mcz)
        chs = {(x, z) for b in ps if b[6] == "corridor"
               for x in range(b[0] >> 4, (b[3] >> 4) + 1) for z in range(b[2] >> 4, (b[5] >> 4) + 1)}
        if any(anvil.status(anvil.chunk(W, *c)) != "full" for c in chs):
            rep.add("chest mineshaft", False, f"{mcx} {mcz}: chunks not generated", "skip")
            continue
        boxes = [b for b in ps if b[6] == "corridor"]
        game = []
        for c in chs:
            for e in anvil.entities(W, *c):
                if not str(e.get("id", "")).endswith("chest_minecart") or "LootTableSeed" not in e:
                    continue
                X, Y, Z = (int(math.floor(v)) for v in e["Pos"])
                if any(b[0] <= X <= b[3] and b[1] <= Y <= b[4] and b[2] <= Z <= b[5] for b in boxes):
                    game.append((X, Y, Z, e["LootTableSeed"]))
        cards = ml.chests(SEED, MC, mcx * 16, mcz * 16, LOOT)
        main = {tuple(c["pos"]): c["seed"] for c in cards if c.get("seed") is not None and " — " not in c["name"]}
        alt = {tuple(c["pos"]): c["seed"] for c in cards if c.get("seed") is not None and " — " in c["name"]}
        cond = {tuple(c["pos"]) for c in cards if c.get("cond")}
        for X, Y, Z, gs in game:
            truth["ms_chests"].append([mcx, mcz, X, Y, Z, gs])
            if main.get((X, Y, Z)) == gs:
                rep.add("chest mineshaft", True)
            elif gs in main.values():                # a minecart pushed by water / mobs
                rep.add("chest mineshaft", True)
            elif alt.get((X, Y, Z)) == gs or gs in alt.values():
                rep.add("chest mineshaft", False, f"{mcx} {mcz}: {X} {Y} {Z}", "alt")
            else:
                rep.add("chest mineshaft", False, f"{mcx} {mcz}: {X} {Y} {Z} game {gs} "
                        f"SSA {main.get((X, Y, Z))}")
        gp = {(X, Y, Z) for X, Y, Z, _s in game}
        gseeds = {s_ for _x, _y, _z, s_ in game}
        for p_, s_ in main.items():
            if p_ not in gp and s_ not in gseeds:
                rep.add("chest mineshaft", False, f"{mcx} {mcz}: SSA chest {p_} not in game",
                        "order" if p_ in cond else None)
    # ── (30/10) ruined portals: layout (template, rotation, mirror, Y) and chest ──
    import ruined_portal_loot as rpl
    truth["ruined_portals"] = []
    for x, z, st_ in found.get("ruined_portal", []):
        p0 = st_["Children"][0]
        b = p0["BB"]
        cc = ((b[0] + b[3]) // 2 >> 4, (b[2] + b[5]) // 2 >> 4)
        chests = []
        full = anvil.status(anvil.chunk(W, *cc)) == "full"
        for a_ in (-1, 0, 1):
            for b_ in (-1, 0, 1):
                c = anvil.chunk(W, cc[0] + a_, cc[1] + b_)
                if anvil.status(c) != "full":
                    continue
                for be in anvil.block_entities(c):
                    if "ruined_portal" in str(be.get("LootTable", "")):
                        chests.append([be["x"], be["y"], be["z"], be.get("LootTableSeed")])
        e = dict(cx=x >> 4, cz=z >> 4, id=st_.get("id"), tpl=str(p0.get("Template")), rot=p0.get("Rotation"),
                 mir=p0.get("Mirror"), tp=[p0.get("TPX"), p0.get("TPY"), p0.get("TPZ")],
                 vp=p0.get("VerticalPlacement"), chests=chests, full=full)
        truth["ruined_portals"].append(e)
        g = rpl.layout(SEED, MC, e["cx"], e["cz"])
        mine = None if g is None else ("minecraft:ruined_portal/" + g["template"], rpl.ROT[g["rot"]],
                                       "FRONT_BACK" if g["mirror"] else "NONE", list(g["origin"]),
                                       g["placement"])
        game = (e["tpl"], e["rot"], e["mir"], e["tp"], e["vp"])
        rep.add("layout ruined_portal", mine is not None and tuple(mine) == tuple(game),
                f"{x} {z}: game {game} SSA {mine}")
        if full:
            cards = [k for k in rpl.chests(SEED, MC, x, z, LOOT) if k.get("seed") is not None]
            for X, Y, Z, gs in chests:
                rep.add("chest ruined_portal", any(tuple(k["pos"]) == (X, Y, Z) and k["seed"] == gs
                                                   for k in cards), f"{X} {Y} {Z} game {gs}")
            if not chests and cards:
                rep.add("chest ruined_portal", False, f"{cards[0]['pos']}: no chest in the game",
                        "order" if cards[0].get("cond") else None)
    rep.show()
    os.makedirs(os.path.join(R, "tests", "server_truth"), exist_ok=True)
    with gzip.open(os.path.join(R, "tests", "server_truth", LABEL + ".json.gz"), "wt") as f:
        json.dump(truth, f, separators=(",", ":"))
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
