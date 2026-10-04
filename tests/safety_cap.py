"""Result limit applied INSIDE the C engine (p96): on a session where every
seed matches, the search must pause at the limit at once (not run a million
matches ahead), and resuming (with a raised limit) must still give exactly
the results of an uninterrupted run, without duplicates.

    python tests/safety_cap.py
"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
import qtui.search_runner as _sr                     # noqa: E402
_sr.SAFETY_FIRST, _sr.SAFETY_MAX = 20_000, 2_000_000
from qtui.search_runner import SearchRunner          # noqa: E402
from engine_vs_batch import batch, signed            # noqa: E402
from diff_engine import S                            # noqa: E402

ALL = [S("climate_minmax", r=64, para="Temperature", range=[None, None], nmax=2)]   # every seed
HALF = [S("climate_minmax", r=64, para="Temperature", range=[None, "0"], nmax=2)]   # ~half


def wait(until, limit=300):
    t = time.time()
    while not until() and time.time() - t < limit:
        app.processEvents(); time.sleep(0.005)


def run(conds, start, n, mode):
    r = SearchRunner()
    got = []
    r.resultsReady.connect(lambda b: got.extend(s for s, _d in b))
    t0 = time.time()
    r.start([dict(c) for c in conds], 28, start, 4, mode, rule=("seeds", n), gpu="cpu")
    pauses, first = 0, None
    while True:
        wait(lambda: r.state in ("paused", "done", "error"))
        if r.state != "paused":
            break
        if first is None:
            first = (r.found, time.time() - t0, r.auto_paused)
        pauses += 1
        r.raise_safety()
        assert r.resume() is None
    return got, r, pauses, first


bad = 0
for name, conds, mode in (("all match, inc", ALL, "inc"), ("half match, inc", HALF, "inc")):
    start, n = 123_456_789, 600_000
    ref = batch(conds, 28, 0, [signed(start + k) for k in range(n)])
    got, r, pauses, first = run(conds, start, n, mode)
    ok = (sorted(got) == sorted(ref)) and len(got) == len(set(got)) and first is not None \
        and first[0] == _sr.SAFETY_FIRST and first[2] == "safety"
    bad += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {name:18s} first pause at {first[0] if first else None} "
          f"results ({first[1] if first else 0:.2f} s, {first[2] if first else ''}), {pauses} pauses, "
          f"results {len(got)}/{len(ref)} dup {len(got) - len(set(got))}", flush=True)
print("FAILURES:", bad)
os._exit(1 if bad else 0)
