"""
cubiomes_session.py — Parser for cubiomes-viewer .session files.

Loads a complete cubiomes-viewer session file and converts each `#Cond:` line
into a SSA condition dict compatible with custom_search.py.

The cubiomes Condition struct (320 bytes, little-endian, exact layout from
cubiomes-viewer search.h):

  offset  size  field
  ------  ----  ------------------------------
       0     2  int16   type
       2     2  uint16  meta             (bit 0 = DISABLED)
       4    16  int32   x1, z1, x2, z2
      20     4  int32   save             (reference index, 1-based)
      24     4  int32   relative         (reference to align against)
      28     1  uint8   skipref
      29     3  uint8   pad0[3]
      32    28  char    text[28]         (label)
      60    12  uint8   pad1[12]
      72     8  uint64  hash             (filter type hash for verification)
      80    16  int8    deps[16]
      96    16  uint64  biomeToFind, biomeToFindM
     112     4  int32   biomeId
     116     4  uint32  biomeSize
     120     1  uint8   tol
     121     1  uint8   minmax           (E_LOCATE_MIN/MAX, E_TEST_LOWER/UPPER)
     122     1  uint8   para             (NP_* for climate noise)
     123     1  uint8   octave           (max octaves)
     124     2  uint16  step
     126     2  uint16  version          (condition data version)
     128    16  uint64  biomeToExcl, biomeToExclM
     144    36  int32   temps[9]
     180     4  int32   count
     184     4  int32   y
     188     4  uint32  flags            (FLG_INVERT, FLG_MATCH_ANY, ...)
     192     4  int32   rmax             (>0 = strict upper radius)
     196     2  uint16  varflags
     198     2  int16   varbiome
     200     8  uint64  varstart
     208    48  int32   limok[NP_MAX=6][2]   (climate noise OK range)
     256    48  int32   limex[NP_MAX=6][2]   (climate noise EXCLUSION range)
     304     4  float   vmin
     308     4  float   vmax
     312     4  float   coverage         (0..1)
     316     4  float   confidence       (0..1)
     320  ----  end (generated_start marker)
"""
from __future__ import annotations

import struct
from typing import List, Tuple, Optional, Dict

# ── Filter IDs from cubiomes-viewer search.h ─────────────────────────
F_SELECT            = 0
F_QH_IDEAL          = 1
F_QH_CLASSIC        = 2
F_QH_NORMAL         = 3
F_QH_BARELY         = 4
F_QM_95             = 5
F_QM_90             = 6
F_BIOME             = 7
F_BIOME_4_RIVER     = 8
F_BIOME_16          = 9
F_BIOME_64          = 10
F_BIOME_256         = 11
F_BIOME_256_OTEMP   = 12
F_TEMPS             = 13
F_SLIME             = 14
F_SPAWN             = 15
F_STRONGHOLD        = 16
F_DESERT            = 17
F_JUNGLE            = 18
F_HUT               = 19
F_IGLOO             = 20
F_MONUMENT          = 21
F_VILLAGE           = 22
F_OUTPOST           = 23
F_MANSION           = 24
F_TREASURE          = 25
F_RUINS             = 26
F_SHIPWRECK         = 27
F_PORTAL            = 28
F_FORTRESS          = 29
F_BASTION           = 30
F_ENDCITY           = 31
F_BIOME_NETHER      = 32
F_BIOME_NETHER_4    = 33
F_BIOME_NETHER_16   = 34
F_BIOME_NETHER_64   = 35
F_BIOME_NETHER_256  = 36
F_BIOME_END         = 37
F_BIOME_END_4       = 38
F_BIOME_END_16      = 39
F_BIOME_END_64      = 40
F_PORTALN           = 41
F_GATEWAY           = 42
F_MINESHAFT         = 43
F_SPIRAL            = 44
F_SPIRAL_16         = 45
F_SPIRAL_64         = 46
F_SPIRAL_256        = 47
F_SPIRAL_512        = 48
F_SPIRAL_1024       = 49
F_BIOME_4           = 50
F_SCALE_TO_NETHER   = 51
F_SCALE_TO_OVERWORLD = 52
F_LOGIC_OR          = 53
F_SPIRAL_4          = 54
F_FIRST_STRONGHOLD  = 55
F_CLIMATE_NOISE     = 56
F_ANCIENT_CITY      = 57
F_LOGIC_NOT         = 58
F_BIOME_CENTER      = 59
F_BIOME_CENTER_256  = 60
F_CLIMATE_MINMAX    = 61
F_HEIGHT            = 62
F_LUA               = 63
F_WELL              = 64
F_TRAILS            = 65
F_BIOME_SAMPLE      = 66
F_NOISE_SAMPLE      = 67
F_CHAMBERS          = 68

