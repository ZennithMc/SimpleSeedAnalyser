"""The Sessions tab's SearchRunner over long sessions: pause / resume many
times with a seed limit must search EXACTLY the requested seeds, find
exactly the results of an uninterrupted run and count the searched seeds
right, in every mode actually run by the engine:
  * Incremental, including a scan that crosses INT64_MAX -> INT64_MIN;
  * "48-bit family" on a session without structures (run as Incremental);
  * "48-bit family" -> Blocks with a start seed above 2^48 (random seeds).

    python tests/runner_pause.py
"""
import os, random, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
from qtui.search_runner import SearchRunner          # noqa: E402
from engine_vs_batch import batch, signed            # noqa: E402
from diff_engine import S                            # noqa: E402

CLIMATE_ONLY = [S("climate_minmax", r=64, para="Temperature", range=["2000", None], nmax=2)]
STRUCT = [S("village", r=500)]


def wait(r, until, limit=300):
    t = time.time()
    while not until() and time.time() - t < limit:
        app.processEvents(); time.sleep(0.01)


def run(conds, start, mode, seeds, pauses=0, rnd=None):
    r = SearchRunner()
    got, last, errs = [], {}, []
    r.resultsReady.connect(lambda b: got.extend(s for s, _d in b))
    r.progress.connect(last.update)
    r.failed.connect(errs.append)
    r.start([dict(c) for c in conds], 28, start, 2, mode, rule=("seeds", seeds), gpu="cpu")
    for _ in range(pauses):
        t = time.time() + rnd.uniform(0.05, 0.5)
        wait(r, lambda: time.time() > t or r.state != "running")
        if r.state != "running":
            break
        r.pause()
        wait(r, lambda: r.state in ("paused", "done", "error"))
        if r.state != "paused":
            break                                   # finished right at the pause

        err = r.resume()
        assert err is None, err
    wait(r, lambda: r.state in ("done", "error"))
    assert not errs, errs
    assert len(got) == len(set(got)), "duplicate results"
    return set(got), r._done_before, r.state


def main():
    rnd = random.Random(5)
    bad = 0

    def check(name, ok, info=""):
        nonlocal bad
        bad += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {name:44s} {info}", flush=True)

    # 1. Incremental across INT64_MAX
    start, n = (1 << 63) - 150_000, 400_000
    ref = batch(CLIMATE_ONLY, 28, 0, [signed(start + k) for k in range(n)])
    got, done, st = run(CLIMATE_ONLY, start, "inc", n, pauses=8, rnd=rnd)
    check("inc across INT64_MAX, 8 pauses", got == ref and done == n,
          f"results {len(got)}/{len(ref)} searched {done}/{n}")

    # 2. "48-bit family" requested, no structure -> Incremental
    start, n = rnd.getrandbits(62), 150_000
    full = -(-n // 65536) * 65536                 # the engine rounds up to whole families
    ref = batch(CLIMATE_ONLY, 28, 0, [start + k for k in range(full)])
    got, done, st = run(CLIMATE_ONLY, start, "fam48", n)
    check("48-bit family w/o structure (as Incremental)", got == ref and done == full,
          f"results {len(got)}/{len(ref)} searched {done}/{full}")
    got2, done2, st = run(CLIMATE_ONLY, start, "fam48", n, pauses=8, rnd=rnd)
    ref2 = {s for s in ref if s - start < done2}
    check("  … with 8 pauses", got2 == ref2 and n <= done2 <= full,
          f"results {len(got2)}/{len(ref2)} searched {done2}")

    # 3. Blocks with a 64-bit start seed (>= 2^48) and a seed limit
    start = rnd.getrandbits(63)
    fams = 3
    got, done, st = run(STRUCT, start, "fam48", fams * 65536)
    low = start & ((1 << 48) - 1)
    ref = batch(STRUCT, 28, 0, [signed((h << 48) | (low + d)) for d in range(fams) for h in range(65536)])
    check("Blocks, start >= 2^48, 3 families", got == ref and done == fams * 65536,
          f"results {len(got)}/{len(ref)} searched {done}")
    got2, done2, st = run(STRUCT, start, "fam48", fams * 65536, pauses=6, rnd=rnd)
    check("  … with 6 pauses", got2 == ref and done2 == fams * 65536,
          f"results {len(got2)}/{len(ref)} searched {done2}")

    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
