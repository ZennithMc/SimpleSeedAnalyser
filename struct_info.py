"""
struct_info.py — what the map's bubble says about one structure (18/10).

    details(seed, mc, large, key, x, z, dim) -> dict(rows=[(label, text)],
                                                    tps=[(label, command)])

Runs in the map's helper PROCESS (qtui/map_engine._eye_pool): the exact
surface (1.18+ terrain), the stronghold simulation or the pieces cost a few ms
to ~100 ms — never in the UI thread. Every /tp carries an exact Y when one is
known (else « ~ »), in the structure's dimension.
"""
from __future__ import annotations

import math

_LIBS = {}
_TERRAIN = {}

BASTION_TYPES = ("Housing units", "Hoglin stables", "Treasure", "Bridge")
END_SHIP = 12                 # cubiomes End city piece enum
BRIDGE_SPAWNER = 5            # cubiomes fortress piece enum
NETHER_WART = 13


def _lib(seed, mc, large, dim):
    import cubiomes_wrap as cw
    k = (seed, mc, large, dim)
    lib = _LIBS.get(k)
    if lib is None:
        if len(_LIBS) > 6:
            _LIBS.clear()
        lib = cw.make_worker_lib()
        lib.setup_and_seed(mc, seed, large_biomes=large, dim=dim)
        _LIBS[k] = lib
    return lib


def tp(dim, x, y, z, yaw=None):
    yy = "~" if y is None else (str(y) if isinstance(y, float) else str(int(y)))
    rot = "" if yaw is None else f" {yaw} 0"
    if dim == -1:
        return f"/execute in minecraft:the_nether run tp @s {x} {yy} {z}{rot}"
    if dim == 1:
        return f"/execute in minecraft:the_end run tp @s {x} {yy} {z}{rot}"
    return f"/tp @s {x} {yy} {z}{rot}"


def surface_y(seed, mc, large, x, z):
    """(Y of the highest solid block, exact?) in the overworld, or (None, False)."""
    import cubiomes_wrap as cw
    try:
        if mc >= cw.MC_VERSIONS["Java 1.18"] and not large:
            eng = _TERRAIN.get((seed, mc))
            if eng is None:
                import terrain
                _TERRAIN.clear()
                eng = _TERRAIN[(seed, mc)] = terrain.TerrainEngine(seed, mc)
            return int(eng.exact_y(x, z, caves=False)), True
        if int(mc) in (19, 20):                         # the exact 1.16.1 terrain
            import feature116 as f116
            return int(f116.surface_grid(mc, seed, x, z, 1, 1, 1, False)[0, 0]), True
        lib = _lib(seed, mc, large, 0)
        return int(round(lib.approx_height(x, z))), False
    except Exception:
        return None, False


def _biome(lib, mc, dim, x, z):
    import cubiomes_wrap as cw
    scale = 1 if (dim or mc >= cw.MC_VERSIONS["Java 1.18"]) else 4
    try:
        bid = lib.get_biome_at(x, z, scale=scale, y=(0 if dim else 320))
        from biome_catalog import biome_name_for
        return biome_name_for(int(mc), int(bid))
    except Exception:
        return "?"


def ship_local(piece, lx, ly, lz):
    """World block of the ship template's local (lx, ly, lz): the template
    origin is the box corner its rotation leaves at the piece position
    (cubiomes / the game: 0 none, 1 cw 90, 2 180, 3 ccw 90 — x' = −z, z' = x
    for cw 90). The ship template (end_city/ship.nbt) is 13 × 24 × 29; its
    « Elytra » data marker is at (6, 5, 7): the item frame is placed there."""
    x0, z0, x1, z1, _t, rot, y0, _y1 = piece
    rot &= 3
    if rot == 0:
        return x0 + lx, y0 + ly, z0 + lz
    if rot == 1:
        return x1 - lz, y0 + ly, z0 + lx
    if rot == 2:
        return x1 - lx, y0 + ly, z1 - lz
    return x0 + lz, y0 + ly, z1 - lx


UNDERGROUND = {"stronghold", "mineshaft", "ancient_city", "dungeon", "dungeon_ok", "trial_chambers"}