# Mapping cubiomes F_* → SSA condition key.
# None = unsupported (we'll skip with a warning).
F_TO_SSA: Dict[int, Optional[str]] = {
    F_SELECT:           None,
    F_QH_IDEAL:         None,    # quad-hut (not in SSA)
    F_QH_CLASSIC:       None,
    F_QH_NORMAL:        None,
    F_QH_BARELY:        None,
    F_QM_95:            None,    # quad-monument
    F_QM_90:            None,
    F_BIOME:            "biome_filter",     # 1:1
    F_BIOME_4_RIVER:    "biome_filter",     # 1:4
    F_BIOME_16:         "biome_filter",
    F_BIOME_64:         "biome_filter",
    F_BIOME_256:        "biome_filter",
    F_BIOME_256_OTEMP:  "biome_filter",
    F_TEMPS:            None,               # legacy <=1.17
    F_SLIME:            "slime",
    F_SPAWN:            "spawn",
    F_STRONGHOLD:       "stronghold",
    F_DESERT:           "desert_pyramid",
    F_JUNGLE:           "jungle_temple",
    F_HUT:              "hut",
    F_IGLOO:            "igloo",
    F_MONUMENT:         "monument",
    F_VILLAGE:          "village",
    F_OUTPOST:          "outpost",
    F_MANSION:          "mansion",
    F_TREASURE:         "treasure",
    F_RUINS:            "ocean_ruin",
    F_SHIPWRECK:        "shipwreck",
    F_PORTAL:           "ruined_portal",
    F_FORTRESS:         "fortress",
    F_BASTION:          "bastion",
    F_ENDCITY:          "end_city",
    F_BIOME_NETHER:     "biome_filter",
    F_BIOME_NETHER_4:   "biome_filter",
    F_BIOME_NETHER_16:  "biome_filter",
    F_BIOME_NETHER_64:  "biome_filter",
    F_BIOME_NETHER_256: "biome_filter",
    F_BIOME_END:        "biome_filter",
    F_BIOME_END_4:      "biome_filter",
    F_BIOME_END_16:     "biome_filter",
    F_BIOME_END_64:     "biome_filter",
    F_PORTALN:          "ruined_portal_nether",   # before: skipped on import
    F_GATEWAY:          "end_gateway",
    F_MINESHAFT:        "mineshaft",
    F_SPIRAL:           "spiral",
    F_SPIRAL_16:        None,
    F_SPIRAL_64:        None,
    F_SPIRAL_256:       None,
    F_SPIRAL_512:       None,
    F_SPIRAL_1024:      None,
    F_BIOME_4:          "biome_filter",
    F_SCALE_TO_NETHER:  "scale_to_nether",
    F_SCALE_TO_OVERWORLD: "scale_to_overworld",
    F_LOGIC_OR:         "logic_or",
    F_SPIRAL_4:         None,
    F_FIRST_STRONGHOLD: "first_stronghold",
    F_CLIMATE_NOISE:    "climate_noise",
    F_ANCIENT_CITY:     "ancient_city",
    F_LOGIC_NOT:        "logic_not",
    F_BIOME_CENTER:     "biome_center",
    F_BIOME_CENTER_256: "biome_center",
    F_CLIMATE_MINMAX:   "climate_minmax",
    F_HEIGHT:           "height",
    F_LUA:              None,    # Lua scripting unsupported
    F_WELL:             "desert_well",
    F_TRAILS:           "trail_ruins",
    F_BIOME_SAMPLE:     "biome_sample",
    F_NOISE_SAMPLE:     "noise_sample",
    F_CHAMBERS:         "trial_chambers",
}

# Human-readable name for unsupported filters (used in warnings)
F_NAME = {
    F_SELECT: "Select", F_QH_IDEAL: "Quad-hut (ideal)",
    F_QH_CLASSIC: "Quad-hut (classic)", F_QH_NORMAL: "Quad-hut (normal)",
    F_QH_BARELY: "Quad-hut (barely)", F_QM_95: "Quad-monument 95%",
    F_QM_90: "Quad-monument 90%", F_TEMPS: "Temperature samples (legacy)",
    F_PORTALN: "Ruined portal (nether)", F_GATEWAY: "End gateway",
    F_SPIRAL_4: "Spiral 1:4", F_SPIRAL_16: "Spiral 1:16",
    F_SPIRAL_64: "Spiral 1:64", F_SPIRAL_256: "Spiral 1:256",
    F_SPIRAL_512: "Spiral 1:512", F_SPIRAL_1024: "Spiral 1:1024",
    F_LUA: "Lua script",
}

# Step size for F_BIOME_* (1, 4, 16, 64, 256)
_BIOME_STEP = {
    F_BIOME: 1, F_BIOME_4: 4, F_BIOME_4_RIVER: 4, F_BIOME_16: 16,
    F_BIOME_64: 64, F_BIOME_256: 256, F_BIOME_256_OTEMP: 256,
    F_BIOME_NETHER: 1, F_BIOME_NETHER_4: 4, F_BIOME_NETHER_16: 16,
    F_BIOME_NETHER_64: 64, F_BIOME_NETHER_256: 256,
    F_BIOME_END: 1, F_BIOME_END_4: 4, F_BIOME_END_16: 16,
    F_BIOME_END_64: 64,
}
# Dimension for F_BIOME_*
_BIOME_DIM = {
    F_BIOME_NETHER: "nether", F_BIOME_NETHER_4: "nether",
    F_BIOME_NETHER_16: "nether", F_BIOME_NETHER_64: "nether",
    F_BIOME_NETHER_256: "nether",
    F_BIOME_END: "end", F_BIOME_END_4: "end", F_BIOME_END_16: "end",
    F_BIOME_END_64: "end",
}

