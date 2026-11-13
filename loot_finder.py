"""
loot_finder.py — (06/11) find seeds whose structure chests hold what you want.

A query (a plain dict, sent to the worker processes):
    key        structure (structure_loot key: desert_pyramid, buried_treasure, …)
    mc, sub    version (sub: the exact loot version when an SSA version covers several)
    radius     blocks around (cx, cz) where the structure must be
    items      [{item, min, max|None, ench|None, ench_lvl, potion|None}]   wanted items
    scope      "chest" (all wanted items in one chest) | "structure" (summed over its chests)
    mode       "find" | "most" (score = count of items[0]) | "layout"
    layout     27 cells: None (any) | "empty" | "filled" | k (wanted item k)
               | [k, n] (wanted item k, exactly n of it)
    tables     optional set of loot tables to consider (e.g. only the shipwreck's treasure)

Each seed: the structure's tries near the centre (cubiomes positions + biome
viability), then structure_loot.chests — the exact chests SSA shows on the map —
judged against the query. Exact but CPU-heavy; a native / GPU prefilter on the
loot seeds is the next step (the loot seed is 48-bit up to 1.17, 64-bit after).
"""
from __future__ import annotations

import ctypes
import os
import threading
import time

# structures the finder knows (structure_loot keys) → cubiomes structure key
FINDER = {
    "desert_pyramid": "desert_pyramid", "jungle_temple": "jungle_temple", "igloo": "igloo",
    "buried_treasure": "buried_treasure", "shipwreck": "shipwreck", "ruined_portal": "ruined_portal",
    "ocean_ruin": "ocean_ruin", "village": "village", "pillager_outpost": "pillager_outpost",
    "mansion": "mansion", "ancient_city": "ancient_city", "abandoned_camp": "abandoned_camp",
    "bastion": "bastion", "fortress": "fortress", "end_city": "end_city",
}
# loot key used by structure_loot for each
LOOT_KEY = {"buried_treasure": "treasure"}
DIM = {"bastion": -1, "fortress": -1, "end_city": 1}


def _match_stack(st, w) -> bool:
    if st is None or st.item != w["item"]:
        return False
    if w.get("ench"):
        lv = [l for e, l in (getattr(st, "ench", None) or []) if e == w["ench"]]
        if not lv or max(lv) < int(w.get("ench_lvl") or 1):
            return False
    if w.get("potion") and getattr(st, "potion", None) != w["potion"]:
        return False
    return True


def _count(slots, w) -> int:
    return sum(st.count for st in slots if _match_stack(st, w))


def _counts(slots, items):
    """[how many of each wanted item] — in C for the VM's chests (loot_native)"""
    if hasattr(slots, "counts"):
        return slots.counts(items)
    return [_count(slots, w) for w in items]


_TITEMS = {}


def _table_items(version, table):
    """the items a table can give (loot_stats) — for the chests whose seed is unknown"""
    k = (version, table)
    v = _TITEMS.get(k)
    if v is None:
        import loot_stats as ls
        st = ls.table_stats(version, table.replace("chests/", "").replace("barrels/", ""))
        v = _TITEMS[k] = None if st is None else {s.item for s in st.items}
    return v


def _could(card, q):
    """(fast mode) a chest whose seed is unknown: could it make the query pass? (its
    table can give the wanted items) — then the structure is checked in full"""
    import loot_engine as le
    items = q.get("items") or []
    have = _table_items(le.loot_version(int(q.get("mc", 35)), q.get("sub")), str(card.get("table", "")))
    if have is None:
        return True
    mode = q.get("mode", "find")
    if mode == "layout":
        lay = q.get("layout") or []
        if any(c in ("empty", "filled") for c in lay):
            return True
        need = {int(c[0] if isinstance(c, (list, tuple)) else c) for c in lay if c is not None}
        return all(0 <= k < len(items) and items[k]["item"] in have for k in need)
    if mode == "most":
        return bool(items) and items[0]["item"] in have
    if q.get("scope") == "structure":
        return any(w["item"] in have for w in items)
    return all(w["item"] in have for w in items)


