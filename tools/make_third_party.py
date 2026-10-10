"""Regenerate THIRD_PARTY_LICENSES.txt from the licences of what the release
bundles (run in the build environment: numpy / pillow / zstandard / PySide6
must be importable).    python tools/make_third_party.py"""
import glob, importlib.metadata as md, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = []
def sec(title, body):
    out.append("=" * 78 + "\n" + title + "\n" + "=" * 78 + "\n\n" + body.strip() + "\n\n")

def read(p):
    return open(p, encoding="utf-8", errors="replace").read()

out.append("""SimpleSeedAnalyser — third-party software
==========================================

SimpleSeedAnalyser is licensed under the GNU GPL v3 (see LICENSE). The release
bundles the following components, each under its own licence, reproduced below.

""")
sec("cubiomes — Cubitect (MIT) — biome generation, structures, noise (compiled into libcubiomes)\n"
    "https://github.com/Cubitect/cubiomes", read(os.path.join(ROOT, "cubiomes_src", "LICENSE")))
sec("cubiomes fork by xpple (MIT) — terrain / surface height (compiled into terrain)\n"
    "https://github.com/xpple/cubiomes", read(os.path.join(ROOT, "cubiomes_terrain", "LICENSE_xpple_cubiomes")))
sec("12-eye / stronghold generator — Gaider10 (compiled into gaider_layouts)\n"
    "https://github.com/Gaider10/12-eye",
    "Stronghold piece generator used to simulate portal rooms. Used with credit;\n"
    "licence to be confirmed with the author before a public release.")
sec("seed-checker — jellejurre (Minecraft check, tools/checker)\n"
    "https://github.com/jellejurre/seed-checker",
    "Used to confirm results with the real Minecraft world generation. SSA ships a\n"
    "modified copy of its SeedChunkGenerator.java (tools/checker/overrides); the\n"
    "upstream repository does not state a licence. Used with credit.")
sec("Qt 6 and PySide6 / Shiboken6 — The Qt Company (LGPL-3.0)\nhttps://www.qt.io  ·  https://pypi.org/project/PySide6/",
    "SimpleSeedAnalyser uses Qt 6 through PySide6 under the GNU Lesser General Public\n"
    "License v3. The Qt / PySide6 libraries are shipped unmodified as separate shared\n"
    "libraries (_internal/PySide6) and can be replaced by the user with another\n"
    "compatible build. Their source code is available from https://download.qt.io and\n"
    "https://code.qt.io. The full LGPL v3 text follows; it supplements the GNU GPL v3\n"
    "(see LICENSE).\n\n" + read("/usr/share/common-licenses/LGPL-3")
    if os.path.exists("/usr/share/common-licenses/LGPL-3") else "See https://www.gnu.org/licenses/lgpl-3.0.txt")
for dist, title in (("numpy", "NumPy (BSD-3-Clause and bundled components)"),
                    ("pillow", "Pillow (MIT-CMU)"), ("zstandard", "python-zstandard (BSD-3-Clause)")):
    try:
        d = md.distribution(dist)
        files = [str(f) for f in (d.files or []) if "licenses/" in str(f) and not str(f).endswith("/")]
        body = "\n\n".join(f"--- {os.path.basename(f)} ---\n" + read(d.locate_file(f)) for f in files)
        sec(f"{title} — version {d.version}", body or "(licence file not found)")
    except md.PackageNotFoundError:
        print("missing", dist, file=sys.stderr)
lic = glob.glob(os.path.join(sys.base_prefix, "lib", "python3*", "LICENSE.txt")) or \
      glob.glob(os.path.join(sys.base_prefix, "LICENSE.txt"))
sec("Python %d.%d — Python Software Foundation License" % sys.version_info[:2],
    read(lic[0]) if lic else "https://docs.python.org/3/license.html")
sec("GCC runtime (libgomp, libgcc) — GPL-3.0 with the GCC Runtime Library Exception",
    "The OpenMP runtime (libgomp) and GCC support libraries are distributed under the\n"
    "GNU GPL v3 with the GCC Runtime Library Exception, which allows shipping them with\n"
    "programs under any licence. https://www.gnu.org/licenses/gcc-exception-3.1.html")
sec("PyInstaller bootloader — GPL-2.0 with the PyInstaller bootloader exception",
    "The executable launcher is PyInstaller's bootloader, distributed under the GPL v2\n"
    "with an exception allowing it to be used with any program. https://pyinstaller.org")
open(os.path.join(ROOT, "THIRD_PARTY_LICENSES.txt"), "w", encoding="utf-8").write("".join(out))
print("THIRD_PARTY_LICENSES.txt written")
