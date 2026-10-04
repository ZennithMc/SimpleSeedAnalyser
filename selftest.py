"""
selftest.py — quick check that an installed SSA works on this PC.

    SimpleSeedAnalyser.exe --selftest        (or: py ssa_qt.py --selftest)

Checks the native libraries with known answers (biomes, the session engine,
eyes of ender, exact Y), that worker processes start (frozen multiprocessing)
and reports the GPU state. The report goes to the console, to
%LOCALAPPDATA%\\SimpleSeedAnalyser\\selftest.txt (~/.local/state/SimpleSeedAnalyser on Linux)
and, when possible, to a message box. Exit code 0 = everything passed.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback


def _abs(v):                       # picklable job for the process-pool check
    return abs(v)


def _cond(t, rel=0, r=0, **params):
    c = {"type": t, "label": "", "enabled": True, "relative": rel, "params": params, "instances": 1}
    if r:
        c.update(area_mode=0, radial=r, rmax=r + 1, x1=-r, z1=-r, x2=r, z2=r)
    return c


def run() -> tuple[bool, str]:
    lines, ok_all = [], True

    def check(name, fn):
        nonlocal ok_all
        t = time.time()
        try:
            ok, detail = fn()
        except Exception as e:                      # noqa: BLE001 — reported
            ok, detail = False, f"{type(e).__name__}: {e}"
            traceback.print_exc()
        ok_all &= ok
        lines.append(f"{'OK  ' if ok else 'FAIL'}  {name:<28} {detail}  ({time.time() - t:.1f} s)")
        print(lines[-1], flush=True)

    import cubiomes_wrap as cw
    mc = cw.MC_VERSIONS["Java 1.21"]

    def biomes():
        lib = cw.make_worker_lib(); lib.setup_and_seed(mc, 1)
        got = (lib.get_biome_at(0, 0, scale=1), lib.get_biome_at(1000, -2000, scale=4))
        return got == (24, 46), f"seed 1 biomes {got}"

    def session():
        import custom_search as cs
        conds = [_cond("village", r=600),
                 _cond("climate_minmax", rel=1, r=64, para="Temperature", range=["2000", None], nmax=2)]
        found = set()
        cs.evaluate_session(conds, 1000, 50_000, mc, max(1, min(8, os.cpu_count() or 1)), threading.Event(),
                            on_match=lambda s, d="": found.add(int(s)), on_progress=lambda d, t: None,
                            search_mode="inc")
        ref = SESSION_REF
        return len(found) == ref, f"{len(found)} seeds (expected {ref})"

    def workers():
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=2) as ex:
            got = list(ex.map(_abs, [-1, -2, -3]))
        return got == [1, 2, 3], "2 worker processes"

    def eyes():
        import stronghold_eyes as se
        seed, mc19 = -4770284401290009883, 23
        lib = cw.CubiomesLib(); lib.setup_and_seed(mc19, seed)
        shs = lib.find_strongholds(max_count=128)
        sx, sz = min(shs, key=lambda p: (p[0] - 18479) ** 2 + (p[1] + 10306) ** 2)
        got = se.count_stronghold_eyes(seed, sx, sz, mc19)
        return got == 1, f"eyes {got} (in game: 1)"

    def layouts():
        import gaider_layouts as gl
        ok = gl.is_available()
        return ok, "stronghold layout generator loaded" if ok else str(gl.get_load_error())

    def exact_y():
        import terrain
        e = terrain.TerrainEngine(1, mc)
        got = (e.exact_y(0, 0), e.exact_y(1234, -567))
        return got == (41, 63), f"surface Y {got}"

    check("Biome generation", biomes)
    check("Session engine (C)", session)
    check("Worker processes", workers)
    check("Eyes of ender", eyes)
    check("Stronghold layouts", layouts)
    check("Exact Y (terrain)", exact_y)
    try:
        import native_libs as nl
        for unit in ("structure_cuda", "noise_gpu", "gaider_cuda"):
            lib, msg = nl.load_cuda(unit)
            lines.append(f"{'GPU ' if lib is not None else 'cpu '}  {unit:<28} {msg}")
            print(lines[-1], flush=True)
    except Exception as e:                          # noqa: BLE001
        lines.append(f"cpu   GPU check                    {e}")
    head = "SimpleSeedAnalyser self-test: " + ("ALL OK" if ok_all else "PROBLEMS FOUND")
    return ok_all, head + "\n\n" + "\n".join(lines)


# seeds 1000 … 50 999, Java 1.21, village ≤ 600 + temperature ≥ 2000 next to it:
# measured with the validated engine (the same on every PC, any thread count)
SESSION_REF = 10911


def main() -> int:
    ok, report = run()
    try:
        import ssa_paths
        d = ssa_paths.log_dir()
        with open(os.path.join(d, "selftest.txt"), "w", encoding="utf-8") as f:
            f.write(report + "\n")
        report += f"\n\n(saved to {os.path.join(d, 'selftest.txt')})"
    except OSError:
        pass
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv[:1])
        box = QMessageBox(QMessageBox.Icon.Information if ok else QMessageBox.Icon.Warning,
                          "SimpleSeedAnalyser self-test", report.split("\n\n")[0])
        box.setDetailedText(report)
        box.setInformativeText("Everything works." if ok else
                               "Something failed — send selftest.txt to the author.")
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            box.exec()
    except Exception:                               # noqa: BLE001 — no GUI: console only
        pass
    return 0 if ok else 1
