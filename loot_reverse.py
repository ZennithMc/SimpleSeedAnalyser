"""
loot_reverse.py — (15/11) the Loot page's Reverse mode (Java 1.16 – 1.17).

A chest's contents are LootTable.fill on java.util.Random seeded with the chest's
loot seed: only the low 48 bits count, so a table has 2^48 possible chests.
Instead of testing world seeds one by one until a chest matches (hopeless for a
1-in-a-billion chest), Reverse goes the other way:

  1. paths     what the draws of the fill must be for the wanted items: which
               entry each pick takes, which count, which enchantment… (`paths`,
               best first). A pick that does not matter only fixes how many draws
               it takes.
  2. states    the 48-bit RNG states whose draws fall there: a lattice over the
               state (cubiomes_src/loot_reverse.c, lr_new / lr_next), every one
               checked by the real fill (loot_engine) — `chest_states`.
  3. seeds     a chest state → the structure RNG in the chest's chunk (the loot
               seed is its nextLong) → the chunk's decoration seed → the structure
               seeds whose structure starts there (population reversal + the
               placement, lr_pop_scan) → the 2^16 world seeds of each (the upper
               bits), checked for the biome like a normal search (loot_finder).

So a search costs what the results cost, not what the rarity costs: a chest that
1 chest in 10^12 has comes as fast as a common one, and when no chest at all can
have it, the paths say so (every state of the table is covered).

Java 1.16 – 1.17 only: from 1.18 the structure RNG is Xoroshiro128++ (its
nextLong cannot be undone from 48 bits). Structures: buried treasure, desert
pyramid, jungle temple, igloo — their chests' draws are known without the
terrain (scattered_loot / structure_loot give the same chests).

(20/11) Dungeons: a feature, in the chunk's decorator RNG (cubiomes_src/loot_reverse.c,
lr_dungeon_scan): each way the draws can lead to the chest (attempt, room size, moss of
the floor, which chest on which try) gives a population seed, then the structure
seeds of every chunk; the terrain decides whether the room is really there (C engine,
dungeon_loot), checked on two of the 65 536 sisters, then sister by sister. Covered:
the first dungeon of its chunk (99.7 %), y 10 – 76 (≈ 98 %), a floor over solid ground
(94 %), not desert / swamp chunks (their fossil comes first) — about 4 chests in 5.
"""
from __future__ import annotations

import ctypes
import heapq
import itertools
import math

M48 = (1 << 48) - 1
MULT = 0x5DEECE66D
VERSIONS = (19, 20, 21)                       # cubiomes MC_1_16_1, MC_1_16_5, MC_1_17


class Unsupported(Exception):
    pass


# ── the model of a table: what each draw does ──────────────────────────────────
class Entry:
    __slots__ = ("idx", "item", "weight", "ops", "steps", "lo", "hi")

    def __init__(self, idx, item, weight, ops):
        self.idx, self.item, self.weight, self.ops = idx, item, weight, ops
        self.steps = _steps(ops)                  # {draws its functions take: probability}
        self.lo = self.hi = 0                     # its values of the pick (nextInt(total))


class Pool:
    __slots__ = ("rlo", "rhi", "entries", "total")

    def __init__(self, rlo, rhi, entries):
        self.rlo, self.rhi, self.entries = rlo, rhi, entries
        self.total = sum(e.weight for e in entries)
        a = 0
        for e in entries:
            e.lo, e.hi = a, a + e.weight - 1
            a += e.weight


def _steps(ops):
    """{number of draws: probability} for a list of ops"""
    dist = {0: 1.0}
    for op in ops:
        k = op[0]
        if k == "count":
            add = {1: 1.0} if op[2] > op[1] else {0: 1.0}
        elif k == "ench":
            c = op[1]
            n2 = sum(1 for _e, mx in c if mx > 1)
            add = {}
            if len(c) - n2:
                add[1] = (len(c) - n2) / len(c)
            if n2:
                add[2] = n2 / len(c)
        elif k == "float":
            add = {1: 1.0}
        elif k == "opaque":
            add = dict(op[1])
        else:
            add = {0: 1.0}
        nd = {}
        for a, pa in dist.items():
            for b, pb in add.items():
                nd[a + b] = nd.get(a + b, 0.0) + pa * pb
        dist = nd
    return dist


