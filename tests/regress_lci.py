"""Regression: a noise sample must not corrupt the biome generator used by a
later biome condition of the same seed (large cherry islands session: biome
sample under a spiral, after many noise samples). The three seeds below were
found by cubiomes-viewer and rejected by SSA before the fix.
Run:  python tests/regress_lci.py"""
import os, sys, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import custom_search as cs
from cubiomes_session import parse_session_file

HERE = os.path.dirname(os.path.abspath(__file__))
SEEDS = [888881461186910455, 888881461191700852, 888881461197997481]
conds = parse_session_file(os.path.join(HERE, "largecherryislands.session"))["conditions"]
found = []
for s in SEEDS:
    ev = threading.Event()
    cs.evaluate_session(conds, s - 5, 11, 28, 2, ev, on_match=lambda x, d="": found.append(x),
                        on_progress=lambda d, t: None, search_mode="inc")
print("found:", found)
ok = sorted(found) == sorted(SEEDS)
print("OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
