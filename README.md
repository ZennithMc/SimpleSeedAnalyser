## SimpleSeedAnalyser (SSA)  v1.0.0
A free seedfinding tool for Minecraft Java (1.16 → 26.3, Overworld, Large Biomes), built on **Cubiomes**.
Think of it as an improved Cubiomes Viewer: many additional seedfinding features, and it will keep getting updates.


## What's new in SSA

- **Speed**: major optimisations for structure and biome conditions (up to ~3× faster on some sessions).
- **Strongholds**: eye count, top Y, broken end portals (1.16), their full layout shown on the map, and more.
- **Customisation**: 35+ themes and 14 noise map palettes, plus an editor to create and share your own.
- **Crunchiness**: crunchiness viewer and session condition, GPU-accelerated (CUDA), plus noise tiling support thanks to a fixed spiral iterator.
- **Drawing**: draw a terrain and find seeds that match it.
- **Statistics**: see how much time each condition takes, the share of seeds that pass it and more, to debug and improve your sessions.
- **Noise**: more visualisation options, octaves "up to" or "only", and the gradient vectors that form each octave can be shown, with their own settings.
- **Exact Y**: maps for both pre-1.18 and 1.18+, including cave systems (up to the final density).
- **More session conditions**: locate climate centre, exact Y, crunchiness, lava lakes, dungeons.

And many smaller quality-of-life features like a button to check every stronghold of a seed instantly with their details, or two search start modes: "Saved" (Cubiomes Viewer's system, slightly improved) or "Random starting seed", handy for experimenting,..


## Download
- **Windows 10 / 11**: `SimpleSeedAnalyser-windows.zip` — unzip and run `SimpleSeedAnalyser.exe` (unsigned: *More info → Run anyway*).
- **Linux x86-64** (glibc 2.34+): `SimpleSeedAnalyser-linux-x86_64.tar.gz` — extract and run `./SimpleSeedAnalyser/SimpleSeedAnalyser`.
- CPU with AVX2. GPU optional: NVIDIA RTX 20 series or newer, driver 580+.


## License & Credits

### License
SimpleSeedAnalyser is free software under the **GNU General Public License v3** (see `LICENSE`): you can use, study, share and modify it; if you distribute a modified version, its source code must be published under the same license. Bundled third-party components keep their own licenses (see `THIRD_PARTY_LICENSES.txt`).

### Credits
SimpleSeedAnalyser stands on the shoulders of the Minecraft seedfinding community.

- **[Cubiomes](https://github.com/Cubitect/cubiomes)** by Cubitect (MIT) — the C library that powers biome generation, structure placement and noise sampling, compiled into SSA's engine. Terrain height uses the **[xpple fork](https://github.com/xpple/cubiomes)**.
- **[Cubiomes Viewer](https://github.com/Cubitect/cubiomes-viewer)** by Cubitect — the reference seedfinder that inspired SSA's condition system, area modes and noise overlays, and whose `.session` files SSA can import. SSA's code is written from scratch.
- **[12-eye](https://github.com/Gaider10/12-eye)** by Gaider10 — the stronghold piece simulator used to find exact portal rooms (CPU and GPU layout search).
- **[seed-checker](https://github.com/jellejurre/seed-checker)** by jellejurre — runs the real Minecraft world generation to confirm results (broken portals, spawn checks). SSA ships a modified copy of its `SeedChunkGenerator.java` (`tools/checker/overrides`).
- **[Qt / PySide6](https://www.qt.io)** (LGPL-3.0), **NumPy**, **Pillow**, **python-zstandard**, **PyInstaller**.
- The **Minecraft seedfinding community** — for documenting the algorithms (LCG seeds, stronghold rings, biome noise) that make tools like SSA possible.
- Minecraft is a trademark of **Mojang Studios / Microsoft**. SSA is an unofficial third-party tool, not affiliated with them.

## Feedback
If you are encountering any issue, contact me via Discord: @zennith7402
Also looking for feedback to improve SSA by fixing bugs or adding new features, if you have any suggestion feel free to reach out to me.
