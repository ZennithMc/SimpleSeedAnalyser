"""
jigsaw_plan.py — (31/10) the top view of a village / pillager outpost / camp for the
map: each column of each template piece coloured like a Minecraft map (the block's
MapColor, shaded by the height of the block north of it), the buildings outlined by a
soft shadow, the streets, fields, water, the trees and piles (feature pieces), and the
loot chests. Data: loot_tables/jigsaw_tops.json.gz (tools/build_jigsaw_tops.py).

    plan(seed, mc, key, cx, cz) -> dict(x0, z0, w, h, rgba (bytes, w × h × 4),
                                        chests [(x, y, z, table)], boxes [...]) | None
"""
from __future__ import annotations

import gzip
import json
import os

_TOPS = None

# Minecraft MapColor (base colours)
_MC = dict(GRASS=(127, 178, 56), SAND=(247, 233, 163), WOOL=(199, 199, 199), ICE=(160, 160, 255),
           METAL=(167, 167, 167), PLANT=(0, 124, 0), SNOW=(255, 255, 255), CLAY=(164, 168, 184),
           DIRT=(151, 109, 77), STONE=(112, 112, 112), WATER=(64, 64, 255), WOOD=(143, 119, 72),
           QUARTZ=(255, 252, 245), ORANGE=(216, 127, 51), YELLOW=(229, 229, 51),
           LIGHT_GREEN=(127, 204, 25), GRAY=(76, 76, 76), LIGHT_GRAY=(153, 153, 153),
           BROWN=(102, 76, 51), GREEN=(102, 127, 51), RED=(153, 51, 51), GOLD=(250, 238, 77),
           PODZOL=(129, 86, 49), T_WHITE=(209, 177, 161), T_ORANGE=(159, 82, 36),
           T_YELLOW=(186, 133, 36), CYAN=(76, 127, 153), LAVA=(255, 0, 0), PALE=(220, 220, 210))
_WOODS = (("dark_oak", "BROWN"), ("spruce", "PODZOL"), ("birch", "SAND"), ("jungle", "DIRT"),
          ("acacia", "ORANGE"), ("cherry", "T_WHITE"), ("pale_oak", "QUARTZ"), ("poplar", "SAND"),
          ("bamboo", "YELLOW"), ("oak", "WOOD"))
# roles: 0 skip, 1 building, 2 path, 3 field, 4 water, 5 ground / plants
_SKIP = ("torch", "candle", "pressure_plate", "sculk_vein", "banner", "button", "ladder", "vine", "flower", "tulip", "daisy", "poppy",
         "dandelion", "orchid", "allium", "bluet", "cornflower", "lily_of_the_valley", "rose_bush",
         "lilac", "peony", "grass", "fern", "dead_bush", "sapling", "petals", "seagrass", "mushroom",
         "wildflowers", "eyeblossom", "carpet", "cobweb", "litter", "trapdoor", "lily_pad",
         "dry_grass", "cactus_flower", "bush", "lantern", "iron_bars", "tripwire", "rail")


def _tops():
    global _TOPS
    if _TOPS is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "jigsaw_tops.json.gz")
        with gzip.open(p, "rt") as f:
            _TOPS = json.load(f)
        _TOPS["_c"] = {}
        _TOPS["_style"] = [_style(n) for n in _TOPS["names"]]
    return _TOPS


# (31/10) the Nether blocks: their texture's mean colour (MapColor makes them all black)
_TEX = {"polished_blackstone_bricks": (50, 43, 51), "cracked_polished_blackstone_bricks": (43, 37, 44),
        "polished_blackstone_brick_stairs": (50, 43, 51), "polished_blackstone_brick_slab": (50, 43, 51),
        "polished_blackstone_brick_wall": (50, 43, 51), "blackstone": (42, 35, 41),
        "blackstone_slab": (42, 35, 41), "blackstone_stairs": (42, 35, 41), "blackstone_wall": (42, 35, 41),
        "polished_blackstone": (56, 50, 58), "chiseled_polished_blackstone": (56, 50, 58),
        "gilded_blackstone": (78, 55, 38), "basalt": (82, 82, 88), "polished_basalt": (100, 99, 103),
        "magma_block": (142, 63, 31), "gold_block": (246, 208, 61), "soul_sand": (81, 62, 50),
        "nether_wart": (165, 27, 32), "glowstone": (171, 131, 84), "nether_bricks": (44, 22, 26),
        "crimson_planks": (101, 48, 70), "warped_planks": (43, 104, 99),
        # (31/10) the ancient cities (deepslate, basalt, wool, sculk)
        "deepslate_bricks": (70, 70, 72), "cracked_deepslate_bricks": (63, 63, 65),
        "deepslate_tiles": (54, 54, 56), "cracked_deepslate_tiles": (50, 50, 52),
        "deepslate": (80, 80, 83), "cobbled_deepslate": (77, 77, 81), "polished_deepslate": (72, 72, 74),
        "chiseled_deepslate": (55, 55, 56), "reinforced_deepslate": (90, 92, 84),
        "smooth_basalt": (72, 72, 79), "gray_wool": (62, 68, 71), "light_blue_wool": (58, 175, 217),
        "blue_wool": (53, 57, 157), "cyan_wool": (21, 137, 145), "sculk": (13, 30, 36),
        "sculk_sensor": (14, 70, 80), "sculk_shrieker": (190, 200, 175), "sculk_catalyst": (15, 32, 38),
        "packed_ice": (141, 180, 250), "blue_ice": (116, 167, 253),
        # (31/10) the mansions' roofs (MapColor: dark oak = a flat brown)
        "dark_oak_planks": (74, 50, 26), "dark_oak_log": (64, 49, 30),
        # (05/11) the End cities (purpur, end stone bricks, glass, end rods)
        "purpur_block": (169, 125, 169), "purpur": (169, 125, 169), "purpur_pillar": (172, 130, 172),
        "end_stone_bricks": (218, 224, 162), "magenta_stained_glass": (190, 96, 222),
        "end_rod": (236, 230, 222), "dragon_wall_head": (26, 26, 28), "ender_chest": (32, 46, 46),
        # (05/11) blocks that fell back on the wood colour
        "obsidian": (21, 18, 32), "crying_obsidian": (38, 12, 66), "netherrack": (110, 50, 50),
        "spawner": (36, 52, 68), "tnt": (182, 62, 44), "lapis_block": (31, 67, 140),
        "glass": (200, 222, 226), "glass_pane": (200, 222, 226), "potted_cactus": (88, 130, 44),
        "campfire": (190, 110, 40), "fire": (230, 140, 40), "soul_fire": (60, 190, 200),
        "prismarine": (99, 156, 151), "attached_melon_stem": (90, 140, 40), "lever": (120, 120, 120),
        # (05/11) monuments, temples
        "prismarine_bricks": (99, 171, 158), "dark_prismarine": (51, 91, 75),
        "sea_lantern": (186, 214, 204), "wet_sponge": (171, 181, 70), "sponge": (195, 192, 74),
        "mossy_cobblestone": (110, 118, 94), "cobblestone": (128, 127, 128),
        "blue_terracotta": (74, 60, 91), "orange_terracotta": (162, 84, 38)}


_TERRA = {"white": (209, 178, 161), "orange": (162, 84, 38), "magenta": (149, 88, 108),
          "light_blue": (113, 108, 137), "yellow": (186, 133, 36), "lime": (103, 117, 52),
          "pink": (161, 78, 78), "gray": (57, 42, 35), "light_gray": (135, 106, 97),
          "cyan": (86, 91, 91), "purple": (118, 70, 86), "blue": (74, 60, 91), "brown": (77, 51, 35),
          "green": (76, 83, 42), "red": (143, 61, 46), "black": (37, 22, 16)}
_COPPER = (("oxidized", (82, 162, 132)), ("weathered", (108, 153, 110)), ("exposed", (161, 125, 103)),
           ("", (192, 107, 79)))
_TEX2 = {"tuff": (108, 109, 102), "tuff_bricks": (98, 102, 92), "polished_tuff": (97, 104, 98),
         "chiseled_tuff": (96, 100, 94), "chiseled_tuff_bricks": (98, 102, 92),
         "mud_bricks": (137, 103, 79), "bricks": (150, 97, 83), "trial_spawner": (40, 52, 60),
         "vault": (54, 62, 70), "iron_chain": (60, 60, 66), "chain": (60, 60, 66),
         "bone_block": (229, 225, 207), "decorated_pot": (140, 80, 60), "moss_block": (89, 109, 45),
         "dispenser": (110, 110, 110), "dropper": (110, 110, 110), "pointed_dripstone": (130, 104, 90),
         "dripstone_block": (134, 107, 92), "packed_mud": (142, 106, 79), "suspicious_gravel": (130, 124, 122),
         "suspicious_sand": (217, 204, 155), "polished_andesite": (132, 135, 134),
         "andesite": (136, 136, 137)}


def _tex(n):
    """_TEX of a block or of its full block (stairs, slab, wall → the block)."""
    if n in _TEX:
        return _TEX[n]
    if n.startswith("waxed_"):                          # (05/11) copper, terracotta, tuff…
        n = n[6:]
    if "copper" in n:
        for k, c in _COPPER:
            if k in n:
                return c
    if "terracotta" in n:
        for k in sorted(_TERRA, key=len, reverse=True):
            if n.startswith(k + "_"):
                c = _TERRA[k]
                return tuple(min(255, int(v * 1.15)) for v in c) if "glazed" in n else c
    if n.endswith("_bed") and n[:-4] in _DYES:
        return _DYES[n[:-4]]
    if n in _TEX2:
        return _TEX2[n]
    for suf in ("_stairs", "_slab", "_wall"):
        if n.endswith(suf) and n[:-len(suf)] in _TEX2:
            return _TEX2[n[:-len(suf)]]
        if n.endswith(suf) and n[:-len(suf)] + "s" in _TEX2:
            return _TEX2[n[:-len(suf)] + "s"]
    for suf in ("_stairs", "_slab", "_wall"):
        if n.endswith(suf):
            b = n[:-len(suf)]
            for c in (b, b + "s", b.replace("brick", "bricks").replace("tile", "tiles")):
                if c in _TEX:
                    return _TEX[c]
    return None


