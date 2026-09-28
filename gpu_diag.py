#!/usr/bin/env python3
"""
gpu_diag.py — Diagnostic GPU/CUDA de SSA (LECTURE SEULE).

Usage (depuis le dossier de SSA, à côté de gui.py) :
    py gpu_diag.py                 diagnostic standard (~1-2 min)
    py gpu_diag.py --build-test    + compilation d'essai « fatbin » (gaider, structure)
                                     dans un dossier TEMPORAIRE, comparaison de perfs et
                                     simulation d'une carte non supportée (~1 min)
    py gpu_diag.py --only terrain  une seule section (dll, gaider, struct, surviv,
                                     noise, biome128, terrain, build) ; rapport séparé
                                     gpu_diag_report_<sections>.txt
    Ctrl+C à tout moment : le rapport partiel est conservé.

Garanties :
  * ne compile RIEN dans le dossier de SSA, ne supprime ni ne remplace aucune DLL
    (les fonctions de compilation/suppression des modules sont neutralisées) ;
  * le seul fichier écrit est le rapport : gpu_diag_report.txt (dossier de SSA) ;
  * chaque lancement GPU est borné (~250 ms) pour ne pas déclencher le watchdog
    Windows (TDR) sur ta machine.

Ce que le rapport contient :
  [ENV]      Windows, Python, CPU (AVX2), GPU, driver, TDR, outils (nvcc, cuobjdump…)
  [DLL]      pour chaque DLL : version CUDA, architectures embarquées, et si SSA
             tenterait une recompilation au lancement (et pourquoi)
  [GAIDER]   yeux de l'end portal : GPU vs CPU graine par graine (1.17 et 1.21)
  [STRUCT]   filtre 48-bit des structures : GPU vs cubiomes getStructurePos
  [NOISE]    biomes 1.18+ FP32 : GPU vs cubiomes (écart de comptage)
  [BIOME128] 128 strongholds 1.13-1.17 : GPU vs cubiomes
  [TERRAIN]  surface Y : GPU FP32 vs CPU exact
  [TIMING]   durée d'un lot de production par kernel → risque TDR sur cartes plus lentes
  [SURVIV]   démonstration de la troncature des survivants (out_cap)
  [BUILD]    (option) build fatbin : compile ? archis ? perfs identiques ?
             + simulation « no kernel image » (ce que vit une carte non supportée)
"""
from __future__ import annotations

import ctypes
import datetime
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
IS_WIN = platform.system() == "Windows"
REPORT_PATH = os.path.join(HERE, "gpu_diag_report.txt")
LAUNCH_BUDGET_S = 0.25          # durée max d'un lancement pendant les mesures
TDR_S = 2.0                     # watchdog Windows par défaut

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


# ═════════════════════════════════════════════════════════════════════════════
# Rapport
# ═════════════════════════════════════════════════════════════════════════════
class Report:
    def __init__(self):
        self.lines = []
        self.summary = []          # (tag, section, message)
        self.section_name = ""

    def _emit(self, s):
        print(s, flush=True)
        self.lines.append(s)

    def section(self, name, title):
        self.section_name = name
        self._emit("")
        self._emit("=" * 78)
        self._emit(f"[{name}] {title}")
        self._emit("=" * 78)

    def info(self, msg):
        self._emit(f"  [INFO] {msg}")

    def ok(self, msg):
        self._emit(f"  [ OK ] {msg}")
        self.summary.append(("OK", self.section_name, msg))

    def warn(self, msg):
        self._emit(f"  [WARN] {msg}")
        self.summary.append(("WARN", self.section_name, msg))

    def fail(self, msg):
        self._emit(f"  [FAIL] {msg}")
        self.summary.append(("FAIL", self.section_name, msg))

    def raw(self, msg):
        for ln in str(msg).rstrip().splitlines():
            self._emit("         " + ln)

    def save(self):
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))
            f.write("\n\n" + "=" * 78 + "\nRÉSUMÉ\n" + "=" * 78 + "\n")
            for tag, sec, msg in self.summary:
                if tag != "OK":
                    f.write(f"[{tag}] {sec}: {msg}\n")
            n_ok = sum(1 for t, _, _ in self.summary if t == "OK")
            n_w = sum(1 for t, _, _ in self.summary if t == "WARN")
            n_f = sum(1 for t, _, _ in self.summary if t == "FAIL")
            f.write(f"\nOK={n_ok}  WARN={n_w}  FAIL={n_f}\n")


R = Report()


def guarded(fn):
    """Exécute un test ; toute exception est consignée sans arrêter le diag."""
    def wrap(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            R.fail(f"{fn.__name__} a levé {type(e).__name__}: {e}")
            R.raw(traceback.format_exc())
            return None
    wrap.__name__ = fn.__name__
    return wrap


def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout or b"").decode(errors="replace"), \
            (r.stderr or b"").decode(errors="replace")
    except FileNotFoundError:
        return None, "", "not found"
    except subprocess.TimeoutExpired:
        return None, "", "timeout"


# ═════════════════════════════════════════════════════════════════════════════
# Neutralisation : aucun module ne doit compiler / supprimer quoi que ce soit
# ═════════════════════════════════════════════════════════════════════════════
def _no_compile(*_a, **_k):
    raise RuntimeError("gpu_diag: compilation désactivée (diagnostic lecture seule)")


def neutralize_modules():
    import cubiomes_wrap
    cubiomes_wrap._compile_cubiomes = _no_compile
    try:
        import noise_gpu
        noise_gpu._compile = lambda: None          # → utilise la DLL existante telle quelle
    except Exception:
        pass
    try:
        import biome_gpu
        biome_gpu._compile = lambda: None
    except Exception:
        pass
    try:
        import terrain
        _tp = os.path.join(HERE, terrain._lib_name())
        terrain._compile = (lambda: _tp) if os.path.exists(_tp) else (lambda: None)
        terrain._compile_load_gpu = lambda: None   # le GPU terrain est lié à la main ici
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
# ENV
# ═════════════════════════════════════════════════════════════════════════════
def cpu_name():
    if IS_WIN:
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except Exception:
            pass
    return platform.processor() or "?"


def has_avx2():
    if IS_WIN:
        try:
            return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))
        except Exception:
            return None
    try:
        with open("/proc/cpuinfo") as f:
            return " avx2" in f.read()
    except Exception:
        return None


