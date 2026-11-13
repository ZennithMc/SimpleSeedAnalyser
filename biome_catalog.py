"""
biome_catalog.py — Overworld biome list in cubiomes-viewer order (pure data,
no GUI toolkit). Shared by the Tk picker (biome_picker.py) and the Qt UI.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from cubiomes_wrap import BIOME_COLORS, BIOME_NAMES
from cubiomes_wrap import (
    PLAINS, SUNFLOWER_PLAINS, FOREST, FLOWER_FOREST, BIRCH_FOREST,
    OLD_GROWTH_BIRCH_FOREST, DARK_FOREST, PALE_GARDEN, DAPPLED_FOREST,
    SULFUR_CAVES,
    TAIGA, OLD_GROWTH_PINE_TAIGA, OLD_GROWTH_SPRUCE_TAIGA,
    SNOWY_TAIGA,
    JUNGLE, SPARSE_JUNGLE, BAMBOO_JUNGLE,
    SAVANNA, SAVANNA_PLATEAU, WINDSWEPT_SAVANNA,
    DESERT, BADLANDS, WOODED_BADLANDS, ERODED_BADLANDS,
    SWAMP, MANGROVE_SWAMP,
    BEACH, SNOWY_BEACH, STONE_SHORE,
    RIVER, FROZEN_RIVER,
    MUSHROOM_FIELDS, ICE_SPIKES, SNOWY_TUNDRA,
    MEADOW, GROVE, CHERRY_GROVE, SNOWY_SLOPES,
    JAGGED_PEAKS, FROZEN_PEAKS, STONY_PEAKS,
    MOUNTAINS, WINDSWEPT_FOREST, WINDSWEPT_GRAVELLY_HILLS,
    OCEAN, DEEP_OCEAN, WARM_OCEAN, LUKEWARM_OCEAN, DEEP_LUKEWARM_OCEAN,
    COLD_OCEAN, DEEP_COLD_OCEAN, FROZEN_OCEAN, DEEP_FROZEN_OCEAN,
)


# ─────────────────────────────────────────────────────────────────────────────
# Ordered list — Cubiomes Viewer's grouping (overworld surface only)
# Each tuple: (biome_id, display_name)
# Excluded: caves (lush, dripstone), deep dark, nether, end.
# ─────────────────────────────────────────────────────────────────────────────

# Group headers (None entries) act as section dividers in the picker.
OVERWORLD_BIOMES_ORDERED: List[Tuple[Optional[int], str]] = [
    # ── Lush ──
    (None, "Lush"),
    (PLAINS,                  "Plains"),
    (SUNFLOWER_PLAINS,        "Sunflower Plains"),
    (FOREST,                  "Forest"),
    (FLOWER_FOREST,           "Flower Forest"),
    (BIRCH_FOREST,            "Birch Forest"),
    (OLD_GROWTH_BIRCH_FOREST, "Old Growth Birch Forest"),
    (DARK_FOREST,             "Dark Forest"),
    (PALE_GARDEN,             "Pale Garden"),
    (DAPPLED_FOREST,          "Dappled Forest"),

    # ── Taiga ──
    (None, "Taiga"),
    (TAIGA,                   "Taiga"),
    (OLD_GROWTH_PINE_TAIGA,   "Old Growth Pine Taiga"),
    (OLD_GROWTH_SPRUCE_TAIGA, "Old Growth Spruce Taiga"),

    # ── Jungle ──
    (None, "Jungle"),
    (JUNGLE,                  "Jungle"),
    (SPARSE_JUNGLE,           "Sparse Jungle"),
    (BAMBOO_JUNGLE,           "Bamboo Jungle"),

    # ── Savanna ──
    (None, "Savanna"),
    (SAVANNA,                 "Savanna"),
    (SAVANNA_PLATEAU,         "Savanna Plateau"),
    (WINDSWEPT_SAVANNA,       "Windswept Savanna"),

    # ── Arid ──
    (None, "Arid"),
    (DESERT,                  "Desert"),
    (BADLANDS,                "Badlands"),
    (WOODED_BADLANDS,         "Wooded Badlands"),
    (ERODED_BADLANDS,         "Eroded Badlands"),

    # ── Swamp ──
    (None, "Swamp"),
    (SWAMP,                   "Swamp"),
    (MANGROVE_SWAMP,          "Mangrove Swamp"),

    # ── Shores ──
    (None, "Shores"),
    (BEACH,                   "Beach"),
    (SNOWY_BEACH,             "Snowy Beach"),
    (STONE_SHORE,             "Stony Shore"),

    # ── Rivers ──
    (None, "Rivers"),
    (RIVER,                   "River"),
    (FROZEN_RIVER,            "Frozen River"),

    # ── Snowy ──
    (None, "Snowy"),
    (SNOWY_TUNDRA,            "Snowy Plains"),
    (SNOWY_TAIGA,             "Snowy Taiga"),
    (ICE_SPIKES,              "Ice Spikes"),

    # ── Mountains ──
    (None, "Mountains"),
    (MEADOW,                  "Meadow"),
    (CHERRY_GROVE,            "Cherry Grove"),
    (GROVE,                   "Grove"),
    (SNOWY_SLOPES,            "Snowy Slopes"),
    (STONY_PEAKS,             "Stony Peaks"),
    (JAGGED_PEAKS,            "Jagged Peaks"),
    (FROZEN_PEAKS,            "Frozen Peaks"),
    (MOUNTAINS,               "Windswept Hills"),
    (WINDSWEPT_FOREST,        "Windswept Forest"),
    (WINDSWEPT_GRAVELLY_HILLS,"Windswept Gravelly Hills"),

    # ── Mushroom ──
    (None, "Mushroom"),
    (MUSHROOM_FIELDS,         "Mushroom Fields"),

    # ── Caves (26.3+) ──
    (None, "Caves"),
    (SULFUR_CAVES,            "Sulfur Caves"),

    # ── Oceans ──
    (None, "Oceans"),
    (WARM_OCEAN,              "Warm Ocean"),
    (LUKEWARM_OCEAN,          "Lukewarm Ocean"),
    (DEEP_LUKEWARM_OCEAN,     "Deep Lukewarm Ocean"),
    (OCEAN,                   "Ocean"),
    (DEEP_OCEAN,              "Deep Ocean"),
    (COLD_OCEAN,              "Cold Ocean"),
    (DEEP_COLD_OCEAN,         "Deep Cold Ocean"),
    (FROZEN_OCEAN,            "Frozen Ocean"),
    (DEEP_FROZEN_OCEAN,       "Deep Frozen Ocean"),
]


# ─────────────────────────────────────────────────────────────────────────────
# (17/10) Version-dependent list: every overworld biome of the chosen version
# (cubiomes isOverworld), with that version's names (cubiomes biome2str: e.g.
# "Mountains", "Giant Tree Taiga Hills" before 1.18), grouped in the families
# above. Before, the picker only knew the 1.18+ biomes.
# ─────────────────────────────────────────────────────────────────────────────
FAMILY_ORDER: List[Tuple[str, List[int]]] = [
    ("Lush", [1, 129, 4, 132, 18, 27, 28, 155, 156, 29, 157, 186, 188]),
    ("Taiga", [5, 19, 133, 32, 33, 160, 161]),
    ("Jungle", [21, 22, 23, 149, 151, 168, 169]),
    ("Savanna", [35, 36, 163, 164]),
    ("Arid", [2, 17, 130, 37, 39, 38, 165, 166, 167]),
    ("Swamp", [6, 134, 184]),
    ("Shores", [16, 26, 25]),
    ("Rivers", [7, 11]),
    ("Snowy", [12, 13, 30, 31, 158, 140]),
    ("Mountains", [177, 185, 178, 179, 182, 180, 181, 3, 34, 131, 162]),
    ("Mushroom", [14, 15]),
    ("Caves", [174, 175, 183, 187]),
    ("Oceans", [44, 47, 45, 48, 0, 24, 46, 49, 10, 50]),
]

_LIST_CACHE: dict = {}
_NAME_CACHE: dict = {}


def _lib_funcs():
    import ctypes
    from cubiomes_wrap import get_lib
    L = get_lib()._lib
    L.isOverworld.argtypes = [ctypes.c_int, ctypes.c_int]
    L.isOverworld.restype = ctypes.c_int
    L.biome2str.argtypes = [ctypes.c_int, ctypes.c_int]
    L.biome2str.restype = ctypes.c_char_p
    return L


def _pretty(raw: str) -> str:
    small = {"of", "and"}
    return " ".join(w if w in small else w.capitalize() for w in raw.split("_"))


def biome_name_for(mc: int, biome_id: int) -> str:
    """Name of the biome in version mc (Minecraft's own name for it)."""
    key = (int(mc), int(biome_id))
    v = _NAME_CACHE.get(key)
    if v is None:
        try:
            raw = _lib_funcs().biome2str(int(mc), int(biome_id))
            v = _pretty(raw.decode()) if raw else None
        except Exception:
            v = None
        if not v:
            v = biome_display_name(biome_id)
        _NAME_CACHE[key] = v
    return v


def overworld_biomes_for(mc: int) -> List[Tuple[Optional[int], str]]:
    """OVERWORLD_BIOMES_ORDERED for version mc: (None, family) headers then
    (id, name); only the biomes that generate in that version, cave biomes
    included (1.18+)."""
    mc = int(mc)
    got = _LIST_CACHE.get(mc)
    if got is not None:
        return got
    try:
        L = _lib_funcs()
        ids = [i for i in range(256) if L.isOverworld(mc, i)]
    except Exception:                       # no library: the 1.18+ list
        return OVERWORLD_BIOMES_ORDERED
    out: List[Tuple[Optional[int], str]] = []
    seen = set()
    for fam, members in FAMILY_ORDER:
        mine = [b for b in members if b in ids]
        if mine:
            out.append((None, fam))
            out += [(b, biome_name_for(mc, b)) for b in mine]
            seen.update(mine)
    rest = [b for b in ids if b not in seen]
    if rest:
        out.append((None, "Other"))
        out += [(b, biome_name_for(mc, b)) for b in rest]
    _LIST_CACHE[mc] = out
    return out


# (18/10) Nether / End biomes (cubiomes ids), in cubiomes-viewer order
NETHER_BIOME_IDS = (8, 170, 171, 172, 173)    # nether_wastes, soul_sand_valley,
                                              # crimson_forest, warped_forest, basalt_deltas
END_BIOME_IDS = (9, 40, 41, 42, 43)           # the_end, small_end_islands,
                                              # end_midlands, end_highlands, end_barrens


def dim_biomes_for(mc: int, dim: int) -> List[Tuple[Optional[int], str]]:
    """The biome list of a dimension for version mc, in the picker's format
    ((None, family) header then (id, name)). dim 0 = overworld, -1 = Nether
    (several biomes from 1.16), 1 = End (islands from 1.9)."""
    mc = int(mc)
    if dim == 0:
        return overworld_biomes_for(mc)
    if dim == -1:
        ids = NETHER_BIOME_IDS if mc >= 19 else NETHER_BIOME_IDS[:1]   # MC_1_16_1
        fam = "Nether"
    else:
        ids = END_BIOME_IDS if mc >= 10 else END_BIOME_IDS[:1]          # MC_1_9
        fam = "End"
    return [(None, fam)] + [(b, biome_name_for(mc, b)) for b in ids]


def biome_dimension(biome_id: int) -> int:
    """0 overworld, -1 Nether, 1 End (of a cubiomes biome id)."""
    if biome_id in NETHER_BIOME_IDS:
        return -1
    if biome_id in END_BIOME_IDS:
        return 1
    return 0


def biome_display_name(biome_id: int, mc: Optional[int] = None) -> str:
    """Return display name for a biome id (in version mc's naming when given),
    falling back to BIOME_NAMES."""
    if mc is not None:
        return biome_name_for(mc, biome_id)
    for bid, name in OVERWORLD_BIOMES_ORDERED:
        if bid == biome_id:
            return name
    return BIOME_NAMES.get(biome_id, f"#{biome_id}")


def biome_swatch_hex(biome_id: int) -> str:
    """Return a #rrggbb hex string for the biome colour swatch."""
    rgb = BIOME_COLORS.get(biome_id, (120, 120, 120))
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


# Biomes only available from a given MC version — greyed out (unselectable) in the
# picker below that version. 26.3 == 35 in the enum (cubiomes_wrap MC_VERSIONS).
BIOME_MIN_MC = {SULFUR_CAVES: 35, DAPPLED_FOREST: 35}

