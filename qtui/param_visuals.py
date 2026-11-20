"""
qtui/param_visuals.py — (14/11 bis) parameters of the stronghold / village /
ruined portal conditions chosen on a picture instead of in a field. Each one
only drives the form's own control (which stays the truth and is read by
value() exactly as before).

    SwitchCard(text, glyph)      a QCheckBox drawn as a card with a switch: the
                                 options that open a block (Eyes in the portal,
                                 Top Y, Broken portal, Houses, Forges…)
    ShScene(scope, anchor)       the stronghold: on the left the rings of the 128
                                 strongholds around (0, 0) — click the first one,
                                 its ring, or the outer rings: First / First ring /
                                 All 128; on the right the stronghold itself —
                                 click its ring point or its end portal: where the
                                 condition's position is taken.
                                 scopeChosen(str) / anchorChosen(str)
    VariantPicker(value)         a village lived in, abandoned, or either one
                                 chosen(str): "Any" / "Not abandoned" / "Abandoned"
    ChoiceCards(seg, glyphs)     (15/11) the options of a widgets.Segmented as cards with a
                                 pictogram; a click presses the Segmented's own button
                                 (hidden), so nothing else changes
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QCheckBox, QSizePolicy, QToolTip, QWidget

from . import motion, theme

RINGS = [3, 6, 10, 15, 21, 28, 36, 9]           # the 128 strongholds, ring by ring


def _c(hex_, alpha=1.0):
    c = QColor(hex_)
    c.setAlphaF(alpha)
    return c


# ── a check box drawn as a card with a switch ────────────────────────────────
class SwitchCard(QCheckBox):
    def __init__(self, text, glyph="", color=None):
        super().__init__(text)
        self.glyph, self._color = glyph, color
        self._t = 0.0
        self._an = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggled.connect(self._glide)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 96, 38)

    def minimumSizeHint(self):
        return QSize(120, 38)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def _glide(self, on):
        to = 1.0 if on else 0.0
        if self._an is not None:
            try:
                self._an.stop()
            except RuntimeError:
                pass
            self._an = None
        if not motion.enabled() or not self.isVisible():
            self._t = to
            self.update()
            return
        t0 = self._t

        def step(t):
            self._t = t0 + (to - t0) * t
            self.update()

        def done():
            self._an = None
        self._an = motion.run(self, 170, step, done)

    def enterEvent(self, e):
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.update()

    def paintEvent(self, e):
        if self._an is None:
            self._t = 1.0 if self.isChecked() else 0.0
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.38)
        a = self._color or theme.ACCENT
        hov = self.underMouse() and self.isEnabled()
        r = QRectF(self.rect()).adjusted(0.5, 1.5, -0.5, -1.5)
        p.setBrush(QColor(theme._mix(theme.field_bg(), a, 0.16 * t + (0.05 if hov else 0.0))))
        p.setPen(QPen(QColor(theme._mix(theme.LINE2 if hov else theme.LINE, a, 0.60 * t)), 1))
        p.drawRoundedRect(r, 10, 10)
        # the glyph on its tile
        tile = QRectF(r.left() + 7, r.center().y() - 11, 22, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(a, 0.12 + 0.16 * t))
        p.drawRoundedRect(tile, 6, 6)
        self._glyph(p, tile, QColor(theme._mix(theme.TEXT3, a, 0.25 + 0.75 * t)))
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold if t > 0.5 else QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(theme._mix(theme.TEXT2, theme.TEXT, t)))
        p.drawText(QRectF(tile.right() + 9, r.top(), r.width() - 90, r.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())
        # the switch
        sw = QRectF(r.right() - 44, r.center().y() - 9, 34, 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme._mix(theme.BG4 or theme.LINE2, a, t)))
        p.drawRoundedRect(sw, 9, 9)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(sw.left() + 9 + 16 * t, sw.center().y()), 6.5, 6.5)

    def _glyph(self, p, r, col):
        cx, cy = r.center().x(), r.center().y()
        pen = QPen(col, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        g = self.glyph
        if g == "eye":
            path = QPainterPath(QPointF(cx - 7, cy))
            path.quadTo(QPointF(cx, cy - 7), QPointF(cx + 7, cy))
            path.quadTo(QPointF(cx, cy + 7), QPointF(cx - 7, cy))
            p.drawPath(path)
            p.setBrush(col)
            p.drawEllipse(QPointF(cx, cy), 2.2, 2.2)
        elif g == "height":
            p.drawLine(QPointF(cx, cy - 6.5), QPointF(cx, cy + 6.5))
            p.drawPolyline([QPointF(cx - 3.5, cy - 3), QPointF(cx, cy - 6.5), QPointF(cx + 3.5, cy - 3)])
            p.drawLine(QPointF(cx - 5, cy + 6.5), QPointF(cx + 5, cy + 6.5))
        elif g == "broken":
            p.drawRect(QRectF(cx - 6, cy - 6, 12, 12))
            p.drawPolyline([QPointF(cx - 1, cy - 6), QPointF(cx + 2, cy - 2), QPointF(cx - 2, cy + 1),
                            QPointF(cx + 1, cy + 6)])
        elif g == "house":
            p.drawPolyline([QPointF(cx - 7, cy - 1), QPointF(cx, cy - 7), QPointF(cx + 7, cy - 1)])
            p.drawRect(QRectF(cx - 5, cy - 1, 10, 7.5))
        elif g == "forge":
            p.drawRect(QRectF(cx - 6.5, cy - 1.5, 13, 7))
            p.drawLine(QPointF(cx - 6.5, cy + 1.5), QPointF(cx + 6.5, cy + 1.5))
            p.drawPolyline([QPointF(cx - 2, cy - 6), QPointF(cx, cy - 3.5), QPointF(cx + 2, cy - 6)])
        elif g == "portal":
            p.drawRect(QRectF(cx - 5, cy - 7, 10, 14))
            p.setBrush(col)
            p.drawRect(QRectF(cx - 1.5, cy - 3.5, 3, 7))
        elif g == "tear":
            path = QPainterPath(QPointF(cx, cy - 7))
            path.quadTo(QPointF(cx + 6.5, cy + 1), QPointF(cx, cy + 6.5))
            path.quadTo(QPointF(cx - 6.5, cy + 1), QPointF(cx, cy - 7))
            p.drawPath(path)
        else:
            p.setBrush(col)
            p.drawEllipse(QPointF(cx, cy), 3, 3)


# ── the stronghold: which ones, and where its position is taken ──────────────
class ShScene(QWidget):
    scopeChosen = Signal(str)
    anchorChosen = Signal(str)
    SCOPE_TIP = {"First": "The filters must pass on the first stronghold (the fastest).",
                 "First ring": "The filters must pass on one of the 3 strongholds of the first ring\n"
                               "(the 3 nearest to 0, 0): about 3× slower.",
                 "All 128": "The filters must pass on at least one of the 128 strongholds (the slowest)."}
    ANCHOR_TIP = {"Stronghold origin": "The ring point of the stronghold, like cubiomes-viewer (≤ 112 blocks from\n"
                                       "the real stronghold). Linked conditions are placed from it. The filters\n"
                                       "always look at the real stronghold.",
                  "End portal": "The centre of the end portal (the /tp of the result). Linked conditions are\n"
                                "placed from it, e.g. a village within 200 blocks of the portal. Exact;\n"
                                "linked conditions are a little slower to search."}

    def __init__(self, scope="First", anchor="Stronghold origin", parent=None):
        super().__init__(parent)
        self.scope, self.anchor = scope, anchor
        self._hot = None                         # ("scope", name) / ("anchor", name)
        self._wave = 1.0                         # 0 → 1: the rings light up from the centre
        rnd = random.Random(128)
        self._dots = []                          # (ring index, angle)
        for k, n in enumerate(RINGS):
            a0 = rnd.random() * 6.283
            self._dots += [(k, a0 + 6.283 * i / n + (rnd.random() - 0.5) * 0.25) for i in range(n)]
        self.setMouseTracking(True)
        self.setFixedHeight(214)
        self.setMinimumWidth(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_state(self, scope, anchor):
        if (scope, anchor) != (self.scope, self.anchor):
            wave = scope != self.scope
            self.scope, self.anchor = scope, anchor
            if wave and motion.enabled() and self.isVisible():
                def step(t):
                    self._wave = t
                    self.update()
                self._wave = 0.0
                motion.run(self, 420, step)
            self.update()

    # ── geometry ─────────────────────────────────────────────────────────────
    def _split(self):
        return self.width() * 0.60

    def _rings(self):
        cx, cy = self._split() / 2.0, self.height() / 2.0 - 6
        r1 = 30.0
        step = (min(self._split() / 2.0 - 8, self.height() / 2.0 - 16) - r1) / (len(RINGS) - 1)
        return cx, cy, r1, step

    def _dot(self, k, ang):
        cx, cy, r1, step = self._rings()
        r = r1 + k * step
        return QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang))

    def _nodes(self):
        """the ring point and the end portal of the stronghold drawn on the right"""
        x0 = self._split() + 8
        w = self.width() - x0 - 6
        return QPointF(x0 + w * 0.20, 58), QPointF(x0 + w * 0.74, 132)

    def _zone(self, pos):
        if pos.x() >= self._split():
            o, e = self._nodes()
            do = math.hypot(pos.x() - o.x(), pos.y() - o.y())
            de = math.hypot(pos.x() - e.x(), pos.y() - e.y())
            return ("anchor", "Stronghold origin" if do <= de else "End portal")
        cx, cy, r1, step = self._rings()
        d = math.hypot(pos.x() - cx, pos.y() - cy)
        if d > r1 + step * 0.5:
            return ("scope", "All 128")
        first = self._dot(*self._dots[0])
        if math.hypot(pos.x() - first.x(), pos.y() - first.y()) <= 15:
            return ("scope", "First")
        return ("scope", "First ring")

    # ── the hand ─────────────────────────────────────────────────────────────
    def mouseMoveEvent(self, e):
        z = self._zone(e.position())
        if z != self._hot:
            self._hot = z
            self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hot = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        kind, name = self._zone(e.position())
        if kind == "scope" and name != self.scope:
            self.scopeChosen.emit(name)
        elif kind == "anchor" and name != self.anchor:
            self.anchorChosen.emit(name)

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            kind, name = self._zone(QPointF(e.pos()))
            QToolTip.showText(e.globalPos(), (self.SCOPE_TIP if kind == "scope" else self.ANCHOR_TIP)[name], self)
            return True
        return super().event(e)

    # ── the picture ──────────────────────────────────────────────────────────
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        a = theme.ACCENT
        frame = QPainterPath()
        frame.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 12, 12)
        p.fillPath(frame, QColor(theme.field_bg()))
        p.save()
        p.setClipPath(frame)
        cx, cy, r1, step = self._rings()
        glow = QRadialGradient(QPointF(cx, cy), h * 0.85)
        glow.setColorAt(0, _c(a, 0.16))
        glow.setColorAt(1, _c(a, 0.0))
        p.fillRect(QRectF(0, 0, self._split(), h), glow)
        hot = self._hot[1] if self._hot and self._hot[0] == "scope" else None
        show = hot or self.scope
        n_lit = {"First": 0, "First ring": 1, "All 128": len(RINGS)}[self.scope]
        n_hot = {"First": 0, "First ring": 1, "All 128": len(RINGS)}[show]
        # the rings
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(len(RINGS)):
            lit = k < n_lit
            rr = r1 + k * step
            col = _c(a, 0.55) if lit else _c(theme.TEXT, 0.09 + (0.10 if k < n_hot else 0.0))
            p.setPen(QPen(col, 1.2 if lit else 1.0, Qt.PenStyle.SolidLine if lit else Qt.PenStyle.DotLine))
            p.drawEllipse(QPointF(cx, cy), rr, rr)
        # (0, 0)
        p.setPen(QPen(QColor(theme.TEXT2), 1.3))
        p.drawLine(QPointF(cx - 4, cy), QPointF(cx + 4, cy))
        p.drawLine(QPointF(cx, cy - 4), QPointF(cx, cy + 4))
        # the 128 strongholds
        p.setPen(Qt.PenStyle.NoPen)
        reach = self._wave * (len(RINGS) + 1)
        for i, (k, ang) in enumerate(self._dots):
            q = self._dot(k, ang)
            first = i == 0
            lit = (first or k < n_lit) and (k <= reach)
            pre = first or k < n_hot
            if lit:
                p.setBrush(_c(a, 0.25))
                p.drawEllipse(q, 6.0 if first else 4.6, 6.0 if first else 4.6)
                p.setBrush(QColor(a))
                p.drawEllipse(q, 3.4 if first else 2.5, 3.4 if first else 2.5)
            else:
                p.setBrush(_c(theme.TEXT, 0.42 if pre else 0.20))
                p.drawEllipse(q, 2.0, 2.0)
        fq = self._dot(*self._dots[0])           # the first one keeps a ring around it
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#ffffff"), 1.4))
        p.drawEllipse(fq, 6.8, 6.8)
        # what is chosen
        p.setFont(theme.num_font(9.5, QFont.Weight.DemiBold))
        p.setPen(QColor(theme.TEXT))
        label = {"First": "First stronghold", "First ring": "First ring · 3", "All 128": "All 128"}[show]
        p.drawText(QPointF(12, 10 + p.fontMetrics().ascent()), label)
        p.setFont(theme.ui_font(7.6))
        p.setPen(QColor(theme.TEXT3))
        cost = {"First": "the fastest", "First ring": "≈ 3× slower", "All 128": "the slowest"}[show]
        p.drawText(QPointF(12, h - 10), cost)

        # ── the stronghold itself (right) ────────────────────────────────────
        x0 = self._split()
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.drawLine(QPointF(x0, 12), QPointF(x0, h - 12))
        o, en = self._nodes()
        hot_a = self._hot[1] if self._hot and self._hot[0] == "anchor" else None
        stone = QColor(theme._mix(theme.field_bg(), "#9aa0ab", 0.55))
        # corridors from the start to the portal room
        mid1, mid2 = QPointF(o.x(), (o.y() + en.y()) / 2 + 6), QPointF(en.x(), (o.y() + en.y()) / 2 + 6)
        pen = QPen(stone, 7)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.drawPolyline([o, mid1, mid2, en])
        p.drawLine(mid1, QPointF(mid1.x() - 16, mid1.y()))
        p.drawLine(QPointF((mid1.x() + mid2.x()) / 2, mid1.y()), QPointF((mid1.x() + mid2.x()) / 2, mid1.y() + 22))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(stone)
        p.drawRect(QRectF(o.x() - 9, o.y() - 9, 18, 18))                    # the start (stairs)
        room = QRectF(en.x() - 19, en.y() - 17, 38, 34)
        p.drawRect(room)
        # the end portal: frame and the starry inside
        p.setBrush(QColor(theme._mix(theme.field_bg(), "#3fae8f", 0.85)))
        p.drawRect(QRectF(en.x() - 9, en.y() - 9, 18, 18))
        p.setBrush(QColor("#0b1620"))
        p.drawRect(QRectF(en.x() - 5.5, en.y() - 5.5, 11, 11))
        p.setBrush(_c("#bfe9ff", 0.9))
        for dx, dy in ((-3, -2), (2, 3), (3, -3), (-1, 3)):
            p.drawRect(QRectF(en.x() + dx, en.y() + dy, 1.2, 1.2))
        # the ring point: a dashed target a little off the stairs (≤ 112 blocks from the real one)
        ring = QPointF(o.x() - 2, o.y() - 2)
        for node, name, text in ((ring, "Stronghold origin", "Ring point"), (en, "End portal", "End portal")):
            on = self.anchor == name
            pre = hot_a == name
            col = QColor(a) if on else QColor(theme.TEXT2 if pre else theme.TEXT3)
            if on:
                halo = QRadialGradient(node, 30)
                halo.setColorAt(0, _c(a, 0.38))
                halo.setColorAt(1, _c(a, 0.0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(halo)
                p.drawEllipse(node, 30, 30)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(col, 1.8 if on else 1.3, Qt.PenStyle.SolidLine if on else Qt.PenStyle.DashLine))
            rad = 15 if name == "Stronghold origin" else 26
            p.drawEllipse(node, rad, rad)
            if on:                               # the pin: the position is taken here
                pin = QPainterPath(QPointF(node.x(), node.y() - rad + 3))
                pin.lineTo(node.x() - 6, node.y() - rad - 8)
                pin.arcTo(QRectF(node.x() - 6, node.y() - rad - 14, 12, 12), 180, -180)
                pin.closeSubpath()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(a))
                p.drawPath(pin)
                p.setBrush(QColor(theme.on_accent(a)))
                p.drawEllipse(QPointF(node.x(), node.y() - rad - 8), 2.2, 2.2)
            f = theme.ui_font(8.2)
            f.setWeight(QFont.Weight.DemiBold if on else QFont.Weight.Medium)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT if on else theme.TEXT2 if pre else theme.TEXT3))
            tw = p.fontMetrics().horizontalAdvance(text)
            ty = node.y() + rad + 14 if name == "End portal" else node.y() + 5
            tx = node.x() - tw / 2 if name == "End portal" else node.x() + rad + 8
            p.drawText(QPointF(min(tx, w - tw - 8), ty), text)
        p.setFont(theme.ui_font(7.6))
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QPointF(x0 + 12, h - 10), "position taken at")
        p.restore()
        p.setPen(QPen(QColor(theme.LINE2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(frame)


# ── a village lived in, abandoned, or either ─────────────────────────────────
class VariantPicker(QWidget):
    chosen = Signal(str)
    NAMES = ["Any", "Not abandoned", "Abandoned"]
    SHOWN = {"Any": "Either", "Not abandoned": "Lived in", "Abandoned": "Abandoned"}

    def __init__(self, value="Any", parent=None):
        super().__init__(parent)
        self.value_ = value if value in self.NAMES else "Any"
        self._hot = None
        self.setMouseTracking(True)
        self.setFixedHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, v):
        if v in self.NAMES and v != self.value_:
            self.value_ = v
            self.update()

    def _cards(self):
        w = (self.width() - 2 * 6) / 3.0
        return [QRectF(i * (w + 6), 0, w, self.height()).adjusted(0.5, 0.5, -0.5, -0.5) for i in range(3)]

    def _at(self, pos):
        for i, r in enumerate(self._cards()):
            if r.contains(pos):
                return self.NAMES[i]
        return None

    def mouseMoveEvent(self, e):
        h = self._at(e.position())
        if h != self._hot:
            self._hot = h
            self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hot = None
        self.update()

    def mousePressEvent(self, e):
        v = self._at(e.position())
        if e.button() == Qt.MouseButton.LeftButton and v and v != self.value_:
            self.chosen.emit(v)

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            v = self._at(QPointF(e.pos()))
            if v == "Abandoned":
                QToolTip.showText(e.globalPos(), "A zombie village (2 % of the villages): zombie villagers, "
                                                 "broken houses.", self)
                return True
            QToolTip.hideText()
            return True
        return super().event(e)

    @staticmethod
    def _house(p, x, base, s, lived):
        """a small house: lit windows and smoke when lived in; mossy, dark and cobwebbed otherwise"""
        wall = QColor("#c9a36b" if lived else "#6f7a5c")
        roof = QColor("#8a4b32" if lived else "#4d5a45")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(wall)
        p.drawRect(QRectF(x - 9 * s, base - 11 * s, 18 * s, 11 * s))
        tri = QPainterPath(QPointF(x - 11.5 * s, base - 11 * s))
        tri.lineTo(x, base - 20 * s)
        tri.lineTo(x + 11.5 * s, base - 11 * s)
        tri.closeSubpath()
        p.fillPath(tri, roof)
        p.setBrush(QColor("#ffd86b" if lived else "#1c211b"))
        p.drawRect(QRectF(x - 6 * s, base - 8.5 * s, 4 * s, 4 * s))
        p.setBrush(QColor("#5a3a22" if lived else "#1c211b"))
        p.drawRect(QRectF(x + 1.5 * s, base - 7 * s, 4 * s, 7 * s))
        if lived:
            p.setBrush(_c("#ffffff", 0.35))
            for i, (dx, dy, rr) in enumerate(((5, -23, 1.8), (7, -27, 2.3), (6, -32, 2.8))):
                p.drawEllipse(QPointF(x + dx * s, base + dy * s), rr * s, rr * s)
        else:
            p.setPen(QPen(_c("#ffffff", 0.55), 0.8))
            cx, cy = x - 9 * s, base - 11 * s
            for ang in (0.2, 0.75, 1.3):
                p.drawLine(QPointF(cx, cy), QPointF(cx + 8 * s * math.cos(ang), cy + 8 * s * math.sin(ang)))
            for rr in (3.5, 6.5):
                p.drawArc(QRectF(cx - rr * s, cy - rr * s, 2 * rr * s, 2 * rr * s), int(-80 * 16), int(70 * 16))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = theme.ACCENT
        for name, r in zip(self.NAMES, self._cards()):
            on, hov = name == self.value_, name == self._hot
            path = QPainterPath()
            path.addRoundedRect(r, 11, 11)
            p.fillPath(path, QColor(theme._mix(theme.field_bg(), a, 0.20 if on else 0.06 if hov else 0.0)))
            p.save()
            p.setClipPath(path)
            ground = QRectF(r.left(), r.bottom() - 30, r.width(), 12)
            p.fillRect(ground, QColor(theme._mix(theme.field_bg(), "#5c9a4a" if name != "Abandoned" else "#4b5a3f", 0.55)))
            base, cx = ground.top() + 2, r.center().x()
            if name == "Any":
                self._house(p, cx - 15, base, 1.0, True)
                self._house(p, cx + 15, base, 1.0, False)
            else:
                self._house(p, cx - 15, base, 0.85, name == "Not abandoned")
                self._house(p, cx + 14, base, 1.1, name == "Not abandoned")
            p.restore()
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(a) if on else QColor(theme.LINE2 if hov else theme.LINE), 1.6 if on else 1))
            p.drawPath(path)
            f = theme.ui_font(8.4)
            f.setWeight(QFont.Weight.DemiBold if on else QFont.Weight.Medium)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT if on else theme.TEXT2))
            p.drawText(QRectF(r.left(), r.bottom() - 18, r.width(), 16), Qt.AlignmentFlag.AlignCenter,
                       self.SHOWN[name])


# ── the options of a Segmented, as cards ─────────────────────────────────────
class ChoiceCards(QWidget):
    def __init__(self, seg, glyphs=None, height=66, parent=None):
        super().__init__(parent)
        self.seg, self.glyphs = seg, glyphs or {}
        self._hot = None
        seg.setParent(self)
        seg.hide()
        self.setMouseTracking(True)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        seg.group.buttonToggled.connect(lambda *_: self.update())
        self._tick = QTimer(self)                # an option enabled / disabled from outside
        self._tick.setInterval(400)
        self._tick.timeout.connect(self.update)

    def showEvent(self, e):
        super().showEvent(e)
        self._tick.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._tick.stop()

    def _cards(self):
        names = list(self.seg.buttons)
        w = (self.width() - 6 * (len(names) - 1)) / float(len(names))
        return [(n, QRectF(i * (w + 6), 0, w, self.height()).adjusted(0.5, 0.5, -0.5, -0.5))
                for i, n in enumerate(names)]

    def _at(self, pos):
        for n, r in self._cards():
            if r.contains(pos):
                return n
        return None

    def mouseMoveEvent(self, e):
        h = self._at(e.position())
        if h != self._hot:
            self._hot = h
            self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hot = None
        self.update()

    def mousePressEvent(self, e):
        n = self._at(e.position())
        if e.button() == Qt.MouseButton.LeftButton and n and self.seg.buttons[n].isEnabled():
            self.seg.buttons[n].click()
            self.update()

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            n = self._at(QPointF(e.pos()))
            tip = self.seg.buttons[n].toolTip() if n else ""
            if tip:
                QToolTip.showText(e.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def _glyph(self, p, name, c, col):
        cx, cy = c.x(), c.y()
        pen = QPen(col, 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if name == "auto":                       # a spark: SSA chooses
            star = QPainterPath(QPointF(cx, cy - 9))
            for ang, rad in ((45, 3), (90, 9), (135, 3), (180, 9), (225, 3), (270, 9), (315, 3), (360, 9)):
                a = math.radians(ang - 90)
                star.lineTo(cx + rad * math.cos(a), cy + rad * math.sin(a))
            p.setBrush(col)
            p.drawPath(star)
        elif name == "plan":                     # measured: bars
            for i, hh in enumerate((6, 12, 9, 15)):
                p.drawLine(QPointF(cx - 7.5 + i * 5, cy + 8), QPointF(cx - 7.5 + i * 5, cy + 8 - hh))
        elif name == "set":                      # as set by hand: sliders
            for i, k in enumerate((0.3, 0.7, 0.45)):
                y = cy - 6 + i * 6
                p.drawLine(QPointF(cx - 9, y), QPointF(cx + 9, y))
                p.setBrush(col)
                p.drawEllipse(QPointF(cx - 9 + 18 * k, y), 2.3, 2.3)
                p.setBrush(Qt.BrushStyle.NoBrush)
        elif name == "cpu":                      # the processor alone
            p.drawRoundedRect(QRectF(cx - 6.5, cy - 6.5, 13, 13), 2.5, 2.5)
            p.drawRect(QRectF(cx - 2.5, cy - 2.5, 5, 5))
            for d in (-3.5, 0, 3.5):
                p.drawLine(QPointF(cx + d, cy - 6.5), QPointF(cx + d, cy - 10))
                p.drawLine(QPointF(cx + d, cy + 6.5), QPointF(cx + d, cy + 10))
                p.drawLine(QPointF(cx - 6.5, cy + d), QPointF(cx - 10, cy + d))
                p.drawLine(QPointF(cx + 6.5, cy + d), QPointF(cx + 10, cy + d))
        else:
            p.setBrush(col)
            p.drawEllipse(c, 3.5, 3.5)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = theme.ACCENT
        cur = self.seg.value()
        for name, r in self._cards():
            b = self.seg.buttons[name]
            on, live = name == cur, b.isEnabled()
            hov = name == self._hot and live
            p.setOpacity(1.0 if live else 0.36)
            p.setBrush(QColor(theme._mix(theme.field_bg(), a, 0.22 if on else 0.07 if hov else 0.0)))
            p.setPen(QPen(QColor(a) if on else QColor(theme.LINE2 if hov else theme.LINE), 1.5 if on else 1))
            p.drawRoundedRect(r, 11, 11)
            col = QColor(a) if on else QColor(theme.TEXT2 if hov else theme.TEXT3)
            self._glyph(p, self.glyphs.get(name, ""), QPointF(r.center().x(), r.top() + 22), col)
            f = theme.ui_font(8.6)
            f.setWeight(QFont.Weight.DemiBold if on else QFont.Weight.Medium)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT if on else theme.TEXT2))
            p.drawText(QRectF(r.left() + 2, r.bottom() - 24, r.width() - 4, 18), Qt.AlignmentFlag.AlignCenter,
                       p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(r.width() - 6)))
        p.setOpacity(1.0)
