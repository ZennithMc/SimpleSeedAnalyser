"""noise_palettes.py — noise colour palettes (LUTs), value ranges and 1.18+
regime labels. Shared by the classic (Tk) and new (Qt) interfaces; pure numpy,
no GUI dependency. (Extracted verbatim from gui.py.)"""
import numpy as np

# ═══════════════════════════════════════════════════════════════════════════════
# NOISE COLOR PALETTES — LUT numpy (256 entrées → RGB uint8)
# ═══════════════════════════════════════════════════════════════════════════════
def _lerp_lut(stops) -> "np.ndarray":
    import numpy as _np
    lut = _np.zeros((256, 3), dtype=_np.uint8)
    if not stops:
        g = _np.arange(256, dtype=_np.uint8)
        return _np.stack([g,g,g], axis=1)
    stops = sorted(stops, key=lambda s: s[0])
    for i in range(256):
        t = i / 255.0
        # Extrapolation gauche
        if t <= stops[0][0]:
            lut[i] = stops[0][1:4]
            continue
        # Extrapolation droite
        if t >= stops[-1][0]:
            lut[i] = stops[-1][1:4]
            continue
        for k in range(len(stops) - 1):
            t0, r0, g0, b0 = stops[k]
            t1, r1, g1, b1 = stops[k + 1]
            if t0 <= t <= t1:
                f = (t - t0) / max(t1 - t0, 1e-9)
                lut[i] = [int(r0+f*(r1-r0)), int(g0+f*(g1-g0)), int(b0+f*(b1-b0))]
                break
    return lut


# Stops par défaut pour chaque noisemap — choisis pour refléter la sémantique
# physique de chaque paramètre climatique Minecraft 1.18+.
NOISE_DEFAULT_STOPS: dict = {
    # Temperature: polar blue → temperate green → warm orange → burning red
    "Temperature": [
        (0.00,  20,  60, 200),   # très froid → bleu glacial
        (0.20,  80, 160, 240),   # froid → bleu clair
        (0.42, 120, 210, 120),   # tempéré → vert doux
        (0.60, 220, 200,  60),   # chaud → jaune
        (0.80, 240, 120,  20),   # très chaud → orange
        (1.00, 200,  20,  20),   # brûlant → rouge
    ],
    # Humidity: arid orange → humid green → saturated blue
    "Humidity": [
        (0.00, 230, 180,  60),   # aride → sable doré
        (0.22, 180, 200,  80),   # sec → vert pâle
        (0.45,  60, 180,  60),   # modéré → vert vif
        (0.68,  20, 140, 120),   # humide → vert-bleu
        (0.85,  20,  80, 180),   # très humide → bleu
        (1.00,  10,  20, 120),   # saturé → bleu foncé
    ],
    # Continentalness: extreme deep ocean (-11500) → ocean blue → green → peak grey
    "Continentalness": [
        (0.00, 120,   0, 200),   # -11500 → violet profond (bord absolu)
        (0.05,  60,  10, 180),   # -11000 → violet-bleu (limite océan profond)
        (0.18,  10,  50, 200),   # océan → bleu
        (0.32,  50, 130, 220),   # côte → bleu clair
        (0.46, 200, 210, 120),   # plaines → vert pâle
        (0.62, 100, 160,  60),   # intérieur → vert
        (0.80, 160, 140, 110),   # plateau → beige
        (1.00, 200, 200, 200),   # peaks → gris
    ],
    # Erosion: hard granite (grey) → loose soil (brown) → flat terrain (bright green)
    "Erosion": [
        (0.00, 180, 180, 180),   # très peu érodé → gris pierre
        (0.20, 140, 120,  80),   # peu érodé → beige-brun
        (0.42, 180, 150,  60),   # moyen → jaune-brun
        (0.62, 120, 190,  80),   # érodé → vert herbe
        (0.82,  60, 220, 140),   # très érodé → vert vif
        (1.00,  30, 180, 220),   # maximum → bleu-vert (plat/marécage)
    ],
    # Weirdness: weird spectrum — violet-red-green-blue symmetric
    "Weirdness": [
        (0.00, 160,   0, 200),   # -1.0 → violet (weirdness maximal négatif)
        (0.20, 220,  40,  80),   # → rouge
        (0.40, 240, 200,  40),   # 0 gauche → jaune
        (0.50, 255, 255, 255),   # 0 exact → blanc (neutre)
        (0.60,  80, 240, 120),   # 0 droit → vert
        (0.80,  40, 120, 240),   # → bleu
        (1.00,  80,   0, 180),   # +1.0 → indigo
    ],
    # Height : palette MC-classique (océan bleu → sable → herbe → mountain
    # marron → peak gris/blanc).  Pos 0.0 = Y_min (-30), Pos 1.0 = Y_max (+320).
    # Sea level (Y=63) = pos ~0.27 → transition océan/plage.
    "Height": [
        (0.00,   0,   0,  80),   # -30 (très profond) → bleu nuit
        (0.18,  20,  60, 180),   # sea floor → bleu profond
        (0.27,  80, 150, 220),   # sea level (Y=63) → bleu clair
        (0.32, 230, 220, 160),   # plage Y=80 → sable
        (0.42,  90, 180,  90),   # plaine Y=110 → vert herbe
        (0.55, 100, 130,  60),   # collines Y=160 → vert-marron
        (0.70, 140, 110,  90),   # mountain Y=215 → marron
        (0.85, 200, 200, 200),   # peaks Y=270 → gris
        (1.00, 255, 255, 255),   # +320 (build limit) → blanc
    ],
}

