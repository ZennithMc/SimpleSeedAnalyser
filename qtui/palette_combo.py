"""
qtui/palette_combo.py — the noise palette chooser, dressed up.

Every entry shows its own gradient (a pill) and its name in the palette's
signature colour; groups are separated by small titled rules:

    Grayscale
    ── CLASSIC ───────────────  Semantic · Warm · … · Nebula
    ── EXTREMES · TO ±25 000 ──  Eclipse · Nocturne      (badge ±25k, the pill
                                                          shows the glowing ends)
    ── YOUR PALETTES ─────────  …

fill_palette_combo(combo, current) (re)fills any QComboBox that way.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QStyle, QStyledItemDelegate

import noise_palettes as npal

from . import theme

ROLE_KIND = Qt.ItemDataRole.UserRole + 7        # "header" | "palette"
PILL_W, PILL_H = 74, 14


def _preview_lut(name):
    """What the pill shows: extended palettes over their whole axis (the
    glowing extremes included), others over their usual range."""
    if name in npal.STOP_BUILTINS or name in npal.CUSTOM_PALETTES:
        return npal.lut_from_stops(npal.CUSTOM_PALETTES[name]["Weirdness"],
                                   "Weirdness" if name in npal.STOP_BUILTINS else None)
    return npal.builtin_lut(name, "Continentalness" if name == "Semantic" else "Temperature")


def pill_pixmap(name, w=PILL_W, h=PILL_H, dpr=2.0):
    lut = _preview_lut(name)
    W, H = int(w * dpr), int(h * dpr)
    row = np.ascontiguousarray(np.repeat(lut[np.linspace(0, 255, W).astype(int)][None], H, axis=0))
    img = QImage(row.data, W, H, W * 3, QImage.Format.Format_RGB888).copy()
    pm = QPixmap(W, H); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath(); path.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), H / 2, H / 2)
    p.setClipPath(path); p.drawImage(0, 0, img)
    p.setClipping(False)
    p.setPen(QPen(QColor(255, 255, 255, 60), dpr)); p.drawPath(path)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def name_color(name):
    return QColor(npal.palette_name_color(name))


class PaletteDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        if index.data(ROLE_KIND) == "header":
            return QSize(option.rect.width(), 24)
        return QSize(max(option.rect.width(), 230), 30)

    def paint(self, p, option, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect)
        if index.data(ROLE_KIND) == "header":
            f = theme.ui_font(7.2, QFont.Weight.DemiBold); f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
            p.setFont(f)
            text = index.data(Qt.ItemDataRole.DisplayRole).upper()
            tw = p.fontMetrics().horizontalAdvance(text)
            y = r.center().y() + 3
            p.setPen(QPen(QColor(theme.LINE2), 1))
            p.drawLine(int(r.left() + 8), int(y), int(r.left() + 16), int(y))
            p.drawLine(int(r.left() + 24 + tw), int(y), int(r.right() - 8), int(y))
            p.setPen(QColor(theme.TEXT3))
            p.drawText(QRectF(r.left() + 20, r.top(), tw + 2, r.height()).adjusted(0, 6, 0, 0),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            p.restore()
            return
        name = index.data(Qt.ItemDataRole.UserRole) or index.data(Qt.ItemDataRole.DisplayRole)
        col = name_color(name)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hov = bool(option.state & QStyle.StateFlag.State_MouseOver)
        card = r.adjusted(4, 2, -4, -2)
        if sel or hov:
            bg = QColor(col); bg.setAlpha(58 if sel else 30)
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 150 if sel else 70), 1))
            p.setBrush(bg)
            p.drawRoundedRect(card, 7, 7)
        # tinted edge: a little of the palette's own colour on every row
        edge = QColor(col); edge.setAlpha(210)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(edge)
        p.drawRoundedRect(QRectF(card.left() + 1, card.top() + 6, 3, card.height() - 12), 1.5, 1.5)
        pm = pill_pixmap(name)
        p.drawPixmap(int(card.left() + 12), int(card.center().y() - PILL_H / 2), pm)
        f = theme.ui_font(9.5, QFont.Weight.DemiBold if (sel or name in npal.STOP_BUILTINS)
                          else QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(col.lighter(115) if sel else col)
        tx = card.left() + 12 + PILL_W + 12
        p.drawText(QRectF(tx, card.top(), card.right() - tx - 50, card.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        if name in npal.STOP_BUILTINS:
            badge = "±25k"
            p.setFont(theme.mono_font(7))
            bw = p.fontMetrics().horizontalAdvance(badge) + 10
            br = QRectF(card.right() - bw - 6, card.center().y() - 8, bw, 16)
            bgc = QColor(col); bgc.setAlpha(45)
            p.setBrush(bgc); p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 160), 1))
            p.drawRoundedRect(br, 8, 8)
            p.setPen(col)
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, badge)
        elif name not in npal.BUILTIN_PALETTES:
            p.setFont(theme.ui_font(7.5))
            p.setPen(QColor(theme.TEXT3))
            p.drawText(QRectF(card.right() - 56, card.top(), 50, card.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "yours")
        p.restore()


def fill_palette_combo(combo: QComboBox, current: str | None = None, placeholder: str = ""):
    """(Re)fill with the grouped, styled palettes; keeps / sets `current`."""
    combo.blockSignals(True)
    combo.clear()
    if not isinstance(combo.itemDelegate(), PaletteDelegate):
        combo.setItemDelegate(PaletteDelegate(combo))
        combo.view().setMouseTracking(True)
        combo.setIconSize(QSize(46, 12))
        # the closed combo must fit narrow panels (Layers drawer): small minimum,
        # while the popup list gets the room it needs
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(6)
        combo.view().setMinimumWidth(270)
    model = combo.model()
    for title, names in npal.palette_groups():
        if title:
            combo.addItem(title)
            it = model.item(combo.count() - 1)
            it.setData("header", ROLE_KIND)
            it.setFlags(Qt.ItemFlag.NoItemFlags)
        for n in names:
            combo.addItem(QIcon(pill_pixmap(n, 46, 12)), n, n)
            it = model.item(combo.count() - 1)
            it.setData("palette", ROLE_KIND)
            it.setForeground(name_color(n))
    if placeholder:
        combo.setPlaceholderText(placeholder)
        combo.setCurrentIndex(-1)
    elif current is not None:
        i = combo.findData(current)
        combo.setCurrentIndex(i if i >= 0 else combo.findData("Semantic"))
    combo.blockSignals(False)
