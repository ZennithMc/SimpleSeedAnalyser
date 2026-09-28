"""
colormaps.py — Gestion des palettes de couleurs pour les biomes.

3 palettes prédéfinies :
  - Classic                       : palette historique (par défaut de cubiomes_wrap)
  - Truecolors                    : couleurs naturelles (sélectionnée au démarrage)
  - Cooler & Brighter (Zennith)   : couleurs vives et contrastées

Pour ajouter une palette :
  1. Dépose le fichier .colormap dans le dossier colormaps/
  2. Ajoute une entrée dans PALETTES ci-dessous (label + nom de fichier).

Format .colormap (Cubiomes Viewer / Amidst) :
    biome_name R G B
"""
from __future__ import annotations

import os
from typing import Dict, Tuple, List, Optional

from cubiomes_wrap import BIOME_COLORS as CLASSIC_COLORS

# ═════════════════════════════════════════════════════════════════════════════
# Mapping biome_name (snake_case) → biome ID
# ═════════════════════════════════════════════════════════════════════════════
BIOME_NAME_TO_ID: Dict[str, int] = {
    "ocean": 0, "plains": 1, "desert": 2,
    "windswept_hills": 3, "mountains": 3, "extreme_hills": 3,
    "forest": 4, "taiga": 5, "swamp": 6, "river": 7,
    "nether_wastes": 8, "hell": 8, "nether": 8,
    "the_end": 9, "sky": 9,
    "frozen_ocean": 10, "frozen_river": 11,
    "snowy_plains": 12, "snowy_tundra": 12, "ice_plains": 12,
    "snowy_mountains": 13, "ice_mountains": 13,
    "mushroom_fields": 14, "mushroom_island": 14,
    "mushroom_field_shore": 15, "mushroom_island_shore": 15,
    "beach": 16, "desert_hills": 17,
    "wooded_hills": 18, "forest_hills": 18,
    "taiga_hills": 19, "mountain_edge": 20,
    "jungle": 21, "jungle_hills": 22,
    "sparse_jungle": 23, "jungle_edge": 23,
    "deep_ocean": 24,
    "stony_shore": 25, "stone_beach": 25, "stone_shore": 25,
    "snowy_beach": 26, "cold_beach": 26,
    "birch_forest": 27, "birch_forest_hills": 28,
    "dark_forest": 29, "roofed_forest": 29,
    "snowy_taiga": 30, "cold_taiga": 30,
    "snowy_taiga_hills": 31, "cold_taiga_hills": 31,
    "old_growth_pine_taiga": 32, "giant_tree_taiga": 32, "mega_taiga": 32,
    "giant_tree_taiga_hills": 33, "mega_taiga_hills": 33,
    "windswept_forest": 34, "extreme_hills_plus": 34, "wooded_mountains": 34,
    "savanna": 35, "savanna_plateau": 36,
    "badlands": 37, "mesa": 37,
    "wooded_badlands": 38, "mesa_plateau_forest": 38, "wooded_badlands_plateau": 38,
    "badlands_plateau": 39, "mesa_plateau": 39,
    "small_end_islands": 40, "end_midlands": 41,
    "end_highlands": 42, "end_barrens": 43,
    "warm_ocean": 44, "lukewarm_ocean": 45, "cold_ocean": 46,
    "deep_warm_ocean": 47, "deep_lukewarm_ocean": 48,
    "deep_cold_ocean": 49, "deep_frozen_ocean": 50,
    "the_void": 127,
    "sunflower_plains": 129,
    "desert_lakes": 130, "desert_mutated": 130,
    "windswept_gravelly_hills": 131, "gravelly_mountains": 131,
    "modified_gravelly_mountains": 162, "gravelly_mountains_plus": 162,
    "flower_forest": 132,
    "taiga_mountains": 133,
    "swamp_hills": 134, "swampland_mountains": 134,
    "ice_spikes": 140,
    "modified_jungle": 149, "jungle_mutated": 149,
    "modified_jungle_edge": 151, "jungle_edge_mutated": 151,
    "old_growth_birch_forest": 155, "tall_birch_forest": 155,
    "tall_birch_hills": 156, "birch_forest_hills_mutated": 156,
    "dark_forest_hills": 157, "roofed_forest_mutated": 157,
    "snowy_taiga_mountains": 158, "cold_taiga_mutated": 158,
    "old_growth_spruce_taiga": 160, "redwood_taiga_mutated": 160,
    "giant_spruce_taiga_hills": 161, "redwood_taiga_hills_mutated": 161,
    "windswept_savanna": 163, "savanna_mutated": 163,
    "shattered_savanna_plateau": 164, "savanna_plateau_mutated": 164,
    "eroded_badlands": 165, "mesa_bryce": 165,
    "modified_wooded_badlands_plateau": 166,
    "modified_badlands_plateau": 167,
    "bamboo_jungle": 168, "bamboo_jungle_hills": 169,
    "soul_sand_valley": 170, "crimson_forest": 171,
    "warped_forest": 172, "basalt_deltas": 173,
    "dripstone_caves": 174, "lush_caves": 175,
    "meadow": 177, "grove": 178,
    "snowy_slopes": 179, "jagged_peaks": 180,
    "frozen_peaks": 181, "stony_peaks": 182,
    "deep_dark": 183, "mangrove_swamp": 184,
    "cherry_grove": 185, "pale_garden": 186,
}


# ═════════════════════════════════════════════════════════════════════════════
# PALETTES — liste fixe (label affiché, fichier ou None pour intégré)
# ═════════════════════════════════════════════════════════════════════════════
PALETTES: List[Tuple[str, Optional[str]]] = [
    ("Classic",                          None),
    ("Cooler & Brighter (Zennith)",      "CoolerAndBrighter__Zennith_.colormap"),
    ("True Colors (ShowMe7)",            "truecolors.colormap"),
    ("AltMeadow (ShowMe7)",             "altmeadow.colormap"),
]

DEFAULT_PALETTE = "True Colors (ShowMe7)"

_COLORMAPS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "colormaps")


def _parse_colormap_file(path: str) -> Dict[int, Tuple[int, int, int]]:
    colors: Dict[int, Tuple[int, int, int]] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                name = parts[0].lower()
                try:
                    r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
                except ValueError:
                    continue
                bid = BIOME_NAME_TO_ID.get(name)
                if bid is not None:
                    colors[bid] = (r & 0xFF, g & 0xFF, b & 0xFF)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[colormaps] error parsing {path}: {e}")
    return colors


def get_available_colormaps() -> List[str]:
    """Retourne les labels disponibles (fichier existant + Classic toujours)."""
    out: List[str] = []
    for label, fname in PALETTES:
        if fname is None:
            out.append(label)
        else:
            path = os.path.join(_COLORMAPS_DIR, fname)
            if os.path.isfile(path):
                out.append(label)
            else:
                print(f"[colormaps] missing: {path}")
    return out


def load_colormap(label: str) -> Dict[int, Tuple[int, int, int]]:
    fname = None
    found = False
    for lbl, fn in PALETTES:
        if lbl == label:
            fname = fn
            found = True
            break
    if not found or fname is None:
        return dict(CLASSIC_COLORS)
    path = os.path.join(_COLORMAPS_DIR, fname)
    if not os.path.isfile(path):
        return dict(CLASSIC_COLORS)
    out = dict(CLASSIC_COLORS)
    out.update(_parse_colormap_file(path))
    return out
