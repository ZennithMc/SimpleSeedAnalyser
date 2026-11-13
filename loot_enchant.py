"""
loot_enchant.py — the enchantments of chest loot (26/10).

Two systems:
  1.16 – 1.20.6  hard-coded in the game (Enchantments registration order,
                 rarity weights, cost formulas, categories, compatibility);
                 transcribed below and cross-checked against the 1.21 data
                 (same costs / weights / levels: tools/loot/check_enchant.py).
  1.21 +         data-driven: enchantment/*.json + their tags
                 (loot_tables/enchant.json.gz, tools/loot/build_loot_data.py).

The loot functions (read in the 26.3 jar; the same draws in 1.16):
  enchant_randomly   candidates = the options (in order) or the registry,
                     without the ones that cannot go on the item (not for a
                     book); one at random: nextInt(n); level: Mth.nextInt(min, max).
  enchant_with_levels  EnchantmentHelper.selectEnchantment:
                     cost += 1 + nextInt(e/4+1) + nextInt(e/4+1)  (e: enchantability)
                     cost = round(cost × (1 + (nextFloat()+nextFloat()-1) × 0.15)), ≥ 1
                     candidates: each enchantment (primary item or book) at its
                     highest level whose [min cost, max cost] holds the cost;
                     one by weight (nextInt(total)); then while nextInt(50) <= cost:
                     drop the incompatible ones, pick another, cost /= 2.
"""
from __future__ import annotations

import gzip
import json
import os

COMMON, UNCOMMON, RARE, VERY_RARE = 10, 5, 2, 1


def _d(f):
    return f


# ── legacy (1.16 – 1.20.6) ──────────────────────────────────────────────────
def _std_min(l):           # Enchantment.getMinCost default
    return 1 + l * 10


