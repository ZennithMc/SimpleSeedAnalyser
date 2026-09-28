"""
cubiomes_session_writer.py — Sérialise les conditions SSA vers un fichier
.session compatible cubiomes-viewer 4.x.

Format hybride :
  • Les conditions reconnues par cubiomes-viewer → lignes `#Cond: <hex>`
    (320 octets little-endian identiques à ce que cubiomes écrirait).
  • Les conditions SSA-only (crunchiness, mountain_ring, cherry_ring,
    mushroom_island, climate_center) → lignes `#SSA: <json>`.
    Cubiomes les ignore (il ne lit que les #Cond:).
    SSA les relit via parse_session_file() étendu.

Toutes les conditions sont sauvegardées ; rien n'est perdu.
"""
from __future__ import annotations

import struct
import json
import datetime
from typing import List, Tuple, Dict, Optional

# ── Constantes cubiomes-viewer ────────────────────────────────────────────────
F_BIOME             = 7
F_BIOME_4_RIVER     = 8
F_BIOME_16          = 9
F_BIOME_64          = 10
F_BIOME_256         = 11
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
F_MINESHAFT         = 43
F_SPIRAL            = 44
F_BIOME_4           = 50
F_SCALE_TO_NETHER   = 51
F_SCALE_TO_OVERWORLD = 52
F_LOGIC_OR          = 53
F_FIRST_STRONGHOLD  = 55
F_CLIMATE_NOISE     = 56
F_ANCIENT_CITY      = 57
F_LOGIC_NOT         = 58
F_BIOME_CENTER      = 59
F_BIOME_CENTER_256  = 60
F_CLIMATE_MINMAX    = 61
F_HEIGHT            = 62
F_WELL              = 64
F_TRAILS            = 65
F_BIOME_SAMPLE      = 66
F_NOISE_SAMPLE      = 67
F_CHAMBERS          = 68

# SSA key → cubiomes F_* type (conditions exportables en #Cond:)
_SSA_TO_F: Dict[str, int] = {
    "biome_filter":        F_BIOME_4,
    "biome_sample":        F_BIOME_SAMPLE,
    "biome_center":        F_BIOME_CENTER,
    "climate_minmax":      F_CLIMATE_MINMAX,
    "climate_center":      F_CLIMATE_MINMAX,  # même backend C
    "height":              F_HEIGHT,
    "logic_or":            F_LOGIC_OR,
    "logic_not":           F_LOGIC_NOT,
    "spawn":               F_SPAWN,
    "stronghold":          F_STRONGHOLD,
    "first_stronghold":    F_FIRST_STRONGHOLD,
    "spiral":              F_SPIRAL,
    "scale_to_nether":     F_SCALE_TO_NETHER,
    "scale_to_overworld":  F_SCALE_TO_OVERWORLD,
    "slime":               F_SLIME,
    "village":             F_VILLAGE,
    "monument":            F_MONUMENT,
    "mansion":             F_MANSION,
    "outpost":             F_OUTPOST,
    "hut":                 F_HUT,
    "igloo":               F_IGLOO,
    "desert_pyramid":      F_DESERT,
    "jungle_temple":       F_JUNGLE,
    "ruined_portal":       F_PORTAL,
    "shipwreck":           F_SHIPWRECK,
    "treasure":            F_TREASURE,
    "ancient_city":        F_ANCIENT_CITY,
    "trial_chambers":      F_CHAMBERS,
    "trail_ruins":         F_TRAILS,
    "ocean_ruin":          F_RUINS,
    "desert_well":         F_WELL,
    "fortress":            F_FORTRESS,
    "bastion":             F_BASTION,
    "end_city":            F_ENDCITY,
    "ruined_portal_nether": 41,              # F_PORTALN
    "end_gateway":         42,               # F_GATEWAY
    "mineshaft":           F_MINESHAFT,
    "climate_noise":       F_CLIMATE_NOISE,
    "noise_sample":        F_NOISE_SAMPLE,   # before: not exported → lost in cubiomes-viewer
}

