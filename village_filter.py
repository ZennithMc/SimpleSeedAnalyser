"""
village_filter.py — (05/11) a village's houses and smiths, for the Village condition's
"Houses" / "Smiths" bounds.

The layout is the exact one of the game (jigsaw.layout: the structure random, the
village type from the biome at the start's centre, the real terrain heights — the
pieces' collisions depend on them, so on the WHOLE seed: two seeds with the same
48 low bits share the same houses only ~13 % of the time).

Houses: the pieces of the village's "houses" pools, without the farms, animal pens,
hay piles / accessories and meeting points (no building). Zombie villages: their own
houses pools count the same.
Forges (05/11, ed's choice): the smiths' houses that hold a loot chest — weaponsmith,
toolsmith or armorer, whichever has one in that village type (from the templates:
plains: the weaponsmith; desert: weaponsmith and toolsmith; savanna: one of the two
weaponsmith houses; snowy: the weaponsmith and one armorer house; taiga: one
weaponsmith house and the toolsmith).
"""
from __future__ import annotations

_NOT_HOUSE = ("farm", "animal_pen", "accessory", "meeting_point")
SMITH_KINDS = {"weaponsmith": ("weaponsmith", "weapon_smith"), "toolsmith": ("tool_smith",),
               "armorer": ("armorer",)}
_CACHE = {}


def _bound(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def bounds(params, key):
    r = (params or {}).get(key) or (None, None)
    r = list(r) + [None, None]
    return _bound(r[0]), _bound(r[1])


TYPES = ("plains", "desert", "savanna", "snowy", "taiga")


def types_of(params):
    """(05/11) the village types asked (none = any)."""
    return [t for t in ((params or {}).get("types") or ()) if t in TYPES]


def active(params) -> bool:
    """Does this Village condition ask for a type, houses or forges?"""
    if types_of(params) and len(types_of(params)) < len(TYPES):
        return True
    return any(b is not None for k in ("houses", "smiths") for b in bounds(params, k))


_TCACHE = {}


def vtype(seed, mc, bx, bz, large=None):
    """(05/11) the type of the village started at block (bx, bz) — "plains"… — or None
    (no village for the game there). Cheap: as jigsaw.layout, but the start piece
    only (its centre decides the biome test)."""
    import jigsaw
    k = (int(seed), int(mc), int(bx) >> 4, int(bz) >> 4, bool(large))
    if k in _TCACHE:
        return _TCACHE[k]
    cx, cz = int(bx) >> 4, int(bz) >> 4
    sk = None
    try:
        mc_ = int(mc)
        if not (jigsaw.MC_FIRST <= mc_ <= jigsaw.MC_LAST):
            sk = None
        elif mc_ < jigsaw.MC_OLD:
            sk = jigsaw._type116(seed, mc_, "village", cx, cz, jigsaw._gen(mc_), large)
        else:
            lay = jigsaw.layout_type(seed, mc_, "village", cx, cz, large)
            sk = lay
    except Exception:
        sk = None
    t = sk.split("_", 1)[1] if sk else None
    if len(_TCACHE) > 8192:
        _TCACHE.clear()
    _TCACHE[k] = t
    return t


def is_house(loc) -> bool:
    if not loc or "/houses/" not in loc:
        return False
    n = loc.rsplit("/", 1)[-1]
    return not any(k in n for k in _NOT_HOUSE)


def is_smith(loc, kind="any") -> bool:
    if not loc or "/houses/" not in loc:
        return False
    n = loc.rsplit("/", 1)[-1]
    keys = [k for v in SMITH_KINDS.values() for k in v] if kind in (None, "", "any") \
        else SMITH_KINDS.get(kind, ())
    return any(k in n for k in keys)


_FORGE_TABLES = ("village_weaponsmith", "village_toolsmith", "village_armorer")


def forge_chest(g, loc):
    """The forge loot table of a template ("village_weaponsmith"…) or None."""
    t = g.T.get(loc) if loc else None
    if not t:
        return None
    for c in t.get("cont", ()):
        tb = (c.get("table") or "").rsplit("/", 1)[-1]
        if tb in _FORGE_TABLES:
            return tb
    return None


def counts(seed, mc, bx, bz, large=None, kind="any"):
    """(houses, forges with a chest) of the village at block (bx, bz) — its start
    chunk — or None (no village there for the game: no valid biome, old version)."""
    k = (int(seed), int(mc), int(bx) >> 4, int(bz) >> 4, bool(large))
    v = _CACHE.get(k)
    if v is not None or k in _CACHE:
        return v
    import jigsaw
    try:
        lay = jigsaw.layout(int(seed), int(mc), "village", int(bx) >> 4, int(bz) >> 4, large)
    except Exception:
        lay = None
    if lay is None:
        v = None
    else:
        g = jigsaw._gen(mc)
        locs = [p.el.loc for p in lay[1] if p.el.kind in ("single", "legacy")]
        v = (sum(1 for l in locs if is_house(l)), sum(1 for l in locs if forge_chest(g, l)))
    if len(_CACHE) > 4096:
        _CACHE.clear()
    _CACHE[k] = v
    return v


def ok(params, seed, mc, bx, bz, large=None) -> bool:
    """The village at (bx, bz) has the asked number of houses and forges (with a chest)."""
    ts = types_of(params)
    if ts and len(ts) < len(TYPES):
        if vtype(seed, mc, bx, bz, large) not in ts:
            return False
    hl, hh = bounds(params, "houses")
    sl, sh = bounds(params, "smiths")
    if hl is None and hh is None and sl is None and sh is None:
        return True
    c = counts(seed, mc, bx, bz, large)
    if c is None:
        return False
    h, s = c
    return ((hl is None or h >= hl) and (hh is None or h <= hh)
            and (sl is None or s >= sl) and (sh is None or s <= sh))
