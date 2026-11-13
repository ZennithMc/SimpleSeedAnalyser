"""
ruined_portal_filter.py — (07/11) the Ruined portal condition's filters: where the
portal stands (its placement), its type, the crying obsidian of its frame, and
whether it can be completed and lit with its own chest.

The portal is the one of the game (ruined_portal_loot.layout: the type, template,
rotation, mirror and exact Y), placed as the game places it (ruined_portal_loot.placed):
  • obsidian → crying obsidian 15 % of the time (BlockAgeProcessor, a random from the
    block's position — so it depends on the Y);
  • spreadNetherrack's drip columns replace what is under the ground spots they cover.

The frame (loot_tables/ruined_portal_frames.json, tools/loot/build_portal_frames.py):
the cheapest vertical rectangle (outer 4 × 5 up to 23 × 23, corners not needed, no
obsidian inside) that uses obsidian of the ruin where it stands. Its cost = the cells
without obsidian + the crying obsidian on it (crying obsidian does not make a portal:
it is broken and replaced). Ties: the fewest crying obsidian.

Completable: the portal's chest is there (not in lava, not under a drip) and holds at
least that many obsidian, and a way to light it: a flint and steel, a fire charge, or
flint + iron nuggets (9 nuggets → an ingot → flint and steel: ed's choice, always).
"""
from __future__ import annotations

import json
import os

PLACEMENTS = ("on_land_surface", "partly_buried", "on_ocean_floor", "in_mountain", "underground")
PLACEMENT_NAMES = {"on_land_surface": "Surface", "partly_buried": "Half buried",
                   "on_ocean_floor": "Sea floor", "in_mountain": "In a mountain",
                   "underground": "Underground"}
KINDS = ("standard", "desert", "jungle", "swamp", "mountain", "ocean")
KIND_NAMES = {"standard": "Plains", "desert": "Desert", "jungle": "Jungle", "swamp": "Swamp",
              "mountain": "Mountain", "ocean": "Ocean"}
IGNITERS = ("flint_and_steel", "fire_charge")

_FR = None


def frames():
    """{template: (obs [(x, y, z)], rects [(axis, k, u0, y0, W, H, m, mask)])}"""
    global _FR
    if _FR is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables",
                         "ruined_portal_frames.json")
        with open(p) as f:
            d = json.load(f)
        _FR = {t: ([tuple(o) for o in v["obs"]], [tuple(r) for r in v["rects"]])
               for t, v in d.items()}
    return _FR


def min_missing(template) -> int:
    """the fewest obsidian the template's frame needs, before any turns crying"""
    rects = frames().get(template, ([], []))[1]
    return min((r[6] for r in rects), default=99)


