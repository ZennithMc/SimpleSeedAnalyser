# SimpleSeedAnalyser (SSA)

Hi, I'm Zennith. I am building this tool with the ambition of combining existing SeedFinding programs with additional features.

First, I want to clarify that this project is HIGHLY inspired by and dependent on Cubiomes by Cubitect. Nothing would have been possible without his incredible contributions to the SeedFinding community. I also want to credit Gaider10 from Andrew, who inspired and helped a ton with the stronghold work integrated into SSA, both on CPU and GPU.

As a SeedFinder, I have been using Cubiomes Viewer for years and have had an amazing experience with it. However, other SeedFinders and I feel that Cubiomes Viewer is missing several important features that could save a lot of time and open up more possibilities, especially for beginners.
For this reason, my goal with SSA is to recreate a complete SeedFinding tool that is updated more frequently while adding features currently missing from Cubiomes Viewer.

I will be listening closely to feedback in order to add even more requested features. You can reach out to me on Discord: **@zennith7402**.
Everyone will be able to use SSA freely. I want this project to remain public once it reaches a state where it is usable without too many bugs or issues.

Lastly, I need to mention that I am NOT an experienced programmer. In fact, this project has been developed mainly with the help of Claude AI. My work is nowhere near the level of what Cubitect has achieved, and once again, this project primarily builds upon existing programs.

Thank you for your understanding. I hope this tool will be useful. Enjoy your SeedFinding sessions!

> **Status: private beta.** Please report anything odd (with the seed, the version and what you did) on Discord.

---

## ⬇️ Install and run

Nothing to install — no Python, no compiler.

### Windows 10 / 11 (64-bit)
1. Download `SimpleSeedAnalyser-windows.zip` from the **Releases** page and unzip it anywhere (e.g. `Documents\SimpleSeedAnalyser`).
2. Double-click **`SimpleSeedAnalyser.exe`**.
3. The first time, Windows may show *“Windows protected your PC”*: the program is not signed (signing costs money). Click **More info → Run anyway**.

Keep the whole folder together: the `.exe` needs its `_internal` folder next to it.

### Linux (x86-64)
1. Download `SimpleSeedAnalyser-linux-x86_64.tar.gz` and extract it: `tar xzf SimpleSeedAnalyser-linux-x86_64.tar.gz`
2. Run `./SimpleSeedAnalyser/SimpleSeedAnalyser`
- Needs glibc 2.34 or newer (Ubuntu 22.04+, Debian 12+, Fedora 35+, Linux Mint 21+, Arch…).
- If it says *“could not load the Qt platform plugin xcb”*, install the missing Qt helper: `sudo apt install libxcb-cursor0` (Debian / Ubuntu / Mint) or `sudo dnf install xcb-util-cursor` (Fedora).
- GPU: same as Windows (NVIDIA RTX 20+, driver 580+, proprietary NVIDIA driver); otherwise the CPU is used.

### Requirements
- **CPU:** 64-bit with **AVX2** (Intel 2013+ / AMD 2015+). SSA uses every core.
- **GPU (optional, Windows and Linux):** NVIDIA **RTX 20 series or newer** with an up-to-date driver (**580 or newer**). Without it SSA runs on the CPU; the top bar shows *GPU* or *CPU only*.
- **RAM:** 4 GB is enough; big seed lists and 1.18+ stronghold scans like more.

### Where SSA keeps your things
Sessions, library, drawings, palettes and settings are stored in your user folder, not in the program folder:
`%APPDATA%\SimpleSeedAnalyser` on Windows, `~/.local/share/SimpleSeedAnalyser` on Linux. Updating SSA = replacing the program folder; your data stays.

### Something wrong?
- **Self-test:** open a terminal in the SSA folder and run `SimpleSeedAnalyser.exe --selftest` (Linux: `./SimpleSeedAnalyser --selftest`). It checks the engines with known answers and the GPU, then shows a report (also saved as `selftest.txt`).
- **Logs:** `%LOCALAPPDATA%\SimpleSeedAnalyser\console.log` and `crash.log` (Linux: `~/.local/state/SimpleSeedAnalyser/`). Send them with your report.

---

## ✨ What SSA does

**Versions:** Java 1.16 → 26.3 (Overworld). **Large Biomes** supported everywhere.

### 🗺 Map
- Fast, smooth biome map (zoom on the cursor, inertia) with structures, spawn, the 128 strongholds and 0,0.
- Per-structure toggles, icon size, **abandoned (zombie) villages** with their own icon, an on-screen **legend** with counts.
- **Noise overlays** (1.18+): Temperature, Humidity, Continentalness, Erosion, Weirdness, approximate height and **exact surface Y**, with 8+ palettes, a full **palette editor** and an opacity slider. Octave arrows (gradient / lattice) for climate noises.
- **Stronghold details** on click: eyes of ender in the portal, portal room position, top Y, and the whole list of 128 strongholds.
- Right-click: copy coordinates or a `/tp`, go to coordinates (Ctrl+G).
- **Tools:** paste a seed + coordinates from anything (chat line, F3+C, “X: … Z: …”) — **Ctrl+Shift+V**; **ruler** to measure distances with Nether equivalents — **M**; fullscreen — **F11**.