def _style(n):
    """(role, (r, g, b)) of a block name."""
    if n in ("water", "water_cauldron"):
        return 4, _MC["WATER"]
    if n == "lava":
        return 4, (214, 98, 22)
    if _tex(n) is not None and "candle" not in n:
        return 1, _tex(n)
    if n in ("dirt_path", "grass_path", "gravel"):
        return 2, _MC["DIRT"] if n != "gravel" else _MC["LIGHT_GRAY"]
    if n in ("farmland", "wheat", "carrots", "potatoes", "beetroots", "melon_stem", "pumpkin_stem",
             "sugar_cane"):
        return 3, (_MC["DIRT"] if n == "farmland" else _MC["PLANT"])
    if n == "bell":
        return 1, _MC["GOLD"]
    if any(k in n for k in _SKIP):
        return 0, None
    if n in ("grass_block", "dirt", "podzol", "sand", "red_sand", "snow", "snow_block", "powder_snow",
             "clay", "pale_moss_block", "coarse_dirt"):
        c = {"grass_block": "GRASS", "dirt": "DIRT", "podzol": "PODZOL", "sand": "SAND",
             "red_sand": "ORANGE", "clay": "CLAY", "pale_moss_block": "LIGHT_GRAY",
             "coarse_dirt": "DIRT"}.get(n, "SNOW")
        return 5, _MC[c]
    if "leaves" in n:
        return 5, _MC["PLANT"]
    if n in ("hay_block", "straw_bed"):
        return 1, _MC["YELLOW"]
    if n in ("melon", "cactus"):
        return 1, _MC["PLANT"]
    if n in ("pumpkin", "carved_pumpkin"):
        return 1, _MC["ORANGE"]
    if "ice" in n:
        return 1, _MC["ICE"]
    if "wool" in n:
        return 1, _MC["YELLOW"] if "yellow" in n else _MC["WOOL"]
    if "terracotta" in n:
        for k, c in (("white", "T_WHITE"), ("orange", "T_ORANGE"), ("yellow", "T_YELLOW")):
            if k in n:
                return 1, _MC[c]
        return 1, _MC["ORANGE"]
    if "red_sandstone" in n:
        return 1, _MC["ORANGE"]
    if "sandstone" in n:
        return 1, _MC["SAND"]
    if "diorite" in n or "quartz" in n:
        return 1, _MC["QUARTZ"]
    if "granite" in n:
        return 1, _MC["DIRT"]
    if "copper" in n:
        return 1, _MC["CYAN"]
    for k, c in _WOODS:
        if k in n:
            return 1, _MC[c]
    if any(k in n for k in ("stone", "cobble", "brick", "andesite", "furnace", "smoker", "anvil",
                            "grindstone", "cauldron", "ore", "blast")):
        return 1, _MC["STONE"]
    if n in ("chest", "barrel", "crafting_table", "composter", "bookshelf", "lectern", "loom",
             "fletching_table", "cartography_table", "smithing_table", "campfire", "bed"):
        return 1, _MC["WOOD"]
    return 1, _MC["WOOD"]


def _grid(h):
    """(sx, sz, [(name index, y) | None]) of a template top view, cached."""
    T = _tops()
    g = T["_c"].get(h)
    if g is None:
        sx, sz, cells = T["tpl"][h]
        lst = []
        for c in cells.split(","):
            lst.append(None if not c else (int(c[:-2], 16), int(c[-2:], 16)))   # (05/11) > 255 names
        g = T["_c"][h] = (sx, sz, lst)
    return g


_FEAT = {"oak": (2, (40, 96, 30)), "spruce": (2, (34, 74, 40)), "pine": (2, (34, 74, 40)),
         "acacia": (2, (86, 110, 30)), "birch": (2, (70, 120, 40)), "cherry": (2, (232, 160, 190)),
         "pile_hay": (1, _MC["YELLOW"]), "pile_melon": (1, _MC["PLANT"]),
         "pile_pumpkin": (1, _MC["ORANGE"])}


def plan(seed, mc, key, cx, cz, large=None):
    import jigsaw as jg
    lay = jg.layout(seed, mc, key, cx, cz, large)
    if lay is None:
        return None
    g = jg._gen(mc)
    res = _render(lay[1], g, _tops()["ver"].get(jg.data_version(mc), {}), legacy=True)
    if res is not None:
        res["boxes"] = jg.map_boxes(seed, mc, key, cx, cz, large)
    return res


def plan_bastion(seed, mc, x, z):
    """(31/10) a bastion's top view: its templates' blocks (blackstone, basalt,
    lava, gold…), the higher floors lighter (it is tall), its chests."""
    import bastion_loot as bl
    lay = bl.layout(seed, mc, int(x) >> 4, int(z) >> 4)
    if not lay:
        return None
    res = _render(lay[0], bl.gen(), _tops()["ver"].get("bastion", {}), relief=True, legacy=True)
    if res is not None:
        res["boxes"] = bl.map_boxes(seed, mc, x, z)
    return res


def plan_ancient_city(seed, mc, x, z):
    """(31/10) an ancient city's top view (deepslate, basalt, wool…), lit by height."""
    import ancient_city_loot as acl
    lay = acl.layout(seed, mc, int(x) >> 4, int(z) >> 4)
    if not lay:
        return None
    res = _render(lay[0], acl.gen(), _tops()["ver"].get("ancient_city", {}), relief=True, legacy=True)
    if res is not None:
        res["boxes"] = acl.map_boxes(seed, mc, x, z)
    return res


_DYES = {"white": (233, 236, 236), "orange": (216, 127, 51), "magenta": (178, 76, 216),
         "light_blue": (102, 153, 216), "yellow": (229, 229, 51), "lime": (127, 204, 25),
         "pink": (242, 127, 165), "gray": (76, 76, 76), "light_gray": (153, 153, 153),
         "cyan": (76, 127, 153), "purple": (127, 63, 178), "blue": (51, 76, 178),
         "brown": (102, 76, 51), "green": (102, 127, 51), "red": (153, 51, 51), "black": (25, 25, 25)}


def _xstyle(n):
    """(31/10) the X-ray floors: the carpets and wool coloured by their dye."""
    if "carpet" in n or n.endswith("_wool"):
        for k in sorted(_DYES, key=len, reverse=True):
            if n.startswith(k + "_"):
                return 1, _DYES[k]
    return _style(n)


# (05/11) the villages, outposts, camps, bastions, ancient cities and mansions keep the
# colours they had before p282 (ed liked them as they were): these blocks' old styles
_LEGACY = {
    'attached_melon_stem': (1, (143, 119, 72)),
    'campfire': (1, (143, 119, 72)),
    'cobblestone': (1, (112, 112, 112)),
    'cobblestone_slab': (1, (112, 112, 112)),
    'cobblestone_stairs': (1, (112, 112, 112)),
    'cobblestone_wall': (1, (112, 112, 112)),
    'fire': (1, (143, 119, 72)),
    'glass': (1, (143, 119, 72)),
    'glass_pane': (1, (143, 119, 72)),
    'lapis_block': (1, (143, 119, 72)),
    'lever': (1, (143, 119, 72)),
    'mossy_cobblestone': (1, (112, 112, 112)),
    'mossy_cobblestone_wall': (1, (112, 112, 112)),
    'obsidian': (1, (143, 119, 72)),
    'orange_terracotta': (1, (159, 82, 36)),
    'oxidized_copper_chest': (1, (76, 127, 153)),
    'oxidized_copper_golem_statue': (1, (76, 127, 153)),
    'oxidized_copper_lantern': (0, None),
    'polished_andesite': (1, (112, 112, 112)),
    'potted_cactus': (1, (143, 119, 72)),
    'soul_fire': (1, (143, 119, 72)),
    'spawner': (1, (143, 119, 72)),
    'tnt': (1, (143, 119, 72)),
    'white_glazed_terracotta': (1, (209, 177, 161)),
    'white_terracotta': (1, (209, 177, 161))}


