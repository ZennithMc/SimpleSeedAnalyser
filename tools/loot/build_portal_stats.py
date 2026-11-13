"""
tools/loot/build_portal_stats.py — (07/11) how common each ruined-portal filter is,
for the Ruined portal condition's panel (rarity, histograms).

1. Random seeds, the portals within 2 000 blocks of a random point (± 20 000), all
   analysed exactly (ruined_portal_filter.analyze): placement, type, template, the
   frame obsidian eaten by the netherrack (lost), whether the chest is still there.
2. For every (template, lost, spot) met: the EXACT distribution of the frame's (need,
   crying) over all the crying patterns of its obsidian (each obsidian turns crying
   independently, p = P(nextFloat() < 0.15f)), counted by tools/loot/portal_frame_dist.c
   (giant portals, 25 – 31 obsidian: 262 144 patterns drawn with that probability).
The panel combines them with the chest's exact odds (loot_exact): the probability of
any filter, however rare, without « not seen in N portals ».

Internally: rows [[placement, kind, template, lost, kept, chest_ok, weight]] = the
scenarios of the netherrack spread of the sampled portals (weight = its probability
summed over them; lost = frame obsidian it eats, kept = obsidian it met that stays,
not crying), and per (template, lost, kept) the frame distribution over the crying
patterns of the free obsidian. Both are combined into
loot_tables/ruined_portal_stats.json:
  {"groups": {"116" | "118": {"n": portals,
             "dist": [[placement, kind, chest_ok, need, crying, probability], …],
             "check": [[placement, crying, need, completable, portals], …]}}}
("116" = 1.16 – 1.17 measured on 1.16.5, "118" = 1.18+ on 26.3; "check" = what the
sampled portals really were, to check the model.)

    py tools/loot/build_portal_stats.py [portals per group] [sample cache .json]
(needs gcc with OpenMP; the cache keeps the sampled portals between two runs)
"""
import json
import os
import random
import subprocess
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)


P_CRY = 2516583 / float(1 << 24)


