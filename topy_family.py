"""
topy_family.py — fast « Top Y » search for 1.16 by 48-bit families (03/10).

Why it is exact and fast
  * The stronghold LAYOUT, hence its top Y after moveBelowSeaLevel, depends only on
    the 48 low bits of the seed and on the start chunk (java.util.Random keeps 48
    bits). Checked: the same chunk gives the same top Y for every high 16 bits.
  * The start chunk is the first ring position (48-bit too) snapped to a valid
    biome within 112 blocks — the only part that needs the 16 high bits.
  So for one 48-bit value (a FAMILY of 65 536 seeds):
    1. the ring position, then the top Y of EVERY chunk the snap can reach
       (17 × 17 chunks, native BFS): ~290 layouts instead of 65 536;
    2. only if one of those chunks reaches the threshold: the snapped start of the
       65 536 seeds (native), and the exact check of the seeds that land on it.
  Tall strongholds are rare, so step 2 is rare: the higher the threshold, the
  faster (≈ 40× at Y ≥ 64 and ≈ 300× at Y ≥ 66 vs. seed by seed, measured on 1.16).

1.16 only for now (19 = 1.16.1, 20 = 1.16.5): validated against the seed-by-seed
search (tests/topy_family_check.py).

  search_topy_families(start48, count_seeds, min_max_y, callback, stop_event,
                       progress_callback, num_workers, mc_version)
      callback(seed, eyes, bx, bz, maxy); progress in seeds (families × 65 536).
"""
from __future__ import annotations

import ctypes as C
import multiprocessing as mp

import numpy as np

FAMILY = 1 << 16
M48 = (1 << 48) - 1
VERSIONS = (19, 20)
SNAP = 112                                   # locateBiome radius of the 1st stronghold
MIN_FAST_Y = 56                              # below, most families have a tall chunk: seed by seed
MIN_FAST_Y_ROOM = 51                         # the same rarity for the portal room top (03/10)

_W = {}


def _winit(mc):
    import stronghold_eyes as se
    import ruined_portal as rp
    import cubiomes_wrap
    nat = se._sl_native_init()
    if not nat:
        raise RuntimeError("native libraries missing (rebuild them)")
    lib = C.CDLL(cubiomes_wrap._find_lib() or cubiomes_wrap._compile_cubiomes())
    rp._bind(lib)
    ring_ctx = lib.cb_create()
    cubiomes_wrap.apply_large_ctx(lib, ring_ctx)
    cdll, ctx, glib = nat
    cubiomes_wrap.apply_large_ctx(cdll, ctx)
    _W.update(mc=mc, nat=nat, lib=lib, ring_ctx=ring_ctx)


def _s64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


def _ring(f):
    lib, ctx, mc = _W["lib"], _W["ring_ctx"], _W["mc"]
    lib.cb_setup(ctx, mc, f & ((1 << 64) - 1))
    a = [C.c_int(0) for _ in range(4)]
    lib.cb_first_stronghold(ctx, mc, f & ((1 << 64) - 1), *[C.byref(v) for v in a])
    return a[0].value, a[1].value


