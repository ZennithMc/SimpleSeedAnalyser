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


def details(seed, mc, large, key, x, z, dim):
    seed, mc, x, z, dim = int(seed), int(mc), int(x), int(z), int(dim)
    rows, tps = [], []
    lib = _lib(seed, mc, bool(large), dim)
    rows.append(("Biome", _biome(lib, mc, dim, x, z)))
    rows.append(("Chunk", f"{x >> 4}, {z >> 4}"))
    d0 = math.hypot(x, z)
    rows.append(("From 0, 0", f"{d0:,.0f} blocks".replace(",", " ")))
    try:
        if key == "stronghold":
            import stronghold_eyes as se
            d = se.get_stronghold_diagnostics(seed, x, z, mc)
            px, py, pz = d.get("portal_pos", (x, 0, z))
            fy = int(d.get("frame_y", py))
            rows.append(("Eyes", f"{int(d.get('eyes', 0))} / 12"))
            rows.append(("Portal", f"X {px}   Y {fy}   Z {pz}"))
            if d.get("highest_y") is not None:
                rows.append(("Top / bottom", f"Y {d['highest_y']} / {d.get('lowest_y')}"))
            y, _ex = surface_y(seed, mc, bool(large), x, z)
            tps.append(("Base", tp(0, x, None if y is None else y + 1, z)))
            tps.append(("Portal room", tp(0, px, fy + 1, pz)))
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
            pcs = lib.struct_pieces("fortress", x, z)
            sp = [q for q in pcs if q[4] == BRIDGE_SPAWNER]
            rows.append(("Pieces", str(len(pcs))))
            rows.append(("Blaze spawners", str(len(sp))))
            rows.append(("Nether wart rooms", str(sum(1 for q in pcs if q[4] == NETHER_WART))))
            off = ((0, -1), (1, 0), (0, 1), (-1, 0))
            for i, q in enumerate(sp[:4]):
                # cubiomes-viewer's spawner square: the piece centre + its rotation offset
                bx = q[0] + (q[2] - q[0]) // 2 + off[q[5] & 3][0]
                bz = q[1] + (q[3] - q[1]) // 2 + off[q[5] & 3][1]
                by = q[6] + 5
                rows.append((f"Spawner {i + 1}", f"X {bx}   Y {by}   Z {bz}"))
                tps.append((f"Spawner {i + 1}", tp(-1, bx, by + 1, bz)))
            if pcs:
                st = pcs[0]
                tps.insert(0, ("Base", tp(-1, (st[0] + st[2]) // 2, st[7] + 1,
                                          (st[1] + st[3]) // 2)))
        elif key == "bastion":
            t = lib.bastion_type(x, z)
            if 0 <= t < len(BASTION_TYPES):
                rows.append(("Type", BASTION_TYPES[t]))
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
        elif key in ("igloo", "igloo_basement"):
            import cubiomes_wrap as cw
            rows.append(("Basement", "yes" if lib.get_variant_basement(cw.Igloo, x, z) else "no"))
        elif key == "buried_treasure":
            x, z = (x >> 4 << 4) + 9, (z >> 4 << 4) + 9
            rows.append(("Chest", f"X {x}   Z {z}  (dig down)"))
    except Exception as e:                                   # noqa: BLE001
        rows.append(("Details", f"unavailable ({type(e).__name__})"))
    if dim == 0 and key not in ("stronghold",):
        y, exact = surface_y(seed, mc, bool(large), x, z)
        if y is not None:
            rows.append(("Ground", f"Y {y}" + ("" if exact else "  (approx.)")))
        tps.insert(0, ("Base", tp(0, x, None if y is None else y + 1, z)))
    elif not tps:
        tps.append(("Base", tp(dim, x, None, z)))
    return dict(rows=rows, tps=tps)
