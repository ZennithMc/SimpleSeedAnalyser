"""
loot_stats.py — (06/11) what a loot table can give, exactly: for every item, the
chance a chest has at least one, the mean count per chest, the stack sizes, and its
share of each pool's rolls. Computed from the table (no sampling):

    per roll   a = Σ entries  (weight / total) · P(count ≥ 1)
    per pool   P = Σ_R P(rolls = R) · (1 − (1 − a)^R)      (rolls are independent)
    per chest  P = 1 − Π_pools (1 − P_pool),   mean = Σ E[rolls] · Σ weight share · E[count]

Nested tables (loot_table entries) are folded in the same way. Random-chance
conditions on an entry scale its weight (the few such entries are marked ≈).

    stats = table_stats("1.21.4", "desert_pyramid")
    for s in stats.items: s.item, s.label, s.chance, s.mean, s.cmin, s.cmax
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import loot_engine as le

ENCHANT_FN = ("enchant_randomly", "enchant_with_levels", "set_enchantments")


# ── number providers → distributions ───────────────────────────────────────────
def _dist(p) -> dict:
    """{value: probability} of an integer number provider (Mth.nextInt bounds)"""
    if isinstance(p, (int, float)):
        return {int(p // 1): 1.0}
    t = le._typ(p, "type")
    if t in ("", "uniform") and "min" in p and "max" in p:
        lo, hi = _bound(p["min"]), _bound(p["max"])
        if hi < lo:
            return {lo: 1.0}
        n = hi - lo + 1
        return {v: 1.0 / n for v in range(lo, hi + 1)}
    if t == "constant":
        return {int(p.get("value", 0)): 1.0}
    if t == "binomial":
        n = _bound(p.get("n", 0))
        q = float(p.get("p", 0)) if isinstance(p.get("p"), (int, float)) else 0.5
        return {k: math.comb(n, k) * q ** k * (1 - q) ** (n - k) for k in range(n + 1)}
    return {1: 1.0}


def _bound(p) -> int:
    if isinstance(p, (int, float)):
        return int(p // 1)
    d = _dist(p)
    return int(round(sum(k * v for k, v in d.items())))


def _mean(d):
    return sum(k * v for k, v in d.items())


# ── one item entry ────────────────────────────────────────────────────────────
def _entry_item(e, version):
    """(key, label, count distribution, extras) of an item entry"""
    item = str(e.get("name", "")).replace("minecraft:", "")
    count = {1: 1.0}
    label = ""
    enchanted = False
    potion = None
    name_key = None
    for f, t in le._functions(e):
        if t == "set_count":
            d = _dist(f.get("count", 1))
            if f.get("add"):
                count = {a + b: pa * pb for a, pa in count.items() for b, pb in d.items()} \
                    if len(count) * len(d) < 4096 else d
            else:
                count = d
        elif t in ENCHANT_FN:
            enchanted = True
            opts = f.get("options", f.get("enchantments"))
            if t == "set_enchantments" and isinstance(opts, dict):
                label = ", ".join(f"{k.replace('minecraft:', '').replace('_', ' ').title()} "
                                  f"{le.roman(v) if isinstance(v, int) else ''}".strip() for k, v in opts.items())
            elif isinstance(opts, list) and opts:
                label = " / ".join(str(o).replace("minecraft:", "").replace("_", " ").title() for o in opts)
            if t == "enchant_with_levels":
                lv = f.get("levels")
                d = _dist(lv) if lv is not None else {}
                if d:
                    label = f"lvl {min(d)}–{max(d)}" if min(d) != max(d) else f"lvl {min(d)}"
                if f.get("treasure"):
                    label += " · treasure"
        elif t == "set_potion":
            potion = str(f.get("id", "")).replace("minecraft:", "")
        elif t == "exploration_map":
            item = "filled_map"                  # the function makes a filled map
            dest = str(f.get("destination", "")).replace("minecraft:", "").replace("#", "")
            label = label or dest.replace("on_", "").replace("_maps", "").replace("_", " ")
        elif t == "set_name":
            nm = f.get("name")
            if isinstance(nm, dict) and nm.get("translate"):
                name_key = nm["translate"]
        elif t == "set_stew_effect":
            label = "random effect"
        elif t == "set_instrument":
            label = "random sound"
        elif t == "set_ominous_bottle_amplifier":
            d = _dist(f.get("amplifier", 0))
            label = f"level {min(d) + 1}–{max(d) + 1}" if len(d) > 1 else f"level {min(d) + 1}"
        elif t == "set_damage":
            label = label or "damaged"
        elif t == "set_item":
            item = str(f.get("item", item)).replace("minecraft:", "")
    if item == "book" and enchanted:
        item = "enchanted_book"                  # an enchanted book becomes one
    key = (item, potion or "", name_key or "", "ench" if enchanted else "", label)
    return key, dict(item=item, label=label, enchanted=enchanted, potion=potion, name_key=name_key), count


@dataclass
class ItemStat:
    item: str
    label: str = ""
    enchanted: bool = False
    potion: str | None = None
    name_key: str | None = None
    chance: float = 0.0              # P(at least one in the chest)
    mean: float = 0.0                # mean count per chest
    cmin: int = 0                    # stack size range when picked
    cmax: int = 0
    share: list = field(default_factory=list)       # [(pool index, per-roll %)]
    approx: bool = False


@dataclass
class PoolInfo:
    rolls: tuple                     # (min, max)
    mean_rolls: float
    weight_total: int
    empty_share: float               # share of the rolls that give nothing


@dataclass
class TableStats:
    name: str
    version: str
    items: list
    pools: list
    approx: bool = False


def _biomes(c):
    pr = c.get("predicate") or {}
    b = pr.get("biomes") or pr.get("biome") or []
    b = b if isinstance(b, list) else [b]
    return [str(x).replace("minecraft:", "").lstrip("#") for x in b]


def _ctype(c):
    t = c.get("condition", c.get("type")) if isinstance(c, dict) else None
    return t.replace("minecraft:", "") if isinstance(t, str) else ""


def _cond(c, biome):
    """(probability the condition holds, approximate?) — biome: the chest's biome
    (None: one no table names)"""
    t = _ctype(c)
    if t == "random_chance":
        return float(c.get("chance", 1)), True
    if t == "location_check":
        return (1.0 if biome is not None and biome in _biomes(c) else 0.0), False
    if t == "inverted":
        v, ap = _cond(c.get("term") or {}, biome)
        return 1.0 - v, ap
    if t == "all_of":
        v, ap = 1.0, False
        for x in c.get("terms", []):
            a, b = _cond(x, biome)
            v, ap = v * a, ap or b
        return v, ap
    if t in ("any_of", "alternative"):
        v, ap = 1.0, False
        for x in c.get("terms", []):
            a, b = _cond(x, biome)
            v, ap = v * (1 - a), ap or b
        return 1 - v, ap
    return 1.0, True


def _expand(entries, out, scale=1.0, biome=None):
    """flatten alternatives / groups / sequences (rare in chests) with their weights"""
    for e in entries or []:
        t = le._typ(e, "type")
        k = scale
        approx = False
        for c in le._conditions(e):
            v, ap = _cond(c, biome)
            k *= v
            approx = approx or (ap and 0 < v < 1)
        if t in ("alternatives", "group", "sequence"):
            _expand(e.get("children"), out, k, biome)
            continue
        out.append((e, k, approx))


def _biome_of(tab):
    """the biomes named by location_check conditions of a table"""
    out = []

    def walk(o):
        if isinstance(o, dict):
            if _ctype(o) == "location_check":
                out.extend(_biomes(o))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(tab)
    return out


def _analyze(tab, version, depth=0, biome=None):
    """{key: [meta, p_present, mean, cmin, cmax, shares, approx]}, pools"""
    res = {}
    pools = []
    if depth > 6 or tab is None:
        return res, pools
    for pi, pool in enumerate(tab.get("pools") or []):
        rolls = _dist(pool.get("rolls", 1))
        pk = 1.0
        for c in le._conditions(pool):
            pk *= _cond(c, biome)[0]
        er = _mean(rolls) * pk
        ents = []
        _expand(pool.get("entries"), ents, 1.0, biome)
        weights = [(e, max(0, int(e.get("weight", 1))) * k, ap) for e, k, ap in ents]
        W = sum(w for _e, w, _a in weights)
        if W <= 0:
            continue
        pools.append(PoolInfo((min(rolls), max(rolls)), er, int(round(W)),
                              sum(w for e, w, _a in weights if le._typ(e, "type") == "empty") / W))
        per = {}                              # key → [meta, a (per roll), b (mean/roll), cmin, cmax, share, approx]
        for e, w, ap in weights:
            if w <= 0:
                continue
            q = w / W if len(weights) > 1 else 1.0
            t = le._typ(e, "type")
            if t == "item":
                key, meta, cnt = _entry_item(e, version)
                pres = sum(v for k, v in cnt.items() if k >= 1)
                r = per.setdefault(key, [meta, 0.0, 0.0, 10 ** 9, 0, 0.0, False])
                r[1] += q * pres
                r[2] += q * _mean(cnt)
                pos = [k for k in cnt if k >= 1]
                if pos:
                    r[3] = min(r[3], min(pos))
                    r[4] = max(r[4], max(pos))
                r[5] += q
                r[6] = r[6] or ap
            elif t == "loot_table":
                ref = le._k(e, "value", "name")
                sub = le.get_table(version, ref) if isinstance(ref, str) else None
                sres, _sp = _analyze(sub, version, depth + 1, biome)
                for key, (meta, p, m, a0, a1, _sh, sap) in sres.items():
                    r = per.setdefault(key, [meta, 0.0, 0.0, 10 ** 9, 0, 0.0, False])
                    r[1] += q * p
                    r[2] += q * m
                    r[3] = min(r[3], a0)
                    r[4] = max(r[4], a1)
                    r[5] += q * p
                    r[6] = r[6] or ap or sap
        for key, (meta, a, b, c0, c1, sh, ap) in per.items():
            a = min(1.0, a)
            pp = pk * sum(pr * (1 - (1 - a) ** R) for R, pr in rolls.items())
            r = res.setdefault(key, [meta, 0.0, 0.0, 10 ** 9, 0, [], False])
            r[1] = 1 - (1 - r[1]) * (1 - pp)
            r[2] += er * b
            r[3] = min(r[3], c0)
            r[4] = max(r[4], c1)
            r[5].append((pi, sh))
            r[6] = r[6] or ap
    return res, pools


_CACHE = {}


def table_stats(version: str, name: str) -> TableStats | None:
    k = (version, name)
    if k in _CACHE:
        return _CACHE[k]
    tab = le.get_table(version, name)
    if tab is None:
        return None
    res, pools = _analyze(tab, version)
    items = []
    for key, (meta, p, m, c0, c1, sh, ap) in res.items():
        items.append(ItemStat(meta["item"], meta["label"], meta["enchanted"], meta["potion"],
                              meta["name_key"], p, m, c0 if c0 < 10 ** 9 else 0, c1, sh, ap))
    extra = {}
    for b in dict.fromkeys(_biome_of(tab)):      # items only in some biome: their chance there
        res2, _p2 = _analyze(tab, version, biome=b)
        for key, (meta, p, m, c0, c1, sh, ap) in res2.items():
            if key not in res and p > 0:
                x = extra.setdefault(key, [meta, p, m, c0, c1, sh, ap, []])
                x[7].append(b)
    for key, (meta, p, m, c0, c1, sh, ap, bs) in extra.items():
        lab = "in " + " / ".join(b.replace("_", " ").title() for b in bs)
        items.append(ItemStat(meta["item"], lab, meta["enchanted"], meta["potion"],
                              meta["name_key"], p, m, c0 if c0 < 10 ** 9 else 0, c1, sh, ap))
    items.sort(key=lambda s: (-s.chance, -s.mean, s.item))
    st = TableStats(name, version, items, pools, any(s.approx for s in items))
    _CACHE[k] = st
    return st


def tables(version: str) -> list:
    """the chest tables of a version (sorted by structure)"""
    return sorted(le._data()["versions"].get(version, {}).keys())
