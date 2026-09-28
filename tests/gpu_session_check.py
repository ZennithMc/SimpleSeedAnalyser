"""GPU session check (run on the RTX machine):
    py tests/gpu_session_check.py [count]
Runs the same structure sessions twice over the same seed range — GPU structure
pipeline (use_gpu) and pure CPU C engine — and checks that both find exactly the
same seeds. Also prints the time of each run."""
import copy, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import custom_search as cs
from diff_engine import S, MC

# Selective sessions (few survivors) — the case the GPU pre-filter is made for.
# (Very permissive sessions pass 30 %+ of the seeds: then the CPU validation
#  dominates and GPU ≈ CPU, which the first version of this test measured.)
SESSIONS = {
    "village>outpost": [S("village", r=300, use_gpu=True), S("outpost", rel=1, r=250, use_gpu=True)],
    "mansion":         [S("mansion", r=300, use_gpu=True)],
    "monument>village":[S("monument", r=200, use_gpu=True), S("village", rel=1, r=250)],
}


def run(conds, start, count, gpu):
    c = copy.deepcopy(conds)
    for x in c:
        if "use_gpu" in x["params"]:
            x["params"]["use_gpu"] = gpu
    found, modes = set(), []
    t = time.perf_counter()
    cs.evaluate_session(c, start, count, MC, max(1, (os.cpu_count() or 4) // 2),
                        threading.Event(), on_match=lambda s, d="": found.add(int(s)),
                        on_progress=lambda d, t: None, search_mode="inc",
                        on_mode=lambda m: modes.append(m))
    return found, time.perf_counter() - t, modes


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 1 << 22
    start = 7_000_000_000_123
    bad = 0
    for name, conds in SESSIONS.items():
        g, tg, mg = run(conds, start, count, True)
        c, tc, _ = run(conds, start, count, False)
        ok = g == c
        bad += not ok
        gpu_ran = any(m == 3 for m in mg)
        print(f"{name:18s} {'OK ' if ok else 'DIFF'}  GPU={len(g):6d} ({tg:6.1f}s{'' if gpu_ran else ', GPU NOT USED'})"
              f"  CPU={len(c):6d} ({tc:6.1f}s)  onlyGPU={len(g - c)} onlyCPU={len(c - g)}", flush=True)
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
