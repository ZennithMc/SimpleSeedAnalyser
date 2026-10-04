"""
condition_types.py — Catalogue of the custom-session condition types (pure
data, no GUI toolkit): parameters of each type, colours, climate octave
tables. Shared by the classic Tk editor (condition_editor.py) and the Qt
workspace (qtui/).
"""
from __future__ import annotations

from typing import Optional

# Paramètres eye-count / max-Y / corrupted, copiés de l'onglet Stronghold
# (search_end_portal_seeds + ruined_portal.search_corrupted_portals). Partagés
# par les conditions "stronghold" et "first_stronghold".
_STRONGHOLD_PARAMS = {
    # (All 128 désactivé — toujours le premier stronghold.)
    # ── Eye count (0 = filtre désactivé ; 1-12 = actif) ──
    "min_eyes":    {"type": "int",  "label": "🔎 Min eyes (0 = off)", "default": 0, "min": 0, "max": 12},
    "exact_eyes":  {"type": "bool", "label": "     Exact count (not ≥)", "default": False},
    "eye_gpu":     {"type": "bool", "label": "     GPU CUDA (fast eye scan)", "default": True},
    # ── Max Y ──
    "maxy_enabled": {"type": "bool", "label": "📏 Max Y filter", "default": False},
    "min_max_y":    {"type": "int",  "label": "     Stronghold top Y ≥", "default": 60, "min": -64, "max": 320},
    # ── Corrupted end portal (1.13-1.17) ──
    "corrupt_enabled": {"type": "bool", "label": "💥 Corruptible portal (1.13-1.17)", "default": False},
    "min_frames":      {"type": "int", "label": "     Min frames under footprint", "default": 1, "min": 0, "max": 12},
}


