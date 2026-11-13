"""
qtui/village_visuals.py — (05/11) the pictures of the Village condition.

  VillageTypePicker  the five village types as cards (a real village of that type
                     seen from above, drawn by SSA's plans): click to keep only
                     those types (none = any type).
  CountRange         a histogram of how many villages have N houses (or forges)
                     — measured on 100 000 villages, 26.2 — with two handles for
                     the minimum and maximum; the kept part is lit and its rarity
                     is written as « 1 in N » with the Stronghold page's tiers.

Data: loot_tables/village_stats.json (houses × forges per type), assets/village_types.
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme
from .stronghold_visuals import one_in, tier_of

TYPES = ("plains", "desert", "savanna", "snowy", "taiga")
NAMES = {"plains": "Plains", "desert": "Desert", "savanna": "Savanna", "snowy": "Snowy",
         "taiga": "Taiga"}
# the ground under each type's picture (its biome's colour)
GROUND = {"plains": "#6e9a44", "desert": "#d9cf9c", "savanna": "#9a9a46", "snowy": "#e6edf2",
          "taiga": "#4c6e3e"}
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATS = None


def stats():
    """{type: {(houses, forges): share}} of the villages of that type (not abandoned)."""
    global _STATS
    if _STATS is None:
        try:
            with open(os.path.join(_ROOT, "loot_tables", "village_stats.json")) as f:
                d = json.load(f)
            _STATS = {}
            for t, j in d["joint"].items():
                n = float(sum(j.values())) or 1.0
                _STATS[t] = {tuple(int(v) for v in k.split(",")): c / n for k, c in j.items()}
        except Exception:
            _STATS = {}
    return _STATS


def stats_info() -> str:
    """(05/11) where the histograms come from (the « Stats » bubble)."""
    try:
        with open(os.path.join(_ROOT, "loot_tables", "village_stats.json")) as f:
            d = json.load(f)
    except Exception:
        return "No statistics file."
    n = sum(int(v) for v in (d.get("n") or {}).values())
    nt = f"{n:,}".replace(",", " ")
    if d.get("real"):
        sh = d.get("share") or {}
        parts = "  ·  ".join(f"{NAMES[t]} {sh.get(t, 0) * 100:.0f} %" for t in TYPES if t in sh)
        return (f"Measured on {nt} real villages (random seeds, real biome and terrain)"
                f"{', ' + d['date'] if d.get('date') else ''}.\n"
                f"Abandoned villages apart.\nTypes: {parts}")
    return (f"Measured on {nt} villages generated on FLAT terrain (Java 26.2).\n"
            "In a real world the terrain changes the layout a little (slopes, room for the "
            "houses):\nabout one house fewer on average. Run tools/village_stats.py for real "
            "statistics.")


def share(types, hl=None, hh=None, fl=None, fh=None):
    """Share of the villages of these types (none = all, averaged) with houses in
    [hl, hh] and forges in [fl, fh] (None = no bound)."""
    st = stats()
    ts = [t for t in (types or TYPES) if t in st]
    if not ts:
        return None
    tot = 0.0
    for t in ts:
        s = 0.0
        for (h, f), p in st[t].items():
            if (hl is None or h >= hl) and (hh is None or h <= hh) and \
                    (fl is None or f >= fl) and (fh is None or f <= fh):
                s += p
        tot += s
    return tot / len(ts)


def histogram(types, which="houses", n=60):
    """[share of the villages with exactly k houses / forges] for k in 0..n-1."""
    st = stats()
    ts = [t for t in (types or TYPES) if t in st]
    out = [0.0] * n
    if not ts:
        return out
    for t in ts:
        for (h, f), p in st[t].items():
            k = min(h if which == "houses" else f, n - 1)     # the last bin: « n or more »
            out[k] += p / len(ts)
    return out


def _font(pt, bold=False):
    f = QFont(theme.ui_font(pt))
    f.setBold(bold)
    return f


# ═════════════════════════════════════════════════════════════════════════════
class VillageTypePicker(QWidget):
    """Five cards; value() = the chosen types (empty = any)."""
    changed = Signal(list)

    def __init__(self, chosen=(), parent=None):
        super().__init__(parent)
        self._sel = [t for t in TYPES if t in (chosen or ())]
        self._hover = -1
        self._pix = {}
        for t in TYPES:
            p = os.path.join(_ROOT, "assets", "village_types", t + ".png")
            pm = QPixmap(p)
            if not pm.isNull():
                self._pix[t] = pm
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("None ticked = any type.")

    def value(self):
        return list(self._sel)

    def _rects(self):
        n = len(TYPES)
        gap = 8.0
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
        t = TYPES[i]
        if t in self._sel:
            self._sel.remove(t)
        else:
            self._sel.append(t)
        self._sel = [x for x in TYPES if x in self._sel]
        self.update()
        self.changed.emit(self.value())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        acc = QColor(theme.ACCENT or "#7c9cff")
        anyc = not self._sel
        for i, (t, r) in enumerate(zip(TYPES, self._rects())):
            on = t in self._sel
            hov = i == self._hover
            card = QPainterPath(); card.addRoundedRect(r, 10, 10)
            bg = QColor(theme.BG2 or "#2a2f38")
            if hov:
                bg = bg.lighter(118)
            p.fillPath(card, bg)
            # the picture: a village of this type on its ground
            pic = QRectF(r.x() + 6, r.y() + 6, r.width() - 12, r.height() - 32)
            clip = QPainterPath(); clip.addRoundedRect(pic, 7, 7)
            p.save(); p.setClipPath(clip)
            p.fillRect(pic, QColor(GROUND[t]))
            pm = self._pix.get(t)
            if pm is not None:
                s = min(pic.width() / pm.width(), pic.height() / pm.height()) * 1.12
                dw, dh = pm.width() * s, pm.height() * s
                p.drawPixmap(QRectF(pic.center().x() - dw / 2, pic.center().y() - dh / 2, dw, dh),
                             pm, QRectF(pm.rect()))
            if not on and not anyc:                       # the others: veiled
                p.fillRect(pic, QColor(20, 22, 28, 150))
            p.restore()
            # the name
            p.setFont(_font(8.6, on))
            p.setPen(QColor(theme.TEXT if (on or anyc) else (theme.TEXT3 or theme.TEXT2)))
            p.drawText(QRectF(r.x(), r.bottom() - 24, r.width(), 20), Qt.AlignmentFlag.AlignCenter,
                       NAMES[t])
            # the border, the tick
            if on:
                p.setPen(QPen(acc, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 10, 10)
                c = QPointF(r.right() - 14, r.y() + 14)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(acc)
                p.drawEllipse(c, 8, 8)
                p.setPen(QPen(QColor("white"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(c.x() - 3.5, c.y()), QPointF(c.x() - 1, c.y() + 2.6))
                p.drawLine(QPointF(c.x() - 1, c.y() + 2.6), QPointF(c.x() + 3.8, c.y() - 2.8))
            else:
                p.setPen(QPen(QColor(theme.LINE or "#3a404a"), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)


# ═════════════════════════════════════════════════════════════════════════════
class CountRange(QWidget):
    """A histogram (villages per count) with a minimum and a maximum handle.
    values() = (lo, hi): lo 0 = no minimum, hi None = no maximum."""
    changed = Signal(object, object)
    PAD_L, PAD_R, PAD_B, PAD_T = 12.0, 12.0, 24.0, 18.0

    def __init__(self, n=50, lo=None, hi=None, unit="houses", parent=None):
        super().__init__(parent)
        self._n = int(n)                    # the axis: 0 … n (n = « n or more »)
        self._lo = int(lo or 0)
        self._hi = None if hi is None else int(hi)
        self._hist = [0.0] * (self._n + 1)
        self._unit = unit
        self._drag = None
        self._hover = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(124)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ── API ──
    def values(self):
        return self._lo, self._hi

    def set_values(self, lo, hi):
        lo = max(0, min(self._n, int(lo or 0)))
        hi = None if hi is None else max(lo, min(self._n, int(hi)))
        if (lo, hi) != (self._lo, self._hi):
            self._lo, self._hi = lo, hi
            self.update()

    def set_hist(self, h):
        h = list(h)[:self._n + 1]
        h += [0.0] * (self._n + 1 - len(h))
        self._hist = h
        self.update()

    # ── geometry ──
    def _plot(self):
        return QRectF(self.PAD_L, self.PAD_T, self.width() - self.PAD_L - self.PAD_R,
                      self.height() - self.PAD_T - self.PAD_B)

    def _x(self, k):
        r = self._plot()
        return r.x() + (k + 0.5) / (self._n + 1) * r.width()

    def _k(self, x):
        r = self._plot()
        return int(max(0, min(self._n, (x - r.x()) / r.width() * (self._n + 1))))

    def _hi_k(self):
        return self._n if self._hi is None else self._hi

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        k = self._k(e.position().x())
        dl, dh = abs(k - self._lo), abs(k - self._hi_k())
        self._drag = "lo" if dl < dh or (dl == dh and k < self._lo) else "hi"
        self._move(k)

    def mouseMoveEvent(self, e):
        k = self._k(e.position().x())
        if self._drag:
            self._move(k)
        elif k != self._hover:
            self._hover = k
            self.update()

    def leaveEvent(self, _e):
        self._hover = None
        self.update()

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def _move(self, k):
        lo, hi = self._lo, self._hi
        if self._drag == "lo":
            lo = min(k, self._hi_k())
        else:
            k = max(k, lo)
            hi = None if k >= self._n else k
        if (lo, hi) != (self._lo, self._hi):
            self._lo, self._hi = lo, hi
            self.update()
            self.changed.emit(lo, hi)

    # ── painting ──
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._plot()
        acc = QColor(theme.ACCENT or "#7c9cff")
        card = QRectF(0, 0, self.width(), self.height() - 16)
        bg = QPainterPath(); bg.addRoundedRect(card, 10, 10)
        g0 = QLinearGradient(0, 0, 0, card.height())
        b0 = QColor(theme.BG2 or "#2a2f38")
        g0.setColorAt(0, b0.lighter(108)); g0.setColorAt(1, b0)
        p.fillPath(bg, g0)
        # faint guide lines
        p.setPen(QPen(QColor(255, 255, 255, 14 if not theme.IS_LIGHT else 0), 1))
        if theme.IS_LIGHT:
            p.setPen(QPen(QColor(0, 0, 0, 16), 1))
        for f in (0.25, 0.5, 0.75):
            y = r.bottom() - f * (r.height() - 4)
            p.drawLine(QPointF(r.x(), y), QPointF(r.right(), y))
        h = list(self._hist)
        mx = max(h) or 1.0
        bw = r.width() / (self._n + 1)
        hk = self._hi_k()
        x0, x1 = r.x() + self._lo * bw, r.x() + (hk + 1) * bw
        # the kept band, under the bars
        band = QLinearGradient(0, r.y(), 0, r.bottom())
        c1 = QColor(acc); c1.setAlpha(10); c2 = QColor(acc); c2.setAlpha(46)
        band.setColorAt(0, c1); band.setColorAt(1, c2)
        bp = QPainterPath(); bp.addRoundedRect(QRectF(x0, r.y() - 2, x1 - x0, r.height() + 4), 6, 6)
        p.fillPath(bp, band)
        mute = QColor(theme.TEXT3 or "#6b7280")
        gap = max(0.8, min(2.0, bw * 0.18))
        for k, v in enumerate(h):
            if v <= 0:
                continue
            hh = max(2.0, (v / mx) ** 0.5 * (r.height() - 6))   # √ scale: the tail stays visible
            bar = QRectF(r.x() + k * bw + gap / 2, r.bottom() - hh, max(1.0, bw - gap), hh)
            inside = self._lo <= k <= hk
            rad = min(3.0, bar.width() / 2)
            path = QPainterPath(); path.addRoundedRect(bar, rad, rad)
            if inside:
                gb = QLinearGradient(0, bar.top(), 0, bar.bottom())
                top = QColor(acc).lighter(135 if self._hover == k else 118)
                gb.setColorAt(0, top); gb.setColorAt(1, QColor(acc).darker(115))
                p.fillPath(path, gb)
            else:
                c = QColor(mute); c.setAlpha(150 if self._hover == k else 85)
                p.fillPath(path, c)
        # the two handles: a line, a knob, the value in a pill
        p.setFont(_font(7.6, True))
        for x, lab, left in ((x0, str(self._lo), True),
                             (x1, "∞" if self._hi is None else str(self._hi), False)):
            p.setPen(QPen(QColor(acc).lighter(120), 1.6))
            p.drawLine(QPointF(x, r.y()), QPointF(x, r.bottom() + 3))
            p.setPen(QPen(QColor(theme.BG1 or "#1b1f26"), 2)); p.setBrush(acc)
            p.drawEllipse(QPointF(x, r.bottom() + 3), 5.5, 5.5)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(lab) + 12
            px = min(max(2.0, x - tw / 2), self.width() - tw - 2)
            pill = QRectF(px, r.y() - 8, tw, 15)
            pp = QPainterPath(); pp.addRoundedRect(pill, 7.5, 7.5)
            p.fillPath(pp, acc)
            p.setPen(QColor("white"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, lab)
        # the axis labels
        p.setFont(_font(7.4))
        p.setPen(QColor(theme.TEXT3 or theme.TEXT2))
        step = 10 if self._n > 20 else (2 if self._n > 6 else 1)
        for k in range(0, self._n + 1, step):
            lab = f"{k}+" if k == self._n else str(k)
            p.drawText(QRectF(self._x(k) - 14, self.height() - 15, 28, 14),
                       Qt.AlignmentFlag.AlignCenter, lab)
        # the hovered count
        if self._hover is not None and not self._drag and 0 <= self._hover < len(h):
            v = h[self._hover]
            k = self._hover
            txt = (f"{k}{'+' if k == self._n else ''} {self._unit} · "
                   + (f"{v * 100:.2f} %" if v >= 1e-4 else "< 0.01 %"))
            p.setFont(_font(8, True))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(txt) + 16
            hh = max(2.0, (v / mx) ** 0.5 * (r.height() - 6))
            ty = max(2.0, r.bottom() - hh - 26)
            tx = max(4.0, min(self.width() - tw - 4, self._x(k) - tw / 2))
            box = QRectF(tx, ty, tw, 20)
            pp = QPainterPath(); pp.addRoundedRect(box, 6, 6)
            p.fillPath(pp, QColor(theme.BG4 or "#11141a"))
            p.setPen(QPen(QColor(theme.LINE or "#3a404a"), 1)); p.drawPath(pp)
            p.setPen(QColor(theme.TEXT))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, txt)


def measured(types=None) -> int:
    """How many villages (of these types) the statistics measured."""
    try:
        with open(os.path.join(_ROOT, "loot_tables", "village_stats.json")) as f:
            n = json.load(f).get("n") or {}
        return sum(int(v) for t, v in n.items() if not types or t in types)
    except Exception:
        return 0


def rarity_text(prob, types=None):
    """(« ~1.4 % of the villages », « ~1 in 71 », colour) of a share of the villages
    (05/11: the tier's colour only, no tier name)."""
    if prob is None:
        return "—", "—", QColor(theme.TEXT3 or theme.TEXT2)
    if prob <= 0:
        n = measured(types)
        k = next((int(n // m) * m for m in (100000, 10000, 1000) if n >= m), n)
        nt = f"{k:,}".replace(",", " ")
        return f"Not found in {nt}+ villages", "—", QColor("#ff4d6d")
    _name, col = tier_of(prob)
    pct = f"~{prob * 100:.1f} %" if prob >= 0.001 else f"~{prob * 100:.3f} %"
    return f"{pct} of the villages", "~" + one_in(prob), QColor(col)
