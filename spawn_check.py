"""
spawn_check.py — where a new player really appears in 1.16 (09/10).

Minecraft 1.16 places the world spawn with setupSpawn: a spawn biome within
256 blocks of (0, 0), then a 32×32-chunk spiral of columns whose top block is
exactly the biome's top material (grass block / podzol only). When nothing is
found, the spawn stays at the FALLBACK point (8, 64, 8) — inside the terrain.
The joining player (ServerPlayerEntity.moveToSpawn) then tries the 21×21
columns around it in a random order, this time WITHOUT the grass rule: any
column whose top block is the biome's top material (sand, red sand…) takes
him, on the surface. Only when none fits does he stay at (8.5, 65, 8.5),
inside the terrain, where nothing moves him up: in a cave, in lava, in water,
in a structure or stuck in the stone. These are the "wtf" spawns.

  prefilter_conditions()  the two biome conditions SSA searches with (cubiomes,
                          every thread): no ocean / beach / mushroom / desert
                          biome at (-2..18, -2..18) and no biome with a valid grass top in
                          the spiral's area (-256..271)
  SpawnCheck              the exact answer, from the real Minecraft 1.16.1
                          generation (tools/checker/SpawnWorker.java, warm
                          worker processes of mc_checker)
  classify(res)           ("lava" | "water" | "cave" | "structure" | "stuck"
                          | "surface" | "chance" | "normal", text, tooltip)
"""
from __future__ import annotations

import threading

# cubiomes ids (1.16) — biomes whose surface has grass blocks (or podzol) the
# spawn search accepts: excluded around the spawn (a spawn would be found)
GRASS_BIOMES = [1, 3, 4, 5, 6, 7, 11, 12, 13, 18, 19, 20, 21, 22, 23, 27, 28, 29, 30, 31,
                32, 33, 34, 35, 36, 129, 131, 132, 133, 134, 149, 151, 155, 156, 157, 158,
                160, 161, 162, 163, 164, 168, 169]
# water / sand / mycelium near the fallback point: the player would just be at
# sea level on the surface, or taken to a sand column — excluded in the 21×21
# columns the joining player tries (desert = sand top everywhere)
SURFACE_BIOMES = [0, 2, 10, 14, 15, 16, 17, 24, 25, 26, 44, 45, 46, 47, 48, 49, 50, 130]
CENTRE_BOX = (-2, -2, 18, 18)
SPIRAL_BOX = (-256, -256, 271, 271)          # 16 chunks around chunk (0, 0) + the 256 search
LABEL_CENTRE = "Spawn 1.16 · land at the fallback point"
LABEL_AREA = "Spawn 1.16 · no valid spawn nearby"


def is_116(mc):
    import cubiomes_wrap as cw
    names = [n for n, v in cw.MC_VERSIONS.items() if v == int(mc)]
    return any("1.16" in n for n in names)


def prefilter_conditions():
    """The two session conditions (engine format) of the preset."""
    from qtui.session_model import new_condition
    c = new_condition("biome_filter")
    x1, z1, x2, z2 = CENTRE_BOX
    c.update(area_mode=2, x1=x1, z1=z1, x2=x2, z2=z2, label=LABEL_CENTRE)
    c["params"]["biomes_incl"] = []
    c["params"]["biomes_excl"] = list(SURFACE_BIOMES)
    d = new_condition("biome_filter")
    x1, z1, x2, z2 = SPIRAL_BOX
    d.update(area_mode=2, x1=x1, z1=z1, x2=x2, z2=z2, label=LABEL_AREA)
    d["params"]["biomes_incl"] = []
    d["params"]["biomes_excl"] = list(GRASS_BIOMES)
    return [c, d]


LABEL_M_NEAR = "Mansion spawn 1.16 · a mansion near (0, 0)"
LABEL_M_SPAWN = "Mansion spawn 1.16 · the spawn"
LABEL_M_AT = "Mansion spawn 1.16 · a mansion at the spawn"
MANSION_NEAR = 400          # the mansion of the region near (0, 0) (cheap, tested first)
MANSION_AT = 80             # its position (a corner of the start piece) around the spawn


def mansion_conditions():
    """The mansion preset (09/10): [(condition, index of its parent in this list
    or None)]. The 1.16 spawn search goes down THROUGH a mansion to the grass
    under its cobblestone foundations; around a spawn there, most of the 21×21
    columns the joining player tries collide — sometimes all of them: he is
    left inside the mansion, its floor or its foundations. About 1 % of these
    seeds are such spawns (753 tested in Minecraft, 09/10)."""
    from qtui.session_model import new_condition
    a = new_condition("mansion")
    a.update(area_mode=2, x1=-MANSION_NEAR, z1=-MANSION_NEAR, x2=MANSION_NEAR, z2=MANSION_NEAR,
             label=LABEL_M_NEAR)
    b = new_condition("spawn")
    r = MANSION_NEAR + MANSION_AT
    b.update(area_mode=2, x1=-r, z1=-r, x2=r, z2=r, label=LABEL_M_SPAWN)
    c = new_condition("mansion")
    c.update(area_mode=2, x1=-MANSION_AT, z1=-MANSION_AT, x2=MANSION_AT, z2=MANSION_AT,
             label=LABEL_M_AT)
    return [(a, None), (b, None), (c, 1)]


