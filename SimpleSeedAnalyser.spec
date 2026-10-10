# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for SimpleSeedAnalyser (Qt UI only, classic Tk UI dropped).
#
# Put this file AND pyi_rth_ssa.py at the root of the SSA folder (next to
# ssa_qt.py), then, from a clean venv on the build PC:
#
#     py -m pip install -U pyinstaller pyinstaller-hooks-contrib PySide6-Essentials numpy pillow zstandard
#     py tools/build_native.py --check          (every unit must say "ok")
#     py -m PyInstaller --noconfirm --clean SimpleSeedAnalyser.spec
#
# Output: dist\SimpleSeedAnalyser\SimpleSeedAnalyser.exe  (+ _internal\)
# Ship the whole dist\SimpleSeedAnalyser folder (zip it, or wrap it in an
# Inno Setup installer that installs per-user to %LOCALAPPDATA%\Programs).

import os
import sys
import importlib.util
from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH                      # folder that contains this .spec (= SSA root)
IS_WIN = sys.platform == "win32"

# ── native libraries (loaded with ctypes from os.path.dirname(__file__) ==
#    sys._MEIPASS, so they MUST land at the bundle root: destination ".") ──────
if IS_WIN:
    REQUIRED_LIBS = ["libcubiomes.dll", "gaider_layouts.dll", "terrain.dll"]
    OPTIONAL_LIBS = ["gaider_cuda.dll", "structure_cuda.dll", "noise_gpu.dll",
                     "terrain_gpu.dll", "topy_gpu.dll"]
else:                                # Linux test build of the same spec
    REQUIRED_LIBS = ["libcubiomes.so", "libgaider_layouts.so", "libterrain.so"]
    OPTIONAL_LIBS = [ "terrain_gpu.so", "libgaider_cuda.so",
                     "libstructure_cuda.so", "libnoise_gpu.so", "libtopy_gpu.so"]

binaries = []
for name in REQUIRED_LIBS:
    p = os.path.join(ROOT, name)
    if not os.path.isfile(p):
        raise SystemExit(f"[spec] required native library missing: {p}")
    binaries.append((p, "."))
for name in OPTIONAL_LIBS:
    p = os.path.join(ROOT, name)
    if os.path.isfile(p):
        binaries.append((p, "."))
    else:
        print(f"[spec] WARNING: {name} not found — that GPU/terrain feature will be "
              f"unavailable in the .exe")

# ── data files (read next to the code at run time) ───────────────────────────
datas = [
    (os.path.join(ROOT, "colormaps"), "colormaps"),              # colormaps.py
    (os.path.join(ROOT, "ruined_footprints.json"), "."),         # ruined_portal.py
]
# the Minecraft checker's Java source (mc_checker.py compiles it once with the JDK;
# no Minecraft code is shipped: the jars come from the user's seed-checker setup)
if os.path.isdir(os.path.join(ROOT, "tools", "checker")):
    datas.append((os.path.join(ROOT, "tools", "checker"), os.path.join("tools", "checker")))
if os.path.isfile(os.path.join(ROOT, "native_build.json")):      # native_libs.py
    datas.append((os.path.join(ROOT, "native_build.json"), "."))
# drawing_presets.json = the classic UI's Draw presets; the Qt Draw page imports
# them ONCE into the user's library on first run (draw_library.import_legacy_presets_once).
# Keep it only if you WANT every new user to start with these drawings.
SHIP_LEGACY_DRAW_PRESETS = False
if SHIP_LEGACY_DRAW_PRESETS:
    datas.append((os.path.join(ROOT, "drawing_presets.json"), "."))

# NOT shipped on purpose: cubiomes_src/, cubiomes_terrain/, tools/ (their presence
# makes terrain.py / cubiomes_wrap.py try to compile on the user's PC), presets/
# (classic UI only), *.session samples, *.old, __pycache__.

# ── hidden imports ───────────────────────────────────────────────────────────
hiddenimports = (
    collect_submodules("qtui")
    + [
        # gpu_planner._gpu_available(): __import__(mod) with a string
        "structure_cuda", "noise_gpu", "gaider_cuda",
        # structure_cuda: __import__("native_libs")
        "native_libs",
        # modules only imported lazily inside functions / in worker processes
        "search_worker", "stronghold_eyes", "ruined_portal", "custom_search", "ssa_paths", "selftest",
        "gaider_layouts", "terrain", "biome_gpu", "checker_client", "seedlist_io",
        "cubiomes_session", "cubiomes_session_writer", "condition_types",
        "biome_catalog", "noise_palettes", "colormaps", "draw_engine",
        "topy_family", "topy_gpu", "lake_portals", "mc_checker", "session_confirm", "spawn_check",
        "feature116", "qtui.feature_confirm",
        # Qt: the stylesheet uses url(*.svg) → qsvg image-format plugin → Qt6Svg
        "PySide6.QtSvg",
        # map_engine noise resize
        "PIL.Image",
    ]
)
if importlib.util.find_spec("zstandard") is not None:   # .seeds (zstd) seed lists
    hiddenimports.append("zstandard")

