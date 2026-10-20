"""(19/10) On a CUDA machine: the GPU spiral scan against its CPU twin, and its speed.
    py tests/ring_gpu_selftest.py ["path/to/session"]
The CPU twin (tests/ring_host.c, the same device code compiled as C) is built
with gcc when missing; without gcc only the speed is shown."""
import sys, os, time, copy, random, subprocess, platform, ctypes as ct
import numpy as np
H = os.path.dirname(os.path.abspath(__file__)); R = os.path.dirname(H); sys.path.insert(0, R)
import custom_search as cs, cubiomes_session as cses, ring_gpu as rg


def host_lib():
    name = "ringhost.dll" if platform.system() == "Windows" else "libringhost.so"
    p = os.path.join(H, name)
    if not os.path.exists(p):
        subprocess.check_call(["gcc", "-O2", "-shared", "-fPIC",
                               "-I" + os.path.join(R, "cubiomes_src"),
                               os.path.join(H, "ring_host.c"), "-o", p, "-lm"])
    L = ct.CDLL(p)
    L.rg_host_scan.restype = ct.c_longlong
    L.rg_host_scan.argtypes = [ct.c_uint64, ct.POINTER(rg.RgSpec), ct.POINTER(ct.c_int32),
                               ct.c_longlong]
    return L


def main():
    # default: a copy of « Dark forest cherry ring STRICT v3 » kept next to this test
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, "ring_selftest.session")
    conds = cses.parse_session_file(path)["conditions"]
    act = cs.active_conditions(copy.deepcopy(conds))
    spec, node, notes = rg.compile_spec(act, 35)
    if spec is None:
        print("not compilable:", notes)
        return
    print("spec:", spec.npt, "point tests,", spec.nor, "ORs", notes or "")
    if not rg.available():
        print("GPU spiral scan NOT available (rebuild CUDA: py tools/build_native.py)")
        return
    st = max(1, spec.step)
    npos = ((spec.x2 // st) - (spec.x1 // st) + 1) * ((spec.z2 // st) - (spec.z1 // st) + 1)
    rng = random.Random(5)
    seeds = np.array([rng.getrandbits(64) for _ in range(64)], np.uint64)
    rg.scan(spec, seeds[:2])                         # warm-up (CUDA context)
    t0 = time.time()
    si, xz = rg.scan(spec, seeds)
    dt = time.time() - t0
    print(f"GPU: 64 seeds x {npos:,} positions in {dt:.2f} s = "
          f"{64 * npos / dt / 1e6:,.0f} M positions/s ({64 / dt:.1f} seeds/s), "
          f"kept {len(si)} ({len(si) / (64 * npos):.4%})")
    try:
        rg._HOST = host_lib()
    except Exception as e:                            # noqa: BLE001
        print("CPU twin skipped (no gcc?):", e)
        return
    gset = {}
    for i, (x, z) in zip(si, xz):
        gset.setdefault(int(i), set()).add((int(x), int(z)))
    miss = tot = 0
    t0 = time.time()
    for i in range(4):
        h = {tuple(p) for p in rg.host_scan(copy.deepcopy(spec), int(seeds[i]))}
        tot += len(h)
        miss += len(h - gset.get(i, set()))
    print(f"CPU twin (4 seeds, {time.time() - t0:.1f} s): {tot} kept, {miss} missing on the GPU "
          f"-> {'OK' if miss <= max(1, tot // 200) else 'TOO MANY: tell Claude'}")


main()