# Conditions SSA-only : sauvegardées en #SSA: <json>, ignorées par cubiomes
_SSA_ONLY = {"crunchiness", "mountain_ring", "mushroom_island", "cherry_ring"}

_SCALE_TO_F_CLEAN = {1: F_BIOME, 4: F_BIOME_4, 16: F_BIOME_16,
                     64: F_BIOME_64, 256: F_BIOME_256}
_NETHER_SCALE_TO_F = {1: 32, 4: 33, 16: 34, 64: 35, 256: 36}
_END_SCALE_TO_F    = {1: 37, 4: 38, 16: 39, 64: 40}

_PARA_FROM_NAME = {
    "Temperature": 0, "Humidity": 1, "Continentalness": 2,
    "Erosion": 3, "Weirdness": 5,
}

INT_MIN_32 = -2147483648
INT_MAX_32 =  2147483647

FLG_IN_RANGE  = 0x0020
FLG_MATCH_ANY = 0x0010
FLG_INVERT    = 0x0040

_MC_LABEL = {
    "Java 1.21.4 +":    "1.21 WD",
    "Java 1.18+":       "1.18",
    "Java 1.16 - 1.17": "1.16",
}


# ── Public API ────────────────────────────────────────────────────────────────

def write_session_file(
    path: str,
    conditions: List[dict],
    mc_str: str = "Java 1.21",
    seed_min: int = 0,
    seed_max: int = 0,
    threads: int = 1,
    results: Optional[List[int]] = None,
    progress: Optional[int] = None,
    large: bool = False,
    state: Optional[dict] = None,
) -> Tuple[int, int]:
    """
    Sérialise toutes les conditions SSA vers un fichier .session.

    * chaque condition reçoit save_idx = sa position (1-based) dans la liste,
      qu'elle soit écrite en #Cond (hex cubiomes) ou en #SSA (JSON) : les liens
      `relative` (positions SSA) restent donc justes au rechargement (avant :
      les #SSA ne comptaient pas → liens décalés) ;
    * chaque #Cond est suivie d'une ligne #SSAx avec la condition SSA complète
      (options GPU, filtres yeux / max Y du stronghold, variantes, étiquettes…),
      que cubiomes-viewer ignore et que SSA relit à l'identique ;
    * #SMin / #SMax / #Progress décrivent la plage et le point de reprise.

    Retourne (n_cond_hex, n_cond_ssa).
    """
    from cubiomes_session import MC_TO_SESSION
    lines: List[str] = []
    mc_label = MC_TO_SESSION.get(mc_str, "1.21 WD")
    now = datetime.datetime.now().strftime("%a %b %d %H:%M:%S %Y")
    if not seed_max:
        seed_max = (1 << 63) - 1
    lines.append("#Version:  4.1.2")
    lines.append(f"#Time:     {now}")
    lines.append(f"#MC:       {mc_label}")
    lines.append(f"#Large:    {1 if large else 0}")
    lines.append("#Search:   0")
    lines.append(f"#SMin:     {int(seed_min)}")
    lines.append(f"#SMax:     {int(seed_max)}")
    lines.append(f"#Progress: {int(progress if progress is not None else seed_min)}")
    lines.append(f"#Threads:  {threads}")
    lines.append("#ResStop:  0")
    lines.append("#Mode48:   0")     # cubiomes-viewer's own 48-bit option (not SSA's mode)
    lines.append("#HutQual:  0")
    lines.append("#MonArea:  13028")
    if state:
        # SSA search state (mode, stop rule, paused search…) — ignored by
        # cubiomes-viewer, restored by SSA
        lines.append("#SSAState: " + json.dumps(state, ensure_ascii=False, separators=(",", ":")))

    n_hex = 0
    n_ssa = 0
    for pos, cond in enumerate(conditions, start=1):
        c = dict(cond)
        c["save"] = pos
        for k in ("_orig_relative", "_rel_in_batch", "parent_idx"):
            c.pop(k, None)
        t = c.get("type", "")
        full = json.dumps(c, ensure_ascii=False, separators=(",", ":"))
        if t in _SSA_TO_F and t not in _SSA_ONLY:
            try:
                lines.append(f"#Cond: {_encode_condition(c, pos)}")
                lines.append(f"#SSAx: {full}")
                n_hex += 1
                continue
            except Exception:
                pass
        lines.append(f"#SSA: {full}")
        n_ssa += 1

    if results:
        for sd in results:
            lines.append(str(sd))

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    return n_hex, n_ssa


