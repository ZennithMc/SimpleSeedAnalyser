"""
qtui/news_icons.py — (19/11) the icons of Home's « What's new »: one real picture per kind
of change (a chest for the loot, a blueprint for the plans, a beetle for the fixes…),
in the same sticker style as the structure icons (colour, top highlight, dark outline).

    tile(title, size)  → the row's tile (tinted square + its icon)
    pick(title)        → (kind, colour) chosen from the title's words
Structures reuse qtui/struct_icons.py (a village for « Villages », the portal…).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

# words of the title → kind of icon, colour (first match wins)
RULES = (
    (("bug",), "bug", "#e5484d"),
    (("loot tab", "reverse"), "chest_search", "#e2ad2c"),
    (("seed library",), "icon:seed_library", "#e2ad2c"),        # (20/11) SSA's own icons
    (("interface",), "spark", "#9b7cf5"),
    (("chest", "loot"), "struct:buried_treasure", "#e2ad2c"),
    (("plan",), "blueprint", "#4a8ee8"),
    (("crack",), "tree", "#4caf50"),
    (("ruined portal",), "struct:ruined_portal", "#8a5ae0"),
    (("village",), "struct:village", "#d9b27c"),
    (("spawn",), "struct:spawn", "#e5484d"),
    (("nether",), "nether_end", "#c4553f"),
    (("detail",), "house_search", "#d9b27c"),
    (("climate",), "struct:climate_minmax", "#e0604a"),
    (("stronghold",), "struct:stronghold", "#2f8f62"),
    (("camp",), "struct:abandoned_camp", "#8f969d"),
    (("crunch",), "struct:crunchiness", "#c77ddb"),
    (("noise",), "struct:noise_sample", "#4aa0e0"),
    (("exact y", "height"), "struct:precise_y", "#8fa37a"),
    (("statist",), "chart", "#3fb47a"),
    (("speed", "faster", "fast", "gpu"), "bolt", "#f5b82e"),
    (("draw",), "brush", "#e5689a"),
    (("theme", "custom", "colour", "palette"), "palette", "#c98a4a"),
    (("setting",), "gear", "#8e98ab"),
    (("condition",), "conditions", "#5aa8ff"),
    (("session",), "icon:hm_sessions", "#5aa8ff"),
    (("map",), "icon:hm_map", "#3fb47a"),
)
_CACHE: dict = {}


def pick(title: str):
    t = (title or "").lower()
    for words, kind, col in RULES:
        if any(w in t for w in words):
            return kind, col
    return "spark", "#f5c242"


def _poly(*pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


# ── the pictures, in a 24 × 24 box ───────────────────────────────────────────
def _draw(kind, p):
    p.setPen(Qt.PenStyle.NoPen)
    if kind == "bug":                                  # a red beetle, its legs, a green tick
        leg = QPen(QColor("#3a2226"), 1.3)
        leg.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(leg)
        for y, d in ((9.5, -2.5), (13, 0), (16.5, 2.5)):
            p.drawLine(QPointF(7.5, y), QPointF(4.2, y + d))
            p.drawLine(QPointF(16.5, y), QPointF(19.8, y + d))
        p.drawLine(QPointF(10.4, 5.4), QPointF(8.6, 3.2))
        p.drawLine(QPointF(13.6, 5.4), QPointF(15.4, 3.2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#3a2226"))
        p.drawEllipse(QRectF(9, 4.4, 6, 4.8))
        p.setBrush(QColor("#e5484d"))
        p.drawEllipse(QRectF(6.6, 7.4, 10.8, 12.6))
        p.setBrush(QColor("#3a2226"))
        p.drawRect(QRectF(11.5, 7.6, 1.0, 12))
        for x, y in ((9.2, 11), (14.8, 11), (9.6, 15.5), (14.4, 15.5)):
            p.drawEllipse(QPointF(x, y), 1.1, 1.1)
        p.setBrush(QColor("#2fb36a"))                  # fixed: a tick in a green disc
        p.drawEllipse(QRectF(13.4, 13.8, 8.4, 8.4))
        tick = QPen(QColor("#ffffff"), 1.5)
        tick.setCapStyle(Qt.PenCapStyle.RoundCap)
        tick.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(tick)
        p.drawPolyline(_poly((15.4, 18.1), (17.1, 19.7), (19.9, 16.4)))
    elif kind == "blueprint":                          # a blue sheet: rooms, the chests in yellow
        p.setBrush(QColor("#2f6fd0"))
        p.drawRoundedRect(QRectF(3.5, 4, 17, 16), 1.6, 1.6)
        p.setBrush(QColor("#4a8ee8"))
        p.drawRoundedRect(QRectF(3.5, 4, 17, 3.2), 1.6, 1.6)
        pen = QPen(QColor("#e8f2ff"), 1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(6, 9, 5.5, 4.5))
        p.drawRect(QRectF(11.5, 9, 6.5, 8.5))
        p.drawRect(QRectF(6, 13.5, 5.5, 4))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffd23f"))
        p.drawRect(QRectF(14.2, 11.2, 2, 2))
        p.drawRect(QRectF(7.4, 14.8, 2, 2))
    elif kind == "tree":                               # an oak (the cracker's trees) and a key
        p.setBrush(QColor("#7a5232"))
        p.drawRect(QRectF(10.6, 12.5, 2.8, 8))
        p.setBrush(QColor("#3d8b40"))
        p.drawEllipse(QRectF(4.5, 6.5, 9, 8))
        p.drawEllipse(QRectF(10.5, 6.5, 9, 8))
        p.setBrush(QColor("#4caf50"))
        p.drawEllipse(QRectF(7, 2.8, 10, 9))
        p.setBrush(QColor("#6fcf6f"))
        p.drawEllipse(QRectF(9, 4.2, 3.5, 3))
        p.setBrush(QColor("#f5c242"))                  # the key
        p.drawEllipse(QRectF(13.6, 14.2, 5, 5))
        p.drawRect(QRectF(17.6, 16, 4, 1.6))
        p.drawRect(QRectF(20, 17.2, 1.4, 2))
        p.setBrush(QColor("#9a6a10"))
        p.drawEllipse(QRectF(15.3, 15.9, 1.6, 1.6))
    elif kind == "conditions":                         # a card of conditions + a plus
        p.setBrush(QColor("#e8ecf3"))
        p.drawRoundedRect(QRectF(3.5, 4, 14.5, 16), 2, 2)
        for i, c in enumerate(("#58a64a", "#e2c46a", "#7a3fe0")):
            y = 7.5 + i * 4.2
            p.setBrush(QColor(c))
            p.drawEllipse(QPointF(6.6, y), 1.5, 1.5)
            p.setBrush(QColor("#9aa3b8"))
            p.drawRoundedRect(QRectF(9.2, y - 0.8, 6.5, 1.6), 0.8, 0.8)
        p.setBrush(QColor("#2fb36a"))
        p.drawEllipse(QRectF(13, 13, 9, 9))
        p.setBrush(QColor("#ffffff"))
        p.drawRect(QRectF(16.8, 14.9, 1.4, 5.2))
        p.drawRect(QRectF(14.9, 16.8, 5.2, 1.4))
    elif kind == "palette":                            # a painter's palette, its colours
        path = QPainterPath()
        path.addEllipse(QRectF(3, 5, 18, 14))
        hole = QPainterPath()
        hole.addEllipse(QRectF(13.2, 13.2, 3.4, 3))
        p.setBrush(QColor("#d9a066"))
        p.drawPath(path.subtracted(hole))
        for (x, y), c in zip(((7, 9), (11, 7.4), (15.4, 8.6), (6.8, 13.6)),
                             ("#e5484d", "#ffd23f", "#4a8ee8", "#3fb47a")):
            p.setBrush(QColor(c))
            p.drawEllipse(QPointF(x, y), 1.9, 1.9)
    elif kind == "bolt":                               # quicker: a lightning bolt
        p.setBrush(QColor("#f5b82e"))
        p.drawPolygon(_poly((13.6, 2.6), (5.6, 13.6), (11, 13.6), (9.6, 21.4), (18.4, 9.8), (12.8, 9.8)))
        p.setBrush(QColor("#ffe08a"))
        p.drawPolygon(_poly((13.6, 2.6), (8.6, 9.6), (11.2, 9.6)))
    elif kind == "brush":                              # a brush and its stroke
        p.setBrush(QColor("#e5689a"))
        path = QPainterPath()
        path.moveTo(3.5, 20)
        path.cubicTo(5, 15.5, 8.5, 18, 10.5, 15)
        path.lineTo(12, 16.6)
        path.cubicTo(10.4, 19.6, 6.6, 21.4, 3.5, 20)
        p.drawPath(path)
        p.setBrush(QColor("#c9cfd8"))
        p.drawPolygon(_poly((10.2, 13.6), (13.4, 16.8), (15.6, 14.6), (12.4, 11.4)))
        p.setBrush(QColor("#a8743c"))
        p.drawPolygon(_poly((12.4, 11.4), (15.6, 14.6), (21.2, 5.6), (20.4, 3.8), (18.4, 3.6)))
    elif kind == "gear":                               # settings
        path = QPainterPath()
        for i in range(16):
            a0 = (i - 0.5) * math.pi / 8.0
            r = 9.4 if i % 2 == 0 else 7.0
            for kk in (0, 1):
                a = a0 + kk * math.pi / 8.0
                pt = QPointF(12 + r * math.cos(a), 12 + r * math.sin(a))
                if i == 0 and kk == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
        path.closeSubpath()
        hole = QPainterPath()
        hole.addEllipse(QPointF(12, 12), 3.0, 3.0)
        p.setBrush(QColor("#8e98ab"))
        p.drawPath(path.subtracted(hole))
    elif kind == "chart":                              # statistics: bars, rising
        for i, (h, c) in enumerate(((6, "#4a8ee8"), (11, "#3fb47a"), (15, "#f5b82e"))):
            p.setBrush(QColor(c))
            p.drawRoundedRect(QRectF(5 + i * 5.2, 20 - h, 4, h), 1, 1)
        p.setBrush(QColor("#c9cfd8"))
        p.drawRect(QRectF(3.5, 20, 17, 1.4))
    elif kind == "spark":                              # something new: a star
        path = QPainterPath()
        path.moveTo(12, 3)
        for a, b in (((13.4, 10.6), (21, 12)), ((13.4, 13.4), (12, 21)), ((10.6, 13.4), (3, 12)),
                     ((10.6, 10.6), (12, 3))):
            path.quadTo(QPointF(*a), QPointF(*b))
        p.setBrush(QColor("#f5c242"))
        p.drawPath(path)
    elif kind == "magnifier":
        ring = QPen(QColor("#e8ecf3"), 2.4)
        p.setPen(ring)
        p.setBrush(QColor(120, 180, 255, 90))
        p.drawEllipse(QRectF(5, 5, 10, 10))
        h = QPen(QColor("#8a5a2a"), 3.2)
        h.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(h)
        p.drawLine(QPointF(14.6, 14.6), QPointF(20, 20))


def _sticker_image(kind, px):
    """The picture with the structure icons' look: top highlight, dark outline."""
    art = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    art.fill(Qt.GlobalColor.transparent)
    p = QPainter(art)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pad = 1.6
    unit = px / (24 + 2 * pad)
    p.scale(unit, unit)
    p.translate(pad, pad)
    _draw(kind, p)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 40))
    p.drawRect(QRectF(0, 0, 24, 10.5))
    p.end()
    shadow = QImage(art)
    sp = QPainter(shadow)
    sp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    sp.fillRect(shadow.rect(), QColor(12, 14, 20, 225))
    sp.end()
    out = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    op = QPainter(out)
    r = max(1.0, unit * 1.0)
    for a in range(0, 360, 30):
        op.drawImage(QPointF(r * math.cos(math.radians(a)), r * math.sin(math.radians(a))), shadow)
    op.drawImage(0, 0, art)
    op.end()
    return out


