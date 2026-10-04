# Broken end portal — measurement tools (03/10, research only, not used by SSA)

What breaks end-portal frames, measured with the real Minecraft 1.16.1 generation
(jellejurre's seed-checker, stronghold pinned to SSA's position).

* `gen.py N SEED` — random seeds -> `seed scx scz` + the 12 frame positions (1st stronghold, 1.16.5 cubiomes).
* `Scan.java` — per request: frame chunks decorated first, one by one, then the ring
  (radius 1). After every decoration the frames are re-read; each newly broken frame
  is attributed to (chunk, replacing block, ruined-portal reference in that chunk).
* `Scan2.java K` — same with K chunk orders (order 0 = frames first, others shuffled).
* `Lakes.java` — LAKES-step features of every 1.16.1 biome (water idx 0 + lava idx 1;
  desert / desert_hills / desert_lakes: lava idx 0 only).
* `lakes.py` — exact 1.16 lake RNG (population seed, decorator seed step 1, water
  1/4 y=rand(256), lava 1/8 y=rand(rand(248)+8) & (y<63 | 1/10), 4–7 ellipsoids),
  validated block-for-block on real lakes. No terrain checks (air drop, aborts).
* `lscan.py` / `feat.py` — lake candidates over frames, and the features of the real breaks.

Results: `random_scan_4400.txt` (4,400 random strongholds), `lake_candidates_60k.txt`
(1,961 lake candidates out of 60,000 strongholds), `lake_breaks_60k.txt` (the 17 real breaks).
Summary in the project doc `claude/SSA_portail_casse.md`.
