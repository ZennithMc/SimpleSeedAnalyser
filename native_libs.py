"""
native_libs.py — chargement unique, sûr et diagnostiquable des DLL CUDA de SSA.

Remplace les anciennes compilations « au premier lancement » dispersées dans
gaider_cuda.py / structure_cuda.py / noise_gpu.py, qui :
  * exigeaient nvcc + MSVC chez l'utilisateur,
  * produisaient des DLL pour UNE seule carte (celle de la machine de build),
  * se déclenchaient selon des dates de fichiers (fragile après un dézippage),
  * supprimaient parfois la DLL après un échec de chargement.

Règles désormais :
  1. Les DLL sont produites par tools/build_native.py (fatbin RTX 20 → 50) et
     décrites dans native_build.json (empreinte des sources, version nvcc, archis).
  2. Au lancement, SSA ne compile JAMAIS chez un utilisateur. Seul un poste de
     développement (sources présentes + nvcc sur le PATH, application non figée
     en .exe) reconstruit automatiquement une DLL dont les sources ont changé.
     SSA_NO_AUTOBUILD=1 désactive même cela.
  3. Une DLL n'est déclarée utilisable qu'après ssa_cuda_selftest() : un vrai
     kernel lancé et vérifié. Une carte non supportée → GPU indisponible avec la
     raison exacte, et les recherches passent par le CPU.
  4. Aucune DLL n'est jamais supprimée ni remplacée par ce module.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading

IS_WIN = platform.system() == "Windows"
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
MANIFEST_NAME = "native_build.json"

# Unités CUDA : fichier produit + sources dont dépend le binaire.
CUDA_UNITS = {
    "gaider_cuda": {
        "file": "gaider_cuda.dll" if IS_WIN else "libgaider_cuda.so",
        "sources": ["cubiomes_src/gaider_cuda.cu", "cubiomes_src/ssa_cuda_common.cuh"],
    },
    "structure_cuda": {
        "file": "structure_cuda.dll" if IS_WIN else "libstructure_cuda.so",
        "sources": ["cubiomes_src/structure_cuda.cu", "cubiomes_src/ssa_cuda_common.cuh"],
    },
    "noise_gpu": {
        "file": "noise_gpu.dll" if IS_WIN else "libnoise_gpu.so",
        "sources": ["cubiomes_src/noise_gpu.cu", "cubiomes_src/noise_gpu_kernel.cuh",
                    "cubiomes_src/ssa_cuda_common.cuh"],
    },    "topy_gpu": {                     # 1.13-1.17 stronghold Top Y (03/10)
        "file": "topy_gpu.dll" if IS_WIN else "libtopy_gpu.so",
        "sources": ["cubiomes_src/topy_gpu.cu", "cubiomes_src/topy_gpu_gen.cuh",
                    "cubiomes_src/ssa_cuda_common.cuh"],
    },
}

_LOCK = threading.Lock()
_CACHE: dict = {}          # unit -> (lib | None, message)


def sources_hash(unit: str) -> str | None:
    """Empreinte SHA-256 des sources d'une unité (None si une source manque)."""
    h = hashlib.sha256()
    for rel in CUDA_UNITS[unit]["sources"]:
        p = os.path.join(APP_DIR, rel)
        if not os.path.exists(p):
            return None
        h.update(rel.encode())
        with open(p, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def read_manifest() -> dict:
    try:
        with open(os.path.join(APP_DIR, MANIFEST_NAME), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def dll_path(unit: str) -> str:
    return os.path.join(APP_DIR, CUDA_UNITS[unit]["file"])


def build_state(unit: str) -> tuple[str, str]:
    """('ok'|'stale'|'unknown'|'missing', explication)."""
    p = dll_path(unit)
    if not os.path.exists(p):
        return "missing", f"{os.path.basename(p)} missing"
    ent = read_manifest().get("units", {}).get(unit)
    if not ent:
        return "unknown", "old library without a build record (native_build.json)"
    cur = sources_hash(unit)
    if cur is None:          # distribution sans sources (exe) : on fait confiance au build
        return "ok", "release build"
    if cur != ent.get("sources_sha256"):
        return "stale", "sources changed since the last build"
    return "ok", f"built {ent.get('built_at', '?')} (nvcc {ent.get('nvcc', '?')})"


def dev_autobuild_allowed() -> bool:
    if FROZEN or os.environ.get("SSA_NO_AUTOBUILD") == "1":
        return False
    return (shutil.which("nvcc") is not None
            and os.path.exists(os.path.join(APP_DIR, "tools", "build_native.py")))


def _run_build(unit: str) -> tuple[bool, str]:
    script = os.path.join(APP_DIR, "tools", "build_native.py")
    print(f"[native] Rebuilding {unit} (dev machine, sources changed)…", flush=True)
    try:
        r = subprocess.run([sys.executable, script, "--unit", unit],
                           capture_output=True, timeout=1800)
    except Exception as e:
        return False, f"build impossible : {e}"
    log = ((r.stdout or b"") + (r.stderr or b"")).decode(errors="replace")
    if r.returncode != 0:
        return False, "échec du build :\n" + log[-2500:]
    print(log.strip()[-600:], flush=True)
    return True, "reconstruite"


def _bind_common(lib):
    for name, res, args in (("ssa_cuda_selftest", ctypes.c_int, []),
                            ("ssa_cuda_last_error", ctypes.c_char_p, []),
                            ("ssa_cuda_last_error_code", ctypes.c_int, []),
                            ("ssa_cuda_device_info", ctypes.c_int, [ctypes.c_char_p, ctypes.c_int])):
        fn = getattr(lib, name, None)
        if fn is None:
            return False
        fn.restype = res
        fn.argtypes = args
    return True


def last_error(lib) -> str:
    """Texte de la dernière erreur CUDA enregistrée par la DLL."""
    try:
        s = lib.ssa_cuda_last_error()
        return s.decode("utf-8", "replace") if s else ""
    except Exception:
        return ""


def device_info(lib) -> str:
    try:
        buf = ctypes.create_string_buffer(256)
        if lib.ssa_cuda_device_info(buf, 256) == 0:
            name, cc, vram, drv = buf.value.decode("utf-8", "replace").split("|")
            return f"{name} (cc {cc}, {vram} Mo, driver CUDA {drv})"
    except Exception:
        pass
    return "?"


def load_cuda(unit: str):
    """Charge + auto-teste une DLL CUDA. Retourne (lib | None, message).
    Mis en cache : un seul essai par processus, thread-safe."""
    with _LOCK:
        if unit in _CACHE:
            return _CACHE[unit]
        res = _load_cuda_impl(unit)
        _CACHE[unit] = res
        tag = "OK" if res[0] is not None else "unavailable"
        print(f"[native] {unit}: {tag} — {res[1]}", flush=True)
        return res


def _load_cuda_impl(unit: str):
    state, why = build_state(unit)
    if state in ("stale", "unknown", "missing") and dev_autobuild_allowed():
        ok, msg = _run_build(unit)
        if not ok:
            if state == "missing":
                return None, msg
            print(f"[native] {unit}: {msg}\n[native] → keeping the existing library", flush=True)
        state, why = build_state(unit)
    p = dll_path(unit)
    if not os.path.exists(p):
        return None, (f"{os.path.basename(p)} not found — GPU off for this feature "
                      f"(searches run on the CPU)")
    try:
        lib = ctypes.CDLL(p)
    except OSError as e:
        return None, f"cannot load {os.path.basename(p)}: {e}"
    if not _bind_common(lib):
        return None, (f"{os.path.basename(p)} is an old library without self-test "
                      f"(rebuild with: py tools/build_native.py)")
    rc = lib.ssa_cuda_selftest()
    if rc != 0:
        err = last_error(lib) or f"code {rc}"
        hint = ""
        if "no kernel image" in err.lower() or "NoKernelImage" in err:
            hint = " — this library has no code for your graphics card"
        elif "insufficient" in err.lower() or "driver" in err.lower():
            hint = (" — no NVIDIA card, or the NVIDIA driver is missing / too old "
                    "(driver 580 or newer needed)")
        elif "no cuda-capable" in err.lower() or "nodevice" in err.lower():
            hint = " — no NVIDIA card found"
        return None, f"GPU self-test failed: {err}{hint}"
    msg = f"{device_info(lib)} ; {why}"
    if state == "stale":
        msg += " ⚠ sources changed, library not rebuilt"
    return lib, msg


def dbg(msg: str) -> None:
    """Developer trace (GPU pipelines, Exact Y): printed only with SSA_DEBUG=1,
    so a normal console shows errors and load reports only."""
    if os.environ.get("SSA_DEBUG", "") not in ("", "0"):
        print(msg, file=sys.stderr, flush=True)


# CUDA errors after which the context is unusable until the process restarts:
# every later call would fail too, so the unit is switched off (CPU fallback)
# instead of failing — and printing — on every chunk.
_STICKY = ("illegaladdress", "illegal memory access", "launchfailure", "unspecified launch failure",
           "misaligned", "illegalinstruction", "hardwarestackerror", "ecc", "invalidpc",
           "launch timed out", "launchtimeout", "cudaerrorassert", "device-side assert")


def is_sticky(err: str) -> bool:
    e = (err or "").lower().replace(" ", "")
    return any(k.replace(" ", "") in e for k in _STICKY)


class GpuError(RuntimeError):
    """Erreur CUDA remontée par une DLL (jamais avalée en silence)."""


def check(lib, rc, what: str):
    """Lève GpuError si rc < 0, avec le message CUDA exact."""
    if rc is None or rc < 0:
        raise GpuError(f"{what} : {last_error(lib) or 'erreur CUDA'}")
    return rc