def _tall_chunks(f, thr):
    """{(cx, cz): topY} of the chunks the snap can reach with topY >= thr."""
    import stronghold_eyes as se
    cdll, ctx, glib = _W["nat"]
    mc = _W["mc"]
    rx, rz = _ring(f)
    # locateBiome (1.16): the 57 × 57 cells of 4 blocks around (ring >> 2); the
    # snapped start is a cell * 4, so its chunk is in this exact range
    cx0, cz0 = rx >> 2, rz >> 2
    xs = range(((cx0 - SNAP // 4) * 4) >> 4, (((cx0 + SNAP // 4) * 4) >> 4) + 1)
    zs = range(((cz0 - SNAP // 4) * 4) >> 4, (((cz0 + SNAP // 4) * 4) >> 4) + 1)
    cx = np.array([x for x in xs for _z in zs], dtype=np.int32)
    cz = np.array([z for _x in xs for z in zs], dtype=np.int32)
    n = len(cx)
    seeds = np.full(n, _s64(f), dtype=np.int64)
    out_s = np.zeros(n, dtype=np.int64)
    out_y = np.zeros(n, dtype=np.int32)
    sea, miny = se._sea_min_for_mc(mc)
    glib.max_y_batch_arr(seeds.ctypes.data_as(C.POINTER(C.c_int64)), n,
                         cx.ctypes.data_as(C.POINTER(C.c_int32)),
                         cz.ctypes.data_as(C.POINTER(C.c_int32)),
                         1 if se._use_old_filler(mc) else 0, sea, 10, miny,
                         se._inflate_arg(mc, thr), -1_000_000,
                         out_s.ctypes.data_as(C.POINTER(C.c_int64)),
                         out_y.ctypes.data_as(C.POINTER(C.c_int32)))
    keep = np.nonzero(out_y >= thr)[0]
    return {(int(cx[i]), int(cz[i])): int(out_y[i]) for i in keep}


def family_hits(f, thr, one=False, tall=None):
    """Exact Top Y hits of the 65 536 seeds whose 48 low bits are f.
    one=True: ONE seed per tall stronghold — per start chunk, the first seed (by
    high 16 bits) that lands on it; the other ones would have the very same
    stronghold (same 48 bits, same chunk), only the biomes / terrain differ."""
    import stronghold_eyes as se
    if tall is None:                      # (given by the GPU in the hybrid mode)
        tall = _tall_chunks(f, thr)
    if not tall:
        return []
    cdll, ctx, glib = _W["nat"]
    mc = _W["mc"]
    out = []
    todo = set(tall)                      # tall chunks still without a seed (one=True)
    step = 2048 if one else FAMILY        # one=True: small slices, stop when all found
    for h0 in range(0, FAMILY, step):
        seeds = ((np.arange(h0, h0 + step, dtype=np.uint64) << np.uint64(48))
                 | np.uint64(f & M48)).view(np.int64)
        bx = np.zeros(step, dtype=np.int32)
        bz = np.zeros(step, dtype=np.int32)
        cdll.seedlist_first_stronghold(ctx, int(mc), seeds.ctypes.data_as(C.POINTER(C.c_int64)),
                                       step, bx.ctypes.data_as(C.POINTER(C.c_int32)),
                                       bz.ctypes.data_as(C.POINTER(C.c_int32)))
        cxs, czs = bx >> 4, bz >> 4
        for (tx, tz) in list(todo):
            for i in np.nonzero((cxs == tx) & (czs == tz))[0].tolist():   # increasing high 16
                s, sbx, sbz = int(seeds[i]), int(bx[i]), int(bz[i])
                eyes, maxy = se.get_stronghold_eyes_and_maxy(s, sbx, sbz, mc,
                                                             what=se.topy_what(thr))   # exact
                if maxy is not None and maxy >= thr:
                    out.append((s, eyes, sbx, sbz, maxy))
                    if one:
                        todo.discard((tx, tz))
                        break
        if one and not todo:
            break
    return out


def _verify(args):
    """Hybrid mode: the GPU found these tall chunks of family f; snap + exact check."""
    f, tall, thr, one = args
    try:
        return 0, family_hits(f, thr, one, tall), []
    except Exception as e:                    # noqa: BLE001 — reported, the search goes on
        return 0, [], [(f, f"{type(e).__name__}: {e}")]


def _chunk(args):
    """A block of families. A family whose check fails (a native error) is
    reported with its 48-bit value instead of stopping the whole search."""
    f0, nf, thr, one = args
    hits, errs = [], []
    for k in range(nf):
        f = (f0 + k) & M48
        try:
            hits += family_hits(f, thr, one)
        except Exception as e:                # noqa: BLE001
            errs.append((f, f"{type(e).__name__}: {e}"))
    return nf, hits, errs


ERROR_LOG = "topy_errors.log"


def _log_error(msg):
    """Appends to topy_errors.log next to the program (to reproduce the case)."""
    import os
    import time
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, ERROR_LOG), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def search_topy_families(start48, count_seeds, min_max_y, callback, stop_event=None,
                         progress_callback=None, num_workers=1, mc_version=20,
                         block=16, one_per_family=False, use_gpu=False, status_callback=None,
                         resume_callback=None):
    """use_gpu: hybrid CPU + GPU (topy_gpu). The GPU takes big blocks of families
    and finds their tall chunks; the CPU workers take the other blocks as usual,
    and also do the snap + exact check of what the GPU found. Without a usable GPU
    (or if it fails during the search) everything runs on the CPU.
    resume_callback(f): every family before f (48-bit, from start48) is fully
    checked — the exact point a paused search continues from (03/10)."""
    if mc_version not in VERSIONS:
        raise ValueError("the family Top Y search is 1.16 only")
    nfam = -(-int(count_seeds) // FAMILY) if count_seeds and count_seeds > 0 else None
    done = [0]
    import stronghold_eyes as se
    # a RoomY threshold (top of the portal room) keeps its type: the workers see it
    thr = min_max_y if isinstance(min_max_y, se.RoomY) else int(min_max_y)
    one = bool(one_per_family)

    def stopped():
        return stop_event is not None and stop_event.is_set()

    def status(msg):
        print(f"[topy] {msg}", flush=True)
        if status_callback:
            status_callback(msg)

    disp = _Dispenser(int(start48) & M48, nfam, stopped)
    f_start = int(start48) & M48
    open_blocks = {}                          # block start -> checks still running
    import threading as _thr
    olock = _thr.Lock()

    def opened(b0, k=1):
        with olock:
            open_blocks[b0] = open_blocks.get(b0, 0) + k

    def closed(b0):
        with olock:
            n = open_blocks.get(b0, 0) - 1
            if n <= 0:
                open_blocks.pop(b0, None)
            else:
                open_blocks[b0] = n

    def report_resume():
        if resume_callback is None:
            return
        with disp.lock:                       # a block is taken + opened under it
            cands = [disp.f] + [b[0] for b in disp.back]
            with olock:
                cands += list(open_blocks)
        off = min(((c - f_start) & M48) for c in cands)
        resume_callback((f_start + off) & M48)

    def blocks():
        while True:
            b = disp.take(block)
            if b is None:
                return
            yield (b[0], b[1], thr, one)

    gpu = None
    if use_gpu:
        try:
            import topy_gpu
            gpu = topy_gpu.TopyGpu(mc_version, room=bool(se.topy_what(thr)))
        except Exception as e:                # no GPU: CPU only, as before
            status(f"GPU off ({e}) — CPU only")
            gpu = None

    def emit(res):
        nf, hits = res[0], res[1]
        for f, err in (res[2] if len(res) > 2 else ()):
            _log_error(f"mc={mc_version} thr={thr} one={one} family48={f}: {err}")
            status(f"family {f} skipped ({err}) — written to {ERROR_LOG}")
        for h in hits:
            callback(*h)
        done[0] += nf * FAMILY
        if progress_callback:
            progress_callback(done[0], (nfam or 0) * FAMILY)

    nwork = max(1, int(num_workers))
    report_resume()                           # (a pause before any block ends: the start)
    if nwork == 1 and gpu is None:
        _winit(mc_version)
        for b in blocks():
            emit(_chunk(b))
            report_resume()
        return
    import threading
    import time
    gq = []                                   # (families scanned, {fam: tall}) from the GPU
    glock = threading.Lock()
    gfam = [0]                                # families scanned by the GPU
    ended = threading.Event()                 # the search is over (any reason)

    def gpu_loop():
        try:
            while not stopped() and not ended.is_set():
                with disp.lock:
                    b = disp.take(GPU_BLOCK)
                    if b is not None:
                        opened(b[0])
                if b is None:
                    return
                try:
                    hits = gpu.scan(b[0], b[1], thr)
                except Exception as e:        # GPU lost: give the block back to the CPU
                    disp.give_back(b)
                    closed(b[0])
                    _log_error(f"mc={mc_version} thr={thr} GPU block {b[0]}+{b[1]}: "
                               f"{type(e).__name__}: {e}")
                    status(f"GPU stopped ({e}) — the CPU goes on alone")
                    return
                by_fam = {}
                for (f, cx, cz, top) in hits:
                    by_fam.setdefault(f, {})[(cx, cz)] = top
                with glock:
                    gq.append((b[0], b[1], by_fam))
                gfam[0] += b[1]
        except Exception as e:                # never kill the search from this thread
            status(f"GPU stopped ({e}) — the CPU goes on alone")

    gth = None
    if gpu is not None:
        gth = threading.Thread(target=gpu_loop, name="topy-gpu", daemon=True)
        gth.start()
    ctx = mp.get_context("spawn")
    with ctx.Pool(nwork, initializer=_winit, initargs=(mc_version,)) as pool:
        try:
            flight = []
            while True:
                with glock:                   # GPU results first: queue their checks
                    got, gq[:] = list(gq), []
                for b0, nf, by_fam in got:
                    if by_fam:
                        opened(b0, len(by_fam))
                    for f, tall in by_fam.items():
                        flight.append((pool.apply_async(_verify, ((f, tall, thr, one),)), b0))
                    closed(b0)                # the scan itself (opened in gpu_loop)
                    emit((nf, []))
                while len(flight) < 2 * nwork:
                    with disp.lock:
                        b = disp.take(block)
                        if b is not None:
                            opened(b[0])
                    if b is None:
                        break
                    flight.append((pool.apply_async(_chunk, ((b[0], b[1], thr, one),)), b[0]))
                ready = [x for x in flight if x[0].ready()]
                for x in ready:
                    flight.remove(x)
                    try:
                        res = x[0].get()
                    except Exception as e:    # noqa: BLE001 — a worker died: say it, go on
                        _log_error(f"mc={mc_version} thr={thr} block lost: "
                                   f"{type(e).__name__}: {e}")
                        status(f"a block of families was lost ({e}) — the search goes on")
                        closed(x[1])
                        continue
                    emit(res)
                    closed(x[1])
                if ready or got:
                    report_resume()
                if stopped():
                    break
                if not ready:
                    gpu_alive = gth is not None and gth.is_alive()
                    if not flight and not gpu_alive:
                        with glock:
                            if not gq:
                                break         # everything scanned and checked
                    time.sleep(0.02)
        finally:
            ended.set()
            pool.terminate()
            pool.join()
            if gth is not None:
                gth.join(timeout=10)
                if done[0]:
                    status(f"GPU share: {100 * gfam[0] * FAMILY / done[0]:.0f} % of the families")
            report_resume()                   # the exact point after the last block (03/10)


GPU_BLOCK = 2048                              # families per GPU scan (≈ 1 s on an RTX 5080)


class _Dispenser:
    """Consecutive blocks of families for the CPU workers and the GPU thread."""

    def __init__(self, f0, nfam, stopped):
        import threading
        self.f, self.left, self.stopped = f0, nfam, stopped
        self.back = []
        self.lock = threading.RLock()        # taken by the resume point too (03/10)

    def take(self, k):
        with self.lock:
            if self.stopped():
                return None
            if self.back:
                b = self.back.pop()
                if b[1] > k:                   # given back by the GPU: CPU-size pieces
                    self.back.append(((b[0] + k) & M48, b[1] - k))
                    b = (b[0], k)
                return b
            if self.left is not None and self.left <= 0:
                return None
            k = k if self.left is None else min(k, self.left)
            b = (self.f, k)
            self.f = (self.f + k) & M48
            if self.left is not None:
                self.left -= k
            return b

    def give_back(self, b):
        with self.lock:
            self.back.append(b)