# Fallback simple — utilisé quand l'utilisateur "Reset to defaults" ou crée
# une nouvelle palette. Plus neutre que les colors sémantiques pour permettre
# de partir d'une base vierge.
NOISE_FALLBACK_STOPS: list = [
    (0.0, 220, 30, 30),    # rouge à pos 0.0 (noise min)
    (1.0, 30, 90, 220),    # bleu à pos 1.0 (noise max)
]

# ─────────────────────────────────────────────────────────────────────────────
# VRAIES PLAGES de noise pour Minecraft Java 1.18+ (officielles, wiki MC)
# Pos 0.0 dans l'éditeur = noise_min, Pos 1.0 = noise_max
# Utilisé pour afficher la VRAIE valeur de noise sous le curseur / les stops.
# ─────────────────────────────────────────────────────────────────────────────
NOISE_VALUE_RANGES: dict = {
    # Climate noises (-1.0 à +1.0 typiquement, jusqu'à ±2 aux extrêmes)
    "Temperature":      (-1.0, 1.0),
    "Humidity":         (-1.0, 1.0),
    "Erosion":          (-1.0, 1.0),
    "Weirdness":        (-1.0, 1.0),
    # Continentalness va de ~-1.2 à +1.0 (le -1.2 = mushroom fields)
    "Continentalness":  (-1.2, 1.0),
    # Height : Y en blocs (Overworld 1.18+ : -30 fond océan → +320 peaks).
    # Sea level Y=63 = milieu de palette pour repère visuel.
    "Height":           (-64.0, 320.0),
}

# Régimes biomatiques officiels (noise breakpoints 1.18+) — affichés à
# proximité du curseur/stops pour éviter le ressenti "subjectif".
# Ce sont les SEUILS officiels de Minecraft 1.18+ utilisés par cubiomes.
NOISE_REGIME_LABELS: dict = {
    "Continentalness": [
        (-1.20, "Mushroom"),
        (-1.05, "Deep Ocean"),
        (-0.455, "Ocean"),
        (-0.19, "Coast"),
        (-0.11, "Near-inland"),
        (0.03, "Mid-inland"),
        (0.30, "Far-inland"),
    ],
    "Erosion": [
        (-1.00, "E0"),
        (-0.78, "E1"),
        (-0.375, "E2"),
        (-0.2225, "E3"),
        (0.05, "E4"),
        (0.45, "E5"),
        (0.55, "E6"),
    ],
    "Temperature": [
        (-1.00, "Frozen"),
        (-0.45, "Cold"),
        (-0.15, "Temperate"),
        (0.20, "Warm"),
        (0.55, "Hot"),
    ],
    "Humidity": [
        (-1.00, "Arid"),
        (-0.35, "Dry"),
        (-0.10, "Neutral"),
        (0.10, "Wet"),
        (0.30, "Humid"),
    ],
    "Weirdness": [
        (-1.00, "Standard"),
        (0.00, "Variant"),
    ],
    # Height : repères de couches MC.  Y est en blocs (donc on saute le
    # *10000 — ces seuils sont déjà en blocs Y).
    "Height": [
        (-64,  "Bedrock"),
        (-30,  "Deepslate"),
        (0,    "Cave"),
        (40,   "Underwater"),
        (63,   "Sea level"),
        (80,   "Lowland"),
        (110,  "Hills"),
        (160,  "Mountain"),
        (220,  "Peak"),
        (280,  "Sky"),
    ],
}


def noise_value_at(noise_key: str, pos: float) -> float:
    """Convert editor position [0..1 active range] to actual noise value."""
    nmin, nmax = NOISE_VALUE_RANGES.get(noise_key, (-1.0, 1.0))
    return nmin + pos * (nmax - nmin)


def pos_bounds_for_noise(noise_key: str, noise_limit: float = 2.0):
    """Return (pos_min, pos_max) for the editor bar.
    Height: exactly 0.0..1.0 (covers -30..320 Y, no extrapolation needed).
    Others: ±noise_limit → ±20000 on map."""
    if noise_key == "Height":
        return 0.0, 1.0
    nmin, nmax = NOISE_VALUE_RANGES.get(noise_key, (-1.0, 1.0))
    span = nmax - nmin
    if span == 0:
        return -1.0, 1.5
    return (-noise_limit - nmin) / span, (noise_limit - nmin) / span


def regime_label_at(noise_key: str, noise_value: float) -> str:
    """Returns the biome regime label at the given noise value (e.g.
    'Deep Ocean' for Continentalness=-0.6). Uses Minecraft 1.18+ official
    breakpoints. Returns '' if no regime defined for this noise."""
    table = NOISE_REGIME_LABELS.get(noise_key)
    if not table:
        return ""
    label = table[0][1]
    for thr, lbl in table:
        if noise_value >= thr:
            label = lbl
        else:
            break
    return label