def tdr_delay():
    if not IS_WIN:
        return None
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers")
        return winreg.QueryValueEx(k, "TdrDelay")[0]
    except Exception:
        return None


def driver_api_version():
    try:
        lib = ctypes.WinDLL("nvcuda.dll") if IS_WIN else ctypes.CDLL("libcuda.so.1")
        v = ctypes.c_int(0)
        if lib.cuDriverGetVersion(ctypes.byref(v)) == 0:
            return v.value
    except Exception:
        pass
    return None


GPU = {}


@guarded
def section_env():
    R.section("ENV", "Système, GPU, driver, outils")
    import numpy
    R.info(f"Date          : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    R.info(f"OS            : {platform.platform()}")
    R.info(f"Python        : {sys.version.split()[0]} ({platform.architecture()[0]})  numpy {numpy.__version__}")
    R.info(f"CPU           : {cpu_name()}  — {os.cpu_count()} threads logiques")
    a = has_avx2()
    if a is False:
        R.fail("CPU SANS AVX2 : libcubiomes.dll (compilée -mavx2) plantera ce PC (C1)")
    elif a:
        R.ok("CPU avec AVX2 (libcubiomes -mavx2 OK sur ce PC)")
    rc, out, err = run(["nvidia-smi", "--query-gpu=name,compute_cap,driver_version,"
                        "memory.total,memory.free", "--format=csv,noheader,nounits"])
    if rc == 0 and out.strip():
        parts = [p.strip() for p in out.strip().splitlines()[0].split(",")]
        GPU.update(name=parts[0], cc=parts[1], driver=parts[2],
                   vram_total=parts[3], vram_free=parts[4])
        R.info(f"GPU           : {parts[0]}  (compute capability {parts[1]})")
        R.info(f"Driver        : {parts[2]}   VRAM {parts[4]} / {parts[3]} MiB libres")
        if len(out.strip().splitlines()) > 1:
            R.info(f"{len(out.strip().splitlines())} GPU détectés — SSA utilise le device 0")
    else:
        R.fail(f"nvidia-smi indisponible ({err.strip() or rc}) — pas de GPU NVIDIA ?")
    dv = driver_api_version()
    if dv:
        R.info(f"CUDA driver API: {dv // 1000}.{(dv % 1000) // 10} (version max supportée par ce driver)")
        GPU["driver_api"] = dv
    t = tdr_delay()
    R.info(f"TdrDelay      : {t if t is not None else 'non défini → défaut Windows 2 s'}")
    for tool in ("nvcc", "cuobjdump", "cl", "gcc"):
        p = shutil.which(tool)
        extra = ""
        if p and tool == "nvcc":
            _, o, _ = run(["nvcc", "--version"])
            m = re.search(r"release ([\d.]+), V([\d.]+)", o)
            extra = f"  (CUDA {m.group(2)})" if m else ""
            GPU["nvcc"] = m.group(1) if m else "?"
        R.info(f"{tool:<13} : {p or 'absent'}{extra}")
    if not shutil.which("nvcc"):
        R.info("(normal pour un utilisateur final — c'est justement ce que SSA ne doit plus exiger)")


# ═════════════════════════════════════════════════════════════════════════════
# DLL : version CUDA, architectures, déclencheurs de recompilation
# ═════════════════════════════════════════════════════════════════════════════
DLLS = {
    # nom                : (sources surveillées par le module, rôle)
    "gaider_cuda.dll":    (["cubiomes_src/gaider_cuda.cu"], "yeux stronghold GPU"),
    "structure_cuda.dll": (["cubiomes_src/structure_cuda.cu"], "filtre structures 48-bit"),
    "noise_gpu.dll":      (["cubiomes_src/noise_gpu.cu", "cubiomes_src/noise_gpu_kernel.cuh"],
                           "biomes 1.18+ FP32"),
    "biome_gpu.dll":      (["cubiomes_src/biome_gpu.cu", "cubiomes_src/biome_gpu_kernel.cuh"],
                           "128 strongholds 1.13-1.17 (retiré)"),
    "terrain_gpu.dll":    (["cubiomes_terrain/terrain_gpu.cu", "cubiomes_terrain/terrainnoise.c",
                            "cubiomes_terrain/biomenoise.c", "cubiomes_terrain/noise.c",
                            "cubiomes_terrain/biomes.c", "cubiomes_terrain/layers.c",
                            "cubiomes_terrain/generator.c", "cubiomes_terrain/util.c"],
                           "surface Y GPU FP32"),
}


def dll_archs(path):
    """Architectures embarquées (via cuobjdump si dispo, sinon heuristique)."""
    if shutil.which("cuobjdump"):
        rc, out, err = run(["cuobjdump", "--list-elf", "--list-ptx", path], timeout=60)
        if rc == 0:
            sass = sorted(set(re.findall(r"sm_(\d+a?)\.cubin", out)))
            ptx = sorted(set(re.findall(r"sm_(\d+a?)\.ptx", out)))
            if sass or ptx:
                return sass, ptx, "cuobjdump"
    b = open(path, "rb").read()
    sm = sorted(set(x.decode() for x in re.findall(rb"sm_(\d{2,3})", b)))
    return sm, [], "heuristique (cuobjdump absent : PTX non détectable)"


def dll_cuda_version(path):
    b = open(path, "rb").read()
    v = sorted(set(x.decode() for x in re.findall(rb"V(1\d\.\d+\.\d+)", b)))
    return v[0] if v else "?"


@guarded
def section_dll():
    R.section("DLL", "Bibliothèques GPU livrées")
    cc = GPU.get("cc", "").replace(".", "")
    for name, (srcs, role) in DLLS.items():
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            if name == "biome_gpu.dll":
                R.info(f"{name:<19} retirée volontairement ({role} → CPU)")
            else:
                R.warn(f"{name:<19} ABSENTE ({role})")
            continue
        sass, ptx, how = dll_archs(p)
        ver = dll_cuda_version(p)
        if how.startswith("cuobjdump"):
            archs_txt = f"SASS={sass or '-'} PTX={ptx or '-'}"
        else:
            archs_txt = f"archis vues={sass or '-'}"
        R.info(f"{name:<19} {os.path.getsize(p)/1024:7.0f} Ko  nvcc {ver}  "
               f"{archs_txt}  [{how}]  ({role})")
        # Compatibilité avec la carte présente
        if cc:
            runs_native = cc in [s.rstrip("a") for s in sass]
            runs_ptx = any(int(x.rstrip("a")) <= int(cc) for x in ptx) if ptx else False
            if runs_native:
                R.ok(f"{name}: code natif pour cette carte (sm_{cc})")
            elif runs_ptx:
                R.warn(f"{name}: pas de code natif sm_{cc} → compilation JIT par le driver "
                       f"(1er lancement lent)")
            elif how.startswith("cuobjdump"):
                R.fail(f"{name}: AUCUN code exécutable sur sm_{cc} → 'no kernel image'")
        # Carte grand public non couvertes par cette DLL
        targets = {"75": "RTX 20 / GTX 16", "86": "RTX 30", "89": "RTX 40", "120": "RTX 50"}
        if how.startswith("cuobjdump"):
            ptx_min = min([int(x.rstrip("a")) for x in ptx], default=999)
            missing = [f"{lbl} (sm_{t})" for t, lbl in targets.items()
                       if t not in [s.rstrip("a") for s in sass] and int(t) < ptx_min]
            if missing:
                R.fail(f"{name}: NE TOURNERA PAS sur : {', '.join(missing)}  (G1)")
        # Déclencheurs de recompilation au lancement (logique des modules)
        mt = os.path.getmtime(p)
        newer = [s for s in srcs if os.path.exists(os.path.join(HERE, s))
                 and os.path.getmtime(os.path.join(HERE, s)) > mt]
        if newer:
            R.warn(f"{name}: source(s) plus récente(s) que la DLL → SSA RECOMPILERA au "
                   f"lancement : {', '.join(os.path.basename(s) for s in newer)}  (G11)")
        if name == "gaider_cuda.dll" and b"gaider_cuda_crunchiness_filter" not in open(p, "rb").read():
            R.warn(f"{name}: symbole crunchiness absent → SSA recompilera")
    try:
        import native_libs as nl
        R.info("")
        R.info("Chargement réel par SSA (native_libs : fiche de build + auto-test GPU) :")
        for unit in nl.CUDA_UNITS:
            st, why = nl.build_state(unit)
            os.environ["SSA_NO_AUTOBUILD"] = "1"      # le diagnostic ne compile rien
            lib, msg = nl.load_cuda(unit)
            (R.ok if (lib is not None and st == "ok") else R.warn if lib is not None else R.fail)(
                f"{unit}: build={st} ({why}) → {'utilisable' if lib is not None else 'INDISPONIBLE'} : {msg}")
    except Exception as e:
        R.warn(f"native_libs indisponible : {e}")
    for name in ("libcubiomes.dll" if IS_WIN else "libcubiomes.so",
                 "terrain.dll" if IS_WIN else "libterrain.so",
                 "gaider_layouts.dll" if IS_WIN else "libgaider_layouts.so"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            R.info(f"{name:<19} {os.path.getsize(p)/1024:7.0f} Ko  (CPU)")
        else:
            R.warn(f"{name} absente")


# ═════════════════════════════════════════════════════════════════════════════
# Outils communs
# ═════════════════════════════════════════════════════════════════════════════
_CUB = None


def cub():
    """libcubiomes (CPU) — référence. Jamais recompilée ici."""
    global _CUB
    if _CUB is None:
        import cubiomes_wrap
        _CUB = cubiomes_wrap.get_lib()._lib
    return _CUB


def load_dll(path):
    return ctypes.CDLL(path)


def i64p(a):
    return a.ctypes.data_as(ctypes.POINTER(ctypes.c_int64))


def i32p(a):
    return a.ctypes.data_as(ctypes.POINTER(ctypes.c_int32))


def timed(fn, *a):
    t = time.perf_counter()
    r = fn(*a)
    return r, time.perf_counter() - t


def ramp(fn, n0, n_max, budget=LAUNCH_BUDGET_S):
    """Appelle fn(n) avec n croissant (×2) tant qu'un lancement reste < budget.
    Retourne (n, secondes) de la dernière mesure. Sert à extrapoler sans risquer
    le TDR sur la machine de test."""
    n, dt = n0, 0.0
    fn(n0)                                   # chauffe (JIT éventuel, allocations)
    while True:
        _, dt = timed(fn, n)
        if dt > budget or n * 2 > n_max:
            return n, dt
        n *= 2


def tdr_line(label, prod_n, n, dt):
    """Extrapole la durée d'UN lancement de production et le risque TDR."""
    t_prod = dt * prod_n / n
    R.info(f"{label}: lancement de production ({prod_n:,} éléments) ≈ {t_prod*1000:,.0f} ms "
           f"sur cette carte (mesuré {n:,} en {dt*1000:.0f} ms)")
    slower = [k for k in (2, 3, 4, 6) if t_prod * k > TDR_S]
    if t_prod > TDR_S * 0.5:
        R.fail(f"{label}: {t_prod:.2f} s par lancement dès CETTE carte → TDR imminent (G5)")
    elif slower:
        R.warn(f"{label}: sur une carte {slower[0]}× plus lente (ex. RTX 3060 ≈ 3-4×, "
               f"portable/RTX 2060 ≈ 5-6×) → > {TDR_S:.0f} s → reset pilote (G5)")
    else:
        R.ok(f"{label}: marge TDR suffisante même sur une carte 6× plus lente")
    return t_prod


# ═════════════════════════════════════════════════════════════════════════════
# GAIDER (yeux)
# ═════════════════════════════════════════════════════════════════════════════
def bind_gaider(lib):
    lib.gaider_cuda_available.restype = ctypes.c_int
    lib.gaider_cuda_eyes_arr.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int32),
                                         ctypes.POINTER(ctypes.c_int32), ctypes.c_int32, ctypes.c_int,
                                         ctypes.c_int, ctypes.POINTER(ctypes.c_int32)]
    lib.gaider_cuda_eyes_arr.restype = ctypes.c_int
    lib.gaider_cuda_full_scan_min_eyes.argtypes = [ctypes.c_int64, ctypes.c_int32, ctypes.c_int,
                                                   ctypes.c_int, ctypes.POINTER(ctypes.c_int64),
                                                   ctypes.POINTER(ctypes.c_int32), ctypes.c_int32]
    lib.gaider_cuda_full_scan_min_eyes.restype = ctypes.c_int
    return lib


