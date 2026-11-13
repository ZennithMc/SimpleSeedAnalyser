"""
tools/village_stats.py — (05/11) the statistics of the Village condition (its
histograms and rarities), measured on REAL villages: random seeds, the villages the
game really builds there (position, biome, terrain), their exact layout (village type,
houses, forges with a chest) as SSA's finder sees them.

    py tools/village_stats.py                        → 50 000 villages per type, Java 26.2
    py tools/village_stats.py --per-type 200000 --mc 34 --threads 24

Writes loot_tables/village_stats.json (the map / condition panel read it at start):
per type, the counts (houses, forges) of the villages that are not abandoned, the
abandoned ones apart, and how often each type comes up. Progress is printed every few
seconds; Ctrl+C writes what was measured so far.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

TYPES = ("plains", "desert", "savanna", "snowy", "taiga")
BOX = 3000                     # villages within ±3000 blocks of 0, 0 of each seed


def _worker(args):
    """Measure the villages of `n_seeds` random seeds → {key: count}."""
    mc, s0, n_seeds = args
    import cubiomes_wrap as cw
    import jigsaw
    import struct_info
    import village_filter as vf
    rnd = random.Random(s0)
    out = {}
    for _ in range(n_seeds):
        seed = rnd.getrandbits(64)
        seed -= (1 << 64) if seed >= (1 << 63) else 0
        try:
            lib = struct_info._lib(seed, mc, None, 0)
            pts = lib.find_structures_in_box(cw.Village, -BOX, -BOX, BOX, BOX, verify_biome=True,
                                             max_count=4000)
        except Exception:
            continue
        L = lib._lib
        g = jigsaw._gen(mc)
        for x, z in pts:
            try:
                if mc >= 22 and not L.isViableStructureTerrain(cw.Village, lib._gen, x, z):
                    continue
                lay = jigsaw.layout(seed, mc, "village", x >> 4, z >> 4)
            except Exception:
                continue
            if lay is None:
                continue
            t = lay[0].split("_", 1)[1]
            locs = [p.el.loc for p in lay[1] if p.el.kind in ("single", "legacy")]
            ab = any("/zombie/" in (l or "") for l in locs)
            h = sum(1 for l in locs if vf.is_house(l))
            f = sum(1 for l in locs if vf.forge_chest(g, l))
            k = f"{'z' if ab else 'n'}|{t}|{h},{f}"
            out[k] = out.get(k, 0) + 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--mc", type=int, default=34, help="cubiomes version id (34 = Java 26.2)")
    ap.add_argument("--per-type", type=int, default=50000,
                    help="villages (not abandoned) wanted for EACH type")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--seed", type=int, default=None, help="random start (default: the clock)")
    ap.add_argument("--max-minutes", type=float, default=600.0)
    ap.add_argument("--out", default=os.path.join(HERE, "loot_tables", "village_stats.json"))
    a = ap.parse_args()
    base = a.seed if a.seed is not None else int(time.time())
    counts = {}
    t0 = time.time()
    last = 0.0

    def per_type():
        n = {t: 0 for t in TYPES}
        for k, c in counts.items():
            ab, t, _hf = k.split("|")
            if ab == "n" and t in n:
                n[t] += c
        return n

    def write():
        joint, zjoint = {t: {} for t in TYPES}, {t: {} for t in TYPES}
        for k, c in counts.items():
            ab, t, hf = k.split("|")
            d = (zjoint if ab == "z" else joint).setdefault(t, {})
            d[hf] = d.get(hf, 0) + c
        n = per_type()
        tot = sum(n.values()) or 1
        data = {"mc": a.mc, "real": True, "date": time.strftime("%Y-%m-%d"),
                "note": "real villages (position, biome, terrain) of random seeds within "
                        f"±{BOX} blocks; houses = houses pools minus farms / pens / accessories / "
                        "meeting points; forges = smith houses with a loot chest",
                "n": n, "share": {t: n[t] / tot for t in TYPES},
                "joint": joint, "abandoned": zjoint}
        tmp = a.out + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, a.out)

    job = 0
    try:
        with mp.Pool(a.threads) as pool:
            def jobs():
                nonlocal job
                while True:
                    job += 1
                    yield (a.mc, base * 1000003 + job, 4)
            for res in pool.imap_unordered(_worker, jobs(), chunksize=1):
                for k, c in res.items():
                    counts[k] = counts.get(k, 0) + c
                n = per_type()
                el = time.time() - t0
                if el - last > 5:
                    last = el
                    tot = sum(n.values())
                    print(f"[{el / 60:5.1f} min] {tot:>8} villages ({tot / max(el, 1e-9):.0f}/s)  " +
                          "  ".join(f"{t} {n[t]}" for t in TYPES), flush=True)
                    write()
                if min(n.values()) >= a.per_type or el > a.max_minutes * 60:
                    pool.terminate()
                    break
    except KeyboardInterrupt:
        print("stopped: writing what was measured", flush=True)
    write()
    n = per_type()
    print("done:", ", ".join(f"{t} {n[t]}" for t in TYPES), "→", a.out, flush=True)


if __name__ == "__main__":
    mp.freeze_support()
    main()
