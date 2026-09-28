"""The multithreaded search engine (Incremental and 48-bit family / Blocks,
with its 48-bit pre-filters, chunking, adaptive order…) must return EXACTLY
the seeds that the plain per-seed test (ssa_test_seeds_batch) accepts.

    python tests/engine_vs_batch.py [n_sessions] [seed]
Sessions: random cubiomes-viewer sessions from fuzz_cv.gen_session.
"""
import ctypes as ct, os, random, sys, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import custom_search as cs
from cubiomes_session import parse_session_file
from cubiomes_wrap import CubiomesLib, MC_VERSIONS, set_large_biomes
import fuzz_cv

raw = CubiomesLib()._lib
raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int, ct.POINTER(ct.c_int64),
                                     ct.c_int64, ct.POINTER(ct.c_int64), ct.c_int64]


def batch(conds, mc, large, seeds):
    arr, k = cs._build_ssacond_array(conds, raw)
    out = set()
    for i in range(0, len(seeds), 200000):
        part = seeds[i:i + 200000]; n = len(part)
        ins = (ct.c_int64 * n)(*part); outs = (ct.c_int64 * n)()
        m = raw.ssa_test_seeds_batch(arr, k, mc, large, ins, n, outs, n)
        out |= set(outs[:min(m, n)])
    return out


def engine(conds, mc, start, count, mode):
    found, modes = set(), []
    ev = threading.Event()
    cs.evaluate_session(conds, start, count, mc, 2, ev, on_match=lambda s, d="": found.add(int(s)),
                        on_progress=lambda d, t: None, search_mode=mode, on_mode=modes.append)
    return found, [m for m in modes if isinstance(m, int)]


def signed(x):
    x &= (1 << 64) - 1
    return x - (1 << 64) if x >> 63 else x


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rs = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    r = random.Random(rs)
    bad = 0
    for i in range(n):
        txt, ver = fuzz_cv.gen_session(r)
        with tempfile.NamedTemporaryFile("w", suffix=".session", delete=False) as f:
            f.write(txt); path = f.name
        try:
            info = parse_session_file(path)
        finally:
            os.unlink(path)
        large = 1 if info.get("large_biomes") else 0
        set_large_biomes(bool(large))
        mc = MC_VERSIONS[info["mc_str"]]
        conds = cs.active_conditions(info["conditions"])
        if not conds:
            continue
        # Incremental: a contiguous range, crossing a 2^48 boundary sometimes
        start = signed(r.getrandbits(64)) if r.random() < .7 else signed((r.getrandbits(16) << 48) - 3000)
        cnt = 6000
        seeds = [signed(start + k) for k in range(cnt)]
        a, ma = engine(conds, mc, start, cnt, "inc")
        b = batch(conds, mc, large, seeds)
        tag = "OK  " if a == b else "DIFF"
        line = f"[{i}] {ver:8s} inc   {tag} engine={len(a)} batch={len(b)} modes={ma}"
        if a != b:
            bad += 1
            line += f"  onlyEngine={sorted(a-b)[:2]} onlyBatch={sorted(b-a)[:2]}"
            keep = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuzz_fail", f"evb_{rs}_{i}.session")
            os.makedirs(os.path.dirname(keep), exist_ok=True); open(keep, "w").write(txt)
        print(line, flush=True)
        # 48-bit family (Blocks when the session has a structure): 2 lows × 65 536
        low = r.getrandbits(48)
        a, ma = engine(conds, mc, low, 2, "fam48")
        if ma and ma[-1] == 2:        # Blocks: compare with the 131 072 expanded seeds
            seeds = [signed((h << 48) | (low + d)) for d in range(2) for h in range(65536)]
            b = batch(conds, mc, large, seeds)
            tag = "OK  " if a == b else "DIFF"
            line = f"[{i}] {ver:8s} blocks {tag} engine={len(a)} batch={len(b)}"
            if a != b:
                bad += 1
                line += f"  onlyEngine={sorted(a-b)[:2]} onlyBatch={sorted(b-a)[:2]}"
                keep = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuzz_fail", f"evbB_{rs}_{i}.session")
                open(keep, "w").write(txt)
            print(line, flush=True)
    print("MISMATCHES:", bad)


if __name__ == "__main__":
    main()