def cpu_chunks_and_eyes(seed_start, n, mc, grid=7):
    import numpy as np
    L = cub()
    L.gaider_batch_compute_chunks.argtypes = [ctypes.c_int64, ctypes.c_int32, ctypes.c_int,
                                              ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32)]
    L.gaider_batch_compute_chunks.restype = None
    L.seedlist_eyes_prefilter_arr.argtypes = [ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int32),
                                              ctypes.POINTER(ctypes.c_int32), ctypes.c_int32, ctypes.c_int,
                                              ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int64),
                                              ctypes.POINTER(ctypes.c_int32)]
    L.seedlist_eyes_prefilter_arr.restype = ctypes.c_int
    seeds = np.arange(seed_start, seed_start + n, dtype=np.int64)
    cx = np.zeros(n, np.int32); cz = np.zeros(n, np.int32)
    L.gaider_batch_compute_chunks(seed_start, n, mc, i32p(cx), i32p(cz))
    os_ = np.zeros(n, np.int64); oe = np.zeros(n, np.int32)
    m = L.seedlist_eyes_prefilter_arr(i64p(seeds), i32p(cx), i32p(cz), n, mc, 0, grid,
                                      i64p(os_), i32p(oe))
    assert m == n, f"CPU a renvoyé {m}/{n} graines avec min_eyes=0"
    return seeds, cx, cz, dict(zip(os_.tolist(), oe.tolist()))