LABEL_FEATURE = {"dungeon": "Spawn 1.16 · a dungeon on the fallback point",
                 "lava_lake": "Spawn 1.16 · a lava lake on the fallback point"}


def feature_conditions(kind):
    """The buggy-spawn biome conditions + the fallback-point feature (09/10).
    The feature condition only depends on the 48 low bits: first in the order
    (cost 0.1 µs), it lets ~1.5 % (dungeon) / ~0.005 % (lava lake) of the
    seeds reach the biome conditions."""
    from qtui.session_model import new_condition
    f = new_condition("spawn_feature116")
    f["params"]["kind"] = kind
    f["label"] = LABEL_FEATURE[kind]
    return [(f, None)] + [(c, None) for c in prefilter_conditions()]


def session_wants_check(conds):
    labs = {c.get("label", "") for c in conds if c.get("enabled", True)}
    return bool(labs & {LABEL_AREA, LABEL_M_AT})


def parse(line):
    p = line.split()
    if p and p[0] == "FRES":                       # (10/10) feature check answer
        # dungeon: 0 / 1; lava lake: "‰:Y" (Y = its real top lava layer)
        res = []
        for v in p[3:3 + int(p[2])]:
            a, _, b = v.partition(":")
            res.append((int(a), int(b)) if b else int(a))
        return dict(feat=True, seed=int(p[1]), res=res)
    if not p or p[0] != "RES":
        raise RuntimeError(line.strip() or "no answer from Minecraft")
    return dict(seed=int(p[1]), fallback=p[2] == "1", sx=int(p[3]), sy=int(p[4]),
                sz=int(p[5]), py=int(p[6]), feet=p[7], head=p[8], covered=p[9] == "covered",
                sky=int(p[10]), around=[] if p[11] == "-" else p[11].split(","),
                ms=int(p[12]),
                mode=p[13] if len(p) > 13 else "stay",
                free=int(p[14]) if len(p) > 14 else 0,
                blocked=int(p[15]) if len(p) > 15 else 0,
                px=int(p[16]) if len(p) > 16 else int(p[3]),
                pz=int(p[17]) if len(p) > 17 else int(p[5]),
                stuck=len(p) > 18 and p[18] == "1",
                roofed=int(p[19]) if len(p) > 19 else 0,
                roofs=[] if len(p) <= 20 or p[20] == "-" else p[20].split(","),
                rpos=tuple(int(v) for v in p[21:24]) if len(p) > 23 else (0, 0, 0),
                feetT=_tally(p[24]) if len(p) > 24 else {},
                headT=_tally(p[25]) if len(p) > 25 else {},
                partial=len(p) > 26 and p[26] == "partial")


def _tally(s):
    if s == "-":
        return {}
    out = {}
    for it in s.split(","):
        k, _, v = it.rpartition(":")
        out[k] = int(v)
    return out


# blocks worth telling when the player can land in them (no free column)
_NOTABLE = ("lava", "water", "obsidian", "spawner", "diamond_block", "chest", "tnt",
            "crying_obsidian", "bookshelf", "cobweb", "gold_block", "iron_bars")


def _notable(res):
    """'obsidian at the head 2/441, …' over the colliding columns."""
    n = res.get("blocked") or 0
    out = []
    for where, t in (("head", res.get("headT", {})), ("feet", res.get("feetT", {}))):
        for k in _NOTABLE:
            if t.get(k):
                out.append(f"{k} at the {where} {t[k]}/{n}")
    return ", ".join(out)


_STRUCT = ("planks", "cobblestone", "bricks", "cut_sandstone", "chiseled", "smooth_sandstone",
           "stairs", "slab", "fence", "rail", "torch", "chest", "bookshelf", "wool", "glass",
           "bell", "lantern", "hay_block", "door", "trapdoor", "ladder", "carpet",
           "stripped", "spawner", "mossy", "cracked", "infested", "obsidian", "iron_bars",
           "wall", "lectern", "bed", "barrel", "composter", "furnace", "crafting_table")


def _roof_kind(blocks):
    return "structure" if any(any(k in b for k in _STRUCT) for b in blocks) else "cave"


