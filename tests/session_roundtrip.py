"""Round-trip test of .session save / load.
  1. SSA → .session → SSA must give back exactly the same conditions.
  2. The hex part alone (what cubiomes-viewer reads) must search the same seeds."""
import copy, os, random, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cubiomes_session import parse_session_file
from cubiomes_session_writer import write_session_file
from cubiomes_wrap import CubiomesLib
import diff_engine as d
S = d.S

CONDS = [
    S("village", r=1500, variant="abandoned", use_gpu=True),                 # 1
    S("crunchiness", box=(-100, -100, 100, 100), climate=2, octaves=["0A"], threshold=0.1),  # 2 (SSA-only)
    S("outpost", rel=1, r=400),                                              # 3 → parent after an SSA line
    S("mansion", r=4000, inst=0),                                            # 4 exclusion
    S("climate_minmax", box=(-800, -800, 800, 800), para="Erosion", locate=1,
      range=(None, None), nmax=-1),                                          # 5
    S("logic_or"),                                                           # 6
    S("monument", rel=6, r=900), S("hut", rel=6, r=1200),                   # 7 8
    S("igloo", r=3000, variant="basement"),                                  # 9
    S("first_stronghold", r=1500, min_eyes=3),                               # 10
]
CONDS[2]["ignore_ref"] = True
CONDS[4]["params"]["para"] = "Erosion"


def strip(cs):
    return [{k: v for k, v in c.items() if k != "save"} for c in cs]


def main():
    bad = 0
    fd, path = tempfile.mkstemp(suffix=".session"); os.close(fd)
    write_session_file(path, CONDS, mc_str="Java 1.19", seed_min=123, progress=456)
    r = parse_session_file(path)
    import json
    a = json.loads(json.dumps(strip(CONDS))); b = json.loads(json.dumps(strip(r["conditions"])))
    ok = a == b and r["mc_str"] == "Java 1.19" and r["seed_min"] == 123 and r["progress"] == 456
    bad += not ok
    print("full round-trip       ", "OK" if ok else "DIFF", "" if ok else [(x, y) for x, y in zip(a, b) if x != y][:2], r["unsupported"])
    # hex only (cubiomes-viewer view): drop #SSAx/#SSA lines
    txt = open(path).read().splitlines()
    hex_only = [ln for ln in txt if not ln.startswith("#SSA")]
    open(path, "w").write("\n".join(hex_only) + "\n")
    r2 = parse_session_file(path)
    # the viewer-representable subset: everything except the SSA-only crunchiness (2)
    # and the SSA stronghold eye filter; relative links re-pointed
    keep = [c for c in r2["conditions"]]
    ref = copy.deepcopy([c for i, c in enumerate(CONDS) if i not in (1, 9)])
    # renumber ref links (condition 2 removed): positions >2 shift by one
    for c in ref:
        if c["relative"] > 2: c["relative"] -= 1
    lib = CubiomesLib()
    rng = random.Random(9); seeds = [rng.getrandbits(63) for _ in range(150)]
    d.MC = 24  # 1.19
    for name, sub in (("village>outpost", [0, 1]), ("exclusion", [2]), ("climate min", [3]),
                      ("or", [4, 5, 6]), ("igloo basement", [7])):
        def pick(lst):
            idx = [i for i in sub]
            out = copy.deepcopy([lst[i] for i in idx])
            for c in out:
                c["relative"] = (idx.index(c["relative"] - 1) + 1) if c["relative"] else 0
            return out
        e1 = d.c_eval(pick(ref), seeds, lib); e2 = d.c_eval(pick(keep), seeds, lib)
        ok = e1 == e2
        bad += not ok
        print(f"hex-only {name:16s}", "OK" if ok else "DIFF", len(e1), len(e2))
    print("FAILURES:", bad)
    os.remove(path)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