def sticker(kind: str, size: int, dpr: float = 2.0) -> QPixmap:
    key = (kind, size, dpr)
    pm = _CACHE.get(key)
    if pm is not None:
        return pm
    px = max(1, int(round(size * dpr)))
    if kind.startswith("struct:"):
        from . import struct_icons
        pm = struct_icons.pixmap(kind[7:], size, dpr)
    elif kind.startswith("icon:"):                     # (20/11) one of SSA's icons, in the rule's colour
        from . import icons
        name = kind[5:]
        col = next((c for _w, k, c in RULES if k == kind), "#f5c242")
        pm = icons.pixmap(name, size, col, dpr)
    elif kind in ("chest_search", "house_search"):     # a structure + a magnifier over it
        from . import struct_icons
        base = struct_icons.pixmap("buried_treasure" if kind == "chest_search" else "village", size, dpr)
        img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(QRectF(0, 0, px * 0.86, px * 0.86), base, QRectF(base.rect()))
        mg = _sticker_image("magnifier", int(px * 0.62))
        p.drawImage(QPointF(px * 0.40, px * 0.40), mg)
        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
    elif kind == "nether_end":                         # the two other dimensions together
        from . import struct_icons
        a = struct_icons.pixmap("fortress", size, dpr)
        b = struct_icons.pixmap("end_city", size, dpr)
        img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(QRectF(0, 0, px * 0.72, px * 0.72), a, QRectF(a.rect()))
        p.drawPixmap(QRectF(px * 0.30, px * 0.30, px * 0.70, px * 0.70), b, QRectF(b.rect()))
        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
    else:
        pm = QPixmap.fromImage(_sticker_image(kind, px))
        pm.setDevicePixelRatio(dpr)
    _CACHE[key] = pm
    return pm


def tile(title: str, size: int = 32, dpr: float = 2.0):
    """The tile of a row of « What's new »: a square tinted with the icon's colour, the icon."""
    kind, col = pick(title)
    key = ("tile", kind, size, dpr)
    pm = _CACHE.get(key)
    if pm is not None:
        return pm, col
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(col)
    c.setAlphaF(0.18)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    edge = QColor(col)
    edge.setAlphaF(0.35)
    p.setPen(QPen(edge, 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * 0.28, size * 0.28)
    isz = size * 0.80
    ic = sticker(kind, int(round(isz)), dpr)
    o = (size - isz) / 2
    p.drawPixmap(QRectF(o, o, isz, isz), ic, QRectF(ic.rect()))
    p.end()
    _CACHE[key] = pm
    return pm, col