def _make_noise_luts() -> dict:
    import numpy as _np, colorsys as _cs
    g = _np.arange(256, dtype=_np.uint8)
    d = {}
    # "Default" = current semantic palette (applies to the active noise)
    # Built from Continentalness stops as the most representative,
    # but rendering uses NOISE_ACTIVE_PALETTE per noise channel.
    d["Semantic"]  = _lerp_lut(NOISE_DEFAULT_STOPS["Continentalness"])
    d["Grayscale"]= _np.stack([g, g, g], axis=1)
    d["Warm"]     = _lerp_lut([(0.0,0,0,0),(0.25,128,0,0),(0.50,220,80,0),(0.75,255,200,0),(1.0,255,255,255)])
    d["Cool"]     = _lerp_lut([(0.0,0,0,0),(0.25,0,0,180),(0.55,0,160,220),(0.80,80,240,255),(1.0,255,255,255)])
    d["Blue↔Red"] = _lerp_lut([(0.0,20,40,200),(0.25,80,140,255),(0.50,255,255,255),(0.75,255,100,60),(1.0,180,0,0)])
    d["Heat"]     = _lerp_lut([(0.0,0,0,60),(0.20,0,80,180),(0.45,0,200,80),(0.65,220,220,0),(0.82,255,100,0),(1.0,200,0,0)])
    rainbow = _np.zeros((256,3), dtype=_np.uint8)
    for i in range(256):
        r2,g2,b2 = _cs.hsv_to_rgb(i/256.0, 1.0, 1.0)
        rainbow[i] = [int(r2*255),int(g2*255),int(b2*255)]
    d["Rainbow"]  = rainbow
    d["Cubiomes"] = _lerp_lut([(0.0,0,0,30),(0.20,20,0,100),(0.40,80,20,200),(0.55,120,180,255),(0.70,40,220,200),(0.85,200,255,160),(1.0,255,255,200)])
    # "Aurora" — palette originale aurore boréale : nuit → teal → vert → menthe →
    # bleu glacé → violet → rose pâle. Douce et contrastée pour tous les noises.
    d["Aurora"]   = _lerp_lut([(0.0,8,10,35),(0.16,12,48,84),(0.36,16,150,120),(0.55,92,240,176),(0.72,150,205,255),(0.87,190,140,250),(1.0,255,226,245)])
    # "Nebula" — palette originale nébuleuse : nuit spatiale → indigo → magenta →
    # rose → corail → poussière d'or → lumière d'étoile. Chaude et lumineuse.
    d["Nebula"]   = _lerp_lut([(0.0,8,6,30),(0.18,46,16,88),(0.38,120,30,140),(0.56,205,60,130),(0.72,250,130,110),(0.86,250,210,130),(1.0,250,250,235)])
    # ── LUTs Height spécifiques pour chaque palette built-in ─────────────────
    # La heightmap couvre Y: -30→320. Chaque palette a des stops calibrés
    # pour cette plage afin d'éviter l'effet "tout blanc" ou "unicouleur".
    # pos = (Y - (-30)) / 350  → pos 0.27 = sea level Y=63, pos 0.31 = beach Y=80
    #
    # "__height_Palette__" est utilisé automatiquement lors du rendu Height
    # si la palette globale est "Palette".

    # Semantic (palette MC classique) — déjà bonne, on la garde
    d["__height_semantic__"] = _lerp_lut(NOISE_DEFAULT_STOPS["Height"])

    # Grayscale Height = cubiomes-viewer (08/10): grey = Y clamped to 0..255
    # (core range -30..320); the red sinkholes (Y <= -64) are on the extended
    # axis, see grayscale_height_lut()
    _h_gray = _np.zeros((256, 3), dtype=_np.uint8)
    for _i in range(256):
        _y = -30.0 + _i / 255.0 * 350.0
        v = int(max(0, min(255, round(_y))))
        _h_gray[_i] = [v, v, v]
    d["__height_Grayscale__"] = _h_gray

    # Warm Height : bleu profond → cyan → sable → orange → rouge (altitude)
    d["__height_Warm__"] = _lerp_lut([
        (0.000,   0,   0,  80),   # Y=-30  fond océan nuit
        (0.086,   5,  20, 130),   # Y=0    profond
        (0.200,  15,  80, 200),   # Y=40   eaux moyennes
        (0.266,  30, 130, 230),   # Y=63   surface mer
        (0.314, 240, 220, 140),   # Y=80   plage sable
        (0.371, 210, 160,  40),   # Y=100  plaines chaudes
        (0.457, 200, 100,  10),   # Y=130  collines
        (0.543, 180,  60,   5),   # Y=160  hauts plateaux
        (0.657, 220,  80,  10),   # Y=200  montagnes chaudes
        (0.800, 255, 140,  50),   # Y=250  peaks orangés
        (1.000, 255, 220, 180),   # Y=320  sommet clair
    ])

    # Cool Height : profond noir → bleu nuit → bleu ciel → vert → neige
    d["__height_Cool__"] = _lerp_lut([
        (0.000,   0,   0,  30),   # Y=-30  noir abyssal
        (0.086,   0,  15, 100),   # Y=0
        (0.200,   0,  60, 180),   # Y=40
        (0.266,   0, 110, 220),   # Y=63   mer bleue
        (0.314,  80, 200, 230),   # Y=80   eau peu profonde
        (0.371,  60, 190, 120),   # Y=100  plaines vertes
        (0.457,  40, 150,  80),   # Y=130  collines
        (0.543,  60, 120,  60),   # Y=160  forêt dense
        (0.657, 100, 140, 100),   # Y=200  subalpine
        (0.800, 200, 220, 220),   # Y=250  neige fraîche
        (1.000, 240, 248, 255),   # Y=320  glacier blanc-bleu
    ])

    # Blue↔Red Height : bleu foncé (fond) → blanc (surface) → rouge (altitude)
    d["__height_Blue↔Red__"] = _lerp_lut([
        (0.000,   0,   0, 120),   # Y=-30  bleu nuit profond
        (0.086,  20,  40, 180),   # Y=0
        (0.200,  60, 100, 220),   # Y=40
        (0.266, 120, 170, 240),   # Y=63   mer bleu clair
        (0.314, 210, 230, 250),   # Y=80   plage blanc-bleu
        (0.400, 255, 245, 240),   # Y=111  blanc neutre (plaines)
        (0.457, 255, 220, 190),   # Y=130  légèrement rosé
        (0.543, 255, 160, 120),   # Y=160  orange doux
        (0.657, 230, 100,  60),   # Y=200  rouge orange
        (0.800, 200,  40,  20),   # Y=250  rouge vif
        (1.000, 140,   0,   0),   # Y=320  rouge très sombre
    ])

    # Heat Height : noir abyssal → vert → jaune → rouge brûlant
    d["__height_Heat__"] = _lerp_lut([
        (0.000,   0,   0,  15),   # Y=-30  noir
        (0.086,   0,  30,  80),   # Y=0    bleu sombre
        (0.200,   0, 100,  60),   # Y=40   vert sous-marin
        (0.266,   0, 180,  80),   # Y=63   vert vif (surface)
        (0.314, 120, 200,  20),   # Y=80   vert-jaune plage
        (0.371, 200, 200,   0),   # Y=100  jaune
        (0.457, 240, 160,   0),   # Y=130  orange
        (0.543, 255,  90,   0),   # Y=160  orange vif
        (0.657, 220,  30,   0),   # Y=200  rouge orangé
        (0.800, 160,   5,   5),   # Y=250  rouge sombre
        (1.000, 200, 200, 200),   # Y=320  gris pic neigeux
    ])

    # Rainbow Height : spectre HSV COMPLET sur toute la plage Y.
    # idx=0 (Y=-30) = violet, idx=67 (Y=63 mer) = cyan, idx=255 (Y=320) = rouge.
    # On parcourt 0.75→0.0 de hue pour avoir violet→bleu→cyan→vert→jaune→orange→rouge.
    # Saturation et value élevées partout pour que le rainbow soit bien visible.
    _h_rainbow = _np.zeros((256, 3), dtype=_np.uint8)
    for _i in range(256):
        hue = 0.75 - (_i / 255.0) * 0.75   # 0.75 (violet) → 0.0 (rouge)
        sat = 0.95
        val = 0.55 + (_i / 255.0) * 0.40   # plus lumineux en altitude (0.55→0.95)
        _r, _g, _b = _cs.hsv_to_rgb(hue, sat, val)
        _h_rainbow[_i] = [int(_r*255), int(_g*255), int(_b*255)]
    d["__height_Rainbow__"] = _h_rainbow

    # Cubiomes Height : style géothermique / topographique contrasté.
    # Totalement différent de Semantic : violet profond → rose → or → blanc pur.
    # Contraste maximum entre océan (violet-noir) et continents (chaud→or).
    d["__height_Cubiomes__"] = _lerp_lut([
        (0.000,  10,   0,  40),   # Y=-30  violet-noir abyssal
        (0.086,  40,   0,  90),   # Y=0    violet profond
        (0.171,  90,   0, 140),   # Y=30   magenta sombre
        (0.266, 140,   0, 160),   # Y=63   magenta (surface mer)
        (0.314, 200,  60, 120),   # Y=80   rose corail (plage)
        (0.371, 240, 140,  40),   # Y=100  orange brûlé (terres basses)
        (0.457, 255, 210,   0),   # Y=130  or vif (collines)
        (0.543, 220, 240,  80),   # Y=160  jaune-vert (hauts plateaux)
        (0.657, 160, 230, 160),   # Y=200  vert clair (subalpine)
        (0.800, 220, 240, 240),   # Y=250  cyan-blanc (neige)
        (1.000, 255, 255, 255),   # Y=320  blanc pur sommet
    ])

    # Aurora Height : nuit → teal (mer) → menthe (côte) → vert → bleu glacé → violet.
    d["__height_Aurora__"] = _lerp_lut([
        (0.000,   8,  10,  35),   # Y=-30  nuit
        (0.086,  12,  40,  80),   # Y=0    bleu-teal profond
        (0.200,  16, 110, 120),   # Y=40   teal
        (0.266,  30, 150, 160),   # Y=63   teal (surface mer)
        (0.314,  92, 220, 176),   # Y=80   menthe (plage)
        (0.371, 150, 230, 150),   # Y=100  vert (terres basses)
        (0.543, 150, 205, 255),   # Y=160  bleu glacé (collines)
        (0.800, 200, 170, 250),   # Y=250  violet (pics)
        (1.000, 255, 226, 245),   # Y=320  rose pâle (sommet)
    ])

    # Nebula Height : nuit spatiale (fond) → magenta (mer) → corail → or → étoile.
    d["__height_Nebula__"] = _lerp_lut([
        (0.000,   8,   6,  30),   # Y=-30  nuit spatiale
        (0.086,  30,  12,  70),   # Y=0    indigo profond
        (0.200,  70,  24, 120),   # Y=40   violet
        (0.266, 130,  40, 150),   # Y=63   magenta (surface mer)
        (0.314, 205,  80, 145),   # Y=80   rose (plage)
        (0.371, 245, 130, 115),   # Y=100  corail (terres basses)
        (0.543, 248, 185, 120),   # Y=160  or doux (collines)
        (0.800, 250, 225, 165),   # Y=250  or clair (pics)
        (1.000, 252, 250, 240),   # Y=320  lumière d'étoile (sommet)
    ])

    return d

