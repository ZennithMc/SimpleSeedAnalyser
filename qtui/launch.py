"""
qtui/launch.py — (20/11) SSA opens.

The window first shows a veil in the colours of the theme: the mark builds itself in the
middle (its tile, then the pixels of its S one after the other), the name settles under
it with a light passing over it, fireflies around. Then the veil opens in a circle from
the mark while the mark flies to its place in the top bar and Home's blocks rise in,
one after the other.

About 1.5 s; a click or a key goes straight to the opening. Nothing at all when the
animations are off (Settings → Appearance). It only draws: nothing of SSA waits for it.
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QFontMetricsF, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from . import brand, theme

# the timeline (ms)
TILE_IN = (0, 440)            # the mark's tile grows in
PIX_START, PIX_GAP, PIX_MS = 170, 34, 230      # the 11 pixels of its S, one after the other
NAME_IN = (300, 820)          # the name: fades in, its letters close up
SHEEN = (560, 1120)           # a light passes over the name
PULSE = (860, 1300)           # the S complete: a ring leaves the mark
OPEN = (920, 1560)            # the veil opens, the mark flies to the top bar
END = OPEN[1]

MARK = 92                     # the mark's size in the middle
S_ORDER = ((2, 0), (1, 0), (0, 0), (0, 1), (0, 2), (1, 2), (2, 2), (2, 3), (2, 4), (1, 4), (0, 4))


def _k(ms, span):
    """0 → 1 over span (clamped)"""
    a, b = span
    return 0.0 if ms <= a else 1.0 if ms >= b else (ms - a) / float(b - a)


def _out3(t):
    return 1 - (1 - t) ** 3


def _inout3(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _back(t, s=1.70158):
    t -= 1
    return 1 + (s + 1) * t ** 3 + s * t ** 2


def _alpha(c, a):
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


class LaunchVeil(QWidget):
    def __init__(self, win, on_open=None, on_end=None):
        super().__init__(win)
        self.win, self._on_open, self._on_end = win, on_open, on_end
        self.c1, self.c2 = (QColor(c) for c in theme.ws_pair("home"))
        self.ink = QColor(theme.on_accent(self.c1.name()))
        self.light = theme.IS_LIGHT
        self.ms = 0.0
        self._clock = None
        self._opened = self._ended = False
        self._bg = None
        self._bg_key = None
        self._mark_pm = brand.mark(MARK, self.c1.name(), self.c2.name())
        self._to = None                               # the mark's place in the top bar (veil coords)
        self._hidden_mark = None
        r = random.random
        self._motes = [dict(a=r() * 6.283, d=0.55 + r() * 1.6, s=2.0 + r() * 4.5, ph=r() * 6.283,
                            v=8 + r() * 22, c=int(r() < 0.5), w=0.4 + r() * 0.6) for _ in range(34)]
        self._sprites = {}
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)     # nothing is painted under it
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setGeometry(win.rect())
        win.installEventFilter(self)
        self._tick = QTimer(self)
        self._tick.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick.timeout.connect(self._advance)
        self._tick.start(self._frame_ms())
        QTimer.singleShot(5000, self._finish)              # whatever happens, it ends
        self.raise_()
        self.show()
        self.setFocus()

    def _frame_ms(self):
        try:
            hz = float(self.screen().refreshRate())
        except Exception:                                  # noqa: BLE001
            hz = 60.0
        return int(max(6, min(16, 1000.0 / max(30.0, hz))))

    # ── time ─────────────────────────────────────────────────────────────────
    def _advance(self):
        now = time.monotonic()
        if self._clock is None:                            # the first frame on screen: the clock starts
            self._clock = now
            self.update()
            return
        dt, self._clock = now - self._clock, now
        self.ms += min(dt, 0.1) * 1000.0                   # a busy start freezes it rather than skips it
        if self.ms >= OPEN[0] and not self._opened:
            self._open()
        if self.ms >= END:
            self._finish()
            return
        self.update()

    def _skip(self):
        if self.ms < OPEN[0]:
            self.ms = float(OPEN[0])
            self._open()
        elif self.ms > OPEN[0] + 120:
            self._finish()

    def mousePressEvent(self, e):
        self._skip()

    def keyPressEvent(self, e):
        self._skip()

    def eventFilter(self, o, e):
        if o is self.win and e.type() == QEvent.Type.Resize:
            self.setGeometry(self.win.rect())
            self._bg = None
            self._to = None
        return False

    def _open(self):
        """The veil starts to open: Home's blocks come in, the real mark waits for the flying one."""
        self._opened = True
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        try:
            lab = self.win.top.mark
            fx = QGraphicsOpacityEffect(lab)
            fx.setOpacity(0.0)
            lab.setGraphicsEffect(fx)
            self._hidden_mark = lab
        except Exception:                                  # noqa: BLE001
            self._hidden_mark = None
        if self._on_open is not None:
            try:
                self._on_open()
            except Exception as ex:                        # noqa: BLE001
                print(f"[launch] {ex}", flush=True)

    def _finish(self):
        if self._ended:
            return
        self._ended = True
        self._tick.stop()
        if not self._opened:
            self._open()
        try:
            if self._hidden_mark is not None:
                self._hidden_mark.setGraphicsEffect(None)
        except RuntimeError:
            pass
        try:
            self.win.removeEventFilter(self)
        except RuntimeError:
            pass
        if self._on_end is not None:
            try:
                self._on_end()
            except Exception:                              # noqa: BLE001
                pass
        self.hide()
        self.deleteLater()

    # ── pictures made once ───────────────────────────────────────────────────
    def _background(self):
        """The veil: the theme's base gradient and a soft light of Home's colours in the middle,
        rendered small (it is soft) then stretched once to the window."""
        dpr = max(1.0, self.devicePixelRatioF())
        key = (self.width(), self.height(), dpr)
        if self._bg is not None and self._bg_key == key:
            return self._bg
        w, h = max(8, self.width() // 4), max(8, self.height() // 4)
        img = QImage(w, h, QImage.Format.Format_RGB32)
        p = QPainter(img)
        g = QLinearGradient(0, 0, w * 0.35, h)
        g.setColorAt(0, QColor(theme.BASE_TOP or theme.BG0))
        g.setColorAt(1, QColor(theme.BASE_BOTTOM or theme.BG1))
        p.fillRect(img.rect(), g)
        k = 0.22 if self.light else 0.30
        for (x, y, rad, c) in ((0.5, 0.46, 0.42, self.c1), (0.32, 0.62, 0.30, self.c2),
                               (0.70, 0.36, 0.28, self.c2)):
            rg = QRadialGradient(QPointF(x * w, y * h), rad * w)
            rg.setColorAt(0, _alpha(c, k))
            rg.setColorAt(0.55, _alpha(c, k * 0.30))
            rg.setColorAt(1, _alpha(c, 0.0))
            p.fillRect(img.rect(), rg)
        p.end()
        pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        q.drawImage(QRectF(pm.rect()), img, QRectF(img.rect()))
        q.end()
        self._bg, self._bg_key = pm, key
        return pm

    def _sprite(self, ci):
        pm = self._sprites.get(ci)
        if pm is None:
            s = 64
            pm = QPixmap(s, s)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            c = self.c1 if ci == 0 else self.c2
            rg = QRadialGradient(QPointF(s / 2, s / 2), s / 2)
            rg.setColorAt(0, QColor(255, 255, 255, 235))
            rg.setColorAt(0.18, _alpha(c, 0.95))
            rg.setColorAt(0.5, _alpha(c, 0.28))
            rg.setColorAt(1, _alpha(c, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rg)
            p.drawEllipse(QRectF(0, 0, s, s))
            p.end()
            self._sprites[ci] = pm
        return pm

    def _target(self):
        if self._to is None:
            try:
                lab = self.win.top.mark
                tl = lab.mapTo(self.win, QPoint(0, 0))
                s = min(lab.width(), lab.height())
                self._to = QRectF(tl.x() + (lab.width() - s) / 2, tl.y() + (lab.height() - s) / 2, s, s)
            except Exception:                              # noqa: BLE001
                self._to = QRectF(18, 17, 30, 30)
        return self._to

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, e):
        if self._clock is None and not self._opened:       # the first frame: the veil only
            p = QPainter(self)
            p.drawPixmap(QRectF(self.rect()), self._background(), QRectF(self._background().rect()))
            return
        ms = self.ms
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = float(self.width()), float(self.height())
        cx, cy = W / 2, H * 0.46
        bg = self._background()
        o = _out3(_k(ms, OPEN)) ** 1.6                     # soft start, then wide open
        if o <= 0.0:
            p.drawPixmap(QRectF(self.rect()), bg, QRectF(bg.rect()))
        else:                                              # the veil, with a hole that grows
            far = max(math.hypot(cx, cy), math.hypot(W - cx, cy), math.hypot(cx, H - cy),
                      math.hypot(W - cx, H - cy)) + 40
            rad = far * o
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            path.addRect(QRectF(self.rect()))
            path.addEllipse(QPointF(cx, cy), rad, rad)
            br = QBrush(bg)
            dpr = bg.width() / max(1.0, W)
            br.setTransform(QTransform.fromScale(1.0 / dpr, 1.0 / dpr))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(br)
            p.drawPath(path)
            fade = 1.0 - o
            if fade > 0.01 and rad > 2:                    # the opening's edge: a ring of light
                cg = QConicalGradient(QPointF(cx, cy), 90 + 200 * o)
                cg.setColorAt(0.0, _alpha(self.c1, 0.9 * fade))
                cg.setColorAt(0.5, _alpha(self.c2, 0.9 * fade))
                cg.setColorAt(1.0, _alpha(self.c1, 0.9 * fade))
                p.setBrush(Qt.BrushStyle.NoBrush)
                for wdt, a in ((10.0, 0.18), (4.0, 0.40), (1.6, 1.0)):
                    pen = QPen(QBrush(cg), wdt)
                    p.setOpacity(a)
                    p.setPen(pen)
                    p.drawEllipse(QPointF(cx, cy), rad, rad)
                p.setOpacity(1.0)
        self._paint_motes(p, ms, cx, cy, 1.0 - _k(ms, (OPEN[0], OPEN[0] + 260)))
        self._paint_name(p, ms, cx, cy + MARK * 0.5 + 34, 1.0 - _out3(_k(ms, (OPEN[0], OPEN[0] + 220))))
        self._paint_mark(p, ms, cx, cy)
        p.end()

    def _paint_motes(self, p, ms, cx, cy, out):
        a_in = _out3(_k(ms, (140, 760)))
        if a_in * out <= 0.01:
            return
        sec = ms / 1000.0
        R = MARK * 1.15
        for m in self._motes:
            ang = m["a"] + sec * 0.25 * (1 if m["c"] else -1) / m["d"]
            dist = R * m["d"] * (0.78 + 0.22 * a_in) + 6 * math.sin(sec * 1.7 + m["ph"])
            x = cx + math.cos(ang) * dist
            y = cy + math.sin(ang) * dist * 0.72 - m["v"] * sec
            tw = 0.55 + 0.45 * math.sin(sec * 3.1 + m["ph"])
            s = m["s"] * 2.6
            p.setOpacity(max(0.0, min(1.0, a_in * out * tw * m["w"])))
            p.drawPixmap(QRectF(x - s / 2, y - s / 2, s, s), self._sprite(m["c"]), QRectF(0, 0, 64, 64))
        p.setOpacity(1.0)

    def _paint_name(self, p, ms, cx, top, out):
        a = _out3(_k(ms, NAME_IN)) * out
        if a <= 0.01:
            return
        f = QFont(theme.DISPLAY_FAMILIES[0])
        f.setFamilies(list(theme.DISPLAY_FAMILIES))
        f.setPixelSize(34)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6 + 9.0 * (1.0 - _out3(_k(ms, NAME_IN))))
        text = "SimpleSeedAnalyser"
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(text)
        drop = 8.0 * (1.0 - a)
        x, y = cx - w / 2, top + fm.ascent() + drop
        path = QPainterPath()
        path.addText(QPointF(x, y), f, text)
        base = QColor(theme.TEXT)
        hi = self.c2.lighter(130) if not self.light else self.c1
        s = -0.3 + 1.6 * _inout3(_k(ms, SHEEN))            # a light passes over the name
        band = 0.2

        def col(pos):
            d = abs(pos - s)
            return base if d >= band else motion_mix(hi, base, d / band)
        g = QLinearGradient(QPointF(x, y), QPointF(x + w, y))
        for pos in sorted({0.0, 1.0, *(q for q in (s - band, s, s + band) if 0.0 < q < 1.0)}):
            g.setColorAt(pos, col(pos))
        p.setOpacity(a)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(path, QBrush(g))
        v = QFont(theme.UI_FAMILIES[0])
        v.setFamilies(list(theme.UI_FAMILIES))
        v.setPixelSize(11)
        v.setWeight(QFont.Weight.DemiBold)
        v.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3.2)
        try:
            import ssa_paths
            ver = "VERSION " + ssa_paths.APP_VERSION
        except Exception:                                  # noqa: BLE001
            ver = ""
        if ver:
            fv = QFontMetricsF(v)
            av = _out3(_k(ms, (NAME_IN[0] + 160, NAME_IN[1] + 120))) * out
            p.setOpacity(av)
            p.setFont(v)
            p.setPen(QColor(theme.TEXT3))
            p.drawText(QPointF(cx - fv.horizontalAdvance(ver) / 2, y + fm.descent() + 22 + drop), ver)
        p.setOpacity(1.0)

    def _paint_mark(self, p, ms, cx, cy):
        fly = _inout3(_k(ms, (OPEN[0] + 20, END)))
        if fly > 0.0:                                      # flying to the top bar: the finished mark
            to = self._target()
            s = MARK + (to.width() - MARK) * fly
            # a slight arc: up first, then into place
            x = cx + (to.center().x() - cx) * fly
            y = cy + (to.center().y() - cy) * fly - 40 * math.sin(math.pi * fly) * (1 - fly)
            glow = 1.0 - fly
            if glow > 0.02:
                rg = QRadialGradient(QPointF(x, y), s * 1.2)
                rg.setColorAt(0, _alpha(self.c1, 0.35 * glow))
                rg.setColorAt(1, _alpha(self.c1, 0.0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(rg)
                p.drawEllipse(QPointF(x, y), s * 1.2, s * 1.2)
            p.drawPixmap(QRectF(x - s / 2, y - s / 2, s, s), self._mark_pm, QRectF(self._mark_pm.rect()))
            return
        t = _k(ms, TILE_IN)
        if t <= 0.0:
            return
        sc = 0.45 + 0.55 * _back(t, 1.4)
        size = MARK * sc
        # the halo behind, breathing
        hk = _out3(t) * (0.85 + 0.15 * math.sin(ms / 1000.0 * 2.4))
        rg = QRadialGradient(QPointF(cx, cy), MARK * 1.6)
        rg.setColorAt(0, _alpha(self.c1, (0.30 if not self.light else 0.20) * hk))
        rg.setColorAt(0.45, _alpha(self.c2, 0.10 * hk))
        rg.setColorAt(1, _alpha(self.c2, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(rg)
        p.drawEllipse(QPointF(cx, cy), MARK * 1.6, MARK * 1.6)
        # the pulse once the S is complete
        pk = _k(ms, PULSE)
        if 0.0 < pk < 1.0:
            rr = MARK * 0.62 + 90 * _out3(pk)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_alpha(self.c1, 0.55 * (1 - pk)), 2.0))
            p.drawEllipse(QPointF(cx, cy), rr, rr)
            p.setPen(QPen(_alpha(self.c2, 0.30 * (1 - pk)), 6.0))
            p.drawEllipse(QPointF(cx, cy), rr * 0.97, rr * 0.97)
        # the tile
        p.setOpacity(min(1.0, t * 2.2))
        rect = QRectF(cx - size / 2, cy - size / 2, size, size)
        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        g.setColorAt(0, self.c1)
        g.setColorAt(1, self.c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(rect, size * 0.30, size * 0.30)
        # its S, pixel after pixel (the geometry of brand.mark)
        c = size * 0.118
        x0, y0 = rect.left() + (size - 3 * c) / 2, rect.top() + (size - 5 * c) / 2
        p.setOpacity(1.0)
        for i, (px, py) in enumerate(S_ORDER):
            k = _k(ms, (PIX_START + i * PIX_GAP, PIX_START + i * PIX_GAP + PIX_MS))
            if k <= 0.0:
                continue
            s = (c + 0.2) * max(0.0, _back(k, 2.2))
            ccx, ccy = x0 + px * c + c / 2, y0 + py * c + c / 2
            flash = max(0.0, 1.0 - k * 1.6)
            p.setBrush(motion_mix(self.ink, QColor(255, 255, 255), flash * 0.6))
            p.drawRect(QRectF(ccx - s / 2, ccy - s / 2, s, s))


def motion_mix(a, b, t):
    from .motion import mix
    return mix(a, b, max(0.0, min(1.0, t)))


class RiseLayer(QWidget):
    """(20/11 bis) Home's blocks rising in as the veil opens, made smooth: each block is
    photographed once (its exact pixels), and the photos glide up and fade in on this layer
    (only pictures drawn — the real widgets neither move nor repaint through an effect).
    When a photo arrives, its real block takes its place in the same frame."""

    def __init__(self, win, items):
        """items: [(widget, dy, delay ms, duration ms, corner radius)]; the widgets are invisible (an opacity
        effect at 0) until they take the place of their photo."""
        super().__init__(win)
        self.win = win
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setGeometry(win.rect())
        self.sprites = []
        # the photos are taken from the window's content (backdrop included: a glass panel
        # looks exactly as it will), all blocks shown for that instant, then hidden again
        host = win.centralWidget() or win
        live = []
        for it in items:
            try:
                it[0].setGraphicsEffect(None)
                live.append(it)
            except RuntimeError:
                continue
        for w, dy, delay, ms, rad in live:
            try:
                tl = w.mapTo(host, QPoint(0, 0))
                pm = host.grab(QRect(tl, w.size()))
                at = w.mapTo(win, QPoint(0, 0))
            except RuntimeError:
                continue
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(0, 0, w.width(), w.height()), rad, rad)
            self.sprites.append(dict(w=w, pm=pm, x=at.x(), y=at.y(), dy=dy, d=delay, ms=ms, done=False,
                                     clip=clip))
        for w, *_r in live:
            try:
                hold = QGraphicsOpacityEffect(w)
                hold.setOpacity(0.0)
                w.setGraphicsEffect(hold)
            except RuntimeError:
                pass
        self._t0 = time.monotonic()
        self._last = None
        win.installEventFilter(self)
        self._tick = QTimer(self)
        self._tick.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick.timeout.connect(self._advance)
        try:
            hz = float(self.screen().refreshRate())
        except Exception:                              # noqa: BLE001
            hz = 60.0
        self._tick.start(int(max(6, min(16, 1000.0 / max(30.0, hz)))))
        self.raise_()
        self.show()

    def eventFilter(self, o, e):
        if o is self.win and e.type() == QEvent.Type.Resize:
            self.finish()                              # the blocks moved: straight to the end
        return False

    def _k(self, s, now):
        return max(0.0, min(1.0, ((now - self._t0) * 1000.0 - s["d"]) / float(s["ms"])))

    def _rect(self, s):
        return QRectF(s["x"], s["y"], s["pm"].width() / s["pm"].devicePixelRatio(),
                      s["pm"].height() / s["pm"].devicePixelRatio())

    def _advance(self):
        now = time.monotonic()
        dirty = QRectF()
        left = 0
        for s in self.sprites:
            if s["done"]:
                continue
            k = self._k(s, now)
            r = self._rect(s).adjusted(-2, -2, 2, s["dy"] + 2)
            dirty = r if dirty.isNull() else dirty.united(r)
            if k >= 1.0:
                s["done"] = True
                try:
                    s["w"].setGraphicsEffect(None)     # the real block, in the same frame
                except RuntimeError:
                    pass
            else:
                left += 1
        if not dirty.isNull():
            self.update(dirty.toAlignedRect())
        if not left:
            self._end()

    def paintEvent(self, e):
        now = time.monotonic()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for s in self.sprites:
            if s["done"]:
                continue
            k = self._k(s, now)
            if k <= 0.0:
                continue
            e_ = _out3(k)
            p.setOpacity(min(1.0, k * 1.6))
            r = self._rect(s)
            p.save()
            p.translate(r.x(), r.y() + s["dy"] * (1.0 - e_))
            p.setClipPath(s["clip"])
            p.drawPixmap(QPointF(0, 0), s["pm"])
            p.restore()
        p.end()

    def finish(self):
        for s in self.sprites:
            if not s["done"]:
                s["done"] = True
                try:
                    s["w"].setGraphicsEffect(None)
                except RuntimeError:
                    pass
        self._end()

    def _end(self):
        self._tick.stop()
        try:
            self.win.removeEventFilter(self)
        except RuntimeError:
            pass
        self.hide()
        self.deleteLater()