# Para to climate name
_PARA_TO_NAME = {
    0: "Temperature", 1: "Humidity", 2: "Continentalness",
    3: "Erosion", 5: "Weirdness",   # NP_DEPTH=4 is unused
}


# ── Public API ───────────────────────────────────────────────────────

def parse_session_file(path: str) -> Dict:
    """Parse a complete .session file and return a dict with keys:

      - 'mc_str': 'Java 1.21.4 +' / 'Java 1.18+' / 'Java 1.16 - 1.17'
      - 'large_biomes': bool
      - 'seed_min', 'seed_max': starting seed range
      - 'progress': last progress seed (or None)
      - 'threads': int
      - 'conditions': List[dict]    (SSA-format conditions)
      - 'unsupported': List[str]    (warnings about filters we couldn't import)
      - 'results': List[int]        (seeds found, if present)
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\r\n") for ln in f]

    out: Dict = {
        "mc_str": "Java 1.21.4 +",
        "large_biomes": False,
        "seed_min": 0,
        "seed_max": 0,
        "progress": None,
        "threads": 1,
        "conditions": [],
        "unsupported": [],
        "results": [],
    }

    import json as _json
    raw_mc = None
    # Collect entries in file order: each entry is either ("hex", hex_str) or ("ssa", dict)
    cond_entries = []
    for ln in lines:
        if ln.startswith("#"):
            # Metadata
            if ln.startswith("#MC:"):
                raw_mc = ln[len("#MC:"):].strip()
            elif ln.startswith("#Large:"):
                out["large_biomes"] = (ln[len("#Large:"):].strip() != "0")
            elif ln.startswith("#SMin:"):
                try: out["seed_min"] = int(ln[len("#SMin:"):].strip())
                except ValueError: pass
            elif ln.startswith("#SMax:"):
                try: out["seed_max"] = int(ln[len("#SMax:"):].strip())
                except ValueError: pass
            elif ln.startswith("#Progress:"):
                try: out["progress"] = int(ln[len("#Progress:"):].strip())
                except ValueError: pass
            elif ln.startswith("#SSAState:"):
                try:
                    st = _json.loads(ln[len("#SSAState:"):].strip())
                    if isinstance(st, dict):
                        out["ssa_state"] = st
                except Exception:
                    out["unsupported"].append("Could not read the saved search state")
            elif ln.startswith("#Mode48:"):
                out["mode48"] = ln[len("#Mode48:"):].strip() not in ("", "0")
            elif ln.startswith("#Threads:"):
                try: out["threads"] = int(ln[len("#Threads:"):].strip())
                except ValueError: pass
            elif ln.startswith("#Cond:"):
                cond_entries.append(("hex", ln[len("#Cond:"):].strip()))
            elif ln.startswith("#SSAx:"):
                # full SSA description of the PREVIOUS #Cond (written by SSA;
                # cubiomes-viewer ignores it) → exact round-trip of every field
                try:
                    ext = _json.loads(ln[len("#SSAx:"):].strip())
                    if cond_entries and cond_entries[-1][0] == "hex" and isinstance(ext, dict):
                        cond_entries[-1] = ("hexx", cond_entries[-1][1], ext)
                except Exception:
                    out["unsupported"].append("Could not parse an #SSAx line (hex version used)")
            elif ln.startswith("#SSA:"):
                # Conditions SSA-only sauvegardées en JSON — cubiomes les ignore
                raw_ssa = ln[len("#SSA:"):].strip()
                try:
                    ssa_cond = _json.loads(raw_ssa)
                    if isinstance(ssa_cond, dict) and "type" in ssa_cond:
                        cond_entries.append(("ssa", ssa_cond))
                    else:
                        out["unsupported"].append(f"Could not parse #SSA line: {raw_ssa[:60]}")
                except Exception:
                    out["unsupported"].append(f"Could not parse #SSA line: {raw_ssa[:60]}")
            # Other meta (HutQual, MonArea, Salt, Gen48*) we ignore for now
        else:
            # Result seed line (just an integer)
            s = ln.strip()
            if s and s.lstrip("-").isdigit():
                try: out["results"].append(int(s))
                except ValueError: pass

    # Map MC version string (before: 1.18/1.19 sessions became "Java 1.18+",
    # a name the version list does not know → searched in 1.21)
    if raw_mc:
        name, warn = mc_from_session(raw_mc)
        out["mc_str"] = name
        if warn:
            out["unsupported"].append(warn)

    # Parse conditions in file order (preserves original order for round-trip)
    _append_entries(out, cond_entries)

    # ── Remap cubiomes save_idx / relative to SSA 1-based positions ──────
    #
    # In cubiomes-viewer, every condition has a globally-unique `save_idx`
    # (e.g. 3, 10, 60, 67 …) and `relative` points to another condition's
    # `save_idx`.  SSA uses 1-based positions in the imported list instead.
    #
    # Build: save_idx → 1-based position in out["conditions"], then rewrite
    # every condition's `relative` field so SSA resolves it correctly.
    save_idx_to_pos: Dict[int, int] = {}
    for pos_0, cond in enumerate(out["conditions"]):
        s = cond.get("save")
        if isinstance(s, int) and s > 0:
            save_idx_to_pos[s] = pos_0 + 1   # 1-based

    for cond in out["conditions"]:
        old_rel = cond.get("relative", 0)
        if old_rel == 0:
            continue   # relative to world origin — keep as-is
        if old_rel in save_idx_to_pos:
            cond["relative"] = save_idx_to_pos[old_rel]
        else:
            # Points to a condition that was skipped/unsupported — detach
            cond["relative"] = 0

    return out


def parse_conditions_from_text(text: str) -> Dict:
    """Parse des conditions collées au format cubiomes-viewer (Ctrl+C).

    Chaque condition = une ligne hex de 320 octets (640 caractères hex),
    éventuellement préfixée par '#Cond:'. Lignes vides / commentaires ignorés.
    Retourne {'conditions': [...], 'unsupported': [...]} avec le remapping
    save_idx→position (1-based) comme parse_session_file, pour injection directe.
    """
    out: Dict = {"conditions": [], "unsupported": []}
    if not text:
        return out
    import json as _json
    cond_entries = []
    for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("#Cond:"):
            cond_entries.append(("hex", ln[len("#Cond:"):].strip()))
        elif ln.startswith("#SSAx:"):
            try:
                ext = _json.loads(ln[len("#SSAx:"):].strip())
                if cond_entries and cond_entries[-1][0] == "hex" and isinstance(ext, dict):
                    cond_entries[-1] = ("hexx", cond_entries[-1][1], ext)
            except Exception:
                pass
        elif ln.startswith("#SSA:"):
            try:
                ssa_cond = _json.loads(ln[len("#SSA:"):].strip())
                if isinstance(ssa_cond, dict) and "type" in ssa_cond:
                    cond_entries.append(("ssa", ssa_cond))
            except Exception:
                pass
        elif ln.startswith("#"):
            continue
        else:
            compact = ln.replace(" ", "")
            if len(compact) >= 320 and all(c in "0123456789abcdefABCDEF" for c in compact):
                cond_entries.append(("hex", compact))

    _append_entries(out, cond_entries)

    save_idx_to_pos: Dict[int, int] = {}
    for pos_0, cond in enumerate(out["conditions"]):
        s = cond.get("save")
        if isinstance(s, int) and s > 0:
            save_idx_to_pos[s] = pos_0 + 1
    for cond in out["conditions"]:
        old_rel = cond.get("relative", 0)
        # Conserver le relative brut (save_idx d'origine) pour que l'appelant
        # (le paste) puisse le re-résoudre dans la session de destination.
        cond["_orig_relative"] = old_rel
        if old_rel == 0:
            continue
        if old_rel in save_idx_to_pos:
            # Cible présente dans le lot collé → position locale.
            cond["relative"] = save_idx_to_pos[old_rel]
            cond["_rel_in_batch"] = True
        else:
            cond["relative"] = 0
    return out


def _append_entries(out, cond_entries):
    """Decode ('hex', h) / ('hexx', h, ssa_dict) / ('ssa', dict) entries."""
    for entry in cond_entries:
        kind, payload = entry[0], entry[1]
        if kind == "ssa":
            out["conditions"].append(payload)
            continue
        try:
            cond_or_err = _parse_condition_hex(payload)
        except Exception as e:
            out["unsupported"].append(f"Failed to parse condition: {e}")
            continue
        if kind == "hexx" and isinstance(cond_or_err, dict):
            ext = dict(entry[2])
            ext["save"] = cond_or_err.get("save")
            ext["relative"] = cond_or_err.get("relative", 0)
            out["conditions"].append(ext)
        elif kind == "hexx":
            out["conditions"].append(dict(entry[2]))
        elif isinstance(cond_or_err, str):
            out["unsupported"].append(cond_or_err)
        elif cond_or_err is not None:
            out["conditions"].append(cond_or_err)


# FilterInfo.grid of cubiomes-viewer (coordinate multiplier of legacy conditions)
_CV_GRID = {1: 512, 2: 512, 3: 512, 4: 512, 5: 512, 6: 512, 8: 4, 12: 256, 13: 1024, 14: 16,
            56: 4, 59: 4, 60: 256, 61: 4, 62: 4, 67: 4}


def _cv_upgrade_type(t, step):
    """Legacy type → (type, step), as Condition::versionUpgrade (VER < 4)."""
    table = {F_SPIRAL: (F_SPIRAL, 1), F_SPIRAL_4: (F_SPIRAL, 4), F_SPIRAL_16: (F_SPIRAL, 16),
             F_SPIRAL_64: (F_SPIRAL, 64), F_SPIRAL_256: (F_SPIRAL, 256),
             F_SPIRAL_512: (F_SPIRAL, 512), F_SPIRAL_1024: (F_SPIRAL, 1024),
             F_BIOME: (F_BIOME, 1), F_BIOME_4: (F_BIOME, 4), F_BIOME_16: (F_BIOME, 16),
             F_BIOME_64: (F_BIOME, 64), F_BIOME_256: (F_BIOME, 256),
             F_BIOME_NETHER: (F_BIOME_NETHER, 1), F_BIOME_NETHER_4: (F_BIOME_NETHER, 4),
             F_BIOME_NETHER_16: (F_BIOME_NETHER, 16), F_BIOME_NETHER_64: (F_BIOME_NETHER, 64),
             F_BIOME_NETHER_256: (F_BIOME_NETHER, 256), F_BIOME_END: (F_BIOME_END, 1),
             F_BIOME_END_4: (F_BIOME_END, 4), F_BIOME_END_16: (F_BIOME_END, 16),
             F_BIOME_END_64: (F_BIOME_END, 64)}
    return table.get(t, (t, step))


def _parse_condition_hex(hex_str: str):
    """Parse one 320-byte hex condition. Returns either:
      - dict (SSA condition format) on success
      - str (warning message) when filter is unsupported
      - None when garbage / completely invalid
    """
    raw = bytes.fromhex(hex_str)
    if len(raw) < 320:
        return f"Condition truncated ({len(raw)} bytes, expected 320)"
    skipref = bool(raw[28])

    # Header
    type_, meta = struct.unpack_from("<hH", raw, 0)
    x1, z1, x2, z2 = struct.unpack_from("<iiii", raw, 4)
    save_idx, relative = struct.unpack_from("<ii", raw, 20)
    text = raw[32:60].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    biomeToFind, biomeToFindM = struct.unpack_from("<QQ", raw, 96)
    biomeId, biomeSize = struct.unpack_from("<iI", raw, 112)
    tol, minmax, para, octave = raw[120:124]
    step, version = struct.unpack_from("<HH", raw, 124)
    biomeToExcl, biomeToExclM = struct.unpack_from("<QQ", raw, 128)
    count, y, flags, rmax = struct.unpack_from("<iiIi", raw, 180)
    varflags, varbiome = struct.unpack_from("<Hh", raw, 196)
    varstart = struct.unpack_from("<Q", raw, 200)[0]
    limok = list(struct.unpack_from("<12i", raw, 208))
    limex = list(struct.unpack_from("<12i", raw, 256))
    vmin, vmax, coverage, confidence = struct.unpack_from("<ffff", raw, 304)

    # cubiomes-viewer Condition::versionUpgrade (search.cpp), applied as it
    # does when it loads a session: conditions saved by older versions store
    # legacy types (F_BIOME_4, F_SPIRAL_64 …) and coordinates in grid units.
    if version == 0:                         # VER_LEGACY
        ocean_to_find = struct.unpack_from("<Q", raw, 112)[0]
        biomeToFind &= ~((1 << 0) | (1 << 24))          # ocean, deep_ocean
        biomeToFind |= ocean_to_find
        skipref = False; text = ""
        biomeId = biomeSize = tol = minmax = para = octave = step = 0
    if version < 2:                          # VER_2_4_0
        varflags = varbiome = varstart = 0
    if version < 3 and type_ == F_CLIMATE_MINMAX:        # VER_3_4_0
        if minmax == 0:   minmax = 0x10 | 0x80; vmin, vmax = vmax, vmin
        elif minmax == 1: minmax = 0x20 | 0x40
        elif minmax == 2: minmax = 0x10 | 0x40
        elif minmax == 3: minmax = 0x20 | 0x80; vmin, vmax = vmax, vmin
    if version < 4:                          # VER_4_0_0
        type_, step = _cv_upgrade_type(type_, step)
        mult = step if step else _CV_GRID.get(type_, 0)
        if mult > 1:
            x1 = x1 * mult; z1 = z1 * mult
            x2 = (x2 + 1) * mult - 1; z2 = (z2 + 1) * mult - 1

    # Map to SSA condition key
    ssa_key = F_TO_SSA.get(type_)
    if ssa_key is None:
        human = F_NAME.get(type_, f"unknown filter #{type_}")
        label = text or human
        return f"Skipped '{label}': filter '{human}' not supported in SSA"

    # Build the common SSA condition shell
    cond: Dict = {
        "type":     ssa_key,
        "label":    text or "",   # empty → UI falls back to the type's display
                                   # name (e.g. "Scale to Overworld") instead of
                                   # the useless "(imported)" placeholder.
        "params":   {},
        "x1": int(x1), "z1": int(z1),
        "x2": int(x2), "z2": int(z2),
        "relative": int(relative),
        "enabled":  ((meta & 0x0001) == 0),   # DISABLED bit
        "ignore_ref": skipref,
        # instances: count of the structure-like filters; 0 = EXCLUSION
        # (before: forced to >= 1, which turned "no village here" into
        # "a village here")
        "instances": int(count) if (ssa_key in _COUNTED and count >= 0) else 1,
        "area_mode": _derive_area_mode(x1, z1, x2, z2, rmax),
        # SSA convention: rmax = radial + 1 (before: radial = rmax → every
        # edit of an imported radial condition grew it by one block)
        "radial":   _derive_radial(x1, z1, x2, z2, rmax),
        "square_side": _derive_square_side(x1, z1, x2, z2),
        "save": int(save_idx),
        "rmax": int(rmax),   # cubiomes "strict upper radius" — also tells
                              # the engine to clip results to a circle.
    }

    invert = bool(flags & 0x0040)   # FLG_INVERT

    # ── Per-type parameter mapping ─────────────────────────────────
    if ssa_key == "biome_filter":
        # current cubiomes-viewer: type F_BIOME / F_BIOME_NETHER / F_BIOME_END
        # and the scale in `step` (1, 4, 16, 64, 256). Before, `step` was
        # ignored → every 1:4 biome filter was imported at 1:1 (16× the work,
        # and a different test).
        _scale = int(step) if (type_ in (F_BIOME, F_BIOME_NETHER, F_BIOME_END)
                               and step in (1, 4, 16, 64, 256)) else _BIOME_STEP.get(type_, 1)
        cond["params"] = {
            "biomes_incl": _biome_mask_to_list(biomeToFind, biomeToFindM),
            "biomes_excl": _biome_mask_to_list(biomeToExcl, biomeToExclM),
            "scale": _scale,
            "dim":   _BIOME_DIM.get(type_, "overworld"),
            "y":     int(y),
            "match_any": bool(flags & 0x0010),  # FLG_MATCH_ANY
            "approx":    bool(flags & 0x0001),  # FLG_APPROX (was lost)
        }

    elif ssa_key == "biome_sample":
        cond["params"] = {
            "biomes_incl":     _biome_mask_to_list(biomeToFind, biomeToFindM),
            "biomes_excl":     _biome_mask_to_list(biomeToExcl, biomeToExclM),
            "mode":            "coverage" if coverage > 0 else "min_count",
            "coverage_pct":    coverage * 100.0,
            "confidence_pct":  confidence * 100.0,
            "blocks_count":    int(count),
            "blocks_scale":    "1/4 (fast)",
            # cubiomes-viewer « Yield individual samples » (count == 1): the
            # children are tested around EACH sampled position (OR), not
            # around the centre of the samples
            "yield_samples":   int(count) == 1,
            "y":               int(y),
        }

    elif ssa_key == "biome_center":
        cond["params"] = {
            "biome_id":  int(biomeId),
            "min_size":  int(biomeSize),
            "tolerance": int(tol),
            "count":     max(1, int(count) if count > 0 else 1),
            "y":         int(y),      # cubiomes-viewer samples at this height (was lost → 256)
        }

    elif ssa_key == "climate_minmax":
        # Range: empty=±∞; vmin/vmax are floats in cubiomes
        lo = None if (minmax & 0x40) == 0 else _float_to_entry(vmin)
        hi = None if (minmax & 0x80) == 0 else _float_to_entry(vmax)
        # mode bitmask in SSA matches cubiomes' minmax >>4 layout:
        # cubiomes:  E_LOCATE_MIN=0x10, E_LOCATE_MAX=0x20, E_TEST_LOWER=0x40, E_TEST_UPPER=0x80
        # SSA mode:  bit 0=loc_min, bit 1=loc_max, bit 2=test_lower, bit 3=test_upper
        ssa_mode = 0
        if minmax & 0x10: ssa_mode |= 1   # loc_min
        if minmax & 0x20: ssa_mode |= 2   # loc_max
        if minmax & 0x40: ssa_mode |= 4   # test_lower
        if minmax & 0x80: ssa_mode |= 8   # test_upper
        cond["params"] = {
            "para":   _PARA_TO_NAME.get(para, "Temperature"),
            "mode":   ssa_mode if ssa_mode else 2,  # default LOCATE_MAX
            "locate": 1 if (minmax & 0x10) else 0,  # editor field (0 max, 1 min)
            "range":  (lo, hi),
            "nmax":   int(octave) if octave > 0 else -1,
            "invert": invert,
        }

    elif ssa_key == "climate_noise":
        # The 6×2 limok / limex arrays are the OK / EXCLUSION ranges per climate.
        cond["params"] = {
            "ok_lo": [limok[i*2]   for i in range(6)],
            "ok_hi": [limok[i*2+1] for i in range(6)],
            "ex_lo": [limex[i*2]   for i in range(6)],
            "ex_hi": [limex[i*2+1] for i in range(6)],
            "nmax":  int(octave) if octave > 0 else -1,
        }

    elif ssa_key == "noise_sample":
        # F_NOISE_SAMPLE: Monte-Carlo sur un paramètre climatique. CV exige
        # qu'une FRACTION (coverage) des points de la zone satisfasse les
        # bornes, avec une confiance (confidence) — PAS juste "∃ un point".
        # Lire coverage/confidence (déjà décodés en haut depuis l'offset 304).
        # minmax bits: 0x40 = TEST_LOWER (vmin actif), 0x80 = TEST_UPPER (vmax actif)
        _vmin = float(vmin) if (minmax & 0x40) and vmin > -1e29 else None
        _vmax = float(vmax) if (minmax & 0x80) and vmax <  1e29 else None
        cond["params"] = {
            "para":   _PARA_TO_NAME.get(para, "Continentalness"),
            "range":  (_vmin, _vmax),
            "nmax":   int(octave) if octave > 0 else -1,
            "invert": invert,
            "coverage_pct":   float(coverage) * 100.0,
            "confidence_pct": float(confidence) * 100.0,
            "yield_samples":  int(count) == 1,   # « Yield individual samples »
            "bx":     int(x1),
            "bz":     int(z1),
        }

    elif ssa_key == "height":
        # CV stores height bounds in limok[NP_DEPTH] = limok[4], not vmin/vmax.
        # limok layout: 6 climates × 2 bounds = 12 int32s starting at offset 208.
        # Index NP_DEPTH=4 → entries 8 and 9 in our flat `limok` array.
        # FLG_IN_RANGE (0x20) means "y in [ymin, ymax]"; otherwise inverted.
        ymin_raw = limok[8]   # limok[NP_DEPTH][0]
        ymax_raw = limok[9]   # limok[NP_DEPTH][1]
        # INT_MIN / INT_MAX mean "no bound" → translate to ±1e30 which the
        # C engine interprets as no constraint.
        INT_MIN_32 = -2147483648
        INT_MAX_32 =  2147483647
        y_min_eff = float(ymin_raw) if ymin_raw > INT_MIN_32 else -1e30
        y_max_eff = float(ymax_raw) if ymax_raw < INT_MAX_32 else  1e30
        in_range = bool(flags & 0x0020)   # FLG_IN_RANGE
        cond["params"] = {
            "y_min":  y_min_eff,
            "y_max":  y_max_eff,
            # CV: "in_range = y∈[ymin,ymax]" if FLG_IN_RANGE else "y≤ymin OR y≥ymax"
            # SSA: in_range = y∈[ymin,ymax] always, then invert flips
            "invert": not in_range,
        }

    elif ssa_key == "logic_or":
        cond["params"] = {}

    elif ssa_key == "logic_not":
        cond["params"] = {}

    elif ssa_key == "spawn":
        cond["params"] = {}

    elif ssa_key == "stronghold":
        cond["params"] = {"count": max(1, int(count) if count > 0 else 1)}

    elif ssa_key == "first_stronghold":
        cond["params"] = {}

    elif ssa_key == "spiral":
        cond["params"] = {"step": int(step) if step > 0 else 0}

    elif ssa_key in ("scale_to_nether", "scale_to_overworld"):
        cond["params"] = {}

    elif ssa_key == "slime":
        cond["params"] = {"count": max(1, int(count) if count > 0 else 1)}

    elif ssa_key in ("village", "monument", "mansion", "outpost", "hut",
                      "igloo", "desert_pyramid", "jungle_temple",
                      "ruined_portal", "shipwreck", "treasure",
                      "ancient_city", "trial_chambers", "trail_ruins",
                      "ocean_ruin", "desert_well",
                      "fortress", "bastion", "end_city", "mineshaft",
                      "ruined_portal_nether", "end_gateway"):
        # Plain structure count
        cond["params"] = {
            "count":   max(1, int(count) if count > 0 else 1),
            "variant": _decode_variant(ssa_key, varflags),
        }

    else:
        # Fallback: empty params, count
        cond["params"] = {"count": int(count) if count > 0 else 1}

    return cond


# ── Helpers ─────────────────────────────────────────────────────────

def _derive_area_mode(x1, z1, x2, z2, rmax=0) -> int:
    """Return SSA area_mode (0=radial, 1=square, 2=custom).

    Cubiomes encodes the search shape via (x1,z1,x2,z2) + rmax:
      - rmax>0 with empty box → centered radial search (mode 0)
      - x1==-x2 and z1==-z2 and width==height → centered square (mode 1)
      - everything else → custom box (mode 2)
    """
    if rmax > 0 and x1 == 0 and z1 == 0 and x2 == 0 and z2 == 0:
        return 0   # radial
    if x1 == 0 and z1 == 0 and x2 == 0 and z2 == 0:
        return 1   # centred square of size 0 (point)
    if x1 == -x2 and z1 == -z2 and (x2 - x1) == (z2 - z1):
        return 1   # centred square
    return 2       # custom box


def _derive_radial(x1, z1, x2, z2, rmax=0) -> int:
    if rmax > 0 and x1 == 0 and z1 == 0 and x2 == 0 and z2 == 0:
        return max(0, int(rmax) - 1)
    return 0


def _derive_square_side(x1, z1, x2, z2) -> int:
    if x1 == -x2 and z1 == -z2 and (x2 - x1) == (z2 - z1):
        return int(x2 - x1)
    return 0


def _float_to_entry(v) -> Optional[str]:
    """Convert a float bound into an SSA range entry string. ±inf → None."""
    if v == float("inf") or v >= 1e30:
        return None
    if v == float("-inf") or v <= -1e30:
        return None
    # Cubiomes stores 0.0 when no bound is set; the test bit (minmax & 0x40/0x80)
    # tells us if it's an actual value. We don't filter 0 here because the
    # caller has already checked the bit.
    return f"{v:g}"


def _biome_mask_to_list(b_lo: int, b_hi: int) -> List[int]:
    """Convert a 128-bit biome bitfield into a list of biome IDs.

    Cubiomes packs biome IDs 0..63 in `biomeToFind` (bits 0..63) and biome IDs
    128..191 in `biomeToFindM` (bits 0..63 mapping to IDs 128..191).
    The range 64..127 is unused — modded variants of vanilla biomes are at +128.
    """
    result = []
    for i in range(64):
        if (b_lo >> i) & 1:
            result.append(i)
    for i in range(64):
        if (b_hi >> i) & 1:
            result.append(128 + i)
    return result


def _decode_variant(ssa_key: str, varflags: int) -> Optional[str]:
    """Decode the variant flags into a string label (or None for default)."""
    if not varflags:
        return None
    # vocabulary of the engine / editor (before: "zombie" / "with_basement",
    # which the engine did not know → the variant was silently ignored)
    if ssa_key == "village" and (varflags & 0x0002):
        return "abandoned"
    if ssa_key == "end_city" and (varflags & 0x0004):
        return "with_ship"
    if ssa_key == "igloo" and (varflags & 0x0020):
        return "basement"
    return None


# filters whose `count` is a number of instances (0 = exclusion)
_COUNTED = {"village", "monument", "mansion", "outpost", "hut", "igloo",
            "desert_pyramid", "jungle_temple", "ruined_portal", "shipwreck",
            "treasure", "ancient_city", "trial_chambers", "trail_ruins",
            "ocean_ruin", "desert_well", "fortress", "bastion", "end_city",
            "ruined_portal_nether", "end_gateway",
            "mineshaft", "slime", "stronghold", "biome_center",
            "ruined_portal_nether", "end_gateway"}

# SSA version name  <->  cubiomes-viewer "#MC:" string
MC_TO_SESSION = {"Java 1.16": "1.16", "Java 1.16.1": "1.16.1", "Java 1.17": "1.17",
                 "Java 1.18": "1.18", "Java 1.19": "1.19", "Java 1.19.2": "1.19.2",
                 "Java 1.20": "1.20", "Java 1.21": "1.21 WD", "Java 1.21.4 +": "1.21 WD",
                 "Java 1.21.1": "1.21.1", "Java 1.21.3": "1.21.3",
                 "Java 26.1": "26.1", "Java 26.2": "26.2", "Java 26.3": "26.3"}

# cubiomes-viewer « #MC: » string → SSA version (cubiomes str2mc). Exact
# versions first: the biome tables differ between 1.19.2 / 1.19.4, 1.21.1 /
# 1.21.3 / 1.21.4 (pale garden), 26.1 / 26.2 / 26.3.
_SESSION_TO_MC = {
    "1.16.1": "Java 1.16.1", "1.16": "Java 1.16", "1.16.5": "Java 1.16",
    "1.17": "Java 1.17", "1.17.1": "Java 1.17",
    "1.18": "Java 1.18", "1.18.2": "Java 1.18",
    "1.19.2": "Java 1.19.2", "1.19": "Java 1.19", "1.19.4": "Java 1.19",
    "1.20": "Java 1.20", "1.20.6": "Java 1.20",
    "1.21.1": "Java 1.21.1", "1.21.2": "Java 1.21.3", "1.21.3": "Java 1.21.3",
    "1.21 wd": "Java 1.21", "1.21.4": "Java 1.21", "1.21": "Java 1.21",
    "1.21.5": "Java 1.21", "1.21.6": "Java 1.21", "1.21.9": "Java 1.21", "1.21.11": "Java 1.21",
    "26.1": "Java 26.1", "26.2": "Java 26.2", "26.3": "Java 26.3",
}


def mc_from_session(raw: str):
    """cubiomes-viewer version string → (SSA version name, warning or None)."""
    v = (raw or "").strip().lower()
    if v in _SESSION_TO_MC:
        return _SESSION_TO_MC[v], None
    for pre, name in (("26", "Java 26.3"), ("1.21", "Java 1.21"), ("1.20", "Java 1.20"),
                      ("1.19", "Java 1.19"), ("1.18", "Java 1.18"), ("1.17", "Java 1.17"),
                      ("1.16", "Java 1.16")):
        if v.startswith(pre):
            return name, None
    return "Java 1.21", (f"Minecraft version '{raw}' is not supported by SSA — "
                         f"the session was loaded as Java 1.21")