NOISE_LUTS: dict = _make_noise_luts()
# Liste des palettes disponibles dans le combobox — uniquement les originales + Default
# (on filtre les LUT internes avec préfixe "__")
NOISE_PALETTE_NAMES: list = [n for n in NOISE_LUTS.keys() if not n.startswith("__")]

# Couleurs signature pour chaque palette built-in — utilisées pour colorier
# les noms dans le menu déroulant. Une couleur représentative du gradient.
NOISE_PALETTE_NAME_COLORS: dict = {
    "Semantic":  "#7fc97f",   # vert nature
    "Grayscale": "#bbbbbb",   # gris
    "Warm":      "#ffaa55",   # orange chaud
    "Cool":      "#55c0ff",   # cyan froid
    "Blue↔Red":  "#c898ff",   # violet (mélange)
    "Heat":      "#ff7733",   # rouge-orange
    "Rainbow":   "#ff66cc",   # rose vif arc-en-ciel
    "Cubiomes":  "#5fd9b8",   # turquoise signature cubiomes
    "Aurora":    "#7fead0",   # menthe aurore boréale
    "Nebula":    "#d264c8",   # magenta nébuleuse
}


def palette_name_color(name: str) -> str:
    """Returns a foreground color for a palette name (used in the menu).
    Built-in palettes get distinct signature colors. Custom palettes
    are highlighted in accent yellow so they stand out from defaults."""
    if name in NOISE_PALETTE_NAME_COLORS:
        return NOISE_PALETTE_NAME_COLORS[name]
    # Custom palettes (saved by the user) get a uniform accent color
    return "#ffd166"

