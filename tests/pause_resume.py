"""Long-session safety: a search paused and resumed many times (at random
moments) must find EXACTLY the seeds of an uninterrupted run — in Incremental
and in 48-bit family (Blocks) mode.

    python tests/pause_resume.py
"""
import os, random, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import custom_search as cs


def S(t, rel=0, r=0, inst=1, **params):
    c = {"type": t, "label": "", "enabled": True, "relative": rel, "params": params, "instances": inst}
    if r:
        c.update(area_mode=0, radial=r, rmax=r + 1, x1=-r, z1=-r, x2=r, z2=r)
    return c


def run(conds, start, count, mode, pauses=0, rnd=None):
    found, resume = set(), [None]
    pos, left = start, count
    done_total = 0
    for k in range(pauses + 1):
        ev = threading.Event()
        rp = [None]
        th = threading.Thread(target=lambda: cs.evaluate_session(
            conds, pos, left, 28, 2, ev, on_match=lambda s, d="": found.add(int(s)),
            on_progress=lambda d, t: None, search_mode=mode, on_resume=lambda p: rp.__setitem__(0, p)))
        th.start()
        if k < pauses:
            time.sleep(rnd.uniform(0.05, 0.6))
            ev.set()
        th.join()
        if k < pauses:
            if rp[0] is None:
                break                               # finished before the pause
            # the resume point is in the engine's iteration units (lows in Blocks)
            consumed = (rp[0] - pos) if mode == "inc" else (rp[0] - (pos & ((1 << 48) - 1)))
            pos += consumed; left -= consumed
            if left <= 0:
                break
    return found


def main():
    rnd = random.Random(7)
    cases = [
        ("inc, biome+structure", [S("village", r=600), S("climate_minmax", rel=1, r=64, para="Temperature",
                                                          range=["2000", None], nmax=2)], "inc", 400_000),
        ("inc, exclusion", [S("village", r=400, inst=0), S("outpost", r=1500)], "inc", 600_000),
        ("blocks, mansion", [S("mansion", r=1200)], "fam48", 40),
        ("blocks, village+biome", [S("village", r=400), S("biome_filter", rel=1, r=64, biomes_incl=[1],
                                                            scale=4, dim="overworld", y=256)], "fam48", 30),
    ]
    bad = 0
    for name, conds, mode, count in cases:
        start = rnd.getrandbits(62) if mode == "inc" else rnd.getrandbits(48)
        ref = run(conds, start, count, mode)
        got = run(conds, start, count, mode, pauses=12, rnd=rnd)
        ok = ref == got
        bad += not ok
        print(f"{'OK  ' if ok else 'DIFF'} {name:24s} results={len(ref)} after 12 pauses={len(got)}"
              + ("" if ok else f" missing={len(ref-got)} extra={len(got-ref)}"), flush=True)
    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