# ── Encodage d'une condition ──────────────────────────────────────────────────

def encode_conditions_to_text(conditions: List[dict], orig_save_idx=None) -> str:
    """Encode une liste de conditions SSA au format hex cubiomes-viewer.
    Une ligne hex de 320 octets par condition — le format que cubiomes met dans
    le presse-papiers et accepte au collage. Permet SSA→SSA et SSA→cubiomes.

    orig_save_idx : liste optionnelle des save_idx ORIGINAUX (position 1-based
    dans la session source) pour chaque condition copiée. Permet de préserver
    les liens 'relative' même quand on ne copie qu'une partie des conditions :
    le relative encodé pointe vers le save_idx d'origine de la cible, que le
    paste re-résoudra dans la session de destination.
    """
    lines = []
    n = len(conditions)
    for i, cond in enumerate(conditions):
        c = dict(cond)
        # save_idx = position originale si fournie, sinon index séquentiel
        save_idx = orig_save_idx[i] if (orig_save_idx and i < len(orig_save_idx)) else (i + 1)
        # relative = position 1-based de la cible dans la session source ; on la
        # garde telle quelle (ce sera re-résolue au paste). 0 = origine du monde.
        rel = c.get("relative", 0) or c.get("parent_idx", 0) or 0
        c["relative"] = rel if isinstance(rel, int) and rel > 0 else 0
        try:
            lines.append(_encode_condition(c, save_idx))
        except Exception:
            import json as _json
            try:
                lines.append("#SSA:" + _json.dumps(c, separators=(",", ":")))
            except Exception:
                pass
    return "\n".join(lines)