# Palette par défaut AU LANCEMENT : aléatoire parmi les built-in (Grayscale exclu).
import random as _random_pal
_pal_pool = [p for p in NOISE_PALETTE_NAMES if p != "Grayscale"]
_DEFAULT_NOISE_PALETTE = _random_pal.choice(_pal_pool) if _pal_pool else "Semantic"

# Palette active par noise : la palette aléatoire par défaut pour toutes au départ.
# The custom editor updates this dict without polluting NOISE_PALETTE_NAMES
# with entries like "Default (Noise)" or "Custom (Noise)".
NOISE_ACTIVE_PALETTE: dict = {name: _DEFAULT_NOISE_PALETTE for name in NOISE_DEFAULT_STOPS}
# Height / Exact Y : palette aléatoire par défaut comme le reste (chaque built-in
# a sa variante topographique __height_<palette>__, donc le rendu reste correct).
NOISE_ACTIVE_PALETTE["Height"] = _DEFAULT_NOISE_PALETTE
NOISE_ACTIVE_PALETTE["Exact Y"] = _DEFAULT_NOISE_PALETTE


# ═════════════════════════════════════════════════════════════════════════════
# CUSTOM PALETTES (26/09) — one gradient per noise channel, saved on disk.
#   CUSTOM_PALETTES = {name: {"Temperature": [{"pos": 0..1, "rgb": (r,g,b)}, …],
#                             …, "Height": […]}}
#   pos 0..1 = the displayed value range of the noise (NOISE_VALUE_RANGES; the
#   map shows height on -30..320). Registered in NOISE_LUTS the same way the
#   classic editor did: NOISE_LUTS[name] (primary), "__pernoise_<name>" and
#   "__stops_<name>".
# ═════════════════════════════════════════════════════════════════════════════
EDITOR_NOISES = ["Temperature", "Humidity", "Continentalness", "Erosion", "Weirdness", "Height"]
BUILTIN_PALETTES = set(NOISE_PALETTE_NAMES)
CUSTOM_PALETTES: dict = {}


def editor_noise(map_noise: str) -> str:
    """Map overlay name → editor channel ('Approx height' / 'Exact Y' → 'Height')."""
    return "Height" if map_noise in ("Approx height", "Exact Y", "Height") else map_noise


# ── extended axis (27/09): extremes are coloured too ──────────────────────────
# pos = (value - lo) / (hi - lo) over the usual range (DISPLAY_RANGES) is the
# "core" and keeps 76 % of the colour axis; values beyond, up to EXTREMES, use
# the 12 % tails on each side with a square-root compression (fine resolution
# just outside the usual range, still distinct colours at the records, e.g.
# weirdness ±3.3 = ±33 000 in cubiomes-viewer units, estimated height Y −230).
# The map's noise tiles and the LUTs share this axis (u in 0..1 → 256 levels).
DISPLAY_RANGES = dict(NOISE_VALUE_RANGES, Height=(-30.0, 320.0))
EXTREMES = {"Temperature": (-4.0, 4.0), "Humidity": (-4.0, 4.0),
            "Continentalness": (-4.0, 4.0), "Erosion": (-4.0, 4.0),
            "Weirdness": (-4.0, 4.0), "Height": (-512.0, 1024.0)}
TAIL = 0.12


def axis(noise):
    """(lo, hi, pos_min, pos_max) of an editor channel: core value range and the
    stop positions of the extremes (pos_min < 0, pos_max > 1)."""
    lo, hi = DISPLAY_RANGES.get(noise, (-1.0, 1.0))
    elo, ehi = EXTREMES.get(noise, (lo - (hi - lo), hi + (hi - lo)))
    return lo, hi, (elo - lo) / (hi - lo), (ehi - lo) / (hi - lo)


