#!/usr/bin/env python3
"""
tools/build_native.py — builds SSA's native libraries (development machine).

    py tools/build_native.py              rebuild every CUDA unit (+ terrain)
    py tools/build_native.py --unit X     one unit only (gaider_cuda, structure_cuda, noise_gpu, topy_gpu, tree_cuda)
    py tools/build_native.py --check      only say what is up to date / to rebuild
    py tools/build_native.py --cubiomes   rebuild libcubiomes (C engine, gcc)
                                          + gaider_layouts (strongholds, g++)
                                          + treecrack (Seedcracking, g++)
                                          + lootvm (loot finder, gcc)
                                          — close SSA first
    py tools/build_native.py --layouts    rebuild gaider_layouts only
    py tools/build_native.py --lootvm     rebuild lootvm only

Each CUDA library is compiled ONCE for every supported card (fatbin):
  sm_75 (RTX 20 / GTX 16), sm_80 (A100), sm_86 (RTX 30), sm_89 (RTX 40),
  sm_120 (RTX 50) + PTX compute_120 (future cards, compiled by the driver).

The build goes to a temporary file that then replaces the library in one step;
if SSA is still running (library locked), the old library stays untouched.
The result is described in native_build.json (read by native_libs.py).
End users only need the NVIDIA driver (580+), nothing else.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
import native_libs as nl  # noqa: E402

FATBIN = ["-gencode", "arch=compute_75,code=sm_75",
          "-gencode", "arch=compute_80,code=sm_80",
          "-gencode", "arch=compute_86,code=sm_86",
          "-gencode", "arch=compute_89,code=sm_89",
          "-gencode", "arch=compute_120,code=sm_120",
          "-gencode", "arch=compute_120,code=compute_120"]
ARCH_LABEL = "sm_75,sm_80,sm_86,sm_89,sm_120 + PTX compute_120"

MAIN_SRC = {  # fichier compilé par nvcc (les autres sources sont des #include)
    "gaider_cuda": "cubiomes_src/gaider_cuda.cu",
    "structure_cuda": "cubiomes_src/structure_cuda.cu",
    "noise_gpu": "cubiomes_src/noise_gpu.cu",
    "topy_gpu": "cubiomes_src/topy_gpu.cu",
    "tree_cuda": "cubiomes_src/tree_cuda.cu",
}


def nvcc_version():
    try:
        o = subprocess.run(["nvcc", "--version"], capture_output=True, timeout=30).stdout.decode()
        m = re.search(r"V(\d+\.\d+\.\d+)", o)
        return m.group(1) if m else "?"
    except Exception:
        return None


def build_unit(unit: str) -> tuple[bool, str]:
    out = nl.dll_path(unit)
    _root, _ext = os.path.splitext(out)
    tmp = _root + ".building" + _ext          # ex. gaider_cuda.building.dll
    src = os.path.join(APP, MAIN_SRC[unit])
    cmd = ["nvcc", "-O3", "-shared"] + FATBIN
    if not nl.IS_WIN:
        cmd += ["-Xcompiler", "-fPIC"]
    cmd += [src, "-o", tmp]
    print(f"[build] {unit} … ({ARCH_LABEL})", flush=True)
    t = time.time()
    r = subprocess.run(cmd, capture_output=True, cwd=os.path.dirname(src))
    dt = time.time() - t
    # nvcc (Windows) laisse parfois .lib/.exp à côté : on les retire du dossier.
    for ext in (".lib", ".exp"):
        side = os.path.splitext(tmp)[0] + ext
        if os.path.exists(side):
            try:
                os.remove(side)
            except OSError:
                pass
    if r.returncode != 0 or not os.path.exists(tmp):
        log = ((r.stderr or b"") + (r.stdout or b"")).decode(errors="replace")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, f"nvcc failed ({dt:.0f} s):\n{log[-3000:]}"
    try:
        os.replace(tmp, out)
    except OSError as e:
        os.remove(tmp)
        return False, (f"cannot replace {os.path.basename(out)} ({e}) — "
                       f"close SSA and run the build again")
    return True, f"{os.path.basename(out)} built in {dt:.0f} s ({os.path.getsize(out)//1024} KB)"


def write_manifest(done: dict, nvcc: str):
    path = os.path.join(APP, nl.MANIFEST_NAME)
    man = nl.read_manifest()
    man.setdefault("units", {})
    man["format"] = 1
    for unit in done:
        man["units"][unit] = {
            "file": nl.CUDA_UNITS[unit]["file"],
            "sources_sha256": nl.sources_hash(unit),
            "nvcc": nvcc,
            "archs": ARCH_LABEL,
            "built_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(man, f, indent=2, ensure_ascii=False)


def build_cubiomes() -> int:
    """Rebuild libcubiomes (C engine: session_engine.c, finders, bridges).
    The old library is kept as .old and restored if the build fails."""
    import cubiomes_wrap as cw
    out = os.path.join(APP, cw._lib_name())
    old = out + ".old"
    if os.path.exists(out):
        try:
            if os.path.exists(old):
                os.remove(old)
            os.replace(out, old)
        except OSError as e:
            print(f"[FAIL] {os.path.basename(out)} is locked (is SSA still open?): {e}")
            return 1
    t0 = time.time()
    try:
        cw._compile_cubiomes()
        ok = cw._is_valid_lib(out)
    except Exception as e:
        print(f"[FAIL] libcubiomes: {e}")
        ok = False
    if not ok:
        if os.path.exists(old):
            os.replace(old, out)
        print("[FAIL] libcubiomes: the old library was restored")
        return 1
    print(f"[ OK ] {os.path.basename(out)} rebuilt in {time.time() - t0:.0f} s "
          f"(ancienne : {os.path.basename(old)})")
    return 0


def build_layouts() -> int:
    """Rebuild gaider_layouts (C++ stronghold generator: eyes, layouts, Top Y,
    portal frames). The old library is kept as .old and restored on failure."""
    import gaider_layouts as gl
    out = os.path.join(APP, gl._lib_name())
    old = out + ".old"
    if os.path.exists(out):
        try:
            if os.path.exists(old):
                os.remove(old)
            os.replace(out, old)
        except OSError as e:
            print(f"[FAIL] {os.path.basename(out)} is locked (is SSA still open?): {e}")
            return 1
    t0 = time.time()
    p = gl._compile()
    ok = p is not None and os.path.exists(out)
    if ok:
        try:
            import ctypes
            ctypes.CDLL(out).max_y_batch_arr      # loads and has the Top Y entry point
        except Exception as e:
            print(f"[FAIL] gaider_layouts: {e}")
            ok = False
    if not ok:
        if os.path.exists(out):
            try:
                os.replace(out, out + ".bad")
            except OSError:
                pass
        if os.path.exists(old):
            os.replace(old, out)
        print(f"[FAIL] gaider_layouts: {gl._LIB_LOAD_ERROR or 'build failed'} — "
              "the old library was restored")
        return 1
    print(f"[ OK ] {os.path.basename(out)} rebuilt in {time.time() - t0:.0f} s "
          f"(ancienne : {os.path.basename(old)})")
    return 0


def build_treecrack() -> int:
    """(05/11) Rebuild treecrack (Seedcracking, CPU side: g++)."""
    import tree_crack as tc
    t0 = time.time()
    p = tc._compile()
    if p is None:
        print(f"[FAIL] {tc._lib_name()}: {tc._LIB_ERR}")
        return 1
    print(f"[ OK ] {os.path.basename(p)} rebuilt in {time.time() - t0:.0f} s")
    return 0


def build_lootvm() -> int:
    """(07/11) Rebuild lootvm (the loot finder's chests in C: gcc)."""
    import loot_native as ln
    t0 = time.time()
    p = ln._compile()
    if p is None:
        print(f"[FAIL] {ln._lib_name()}: {ln._LIB_ERR}")
        return 1
    print(f"[ OK ] {os.path.basename(p)} rebuilt in {time.time() - t0:.0f} s")
    return 0


def main():
    args = sys.argv[1:]
    if "--layouts" in args:
        return build_layouts()
    if "--lootvm" in args:
        return build_lootvm()
    if "--cubiomes" in args:
        r = build_cubiomes()
        r = build_layouts() or r
        r = build_lootvm() or r
        return build_treecrack() or r
    units = list(MAIN_SRC)
    if "--unit" in args:
        u = args[args.index("--unit") + 1]
        if u not in MAIN_SRC:
            print(f"unknown unit: {u} (choose from: {', '.join(MAIN_SRC)})")
            return 2
        units = [u]
    if "--check" in args:
        for u in units:
            st, why = nl.build_state(u)
            print(f"{u:<15} {st:<8} {why}")
        return 0
    nvcc = nvcc_version()
    if nvcc is None:
        print("nvcc not found: install the CUDA Toolkit 13.x and open a new terminal.")
        return 2
    print(f"[build] nvcc {nvcc}")
    done, failed = {}, []
    for u in units:
        ok, msg = build_unit(u)
        print(("[ OK ] " if ok else "[FAIL] ") + msg, flush=True)
        if ok:
            done[u] = True
        else:
            failed.append(u)
    if done:
        write_manifest(done, nvcc)
        print(f"[build] {nl.MANIFEST_NAME} updated")
    if "--unit" not in args:
        # Terrain (Exact-Y) : encore construit par terrain.py (gcc pour terrain.dll,
        # nvcc pour terrain_gpu.dll) — ne reconstruit que si les sources ont changé.
        try:
            import terrain
            p = terrain._compile()
            print(("[ OK ] " if p else "[FAIL] ") + f"terrain (CPU): {p or terrain._ERR[:800]}")
            if p is None:
                failed.append("terrain")
            g = terrain._compile_load_gpu()
            print(("[ OK ] " if g else "[WARN] ") + "terrain_gpu (Exact-Y map): "
                  + ("ready" if g else "not built (the map will use the CPU)"))
        except Exception as e:
            print(f"[FAIL] terrain: {e}")
            failed.append("terrain")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
