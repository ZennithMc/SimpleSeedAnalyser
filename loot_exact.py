"""
loot_exact.py — (07/11) the exact probability of a loot-finder query, per chest.

No sampling: every draw of the fill (loot_engine.fill_chest, the game's own
algorithm) is enumerated with its exact probability —
    pools: conditions, rolls (constant / uniform / binomial), entries kept by
           their conditions, one by weight;
    entries: set_count, the enchantments (enchant_randomly; enchant_with_levels
           with its cost bonus enumerated: the two nextFloat() of the ±15 %
           span, in float32, as 2^48 pairs counted exactly), potions, maps,
           set_item…, then the max-stack splitter;
    the chest: shuffleAndSplitItems (the stack splitter, step by step) and the
           shuffles (every arrangement equally likely).
The draws are taken as independent and uniform (a perfect random source). The
real fill uses java.util.Random (48-bit state, every version): an event the
model gives 0 is impossible in the game; a non-zero one far below 2^-48 may
not exist there (loot_finder's GPU proof is the next step).

Floats compare like Java (nextFloat() < float32(p): ceil(p·2^24) of 2^24).
location_check: the chest's biome (None = unknown → false, as the engine).

API
    chest_prob(q, table, version, biome=None) -> Result
        q: loot_finder's query (items, mode, layout)
    Result: p (float, exact up to the float64 rounding), impossible (bool),
            max_count (most of items[0] a chest can hold), dist (Most: count → p)
"""
from __future__ import annotations

import math
import struct
from collections import defaultdict
from functools import lru_cache

import loot_engine as le

SLOTS = 27
_T24 = 1 << 24


# ── Java numbers ──────────────────────────────────────────────────────────────
def _f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", float(x)))[0]


def p_less(p) -> float:
    """P(nextFloat() < (float) p)"""
    t = math.ceil(_f32(p) * _T24)
    return min(max(t, 0), _T24) / _T24


def _add(d, k, v):
    if v > 0:
        d[k] = d.get(k, 0.0) + v