def u_from_pos(pos, noise):
    """stop / value position → colour-axis position 0..1 (numpy or float)."""
    _, _, pmin, pmax = axis(noise)
    p = np.asarray(pos, dtype=np.float64)
    core = TAIL + np.clip(p, 0.0, 1.0) * (1.0 - 2 * TAIL)
    left = TAIL * (1.0 - np.sqrt(np.clip(p / pmin, 0.0, 1.0)))
    right = 1.0 - TAIL + TAIL * np.sqrt(np.clip((p - 1.0) / (pmax - 1.0), 0.0, 1.0))
    u = np.where(p < 0.0, left, np.where(p > 1.0, right, core))
    return float(u) if np.ndim(u) == 0 else u


SINK_Y = -64        # cubiomes-viewer Grayscale: height ≤ −64 in red (sinkholes)


def sink_index():
    """Last colour index (0..255) of the Height channel whose level is ≤ SINK_Y."""
    lo, hi = DISPLAY_RANGES["Height"]
    u = u_from_pos((SINK_Y - lo) / (hi - lo), "Height")
    return int(np.floor(u * 255.0 + 1e-9))


def grayscale_height_lut():
    """cubiomes-viewer's Grayscale height view (applyHeightShading, HV_GRAYSCALE):
    grey = the height clamped to 0..255 (black at y ≤ 0, white from 255 up), and
    pure red at y ≤ −64 — on SSA's extended colour axis (08/10)."""
    lo, hi = DISPLAY_RANGES["Height"]
    idx = np.arange(256, dtype=np.float64)
    y = lo + pos_from_u(idx / 255.0, "Height") * (hi - lo)
    g = np.clip(np.rint(y), 0, 255).astype(np.uint8)
    lut = np.stack([g, g, g], axis=1)
    lut[: sink_index() + 1] = (255, 0, 0)
    return lut


def pos_from_u(u, noise):
    _, _, pmin, pmax = axis(noise)
    u = np.asarray(u, dtype=np.float64)
    core = (u - TAIL) / (1.0 - 2 * TAIL)
    left = pmin * (1.0 - np.clip(u / TAIL, 0.0, 1.0)) ** 2
    right = 1.0 + (pmax - 1.0) * np.clip((u - (1.0 - TAIL)) / TAIL, 0.0, 1.0) ** 2
    p = np.where(u < TAIL, left, np.where(u > 1.0 - TAIL, right, core))
    return float(p) if np.ndim(p) == 0 else p


def warp_lut(lut, noise):
    """A classic 256-entry LUT (index = core pos 0..1) → the extended axis
    (extremes take the end colours, as before)."""
    pos = np.clip(pos_from_u(np.linspace(0.0, 1.0, 256), noise), 0.0, 1.0)
    return lut[np.rint(pos * 255).astype(int)]


def lut_from_stops(stops, noise=None) -> "np.ndarray":
    """(256, 3) uint8 LUT, linear between stops, clamped outside (same result as
    _lerp_lut, vectorised: fast enough to rebuild while a stop is dragged).
    With `noise`: on the extended axis (see axis / u_from_pos)."""
    if not stops:
        g = np.arange(256, dtype=np.uint8)
        return np.stack([g, g, g], axis=1)
    st = sorted(stops, key=lambda s: s["pos"])
    xs = np.linspace(0.0, 1.0, 256) if noise is None else pos_from_u(np.linspace(0.0, 1.0, 256), noise)
    ps = np.array([float(s["pos"]) for s in st])
    cols = np.array([s["rgb"] for s in st], dtype=np.float64)
    out = np.empty((256, 3), dtype=np.float64)
    for c in range(3):
        out[:, c] = np.interp(xs, ps, cols[:, c])
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _sample_lut(lut, n=9):
    return [{"pos": k / (n - 1), "rgb": tuple(int(v) for v in lut[int(round(k / (n - 1) * 255))])}
            for k in range(n)]


def builtin_lut(palette: str, noise: str):
    """The LUT the map uses for a built-in palette on this editor channel."""
    if noise == "Height":
        lut = NOISE_LUTS.get("__height_semantic__" if palette == "Semantic" else f"__height_{palette}__")
        if lut is not None:
            return lut
    elif palette == "Semantic" and noise in NOISE_DEFAULT_STOPS:
        return _lerp_lut(NOISE_DEFAULT_STOPS[noise])
    return NOISE_LUTS.get(palette, NOISE_LUTS["Grayscale"])


def stops_for(palette: str, noise: str) -> list:
    """Editable stops (copies) of any palette for one channel: exact stops for a
    custom palette / Semantic, a 9-point sampling of the LUT otherwise."""
    if palette in CUSTOM_PALETTES:
        return [dict(s, rgb=tuple(s["rgb"])) for s in CUSTOM_PALETTES[palette].get(noise, [])] \
            or [{"pos": s[0], "rgb": tuple(s[1:4])} for s in NOISE_FALLBACK_STOPS]
    if palette == "Semantic" and noise in NOISE_DEFAULT_STOPS and noise != "Height":
        return [{"pos": float(s[0]), "rgb": (int(s[1]), int(s[2]), int(s[3]))}
                for s in NOISE_DEFAULT_STOPS[noise] if len(s) >= 4 and not isinstance(s[1], str)]
    return _sample_lut(builtin_lut(palette, noise))


