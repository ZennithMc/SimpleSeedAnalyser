"""
qtui/home_lights.py — (20/11) Home answers the mouse with light.

    under the blocks : a soft light follows the cursor (it shows in the gaps between the
                       blocks and through their glass), in the colour of what is under it
                       (a tile's workspace, the Seed Library's gold, an open news row…);
                       a few sparks are left behind when the mouse moves, and drift away
    over the blocks  : the rims of the tiles and cards light up where the cursor is near

One timer at the screen's rate, only while something moves; only the region around the
light is repainted. Nothing when the animations are off or the window is inactive.
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from . import motion, theme

LIGHT_R = 300.0          # the light under the blocks
RIM_R = 230.0            # the reach of the lit rims
MAX_SPARKS = 20


def _alpha(c, a):
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


class _Layer(QWidget):
    def __init__(self, host, ctl, over):
        super().__init__(host)
        self.ctl, self.over = ctl, over
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, e):
        if self.over:
            self.ctl.paint_over(self, e)
        else:
            self.ctl.paint_under(self, e)


class HomeLights(QObject):
    def __init__(self, host, blocks):
        """host: the Home page; blocks(): [(widget, colour, corner radius)] of the page."""
        super().__init__(host)
        self.host, self.blocks = host, blocks
        self.under = _Layer(host, self, False)
        self.over = _Layer(host, self, True)
        self.under.lower()
        self.over.raise_()
        self.pos = None                 # the light (smoothed), page coordinates
        self.col = QColor(theme.ws_color("home"))
        self.a = 0.0                    # its strength 0 → 1
        self.sparks = []                # dict(x, y, vx, vy, t, life, s, c)
        self.rings = []                 # (x, y, t0, colour)
        self._last = None
        self._acc = 0.0
        self._clock = time.monotonic()
        self._dirty = QRect()
        self._sprites = {}
        self._timer = QTimer(host)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        host.installEventFilter(self)
        self._fit()

    # ── events of the page ───────────────────────────────────────────────────
    def eventFilter(self, o, e):
        t = e.type()
        if t == QEvent.Type.Resize:
            self._fit()
        elif t in (QEvent.Type.Enter, QEvent.Type.Show):
            self._wake()
        return False

    def _fit(self):
        r = self.host.rect()
        self.under.setGeometry(r)
        self.over.setGeometry(r)
        self.under.lower()
        self.over.raise_()

    def _wake(self):
        if not motion.enabled():
            return
        if not self._timer.isActive():
            self._clock = time.monotonic()
            try:
                hz = float(self.host.screen().refreshRate())
            except Exception:                              # noqa: BLE001
                hz = 60.0
            self._timer.start(int(max(7, min(16, 1000.0 / max(30.0, hz)))))
        self.over.raise_()

    # ── what is under the cursor ─────────────────────────────────────────────
    def _target_colour(self, p):
        for w, col, _rad in self.blocks():
            try:
                if w.isVisible() and w.geometry().contains(p.toPoint()):
                    return col, w
            except RuntimeError:
                continue
        return theme.ws_pair("home")[1], None

    # ── time ─────────────────────────────────────────────────────────────────
    def _tick(self):
        now = time.monotonic()
        dt, self._clock = min(0.05, now - self._clock), now
        host = self.host
        win = host.window()
        active = (host.isVisible() and win is not None and win.isActiveWindow() and motion.enabled())
        cur = QPointF(host.mapFromGlobal(QCursor.pos()))
        inside = active and QRectF(host.rect()).contains(cur)
        old = self._bounds()
        if inside:
            if self.pos is None:
                self.pos = QPointF(cur)
            k = 1.0 - math.exp(-dt * 16.0)
            self.pos = QPointF(self.pos.x() + (cur.x() - self.pos.x()) * k,
                               self.pos.y() + (cur.y() - self.pos.y()) * k)
            tc = QColor(self._target_colour(cur)[0])
            kc = 1.0 - math.exp(-dt * 7.0)
            self.col = motion.mix(self.col, tc, kc)
            self.a += (1.0 - self.a) * (1.0 - math.exp(-dt * 6.0))
            if self._last is not None:                       # sparks left behind by the movement
                d = math.hypot(cur.x() - self._last.x(), cur.y() - self._last.y())
                self._acc += d
                while self._acc > 40.0 and len(self.sparks) < MAX_SPARKS:
                    self._acc -= 40.0
                    f = random.random()
                    x = self._last.x() + (cur.x() - self._last.x()) * f
                    y = self._last.y() + (cur.y() - self._last.y()) * f
                    ang = random.random() * 6.283
                    sp = 10 + random.random() * 26
                    self.sparks.append(dict(x=x, y=y, vx=math.cos(ang) * sp, vy=math.sin(ang) * sp - 14,
                                            t=0.0, life=0.7 + random.random() * 0.7,
                                            s=3.0 + random.random() * 5.0,
                                            c=motion.mix(self.col, QColor("#ffffff"), random.random() * 0.35)))
                if self._acc > 40.0:
                    self._acc = 0.0
            self._last = QPointF(cur)
        else:
            self.a *= math.exp(-dt * 5.0)
            self._last = None
        for s in self.sparks:
            s["t"] += dt
            s["x"] += s["vx"] * dt
            s["y"] += s["vy"] * dt
            s["vx"] *= math.exp(-dt * 1.8)
            s["vy"] = s["vy"] * math.exp(-dt * 1.8) - 6 * dt
        self.sparks = [s for s in self.sparks if s["t"] < s["life"]]
        self.rings = [r for r in self.rings if now - r[2] < 0.9]
        new = self._bounds()
        dirty = old.united(new)
        if not dirty.isEmpty():
            self.over.update(dirty)
        if not inside and self.a < 0.01 and not self.sparks and not self.rings:
            self.a = 0.0
            self.pos = None
            self._timer.stop()

    def _bounds(self):
        """The region drawn now (page coordinates)."""
        r = QRectF()
        if self.pos is not None and self.a > 0.003:
            R = max(LIGHT_R, RIM_R) + 4
            r = QRectF(self.pos.x() - R, self.pos.y() - R, 2 * R, 2 * R)
        for s in self.sparks:
            q = QRectF(s["x"] - 12, s["y"] - 12, 24, 24)
            r = q if r.isNull() else r.united(q)
        now = time.monotonic()
        for x, y, t0, _c in self.rings:
            rr = 30 + 160 * min(1.0, (now - t0) / 0.9) + 12
            q = QRectF(x - rr, y - rr, 2 * rr, 2 * rr)
            r = q if r.isNull() else r.united(q)
        return r.toAlignedRect()

    # ── drawing ──────────────────────────────────────────────────────────────
    def _sprite(self, c):
        key = c.rgb()
        pm = self._sprites.get(key)
        if pm is None:
            if len(self._sprites) > 64:
                self._sprites.clear()
            pm = QPixmap(48, 48)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            g = QRadialGradient(QPointF(24, 24), 24)
            g.setColorAt(0, QColor(255, 255, 255, 240))
            g.setColorAt(0.2, _alpha(c, 0.95))
            g.setColorAt(0.55, _alpha(c, 0.25))
            g.setColorAt(1, _alpha(c, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QRectF(0, 0, 48, 48))
            p.end()
            self._sprites[key] = pm
        return pm

    def paint_under(self, w, e):
        """Under the blocks: the light, the sparks, the rings."""
        if self.a <= 0.003 and not self.sparks and not self.rings:
            return
        p = QPainter(w)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        light = theme.IS_LIGHT
        if self.pos is not None and self.a > 0.003:
            g = QRadialGradient(self.pos, LIGHT_R)
            k = (0.08 if light else 0.16) * self.a
            g.setColorAt(0.0, _alpha(self.col, k))
            g.setColorAt(0.4, _alpha(self.col, k * 0.35))
            g.setColorAt(1.0, _alpha(self.col, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(self.pos, LIGHT_R, LIGHT_R)
        p.end()

    def paint_over(self, w, e):
        """Over the blocks: the lit rims, the sparks and the rings (they stay on top, faint)."""
        p = QPainter(w)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        light = theme.IS_LIGHT
        if self.pos is not None and self.a > 0.003:
            g = QRadialGradient(self.pos, RIM_R)
            g.setColorAt(0.0, _alpha(self.col, 0.50 * self.a))
            g.setColorAt(0.45, _alpha(self.col, 0.18 * self.a))
            g.setColorAt(1.0, _alpha(self.col, 0.0))
            fill = QRadialGradient(self.pos, RIM_R * 0.9)
            fill.setColorAt(0.0, _alpha(self.col, (0.0 if light else 0.045) * self.a))
            fill.setColorAt(1.0, _alpha(self.col, 0.0))
            near = QRectF(self.pos.x() - RIM_R, self.pos.y() - RIM_R, 2 * RIM_R, 2 * RIM_R)
            for bw, _col, rad in self.blocks():
                try:
                    if not bw.isVisible():
                        continue
                    r = QRectF(bw.geometry())
                except RuntimeError:
                    continue
                if not r.intersects(near):
                    continue
                r = r.adjusted(0.5, 0.5, -0.5, -0.5)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(r, rad, rad)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(g, 1.2))
                p.drawRoundedRect(r, rad, rad)
        for s in self.sparks:
            f = s["t"] / s["life"]
            a = (1.0 - f) * min(1.0, s["t"] * 8.0)
            if a <= 0.01:
                continue
            sz = s["s"] * 2.4 * (1.0 - 0.4 * f)
            p.setOpacity(a * (0.42 if not light else 0.5))
            p.drawPixmap(QRectF(s["x"] - sz / 2, s["y"] - sz / 2, sz, sz), self._sprite(s["c"]),
                         QRectF(0, 0, 48, 48))
        p.setOpacity(1.0)
        now = time.monotonic()
        for x, y, t0, c in self.rings:
            k = min(1.0, (now - t0) / 0.9)
            e_ = 1 - (1 - k) ** 3
            rr = 30 + 160 * e_
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_alpha(c, 0.75 * (1 - k)), 2.0))
            p.drawEllipse(QPointF(x, y), rr, rr)
            p.setPen(QPen(_alpha(c, 0.25 * (1 - k)), 7.0))
            p.drawEllipse(QPointF(x, y), rr * 0.96, rr * 0.96)
        p.end()

