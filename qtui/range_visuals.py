"""
qtui/range_visuals.py — (14/11) two ranges of the condition editor set by hand
on a picture. Both only write in the form's own fields, which stay the truth.

    YColumn(lo, hi, vlo, vhi)      a height range on a slice of the world: sky,
                                   clouds, sea level, stone, deepslate, bedrock.
                                   Same interface as widgets.RangeSlider
                                   (rangeChanged(int, int), set_values, set_inverted).
    ClimateBand(param)             a range of a climate noise on the scale of that
                                   parameter, cut in the bands the game uses
                                   (Frozen … Hot, Deep ocean … Far inland, erosion
                                   0 … 6, …). Values × 10000 as in Cubiomes Viewer;
                                   an end pushed to the border = no bound.
                                   rangeChanged(lo or None, hi or None).

  YColumn       drag a line, or the band between the two lines
  ClimateBand   drag an end (it catches the limits of the bands); click a band:
                exactly that band; Shift + click: up to that band
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from . import theme

# the ground of the slice: (fraction of the width, Y)
_GROUND = [(0.00, 70), (0.07, 73), (0.13, 98), (0.19, 152), (0.24, 206), (0.28, 178), (0.33, 118),
           (0.38, 84), (0.44, 70), (0.50, 65), (0.55, 55), (0.60, 40), (0.66, 31), (0.72, 37),
           (0.77, 53), (0.82, 65), (0.88, 69), (0.94, 80), (1.00, 75)]
_CAVES = [(0.18, 34, 0.11, 16), (0.47, -22, 0.16, 18), (0.83, 12, 0.10, 14), (0.66, -44, 0.09, 10)]
SEA = 63


class YColumn(QWidget):
    rangeChanged = Signal(int, int)

    def __init__(self, lo=-64, hi=320, vlo=None, vhi=None, parent=None):
        super().__init__(parent)
        self.lo, self.hi = int(lo), int(hi)
        self.a = self.lo if vlo is None else int(vlo)
        self.b = self.hi if vhi is None else int(vhi)
        self.inverted = False
        self._drag = None
        self._hot = None
        self.setMouseTracking(True)
        self.setFixedHeight(176)
        self.setMinimumWidth(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ── RangeSlider's interface ──────────────────────────────────────────────
    def values(self):
        return self.a, self.b

    def set_values(self, lo, hi, emit=False):
        lo = max(self.lo, min(self.hi, int(lo)))
        hi = max(self.lo, min(self.hi, int(hi)))
        if (lo, hi) != (self.a, self.b):
            self.a, self.b = lo, hi
            self.update()
            if emit:
                self.rangeChanged.emit(self.a, self.b)

    def set_inverted(self, on):
        self.inverted = bool(on)
        self.update()

    # ── geometry ─────────────────────────────────────────────────────────────
    def _plot(self):
        return QRectF(32, 7, self.width() - 40, self.height() - 14)

    def _y(self, v):
        r = self._plot()
        return r.top() + (self.hi - v) / float(self.hi - self.lo) * r.height()

    def _v(self, y):
        r = self._plot()
        v = self.hi - (y - r.top()) / r.height() * (self.hi - self.lo)
        return int(round(max(self.lo, min(self.hi, v))))

    def _near(self, y):
        da, db = abs(y - self._y(self.a)), abs(y - self._y(self.b))
        if min(da, db) <= 9:
            return "a" if da < db or (da == db and y > self._y(self.a)) else "b"
        if self._y(max(self.a, self.b)) < y < self._y(min(self.a, self.b)):
            return "band"
        return None

    # ── the hand ─────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        y = e.position().y()
        h = self._near(y)
        if h is None:                            # outside: the nearer line comes here
            h = "a" if abs(y - self._y(self.a)) < abs(y - self._y(self.b)) else "b"
        self._drag = (h, self._v(y), self.a, self.b)
        self._move(y)

    def mouseMoveEvent(self, e):
        y = e.position().y()
        if self._drag is not None:
            self._move(y)
            return
        h = self._near(y)
        if h != self._hot:
            self._hot = h
            self.setCursor(Qt.CursorShape.SizeVerCursor if h in ("a", "b") else
                           Qt.CursorShape.OpenHandCursor if h == "band" else Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self._hot is not None and self._drag is None:
            self._hot = None
            self.update()

    def _move(self, y):
        h, v0, a0, b0 = self._drag
        v = self._v(y)
        a, b = self.a, self.b
        if h == "a":
            a = min(v, b)
        elif h == "b":
            b = max(v, a)
        else:
            d = v - v0
            d = max(self.lo - min(a0, b0), min(self.hi - max(a0, b0), d))
            a, b = a0 + d, b0 + d
        if (a, b) != (self.a, self.b):
            self.a, self.b = a, b
            self.rangeChanged.emit(a, b)
        self.update()

    # ── the picture ──────────────────────────────────────────────────────────
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._plot()
        bg = theme.field_bg()
        mix = theme._mix
        frame = QPainterPath()
        frame.addRoundedRect(r, 10, 10)
        p.save()
        p.setClipPath(frame)
        sky = QLinearGradient(0, r.top(), 0, self._y(SEA))
        sky.setColorAt(0, QColor(mix(bg, "#3f74c8", 0.62)))
        sky.setColorAt(1, QColor(mix(bg, "#9ad0f7", 0.80)))
        p.fillRect(r, sky)
        # the ground
        pts = [QPointF(r.left() + fx * r.width(), self._y(gy)) for fx, gy in _GROUND]
        top = QPainterPath(pts[0])
        for i in range(1, len(pts)):
            mid = QPointF((pts[i - 1].x() + pts[i].x()) / 2, (pts[i - 1].y() + pts[i].y()) / 2)
            top.quadTo(pts[i - 1], mid)
        top.lineTo(pts[-1])
        ground = QPainterPath(top)
        ground.lineTo(r.right(), r.bottom() + 2)
        ground.lineTo(r.left(), r.bottom() + 2)
        ground.closeSubpath()
        water = QPainterPath()
        water.addRect(QRectF(r.left(), self._y(SEA), r.width(), r.bottom() - self._y(SEA)))
        p.fillPath(water.subtracted(ground), QColor(mix(bg, "#2f6fd0", 0.85)))
        p.fillPath(ground, QColor(mix(bg, "#8f949d", 0.74)))
        if self.lo < 0:                          # deepslate under Y 0
            deep = QPainterPath()
            deep.addRect(QRectF(r.left(), self._y(0), r.width(), r.bottom() - self._y(0) + 2))
            p.fillPath(deep.intersected(ground), QColor(mix(bg, "#4a4d57", 0.80)))
        p.setPen(Qt.PenStyle.NoPen)
        cave = QColor("#000000")
        cave.setAlphaF(0.34)
        p.setBrush(cave)
        for fx, gy, fw, gh in _CAVES:
            if self.lo <= gy <= self.hi:
                hh = gh / float(self.hi - self.lo) * r.height()
                p.drawEllipse(QPointF(r.left() + fx * r.width(), self._y(gy)), fw * r.width() / 2, hh / 2)
        p.fillRect(QRectF(r.left(), self._y(self.lo + 4), r.width(), r.bottom() - self._y(self.lo + 4) + 2),
                   QColor(mix(bg, "#16171a", 0.9)))                      # bedrock
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(mix(bg, "#6fcf4f", 0.95)), 2.4))
        p.drawPath(top)
        if self.hi >= 200:                       # clouds, at Y 192
            p.setPen(Qt.PenStyle.NoPen)
            cl = QColor("#ffffff")
            cl.setAlphaF(0.55)
            p.setBrush(cl)
            for fx, fw in ((0.52, 0.17), (0.80, 0.11), (0.05, 0.08)):
                p.drawRoundedRect(QRectF(r.left() + fx * r.width(), self._y(192) - 3, fw * r.width(), 6), 3, 3)
        # every 64 blocks
        grid = QColor("#ffffff")
        grid.setAlphaF(0.10)
        p.setPen(QPen(grid, 1, Qt.PenStyle.DotLine))
        ticks = [v for v in range(-64, 321, 64) if self.lo < v < self.hi]
        for v in ticks:
            p.drawLine(QPointF(r.left(), self._y(v)), QPointF(r.right(), self._y(v)))
        # what is not kept goes dark
        lo, hi = min(self.a, self.b), max(self.a, self.b)
        veil = QColor(bg)
        veil.setAlphaF(0.66)
        ya, yb = self._y(hi), self._y(lo)
        if self.inverted:
            p.fillRect(QRectF(r.left(), ya, r.width(), yb - ya), veil)
        else:
            p.fillRect(QRectF(r.left(), r.top(), r.width(), ya - r.top()), veil)
            p.fillRect(QRectF(r.left(), yb, r.width(), r.bottom() - yb), veil)
        p.restore()
        p.setPen(QPen(QColor(theme.LINE2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(frame)
        # the scale
        p.setFont(theme.num_font(7.2))
        fm = p.fontMetrics()
        p.setPen(QColor(theme.TEXT3))
        for v in [self.lo] + ticks + [self.hi]:
            y = max(r.top() + fm.ascent() - 1, min(r.bottom(), self._y(v) + fm.ascent() / 2 - 1))
            p.drawText(QRectF(0, y - fm.ascent(), 27, fm.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(v))
        # the two lines
        acc = QColor(theme.ACCENT)
        for which, v in (("b", self.b), ("a", self.a)):
            y = self._y(v)
            big = self._hot == which or (self._drag is not None and self._drag[0] in (which, "band"))
            p.setPen(QPen(acc, 2.2 if big else 1.6))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            tag = f"Y {v}"
            p.setFont(theme.num_font(7.8, QFont.Weight.DemiBold))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(tag) + 12
            up = (which == "b") != (self.a > self.b)
            ty = y - fm.height() - 5 if up else y + 3
            if ty < r.top() + 2:
                ty = y + 3
            if ty + fm.height() + 2 > r.bottom() - 1:
                ty = y - fm.height() - 5
            pill = QRectF(r.right() - tw - 6, ty, tw, fm.height() + 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(acc)
            p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
            p.setPen(QColor(theme.on_accent(acc.name())))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, tag)
            p.setPen(QPen(acc, 2))
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(r.left() + 9, y), 5.2 if big else 4.2, 5.2 if big else 4.2)


# ── climate noises ───────────────────────────────────────────────────────────
# (upper limit × 10000 or None for the last one, name, colour): the limits Minecraft's
# biome builder uses for each parameter
BANDS = {
    "Temperature": [(-4500, "Frozen", "#8fd0ff"), (-1500, "Cold", "#7fb8a8"), (2000, "Temperate", "#7cc46a"),
                    (5500, "Warm", "#e6b450"), (None, "Hot", "#ea6a4b")],
    "Humidity": [(-3500, "Arid", "#d8b46a"), (-1000, "Dry", "#b9c46a"), (1000, "Neutral", "#79c276"),
                 (3000, "Wet", "#4fb3a0"), (None, "Humid", "#3f8fd6")],
    "Continentalness": [(-10500, "Mushroom", "#c06ad8"), (-4550, "Deep ocean", "#2e4fb0"),
                        (-1900, "Ocean", "#3f7fe0"), (-1100, "Coast", "#e3d28a"), (300, "Near inland", "#9bcf6a"),
                        (3000, "Mid inland", "#5fae55"), (None, "Far inland", "#8a9099")],
    "Erosion": [(-7800, "0", "#f0f2f5"), (-3750, "1", "#c9ccd4"), (-2225, "2", "#a8b89a"), (500, "3", "#8cc276"),
                (4500, "4", "#b9c46a"), (5500, "5", "#d8b46a"), (None, "6", "#b08a5a")],
    "Weirdness": [(-9333, "Mid", "#8fb878"), (-7666, "High", "#b9bfc9"), (-5666, "Peaks", "#f0f2f5"),
                  (-4000, "High", "#b9bfc9"), (-2666, "Mid", "#8fb878"), (-500, "Low", "#6fa860"),
                  (500, "Valleys", "#4f8fe0"), (2666, "Low", "#6fa860"), (4000, "Mid", "#8fb878"),
                  (5666, "High", "#b9bfc9"), (7666, "Peaks", "#f0f2f5"), (9333, "High", "#b9bfc9"),
                  (None, "Mid", "#8fb878")],
}
DOMAIN = {"Continentalness": (-12000, 10000)}


class ClimateBand(QWidget):
    rangeChanged = Signal(object, object)

    def __init__(self, param="Temperature", parent=None):
        super().__init__(parent)
        self.param = param if param in BANDS else "Temperature"
        self.a = self.b = None                   # None = no bound
        self.inverted = False
        self._drag = None
        self._hot = None
        self._press = None
        self.setMouseTracking(True)
        self.setFixedHeight(58)
        self.setMinimumWidth(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ── values ───────────────────────────────────────────────────────────────
    def set_param(self, name):
        if name in BANDS and name != self.param:
            self.param = name
            self.update()

    def set_values(self, lo, hi):
        if (lo, hi) != (self.a, self.b):
            self.a, self.b = lo, hi
            self.update()

    def set_inverted(self, on):
        self.inverted = bool(on)
        self.update()

    # ── geometry ─────────────────────────────────────────────────────────────
    def _dom(self):
        return DOMAIN.get(self.param, (-10000, 10000))

    def _strip(self):
        return QRectF(7, 6, self.width() - 14, 26)

    def _x(self, v, end=0):
        """Pixel of a value; None = the left (end 0) or right (end 1) border"""
        s, (d0, d1) = self._strip(), self._dom()
        if v is None:
            return s.left() if end == 0 else s.right()
        return s.left() + (max(d0, min(d1, v)) - d0) / float(d1 - d0) * s.width()

    def _segments(self):
        """[(x0, x1, lower or None, upper or None, name, colour)]"""
        out, prev = [], None
        for up, name, col in BANDS[self.param]:
            out.append((self._x(prev, 0), self._x(up, 1), prev, up, name, col))
            prev = up
        return out

    def _value_at(self, x):
        s, (d0, d1) = self._strip(), self._dom()
        if x <= s.left() + 4:
            return None, 0
        if x >= s.right() - 4:
            return None, 1
        for _x0, x1, _lo, up, _n, _c in self._segments():     # the limits of the bands catch the hand
            if up is not None and abs(x - x1) <= 6:
                return up, None
        v = d0 + (x - s.left()) / s.width() * (d1 - d0)
        step = 1 if QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier else 50
        return int(round(v / step) * step), None

    def _handle_at(self, pos):
        if not (0 <= pos.y() <= 40):
            return None
        xa, xb = self._x(self.a, 0), self._x(self.b, 1)
        da, db = abs(pos.x() - xa), abs(pos.x() - xb)
        if min(da, db) > 8:
            return None
        return "a" if da < db or (da == db and pos.x() < xa) else "b"

    def _seg_at(self, pos):
        if not self._strip().adjusted(0, -2, 0, 2).contains(pos):
            return None
        for seg in self._segments():
            if seg[0] <= pos.x() <= seg[1]:
                return seg
        return None

    # ── the hand ─────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._press = e.position()
        self._drag = self._handle_at(e.position())
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is None and self._press is not None and abs(pos.x() - self._press.x()) > 4:
            # a drag that starts on the strip: the nearer end follows
            xa, xb = self._x(self.a, 0), self._x(self.b, 1)
            self._drag = "a" if abs(self._press.x() - xa) <= abs(self._press.x() - xb) else "b"
        if self._drag is not None:
            v, end = self._value_at(pos.x())
            a, b = self.a, self.b
            if self._drag == "a":
                a = None if end == 0 else (v if end is None else a)
                if a is not None and b is not None and a > b:
                    a = b
            else:
                b = None if end == 1 else (v if end is None else b)
                if a is not None and b is not None and b < a:
                    b = a
            self._set(a, b)
            return
        h = self._handle_at(pos)
        if h != self._hot:
            self._hot = h
            self.setCursor(Qt.CursorShape.SizeHorCursor if h else Qt.CursorShape.PointingHandCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is None and self._press is not None:
            seg = self._seg_at(e.position())
            if seg is not None:                  # a click on a band: that band
                lo, up = seg[2], seg[3]
                if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:   # … or up to it
                    lows = [v for v in (self.a, lo)]
                    ups = [v for v in (self.b, up)]
                    lo = None if None in lows else min(lows)
                    up = None if None in ups else max(ups)
                self._set(lo, up)
        self._drag = self._press = None
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self._hot is not None:
            self._hot = None
            self.update()

    def _set(self, a, b):
        if (a, b) != (self.a, self.b):
            self.a, self.b = a, b
            self.rangeChanged.emit(a, b)
        self.update()

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            seg = self._seg_at(QPointF(e.pos()))
            if seg is not None:
                lo = "−∞" if seg[2] is None else str(seg[2])
                up = "+∞" if seg[3] is None else str(seg[3])
                name = seg[4] if self.param != "Erosion" else f"Erosion {seg[4]}"
                QToolTip.showText(e.globalPos(), f"{name}   {lo} … {up}\nClick: this band · Shift + click: up to it",
                                  self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    # ── the picture ──────────────────────────────────────────────────────────
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._strip()
        bg = theme.field_bg()
        clip = QPainterPath()
        clip.addRoundedRect(s, 7, 7)
        p.save()
        p.setClipPath(clip)
        f = theme.ui_font(7.0)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        for x0, x1, _lo, _up, name, col in self._segments():
            c = QColor(theme._mix(bg, col, 0.86))
            p.fillRect(QRectF(x0, s.top(), x1 - x0 - 1, s.height()), c)
            if fm.horizontalAdvance(name) + 6 <= x1 - x0:
                lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
                ink = QColor("#0b0c0e" if lum > 140 else "#ffffff")
                ink.setAlphaF(0.86)
                p.setPen(ink)
                p.drawText(QRectF(x0, s.top(), x1 - x0 - 1, s.height()), Qt.AlignmentFlag.AlignCenter, name)
        xa, xb = self._x(self.a, 0), self._x(self.b, 1)
        veil = QColor(bg)
        veil.setAlphaF(0.74)
        if self.inverted:
            p.fillRect(QRectF(xa, s.top(), xb - xa, s.height()), veil)
        else:
            p.fillRect(QRectF(s.left(), s.top(), xa - s.left(), s.height()), veil)
            p.fillRect(QRectF(xb, s.top(), s.right() - xb, s.height()), veil)
        p.restore()
        acc = QColor(theme.ACCENT)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(acc, 1.8))
        sel = QRectF(xa, s.top(), max(1.0, xb - xa), s.height())
        p.drawRoundedRect(sel.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.setFont(theme.num_font(7.8, QFont.Weight.DemiBold))
        fm = p.fontMetrics()
        tags = []
        for which, x, v in (("a", xa, self.a), ("b", xb, self.b)):
            if v is not None:                    # an open end has no grip: it runs to the border
                big = self._hot == which or self._drag == which
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#ffffff"))
                wdt = 5.0 if big else 4.0
                p.drawRoundedRect(QRectF(x - wdt / 2, s.top() - 3, wdt, s.height() + 6), 2, 2)
                p.setPen(QPen(acc, 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(x - wdt / 2, s.top() - 3, wdt, s.height() + 6), 2, 2)
            txt = ("−∞" if which == "a" else "+∞") if v is None else str(v)
            tw = fm.horizontalAdvance(txt)
            tx = max(s.left(), min(s.right() - tw, x - tw / 2))
            tags.append([tx, tw, txt, v is None])
        if tags[0][0] + tags[0][1] + 6 > tags[1][0]:         # the two values never overlap
            mid = (tags[0][0] + tags[0][1] + tags[1][0]) / 2
            tags[0][0] = max(s.left(), mid - 3 - tags[0][1])
            tags[1][0] = min(s.right() - tags[1][1], tags[0][0] + tags[0][1] + 6)
        for tx, tw, txt, open_ in tags:
            p.setPen(QColor(theme.TEXT3 if open_ else theme.TEXT))
            p.drawText(QPointF(tx, s.bottom() + 6 + fm.ascent()), txt)