def clean_stops(stops, noise=None) -> list:
    """Clamp to the channel's extremes (0..1 without a channel), drop
    duplicates, sort; at least 2 stops."""
    out, seen = [], set()
    pmin, pmax = (0.0, 1.0) if noise is None else axis(noise)[2:]
    for s in stops or []:
        try:
            p = round(min(pmax, max(pmin, float(s["pos"]))), 4)
            rgb = tuple(int(min(255, max(0, int(c)))) for c in s["rgb"])[:3]
        except (KeyError, TypeError, ValueError):
            continue
        if len(rgb) == 3 and (p, rgb) not in seen:
            seen.add((p, rgb)); out.append({"pos": p, "rgb": rgb})
    out.sort(key=lambda s: s["pos"])
    if len(out) == 1:
        out.append({"pos": 1.0 if out[0]["pos"] < 1.0 else 0.0, "rgb": out[0]["rgb"]})
        out.sort(key=lambda s: s["pos"])
    if not out:
        out = [{"pos": s[0], "rgb": tuple(s[1:4])} for s in NOISE_FALLBACK_STOPS]
    return out


def register_custom(name: str, stops_by_noise: dict):
    """(Re)define a custom palette in memory (LUTs rebuilt)."""
    pal = {k: clean_stops(stops_by_noise.get(k), k) for k in EDITOR_NOISES}
    CUSTOM_PALETTES[name] = pal
    luts = {k: lut_from_stops(v) for k, v in pal.items()}
    NOISE_LUTS[name] = luts["Temperature"]
    NOISE_LUTS[f"__pernoise_{name}"] = luts
    NOISE_LUTS[f"__stops_{name}"] = pal
    if name not in NOISE_PALETTE_NAMES:
        NOISE_PALETTE_NAMES.append(name)


def remove_custom(name: str):
    CUSTOM_PALETTES.pop(name, None)
    for k in (name, f"__pernoise_{name}", f"__stops_{name}"):
        NOISE_LUTS.pop(k, None)
    if name in NOISE_PALETTE_NAMES and name not in BUILTIN_PALETTES:
        NOISE_PALETTE_NAMES.remove(name)


def custom_lut(name: str, map_noise: str):
    luts = NOISE_LUTS.get(f"__pernoise_{name}")
    return None if luts is None else luts.get(editor_noise(map_noise))


def custom_file() -> str:
    import os
    import ssa_paths
    new = os.path.join(ssa_paths.data_dir(), "noise_palettes.json")
    if not os.environ.get("APPDATA"):         # Linux before 29/09: ~/.config/SimpleSeedAnalyser
        old = os.path.join(os.path.expanduser("~"), ".config", "SimpleSeedAnalyser", "noise_palettes.json")
        if not os.path.exists(new) and os.path.exists(old):
            try:
                os.replace(old, new)
            except OSError:
                return old
    return new


def save_custom():
    import json, os
    path = custom_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {"version": 2, "palettes": {n: {k: [{"pos": s["pos"], "rgb": list(s["rgb"])} for s in v]
                                           for k, v in p.items()}
                                       for n, p in CUSTOM_PALETTES.items()
                                       if n not in BUILTIN_PALETTES}}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)                # never a half-written file


def load_custom():
    import json, os
    path = custom_file()
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for name, pal in (data.get("palettes") or {}).items():
            if name not in BUILTIN_PALETTES and isinstance(pal, dict):
                register_custom(name, pal)
    except Exception as e:                # a broken file must not stop SSA
        print(f"[noise palettes] could not read {path}: {e}")


def read_noisepak(path: str) -> dict:
    """.noisepak (classic editor export, v1): {"palettes": {noise: [stops]}}."""
    import json
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pals = data.get("palettes", data)
    return {k: clean_stops(v, k) for k, v in pals.items() if k in EDITOR_NOISES}


def write_noisepak(path: str, stops_by_noise: dict):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "palettes": {k: [{"pos": s["pos"], "rgb": list(s["rgb"])}
                                                   for s in v]
                                               for k, v in stops_by_noise.items()}}, f, indent=2)


# ── built-in palettes defined by stops, extremes included (27/09) ─────────────
# Same machinery as a custom palette (per-noise stops on the extended axis) but
# read-only and never written to the user's file.
def _vstops(noise, pts):
    """[(value, '#rrggbb')] in cubiomes-viewer units (×10 000; blocks for
    Height) → stops (pos relative to the usual range)."""
    lo, hi = DISPLAY_RANGES[noise]
    k = 1.0 if noise == "Height" else 10000.0
    out = []
    for v, hx in pts:
        hx = hx.lstrip("#")
        out.append({"pos": round((v / k - lo) / (hi - lo), 4),
                    "rgb": (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))})
    return out


# "Eclipse": a dark, quiet diverging core (indigo · steel · ash corona · copper ·
# wine) so ordinary terrain reads calmly, and luminous extremes that ignite past
# ±10 000 — electric blue → cyan → ice below, magenta → neon orange → solar gold
# above, up to ±25 000. Records jump out of the map.
_ECLIPSE_CLIMATE = [(-25000, "#f2f8ff"), (-19000, "#7df9ff"), (-13500, "#3a6cf4"),
                    (-10000, "#161a42"), (-6000, "#28386a"), (-2500, "#56727f"),
                    (0, "#d9ceb2"), (2500, "#a9824f"), (6000, "#6e3826"),
                    (10000, "#34121e"), (13500, "#b3246b"), (19000, "#ff7a3d"),
                    (25000, "#fff1a8")]
_ECLIPSE_HEIGHT = [(-230, "#f2f8ff"), (-160, "#7df9ff"), (-90, "#3a6cf4"),
                   (-30, "#161a42"), (25, "#28386a"), (62, "#56727f"), (64, "#d9ceb2"),
                   (110, "#a9824f"), (190, "#6e3826"), (320, "#34121e"), (390, "#b3246b"),
                   (470, "#ff7a3d"), (560, "#fff1a8")]