def details(seed, mc, large, key, x, z, dim):
    seed, mc, x, z, dim = int(seed), int(mc), int(x), int(z), int(dim)
    rows, tps = [], []
    lib = _lib(seed, mc, bool(large), dim)
    rows.append(("Biome", _biome(lib, mc, dim, x, z)))
    rows.append(("Chunk", f"{x >> 4}, {z >> 4}"))
    d0 = math.hypot(x, z)
    rows.append(("From 0, 0", f"{d0:,.0f} blocks".replace(",", " ")))
    try:
        if key == "spawn" and dim == 0 and mc >= 22:     # (12/11) the game's world spawn
            import spawn_exact
            sp = spawn_exact.spawn(seed, mc)
            if sp is not None:
                rows.append(("World spawn", f"X {sp[0]}   Y {sp[1]}   Z {sp[2]}"))
                rows.append(("Distance", f"{math.hypot(sp[0], sp[2]):.2f} blocks from 0, 0"))
                dbt = spawn_exact.doubt(seed, mc)
                if dbt:
                    rows.append(("Note", f"a tree may stand in the water at {dbt[0]} {dbt[1]} "
                                         f"({dbt[2].replace('_', ' ')}): the spawn would be there, "
                                         "on its leaves"))
                else:
                    rows.append(("Note", "same X Z as the game; Y higher if a tree or a rock "
                                         "stands on this column"))
                tps.append(("Spawn", tp(0, sp[0], sp[1], sp[2])))
        elif key == "stronghold":
            import stronghold_eyes as se
            d = se.get_stronghold_diagnostics(seed, x, z, mc)
            px, py, pz = d.get("portal_pos", (x, 0, z))
            fy = int(d.get("frame_y", py))
            rows.append(("Eyes", f"{int(d.get('eyes', 0))} / 12"))
            rows.append(("Portal", f"X {px}   Y {fy}   Z {pz}"))
            if d.get("highest_y") is not None:
                rows.append(("Top / bottom", f"Y {d['highest_y']} / {d.get('lowest_y')}"))
            tps.append(("Portal room", tp(0, px, fy + 1, pz)))
        elif key == "ancient_city" and dim == 0:       # (30/10) ancient_city_loot
            import ancient_city_loot as acl
            lay = acl.layout(seed, mc, x >> 4, z >> 4)
            if lay is not None:
                ps, (ccx, ccy, ccz) = lay
                rows.append(("Pieces", str(sum(1 for p in ps if p.el.kind not in ("feature", "empty")))))
                cl = [c for c in acl.chest_list(seed, mc, x, z) if c[4]]
                ni = sum(1 for c in cl if c[4].endswith("ice_box"))
                rows.append(("Loot chests", str(len(cl)) + (f" ({ni} ice box)" if ni else "")))
                rows.append(("Centre", f"X {ccx}   Y {ccy}   Z {ccz}"))
                tps.append(("Centre", tp(0, ccx, ccy + 1, ccz)))
        elif key == "mansion" and dim == 0:            # (30/10)
            import mansion_loot as mnl
            lay = mnl.layout(seed, mc, x >> 4, z >> 4)
            if lay is not None:
                (ox, oy, oz), _rot, ps = lay
                rooms = [p for p in ps if p[0].startswith(("1x1", "1x2", "2x2"))]
                rows.append(("Rooms", f"{len(rooms)} ({sum(1 for p in ps if p[0].endswith('_stairs'))} stairs)"))
                nch = len(mnl.chest_list(seed, mc, x >> 4, z >> 4))
                rows.append(("Loot chests", str(nch)))
                # (31/10) just inside the front door: the entrance template's doorway
                # (local x 10..14 at z 15, red carpet at y 1)
                ent = next((p for p in ps if p[0] == "entrance"), None)
                if ent is not None:
                    ex, ey, ez = mnl.world(ent, 12, 1, 13)
                    tps.append(("Front door", tp(0, ex + 0.5, float(ey), ez + 0.5)))
            else:
                rows.append(("Mansion", "not generated (ground under Y 60)"))
        elif key == "ruined_portal" and dim == 0:      # (30/10)
            import ruined_portal_loot as rpl
            g = rpl.layout(seed, mc, x >> 4, z >> 4)
            if g is not None:
                kind = "overworld" if g["kind"] == "standard" else g["kind"]
                rows.append(("Type", f"{kind}, {g['template'].replace('_', ' ')}"))
                rows.append(("Placement", g["placement"].replace("_", " ")
                             + (", air pocket" if g["air"] else "")))
                b = g["box"]
                rows.append(("Y", f"{b[1]} → {b[4]}"))
                cx_, cy_, cz_ = rpl.chest_pos(g)
                tps.append(("Chest", tp(0, cx_, cy_ + 1, cz_)))
                try:                                   # (07/11) its frame, can it be lit
                    import ruined_portal_filter as rpf
                    a = rpf.analyze(seed, mc, x >> 4, z >> 4, layout=g)
                    f = a and a["frame"]
                    if f:
                        rows.append(("Frame", f"{f['size'][0]}×{f['size'][1]} · {f['missing']} missing"
                                     f" · {f['crying']} crying → {f['need']} obsidian"))
                        fx, fy, fz = f["cells"][0][:3]
                        tps.append(("Frame", tp(0, fx, fy + 1, fz)))
                    if a and a["chest_ok"]:
                        rows.append(("Chest", f"{a['obsidian']} obsidian"
                                     + (" · can light it" if a["light"] else " · nothing to light it")))
                    elif a:
                        rows.append(("Chest", "gone (lava / netherrack)"))
                    if a:
                        rows.append(("Completable", "yes — with its chest" if a["completable"] else "no"))
                except Exception:
                    pass
        elif key == "mineshaft":                       # (29/10)
            import mineshaft_loot as ml
            ps = ml.pieces(seed, mc, x >> 4, z >> 4)
            if ps:
                room = ps[0]
                nc = sum(1 for q in ps if q[6] == "corridor")
                rows.append(("Pieces", f"{len(ps)} ({nc} corridors)"))
                rows.append(("Room", f"Y {room[1]} → {room[4]}"))
                rows.append(("Depth", f"Y {min(q[1] for q in ps)} → {max(q[4] for q in ps)}"))
                tps.append(("Room", tp(0, (room[0] + room[3]) // 2, room[1] + 1, (room[2] + room[5]) // 2)))
            else:
                rows.append(("Y", "depends on the surface (badlands mineshaft)"))
        elif key in ("end_city", "end_city_ship"):
            pcs = lib.struct_pieces("end_city", x, z)
            if pcs:
                top = max(pcs, key=lambda q: q[7])
                ship = next((q for q in pcs if q[4] == END_SHIP), None)
                rows.append(("Pieces", str(len(pcs))))
                rows.append(("City top", f"Y {top[7]}"))
                base = pcs[0]                                  # base_floor
                tps.append(("Base", tp(1, (base[0] + base[2]) // 2, base[6] + 1,
                                       (base[1] + base[3]) // 2)))
                if ship is not None:
                    rows.append(("Ship", f"Y {ship[6]} → {ship[7]}"))
                    ex, ey, ez = ship_local(ship, 6, 5, 7)     # the « Elytra » data marker
                    rows.append(("Elytra", f"X {ex}   Y {ey}   Z {ez}"))
                    # standing on the deck in front of the frame (template (6, 4, 8):
                    # floor below, the frame at eye level), looking at it
                    fx, fy, fz = ship_local(ship, 6, 4, 8)
                    yaw = (180, -90, 0, 90)[ship[5] & 3]      # the frame faces local north
                    tps.append(("In front of the elytra",
                                tp(1, fx + 0.5, float(fy), fz + 0.5, yaw)))
                else:
                    rows.append(("Ship", "none"))
        elif key == "fortress":
            # (30/10) fortress_loot: the game's pieces moved to their final Y
            # (moveInsideHeights 48 → 70; cubiomes leaves them at y 64)
            import fortress_loot as fl
            fps = fl.pieces(seed, mc, x >> 4, z >> 4)
            pcs = fl.map_boxes(seed, mc, x, z)
            sp = [q for q in fps if q.typ == BRIDGE_SPAWNER]
            rows.append(("Pieces", str(len(pcs))))
            rows.append(("Y", f"{min(q[6] for q in pcs)} → {max(q[7] for q in pcs)}"))
            rows.append(("Blaze spawners", str(len(sp))))
            rows.append(("Nether wart rooms", str(sum(1 for q in pcs if q[4] == NETHER_WART))))
            rows.append(("Loot chests", str(len(fl.chest_list(seed, mc, x, z)))))
            for i, q in enumerate(sp[:4]):
                bx, by, bz = q.world(3, 5, 5)                  # MonsterThrone's spawner
                rows.append((f"Spawner {i + 1}", f"X {bx}   Y {by}   Z {bz}"))
                tps.append((f"Spawner {i + 1}", tp(-1, bx, by + 1, bz)))
            if pcs:
                st = pcs[0]
                tps.insert(0, ("Base", tp(-1, (st[0] + st[2]) // 2, st[7] + 1,
                                          (st[1] + st[3]) // 2)))
        elif key == "bastion":
            # (30/10) bastion_loot: the start piece SSA builds (checked on the servers)
            import bastion_loot as bl
            kd = bl.kind(seed, mc, x >> 4, z >> 4)
            t = lib.bastion_type(x, z)
            if kd:
                rows.append(("Type", kd))
            elif 0 <= t < len(BASTION_TYPES):
                rows.append(("Type", BASTION_TYPES[t]))
            cl = bl.chest_list(seed, mc, x, z)
            if cl:
                nt = sum(1 for c in cl if c[4] == "chests/bastion_treasure")
                rows.append(("Loot chests", str(len(cl)) + (f" ({nt} treasure)" if nt else "")))
            bb = lib.variant_bb("bastion", x, z)
            if bb:
                cx, cz = (bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2
                rows.append(("Size", f"{bb[2] - bb[0] + 1} × {bb[3] - bb[1] + 1}"))
                tps.append(("Base", tp(-1, cx, None, cz)))
        elif key == "end_gateway":
            try:
                h = lib.end_height_grid(x, z, 1, 1, 1)
                y = int(h[0, 0]) if h is not None else 0
                if y > 0:
                    rows.append(("Island surface", f"Y {y}"))
                    tps.append(("Base", tp(1, x, y + 1, z)))
            except Exception:
                pass
        elif key in ("village", "zombie_village"):
            import cubiomes_wrap as cw
            # (18/10) the variant needs the village's biome type (as the map's
            # icon): with biome -1 getVariant never says abandoned
            ab = lib.village_abandoned_flags([(x, z)])[0]
            rows.append(("Abandoned", "yes (zombie village)" if ab else "no"))
            try:                                       # (05/11) its houses and smiths
                import jigsaw
                import village_filter as vf
                lay = jigsaw.layout(seed, mc, "village", x >> 4, z >> 4, large)
                if lay is not None:
                    locs = [p.el.loc for p in lay[1] if p.el.kind in ("single", "legacy")]
                    rows.append(("Houses", str(sum(1 for l in locs if vf.is_house(l)))))
                    g = jigsaw._gen(mc)
                    fc = [vf.forge_chest(g, l) for l in locs]
                    fc = [f.replace("village_", "") for f in fc if f]
                    rows.append(("Forges", f"{len(fc)} with a chest" +
                                 (f"   ·   {', '.join(sorted(fc))}" if fc else "")))
            except Exception:
                pass
        elif key in ("igloo", "igloo_basement"):
            import cubiomes_wrap as cw
            rows.append(("Basement", "yes" if lib.get_variant_basement(cw.Igloo, x, z) else "no"))
        elif key in ("dungeon", "dungeon_ok"):             # (31/10) dungeon_loot
            import dungeon_loot as dl
            v = dl.find(seed, mc, x, None, z)
            d = v.get(True) or v.get(False)
            if d is not None:
                rows.append(("Spawner", f"{dl.MOBS[d[5] & 3]}   ·   X {d[0]}   Y {d[1]}   Z {d[2]}"))
                rows.append(("Room", f"{2 * d[3] + 1} × {2 * d[4] + 1}"))
                rows.append(("Chests", str(len(d[6]))))
                if v.get(True) is None:
                    rows.append(("Note", "only if this chunk generates before its neighbours"))
                tps.append(("Spawner", tp(0, d[0] + 0.5, float(d[1] + 1), d[2] + 0.5)))
            else:
                rows.append(("Dungeon", "not found here"))
        elif key == "buried_treasure":
            x, z = (x >> 4 << 4) + 9, (z >> 4 << 4) + 9
            rows.append(("Chest", f"X {x}   Z {z}  (dig down)"))
    except Exception as e:                                   # noqa: BLE001
        rows.append(("Details", f"unavailable ({type(e).__name__})"))
    if dim == 0 and key not in ("stronghold",):
        y, exact = surface_y(seed, mc, bool(large), x, z)
        if y is not None:
            rows.append(("Ground", f"Y {y}" + ("" if exact else "  (approx.)")))
        # (31/10) underground structures: their own /tp only (not the surface)
        if not (tps and key in UNDERGROUND) and not (tps and key == "spawn"):
            tps.insert(0, ("Base", tp(0, x, None if y is None else y + 1, z)))
    elif not tps:
        tps.append(("Base", tp(dim, x, None, z)))
    return dict(rows=rows, tps=tps)