def _one(args):
    """the portals of one random seed: for each, the scenarios of its netherrack spread
    [(placement, kind, template, lost, kept, chest_ok, weight)] and what the game really
    did (placement, crying, need, completable).

    The spread is the only thing on the portal side that depends on the crying obsidian:
    a ground spot on a crying obsidian replaces it (and its drip goes on), on an obsidian
    it does not — and that changes the random's use for the next spots. So the spread is
    replayed for each fate of each obsidian it meets (a tree, weighted by P(crying));
    every other obsidian stays free (the frame distribution enumerates them)."""
    mc, seed = args
    import copy
    import loot_finder as lf
    import ruined_portal_filter as rf
    import ruined_portal_loot as rpl
    rnd = random.Random(seed)
    q = dict(key="ruined_portal", mc=mc, radius=2000, cx=rnd.randint(-20000, 20000),
             cz=rnd.randint(-20000, 20000))
    out, check = [], []
    for x, z in lf.positions(seed, q)[:6]:
        try:
            cx, cz = x >> 4, z >> 4
            g = rpl.layout(seed, mc, cx, cz)
            if g is None or rpl.ghost_116(mc, g, cx, cz):
                continue
            a = rf.analyze(seed, mc, cx, cz, layout=g)
            if not a or not a["frame"]:
                continue
            _ls, r0 = rpl.chest_rng(seed, mc, g, cx, cz)
            T = rpl._Carved(seed, mc)
            size = g["size"]
            ox, oy, oz = g["origin"]
            wps = []
            for (lx, ly, lz) in rf.frames()[g["template"]][0]:
                u, v = rpl.transform(lx, lz, g["mirror"], g["rot"], size[0] // 2, size[2] // 2)
                wps.append((ox + u, oy + ly, oz + v))
            chest = rpl.chest_pos(g)
            leaves = []

            def dfs(decide, w, depth=0):
                pend = []
                W, gone = rpl.placed(seed, mc, g, copy.deepcopy(r0), decide=decide,
                                     pending=pend, T=T)
                if pend and depth < 10:
                    q0 = pend[0]
                    dfs({**decide, q0: True}, w * P_CRY, depth + 1)
                    dfs({**decide, q0: False}, w * (1 - P_CRY), depth + 1)
                    return
                lost = sum(1 << i for i, wp in enumerate(wps) if wp in gone)
                kept = sum(1 << i for i, wp in enumerate(wps)
                           if decide.get(wp) is False and wp not in gone)
                ok = W.get(chest) == "c" and chest not in gone
                leaves.append((lost, kept, int(ok), w))
            dfs({}, 1.0)
        except Exception:                                   # noqa: BLE001
            continue
        for lost, kept, ok, w in leaves:
            out.append((a["placement"], a["kind"], a["template"], lost, kept, ok, w))
        f = a["frame"]
        check.append((a["placement"], f["crying"], f["need"], int(a["completable"])))
    return out, check


def eff_rects(tpl, lost, kept=0):
    """the template's frames with `lost` obsidian gone and `kept` obsidian sure (not
    crying): (n free obsidian, 0, [(m, mask over the free ones)]), dominated frames
    dropped (fewer missing AND a subset of the free obsidian)"""
    import ruined_portal_filter as rf
    obs, rects = rf.frames()[tpl]
    left = [i for i in range(len(obs)) if not ((lost | kept) >> i) & 1]
    remap = {b: j for j, b in enumerate(left)}
    S = 0
    rs = set()
    for r in rects:
        m, mask = r[6], r[7]
        mk = 0
        for b in left:
            if (mask >> b) & 1:
                mk |= 1 << remap[b]
        rs.add((m + bin(mask & lost).count("1"), mk))
    rs = sorted(rs)
    # r2 dominates r: never costs more (m2 ≤ m, its obsidian ⊆ r's) and never counts more
    # crying (its non-spot obsidian ⊆ r's)
    keep = [(m, k) for m, k in rs
            if not any(m2 <= m and (k2 & ~k) == 0 and (m2, k2) != (m, k) for m2, k2 in rs)]
    return len(left), S, keep


EXACT_BITS = 22                       # up to 2^22 patterns: all of them; above (giants): sampled
SAMPLES = 1 << 18


def frame_dist(exe, tpl, lost, kept=0):
    """{"n": obsidian left, "rows": [[need, c, k, patterns]]} — every pattern; for the
    giants (n > EXACT_BITS) {"n", "mc": samples, "rows": [[need, c, -1, count]]}: 262 144
    patterns drawn with the exact probability (± 0.2 % absolute; the giants are 5 % of the portals)"""
    n, S, rects = eff_rects(tpl, lost, kept)
    inp = f"{n} {S} {len(rects)}\n" + "".join(f"{m} {k}\n" for m, k in rects)
    mc = n > EXACT_BITS
    if mc:
        import zlib
        inp += f"{SAMPLES} {zlib.crc32(f'{tpl}|{lost}|{kept}'.encode()) | 1}\n"
    out = subprocess.run([exe], input=inp, capture_output=True, text=True, check=True).stdout
    d = {"n": n, "rows": [[int(v) for v in l.split()] for l in out.split("\n") if l.strip()]}
    if mc:
        d["mc"] = SAMPLES
    return d


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    cache = sys.argv[2] if len(sys.argv) > 2 else None
    exe = os.path.join(HERE, "portal_frame_dist" + (".exe" if os.name == "nt" else ""))
    subprocess.run(["gcc", "-O3", "-march=native", "-fopenmp",
                    os.path.join(HERE, "portal_frame_dist.c"), "-o", exe], check=True)
    groups = {}
    if cache and os.path.exists(cache):
        with open(cache) as f:
            groups = json.load(f)
    for group, mc in (("116", 20), ("118", 35)):
        if group in groups:
            continue
        rnd = random.Random(7 + mc)
        cnt = Counter()
        chk = Counter()
        total = 0
        with ProcessPoolExecutor() as ex:
            while total < n:
                jobs = [(mc, rnd.getrandbits(63)) for _ in range(64)]
                for rows, check in ex.map(_one, jobs):
                    for r in rows:
                        cnt[r[:6]] += r[6]
                    for c in check:
                        chk[c] += 1
                        total += 1
                print(group, total, flush=True)
        groups[group] = {"n": total, "rows": [list(k) + [v] for k, v in sorted(cnt.items())],
                         "check": [list(k) + [v] for k, v in sorted(chk.items())]}
        if cache:
            with open(cache, "w") as f:
                json.dump(groups, f)
    keys = sorted({(r[2], r[3], r[4]) for g in groups.values() for r in g["rows"]})
    fdist = {}
    for tpl, lost, kept in keys:
        fdist[f"{tpl}|{lost}|{kept}"] = frame_dist(exe, tpl, lost, kept)
        print("frame", tpl, lost, kept, flush=True)
    p = os.path.join(ROOT, "loot_tables", "ruined_portal_stats.json")
    with open(p, "w") as f:
        json.dump(compact(groups, fdist), f, separators=(",", ":"))
    print("→", p)


P_CRY_F = 2516583 / float(1 << 24)


def compact(groups, fdist):
    """what the panel needs, summed: {"groups": {g: {"n", "dist": [[placement, kind,
    chest_ok, need, crying, probability], …], "check": …}}} — the scenarios' weights
    times their frame distribution, gathered by (placement, kind, chest_ok)"""
    from collections import defaultdict
    fd = {}
    for key, d in fdist.items():
        v = defaultdict(float)
        if d.get("mc"):
            for need, c, _k, cnt in d["rows"]:
                v[(need, c)] += cnt / float(d["mc"])
        else:
            n = int(d["n"])
            for need, c, k, cnt in d["rows"]:
                v[(need, c)] += cnt * P_CRY_F ** k * (1 - P_CRY_F) ** (n - k)
        fd[key] = v
    out = {}
    for g, G in groups.items():
        n = G["n"]
        acc = defaultdict(float)
        for pl, kind, tpl, lost, kept, ok, w in G["rows"]:
            for (need, c), pr in fd[f"{tpl}|{lost}|{kept}"].items():
                acc[(pl, kind, ok, need, c)] += w * pr / n
        out[g] = {"n": n, "dist": [list(k) + [v] for k, v in sorted(acc.items()) if v > 0],
                  "check": G.get("check", [])}
    return {"groups": out}


if __name__ == "__main__":
    main()