CONDITION_TYPES = [
    # ── Iterators ──
    {
        "key": "spiral",
        "name": "Spiral iterator",
        "category": "Iterators",
        "icon": "🌀",
        "desc": "",
        "params": {
            # stored in BLOCKS like cubiomes-viewer (position = cell × step);
            # « step_unit » only chooses how the editor shows it (01/10)
            "step": {"type": "spiral_step", "label": "Step", "default": 16},
        },
    },

    # ── Structures ──
    {"key": "first_stronghold", "name": "Stronghold",  "category": "Structures", "icon": "🏰", "desc": "", "no_area": True, "params": dict(_STRONGHOLD_PARAMS)},
    {"key": "monument",         "name": "Ocean monument",    "category": "Structures", "icon": "🌊", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "mansion",          "name": "Woodland mansion",  "category": "Structures", "icon": "🏚", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "outpost",          "name": "Pillager outpost",  "category": "Structures", "icon": "🗼", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "hut",              "name": "Swamp hut",         "category": "Structures", "icon": "🧙", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "desert_pyramid",   "name": "Desert pyramid",    "category": "Structures", "icon": "🏜", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "jungle_temple",    "name": "Jungle temple",     "category": "Structures", "icon": "🌿", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "ruined_portal",    "name": "Ruined portal (overworld)", "category": "Structures", "icon": "🌀", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "shipwreck",        "name": "Shipwreck",         "category": "Structures", "icon": "⛵", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "treasure",         "name": "Buried treasure",   "category": "Structures", "icon": "💰", "desc": "", "params": {}},
    {"key": "ancient_city",     "name": "Ancient city",      "category": "Structures", "icon": "🏛", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "trial_chambers",   "name": "Trial chambers",    "category": "Structures", "icon": "⚔", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "ocean_ruin",       "name": "Ocean ruin",        "category": "Structures", "icon": "🌊", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {"key": "trail_ruins",      "name": "Trail ruins",       "category": "Structures", "icon": "🏺", "desc": "", "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    # TEMPORARILY DISABLED (01/10, Édouard): its generation (salt, rules) changes
    # with almost every small Minecraft version — right once, wrong now. Hidden
    # from the condition menu and the map; the code stays (a loaded session
    # that has one still runs it). Remove « disabled » to bring it back.
    {"key": "abandoned_camp",   "name": "Abandoned camp",    "category": "Structures", "icon": "⛺", "desc": "",
     "disabled": "Abandoned camps are temporarily disabled: their generation changes with "
                 "almost every small Minecraft version, so the positions are not reliable.",
     "params": {"use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False}}},
    {
        "key": "igloo",
        "name": "Igloo",
        "category": "Structures",
        "icon": "🏔",
        "desc": "",
        "params": {
            "variant": {"type": "choice", "label": "Basement",
                         "options": ["any", "basement", "no_basement"],
                         "labels": {"any": "Any", "basement": "With basement",
                                    "no_basement": "Without"},
                         "segmented": True,
                         "default": "any"},
            "use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False},
        },
    },
    {
        "key": "village",
        "name": "Village",
        "category": "Structures",
        "icon": "🏘",
        "desc": "",
        "params": {
            "variant": {"type": "choice", "label": "Abandoned (zombie village)",
                         "options": ["any", "abandoned", "not_abandoned"],
                         "labels": {"any": "Any", "abandoned": "Abandoned",
                                    "not_abandoned": "Not abandoned"},
                         "segmented": True,
                         "default": "any"},
            "use_gpu": {"type": "bool", "label": "GPU CUDA (48-bit only)", "default": False},
        },
    },
    # Nether/End structures (fortress, bastion, end_city) retirées : SSA ne
    # couvre pas le Nether/End.

    # ── Biomes ──
    {
        "key": "biome_filter",
        "name": "Overworld at scale",
        "category": "Biomes",
        "icon": "🌿",
        "desc": "",
        "params": {
            "biomes_incl": {"type": "biome_list", "label": "Biomes",
                             "default": []},
            "use_gpu":     {"type": "bool",
                             "label": "GPU CUDA (1.18+, full grid, required-present)",
                             "default": False},
        },
    },
    {
        "key": "biome_sample",
        "name": "Biome samples",
        "category": "Biomes",
        "icon": "🎲",
        "desc": "",
        "params": {
            "biomes_incl":    {"type": "biome_list", "label": "Biomes",
                                "default": []},
            "mode":           {"type": "exclusive_pair", "label": "Mode",
                                "default": "coverage",
                                "options": [
                                    ("min_count", "Minimum block count", "blocks_count"),
                                    ("coverage",  "Required coverage",   "coverage_pct"),
                                ]},
            "blocks_count":   {"type": "int", "label": "",
                                "default": 0, "min": 0, "max": 10_000_000_000},
            "blocks_scale":   {"type": "choice", "label": "Sampling step",
                                "options": ["1/4 (fast)", "1/1 (precise)"],
                                "default": "1/4 (fast)"},
            "coverage_pct":   {"type": "percent", "label": "",
                                "default": 50, "min": 0, "max": 100},
            "confidence_pct": {"type": "percent", "label": "Confidence",
                                "default": 95, "min": 1, "max": 99.99},
            "yield_samples":  {"type": "bool",
                                "label": "Yield individual samples (children tested around each sample)",
                                "default": False},
            "use_gpu":        {"type": "bool",
                                "label": "GPU CUDA (1.18+, noise biomes)",
                                "default": False},
        },
    },
    {
        "key": "biome_center",
        "name": "Locate biome center",
        "category": "Biomes",
        "icon": "🎯",
        "desc": "",
        "params": {
            "biome_id":  {"type": "biome_single", "label": "Biome",
                           "default": 0},
            # cubiomes keeps it as a 32-bit int: record-size biomes go far past
            # the old 10 000 cap (01/10)
            "min_size":  {"type": "int", "label": "Minimum size (chunks²)",
                           "default": 1, "min": 1, "max": 2_000_000_000},
            "tolerance": {"type": "int", "label": "Border tolerance (chunks)",
                           "default": 4, "min": 0, "max": 32},
            "count":     {"type": "int", "label": "Min centers",
                           "default": 1, "min": -1, "max": 16},
            "use_gpu":   {"type": "bool",
                           "label": "GPU CUDA (1.18+, full grid, biome present)",
                           "default": False},
        },
    },
    {
        "key": "climate_minmax",
        "name": "Locate climate extreme",
        "category": "Biomes",
        "icon": "🌡",
        "section_title": "Locate climate extreme",
        "desc": "Find the position of the min or max value of a climate noise "
                "in the search area, and (optionally) test it against a range.",
        "params": {
            "para":   {"type": "choice", "label": "Climate parameter",
                        "options": ["Temperature", "Humidity",
                                    "Continentalness", "Erosion", "Weirdness"],
                        "default": "Temperature"},
            "locate": {"type": "choice", "label": "Locate",
                        "options": [
                            ("Locate max (strongest positive)", 0),
                            ("Locate min (strongest negative)", 1),
                        ],
                        "default": 0},
            "range":  {"type": "range", "label": "Value range",
                        "default": (None, None)},
            "nmax":   {"type": "octave_choice", "label": "Generate up to octave",
                        "default": -1, "depends_on": "para"},
            "invert": {"type": "bool",  "label": "Invert test",
                        "default": False},
            "use_gpu": {"type": "bool",
                        "label": "GPU CUDA (1.18+, full octaves, non-inverted)",
                        "default": False},
        },
    },
    {
        "key": "climate_center",
        "name": "Locate climate center",
        "category": "Biomes",
        "icon": "🎯",
        "section_title": "Locate climate center",
        # coarse flood fill (01/10, idea of CoffeeAddicted): the connected
        # region of ONE climate value around the reference position
        "desc": "Measures the connected region where a climate value stays in "
                "the range, starting from the reference position (coarse flood "
                "fill, like biome size checks but for one climate noise). Passes "
                "when the region is big enough; dependent conditions are tested "
                "at its centre. Explored within the area (area 0 = ±128 cells).",
        "params": {
            "para":     {"type": "choice", "label": "Climate parameter",
                          "options": ["Temperature", "Humidity",
                                      "Continentalness", "Erosion", "Weirdness"],
                          "default": "Continentalness"},
            "range":    {"type": "range", "label": "Value range",
                          "default": (None, None)},
            "invert":   {"type": "bool",  "label": "Outside the range",
                          "default": False},
            "nmax":     {"type": "octave_choice", "label": "Generate up to octave",
                          "default": -1, "depends_on": "para"},
            "scale":    {"type": "choice", "label": "Cell size (scale)",
                          "options": [("1:4 (fine, slow)", 4), ("1:16 (chunk)", 16),
                                      ("1:64", 64), ("1:256 (coarse, fast)", 256)],
                          "default": 16},
            "min_size": {"type": "int", "label": "Minimum region size (cells)",
                          "default": 64, "min": 1, "max": 1_000_000},
            "max_size": {"type": "int", "label": "Maximum region size (cells, 0 = none)",
                          "default": 0, "min": 0, "max": 1_000_000},
        },
    },
    {
        "key": "crunchiness",
        "name": "Crunchiness",
        "category": "Biomes",
        "icon": "🌊",
        "section_title": "Climate noise crunchiness",
        "no_radial": True,
        "info_help": True,
        "desc": "",
        "params": {
            "climate": {"type": "choice", "label": "Climate parameter",
                          "options": [("Temperature",       0),
                                       ("Humidity",          1),
                                       ("Continentalness",   2),
                                       ("Erosion",           3),
                                       ("Weirdness",         5)],
                          "default": 2},
            "octaves":  {"type": "octave_grid", "label": "Octaves",
                          "default": {"0A": True, "0B": True,
                                       "1A": False, "1B": False,
                                       "2A": False, "2B": False,
                                       "3A": False, "3B": False,
                                       "4A": False, "4B": False,
                                       "5A": False, "5B": False}},
            "weighting": {"type": "choice", "label": "Weighting",
                           "options": ["Equal",
                                        "Commission (35/35/11/11/4/4)",
                                        "First octave dominant",
                                        "Custom"],
                           "default": "Equal"},
            "custom_weights": {"type": "weight_matrix",
                                "label": "Custom weights",
                                "default": {}},
            "mode":      {"type": "choice", "label": "Mode",
                           "options": [("Crunchy (score ≤ threshold)", 0),
                                        ("Bland (score ≥ threshold)",   1)],
                           "default": 0},
            "threshold": {"type": "slider_with_entry",
                           "label": "Threshold",
                           "min": 0.001, "max": 0.5, "step": 0.001,
                           "default": 0.10},
            "use_gpu":   {"type": "bool", "label": "GPU CUDA acceleration",
                           "default": False},
        },
    },
    {
        "key": "height",
        "name": "Approx Y",
        "category": "Biomes",
        "icon": "⛰",
        "desc": "Fast approximate surface Y (noise heightmap). Passes if any sampled "
                "block in the radial zone around (at x,z) has a Y inside the range.",
        "radial_only": True,    # only the radial area mode + a mandatory "at: x z"
        "requires_at": True,
        "params": {
            "y_range": {"type": "dual_height_slider", "label": "Height (Y)",
                        "default": (-64, 320), "min": -64, "max": 320},
            "invert": {"type": "bool",  "label": "Outside range",
                        "default": False},
        },
    },
    {
        "key": "precise_y",
        "name": "Exact Y",
        "category": "Biomes",
        "icon": "⛰",
        "desc": "Block-exact surface Y (full 1.18+ terrain, exact CPU engine). Samples a grid "
                "over the radial zone around (at x,z); passes if any sample's Y is in "
                "range. Heavier than Approx Y — best placed after cheaper filters.",
        "radial_only": True,
        "requires_at": True,
        "params": {
            "y_range": {"type": "dual_height_slider", "label": "Height (Y)",
                        "default": (-64, 320), "min": -64, "max": 320},
            "caves":  {"type": "bool",  "label": "Include caves",
                        "default": True},
            "invert": {"type": "bool",  "label": "Outside range",
                        "default": False},
            # (option « Use GPU » retirée : le GPU FP32 était approché et plus lent
            #  que le moteur CPU exact ; les anciennes sessions qui l'ont restent
            #  valides — le paramètre est ignoré.)
        },
    },
    {
        "key": "noise_sample",
        "name": "Climate noise samples",
        "category": "Biomes",
        "icon": "📍",
        "section_title": "Climate noise samples",
        "desc": "Sample a climate parameter across the area (Monte-Carlo) and pass "
                "if at least the required proportion of samples fall inside the "
                "value range. Equivalent to cubiomes-viewer 'Climate noise samples' "
                "(F_NOISE_SAMPLE).",
        "params": {
            "para":   {"type": "choice", "label": "Climate parameter",
                        "options": ["Temperature", "Humidity",
                                    "Continentalness", "Erosion", "Weirdness"],
                        "default": "Continentalness"},
            "nmax":   {"type": "octave_choice", "label": "Generate up to octave",
                        "default": -1, "depends_on": "para"},
            "range":  {"type": "range", "label": "If value is in range",
                        "default": (None, None)},
            "invert": {"type": "bool",  "label": "Invert range",
                        "default": False},
            "coverage_pct":   {"type": "percent", "label": "Required coverage",
                                "default": 50, "min": 0, "max": 100},
            "confidence_pct": {"type": "percent", "label": "Confidence",
                                "default": 95, "min": 1, "max": 99.99},
            "yield_samples":  {"type": "bool",
                                "label": "Yield individual samples (children tested around each sample)",
                                "default": False},
            "use_gpu":        {"type": "bool",
                                "label": "GPU CUDA (1.18+, full octaves)",
                                "default": False},
        },
    },

    # ── World ──
    {"key": "spawn", "name": "Spawn", "category": "World", "icon": "🌍", "desc": "", "params": {}},
    {"key": "slime", "name": "Slime chunk",    "category": "World", "icon": "🟢", "desc": "",
     "params": {}},

    # ── Helpers ──
    {"key": "logic_or",           "name": "OR logic gate",      "category": "Helpers", "icon": "∨", "desc": "", "params": {}},
    {"key": "logic_not",          "name": "NOT logic gate",     "category": "Helpers", "icon": "¬", "desc": "", "params": {}},
    {"key": "scale_to_nether",    "name": "Scale to Nether",    "category": "Helpers", "icon": "↘", "desc": "", "params": {}},
    {"key": "scale_to_overworld", "name": "Scale to Overworld", "category": "Helpers", "icon": "↗", "desc": "", "params": {}},
]



# Couleurs par catégorie pour le header de l'éditeur
CATEGORY_COLORS = {
    "Iterators":  "#5fb3ff",
    "Structures": "#26d96b",
    "Biomes":     "#5dd4a0",
    "World":      "#5dd4d4",
    "Helpers":    "#cccccc",
}

# Couleurs par type (correspond aux toggles structures dans le panel)
TYPE_COLORS = {
    "spiral":            "#5fb3ff",
    "stronghold":        "#9c5dff",
    "first_stronghold":  "#9c5dff",
    "village":           "#d4ad6a",
    "monument":          "#5dd4d4",
    "mansion":           "#a06d40",
    "outpost":           "#c87060",
    "hut":               "#7da846",
    "igloo":             "#aac8e0",
    "desert_pyramid":    "#e0c878",
    "jungle_temple":     "#5d9c4f",
    "ruined_portal":     "#9c5dff",
    "shipwreck":         "#a0a0a0",
    "treasure":          "#e0b040",
    "mineshaft":         "#806040",
    "ancient_city":      "#6080a0",
    "trial_chambers":    "#b06090",
    "trail_ruins":       "#a08060",
    "abandoned_camp":    "#dedede",
    "ocean_ruin":        "#4090b0",
    "desert_well":       "#e0c878",
    "fortress":          "#ff6b35",
    "bastion":           "#e05020",
    "end_city":          "#d4a0ff",
    "spawn":             "#5dd4d4",
    "slime":             "#78d850",
    "scale_to_nether":   "#ff9050",
    "scale_to_overworld":"#50b0ff",
    "logic_or":          "#cccccc",
    "logic_not":         "#cccccc",
    "climate_extreme":   "#5dd4a0",
    "climate_minmax":    "#5dd4a0",
    "climate_noise":     "#3db890",
    "noise_sample":      "#2da880",
    "biome_filter":      "#40c870",
    "biome_sample":      "#50d880",
    "biome_center":      "#30b860",
    "height":            "#a0c070",
    "precise_y":         "#80b850",
}


# Octave ranges per climate noise (Minecraft 1.18+ generator).
# Each entry says how many octave pairs are available in the dropdown.
# The menu shows iA, iB for each i in [0, count-1].
# So count=2 → 4 entries (0A, 0B, 1A, 1B), count=9 → 18 entries (0A..8B).
#
# Values match the Cubiomes Viewer UI :
#   Temperature     : 0A → 1B  (4 entries)
#   Humidity        : 0A → 1B  (4 entries)
#   Continentalness : 0A → 8B  (18 entries)
#   Erosion         : 0A → 3B  (8 entries)
#   Weirdness       : 0A → 2B  (6 entries)
OCTAVE_RANGES = {
    "Temperature":     {"count": 2},
    "Humidity":        {"count": 2},
    "Continentalness": {"count": 9},
    "Erosion":         {"count": 4},
    "Weirdness":       {"count": 3},
}


def _octave_options_for_para(para_name: str) -> list:
    """Build the list of octave choices for a given climate parameter.
    Format matches Cubiomes Viewer: 'all', '0A', '0B', '1A', '1B', ...
    where the letter alternates between the two octave sets in a double
    perlin noise. So Continentalness with 9 octaves → 0A..8B = 18 entries
    + 'all' at the start."""
    info = OCTAVE_RANGES.get(para_name)
    if info is None:
        return ["all"]
    count = info["count"]
    opts = ["all"]
    for i in range(count):
        opts.append(f"{i}A")
        opts.append(f"{i}B")
    return opts


def _get_type_info(key: str) -> Optional[dict]:
    for t in CONDITION_TYPES:
        if t["key"] == key:
            return t
    return None


def _types_in_category(cat: str) -> list:
    return [t for t in CONDITION_TYPES if t["category"] == cat]


# ─────────────────────────────────────────────────────────────────────
# Octave dropdown options — cubiomes-viewer-compatible labels
# ─────────────────────────────────────────────────────────────────────
# Native climate amplitudes from cubiomes biomenoise.c (1.18+):
_CLIMATE_AMPS = {
    "Temperature":     [1.5, 0, 1, 0, 0, 0],
    "Humidity":        [1, 1, 0, 0, 0, 0],
    "Continentalness": [1, 1, 2, 2, 2, 1, 1, 1, 1],
    "Erosion":         [1, 1, 0, 1, 1],
    "Weirdness":       [1, 2, 1, 0, 0, 0],
}
# firstOctave (negative; period_A = 4 << |firstOctave - i|)
_CLIMATE_FIRST_OCTAVE = {
    "Temperature":     -10,   # large = -12
    "Humidity":        -8,    # large = -10
    "Continentalness": -9,    # large = -11
    "Erosion":         -9,    # large = -11
    "Weirdness":       -7,
}

def _build_octave_options(climate_name: str, large: bool = False):
    """Return a list of (label, value) tuples for the octave dropdown.

    `value` is the `nmax` integer to pass to the C engine:
      -1  → use all octaves (Σ.. (all))
       N  → cap at N halves (each octave A and B counts as 1 half)
    Labels are cubiomes-viewer style: 'Σ..0A   1:512   x0.634921'
    """
    amps = _CLIMATE_AMPS.get(climate_name, [])
    if not amps:
        return [("Σ.. (all)", -1)]
    first_oct = _CLIMATE_FIRST_OCTAVE.get(climate_name, -10)
    if large:
        first_oct -= 2  # cubiomes large biomes uses larger period
    # Contribution per half: amp[i] / 2^i (matches cubiomes-viewer scale up to a constant)
    contribs = [a / (1 << i) for i, a in enumerate(amps)]
    total = sum(contribs) * 2  # both halves
    # Normalize so total matches cubiomes-viewer display (approx)
    norm = 1.0   # display unit; user-facing only
    options = [(f"Σ.. (all)  x{total*norm:.6f}", -1)]
    # Per-half period: period_A = 4 << (|first_oct| - i)
    # period_B ≈ period_A * 331/337 (slightly smaller)
    nmax_counter = 0
    for i, a in enumerate(amps):
        if a == 0:
            continue
        period_A = 4 << (abs(first_oct) - i) if (abs(first_oct) - i) >= 0 else 4
        period_B = max(1, round(period_A * 331 / 337))
        half_contrib = a / (1 << i)
        nmax_counter += 1
        options.append((f"Σ..{i}A   1:{period_A}   x{half_contrib:.6f}",
                         nmax_counter))
        nmax_counter += 1
        options.append((f"Σ..{i}B   1:{period_B}   x{half_contrib:.6f}",
                         nmax_counter))
    return options




# types hidden for now (kept in the code, see « disabled » above)
DISABLED_TYPES = {t["key"]: t["disabled"] for t in CONDITION_TYPES if t.get("disabled")}