def _encode_condition(cond: dict, save_idx: int) -> str:
    t        = cond.get("type", "")
    params   = cond.get("params", {})
    label    = (cond.get("label", "") or "")[:27]
    x1       = int(cond.get("x1", 0))
    z1       = int(cond.get("z1", 0))
    x2       = int(cond.get("x2", 0))
    z2       = int(cond.get("z2", 0))
    relative = int(cond.get("relative", 0))
    rmax     = int(cond.get("rmax", 0))
    enabled  = cond.get("enabled", True)
    try:
        instances = int(cond["instances"]) if "instances" in cond else int(params.get("count", 1))
    except (TypeError, ValueError):
        instances = 1

    meta       = 0 if enabled else 1
    text_bytes = label.encode("utf-8", "replace")[:27].ljust(28, b"\x00")

    biome_to_find   = 0; biome_to_find_m = 0
    biome_to_excl   = 0; biome_to_excl_m = 0
    biome_id        = 0; biome_size      = 0
    tol             = 0; minmax          = 0
    para            = 0; octave          = 0
    step            = 0; version         = 4
    count           = 0; y               = 0
    flags           = 0; varflags        = 0
    varbiome        = 0; varstart        = 0
    temps           = [0] * 9
    limok           = [INT_MIN_32, INT_MAX_32] * 6
    limex           = [INT_MIN_32, INT_MAX_32] * 6
    vmin            = 0.0; vmax = 0.0
    coverage        = 0.0; confidence = 0.0

    f_type = _SSA_TO_F[t]

    if t == "biome_filter":
        scale  = int(params.get("scale", 4))
        dim    = params.get("dim", "overworld")
        # current cubiomes-viewer format (condition version 4): ONE type per
        # dimension and the scale in `step`. The legacy F_BIOME_4 / _16 … types
        # are only understood by cubiomes-viewer in version < 4 conditions: with
        # version 4 they matched nothing there (exported biome filters were dead).
        f_type = {"nether": 32, "end": 37}.get(dim, 7)     # F_BIOME_NETHER / F_BIOME_END / F_BIOME
        step = scale if scale in (1, 4, 16, 64, 256) else 4
        if dim == "end" and step > 64:
            step = 64
        y = int(params.get("y", 0))
        biome_to_find, biome_to_find_m = _list_to_biome_mask(params.get("biomes_incl", []))
        biome_to_excl, biome_to_excl_m = _list_to_biome_mask(params.get("biomes_excl", []))
        if params.get("match_any", False):
            flags |= FLG_MATCH_ANY
        if params.get("approx", False):
            flags |= 0x0001                       # FLG_APPROX

    elif t == "biome_sample":
        y = int(params.get("y", 0))
        biome_to_find, biome_to_find_m = _list_to_biome_mask(params.get("biomes_incl", []))
        biome_to_excl, biome_to_excl_m = _list_to_biome_mask(params.get("biomes_excl", []))
        count      = int(params.get("blocks_count", 0))
        if "yield_samples" in params:
            count = 1 if params.get("yield_samples") else (0 if count == 1 else count)
        coverage   = float(params.get("coverage_pct", 0.0)) / 100.0
        confidence = float(params.get("confidence_pct", 95.0)) / 100.0

    elif t == "biome_center":
        biome_id   = int(params.get("biome_id", 0))
        biome_size = int(params.get("min_size", 0))
        tol        = int(params.get("tolerance", 0))
        count      = instances
        y          = int(params.get("y", 256))

    elif t in ("climate_minmax", "climate_center"):
        para   = _PARA_FROM_NAME.get(params.get("para", "Continentalness"), 2)
        from custom_search import climate_locate
        # locate + tests only for the bounds actually set (before: the default
        # mode 6 always wrote TEST_LOWER → a spurious ">= 0" test in cubiomes)
        minmax = 0x10 if climate_locate(params) == 1 else 0x20
        range_lo, range_hi = (params.get("range") or [None, None])[:2]
        def _num(v):
            if v is None or (isinstance(v, str) and v.strip() in ("", "-inf", "+inf", "inf",
                                                                  "-Infinity", "+Infinity")):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        lo_v, hi_v = _num(range_lo), _num(range_hi)
        if lo_v is not None:
            vmin = lo_v; minmax |= 0x40
        if hi_v is not None:
            vmax = hi_v; minmax |= 0x80
        octave     = int(params.get("nmax", -1)) if params.get("nmax", -1) > 0 else 0
        if params.get("invert", False):
            flags |= FLG_INVERT
        y          = 256
        coverage   = 0.5
        confidence = 0.95

    elif t == "noise_sample":
        para   = _PARA_FROM_NAME.get(params.get("para", "Continentalness"), 2)
        lo, hi = (params.get("range") or [None, None])[:2]
        minmax = 0x10                              # as cubiomes-viewer writes it (locate min)
        flags |= FLG_IN_RANGE
        if lo is not None:
            vmin = float(lo); minmax |= 0x40      # TEST_LOWER
        if hi is not None:
            vmax = float(hi); minmax |= 0x80      # TEST_UPPER
        octave     = int(params.get("nmax", -1)) if params.get("nmax", -1) > 0 else 0
        if params.get("invert", False):
            flags |= FLG_INVERT
        y          = int(params.get("y", 256))
        count      = 1 if params.get("yield_samples") else 0
        coverage   = float(params.get("coverage_pct", 50.0)) / 100.0
        confidence = float(params.get("confidence_pct", 95.0)) / 100.0

    elif t == "climate_noise":
        ok_lo = params.get("ok_lo", [INT_MIN_32]*6)
        ok_hi = params.get("ok_hi", [INT_MAX_32]*6)
        ex_lo = params.get("ex_lo", [INT_MIN_32]*6)
        ex_hi = params.get("ex_hi", [INT_MAX_32]*6)
        for i in range(6):
            limok[i*2]   = int(ok_lo[i]) if i < len(ok_lo) else INT_MIN_32
            limok[i*2+1] = int(ok_hi[i]) if i < len(ok_hi) else INT_MAX_32
            limex[i*2]   = int(ex_lo[i]) if i < len(ex_lo) else INT_MIN_32
            limex[i*2+1] = int(ex_hi[i]) if i < len(ex_hi) else INT_MAX_32
        octave = int(params.get("nmax", -1)) if params.get("nmax", -1) > 0 else 0

    elif t == "height":
        y_min  = params.get("y_min", -1e30)
        y_max  = params.get("y_max",  1e30)
        invert = params.get("invert", False)
        limok[8] = INT_MIN_32 if y_min <= -1e29 else int(y_min)
        limok[9] = INT_MAX_32 if y_max >=  1e29 else int(y_max)
        if not invert:
            flags |= FLG_IN_RANGE

    elif t == "spiral":
        step = int(params.get("step", 0)) & 0xFFFF

    elif t == "stronghold":
        count = instances

    elif t == "slime":
        count = instances

    elif t in ("village", "monument", "mansion", "outpost", "hut", "igloo",
               "desert_pyramid", "jungle_temple", "ruined_portal", "shipwreck",
               "treasure", "ancient_city", "trial_chambers", "trail_ruins",
               "ocean_ruin", "desert_well", "fortress", "bastion",
               "end_city", "mineshaft", "ruined_portal_nether", "end_gateway"):
        count   = instances                       # 0 = exclusion
        variant = params.get("variant")
        if variant in ("abandoned", "zombie"):          varflags |= 0x0002
        elif variant == "with_ship":                    varflags |= 0x0004
        elif variant in ("basement", "with_basement"):  varflags |= 0x0020

    # ── Assemblage 320 bytes ──────────────────────────────────────────
    buf = bytearray(320)
    struct.pack_into("<hH",    buf,   0, f_type, meta)
    struct.pack_into("<iiii",  buf,   4, x1, z1, x2, z2)
    struct.pack_into("<ii",    buf,  20, save_idx, relative)
    buf[28] = 1 if cond.get("ignore_ref") else 0  # skipref
    buf[29:32] = b"\x00\x00\x00"
    buf[32:60] = text_bytes[:28].ljust(28, b"\x00")
    buf[60:72] = b"\x00" * 12
    struct.pack_into("<Q",     buf,  72, 0)       # hash
    buf[80:96] = b"\x00" * 16                    # deps
    struct.pack_into("<QQ",   buf,  96, biome_to_find, biome_to_find_m)
    struct.pack_into("<iI",   buf, 112, biome_id, biome_size)
    buf[120] = tol & 0xFF
    buf[121] = minmax & 0xFF
    buf[122] = para & 0xFF
    buf[123] = octave & 0xFF
    struct.pack_into("<HH",   buf, 124, step, version)
    struct.pack_into("<QQ",   buf, 128, biome_to_excl, biome_to_excl_m)
    struct.pack_into("<9i",   buf, 144, *temps)
    struct.pack_into("<iiIi", buf, 180, count, y, flags, rmax)
    struct.pack_into("<Hh",   buf, 196, varflags, varbiome)
    struct.pack_into("<Q",    buf, 200, varstart)
    struct.pack_into("<12i",  buf, 208, *limok)
    struct.pack_into("<12i",  buf, 256, *limex)
    struct.pack_into("<ffff", buf, 304, vmin, vmax, coverage, confidence)
    return buf.hex()


def _list_to_biome_mask(biome_ids: List[int]) -> Tuple[int, int]:
    lo, hi = 0, 0
    for bid in biome_ids:
        bid = int(bid)
        if 0 <= bid < 64:       lo |= (1 << bid)
        elif 128 <= bid < 192:  hi |= (1 << (bid - 128))
    return lo, hi