# ── excludes: classic Tk UI + dev/diagnostic scripts + unused heavy packages ─
excludes = [
    "tkinter", "_tkinter", "turtle", "idlelib",
    # classic UI
    "gui", "main", "condition_editor", "drawing_tab", "drawing_engine",
    "drawing_native", "drawing_search", "presets", "criteria", "analyzer",
    "biome_picker", "structure_icons",
    # dev / validation scripts
    "gpu_diag", "check_first_ring", "check_ring_vs_snapped", "check_sisters",
    "compare_firstpos_118", "diag_strongholds_118", "measure_eye_spanning",
    "ruined_118", "scan_118", "scan_corrupt_118", "scan_ruined_118",
    "validate_118", "validate_seedlist_native", "tools", "tests",
    # never used by SSA
    "matplotlib", "scipy", "pandas", "IPython", "jupyter", "notebook", "pytest",
    "PyQt5", "PyQt6", "PySide2",
    # Qt modules SSA never imports (only QtCore/QtGui/QtWidgets + QtSvg plugin)
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech", "PySide6.QtNfc", "PySide6.QtHttpServer",
]

a = Analysis(
    [os.path.join(ROOT, "ssa_qt.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(ROOT, "pyi_rth_ssa.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,          # keep asserts/docstrings: SSA code relies on neither, but 0 is safest
)
# ── Linux: do not ship the build machine's system libraries ─────────────────
# PyInstaller copies every .so a module links to, including X11 / GTK / glib /
# fontconfig / dbus / libstdc++ from /usr/lib. Those need the build machine's
# glibc (2.38+ on a recent distro) and break the app on older ones, while every
# desktop Linux already has them. Keep only what comes from Python / the wheels
# (Qt, numpy, pillow…) and SSA's own libraries; the Qt plug-ins that only pull
# GTK / TLS / network in are dropped (SSA uses none of them).
# libcubiomes needs OpenMP: tools/build_linux.sh puts a portable libgomp
# (glibc 2.17) in tools/linux_vendor/, shipped as libgomp.so.1.
if not IS_WIN:
    _SYS = ("/lib/", "/lib64/", "/usr/lib/", "/usr/lib64/", "/usr/local/lib/")
    _DROP_PLUGINS = ("platformthemes/libqgtk3", "/tls/", "networkinformation/")

    def _keep(entry):
        dest, src = entry[0], entry[1]
        if any(x in dest.replace("\\", "/") for x in _DROP_PLUGINS):
            return False
        if src and src.startswith(_SYS) and "site-packages" not in src and "dist-packages" not in src:
            return False
        return True
    a.binaries = [e for e in a.binaries if _keep(e)]
    _gomp = os.path.join(ROOT, "tools", "linux_vendor", "libgomp.so.1")
    if os.path.isfile(_gomp):
        a.binaries.append(("libgomp.so.1", _gomp, "BINARY"))
    else:
        print("[spec] WARNING: tools/linux_vendor/libgomp.so.1 missing (run tools/build_linux.sh): "
              "the system's libgomp1 will be needed")

pyz = PYZ(a.pure)

ICON = os.path.join(ROOT, "ssa.ico")      # optional: add a .ico and it is used

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,               # onedir
    name="SimpleSeedAnalyser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                           # UPX breaks Qt/CUDA DLLs and triggers AV heuristics
    console=False,                       # windowed; stdout/stderr → console.log (runtime hook)
    disable_windowed_traceback=False,
    icon=ICON if os.path.isfile(ICON) else None,
    # version="file_version_info.txt",   # optional: VSVersionInfo lowers AV false positives
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SimpleSeedAnalyser",
)

# README / licences next to the executable (users see them; GPL + LGPL notices)
import shutil
for _f in ("README.md", "LICENSE", "THIRD_PARTY_LICENSES.txt"):
    _p = os.path.join(ROOT, _f)
    if os.path.isfile(_p):
        shutil.copy2(_p, os.path.join(DISTPATH, "SimpleSeedAnalyser", _f))
    else:
        print(f"[spec] WARNING: {_f} missing")