# ── params ──────────────────────────────────────────────────────────────────────
def _bound(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def bounds(params, key):
    r = list((params or {}).get(key) or (None, None)) + [None, None]
    return _bound(r[0]), _bound(r[1])


def placements_of(params):
    v = [p for p in ((params or {}).get("placements") or ()) if p in PLACEMENTS]
    return v if v and len(v) < len(PLACEMENTS) else []


def kinds_of(params):
    v = [k for k in ((params or {}).get("kinds") or ()) if k in KINDS]
    return v if v and len(v) < len(KINDS) else []


def active(params) -> bool:
    """Does this Ruined portal condition ask for anything beyond the position?"""
    p = params or {}
    return bool(placements_of(p) or kinds_of(p) or p.get("completable")
                or any(b is not None for b in bounds(p, "crying")))


# ── the portal ──────────────────────────────────────────────────────────────────
def frame_of(g, W, gone):
    """the cheapest frame of portal g as placed: dict(need, missing, crying, size,
    obsidian) or None (no frame)"""
    import ruined_portal_loot as rpl
    fr = frames().get(g["template"])
    if not fr:
        return None
    obs, rects = fr
    size = g["size"]
    px, pz = size[0] // 2, size[2] // 2
    ox, oy, oz = g["origin"]
    cry = 0
    lost = 0
    for i, (x, y, z) in enumerate(obs):
        a, b = rpl.transform(x, z, g["mirror"], g["rot"], px, pz)
        wp = (ox + a, oy + y, oz + b)
        if wp in gone:
            lost |= 1 << i                         # under a netherrack drip
        elif W.get(("cry",) + wp):
            cry |= 1 << i
    best = None
    for rect in rects:
        axis, k, u0, y0, w, h, m, mask = rect
        n_lost = bin(mask & lost).count("1")
        c = bin(mask & cry).count("1")
        need = m + n_lost + c
        key = (need, c)
        if best is None or key < best[0]:
            best = (key, dict(need=need, missing=m + n_lost, crying=c, size=(w, h),
                              obsidian=bin(mask).count("1") - n_lost - c), rect)
    if best is None:
        return None
    out = best[1]
    out["lost"], out["cry"] = lost, cry           # (bit i = the template's obsidian i)
    # its cells in the world: (x, y, z, "o" obsidian | "c" crying | "m" to place),
    # bottom row first, then the sides, then the top
    axis, k, u0, y0, w, h, _m, mask = best[2]
    state = {}
    for i, o in enumerate(obs):
        if mask >> i & 1:
            state[o] = "m" if lost >> i & 1 else "c" if cry >> i & 1 else "o"
    cells = []
    for y in range(y0, y0 + h):
        for u in range(u0, u0 + w):
            edge_u, edge_y = u in (u0, u0 + w - 1), y in (y0, y0 + h - 1)
            if not (edge_u or edge_y) or (edge_u and edge_y):
                continue
            loc = (k, y, u) if axis == "x" else (u, y, k)
            a, b = rpl.transform(loc[0], loc[2], g["mirror"], g["rot"], px, pz)
            cells.append((ox + a, oy + loc[1], oz + b, state.get(loc, "m"),
                          u - u0, y - y0))
    out["cells"] = cells
    return out


def chest_stats(slots):
    """(obsidian, can light) of a chest's slots"""
    obs = flint = nug = 0
    light = False
    for s in (slots or ()):
        if s is None or isinstance(s, str):
            continue
        it, n = s.item, int(s.count)
        if it == "obsidian":
            obs += n
        elif it in IGNITERS:
            light = True
        elif it == "flint":
            flint += n
        elif it == "iron_nugget":
            nug += n
    return obs, light or (flint > 0 and nug >= 9)


def analyze(seed, mc, cx, cz, ver=None, layout=None, chest=True):
    """Everything the filter looks at for the portal started in chunk (cx, cz), or None:
    dict(kind, placement, template, origin, frame, chest_ok, obsidian, light,
    completable). chest=False: the frame only (chest_ok / obsidian / light unknown).

    The netherrack drip columns (ruined_portal_loot.placed, the carved terrain of the
    chunks around) are replayed only when they can reach the frame or the chest: a drip
    starts under a ground spot of the carved terrain, never above the noise terrain's
    surface (carvers only remove blocks), so a block above that surface (or out of the
    spread) is safe."""
    import copy
    import loot_engine as le
    import ruined_portal_loot as rpl
    cols = rpl._Cols(seed, mc)
    g = layout or rpl.layout(seed, mc, cx, cz, cols)
    if g is None or rpl.ghost_116(mc, g, cx, cz):
        return None
    ls, r = rpl.chest_rng(seed, mc, g, cx, cz)
    X, Y, Z = rpl.chest_pos(g)
    size = g["size"]
    px, pz = size[0] // 2, size[2] // 2
    ox, oy, oz = g["origin"]
    cxx, _cy, czz = rpl._center(g["box"])
    md = len(rpl._PROB)
    floor_ = g["placement"] == rpl.OCEAN_FLOOR
    obs_w = []
    for (x, y, z) in frames().get(g["template"], ([], []))[0]:
        a, b = rpl.transform(x, z, g["mirror"], g["rot"], px, pz)
        obs_w.append((ox + a, oy + y, oz + b))

    def at_risk(x, y, z):
        if abs(x - cxx) + abs(z - czz) >= md:
            return False
        return y <= cols.base_height(x, z, floor_) - 1
    risky = any(at_risk(*w) for w in obs_w) or (chest and at_risk(X, Y, Z))
    if risky:
        W, gone = rpl.placed(seed, mc, g, copy.deepcopy(r))
    else:                                         # the processors that matter, no terrain
        W, gone = {}, set()
        for w in obs_w:
            if rpl._pos_float(*w) < rpl._f32(0.15):
                W[("cry",) + w] = True
        if chest:
            W[(X, Y, Z)] = "l" if rpl._Carved(seed, mc).at(X, Y, Z) == 3 else "c"
    fr = frame_of(g, W, gone)
    chest_ok = None
    obs, light = 0, False
    if chest:
        chest_ok = W.get((X, Y, Z)) == "c" and (X, Y, Z) not in gone
        if chest_ok:
            slots, _ap = le.fill_chest("ruined_portal", ls, ver or le.loot_version(mc))
            obs, light = chest_stats(slots)
    return dict(kind=g["kind"], placement=g["placement"], template=g["template"],
                origin=g["origin"], air=g["air"], frame=fr, chest_ok=chest_ok, obsidian=obs,
                light=light, completable=bool(chest_ok and fr and light and obs >= fr["need"]),
                chest_pos=(X, Y, Z))


def analyze_full(seed, mc, cx, cz, ver=None):
    """analyze, always replaying the drip columns (the reference for the tests)"""
    import copy
    import loot_engine as le
    import ruined_portal_loot as rpl
    g = rpl.layout(seed, mc, cx, cz)
    if g is None or rpl.ghost_116(mc, g, cx, cz):
        return None
    ls, r = rpl.chest_rng(seed, mc, g, cx, cz)
    W, gone = rpl.placed(seed, mc, g, copy.deepcopy(r))
    fr = frame_of(g, W, gone)
    X, Y, Z = rpl.chest_pos(g)
    chest_ok = W.get((X, Y, Z)) == "c" and (X, Y, Z) not in gone
    obs, light = 0, False
    if chest_ok:
        slots, _ap = le.fill_chest("ruined_portal", ls, ver or le.loot_version(mc))
        obs, light = chest_stats(slots)
    return dict(kind=g["kind"], placement=g["placement"], template=g["template"],
                origin=g["origin"], air=g["air"], frame=fr, chest_ok=chest_ok, obsidian=obs,
                light=light, completable=bool(chest_ok and fr and light and obs >= fr["need"]),
                chest_pos=(X, Y, Z))


def _cheap_ok(params, seed, mc, cx, cz, ver):
    """without the terrain: can one of the portals the start can be pass?"""
    import loot_engine as le
    import ruined_portal_loot as rpl
    import scattered_loot as sc
    pl, ks = placements_of(params), kinds_of(params)
    comp = bool(params.get("completable"))
    for g in rpl.candidates(seed, mc, cx, cz):
        if rpl.ghost_116(mc, g, cx, cz):
            continue
        if pl and g["placement"] not in pl:
            continue
        if ks and g["kind"] not in ks:
            continue
        if comp:
            c = rpl._center(g["box"])
            ch = (cx, cz) if int(mc) <= 20 else (c[0] >> 4, c[2] >> 4)
            name = rpl._STRUCT[g["kind"]] if int(mc) >= 22 else "ruined_portal"
            ls = sc._next_long(sc._rng(seed, mc, ch[0], ch[1], name))
            slots, _ap = le.fill_chest("ruined_portal", ls, ver)
            obs, light = chest_stats(slots)
            if not light or obs < min_missing(g["template"]):
                continue
        return True
    return False


def ok(params, seed, mc, bx, bz, large=None, ver=None) -> bool:
    """Does the ruined portal started at block (bx, bz) pass the condition's filters?"""
    import loot_engine as le
    p = params or {}
    mc = int(mc)
    if mc < 19:
        return False                              # no ruined portals before 1.16
    cx, cz = int(bx) >> 4, int(bz) >> 4
    ver = ver or le.loot_version(mc)
    lo, hi = bounds(p, "crying")
    deep = bool(p.get("completable")) or lo is not None or hi is not None
    try:
        if not _cheap_ok(p, seed, mc, cx, cz, ver):
            return False
        if not deep:
            if mc < 22:                           # 1.16 / 1.17: the candidate is the portal
                return True
            import ruined_portal_loot as rpl
            g = rpl.layout(seed, mc, cx, cz)
            a = None if g is None else dict(kind=g["kind"], placement=g["placement"])
        else:
            a = analyze(seed, mc, cx, cz, ver, chest=bool(p.get("completable")))
    except Exception:                             # noqa: BLE001
        return False
    if a is None:
        return False
    pl, ks = placements_of(p), kinds_of(p)
    if pl and a["placement"] not in pl:
        return False
    if ks and a["kind"] not in ks:
        return False
    if lo is not None or hi is not None:
        c = a["frame"]["crying"] if a["frame"] else None
        if c is None or (lo is not None and c < lo) or (hi is not None and c > hi):
            return False
    if p.get("completable") and not a["completable"]:
        return False
    return True


def describe(a) -> str:
    """one line for a result's tooltip"""
    if not a:
        return "No ruined portal"
    f = a["frame"]
    s = (f"{KIND_NAMES.get(a['kind'], a['kind'])} · {PLACEMENT_NAMES.get(a['placement'], a['placement'])}"
         f" · {a['template'].replace('_', ' ')}")
    if f:
        s += (f" · frame {f['size'][0]}×{f['size'][1]}: {f['missing']} missing, "
              f"{f['crying']} crying → {f['need']} obsidian")
    if a["chest_ok"]:
        s += f" · chest: {a['obsidian']} obsidian" + (", can light" if a["light"] else "")
    else:
        s += " · no chest"
    if a["completable"]:
        s += " · completable"
    return s


# ── (07/11) the exact odds of a filter, for the panel ───────────────────────────
# Placement, type, template and the netherrack's bites come from real portals
# (loot_tables/ruined_portal_stats.json, tools/loot/build_portal_stats.py); the frame's
# crying obsidian is computed over every crying pattern (each obsidian independently,
# p = P(nextFloat() < 0.15f)) and the chest's contents with loot_exact. So a filter
# rarer than the sample still gets its probability.
P_CRY = 2516583 / float(1 << 24)                  # k / 2^24 < f32(0.15): k ≤ 2 516 582
_ST = None
_CHEST = {}


def _stats():
    global _ST
    if _ST is None:
        try:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables",
                             "ruined_portal_stats.json")
            with open(p) as f:
                _ST = json.load(f)
        except Exception:                          # noqa: BLE001
            _ST = {}
    return _ST