# id, weight, max level, min(l), max(l), category, treasure, discoverable
_LEGACY = [
    ("protection",            COMMON,    4, lambda l: 1 + (l - 1) * 11,  lambda l: 1 + (l - 1) * 11 + 11,  "armor",  0, 1),
    ("fire_protection",       UNCOMMON,  4, lambda l: 10 + (l - 1) * 8,  lambda l: 10 + (l - 1) * 8 + 8,   "armor",  0, 1),
    ("feather_falling",       UNCOMMON,  4, lambda l: 5 + (l - 1) * 6,   lambda l: 5 + (l - 1) * 6 + 6,    "feet",   0, 1),
    ("blast_protection",      RARE,      4, lambda l: 5 + (l - 1) * 8,   lambda l: 5 + (l - 1) * 8 + 8,    "armor",  0, 1),
    ("projectile_protection", UNCOMMON,  4, lambda l: 3 + (l - 1) * 6,   lambda l: 3 + (l - 1) * 6 + 6,    "armor",  0, 1),
    ("respiration",           RARE,      3, lambda l: 10 * l,            lambda l: 10 * l + 30,            "head",   0, 1),
    ("aqua_affinity",         RARE,      1, lambda l: 1,                 lambda l: 41,                     "head",   0, 1),
    ("thorns",                VERY_RARE, 3, lambda l: 10 + 20 * (l - 1), lambda l: _std_min(l) + 50,       "chest",  0, 1),
    ("depth_strider",         RARE,      3, lambda l: 10 * l,            lambda l: 10 * l + 15,            "feet",   0, 1),
    ("frost_walker",          RARE,      2, lambda l: 10 * l,            lambda l: 10 * l + 15,            "feet",   1, 1),
    ("binding_curse",         VERY_RARE, 1, lambda l: 25,                lambda l: 50,                     "wearable", 1, 1),
    ("soul_speed",            VERY_RARE, 3, lambda l: 10 * l,            lambda l: 10 * l + 15,            "feet",   1, 0),
    ("swift_sneak",           VERY_RARE, 3, lambda l: 25 * l,            lambda l: 25 * l + 50,            "legs",   1, 0),
    ("sharpness",             COMMON,    5, lambda l: 1 + (l - 1) * 11,  lambda l: 1 + (l - 1) * 11 + 20,  "weapon", 0, 1),
    ("smite",                 UNCOMMON,  5, lambda l: 5 + (l - 1) * 8,   lambda l: 5 + (l - 1) * 8 + 20,   "weapon", 0, 1),
    ("bane_of_arthropods",    UNCOMMON,  5, lambda l: 5 + (l - 1) * 8,   lambda l: 5 + (l - 1) * 8 + 20,   "weapon", 0, 1),
    ("knockback",             UNCOMMON,  2, lambda l: 5 + 20 * (l - 1),  lambda l: _std_min(l) + 50,       "weapon", 0, 1),
    ("fire_aspect",           RARE,      2, lambda l: 10 + 20 * (l - 1), lambda l: _std_min(l) + 50,       "weapon", 0, 1),
    ("looting",               RARE,      3, lambda l: 15 + (l - 1) * 9,  lambda l: _std_min(l) + 50,       "weapon", 0, 1),
    ("sweeping",              RARE,      3, lambda l: 5 + (l - 1) * 9,   lambda l: 5 + (l - 1) * 9 + 15,   "weapon", 0, 1),
    ("efficiency",            COMMON,    5, lambda l: 1 + 10 * (l - 1),  lambda l: _std_min(l) + 50,       "digger", 0, 1),
    ("silk_touch",            VERY_RARE, 1, lambda l: 15,                lambda l: _std_min(l) + 50,       "digger", 0, 1),
    ("unbreaking",            UNCOMMON,  3, lambda l: 5 + (l - 1) * 8,   lambda l: _std_min(l) + 50,       "breakable", 0, 1),
    ("fortune",               RARE,      3, lambda l: 15 + (l - 1) * 9,  lambda l: _std_min(l) + 50,       "digger", 0, 1),
    ("power",                 COMMON,    5, lambda l: 1 + (l - 1) * 10,  lambda l: 1 + (l - 1) * 10 + 15,  "bow",    0, 1),
    ("punch",                 RARE,      2, lambda l: 12 + (l - 1) * 20, lambda l: 12 + (l - 1) * 20 + 25, "bow",    0, 1),
    ("flame",                 RARE,      1, lambda l: 20,                lambda l: 50,                     "bow",    0, 1),
    ("infinity",              VERY_RARE, 1, lambda l: 20,                lambda l: 50,                     "bow",    0, 1),
    ("luck_of_the_sea",       RARE,      3, lambda l: 15 + (l - 1) * 9,  lambda l: _std_min(l) + 50,       "fishing_rod", 0, 1),
    ("lure",                  RARE,      3, lambda l: 15 + (l - 1) * 9,  lambda l: _std_min(l) + 50,       "fishing_rod", 0, 1),
    ("loyalty",               UNCOMMON,  3, lambda l: 5 + l * 7,         lambda l: 50,                     "trident", 0, 1),
    ("impaling",              RARE,      5, lambda l: 1 + (l - 1) * 8,   lambda l: 1 + (l - 1) * 8 + 20,   "trident", 0, 1),
    ("riptide",               RARE,      3, lambda l: 10 + l * 7,        lambda l: 50,                     "trident", 0, 1),
    ("channeling",            VERY_RARE, 1, lambda l: 25,                lambda l: 50,                     "trident", 0, 1),
    ("multishot",             RARE,      1, lambda l: 20,                lambda l: 50,                     "crossbow", 0, 1),
    ("quick_charge",          UNCOMMON,  3, lambda l: 12 + (l - 1) * 20, lambda l: 50,                     "crossbow", 0, 1),
    ("piercing",              COMMON,    4, lambda l: 1 + (l - 1) * 10,  lambda l: 50,                     "crossbow", 0, 1),
    ("mending",               RARE,      1, lambda l: l * 25,            lambda l: l * 25 + 50,            "breakable", 1, 1),
    ("vanishing_curse",       VERY_RARE, 1, lambda l: 25,                lambda l: 50,                     "vanishable", 1, 1),
]
_PROT = {"protection": "all", "fire_protection": "fire", "feather_falling": "fall",
         "blast_protection": "explosion", "projectile_protection": "projectile"}
_DAMAGE = {"sharpness", "smite", "bane_of_arthropods"}


def _legacy_compat_one(a, b):
    """a.checkCompatibility(b) (1.16 – 1.20)."""
    if a == b:
        return False
    if a in _PROT and b in _PROT:
        return _PROT[a] == "fall" or _PROT[b] == "fall"
    if a in _DAMAGE:
        return b not in _DAMAGE
    if a == "depth_strider":
        return b != "frost_walker"
    if a == "frost_walker":
        return b != "depth_strider"
    if a == "silk_touch":
        return b != "fortune"
    if a in ("fortune", "looting", "luck_of_the_sea"):
        return b != "silk_touch"
    if a == "infinity":
        return b != "mending"
    if a == "riptide":
        return b not in ("loyalty", "channeling")
    if a == "multishot":
        return b != "piercing"
    if a == "piercing":
        return b != "multishot"
    return True


