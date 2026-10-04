"""
broken_search.py — the broken end portal search shared by the Stronghold page
and the Sessions "Broken portal" condition (moved out of qtui/stronghold_page.py
unchanged, 03/10): ruined portals (ruined_portal) and lakes (lake_portals, 1.16)
side by side, the processes shared between them.
"""
from __future__ import annotations

import threading


class ResumeTracker:
    """Exact resume point of a range search whose blocks finish in any order
    (03/10): every seed before it is fully checked, by EVERY job. Before, the
    point was start + seeds done, which skipped a slow block still running
    when the search was paused."""

    def __init__(self, start, jobs):
        self.start = int(start)
        self.lock = threading.Lock()
        self.nxt = {j: int(start) for j in jobs}
        self.done = {j: {} for j in jobs}
        self.used = False                    # a block was reported (else: unknown)
        self.held = {}                       # seeds handed on but not answered yet

    def chunk_cb(self, job):
        def cb(s0, n):
            with self.lock:
                self.used = True
                d = self.done[job]
                d[int(s0)] = int(n)
                while self.nxt[job] in d:
                    self.nxt[job] += d.pop(self.nxt[job])
        return cb

    def hold(self, seed):
        """A candidate whose check (Minecraft) is still to come: the resume
        point stays at or before it."""
        with self.lock:
            self.held[int(seed)] = self.held.get(int(seed), 0) + 1

    def release(self, seed):
        with self.lock:
            k = int(seed)
            n = self.held.get(k, 0) - 1
            if n <= 0:
                self.held.pop(k, None)
            else:
                self.held[k] = n

    def point(self):
        """The resume seed (signed 64-bit)."""
        M = 1 << 64
        with self.lock:
            cands = list(self.nxt.values()) or [self.start]
            cands += list(self.held)
        p = min(cands, key=lambda v: (v - self.start) % M)
        return ((p + (1 << 63)) % M) - (1 << 63)


def run_broken_sources(sources, run_start, run_count, threads, stop, on_progress, mc, all128,
                       seed_list, ruined_cb, lake_cb, throttle=None, tracker=None):
    """Runs the selected broken-portal searches over the same seeds: the ruined
    portals (ruined_portal) and the lakes (lake_portals, 1.16), side by side with
    the processes shared between them; the progress is the slower of the two (03/10)."""
    import ruined_portal as rp
    import lake_portals as lp
    kinds = tuple(k for k in ("lava", "water") if k in sources)
    jobs = []
    if "ruined" in sources:
        jobs.append("ruined")
    if kinds and mc in lp.LAKE_VERSIONS:
        jobs.append("lakes")
    if not jobs:
        return
    if tracker is not None:                 # one resume track per job actually run
        tracker.nxt = {j: tracker.start for j in jobs}
        tracker.done = {j: {} for j in jobs}
    done = {j: 0 for j in jobs}
    lock = threading.Lock()
    if len(jobs) == 2:                      # lakes cost more per seed than the ruined gate
        n_l = max(1, (int(threads) * 2) // 3)
        workers = {"lakes": n_l, "ruined": max(1, int(threads) - n_l)}
    else:
        workers = {jobs[0]: max(1, int(threads))}

    def prog_for(j):
        def prog(n, total):
            with lock:
                done[j] = n
                low = min(done.values())
            on_progress(low, total)
        return prog

    def run(j):
        if j == "ruined":
            rp.search_corrupted_portals(run_start, run_count, ruined_cb, stop,
                                        progress_callback=prog_for(j), num_workers=workers[j],
                                        mc_version=mc, scan_128=all128, seed_list=seed_list,
                                        chunk_callback=tracker.chunk_cb(j) if tracker else None)
        else:
            lp.search_lake_portals(run_start, run_count, lake_cb, stop,
                                   progress_callback=prog_for(j), num_workers=workers[j],
                                   mc_version=mc, kinds=kinds, scan_128=all128,
                                   seed_list=seed_list, throttle=throttle,
                                   chunk_callback=tracker.chunk_cb(j) if tracker else None)
    if len(jobs) == 1:
        run(jobs[0])
        return
    errs = []

    def guarded(j):
        try:
            run(j)
        except Exception as e:              # noqa: BLE001 — reported after the join
            errs.append(e)
            stop.set()
    t = threading.Thread(target=guarded, args=("ruined",), daemon=True)
    t.start()
    guarded("lakes")
    t.join()
    if errs:
        raise errs[0]