def int_dist(p, world=None) -> dict:
    """loot_engine.num_int as a distribution {value: probability}"""
    if isinstance(p, (int, float)):
        return {int(p) if isinstance(p, int) else int(p // 1): 1.0}
    t = le._typ(p, "type")
    if t in ("", "uniform") and "min" in p and "max" in p:
        out = {}
        for lo, a in _bound(p["min"]).items():
            for hi, b in _bound(p["max"]).items():
                if lo >= hi:
                    _add(out, lo, a * b)
                else:
                    w = a * b / (hi - lo + 1)
                    for v in range(lo, hi + 1):
                        _add(out, v, w)
        return out
    if t == "constant":
        return {int(p["value"]): 1.0}
    if t == "binomial":
        out = {}
        for n, a in int_dist(p["n"]).items():
            for pr, b in float_dist(p["p"]).items():
                q = p_less(pr)
                for k in range(0, max(n, 0) + 1):
                    _add(out, k, a * b * math.comb(n, k) * q ** k * (1 - q) ** (n - k))
        return out
    raise Unsupported(f"number provider {t}")


def _bound(p):
    if isinstance(p, (int, float)):
        return {int(p // 1): 1.0}
    return int_dist(p)


def float_dist(p) -> dict:
    """a float provider used as a probability: only constants are found in chests"""
    if isinstance(p, (int, float)):
        return {float(p): 1.0}
    if le._typ(p, "type") == "constant":
        return {float(p["value"]): 1.0}
    if le._typ(p, "type") == "binomial":
        return {float(k): v for k, v in int_dist(p).items()}
    raise Unsupported("random float provider")


class Unsupported(Exception):
    pass


# ── conditions ────────────────────────────────────────────────────────────────
def cond_p(c, biome) -> float:
    t = le._typ(c, "condition", "type")
    if t == "random_chance":
        ch = c.get("chance", 0)
        if isinstance(ch, dict):
            return sum(w * p_less(v) for v, w in float_dist(ch).items())
        return p_less(ch)
    if t == "inverted":
        return 1.0 - cond_p(c["term"], biome)
    if t == "all_of":
        return math.prod(cond_p(x, biome) for x in c.get("terms", []))
    if t in ("any_of", "alternative"):
        return 1.0 - math.prod(1.0 - cond_p(x, biome) for x in c.get("terms", []))
    if t == "location_check":
        pred = c.get("predicate") or {}
        want = pred.get("biomes") or pred.get("biome")
        if isinstance(want, list):
            want = [w.replace("minecraft:", "") for w in want]
        elif isinstance(want, str):
            want = [want.replace("minecraft:", "").lstrip("#")]
        if biome is None:
            return 0.0
        return 1.0 if (want is None or biome in want) else 0.0
    raise Unsupported(f"condition {t}")


def conds_p(o, biome) -> float:
    return math.prod(cond_p(c, biome) for c in le._conditions(o))


def biomes_of(tab) -> set:
    """the biomes a table's location_checks name"""
    out = set()

    def walk(o):
        if isinstance(o, dict):
            if le._typ(o, "condition", "type") == "location_check":
                pred = o.get("predicate") or {}
                w = pred.get("biomes") or pred.get("biome")
                for b in (w if isinstance(w, list) else [w] if w else []):
                    out.add(b.replace("minecraft:", "").lstrip("#"))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(tab)
    return out


# ── enchantments ──────────────────────────────────────────────────────────────
_S_MAX = (1 << 25) - 2                       # nextFloat()+nextFloat(): k1 + k2


def _w_upto(s):
    """Σ_{S ≤ s} #(k1 + k2 = S), k1, k2 in [0, 2^24)"""
    if s < 0:
        return 0
    n = _T24
    if s < n:
        return (s + 1) * (s + 2) // 2
    tot_low = n * (n + 1) // 2
    m = s - n + 1                             # S = n .. s : 2n-1-S each
    return tot_low + m * (2 * n - 1) - (n + s) * m // 2


def _round_of(cost, s):
    """Math.round(cost + cost × span) for the pair sum s (loot_enchant._cost_round)"""
    import loot_enchant as lx
    a = _f32(s / _T24)
    span = _f32(_f32(a - 1.0) * lx._F015)
    return lx._cost_round(cost, span)


@lru_cache(maxsize=None)
def round_dist(cost):
    """{rounded cost: p} — r(S) is non-decreasing in S, so its steps are found by
    bisection and weighted by the exact pair counts (2^48 pairs)"""
    out = {}
    s = 0
    while s <= _S_MAX:
        r = _round_of(cost, s)
        lo, hi = s, _S_MAX                    # last S with the same r
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _round_of(cost, mid) == r:
                lo = mid
            else:
                hi = mid - 1
        w = _w_upto(lo) - _w_upto(s - 1)
        _add(out, max(1, r), w / float(1 << 48))
        s = lo + 1
    return out


def _mask(res, reqs):
    m = 0
    for i, r in enumerate(reqs):
        eid, lvl = r[0], r[1]
        if eid is None:
            continue
        if any(e == eid and l >= lvl for e, l in res):
            m |= 1 << i
    return m


def _select_dist(avail, cost, version, reqs):
    """EnchantmentHelper.selectEnchantment after the cost: {mask: p}.

    The picks: one by weight, then while nextInt(50) <= cost: drop the ones
    incompatible with the last, pick another, cost /= 2. The number of loop
    turns does not depend on the picks, so a forward pass over the turns. The
    state: the remaining enchantments that matter (wanted ones and those with
    incompatibilities, as bits) + how many « free » ones remain per weight
    (compatible with everything, not wanted: only their weight counts) + the
    mask so far. A state where no remaining pick can change the mask stops."""
    import loot_enchant as lx
    if not avail:
        return {0: 1.0}
    E = lx.env(version)
    n = len(avail)
    pbits = [_mask([(e.id, l)], reqs) for e, l in avail]
    inc = [0] * n
    for i in range(n):
        for j in range(n):
            if i != j and not E.compatible(avail[i][0], avail[j][0]):
                inc[i] |= 1 << j
    if not any(pbits):
        return {0: 1.0}
    tg = [i for i in range(n) if pbits[i]]
    if len(tg) == 1:
        key = (tuple((e.id, l) for e, l in avail), cost, version, tg[0])
        pt = _one_target(key, avail, inc, tg[0], cost)
        return {pbits[tg[0]]: pt, 0: 1.0 - pt} if pt < 1 else {pbits[tg[0]]: 1.0}
    tracked = [i for i in range(n) if pbits[i] or inc[i]]
    free_w = sorted({avail[i][0].weight for i in range(n) if i not in tracked})
    if not any(pbits):
        return {0: 1.0}
    tix = {i: k for k, i in enumerate(tracked)}
    tw = [avail[i][0].weight for i in tracked]
    tb = [pbits[i] for i in tracked]
    tinc = []
    for i in tracked:
        m = 0
        for j in tracked:
            if (inc[i] >> j) & 1:
                m |= 1 << tix[j]
        tinc.append(m | (1 << tix[i]))
    fcount = tuple(sum(1 for i in range(n) if i not in tracked and avail[i][0].weight == w)
                   for w in free_w)
    out = {}

    def can_change(rem, have):
        k = 0
        while rem:
            if rem & 1 and tb[k] & ~have:
                return True
            rem >>= 1
            k += 1
        return False

    def picks(st):
        rem, fc, have = st
        tot = sum(tw[k] for k in range(len(tw)) if (rem >> k) & 1) + \
            sum(c * w for c, w in zip(fc, free_w))
        if tot <= 0:
            return None
        res = []
        for k in range(len(tw)):
            if (rem >> k) & 1 and tw[k] > 0:
                res.append(((rem & ~tinc[k], fc, have | tb[k]), tw[k] / tot))
        for j, (c, w) in enumerate(zip(fc, free_w)):
            if c and w > 0:
                fc2 = fc[:j] + (c - 1,) + fc[j + 1:]
                res.append(((rem, fc2, have), c * w / tot))
        return res

    def settle(st, p, cur):
        rem, fc, have = st
        if not can_change(rem, have):
            _add(out, have, p)
        else:
            cur[st] = cur.get(st, 0.0) + p

    cur = {}
    first = picks(((1 << len(tracked)) - 1, fcount, 0))
    if first is None:
        return {0: 1.0}
    for st, p in first:
        settle(st, p, cur)
    c = cost
    while cur:
        cont = min(c + 1, 50) / 50.0
        nxt = {}
        for st, p in cur.items():
            if cont < 1:
                _add(out, st[2], p * (1 - cont))
            pk = picks(st)
            if not pk:                          # nothing left to pick: the loop ends
                _add(out, st[2], p * cont)
                continue
            for st2, w in pk:
                settle(st2, p * cont * w, nxt)
        cur = nxt
        c //= 2
    return out


_ONE = {}


def _one_target(key, avail, inc, t, cost):
    """P(the one wanted enchantment `t` is picked) — exact (Fractions).

    Weighted picks without replacement = arrivals of independent exponential
    clocks (rate = weight) in time order; a pick blocks its incompatible ones.
    Given the target arrives at time u, each group of incompatible enchantments
    (a component) evolves on its own: G_C(u, z) = E[z^(picks in C before u)],
    a polynomial in x = e^-u (integer weights) and z, from the recursion
        G_R = x^W_R + Σ_i w_i z ∫0^u e^(-W_R s) G_R'(u - s) ds,
        ∫0^u e^(-W s) x(u-s)^λ ds = (x^λ − x^W) / (W − λ)   (λ < W always).
    In the target's component a pick incompatible with the target kills the
    path. P(target is the k-th pick) = Σ_λ [z^(k-1)] F_λ · w_t / (w_t + λ), and
    the k-th pick happens with Π_{j<k} min((cost >> (j-1)) + 1, 50) / 50."""
    if key in _ONE:
        return _ONE[key]
    from fractions import Fraction as Fr
    n = len(avail)
    w = [int(e.weight) for e, _l in avail]
    others = [i for i in range(n) if i != t]
    # components of the incompatibility graph (without the target)
    comp, seen = [], set()
    for i in others:
        if i in seen:
            continue
        st, c = [i], []
        seen.add(i)
        while st:
            j = st.pop()
            c.append(j)
            for k in others:
                if k not in seen and ((inc[j] >> k) & 1 or (inc[k] >> j) & 1):
                    seen.add(k)
                    st.append(k)
        comp.append(c)

    def padd(a, b, f=1):
        for lam, zs in b.items():
            cur = a.setdefault(lam, {})
            for d, v in zs.items():
                cur[d] = cur.get(d, 0) + f * v
                if cur[d] == 0:
                    del cur[d]
            if not cur:
                del a[lam]
        return a

    def gfun(members):
        memo = {}

        def G(R):
            if R in memo:
                return memo[R]
            W = sum(w[i] for i in R)
            out = {W: {0: Fr(1)}}
            for i in R:
                if (inc[t] >> i) & 1 or (inc[i] >> t) & 1:
                    continue                    # picked before the target: it is blocked
                R2 = frozenset(j for j in R if j != i and not ((inc[i] >> j) & 1 or (inc[j] >> i) & 1))
                sub = G(R2)
                term = {}
                for lam, zs in sub.items():
                    k = Fr(w[i], W - lam)
                    for d, v in zs.items():
                        term.setdefault(lam, {})[d + 1] = term.get(lam, {}).get(d + 1, 0) + k * v
                        term.setdefault(W, {})[d + 1] = term.get(W, {}).get(d + 1, 0) - k * v
                padd(out, term)
            memo[R] = out
            return out
        return G(frozenset(members))

    F = {0: {0: Fr(1)}}
    for c in comp:
        g = gfun(c)
        nf = {}
        for l1, z1 in F.items():
            for l2, z2 in g.items():
                cur = nf.setdefault(l1 + l2, {})
                for d1, v1 in z1.items():
                    for d2, v2 in z2.items():
                        cur[d1 + d2] = cur.get(d1 + d2, 0) + v1 * v2
        F = {l: {d: v for d, v in zs.items() if v} for l, zs in nf.items()}
    wt = w[t]
    rank = {}
    for lam, zs in F.items():
        k = Fr(wt, wt + lam)
        for d, v in zs.items():
            rank[d] = rank.get(d, 0) + v * k
    p = Fr(0)
    reach = Fr(1)
    c = cost
    for k in range(0, max(rank) + 1 if rank else 0):
        if k > 0:
            reach *= Fr(min(c + 1, 50), 50)
            c //= 2
        p += reach * rank.get(k, 0)
    _ONE[key] = float(p)
    return _ONE[key]


def with_levels_dist(item, f, version, reqs):
    import loot_enchant as lx
    E = lx.env(version)
    ev = lx.enchantability(item)
    if ev <= 0:
        return {0: 1.0}
    opts = E.options(f.get("options")) if not E.legacy else None
    treasure = bool(f.get("treasure", False))
    out = {}
    b = ev // 4 + 1
    for lv, p0 in int_dist(f.get("levels", 0)).items():
        for x in range(b):
            for y in range(b):
                c = lv + 1 + x + y
                for r, p1 in round_dist(c).items():
                    avail = lx.available(item, r, version, opts, treasure)
                    for m, p2 in _select_dist(avail, r, version, reqs).items():
                        _add(out, m, p0 * p1 * p2 / (b * b))
    return out


# ── one stack through the functions ───────────────────────────────────────────
# a stack: (item, count, potion, mask)  — mask: the wanted enchantments it has
def _fn(dist, f, t, version, biome, reqs):
    q = conds_p(f, biome)
    if q <= 0:
        return dist
    out = {}
    for st, p in dist.items():
        if q < 1:
            _add(out, st, p * (1 - q))
        for st2, p2 in _fn1(st, f, t, version, reqs).items():
            _add(out, st2, p * q * p2)
    return out


def _fn1(st, f, t, version, reqs):
    import loot_enchant as lx
    item, n, pot, m = st
    if t == "set_count":
        add = bool(f.get("add"))
        return {(item, (n if add else 0) + v, pot, m): p for v, p in int_dist(f["count"]).items()}
    if t in ("enchant_randomly", "enchant_with_levels"):
        it2 = "enchanted_book" if item == "book" else item
        if not any(r[0] and r[2] == it2 for r in reqs):
            return {(it2, n, pot, m): 1.0} if (t == "enchant_with_levels" or
                                               lx.randomly_candidates(item, f, version)) \
                else {st: 1.0}
    if t == "enchant_randomly":
        cand = lx.randomly_candidates(item, f, version)
        if not cand:
            return {st: 1.0}
        it2 = "enchanted_book" if item == "book" else item
        out = {}
        for e in cand:
            for l in range(1, e.max_level + 1):
                _add(out, (it2, n, pot, m | _mask([(e.id, l)], reqs)),
                     1.0 / len(cand) / e.max_level)
        return out
    if t == "enchant_with_levels":
        it2 = "enchanted_book" if item == "book" else item
        return {(it2, n, pot, m | mm): p for mm, p in with_levels_dist(item, f, version, reqs).items()}
    if t == "set_enchantments":
        out = {(item, n, pot, m): 1.0}
        for k, v in (f.get("enchantments") or {}).items():
            eid = k.replace("minecraft:", "")
            lv = {v: 1.0} if isinstance(v, int) else (int_dist(v) if isinstance(v, dict) else {1: 1.0})
            o2 = {}
            for (it, nn, pp, mm), a in out.items():
                for l, b in lv.items():
                    _add(o2, (it, nn, pp, mm | _mask([(eid, int(l))], reqs)), a * b)
            out = o2
        if item == "book":
            out = {("enchanted_book",) + k[1:]: v for k, v in out.items()}
        return out
    if t == "set_potion":
        return {(item, n, str(f.get("id", "")).replace("minecraft:", ""), m): 1.0}
    if t == "exploration_map":
        return {("filled_map", n, pot, m): 1.0}
    if t == "set_item":
        return {(str(f.get("item", item)).replace("minecraft:", ""), n, pot, m): 1.0}
    if t in ("set_damage", "set_stew_effect", "set_instrument", "set_ominous_bottle_amplifier",
             "set_name", "set_lore", "set_components", "set_custom_data", "set_nbt",
             "toggle_tooltips", "filtered", "discard", "set_book_cover", "set_written_book_pages",
             "set_writable_book_pages", "set_banner_pattern", "set_fireworks",
             "set_firework_explosion", "set_custom_model_data", "set_attributes", "limit_count",
             "furnace_smelt", "copy_name", "copy_components", "copy_custom_data",
             "explosion_decay"):
        return {st: 1.0}                     # (the engine does the same: no effect on the item)
    raise Unsupported(f"function {t}")


def through(dist, o, version, biome, reqs):
    for f, t in le._functions(o):
        dist = _fn(dist, f, t, version, biome, reqs)
    return dist


# ── what a roll emits ─────────────────────────────────────────────────────────
# a roll's outcome: a sorted tuple of stacks; the caller projects stacks to keys
def _split_max(st):
    item, n, pot, m = st
    mx = le.max_stack(item)
    if n < mx:
        return (st,)
    out = []
    c = n
    while c > 0:
        k = min(mx, c)
        out.append((item, k, pot, m))
        c -= k
    return tuple(out)


def _expand(e, biome):
    """loot_engine.expand_entry: [(p, [entries])]"""
    t = le._typ(e, "type")
    q = conds_p(e, biome)
    res = []
    if q < 1:
        res.append((1 - q, False, []))
    if q <= 0:
        return res
    if t == "alternatives":
        def alt(chs):
            if not chs:
                return [(1.0, False, [])]
            out = []
            for p, ok, lst in _expand(chs[0], biome):
                if ok:
                    out.append((p, True, lst))
                else:
                    for p2, ok2, l2 in alt(chs[1:]):
                        out.append((p * p2, ok2, lst + l2))
            return out
        for p, ok, lst in alt(e.get("children", [])):
            res.append((q * p, ok, lst))
    elif t == "group":
        acc = [(1.0, [])]
        for ch in e.get("children", []):
            acc = [(p * p2, l + l2) for p, l in acc for p2, _ok, l2 in _expand(ch, biome)]
        res += [(q * p, True, l) for p, l in acc]
    elif t == "sequence":
        def seq(chs):
            if not chs:
                return [(1.0, True, [])]
            out = []
            for p, ok, lst in _expand(chs[0], biome):
                if not ok:
                    out.append((p, False, lst))
                else:
                    for p2, ok2, l2 in seq(chs[1:]):
                        out.append((p * p2, ok2, lst + l2))
            return out
        for p, ok, lst in seq(e.get("children", [])):
            res.append((q * p, ok, lst))
    else:
        res.append((q, True, [e]))
    return res


class _Tab:
    """one table's per-roll outcome distributions, for a projection of stacks"""

    def __init__(self, version, biome, reqs, proj, depth=0):
        self.version, self.biome, self.reqs, self.proj = version, biome, reqs, proj
        self.depth = depth

    def entry_stacks(self, e, pool, tab, top):
        """{tuple of stacks (unprojected for nested, projected at top): p}"""
        t = le._typ(e, "type")
        if t == "item":
            d = {(e["name"].replace("minecraft:", ""), 1, None, 0): 1.0}
            d = through(d, e, self.version, self.biome, self.reqs)
            return self._finish(d, pool, tab, top)
        if t == "empty":
            return {(): 1.0}
        if t == "loot_table":
            ref = le._k(e, "value", "name")
            sub = le.get_table(self.version, ref) if isinstance(ref, str) else None
            if sub is None or self.depth > 6:
                raise Unsupported("nested loot table")
            inner = _Tab(self.version, self.biome, self.reqs, None, self.depth + 1)
            multi = inner.table_multisets(sub)          # {tuple of raw stacks: p}
            out = {}
            for stacks, p in multi.items():
                acc = {(): p}
                for st in stacks:
                    d = through({st: 1.0}, e, self.version, self.biome, self.reqs)
                    one = self._finish(d, pool, tab, top)
                    acc2 = {}
                    for a, pa in acc.items():
                        for b, pb in one.items():
                            _add(acc2, tuple(sorted(a + b, key=repr)), pa * pb)
                    acc = acc2
                for k, v in acc.items():
                    _add(out, k, v)
            return out
        raise Unsupported(f"entry {t}")

    def _finish(self, d, pool, tab, top):
        d = through(d, pool, self.version, self.biome, self.reqs)
        d = through(d, tab, self.version, self.biome, self.reqs)     # roll_table's « top »
        if not top:
            return {(st,): p for st, p in d.items()}
        out = {}
        for st, p in d.items():
            ks = tuple(sorted((self.proj(s) for s in _split_max(st) if s[1] > 0), key=repr))
            ks = tuple(k for k in ks if k is not None)
            _add(out, ks, p)
        return out

    def roll(self, pool, tab, top=True):
        """{tuple of stacks: p} for one roll of `pool`"""
        out = {}
        for pv, valid in self._valid(pool):
            vw = [(e, int(e.get("weight", 1))) for e in valid if int(e.get("weight", 1)) > 0]
            tot = sum(w for _e, w in vw)
            if not vw or tot == 0:
                _add(out, (), pv)
                continue
            for e, w in vw:
                for ks, p in self.entry_stacks(e, pool, tab, top).items():
                    _add(out, ks, pv * w / tot * p)
        return out

    def _valid(self, pool):
        acc = [(1.0, [])]
        for e in pool.get("entries", []):
            acc = [(p * p2, l + l2) for p, l in acc for p2, _ok, l2 in _expand(e, self.biome)]
        merged = {}
        for p, l in acc:
            k = tuple(id(x) for x in l)
            if k in merged:
                merged[k] = (merged[k][0] + p, l)
            else:
                merged[k] = (p, l)
        return [v for v in merged.values() if v[0] > 0]

    def pools(self, tab, top=True):
        """[(p_active, {rolls: p}, roll outcome dist)]"""
        out = []
        for pool in tab.get("pools", []):
            q = conds_p(pool, self.biome)
            if q <= 0:
                continue
            out.append((q, int_dist(pool.get("rolls", 1)), self.roll(pool, tab, top)))
        return out

    def table_multisets(self, tab):
        """the multiset of raw stacks a nested table emits: {sorted tuple: p}"""
        acc = {(): 1.0}
        for q, rd, roll in self.pools(tab, top=False):
            pool_d = _pool_conv(q, rd, roll, lambda a, b: tuple(sorted(a + b, key=repr)))
            acc = _conv(acc, pool_d, lambda a, b: tuple(sorted(a + b, key=repr)))
        return acc


def _conv(a, b, comb):
    out = {}
    for ka, pa in a.items():
        for kb, pb in b.items():
            _add(out, comb(ka, kb), pa * pb)
    return out


def _pool_conv(q, rd, roll, comb):
    out = {}
    if q < 1:
        _add(out, (), 1 - q)
    cur = {(): 1.0}
    rmax = max(rd) if rd else 0
    for r in range(0, rmax + 1):
        if r > 0:
            cur = _conv(cur, roll, comb)
        pr = rd.get(r, 0.0)
        if pr:
            for k, v in cur.items():
                _add(out, k, q * pr * v)
    return out


# ── the query ─────────────────────────────────────────────────────────────────
def _reqs(items):
    return [(w.get("ench"), int(w.get("ench_lvl") or 1), w["item"]) if w.get("ench")
            else (None, 0, w["item"]) for w in items]


def _bits(st, items):
    item, n, pot, m = st
    b = 0
    for i, w in enumerate(items):
        if item != w["item"]:
            continue
        if w.get("ench") and not (m >> i) & 1:
            continue
        if w.get("potion") and pot != w["potion"]:
            continue
        b |= 1 << i
    return b


class Result:
    __slots__ = ("p", "impossible", "max_count", "dist", "note")

    def __init__(self, p, max_count=None, dist=None, note=""):
        self.p = p
        self.impossible = p <= 0
        self.max_count, self.dist, self.note = max_count, dist, note

    def __repr__(self):
        return f"Result(p={self.p!r}, max={self.max_count})"


def _in_range(n, w):
    return n >= int(w.get("min") or 0) and (w.get("max") is None or n <= int(w["max"]))


def chest_prob(q, table, version, biome=None) -> Result:
    tab = le.get_table(version, table)
    if tab is None:
        return Result(0.0, 0, note="no such table")
    items = q.get("items") or []
    mode = q.get("mode", "find")
    if mode == "layout":
        return _layout_prob(q, tab, version, biome)
    reqs = _reqs(items)
    # quantities: each want's total, capped where the answer no longer changes
    caps = []
    for i, w in enumerate(items):
        if mode == "most" and i == 0:
            caps.append(10 ** 6)
        elif w.get("max") is not None:
            caps.append(int(w["max"]) + 1)
        else:
            caps.append(max(int(w.get("min") or 0), 1))

    def proj(st):
        b = _bits(st, items)
        return (b, st[1]) if b else None
    T = _Tab(version, biome, reqs, proj)
    k = len(items)

    def add_vec(a, stacks):
        v = list(a)
        for b, n in stacks:
            for i in range(k):
                if (b >> i) & 1:
                    v[i] = min(caps[i], v[i] + n)
        return tuple(v)
    acc = {(0,) * k: 1.0}
    for qp, rd, roll in T.pools(tab):
        rv = {}
        for ks, p in roll.items():
            _add(rv, add_vec((0,) * k, ks), p)
        comb = lambda a, b: tuple(min(caps[i], a[i] + b[i]) for i in range(k))
        pool_d = {}
        if qp < 1:
            _add(pool_d, (0,) * k, 1 - qp)
        cur = {(0,) * k: 1.0}
        for r in range(0, (max(rd) if rd else 0) + 1):
            if r > 0:
                cur = _conv(cur, rv, comb)
            pr = rd.get(r, 0.0)
            if pr:
                for kk, v in cur.items():
                    _add(pool_d, kk, qp * pr * v)
        acc = _conv(acc, pool_d, comb)
    p = sum(v for vec, v in acc.items() if all(_in_range(vec[i], w) for i, w in enumerate(items)))
    dist = None
    mx = None
    if items:
        d0 = defaultdict(float)
        for vec, v in acc.items():
            d0[vec[0]] += v
        dist = dict(sorted(d0.items()))
        mx = max((n for n, v in dist.items() if v > 0), default=0)
    return Result(p, mx, dist)


# ── layout: the splitter and the shuffles ─────────────────────────────────────
#
# shuffleAndSplitItems, step by step: while pieces < 27 and some stack of more
# than one item waits in the « split » list, one of those (uniformly) is cut in
# two (k = 1 … n/2), each part going back to the list with probability 1/2 when
# it still holds more than one item. Then every piece is placed: all the
# arrangements of the pieces in the 27 slots are equally likely (the slots and
# the pieces are both shuffled; over-full: 27 of them, the rest is lost).
#
# The cut order couples every stack; in continuous time it does not: give each
# waiting piece a clock of rate 1 (the next cut is then uniform among them) and
# the pieces evolve independently. A piece of n items has the generating
# function A_n(s) (variables: v = a piece, x = still waiting, z_t = a piece
# matching the layout's cells), from the backward equation
#     A_n' = -A_n + avg_k Q_{n-k} Q_k,   A_n(0) = v x z_t(n),
#     Q_1 = v z_t(1),  Q_m = (v z_t(m) + A_m) / 2,
# and the whole chest, rolls included, is Φ(s) = Π_pools E_R[Γ_pool(s)^R]
# (independent rolls: no enumeration of the starting stacks). The stops:
#   · pieces = 27 with A waiting pieces: P(visit) = A × E[time there]
#       = ∫0^∞ [v^27] ∂x Φ(s) ds          (x = 1 + ε: the ε part)
#   · nothing left to cut (pieces ≤ 27): [v^n] Φ(∞) — A_n(∞) exactly, by recursion
#   · more than 27 stacks before cutting: [v^n] Φ(0).
# The integral: A_n on Chebyshev–Lobatto nodes, interval by interval
# (A(s) = e^-(s-a) [A(a) + ∫a^s e^(w-a) G(w) dw], spectral integration), and
# Clenshaw–Curtis for ∫ — double precision (≈ 1e-13), all terms positive.
# Cells: what matters of the pieces is how many satisfy each cell — the
# factorial moments of those counts, from the Taylor coefficients of Φ in
# z = 1 + ζ (degree ≤ the number of cells).

def _norm_cell(c):
    """None | 'empty' | 'filled' | (want, count|None)"""
    if c is None or c in ("empty", "filled"):
        return c
    if isinstance(c, (list, tuple)):
        return (int(c[0]), (int(c[1]) if len(c) > 1 and c[1] else None))
    return (int(c), None)


class _Alg:
    """truncated polynomials in v (≤ V), x = 1 + ε (dual) and one mark u_g per
    group of cells (degree ≤ its number of cells), stored flat; leading axes =
    evaluation points. A piece satisfying the groups t carries (1 + Σ_{g∈t} u_g):
    the coefficient of Π u_g^{n_g} counts the ways to give each group's cells
    distinct pieces (÷ Π n_g!)."""

    def __init__(self, V, dual, caps):
        import itertools
        import numpy as np
        self.V, self.Xn, self.caps = V, (2 if dual else 1), tuple(caps)
        mons = list(itertools.product(*[range(c + 1) for c in caps]))
        self.mons = mons
        mi = {m: i for i, m in enumerate(mons)}
        self.mi = mi
        self.Zn = len(mons)
        self.S = (V + 1) * self.Xn * self.Zn
        zp = []
        for z1, m1 in enumerate(mons):
            for z2, m2 in enumerate(mons):
                m3 = tuple(a + b for a, b in zip(m1, m2))
                if all(a <= c for a, c in zip(m3, caps)):
                    zp.append((z1, z2, mi[m3]))
        I, J, K = [], [], []
        for v1 in range(V + 1):
            for v2 in range(V + 1 - v1):
                for x1 in range(self.Xn):
                    for x2 in range(self.Xn - x1):
                        for z1, z2, z3 in zp:
                            I.append(self.ix(v1, x1, z1)); J.append(self.ix(v2, x2, z2))
                            K.append(self.ix(v1 + v2, x1 + x2, z3))
        o = np.argsort(K, kind="stable")
        self.I, self.J = np.array(I)[o], np.array(J)[o]
        Ks = np.array(K)[o]
        self.ukeys, self.starts = np.unique(Ks, return_index=True)
        self.np = np

    def top(self):
        """the flat ζ index of Π u_g^{n_g} and its weight Π n_g!"""
        return self.mi[self.caps], float(math.prod(math.factorial(c) for c in self.caps))

    def ix(self, v, x, z):
        return (v * self.Xn + x) * self.Zn + z

    def zeros(self, n):
        return self.np.zeros((n, self.S))

    def _prep_dense(self):
        """the product as v-Toeplitz × inner bilinear map (dual × marks): batched matmuls"""
        np = self.np
        V1, In = self.V + 1, self.Xn * self.Zn
        k = np.arange(V1)
        D = k[:, None] - k[None, :]
        self._M = (D >= 0).astype(float)
        self._D = np.clip(D, 0, None)
        Bm = np.zeros((In, In, In))
        for x1 in range(self.Xn):
            for x2 in range(self.Xn - x1):
                for z1, m1 in enumerate(self.mons):
                    for z2, m2 in enumerate(self.mons):
                        m3 = tuple(a + b for a, b in zip(m1, m2))
                        if m3 in self.mi:
                            Bm[x1 * self.Zn + z1, x2 * self.Zn + z2, (x1 + x2) * self.Zn + self.mi[m3]] = 1
        self._B = Bm.reshape(In * In, In)
        self._In = In

    def mul(self, a, b):
        np = self.np
        if not hasattr(self, "_B"):
            self._prep_dense()
        n = a.shape[0]
        V1, In = self.V + 1, self._In
        L = _polymul()
        if L is not None:                                # (12/11) the same product in C
            if not hasattr(self, "_rt"):
                rt = -np.ones((In, In), dtype=np.int32)
                p, q, r = np.nonzero(self._B.reshape(In, In, In))
                rt[p, q] = r
                self._rt = np.ascontiguousarray(rt.ravel())
            a = np.ascontiguousarray(a, dtype=np.float64)
            b = np.ascontiguousarray(b, dtype=np.float64)
            out = np.empty_like(a)
            L.lv_polymul2(a.ctypes.data, b.ctypes.data, out.ctypes.data, n, V1, In,
                          self._rt.ctypes.data)
            return out
        a3 = a.reshape(n, V1, In)
        b3 = b.reshape(n, V1, In)
        TA = a3[:, self._D, :] * self._M[None, :, :, None]          # (n, k, j, p): a[k-j]
        X = np.matmul(TA.transpose(0, 1, 3, 2), b3[:, None, :, :])  # (n, k, p, q)
        out = X.reshape(n, V1, In * In) @ self._B                    # (n, k, r)
        return out.reshape(n, -1)

    def mul_sparse(self, a, b):
        np = self.np
        prod = a[:, self.I] * b[:, self.J]
        out = np.zeros_like(a)
        out[:, self.ukeys] = np.add.reduceat(prod, self.starts, axis=1)
        return out

    def piece(self, n, t, active):
        """v · (1 + Σ_{g∈t} u_g) (· x when waiting) on n points"""
        a = self.zeros(n)
        G = len(self.caps)
        gm = getattr(self, "gmap", None)               # (12/11) a sub-algebra: its groups
        if gm is not None:
            t = sum(1 << i for g, i in gm.items() if (t >> g) & 1)
        zs = [(0, 1.0)] + [(self.mi[tuple(1 if j == g else 0 for j in range(G))], 1.0)
                           for g in range(G) if (t >> g) & 1 and self.caps[g] > 0]
        for z, c in zs:
            a[:, self.ix(1, 0, z)] += c
            if active and self.Xn == 2:
                a[:, self.ix(1, 1, z)] += c
        return a


_PM = None


def _polymul():
    """lootvm's lv_polymul2 (None: not built, or an older lootvm)"""
    global _PM
    if _PM is None:
        _PM = False
        try:
            import ctypes
            import loot_native
            L = loot_native.lib()
            if L is not None and hasattr(L, "lv_polymul2"):
                L.lv_polymul2.restype = None
                L.lv_polymul2.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int] * 3 + [ctypes.c_void_p]
                _PM = L
        except Exception:                                 # noqa: BLE001
            _PM = False
    return _PM or None


def _cheb(n):
    """Chebyshev–Lobatto nodes on [0, 1] (increasing) and the matrix of
    ∫0^x_j of the interpolant"""
    import numpy as np
    from numpy.polynomial import chebyshev as C
    x = -np.cos(np.pi * np.arange(n + 1) / n)          # [-1, 1], increasing
    Vm = C.chebvander(x, n)
    E = np.zeros((n + 1, n + 1))
    for k in range(n + 1):
        c = np.zeros(n + 1); c[k] = 1
        E[:, k] = C.chebval(x, C.chebint(c, lbnd=-1))
    Q = E @ np.linalg.inv(Vm) / 2.0                      # dx = 2 du on [0, 1]
    return (x + 1) / 2, Q


def _intervals(s_max=45.0):
    b = [0.0]
    while b[-1] < s_max:
        s = b[-1]
        R = min(27.0, 1.0 + 41.0 / max(s, 1e-9))
        b.append(min(s_max, s + 3.5 / R))
    return b


def _layout_prob(q, tab, version, biome):
    import numpy as np
    items = q.get("items") or []
    cells = [_norm_cell(c) for c in (q.get("layout") or [None] * SLOTS)][:SLOTS]
    reqs = _reqs(items)
    groups = sorted({c for c in cells if isinstance(c, tuple)}, key=repr)
    g_cells = [sum(1 for c in cells if c == g) for g in groups]
    n_empty = sum(1 for c in cells if c == "empty")
    n_filled = sum(1 for c in cells if c == "filled")
    C = sum(1 for c in cells if c is not None)
    Gt = sum(g_cells)
    m = Gt + n_filled
    if not groups and not n_filled and not n_empty:
        return Result(1.0)

    def proj(st):
        return (_bits(st, items), st[1])
    T_ = _Tab(version, biome, reqs, proj)
    pools = T_.pools(tab)
    # the piece types: which cell groups a piece of (bits, n) satisfies
    types = {}

    def ptype(b, n):
        k = (b, n)
        if k not in types:
            t = 0
            for gi, (w, cnt) in enumerate(groups):
                if (b >> w) & 1 and (cnt is None or cnt == n):
                    t |= 1 << gi
            types[k] = t
        return types[k]
    allt = {ptype(b, a) for _q, _rd, roll in pools for ks in roll for b, c in ks
            for a in range(1, c + 1)}
    if any(not any((t >> g) & 1 for t in allt) for g in range(len(groups))):
        return Result(0.0)                              # a cell no piece can ever fill

    class _Id:                                          # the type is the mark set itself
        @staticmethod
        def get(t, _d=0):
            return t
    tvar = _Id()

    def weights(alg):
        W = np.zeros(alg.Zn)
        k, w = alg.top()
        W[k] = w
        return W

    def arr(n):
        """P(the cells | n pieces, the chosen ones) without the SDR part"""
        if n < m:
            return 0.0
        f = math.perm(n - Gt, n_filled) if n - Gt >= n_filled else 0
        if not f:
            return 0.0
        if n <= SLOTS:
            if n - m > SLOTS - C:
                return 0.0
            return f * math.perm(SLOTS - C, n - m) / math.perm(SLOTS, n)
        if n_empty:
            return 0.0
        return f * math.perm(n - m, SLOTS - C) / math.perm(n, SLOTS)

    def chest(alg, piece_fn, npts):
        """Φ on npts points from piece_fn(b, n) → array"""
        acc = None
        one = alg.zeros(npts); one[:, 0] = 1.0
        for qp, rd, roll in pools:
            gam = one * (1 - qp)
            for ks, p in roll.items():
                ks = [k for k in ks if k[1] > 0]
                if not ks:
                    gam = gam + qp * p * one
                    continue
                term = piece_fn(*ks[0])                 # (one stack: no product)
                for b, c in ks[1:]:
                    term = alg.mul(term, piece_fn(b, c))
                gam = gam + qp * p * term
            pw = one.copy()
            tot = one * rd.get(0, 0.0)
            for r in range(1, max(rd) + 1):
                pw = alg.mul(pw, gam)
                if rd.get(r):
                    tot = tot + rd[r] * pw
            acc = tot if acc is None else alg.mul(acc, tot)
        return acc if acc is not None else one

    res = 0.0
    # 1) more than 27 stacks before any cut
    maxst = sum(max(rd) * max((len(k) for k in roll), default=0) for _q, rd, roll in pools)
    if maxst > SLOTS:
        A0 = _Alg(maxst, False, g_cells)
        W0 = weights(A0)
        phi0 = chest(A0, lambda b, c: A0.piece(1, tvar.get(ptype(b, c), 0), c > 1), 1)[0]
        for n in range(SLOTS + 1, maxst + 1):
            f = arr(n)
            if f:
                res += f * sum(W0[z] * phi0[A0.ix(n, 0, z)] for z in range(A0.Zn))
    # 2) every cut done, ≤ 27 pieces: A_n(∞)
    Ai = _Alg(SLOTS, True, g_cells)
    Wi = weights(Ai)
    # the stacks no cell cares about: their pieces carry no mark (a smaller algebra)
    maxc = defaultdict(int)
    for _q, _rd, roll in pools:
        for ks in roll:
            for b, c in ks:
                maxc[b] = max(maxc[b], c)
    # (12/11) and the others carry only the marks of the cells they can fill: each its
    # own smaller algebra (3 single cells of 3 items: 4 × 4 instead of 16 × 16 × 16)
    gsets = {}
    for b, c in maxc.items():
        t = 0
        for a in range(1, c + 1):
            t |= ptype(b, a)
        gsets[b] = tuple(g for g in range(len(groups)) if (t >> g) & 1)
    algs, embs = {}, {}

    def _sub(gs):
        if gs not in algs:
            if len(gs) == len(groups):
                algs[gs], embs[gs] = Ai, None
            else:
                al = _Alg(SLOTS, True, tuple(g_cells[g] for g in gs))
                al.gmap = {g: i for i, g in enumerate(gs)}
                e = np.zeros(al.S, dtype=np.int64)
                for v in range(SLOTS + 1):
                    for x in range(al.Xn):
                        for z, mon in enumerate(al.mons):
                            full = [0] * len(groups)
                            for g, d in zip(gs, mon):
                                full[g] = d
                            e[al.ix(v, x, z)] = Ai.ix(v, x, Ai.mi[tuple(full)])
                algs[gs], embs[gs] = al, e
        return algs[gs]

    def AL(b):
        return _sub(gsets.get(b, tuple(range(len(groups)))))

    def lift(b, a):
        gs = gsets.get(b, tuple(range(len(groups))))
        _sub(gs)
        e = embs[gs]
        if e is None:
            return a
        out = Ai.zeros(a.shape[0])
        out[:, e] = a
        return out
    inf = {}

    def a_inf(b, c):
        if (b, c) in inf:
            return inf[(b, c)]
        al = AL(b)
        if c == 1:
            r = al.piece(1, ptype(b, 1), False)
        else:
            hk = c // 2
            g = al.zeros(1)
            for k in range(1, hk + 1):
                g = g + al.mul(q_inf(b, c - k), q_inf(b, k)) / hk
            r = g
        inf[(b, c)] = r
        return r

    def q_inf(b, a):
        al = AL(b)
        if a == 1:
            return al.piece(1, ptype(b, 1), False)
        return 0.5 * al.piece(1, ptype(b, a), False) + 0.5 * a_inf(b, a)
    phi_inf = chest(Ai, lambda b, c: lift(b, a_inf(b, c) if c > 1 else
                                     AL(b).piece(1, ptype(b, 1), False)), 1)[0]
    for n in range(0, SLOTS + 1):
        f = arr(n)
        if f:
            res += f * sum(Wi[z] * phi_inf[Ai.ix(n, 0, z)] for z in range(Ai.Zn))
    # 3) the stop at 27 pieces with stacks still waiting
    maxitems = sum(max(rd) * max((sum(c for _b, c in k) for k in roll), default=0)
                   for _q, rd, roll in pools)
    f27 = arr(SLOTS)
    if f27 and maxitems >= SLOTS:
        res += f27 * _full_integral(Ai, Wi, pools, ptype, AL, lift, chest)
    return Result(max(0.0, res))


def _full_integral(alg, W, pools, ptype, AL, lift, chest):
    """∫0^∞ Σ_z W_z [v^27 ε ζ^z] Φ(s) ds — until no stack can still be waiting
    with ≤ 27 pieces (bounded by E[waiting stacks], the ε part of Φ)"""
    import numpy as np
    nodes, Qm = _cheb(12)
    keys = sorted({(b, c) for _q, _rd, roll in pools for ks in roll for b, c in ks if c > 1})
    need = set()
    for b, c in keys:
        need |= {(b, a) for a in range(2, c + 1)}
    need = sorted(need, key=lambda k: (k[1], k[0]))
    cur = {}
    for b, c in need:
        cur[(b, c)] = AL(b).piece(1, ptype(b, c), True)[0]
    lo_idx = [alg.ix(SLOTS, 1, z) for z in range(alg.Zn)]
    act_idx = [alg.ix(n, 1, 0) for n in range(SLOTS + 1)]
    total = 0.0
    s0 = 0.0
    while s0 < 400.0:
        R = min(27.0, 1.0 + 41.0 / max(s0, 1e-9))
        h = 3.5 / R
        s = s0 + h * nodes
        npts = len(s)
        dec = np.exp(-(s - s0))[:, None]
        grow = np.exp(s - s0)[:, None]
        val = {}
        fin = {}

        def fixed(b, k):
            if (b, k) not in fin:
                fin[(b, k)] = AL(b).piece(npts, ptype(b, k), False)
            return fin[(b, k)]

        def Q(b, k):
            return fixed(b, 1) if k == 1 else 0.5 * fixed(b, k) + 0.5 * val[(b, k)]
        for b, c in need:
            hk = c // 2
            QA = np.concatenate([Q(b, c - k) for k in range(1, hk + 1)])
            QB = np.concatenate([Q(b, k) for k in range(1, hk + 1)])
            g = AL(b).mul(QA, QB).reshape(hk, npts, -1).sum(0) / hk
            integ = h * (Qm @ (grow * g))
            val[(b, c)] = dec * (cur[(b, c)][None, :] + integ)
        for k in need:
            cur[k] = val[k][-1]
        phi = chest(alg, lambda b, c: lift(b, val[(b, c)] if c > 1 else fixed(b, 1)), npts)
        F = phi[:, lo_idx] @ W
        total += h * float(Qm[-1] @ F)
        s0 += h
        waiting = float(phi[-1, act_idx].sum())        # ≥ P(still cutting, ≤ 27 pieces)
        if s0 > 3 and waiting <= 1e-13 * max(total, 1e-280):
            break
        if not need and s0 > 3:
            break
    return total


def query_odds(q, tables, version):
    """[(table, Result | None, biome, exact)] — None: not computable (Unsupported)"""
    out = []
    for tb in tables:
        tab = le.get_table(version, tb)
        if tab is None:
            continue
        try:
            r = chest_prob(q, tb, version, None)
            bio = None
            if r.p <= 0:
                for b in sorted(biomes_of(tab)):         # e.g. a disc only in Sulfur Caves
                    r2 = chest_prob(q, tb, version, b)
                    if r2.p > 0:
                        r, bio = r2, b
                        break
            out.append((tb, r, bio, True))
        except Unsupported:
            out.append((tb, None, None, False))
    return out