# ── items ───────────────────────────────────────────────────────────────────
_ARMOR = ("_helmet", "_chestplate", "_leggings", "_boots")
_TOOLS = ("_pickaxe", "_axe", "_shovel", "_hoe")


def _base(it):
    return it.replace("minecraft:", "")


def is_armor(it):
    return _base(it).endswith(_ARMOR) and "horse" not in it


def _damageable(it):
    import loot_engine as le
    return le.max_damage(it) is not None or _base(it) in ("shield",)


def legacy_category(cat, it, override=None) -> bool:
    it = _base(it)
    if cat == "armor":
        return is_armor(it)
    if cat == "feet":
        return it.endswith("_boots")
    if cat == "legs":
        return it.endswith("_leggings")
    if cat == "chest":
        return it.endswith("_chestplate")
    if cat == "head":
        return it.endswith("_helmet")
    if cat == "weapon":
        return it.endswith("_sword")
    if cat == "digger":
        return it.endswith(_TOOLS)
    if cat == "fishing_rod":
        return it == "fishing_rod"
    if cat == "trident":
        return it == "trident"
    if cat == "bow":
        return it == "bow"
    if cat == "crossbow":
        return it == "crossbow"
    if cat == "breakable":
        return _damageable(it)
    if cat == "wearable":
        return is_armor(it) or it in ("elytra", "carved_pumpkin") or it.endswith(("_head", "_skull"))
    if cat == "vanishable":
        return (_damageable(it) or is_armor(it) or it in ("compass", "carrot_on_a_stick", "elytra",
                                                         "carved_pumpkin") or it.endswith(("_head", "_skull")))
    return False


def legacy_can_enchant(eid, cat, it) -> bool:
    """Enchantment.canEnchant(stack) with the 1.16 – 1.20 overrides."""
    b = _base(it)
    if eid in _DAMAGE and b.endswith("_axe"):
        return True
    if eid == "efficiency" and b == "shears":
        return True
    if eid == "thorns" and is_armor(b):
        return True
    if eid == "unbreaking" and _damageable(b):
        return True
    return legacy_category(cat, b)


_ENCHANTABILITY_TOOL = {"wooden": 15, "stone": 5, "iron": 14, "golden": 22, "diamond": 10,
                        "netherite": 15, "copper": 13}
_ENCHANTABILITY_ARMOR = {"leather": 15, "chainmail": 12, "iron": 9, "golden": 25, "diamond": 10,
                         "netherite": 15, "turtle": 9, "copper": 8}


def enchantability(it) -> int:
    b = _base(it)
    if b in ("book", "bow", "crossbow", "fishing_rod", "trident"):
        return 1
    if b == "mace":
        return 15
    for suf in _ARMOR:
        if b.endswith(suf):
            return _ENCHANTABILITY_ARMOR.get(b[:-len(suf)], 0)
    for suf in _TOOLS + ("_sword", "_spear"):
        if b.endswith(suf):
            return _ENCHANTABILITY_TOOL.get(b[:-len(suf)], 0)
    return 0


# ── one enchantment, both systems ───────────────────────────────────────────
class Ench:
    __slots__ = ("id", "weight", "max_level", "_min", "_max", "supported", "primary", "excl",
                 "treasure", "discoverable", "legacy", "cat")

    def min_cost(self, l):
        return self._min(l)

    def max_cost(self, l):
        return self._max(l)