def judge(cards, q):
    """(ok, score, [indexes of the matching cards])"""
    import loot_engine as le
    items = q.get("items") or []
    tabs = q.get("tables")
    unknown = [i for i, c in enumerate(cards or []) if c.get("slots") is le.UNKNOWN
               and (not tabs or str(c.get("table", "")).replace("chests/", "") in tabs)]
    if any(_could(cards[i], q) for i in unknown):
        return True, 0, unknown                         # maybe: the caller checks it in full
    cards = [dict(c, slots=None) if c.get("slots") is le.UNKNOWN else c for c in (cards or [])]
    cs = [(i, c) for i, c in enumerate(cards or []) if c.get("slots") is not None
          and (not tabs or str(c.get("table", "")).replace("chests/", "") in tabs)]
    if not cs:
        return False, 0, []

    def in_range(n, w):
        return n >= int(w.get("min") or 0) and (w.get("max") is None or n <= int(w["max"]))
    mode = q.get("mode", "find")
    if mode == "layout":
        lay = q.get("layout") or [None] * 27
        hits = []
        for i, c in cs:
            s = c["slots"]
            if hasattr(s, "layout_ok"):                 # the VM's chest: judged in C
                if s.layout_ok(lay, items):
                    hits.append(i)
                continue
            ok = True
            for k, want in enumerate(lay[:len(s)]):
                st = s[k]
                if want is None:
                    continue
                if want == "empty":
                    ok = st is None
                elif want == "filled":
                    ok = st is not None
                else:                           # k, or [k, exact count] (07/11)
                    k, n = (want[0], want[1]) if isinstance(want, (list, tuple)) else (want, None)
                    ok = (0 <= int(k) < len(items) and _match_stack(st, items[int(k)])
                          and (not n or st.count == int(n)))
                if not ok:
                    break
            if ok:                                      # the cells only (07/11)
                hits.append(i)
        return bool(hits), len(hits), hits
    if q.get("scope") == "structure":
        per = [_counts(c["slots"], items) for _i, c in cs]
        tot = [sum(p[j] for p in per) for j in range(len(items))]
        ok = all(in_range(n, w) for n, w in zip(tot, items))
        score = tot[0] if items else 0
        return ok, score, [i for (i, c), p in zip(cs, per) if any(p)]
    best, hits = 0, []
    for i, c in cs:
        n = _counts(c["slots"], items)
        if all(in_range(a, w) for a, w in zip(n, items)):
            hits.append(i)
            best = max(best, n[0] if n else 0)
    return bool(hits), best, hits


# ── structures near the centre ────────────────────────────────────────────────
_G = {}


def _gen(mc, dim):
    import cubiomes_wrap as cw
    k = (os.getpid(), threading.get_ident(), dim)
    g = _G.get(k)
    if g is None:
        g = _G[k] = cw.CubiomesLib()
    return g


_XZN = 4096
_XZ = (ctypes.c_int * (2 * _XZN))()
_CPOS = {}


def _c_positions(L):
    """(ssa_struct_candidates, ssa_struct_viable) of libcubiomes, None for an older build"""
    k = id(L)
    if k not in _CPOS:
        try:
            a, b = L.ssa_struct_candidates, L.ssa_struct_viable
            a.restype = b.restype = ctypes.c_int
            a.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.c_int]
            b.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            _CPOS[k] = (a, b)
        except AttributeError:
            _CPOS[k] = None
    return _CPOS[k]


