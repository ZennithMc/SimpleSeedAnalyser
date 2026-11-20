"""
qtui/area_pad.py — (14/11) the Location block's picture: the area of a condition
drawn to scale around its reference, and shaped by hand.

    pad = AreaPad(color, icon)                  the type's colour and small icon
    pad.set_state("Radius", r=320, side=0, box=(0, 0, 0, 0), instances=2, ref="Village")
    pad.radiusChanged(int) / squareChanged(int) / boxChanged(x1, z1, x2, z2)

The pad owns no value: the form's fields are the truth. A drag emits the new
value, the form writes it in its field, the field's change comes back through
set_state() — so the pad, the fields and the map's overlay always agree, and a
value set here is exactly the one that would have been typed.

  Radius   drag anywhere: the radius is the distance to the reference
  Square   drag anywhere: half the side is the larger of the two distances
  Box      drag a corner, drag the inside to move it, or draw a new box outside
  near the border of the pad the view zooms out while the button is held
  16-block steps, Shift: to the block

The shape is always a rounded rectangle (a circle is one whose corners meet), so
a change of mode or of value glides from one shape to the other.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import motion, theme

# where the wanted instances are drawn inside the shape (fractions of its half size)
_SPOTS = [(-0.34, -0.36), (0.40, -0.30), (-0.44, 0.28), (0.30, 0.48),
          (0.00, -0.62), (-0.63, -0.04), (0.63, 0.10), (-0.08, 0.62)]      # none on the reference
_FLOOR = 48.0                                   # an empty area still shows this many blocks


def _fmt(v) -> str:
    return f"{int(v):,}".replace(",", " ")


class AreaPad(QWidget):
    radiusChanged = Signal(int)
    squareChanged = Signal(int)
    boxChanged = Signal(int, int, int, int)

    def __init__(self, color, icon=None, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.icon = icon                         # QPixmap (or None: a dot) for each wanted instance
        self.mode, self.r, self.side, self.box = "Radius", 0, 0, (0, 0, 0, 0)
        self.instances = None                    # None: this type has no instance count
        self.ref = "0, 0"
        self._d = None                           # drawn now: [x1, z1, x2, z2, roundness, pixels per block]
        self._an = None
        self._drag = None
        self._hot = None                         # what is under the mouse ("edge", corner index, "in")
        self._ang = -0.56                        # where the radius handle sits on the circle
        self._ping = -1.0
        self._pos = QPointF()
        self._zoom = QTimer(self)                # holds the zoom-out while dragging near the border
        self._zoom.setInterval(16)
        self._zoom.timeout.connect(self._zoom_tick)
        self.setMouseTracking(True)
        self.setFixedHeight(198)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("Drag to shape the area — hold near the border to zoom out.\n"
                        "16-block steps; Shift: to the block.")

    # ── state ────────────────────────────────────────────────────────────────
    def set_state(self, mode, r=0, side=0, box=(0, 0, 0, 0), instances=None, ref=None, animate=True):
        self.mode, self.r, self.side = mode, int(r), int(side)
        self.box = tuple(int(v) for v in box)
        self.instances = instances
        if ref is not None:
            self.ref = ref
        geo = self._geo()
        if self._drag is not None:               # the hand leads: no glide, the scale stays put
            self._d[:5] = geo
            self.update()
            return
        target = list(geo) + [self._fit(geo)]
        if self._d is None or not animate or not motion.enabled() or not self.isVisible():
            self._stop()
            self._d = target
            self.update()
            return
        if all(abs(a - b) < 1e-6 for a, b in zip(self._d, target)):
            self.update()
            return
        self._stop()
        d0 = list(self._d)

        def step(t):
            d = [a + (b - a) * t for a, b in zip(d0[:5], target[:5])]
            d.append(d0[5] * (target[5] / d0[5]) ** t)          # the zoom glides in ratio
            self._d = d
            self.update()
        self._an = motion.run(self, 260, step)

    def _stop(self):
        if self._an is not None:
            try:
                self._an.stop()
            except RuntimeError:
                pass
            self._an = None

    def _geo(self):
        if self.mode == "Radius":
            return [-self.r, -self.r, self.r, self.r, 1.0]
        if self.mode == "Square":
            h = self.side / 2.0
            return [-h, -h, h, h, 0.0]
        x1, z1, x2, z2 = self.box
        return [min(x1, x2), min(z1, z2), max(x1, x2), max(z1, z2), 0.0]

    def _room(self):
        return max(20.0, self.width() / 2 - 30), max(20.0, self.height() / 2 - 24)

    def _fit(self, geo):
        ex = max(abs(geo[0]), abs(geo[2]), _FLOOR)
        ez = max(abs(geo[1]), abs(geo[3]), _FLOOR)
        rw, rh = self._room()
        return min(rw / ex, rh / ez) * 0.84

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._d is not None and self._drag is None:
            self._stop()
            self._d[5] = self._fit(self._d[:5])

    def showEvent(self, e):
        super().showEvent(e)
        if self._d is not None:
            self._d[5] = self._fit(self._d[:5])
        if motion.enabled() and self._ping < 0:      # a ring leaves the reference: this is a radar

            def step(t):
                self._ping = t
                self.update()

            def done():
                self._ping = 2.0
                self.update()
            self._ping = 0.0
            motion.run(self, 760, step, done)

    # ── geometry ─────────────────────────────────────────────────────────────
    def _c(self):
        return self.width() / 2.0, self.height() / 2.0

    def _to_px(self, x, z):
        cx, cy = self._c()
        return cx + x * self._d[5], cy + z * self._d[5]

    def _to_world(self, p):
        cx, cy = self._c()
        return (p.x() - cx) / self._d[5], (p.y() - cy) / self._d[5]

    def _rect(self):
        x1, y1 = self._to_px(self._d[0], self._d[1])
        x2, y2 = self._to_px(self._d[2], self._d[3])
        return QRectF(x1, y1, x2 - x1, y2 - y1)

    def _corners(self):
        r = self._rect()
        return [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]

    def _hit(self, p):
        if self._d is None:
            return None
        if self.mode == "Box":
            for i, c in enumerate(self._corners()):
                if math.hypot(p.x() - c.x(), p.y() - c.y()) <= 11:
                    return i
            return "in" if self._rect().adjusted(-2, -2, 2, 2).contains(p) else None
        cx, cy = self._c()
        dx, dy = p.x() - cx, p.y() - cy
        if self.mode == "Radius":
            return "edge" if abs(math.hypot(dx, dy) - self.r * self._d[5]) <= 9 else None
        return "edge" if abs(max(abs(dx), abs(dy)) - self.side / 2.0 * self._d[5]) <= 9 else None

    # ── the hand ─────────────────────────────────────────────────────────────
    @staticmethod
    def _snap(v, step=16):
        if QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            step = 1
        return int(round(v / step) * step)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._d is None:
            return
        self._stop()
        p = e.position()
        self._pos = p
        if self.mode == "Box":
            h = self._hit(p)
            x1, z1, x2, z2 = self._geo()[:4]
            if isinstance(h, int):               # the opposite corner stays where it is
                fx, fz = [(x2, z2), (x1, z2), (x1, z1), (x2, z1)][h]
                self._drag = ("corner", fx, fz)
            elif h == "in":
                wx, wz = self._to_world(p)
                self._drag = ("move", wx - x1, wz - z1, x2 - x1, z2 - z1)
            else:                                # a new box starts here
                wx, wz = self._to_world(p)
                self._drag = ("corner", self._snap(wx), self._snap(wz))
        else:
            self._drag = ("size",)
        self._zoom.start()
        self._apply()

    def mouseMoveEvent(self, e):
        self._pos = e.position()
        if self._drag is not None:
            self._apply()
            return
        h = self._hit(self._pos)
        if h != self._hot:
            self._hot = h
            self.setCursor(Qt.CursorShape.SizeAllCursor if h == "in" else
                           Qt.CursorShape.PointingHandCursor if h is not None else
                           Qt.CursorShape.CrossCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        self._drag = None
        self._zoom.stop()
        self.set_state(self.mode, self.r, self.side, self.box, self.instances)   # glide back to fit

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self._hot is not None and self._drag is None:
            self._hot = None
            self.update()

    def _zoom_tick(self):
        if self._drag is None:
            self._zoom.stop()
            return
        cx, cy = self._c()
        rw, rh = self._room()
        over = max(abs(self._pos.x() - cx) / (rw + 22), abs(self._pos.y() - cy) / (rh + 16))
        if over > 0.90:                          # the further out, the faster
            self._d[5] *= 1.0 - min(0.06, 0.012 + (over - 0.90) * 0.22)
            self._apply()

    def _apply(self):
        wx, wz = self._to_world(self._pos)
        kind = self._drag[0]
        if kind == "size" and self.mode == "Radius":
            self._ang = math.atan2(wz, wx)
            v = max(0, min(30_000_000, self._snap(math.hypot(wx, wz))))
            if v != self.r:
                self.radiusChanged.emit(v)
        elif kind == "size":
            v = max(0, min(60_000_000, 2 * self._snap(max(abs(wx), abs(wz)))))
            if v != self.side:
                self.squareChanged.emit(v)
        elif kind == "corner":
            box = (self._drag[1], self._drag[2], self._snap(wx), self._snap(wz))
            box = (min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3]))
            if box != tuple(self._geo()[:4]):
                self.boxChanged.emit(*box)
        elif kind == "move":
            _, ox, oz, bw, bh = self._drag
            x1, z1 = self._snap(wx - ox), self._snap(wz - oz)
            box = (x1, z1, int(x1 + bw), int(z1 + bh))
            if box != tuple(self._geo()[:4]):
                self.boxChanged.emit(*box)
        self.update()

    # ── the picture ──────────────────────────────────────────────────────────
    def paintEvent(self, e):
        if self._d is None:
            return
        w, h = self.width(), self.height()
        cx, cy = self._c()
        sc = self._d[5]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QPainterPath()
        frame.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 12, 12)
        p.fillPath(frame, QColor(theme.field_bg()))
        p.setClipPath(frame)
        excl = self.instances == 0               # exclusion: nothing may be inside
        col = QColor(theme.DANGER) if excl else QColor(self.color)
        text, text3 = QColor(theme.TEXT), QColor(theme.TEXT3)

        glow = QRadialGradient(QPointF(cx, cy), h * 0.9)
        g0 = QColor(col)
        g0.setAlphaF(0.13)
        g1 = QColor(col)
        g1.setAlphaF(0.0)
        glow.setColorAt(0, g0)
        glow.setColorAt(1, g1)
        p.fillRect(self.rect(), glow)

        # rings every « step » blocks around the reference
        step = 1.0
        while step * sc < 30:
            step *= 2
        ring = QColor(text)
        ring.setAlphaF(0.075)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(ring, 1))
        reach = math.hypot(w, h) / 2
        k = 1
        while k * step * sc < reach and k < 40:
            rr = k * step * sc
            p.drawEllipse(QPointF(cx, cy), rr, rr)
            k += 1
        axis = QColor(text)
        axis.setAlphaF(0.05)
        p.setPen(QPen(axis, 1))
        p.drawLine(QPointF(0, cy), QPointF(w, cy))
        p.drawLine(QPointF(cx, 0), QPointF(cx, h))

        if 0.0 <= self._ping <= 1.0:
            pc = QColor(col)
            pc.setAlphaF(0.42 * (1.0 - self._ping))
            p.setPen(QPen(pc, 1.6))
            rr = 6 + self._ping * max(w, h) * 0.55
            p.drawEllipse(QPointF(cx, cy), rr, rr)

        # the area
        r = self._rect()
        active = self._drag is not None
        rad = self._d[4] * min(r.width(), r.height()) / 2.0
        shape = QPainterPath()
        if r.width() < 3 and r.height() < 3:
            shape.addEllipse(r.center(), 2.5, 2.5)
        else:
            shape.addRoundedRect(r, rad, rad)
        halo = QColor(col)
        halo.setAlphaF(0.16 if active else 0.10)
        p.setPen(QPen(halo, 7))
        p.drawPath(shape)
        fill = QColor(col)
        fill.setAlphaF(0.26 if active else 0.21 if self._hot is not None else 0.16)
        p.fillPath(shape, fill)
        if excl:                                 # hatched: keep out
            p.save()
            p.setClipPath(shape, Qt.ClipOperation.IntersectClip)
            hc = QColor(col)
            hc.setAlphaF(0.30)
            p.setPen(QPen(hc, 1))
            x = r.left() - r.height()
            while x < r.right():
                p.drawLine(QPointF(x, r.bottom()), QPointF(x + r.height(), r.top()))
                x += 9
            p.restore()
        p.setPen(QPen(col, 1.8))
        p.drawPath(shape)

        # the wanted instances, inside
        n = self.instances or 0
        half = min(r.width(), r.height()) / 2.0
        if n > 0 and half > 26:
            hw, hh = (half, half) if self._d[4] > 0.5 else (r.width() / 2 * 1.12, r.height() / 2 * 1.12)
            for nx, ny in _SPOTS[:min(n, len(_SPOTS))]:
                q = QPointF(r.center().x() + nx * hw, r.center().y() + ny * hh)
                if self.icon is not None:
                    s = 15
                    p.drawPixmap(QRectF(q.x() - s / 2, q.y() - s / 2, s, s), self.icon, QRectF(self.icon.rect()))
                else:
                    dot = QColor(col)
                    dot.setAlphaF(0.95)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(dot)
                    p.drawEllipse(q, 3.2, 3.2)

        # the reference
        p.setPen(QPen(QColor(theme.TEXT2), 1.4))
        p.setBrush(QColor(theme.field_bg()))
        p.drawEllipse(QPointF(cx, cy), 3.6, 3.6)
        f = theme.num_font(7.5)
        p.setFont(f)
        fm = p.fontMetrics()
        p.setPen(text3)
        ref = fm.elidedText(self.ref, Qt.TextElideMode.ElideRight, 120)
        p.drawText(QPointF(cx + 8, cy + fm.ascent() + 5), ref)

        # handles
        p.setPen(QPen(col, 2))
        p.setBrush(QColor("#ffffff"))
        if self.mode == "Radius":
            hp = QPointF(cx + math.cos(self._ang) * self.r * sc, cy + math.sin(self._ang) * self.r * sc)
            if active:
                guide = QColor(col)
                guide.setAlphaF(0.8)
                p.setPen(QPen(guide, 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(cx, cy), hp)
                p.setPen(QPen(col, 2))
            big = active or self._hot == "edge"
            p.drawEllipse(hp, 5.6 if big else 4.4, 5.6 if big else 4.4)
        else:
            for i, c in enumerate(self._corners()):
                big = active or self._hot == i or (self.mode == "Square" and self._hot == "edge")
                p.drawEllipse(c, 5.2 if big else 4.0, 5.2 if big else 4.0)

        # read-out (top left) and scale (bottom right)
        if self.mode == "Radius":
            val = f"r {_fmt(self.r)}"
        elif self.mode == "Square":
            val = f"{_fmt(self.side)} × {_fmt(self.side)}"
        else:
            g = self._geo()
            val = f"{_fmt(g[2] - g[0])} × {_fmt(g[3] - g[1])}"
        f = theme.num_font(9.5, QFont.Weight.DemiBold)
        p.setFont(f)
        fm = p.fontMetrics()
        x = 12.0
        p.setPen(text)
        p.drawText(QPointF(x, 10 + fm.ascent()), val)
        x += fm.horizontalAdvance(val) + 8
        if self.instances is not None:
            tag = "none inside" if excl else f"≥ {self.instances}"
            tw = fm.horizontalAdvance(tag) + 14
            chip = QRectF(x, 8, tw, fm.height() + 4)
            cb = QColor(col)
            cb.setAlphaF(0.20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(cb)
            p.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
            p.setPen(QColor(theme._mix(theme.TEXT, col.name(), 0.55)))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, tag)

        bar = 1.0
        while bar * sc < 44:
            bar *= 2
        bw = bar * sc
        p.setFont(theme.num_font(7.5))
        fm = p.fontMetrics()
        lab = f"{_fmt(bar)} blocks"
        bx, by = w - 12 - bw, h - 12
        p.setPen(QPen(text3, 1.2))
        p.drawLine(QPointF(bx, by), QPointF(bx + bw, by))
        p.drawLine(QPointF(bx, by - 3), QPointF(bx, by))
        p.drawLine(QPointF(bx + bw, by - 3), QPointF(bx + bw, by))
        p.drawText(QPointF(w - 12 - fm.horizontalAdvance(lab), by - 6), lab)

        p.setClipping(False)
        edge = QColor(theme.LINE2)
        if active:
            edge = QColor(col)
            edge.setAlphaF(0.75)
        p.setPen(QPen(edge, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(frame)
