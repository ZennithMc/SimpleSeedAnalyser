#!/usr/bin/env python3
"""
tools/build_native.py — build UNIQUE des DLL CUDA de SSA (poste de développement).

    py tools/build_native.py              reconstruit toutes les unités CUDA (+ terrain)
    py tools/build_native.py --unit X     une seule (gaider_cuda, structure_cuda, noise_gpu)
    py tools/build_native.py --check      indique seulement ce qui est à jour / à refaire
    py tools/build_native.py --cubiomes   reconstruit libcubiomes.dll (moteur C, gcc)
                                          — SSA doit être fermé

Chaque DLL est compilée UNE fois pour toutes les cartes supportées (fatbin) :
  sm_75 (RTX 20 / GTX 16), sm_80 (A100), sm_86 (RTX 30), sm_89 (RTX 40),
  sm_120 (RTX 50) + PTX compute_120 (cartes futures, compilé par le pilote).
Validé sur RTX 5080 : résultats et performances identiques à l'ancienne DLL sm_120.

Le build se fait dans un fichier temporaire puis remplace la DLL d'un coup ;
si SSA tourne encore (DLL verrouillée), l'ancienne DLL reste intacte.
Le résultat est décrit dans native_build.json (lu par native_libs.py).
Utilisateur final : n'a besoin que du pilote NVIDIA (≥ 580), rien d'autre.
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
        return False, f"nvcc a échoué ({dt:.0f} s) :\n{log[-3000:]}"
    try:
        os.replace(tmp, out)
    except OSError as e:
        os.remove(tmp)
        return False, (f"impossible de remplacer {os.path.basename(out)} ({e}) — "
                       f"ferme SSA puis relance le build")
    return True, f"{os.path.basename(out)} construite en {dt:.0f} s ({os.path.getsize(out)//1024} Ko)"


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
            print(f"[FAIL] {os.path.basename(out)} verrouillée (SSA encore ouvert ?) : {e}")
            return 1
    t0 = time.time()
    try:
        cw._compile_cubiomes()
        ok = cw._is_valid_lib(out)
    except Exception as e:
        print(f"[FAIL] libcubiomes : {e}")
        ok = False
    if not ok:
        if os.path.exists(old):
            os.replace(old, out)
        print("[FAIL] libcubiomes : ancienne bibliothèque restaurée")
        return 1
    print(f"[ OK ] {os.path.basename(out)} reconstruite en {time.time() - t0:.0f} s "
          f"(ancienne : {os.path.basename(old)})")
    return 0


def main():
    args = sys.argv[1:]
    if "--cubiomes" in args:
        return build_cubiomes()
    units = list(MAIN_SRC)
    if "--unit" in args:
        u = args[args.index("--unit") + 1]
        if u not in MAIN_SRC:
            print(f"unité inconnue : {u} (choix : {', '.join(MAIN_SRC)})")
            return 2
        units = [u]
    if "--check" in args:
        for u in units:
            st, why = nl.build_state(u)
            print(f"{u:<15} {st:<8} {why}")
        return 0
    nvcc = nvcc_version()
    if nvcc is None:
        print("nvcc introuvable : installe le CUDA Toolkit 13.x et ouvre un nouveau terminal.")
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
        print(f"[build] {nl.MANIFEST_NAME} mis à jour")
    if "--unit" not in args:
        # Terrain (Exact-Y) : encore construit par terrain.py (gcc pour terrain.dll,
        # nvcc pour terrain_gpu.dll) — ne reconstruit que si les sources ont changé.
        try:
            import terrain
            p = terrain._compile()
            print(("[ OK ] " if p else "[FAIL] ") + f"terrain (CPU) : {p or terrain._ERR[:800]}")
            if p is None:
                failed.append("terrain")
            g = terrain._compile_load_gpu()
            print(("[ OK ] " if g else "[WARN] ") + "terrain_gpu (carte Exact-Y) : "
                  + ("prêt" if g else "non construit (la carte utilisera le CPU)"))
        except Exception as e:
            print(f"[FAIL] terrain : {e}")
            failed.append("terrain")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