def stats_rows(mc):
    """(portals measured, [(placement, kind, chest_ok, need, crying, probability)]):
    real portals' placements, types and netherrack (all its scenarios) × the exact
    frame distribution over the crying patterns (tools/loot/build_portal_stats.py)"""
    g = (_stats().get("groups") or {}).get("116" if int(mc) < 22 else "118") or {}
    return int(g.get("n") or 0), [(r[0], r[1], int(r[2]), int(r[3]), int(r[4]), float(r[5]))
                                  for r in g.get("dist", ())]


def chest_odds(mc, ver=None):
    """[P(the chest holds ≥ k obsidian and something to light the portal)] k = 0..17
    (loot_exact, inclusion–exclusion over the three ways to light it)"""
    import loot_engine as le
    import loot_exact as lx
    ver = ver or le.loot_version(mc)
    if ver in _CHEST:
        return _CHEST[ver]
    ways = ([dict(item="flint_and_steel", min=1)], [dict(item="fire_charge", min=1)],
            [dict(item="flint", min=1), dict(item="iron_nugget", min=9)])
    import itertools
    out = []
    for k in range(18):
        base = [dict(item="obsidian", min=k)] if k else []
        tot = 0.0
        for r in (1, 2, 3):
            for combo in itertools.combinations(ways, r):
                items = base + [w for way in combo for w in way]
                res = lx.chest_prob(dict(items=items, mode="find"), "ruined_portal", ver)
                tot += (-1) ** (r + 1) * float(res.p)
        out.append(max(0.0, tot))
    _CHEST[ver] = out
    return out