### 🔎 Sessions (custom seed search)
- Conditions like Cubiomes Viewer, and more:
  - **Structures:** village (normal / abandoned), outpost, pyramids, temples, swamp hut, igloo (basement), mansion, monument, shipwreck, buried treasure, ocean ruin, trail ruins, ruined portal, ancient city, trial chambers, abandoned camp, first stronghold.
  - **Biomes / climate:** overworld at scale, biome samples (e.g. *99.5 % coverage*), locate biome center, locate climate extreme / center, climate noise samples, crunchiness, approximate Y, **exact Y**.
  - **World / helpers:** spawn, slime chunk, spiral iterator, **OR / NOT** gates, scale to Nether / Overworld.
- Conditions can be relative to each other (a village next to a monument next to the spawn…).
- **Native C engine**, multi-threaded, with **GPU (CUDA) pre-filters** for structures and climate; an **Engine** analysis suggests the fastest mode (Incremental or 48-bit family) for your session.
- **Pause / resume** exactly where it stopped, even after closing SSA (the session comes back at the next launch).
- Search a range or a **seed list** (`.txt` or compact `.seeds`; big text lists can be converted in one click).
- Results linked to the map: click a seed to see where each condition is satisfied, show / hide conditions on the map.
- **Session library**, `.session` files, and **import of Cubiomes Viewer sessions**.

### 👁 Stronghold
- Search seeds by **eye count** (up to 12/12), **top Y** of the portal, or **broken portals** (corruptible end portals).
- CPU and **GPU** (billions of seeds per second for 12-eye layouts on an RTX card).
- First stronghold or all 128, range or seed list, export of the results.

### 🖌 Draw
- **Draw a terrain** (biomes / shapes) over the map and let SSA find the seeds that look like it.
- Drawing library, undo / redo, weights, test the current seed.

### ⌨️ Shortcuts
Press **F1** in SSA for the full list.

---

## 🛠 Building from source

Only needed if you want to change SSA. Windows:

```
py -m pip install -U pyinstaller pyinstaller-hooks-contrib PySide6-Essentials numpy pillow zstandard
py tools/build_native.py --cubiomes          (MinGW-w64 gcc / g++ on PATH)
py tools/build_native.py --check             (every unit must say "ok"; GPU units need the CUDA Toolkit)
py ssa_qt.py                                 (run from source)
py -m PyInstaller --noconfirm --clean SimpleSeedAnalyser.spec   → dist\SimpleSeedAnalyser\
```

Linux: `bash tools/build_linux.sh` → `dist/SimpleSeedAnalyser-linux-x86_64.tar.gz` (gcc / g++ needed).

### Project structure
| Path | Purpose |
|---|---|
| `ssa_qt.py` | Entry point (Qt interface). |
| `qtui/` | The interface: main window, map, sessions, stronghold, draw, dialogs, themes. |
| `custom_search.py`, `search_worker.py` | Session search: dispatch between the C engine, the GPU pre-filters and the workers. |
| `stronghold_eyes.py`, `ruined_portal.py` | Stronghold eye / top-Y / broken-portal searches. |
| `cubiomes_wrap.py` | ctypes wrapper around `libcubiomes` (cubiomes + SSA's C engine). |
| `cubiomes_src/` | C / C++ / CUDA sources: cubiomes, the session engine, Gaider10's generator, CUDA kernels. |
| `cubiomes_terrain/`, `terrain.py` | Exact surface height (terrain) library. |
| `noise_gpu.py`, `structure_cuda.py`, `gaider_cuda.py`, `native_libs.py` | GPU libraries and their loading / self-test. |
| `tools/` | Build scripts. |

---

## 📜 License & Credits

### License
SimpleSeedAnalyser is free software under the **GNU General Public License v3** (see `LICENSE`): you can use, study, share and modify it; if you distribute a modified version, its source code must be published under the same license. Bundled third-party components keep their own licenses (see `THIRD_PARTY_LICENSES.txt`).

### Credits
SimpleSeedAnalyser stands on the shoulders of the Minecraft seedfinding community.

- **[Cubiomes](https://github.com/Cubitect/cubiomes)** by Cubitect (MIT) — the C library that powers biome generation, structure placement and noise sampling, compiled into SSA's engine. Terrain height uses the **[xpple fork](https://github.com/xpple/cubiomes)**.
- **[Cubiomes Viewer](https://github.com/Cubitect/cubiomes-viewer)** by Cubitect — the reference seedfinder that inspired SSA's condition system, area modes and noise overlays, and whose `.session` files SSA can import. SSA's code is written from scratch.
- **[12-eye](https://github.com/Gaider10/12-eye)** by Gaider10 — the stronghold piece simulator used to find exact portal rooms (CPU and GPU layout search).
- **[Qt / PySide6](https://www.qt.io)** (LGPL-3.0), **NumPy**, **Pillow**, **python-zstandard**, **PyInstaller**.
- The **Minecraft seedfinding community** — for documenting the algorithms (LCG seeds, stronghold rings, biome noise) that make tools like SSA possible.
- Minecraft is a trademark of **Mojang Studios / Microsoft**. SSA is an unofficial third-party tool, not affiliated with them.