def _render(pieces, g, tv, relief=False, containers=True, bounds=None, xray=False, shadow=None,
            ground_solid=False, legacy=False):
    import numpy as np
    import jigsaw as jg
    T = _tops()
    boxes = []
    for p in pieces:
        if hasattr(p, "box"):                   # (31/10) a ready piece (mansions)
            boxes.append(p.box)
            continue
        b = jg.piece_box(g, p) if p.el.kind != "feature" else (p.pos[0], p.pos[1], p.pos[2]) * 2
        if b is not None:
            boxes.append(b)
    if not boxes and bounds is None:
        return None
    m = 3
    if bounds is not None:
        x0, z0, w, h = bounds
    else:
        x0 = min(b[0] for b in boxes) - m
        z0 = min(b[2] for b in boxes) - m
        # (05/11) room to the south-east for the sun's shadow
        e = 0 if shadow is None else min(26, int((max(b[4] for b in boxes) - shadow) * 0.3) + 1)
        w = max(b[3] for b in boxes) + m + e + 1 - x0
        h = max(b[5] for b in boxes) + m + e + 1 - z0
    col = np.zeros((h, w, 3), np.uint8)
    role = np.zeros((h, w), np.uint8)
    hy = np.full((h, w), -10000, np.int32)
    nm = np.full((h, w), -1, np.int16)           # (05/11) the block of each column (HD view)
    if xray:
        if "_xstyle" not in T:
            T["_xstyle"] = [_xstyle(n) for n in T["names"]]
        styles = T["_xstyle"]
    else:
        styles = T["_style"]
    if legacy:
        k_ = "_lxstyle" if xray else "_lstyle"
        if k_ not in T:
            T[k_] = [_LEGACY.get(n, st) for n, st in zip(T["names"], styles)]
        styles = T[k_]
    for p in pieces:
        if p.el.kind == "feature":
            continue
        for e in jg._elements(p.el):
            if e.kind not in ("single", "legacy") or e.loc not in tv:
                continue
            sx, sz, cells = _grid(tv[e.loc])
            for i, c in enumerate(cells):
                if c is None:
                    continue
                rl, rgb = styles[c[0]]
                if not rl:
                    continue
                lx, lz = i % sx, i // sx
                if hasattr(p, "xf"):
                    wx, wz = p.xf(lx, lz)
                else:
                    wx, _y, wz = jg._rot_pos(lx, 0, lz, p.rot)
                X, Z = wx + p.pos[0] - x0, wz + p.pos[2] - z0
                Y = p.pos[1] + c[1]
                if not (0 <= X < w and 0 <= Z < h):
                    continue
                # the highest block of the column wins (a house over a street's path)
                if Y >= hy[Z, X] or (role[Z, X] in (2, 5) and rl == 1):
                    hy[Z, X] = Y
                    role[Z, X] = rl
                    col[Z, X] = rgb
                    nm[Z, X] = c[0]
    # feature pieces: trees (a crown), piles
    leaf = T["names"].index("oak_leaves") if "oak_leaves" in T["names"] else -1
    for p in pieces:
        if p.el.kind != "feature":
            continue
        f = _FEAT.get(p.el.feature)
        if f is None:
            continue
        r, rgb = f
        X, Z = p.pos[0] - x0, p.pos[2] - z0
        for dz in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dz * dz > r * r + 1:
                    continue
                a, b = X + dx, Z + dz
                if 0 <= a < w and 0 <= b < h and role[b, a] in (0, 2, 5):
                    role[b, a] = 6 if r > 1 else 7          # a tree / a pile
                    col[b, a] = rgb
                    nm[b, a] = leaf if r > 1 else -1
                    hy[b, a] = p.pos[1] + (6 if r > 1 else 1) - (dx * dx + dz * dz) // 2
    if ground_solid:                     # (05/11) an igloo's snow is its building
        role[role == 5] = 1
    # the map's shading: brighter / darker than the block north of it
    north = np.vstack([hy[:1], hy[:-1]])
    shade = np.where(hy > north, 255, np.where(hy < north, 180, 220)).astype(np.uint16)
    rgb = (col.astype(np.uint16) * shade[..., None] // 255).astype(np.uint8)
    if relief:                                       # (31/10) tall: the height as light
        m = role > 0
        if m.any():
            lo, hi = int(hy[m].min()), int(hy[m].max())
            t = np.clip((hy - lo) / max(1, hi - lo), 0, 1)
            gain = 0.62 + 0.9 * t                        # low floors dark, the top bright
            rgbf = rgb.astype(np.float32) * gain[..., None] + 14 * t[..., None]
            # (05/11) depth: lower than a neighbour = in its shade, higher = a lit rim
            # (not on the legacy ones: they stay as they were)
            hup = np.where(m & (not legacy), hy, -10000)
            occ = np.zeros(hy.shape, np.float32)
            rim = np.zeros(hy.shape, np.float32)
            for dz, dx, wgt in ((1, 0, 1.0), (0, 1, 1.0), (-1, 0, 0.5), (0, -1, 0.5)):
                nb = np.roll(np.roll(hup, dz, 0), dx, 1)
                occ += wgt * ((nb > hy + 1) & m)
                rim += wgt * ((nb < hy - 1) & (nb > -10000) & m)
            rgbf *= (0.84 ** occ * (1.0 + 0.06 * rim))[..., None]
            rgb = np.clip(rgbf, 0, 255).astype(np.uint8)
    alpha = np.zeros((h, w), np.uint8)
    alpha[role == 1] = 255
    alpha[role == 6] = 235
    alpha[role == 7] = 220
    alpha[role == 2] = 215
    alpha[role == 3] = 225
    alpha[role == 4] = 220
    alpha[role == 5] = 120
    # a soft shadow around the buildings (and the trees)
    solid = (role == 1) | (role == 6)
    d = np.zeros_like(solid)                         # the 8 neighbours: an outline…
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dz:
                d |= np.roll(np.roll(solid, dz, 0), dx, 1)
    d2 = np.zeros_like(solid)                        # …and a shadow to the south-east
    d2[1:, 1:] |= solid[:-1, :-1]
    d2[2:, 2:] |= solid[:-2, :-2]
    free = ~solid & (role != 3) & (role != 4)
    edge = d & free
    shad = d2 & free & ~edge
    rgb[edge] = (24, 22, 20)
    alpha[edge] = 170
    rgb[shad] = (16, 16, 20)
    alpha[shad] = np.maximum(alpha[shad], 80)
    # the buildings' own border, a little darker (their shape on light ground)
    inner = solid & ~(np.roll(solid, 1, 0) & np.roll(solid, -1, 0) & np.roll(solid, 1, 1)
                      & np.roll(solid, -1, 1))
    rgb[inner & (role == 1)] = (rgb[inner & (role == 1)].astype(np.uint16) * 3 // 4).astype(np.uint8)
    img = np.dstack([rgb, alpha]).astype(np.uint8)
    chests = []
    for p in (pieces if containers else ()):
        for (cw, _blk, t) in jg.containers(g, p):
            if t:
                chests.append((cw[0], cw[1], cw[2], t))
    vis = (alpha > 0) & (nm >= 0) & ((role == 1) | (role == 2) | (role == 3) | (role == 4) | (role == 6))
    out = img.tobytes()
    if shadow is not None and (role > 0).any():
        out = _sun_shadow(out, w, h, hy, (role == 1) | (role == 6), shadow)
    return dict(x0=int(x0), z0=int(z0), w=int(w), h=int(h), rgba=out, chests=chests,
                nm=np.where(vis, nm, -1).astype(np.int16).tobytes(), names=T["names"])


# ── (31/10) mineshafts: their pieces drawn as seen from above, no ceiling ──
_MS_WALK = (58, 50, 42)          # the corridor floor in the dark
_MS_PLANK = (162, 130, 78)       # oak planks / fences (supports)
_MS_RAIL = (150, 150, 150)
_MS_TIE = (108, 84, 52)
_MS_DIRT = (134, 96, 67)
_MS_WEB = (225, 225, 225)


def plan_mineshaft(seed, mc, x, z):
    """A mineshaft from above: the corridors (floor, a support every 5 blocks, the
    rails, the cobwebs), crossings, stairs and the dirt room; deeper = darker. Its
    chests (minecarts) come later (they need the terrain): MapEngine."""
    import numpy as np
    import mineshaft_loot as ml
    cx, cz = int(x) >> 4, int(z) >> 4
    mc = int(mc)
    pcs = []                                   # (kind, bb, facing, length, rails, webs, two)
    if mc >= 22:
        import mineshaft118 as ms
        hit = [m for a, b, m in ml.starts(seed, mc, cx, cz, cx, cz) if (a, b) == (cx, cz)]
        if not hit:
            return None
        ps, ents = ms.layout_ex(seed, cx, cz, mesa=hit[0], mc=mc, height=ms._height_fn(seed, mc))
        if ents is None:
            return None
        for q in ps:
            pcs.append((ml._T[q.type], list(q.bb), q.facing, q.length, q.rails, q.webs, q.two))
    else:
        for b in ml.pieces(seed, mc, cx, cz):
            bb = list(b[:6])
            ns = (bb[5] - bb[2]) >= (bb[3] - bb[0])
            ln = ((bb[5] - bb[2] + 1) if ns else (bb[3] - bb[0] + 1)) // 5
            pcs.append((b[6], bb, 1 if ns else 3, ln, None, None, (bb[4] - bb[1]) > 4))
    if not pcs:
        return None
    m = 3
    x0 = min(p[1][0] for p in pcs) - m
    z0 = min(p[1][2] for p in pcs) - m
    w = max(p[1][3] for p in pcs) + m + 1 - x0
    h = max(p[1][5] for p in pcs) + m + 1 - z0
    col = np.zeros((h, w), np.uint8)                # (05/11) the clean plan's category
    role = np.zeros((h, w), np.uint8)               # 1 floor, 2 wood, 3 rail, 4 web, 5 dirt
    hy = np.full((h, w), -10000, np.int32)

    def put(X, Z, Y, r, c, force=False):
        a, b = X - x0, Z - z0
        if 0 <= a < w and 0 <= b < h and (force or Y >= hy[b, a] or role[b, a] == 0):
            hy[b, a] = max(hy[b, a], Y)
            role[b, a] = r
            col[b, a] = c

    def mix(a, b_):
        return ((a * 2654435761) ^ (b_ * 40503)) & 0xFFFF

    for kind, bb, f, ln, rails, webs, two in pcs:
        X0, Y0, Z0, X1, Y1, Z1 = bb
        if kind == "room":
            for X in range(X0, X1 + 1):
                for Z in range(Z0, Z1 + 1):
                    put(X, Z, Y0, 5, 24)
            continue
        for X in range(X0, X1 + 1):
            for Z in range(Z0, Z1 + 1):
                put(X, Z, Y0, 1, 25 if kind == "crossing" else 20)
        if kind == "crossing":
            for X, Z in ((X0 + 1, Z0 + 1), (X0 + 1, Z1 - 1), (X1 - 1, Z0 + 1), (X1 - 1, Z1 - 1)):
                put(X, Z, Y1, 2, 21, True)
            continue
        if kind == "stairs":
            ns = f in (0, 1)
            n_ = (Z1 - Z0 + 1) if ns else (X1 - X0 + 1)
            for i in range(n_):
                for j in range(3):
                    if ns:
                        put(X0 + j, Z0 + i, Y0, 1, 26, True)
                    else:
                        put(X0 + i, Z0 + j, Y0, 1, 26, True)
            continue
        # corridor: local (lx 0..2 across, lz 0..len*5-1 along), as the game places it
        L = max(1, ln) * 5

        def wpos(lx, lz):
            if f == 0:
                return X0 + lx, Z1 - lz
            if f == 1:
                return X0 + lx, Z0 + lz
            if f == 2:
                return X1 - lz, Z0 + lx
            return X0 + lz, Z0 + lx
        if rails is not False:                     # the rails (1.16: not known → drawn)
            for lz in range(L):
                X, Z = wpos(1, lz)
                put(X, Z, Y0, 3, 22 if f in (0, 1) else 28, True)
        for i in range(max(1, ln)):
            o = 2 + i * 5
            for lx in range(3):
                X, Z = wpos(lx, o)
                put(X, Z, Y0, 2, 21, True)
        if webs:
            for lz in range(L):
                for lx in (0, 2):
                    if mix(lz + 7 * lx, X0 * 31 + Z0) % 5 == 0:
                        X, Z = wpos(lx, lz)
                        put(X, Z, Y0, 4, 23, True)
    # (05/11) the clean plan: a sandy wooden floor, the beams, the rails, the cobwebs
    if mc >= 22:                                   # under Y 0: deepslate
        col[np.isin(col, (20, 25)) & (hy < 0)] = 29
    rgba = _clean_rgba(col, hy)[0]
    return dict(x0=int(x0), z0=int(z0), w=int(w), h=int(h), rgba=rgba, chests=[],
                pc=col.tobytes(), py=hy.astype(np.int32).tobytes(),
                boxes=ml.map_boxes(seed, mc, cx, cz), lazy_chests="mineshaft")



class _MPiece:
    """(31/10) a woodland mansion template placed (mansion_loot: rotation + mirror)."""

    class _El:
        kind = "single"

        def __init__(self, loc):
            self.loc = loc

    def __init__(self, piece, size):
        import mansion_loot as mnl
        self.el = self._El(piece[0])
        self.pos = piece[1]
        self.rot, self.mir = piece[2], piece[3]
        self.box = mnl.box(piece, size)

    def xf(self, lx, lz):
        import mansion_loot as mnl
        return mnl.transform(lx, lz, self.rot, self.mir)


def plan_mansion(seed, mc, x, z):
    """(31/10) a woodland mansion from above (its roofs: dark oak, cobblestone, the
    wool / birch of the open rooms), lit by height; its loot chests."""
    import mansion_loot as mnl
    lay = mnl.layout(seed, mc, int(x) >> 4, int(z) >> 4)
    if lay is None:
        return None
    T = mnl.templates(mc)
    ps = [_MPiece(p, T[p[0]]["size"]) for p in lay[2] if p[0] in T]
    vk = "mansion_116" if int(mc) <= 22 else "mansion_119"
    tops = _tops()["ver"]
    res = _render(ps, None, tops.get(vk, {}), relief=True, containers=False, legacy=True)
    if res is None:
        return None
    oy = lay[0][1]
    chests = [(c[0], c[1], c[2], "chests/woodland_mansion")
              for c in mnl.chest_list(seed, mc, int(x) >> 4, int(z) >> 4)]
    res["chests"] = chests
    res["boxes"] = mnl.map_boxes(seed, mc, int(x) >> 4, int(z) >> 4)
    # (31/10) the X-ray floors: each floor's rooms, walls, carpets and furniture
    # without its ceiling (blocks up to 5 above its floor); floor 1: + the entrance
    tvx = tops.get(vk + "_x", {})
    bounds = (res["x0"], res["z0"], res["w"], res["h"])
    floors, fchests = {}, {}
    for f in (1, 2, 3):
        sub = [q for q in ps if floor_of(q.pos[1] - oy) == f
               and not q.el.loc.startswith(("roof", "small_wall"))]
        if not sub:
            continue
        r = _render(sub, None, tvx, relief=False, containers=False, bounds=bounds, xray=True, legacy=True)
        if r is not None:
            floors[f] = r["rgba"]
            fchests[f] = [c for c in chests if floor_of(c[1] - oy) == f]
    res["floors"], res["floor_chests"] = floors, fchests
    return res


def floor_of(dy):
    """The mansion floor (1, 2, 3) of a height above its origin."""
    return 1 if dy < 8 else (2 if dy < 19 else 3)


# ── (05/11) Strongholds and Nether fortresses: their REAL blocks ──
# loot_tables/piece_blocks.json.gz (tools/servercheck/piececat.py): every piece type
# read from a server world, in the piece's own frame (width, height, depth).
_PCAT = None

# the colours of their blocks (texture means; the stone bricks a little lifted)
_PB = {"stone_bricks": (124, 123, 124), "mossy_stone_bricks": (112, 122, 100),
       "cracked_stone_bricks": (114, 113, 114), "infested_stone_bricks": (124, 123, 124),
       "infested_mossy_stone_bricks": (112, 122, 100), "infested_cracked_stone_bricks": (114, 113, 114),
       "stone_brick_stairs": (124, 123, 124), "stone_brick_slab": (124, 123, 124),
       "smooth_stone": (160, 160, 160), "smooth_stone_slab": (160, 160, 160),
       "stone_slab": (160, 160, 160), "cobblestone": (128, 127, 128),
       "cobblestone_stairs": (128, 127, 128), "cobblestone_slab": (128, 127, 128),
       "iron_bars": (150, 152, 158), "oak_door": (140, 108, 62), "iron_door": (196, 196, 196),
       "stone_button": (128, 128, 128), "torch": (255, 186, 80), "wall_torch": (255, 186, 80),
       "bookshelf": (117, 94, 59), "oak_planks": (162, 130, 78), "oak_fence": (150, 120, 72),
       "oak_stairs": (162, 130, 78), "ladder": (125, 96, 56), "cobweb": (220, 222, 226),
       "chest": (164, 116, 46), "spawner": (36, 52, 68), "end_portal_frame": (124, 152, 116),
       "lava": (214, 98, 22), "water": (56, 92, 210),
       "nether_bricks": (58, 29, 35), "nether_brick_fence": (40, 20, 25),
       "nether_brick_stairs": (58, 29, 35), "nether_brick_slab": (58, 29, 35),
       "soul_sand": (81, 62, 50), "nether_wart": (165, 27, 32)}
# blocks one sees through (thin): the X-ray stops its ceiling at them
_THIN = ("iron_bars", "door", "button", "fence", "ladder", "cobweb", "air", "spawner",
         "chest", "water", "lava", "end_portal_frame", "slab", "stairs", "wart")


def _pcat():
    global _PCAT
    if _PCAT is None:
        import numpy as np
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "piece_blocks.json.gz")
        with gzip.open(p, "rt") as f:
            d = json.load(f)
        names = d["names"]
        for i, n in enumerate(names):            # torches: a light, not a block on the plan
            if n in ("torch", "wall_torch"):
                names[i] = "air_lit"
        cat = {}
        for k, v in d["pieces"].items():
            W, H, D = v["size"]
            cat[k] = np.array(v["cells"], np.int16).reshape(H, D, W)
        thin = np.array([any(t in n for t in _THIN) for n in names], bool)
        air = names.index("air")
        for k, g in cat.items():
            g[np.isin(g, [i for i, n in enumerate(names) if n == "air_lit"])] = air
        _PCAT = dict(names=names, cat=cat, thin=thin, air=air)
    return _PCAT


def _pb(n):
    return _PB.get(n) or _tex(n) or (128, 128, 128)


def _surface(G, air, thin, xray):
    """Per column (D, W) of a piece's block grid G (H, D, W): (block, height, wall).
    xray: the ceiling taken off (the solid run at the top), the first block under it;
    otherwise the highest block."""
    import numpy as np
    H = G.shape[0]
    rev = G[::-1]
    nonair = rev != air
    hs = np.arange(H)[:, None, None]
    if xray:
        see = thin[rev]
        has = see.any(0)
        s = see.argmax(0)
        cand = nonair & (hs >= s[None])
        ok = cand.any(0)
        t = np.where(ok, cand.argmax(0), 0)
        top = nonair.argmax(0)
        wall = ~has
        t = np.where(wall, top, t)
        vis = nonair.any(0) & (ok | wall)
        _surface.covered = has & (s > 0)          # a ceiling was taken off above it
    else:
        t = nonair.argmax(0)
        wall = np.zeros(G.shape[1:], bool)
        vis = nonair.any(0)
        _surface.covered = np.zeros(G.shape[1:], bool)
    blk = np.take_along_axis(rev, t[None], 0)[0]
    return blk, H - 1 - t, wall, vis


def _blocks_image(nm, hy, vis, names, light=(0.62, 0.62), edge_col=(18, 18, 20), wall=None,
                  covered=None):
    """(rgba bytes) of a grid of blocks seen from above: block colours, lit by height,
    the map's north shading, an outline and a shadow."""
    import numpy as np
    h, w = nm.shape
    pal = np.array([_pb(n) for n in names] + [(0, 0, 0)], np.float32)
    col = pal[np.where(vis, nm, len(names))]
    mk = vis
    lo, hi = int(hy[mk].min()), int(hy[mk].max())
    tt = np.clip((hy - lo) / max(1, hi - lo), 0, 1)
    col *= (light[0] + light[1] * tt)[..., None] + 0.25
    north = np.vstack([hy[:1], hy[:-1]])
    col *= np.where(hy > north, 1.12, np.where(hy < north, 0.80, 1.0))[..., None]
    # (05/11) depth: a block lower than its neighbours lies in their shade, a
    # higher one catches the light on its rim
    hup = np.where(mk, hy, -10000)
    occ = np.zeros(hy.shape, np.float32)
    rim = np.zeros(hy.shape, np.float32)
    for dz, dx, wgt in ((1, 0, 1.0), (0, 1, 1.0), (-1, 0, 0.5), (0, -1, 0.5)):
        nb = np.roll(np.roll(hup, dz, 0), dx, 1)
        occ += wgt * ((nb > hy) & mk)
        rim += wgt * ((nb < hy) & (nb > -10000) & mk)
    col *= (0.86 ** occ * (1.0 + 0.04 * rim))[..., None]
    if wall is not None:                  # X-ray: the floors deep in, under the walls' shadow
        fl_ = mk & ~wall if covered is None else covered & mk
        col[fl_] *= 0.66
        nw = np.zeros_like(wall)
        nw[1:, :] |= wall[:-1, :]
        nw[:, 1:] |= wall[:, :-1]
        col[fl_ & nw] *= 0.78
        col[wall] *= 1.06
    rgb = np.clip(col, 0, 255).astype(np.uint8)
    alpha = np.where(mk, 255, 0).astype(np.uint8)
    d = np.zeros_like(mk)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dz:
                d |= np.roll(np.roll(mk, dz, 0), dx, 1)
    d2 = np.zeros_like(mk)
    d2[1:, 1:] |= mk[:-1, :-1]
    d2[2:, 2:] |= mk[:-2, :-2]
    edge = d & ~mk
    shad = d2 & ~mk & ~edge
    rgb[edge] = edge_col; alpha[edge] = 200
    rgb[shad] = (8, 8, 10); alpha[shad] = 90
    return np.dstack([rgb, alpha]).astype(np.uint8).tobytes()


def _sh_key(p, first):
    import stronghold_eyes as se
    b = p.bbox
    t = p.type
    if first:
        return "sh_start"
    if t == se.LIBRARY:
        return "sh_%d_%d" % (t, 1 if b.maxY - b.minY > 6 else 0)
    if t in (se.ROOM_CROSSING, se.STRAIGHT, se.FIVE_CROSSING):
        return "sh_%d_%d" % (t, p.additionalData)
    if t == se.FILLER_CORRIDOR:
        ns = p.orientation in (se.NORTH, se.SOUTH)
        return "sh_%d_%d" % (t, (b.maxZ - b.minZ + 1) if ns else (b.maxX - b.minX + 1))
    return "sh_%d" % t


def _sh_door(G, names, kind, x0):
    """The piece's entry door (randomSmallDoor: 0-1 opening, 2 wood, 3 grates, 4 iron)
    at local (x0..x0+2, 1..3, 0), as the game builds it."""
    ix = {n: i for i, n in enumerate(names)}
    sb, air = ix["stone_bricks"], ix["air"]
    door = ix.get("oak_door" if kind == 2 else "iron_door", sb)
    bars = ix.get("iron_bars", sb)
    if kind in (0, 1):
        G[1:4, 0, x0:x0 + 3] = air
    elif kind == 3:
        G[1:4, 0, x0:x0 + 3] = bars
        G[1:3, 0, x0 + 1] = air
    else:
        G[1:4, 0, x0:x0 + 3] = sb
        G[1:3, 0, x0 + 1] = door


def plan_stronghold(seed, mc, x, z):
    """(05/11) A stronghold seen from above in X-ray (it is underground: its ceilings
    taken off), block by block as the game builds it (piece_blocks): the stone,
    mossy and cracked bricks of the walls, each piece's entry door (open, wooden,
    iron, grates), the prisons' bars, the crossings (pillar, fountain, storeroom and
    its upper floor), the stairs, the libraries (shelves, balcony, chandelier), the
    portal room (the frame and its real eyes, the lava, the stairs, the silverfish
    spawner); deeper = darker; the chests."""
    import numpy as np
    import stronghold_eyes as se
    gen, rng = se._generate_full_bfs(int(seed), int(x) >> 4, int(z) >> 4,
                                     pre118=se._use_old_filler(mc))
    if gen.portal_room_piece is None:
        return None
    sea, miny = se._sea_min_for_mc(mc)
    dy = se._move_below_sea_level(gen, rng, sea, miny, 10, se._inflate_for_mc(mc))
    try:
        ef = list(se._eyes_per_frame(int(seed), gen, mc))
    except Exception:
        ef = []
    C = _pcat()
    names, cat, thin, air = C["names"], C["cat"], C["thin"], C["air"]
    names = list(names) + ["end_portal_frame_eye"]
    eye_i = len(names) - 1
    thin = np.append(thin, True)
    ix = {n: i for i, n in enumerate(names)}
    ps = gen.pieces
    m = 3
    x0 = min(q.bbox.minX for q in ps) - m
    z0 = min(q.bbox.minZ for q in ps) - m
    w = max(q.bbox.maxX for q in ps) + m + 1 - x0
    h = max(q.bbox.maxZ for q in ps) + m + 1 - z0
    nm = np.zeros((h, w), np.int16)
    hy = np.full((h, w), -10000, np.int32)
    vis = np.zeros((h, w), bool)
    wl = np.zeros((h, w), bool)
    cv = np.zeros((h, w), bool)
    pc = np.zeros((h, w), np.uint8)               # (05/11) the clean plan's categories
    ncat = np.zeros(len(names) + 1, np.uint8)     # 0: the room's floor
    for n, k in (("bookshelf", 5), ("iron_bars", 7), ("end_portal_frame", 9),
                 ("end_portal_frame_eye", 10), ("lava", 11), ("water", 12), ("spawner", 16),
                 ("oak_planks", 14), ("oak_fence", 14), ("ladder", 14), ("oak_door", 14),
                 ("oak_stairs", 14), ("iron_door", 15), ("stone_brick_stairs", 13),
                 ("cobblestone_stairs", 13)):
        if n in ix:
            ncat[ix[n]] = k
    room = {se.ROOM_CROSSING: 3, se.LIBRARY: 4, se.PRISON_HALL: 6, se.PORTAL_ROOM: 8,
            se.STAIRS_DOWN: 13, se.STRAIGHT_STAIRS_DOWN: 13, se.FIVE_CROSSING: 18}
    stone = [ix[n] for n in ("stone_bricks", "mossy_stone_bricks", "cracked_stone_bricks")]
    st_set = np.zeros(len(names) + 1, bool)
    for n in ("stone_bricks", "mossy_stone_bricks", "cracked_stone_bricks", "infested_stone_bricks",
              "infested_mossy_stone_bricks", "infested_cracked_stone_bricks"):
        if n in ix:
            st_set[ix[n]] = True
    order = sorted(range(len(ps)), key=lambda i: (ps[i].type == se.PORTAL_ROOM, ps[i].bbox.minY))
    for qi in order:
        q = ps[qi]
        G = cat.get(_sh_key(q, qi == 0))
        if G is None and q.type == se.FILLER_CORRIDOR:
            G0 = cat.get("sh_11_3")
            if G0 is not None:
                b = q.bbox
                ns = q.orientation in (se.NORTH, se.SOUTH)
                Dq = (b.maxZ - b.minZ + 1) if ns else (b.maxX - b.minX + 1)
                G = np.concatenate([G0[:, :1]] * Dq, 1)
        if G is None:
            continue
        G = G.astype(np.int16).copy()
        H_, D_, W_ = G.shape
        if qi and q.type not in (se.FILLER_CORRIDOR, se.PORTAL_ROOM):
            _sh_door(G, names, int(getattr(q, "door", 0) or 0),
                     4 if q.type in (se.ROOM_CROSSING, se.FIVE_CROSSING, se.LIBRARY) else 1)
        if q.type == se.PORTAL_ROOM and "end_portal_frame" in ix:     # the real eyes
            fr = [(4, 8), (5, 8), (6, 8), (4, 12), (5, 12), (6, 12),
                  (3, 9), (3, 10), (3, 11), (7, 9), (7, 10), (7, 11)]
            for i, (lw, ld) in enumerate(fr):
                if lw < W_ and ld < D_:
                    G[3, ld, lw] = eye_i if (i < len(ef) and ef[i]) else ix["end_portal_frame"]
        blk, sh, wall, vv = _surface(G, air, thin, True)
        lw_, ld_ = np.meshgrid(np.arange(W_), np.arange(D_))
        X = np.vectorize(q.get_world_x)(lw_, ld_) - x0
        Z = np.vectorize(q.get_world_z)(lw_, ld_) - z0
        ok = vv & (X >= 0) & (X < w) & (Z >= 0) & (Z < h)
        Xo, Zo = X[ok], Z[ok]
        b_ = blk[ok]
        # the walls' bricks: the game's StrongholdStones (cracked 20 %, mossy 30 %)
        hsh = ((Xo + x0) * 73856093 ^ (Zo + z0) * 19349663 ^ (q.bbox.minY * 83492791)) & 0xffff
        f = hsh / 65536.0
        repl = np.where(f < 0.2, stone[2], np.where(f < 0.5, stone[1], stone[0]))
        b_ = np.where(st_set[b_], repl, b_)
        nm[Zo, Xo] = b_
        hy[Zo, Xo] = q.bbox.minY + dy + sh[ok]
        vis[Zo, Xo] = True
        wl[Zo, Xo] = wall[ok]
        cv[Zo, Xo] = _surface.covered[ok]
        k_ = ncat[blk[ok]]
        k_ = np.where(k_ == 0, room.get(q.type, 13 if qi == 0 else 2), k_)
        pc[Zo, Xo] = np.where(wall[ok], 1, k_)
    # an eye: the frame's colour, its eye dark green
    _PB.setdefault("end_portal_frame_eye", (40, 92, 76))
    rgba = _clean_rgba(pc, hy)[0]
    chests = []
    for q in ps:                                  # (structure_loot._stronghold_chests)
        want = []
        if q.type == se.CHEST_CORRIDOR:
            want.append((3, 2, 3, "stronghold_corridor"))
        elif q.type == se.ROOM_CROSSING and q.additionalData == 2:
            want.append((3, 4, 8, "stronghold_crossing"))
        elif q.type == se.LIBRARY:
            want.append((3, 3, 5, "stronghold_library"))
            if (q.bbox.maxY - q.bbox.minY) > 6:
                want.append((12, 8, 1, "stronghold_library"))
        for lx, ly, lz, tb in want:
            chests.append((q.get_world_x(lx, lz), q.get_world_y(ly) + dy, q.get_world_z(lx, lz),
                           "chests/" + tb))
    return dict(x0=int(x0), z0=int(z0), w=int(w), h=int(h), rgba=rgba, chests=chests,
                pc=pc.tobytes(), py=hy.astype(np.int32).tobytes())


def plan_fortress(seed, mc, x, z):
    """(05/11) A Nether fortress from above, block by block as the game builds it
    (piece_blocks): the bridges' decks and fences, the crossings, the blaze
    spawners' thrones, the castle (its roofs, the nether wart courtyards in their
    soul sand, the lava well of the entrance); lit by height; the chests."""
    import numpy as np
    import fortress_loot as fl
    ps = fl.pieces(seed, mc, int(x) >> 4, int(z) >> 4)
    if not ps:
        return None
    C = _pcat()
    names, cat, thin, air = C["names"], C["cat"], C["thin"], C["air"]
    m = 3
    x0 = min(min(q.b0[0], q.b1[0]) for q in ps) - m
    z0 = min(min(q.b0[2], q.b1[2]) for q in ps) - m
    ylo = min(q.b0[1] for q in ps)
    yhi = max(q.b1[1] for q in ps)
    e = min(26, int((yhi - ylo + 8) * 0.3) + 1)          # (05/11) room for the sun's shadow
    w = max(max(q.b0[0], q.b1[0]) for q in ps) + m + e + 1 - x0
    h = max(max(q.b0[2], q.b1[2]) for q in ps) + m + e + 1 - z0
    nm = np.zeros((h, w), np.int16)
    hy = np.full((h, w), -10000, np.int32)
    vis = np.zeros((h, w), bool)
    wl = np.zeros((h, w), bool)
    cv = np.zeros((h, w), bool)
    for q in sorted(ps, key=lambda q: q.b0[1]):
        G = cat.get("fort_%d" % q.typ)
        if G is None:
            continue
        H_, D_, W_ = G.shape
        blk, sh, wall, vv = _surface(G, air, thin, True)
        cov = _surface.covered
        for ld in range(D_):
            for lw in range(W_):
                if not vv[ld, lw]:
                    continue
                X, Y, Z = q.world(lw, int(sh[ld, lw]), ld)
                a, b = X - x0, Z - z0
                if 0 <= a < w and 0 <= b < h and Y >= hy[b, a]:
                    nm[b, a] = blk[ld, lw]
                    hy[b, a] = Y
                    vis[b, a] = True
                    wl[b, a] = wall[ld, lw]
                    cv[b, a] = cov[ld, lw]
    rgba = _blocks_image(nm, hy, vis, names, light=(0.75, 0.75), edge_col=(14, 6, 8), wall=wl & vis, covered=cv)
    rgba = _sun_shadow(rgba, w, h, hy, vis, ylo - 8)
    chests = [(c[0], c[1], c[2], "chests/nether_bridge") for c in fl.chest_list(seed, mc, x, z)]
    return dict(x0=int(x0), z0=int(z0), w=int(w), h=int(h), rgba=rgba, chests=chests,
                boxes=fl.map_boxes(seed, mc, x, z),
                nm=np.where(vis, nm, -1).astype(np.int16).tobytes(), names=list(names))


# ── (05/11) End cities: their templates (end_city_loot), roofs from above ──
class _EPiece:
    """An End city piece placed (end_city_loot.local_to_world)."""

    class _El:
        kind = "single"

        def __init__(self, loc):
            self.loc = loc

    def __init__(self, piece, name):
        self.el = self._El(name)
        self.pc = piece
        self.pos = (0, piece[6], 0)
        self.box = (piece[0], piece[6], piece[1], piece[2], piece[7], piece[3])

    def xf(self, lx, lz):
        import end_city_loot as ecl
        wx, _y, wz = ecl.local_to_world(self.pc, lx, 0, lz)
        return wx, wz


def plan_end_city(seed, mc, x, z):
    """An End city from above: its towers' purpur roofs, the end stone brick
    floors, the magenta windows and end rods, the bridges, the ship (its deck, the
    dragon head at the bow); lit by height (the top of the tallest tower bright);
    the chests (and the elytra's place, cyan)."""
    import end_city_loot as ecl
    ps = ecl.pieces(seed, mc, int(x), int(z))
    if not ps:
        return None
    eps = [_EPiece(p, ecl._NAMES[p[4]]) for p in ps if 0 <= p[4] < len(ecl._NAMES)]
    res = _render(eps, None, _tops()["ver"].get("end_city", {}), relief=True, containers=False,
                  shadow=min(p[6] for p in ps) - 2)
    if res is None:
        return None
    res["chests"] = [(c[0], c[1], c[2], "chests/end_city_treasure")
                     for c in ecl.chest_list(seed, mc, int(x), int(z))]
    ship = next((p for p in ps if ecl._NAMES[p[4]] == "ship"), None)
    if ship is not None:                            # the elytra's item frame
        import numpy as np
        ex, _ey, ez = ecl.local_to_world(ship, 6, 5, 7)
        a = np.frombuffer(res["rgba"], np.uint8).reshape(res["h"], res["w"], 4).copy()
        X, Z = ex - res["x0"], ez - res["z0"]
        if 0 <= X < res["w"] and 0 <= Z < res["h"]:
            a[Z, X] = (90, 230, 235, 255)
            res["rgba"] = a.tobytes()
        res["elytra"] = (ex, _ey, ez)
    res["boxes"] = ps
    return res


# ── (05/11) the HD view: each block drawn 4 × 4 with its texture's pattern ──
HD = 4
_FAM = {}


def _family(n):
    """The texture pattern of a block (top face)."""
    if n is None:
        return 0
    f = _FAM.get(n)
    if f is not None:
        return f
    if n in ("bookshelf",):
        f = 7
    elif "end_portal_frame" in n:
        f = 8
    elif n in ("iron_bars",) or n.endswith("_pane"):
        f = 9
    elif "fence" in n or n.endswith("_wall") or "chain" in n:
        f = 10
    elif n in ("lava", "magma_block"):
        f = 11
    elif n == "water":
        f = 12
    elif "glass" in n:
        f = 13
    elif "pillar" in n or n.endswith("_log") or n.endswith("_wood") or "basalt" in n or "stem" in n:
        f = 14
    elif "leaves" in n or "wart" in n or "moss" in n or "vine" in n:
        f = 15
    elif "purpur_block" == n or n.startswith("purpur_"):
        f = 16
    elif "planks" in n or n.endswith("_door") or "ladder" in n or "trapdoor" in n or "barrel" in n:
        f = 3
    elif "brick" in n or "tiles" in n:
        f = 2
    elif "stairs" in n or "slab" in n:
        f = 4
    elif any(k in n for k in ("cobble", "gravel", "sand", "dirt", "netherrack", "soul", "end_stone",
                                "blackstone", "deepslate", "andesite", "granite", "tuff", "path")):
        f = 5
    elif "wool" in n or "carpet" in n or "terracotta" in n or "concrete" in n:
        f = 6
    else:
        f = 1
    _FAM[n] = f
    return f


def _pats():
    import numpy as np
    P = np.ones((17, 4, 4), np.float32)
    B, m = 1.07, 0.80
    P[2] = [[B, B, B, m], [m, m, m, m], [B, m, B, B], [m, m, m, m]]           # bricks
    P[3] = [[1.06, 1.06, 1.06, 1.06], [0.97, 0.97, 0.97, 0.97],                 # planks
            [1.03, 1.03, 1.03, 1.03], [0.84, 0.84, 0.84, 0.84]]
    P[4] = [[1.08] * 4, [1.08] * 4, [0.9] * 4, [0.9] * 4]                      # a step
    P[6] = [[1.03, 1.0, 1.02, 0.99], [1.0, 1.02, 0.99, 1.01], [1.01, 0.99, 1.03, 1.0],
            [0.99, 1.01, 1.0, 1.02]]
    P[7] = [[0.85] * 4, [1, 1, 1, 1], [1, 1, 1, 1], [0.85] * 4]
    P[8] = [[1.08, 1.08, 1.08, 1.08], [1.08, 0.55, 0.55, 1.08], [1.08, 0.55, 0.55, 1.08],
            [0.92, 0.92, 0.92, 0.92]]
    P[13] = [[1.15, 1.15, 1.15, 1.15], [1.15, 0.92, 0.92, 1.15], [1.15, 0.92, 0.92, 1.15],
             [1.15, 1.15, 1.15, 1.15]]
    P[14] = [[0.88, 0.88, 0.88, 0.88], [0.88, 1.1, 1.1, 0.88], [0.88, 1.1, 1.1, 0.88],
             [0.88, 0.88, 0.88, 0.88]]
    P[16] = [[1.08, 1.0, 1.08, 1.0], [1.0, 0.9, 1.0, 0.9], [1.08, 1.0, 1.08, 1.0],
             [1.0, 0.9, 1.0, 0.9]]
    return P


_PAT = None
# the noise of each family (± per pixel)
_NOISE = (0.05, 0.05, 0.04, 0.04, 0.04, 0.12, 0.03, 0.03, 0.03, 0.0, 0.0, 0.16, 0.06, 0.02,
          0.04, 0.16, 0.03)


def hd_image(plan, raw=None):
    """(rgba bytes, width, height) of the plan drawn 4 pixels per block (the textures'
    patterns over the plan's lit colours), or None without block names."""
    import numpy as np
    global _PAT
    if plan.get("pc") is not None and (raw is None or raw is plan["rgba"]):    # (05/11) clean plans
        cat = np.frombuffer(plan["pc"], np.uint8).reshape(plan["h"], plan["w"])
        hy = np.frombuffer(plan["py"], np.int32).reshape(plan["h"], plan["w"])
        return _clean_rgba(cat, hy, plan.get("edge", (28, 28, 32)), HD)
    # (05/11) the textured HD is off everywhere else: ed found the plans clearer
    # without it (flat blocks, as the villages always were)
    return None
    if _PAT is None:
        _PAT = _pats()
    w, h, S = plan["w"], plan["h"], HD
    names = plan["names"]
    nm = np.frombuffer(plan["nm"], np.int16).reshape(h, w)
    a = np.frombuffer(plan["rgba"], np.uint8).reshape(h, w, 4)
    fam_of = np.array([_family(n) for n in names] + [0], np.int16)
    F = fam_of[np.where(nm >= 0, nm, len(names))]
    pat = _PAT[F]                                            # (h, w, 4, 4)
    fac = pat.transpose(0, 2, 1, 3).reshape(h * S, w * S)
    gx, gz = np.meshgrid(np.arange(w * S) + plan["x0"] * S, np.arange(h * S) + plan["z0"] * S)
    nz = (((gx * 73856093) ^ (gz * 19349663)) & 0xff) / 127.5 - 1.0
    amp = np.array(_NOISE, np.float32)[np.repeat(np.repeat(F, S, 0), S, 1)]
    fac = fac * (1.0 + amp * nz)
    # each block's bevel: lit from the north-west
    ix = np.arange(h * S) % S
    jx = np.arange(w * S) % S
    bev = np.ones((h * S, w * S), np.float32)
    bev[ix == 0, :] *= 1.05
    bev[ix == S - 1, :] *= 0.93
    bev[:, jx == 0] *= 1.03
    bev[:, jx == S - 1] *= 0.95
    blk = np.repeat(np.repeat(nm >= 0, S, 0), S, 1)
    fac = np.where(blk, fac * bev, 1.0)
    rgb = np.repeat(np.repeat(a[..., :3], S, 0), S, 1).astype(np.float32) * fac[..., None]
    al = np.repeat(np.repeat(a[..., 3], S, 0), S, 1)
    Fb = np.repeat(np.repeat(F, S, 0), S, 1)
    # bookshelves: the books' spines
    m7 = Fb == 7
    if m7.any():
        books = np.array([(150, 40, 40), (60, 80, 150), (70, 120, 60), (170, 140, 70)], np.float32)
        k = ((gx // 1) * 7 + (gz // S) * 3) % 4
        mid = m7 & ((ix[:, None] == 1) | (ix[:, None] == 2)) & np.ones_like(m7)
        rgb[mid] = rgb[mid] * 0.45 + books[k[mid]] * 0.55 * (rgb[mid].mean(-1, keepdims=True) / 90.0)
    # iron bars: thin bars over the dark
    m9 = Fb == 9
    if m9.any():
        bar = (jx[None, :] % 2 == 1) | (ix[:, None] == 0)
        rgb[m9 & ~bar] *= 0.35
    # fences: a post in the middle
    m10 = Fb == 10
    if m10.any():
        post = (ix[:, None] >= 1) & (ix[:, None] <= 2) & (jx[None, :] >= 1) & (jx[None, :] <= 2)
        rgb[m10 & ~post] *= 0.55
    # lava, water: bright speckles
    m11 = Fb == 11
    if m11.any():
        sp = m11 & (nz > 0.7)
        rgb[sp] = np.minimum(rgb[sp] * 1.25 + 30, 255)
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    return np.dstack([rgb, al]).tobytes(), w * S, h * S


def _sun_shadow(rgba, w, h, hy, mk, ground, slope=0.3, maxd=26, strength=0.72):
    """(05/11) the sun's shadow (from the north-west, like the outlines): each tall
    part casts it to the south-east, length = its height × slope, on the lower
    parts and on the ground around (ground: the Y the shadow falls on outside)."""
    import numpy as np
    a = np.frombuffer(rgba, np.uint8).reshape(h, w, 4).copy()
    hh = np.where(mk, hy, ground).astype(np.float32)
    sh = np.zeros((h, w), bool)
    for d in range(1, maxd + 1):
        src = np.full((h, w), -1e9, np.float32)
        src[d:, d:] = hh[:-d, :-d]
        sh |= src - d / slope * 1.0 > hh + 0.5
        if d > 4 and not ((hh.max() - d / slope) > hh.min()):
            break
    inside = sh & mk
    a[inside, :3] = (a[inside, :3].astype(np.float32) * strength).astype(np.uint8)
    out = sh & ~mk
    al = a[..., 3]
    a[out, :3] = (a[out, :3].astype(np.float32) * 0.3).astype(np.uint8)
    a[out, 3] = np.maximum(al[out], 72)
    return a.tobytes()


# ── (05/11) the underground structures (strongholds, mineshafts) seen as in the game,
# kept clear: each block its in-game colour, flat (no heavy texture), the walls' cut tops
# lit, the floors in their shade, the furniture recognisable (shelves, bars, portal,
# rails, beams…); deeper = darker ──
_CLEAN = {1: (140, 139, 140),                                # wall: stone bricks, cut top
          2: (98, 97, 98), 3: (98, 97, 98), 4: (98, 97, 98), 6: (98, 97, 98), 8: (98, 97, 98),
          13: (104, 103, 104), 18: (98, 97, 98),            # floors: stone bricks in shade
          5: (112, 84, 52), 7: (150, 152, 158), 9: (124, 152, 114), 10: (30, 84, 70),
          11: (222, 106, 26), 12: (54, 92, 204), 14: (162, 130, 78), 15: (198, 198, 202),
          16: (30, 38, 48),
          # mineshafts: the rock floor, oak beams, rails, cobwebs, the dirt room
          20: (104, 104, 104), 21: (162, 130, 78), 22: (104, 104, 104), 23: (104, 104, 104),
          24: (134, 96, 67), 25: (104, 104, 104), 26: (110, 110, 110), 27: (40, 38, 36),
          28: (104, 104, 104), 29: (70, 70, 76)}            # 29: deepslate floor
_CLEAN_WALLS = (1, 5, 7, 9, 10, 15, 16, 21)                   # raised: no shade on them


def _clean_rgba(cat, hy, edge=(28, 28, 32), S=1):
    """(rgba bytes, w, h) of a category grid (0 = nothing) drawn as in the game; S > 1:
    S pixels per block, each block with its edges and the furniture's details."""
    import numpy as np
    h, w = cat.shape
    pal = np.zeros((256, 3), np.float32)
    for k, v in _CLEAN.items():
        pal[k] = v
    mk = cat > 0
    lo, hi = (int(hy[mk].min()), int(hy[mk].max())) if mk.any() else (0, 1)
    t = np.clip((hy - lo) / max(1, hi - lo), 0, 1)
    wall = np.isin(cat, (1,))
    raised = np.isin(cat, _CLEAN_WALLS)
    nw = np.zeros_like(wall)                         # a floor with a wall north / west
    nw[1:, :] |= wall[:-1, :]
    nw[:, 1:] |= wall[:, :-1]
    rep = lambda a: np.repeat(np.repeat(a, S, 0), S, 1)
    C = rep(cat)
    rgb = pal[C].copy()
    if S == 1:
        rgb[mk & ~raised & nw] *= 0.8
    else:
        iy = (np.arange(h * S) % S)[:, None]
        ix = (np.arange(w * S) % S)[None, :]
        R = rep(raised)
        fl = rep(mk & ~raised)
        # each block: its edges (lit from the north-west), soft on the floors
        rgb[R & (iy == 0)] *= 1.12
        rgb[R & (ix == 0)] *= 1.06
        rgb[R & (iy == S - 1)] *= 0.82
        rgb[R & (ix == S - 1)] *= 0.9
        rgb[fl & ((iy == S - 1) | (ix == S - 1))] *= 0.92
        wn = rep(np.vstack([np.zeros((1, w), bool), wall[:-1]]))
        ww = rep(np.hstack([np.zeros((h, 1), bool), wall[:, :-1]]))
        rgb[fl & wn & (iy <= 1)] *= 0.78                # the walls' shade on the floor
        rgb[fl & ww & (ix <= 0)] *= 0.84
        m = C == 5                                       # bookshelves: the books
        bk = np.array([(150, 44, 38), (52, 78, 150), (64, 118, 58), (176, 146, 70),
                       (120, 64, 120)], np.float32)
        sel = m & (iy >= 1) & (iy <= S - 2) & (ix <= S - 2)
        gx = np.arange(w * S)[None, :] + (np.arange(h * S)[:, None] // S) * 3
        rgb[sel] = bk[(gx % 5)[sel]] * 0.85
        m = C == 7                                       # iron bars over the floor
        rgb[m & ~((ix == 1) | (ix == S - 1))] = pal[2] * 0.8
        m = (C == 9) | (C == 10)                         # the portal frame, its socket / eye
        mid = (iy >= 1) & (iy <= S - 2) & (ix >= 1) & (ix <= S - 2)
        rgb[(C == 9) & mid] = (86, 112, 84)
        rgb[(C == 10) & mid] = (40, 150, 120)
        m = C == 16                                      # the spawner's cage
        rgb[m & ((ix % 2 == 1) | (iy % 2 == 1))] = (64, 76, 92)
        m = (C == 13) | (C == 26)                        # steps
        rgb[m & (iy == S - 1)] *= 0.78
        m = C == 11
        rgb[m & ((ix + iy * 3) % 5 == 0)] = (255, 196, 80)
        m = C == 12
        rgb[m & ((ix + iy * 2) % 6 == 0)] = (120, 160, 240)
        m = C == 14                                      # planks: their seams
        rgb[m & (iy == 1)] *= 0.86
        m = C == 21
        rgb[m & ((iy == 1) | (iy == S - 1))] *= 0.86
        m = C == 22                                      # rails N-S: ties, then the rails
        rgb[m & (iy % 2 == 0)] = (116, 88, 56)
        rgb[m & ((ix == 0) | (ix == S - 1))] = (150, 150, 156)
        m = C == 28
        rgb[m & (ix % 2 == 0)] = (116, 88, 56)
        rgb[m & ((iy == 0) | (iy == S - 1))] = (150, 150, 156)
        m = C == 23                                      # cobweb
        rgb[m & ((ix == iy) | (ix == S - 1 - iy))] = (232, 232, 236)
        t = rep(t)
        mk = rep(mk)
        h, w = h * S, w * S
    rgb *= (0.7 + 0.3 * t)[..., None]                    # deeper = darker
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    alpha = np.where(mk, 255, 0).astype(np.uint8)
    d = np.zeros_like(mk)
    rr = 1 if S == 1 else 2
    for dz in range(-rr, rr + 1):
        for dx in range(-rr, rr + 1):
            if dx or dz:
                d |= np.roll(np.roll(mk, dz, 0), dx, 1)
    edge_ = d & ~mk
    rgb[edge_] = edge
    alpha[edge_] = 225
    k = 2 * S
    d2 = np.zeros_like(mk)
    d2[k:, k:] |= mk[:-k, :-k]
    shad = d2 & ~mk & ~edge_
    rgb[shad] = (10, 10, 14)
    alpha[shad] = 80
    return np.dstack([rgb, alpha]).astype(np.uint8).tobytes(), w, h


# ── (05/11) single templates (ruined portals, shipwrecks, ocean ruins, igloos),
# drawn like the villages (jigsaw_tops: their top views, read from the jar) ──
class _TPiece:
    """A template placed: its name, origin (x, y, z), the local → world (x, z) map."""

    class _El:
        kind = "single"

        def __init__(self, loc):
            self.loc = loc

    def __init__(self, name, origin, size, f):
        self.el = self._El(name)
        self.pos = (0, origin[1], 0)
        self._f = f
        pts = [f(a, b) for a in (0, size[0] - 1) for b in (0, size[1] - 1)]
        xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        self.box = (min(xs), origin[1], min(zs), max(xs), origin[1] + 8, max(zs))

    def xf(self, lx, lz):
        return self._f(lx, lz)


def _tsize(grp, name):
    h = _tops()["ver"].get(grp, {}).get(name)
    if h is None:
        return None
    sx, sz, _c = _tops()["tpl"][h]
    return sx, sz


def _loot_chests(key, seed, mc, x, z):
    """[(x, y, z, table)] of a structure's chests (structure_loot: the same places the
    loot window shows, so a click opens it on that chest)."""
    out = []
    try:
        import structure_loot as sl
        for c in sl.chests(key, int(seed), int(mc), int(x), int(z)) or ():
            p = c.get("pos")
            if p and p[0] is not None and c.get("slots") is not None:
                out.append((p[0], p[1], p[2], c.get("table", "")))
    except Exception:
        pass
    seen, res = set(), []
    for c in out:                                 # (alternatives: one marker per place)
        if (c[0], c[2]) not in seen:
            seen.add((c[0], c[2]))
            res.append(c)
    return res


def plan_ruined_portal(seed, mc, x, z):
    """A ruined portal from above: its template (obsidian, crying obsidian, netherrack,
    stone bricks, gold, lava…) turned and mirrored as placed; its chest."""
    import ruined_portal_loot as rpl
    cx, cz = int(x) >> 4, int(z) >> 4
    g = rpl.layout(seed, mc, cx, cz)
    if g is None or rpl.ghost_116(mc, g, cx, cz):
        return None
    size = g["size"]
    px, pz = size[0] // 2, size[2] // 2
    ox, oy, oz = g["origin"]
    tz = _tsize("ruined_portal", g["template"])
    if tz is None:
        return None
    pc = _TPiece(g["template"], g["origin"], tz,
                 lambda a, b: tuple(v + o for v, o in zip(rpl.transform(a, b, g["mirror"], g["rot"], px, pz),
                                                          (ox, oz))))
    res = _render([pc], None, _tops()["ver"]["ruined_portal"], relief=True, containers=False,
                  shadow=oy, ground_solid=True)
    if res is None:
        return None
    res["chests"] = _loot_chests("ruined_portal", seed, mc, x, z)
    res["boxes"] = rpl.map_boxes(seed, mc, cx, cz)
    return res


class _NoCols:
    """(05/11) a Nether ruined portal: no terrain here (its Y only lights the plan)."""

    def base_height(self, x, z, floor=False):
        return 64

    def at(self, x, y, z):
        return 1 if y < 60 else 0


def plan_ruined_portal_nether(seed, mc, x, z):
    """A Nether ruined portal from above (its template turned and mirrored as placed:
    the template, rotation and mirror come before its Y)."""
    import ruined_portal_loot as rpl
    cx, cz = int(x) >> 4, int(z) >> 4
    try:
        g = rpl.gen(seed, mc, cx, cz, "nether", _NoCols())
    except Exception:
        return None
    size = g["size"]
    px, pz = size[0] // 2, size[2] // 2
    ox, _oy, oz = g["origin"]
    tz = _tsize("ruined_portal", g["template"])
    if tz is None:
        return None
    pc = _TPiece(g["template"], (ox, 0, oz), tz,
                 lambda a, b: tuple(v + o for v, o in zip(rpl.transform(a, b, g["mirror"], g["rot"], px, pz),
                                                          (ox, oz))))
    res = _render([pc], None, _tops()["ver"]["ruined_portal"], relief=True, containers=False,
                  shadow=0, ground_solid=True)
    if res is not None:
        res["chests"] = []
    return res


def plan_end_gateway(seed, mc, x, z):
    """(05/11) An End gateway from above: its bedrock shell (the beam's column), the
    gateway block inside."""
    import numpy as np
    m = 3
    w = h = 3 + 2 * m + 2
    x0, z0 = int(x) - 1 - m, int(z) - 1 - m
    names = ["bedrock", "end_gateway"]
    nm = np.full((h, w), -1, np.int16)
    hy = np.full((h, w), -10000, np.int32)
    for i, j, k, y in ((0, 0, 0, 2), (1, 0, 0, 1), (-1, 0, 0, 1), (0, 1, 0, 1), (0, -1, 0, 1)):
        nm[j + 1 + m, i + 1 + m] = k
        hy[j + 1 + m, i + 1 + m] = y
    vis = nm >= 0
    pal = np.array([(86, 86, 86), (20, 16, 40), (0, 0, 0)], np.float32)
    col = pal[np.where(vis, nm, 2)]
    col[1 + m, 1 + m] = (60, 60, 64)
    rgb = np.clip(col, 0, 255).astype(np.uint8)
    alpha = np.where(vis, 255, 0).astype(np.uint8)
    d = np.zeros_like(vis)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dz:
                d |= np.roll(np.roll(vis, dz, 0), dx, 1)
    edge = d & ~vis
    rgb[edge] = (20, 20, 24); alpha[edge] = 170
    rgba = _sun_shadow(np.dstack([rgb, alpha]).astype(np.uint8).tobytes(), w, h,
                       np.where(vis, hy, 0), vis, 0)
    return dict(x0=x0, z0=z0, w=w, h=h, rgba=rgba, chests=[], nm=nm.tobytes(), names=names)


def plan_shipwreck(seed, mc, x, z):
    """A shipwreck from above: its deck, masts, hull (planks, logs, trapdoors) as
    placed (template + rotation); its chests."""
    import scattered_loot as sc
    cx, cz = int(x) >> 4, int(z) >> 4
    _bb, name, rot, beached = sc.shipwreck_box(seed, mc, cx, cz)
    tz = _tsize("shipwreck", name)
    if tz is None:
        return None
    ox, oz = cx << 4, cz << 4
    pc = _TPiece(name, (ox, 0, oz), tz,
                 lambda a, b: (lambda r: (r[0] + ox, r[1] + oz))(sc._rot(a, b, rot, *sc._SHIP_PIVOT)))
    res = _render([pc], None, _tops()["ver"]["shipwreck"], relief=True, containers=False)
    if res is None:
        return None
    res["chests"] = _loot_chests("shipwreck", seed, mc, x, z)
    return res


def plan_ocean_ruin(seed, mc, x, z):
    """An ocean ruin (and its cluster) from above: stone bricks / sandstone, cracked,
    mossy, as placed; its chests."""
    import scattered_loot as sc
    cx, cz = int(x) >> 4, int(z) >> 4
    warm = sc.ocean_ruin_warm(seed, mc, cx, cz)
    ps = []
    for name, px, pz, rot, big in sc.ocean_ruin_layout(seed, cx, cz, warm):
        tz = _tsize("underwater_ruin", name)
        if tz is None:
            continue
        ps.append(_TPiece(name, (px, 0, pz), tz,
                          lambda a, b, px=px, pz=pz, rot=rot: (lambda r: (r[0] + px, r[1] + pz))(
                              sc._rot(a, b, rot, 0, 0))))
    if not ps:
        return None
    res = _render(ps, None, _tops()["ver"]["underwater_ruin"], relief=False, containers=False)
    if res is None:
        return None
    res["chests"] = _loot_chests("ocean_ruin", seed, mc, x, z)
    return res


def plan_igloo(seed, mc, x, z):
    """An igloo from above: its snow dome (the ice window, the door). With a basement:
    its chest, and the basement in X-ray (the laboratory: brewing stand, chest,
    cauldron, the cell) as a second view chosen in the bubble."""
    import scattered_loot as sc
    cx, cz = int(x) >> 4, int(z) >> 4
    rot, depth = sc.igloo_layout(seed, cx, cz)
    tz = _tsize("igloo", "top")
    if tz is None:
        return None
    ox, oz = cx << 4, cz << 4
    top = _TPiece("top", (ox, 0, oz), tz,
                  lambda a, b: (lambda r: (r[0] + ox, r[1] + oz))(sc._rot(a, b, rot, 3, 5)))
    ps = [top]
    base = None
    if depth is not None:
        bz = _tsize("igloo_x", "bottom")
        if bz is not None:
            bo = (ox, -3 - 3 * depth, oz - 2)
            base = _TPiece("bottom", bo, bz,
                           lambda a, b: (lambda r: (r[0] + bo[0], r[1] + bo[2]))(sc._rot(a, b, rot, 3, 7)))
            ps.append(base)
    m = 3
    bx0 = min(p.box[0] for p in ps) - m
    bz0 = min(p.box[2] for p in ps) - m
    bounds = (bx0, bz0, max(p.box[3] for p in ps) + m + 1 - bx0, max(p.box[5] for p in ps) + m + 1 - bz0)
    res = _render([top], None, _tops()["ver"]["igloo"], relief=True, containers=False, ground_solid=True,
                  bounds=bounds)
    if res is None:
        return None
    res["chests"] = []
    if base is not None:
        ch = _loot_chests("igloo", seed, mc, x, z)
        res["chests"] = ch                         # (under the dome: shown on both views)
        r2 = _render([base], None, _tops()["ver"]["igloo_x"], relief=False, containers=False,
                     ground_solid=True, bounds=bounds)
        if r2 is not None:
            # (05/11) the default view shows both: the basement (larger than the dome)
            # around the dome, a little darker (it is underground)
            import numpy as np
            h_, w_ = res["h"], res["w"]
            top = np.frombuffer(res["rgba"], np.uint8).reshape(h_, w_, 4).astype(np.float32)
            bot = np.frombuffer(r2["rgba"], np.uint8).reshape(h_, w_, 4).astype(np.float32)
            bot[..., :3] *= 0.72
            at = top[..., 3:4] / 255.0
            ab = bot[..., 3:4] / 255.0
            a = at + ab * (1 - at)
            rgb = (top[..., :3] * at + bot[..., :3] * ab * (1 - at)) / np.maximum(a, 1e-6)
            res["rgba"] = np.dstack([np.clip(rgb, 0, 255), a * 255]).astype(np.uint8).tobytes()
            res["floors"] = {1: r2["rgba"]}
            res["floor_chests"] = {1: ch}
            res["floor_labels"] = ((0, "Igloo", "The igloo and, around it, its basement (darker)"),
                                   (1, "Basement", "X-ray: the basement under the igloo"))
    return res


# ── (05/11) desert pyramids, jungle temples, swamp huts, ocean monuments: built in
# code; their top view read from a server (tools/servercheck/scatcat.py) ──
_STOPS = None
_SC_DIMS = {"desert_pyramid": ((21, 15, 21), 0), "jungle_temple": ((12, 10, 15), 0),
            "swamp_hut": ((7, 7, 9), 0), "monument": ((58, 23, 58), -29)}


def _stops():
    global _STOPS
    if _STOPS is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "scattered_tops.json.gz")
        with gzip.open(p, "rt") as f:
            _STOPS = json.load(f)
    return _STOPS


def plan_scattered(seed, mc, key, x, z):
    """A desert pyramid / jungle temple / swamp hut / ocean monument from above, turned
    as placed (ScatteredFeaturePiece: its random direction); its chests."""
    import numpy as np
    import scattered_loot as sc
    D = _stops()
    if key not in D["tops"]:
        return None
    (w_, h_, d_), off = _SC_DIMS[key]
    cx, cz = int(x) >> 4, int(z) >> 4
    if key in ("desert_pyramid", "jungle_temple"):
        try:
            if sc._absent(key, seed, mc, cx, cz):     # (not built by the game here)
                return None
        except Exception:
            pass
    ori = sc._direction(seed, cx, cz)
    p = sc.Scattered((cx << 4) + off, 0, (cz << 4) + off, w_, h_, d_, ori)
    W, Dd, cells = D["tops"][key]
    names = D["names"]
    m = 3
    b = p.box
    e = 3
    x0, z0 = b[0] - m, b[2] - m
    w, h = b[3] - b[0] + 1 + 2 * m + e, b[5] - b[2] + 1 + 2 * m + e
    nm = np.full((h, w), -1, np.int16)
    hy = np.full((h, w), -10000, np.int32)
    for i, c in enumerate(cells.split(",")):
        if not c:
            continue
        k, hh = c.split(":")
        lx, lz = i % W, i // W
        X, Z = p.wx(lx, lz) - x0, p.wz(lx, lz) - z0
        nm[Z, X] = int(k, 16)
        hy[Z, X] = int(hh)
    vis = nm >= 0
    pal = np.array([_style(n)[1] or (128, 128, 128) for n in names] + [(0, 0, 0)], np.float32)
    col = pal[np.where(vis, nm, len(names))]
    north = np.vstack([hy[:1], hy[:-1]])
    col *= np.where(hy > north, 1.0, np.where(hy < north, 0.71, 0.86))[..., None]
    lo, hi = int(hy[vis].min()), int(hy[vis].max())
    t = np.clip((hy - lo) / max(1, hi - lo), 0, 1)
    col *= (0.8 + 0.35 * t)[..., None]
    hup = np.where(vis, hy, -10000)
    occ = np.zeros(hy.shape, np.float32)
    for dz, dx, wgt in ((1, 0, 1.0), (0, 1, 1.0), (-1, 0, 0.5), (0, -1, 0.5)):
        nb = np.roll(np.roll(hup, dz, 0), dx, 1)
        occ += wgt * ((nb > hy + 1) & vis)
    col *= (0.86 ** occ)[..., None]
    rgb = np.clip(col, 0, 255).astype(np.uint8)
    alpha = np.where(vis, 255, 0).astype(np.uint8)
    d = np.zeros_like(vis)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dz:
                d |= np.roll(np.roll(vis, dz, 0), dx, 1)
    edge = d & ~vis
    rgb[edge] = (24, 22, 20); alpha[edge] = 170
    rgba = np.dstack([rgb, alpha]).astype(np.uint8).tobytes()
    rgba = _sun_shadow(rgba, w, h, np.where(vis, hy, lo), vis, lo - 1)
    lk = {"desert_pyramid": "desert_pyramid", "jungle_temple": "jungle_temple"}.get(key)
    return dict(x0=int(x0), z0=int(z0), w=int(w), h=int(h), rgba=rgba,
                chests=_loot_chests(lk, seed, mc, x, z) if lk else [],
                nm=nm.tobytes(), names=names)


def plan_desert_well(seed, mc, x, z):
    """(05/11) A desert well from above (DesertWellFeature): its sandstone roof on four
    pillars, the rim with its four slabs, the water under the roof."""
    import numpy as np
    m = 3
    w = h = 5 + 2 * m + 2
    x0, z0 = int(x) - 2 - m, int(z) - 2 - m
    names = ["sandstone", "sandstone_slab", "water"]
    nm = np.full((h, w), -1, np.int16)
    hy = np.full((h, w), -10000, np.int32)
    for i in range(-2, 3):
        for j in range(-2, 3):
            if abs(i) == 2 or abs(j) == 2:            # the rim (slabs in the middle of each side)
                k, y = (1 if (i == 0 or j == 0) else 0), 1
            else:                                      # the roof (sandstone in the middle)
                k, y = (0 if (i == 0 and j == 0) else 1), 4
            nm[j + 2 + m, i + 2 + m] = k
            hy[j + 2 + m, i + 2 + m] = y
    vis = nm >= 0
    pal = np.array([(216, 203, 155), (206, 193, 146), (64, 64, 255), (0, 0, 0)], np.float32)
    col = pal[np.where(vis, nm, 3)]
    north = np.vstack([hy[:1], hy[:-1]])
    col *= np.where(hy > north, 1.0, np.where(hy < north, 0.71, 0.86))[..., None]
    rgb = np.clip(col, 0, 255).astype(np.uint8)
    alpha = np.where(vis, 255, 0).astype(np.uint8)
    d = np.zeros_like(vis)
    for dz in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dz:
                d |= np.roll(np.roll(vis, dz, 0), dx, 1)
    edge = d & ~vis
    rgb[edge] = (60, 52, 34); alpha[edge] = 170
    rgba = _sun_shadow(np.dstack([rgb, alpha]).astype(np.uint8).tobytes(), w, h,
                       np.where(vis, hy, 0), vis, 0)
    return dict(x0=x0, z0=z0, w=w, h=h, rgba=rgba, chests=[], nm=nm.tobytes(), names=names)


# ── (05/11) trail ruins and trial chambers (jigsaw_extra: their layout, exact) ──
def plan_trail_ruins(seed, mc, x, z):
    """Trail ruins from above, as if dug out (they lie buried ~15 blocks deep): the
    terracotta and mud brick walls, the gravel, the tower; lit by height."""
    import jigsaw_extra as je
    lay = je.layout(seed, mc, "trail_ruins", int(x) >> 4, int(z) >> 4)
    if not lay:
        return None
    res = _render(lay[0], je.gen("trail_ruins"), _tops()["ver"].get("trail_ruins", {}), relief=True,
                  containers=False)
    if res is not None:
        res["chests"] = []
    return res


def plan_trial_chambers(seed, mc, x, z):
    """Trial chambers in X-ray (they are underground: each room without its ceiling):
    tuff bricks, copper, the corridors, the chambers with their trial spawners and
    vaults, the water and the decorations; deeper = darker."""
    import jigsaw_extra as je
    lay = je.layout(seed, mc, "trial_chambers", int(x) >> 4, int(z) >> 4)
    if not lay:
        return None
    res = _render(lay[0], je.gen("trial_chambers"), _tops()["ver"].get("trial_chambers", {}), relief=True,
                  containers=False)
    if res is not None:
        res["chests"] = []
    return res