def _num(p):
    """an int provider → (lo, hi) (constant: lo == hi); None when not a plain uniform / constant"""
    import loot_engine as le
    if isinstance(p, (int, float)):
        return int(p // 1), int(p // 1)
    t = le._typ(p, "type")
    if t in ("", "uniform") and "min" in p and "max" in p:
        a, b = _num(p["min"]), _num(p["max"])
        if a is None or b is None or a[0] != a[1] or b[0] != b[1]:
            return None
        return a[0], max(a[0], b[0])
    if t == "constant":
        return int(p["value"]), int(p["value"])
    return None


_OPAQUE = {}


def _opaque_steps(version, item, f, t, n=6000):
    """(draws, probability) of a function whose draws vary (enchant_with_levels, the
    stew's effect): measured on the real function"""
    import json
    import random
    import loot_engine as le
    key = (version, item, t, json.dumps(f, sort_keys=True))
    v = _OPAQUE.get(key)
    if v is None:
        r = random.Random(12345)
        cnt = {}
        for _ in range(n):
            rnd = le.JavaRandom(r.getrandbits(48))
            ctx = le.Ctx(rnd, version)
            le.apply_function(le.Stack(item), f, t, ctx)
            cnt[rnd.used] = cnt.get(rnd.used, 0) + 1
        v = _OPAQUE[key] = tuple(sorted((k, c / n) for k, c in cnt.items()))
    return v


_MODELS = {}


def model(version, table):
    """[Pool] for a loot table, or Unsupported (what the reverse cannot model)"""
    key = (version, table)
    if key in _MODELS:
        m = _MODELS[key]
        if isinstance(m, Exception):
            raise m
        return m
    try:
        m = _MODELS[key] = _build(version, table)
    except Unsupported as e:
        _MODELS[key] = e
        raise
    return m


def _build(version, table):
    import loot_engine as le
    import loot_enchant as lx
    tab = le.get_table(version, table)
    if tab is None:
        raise Unsupported(f"no table {table}")
    if le._functions(tab) or tab.get("conditions"):
        raise Unsupported("table functions")
    pools = []
    for p in tab.get("pools", []):
        if p.get("conditions") or p.get("functions") or p.get("modifier"):
            raise Unsupported("pool conditions / functions")
        br = p.get("bonus_rolls")
        if br not in (None, 0, 0.0) and _num(br) != (0, 0):
            raise Unsupported("bonus rolls")
        r = _num(p.get("rolls", 1))
        if r is None:
            raise Unsupported("rolls")
        ents = []
        for i, e in enumerate(p.get("entries", [])):
            t = le._typ(e, "type")
            if e.get("conditions") or e.get("condition"):
                raise Unsupported("entry conditions")
            w = int(e.get("weight", 1))
            if w <= 0:
                continue
            if t == "empty":
                ents.append(Entry(i, None, w, []))
                continue
            if t != "item":
                raise Unsupported(f"entry {t}")
            item = e["name"].replace("minecraft:", "")
            ops = []
            for f, ft in le._functions(e):
                if f.get("conditions") or f.get("condition"):
                    raise Unsupported("function conditions")
                if ft == "set_count":
                    if f.get("add"):
                        raise Unsupported("set_count add")
                    c = _num(f["count"])
                    if c is None:
                        raise Unsupported("count provider")
                    ops.append(("count", c[0], c[1]) if c[1] > c[0] else ("const", c[0]))
                elif ft == "enchant_randomly":
                    cand = lx.randomly_candidates(item, f, version)
                    if not cand:
                        continue
                    ops.append(("ench", tuple((str(c.id).replace("minecraft:", ""), int(c.max_level))
                                              for c in cand)))
                    if item == "book":
                        item = "enchanted_book"
                elif ft == "set_damage":
                    d = f.get("damage")
                    if isinstance(d, (int, float)) or (isinstance(d, dict) and le._typ(d, "type") == "constant"):
                        continue
                    if isinstance(d, dict) and le._typ(d, "type") in ("", "uniform"):
                        ops.append(("float",))
                    else:
                        raise Unsupported("damage provider")
                elif ft in ("enchant_with_levels", "set_stew_effect"):
                    ops.append(("opaque", _opaque_steps(version, item, f, ft), ft))
                    if ft == "enchant_with_levels" and item == "book":
                        item = "enchanted_book"
                elif ft == "exploration_map":
                    item = "filled_map"
                elif ft in ("set_name", "set_lore", "set_potion", "set_nbt", "set_components",
                            "set_custom_data", "set_book_cover", "set_written_book_pages",
                            "set_banner_pattern", "explosion_decay", "furnace_smelt"):
                    continue
                else:
                    raise Unsupported(f"function {ft}")
            ents.append(Entry(i, item, w, ops))
        if ents:
            pools.append(Pool(r[0], r[1], ents))
    return pools


def simulate(pools, state):
    """the model run with real draws from a 48-bit state: ([(item, count, ench)], draws)
    — to check the model against loot_engine"""
    import loot_engine as le
    rnd = le.JavaRandom(0)
    rnd.s = state
    out = []
    for p in pools:
        n = p.rlo if p.rhi == p.rlo else rnd.next_int(p.rhi - p.rlo + 1) + p.rlo
        for _ in range(n):
            if len(p.entries) == 1:
                e = p.entries[0]
            else:
                v = rnd.next_int(p.total)
                e = next(x for x in p.entries if x.lo <= v <= x.hi)
            count, ench = 1, None
            for op in e.ops:
                if op[0] == "count":
                    count = rnd.next_int(op[2] - op[1] + 1) + op[1]
                elif op[0] == "const":
                    count = op[1]
                elif op[0] == "ench":
                    eid, mx = op[1][rnd.next_int(len(op[1]))]
                    lvl = rnd.next_int(mx) + 1 if mx > 1 else 1
                    ench = (eid, lvl)
                elif op[0] == "float":
                    rnd.next_float()
                elif op[0] == "opaque":
                    return None, rnd.used               # (measured, not replayed)
            if e.item is not None and count > 0:
                out.append((e.item, count, ench))
    return out, rnd.used


# ── the paths ──────────────────────────────────────────────────────────────────
# a constraint: (step, n, lo, hi) — the draw reading state s_step is nextInt(n) in
# [lo, hi] (n = 0: s_step itself in [lo, hi])

def _wants(q):
    """the wanted items with a minimum (the reverse needs at least one)"""
    out = []
    for w in q.get("items") or []:
        mn = int(w.get("min") or 0)
        if mn >= 1:
            out.append(dict(item=w["item"].replace("minecraft:", ""), need=mn,
                            ench=(w.get("ench") or None), lvl=int(w.get("ench_lvl") or 1),
                            potion=w.get("potion") or None))
    return out


def _gives(e, w):
    """how entry e can give want w: None (never) | 'sure' | 'ench' (exact enchant) | 'loose'"""
    if e.item != w["item"]:
        return None
    if not w["ench"]:
        return "sure"
    for op in e.ops:
        if op[0] == "ench":
            if any(eid == w["ench"] and mx >= w["lvl"] for eid, mx in op[1]):
                return "ench"
            return None
        if op[0] == "opaque" and op[2] == "enchant_with_levels":
            return "loose"
    return None


def _maxcount(e):
    for op in e.ops:
        if op[0] == "count":
            return op[2]
        if op[0] == "const":
            return op[1]
    return 1


class Path:
    __slots__ = ("cons", "prob", "loose", "steps")

    def __init__(self, cons, prob, loose, steps):
        self.cons, self.prob, self.loose, self.steps = cons, prob, loose, steps

    def __repr__(self):
        return f"Path(p={self.prob:.3g}, {len(self.cons)} constraints{', loose' if self.loose else ''})"


def paths(pools, q, max_nodes=400000, stats=None):
    """the paths of draws that give the wanted items, most likely first (a generator).
    Every chest that matches follows at least one (the picks that do not matter are
    only counted in draws); a path's chests are checked by the real fill.
    stats["cut"] = True when it stopped at max_nodes (then not every path was given)."""
    wants = _wants(q)
    if not wants:
        raise Unsupported("no minimum")
    W = len(wants)
    P = len(pools)
    # the most each pool can still give each want (pruning)
    per = [[max([_maxcount(e) for e in p.entries if _gives(e, w)] or [0]) for w in wants] for p in pools]
    after = [[0] * W for _ in range(P + 1)]
    for i in range(P - 1, -1, -1):
        for j in range(W):
            after[i][j] = after[i + 1][j] + pools[i].rhi * per[i][j]
    for j in range(W):
        if after[0][j] < wants[j]["need"]:
            return                                       # impossible: no path
    cnt = itertools.count()
    # node: (-prob, tie, state); state = (p, r, t, rstep, minr, need, cons, loose)
    heap = []

    def push(prob, st):
        if prob > 0:
            heapq.heappush(heap, (-prob, next(cnt), st))

    def start_pool(prob, p, t, need, cons, loose):
        if p >= P:
            return
        pl = pools[p]
        if pl.rhi > pl.rlo:
            push(prob, (p, 0, t + 1, t + 1, 0, need, cons, loose))
        else:
            push(prob, (p, 0, t, None, 0, need, cons, loose))

    start_pool(1.0, 0, 0, tuple(w["need"] for w in wants), (), False)
    nodes = 0
    seen = set()                                         # the same state reached twice
    while heap:
        if nodes >= max_nodes:
            if stats is not None:
                stats["cut"] = True
            return
        negp, _tie, st = heapq.heappop(heap)
        if st in seen:
            continue
        seen.add(st)
        prob = -negp
        nodes += 1
        p, r, t, rstep, minr, need, cons, loose = st
        if all(n <= 0 for n in need):
            cc = list(cons)
            pl = pools[p] if p < P else None
            if pl is not None and rstep is not None and minr > pl.rlo:
                cc.append((rstep, pl.rhi - pl.rlo + 1, minr - pl.rlo, pl.rhi - pl.rlo))
            yield Path(tuple(cc), prob, loose, t)
            continue
        pl = pools[p]
        # can the rest still give what is missing?
        if any(need[j] > (pl.rhi - r) * per[p][j] + after[p + 1][j] for j in range(W)):
            continue
        left = pl.rhi - r + 1                            # rolls in [r, rhi], uniform
        # END: this pool rolled exactly r
        if r >= pl.rlo:
            pe = 1.0 / left if rstep is not None else (1.0 if r == pl.rhi else 0.0)
            if pe > 0:
                cc = cons
                if rstep is not None:
                    cc = cons + ((rstep, pl.rhi - pl.rlo + 1, r - pl.rlo, r - pl.rlo),)
                start_pool(prob * pe, p + 1, t, need, cc, loose)
        if r >= pl.rhi:
            continue
        pc = (1.0 - 1.0 / left) if (rstep is not None and r >= pl.rlo) else 1.0
        if pc <= 0:
            continue
        nr, nminr = r + 1, max(minr, r + 1)
        one = len(pl.entries) == 1
        tp = t if one else t + 1                         # the pick's draw
        # relaxed picks: only their number of draws
        rel = {}
        for e in pl.entries:
            for k, pk in e.steps.items():
                rel[k] = rel.get(k, 0.0) + pk * e.weight / pl.total
        for k, pk in rel.items():
            push(prob * pc * pk, (p, nr, tp + k, rstep, nminr, need, cons, loose))
        # exact picks: the entries that give an item still wanted
        for e in pl.entries:
            gv = [(j, _gives(e, w)) for j, w in enumerate(wants) if need[j] > 0]
            gv = [(j, g) for j, g in gv if g]
            if not gv:
                continue
            c0 = cons if one else cons + ((tp, pl.total, e.lo, e.hi),)
            pe = e.weight / pl.total
            for prob2, t2, need2, c2, loose2 in _entry_branches(e, gv, wants, need, tp, c0, loose):
                push(prob * pc * pe * prob2, (p, nr, t2, rstep, nminr, need2, c2, loose2))


def _entry_branches(e, gv, wants, need, t, cons, loose):
    """the ways the wanted entry e can come out: (prob, t after, need after, cons, loose)"""
    states = [(1.0, t, 1, None, cons, loose, False)]     # prob, t, count, ench ok?, cons, loose, ench decided
    want_ench = {wants[j]["ench"]: wants[j]["lvl"] for j, g in gv if g == "ench"}
    maxneed = max(need[j] for j, _g in gv)
    for op in e.ops:
        nxt = []
        for prob, tt, count, eok, cc, lz, ed in states:
            k = op[0]
            if k == "count":
                lo, hi = op[1], op[2]
                n = hi - lo + 1
                top = max(lo, min(hi, maxneed))
                for c in range(lo, top):                 # less than needed: exact
                    nxt.append((prob / n, tt + 1, c, eok, cc + ((tt + 1, n, c - lo, c - lo),), lz, ed))
                c2 = cc + ((tt + 1, n, top - lo, hi - lo),) if top > lo else cc
                nxt.append((prob * (hi - top + 1) / n, tt + 1, top, eok, c2, lz, ed))
            elif k == "const":
                nxt.append((prob, tt, op[1], eok, cc, lz, ed))
            elif k == "ench":
                c = op[1]
                n = len(c)
                for eid, mx in c:                        # an enchantment a want asks for
                    if eid in want_ench and mx >= want_ench[eid]:
                        i = [x[0] for x in c].index(eid)
                        c2 = cc + ((tt + 1, n, i, i),)
                        if mx > 1:
                            L = want_ench[eid]
                            c2 = c2 + ((tt + 2, mx, L - 1, mx - 1),)
                            nxt.append((prob / n * (mx - L + 1) / mx, tt + 2, count, eid, c2, lz, True))
                        else:
                            nxt.append((prob / n, tt + 1, count, eid, c2, lz, True))
                # any other: only its draws
                n1 = sum(1 for _e, mx in c if mx <= 1)
                if n1:
                    nxt.append((prob * n1 / n, tt + 1, count, None, cc, lz, True))
                if n - n1:
                    nxt.append((prob * (n - n1) / n, tt + 2, count, None, cc, lz, True))
            elif k == "float":
                nxt.append((prob, tt + 1, count, eok, cc, lz, ed))
            elif k == "opaque":
                for st, ps in op[1]:
                    lz2 = lz or (op[2] == "enchant_with_levels" and bool(want_ench or
                                 any(g == "loose" for _j, g in gv)))
                    nxt.append((prob * ps, tt + st, count, "?" if op[2] == "enchant_with_levels" else eok,
                                cc, lz2, True))
        states = nxt
    for prob, tt, count, eok, cc, lz, _ed in states:
        nn = list(need)
        for j, g in gv:
            w = wants[j]
            if g == "sure" or (g == "ench" and eok == w["ench"]) or (g == "loose" and eok == "?"):
                nn[j] -= count
        yield prob, tt, tuple(nn), cc, lz


# ── the chest states (the lattice, in C) ───────────────────────────────────────
_L = None


def lib():
    """lootvm with the lr_* functions, or None"""
    global _L
    if _L is not None:
        return _L or None
    try:
        import loot_native
        L = loot_native.lib()
        if L is None or not hasattr(L, "lr_api"):
            _L = False
            return None
        L.lr_new.restype = ctypes.c_void_p
        L.lr_new.argtypes = [ctypes.c_int] + [ctypes.c_void_p] * 5 + [ctypes.c_int]
        L.lr_next.restype = ctypes.c_int
        L.lr_next.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int64]
        for f, rt in (("lr_isdone", ctypes.c_int), ("lr_dims", ctypes.c_int), ("lr_nodes", ctypes.c_int64)):
            getattr(L, f).restype = rt
            getattr(L, f).argtypes = [ctypes.c_void_p]
        L.lr_expected.restype = ctypes.c_double
        L.lr_expected.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.lr_free.restype = None
        L.lr_free.argtypes = [ctypes.c_void_p]
        L.lr_pop_scan.restype = ctypes.c_int
        L.lr_pop_scan.argtypes = [ctypes.c_uint64, ctypes.c_int64, ctypes.c_int64, ctypes.c_void_p,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        L.lr_siblings.restype = ctypes.c_int
        L.lr_siblings.argtypes = [ctypes.c_uint64, ctypes.c_int64, ctypes.c_void_p, ctypes.c_int,
                                  ctypes.c_void_p, ctypes.c_int]
        L.lr_chest_state.restype = ctypes.c_uint64
        L.lr_chest_state.argtypes = [ctypes.c_uint64, ctypes.c_int64, ctypes.c_int64]
        # (15/11) the long doubles must have 64 bits (Windows starts the x87 unit at 53:
        # the C side switches it, an older build did not and found nothing)
        if not hasattr(L, "lr_ld_bits"):
            _L = False
            return None
        L.lr_ld_bits.restype = ctypes.c_int
        L.lr_ld_bits.argtypes = []
        if L.lr_ld_bits() < 64:
            _L = False
            return None
        _L = L
    except Exception:                                   # noqa: BLE001
        _L = False
        return None
    return _L


def _c_constraint(c):
    """(step, n, lo, hi) → (step, kind, n, lo, hi) for lr_new"""
    t, n, lo, hi = c
    if n == 0:
        return t, 0, 0, lo, hi
    if n & (n - 1) == 0:                                 # nextInt(2^b): the top b bits of next(31)
        b = n.bit_length() - 1
        return t, 0, 0, lo << (48 - b), ((hi + 1) << (48 - b)) - 1
    return t, 1, n, lo, hi


class Solver:
    """the states of one path (lr_new / lr_next)"""

    def __init__(self, cons, dmax=22):
        L = lib()
        if L is None:
            raise Unsupported("lootvm without the reverse (rebuild: py tools/build_native.py --lootvm)")
        cc = [_c_constraint(c) for c in cons]
        m = len(cc)
        if m == 0:
            raise Unsupported("no constraint")
        A32, A64 = ctypes.c_int32 * m, ctypes.c_int64 * m
        self._args = (A32(*[c[0] for c in cc]), A32(*[c[1] for c in cc]), A64(*[c[2] for c in cc]),
                      A64(*[c[3] for c in cc]), A64(*[c[4] for c in cc]))
        self.L = L
        self.h = L.lr_new(m, *self._args, dmax)
        if not self.h:
            raise Unsupported("lr_new")
        self.expected = L.lr_expected(self.h, 0)
        self.dims = L.lr_dims(self.h)
        self.found = 0
        self._buf = (ctypes.c_uint64 * 256)()

    def next(self, cap=64, budget=2_000_000):
        if self.h is None:
            return []
        k = self.L.lr_next(self.h, self._buf, min(cap, 256), budget)
        self.found += k
        return list(self._buf[:k])

    @property
    def done(self):
        return self.h is None or bool(self.L.lr_isdone(self.h))

    @property
    def nodes(self):
        return 0 if self.h is None else int(self.L.lr_nodes(self.h))

    def close(self):
        if self.h is not None:
            self.L.lr_free(self.h)
            self.h = None

    def __del__(self):
        try:
            self.close()
        except Exception:                               # noqa: BLE001
            pass


def loot_seed(state):
    """a loot seed whose fill starts at this state"""
    v = (state ^ MULT) & M48
    return v if v else (1 << 48)                       # (0 means "no seed" to the game)


def check_state(version, table, state, q):
    """the real fill (loot_engine / lootvm) judged as one chest"""
    import loot_engine as le
    import loot_finder as lf
    slots, _ap = le.fill_chest(table, loot_seed(state), version)
    if slots is None:
        return False
    qq = dict(q, scope="chest", mode="find", tables=None)
    ok, _s, _h = lf.judge([{"slots": slots, "table": "chests/" + table}], qq)
    return ok


# ── the structures: where their chests' draws are ──────────────────────────────
# placement: kind 1 one try per region (salt, region, range) as cubiomes'
# getFeaturePos; kind 2 a chance per chunk (salt, next(24) <= thr) — the treasure
PLACE = {
    "desert_pyramid": (1, 14357617, 32, 24, 0),
    "igloo": (1, 14357618, 32, 24, 0),
    "jungle_temple": (1, 14357619, 32, 24, 0),
    "buried_treasure": (2, 10387320, 1, 1, 167772),
    "dungeon": (0, 0, 1, 1, 0),                          # (20/11) a feature: every chunk
    "shipwreck": (1, 165745295, 24, 20, 0),              # (20/11)
    "ocean_ruin": (1, 14357621, 20, 12, 0),
    "ruined_portal": (1, 34222645, 40, 25, 0),
}
TABLES = {"desert_pyramid": ("desert_pyramid",), "igloo": ("igloo_chest",),
          "jungle_temple": ("jungle_temple", "jungle_temple_dispenser"),
          "buried_treasure": ("buried_treasure",), "dungeon": ("simple_dungeon",),
          "shipwreck": ("shipwreck_supply", "shipwreck_map", "shipwreck_treasure"),
          "ocean_ruin": ("underwater_ruin_small", "underwater_ruin_big"),
          "ruined_portal": ("ruined_portal",)}
# (20/11) where their chests' draws are: (chest chunk − start chunk, draws before its
# nextLong), the commonest first — measured on ~400 000 real chests (art/rv/cfgmine2.py):
# the wreck's / ruin's containers and markers before it in its chunk, the sinking of a
# beached wreck (+1), the portal's chunk (1.17: the centre of its box). Kept: ≥ 98.5 %.
MINED = {
    "shipwreck_supply": [(0, 0, 2), (0, 1, 2), (-1, 0, 2), (0, 0, 6), (0, 0, 3), (0, 1, 3), (-1, 0, 3),
                         (0, 1, 4)],
    "shipwreck_map": [(0, 0, 4), (0, 1, 6), (0, 0, 6), (0, 1, 2), (0, 1, 4), (0, 0, 2), (-1, 0, 2),
                      (0, 0, 5), (0, 1, 5), (-1, 1, 2), (0, 1, 3), (0, 1, 7), (-1, 0, 3)],
    "shipwreck_treasure": [(0, 0, 6), (0, 1, 4), (0, 0, 4), (0, 1, 6), (0, 1, 2), (-1, 0, 2), (-1, 1, 2),
                           (0, 0, 2), (0, 0, 7), (0, 1, 7), (-1, 1, 3), (0, 0, 3), (0, 1, 5), (0, 1, 3)],
    "underwater_ruin_big": [(-1, 0, 0), (-1, -1, 0), (0, 0, 0), (0, -1, 0), (-1, 0, 4), (-1, 0, 2),
                            (0, 0, 4), (0, 0, 2), (-1, -1, 4), (-1, -1, 2), (0, -1, 4), (0, -1, 2)],
    "underwater_ruin_small": [
        (-1, -1, 0), (0, -1, 0), (-1, 0, 0), (0, 0, 0), (-1, -1, 2), (0, -1, 2), (-1, 0, 2), (0, 0, 2),
        (-1, -1, 4), (0, -1, 4), (-1, 0, 4), (0, 0, 4), (-2, -1, 0), (-2, 0, 0), (-1, -2, 0), (0, -2, 0),
        (-2, -1, 2), (-2, 0, 2), (-1, 1, 0), (-1, -2, 2), (1, -1, 0), (0, 1, 0), (0, -2, 2), (-2, -1, 4),
        (1, 0, 0), (-2, 0, 4), (-1, -2, 4), (0, -2, 4), (-1, 1, 2), (-2, -2, 0), (0, 1, 2), (1, -1, 2),
        (1, 0, 2), (-1, 1, 4), (1, -1, 4), (0, 1, 4), (1, 0, 4), (-2, 1, 0), (1, -2, 0), (-2, -2, 2),
        (1, 1, 0), (-2, 1, 2), (-2, -2, 4), (1, -2, 2), (-2, 1, 4), (1, 1, 2), (1, -2, 4), (1, 1, 4),
        (-1, -1, 6), (-1, -1, 8), (-1, -1, 10), (-1, -2, 6), (-1, -2, 8), (-2, -1, 6), (-1, 0, 6),
        (-2, -1, 8), (-1, 0, 8), (0, -1, 6), (0, -1, 8), (-2, 0, 6)],
    "ruined_portal": [(0, 0, 0), (0, -1, 0), (-1, 0, 0), (0, 1, 0)],
}
# (20/11) their chests depend on a biome (the beached wreck, the warm ruin, the portal's
# type: template, box): a structure seed is confirmed with each, every sister re-judged
VARIANTS = {"shipwreck": (False, True), "ocean_ruin": (False, True),
            "ruined_portal": ("standard", "desert", "jungle", "swamp", "mountain", "ocean")}
DUNGEON_Y = (10, 76)                                     # the attempts' y covered (≈ 98 % of the dungeons)
DUNGEON_PER_CHUNK = 0.0073                               # (1.16 / 1.17, measured on the C engine)
_SALT_NAME = {"desert_pyramid": "desert_pyramid", "igloo": "igloo", "jungle_temple": "jungle_pyramid",
              "shipwreck": "shipwreck", "ocean_ruin": "ocean_ruin", "ruined_portal": "ruined_portal"}


def salt(key, mc):
    """index + 10000 step of setFeatureSeed"""
    if key == "buried_treasure":
        import structure_loot as sl
        return sl._treasure_index(mc) + 10000 * sl.UNDERGROUND_STRUCTURES
    import scattered_loot as sc
    return sc.surface_index(_SALT_NAME[key], mc) + 10000 * sc.SURFACE_STRUCTURES


def configs(key, table, mc=None):
    """[(dx, dz, dsteps)]: the chest's chunk from the start chunk and the draws of the
    chunk's structure RNG before its nextLong (every rotation / chest of the table)"""
    out = []
    if key == "dungeon":                                 # (20/11) (salt index, holes h0..h1)
        return [(2, 0, 0)]
    if key == "ruined_portal" and mc is not None and int(mc) <= 20:
        return [(0, 0, 0)]                               # 1.16: the start chunk places it
    if table in MINED and key in VARIANTS:
        return list(MINED[table])
    if key == "buried_treasure":
        out = [(0, 0, 0)]
    elif key == "desert_pyramid":
        out = [(0, 0, 2 * k) for k in range(4)]          # N E S W, all in the start chunk
    elif key == "igloo":
        import scattered_loot as sc
        for rot in range(4):
            lx, lz = sc._rot(1, 6, rot, 3, 7)           # the basement chest
            out.append((lx >> 4, (lz - 2) >> 4, 2))     # after the template's own nextLong
    elif key == "jungle_temple":
        import scattered_loot as sc
        want = ("chest_main", "chest_hidden") if table == "jungle_temple" else ("disp1", "disp2")
        for ori in range(4):
            p = sc.Scattered(0, 64, 0, 12, 10, 15, ori)
            for key2 in want:
                lx, _ly, lz = next(b for k, b in sc._J_EVENTS if k == key2)
                cc = (p.wx(lx, lz) >> 4, p.wz(lx, lz) >> 4)
                d = 0
                for k, b in sc._J_EVENTS:
                    if k == key2:
                        break
                    if k == "box":
                        d += (b[3] - b[0] + 1) * (b[4] - b[1] + 1) * (b[5] - b[2] + 1)
                    elif (p.wx(b[0], b[2]) >> 4, p.wz(b[0], b[2]) >> 4) == cc:
                        d += 2                           # a container before it, same chunk
                out.append((cc[0], cc[1], d))
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res


def start_chunks(key, cx, cz, radius):
    """the start chunks whose structure is within radius of (cx, cz), nearest first
    (the positions loot_finder.positions measures from) — all of them (tests)"""
    c = Chunks(key, cx, cz, radius)
    out, i = [], 0
    while True:
        b = c.get(i, 4096)
        if not b:
            return out
        out += b
        i += len(b)


class Chunks:
    """(15/11) the start chunks within the radius, nearest first, made ring by ring
    as they are needed: a 30 000-block radius holds millions of chunks (a search
    rarely goes beyond the first thousands)"""

    def __init__(self, key, cx, cz, radius):
        import array
        self.pl = PLACE[key]
        self.off = 9 if key == "buried_treasure" else (8 if key == "dungeon" else 0)
        self.cx, self.cz, self.r = int(cx), int(cz), int(radius)
        self.c0x, self.c0z = (self.cx - self.off) // 16, (self.cz - self.off) // 16
        self.kmax = self.r // 16 + 2
        self.k = 0                                       # the next ring
        self.pend = []                                   # heap (d2 in chunks, x, z)
        self.xs, self.zs = array.array("i"), array.array("i")

    def _ok(self, x, z):
        pl = self.pl
        if pl[0] == 1 and ((x % pl[2]) >= pl[3] or (z % pl[2]) >= pl[3]):
            return False
        bx, bz = x * 16 + self.off, z * 16 + self.off
        return (bx - self.cx) ** 2 + (bz - self.cz) ** 2 <= self.r * self.r

    def _ring(self):
        k, x0, z0 = self.k, self.c0x, self.c0z
        self.k += 1
        if k == 0:
            pts = [(x0, z0)]
        else:
            pts = [(x0 + d, z0 - k) for d in range(-k, k + 1)] + [(x0 + d, z0 + k) for d in range(-k, k + 1)]
            pts += [(x0 - k, z0 + d) for d in range(-k + 1, k)] + [(x0 + k, z0 + d) for d in range(-k + 1, k)]
        for x, z in pts:
            if self._ok(x, z):
                heapq.heappush(self.pend, ((x - x0) ** 2 + (z - z0) ** 2, x, z))
        # every chunk of the next rings is at least k + 1 chunks away
        lim = (k + 1) ** 2 if self.k <= self.kmax else None
        while self.pend and (lim is None or self.pend[0][0] < lim):
            _d, x, z = heapq.heappop(self.pend)
            self.xs.append(x)
            self.zs.append(z)

    def get(self, pos, n):
        while len(self.xs) < pos + n and self.k <= self.kmax:
            self._ring()
        return [(self.xs[i], self.zs[i]) for i in range(pos, min(pos + n, len(self.xs)))]

    def exhausted(self, pos):
        while len(self.xs) <= pos and self.k <= self.kmax:
            self._ring()
        return pos >= len(self.xs)


def pop_scan(key, mc, state, cfg, chunks, cap=256):
    """the structure seeds that put a chest of this state at one of these start
    chunks (config cfg): [(structure seed, (cx, cz))]"""
    L = lib()
    if L is None or not chunks:
        return []
    dx, dz, ds = cfg
    n = len(chunks)
    arr = (ctypes.c_int32 * (4 * n))()
    for i, (x, z) in enumerate(chunks):
        arr[4 * i], arr[4 * i + 1], arr[4 * i + 2], arr[4 * i + 3] = x, z, x + dx, z + dz
    pl = PLACE[key]
    out = (ctypes.c_uint64 * cap)()
    oi = (ctypes.c_int32 * cap)()
    k = L.lr_pop_scan(ctypes.c_uint64(state), ds, salt(key, mc), arr, n, pl[0], pl[1], pl[2], pl[3],
                      pl[4], out, oi, cap)
    return [(int(out[i]), chunks[oi[i]]) for i in range(min(k, cap))]


def dungeon_scan(mc, state, cfg, chunks, cap=65536):
    """(20/11) the structure seeds whose dungeon in one of these chunks can put this chest
    state (config cfg = (salt index, h0, h1)): [(structure seed, chunk)]"""
    L = lib()
    if L is None or not chunks or not hasattr(L, "lr_dungeon_scan"):
        return []
    if not getattr(L, "_dng_set", False):
        L.lr_dungeon_scan.restype = ctypes.c_int
        L.lr_dungeon_scan.argtypes = [ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_int]
        L._dng_set = True
    n = len(chunks)
    arr = (ctypes.c_int32 * (2 * n))()
    for i, (x, z) in enumerate(chunks):
        arr[2 * i], arr[2 * i + 1] = x, z
    out = (ctypes.c_uint64 * cap)()
    oi = (ctypes.c_int32 * (4 * cap))()
    idx, h0, h1 = cfg
    k = L.lr_dungeon_scan(ctypes.c_uint64(state), arr, n, idx, h0, h1, DUNGEON_Y[0], DUNGEON_Y[1], out, oi, cap)
    seen, res = set(), []
    for i in range(min(k, cap)):
        r = (int(out[i]), chunks[oi[4 * i]])
        if r not in seen:
            seen.add(r)
            res.append(r)
    return res


def _signed(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


def dungeon_sisters(S):
    """the sisters a dungeon candidate is tried on first: the terrain (biomes) changes
    with the upper bits — 2 of them find ~86 % of the real ones"""
    return (0, ((S * 0x9E3779B97F4A7C15) >> 40) & 0xFFFF or 1)


def dungeon_match(seed, mc, sub, chunk, q, qa=None, state=None, xz=None):
    """(20/11) the dungeons of `chunk` for this world seed, as the map shows them (C engine,
    the main generation order): (x, z, score, matching card indexes) of the first one
    whose chests match q (one of them the chest `state` matching qa, when given), else
    None. xz: only the dungeon whose spawner is there."""
    import dungeon_loot as dl
    import loot_engine as le
    import loot_finder as lf
    alone = dl.chunk_dungeons(seed, mc, chunk[0], chunk[1], False) or []
    for d in alone:                                      # (the chunk alone first: 4× faster)
        if xz is not None and (d[0], d[2]) != tuple(xz):
            continue
        if state is not None and not any(((c[3] ^ MULT) & M48) == state for c in d[6]):
            continue
        cards = dl.chests(seed, mc, d[0], d[2], le.loot_version(mc, sub), d[1])
        main = []
        for c in cards:                                  # the main order's cards come first
            if c.get("exact", True) is False or "if this chunk" in str(c.get("name", "")):
                break
            main.append(c)
        if not main:
            continue
        if state is not None:
            mine = False
            for c in main:
                if c.get("seed") is None or ((int(c["seed"]) ^ MULT) & M48) != state:
                    continue
                ok, _s, _h = lf.judge([c], dict(qa or q, scope="chest", mode="find", tables=None))
                mine = mine or ok
            if not mine:
                continue
        ok, score, hits = lf.judge(main, q)
        if ok:
            return d[0], d[2], score, hits
    return None


def confirm_dungeon(mc, sub, S, chunk, state, q, qa=None):
    """a dungeon candidate (structure seed S, chunk): the first of its trial sisters where
    the dungeon is there with this chest and matches → (x, z, score, hits), else None"""
    for u in dungeon_sisters(S):
        r = dungeon_match(_signed(S | (u << 48)), mc, sub, chunk, q, qa, state)
        if r is not None:
            return r
    return None


def struct_pos(key, chunk):
    off = 9 if key == "buried_treasure" else 0
    return chunk[0] * 16 + off, chunk[1] * 16 + off


def confirm(key, mc, sub, S, chunk, state, table, q, qa=None):
    """the structure seed's chests at that chunk, as SSA shows them (structure_loot,
    without the terrain): one of the `table` chests has this state and matches qa
    (the chest's own part, q by default) → (x, z, score, matching card indexes)
    judged on the whole query q, else None"""
    import loot_finder as lf
    import structure_loot as sl
    x, z = struct_pos(key, chunk)
    if key in VARIANTS:                                  # (20/11) each biome it can have
        for v in VARIANTS[key]:
            try:
                cards = variant_cards(key, S, mc, x, z, sub, v)
            except Exception:                            # noqa: BLE001
                continue
            r = _judge_mine(cards, state, table, q, qa)
            if r is not None:
                return (x, z) + r
        return None
    try:
        cards = sl.chests(lf.LOOT_KEY.get(key, key), S, mc, x, z, sub, fast=True)
    except Exception:                                    # noqa: BLE001
        return None
    mine = False
    for c in cards or []:
        if c.get("seed") is None or str(c.get("table", "")).replace("chests/", "") != table:
            continue
        if ((int(c["seed"]) ^ MULT) & M48) != state:
            continue
        ok, _s, _h = lf.judge([c], dict(qa or q, scope="chest", mode="find", tables=None))
        mine = mine or ok
    if not mine:
        return None
    ok, score, hits = lf.judge(cards, q)
    return (x, z, score, hits) if ok else None


def _judge_mine(cards, state, table, q, qa=None):
    """(score, hits) when one of the `table` cards has this state and matches qa, and all
    the cards match q; else None"""
    import loot_finder as lf
    mine = False
    for c in cards or []:
        if c.get("seed") is None or str(c.get("table", "")).replace("chests/", "") != table:
            continue
        if ((int(c["seed"]) ^ MULT) & M48) != state:
            continue
        ok, _s, _h = lf.judge([c], dict(qa or q, scope="chest", mode="find", tables=None))
        mine = mine or ok
    if not mine:
        return None
    ok, score, hits = lf.judge(cards, q)
    return (score, hits) if ok else None


def variant_cards(key, S, mc, x, z, sub, v):
    """(20/11) the chests (structure_loot, no terrain) with the biome-dependent choice
    forced: a beached wreck (v True), a warm ruin, a portal of type v"""
    import loot_finder as lf
    import scattered_loot as sc
    import structure_loot as sl
    if key == "shipwreck":
        old, name = sc.shipwreck_beached, "shipwreck_beached"
        new = (lambda *a, **k: v)
        mod = sc
    elif key == "ocean_ruin":
        old, name = sc.ocean_ruin_warm, "ocean_ruin_warm"
        new = (lambda *a, **k: v)
        mod = sc
    else:
        import ruined_portal_loot as rp
        old, name, mod = rp.candidates, "candidates", rp

        def new(seed, mc_, cx, cz):
            _r, vp, air, tpl, rot, mirror, size, chest, bb = rp._prefix(seed, mc_, cx, cz, v)
            return [dict(kind=v, template=tpl, rot=rot, mirror=mirror, box=bb, size=size, chest=chest,
                         placement=vp, air=air, origin=(cx << 4, 0, cz << 4))]
    setattr(mod, name, new)
    try:
        return sl.chests(lf.LOOT_KEY.get(key, key), S, mc, x, z, sub, fast=True)
    finally:
        setattr(mod, name, old)


# ── (15/11) how fast: the chance that a candidate matches ─────────────────────
def stage_odds(q, ver, key, stages):
    """{"p": one structure (« Whole structure ») / one chest matches, "pi": {stage: chance
    that a candidate structure of that stage matches}} — loot_exact's exact chest
    distributions, the chests of a structure taken as independent (as its odds)"""
    import loot_exact as lx
    qm = dict(q, mode="find")
    items = qm.get("items") or []
    caps = lx._caps(qm)
    k = len(items)
    zero = (0,) * k
    sel = set(_tables(q))
    comp = [t for t, _d in siblings(key) if t in sel]
    if q.get("scope") != "structure" or len(comp) < 2:
        t = next(iter(sel), None)
        r = lx.chest_prob(qm, t, ver) if t else None
        return {"p": r.p if r else 0.0, "pi": {}, "chests": max(1, NCHESTS.get(t, 1))}
    f = {t: lx.chest_dist(qm, t, ver, None, caps) or {zero: 1.0} for t in set(comp)}

    def comb(a, b):
        return tuple(min(caps[i], a[i] + b[i]) for i in range(k))

    def ok(v):
        return all(lx._in_range(v[i], w) for i, w in enumerate(items))
    whole = {zero: 1.0}
    for t in comp:
        whole = lx._conv(whole, f[t], comb)
    p = sum(x for v, x in whole.items() if ok(v))
    others = {}
    pi = {}
    for si, (t, qa) in enumerate(stages):
        if t not in f:
            continue
        if t not in others:
            o = {zero: 1.0}
            rest = list(comp)
            rest.remove(t)
            for t2 in rest:
                o = lx._conv(o, f[t2], comb)
            others[t] = o
        idx = []                                         # qa's wants in q's order
        for w in qa.get("items") or []:
            j = next((i for i, x in enumerate(items) if x.get("item") == w.get("item")
                      and x.get("ench") == w.get("ench") and x.get("potion") == w.get("potion")), None)
            if j is not None:
                idx.append((j, int(w.get("min") or 0)))
        pa = pq = 0.0
        for v, x in f[t].items():
            if not all(v[j] >= m for j, m in idx):
                continue
            pa += x
            pq += x * sum(y for u, y in others[t].items() if ok(comb(v, u)))
        pi[si] = pq / pa if pa > 0 else 0.0
    return {"p": p, "pi": pi, "chests": len(comp)}


# ── the search ─────────────────────────────────────────────────────────────────
def _tables(q):
    tabs = TABLES.get(q.get("key"), ())
    sel = q.get("tables")
    if sel:
        sel = {str(t).replace("chests/", "") for t in sel}
        tabs = tuple(t for t in tabs if t in sel)
    return tabs


def supported(q, check_lib=True):
    """None when Reverse can search this query, else why not (one short sentence).
    check_lib=False: lootvm is not loaded (nor compiled) here when it is not yet"""
    import loot_engine as le
    key, mc = q.get("key"), int(q.get("mc", 0))
    if key not in PLACE:
        return ("Reverse searches buried treasures, desert pyramids, jungle temples, igloos, shipwrecks, "
                "ocean ruins, ruined portals and dungeons.")
    if key == "dungeon" and (check_lib or _L is not None) and lib() is not None \
            and not hasattr(lib(), "lr_dungeon_scan"):
        return "Reverse needs lootvm rebuilt: py tools/build_native.py --lootvm"
    if mc not in VERSIONS:
        return "Reverse works in Java 1.16 – 1.17 (from 1.18 a chest's seed cannot be traced back)."
    if q.get("mode", "find") != "find":
        return "Reverse works in Find mode."
    if not _wants(q):
        return "Reverse needs a wanted item with a minimum."
    if (check_lib or _L is not None) and lib() is None:
        return "Reverse needs lootvm rebuilt: py tools/build_native.py --lootvm"
    ver = le.loot_version(mc, q.get("sub"))
    tabs = _tables(q)
    if not tabs:
        return "Reverse: choose a chest of this structure."
    if not check_lib and any((ver, t) not in _MODELS for t in tabs):
        return None                                      # (the UI thread: checked at Start)
    ok = []
    for t in tabs:
        try:
            model(ver, t)
            ok.append(t)
        except Unsupported:
            pass
    if not ok:
        return "Reverse cannot model these chests."
    return None


# (15/11) « Whole structure »: how many chests of each table a structure has
NCHESTS = {"desert_pyramid": 4, "jungle_temple": 2, "jungle_temple_dispenser": 2, "igloo_chest": 1,
           "buried_treasure": 1, "simple_dungeon": 2, "shipwreck_supply": 1, "shipwreck_map": 1,
           "shipwreck_treasure": 1, "underwater_ruin_small": 2, "underwater_ruin_big": 1, "ruined_portal": 1}


def siblings(key):
    """(15/11) the containers that share the start chunk's structure RNG: [(table,
    draws before its nextLong)] (the desert pyramid's 4 chests, the jungle temple's 2
    dispensers and 2 chests), [] for a structure with one chest"""
    if key == "desert_pyramid":
        return [("desert_pyramid", d) for d in (0, 2, 4, 6)]
    if key == "jungle_temple":
        return ([("jungle_temple_dispenser", c[2]) for c in configs(key, "jungle_temple_dispenser")]
                + [("jungle_temple", c[2]) for c in configs(key, "jungle_temple")])
    return []


def structure_cfgs(key, ver, table, state, q):
    """(« Whole structure ») the configs of `table` where this chest state makes the whole
    structure match q — its other chests come from the same RNG, known at once"""
    import loot_engine as le
    import loot_finder as lf
    sib = siblings(key)
    cfgs = configs(key, table, q.get("mc"))
    if not sib:
        return cfgs
    L = lib()
    n = len(sib)
    ds = (ctypes.c_int64 * n)(*[d for _t, d in sib])
    out = (ctypes.c_uint64 * (8 * n))()
    good = []
    for cfg in cfgs:
        k = L.lr_siblings(ctypes.c_uint64(state), cfg[2], ds, n, out, 8)
        for a in range(k):
            cards = []
            for i, (t, _d) in enumerate(sib):
                slots, _ap = le.fill_chest(t, loot_seed(int(out[a * n + i])), ver)
                cards.append({"slots": slots, "table": "chests/" + t})
            if lf.judge(cards, dict(q, mode="find"))[0]:
                good.append(cfg)
                break
    return good


def anchors(q, ver, tabs):
    """[(table, the query one chest must meet)], strongest first. One chest: the query
    itself. Whole structure: first the whole query in one chest, then the anchor
    item alone, from all of it in one chest down to its share over all the chests
    that can hold it (ceil(min / chests)) — every matching structure has such a
    chest, so the Reverse stays complete; the structure is then judged whole."""
    if q.get("scope") != "structure":
        return [(t, q) for t in tabs]
    wants = [w for w in (q.get("items") or []) if int(w.get("min") or 0) >= 1]
    mods = {}
    for t in tabs:
        try:
            mods[t] = model(ver, t)
        except Unsupported:
            pass
    out = [(t, dict(q, scope="chest")) for t in mods]
    best = None
    for w in wants:
        ww = _wants(dict(items=[w]))[0]
        gives = [t for t, m in mods.items() if any(_gives(e, ww) for p in m for e in p.entries)]
        K = sum(NCHESTS.get(t, 1) for t in gives)
        if K == 0:
            return []                                    # no chest here can hold it
        share = max((sum(e.weight for e in p.entries if _gives(e, ww)) / p.total
                     for t in gives for p in mods[t]), default=1.0)
        lo = -(-int(w["min"]) // K)
        score = math.log(max(share, 1e-12)) * lo        # the rarest, the more of it per chest
        if best is None or score < best[0]:
            best = (score, w, gives, lo)
    if best is None:
        return out
    _sc, w, gives, lo = best
    for r in range(int(w["min"]), lo - 1, -1):
        for t in gives:
            out.append((t, dict(q, scope="chest", items=[dict(w, min=r, max=None)])))
    return out


SISTERS = 1 << 16
FIRST = 2048                                             # the sisters of a structure before the next one


class Engine:
    """what the Reverse search has found and where it is (kept across Pause / Resume)"""

    def __init__(self, q):
        import loot_engine as le
        self.q = dict(q)
        self.key, self.mc, self.sub = q["key"], int(q["mc"]), q.get("sub")
        self.ver = le.loot_version(self.mc, self.sub)
        self.chunks = Chunks(self.key, int(q.get("cx", 0)), int(q.get("cz", 0)),
                             int(q.get("radius", 1000)))
        self.gens = []                                   # [(table, its chest's query, paths)]
        self.stats = {}
        self.stages = anchors(q, self.ver, _tables(q))     # [(table, qa)]
        for si, (t, qa) in enumerate(self.stages):
            try:
                self.gens.append((t, qa, paths(model(self.ver, t), qa, stats=self.stats), si))
            except Unsupported:
                pass
        self.sid_stage = {}
        self.odds = None                                 # (15/11) the speed model (prepare_odds)
        self.n_expect = 0.0                              # matches expected from what was checked
        self.solvers = []                                # the paths being enumerated (dicts)
        self.parked = []                                 # the paths whose chests keep failing
        self.active = []                                 # [[table, state, cfgs, ci, pos, qa]]
        self.retry_pop, self.retry_sis, self.retry_chk = [], [], []
        self.structs = []                                # [[S, x, z, score, hits, u]]
        self.seen_s, self.seen_states = set(), set()
        self.n_paths = self.n_states = self.n_tried = self.n_struct = 0
        self.chk_out = 0                                 # check blocks being worked on
        self.suspect = False
        self.whole = q.get("scope") == "structure" and bool(siblings(self.key))
        self.gens_done = False
        self.nowhere = self.chunks.exhausted(0)          # (no place for the structure)
        self.discovery_done = self.nowhere
        self.step = 0
        self._sid = itertools.count()

    # discovery ─────────────────────────────────────────────────────────────
    # (15/11) the chest states come from the paths' lattices here (C, fast); the
    # processes check them ("chk" blocks: the real fill, and in « Whole structure »
    # the other chests of the structure) — a rare structure needs millions of them.
    def _solver(self):
        """the next path to take states from (round robin over a few)"""
        while len(self.solvers) < 4 and (self.gens or self.parked):
            if not self.gens:                            # nothing else: back to the slow ones
                self.solvers.append(self.parked.pop(0))
                continue
            t, qa, g, si = self.gens[0]
            try:
                pth = next(g)
            except StopIteration:
                self.gens.pop(0)
                continue
            self.n_paths += 1
            try:
                sid = next(self._sid)
                self.solvers.append(dict(id=sid, t=t, qa=qa, so=Solver(pth.cons), tried=0, since=0))
                self.sid_stage[sid] = si
            except Unsupported:
                continue
        if not self.solvers:
            return None
        sv = self.solvers.pop(0)
        self.solvers.append(sv)
        return sv

    def chk_block(self, n=256, budget=400_000):
        """("chk", q, table, qa, solver id, [states]) or None"""
        if self.retry_chk:
            self.chk_out += 1
            return self.retry_chk.pop()
        for _ in range(8):
            sv = self._solver()
            if sv is None:
                self._check_done()
                return None
            so = sv["so"]
            xs = [x for x in so.next(n, budget) if (sv["t"], x) not in self.seen_states]
            if so.done:
                if so.found == 0 and so.expected >= 4.0:
                    self.suspect = True                  # thousands expected, none: never "impossible"
                so.close()
                self.solvers.remove(sv)
            if xs:
                for x in xs:
                    self.seen_states.add((sv["t"], x))
                self.chk_out += 1
                return ("chk", self.q, sv["t"], sv["qa"], sv["id"], xs)
        return None

    def feed_chk(self, b, out, n=None):
        """a check block's result: the states that pass → active"""
        self.chk_out = max(0, self.chk_out - 1)
        t, qa, sid, xs = b[2], b[3], b[4], b[5]
        self.n_tried += len(xs)
        nst = (n or (0, 0))[1]
        self.n_struct += nst
        if self.odds is not None:
            self.n_expect += nst * self.odds["pi"].get(self.sid_stage.get(sid), 0.0)
        for x, cfgs in out:
            self.n_states += 1
            self.active.append([t, x, list(cfgs), 0, 0, qa])
        for sv in self.solvers:
            if sv["id"] == sid:
                sv["tried"] += len(xs)
                sv["since"] = 0 if out else sv["since"] + len(xs)
                if sv["since"] >= 4096 and (self.gens or self.parked) and not (
                        self.whole and self.odds):       # (whole: the stages' odds decide)
                    self.solvers.remove(sv)              # its chests rarely pass: others first
                    self.parked.append(sv)
                break
        self._check_done()

    def _check_done(self):
        if not (self.gens or self.solvers or self.parked or self.retry_chk) and self.chk_out == 0:
            self.discovery_done = True

    def area_fit(self):
        """(15/11) how many of the structure's places lie in the area, on average, for
        one matching chest (its structure seeds put it at each start chunk with the
        placement's chance): below 1, most matches have no structure in the area"""
        if getattr(self, "_fit", None) is None:
            pl = PLACE[self.key]
            R = int(self.q.get("radius", 1000))
            if self.key == "dungeon":                    # (20/11) one dungeon every ~140 chunks
                self._fit = math.pi * R * R / 256.0 * DUNGEON_PER_CHUNK
                return self._fit
            if R <= 1500:
                n = len(start_chunks(self.key, int(self.q.get("cx", 0)), int(self.q.get("cz", 0)), R))
            else:
                n = math.pi * R * R / 256.0 * ((pl[3] / pl[2]) ** 2 if pl[0] == 1 else 1.0)
            self._fit = n * (1.0 / (pl[3] * pl[3]) if pl[0] == 1 else (pl[4] + 1) / float(1 << 24))
        return self._fit

    def prepare_odds(self):
        try:
            self.odds = stage_odds(self.q, self.ver, self.key, self.stages)
        except Exception:                                # noqa: BLE001 — no estimate, that is all
            self.odds = None
            return
        if self.whole and self.odds.get("pi"):
            # (15/11) the stages whose candidates match most often first: every one is
            # still gone through (complete), the likeliest matches come first
            pi = self.odds["pi"]
            self.gens.sort(key=lambda g: -pi.get(g[3], 0.0))

    def impossible(self):
        """every path tried to the end and no chest state: no chest of these tables can
        match (up to Java's nextInt re-draws, less than 1 in 10^6 draws)"""
        return (self.discovery_done and self.n_states == 0 and not self.active and not self.nowhere
                and not self.stats.get("cut") and not self.suspect)

    def checks_left(self):
        return not self.discovery_done and bool(self.gens or self.solvers or self.parked or self.retry_chk)

    # blocks ────────────────────────────────────────────────────────────────
    def pop_block(self, size):
        if self.retry_pop:
            return self.retry_pop.pop()
        if not self.active:
            return None
        self.step += 1
        a = self.active[self.step % len(self.active)]
        t, x, cfgs, ci, pos, qa = a
        chunks = self.chunks.get(pos, size)
        cfg = cfgs[ci]
        if ci + 1 < len(cfgs):
            a[3] = ci + 1
        else:
            a[3], a[4] = 0, pos + size
            if self.chunks.exhausted(pos + size):
                self.active.remove(a)
        if not chunks:
            return self.pop_block(size)
        return ("pop", self.q, t, x, cfg, chunks, qa)

    def sis_block(self):
        if self.retry_sis:
            return self.retry_sis.pop()
        live = [s for s in self.structs if s[5] < SISTERS]
        if not live:
            return None
        s = min(live, key=lambda r: r[5])
        if self.key == "dungeon":                        # (20/11) ~20 ms a sister (the terrain)
            n = 64 if s[5] < FIRST else 512
        else:
            n = 512 if s[5] < FIRST else 8192
        u0 = s[5]
        s[5] = min(SISTERS, u0 + n)
        return ("sis", self.q, s[0], s[1], s[2], s[3], s[4], u0, s[5] - u0)

    def sisters_left(self):
        return bool(self.retry_sis) or any(s[5] < SISTERS for s in self.structs)

    def first_pass_done(self):
        """every structure found has had its first sisters (variety: more structures
        before more of the same one)"""
        return not self.retry_sis and all(s[5] >= FIRST for s in self.structs)

    def pops_left(self):
        return bool(self.retry_pop or self.active or not self.discovery_done)

    def give_back(self, b):
        if b[0] == "chk":
            self.chk_out = max(0, self.chk_out - 1)
            self.retry_chk.append(b)
            return
        (self.retry_pop if b[0] == "pop" else self.retry_sis).append(b)

    def feed_structs(self, out):
        for S, x, z, score, hits in out:
            if (S, x, z) not in self.seen_s:
                self.seen_s.add((S, x, z))
                self.structs.append([S, x, z, score, hits, 0])


_WK = {}


def _rwork(b):
    """a worker's block: ("pop", …) → the confirmed structure seeds; ("sis", …) →
    the world seeds (the upper 16 bits) where the structure is really there"""
    import time
    t0 = time.perf_counter()
    if "vm" not in _WK:
        try:
            import loot_native
            _WK["vm"] = loot_native.enable(True)
        except Exception:                                # noqa: BLE001
            _WK["vm"] = False
    q = b[1]
    key, mc, sub = q["key"], int(q["mc"]), q.get("sub")
    if b[0] == "chk":                                    # (15/11) the chest states: real fill,
        import loot_engine as le                         # then the whole structure
        _k, _q, table, qa, _sid, xs = b
        ver = le.loot_version(mc, sub)
        whole = q.get("scope") == "structure" and bool(siblings(key))
        out = []
        nst = 0                                          # whole structures judged
        for x in xs:
            if not check_state(ver, table, x, qa):
                continue
            if whole:
                nst += len(configs(key, table, mc))
                cfgs = structure_cfgs(key, ver, table, x, q)
            else:
                cfgs = configs(key, table, mc)
            if cfgs:
                out.append((x, tuple(cfgs)))
        return "chk", (len(xs), nst), out, time.perf_counter() - t0
    if b[0] == "pop":
        _k, _q, table, state, cfg, chunks, qa = b
        out = []
        if key == "dungeon":                             # (20/11) the terrain decides
            for S, ch in dungeon_scan(mc, state, cfg, chunks):
                r = confirm_dungeon(mc, sub, S, ch, state, q, qa)
                if r is not None:
                    out.append((S,) + tuple(r))
            return "pop", len(chunks), out, time.perf_counter() - t0
        for S, ch in pop_scan(key, mc, state, cfg, chunks):
            r = confirm(key, mc, sub, S, ch, state, table, q, qa)
            if r is not None:
                out.append((S,) + tuple(r))
        return "pop", len(chunks), out, time.perf_counter() - t0
    _k, _q, S, x, z, score, hits, u0, n = b
    if key == "dungeon":                                 # (20/11) each sister: its terrain
        out = []
        ch = (x >> 4, z >> 4)
        for u in range(u0, u0 + n):
            seed = _signed(S | (u << 48))
            r = dungeon_match(seed, mc, sub, ch, q, xz=(x, z))
            if r is not None:
                out.append((seed,) + tuple(r))
        return "sis", n, out, time.perf_counter() - t0
    import cubiomes_wrap as cw
    import loot_finder as lf
    g = lf._gen(mc, 0)
    L = g._lib
    st = cw.ALL_STRUCTURE_TYPES[lf.FINDER[key]]
    fast = lf._c_positions(L)
    out = []
    for u in range(u0, u0 + n):
        seed = S | (u << 48)
        if seed >= 1 << 63:
            seed -= 1 << 64
        g.setup_and_seed(mc, seed, large_biomes=False, dim=0)
        ok = fast[1](st, g._gen, x, z) if fast else L.isViableStructurePos(st, g._gen, x, z, 0)
        if ok and key in VARIANTS:                       # (20/11) its biome: its own chests
            ok = False
            try:
                import structure_loot as sl
                cards = sl.chests(lf.LOOT_KEY.get(key, key), seed, mc, x, z, sub, fast=True)
                ok, score, hits = lf.judge(cards, q)
                if ok and key == "ruined_portal":        # the netherrack / lava can take the chest
                    cards = sl.chests(key, seed, mc, x, z, sub)
                    ok, score, hits = lf.judge(cards, q)
            except Exception:                            # noqa: BLE001
                ok = False
        if ok:
            out.append((seed, x, z, score, hits))
    return "sis", n, out, time.perf_counter() - t0


def _fmt_dt(sec):
    sec = float(sec)
    for d, u in ((365.25 * 86400, "years"), (86400, "days"), (3600, "h"), (60, "min")):
        if sec >= d:
            v = sec / d
            return f"{v:.2g} {u}" if v < 10 else f"{v:.0f} {u}"
    return "< 1 s" if sec < 1 else f"{sec:.0f} s"


def _fmt_n(v):
    v = float(v)
    for d, u in ((1e9, " G"), (1e6, " M"), (1e3, " k")):
        if v >= d:
            return f"{v / d:.3g}{u}"
    return f"{v:.0f}"


class ReverseSearch:
    """loot_finder.Search's interface (run, stop, pause, the state to resume from)
    for the Reverse mode. on_hit(seed, x, z, score, hits); on_progress(done, None,
    rate, found, elapsed) — done = world seeds tested."""

    POP_S = 0.25                                         # seconds of work per pop block

    def __init__(self, q, threads, on_hit, on_progress, source=("reverse", None), rule=("never", None),
                 state=None, block=4):
        import threading
        self.q = q
        self.threads = max(1, int(threads))
        self.on_hit, self.on_progress = on_hit, on_progress
        self.source, self.rule = source, rule
        st = dict(state or {})
        self.eng = st.get("engine") or Engine(q)
        self.done = int(st.get("done", 0))
        self.found = int(st.get("found", 0))
        self.elapsed0 = float(st.get("elapsed", 0.0))
        self.elapsed = self.elapsed0
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.dungeon = q.get("key") == "dungeon"
        self.pop_size = 2 if self.dungeon else 256        # (20/11) a dungeon chunk: ~0.3 s
        self.err = None
        self.fails = self.oks = 0
        self._hist, self._sp = [], {}

    def total(self):
        return None

    def state(self, end):
        return dict(cursor=0, done=self.done, found=self.found, elapsed=self.elapsed, list_pos=0,
                    end=end, engine=self.eng, note=self.note())

    def note(self):
        e = self.eng
        if self.err is not None and self.oks == 0:
            return f"error: {self.err}"
        if e.impossible():
            return "impossible"
        try:
            sp = self.speed()
        except Exception:                                # noqa: BLE001 (two threads read it)
            sp = {}
        word = "dungeon" if e.key == "dungeon" else "structure"
        if not e.structs:
            cnt = e.n_struct if e.whole else e.n_tried
            txt = f"{_fmt_n(cnt)} candidate{'s' if e.whole else ' chests'}"
            if sp.get("rate"):
                txt += f" · {_fmt_n(sp['rate'])}/s"
        else:
            ns, nc = len(e.structs), e.n_states
            txt = f"{ns} {word}{'s' * (ns != 1)} · {nc} chest{'s' * (nc != 1)}"
        if not e.whole:                                  # (20/11) one chest: how long per structure
            if sp.get("found_every"):
                txt += f" · 1 {word} / {_fmt_dt(sp['found_every'])}"
            elif sp.get("model_every"):
                txt += f" · ≈ 1 {word} / {_fmt_dt(sp['model_every'])}"
        if e.whole and e.n_states and not e.structs:
            txt += f" · {e.n_states} match{'es' * (e.n_states != 1)}"
        if sp.get("every") and e.whole:
            txt += f" · 1 match / {_fmt_dt(sp['every'])}"
            fit = e.area_fit()
            if fit < 0.5:                                # a small area: few matches have a place in it
                pc = f"{fit * 100:.2g} %"
                txt += f" · {pc} in the area → 1 result / {_fmt_dt(sp['every'] / max(fit, 1e-12))}"
        if sp.get("equiv"):
            txt += f" · like {_fmt_n(sp['equiv'])} structures/s"
        return txt

    def speed(self):
        """(15/11) {"rate": candidates/s, "every": seconds per match expected (« Whole
        structure »), "equiv": structures a plain search would have to check per second
        to find them as fast} over the last ~10 s"""
        import time
        e = self.eng
        now = time.time()
        cnt = e.n_struct if e.whole else e.n_tried
        h = self._hist
        h.append((now, cnt, e.n_expect, len(e.structs)))
        while len(h) > 2 and now - h[0][0] > 10.0:
            h.pop(0)
        out = {}
        if len(h) < 2 or now - h[0][0] < 1.0:
            return self._sp
        dt = now - h[0][0]
        out["rate"] = (cnt - h[0][1]) / dt
        # (20/11) structures found: the time per structure since the start (once a few are
        # found); dungeons before the first: the model (≈ 1 per 100 (chest, chunk) tried)
        ns = len(e.structs)
        el = max(1e-6, self.elapsed)
        if ns >= 2:
            out["found_every"] = el / ns
        elif e.key == "dungeon":
            pairs = getattr(e, "n_pairs", 0)
            if pairs >= 20:
                per = el / pairs
                out["model_every"] = per / (DUNGEON_PER_CHUNK * 1.65 * 0.83)
        od = e.odds
        if od and od.get("p", 0) > 0:
            if e.whole:
                xr = (e.n_expect - h[0][2]) / dt         # matches expected per second
                if xr > 0:
                    out["every"] = 1.0 / xr
                    out["equiv"] = xr / od["p"]
            else:
                sr = (len(e.structs) - h[0][3]) / dt     # structures found per second
                if sr > 0:
                    pc = -math.expm1(od.get("chests", 1) * math.log1p(-min(od["p"], 0.999999)))
                    out["equiv"] = sr / max(pc, 1e-300)
        self._sp = out
        return out

    def _next(self, pend):
        """the next block for a free process: the structures of the chests found
        (pop), their seeds (sis), more chests (chk) — the chests while few are known"""
        e = self.eng
        pops = sum(1 for b in pend.values() if b[0] == "pop")
        chks = sum(1 for b in pend.values() if b[0] == "chk")
        cap = self.threads // 3                          # (1-2 processes: the seeds first)
        if e.active or e.retry_pop:
            if pops < cap or not e.sisters_left() or e.first_pass_done():
                b = e.pop_block(self.pop_size)
                if b is not None:
                    return b
        if e.checks_left() and len(e.active) < 4 and chks < self.threads:
            b = e.chk_block()
            if b is not None:
                return b
        b = e.sis_block()
        if b is not None:
            return b
        b = e.pop_block(self.pop_size)
        if b is not None:
            return b
        if e.checks_left() and chks < self.threads:
            return e.chk_block()
        return None

    def run(self):
        try:
            return self._run()
        except Exception as e:                           # noqa: BLE001 — the page says why
            import traceback
            traceback.print_exc()
            self.err = f"{type(e).__name__}: {e}"
            return self.state("stopped")

    def _run(self):
        import multiprocessing as mp
        import time
        from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
        if self.eng.odds is None:
            self.eng.prepare_odds()                      # (15/11) the speed shown (~ms)
        t0 = time.time()
        done0 = self.done
        end = "done"
        pend = {}
        ex = ProcessPoolExecutor(self.threads, mp_context=mp.get_context("spawn"))
        try:
            while True:
                stopping = self.stop.is_set() or self.pause.is_set()
                while not stopping and len(pend) < self.threads * 2:
                    b = self._next(pend)
                    if b is None:
                        break
                    try:
                        pend[ex.submit(_rwork, b)] = b
                    except RuntimeError:
                        self.stop.set()
                        break
                if self.pause.is_set():                  # Pause: the blocks not started go back
                    for f, b in list(pend.items()):
                        if f.cancel():
                            pend.pop(f)
                            self.eng.give_back(b)
                if not pend:
                    e = self.eng
                    if stopping or not (e.pops_left() or e.sisters_left()):
                        break
                    # (15/11) nothing to hand out yet: the chests are still being looked
                    # for (a structure whose other chests rarely match) — go on, not end
                    self.elapsed = self.elapsed0 + time.time() - t0
                    self.on_progress(self.done, None, 0.0, self.found, self.elapsed)
                    if self.stop.is_set():
                        end = "stopped"
                        break
                    if self.rule[0] == "time" and self.elapsed >= float(self.rule[1]):
                        self.pause.set()
                        end = "rule"
                    continue
                fin, _ = wait(list(pend), timeout=0.1, return_when=FIRST_COMPLETED)
                for f in fin:
                    b = pend.pop(f)
                    try:
                        kind, n, out, dt = f.result()
                    except Exception as e:               # noqa: BLE001
                        if b[0] == "chk":
                            self.eng.feed_chk(b, [])
                        self.fails += 1
                        if self.err is None:
                            self.err = f"{type(e).__name__}: {e}"
                            print(f"[reverse] a block failed: {self.err}", flush=True)
                        if self.fails >= 8 and self.oks == 0:
                            self.stop.set()              # every block fails: say why, stop
                        continue
                    self.oks += 1
                    if kind == "chk":
                        self.eng.feed_chk(b, out, n)
                    elif kind == "pop":
                        self.eng.n_pairs = getattr(self.eng, "n_pairs", 0) + (n or 0)
                        if n and dt > 0:
                            per = dt / n
                            # (20/11) dungeons: a block whose state has no previous one ends at
                            # once — never 4 096 chunks of ~0.3 s each after it
                            self.pop_size = max(1, min(8, int(self.POP_S / max(per, 0.05)))) if self.dungeon \
                                else max(16, min(4096, int(self.POP_S / max(per, 1e-6))))
                        self.eng.feed_structs(out)
                    else:
                        self.done += n
                        for h in out:
                            self.found += 1
                            self.on_hit(*h)
                self.elapsed = self.elapsed0 + time.time() - t0
                rate = (self.done - done0) / max(1e-6, time.time() - t0)
                self.on_progress(self.done, None, rate, self.found, self.elapsed)
                if self.stop.is_set():
                    end = "stopped"
                    break
                r = self.rule
                if not self.pause.is_set() and ((r[0] == "results" and self.found >= int(r[1])) or
                                                (r[0] == "time" and self.elapsed >= float(r[1])) or
                                                (r[0] == "seeds" and self.done >= int(r[1]))):
                    self.pause.set()
                    end = "rule"
            if end == "done" and self.pause.is_set():
                end = "paused"
            if end == "stopped":
                for b in pend.values():
                    self.eng.give_back(b)
        finally:
            if self.stop.is_set():
                for pr in list((getattr(ex, "_processes", None) or {}).values()):
                    try:
                        pr.terminate()
                    except Exception:                    # noqa: BLE001
                        pass
                ex.shutdown(wait=False, cancel_futures=True)
            else:
                ex.shutdown(wait=True)
        return self.state(end)
