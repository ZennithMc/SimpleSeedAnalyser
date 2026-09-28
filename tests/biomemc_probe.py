"""Child process of gpu_bench --probe: the biome_sample GPU kernel alone.
Prints one line per step (flushed), so a hang shows exactly where it stopped;
after 40 s the Python stacks are dumped (faulthandler)."""
import faulthandler, os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
faulthandler.dump_traceback_later(40, exit=False)


def say(s):
    print(s, flush=True)


def main():
    import ctypes as ct
    import numpy as np
    import custom_search as cs
    import cubiomes_wrap as cw
    import noise_gpu as ng
    say("step load: " + ("kernel present" if ng.has_biomemc() else "NO KERNEL (rebuild noise_gpu)"))
    if not ng.has_biomemc():
        return
    lib = cw.CubiomesLib(); raw = lib._lib; raw.getBiomeAt.restype = ct.c_int
    cond = {"type": "biome_sample", "label": "", "enabled": True, "relative": 0, "instances": 1,
            "params": {"biomes_incl": [185], "coverage_pct": 1.0, "confidence_pct": 95.0,
                       "blocks_scale": "1/4 (fast)"},
            "area_mode": 0, "radial": 1000, "rmax": 1001, "x1": -1000, "z1": -1000,
            "x2": 1000, "z2": 1000}
    arr, _n = cs._build_ssacond_array([cond], raw)
    plan, why = cs.monte_prefix_plan(arr[0])
    say(f"step plan: {plan or why}")
    t = time.perf_counter()
    tb = ng.get_tables(28)
    say(f"step tables: {'ok' if tb else 'FAILED'} ({(time.perf_counter() - t) * 1000:.0f} ms)")
    mask = ng.allowed_bitmask([185])
    base = 9_000_000_000
    for n in (1, 64, 1024, 16384):
        say(f"step kernel {n} seeds: calling…")
        t = time.perf_counter()
        k = ng.biomemc(28, np.arange(base, base + n, dtype=np.int64), plan, mask)
        dt = time.perf_counter() - t
        say(f"step kernel {n} seeds: {dt * 1000:.1f} ms  "
            + (f"{n / dt:,.0f} seeds/s  keeps {int(k.sum())}" if k is not None
               else f"FAILED: {ng.last_error()}"))
        if k is None:
            return
    N = 400
    k = ng.biomemc(28, np.arange(base, base + N, dtype=np.int64), plan, mask)
    miss = extra = 0; ex = []
    incl = {185}
    for i in range(N):
        s = base + i
        lib.setup_and_seed(28, s, False)
        ref = cs.monte_prefix_emul(s, plan, lambda x, y, z: raw.getBiomeAt(lib._gen, 4, x, y, z), incl)
        if ref and not k[i]:
            miss += 1; ex.append(s)
        elif k[i] and not ref:
            extra += 1
    say(f"step exact: {N} seeds · GPU drops {miss} the CPU keeps (FP32 misses) · "
        f"keeps {extra} the CPU drops · {ex[:5]}")


if __name__ == "__main__":
    main()
    faulthandler.cancel_dump_traceback_later()
    os._exit(0)