def _last_err(lib):
    try:
        f = lib.ssa_cuda_last_error
        f.restype = ctypes.c_char_p
        v = f()
        return v.decode("utf-8", "replace") if v else "?"
    except Exception:
        return "ancienne DLL (pas de message)"


def gaider_check(lib, label, mcs=((21, "1.17"), (28, "1.21")), n=3000):
    """Compare GPU vs CPU. Retourne True si tout est identique."""
    import numpy as np
    allok = True
    for mc, vname in mcs:
        seeds, cx, cz, ref = cpu_chunks_and_eyes(987654321, n, mc)
        # (a) eyes_arr : positions fournies par le CPU
        out = np.full(n, -1, np.int32)
        rc = lib.gaider_cuda_eyes_arr(i64p(seeds), i32p(cx), i32p(cz), n, mc, 7, i32p(out))
        if rc < 0:
            R.ok(f"{label} eyes_arr {vname}: échec SIGNALÉ explicitement ({_last_err(lib)}) "
                 f"→ SSA repasse en CPU au lieu de rendre 0 résultat")
            allok = False
            continue
        if (out == -1).all():
            R.fail(f"{label} eyes_arr {vname}: le kernel n'a PAS tourné (sortie intacte) et "
                   f"la fonction n'a signalé AUCUNE erreur → faux négatifs silencieux (G1/G4)")
            allok = False
            continue
        mism = sum(1 for i, s in enumerate(seeds.tolist()) if out[i] != ref[s])
        if mism:
            R.fail(f"{label} eyes_arr {vname}: {mism}/{n} graines divergent du CPU")
            allok = False
        else:
            R.ok(f"{label} eyes_arr {vname}: {n}/{n} identiques au CPU "
                 f"(max {int(out.max())} yeux)")
        # (b) full_scan : position calculée sur GPU
        os_ = np.zeros(n, np.int64); oe = np.zeros(n, np.int32)
        tot = lib.gaider_cuda_full_scan_min_eyes(int(seeds[0]), n, mc, 0, i64p(os_), i32p(oe), n)
        if tot < 0:
            R.ok(f"{label} full_scan {vname}: échec SIGNALÉ explicitement ({_last_err(lib)})")
            allok = False
            continue
        if tot != n:
            R.fail(f"{label} full_scan {vname}: {tot}/{n} graines renvoyées (attendu {n})")
            allok = False
            continue
        g = dict(zip(os_.tolist(), oe.tolist()))
        mism = sum(1 for s in seeds.tolist() if g.get(s) != ref[s])
        if mism:
            R.fail(f"{label} full_scan {vname}: {mism}/{n} divergences (position initiale "
                   f"GPU ≠ initFirstStronghold CPU ?)")
            allok = False
        else:
            R.ok(f"{label} full_scan {vname}: {n}/{n} identiques au CPU")
    return allok


def gaider_timing(lib, label):
    import numpy as np
    buf_s = np.zeros(1 << 16, np.int64); buf_e = np.zeros(1 << 16, np.int32)
    res = {}
    for mc, vname in ((21, "1.17"), (28, "1.21")):
        def f(n, mc=mc):
            lib.gaider_cuda_full_scan_min_eyes(123456789, n, mc, 12, i64p(buf_s), i32p(buf_e), 1 << 16)
        n, dt = ramp(f, 1 << 14, 1 << 22)
        res[vname] = tdr_line(f"{label} full_scan {vname} (onglet Stronghold, lot 4 M)", 1 << 22, n, dt)
        R.info(f"{label} full_scan {vname}: débit ≈ {n/dt/1e6:,.1f} M graines/s")
    return res


@guarded
def section_gaider(path=None, label="gaider_cuda.dll", timing=True):
    if path is None:
        R.section("GAIDER", "Yeux de l'end portal : GPU vs CPU")
        path = os.path.join(HERE, "gaider_cuda.dll" if IS_WIN else "libgaider_cuda.so")
    if not os.path.exists(path):
        R.warn(f"{label} absente — test sauté")
        return None
    lib = bind_gaider(load_dll(path))
    if not lib.gaider_cuda_available():
        R.fail(f"{label}: gaider_cuda_available()=0 (pas de GPU / driver trop ancien ?)")
        return None
    import numpy as np
    _s = np.arange(1, 65, dtype=np.int64); _c = np.zeros(64, np.int32); _o = np.full(64, -1, np.int32)
    (_, t_first) = timed(lib.gaider_cuda_eyes_arr, i64p(_s), i32p(_c), i32p(_c), 64, 28, 7, i32p(_o))
    R.info(f"{label}: 1er appel {t_first*1000:.0f} ms (inclut JIT éventuel du driver)")
    ok = gaider_check(lib, label)
    tim = gaider_timing(lib, label) if timing else None
    return ok, tim


# ═════════════════════════════════════════════════════════════════════════════
# STRUCT (filtre 48-bit)
# ═════════════════════════════════════════════════════════════════════════════
STYPE = {"Village": 5, "Monument": 8, "Mansion": 9, "Outpost": 10}