class Env:
    """The enchantments of one version."""

    def __init__(self, version):
        self.version = version
        self.legacy = _vt(version) < (1, 21)
        self.items = {}
        if self.legacy:
            sw = _vt(version) >= (1, 19)
            self.order = []
            for eid, w, mx, mn, mxc, cat, tr, disc in _LEGACY:
                if eid == "swift_sneak" and not sw:
                    continue
                e = Ench()
                e.id, e.weight, e.max_level, e._min, e._max = eid, w, mx, mn, mxc
                e.cat, e.treasure, e.discoverable, e.legacy = cat, bool(tr), bool(disc), True
                self.order.append(e)
            if _vt(version) >= (1, 20, 5):
                # 1.20.5 moved the definitions to Enchantment.definition(… dynamicCost …):
                # the 1.21 data's costs (the « super.getMinCost + 50 » maxima changed)
                d21 = Env("1.21")
                for e in self.order:
                    n = d21.by_id.get("sweeping_edge" if e.id == "sweeping" else e.id)
                    if n is not None:
                        e._min, e._max = n._min, n._max
        else:
            self._load()

    # data-driven ─────────────────────────────────────────────────────────
    def _load(self):
        d = _edata()
        ver = self.version if self.version in d["versions"] else _closest(self.version, d["versions"])
        files = d["versions"][ver]
        self.files = {k: d["blobs"][h] for k, h in files.items()}
        self.order = []
        for name in sorted(k for k in self.files if k.startswith("enchantment/")):
            j = self.files[name]
            e = Ench()
            e.id = name.split("/", 1)[1]
            e.weight = int(j.get("weight", 1))
            e.max_level = int(j.get("max_level", 1))
            mn, mx = j.get("min_cost", {}), j.get("max_cost", {})
            e._min = lambda l, mn=mn: int(mn.get("base", 0)) + int(mn.get("per_level_above_first", 0)) * (l - 1)
            e._max = lambda l, mx=mx: int(mx.get("base", 0)) + int(mx.get("per_level_above_first", 0)) * (l - 1)
            e.supported = j.get("supported_items")
            e.primary = j.get("primary_items", e.supported)
            e.excl = j.get("exclusive_set")
            e.legacy = False
            self.order.append(e)
        self.by_id = {e.id: e for e in self.order}

    def _tag_values(self, kind, name, seen=None):
        """A tag's elements, in the game's order (entries in order, nested tags
        expanded in place, the first occurrence kept)."""
        name = name.replace("minecraft:", "").lstrip("#")
        j = self.files.get(f"tags/{kind}/{name}")
        if j is None:
            return []
        out = []
        seen = set() if seen is None else seen
        for v in j.get("values", []):
            v = v["id"] if isinstance(v, dict) else v
            if v.startswith("#"):
                for x in self._tag_values(kind, v, set()):
                    if x not in seen:
                        seen.add(x); out.append(x)
            else:
                x = v.replace("minecraft:", "")
                if x not in seen:
                    seen.add(x); out.append(x)
        return out

    def _holderset(self, kind, spec):
        if spec is None:
            return None
        if isinstance(spec, list):
            return [s.replace("minecraft:", "") for s in spec]
        if spec.startswith("#"):
            return self._tag_values(kind, spec)
        return [spec.replace("minecraft:", "")]

    def item_in(self, spec, it) -> bool:
        key = (str(spec), _base(it))
        if key not in self.items:
            vals = self._holderset("item", spec)
            self.items[key] = vals is not None and _base(it) in vals
        return self.items[key]

    # common API ─────────────────────────────────────────────────────────
    def options(self, spec):
        """The enchantments of a loot function's options (None: the whole registry)."""
        if self.legacy:
            if spec is None:
                return None
            ids = spec if isinstance(spec, list) else [spec]
            byid = {e.id: e for e in self.order}
            return [byid[i.replace("minecraft:", "")] for i in ids if i.replace("minecraft:", "") in byid]
        if spec is None:
            return None
        ids = self._holderset("enchantment", spec)
        return [self.by_id[i] for i in ids if i in self.by_id]

    def can_enchant(self, e, it) -> bool:
        if e.legacy:
            return legacy_can_enchant(e.id, e.cat, it)
        return self.item_in(e.supported, it)

    def is_primary(self, e, it) -> bool:
        if e.legacy:
            return legacy_category(e.cat, it)
        return self.item_in(e.primary, it)

    def compatible(self, a, b) -> bool:
        if a.legacy:
            return _legacy_compat_one(a.id, b.id) and _legacy_compat_one(b.id, a.id)
        if a.id == b.id:
            return False
        ea = self._holderset("enchantment", a.excl) or []
        eb = self._holderset("enchantment", b.excl) or []
        return b.id not in ea and a.id not in eb


def _vt(v):
    return tuple(int(x) for x in v.split("."))


def _closest(v, have):
    t = _vt(v)
    best = None
    for h in have:
        if _vt(h) <= t and (best is None or _vt(h) > _vt(best)):
            best = h
    return best or sorted(have, key=_vt)[0]


_ED = None


def _edata():
    global _ED
    if _ED is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "enchant.json.gz")
        with gzip.open(p, "rt", encoding="utf-8") as f:
            _ED = json.load(f)
    return _ED


_ENVS = {}


def env(version) -> Env:
    if version not in _ENVS:
        _ENVS[version] = Env(version)
    return _ENVS[version]


