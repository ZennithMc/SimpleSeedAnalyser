"""
tools/loot/check_reverse.py — (15/11) the Loot page's Reverse mode (loot_reverse.py,
cubiomes_src/loot_reverse.c), Java 1.16 – 1.17, checked four ways:

  model     the reverse's model of each table draws exactly as loot_engine
  lattice   lr_new / lr_next against brute force (every state of a 2^20 window)
  cover     random chests that match a query: each follows one of its paths
  chain     real seeds: each chest's state → its structure seed again; then a
            short Reverse search whose results loot_finder.check_seed confirms
  dungeon   (20/11) real dungeon chests (C engine) → their structure seed again (the
            share covered: first dungeon of its chunk, y 10 – 76, floor over solid
            ground, not desert / swamp); a short search confirmed by check_seed

    py tools/loot/check_reverse.py [model|lattice|cover|chain ...]
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import loot_engine as le        # noqa: E402
import loot_finder as lf        # noqa: E402
import loot_native as ln        # noqa: E402
import loot_reverse as lr       # noqa: E402

M48 = lr.M48
VERS = ("1.16.1", "1.16.5", "1.17.1")
TABLES = ("desert_pyramid", "buried_treasure", "igloo_chest", "jungle_temple", "jungle_temple_dispenser",
          "simple_dungeon", "shipwreck_supply", "shipwreck_map", "shipwreck_treasure", "underwater_ruin_small",
          "underwater_ruin_big", "ruined_portal")


def _state_at(x, t):
    for _ in range(t):
        x = (x * lr.MULT + 11) & M48
    return x


def _sat(x, c):
    t, n, lo, hi = c
    s = _state_at(x, t)
    if n == 0:
        return lo <= s <= hi
    X = s >> 17
    v = (n * X) >> 31 if n & (n - 1) == 0 else X % n
    return lo <= v <= hi


def check_model():
    r = random.Random(5)
    bad = 0
    for v in VERS:
        for t in TABLES:
            m = lr.model(v, t)
            tab = le.get_table(v, t)
            for _ in range(2000):
                st = r.getrandbits(48)
                rnd = le.JavaRandom(0)
                rnd.s = st
                ctx = le.Ctx(rnd, v)
                items = []
                le.roll_table(tab, ctx, items.append, split=False)
                a = [(s.item, s.count, tuple(s.ench[0]) if s.ench else None) for s in items if s.count > 0]
                b, used = lr.simulate(m, st)
                if b is not None and (a != b or used != rnd.used):
                    bad += 1
    print(f"model    {'OK' if not bad else f'{bad} BAD'}  ({len(VERS) * len(TABLES)} tables × 2000 chests)")
    return bad


def check_lattice():
    r = random.Random(1)
    bad = 0
    import numpy as np
    for trial in range(30):
        x = r.getrandbits(48)
        T = r.randint(3, 30)
        ss = [_state_at(x, t) for t in range(1, T + 1)]
        w0 = 1 << 20
        L0 = max(0, ss[0] - r.randrange(w0))
        cons = [(1, 0, L0, min(M48, L0 + w0 - 1))]
        for _ in range(r.randint(1, 6)):
            t = r.randint(1, T)
            s = ss[t - 1]
            n = r.choice([3, 5, 6, 7, 10, 12, 24, 27, 232, 88, 1000, 26, 8, 16])
            v = (n * (s >> 17)) >> 31 if n & (n - 1) == 0 else (s >> 17) % n
            w = r.randint(1, max(1, n // 3))
            lo = max(0, v - r.randrange(w))
            hi = min(n - 1, lo + w - 1)
            if not lo <= v <= hi:
                lo = hi = v
            cons.append((t, n, lo, hi))
        s1 = np.arange(L0, min(M48, L0 + w0 - 1) + 1, dtype=np.uint64)
        xs = ((s1 - np.uint64(11)) * np.uint64(pow(lr.MULT, -1, 1 << 48))) & np.uint64(M48)
        ok = np.ones(len(xs), bool)
        for t, n, lo, hi in cons[1:]:
            cur = xs.copy()
            for _ in range(t):
                cur = (cur * np.uint64(lr.MULT) + np.uint64(11)) & np.uint64(M48)
            X = cur >> np.uint64(17)
            v = (np.uint64(n) * X) >> np.uint64(31) if n & (n - 1) == 0 else X % np.uint64(n)
            ok &= (v >= np.uint64(lo)) & (v <= np.uint64(hi))
        brute = {int(v) for v in xs[ok]}
        sv = lr.Solver(cons)
        got = set()
        while not sv.done:
            got |= set(sv.next(256, 10 ** 9))
        sv.close()
        if got != brute or x not in got:
            bad += 1
            print("   lattice mismatch", trial, len(got), len(brute))
    print(f"lattice  {'OK' if not bad else f'{bad} BAD'}  (30 problems, every state of the window)")
    return bad


def check_cover():
    cases = [
        ("1.16.5", "desert_pyramid", [dict(item="enchanted_golden_apple", min=2)], 1_500_000),
        ("1.16.5", "desert_pyramid", [dict(item="enchanted_book", min=1, ench="mending", ench_lvl=1)], 200_000),
        ("1.16.1", "buried_treasure", [dict(item="diamond", min=4), dict(item="emerald", min=8)], 600_000),
        ("1.17.1", "igloo_chest", [dict(item="emerald", min=2), dict(item="stone_axe", min=1)], 600_000),
        ("1.16.5", "jungle_temple", [dict(item="diamond", min=4)], 600_000),
        ("1.16.5", "shipwreck_treasure", [dict(item="diamond", min=2)], 300_000),            # (20/11)
        ("1.17.1", "underwater_ruin_big", [dict(item="emerald", min=2)], 300_000),
        ("1.16.1", "ruined_portal", [dict(item="enchanted_golden_apple", min=1)], 300_000),
        ("1.16.5", "simple_dungeon", [dict(item="enchanted_golden_apple", min=2)], 600_000),
    ]
    r = random.Random(3)
    bad = 0
    for v, t, items, n in cases:
        q = dict(items=items)
        allp = list(lr.paths(lr.model(v, t), q, max_nodes=3_000_000))
        hits = []
        for _ in range(n):
            x = r.getrandbits(48)
            slots, _a = le.fill_chest(t, lr.loot_seed(x), v)
            ok, _s, _h = lf.judge([{"slots": slots, "table": "chests/" + t}], dict(q, scope="chest", mode="find"))
            if ok:
                hits.append(x)
        miss = sum(1 for x in hits if not any(all(_sat(x, c) for c in p.cons) for p in allp))
        bad += miss
        print(f"cover    {'OK' if not miss else f'{miss} MISSED'}  {t} {v}: {len(hits)} matching chests, "
              f"{len(allp)} paths")
    return bad


def check_chain():
    r = random.Random(4)
    bad = 0
    for key in ("desert_pyramid", "buried_treasure", "igloo", "jungle_temple",
                "shipwreck", "ocean_ruin", "ruined_portal"):           # (20/11) + the last three
        for mc in lr.VERSIONS:
            n = 0
            while n < 2:
                s = r.getrandbits(63)
                q = dict(key=key, mc=mc, cx=0, cz=0, radius=3000)
                pos = lf.positions(s, q)
                if not pos:
                    continue
                x, z = pos[0]
                cards = __import__("structure_loot").chests(lf.LOOT_KEY.get(key, key), s, mc, x, z, None,
                                                            fast=True)
                if not any(c.get("seed") is not None for c in cards):
                    continue
                n += 1
                for c in cards:
                    if c.get("seed") is None:
                        continue
                    t = c["table"].replace("chests/", "")
                    st = (int(c["seed"]) ^ lr.MULT) & M48
                    found = {S for cfg in lr.configs(key, t, mc) for S, _ch in
                             lr.pop_scan(key, mc, st, cfg, [(x >> 4, z >> 4)])}
                    if s & M48 not in found:
                        bad += 1
                        print("   chain: missed", key, mc, s, c["name"])
    print(f"chain    {'OK' if not bad else f'{bad} BAD'}  (every chest of 42 real structures → its seed)")
    base = dict(sub=None, cx=0, cz=0, radius=2000, scope="chest", mode="find", tables=None)
    for key, mc, items, scope in (
            ("desert_pyramid", 20, [dict(item="enchanted_golden_apple", min=4)], "chest"),
            ("buried_treasure", 19, [dict(item="diamond", min=4), dict(item="emerald", min=8)], "chest"),
            ("igloo", 21, [dict(item="emerald", min=5)], "chest"),
            ("jungle_temple", 20, [dict(item="diamond", min=9)], "chest"),
            ("desert_pyramid", 21, [dict(item="enchanted_golden_apple", min=6)], "structure"),   # 2+ chests
            ("jungle_temple", 19, [dict(item="diamond", min=12)], "structure"),
            ("shipwreck", 20, [dict(item="diamond", min=3)], "chest"),                    # (20/11)
            ("ocean_ruin", 21, [dict(item="emerald", min=4)], "structure"),
            ("ruined_portal", 21, [dict(item="enchanted_golden_apple", min=2)], "chest"),
            ("shipwreck", 21, [dict(item="emerald", min=20)], "structure")):
        q = dict(base, key=key, mc=mc, items=items, scope=scope)
        hits = []
        t0 = time.time()
        S = lr.ReverseSearch(q, max(1, min(4, (os.cpu_count() or 2) - 1)), lambda *h: hits.append(h),
                             lambda *a: None, rule=("results", 20))
        st = S.run()
        nb = sum(1 for h in hits[:10] if not lf.check_seed(h[0], q))
        bad += nb
        print(f"search   {'OK' if not nb else f'{nb} BAD'}  {key} {scope} {items}: {len(hits)} seeds in "
              f"{time.time() - t0:.1f} s ({st['note']}), 10 confirmed by check_seed")
    return bad


def check_dungeon():
    import dungeon_loot as dl
    r = random.Random(8)
    bad = 0
    n = cov = 0
    t0 = time.time()
    while n < 60 and time.time() - t0 < 300:
        mc = r.choice(lr.VERSIONS)
        s = r.getrandbits(63)
        for cx in range(-3, 3):
            for cz in range(-3, 3):
                for d in dl.chunk_dungeons(s, mc, cx, cz, False) or []:
                    for c in d[6]:
                        st = (c[3] ^ lr.MULT) & M48
                        n += 1
                        cov += any(S == s & M48 for S, _ch in lr.dungeon_scan(mc, st, (2, 0, 0), [(cx, cz)]))
    share = cov / max(1, n)
    if share < 0.75:
        bad += 1
    print(f"dungeon  {'OK' if share >= 0.75 else 'LOW'}  {cov} / {n} real dungeon chests traced back "
          f"to their structure seed ({share * 100:.0f} %, ~80-90 % expected)")
    base = dict(sub=None, cx=0, cz=0, radius=3000, mode="find", tables=None)
    for mc, items, scope in ((20, [dict(item="enchanted_golden_apple", min=3)], "chest"),
                             (21, [dict(item="enchanted_golden_apple", min=3)], "structure")):
        q = dict(base, key="dungeon", mc=mc, items=items, scope=scope)
        hits = []
        t0 = time.time()
        S = lr.ReverseSearch(q, max(1, min(4, (os.cpu_count() or 2) - 1)), lambda *h: hits.append(h),
                             lambda *a: None, rule=("results", 20))
        st = S.run()
        nb = sum(1 for h in hits[:10] if not lf.check_seed(h[0], dict(q, cx=h[1], cz=h[2], radius=24)))
        bad += nb
        print(f"search   {'OK' if not nb else f'{nb} BAD'}  dungeon {scope} {items}: {len(hits)} seeds in "
              f"{time.time() - t0:.1f} s ({st['note']}), 10 confirmed by check_seed")
    return bad


def main():
    if not ln.available() or lr.lib() is None:
        print("lootvm missing or without the reverse:", ln._LIB_ERR)
        return 2
    ln.enable(True)
    print(f"lootvm   long double: {lr.lib().lr_ld_bits()} bits (64 needed)")
    what = sys.argv[1:] or ["model", "lattice", "cover", "chain", "dungeon"]
    bad = 0
    for w in what:
        bad += {"model": check_model, "lattice": check_lattice, "cover": check_cover,
                "chain": check_chain, "dungeon": check_dungeon}[w]()
    print("ALL OK" if not bad else f"{bad} PROBLEM(S)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