def make_job(stype, mc, box, min_count=1):
    from structure_cuda import GpuStructJob
    L = cub()
    L.ssa_get_struct_config.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int32),
                                        ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32)]
    L.ssa_get_struct_config.restype = ctypes.c_int
    L.ssa_struct_gpu_algo.argtypes = [ctypes.c_int]
    L.ssa_struct_gpu_algo.restype = ctypes.c_int
    salt, rs, cr = ctypes.c_int32(), ctypes.c_int32(), ctypes.c_int32()
    if not L.ssa_get_struct_config(stype, mc, ctypes.byref(salt), ctypes.byref(rs), ctypes.byref(cr)):
        return None
    algo = L.ssa_struct_gpu_algo(stype)
    x1, z1, x2, z2 = box
    csz = rs.value * 16
    rx1, rz1, rx2, rz2 = x1 // csz, z1 // csz, x2 // csz, z2 // csz
    return GpuStructJob(algo_kind=algo, salt=salt.value, region_size=rs.value, chunk_range=cr.value,
                        rx1=rx1, rz1=rz1, rx2=rx2, rz2=rz2, x1=x1, z1=z1, x2=x2, z2=z2,
                        rmax_sq=0, cx=0, cz=0, min_count=min_count, exclude=0, _pad=0)


def cpu_struct_ref(stype, mc, job, lows):
    """Référence : cubiomes getStructurePos, même règle de comptage que le kernel."""
    import cubiomes_wrap as cw
    L = cub()
    p = cw.Pos()
    keep = set()
    for low in lows:
        hits = 0
        for rz in range(job.rz1, job.rz2 + 1):
            for rx in range(job.rx1, job.rx2 + 1):
                if not L.getStructurePos(stype, mc, ctypes.c_uint64(low), rx, rz, ctypes.byref(p)):
                    continue
                if job.x1 <= p.x <= job.x2 and job.z1 <= p.z <= job.z2:
                    hits += 1
        if hits >= job.min_count:
            keep.add(low)
    return keep


def bind_struct(lib):
    from structure_cuda import GpuStructJob
    lib.structure_cuda_available.restype = ctypes.c_int
    lib.structure_cuda_filter.argtypes = [ctypes.c_int64, ctypes.c_int64, ctypes.POINTER(GpuStructJob),
                                          ctypes.c_int, ctypes.POINTER(ctypes.c_int64), ctypes.c_int,
                                          ctypes.c_int]
    lib.structure_cuda_filter.restype = ctypes.c_int64
    return lib


def struct_run(lib, jobs, start, n, cap):
    import numpy as np
    from structure_cuda import GpuStructJob
    arr = (GpuStructJob * len(jobs))(*jobs)
    out = np.zeros(cap, np.int64)
    tot = lib.structure_cuda_filter(start, n, arr, len(jobs), i64p(out), cap, 1)
    return tot, out[:max(0, min(tot, cap))]


def struct_check(lib, label, n=20000):
    cases = [
        ("Village 1.21", "Village", 28, (0, 0, 700, 700), 2),
        ("Village 1.16", "Village", 20, (0, 0, 700, 700), 2),
        ("Outpost 1.21", "Outpost", 28, (-1000, -1000, 1000, 1000), 2),
        ("Monument 1.21", "Monument", 28, (0, 0, 300, 300), 1),
        ("Mansion 1.21", "Mansion", 28, (0, 0, 600, 600), 1),
    ]
    allok = True
    start = 31415926535
    for cname, sname, mc, box, mn in cases:
        stype = STYPE[sname]
        job = make_job(stype, mc, box, mn)
        if job is None:
            R.warn(f"{label} {cname}: pas de config structure — sauté")
            continue
        tot, got = struct_run(lib, [job], start, n, n)
        if tot < 0:
            R.fail(f"{label} {cname}: le kernel a renvoyé une ERREUR (-1)")
            allok = False
            continue
        ref = cpu_struct_ref(stype, mc, job, range(start, start + n))
        g = set(int(x) for x in got.tolist())
        if g == ref:
            R.ok(f"{label} {cname}: {len(ref)}/{n} lows retenus, identiques à cubiomes")
        else:
            R.fail(f"{label} {cname}: GPU {len(g)} vs cubiomes {len(ref)} "
                   f"(manquants {len(ref - g)}, en trop {len(g - ref)})")
            allok = False
    return allok


def struct_timing(lib, label):
    res = {}
    # Nombre d'instances impossible à atteindre → aucune sortie anticipée, aucun
    # survivant : on mesure le calcul pur, sans bruit d'atomics.
    light = make_job(STYPE["Village"], 28, (0, 0, 1087, 1087), 10 ** 6)       # 2×2 = 4 régions
    heavy = make_job(STYPE["Mansion"], 28, (-5000, -5000, 5000, 5000), 10 ** 6)  # 8×8 = 64 régions
    for name, job in (("léger (village 4 régions)", light), ("lourd (manoirs 64 régions)", heavy)):
        def f(n, job=job):
            struct_run(lib, [job], 0, n, 1 << 16)
        n, dt = ramp(f, 1 << 20, 1 << 28)
        res[name] = tdr_line(f"{label} filtre {name}, BATCH=256 M", 1 << 28, n, dt)
        R.info(f"{label} {name}: débit ≈ {n/dt/1e9:,.2f} G lows/s")
    return res


@guarded
def section_struct(path=None, label="structure_cuda.dll", timing=True):
    if path is None:
        R.section("STRUCT", "Filtre 48-bit des structures : GPU vs cubiomes")
        path = os.path.join(HERE, "structure_cuda.dll" if IS_WIN else "libstructure_cuda.so")
    if not os.path.exists(path):
        R.warn(f"{label} absente — test sauté")
        return None
    lib = bind_struct(load_dll(path))
    if not lib.structure_cuda_available():
        R.fail(f"{label}: structure_cuda_available()=0")
        return None
    ok = struct_check(lib, label)
    tim = struct_timing(lib, label) if timing else None
    return ok, tim, lib


@guarded
def section_surviv(lib):
    R.section("SURVIV", "Troncature des survivants GPU (G6)")
    if lib is None:
        R.warn("structure_cuda indisponible — sauté")
        return
    # Condition large (1 village dans 2×2 régions) : presque tout passe.
    job = make_job(STYPE["Village"], 28, (0, 0, 1087, 1087), 1)
    n, cap = 1 << 23, 1 << 20          # même ratio que la prod (256 M / 4 M = 64)… en plus petit
    tot, _ = struct_run(lib, [job], 0, n, cap)
    if tot < 0:
        R.fail("erreur kernel")
        return
    rate = tot / n
    R.info(f"condition large : {tot:,} survivants sur {n:,} lows ({rate*100:.1f} %)")
    lost_prod = max(0.0, 1 - (1 << 22) / max(1, rate * (1 << 28)))
    if lost_prod > 0:
        R.fail(f"en production (BATCH 256 M, out_cap 4 M) ce taux ferait PERDRE "
               f"{lost_prod*100:.0f} % des graines valides, sans avertissement (G6)")
    else:
        R.ok("pas de troncature à ce taux")


