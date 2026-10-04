"""
qtui/stronghold_visuals.py — the pictures of the Stronghold search panel (03/10).

  PortalPicker   the end portal seen from above: 12 frames, click one to choose
                 how many eyes (hover previews); the centre shows the count, and
                 the starry End void when all 12 are filled.
  HeightGauge    a slice of the world (sky, surface, stone) with a stronghold
                 rising to the chosen Top Y: drag the line to change it. In
                 1.13–1.16 it also shows how rare that height is (measured).

Both only drive a backing spin box (the page keeps using it), so nothing else
in the page changes.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

# ── rarity of a 1.13–1.16 stronghold top (P(top >= y), 03/10) ─────────────────
# 1.4 M strongholds with the exact generator up to Y 66, 26 M for 68 / 70, then
# the extrapolation of SSA_topy_116.md (72: ×3, 74: ×3–5 uncertainty).
_P116 = [(30, 0.97), (40, 0.68), (44, 0.50), (48, 0.29), (50, 0.18), (52, 0.067),
         (54, 4.9e-3), (56, 2.85e-3), (58, 1.5e-3), (60, 7.9e-4), (62, 4.3e-4),
         (64, 2.0e-4), (66, 1.7e-5), (68, 1.0e-6), (70, 4e-8), (72, 1e-9),
         (74, 1.5e-11), (76, 1e-13), (78, 1e-15), (80, 1e-16)]

TIERS = (  # (lowest probability of the tier, name, colour)
    (1e-3, "Common", None),
    (1e-5, "Rare", "#4ea1ff"),
    (1e-7, "Epic", "#b46cf0"),
    (1e-9, "Legendary", "#ff9a2e"),
    (1e-12, "Record", "#ffcc33"),
    (0.0, "Out of reach", "#ff4d6d"),
)


# top of the PORTAL ROOM (same generator, 03/10): 6.4 M strongholds up to Y 64
# (3 cases at 64), then a rough extrapolation (the tail falls fast: ×30 / 2 blocks)
_PROOM = [(20, 0.95), (24, 0.89), (28, 0.79), (32, 0.65), (36, 0.47), (40, 0.30),
          (44, 0.14), (46, 0.085), (48, 0.039), (50, 0.0107), (52, 1.3e-3), (54, 4.3e-4),
          (56, 2.2e-4), (58, 1.04e-4), (60, 3.6e-5), (62, 1.4e-5), (64, 5e-7),
          (66, 2e-8), (68, 1e-9), (70, 5e-11)]


def topy_probability(y: int, mc: int, room: bool = False):
    """P(top >= y) of a 1.13–1.16 stronghold (mc <= 20) — or of its portal room
    (room=True) — else None."""
    if mc > 20:
        return None
    tab = _PROOM if room else _P116
    if y <= tab[0][0]:
        return tab[0][1]
    if y >= tab[-1][0]:
        return tab[-1][1] * 10 ** (-(y - tab[-1][0]) / 2)
    for (y0, p0), (y1, p1) in zip(tab, tab[1:]):
        if y0 <= y <= y1:
            t = (y - y0) / (y1 - y0)
            return 10 ** (math.log10(p0) + t * (math.log10(p1) - math.log10(p0)))
    return None


def tier_of(p):
    for lo, name, col in TIERS:
        if p >= lo:
            return name, col or theme.TEXT3
    return TIERS[-1][1], TIERS[-1][2]


def one_in(p) -> str:
    """'1 in 52 000' / '1 in 25 M' / '1 in 1.0 T'."""
    if p is None or p <= 0:
        return "—"
    n = 1 / p
    if n < 1.5:
        return "almost all"
    for div, suf in ((1e15, " P"), (1e12, " T"), (1e9, " B"), (1e6, " M")):
        if n >= div:
            return f"1 in {n / div:.1f}{suf}" if n / div < 100 else f"1 in {n / div:.0f}{suf}"
    return f"1 in {int(round(n, -1 if n > 100 else 0)):,}".replace(",", " ")


def _mix(a: str, b: str, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t),
                  int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t))


# ═════════════════════════════════════════════════════════════════════════════
class PortalPicker(QWidget):
    """End portal from above. value() = number of eyes (1..12); exact = « = »."""
    changed = Signal(int)

    # frame cells (col, row) on the 5 × 5 grid, clockwise from the top-left one
    CELLS = [(1, 0), (2, 0), (3, 0), (4, 1), (4, 2), (4, 3),
             (3, 4), (2, 4), (1, 4), (0, 3), (0, 2), (0, 1)]
    # fixed « stars » of the End void (x, y in 0..1, size)
    STARS = [(0.18, 0.22, 1.6), (0.71, 0.15, 1.2), (0.42, 0.48, 2.0), (0.83, 0.62, 1.4),
             (0.27, 0.78, 1.1), (0.58, 0.86, 1.7), (0.12, 0.55, 1.0), (0.64, 0.35, 1.0),
             (0.36, 0.12, 0.9), (0.90, 0.88, 1.2), (0.50, 0.68, 0.8), (0.05, 0.92, 1.3)]

    def __init__(self, value=12, parent=None, style="eyes"):
        super().__init__(parent)
        self._value = int(value)
        self._exact = False
        self._hover = -1
        self._style = style                  # "eyes" | "broken"
        self._causes = ["#b46cf0"]           # colours of the broken frames (broken style)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(150, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(156)
        self.setToolTip("Eyes in the end portal: click a frame to choose how many "
                        "(hover to preview)." if style == "eyes" else
                        "Broken frames of the end portal: click a frame to choose the minimum\n"
                        "(hover to preview). Their colours are the causes searched.")

    # ── API ──
    def value(self) -> int:
        return self._value

    def set_value(self, v):
        v = max(1, min(12, int(v)))
        if v != self._value:
            self._value = v
            self.update()

    def set_causes(self, colors):
        self._causes = list(colors) or ["#6b7280"]
        self.update()

    def set_exact(self, on):
        self._exact = bool(on)
        self.update()

    # ── geometry ──
    def _geom(self):
        s = min(self.width(), self.height()) - 6
        cell = s / 5.0
        ox = (self.width() - s) / 2
        oy = (self.height() - s) / 2
        return ox, oy, cell

    def _cell_rect(self, i):
        ox, oy, c = self._geom()
        col, row = self.CELLS[i]
        g = c * 0.06
        return QRectF(ox + col * c + g, oy + row * c + g, c - 2 * g, c - 2 * g)

    def _hit(self, pos):
        for i in range(12):
            if self._cell_rect(i).adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                return i
        return -1

    # ── events ──
    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if h >= 0 and e.button() == Qt.MouseButton.LeftButton:
            self.set_value(h + 1)
            self.changed.emit(self._value)

    # ── painting ──
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ox, oy, c = self._geom()
        if self._style == "broken":
            self._paint_broken(p, ox, oy, c)
            p.end()
            return
        full = self._value == 12
        # the End void (centre 3 × 3)
        void = QRectF(ox + c * 1.06, oy + c * 1.06, c * 2.88, c * 2.88)
        g = QLinearGradient(void.topLeft(), void.bottomRight())
        g.setColorAt(0, QColor("#0b1424" if full else "#151a22"))
        g.setColorAt(1, QColor("#05070d" if full else "#0d1016"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(void, 6, 6)
        if full:                                         # the portal is open
            glow = QColor("#3fd6a0"); glow.setAlpha(46)
            p.setBrush(glow)
            p.drawRoundedRect(void.adjusted(4, 4, -4, -4), 8, 8)
            for (sx, sy, sz) in self.STARS:
                col = QColor("#cdeee2" if sz > 1.3 else "#7fb8ff")
                col.setAlpha(220)
                p.setBrush(col)
                p.drawEllipse(QPointF(void.left() + sx * void.width(),
                                      void.top() + sy * void.height()), sz, sz)
        # count in the middle
        f = QFont(theme.ui_font(9.5)); f.setPixelSize(int(c * 0.95)); f.setBold(True)
        p.setFont(f)
        txt = ("=" if self._exact else ("" if full else "≥")) + str(self._value)
        p.setPen(QColor("#e8fff6") if full else QColor(theme.TEXT))
        p.drawText(void, Qt.AlignmentFlag.AlignCenter, txt)
        # the 12 frames
        hov = self._hover
        for i in range(12):
            r = self._cell_rect(i)
            on = i < self._value
            preview = hov >= 0 and i <= hov and not on
            unprev = hov >= 0 and i > hov and on
            # frame block: end-stone sides, green-grey top
            p.setPen(QPen(QColor("#2f3d35"), 1.2))
            p.setBrush(QColor("#c9c79a"))
            p.drawRoundedRect(r, 3, 3)
            top = r.adjusted(r.width() * 0.10, r.height() * 0.10, -r.width() * 0.10, -r.height() * 0.10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#4f7363"))
            p.drawRoundedRect(top, 2, 2)
            eye = top.adjusted(top.width() * 0.2, top.height() * 0.2, -top.width() * 0.2, -top.height() * 0.2)
            if on and not unprev:
                p.setBrush(QColor("#0d3d33"))
                p.drawEllipse(eye)
                p.setBrush(QColor("#38c98f"))
                p.drawEllipse(eye.adjusted(eye.width() * 0.22, eye.height() * 0.22,
                                           -eye.width() * 0.22, -eye.height() * 0.22))
                p.setBrush(QColor("#0b1f1a"))
                p.drawEllipse(eye.center(), eye.width() * 0.11, eye.height() * 0.11)
            elif preview or unprev:
                col = QColor("#38c98f" if preview else "#ff6b6b"); col.setAlpha(110)
                p.setBrush(col)
                p.drawEllipse(eye)
            else:
                p.setBrush(QColor("#2c4038"))
                p.drawEllipse(eye.adjusted(eye.width() * 0.18, eye.height() * 0.18,
                                           -eye.width() * 0.18, -eye.height() * 0.18))
            if i == hov:
                p.setPen(QPen(QColor(theme.ACCENT), 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 4, 4)
        p.end()


    def _paint_broken(self, p, ox, oy, c):
        """Broken frames: holes with the colour of a cause, the rest intact."""
        void = QRectF(ox + c * 1.06, oy + c * 1.06, c * 2.88, c * 2.88)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#151a22"))
        p.drawRoundedRect(void, 6, 6)
        col0 = QColor(self._causes[0])
        f = QFont(theme.ui_font(9.5)); f.setPixelSize(int(c * 0.95)); f.setBold(True)
        p.setFont(f)
        p.setPen(col0.lighter(130))
        txt = ("=" if self._exact else "≥") + str(self._value)
        p.drawText(void, Qt.AlignmentFlag.AlignCenter, txt)
        hov = self._hover
        for i in range(12):
            r = self._cell_rect(i)
            on = i < self._value
            preview = hov >= 0 and i <= hov and not on
            unprev = hov >= 0 and i > hov and on
            if on and not unprev:
                cc = QColor(self._causes[i % len(self._causes)])
                # the hole: what replaced the frame (lava, water, netherrack / air)
                p.setPen(QPen(QColor("#2f3d35"), 1.2, Qt.PenStyle.DashLine))
                p.setBrush(QColor("#10141a"))
                p.drawRoundedRect(r, 3, 3)
                fill = QColor(cc); fill.setAlpha(150)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(fill)
                inner = r.adjusted(r.width() * 0.16, r.height() * 0.16,
                                   -r.width() * 0.16, -r.height() * 0.16)
                p.drawRoundedRect(inner, 3, 3)
                # a few broken shards
                p.setBrush(QColor("#c9c79a"))
                for (fx, fy, fs) in ((0.08, 0.10, 0.18), (0.74, 0.70, 0.16), (0.70, 0.06, 0.12)):
                    p.drawRect(QRectF(r.left() + fx * r.width(), r.top() + fy * r.height(),
                                      fs * r.width(), fs * r.height()))
            else:
                p.setPen(QPen(QColor("#2f3d35"), 1.2))
                p.setBrush(QColor("#c9c79a"))
                p.drawRoundedRect(r, 3, 3)
                top = r.adjusted(r.width() * 0.10, r.height() * 0.10, -r.width() * 0.10, -r.height() * 0.10)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#4f7363"))
                p.drawRoundedRect(top, 2, 2)
                if preview or unprev:
                    cc = QColor(self._causes[i % len(self._causes)] if preview else "#9aa0a6")
                    cc.setAlpha(120)
                    p.setBrush(cc)
                    p.drawRoundedRect(top.adjusted(2, 2, -2, -2), 2, 2)
            if i == hov:
                p.setPen(QPen(QColor(theme.ACCENT), 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 4, 4)


# ═════════════════════════════════════════════════════════════════════════════
class HeightGauge(QWidget):
    """A slice of the world with a stronghold whose top is at the threshold."""
    changed = Signal(int)

    def __init__(self, value=60, parent=None):
        super().__init__(parent)
        self._value = int(value)
        self._mc = 20
        self._room = False
        self._drag = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFixedHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("Top of the stronghold after Minecraft moves it below sea level.\n"
                        "Drag the line (or type the value) to choose the minimum height:\n"
                        "a stronghold close to the surface can be seen or dug into from above.")

    def value(self):
        return self._value

    def set_value(self, v):
        v = int(v)
        if v != self._value:
            self._value = v
            self.update()

    def set_mc(self, mc):
        self._mc = int(mc)
        self.update()

    def set_room(self, on):
        """The threshold is the top of the portal room (not of the stronghold)."""
        self._room = bool(on)
        self.update()

    # visible Y range: around sea level
    def _range(self):
        sea = 63
        return sea - 43, sea + 22                        # Y 20 … 85

    def _y_to_px(self, y):
        lo, hi = self._range()
        top, bot = 8.0, self.height() - 24.0
        return bot - (y - lo) / (hi - lo) * (bot - top)

    def _px_to_y(self, py):
        lo, hi = self._range()
        top, bot = 8.0, self.height() - 24.0
        return int(round(lo + (bot - py) / (bot - top) * (hi - lo)))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._set_from(e.position().y())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._set_from(e.position().y())

    def mouseReleaseEvent(self, e):
        self._drag = False

    def _set_from(self, py):
        lo, hi = self._range()
        v = max(lo, min(hi, self._px_to_y(py)))
        if v != self._value:
            self._value = v
            self.update()
            self.changed.emit(v)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        lo, hi = self._range()
        top_px, bot_px = 8.0, self.height() - 24.0
        prob = topy_probability(self._value, self._mc, self._room)
        name, tcol = tier_of(prob) if prob is not None else ("", theme.ACCENT)
        scene = QRectF(0, top_px, w, bot_px - top_px)
        clip = QPainterPath(); clip.addRoundedRect(scene, 8, 8)
        p.setClipPath(clip)
        # sky
        surf = self._y_to_px(66)
        g = QLinearGradient(0, top_px, 0, surf)
        g.setColorAt(0, QColor("#1d3557" if not theme.IS_LIGHT else "#9cc9f0"))
        g.setColorAt(1, QColor("#3d6a8f" if not theme.IS_LIGHT else "#d6ecfa"))
        p.fillRect(QRectF(0, top_px, w, surf - top_px), g)
        # ground: grass, dirt, stone, deeper stone
        p.fillRect(QRectF(0, surf, w, 4), QColor("#5f9e3a"))
        dirt = self._y_to_px(62)
        p.fillRect(QRectF(0, surf + 4, w, dirt - surf - 4), QColor("#7a5a3a"))
        g2 = QLinearGradient(0, dirt, 0, bot_px)
        g2.setColorAt(0, QColor("#6f7377")); g2.setColorAt(1, QColor("#3e4146"))
        p.fillRect(QRectF(0, dirt, w, bot_px - dirt), g2)
        # sea level
        sea = self._y_to_px(63)
        pen = QPen(QColor(110, 180, 255, 150), 1, Qt.PenStyle.DotLine)
        p.setPen(pen); p.drawLine(QPointF(0, sea), QPointF(w, sea))
        # the stronghold: blocky rooms from the bottom up to the threshold
        thr = max(lo, min(hi, self._value))
        ty = self._y_to_px(thr)
        cx = w * 0.40
        base = bot_px
        rooms = [(-0.22, 0.30, 0.00, 0.22), (0.05, 0.42, 0.10, 0.40), (-0.30, 0.10, 0.25, 0.55),
                 (-0.05, 0.22, 0.45, 0.78), (0.10, 0.34, 0.70, 1.00)]
        h = max(10.0, base - ty)
        brick = QColor("#9aa0a6"); dark = QColor("#4a4f55")
        if self._room:                       # the rest of the stronghold goes on above
            ghost = QColor(154, 160, 166, 70)
            p.setPen(QPen(QColor(74, 79, 85, 90), 1, Qt.PenStyle.DashLine)); p.setBrush(ghost)
            p.drawRect(QRectF(cx + 0.02 * w, ty - min(34.0, ty - top_px - 2), 0.14 * w,
                              min(34.0, ty - top_px - 2)))
        for (x0, x1, f0, f1) in rooms:
            r = QRectF(cx + x0 * w * 0.5, base - f1 * h, (x1 - x0) * w * 0.5, (f1 - f0) * h)
            p.setPen(QPen(dark, 1)); p.setBrush(brick)
            p.drawRect(r)
            # brick lines
            p.setPen(QPen(QColor(70, 75, 80, 90), 1))
            yy = r.top() + 5
            while yy < r.bottom() - 1:
                p.drawLine(QPointF(r.left() + 1, yy), QPointF(r.right() - 1, yy))
                yy += 5
        # the portal room: at the bottom, or at the line (portal room top mode)
        if self._room:
            x0, x1, f0, f1 = rooms[-1]
            pr = QRectF(cx + x0 * w * 0.5, base - f1 * h, (x1 - x0) * w * 0.5, (f1 - f0) * h * 0.6)
        else:
            pr = QRectF(cx - w * 0.10, base - h * 0.18, w * 0.11, h * 0.14)
        glow = QColor("#3fd6a0"); glow.setAlpha(190 if self._room else 170)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(glow); p.drawRect(pr)
        p.setClipping(False)
        # threshold line + labels
        col = QColor(tcol)
        p.setPen(QPen(col, 2.2, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(0, ty), QPointF(w, ty))
        f = QFont(theme.ui_font(9.5)); f.setPixelSize(19); f.setBold(True)
        p.setFont(f)
        lab = ("Room Y " if self._room else "Y ") + str(self._value)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(lab) + 14
        box = QRectF(w - tw - 6, ty - 15, tw, 26)
        bg = QColor(theme.BG1 or "#1b1f27"); bg.setAlpha(225)
        p.setPen(QPen(col, 1.4)); p.setBrush(bg)
        p.drawRoundedRect(box, 7, 7)
        p.setPen(col if prob is not None else QColor(theme.TEXT))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, lab)
        # sea level tag
        f2 = QFont(theme.ui_font(8)); f2.setPixelSize(10)
        p.setFont(f2)
        p.setPen(QColor(150, 200, 255, 200))
        p.drawText(QPointF(6, sea - 3), "sea 63")
        # rarity under the scene
        if prob is not None:
            f3 = QFont(theme.ui_font(9)); f3.setPixelSize(12); f3.setBold(True)
            p.setFont(f3)
            p.setPen(col)
            p.drawText(QRectF(4, bot_px + 3, w * 0.5, 20),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            f4 = QFont(theme.ui_font(9)); f4.setPixelSize(12)
            p.setFont(f4)
            p.setPen(QColor(theme.TEXT2 or theme.TEXT))
            p.drawText(QRectF(w * 0.35, bot_px + 3, w * 0.65 - 4, 20),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{one_in(prob)} " + ("portal rooms" if self._room else "strongholds"))
        p.end()