_ODDS = {}


def odds(mc, placements=(), kinds=(), cry_lo=None, cry_hi=None, completable=False):
    """the probability that a ruined portal passes these filters (None: no data)"""
    key = ("o", int(mc) < 22, int(mc), tuple(sorted(placements or ())), tuple(sorted(kinds or ())),
           cry_lo, cry_hi, bool(completable))
    if key not in _ODDS:
        _ODDS[key] = _odds(mc, placements, kinds, cry_lo, cry_hi, completable)
    return _ODDS[key]


def _odds(mc, placements=(), kinds=(), cry_lo=None, cry_hi=None, completable=False):
    n, rows = stats_rows(mc)
    if not n:
        return None
    ch = chest_odds(mc) if completable else None
    tot = 0.0
    for pl, k, chest_ok, need, c, pr in rows:
        if placements and pl not in placements:
            continue
        if kinds and k not in kinds:
            continue
        if completable and not chest_ok:
            continue
        if cry_lo is not None and c < cry_lo:
            continue
        if cry_hi is not None and c > cry_hi:
            continue
        tot += pr * (ch[min(need, 17)] if completable else 1.0)
    return tot


def crying_odds(mc, placements=(), kinds=(), completable=False, n=6):
    """[P(frame with exactly c crying obsidian (n = n or more) | the other filters)]"""
    key = ("c", int(mc), tuple(sorted(placements or ())), tuple(sorted(kinds or ())),
           bool(completable), n)
    if key not in _ODDS:
        _ODDS[key] = _crying_odds(mc, placements, kinds, completable, n)
    return list(_ODDS[key])


def _crying_odds(mc, placements=(), kinds=(), completable=False, n=6):
    _n, rows = stats_rows(mc)
    out = [0.0] * (n + 1)
    ch = chest_odds(mc) if completable else None
    for pl, k, chest_ok, need, c, pr in rows:
        if placements and pl not in placements:
            continue
        if kinds and k not in kinds:
            continue
        if completable and not chest_ok:
            continue
        out[min(c, n)] += pr * (ch[min(need, 17)] if completable else 1.0)
    t = sum(out)
    return [v / t if t else 0.0 for v in out]