# ── the two loot functions ──────────────────────────────────────────────────
def _weighted(rnd, lst):
    tot = sum(w for _x, w in lst)
    if tot <= 0:
        return None
    i = rnd.next_int(tot)
    for x, w in lst:
        i -= w
        if i < 0:
            return x
    return None


def randomly_candidates(item, f, version):
    """enchant_randomly's candidates for `item` (shared with loot_exact)"""
    E = env(version)
    is_book = item == "book"
    spec = f.get("options", f.get("enchantments"))
    opts = E.options(spec)
    only_compat = bool(f.get("only_compatible", True))
    if opts is None:                                    # the registry
        if E.legacy:
            cand = [e for e in E.order if e.discoverable and (is_book or E.can_enchant(e, item))]
        else:
            cand = [e for e in E.order if is_book or E.can_enchant(e, item)]
    elif E.legacy:
        cand = list(opts)                               # a given list: no filter (1.16 – 1.20.4)
        if _vt(version) >= (1, 20, 5):
            cand = [e for e in cand if is_book or not only_compat or E.can_enchant(e, item)]
    else:
        cand = [e for e in opts if is_book or not only_compat or E.can_enchant(e, item)]
    return cand


def enchant_randomly(st, f, ctx):
    """EnchantRandomlyFunction.run (1.16 – 26.3). Returns the enchantments added."""
    import loot_engine as le
    rnd = ctx.rnd
    is_book = st.item == "book"
    cand = randomly_candidates(st.item, f, ctx.version)
    if not cand:
        return []
    e = cand[rnd.next_int(len(cand))]
    lvl = le.mth_next_int(rnd, 1, e.max_level)
    if is_book:
        st.item = "enchanted_book"
    return [(e.id, lvl)]


def select_enchantment(rnd, item, cost, ctx, opts, treasure):
    E = env(ctx.version)
    ev = enchantability(item)
    if ev <= 0:
        return []
    cost += 1 + rnd.next_int(ev // 4 + 1) + rnd.next_int(ev // 4 + 1)
    cost = max(1, _cost_round(cost, _span(rnd)))
    avail = available(item, cost, ctx.version, opts, treasure)
    res = []
    if avail:
        x = _weighted(rnd, [(a, a[0].weight) for a in avail])
        if x is not None:
            res.append(x)
        while rnd.next_int(50) <= cost:
            if res:
                last = res[-1][0]
                avail = [a for a in avail if E.compatible(last, a[0])]
            if not avail:
                break
            x = _weighted(rnd, [(a, a[0].weight) for a in avail])
            if x is not None:
                res.append(x)
            cost //= 2
    return [(e.id, l) for e, l in res]


def available(item, cost, version, opts, treasure):
    """getAvailableEnchantmentResults: (enchantment, level) at this cost (shared with loot_exact)"""
    E = env(version)
    is_book = _base(item) == "book"
    if E.legacy:
        pool = [e for e in E.order if (not e.treasure or treasure) and e.discoverable
                and (is_book or E.is_primary(e, item))]
    else:
        pool = [e for e in (opts if opts is not None else E.order) if is_book or E.is_primary(e, item)]
    avail = []
    for e in pool:
        for l in range(e.max_level, 0, -1):
            if e.min_cost(l) <= cost <= e.max_cost(l):
                avail.append((e, l))
                break
    return avail


def _f32(x: float) -> float:
    import struct
    return struct.unpack("f", struct.pack("f", x))[0]


_F015 = _f32(0.15)


def _span(rnd) -> float:
    """(nextFloat() + nextFloat() - 1.0f) * 0.15f, in float arithmetic."""
    a = _f32(rnd.next_float() + rnd.next_float())
    return _f32(_f32(a - 1.0) * _F015)


def _cost_round(cost: int, span: float) -> int:
    """Math.round((float) cost + (float) cost * span)."""
    import math
    c = _f32(float(cost))
    return int(math.floor(_f32(_f32(c + _f32(c * span)) + 0.5)))


def enchant_with_levels(st, f, ctx):
    import loot_engine as le
    E = env(ctx.version)
    lv = le.num_int(f.get("levels", 0), ctx)
    opts = E.options(f.get("options")) if not E.legacy else None
    treasure = bool(f.get("treasure", False))
    res = select_enchantment(ctx.rnd, st.item, lv, ctx, opts, treasure)
    if st.item == "book":
        st.item = "enchanted_book"
    return res
