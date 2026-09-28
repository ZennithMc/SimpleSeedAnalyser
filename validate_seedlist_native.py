"""
validate_seedlist_native.py — check the new native seedlist primitives against
the (correct, slow) Python path, on a sample of real seeds.

Validates:
  1. seedlist_first_stronghold (libcubiomes)  ==  cubiomes find_strongholds
  2. max_y_batch_arr           (gaider_layouts) ==  Python get_stronghold_eyes_and_maxy maxY

Run AFTER recompiling (delete libcubiomes.dll + gaider_layouts.dll, relaunch SSA
once to rebuild, or just run this — it triggers the cubiomes build):

    py validate_seedlist_native.py <seedfile> <mc> [N]
    py validate_seedlist_native.py 12eye1.16_seedonly.seeds 20 2000

mc: 1.16=20, 1.17.1=21, 1.18=22, 1.19=24, 1.20=25, 1.21.1=26, 1.21.4=28.
"""
import sys, time, ctypes
import numpy as np

import cubiomes_wrap as cw
import gaider_layouts as gl
import seedlist_io
from stronghold_eyes import get_stronghold_eyes_and_maxy, _use_old_filler, \
    _sea_min_for_mc, _inflate_for_mc


def _sea_params(mc):
    sea, miny = _sea_min_for_mc(mc)
    return sea, 10, miny, _inflate_for_mc(mc)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "12eye1.16_seedonly.seeds"
    mc = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    N = int(sys.argv[3]) if len(sys.argv) > 3 else 2000

    # sample N seeds from the file
    seeds = []
    for s in seedlist_io.iter_seedlist(path):
        seeds.append(s)
        if len(seeds) >= N:
            break
    seeds = np.array(seeds, dtype=np.int64)
    N = len(seeds)
    print(f"loaded {N} sample seeds from {path}  (mc={mc})")

    lib = cw.make_worker_lib()
    cdll = lib._lib

    # ---- bind the new natives ----
    if not hasattr(cdll, "seedlist_first_stronghold"):
        print("seedlist_first_stronghold MISSING — delete libcubiomes.dll to recompile.")
        return
    cdll.cb_create.restype = ctypes.c_void_p
    cdll.seedlist_first_stronghold.argtypes = [
        ctypes.c_void_p, ctypes.c_int,
        ctypes.POINTER(ctypes.c_int64), ctypes.c_int,
        ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32)]
    cdll.seedlist_first_stronghold.restype = ctypes.c_int

    gl._load()
    glib = gl._LIB
    if glib is None or not hasattr(glib, "max_y_batch_arr"):
        print("max_y_batch_arr MISSING — delete gaider_layouts.dll to recompile.")
        return
    glib.max_y_batch_arr.argtypes = [
        ctypes.POINTER(ctypes.c_int64), ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int32)]
    glib.max_y_batch_arr.restype = ctypes.c_uint32

    # ---- 1) native positions ----
    ctx = cdll.cb_create()
    nx = np.zeros(N, dtype=np.int32); nz = np.zeros(N, dtype=np.int32)
    t = time.perf_counter()
    cdll.seedlist_first_stronghold(
        ctx, mc, seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)), N,
        nx.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        nz.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)))
    t_native_pos = time.perf_counter() - t

    # cubiomes find_strongholds reference
    ref_x = np.zeros(N, dtype=np.int64); ref_z = np.zeros(N, dtype=np.int64)
    t = time.perf_counter()
    for i, s in enumerate(seeds.tolist()):
        lib.setup_and_seed(mc, int(s))
        sh = lib.find_strongholds(max_count=1)
        if sh:
            ref_x[i], ref_z[i] = sh[0]
    t_ref_pos = time.perf_counter() - t
    pos_match = int(np.sum((nx == ref_x) & (nz == ref_z)))
    print(f"[positions] native {t_native_pos:.2f}s vs cubiomes {t_ref_pos:.2f}s  "
          f"({t_ref_pos/max(t_native_pos,1e-9):.1f}x)  "
          f"match {pos_match}/{N} ({100*pos_match/N:.1f}%)")

    # ---- 2) native maxY (use cubiomes positions so we isolate the topY calc) ----
    cxs = (ref_x >> 4).astype(np.int32); czs = (ref_z >> 4).astype(np.int32)
    sea, margin, miny, inflate = _sea_params(mc)
    out_s = np.zeros(N, dtype=np.int64); out_y = np.zeros(N, dtype=np.int32)
    t = time.perf_counter()
    cnt = glib.max_y_batch_arr(
        seeds.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)), N,
        cxs.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        czs.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)),
        1 if _use_old_filler(mc) else 0, sea, margin, miny, inflate,
        -1_000_000,  # threshold low -> keep all, so we can compare every value
        out_s.ctypes.data_as(ctypes.POINTER(ctypes.c_int64)),
        out_y.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)))
    t_native_my = time.perf_counter() - t
    native_y = {int(out_s[i]): int(out_y[i]) for i in range(cnt)}

    # Python maxY reference
    t = time.perf_counter()
    mism = 0; checked = 0
    for i, s in enumerate(seeds.tolist()):
        _e, py_y = get_stronghold_eyes_and_maxy(int(s), int(ref_x[i]), int(ref_z[i]), mc)
        if py_y is None:
            continue
        checked += 1
        if native_y.get(int(s)) != py_y:
            mism += 1
            if mism <= 5:
                print(f"    maxY mismatch seed {int(s)}: native={native_y.get(int(s))} python={py_y}")
    t_ref_my = time.perf_counter() - t
    print(f"[maxY] native {t_native_my:.3f}s vs python {t_ref_my:.2f}s  "
          f"({t_ref_my/max(t_native_my,1e-9):.0f}x)  "
          f"match {checked-mism}/{checked} ({100*(checked-mism)/max(checked,1):.1f}%)")

    # ---- 3) end-to-end: native worker vs python worker (same filters) ----
    from stronghold_eyes import _seed_list_maxy_native_worker, _seed_list_eye_worker
    thr = int(np.percentile(out_y[:cnt], 80)) if cnt else 0
    wargs = (seeds.tolist(), 12, thr, mc)   # min_eyes=12, min_max_y=thr
    t = time.perf_counter(); res_nat = sorted(_seed_list_maxy_native_worker(wargs))
    t_wn = time.perf_counter() - t
    t = time.perf_counter(); res_py = sorted(_seed_list_eye_worker(wargs))
    t_wp = time.perf_counter() - t
    same = (res_nat == res_py)
    print(f"[worker e2e] min_max_y={thr}, min_eyes=12 : "
          f"native {len(res_nat)} hits ({t_wn:.2f}s) vs python {len(res_py)} hits "
          f"({t_wp:.2f}s, {t_wp/max(t_wn,1e-9):.0f}x)  identical={same}")

    # ---- 4) 12-eye any-chunk rule + native eye finder vs python ----
    from stronghold_eyes import _seed_list_eye_native_worker, count_stronghold_eyes
    twelve = 0
    for i, s in enumerate(seeds.tolist()):
        if count_stronghold_eyes(int(s), int(ref_x[i]), int(ref_z[i]), mc) == 12:
            twelve += 1
    print(f"[12-eye rule] {twelve}/{N} = {100*twelve/N:.1f}% are 12-eye now "
          f"(was ~17% before the any-chunk rule; should be ~100% for your list)")
    eargs = (seeds.tolist(), 12, None, mc)   # min_eyes=12, no Max Y
    t = time.perf_counter(); res_en = sorted(_seed_list_eye_native_worker(eargs))
    t_en = time.perf_counter() - t
    t = time.perf_counter(); res_ep = sorted(_seed_list_eye_worker(eargs))
    t_ep = time.perf_counter() - t
    same_e = (res_en == res_ep)
    print(f"[eye e2e] min_eyes=12 : native {len(res_en)} hits ({t_en:.2f}s) vs "
          f"python {len(res_ep)} hits ({t_ep:.2f}s, {t_ep/max(t_en,1e-9):.0f}x)  "
          f"identical={same_e}")

    # ---- 5) ALL-128 strongholds worker: native vs python (smaller sample) ----
    from stronghold_eyes import _seed_list_128_worker, _seed_list_128_python
    M = min(200, N)
    a128 = (seeds.tolist()[:M], 12, None, mc)   # min_eyes=12 over ALL strongholds
    t = time.perf_counter(); r128n = sorted(_seed_list_128_worker(a128))
    t_n = time.perf_counter() - t
    t = time.perf_counter(); r128p = sorted(_seed_list_128_python(a128))
    t_p = time.perf_counter() - t
    same_128 = (r128n == r128p)
    seeds_with_12 = len(set(h[0] for h in r128n))
    print(f"[all-128 e2e] min_eyes=12 over {M} seeds : native {len(r128n)} hits "
          f"({t_n:.1f}s) vs python {len(r128p)} ({t_p:.1f}s)  identical={same_128}")
    print(f"   -> {seeds_with_12}/{M} seeds ({100*seeds_with_12/M:.1f}%) have a 12-eye "
          f"stronghold among their 128 (your list should be ~100%)")

    # ---- 6) GPU eye-count vs Python (12-eye determination) ----
    gpu_line = "[GPU eyes] skipped (no CUDA eyes_arr)"
    gpu_ok_flag = True
    try:
        import gaider_cuda as gc
        if gc.has_eyes_arr():
            gcx = (ref_x >> 4).astype(np.int32)
            gcz = (ref_z >> 4).astype(np.int32)
            t = time.perf_counter()
            gpu_e = gc.eyes_arr(seeds, gcx, gcz, mc, 7)
            t_g = time.perf_counter() - t
            gpu12 = (gpu_e == 12)
            py12 = np.array([
                count_stronghold_eyes(int(seeds[i]), int(ref_x[i]), int(ref_z[i]), mc) == 12
                for i in range(N)])
            agree = int(np.sum(gpu12 == py12))
            gpu_ok_flag = (agree == N)
            gpu_line = (f"[GPU eyes] 12-eye GPU vs Python : {agree}/{N} agree "
                        f"({100*agree/N:.2f}%)  GPU {t_g*1000:.1f}ms for {N} positions")
    except Exception as e:
        gpu_line = f"[GPU eyes] error: {e}"
    print(gpu_line)

    print()
    if pos_match == N and mism == 0 and same and same_e and same_128 and gpu_ok_flag:
        print("✓ all natives (pos, maxY, eye, all-128, GPU) match Python — good to use.")
    else:
        print("⚠ mismatch above — paste me the output before using it.")


if __name__ == "__main__":
    main()
