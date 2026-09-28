"""Targeted engine checks (high pass rates): the multithreaded engine, with
its 48-bit pre-filters, must return exactly the per-seed test's seeds.
    python tests/engine_vs_batch2.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine_vs_batch import engine, batch, signed
from diff_engine import S

CASES = {
    "structs only (pos pre-pass)": [S("village", r=1200), S("outpost", r=2000)],
    "struct exclusion only":       [S("village", r=500, inst=0)],
    "excl + struct":               [S("mansion", r=3000, inst=0), S("village", r=900)],
    "struct x2 + child":           [S("village", r=1500, inst=2), S("outpost", rel=1, r=800)],
    "slime child (BR_CLUST)":      [S("village", r=1000), S("slime", rel=1, r=64)],
    "stronghold anchor":           [S("stronghold", r=2800), S("village", rel=1, r=900)],
}


def main():
    r = random.Random(11)
    bad = 0
    for name, conds in CASES.items():
        start = signed(r.getrandbits(64)); cnt = 20000
        a, m = engine(conds, 28, start, cnt, "inc")
        b = batch(conds, 28, 0, [signed(start + k) for k in range(cnt)])
        ok = a == b; bad += not ok
        print(f"{'OK  ' if ok else 'DIFF'} inc    {name:28s} engine={len(a)} batch={len(b)}", flush=True)
        low = r.getrandbits(48)
        a, m = engine(conds, 28, low, 2, "fam48")
        if m and m[-1] == 2:
            b = batch(conds, 28, 0, [signed((h << 48) | (low + d)) for d in range(2) for h in range(65536)])
            ok = a == b; bad += not ok
            print(f"{'OK  ' if ok else 'DIFF'} blocks {name:28s} engine={len(a)} batch={len(b)}", flush=True)
    print("FAILURES:", bad)


if __name__ == "__main__":
    main()