def positions(seed, q):
    """the tries of the structure within radius of the centre that can spawn"""
    import cubiomes_wrap as cw
    key = q["key"]
    st = cw.ALL_STRUCTURE_TYPES[FINDER[key]]
    mc = int(q["mc"])
    dim = DIM.get(key, 0)
    g = _gen(mc, dim)
    L = g._lib
    reg = g.region_blocks(st, mc) if hasattr(g, "region_blocks") else 512
    cx, cz, R = int(q.get("cx", 0)), int(q.get("cz", 0)), int(q.get("radius", 1000))
    out = []
    pos = cw.Pos()
    ready = False
    if key == "buried_treasure" and mc >= 13:           # (07/11) 1-chunk regions: all at once
        return _treasure_positions(seed, q, g, L, st, mc, dim, cx, cz, R)
    fast = _c_positions(L)
    if fast:                                            # (07/11) the region loop and the viability in C
        n = fast[0](st, mc, ctypes.c_uint64(seed & 0xFFFFFFFFFFFFFFFF), reg, cx, cz, R, _XZ, _XZN)
        if n > _XZN:
            fast = None
        else:
            for i in range(n):
                x, z = _XZ[2 * i], _XZ[2 * i + 1]
                if not ready:
                    g.setup_and_seed(mc, seed, large_biomes=False, dim=dim)
                    ready = True
                if fast[1](st, g._gen, x, z):
                    out.append((x, z))
            out.sort(key=lambda p: (p[0] - cx) ** 2 + (p[1] - cz) ** 2)
            return out
    for rx in range((cx - R) // reg - 1, (cx + R) // reg + 1):
        for rz in range((cz - R) // reg - 1, (cz + R) // reg + 1):
            if not L.getStructurePos(st, mc, ctypes.c_uint64(seed & 0xFFFFFFFFFFFFFFFF), rx, rz,
                                     ctypes.byref(pos)):
                continue
            if (pos.x - cx) ** 2 + (pos.z - cz) ** 2 > R * R:
                continue
            if not ready:
                g.setup_and_seed(mc, seed, large_biomes=False, dim=dim)
                ready = True
            if not L.isViableStructurePos(st, g._gen, pos.x, pos.z, 0):
                continue
            out.append((pos.x, pos.z))
    out.sort(key=lambda p: (p[0] - cx) ** 2 + (p[1] - cz) ** 2)
    return out


# (07/11) contents first: the terrain (heights, whether the chest survives) only
# for the structures whose chests match
FAST = {"buried_treasure", "shipwreck", "ruined_portal", "desert_pyramid", "jungle_temple", "igloo",
        "ocean_ruin", "village", "pillager_outpost", "abandoned_camp"}


_TGRID = {}
_U = None


def _treasure_positions(seed, q, g, L, st, mc, dim, cx, cz, R):
    """cubiomes' getStructurePos(Treasure) for every chunk of the disc in numpy:
    setSeed(rx·341873128712 + rz·132897987541 + seed + 10387320), nextFloat() < 0.01
    (next(24) ≤ 167772); then the biome check of the few that pass"""
    import numpy as np
    global _U
    if _U is None:
        _U = np.uint64
    k = (cx, cz, R)
    grid = _TGRID.get(k)
    if grid is None:
        rx = np.arange((cx - R) // 16 - 1, (cx + R) // 16 + 2, dtype=np.int64)
        rz = np.arange((cz - R) // 16 - 1, (cz + R) // 16 + 2, dtype=np.int64)
        RX, RZ = np.meshgrid(rx, rz, indexing="ij")
        RX, RZ = RX.ravel(), RZ.ravel()
        px, pz = RX * 16 + 9, RZ * 16 + 9
        keep = (px - cx) ** 2 + (pz - cz) ** 2 <= R * R
        RX, RZ, px, pz = RX[keep], RZ[keep], px[keep], pz[keep]
        base = (RX.astype(_U) * _U(341873128712) + RZ.astype(_U) * _U(132897987541))
        grid = _TGRID[k] = (base, px, pz)
        if len(_TGRID) > 8:
            _TGRID.clear()
    base, px, pz = grid
    M48 = _U((1 << 48) - 1)
    with np.errstate(over="ignore"):
        v = base + _U((seed + 10387320) & 0xFFFFFFFFFFFFFFFF)
        sd = (v ^ _U(0x5DEECE66D)) & M48
        sd = (sd * _U(0x5DEECE66D) + _U(0xB)) & M48
    hit = np.nonzero((sd >> _U(24)) <= _U(167772))[0]
    out = []
    if len(hit):
        g.setup_and_seed(mc, seed, large_biomes=False, dim=dim)
        for i in hit:
            x, z = int(px[i]), int(pz[i])
            if L.isViableStructurePos(st, g._gen, x, z, 0):
                out.append((x, z))
    out.sort(key=lambda p: (p[0] - cx) ** 2 + (p[1] - cz) ** 2)
    return out


def check_seed(seed, q):
    """[(x, z, score, matching card indexes)] for the structures of `seed` that match"""
    import structure_loot as sl
    res = []
    lk = LOOT_KEY.get(q["key"], q["key"])
    fast = q["key"] in FAST
    for x, z in positions(seed, q):
        try:
            cards = sl.chests(lk, seed, int(q["mc"]), x, z, q.get("sub"), fast=fast)
            ok, score, hits = judge(cards, q)
            if ok and fast:                             # the exact chests, terrain included
                cards = sl.chests(lk, seed, int(q["mc"]), x, z, q.get("sub"))
                ok, score, hits = judge(cards, q)
        except Exception:                               # noqa: BLE001
            continue
        if ok:
            res.append((x, z, score, hits))
            if q.get("mode") != "most":
                break
    return res


_VM = None


def _work(args):
    """(blk, q): blk = ("r", first, n) a range, or a list of seeds"""
    global _VM
    blk, q = args
    if _VM is None:                                     # this worker: chests in C (loot_native)
        try:
            import loot_native
            _VM = loot_native.enable(not q.get("python"))
        except Exception:                               # noqa: BLE001
            _VM = False
    seeds = range(blk[1], blk[1] + blk[2]) if isinstance(blk, tuple) else blk
    t = time.perf_counter()
    out = []
    for s in seeds:
        for x, z, score, hits in check_seed(s, q):
            out.append((s, x, z, score, hits))
    return len(seeds), out, time.perf_counter() - t


BLOCK_S = 0.25                                          # seconds of work per block


class Search:
    """(07/11) the Loot page's search, with the Sessions' controls.

    source  ("range", first) | ("random", None) | ("list", seeds — len()-able, iterable)
    rule    ("never", None) | ("seeds", n) the search ends there |
            ("results", n) / ("time", seconds) the search PAUSES (Resume goes on)
    state   from a previous run (Pause → Resume): cursor, done, found, elapsed, list_pos
    on_hit(seed, x, z, score, hits); on_progress(done, total|None, rate, found, elapsed)
    run() → the state to resume from, with "end": done | paused | stopped | rule"""

    def __init__(self, q, threads, on_hit, on_progress, source=("range", 0), rule=("never", None),
                 state=None, block=4):
        self.q = q
        self.threads = max(1, int(threads))
        self.on_hit, self.on_progress = on_hit, on_progress
        self.source, self.rule = source, rule
        st = dict(state or {})
        self.cursor = int(st.get("cursor", source[1] if source[0] == "range" else 0) or 0)
        self.done = int(st.get("done", 0))
        self.found = int(st.get("found", 0))
        self.elapsed0 = float(st.get("elapsed", 0.0))
        self.list_pos = int(st.get("list_pos", 0))
        self.block = block
        self.stop = threading.Event()
        self.pause = threading.Event()

    def total(self):
        if self.rule[0] == "seeds":
            return int(self.rule[1])
        if self.source[0] == "list":
            return len(self.source[1])
        return None

    def state(self, end, resume=None):
        return dict(cursor=self.cursor if resume is None else resume, done=self.done,
                    found=self.found, elapsed=self.elapsed, list_pos=self.list_pos, end=end)

    def _blocks(self):
        """the next blocks of seeds, forever (the caller stops)"""
        import itertools
        import random as _r
        kind = self.source[0]
        if kind == "range":
            while True:
                yield ("r", self.cursor, self.block)
        elif kind == "random":
            rng = _r.Random(int.from_bytes(os.urandom(16), "little"))
            while True:
                yield [rng.getrandbits(64) - (1 << 63) for _ in range(self.block)]
        else:
            it = iter(self.source[1])
            if self.list_pos:
                it = itertools.islice(it, self.list_pos, None)
            while True:
                b = list(itertools.islice(it, self.block))
                if not b:
                    return
                yield b

    def run(self):
        from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
        import multiprocessing as mp
        t0 = time.time()
        self.elapsed = self.elapsed0
        total = self.total()
        done0 = self.done
        end = "done"
        pend = {}
        blocks = self._blocks()
        self._unqueued = False
        try:                                            # compiled here once, not by every worker
            import loot_native
            loot_native.available()
        except Exception:                               # noqa: BLE001
            pass
        ex = ProcessPoolExecutor(self.threads, mp_context=mp.get_context("spawn"))
        try:
            exhausted = False
            while True:
                stopping = self.stop.is_set() or self.pause.is_set()
                while not stopping and not exhausted and len(pend) < self.threads * 3:
                    left = None if total is None else total - self.done - sum(
                        (b[2] if isinstance(b, tuple) else len(b)) for b in pend.values())
                    if left is not None and left <= 0:
                        exhausted = True
                        break
                    try:
                        b = next(blocks)
                    except StopIteration:
                        exhausted = True
                        break
                    if left is not None:
                        b = ("r", b[1], min(b[2], left)) if isinstance(b, tuple) else b[:left]
                    if isinstance(b, tuple):
                        self.cursor = b[1] + b[2]
                    elif self.source[0] == "list":
                        self.list_pos += len(b)
                    try:
                        f = ex.submit(_work, (b, self.q))
                    except RuntimeError:                # the pool broke
                        self.stop.set()
                        break
                    pend[f] = b
                if self.pause.is_set() and not self._unqueued:
                    self._unqueued = True               # Pause: the blocks not started go back
                    back = [(f, b) for f, b in list(pend.items()) if f.cancel()]
                    for f, b in back:
                        pend.pop(f)
                    starts = [b[1] for _f, b in back if isinstance(b, tuple)]
                    if starts:
                        self.cursor = min(starts)
                    if self.source[0] == "list":
                        self.list_pos -= sum(len(b) for _f, b in back if not isinstance(b, tuple))
                if not pend:
                    break
                fin, _ = wait(list(pend), timeout=0.1, return_when=FIRST_COMPLETED)
                for f in fin:
                    b = pend.pop(f)
                    try:
                        n, out, dt = f.result()
                        if n:                           # blocks of about BLOCK_S seconds:
                            per = dt / n                # Stop / Pause answer that fast
                            self.block = max(1, min(4096, int(BLOCK_S / max(per, 1e-6))))
                    except Exception:                   # noqa: BLE001
                        n, out = (b[2] if isinstance(b, tuple) else len(b)), []
                    self.done += n
                    for h in out:
                        self.found += 1
                        self.on_hit(*h)
                self.elapsed = self.elapsed0 + time.time() - t0
                rate = (self.done - done0) / max(1e-6, time.time() - t0)
                self.on_progress(self.done, total, rate, self.found, self.elapsed)
                if self.stop.is_set():
                    end = "stopped"
                    break
                r = self.rule
                if not self.pause.is_set() and ((r[0] == "results" and self.found >= int(r[1])) or
                                                (r[0] == "time" and self.elapsed >= float(r[1]))):
                    self.pause.set()
                    end = "rule"
            resume = None
            if pend:                                    # stopped: those blocks are not done
                starts = [b[1] for b in pend.values() if isinstance(b, tuple)]
                resume = min(starts) if starts else None
                for f in pend:
                    f.cancel()
            if end == "done" and self.pause.is_set():
                end = "paused"
        finally:
            if self.stop.is_set():                      # Stop: no waiting for the blocks running
                for pr in list((getattr(ex, "_processes", None) or {}).values()):
                    try:
                        pr.terminate()
                    except Exception:                   # noqa: BLE001
                        pass
                ex.shutdown(wait=False, cancel_futures=True)
            else:
                ex.shutdown(wait=True)
        return self.state(end, resume)


# ── how rare is it? ────────────────────────────────────────────────────────────
def chest_odds(q, tables, version, n=4000, seed=1):
    """P(one chest of these tables matches the query) — simulated fills (the
    tables' loot, not the positions)"""
    import random
    import loot_engine as le
    if not tables:
        return None
    r = random.Random(seed)
    hit = tot = 0
    qq = dict(q, scope="chest")
    if qq.get("mode") == "most":
        qq["mode"] = "find"
    per = max(1, n // len(tables))
    for tb in tables:
        for _ in range(per):
            slots, _ap = le.fill_chest(tb, r.getrandbits(63) or 1, version)
            if slots is None:
                continue
            tot += 1
            ok, _s, _h = judge([{"slots": slots, "table": tb}], qq)
            hit += ok
    return hit / tot if tot else None


_ODDS_CACHE = {}


def odds_warm():
    """(12/11) the odds process loads its modules and the C library at once"""
    import loot_exact as lx                             # noqa: F401
    import loot_native
    loot_native.lib()
    lx._polymul()
    return True


def odds_job(q, tables, version):
    """(07/11) for the Loot page's card, in a helper process: the exact odds per
    table (loot_exact), and a simulation for what it cannot compute. (12/11) The
    answers are kept: coming back to a query is instant."""
    import json
    key = json.dumps([q, list(tables), version], sort_keys=True, default=str)
    if key in _ODDS_CACHE:
        return _ODDS_CACHE[key]
    out = _odds_job(q, tables, version)
    if len(_ODDS_CACHE) > 256:
        _ODDS_CACHE.clear()
    _ODDS_CACHE[key] = out
    return out


def _odds_job(q, tables, version):
    import loot_exact as lx
    try:
        res = lx.query_odds(q, tables, version)
    except Exception as e:                              # noqa: BLE001
        return repr(e), None
    res = [(t, (None if r is None else (float(r.p), r.max_count)), b, x) for t, r, b, x in res]
    approx = None
    if any(r is None for _t, r, _b, _x in res):
        approx = chest_odds(q, [t for t, r, _b, _x in res if r is None], version, n=3000)
    return res, approx
