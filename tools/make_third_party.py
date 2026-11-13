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
    "Stronghold piece generator used to simulate portal rooms. The upstream\n"
    "repository does not state a licence. Used with credit.")
sec('Seedcracking from trees (tree_crack.py, cubiomes_src/treecrack*, tree_cuda.cu)',
    "SSA's tree cracker is a new implementation written for SSA, following the method\nof these tools (no code copied from them):\n\n  * TreeCracker and PopulationCrr — Andrew (Gaider10)\n    https://github.com/Gaider10/TreeCracker\n    https://github.com/Gaider10/PopulationCrr\n    The lattice over the first tree, the position / attribute windows, the exact\n    replay of the chunk's trees and the population chunk reversal. The upstream\n    repositories do not state a licence. Used with credit.\n  * tree-cracker — NelS (fork of the above, information estimates, 1.17 salt)\n    https://github.com/Nel-S/tree-cracker\n  * TreeCracking documentation — Epic10l2, with Edd (humhue)\n  * Tree decorator salts — Neil (hube12)\n  * Earlier tree crackers — Cortex (MCRcortex), TatertotGr8\n  * TextureRotations — 19MisterX98 (finding a forest's coordinates)\n    https://github.com/19MisterX98/TextureRotations\n  * SeedCandy — WearBlackAllDay (structure seed → world seeds)\n    https://github.com/WearBlackAllDay/SeedCandy\n\nThe tree models were checked against real Java 1.16.1, 1.16.5 and 1.17.1 worlds.\n\nStructures and End pillars (struct_crack.py, cubiomes_src/structcrack_core.h,\nend_pillars.py) — also new code, following:\n\n  * SeedCrackerX — 19MisterX98, after KaptainWutax's SeedCracker\n    https://github.com/19MisterX98/SeedcrackerX\n    Lifting the 20 low bits of the structure seed from the structures whose chunk\n    range is even, then searching the 28 high bits; the End pillar seed.\n  * cubiomes — Cubitect (structure configurations, positions and biome viability,\n    used to check every result)\n\nStructure anchors and the pillar model were checked on Java 1.16.1 – 1.21.4 servers.")
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
sec("Landscape photos — Unsplash (CC0 / Unsplash License)",
    """The five Landscapes themes use these photos (resized to 2560 px, assets/landscapes):

  Mountain Lake  — "A Large Body of Water Surrounded By Mountains", Peter Thomas
                   https://unsplash.com/photos/Dxod5pdRtsk — Unsplash License
  Fern Forest    — "Morskie Oko", https://unsplash.com/photos/_1UF_3TlKcQ — Unsplash License
  Ocean Shore    — Nattu Adnan, https://unsplash.com/photos/Ai2TRdvI6gM — Unsplash License
  Pier at Dusk   — "Sunset by the Pier", https://unsplash.com/photos/ces8_Bo7bhQ — CC0
  Desert Night   — Jared Evans, https://unsplash.com/photos/Wwg1TzCuV9E — CC0

Unsplash photos published before June 8, 2017 are CC0 (public domain). The later
ones are under the Unsplash License (https://unsplash.com/license): free to use,
modify and redistribute, not to be sold as they are. Sources and licences as listed
by the elementary OS and Pop!_OS wallpaper collections.""")
sec('Minecraft game data — Mojang Studios (loot_tables/)',
    "SSA reproduces Minecraft's world generation. To do so, loot_tables/ holds data\nread from the game's files (loot tables, structure templates' sizes, jigsaw pools\nand block lists, enchantment tables) and tests/server_truth/ holds what real\nservers generated. This data belongs to Mojang Studios / Microsoft; it is not\ncovered by SSA's GPL licence and is only used for compatibility. No game code,\ntexture or sound is shipped: the item and block pictures are read from the user's\nown Minecraft installation. SSA is not affiliated with Mojang Studios or Microsoft.")
sec("PyInstaller bootloader — GPL-2.0 with the PyInstaller bootloader exception",
    "The executable launcher is PyInstaller's bootloader, distributed under the GPL v2\n"
    "with an exception allowing it to be used with any program. https://pyinstaller.org")
open(os.path.join(ROOT, "THIRD_PARTY_LICENSES.txt"), "w", encoding="utf-8").write("".join(out))
print("THIRD_PARTY_LICENSES.txt written")