# "Nocturne": the same idea in the other half of the colour wheel — a muted
# core like an old botanical plate (dark forest · sage · bone · mauve-grey ·
# aubergine), extremes that glow: emerald → acid lime → pale mint below
# −10 000, electric violet → hot pink → pearl above +10 000 (to ±25 000).
_NOCTURNE_CLIMATE = [(-25000, "#f2ffd8"), (-19000, "#a4ff3c"), (-13500, "#10b56c"),
                     (-10000, "#17221c"), (-6000, "#34463b"), (-2500, "#6f8170"),
                     (0, "#d8d3c5"), (2500, "#978a96"), (6000, "#56455f"),
                     (10000, "#20172a"), (13500, "#7b3cff"), (19000, "#ff3db8"),
                     (25000, "#ffe4f5")]
_NOCTURNE_HEIGHT = [(-230, "#f2ffd8"), (-160, "#a4ff3c"), (-90, "#10b56c"),
                    (-30, "#17221c"), (25, "#34463b"), (62, "#6f8170"), (64, "#d8d3c5"),
                    (110, "#978a96"), (190, "#56455f"), (320, "#20172a"), (390, "#7b3cff"),
                    (470, "#ff3db8"), (560, "#ffe4f5")]


# (08/10) two more extended palettes, other halves of the colour wheel again:
# "Reef": a sea-floor core (deep blue · teal · wet sand · dusty coral · plum),
# extremes below −10 000 in bioluminescent aqua → mint → foam, above +10 000 in
# hot coral → magenta → shell pink. "Spectral": a soft olive / stone core, the
# extremes running through the spectrum — violet → blue → cyan below, yellow →
# orange → red above (to ±25 000).
_REEF_CLIMATE = [(-25000, "#f0fff9"), (-19000, "#7dffd6"), (-13500, "#18c7c1"),
                 (-10000, "#0d2a3f"), (-6000, "#174d63"), (-2500, "#3f7f7a"),
                 (0, "#e3d3b0"), (2500, "#c98f7a"), (6000, "#8e4f68"),
                 (10000, "#3b1838"), (13500, "#ff5f6d"), (19000, "#ff3fb0"),
                 (25000, "#ffe3ef")]
_REEF_HEIGHT = [(-230, "#f0fff9"), (-160, "#7dffd6"), (-90, "#18c7c1"),
                (-30, "#0d2a3f"), (25, "#174d63"), (62, "#3f7f7a"), (64, "#e3d3b0"),
                (110, "#c98f7a"), (190, "#8e4f68"), (320, "#3b1838"), (390, "#ff5f6d"),
                (470, "#ff3fb0"), (560, "#ffe3ef")]
_SPECTRAL_CLIMATE = [(-25000, "#e9f9ff"), (-19000, "#38d8ff"), (-13500, "#3f5bff"),
                     (-10000, "#2a1a5e"), (-6000, "#3c4a3a"), (-2500, "#6f7a5a"),
                     (0, "#cfc9b0"), (2500, "#9d8f62"), (6000, "#5f5236"),
                     (10000, "#3a2416"), (13500, "#ffd23a"), (19000, "#ff7b1c"),
                     (25000, "#ff2a2a")]
_SPECTRAL_HEIGHT = [(-230, "#e9f9ff"), (-160, "#38d8ff"), (-90, "#3f5bff"),
                    (-30, "#2a1a5e"), (25, "#3c4a3a"), (62, "#6f7a5a"), (64, "#cfc9b0"),
                    (110, "#9d8f62"), (190, "#5f5236"), (320, "#3a2416"), (390, "#ffd23a"),
                    (470, "#ff7b1c"), (560, "#ff2a2a")]


def _from(climate, height):
    return {n: _vstops(n, height if n == "Height" else climate) for n in EDITOR_NOISES}


STOP_BUILTINS = {"Eclipse": _from(_ECLIPSE_CLIMATE, _ECLIPSE_HEIGHT),
                 "Nocturne": _from(_NOCTURNE_CLIMATE, _NOCTURNE_HEIGHT),
                 "Reef": _from(_REEF_CLIMATE, _REEF_HEIGHT),
                 "Spectral": _from(_SPECTRAL_CLIMATE, _SPECTRAL_HEIGHT)}
_STOP_COLORS = {"Eclipse": "#ff8a4d", "Nocturne": "#b98cff", "Reef": "#3fe0c8",
                "Spectral": "#ffc23a"}
for _n, _p in STOP_BUILTINS.items():
    register_custom(_n, _p)
    BUILTIN_PALETTES.add(_n)
    NOISE_PALETTE_NAME_COLORS[_n] = _STOP_COLORS[_n]


def palette_groups():
    """Menu order: [(title, [names])] — Grayscale, the classic palettes, the
    extended ones (colours to ±25 000), then the user's own palettes."""
    names = list(NOISE_PALETTE_NAMES)
    classic = [n for n in names if n in BUILTIN_PALETTES and n not in STOP_BUILTINS
               and n != "Grayscale"]
    ext = [n for n in STOP_BUILTINS if n in names]
    mine = [n for n in names if n not in BUILTIN_PALETTES]
    groups = [("", ["Grayscale"] if "Grayscale" in names else []),
              ("Classic", classic), ("Extremes · to ±25 000", ext), ("Your palettes", mine)]
    return [(t, g) for t, g in groups if g]

load_custom()