# ═════════════════════════════════════════════════════════════════════════════
# NOISE (biomes 1.18+ FP32) — reprend l'auto-test de noise_gpu.py
# ═════════════════════════════════════════════════════════════════════════════
@guarded
def section_noise():
    R.section("NOISE", "Biomes 1.18+ (FP32) : GPU vs cubiomes")
    import numpy as np
    import noise_gpu as ng
    import cubiomes_wrap as cw
    if not os.path.exists(os.path.join(HERE, ng._lib_name())):
        R.warn("noise_gpu.dll absente — sauté")
        return
    (_, t) = timed(ng.available)
    if not ng.available():
        R.fail(f"noise_gpu indisponible : {ng.last_error()}")
        return
    lib = cw.make_worker_lib()
    N = 9
    base = [-750 + 1500 * i // (N - 1) for i in range(N)]
    xs = np.array([i for i in base for _ in base], np.int32)
    zs = np.array([j for _ in base for j in base], np.int32)
    allow = [4, 27, 29, 5, 30, 21, 1, 3]
    mask = ng.allowed_bitmask(allow); aset = set(allow)
    for mc, vname in ((22, "1.18"), (28, "1.21"), (35, "26.3")):
        if ng.get_tables(mc) is None:
            R.warn(f"biomescan {vname}: tables non extraites pour cette version (non supportée ?)")
            continue
        seeds = np.arange(1, 401, dtype=np.uint64)
        (counts, t1) = timed(ng.biomescan, mc, seeds, xs, zs, mask, 15)
        if counts is None:
            R.fail(f"biomescan {vname}: None ({ng.last_error()}) — kernel en échec")
            continue
        worst, hist = 0, {}
        for si, s in enumerate(seeds.tolist()):
            lib.setup_and_seed(mc, int(s))
            c = sum(1 for k in range(len(xs))
                    if lib.get_biome_at(int(xs[k]) * 4, int(zs[k]) * 4, scale=4, y=60) in aset)
            d = abs(int(counts[si]) - c)
            hist[d] = hist.get(d, 0) + 1
            worst = max(worst, d)
        dist = ", ".join(f"|Δ|={k}:{v}" for k, v in sorted(hist.items()))
        msg = f"biomescan {vname}: 400 graines, écart max {worst} ({dist}) — 1er appel {t1*1000:.0f} ms"
        (R.ok if worst <= 1 else R.fail)(msg + ("" if worst <= 1 else "  → marge 'within 1' VIOLÉE (G10)"))
    M = 1 << 17
    bs = np.arange(10 ** 6, 10 ** 6 + M, dtype=np.uint64)
    (_, dt) = timed(ng.biomescan, 28, bs, xs, zs, mask, 15)
    R.info(f"débit biomescan 1.21 ({len(xs)} points/graine) ≈ {M/dt:,.0f} graines/s "
           f"(appel de {dt*1000:.0f} ms pour {M:,})")


# ═════════════════════════════════════════════════════════════════════════════
# BIOME128 (1.13-1.17)
# ═════════════════════════════════════════════════════════════════════════════
@guarded
def section_biome128():
    R.section("BIOME128", "128 strongholds 1.13-1.17 : GPU vs cubiomes")
    import numpy as np
    import biome_gpu as bg
    import cubiomes_wrap as cw
    if not os.path.exists(os.path.join(HERE, bg._lib_name())):
        R.warn("biome_gpu.dll ABSENTE : le mode « 128 strongholds GPU » 1.13-1.17 n'a "
               "jamais été compilé sur ce PC → chaque usage tente nvcc ou retombe en CPU")
        return
    if not bg.available():
        R.fail(f"biome_gpu indisponible : {bg.last_error()}")
        return
    lib = cw.make_worker_lib()
    seeds = np.array([1000, 1234567, 8675309, -42, 2 ** 40 + 7], dtype=np.int64)
    for mc, name in ((16, "1.13"), (17, "1.14"), (18, "1.15"), (20, "1.16"), (21, "1.17")):
        res = bg.strongholds128(mc, seeds)
        if res is None:
            R.fail(f"{name}: strongholds128 → None ({bg.last_error()})")
            continue
        xs, zs, _ = res
        mism = 0
        for si, s in enumerate(seeds.tolist()):
            lib.setup_and_seed(mc, int(s))
            for i, (rx, rz) in enumerate(lib.find_strongholds(max_count=128)):
                if xs[si, i] != rx or zs[si, i] != rz:
                    mism += 1
        (R.ok if mism == 0 else R.fail)(f"{name}: {128*len(seeds)-mism}/{128*len(seeds)} positions identiques")


# ═════════════════════════════════════════════════════════════════════════════
# TERRAIN (surface Y)
# ═════════════════════════════════════════════════════════════════════════════
VNAME = {22: "1.18", 28: "1.21"}
TERRAIN_CASES = [
    # (x0, z0, W, H, step, caves, libellé)
    (-384, -384, 48, 48, 16, 1, "origine, pas 16, grottes"),
    (-384, -384, 48, 48, 16, 0, "origine, pas 16, SANS grottes"),
    (100, 100, 32, 32, 1, 1, "32×32 blocs contigus, grottes"),
    (1_000_000, 1_000_000, 48, 48, 16, 1, "à 1 M blocs, pas 16, grottes"),
]


def terrain_worker(mcs):
    """Sous-processus : isole un éventuel crash natif. Émet une ligne 'TW {json}'
    par mesure sur stdout."""
    import json
    import numpy as np
    neutralize_modules()
    import terrain as tr

    def emit(**k):
        print("TW " + json.dumps(k), flush=True)

    if not tr.available():
        emit(kind="err", msg=f"terrain CPU indisponible : {tr.last_error()}")
        return
    gp = os.path.join(HERE, "terrain_gpu.dll" if IS_WIN else "terrain_gpu.so")
    g = load_dll(gp)
    g.tgpu_surface_grid.argtypes = [ctypes.c_int, ctypes.c_uint64, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_int)]
    g.tgpu_surface_grid.restype = ctypes.c_int
    seed = 20260926
    import random
    for mc in mcs:
        for (x0, z0, W, H, step, caves, lbl) in TERRAIN_CASES:
            emit(kind="start", mc=mc, case=lbl)
            eng = tr.TerrainEngine(seed, mc)          # contexte NEUF pour chaque cas
            cpu = np.empty(W * H, np.int32)
            tr._LIB.tb_surface_grid(eng._ctx, x0, z0, W, H, step, caves,
                                    cpu.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
            # contrôle de l'ordre des cases (ligne = z, colonne = x) sur quelques points
            layout_ok = True
            for (i, j) in ((0, 0), (W - 1, 0), (0, H - 1), (W // 2, H // 3)):
                y = tr._LIB.tb_surface_y(eng._ctx, x0 + i * step, z0 + j * step, caves)
                if y != cpu[j * W + i]:
                    layout_ok = False
            # référence CPU vérifiée : 30 points recalculés chacun dans un contexte neuf
            rnd = random.Random(7)
            ref_bad = 0
            for _ in range(30):
                i, j = rnd.randrange(W), rnd.randrange(H)
                e2 = tr.TerrainEngine(seed, mc)
                if tr._LIB.tb_surface_y(e2._ctx, x0 + i * step, z0 + j * step, caves) != cpu[j * W + i]:
                    ref_bad += 1
            gpu = np.full(W * H, -9999, np.int32)
            t = time.perf_counter()
            rc = g.tgpu_surface_grid(mc, ctypes.c_uint64(seed), x0, z0, W, H, step, caves,
                                     gpu.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
            dt = time.perf_counter() - t
            if rc != 0:
                emit(kind="res", mc=mc, case=lbl, rc=rc)
                continue
            d = np.abs(gpu - cpu)
            dT = np.abs(gpu.reshape(H, W).T.reshape(-1) - cpu) if W == H else d
            hist = {str(k): int((d == k).sum()) for k in range(0, 4)}
            hist[">3"] = int((d > 3).sum())
            emit(kind="res", mc=mc, case=lbl, rc=0, ms=dt * 1000, layout_ok=layout_ok, ref_bad=ref_bad,
                 exact=float((d == 0).mean()) * 100, maxd=int(d.max()),
                 exactT=float((dT == 0).mean()) * 100, hist=hist,
                 gpu_mean=float(gpu.mean()), cpu_mean=float(cpu.mean()))


@guarded
def section_terrain():
    R.section("TERRAIN", "Surface Y : GPU FP32 vs CPU exact (sous-processus isolés)")
    import json
    gp = os.path.join(HERE, "terrain_gpu.dll" if IS_WIN else "terrain_gpu.so")
    if not os.path.exists(gp):
        R.warn("terrain_gpu.dll absente — sauté")
        return
    runs = [("1.18 seul", "22"), ("1.21 seul (processus neuf)", "28"),
            ("1.18 PUIS 1.21 dans le même processus (changement de version)", "22,28")]
    for title, arg in runs:
        R.info(f"— {title}")
        rc, out, err = run([sys.executable, os.path.abspath(__file__), "--terrain-worker", arg],
                           timeout=600)
        last_start = None
        for ln in out.splitlines():
            if not ln.startswith("TW "):
                continue
            m = json.loads(ln[3:])
            if m["kind"] == "err":
                R.fail(m["msg"])
            elif m["kind"] == "start":
                last_start = m
            elif m["kind"] == "res":
                last_start = None
                v = VNAME.get(m["mc"], m["mc"])
                if m["rc"] != 0:
                    R.fail(f"{v} {m['case']}: tgpu_surface_grid rc={m['rc']}")
                    continue
                if m.get("ref_bad"):
                    R.fail(f"{v} {m['case']}: la grille CPU diffère du calcul point par point sur "
                           f"{m['ref_bad']}/30 points → terrain.dll pas encore reconstruite avec le "
                           f"correctif du cache (lance d'abord : py tools/build_native.py)")
                if not m["layout_ok"]:
                    R.warn(f"{v} {m['case']}: incohérence de l'ordre des cases côté CPU (contrôle)")
                msg = (f"{v} {m['case']}: {m['exact']:.1f} % exacts, écart max {m['maxd']} "
                       f"(répartition |Δ| {m['hist']}), moyennes GPU {m['gpu_mean']:.1f} / "
                       f"CPU {m['cpu_mean']:.1f}, {m['ms']:.0f} ms")
                if m["exactT"] > m["exact"] + 20:
                    msg += f"  — TRANSPOSÉ : {m['exactT']:.1f} % exacts (x/z inversés ?)"
                (R.ok if m["maxd"] <= 1 else R.warn)(msg)
        if last_start is not None or rc not in (0,):
            where = (f"pendant « {VNAME.get(last_start['mc'], last_start['mc'])} "
                     f"{last_start['case']} »") if last_start else ""
            native = ("access violation" in (err or "").lower()) or rc is None or \
                     (rc not in (0, 1))
            R.fail(f"le sous-processus a planté {where} (code {rc}) → "
                   + ("crash natif dans terrain_gpu.dll" if native else "erreur Python (voir ci-dessous)"))
            tail = (err or "").strip().splitlines()[-6:]
            if tail:
                R.raw("\n".join(tail))
    R.info("NB : l'Exact-Y « par graine » (custom_search, condition relative ou avec d'autres "
           "conditions) utilise CE calcul GPU FP32 comme verdict final, et renvoie False "
           "sans GPU (D3).")


# ═════════════════════════════════════════════════════════════════════════════
# BUILD-TEST (option)
# ═════════════════════════════════════════════════════════════════════════════
FATBIN = ["-gencode", "arch=compute_75,code=sm_75",
          "-gencode", "arch=compute_80,code=sm_80",
          "-gencode", "arch=compute_86,code=sm_86",
          "-gencode", "arch=compute_89,code=sm_89",
          "-gencode", "arch=compute_120,code=sm_120",
          "-gencode", "arch=compute_120,code=compute_120"]

BUILD_UNITS = {
    "gaider_cuda": (["cubiomes_src/gaider_cuda.cu"], []),
    "structure_cuda": (["cubiomes_src/structure_cuda.cu"], []),
    "noise_gpu": (["cubiomes_src/noise_gpu.cu"], []),
    "biome_gpu": (["cubiomes_src/biome_gpu.cu"], []),
    "terrain_gpu": (["cubiomes_terrain/terrain_gpu.cu", "cubiomes_terrain/terrainnoise.c",
                     "cubiomes_terrain/biomenoise.c", "cubiomes_terrain/noise.c",
                     "cubiomes_terrain/biomes.c", "cubiomes_terrain/layers.c",
                     "cubiomes_terrain/generator.c", "cubiomes_terrain/util.c"],
                    ["-DTGPU_FP32", "-I", os.path.join(HERE, "cubiomes_terrain")]),
}


def nvcc_build(unit, out_dir, arch_flags, prefix="fat_"):
    # Nom unique : Windows peut réutiliser une DLL déjà chargée qui porte le même nom.
    srcs, extra = BUILD_UNITS[unit]
    ext = ".dll" if IS_WIN else ".so"
    out = os.path.join(out_dir, prefix + unit + ext)
    cmd = ["nvcc", "-O3", "-shared"] + arch_flags + extra
    if not IS_WIN:
        cmd += ["-Xcompiler", "-fPIC"]
    cmd += [os.path.join(HERE, s) for s in srcs] + ["-o", out]
    t = time.perf_counter()
    rc, o, e = run(cmd, timeout=900)
    return rc, out, time.perf_counter() - t, (e + o).strip()


@guarded
def section_build(orig_timings):
    R.section("BUILD", "Essai de build fatbin multi-archi (dossier temporaire)")
    if not shutil.which("nvcc"):
        R.warn("nvcc absent — test sauté")
        return
    tmp = tempfile.mkdtemp(prefix="ssa_fatbin_")
    R.info(f"dossier temporaire : {tmp}")
    R.info("flags : " + " ".join(FATBIN))
    built = {}

    def build_one(unit, hint):
        R.info(f"compilation de {unit} … (peut prendre {hint} — la console reste muette, c'est normal)")
        rc, out, dt, log = nvcc_build(unit, tmp, FATBIN)
        if rc == 0 and os.path.exists(out):
            sass, ptx, how = dll_archs(out)
            R.ok(f"{unit}: compilé en {dt:.0f} s, {os.path.getsize(out)/1024:.0f} Ko, SASS={sass} PTX={ptx}")
            built[unit] = out
        else:
            R.fail(f"{unit}: échec de compilation (rc={rc}, {dt:.0f} s)")
            R.raw(log[-1500:])
        R.save()

    # Rapides d'abord (+ tests de perf), les longues à la fin.
    build_one("gaider_cuda", "~10 s")
    build_one("structure_cuda", "~10 s")
    # Perfs et justesse identiques sur CETTE carte ?
    if "gaider_cuda" in built:
        r = section_gaider(built["gaider_cuda"], "gaider[fatbin]", timing=True)
        if r and orig_timings.get("gaider") and r[1]:
            for k, v in r[1].items():
                o = orig_timings["gaider"].get(k)
                if o:
                    R.info(f"perf gaider {k}: fatbin/original = {v/o:.2f} (1.00 = identique)")
    if "structure_cuda" in built:
        r = section_struct(built["structure_cuda"], "struct[fatbin]", timing=True)
        if r and orig_timings.get("struct") and r[1]:
            for k, v in r[1].items():
                o = orig_timings["struct"].get(k)
                if o:
                    R.info(f"perf struct {k}: fatbin/original = {v/o:.2f} (1.00 = identique)")
    # Simulation : DLL SANS code pour cette carte (= ce que vit une RTX 30/40 aujourd'hui)
    R.info("")
    R.info("Simulation d'une carte non supportée : gaider_cuda compilé pour sm_86 SEUL")
    rc, out, dt, log = nvcc_build("gaider_cuda", tmp, ["-gencode", "arch=compute_86,code=sm_86"],
                                  prefix="sm86only_")
    if rc == 0:
        lib = bind_gaider(load_dll(out))
        av = lib.gaider_cuda_available()
        (R.ok if av == 0 else R.fail)(
            f"gaider_cuda_available() renvoie {av} (ancien code : 1 = « GPU utilisable » à tort ; "
            f"attendu désormais : 0, raison : {_last_err(lib)})")
        gaider_check(lib, "gaider[sm86-only]", mcs=((28, "1.21"),), n=256)
    else:
        R.warn("compilation sm_86 impossible — simulation sautée")
    R.save()
    R.info(f"(tu peux supprimer {tmp} ; rien d'autre n'a été modifié)")


# ═════════════════════════════════════════════════════════════════════════════
def main():
    global REPORT_PATH
    if "--terrain-worker" in sys.argv:
        terrain_worker([int(x) for x in sys.argv[sys.argv.index("--terrain-worker") + 1].split(",")])
        return
    build = "--build-test" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].lower().split(","))
        REPORT_PATH = os.path.join(HERE, "gpu_diag_report_" + "_".join(sorted(only)) + ".txt")
        build = build or "build" in only

    def want(name):
        return only is None or name in only

    t0 = time.time()
    print("SSA — diagnostic GPU (lecture seule). Rapport :", REPORT_PATH)
    try:
        neutralize_modules()
    except Exception as e:
        R.fail(f"import des modules SSA impossible : {e}")
    try:
        section_env(); R.save()
        if want("dll"):
            section_dll(); R.save()
        timings = {}
        lib_struct = None
        if want("gaider") or build:
            r = section_gaider(); R.save()
            if r:
                timings["gaider"] = r[1]
        if want("struct") or want("surviv") or build:
            r = section_struct(); R.save()
            if r:
                timings["struct"] = r[1]
                lib_struct = r[2]
        if want("surviv"):
            section_surviv(lib_struct); R.save()
        if want("noise"):
            section_noise(); R.save()
        if want("biome128"):
            section_biome128(); R.save()
        if want("terrain"):
            section_terrain(); R.save()
        if build:
            section_build(timings)
    except KeyboardInterrupt:
        R.section("INTERROMPU", "Ctrl+C — rapport partiel enregistré")
    R.section("FIN", f"Terminé en {time.time()-t0:.0f} s")
    n_w = sum(1 for t, _, _ in R.summary if t == "WARN")
    n_f = sum(1 for t, _, _ in R.summary if t == "FAIL")
    R.info(f"{n_f} FAIL, {n_w} WARN — détail en fin de {os.path.basename(REPORT_PATH)}")
    R.save()
    print("\nRapport écrit :", REPORT_PATH)


if __name__ == "__main__":
    main()
