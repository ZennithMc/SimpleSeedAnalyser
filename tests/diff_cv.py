"""Differential test against cubiomes-viewer's OWN search code.

tools/cvoracle (built from cubiomes-viewer's search.cpp: testTreeAt /
testCondAt, verbatim, against the same cubiomes) says which seeds CV accepts;
SSA imports the same .session and tests the same seeds with its C engine.
Sub-sessions are generated so that each condition is exercised with a
reasonable pass rate: for every condition, the chain of its ancestors plus
the condition (and its whole subtree).

    python tests/diff_cv.py SESSION [n_seeds] [--mc-ssa N]
"""
import ctypes as ct, os, random, re, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import custom_search as cs
from cubiomes_session import parse_session_file
from cubiomes_wrap import CubiomesLib, MC_VERSIONS, set_large_biomes

ORACLE = os.environ.get("CVORACLE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "tools",
    "cvoracle" + (".exe" if os.name == "nt" else ""))      # py tests/oracle/build_oracle.py
raw = CubiomesLib()._lib
raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.POINTER(ct.c_int64),
                                     ct.c_int64, ct.POINTER(ct.c_int64), ct.c_int64]


def read(path):
    head, conds = [], []
    for l in open(path, encoding="utf-8", errors="replace"):
        l = l.rstrip("\r\n")
        if l.startswith("#Cond:"):
            h = l.split(None, 1)[1].strip()
            b = bytes.fromhex(h[:64])
            typ = int.from_bytes(b[0:2], "little"); meta = int.from_bytes(b[2:4], "little")
            save = int.from_bytes(b[20:24], "little", signed=True)
            rel = int.from_bytes(b[24:28], "little", signed=True)
            conds.append(dict(line=l, type=typ, save=save, rel=rel, disabled=bool(meta & 1)))
        elif l.startswith("#MC:") or l.startswith("#Large:"):
            head.append(l)
    return head, conds


def subsets(conds):
    by = {c["save"]: c for c in conds if not c["disabled"]}
    kids = {}
    for c in by.values():
        kids.setdefault(c["rel"], []).append(c["save"])

    def anc(s):
        out = []
        while s in by and s not in out:
            out.append(s); s = by[s]["rel"]
        return out

    def sub(s):
        out, st = [], [s]
        while st:
            x = st.pop(); out.append(x); st += kids.get(x, [])
        return out
    seen, res = set(), []
    for s in sorted(by):
        for name, S in ((f"path#{s}", anc(s)), (f"subtree#{s}", sorted(set(anc(s)) | set(sub(s))))):
            key = tuple(sorted(S))
            if key not in seen:
                seen.add(key); res.append((name, key))
    res.append(("FULL", tuple(sorted(by))))
    return res, by


def run_ssa(path, seeds, mc_override=None):
    r = parse_session_file(path)
    set_large_biomes(bool(r.get("large_biomes")))
    mc = mc_override or MC_VERSIONS[r["mc_str"]]
    conds = cs.active_conditions(r["conditions"])
    arr, k = cs._build_ssacond_array(conds, raw)
    n = len(seeds)
    ins = (ct.c_int64 * n)(*seeds); outs = (ct.c_int64 * n)()
    m = raw.ssa_test_seeds_batch(arr, k, mc, 1 if r.get("large_biomes") else 0, ins, n, outs, n)
    return set(outs[:min(m, n)])


def run_cv(path, seeds):
    p = subprocess.run([ORACLE, path], input="\n".join(str(s) for s in seeds) + "\n",
                       capture_output=True, text=True, timeout=3600)
    if p.returncode:
        raise RuntimeError(p.stderr)
    return {int(x) for x in p.stdout.split()}


def main():
    path = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 400
    mco = None
    if "--mc-ssa" in sys.argv:
        mco = int(sys.argv[sys.argv.index("--mc-ssa") + 1])
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    head, conds = read(path)
    subs, by = subsets(conds)
    random.seed(12345)
    pool = [random.getrandbits(64) - (1 << 63) for _ in range(n)]
    bad = 0
    accepted = {}          # path (tuple) → seeds CV accepts, for enrichment

    def cv_on(S, seeds):
        with tempfile.NamedTemporaryFile("w", suffix=".session", delete=False) as f:
            f.write("\n".join(head) + "\n" + "\n".join(by[x]["line"] for x in S) + "\n")
            tmp = f.name
        try:
            return tmp, run_cv(tmp, seeds)
        except Exception:
            os.unlink(tmp); raise

    for name, S in subs:
        if only and name != only:
            continue
        seeds = pool
        if name.startswith("path#"):
            # enrich: the seeds that already pass everything above this condition
            s_last = int(name[5:])
            parent = tuple(x for x in S if x != s_last)
            if parent:
                if parent not in accepted:
                    t, acc = cv_on(parent, pool); os.unlink(t)
                    accepted[parent] = sorted(acc)
                if accepted[parent]:
                    seeds = accepted[parent]
        tmp, b = cv_on(S, seeds)
        try:
            a = run_ssa(tmp, seeds, mco)
        finally:
            os.unlink(tmp)
        if name.startswith("path#"):
            accepted[tuple(S)] = sorted(b)
        tag = "OK  " if a == b else "DIFF"
        if a != b:
            bad += 1
        print(f"{tag} {name:14s} n={len(S):3d} seeds={len(seeds):5d}  SSA={len(a):4d} CV={len(b):4d}  onlySSA={len(a-b):3d} onlyCV={len(b-a):3d}"
              + (f"  e.g. {sorted(b-a)[:2] or sorted(a-b)[:2]}" if a != b else ""), flush=True)
    print("MISMATCHING SUBSETS:", bad)


if __name__ == "__main__":
    main()
