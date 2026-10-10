"""
qtui/struct_icons.py — SSA's own structure icon set (vector, QPainter).

One visual language for the whole app (map, layers list, conditions tree,
condition picker): each structure has its OWN silhouette — a house for a
village, a watchtower for an outpost, a pyramid, a dome for an igloo… — in the
structure's colour, with a dark sticker outline so it stands out on every
biome, and details (doors, windows) cut in a darker shade. Sharp at any DPI.

    pixmap(key, size)  → QPixmap (cached, HiDPI)
    icon(key, size)    → QIcon
    color(key)         → badge colour (hex)
    label(key)         → display name
Keys: map keys (cubiomes_wrap.STRUCTURE_TYPES) and condition keys (outpost,
hut, treasure, first_stronghold…) are both accepted.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

ALIASES = {
    "outpost": "pillager_outpost", "hut": "swamp_hut", "witch_hut": "swamp_hut",
    "treasure": "buried_treasure", "first_stronghold": "stronghold",
    "origin_cross": "origin", "abandoned_village": "zombie_village",
}

# key: (label, badge colour)
SPEC = {
    "village":          ("Village",           "#d9b27c"),
    "zombie_village":   ("Abandoned village", "#c3c9b0"),
    "pillager_outpost": ("Pillager outpost",  "#5d636b"),
    "desert_pyramid":   ("Desert pyramid",    "#e2c46a"),
    "jungle_temple":    ("Jungle temple",     "#3f8f3a"),
    "swamp_hut":        ("Swamp hut",         "#7e6a45"),
    "igloo":            ("Igloo",             "#bfe2f3"),
    "igloo_basement":   ("Igloo with basement", "#bfe2f3"),
    "monument":         ("Ocean monument",    "#1f9aa6"),
    "mansion":          ("Woodland mansion",  "#5b3a22"),
    "ruined_portal":    ("Ruined portal",     "#7a3fe0"),
    "ancient_city":     ("Ancient city",      "#24435f"),
    "buried_treasure":  ("Buried treasure",   "#e2ad2c"),
    "shipwreck":        ("Shipwreck",         "#8a6540"),
    "ocean_ruin":       ("Ocean ruin",        "#b5b09a"),
    "trail_ruins":      ("Trail ruins",       "#b56a45"),
    "trial_chambers":   ("Trial chambers",    "#c46a2b"),
    "abandoned_camp":   ("Abandoned camp",    "#8f969d"),
    "stronghold":       ("Stronghold",        "#2f8f62"),
    "spawn":            ("Spawn",             "#e5484d"),
    "origin":           ("Origin (0, 0)",     "#e8ecf3"),
    "fortress":         ("Nether fortress",   "#8a2a2a"),
    "bastion":          ("Bastion remnant",   "#3b3940"),
    "end_city":         ("End city",          "#b58ccf"),
    "ruined_portal_nether": ("Ruined portal (Nether)", "#7a3fe0"),
    "end_gateway":      ("End gateway",       "#6fd3c9"),
    "mineshaft":        ("Mineshaft",         "#8b6a3c"),
    "desert_well":      ("Desert well",       "#d6bd78"),
    "dungeon":          ("Dungeon (1.16)",    "#5f6b75"),
    "dungeon_ok":       ("Dungeon (in Minecraft)", "#3f8f62"),
    "lava_lake":        ("Lava lake (1.16)",  "#d9531e"),
    "lava_lake_surface": ("Surface lava lake", "#e88a3a"),
    # ── non-structure conditions ──
    "biome_filter":     ("Overworld at scale", "#58a64a"),
    "biome_sample":     ("Biome samples",      "#58a64a"),
    "biome_center":     ("Locate biome center", "#58a64a"),
    "climate_minmax":   ("Locate climate extreme", "#e0604a"),
    "climate_center":   ("Locate climate center", "#e0604a"),
    "climate_noise":    ("Climate parameters", "#e0604a"),
    "noise_sample":     ("Climate noise samples", "#4aa0e0"),
    "crunchiness":      ("Crunchiness",        "#c77ddb"),
    "height":           ("Approx Y",           "#8fa37a"),
    "precise_y":        ("Exact Y",            "#8fa37a"),
    "slime":            ("Slime chunk",        "#72c75a"),
    "spiral":           ("Spiral iterator",    "#5fb3ff"),
    "logic_or":         ("OR gate",            "#8e98ab"),
    "logic_not":        ("NOT gate",           "#8e98ab"),
    "scale_to_nether":  ("Scale to Nether",    "#b8342c"),
    "scale_to_overworld": ("Scale to Overworld", "#4f9a3a"),
}

_CACHE: dict = {}


def canon(key: str) -> str:
    return ALIASES.get(key, key)


def known(key: str) -> bool:
    return canon(key) in SPEC


def color(key: str) -> str:
    return SPEC.get(canon(key), ("", "#9aa3b8"))[1]


def label(key: str) -> str:
    return SPEC.get(canon(key), (key.replace("_", " ").capitalize(), ""))[0]


def _ink(bg: QColor) -> QColor:
    """White pictogram on dark badges, near-black on light ones."""
    lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return QColor("#1b1f27") if lum > 170 else QColor("#ffffff")


def _poly(*pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


# ── pictograms, drawn in a 24 × 24 box centred on (12, 12) ──────────────────
def _glyph(k: str, p: QPainter, ink: QColor, bg: QColor):
    fill = ink
    hole = QColor(bg)                      # "cut-outs" (doors, windows) in badge colour
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)

    def cut(shape_fn):
        p.setBrush(hole); shape_fn(); p.setBrush(fill)

    def stroke(w=1.6):
        pen = QPen(ink, w); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin); p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)

    if k == "village":                               # house: plank walls, red-brown roof
        p.drawRect(QRectF(6.5, 10.5, 11, 8))
        p.setBrush(QColor("#9c4a2c"))
        p.drawPolygon(_poly((12, 4.6), (19.6, 11.2), (4.4, 11.2)))
        p.setBrush(fill)
        cut(lambda: p.drawRect(QRectF(10.7, 13.6, 2.6, 4.9)))
        cut(lambda: p.drawRect(QRectF(7.9, 12.5, 1.8, 1.8)))
        cut(lambda: p.drawRect(QRectF(14.3, 12.5, 1.8, 1.8)))
    elif k == "zombie_village":                      # abandoned: mossy torn roof, dark
        p.drawRect(QRectF(6.5, 10.5, 11, 8))         # windows, a cobweb in the corner
        p.setBrush(QColor("#3d4a2a"))
        p.drawPolygon(_poly((12, 4.6), (19.6, 11.2), (13.9, 11.2), (12.6, 8.2), (11.2, 11.2), (4.4, 11.2)))
        p.setBrush(QColor("#1d2118"))
        p.drawRect(QRectF(10.7, 13.6, 2.6, 4.9))
        p.drawRect(QRectF(7.9, 12.5, 1.8, 1.8))
        p.drawRect(QRectF(14.3, 12.5, 1.8, 1.8))
        web = QPen(QColor(40, 44, 36, 230), 0.75)
        p.setPen(web); p.setBrush(Qt.BrushStyle.NoBrush)
        c = QPointF(17.5, 10.5)                       # web in the top-right corner
        for a, b in (((17.5, 13.6), (14.4, 10.5)), ((17.5, 16.0), (12.4, 10.5))):
            p.drawLine(c, QPointF(*a)); p.drawLine(c, QPointF(*b))
        path = QPainterPath(); path.moveTo(17.5, 12.2); path.quadTo(16.4, 11.6, 15.8, 10.5); p.drawPath(path)
        path = QPainterPath(); path.moveTo(17.5, 14.4); path.quadTo(15.2, 12.8, 14.0, 10.5); p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(fill)
    elif k == "pillager_outpost":                    # watchtower with flag
        p.drawRect(QRectF(9, 9.5, 6, 9.5))
        p.drawPolygon(_poly((7, 7.5), (17, 7.5), (17, 10), (7, 10)))
        for x in (7, 10.7, 14.4):
            p.drawRect(QRectF(x, 6, 2.6, 1.8))
        p.drawRect(QRectF(11.4, 2.8, 1.2, 3.4))
        p.drawPolygon(_poly((12.6, 2.8), (16.2, 3.8), (12.6, 4.9)))
        cut(lambda: p.drawRect(QRectF(11, 12, 2, 3)))
    elif k == "desert_pyramid":                      # stepped pyramid
        p.drawPolygon(_poly((12, 5.5), (19.5, 18.5), (4.5, 18.5)))
        stroke(1.1); p.setPen(QPen(bg, 1.1))
        p.drawLine(QPointF(8.4, 13.2), QPointF(15.6, 13.2)); p.drawLine(QPointF(6.6, 16.1), QPointF(17.4, 16.1))
    elif k == "jungle_temple":                       # ziggurat with door
        for i, (w, y) in enumerate(((15, 15), (11, 11.2), (7, 7.4))):
            p.drawRect(QRectF(12 - w / 2, y, w, 4 if i == 0 else 3.9))
        cut(lambda: p.drawRect(QRectF(10.7, 15.8, 2.6, 3.2)))
    elif k == "swamp_hut":                           # hut on stilts, dark roof
        p.drawRect(QRectF(7, 9.4, 10, 5.6))
        p.setBrush(QColor("#4a3a24"))
        p.drawPolygon(_poly((12, 4.8), (19, 9.6), (5, 9.6)))
        p.setBrush(fill)
        p.drawRect(QRectF(7.5, 15, 1.6, 4)); p.drawRect(QRectF(14.9, 15, 1.6, 4))
        cut(lambda: p.drawRect(QRectF(11, 11, 2.2, 4)))
    elif k == "igloo":                               # snow dome + entrance
        path = QPainterPath(); path.moveTo(4.5, 17.5); path.arcTo(QRectF(4.5, 7, 15, 21), 180, -180)
        path.closeSubpath(); p.drawPath(path)
        cut(lambda: p.drawPie(QRectF(9.6, 12.4, 4.8, 10), 0, 180 * 16))
    elif k == "igloo_basement":                      # the igloo, a ladder, a small basement
        path = QPainterPath(); path.moveTo(4.8, 13.6); path.arcTo(QRectF(4.8, 3.4, 14.4, 20.4), 180, -180)
        path.closeSubpath(); p.drawPath(path)
        cut(lambda: p.drawPie(QRectF(10.0, 9.0, 4.0, 9.2), 0, 180 * 16))
        p.setBrush(QColor("#9a6a3a"))                # the basement, narrower than the dome
        p.drawRect(QRectF(8.3, 16.6, 7.4, 3.0))
        p.setBrush(QColor("#5a3a1e"))
        p.drawRect(QRectF(9.4, 17.4, 1.5, 1.5)); p.drawRect(QRectF(13.1, 17.4, 1.5, 1.5))
        lad = QPen(QColor("#7a5530"), 0.8); p.setPen(lad); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(11.3, 13.6), QPointF(11.3, 16.6)); p.drawLine(QPointF(12.7, 13.6), QPointF(12.7, 16.6))
        p.drawLine(QPointF(11.3, 15.1), QPointF(12.7, 15.1))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(fill)
    elif k == "monument":                            # underwater temple + waves
        p.drawRect(QRectF(5, 11, 14, 4))
        p.drawPolygon(_poly((7, 11), (12, 6.2), (17, 11)))
        for x in (7.2, 11.2, 15.2):
            cut(lambda x=x: p.drawRect(QRectF(x, 12, 1.6, 3)))
        stroke(1.4)
        path = QPainterPath(); path.moveTo(4.5, 18.2)
        for i in range(4):
            path.quadTo(5.9 + i * 3.75, 16.6, 7.3 + i * 3.75, 18.2)
        p.drawPath(path)
    elif k == "mansion":                             # wide dark-oak block, 2 floors, gables
        roof = QColor("#3b2414"); birch = QColor("#e3d7b0"); cobble = QColor("#8a8a86")
        p.setBrush(cobble); p.drawRect(QRectF(3.4, 17.6, 17.2, 1.9))          # cobblestone base
        p.setBrush(fill); p.drawRect(QRectF(3.4, 9.6, 17.2, 8.1))              # dark-oak body
        p.setBrush(roof)
        p.drawPolygon(_poly((2.9, 9.8), (5.2, 7.2), (8.6, 7.2), (8.6, 9.8)))   # left wing roof
        p.drawPolygon(_poly((15.4, 9.8), (15.4, 7.2), (18.8, 7.2), (21.1, 9.8)))  # right wing roof
        p.setBrush(fill); p.drawRect(QRectF(8.6, 6.4, 6.8, 3.4))               # central block
        p.setBrush(roof); p.drawPolygon(_poly((8.1, 6.6), (12, 3.4), (15.9, 6.6)))  # central gable
        p.setBrush(birch)                                                       # birch floor line
        p.drawRect(QRectF(3.4, 13.3, 17.2, 0.8))
        for x in (4.7, 7.4, 13.9, 16.6):                                        # windows, 2 floors
            p.drawRect(QRectF(x, 10.8, 1.7, 1.7)); p.drawRect(QRectF(x, 15.1, 1.7, 1.7))
        p.drawRect(QRectF(11.1, 7.4, 1.8, 1.6))
        p.drawRect(QRectF(10.6, 15.0, 2.8, 2.7))                               # front door
        p.setBrush(fill)
    elif k == "ruined_portal":                       # obsidian frame, one corner broken
        p.setBrush(QColor("#d9b8ff"))
        p.drawRect(QRectF(8.6, 7.6, 6.8, 10.8))          # portal glow
        p.setBrush(QColor("#1d1230"))                    # obsidian
        p.drawRect(QRectF(5.6, 4.6, 3, 16.8))            # left post
        p.drawRect(QRectF(5.6, 18.4, 12.8, 3))           # bottom
        p.drawRect(QRectF(15.4, 10.4, 3, 11))            # right post (top broken off)
        p.drawRect(QRectF(5.6, 4.6, 6.4, 3))             # top (half)
        p.drawRect(QRectF(16.6, 4.4, 2.4, 2.4))          # loose block
    elif k == "ancient_city":                        # the great arch
        path = QPainterPath()
        path.moveTo(4.5, 19); path.lineTo(4.5, 10); path.quadTo(4.5, 4.5, 12, 4.5)
        path.quadTo(19.5, 4.5, 19.5, 10); path.lineTo(19.5, 19); path.lineTo(15.8, 19); path.lineTo(15.8, 11)
        path.quadTo(15.8, 8.2, 12, 8.2); path.quadTo(8.2, 8.2, 8.2, 11); path.lineTo(8.2, 19)
        path.closeSubpath(); p.drawPath(path)
        p.drawRect(QRectF(10.8, 3.2, 2.4, 3))
        c = QColor("#56e0e8"); p.setBrush(c); p.drawEllipse(QPointF(12, 14), 1.4, 1.4)
    elif k == "buried_treasure":                     # chest
        p.drawRoundedRect(QRectF(5, 8.5, 14, 4.2), 1.8, 1.8)
        p.drawRect(QRectF(5, 12.2, 14, 6.3))
        cut(lambda: p.drawRect(QRectF(5, 12.1, 14, 0.9)))
        cut(lambda: p.drawRect(QRectF(10.8, 11, 2.4, 3.2)))
    elif k == "shipwreck":                           # tilted hull + broken mast
        p.save(); p.translate(12, 12); p.rotate(-12); p.translate(-12, -12)
        p.drawPolygon(_poly((3.8, 13), (20.2, 13), (17.2, 18), (6.8, 18)))
        p.drawRect(QRectF(11, 5.5, 1.6, 7.6))
        p.drawPolygon(_poly((12.6, 6), (17.4, 10.8), (12.6, 10.8)))
        p.restore()
    elif k == "ocean_ruin":                          # broken stone columns + blue wave
        p.drawRect(QRectF(6, 8, 3, 8.5)); p.drawRect(QRectF(5.2, 7, 4.6, 1.6))
        p.drawPolygon(_poly((14, 16.5), (14, 10.5), (15.5, 9.5), (17, 11), (17, 16.5)))
        p.drawRect(QRectF(10.3, 14, 2.4, 2.5))
        p.setBrush(QColor("#5f8a4a"))                                           # moss
        p.drawRect(QRectF(6, 12, 3, 1.3)); p.drawRect(QRectF(14, 14, 3, 1.2))
        ink = QColor("#3f8fc8")
        stroke(1.6)
        path = QPainterPath(); path.moveTo(4.5, 19)
        for i in range(4):
            path.quadTo(5.9 + i * 3.75, 17.5, 7.3 + i * 3.75, 19)
        p.drawPath(path)
    elif k == "trail_ruins":                         # decorated pot (amphora)
        path = QPainterPath()
        path.moveTo(9.5, 5); path.lineTo(14.5, 5); path.lineTo(14.5, 7.2)
        path.cubicTo(19.5, 9, 19.5, 17.5, 15.5, 19); path.lineTo(8.5, 19)
        path.cubicTo(4.5, 17.5, 4.5, 9, 9.5, 7.2); path.closeSubpath(); p.drawPath(path)
        p.setPen(QPen(bg, 1.2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(7.4, 12), QPointF(16.6, 12)); p.drawLine(QPointF(7.6, 15), QPointF(16.4, 15))
    elif k == "trial_chambers":                      # spawner cage (grid cube)
        p.drawRoundedRect(QRectF(5.5, 5.5, 13, 13), 2, 2)
        for i in range(3):
            for j in range(3):
                cut(lambda i=i, j=j: p.drawRect(QRectF(7.2 + i * 3.6, 7.2 + j * 3.6, 2.4, 2.4)))
        c = QColor("#ffd27a"); p.setBrush(c); p.drawRect(QRectF(10.8, 10.8, 2.4, 2.4))
    elif k == "abandoned_camp":                      # tent
        p.drawPolygon(_poly((12, 5), (19.5, 18.5), (4.5, 18.5)))
        cut(lambda: p.drawPolygon(_poly((12, 11), (14.8, 18.5), (9.2, 18.5))))
        stroke(1.3); p.drawLine(QPointF(12, 5), QPointF(12, 3.6))
    elif k == "stronghold":                          # eye of ender
        path = QPainterPath(); path.moveTo(3.8, 12)
        path.quadTo(12, 3.5, 20.2, 12); path.quadTo(12, 20.5, 3.8, 12); p.drawPath(path)
        p.setBrush(QColor("#0f3b28")); p.drawEllipse(QPointF(12, 12), 3.6, 3.6)
        p.setBrush(QColor("#7ef0b4")); p.drawEllipse(QPointF(12, 12), 1.5, 1.5)
    elif k == "origin":                              # crosshair
        stroke(1.8)
        p.drawEllipse(QPointF(12, 12), 5, 5)
        p.drawLine(QPointF(12, 3.5), QPointF(12, 8)); p.drawLine(QPointF(12, 16), QPointF(12, 20.5))
        p.drawLine(QPointF(3.5, 12), QPointF(8, 12)); p.drawLine(QPointF(16, 12), QPointF(20.5, 12))
    elif k == "fortress":                            # battlements
        p.drawRect(QRectF(5, 10, 14, 8.5))
        for x in (5, 9.1, 13.2, 16.6):
            p.drawRect(QRectF(x, 7, 2.4, 3.2))
        cut(lambda: p.drawPie(QRectF(9.6, 13, 4.8, 9), 0, 180 * 16))
    elif k == "bastion":                             # blackstone keep with gold
        p.drawPolygon(_poly((5, 18.5), (5, 9), (8, 6), (16, 6), (19, 9), (19, 18.5)))
        p.setBrush(QColor("#f2c43b")); p.drawRect(QRectF(10, 10.5, 4, 4))
    elif k == "end_city":                            # stacked tower
        for w, y, h in ((10, 13, 5.5), (7.5, 8.5, 4.6), (5, 4.8, 3.8)):
            p.drawRect(QRectF(12 - w / 2, y, w, h))
            p.drawRect(QRectF(12 - w / 2 - 1.2, y, w + 2.4, 1.3))
    elif k == "mineshaft":                           # pickaxe
        stroke(2.2)
        path = QPainterPath(); path.moveTo(5, 9); path.quadTo(12, 3.5, 19, 9); p.drawPath(path)
        p.drawLine(QPointF(12, 6.4), QPointF(12, 19.5))
    elif k in ("dungeon", "dungeon_ok"):             # spawner cage (10/10)
        p.drawRoundedRect(QRectF(5.5, 5.5, 13, 13), 1.6, 1.6)
        cut(lambda: p.drawRect(QRectF(7.2, 7.2, 9.6, 9.6)))
        for x in (9.2, 11.4, 13.6):                  # bars
            p.drawRect(QRectF(x, 7.2, 1.2, 9.6))
        p.drawRect(QRectF(7.2, 11.4, 9.6, 1.2))
        p.setBrush(QColor("#ff8a3a") if k == "dungeon" else QColor("#7dffb0"))
        p.drawEllipse(QPointF(12, 12), 1.9, 1.9)     # the flame inside
    elif k in ("lava_lake", "lava_lake_surface"):    # lava pool with a drop
        path = QPainterPath(); path.moveTo(12, 3.5)
        path.cubicTo(15.5, 8, 17, 10.5, 17, 12.6); path.cubicTo(17, 15.5, 14.8, 17.4, 12, 17.4)
        path.cubicTo(9.2, 17.4, 7, 15.5, 7, 12.6); path.cubicTo(7, 10.5, 8.5, 8, 12, 3.5)
        p.setBrush(QColor("#ffd23a")); p.drawPath(path)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(4, 17.2, 16, 3.2), 1.6, 1.6)
        cut(lambda: p.drawEllipse(QPointF(10.6, 12.8), 1.3, 1.6))
    elif k == "desert_well":                         # well
        p.drawRect(QRectF(6, 13, 12, 5.5))
        p.drawPolygon(_poly((5, 8.5), (12, 4.5), (19, 8.5)))
        p.drawRect(QRectF(7, 8.4, 1.4, 4.8)); p.drawRect(QRectF(15.6, 8.4, 1.4, 4.8))
        cut(lambda: p.drawRect(QRectF(8, 14.4, 8, 1.4)))
    elif k in ("biome_filter", "biome_sample", "biome_center"):
        # a patch of map: four biomes, then samples / a target on top
        tile = QPainterPath(); tile.addRoundedRect(QRectF(4, 4, 16, 16), 3, 3)
        p.save(); p.setClipPath(tile)
        for (x, y, c) in ((4, 4, "#58a64a"), (12, 4, "#2f6fb0"), (4, 12, "#d9c77e"), (12, 12, "#2e7d32")):
            p.setBrush(QColor(c)); p.drawRect(QRectF(x, y, 8, 8))
        p.setBrush(QColor("#3f86c8")); p.drawEllipse(QRectF(9.5, 3, 7, 7))
        p.restore()
        if k == "biome_sample":
            p.setBrush(QColor("#ffffff"))
            for x, y in ((7, 7), (16, 8), (8, 16), (15.5, 15.5), (12, 11.5)):
                p.drawEllipse(QPointF(x, y), 1.25, 1.25)
        elif k == "biome_center":
            pen = QPen(QColor("#ffffff"), 1.5); p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(12, 12), 3.6, 3.6)
            for a, b in (((12, 5), (12, 8)), ((12, 16), (12, 19)), ((5, 12), (8, 12)), ((16, 12), (19, 12))):
                p.drawLine(QPointF(*a), QPointF(*b))
            p.setPen(Qt.PenStyle.NoPen)
    elif k in ("climate_minmax", "climate_center", "climate_noise"):
        # thermometer (+ arrow / target / bars)
        p.drawRoundedRect(QRectF(6.2, 3.5, 4.6, 12), 2.3, 2.3)
        p.drawEllipse(QPointF(8.5, 16.8), 3.7, 3.7)
        p.setBrush(QColor("#fff2ec")); p.drawRoundedRect(QRectF(7.6, 5, 1.8, 10), 0.9, 0.9)
        p.setBrush(QColor("#c0392b")); p.drawRect(QRectF(7.6, 10, 1.8, 6)); p.drawEllipse(QPointF(8.5, 16.8), 2.2, 2.2)
        p.setBrush(fill)
        if k == "climate_minmax":                    # up / down arrow = extreme
            p.drawPolygon(_poly((16, 3.8), (20, 8.4), (17.3, 8.4), (17.3, 11), (14.7, 11), (14.7, 8.4), (12, 8.4)))
            p.drawPolygon(_poly((16, 20.2), (20, 15.6), (17.3, 15.6), (17.3, 13), (14.7, 13), (14.7, 15.6), (12, 15.6)))
        elif k == "climate_center":
            pen = QPen(fill, 1.6); p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(16.2, 12), 3.2, 3.2); p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill); p.drawEllipse(QPointF(16.2, 12), 1.2, 1.2)
        else:
            for i, h in enumerate((5, 9, 7, 11)):
                p.drawRect(QRectF(12.4 + i * 2.1, 19 - h, 1.5, h))
    elif k == "noise_sample":                        # noise curve + sample dots
        stroke(1.9)
        path = QPainterPath(); path.moveTo(3.5, 14)
        path.cubicTo(6, 6, 8.5, 6, 10.5, 12); path.cubicTo(12.5, 18, 15, 18, 17, 10)
        path.cubicTo(18, 7, 19.5, 7, 20.5, 9); p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#ffffff"))
        for x, y in ((6.3, 9.4), (13.6, 16.2), (18.8, 7.9)):
            p.drawEllipse(QPointF(x, y), 1.7, 1.7)
    elif k == "crunchiness":                         # jagged noise
        stroke(2.0)
        p.drawPolyline(_poly((3.5, 15), (6, 9), (8, 13), (10, 5.5), (12.2, 14), (14.2, 8),
                             (16.4, 17), (18.4, 10), (20.5, 13)))
    elif k in ("height", "precise_y"):               # mountain + Y level
        p.drawPolygon(_poly((3.5, 19.5), (10, 7), (13, 12), (15.2, 9.5), (20.5, 19.5)))
        p.setBrush(QColor("#ffffff")); p.drawPolygon(_poly((10, 7), (11.9, 10.7), (10.4, 10.2), (8.6, 11.3)))
        if k == "height":
            pen = QPen(QColor("#ffe066"), 1.5); pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen); p.drawLine(QPointF(3, 12.5), QPointF(21, 12.5))
        else:
            p.setPen(QPen(QColor("#ffe066"), 1.6)); p.drawLine(QPointF(3, 12.5), QPointF(21, 12.5))
            for x in (5, 9, 13, 17):
                p.drawLine(QPointF(x, 11), QPointF(x, 14))
        p.setPen(Qt.PenStyle.NoPen)
    elif k == "slime":                               # slime cube with a face
        p.drawRoundedRect(QRectF(4.5, 5, 15, 14), 2.5, 2.5)
        p.setBrush(QColor(255, 255, 255, 60)); p.drawRoundedRect(QRectF(7, 7.5, 10, 9), 1.5, 1.5)
        p.setBrush(QColor("#1f3d17"))
        p.drawRect(QRectF(8, 9.5, 2.2, 2.2)); p.drawRect(QRectF(13.8, 9.5, 2.2, 2.2)); p.drawRect(QRectF(11.3, 14, 1.6, 1.4))
    elif k == "spiral":                              # spiral: tries positions outward
        import math as _m
        stroke(2.1)
        path = QPainterPath(); path.moveTo(12, 12)
        for i in range(1, 121):
            t = i / 120 * 2.6 * 2 * _m.pi
            r = 0.9 + 8.2 * i / 120
            path.lineTo(12 + r * _m.cos(t), 12 + r * _m.sin(t))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(fill); p.drawEllipse(QPointF(12, 12), 1.6, 1.6)
    elif k in ("logic_or", "logic_not"):             # labelled gate tag
        p.drawRoundedRect(QRectF(2.8, 6.5, 18.4, 11), 5.5, 5.5)
        p.setPen(QColor("#ffffff"))
        f = p.font(); f.setBold(True); f.setPixelSize(7 if k == "logic_or" else 6); p.setFont(f)
        p.drawText(QRectF(2.8, 6.5, 18.4, 11), Qt.AlignmentFlag.AlignCenter, "OR" if k == "logic_or" else "NOT")
        p.setPen(Qt.PenStyle.NoPen)
    elif k in ("scale_to_nether", "scale_to_overworld"):   # portal + ÷8 / ×8
        p.setBrush(QColor("#1d1230")); p.drawRect(QRectF(3.5, 4, 9, 16))
        p.setBrush(QColor("#9b5cf2")); p.drawRect(QRectF(5.6, 6.1, 4.8, 11.8))
        p.setBrush(fill); p.drawRoundedRect(QRectF(12.5, 8, 9, 8), 2.5, 2.5)
        p.setPen(QColor("#ffffff"))
        f = p.font(); f.setBold(True); f.setPixelSize(6); p.setFont(f)
        p.drawText(QRectF(12.5, 8, 9, 8), Qt.AlignmentFlag.AlignCenter,
                   "÷8" if k == "scale_to_nether" else "×8")
        p.setPen(Qt.PenStyle.NoPen)
    else:
        p.drawEllipse(QPointF(12, 12), 3, 3)


def _pin(p: QPainter, col: QColor):
    """Spawn: a map pin instead of a badge."""
    path = QPainterPath()
    path.moveTo(12, 22.5)
    path.cubicTo(7.5, 16.5, 4.5, 13, 4.5, 9.5)
    path.arcTo(QRectF(4.5, 2, 15, 15), 180, -180)
    path.cubicTo(19.5, 13, 16.5, 16.5, 12, 22.5)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(col); p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#ffffff")); p.drawEllipse(QPointF(12, 9.5), 3, 3)


def _detail(c: QColor) -> QColor:
    """Colour of doors / windows / engravings inside a silhouette."""
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return c.darker(260) if lum > 150 else c.lighter(190) if lum < 70 else c.darker(210)


def pixmap(key: str, size: int = 20, dpr: float = 2.0) -> QPixmap:
    """The structure's own silhouette, in its colour, with a dark outline
    (sticker style: legible on every biome), highlight and detail cut-outs."""
    k = canon(key)
    ck = (k, int(size), dpr)
    pm = _CACHE.get(ck)
    if pm is not None:
        return pm
    px = max(1, int(round(size * dpr)))
    col = QColor(color(k))
    # 1) the silhouette alone
    art = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    art.fill(Qt.GlobalColor.transparent)
    p = QPainter(art)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # the pictograms live in [3, 21] of the 24-unit grid (the pin in [1.5, 23]);
    # that box fills the icon, with a margin for the outline
    lo, hi = (1.5, 23.0) if k == "spawn" else (3.0, 21.0)
    pad = 1.7
    unit = px / (hi - lo + 2 * pad)
    p.scale(unit, unit); p.translate(pad - lo, pad - lo)
    if k == "spawn":
        _pin(p, col)
    elif k == "origin":
        _glyph(k, p, QColor("#ffffff"), col)
    else:
        _glyph(k, p, col, _detail(col))
        # soft top highlight, clipped to the silhouette
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 46)); p.drawRect(QRectF(0, 0, 24, 10.5))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    p.end()
    # 2) dark outline = the silhouette's alpha, dilated
    shadow = QImage(art)
    sp = QPainter(shadow)
    sp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    sp.fillRect(shadow.rect(), QColor(12, 14, 20, 225))
    sp.end()
    out = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    op = QPainter(out)
    r = max(1.0, unit * 1.05)
    import math
    for a in range(0, 360, 30):
        op.drawImage(QPointF(r * math.cos(math.radians(a)), r * math.sin(math.radians(a))), shadow)
    op.drawImage(0, 0, art)
    op.end()
    pm = QPixmap.fromImage(out)
    pm.setDevicePixelRatio(dpr)
    _CACHE[ck] = pm
    return pm


def icon(key: str, size: int = 20) -> QIcon:
    return QIcon(pixmap(key, size))


def clear_cache():
    _CACHE.clear()
