"""
biome_catalog.py — Overworld biome list in cubiomes-viewer order (pure data,
no GUI toolkit). Shared by the Tk picker (biome_picker.py) and the Qt UI.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

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


def biome_display_name(biome_id: int) -> str:
    """Return display name for a biome id, falling back to BIOME_NAMES."""
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

