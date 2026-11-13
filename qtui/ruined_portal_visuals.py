"""
qtui/ruined_portal_visuals.py — (07/11) the pictures of the Ruined portal condition.

  PlacementPicker  the five places a ruined portal stands (surface, half buried, sea
                   floor, in a mountain, underground) as little scenes: click to keep
                   only those (none = any). Each shows how common it is.
  PortalFrame      a nether portal frame: obsidian, crying obsidian (as many as the
                   crying range lets through), the missing blocks — and, when
                   « Completable » is on, the portal lit (animated) with what its chest
                   brings.

Odds: ruined_portal_filter.odds (real portals + exact crying obsidian and chest).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme
from .stronghold_visuals import one_in, tier_of

PLACEMENTS = ("on_land_surface", "partly_buried", "on_ocean_floor", "in_mountain", "underground")
PL_NAMES = {"on_land_surface": "Surface", "partly_buried": "Half buried",
            "on_ocean_floor": "Sea floor", "in_mountain": "Mountain", "underground": "Underground"}
PL_TIPS = {"on_land_surface": "Standing on the ground, in the open air.",
           "partly_buried": "Sunk 2 to 8 blocks into the ground (desert portals).",
           "on_ocean_floor": "On the floor of an ocean or a swamp, under water.",
           "in_mountain": "Inside a mountain (between Y 70 and the surface).",
           "underground": "Buried somewhere between the bottom of the world and the surface."}
KINDS = ("standard", "desert", "jungle", "swamp", "mountain", "ocean")
KIND_NAMES = {"standard": "Plains", "desert": "Desert", "jungle": "Jungle", "swamp": "Swamp",
              "mountain": "Mountain", "ocean": "Ocean"}
KIND_COLORS = {"standard": "#7cb342", "desert": "#e2c27a", "jungle": "#2fa35a", "swamp": "#6b8f4e",
               "mountain": "#a3a8b0", "ocean": "#3b82f6"}
OBS, OBS_HI, CRY, CRY_HI, PORTAL = "#1c1230", "#3a2a5c", "#2b0f4d", "#b05cff", "#8b3dff"



def _font(pt, bold=False):
    f = QFont(theme.ui_font(pt))
    f.setBold(bold)
    return f


# ── statistics (07/11: exact odds, ruined_portal_filter.odds) ──────────────────
def share(mc, placements=(), kinds=(), cry_lo=None, cry_hi=None, completable=False):
    """the probability that a ruined portal passes these filters (None: no data)"""
    import ruined_portal_filter as rpf
    try:
        return rpf.odds(mc, placements, kinds, cry_lo, cry_hi, completable)
    except Exception:                                   # noqa: BLE001
        return None


def crying_hist(mc, placements=(), kinds=(), completable=False, n=6):
    """[share of the portals with exactly k crying obsidian in their frame] k = 0..n"""
    import ruined_portal_filter as rpf
    try:
        return rpf.crying_odds(mc, placements, kinds, completable, n)
    except Exception:                                   # noqa: BLE001
        return [0.0] * (n + 1)


def placement_shares(mc):
    import ruined_portal_filter as rpf
    _n, rows = rpf.stats_rows(mc)
    out = {p: 0.0 for p in PLACEMENTS}
    for pl, *_r, w in rows:
        if pl in out:
            out[pl] += w
    return out


def stats_info(mc) -> str:
    import ruined_portal_filter as rpf
    n, _rows = rpf.stats_rows(mc)
    if not n:
        return "No statistics file."
    nt = f"{n:,}".replace(",", " ")
    v = "1.16 – 1.17 (measured on 1.16.5)" if int(mc) < 22 else "1.18+ (measured on 26.3)"
    return (f"Where the portals stand and what the netherrack eats: {nt} real portals,\n"
            f"{v}. The crying obsidian (every pattern) and the chest (its loot table)\n"
            "are computed exactly, so even the rarest filter gets its probability.")


def rarity_text(prob, mc):
    """(« ~4.1 % of the ruined portals », « ~1 in 24 », colour)"""
    if prob is None:
        return "—", "—", QColor(theme.TEXT3 or theme.TEXT2)
    if prob <= 0:
        return "Impossible", "Impossible", QColor("#ff4d6d")
    _name, col = tier_of(prob)
    v = prob * 100
    if v >= 0.1:
        pct = f"~{v:.1f} %"
    else:                                          # 2 significant digits, no exponent
        d = min(12, 1 - int(math.floor(math.log10(v))))
        pct = f"~{v:.{d}f} %"
    return f"{pct} of the ruined portals", "~" + one_in(prob), QColor(col or theme.TEXT)


# ── blocks ────────────────────────────────────────────────────────────────────
def paint_obsidian(p, r, crying=False, glow=0.0):
    """an obsidian (or crying obsidian) block in r"""
    rad = max(1.5, r.width() * 0.12)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor(OBS_HI if not crying else "#4a1f7a"))
    g.setColorAt(1, QColor(OBS if not crying else CRY))
    p.setPen(QPen(QColor("#0b0714"), 1))
    p.setBrush(g)
    p.drawRoundedRect(r, rad, rad)
    # facets
    p.setPen(Qt.PenStyle.NoPen)
    hi = QColor("#6b4fa0" if not crying else "#c78cff"); hi.setAlpha(70)
    p.setBrush(hi)
    w, h = r.width(), r.height()
    for fx, fy, fw, fh in ((0.18, 0.2, 0.22, 0.14), (0.58, 0.52, 0.2, 0.12), (0.3, 0.7, 0.14, 0.1)):
        p.drawRect(QRectF(r.x() + fx * w, r.y() + fy * h, fw * w, fh * h))
    if crying:                                   # the purple tears
        c = QColor(CRY_HI); c.setAlpha(int(170 + 85 * glow))
        p.setBrush(c)
        for fx, fy, fs in ((0.25, 0.32, 0.13), (0.62, 0.22, 0.11), (0.48, 0.66, 0.14), (0.75, 0.7, 0.1)):
            p.drawEllipse(QPointF(r.x() + fx * w, r.y() + fy * h), fs * w * 0.5, fs * h * 0.75)


def paint_missing(p, r, accent=None):
    rad = max(1.5, r.width() * 0.12)
    col = QColor(accent or "#aab3c2")
    pen = QPen(col, 1.6, Qt.PenStyle.DashLine)
    p.setPen(pen)
    p.setBrush(QColor("#151821"))
    p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad, rad)


def paint_mini_portal(p, x, y, cell, lit=False, ghost=False):
    """a tiny 4 × 5 frame whose bottom-left corner is at (x, y) (y = its bottom)"""
    inside = QRectF(x + cell, y - 4 * cell, 2 * cell, 3 * cell)
    if not lit:
        p.fillRect(inside, QColor(10, 6, 20, 70))
    for j in range(5):
        for i in range(4):
            edge = i in (0, 3) or j in (0, 4)
            if not edge:
                continue
            if (i, j) in ((3, 4),):               # a ruin: a block gone
                continue
            r = QRectF(x + i * cell, y - (j + 1) * cell, cell - 0.6, cell - 0.6)
            c = QColor("#1f1533" if (i + j) % 3 else "#3b1e66")
            if ghost:
                c.setAlpha(120)
            p.fillRect(r, c)
    if lit:
        inner = QRectF(x + cell, y - 4 * cell, 2 * cell, 3 * cell)
        c = QColor(PORTAL); c.setAlpha(180)
        p.fillRect(inner, c)


# ═════════════════════════════════════════════════════════════════════════════
class PlacementPicker(QWidget):
    """Five little scenes; value() = the chosen placements (empty = any)."""
    changed = Signal(list)

    def __init__(self, chosen=(), parent=None):
        super().__init__(parent)
        self._sel = [t for t in PLACEMENTS if t in (chosen or ())]
        self._hover = -1
        self._share = {}
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(122)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def value(self):
        return list(self._sel)

    def set_shares(self, d):
        self._share = dict(d or {})
        self.update()

    def _rects(self):
        n = len(PLACEMENTS)
        gap = 7.0
        w = (self.width() - gap * (n - 1)) / n
        return [QRectF(i * (w + gap), 2, w, self.height() - 4) for i in range(n)]

    def _at(self, pos):
        for i, r in enumerate(self._rects()):
            if r.contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):
        i = self._at(e.position())
        if i != self._hover:
            self._hover = i
            self.setToolTip(PL_TIPS[PLACEMENTS[i]] if i >= 0 else "")
            self.update()

    def leaveEvent(self, _e):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        i = self._at(e.position())
        if i < 0:
            return
        t = PLACEMENTS[i]
        if t in self._sel:
            self._sel.remove(t)
        else:
            self._sel.append(t)
        self._sel = [x for x in PLACEMENTS if x in self._sel]
        if len(self._sel) == len(PLACEMENTS):
            self._sel = []
        self.update()
        self.changed.emit(self.value())

    def _scene(self, p, t, r):
        """the little picture of placement t in r"""
        W, H = r.width(), r.height()
        sky = QLinearGradient(r.topLeft(), r.bottomLeft())
        sky.setColorAt(0, QColor("#7fb2ff")); sky.setColorAt(1, QColor("#cfe3ff"))
        p.fillRect(r, sky)
        cell = max(3.0, min(W / 11.0, H / 9.5))
        cx = r.center().x() - 2 * cell
        if t == "on_land_surface":
            gy = r.y() + H * 0.70
            p.fillRect(QRectF(r.x(), gy, W, r.bottom() - gy), QColor("#7a5532"))
            p.fillRect(QRectF(r.x(), gy, W, cell * 0.8), QColor("#5f9e3a"))
            # netherrack around the portal
            p.fillRect(QRectF(cx - cell * 1.5, gy, cell * 7, cell * 0.8), QColor("#8a3b3b"))
            paint_mini_portal(p, cx, gy, cell)
        elif t == "partly_buried":
            p.fillRect(r, QColor("#f2d79a"))
            sky2 = QLinearGradient(r.topLeft(), r.bottomLeft())
            sky2.setColorAt(0, QColor("#8fc0ff")); sky2.setColorAt(1, QColor("#ffe7b8"))
            p.fillRect(QRectF(r.x(), r.y(), W, H * 0.55), sky2)
            gy = r.y() + H * 0.55
            p.fillRect(QRectF(r.x(), gy, W, r.bottom() - gy), QColor("#dcc285"))
            paint_mini_portal(p, cx, gy + cell * 2.4, cell)
            sand = QColor("#dcc285"); sand.setAlpha(150)
            p.fillRect(QRectF(cx - cell * 0.2, gy, cell * 4.4, cell * 2.4), sand)
        elif t == "on_ocean_floor":
            wy = r.y() + H * 0.18
            water = QLinearGradient(QPointF(r.x(), wy), r.bottomLeft())
            water.setColorAt(0, QColor("#3f7fe0")); water.setColorAt(1, QColor("#1d3f8f"))
            p.fillRect(QRectF(r.x(), wy, W, r.bottom() - wy), water)
            fy = r.y() + H * 0.82
            p.fillRect(QRectF(r.x(), fy, W, r.bottom() - fy), QColor("#8b7b5a"))
            paint_mini_portal(p, cx, fy, cell)
            veil = QColor("#2a5bd0"); veil.setAlpha(70)
            p.fillRect(QRectF(r.x(), wy, W, fy - wy), veil)
        elif t == "in_mountain":
            path = QPainterPath()
            path.moveTo(r.x(), r.bottom())
            path.lineTo(r.x() + W * 0.08, r.y() + H * 0.55)
            path.lineTo(r.x() + W * 0.45, r.y() + H * 0.10)
            path.lineTo(r.x() + W * 0.75, r.y() + H * 0.32)
            path.lineTo(r.right(), r.y() + H * 0.6)
            path.lineTo(r.right(), r.bottom())
            path.closeSubpath()
            p.fillPath(path, QColor("#8d8f93"))
            snow = QPainterPath()
            snow.moveTo(r.x() + W * 0.33, r.y() + H * 0.25)
            snow.lineTo(r.x() + W * 0.45, r.y() + H * 0.10)
            snow.lineTo(r.x() + W * 0.56, r.y() + H * 0.18)
            snow.closeSubpath()
            p.fillPath(snow, QColor("#f2f5f8"))
            gy = r.y() + H * 0.80
            pocket = QRectF(cx - cell * 0.6, gy - cell * 5.6, cell * 5.2, cell * 5.6)
            p.fillRect(pocket, QColor("#2a2c31"))
            paint_mini_portal(p, cx, gy, cell)
        else:                                          # underground
            gy = r.y() + H * 0.24
            p.fillRect(QRectF(r.x(), gy, W, r.bottom() - gy), QColor("#76787d"))
            p.fillRect(QRectF(r.x(), gy, W, cell * 0.8), QColor("#5f9e3a"))
            p.fillRect(QRectF(r.x(), gy + cell * 0.8, W, cell * 1.4), QColor("#7a5532"))
            by = r.y() + H * 0.93
            pocket = QRectF(cx - cell * 0.7, by - cell * 5.6, cell * 5.4, cell * 5.6)
            p.fillRect(pocket, QColor("#25272b"))
            paint_mini_portal(p, cx, by, cell)
            for fx, fy in ((0.12, 0.55), (0.84, 0.72), (0.2, 0.85)):
                p.fillRect(QRectF(r.x() + fx * W, r.y() + fy * H, cell, cell), QColor("#5d6066"))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        acc = QColor(theme.ACCENT or "#7c9cff")
        anyc = not self._sel
        for i, (t, r) in enumerate(zip(PLACEMENTS, self._rects())):
            on = t in self._sel
            hov = i == self._hover
            card = QPainterPath(); card.addRoundedRect(r, 10, 10)
            bg = QColor(theme.BG2 or "#2a2f38")
            if hov:
                bg = bg.lighter(118)
            p.fillPath(card, bg)
            pic = QRectF(r.x() + 5, r.y() + 5, r.width() - 10, r.height() - 42)
            clip = QPainterPath(); clip.addRoundedRect(pic, 7, 7)
            p.save(); p.setClipPath(clip)
            self._scene(p, t, pic)
            if not on and not anyc:
                p.fillRect(pic, QColor(20, 22, 28, 150))
            p.restore()
            p.setFont(_font(8.4, on))
            p.setPen(QColor(theme.TEXT if (on or anyc) else (theme.TEXT3 or theme.TEXT2)))
            p.drawText(QRectF(r.x(), r.bottom() - 36, r.width(), 18), Qt.AlignmentFlag.AlignCenter,
                       PL_NAMES[t])
            sh = self._share.get(t)
            if sh is not None:
                p.setFont(_font(7.6))
                p.setPen(QColor(theme.TEXT3 or theme.TEXT2))
                p.drawText(QRectF(r.x(), r.bottom() - 20, r.width(), 16), Qt.AlignmentFlag.AlignCenter,
                           f"{sh * 100:.0f} %" if sh >= 0.01 else f"{sh * 100:.1f} %")
            if on:
                p.setPen(QPen(acc, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 10, 10)
                c = QPointF(r.right() - 13, r.y() + 13)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(acc)
                p.drawEllipse(c, 7.5, 7.5)
                p.setPen(QPen(QColor("white"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(c.x() - 3.3, c.y()), QPointF(c.x() - 0.9, c.y() + 2.4))
                p.drawLine(QPointF(c.x() - 0.9, c.y() + 2.4), QPointF(c.x() + 3.6, c.y() - 2.6))
            else:
                p.setPen(QPen(QColor(theme.LINE or "#3a404a"), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        p.end()


# ═════════════════════════════════════════════════════════════════════════════
class PortalFrame(QWidget):
    """A nether portal frame (4 × 5, the corners faint: a portal does not need them):
    the crying obsidian the range lets through, two holes of a typical ruin, and when
    « completable », the portal lit with the chest's obsidian filling the holes."""

    # the 10 cells that matter, bottom row first (col, row from the bottom)
    CELLS = [(1, 0), (2, 0), (0, 1), (3, 1), (0, 2), (3, 2), (0, 3), (3, 3), (1, 4), (2, 4)]
    HOLES = (7, 9)                              # a typical ruin: 2 blocks gone
    CRY_ORDER = (2, 8, 5, 0, 4, 3, 6, 1)        # where the crying obsidian shows first

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lo, self._hi = 0, None
        self._comp = False
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self.setMinimumHeight(176)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(176)

    def set_state(self, cry_lo, cry_hi, completable):
        self._lo = int(cry_lo or 0)
        self._hi = None if cry_hi is None else int(cry_hi)
        self._comp = bool(completable)
        if self._comp and self.isVisible():
            self._timer.start()
        elif not self._comp:
            self._timer.stop()
        self.update()

    def showEvent(self, e):
        if self._comp:
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self):
        self._t += 0.05
        self.update()

    def _crying_shown(self):
        """(sure, maybe): the cells drawn crying, and those drawn « maybe crying »"""
        lo = min(self._lo, len(self.CRY_ORDER))
        hi = len(self.CRY_ORDER) if self._hi is None else min(self._hi, len(self.CRY_ORDER))
        sure = set(self.CRY_ORDER[:lo])
        maybe = set(self.CRY_ORDER[lo:hi]) if self._hi is not None else set()
        return sure, maybe

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        LEG = 178.0                                     # the legend's width
        H = self.height() - 8
        cell = max(14.0, min(H / 5.0, (self.width() - LEG - 24) / 4.0))
        fw = cell * 4
        ox = max(4.0, (self.width() - fw - LEG - 16) / 2)
        oy = 4.0 + (H - cell * 5) / 2

        def rect(col, row):
            return QRectF(ox + col * cell + 1, oy + (4 - row) * cell + 1, cell - 2, cell - 2)
        # the inside
        inner = QRectF(ox + cell, oy + cell, cell * 2, cell * 3)
        if self._comp:
            g = QLinearGradient(inner.topLeft(), inner.bottomRight())
            g.setColorAt(0, QColor("#5a1fb8")); g.setColorAt(1, QColor("#9d4dff"))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(g)
            p.drawRect(inner)
            p.save(); p.setClipRect(inner)
            for k in range(7):                         # the swirl
                ph = self._t * 1.6 + k * 0.9
                c = QColor("#e7c8ff"); c.setAlpha(int(40 + 35 * math.sin(ph * 1.3)))
                pen = QPen(c, 2.2); p.setPen(pen)
                path = QPainterPath()
                y0 = inner.top() + (k + 0.5) * inner.height() / 7
                path.moveTo(inner.left(), y0)
                for s in range(1, 13):
                    x = inner.left() + s * inner.width() / 12
                    path.lineTo(x, y0 + math.sin(ph + s * 0.7) * cell * 0.18)
                p.drawPath(path)
            glow = QRadialGradient(inner.center(), inner.width())
            gc = QColor("#c084fc"); gc.setAlpha(int(70 + 40 * math.sin(self._t * 2)))
            glow.setColorAt(0, gc); glow.setColorAt(1, QColor(0, 0, 0, 0))
            p.setBrush(glow); p.setPen(Qt.PenStyle.NoPen); p.drawRect(inner)
            p.restore()
        else:
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.BG4 or "#11141a"))
            p.drawRect(inner)
        # the corners (not needed)
        for col, row in ((0, 0), (3, 0), (0, 4), (3, 4)):
            r = rect(col, row)
            c = QColor(OBS); c.setAlpha(80)
            p.setPen(QPen(QColor(theme.LINE or "#3a404a"), 1, Qt.PenStyle.DotLine))
            p.setBrush(c); p.drawRoundedRect(r, 3, 3)
        sure, maybe = self._crying_shown()
        glow = 0.5 + 0.5 * math.sin(self._t * 3)
        for i, (col, row) in enumerate(self.CELLS):
            r = rect(col, row)
            if i in self.HOLES:
                if self._comp:                          # filled from the chest
                    paint_obsidian(p, r)
                    p.setPen(QPen(QColor("#ffd166"), 1.6)); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), 3, 3)
                else:
                    paint_missing(p, r)
                continue
            if i in sure:
                if self._comp:                          # replaced by the chest's obsidian
                    paint_obsidian(p, r)
                    p.setPen(QPen(QColor("#ffd166"), 1.6)); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), 3, 3)
                else:
                    paint_obsidian(p, r, crying=True, glow=glow)
            elif i in maybe:
                paint_obsidian(p, r, crying=True, glow=glow)
                veil = QColor(theme.BG1 or "#1b1f26"); veil.setAlpha(120)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(veil)
                p.drawRoundedRect(r, 3, 3)
            else:
                paint_obsidian(p, r)
        # the legend / what the chest brings
        lx = ox + fw + 16
        p.setFont(_font(8.2))
        rows = []
        if self._comp:
            n = len(self.HOLES) + len(sure)
            rows.append(("obs", f"{n}+ obsidian in the chest" if n else "obsidian in the chest"))
            rows.append(("fire", "flint and steel · fire charge\nor flint + 9 nuggets"))
        else:
            if sure or maybe:
                lo, hi = self._lo, self._hi
                txt = (f"{lo} crying" if hi == lo else f"≥ {lo} crying" if hi is None
                       else f"≤ {hi} crying" if not lo else f"{lo} – {hi} crying")
                rows.append(("cry", txt))
            rows.append(("hole", "missing"))
        y = oy + cell * 0.4
        for kind, txt in rows:
            ic = QRectF(lx, y, 18, 18)
            if kind == "obs":
                paint_obsidian(p, ic)
            elif kind == "cry":
                paint_obsidian(p, ic, crying=True, glow=glow)
            elif kind == "hole":
                paint_missing(p, ic)
            else:                                       # a flame
                fl = QPainterPath()
                c0 = ic.center()
                fl.moveTo(c0.x(), ic.top())
                fl.cubicTo(ic.right(), c0.y(), ic.right(), ic.bottom(), c0.x(), ic.bottom())
                fl.cubicTo(ic.left(), ic.bottom(), ic.left(), c0.y(), c0.x(), ic.top())
                gfl = QLinearGradient(ic.topLeft(), ic.bottomLeft())
                gfl.setColorAt(0, QColor("#ffe08a")); gfl.setColorAt(1, QColor("#ff6a2b"))
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(gfl); p.drawPath(fl)
            p.setPen(QColor(theme.TEXT2 or theme.TEXT))
            lines = txt.split("\n")
            p.drawText(QRectF(ic.right() + 8, ic.top() - 3, self.width() - ic.right() - 10,
                              18 * len(lines)), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, txt)
            y += 18 * len(lines) + 12
        p.end()