def _classify_normal(res, where):
    """A grass block was found — but the search goes down THROUGH everything
    (roofs, floors, foundations): grass under a mansion's foundations or under
    an overhang counts, and the joining player can only take the columns where
    he does not collide. Tooltips kept short (10/10)."""
    free, blocked, roofed = res["free"], res["blocked"], res["roofed"]
    base = f"Spawn on grass ({res['sx']} {res['sy']} {res['sz']}) · {free} free / {blocked} blocked columns"
    roofs = ", ".join(res["roofs"][:4])
    rp = "{} {} {}".format(*res["rpos"])
    if free == 0 and blocked > 0:
        cat = "structure" if _roof_kind(res["around"]) == "structure" else "stuck"
        note = _notable(res)
        head = ", ".join(f"{k} {v}" for k, v in list(res.get("headT", {}).items())[:4])
        return (cat, f"inside blocks · all {blocked} col." + (" · " + note.split(",")[0] if note else ""),
                base + "\nNo free column: he stays inside the blocks (random column)"
                + (f"\nHead: {head}" if head else "") + (f"\n{note}" if note else ""))
    if free and roofed == free:
        kind = _roof_kind(res["roofs"])
        return (kind, ("under a structure" if kind == "structure" else "under a roof")
                + f" · {free} spot" + ("s" if free > 1 else ""),
                base + f"\nAll free ones under {roofs} · e.g. {rp}")
    if roofed:
        # "chance" (kept by a buggy-spawn search) only when he lands under the
        # roof at least half the time; below, an ordinary spawn
        return ("chance" if 2 * roofed >= free else "normal", f"normal · {roofed}/{free} under a roof",
                base + f"\n{100 * roofed // free} % under {roofs} · e.g. {rp}")
    return "normal", "normal", base + "\nAll free ones under the sky or a tree"


def classify(res):
    """(category, short text, tooltip) of a SpawnWorker answer."""
    x, y, z = res["px"], res["py"], res["pz"]
    where = f"{x} {y} {z}"
    feet, head = res["feet"], res["head"]
    if not res["fallback"]:
        return _classify_normal(res, where)
    env = (f"\nFeet {feet} · head {head} · "
           + ("open sky" if not res["covered"] else f"roof at y {res['sky']}"))
    if res["mode"] == "random":
        n = "" if res.get("partial") else f" ({res['free']} col.)"
        return ("surface", "surface · random" + n,
                f"Fallback (8, 64, 8) → a sand / red sand column{n}: surface, at random\n"
                f"Nearest: {where}")
    feet_bad = res.get("stuck") and feet not in ("lava", "water") and head not in ("lava", "water")
    st = sorted({b for b in res["around"] if any(k in b for k in _STRUCT)})
    if "lava" in (feet, head):
        cat, txt = "lava", f"lava · y {y}"
    elif "water" in (feet, head) or feet.endswith("water"):
        cat, txt = "water", f"water · y {y}"
    elif "spawner" in (feet, head):
        cat, txt = "structure", f"in the spawner · y {y}"
    elif st:
        dung = "spawner" in res["around"] or "mossy_cobblestone" in res["around"]
        cat, txt = "structure", (f"dungeon · y {y}" if dung else f"structure · y {y}")
    elif feet_bad:
        cat, txt = "stuck", f"in the stone · y {y}"
    elif res["covered"]:
        cat, txt = "cave", f"underground · y {y}"
    else:
        cat, txt = "surface", f"surface · y {y}"
    how = ("Fallback (8, 64, 8), all columns collide → random one" if res["mode"] == "blocked"
           else f"Fallback (8, 64, 8), no column fits → stays at {where}")
    return cat, txt, how + env + (f"\nAround: {', '.join(st[:4])}" if st else "")


_POOL = None
_LOCK = threading.Lock()


def pool():
    """Warm SpawnWorker processes (same Minecraft 1.16.1 jars as the portal checker)."""
    global _POOL
    import mc_checker
    with _LOCK:
        if _POOL is None or _POOL.closed:
            _POOL = mc_checker.CheckerPool(main="SpawnWorker", parse=parse)
        return _POOL


def status():
    import mc_checker
    return mc_checker.status()


def check(seed, callback):
    """callback(result dict | None, error | None) on a worker thread."""
    pool().submit_line(str(int(seed)), callback)


def check_many(seeds, timeout=600.0):
    """{seed: result} (blocking — tests / tools)."""
    done = threading.Event()
    out, left, lk = {}, [len(seeds)], threading.Lock()

    def cb_for(s):
        def cb(res, err):
            with lk:
                out[s] = res if res is not None else {"error": err}
                left[0] -= 1
                if left[0] == 0:
                    done.set()
        return cb
    for s in seeds:
        check(s, cb_for(int(s)))
    if seeds:
        done.wait(timeout)
    return out
