"""
scan_corrupt_118.py — find 1.18+ corrupted-portal candidates and validate them
END-TO-END against the real Minecraft 1.18.1 checker.

Pipeline: iterate seeds -> corrupt_118_first (cheap ruined-near gate, then exact
1.18+ footprint overlap) -> candidates -> checker confirms the real corruption.
Version-correct (no hardcoded region size). ~hundreds of seeds/s.
"""
import os
import cubiomes_wrap as cw
import ruined_118 as r118
import checker_client

MC = 22
WANT = 3            # stop after this many predicted-corrupted seeds
MAX_SCAN = 2_000_000

_here = os.path.dirname(os.path.abspath(__file__))
checker_client._CHECKER_DIR = os.path.normpath(os.path.join(
    _here, "..", "Sources to relie on",
    "seed-checker-1.18.1", "seed-checker-1.18.1"))


def main():
    import time
    lib = cw.get_lib()                 # find_strongholds / find_structures / height
    found = []
    print("Scanning for 1.18 corrupted candidates...")
    t0 = time.time()
    for seed in range(MAX_SCAN):
        try:
            hits = r118.corrupt_118_first(lib, seed)
        except Exception:
            hits = []
        if hits:
            found.append((seed, hits))
            print(f"  candidate seed {seed}: {len(hits)} frame(s) under a "
                  f"ruined (ti={hits[0][2]}, ruined chunk {hits[0][1]})")
            if len(found) >= WANT:
                break
        if seed and seed % 20000 == 0:
            sps = seed / max(0.001, time.time() - t0)
            print(f"   ...{seed:,} seeds, {len(found)} candidates ({sps:.0f}/s)")

    if not found:
        print("No candidate found in range."); return

    # build checker requests (seed, frames, ruined origin chunk)
    print(f"\nValidating {len(found)} candidate(s) vs real Minecraft 1.18.1...")
    from stronghold_eyes import get_end_portal_frames
    reqs = []
    for (seed, hits) in found:
        lib.setup_and_seed(MC, seed)
        bx, bz = lib.find_strongholds(max_count=1)[0]
        frames, ex, ez, fy = get_end_portal_frames(seed, bx, bz, MC)
        rcx, rcz = hits[0][1]
        reqs.append((seed, frames, rcx, rcz))
    res = checker_client.verify(reqs, timeout=400.0)
    print()
    for (seed, frames, rcx, rcz) in reqs:
        r = res.get(seed, {})
        nat, forced = r.get("natural", -1), r.get("forced", -1)
        tag = "✓ CORRUPTED" if forced > 0 else "✗ not corrupted (false positive)"
        print(f"seed {seed}: NATURAL={nat}  FORCED(broken)={forced}   {tag}")
        for (x, y, z, b) in r.get("broken", [])[:12]:
            print(f"     frame ({x},{y},{z}) -> {b}")


if __name__ == "__main__":
    main()
