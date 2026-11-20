"""
qtui/icons.py — small vector line-icon set drawn with QPainter.

Replaces the emojis of the Tk UI (inconsistent from one system font to the next,
blurry, colourful) with one coherent set: 1.6-px strokes on a 24-px grid, drawn
at any size and DPI, recoloured from theme tokens.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme

_CACHE: dict = {}


def _pen(p: QPainter, color: QColor, w: float = 1.6):
    pen = QPen(color, w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _draw(name: str, p: QPainter, c: QColor):
    _pen(p, c)
    if name == "map":
        path = QPainterPath()
        path.moveTo(3, 6); path.lineTo(9, 4); path.lineTo(15, 6); path.lineTo(21, 4)
        path.lineTo(21, 18); path.lineTo(15, 20); path.lineTo(9, 18); path.lineTo(3, 20); path.closeSubpath()
        p.drawPath(path); p.drawLine(9, 4, 9, 18); p.drawLine(15, 6, 15, 20)
    elif name == "search":
        p.drawEllipse(QPointF(10.5, 10.5), 6, 6); p.drawLine(15, 15, 20.5, 20.5)
    elif name == "sessions":         # conditions list + funnel
        for y in (6, 12, 18):
            p.drawEllipse(QPointF(5, y), 1.2, 1.2); p.drawLine(9, y, 20, y)
    elif name == "eye":
        path = QPainterPath(); path.moveTo(2.5, 12)
        path.quadTo(12, 3, 21.5, 12); path.quadTo(12, 21, 2.5, 12); p.drawPath(path)
        p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
    elif name == "analyze":
        p.drawLine(4, 20, 20, 20)
        for x, h in ((7, 7), (12, 12), (17, 5)):
            p.drawRoundedRect(QRectF(x - 1.8, 20 - h - 1, 3.6, h), 1, 1)
    elif name == "brush":
        p.drawLine(19, 4, 10.5, 12.5)
        path = QPainterPath(); path.moveTo(10, 12); path.cubicTo(6, 12, 6, 16, 5, 19)
        path.cubicTo(9, 19, 12, 18, 12, 14); p.drawPath(path)
    elif name == "dice":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 3.5, 3.5)
        p.setBrush(c)
        for x, y in ((8.5, 8.5), (15.5, 15.5), (12, 12)):
            p.drawEllipse(QPointF(x, y), 1.1, 1.1)
    elif name == "copy":
        p.drawRoundedRect(QRectF(8, 8, 12, 12), 2.5, 2.5)
        p.drawPolyline([QPointF(16, 5), QPointF(16, 4.5), QPointF(4.5, 4.5), QPointF(4.5, 16), QPointF(5, 16)])
    elif name == "hits":             # a zone (dashed square) and a hit marker
        pen = QPen(c, 1.4); pen.setStyle(Qt.PenStyle.DashLine); pen.setDashPattern([2.2, 2.0])
        p.setPen(pen); p.drawRect(QRectF(3.5, 3.5, 12, 12))
        _pen(p, c)
        p.drawEllipse(QPointF(15.5, 15.5), 3.6, 3.6)
        p.drawLine(QPointF(15.5, 9.5), QPointF(15.5, 11)); p.drawLine(QPointF(15.5, 20), QPointF(15.5, 21.5))
        p.drawLine(QPointF(9.5, 15.5), QPointF(11, 15.5)); p.drawLine(QPointF(20, 15.5), QPointF(21.5, 15.5))
    elif name == "target":
        p.drawEllipse(QPointF(12, 12), 7, 7)
        for a, b in (((12, 2), (12, 6)), ((12, 18), (12, 22)), ((2, 12), (6, 12)), ((18, 12), (22, 12))):
            p.drawLine(QPointF(*a), QPointF(*b))
    elif name == "plus":
        p.drawLine(12, 5, 12, 19); p.drawLine(5, 12, 19, 12)
    elif name == "minus":
        p.drawLine(5, 12, 19, 12)
    elif name == "home":
        path = QPainterPath(); path.moveTo(4, 11); path.lineTo(12, 4); path.lineTo(20, 11)
        p.drawPath(path); p.drawPolyline([QPointF(6, 10), QPointF(6, 20), QPointF(18, 20), QPointF(18, 10)])
    elif name == "layers":
        for dy in (0, 5):
            path = QPainterPath(); path.moveTo(12, 4 + dy); path.lineTo(21, 9 + dy)
            path.lineTo(12, 14 + dy); path.lineTo(3, 9 + dy); path.closeSubpath(); p.drawPath(path)
    elif name == "gpu":
        p.drawRoundedRect(QRectF(5, 5, 14, 14), 2.5, 2.5)
        p.drawRect(QRectF(9, 9, 6, 6))
        for t in (8.5, 12, 15.5):
            p.drawLine(QPointF(t, 2), QPointF(t, 5)); p.drawLine(QPointF(t, 19), QPointF(t, 22))
            p.drawLine(QPointF(2, t), QPointF(5, t)); p.drawLine(QPointF(19, t), QPointF(22, t))
    elif name == "play":
        path = QPainterPath(); path.moveTo(8, 5); path.lineTo(19, 12); path.lineTo(8, 19); path.closeSubpath()
        p.setBrush(c); p.drawPath(path)
    elif name == "stop":
        p.setBrush(c); p.drawRoundedRect(QRectF(6.5, 6.5, 11, 11), 2, 2)
    elif name == "legacy":           # "classic UI" window icon
        p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2.5, 2.5); p.drawLine(3.5, 9, 20.5, 9)
    elif name in ("undo", "redo"):
        path = QPainterPath()
        if name == "undo":
            path.moveTo(9, 6); path.lineTo(4.5, 10); path.lineTo(9, 14)
            p.drawPath(path)
            arc = QPainterPath(); arc.moveTo(5, 10); arc.lineTo(14, 10)
            arc.cubicTo(18.5, 10, 20, 13, 20, 15); arc.cubicTo(20, 17.5, 18, 19, 15, 19)
            p.drawPath(arc)
        else:
            path.moveTo(15, 6); path.lineTo(19.5, 10); path.lineTo(15, 14)
            p.drawPath(path)
            arc = QPainterPath(); arc.moveTo(19, 10); arc.lineTo(10, 10)
            arc.cubicTo(5.5, 10, 4, 13, 4, 15); arc.cubicTo(4, 17.5, 6, 19, 9, 19)
            p.drawPath(arc)
    elif name == "library":          # (20/11) books on a shelf: tinted covers, spine bands, one leaning
        tint = QColor(c)
        tint.setAlphaF(0.2)
        p.setBrush(tint)
        p.drawRoundedRect(QRectF(3.4, 4.2, 4.6, 15.6), 1.3, 1.3)          # a tall book
        p.drawRoundedRect(QRectF(9.2, 7, 4.4, 12.8), 1.3, 1.3)            # a shorter one
        path = QPainterPath(); path.moveTo(15.1, 7.9); path.lineTo(18.9, 6.9); path.lineTo(21.9, 18.6)
        path.lineTo(18.1, 19.6); path.closeSubpath(); p.drawPath(path)    # leaning on them
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, c, 1.2)
        p.drawLine(QPointF(3.6, 7.2), QPointF(7.8, 7.2)); p.drawLine(QPointF(3.6, 16.6), QPointF(7.8, 16.6))
        p.drawRoundedRect(QRectF(10.3, 10.2, 2.2, 3.4), 0.6, 0.6)        # its label
        p.drawLine(QPointF(15.9, 10.6), QPointF(19.6, 9.6))
        _pen(p, c, 1.6)
        p.drawLine(QPointF(2, 20.6), QPointF(22, 20.6))                     # the shelf
    elif name == "seed_library":     # (20/11) the Seed Library: an album with a star, a ribbon
        tint = QColor(c)
        tint.setAlphaF(0.2)
        p.setBrush(tint)
        p.drawRoundedRect(QRectF(4.5, 3, 15, 17.5), 2.4, 2.4)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(8, 3.4), QPointF(8, 20.1))                       # the spine
        star = QPainterPath()
        import math as _m
        for i in range(10):
            a = -_m.pi / 2 + i * _m.pi / 5
            rr = 3.9 if i % 2 == 0 else 1.65
            pt = QPointF(13.9 + rr * _m.cos(a), 11.3 + rr * _m.sin(a))
            star.moveTo(pt) if i == 0 else star.lineTo(pt)
        star.closeSubpath()
        _pen(p, c, 1.0)
        p.setBrush(c)
        p.drawPath(star)
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, c, 1.6)
        rib = QPainterPath(); rib.moveTo(14.6, 20.5); rib.lineTo(14.6, 23); rib.lineTo(16.1, 21.9)
        rib.lineTo(17.6, 23); rib.lineTo(17.6, 20.5)
        p.drawPath(rib)                                                     # the bookmark
    elif name == "keyboard":
        p.drawRoundedRect(QRectF(2.5, 6.5, 19, 11), 2.5, 2.5)
        p.setBrush(c)
        for x in (6, 9.3, 12.6, 16):
            p.drawRect(QRectF(x - 0.8, 9.2, 1.6, 1.6))
        p.drawRect(QRectF(7.5, 13.2, 9, 1.6))
    elif name == "palette":
        path = QPainterPath(); path.moveTo(12, 3.5)
        path.cubicTo(6.5, 3.5, 3.5, 7.5, 3.5, 12); path.cubicTo(3.5, 16.8, 7.2, 20.5, 11.5, 20.5)
        path.cubicTo(13.5, 20.5, 13.6, 18.6, 12.6, 17.6); path.cubicTo(11.6, 16.5, 12.4, 15, 14, 15)
        path.lineTo(16, 15); path.cubicTo(18.7, 15, 20.5, 13, 20.5, 10.5)
        path.cubicTo(20.5, 6.5, 16.7, 3.5, 12, 3.5); p.drawPath(path)
        p.setBrush(c)
        for x, y in ((8, 11), (10.5, 7.5), (15, 7.8)):
            p.drawEllipse(QPointF(x, y), 1.2, 1.2)
    elif name == "chevron-left":
        p.drawPolyline([QPointF(15, 6), QPointF(9, 12), QPointF(15, 18)])
    elif name == "chevron-right":
        p.drawPolyline([QPointF(9, 6), QPointF(15, 12), QPointF(9, 18)])
    elif name == "chevron-down":
        p.drawPolyline([QPointF(6, 9), QPointF(12, 15), QPointF(18, 9)])
    elif name == "chevron-up":
        p.drawPolyline([QPointF(6, 15), QPointF(12, 9), QPointF(18, 15)])
    elif name == "close":
        p.drawLine(6, 6, 18, 18); p.drawLine(18, 6, 6, 18)
    elif name == "pause":
        p.setBrush(c)
        p.drawRoundedRect(QRectF(7, 5.5, 3.2, 13), 1, 1); p.drawRoundedRect(QRectF(13.8, 5.5, 3.2, 13), 1, 1)
    elif name == "trash":
        p.drawLine(4.5, 7, 19.5, 7); p.drawPolyline([QPointF(9.5, 7), QPointF(10, 4.5), QPointF(14, 4.5), QPointF(14.5, 7)])
        path = QPainterPath(); path.moveTo(6.5, 7); path.lineTo(7.5, 20); path.lineTo(16.5, 20)
        path.lineTo(17.5, 7); p.drawPath(path); p.drawLine(10.5, 10.5, 10.5, 16.5); p.drawLine(13.5, 10.5, 13.5, 16.5)
    elif name == "trash-all":        # a bin with a cross: everything (08/10)
        p.drawLine(4.5, 7, 19.5, 7); p.drawPolyline([QPointF(9.5, 7), QPointF(10, 4.5), QPointF(14, 4.5), QPointF(14.5, 7)])
        path = QPainterPath(); path.moveTo(6.5, 7); path.lineTo(7.5, 20); path.lineTo(16.5, 20)
        path.lineTo(17.5, 7); p.drawPath(path)
        p.drawLine(QPointF(9.5, 10.5), QPointF(14.5, 16.5)); p.drawLine(QPointF(14.5, 10.5), QPointF(9.5, 16.5))
    elif name == "more":             # three dots (08/10)
        p.setBrush(c)
        for x in (6.0, 12.0, 18.0):
            p.drawEllipse(QPointF(x, 12), 1.6, 1.6)
    elif name == "edit":
        path = QPainterPath(); path.moveTo(15.5, 4.5); path.lineTo(19.5, 8.5); path.lineTo(9, 19)
        path.lineTo(4.5, 19.5); path.lineTo(5, 15); path.closeSubpath(); p.drawPath(path)
        p.drawLine(13.5, 6.5, 17.5, 10.5)
    elif name == "chest":            # (06/11) Loot: a chest, lid seam and latch
        p.drawRoundedRect(QRectF(3.5, 5.5, 17, 14), 2, 2)
        p.drawLine(QPointF(3.5, 10.5), QPointF(20.5, 10.5))
        p.drawRoundedRect(QRectF(10.3, 8.8, 3.4, 4.4), 0.8, 0.8)
    elif name == "folder":
        path = QPainterPath(); path.moveTo(3.5, 7); path.lineTo(3.5, 18.5); path.lineTo(20.5, 18.5)
        path.lineTo(20.5, 8.5); path.lineTo(11.5, 8.5); path.lineTo(9.5, 6); path.lineTo(4, 6)
        path.closeSubpath(); p.drawPath(path)
    elif name == "save":
        p.drawRoundedRect(QRectF(4.5, 4.5, 15, 15), 2, 2)
        p.drawRect(QRectF(8, 4.5, 8, 5)); p.drawRect(QRectF(7.5, 13, 9, 6.5))
    elif name == "paste":
        p.drawRoundedRect(QRectF(5, 5.5, 14, 15), 2, 2); p.drawRoundedRect(QRectF(9, 3.5, 6, 4), 1, 1)
        p.drawLine(9, 12, 15, 12); p.drawLine(9, 15.5, 13, 15.5)
    elif name == "check":
        p.drawPolyline([QPointF(5, 12.5), QPointF(10, 17.5), QPointF(19, 7)])
    elif name == "forest":           # (05/11) Seedcracking: a block tree
        path = QPainterPath()
        path.moveTo(8, 4); path.lineTo(16, 4); path.lineTo(16, 7); path.lineTo(20, 7)
        path.lineTo(20, 14); path.lineTo(4, 14); path.lineTo(4, 7); path.lineTo(8, 7); path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(12, 14), QPointF(12, 20.5)); p.drawLine(QPointF(8, 20.5), QPointF(16, 20.5))
    # ── (20/11) Home's five tiles: richer duotone pictures (the top bar keeps the plain ones) ──
    elif name.startswith("hm_"):
        _home_icon(name, p, c)
    elif name == "tree":
        p.drawEllipse(QPointF(6, 6), 2, 2); p.drawEllipse(QPointF(14, 12), 2, 2); p.drawEllipse(QPointF(14, 19), 2, 2)
        p.drawPolyline([QPointF(6, 8), QPointF(6, 19), QPointF(12, 19)]); p.drawLine(6, 12, 12, 12)
    # ── Draw page tools ─────────────────────────────────────────────────────
    elif name == "eraser":
        path = QPainterPath(); path.moveTo(4, 15.5); path.lineTo(12.5, 7); path.lineTo(19.5, 14)
        path.lineTo(13.5, 20); path.lineTo(8.5, 20); path.closeSubpath(); p.drawPath(path)
        p.drawLine(QPointF(8.2, 11.3), QPointF(15.2, 18.3)); p.drawLine(QPointF(13.5, 20), QPointF(20.5, 20))
    elif name == "fill":             # paint bucket
        path = QPainterPath(); path.moveTo(10, 4.5); path.lineTo(18, 12.5); path.lineTo(11.5, 19)
        path.lineTo(3.5, 11); path.closeSubpath(); p.drawPath(path)
        p.drawLine(QPointF(3.5, 11), QPointF(17.5, 11))
        p.setBrush(c)
        drop = QPainterPath(); drop.moveTo(20, 14); drop.cubicTo(18.3, 16.5, 18.3, 18.5, 20, 18.5)
        drop.cubicTo(21.7, 18.5, 21.7, 16.5, 20, 14); p.drawPath(drop)
    elif name == "rect":
        p.drawRoundedRect(QRectF(4, 6, 16, 12), 1.5, 1.5)
    elif name == "ellipse":
        p.drawEllipse(QPointF(12, 12), 8.5, 6.5)
    elif name == "circle":
        p.drawEllipse(QPointF(12, 12), 7.5, 7.5)
        p.setBrush(c); p.drawEllipse(QPointF(12, 12), 1.4, 1.4)
    elif name == "line":
        p.drawLine(QPointF(5, 19), QPointF(19, 5))
        p.setBrush(c); p.drawEllipse(QPointF(5, 19), 1.6, 1.6); p.drawEllipse(QPointF(19, 5), 1.6, 1.6)
    elif name == "picker":           # eyedropper
        p.drawLine(QPointF(4.5, 19.5), QPointF(13, 11))
        path = QPainterPath(); path.moveTo(11, 9); path.lineTo(15, 13); p.drawPath(path)
        p.drawEllipse(QPointF(16.8, 7.2), 3, 3)
        p.drawLine(QPointF(4.5, 19.5), QPointF(6, 18))
    elif name == "weight":           # a kettlebell-like weight
        p.drawEllipse(QPointF(12, 14.5), 6.5, 5.5)
        p.drawPolyline([QPointF(8.5, 10.2), QPointF(8.5, 5.5), QPointF(15.5, 5.5), QPointF(15.5, 10.2)])
        p.drawLine(QPointF(10, 14.5), QPointF(14, 14.5)); p.drawLine(QPointF(12, 12.5), QPointF(12, 16.5))
    elif name == "hand":
        path = QPainterPath()
        path.moveTo(8, 12); path.lineTo(8, 6); path.cubicTo(8, 4.5, 10, 4.5, 10, 6); path.lineTo(10, 11)
        path.moveTo(10, 6); path.lineTo(10, 4.8); path.cubicTo(10, 3.3, 12, 3.3, 12, 4.8); path.lineTo(12, 11)
        path.moveTo(12, 5.5); path.cubicTo(12, 4, 14, 4, 14, 5.5); path.lineTo(14, 11)
        path.moveTo(14, 7); path.cubicTo(14, 5.5, 16, 5.5, 16, 7); path.lineTo(16, 14)
        path.cubicTo(16, 18, 14, 20.5, 11, 20.5); path.cubicTo(8.5, 20.5, 7.5, 19, 6, 16.5)
        path.lineTo(4.3, 13.4); path.cubicTo(3.6, 12, 5.5, 11, 6.5, 12.3); path.lineTo(8, 14)
        p.drawPath(path)
    elif name == "grid":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 1.5, 1.5)
        for t in (9.33, 14.67):
            p.drawLine(QPointF(t, 4), QPointF(t, 20)); p.drawLine(QPointF(4, t), QPointF(20, t))
    elif name == "flip":
        p.drawLine(QPointF(12, 3.5), QPointF(12, 20.5))
        path = QPainterPath(); path.moveTo(9.5, 6.5); path.lineTo(3.5, 17.5); path.lineTo(9.5, 17.5)
        path.closeSubpath(); p.drawPath(path)
        p.setBrush(c)
        path = QPainterPath(); path.moveTo(14.5, 6.5); path.lineTo(20.5, 17.5); path.lineTo(14.5, 17.5)
        path.closeSubpath(); p.drawPath(path)
    elif name == "flipv":
        p.save(); p.translate(12, 12); p.rotate(90); p.translate(-12, -12)
        _draw("flip", p, c); p.restore()
    elif name == "rotate":
        path = QPainterPath(); path.moveTo(18.5, 12)
        path.arcTo(QRectF(5.5, 5.5, 13, 13), 0, 270); p.drawPath(path)
        p.drawPolyline([QPointF(16, 3.5), QPointF(12, 5.5), QPointF(14.5, 9)])
    elif name == "seedcopy":         # a map square with an arrow into a grid
        p.drawRoundedRect(QRectF(3.5, 3.5, 9, 9), 1.5, 1.5)
        p.drawRect(QRectF(12, 12, 8.5, 8.5)); p.drawLine(QPointF(16.25, 12), QPointF(16.25, 20.5))
        p.drawLine(QPointF(12, 16.25), QPointF(20.5, 16.25))
        p.drawLine(QPointF(9, 9), QPointF(14.5, 14.5)); p.drawPolyline([QPointF(14.5, 11), QPointF(14.5, 14.5), QPointF(11, 14.5)])
    elif name == "crosshair":
        p.drawEllipse(QPointF(12, 12), 5.5, 5.5)
        for a, b in (((12, 2.5), (12, 8)), ((12, 16), (12, 21.5)), ((2.5, 12), (8, 12)), ((16, 12), (21.5, 12))):
            p.drawLine(QPointF(*a), QPointF(*b))
        p.setBrush(c); p.drawEllipse(QPointF(12, 12), 1.2, 1.2)
    elif name == "ruler":            # a diagonal ruler with ticks
        path = QPainterPath()
        path.moveTo(3, 16.5); path.lineTo(16.5, 3); path.lineTo(21, 7.5); path.lineTo(7.5, 21); path.closeSubpath()
        p.drawPath(path)
        for i, ln in enumerate((3.2, 2.0, 3.2, 2.0)):
            x, y = 7.2 + i * 2.9, 12.3 - i * 2.9
            p.drawLine(QPointF(x, y), QPointF(x + ln * 0.7, y + ln * 0.7))
    elif name == "tools":            # wrench
        path = QPainterPath()
        path.moveTo(14.5, 4.2); path.cubicTo(11.5, 4.2, 9.8, 7.2, 10.9, 9.9)
        path.lineTo(4.2, 16.6); path.cubicTo(3.2, 17.6, 3.2, 19.3, 4.4, 20.1)
        path.cubicTo(5.3, 20.8, 6.6, 20.6, 7.4, 19.8); path.lineTo(14.1, 13.1)
        path.cubicTo(16.8, 14.2, 19.8, 12.5, 19.8, 9.5); path.lineTo(16.7, 12.0)
        path.lineTo(13.6, 10.4); path.lineTo(12.4, 7.6); path.closeSubpath()
        p.drawPath(path)
    elif name == "fullscreen":
        for (x, y, dx, dy) in ((4, 4, 1, 1), (20, 4, -1, 1), (4, 20, 1, -1), (20, 20, -1, -1)):
            p.drawPolyline([QPointF(x, y + 5 * dy), QPointF(x, y), QPointF(x + 5 * dx, y)])
    elif name == "legend":           # icon rows + lines
        for y in (6.5, 12, 17.5):
            p.drawRoundedRect(QRectF(3.5, y - 2, 4, 4), 1, 1); p.drawLine(10.5, y, 20.5, y)
    elif name == "pin":
        path = QPainterPath(); path.moveTo(12, 21)
        path.cubicTo(7, 15, 5.5, 12.5, 5.5, 9.5); path.cubicTo(5.5, 5.8, 8.4, 3, 12, 3)
        path.cubicTo(15.6, 3, 18.5, 5.8, 18.5, 9.5); path.cubicTo(18.5, 12.5, 17, 15, 12, 21)
        p.drawPath(path); p.drawEllipse(QPointF(12, 9.5), 2.4, 2.4)
    elif name == "gear":             # settings (17/10): a cog with 8 teeth
        import math
        path = QPainterPath()
        for i in range(16):
            a0 = (i - 0.5) * math.pi / 8.0
            r = 9.0 if i % 2 == 0 else 6.8
            for k in (0, 1):
                a = a0 + k * math.pi / 8.0
                pt = QPointF(12 + r * math.cos(a), 12 + r * math.sin(a))
                if i == 0 and k == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path); p.drawEllipse(QPointF(12, 12), 2.8, 2.8)
    elif name == "bug":              # (19/11) a fixed bug: a beetle
        p.drawRoundedRect(QRectF(7.5, 8.5, 9, 12), 4.5, 4.5)
        p.drawArc(QRectF(9, 4.5, 6, 6), 0, 180 * 16)
        p.drawLine(QPointF(12, 11), QPointF(12, 20))
        for y, d in ((11, -2.5), (15, 0), (19, 2.5)):
            p.drawLine(QPointF(7.5, y), QPointF(4, y + d)); p.drawLine(QPointF(16.5, y), QPointF(20, y + d))
    elif name == "spark":            # (19/11) something new: a four-point star + a small one
        path = QPainterPath(); path.moveTo(10, 3)
        path.quadTo(11, 10, 17, 11); path.quadTo(11, 12, 10, 19); path.quadTo(9, 12, 3, 11)
        path.quadTo(9, 10, 10, 3); p.drawPath(path)
        p.drawLine(QPointF(18, 3.5), QPointF(18, 7.5)); p.drawLine(QPointF(16, 5.5), QPointF(20, 5.5))
    elif name == "chevron":          # (19/11) pointing down
        p.drawPolyline([QPointF(7, 10), QPointF(12, 15), QPointF(17, 10)])
    elif name == "info":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5); p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
        p.setBrush(c); p.drawEllipse(QPointF(12, 7.8), 0.9, 0.9)
    elif name == "bubble":           # (19/11) help bubbles
        path = QPainterPath(); path.addRoundedRect(QRectF(3.5, 4.5, 17, 12), 4, 4)
        p.drawPath(path)
        p.drawPolyline([QPointF(8, 16.5), QPointF(8, 20.5), QPointF(12, 16.5)])
    elif name == "bolt":             # speed
        p.drawPolyline([QPointF(13.5, 3), QPointF(6, 13.5), QPointF(11.5, 13.5), QPointF(10.5, 21),
                        QPointF(18, 10.5), QPointF(12.5, 10.5), QPointF(13.5, 3)])
    elif name == "globe":
        p.drawEllipse(QPointF(12, 12), 8.5, 8.5); p.drawEllipse(QRectF(8.2, 3.5, 7.6, 17))
        p.drawLine(QPointF(3.5, 12), QPointF(20.5, 12))
    elif name in ("star", "star_fill"):          # (19/11) the Seed Library
        import math
        path = QPainterPath()
        for i in range(10):
            r = 8.6 if i % 2 == 0 else 3.9
            a = -math.pi / 2 + i * math.pi / 5
            pt = QPointF(12 + r * math.cos(a), 12.6 + r * math.sin(a))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        if name == "star_fill":
            p.setBrush(c)
        p.drawPath(path)
    elif name == "image":
        p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2.5, 2.5)
        p.drawEllipse(QPointF(8.6, 9.6), 1.6, 1.6)
        p.drawPolyline([QPointF(4, 17.5), QPointF(10, 12.5), QPointF(13.5, 15.5), QPointF(16, 13.5),
                        QPointF(20, 17)])
    elif name == "note":
        path = QPainterPath(); path.moveTo(5, 4); path.lineTo(19, 4); path.lineTo(19, 15)
        path.lineTo(14, 20); path.lineTo(5, 20); path.closeSubpath(); p.drawPath(path)
        p.drawPolyline([QPointF(19, 15), QPointF(14, 15), QPointF(14, 20)])
        p.drawLine(QPointF(8, 9), QPointF(16, 9)); p.drawLine(QPointF(8, 12.5), QPointF(13, 12.5))
    elif name in ("lock", "unlock"):
        p.drawRoundedRect(QRectF(5.5, 11, 13, 9), 2, 2)
        path = QPainterPath(); path.moveTo(8.5, 11)
        if name == "lock":
            path.lineTo(8.5, 8); path.arcTo(QRectF(8.5, 4.5, 7, 7), 180, -180); path.lineTo(15.5, 11)
        else:
            path.lineTo(8.5, 8); path.arcTo(QRectF(8.5, 4.5, 7, 7), 180, -150)
        p.drawPath(path)
        p.setBrush(c); p.drawEllipse(QPointF(12, 15.5), 1.3, 1.3)
    elif name == "back":
        p.drawPolyline([QPointF(14, 6), QPointF(8, 12), QPointF(14, 18)])
    else:                            # unknown → dot
        p.setBrush(c); p.drawEllipse(QPointF(12, 12), 3, 3)


def pixmap(name: str, size: int = 20, color: str = theme.TEXT2, dpr: float = 2.0) -> QPixmap:
    key = (name, size, color, dpr)
    pm = _CACHE.get(key)
    if pm is not None:
        return pm
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 24.0
    p.scale(s, s)
    _draw(name, p, QColor(color))
    p.end()
    _CACHE[key] = pm
    return pm


def icon(name: str, size: int = 20, color: str = theme.TEXT2,
         active: str | None = None) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, size, active or theme.TEXT), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, size, active or theme.TEXT), QIcon.Mode.Active, QIcon.State.Off)
    ic.addPixmap(pixmap(name, size, theme.TEXT3), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


# ── theme-aware binding: icons follow live theme changes ────────────────────
def _role(role: str) -> str:
    return getattr(theme, role.upper(), role) if not role.startswith("#") else role


def bind(button, name: str, size: int = 20, normal: str = "text2", active: str = "text"):
    """Set a themed icon on a button and remember how to redraw it."""
    button.setProperty("ssaIcon", f"{name}|{size}|{normal}|{active}")
    button.setIcon(icon(name, size, _role(normal), _role(active)))
    return button


def refresh(root):
    """Redraw every bound icon under `root` with the current theme colours."""
    from PySide6.QtWidgets import QAbstractButton
    _CACHE.clear()
    for b in root.findChildren(QAbstractButton):
        spec = b.property("ssaIcon")
        if spec:
            name, size, normal, active = str(spec).split("|")
            b.setIcon(icon(name, int(size), _role(normal), _role(active)))


def _tint(c: QColor, a: float) -> QColor:
    t = QColor(c)
    t.setAlphaF(a)
    return t


def _home_icon(name: str, p: QPainter, c: QColor):
    """(20/11) The Home tiles' icons: tinted shapes under the strokes, a detail or two."""
    import math as _m
    if name == "hm_map":                 # a folded map, a dashed route, a pin
        panels = [((2.5, 5.6), (8.2, 3.6), (8.2, 18.4), (2.5, 20.4)),
                  ((8.2, 3.6), (15.8, 5.6), (15.8, 20.4), (8.2, 18.4)),
                  ((15.8, 5.6), (21.5, 3.6), (21.5, 18.4), (15.8, 20.4))]
        for i, pts in enumerate(panels):
            path = QPainterPath(); path.moveTo(*pts[0])
            for q in pts[1:]:
                path.lineTo(*q)
            path.closeSubpath()
            p.fillPath(path, _tint(c, 0.30 if i == 1 else 0.14))
            p.drawPath(path)
        _pen(p, c, 1.2)
        pen = p.pen(); pen.setDashPattern([1.6, 1.8]); p.setPen(pen)
        route = QPainterPath(); route.moveTo(4.2, 16.5); route.cubicTo(7.5, 11.5, 11, 17.5, 14.5, 12.2)
        p.drawPath(route)
        _pen(p, c, 1.4)
        pin = QPainterPath(); pin.moveTo(18.6, 15.2)
        pin.cubicTo(16.2, 12.4, 15.9, 11.6, 15.9, 10.5); pin.arcTo(QRectF(15.9, 7.8, 5.4, 5.4), 180, -180)
        pin.cubicTo(21.3, 11.6, 21.0, 12.4, 18.6, 15.2); pin.closeSubpath()
        p.setBrush(c); p.drawPath(pin)
        p.setBrush(_tint(QColor("#ffffff"), 0.9)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(18.6, 10.5), 1.0, 1.0)
    elif name == "hm_sessions":          # a list of conditions ticked, a lens over it
        p.setBrush(_tint(c, 0.16))
        p.drawRoundedRect(QRectF(3, 3, 14.5, 17.5), 2.6, 2.6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k, y in enumerate((7.4, 11.6, 15.8)):
            p.setBrush(_tint(c, 0.9 if k < 2 else 0.25))
            _pen(p, c, 1.0)
            p.drawRoundedRect(QRectF(5.4, y - 1.4, 2.8, 2.8), 0.7, 0.7)
            p.setBrush(Qt.BrushStyle.NoBrush)
            _pen(p, c, 1.4)
            p.drawLine(QPointF(10, y), QPointF(14.8 if k != 1 else 13, y))
        _pen(p, c, 1.6)
        p.setBrush(_tint(c, 0.32))
        p.drawEllipse(QPointF(16.6, 15.4), 3.9, 3.9)
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, c, 2.0)
        p.drawLine(QPointF(19.4, 18.2), QPointF(21.6, 20.4))
        _pen(p, c, 1.0)
        p.drawArc(QRectF(14.2, 13.0, 3.2, 3.2), 100 * 16, 80 * 16)          # its glint
    elif name == "hm_loot":              # a chest, its lid tinted, a latch, a sparkle
        p.setBrush(_tint(c, 0.18))
        p.drawRoundedRect(QRectF(3, 8.5, 18, 12), 2, 2)
        lid = QPainterPath(); lid.moveTo(3, 11.5); lid.lineTo(3, 9.2)
        lid.cubicTo(3, 6.2, 5.2, 5.2, 7.5, 5.2); lid.lineTo(16.5, 5.2)
        lid.cubicTo(18.8, 5.2, 21, 6.2, 21, 9.2); lid.lineTo(21, 11.5); lid.closeSubpath()
        p.setBrush(_tint(c, 0.36)); p.drawPath(lid)
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, c, 1.2)
        p.drawLine(QPointF(7.2, 5.6), QPointF(7.2, 20.2)); p.drawLine(QPointF(16.8, 5.6), QPointF(16.8, 20.2))
        _pen(p, c, 1.4)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(10.4, 10.1, 3.2, 4.2), 0.8, 0.8)
        p.setBrush(Qt.BrushStyle.NoBrush)
        star = QPainterPath()
        cx, cy = 20.2, 3.3
        for i in range(8):
            a = -_m.pi / 2 + i * _m.pi / 4
            r = 2.6 if i % 2 == 0 else 0.7
            q = QPointF(cx + r * _m.cos(a), cy + r * _m.sin(a))
            star.moveTo(q) if i == 0 else star.lineTo(q)
        star.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(c); p.drawPath(star)
    elif name == "hm_crack":             # a video frame, a block tree in it, a play badge
        p.setBrush(_tint(c, 0.14))
        p.drawRoundedRect(QRectF(2.5, 3.5, 19, 14.5), 2.4, 2.4)
        p.setBrush(Qt.BrushStyle.NoBrush)
        _pen(p, c, 1.3)
        canopy = QPainterPath(); canopy.moveTo(4.9, 11.6); canopy.lineTo(4.9, 8.0); canopy.lineTo(6.9, 8.0)
        canopy.lineTo(6.9, 5.8); canopy.lineTo(13.5, 5.8); canopy.lineTo(13.5, 8.0); canopy.lineTo(15.5, 8.0)
        canopy.lineTo(15.5, 11.6); canopy.closeSubpath()                       # an oak, block by block
        p.setBrush(_tint(c, 0.45)); p.drawPath(canopy)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(_tint(c, 0.95))
        for x, y in ((8.0, 7.2), (11.6, 9.2), (6.4, 9.6), (13.2, 8.6)):
            p.drawRect(QRectF(x, y, 1.4, 1.4))
        p.setBrush(c)
        p.drawRect(QRectF(9.3, 11.6, 1.9, 4.0))                               # its trunk
        _pen(p, c, 1.3)
        p.drawLine(QPointF(4.4, 15.6), QPointF(12.8, 15.6))                   # the ground
        _pen(p, c, 1.4)
        p.setBrush(c)
        p.drawEllipse(QPointF(17.6, 17.6), 4.1, 4.1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        tri = QPainterPath(); tri.moveTo(16.4, 15.6); tri.lineTo(19.8, 17.6); tri.lineTo(16.4, 19.6); tri.closeSubpath()
        p.setBrush(Qt.GlobalColor.black); p.drawPath(tri)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    elif name == "hm_draw":              # hills sketched, the brush that draws them
        front = QPainterPath(); front.moveTo(2.5, 20.5); front.lineTo(2.5, 15.2)
        front.cubicTo(4.8, 11.4, 7.8, 11.0, 9.8, 14.2); front.cubicTo(11.6, 12.4, 14.6, 12.8, 17.2, 16.2)
        front.lineTo(17.2, 20.5); front.closeSubpath()
        p.fillPath(front, _tint(c, 0.34))
        ridge = QPainterPath(); ridge.moveTo(2.5, 15.2)
        ridge.cubicTo(4.8, 11.4, 7.8, 11.0, 9.8, 14.2); ridge.cubicTo(11.6, 12.4, 14.6, 12.8, 17.2, 16.2)
        p.drawPath(ridge)
        p.drawLine(QPointF(2.5, 20.5), QPointF(17.2, 20.5))
        _pen(p, c, 1.3)
        p.setBrush(_tint(c, 0.25))
        handle = QPainterPath(); handle.moveTo(20.6, 2.2); handle.lineTo(22.2, 3.8); handle.lineTo(16.0, 10.6)
        handle.lineTo(14.4, 9.0); handle.closeSubpath()
        p.drawPath(handle)
        p.setBrush(c)
        ferrule = QPainterPath(); ferrule.moveTo(14.4, 9.0); ferrule.lineTo(16.0, 10.6); ferrule.lineTo(15.0, 11.7)
        ferrule.lineTo(13.3, 10.0); ferrule.closeSubpath()
        p.drawPath(ferrule)
        tip = QPainterPath(); tip.moveTo(13.3, 10.0); tip.lineTo(15.0, 11.7)
        tip.cubicTo(15.0, 14.0, 13.4, 15.4, 11.0, 15.8); tip.cubicTo(11.2, 13.6, 11.8, 11.4, 13.3, 10.0)
        tip.closeSubpath()
        p.drawPath(tip)
