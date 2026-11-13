"""(26/10) The transcribed 1.16-1.20 enchantments (loot_enchant._LEGACY) against
the 1.21 data: same weights, max levels and costs for every level.
    py tools/loot/check_enchant.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import loot_enchant as lx

old = lx.env("1.20.6")
new = lx.env("1.21")
bad = 0
for e in old.order:
    nid = "sweeping_edge" if e.id == "sweeping" else e.id
    n = new.by_id.get(nid)
    if n is None:
        print("missing in 1.21:", e.id); bad += 1; continue
    d = []
    if e.weight != n.weight: d.append(f"weight {e.weight}/{n.weight}")
    if e.max_level != n.max_level: d.append(f"max {e.max_level}/{n.max_level}")
    for l in range(1, e.max_level + 1):
        if (e.min_cost(l), e.max_cost(l)) != (n.min_cost(l), n.max_cost(l)):
            d.append(f"L{l} {e.min_cost(l)}-{e.max_cost(l)} / {n.min_cost(l)}-{n.max_cost(l)}")
    print(f"{e.id:22s} {'OK' if not d else 'DIFF ' + '; '.join(d)}")
    bad += bool(d)
print("DIFFERENCES:", bad)
