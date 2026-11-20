"""
qtui/brand.py — (13/11) SSA's small pictures: the seed sigil, the application's
mark and the coloured icon tiles.

    sigil(seed, 26)        a 5 x 5 mirrored pixel banner unique to the seed: the
                           same seed has the same sigil everywhere (top bar,
                           results, Home)
    mark(30)               the pixel « S » on the accent gradient
    icon_tile("chest", "#f5b83d", 48, 24)
    signature(["village", "biome_sample"], 46)   (15/11) a session's picture: the icons of its
                           first condition types (up to four) on their colours
"""
from __future__ import annotations

import hashlib

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap

from . import icons, theme

_SIG: dict = {}
_TILE: dict = {}


def _canvas(size: float, dpr: float):
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pm, p


def sigil(seed: int, size: int, dpr: float = 2.0) -> QPixmap:
    key = (int(seed), size, dpr)
    pm = _SIG.get(key)
    if pm is not None:
        return pm
    h = hashlib.sha256(str(int(seed)).encode()).digest()
    hue = int.from_bytes(h[:2], "big") % 360
    pm, p = _canvas(size, dpr)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.27, size * 0.27)
    p.fillPath(path, QColor.fromHsv(hue, 150, 62))
    p.setClipPath(path)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor.fromHsv(hue, 120, 255))
    g.setColorAt(1, QColor.fromHsv((hue + 45) % 360, 175, 235))
    p.setBrush(g)
    p.setPen(Qt.PenStyle.NoPen)
    n, pad = 5, size * 0.17
    cell = (size - 2 * pad) / n
    bits = int.from_bytes(h[2:6], "big")
    k = 0
    for y in range(n):
        for x in range(3):                    # 3 columns, mirrored on the 2 others
            if (bits >> k) & 1:
                for xx in {x, n - 1 - x}:
                    p.drawRect(QRectF(pad + xx * cell, pad + y * cell, cell + 0.25, cell + 0.25))
            k += 1
    p.end()
    if len(_SIG) > 4000:
        _SIG.clear()
    _SIG[key] = pm
    return pm


def mark(size: int, c1: str | None = None, c2: str | None = None, dpr: float = 2.0) -> QPixmap:
    a = c1 or theme.TOKENS.get("accent", theme.ACCENT)  # (default) the theme's own accent
    pm, p = _canvas(size, dpr)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.30, size * 0.30)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(a))
    g.setColorAt(1, QColor(c2 or theme.accent2(a)))
    p.fillPath(path, g)
    p.setBrush(QColor(theme.on_accent(a)))
    p.setPen(Qt.PenStyle.NoPen)
    c = size * 0.118
    x0, y0 = (size - 3 * c) / 2, (size - 5 * c) / 2
    for y, row in enumerate(("111", "100", "111", "001", "111")):
        for x, ch in enumerate(row):
            if ch == "1":
                p.drawRect(QRectF(x0 + x * c, y0 + y * c, c + 0.2, c + 0.2))
    p.end()
    return pm


def icon_tile(name: str, color: str, size: int = 44, isz: int = 22, dpr: float = 2.0) -> QPixmap:
    key = (name, color, size, isz, dpr)
    pm = _TILE.get(key)
    if pm is not None:
        return pm
    pm, p = _canvas(size, dpr)
    c = QColor(color)
    c.setAlphaF(0.16)
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    ic = icons.pixmap(name, isz, color)
    o = (size - isz) / 2
    p.drawPixmap(QRectF(o, o, isz, isz), ic, QRectF(ic.rect()))
    p.end()
    _TILE[key] = pm
    return pm


def ws_tile(name: str, color: str, size: int = 48, isz: int = 28, dpr: float = 2.0) -> QPixmap:
    """(20/11) Home's workspace tiles: the same tinted square, with a light falling from the top
    left, a fine rim, and the richer icon."""
    key = ("ws", name, color, size, isz, dpr)
    pm = _TILE.get(key)
    if pm is not None:
        return pm
    from PySide6.QtGui import QLinearGradient, QPen
    pm, p = _canvas(size, dpr)
    r = QRectF(0.5, 0.5, size - 1, size - 1)
    rad = size * 0.28
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    a, b = QColor(color), QColor(color)
    a.setAlphaF(0.26)
    b.setAlphaF(0.10)
    g.setColorAt(0, a)
    g.setColorAt(1, b)
    p.setBrush(g)
    rim = QLinearGradient(r.topLeft(), r.bottomLeft())
    ra, rb = QColor(color), QColor(color)
    ra.setAlphaF(0.45)
    rb.setAlphaF(0.08)
    rim.setColorAt(0, ra)
    rim.setColorAt(1, rb)
    p.setPen(QPen(rim, 1.0))
    p.drawRoundedRect(r, rad, rad)
    ic = icons.pixmap(name, isz, color)
    o = (size - isz) / 2
    p.drawPixmap(QRectF(o, o, isz, isz), ic, QRectF(ic.rect()))
    p.end()
    _TILE[key] = pm
    return pm


_SIGN: dict = {}


def signature(types, size: int = 46, dpr: float = 2.0) -> QPixmap:
    """The picture of a session: up to four of its condition types, each with its icon (or
    a dot of its colour), on a tile shaded with their colours."""
    from .session_model import type_color, type_icon
    types = [t for t in (types or []) if t][:4]
    key = (tuple(types), size, dpr, theme.THEME)
    pm = _SIGN.get(key)
    if pm is not None:
        return pm
    pm, p = _canvas(size, dpr)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.26, size * 0.26)
    cols = [QColor(type_color(t)) for t in types] or [QColor(theme.TEXT3)]
    g = QLinearGradient(0, 0, size, size)
    c0, c1 = QColor(cols[0]), QColor(cols[-1])
    c0.setAlphaF(0.34)
    c1.setAlphaF(0.16)
    g.setColorAt(0, c0)
    g.setColorAt(1, c1)
    p.fillPath(path, QColor(theme.field_bg()))
    p.fillPath(path, g)
    n = len(types)
    cells = {0: [], 1: [(0.5, 0.5, 0.56)], 2: [(0.30, 0.34, 0.40), (0.70, 0.66, 0.40)],
             3: [(0.30, 0.30, 0.38), (0.70, 0.30, 0.38), (0.50, 0.72, 0.38)],
             4: [(0.29, 0.29, 0.36), (0.71, 0.29, 0.36), (0.29, 0.71, 0.36), (0.71, 0.71, 0.36)]}[n]
    for t, (fx, fy, fs) in zip(types, cells):
        s_ = size * fs
        ic = type_icon(t, int(s_))
        r = QRectF(size * fx - s_ / 2, size * fy - s_ / 2, s_, s_)
        if ic is not None:
            src = ic.pixmap(int(s_ * 2), int(s_ * 2))
            p.drawPixmap(r, src, QRectF(src.rect()))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(type_color(t)))
            p.drawEllipse(r.center(), s_ * 0.24, s_ * 0.24)
    edge = QColor(cols[0])
    edge.setAlphaF(0.55)
    p.setPen(edge)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.end()
    if len(_SIGN) > 600:
        _SIGN.clear()
    _SIGN[key] = pm
    return pm
