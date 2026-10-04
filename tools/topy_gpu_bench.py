"""
tools/topy_gpu_bench.py — GPU vs CPU for the 1.16 « Top Y » family search (03/10).

    py tools/topy_gpu_bench.py            (close SSA first, ~2–3 minutes)

1. Exactness: every stronghold of ~150 families (225 start chunks each), GPU vs
   the CPU generator (gaider_layouts) on 1.16, 1.17 and 1.18. Must be 0 errors.
2. Speed: strongholds / s on the GPU vs the CPU with all its cores (the work
   the family search does for each family), and the ratio.
Paste the whole output back in the chat.
"""
from __future__ import annotations

import ctypes as C
import multiprocessing as mp
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np  # noqa: E402

PER_FAM = 225
M48 = (1 << 48) - 1


def _s64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


def cpu_family(glib, g, f, cx0, cz0):
    """Top Y of the 225 chunks of family f on the CPU (gaider_layouts)."""
    j = np.arange(PER_FAM)
    cx = (cx0 + j // 15).astype(np.int32)
    cz = (cz0 + j % 15).astype(np.int32)
    seeds = np.full(PER_FAM, _s64(f), np.int64)
    os_ = np.zeros(PER_FAM, np.int64); oy = np.zeros(PER_FAM, np.int32)
    glib.max_y_batch_arr(seeds.ctypes.data_as(C.POINTER(C.c_int64)), PER_FAM,
                         cx.ctypes.data_as(C.POINTER(C.c_int32)),
                         cz.ctypes.data_as(C.POINTER(C.c_int32)),
                         g.pre118, g.sea, g.margin, g.miny, g.inflate, -1_000_000,
                         os_.ctypes.data_as(C.POINTER(C.c_int64)),
                         oy.ctypes.data_as(C.POINTER(C.c_int32)))
    return {(int(cx[i]), int(cz[i])): int(oy[i]) for i in range(PER_FAM)}


# ---- CPU baseline worker (one process per core) ------------------------------
_G = {}


def _winit(mc):
    import topy_family as tf
    tf._winit(mc)
    _G["tf"] = tf


def _cpu_work(args):
    f0, n = args
    tf = _G["tf"]
    t = time.perf_counter()
    for k in range(n):
        tf._tall_chunks((f0 + k) & M48, 1000)          # exactly the per-family CPU work
    return n, time.perf_counter() - t


def main():
    import native_libs
    import topy_gpu
    print("=" * 66)
    print("SSA — Top Y GPU benchmark")
    lib, msg = topy_gpu.load()
    print(f"GPU library : {msg}")
    if lib is None:
        print("→ no usable GPU: stop here (the search stays on the CPU).")
        return 1
    import stronghold_eyes as se
    nat = se._sl_native_init()
    if not nat:
        print("→ native CPU libraries missing: run  py tools/build_native.py --cubiomes")
        return 1
    glib = nat[2]
    rnd = random.Random(20261003)

    # ---- 1. exactness -----------------------------------------------------------
    print("-" * 66)
    print("1) Exactness GPU = CPU (every start chunk of random families)")
    total_bad = 0
    for mc, name, nf in ((20, "1.16", 100), (21, "1.17", 25), (22, "1.18", 25)):
        g = topy_gpu.TopyGpu(mc)
        bad = n = 0
        for _ in range(nf):
            f = rnd.getrandbits(48)
            cx0, cz0 = g.ring_chunk0(f)
            got = {(cx, cz): top for (_f, cx, cz, top) in
                   g.scan(f, 1, -1000, np.array([cx0], np.int16), np.array([cz0], np.int16))}
            ref = cpu_family(glib, g, f, cx0, cz0)
            n += len(ref)
            for k, v in ref.items():
                if got.get(k) != v:
                    bad += 1
                    if bad <= 5:
                        print(f"   MISMATCH {name} seed48={f} chunk={k} cpu={v} gpu={got.get(k)}")
        total_bad += bad
        print(f"   {name}: {n:6d} strongholds, {bad} errors, {g.overflows} handed to the CPU (too big)")
    if total_bad:
        print("→ ERRORS: the GPU is NOT exact — paste this output, nothing else to do.")
        return 1
    print("   OK — identical.")

    # ---- 2. speed -------------------------------------------------------------
    print("-" * 66)
    print("2) Speed (1.16, threshold Top Y >= 66)")
    g = topy_gpu.TopyGpu(20)
    f0 = rnd.getrandbits(48)
    nf = 4096
    cx0 = np.empty(nf, np.int16); cz0 = np.empty(nf, np.int16)
    t = time.perf_counter()
    for k in range(nf):
        cx0[k], cz0[k] = g.ring_chunk0((f0 + k) & M48)
    ring_s = (time.perf_counter() - t) / nf
    def measure(min_s):
        g.scan(f0, 2048, 66, cx0, cz0)                 # warm-up (JIT, clocks, launch size)
        n = 4096
        while True:
            reps = max(1, n // nf)
            t = time.perf_counter()
            hits = 0
            for _r in range(reps):
                hits += len(g.scan(f0, nf, 66, cx0, cz0))
            dt = time.perf_counter() - t
            if dt >= min_s or n >= 1 << 22:
                return reps * nf * PER_FAM / dt, reps * nf, dt, hits
            n *= 2

    best = (None, 0.0)
    names = topy_gpu.VARIANTS
    if topy_gpu.set_variant(0) is not None:
        for v, name in enumerate(names):
            topy_gpu.set_variant(v)
            rate, _f, _dt, _h = measure(2.5)
            st = topy_gpu.stats()
            print(f"   variant {v} ({name}): {rate / 1e6:5.2f} M strongholds/s"
                  + (f"  [{st['resident']} threads]" if st else ""))
            if rate > best[1]:
                best = (v, rate)
        topy_gpu.set_variant(best[0])
        print(f"   → best: variant {best[0]}")
    gpu_rate, fams_done, dt, hits = measure(4)
    fam_gpu = 1 / (1 / (gpu_rate / PER_FAM) + ring_s)
    print(f"   GPU : {gpu_rate / 1e6:7.2f} M strongholds/s  ({fams_done} families in {dt:.1f} s, {hits} hits)")
    st = topy_gpu.stats()
    if st:
        print(f"         {st['resident']} threads at once ({st['blocks']} blocks), "
              f"{st['fam_chunk']} families per launch, last launch {st['last_ms']:.0f} ms")

    ncpu = os.cpu_count() or 1
    ctx = mp.get_context("spawn")
    with ctx.Pool(ncpu, initializer=_winit, initargs=(20,)) as pool:
        pool.map(_cpu_work, [(rnd.getrandbits(48), 8)] * ncpu)          # warm-up
        jobs = [(rnd.getrandbits(48), 64) for _ in range(ncpu * 3)]
        t = time.perf_counter()
        res = pool.map(_cpu_work, jobs, chunksize=1)
        dt_cpu = time.perf_counter() - t
    fams = sum(r[0] for r in res)
    cpu_rate = fams * PER_FAM / dt_cpu
    print(f"   CPU : {cpu_rate / 1e6:7.2f} M strongholds/s  ({ncpu} processes, {fams} families in {dt_cpu:.1f} s)")
    print("-" * 66)
    ratio = fam_gpu / (cpu_rate / PER_FAM)
    print(f"   GPU / CPU (families/s, ring on the CPU included) = ×{ratio:.1f}")
    print(f"   ≈ {fam_gpu * 65536 / 1e6:,.0f} M seeds/s on the GPU vs "
          f"{cpu_rate / PER_FAM * 65536 / 1e6:,.0f} M seeds/s on the CPU")
    print(f"   (device: {native_libs.device_info(lib)})")
    # ---- 3. the real search: CPU only vs CPU + GPU --------------------------------
    print("-" * 66)
    print("3) Real search (1.16, Top Y >= 70, 25 s each): CPU only vs CPU + GPU")
    import threading
    import topy_family as tf
    nw = max(1, ncpu - 1)
    rates = {}
    for use in (False, True):
        stop = threading.Event()
        prog = [0]
        found = []
        start = rnd.getrandbits(48)
        th = threading.Thread(target=tf.search_topy_families, args=(
            start, 0, 70, lambda *h: found.append(h), stop,
            lambda d, _t: prog.__setitem__(0, d), nw, 20),
            kwargs=dict(one_per_family=True, use_gpu=use), daemon=True)
        th.start()
        time.sleep(5)                                   # workers start, first blocks
        d0, t0 = prog[0], time.perf_counter()
        time.sleep(25)
        d1, t1 = prog[0], time.perf_counter()
        stop.set(); th.join(30)
        rates[use] = (d1 - d0) / (t1 - t0)
        print(f"   {'CPU + GPU' if use else 'CPU only '}: {rates[use] / 1e6:8.1f} M seeds/s "
              f"({nw} processes, {len(found)} found)")
    if rates[False] > 0:
        print(f"   → CPU + GPU = ×{rates[True] / rates[False]:.2f} the CPU alone")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
