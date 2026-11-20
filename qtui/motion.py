"""
qtui/motion.py — (14/11) SSA in motion: the transitions and the small living
things of the interface. Nothing here changes what SSA computes.

    enabled()            Settings → Appearance → Animations (prefs "motion")
    Backdrop             the window's background: in SSA's own theme, soft lights
                         of the workspace's two colours that glide to a new random
                         arrangement and change colour when the page changes
    FadeStack            a QStackedWidget whose pages cross-fade (the old page's
                         picture slides a little and fades over the new one)
    Shine                a light that sweeps across a button (hover, now and then)
                         or along a progress bar while a search runs
    run(owner, ms, fn)   a 0 → 1 animation (QVariantAnimation), eased

Costs: every animation is short and stops by itself; nothing runs while the
interface is idle, except the Home page's fireflies (20 frames a second on the
hero only, while Home is on screen and the window active).
"""
from __future__ import annotations

import random
import time

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QStackedWidget, QStyle, QStyleOption, QWidget

from . import theme

_ON = [None, 0.0]


def enabled() -> bool:
    now = time.monotonic()
    if _ON[0] is None or now - _ON[1] > 2.0:
        try:
            import os
            from . import prefs
            _ON[0] = bool(prefs.get("motion")) and not os.environ.get("SSA_NO_MOTION")
        except Exception:
            _ON[0] = True
        _ON[1] = now
    return _ON[0]


def run(owner, ms, on_value, on_done=None, curve=QEasingCurve.Type.OutCubic):
    """Animate 0 → 1 in ms milliseconds; on_value(t) at each step. Returns the animation."""
    a = QVariantAnimation(owner)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setDuration(int(ms))
    a.setEasingCurve(curve)

    def tick(v):
        try:
            on_value(float(v))
        except RuntimeError:                 # what it moved is gone (a widget closed, an effect replaced):
            try:                             # the animation ends there, silently
                a.stop()
            except RuntimeError:
                pass

    def end():
        try:
            on_done()
        except RuntimeError:
            pass
    a.valueChanged.connect(tick)
    if on_done is not None:
        a.finished.connect(end)
    a.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return a


def mix(a, b, t: float) -> QColor:
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t), round(a.alpha() + (b.alpha() - a.alpha()) * t))


def fade_in(widget, ms=220):
    """The widget appears (opacity 0 → 1); the effect is removed at the end."""
    if not enabled() or not widget.isVisible():
        return
    old = getattr(widget, "_fade_an", None)      # (14/11) a fade restarted before its end (a seed typed
    if old is not None:                          # digit by digit): the previous one stops first — its
        try:                                     # effect is deleted by the new one
            old.stop()
        except RuntimeError:
            pass
    fx = QGraphicsOpacityEffect(widget)
    fx.setOpacity(0.0)
    widget.setGraphicsEffect(fx)

    def done():
        widget._fade_an = None
        if widget.graphicsEffect() is fx:        # only its own effect
            widget.setGraphicsEffect(None)
    widget._fade_an = run(widget, ms, fx.setOpacity, done)


def rise(w, dx=0, dy=16, ms=480, delay=0):
    """(20/11) w comes in: from (dx, dy) away to its place, fading in (SSA opening). Its place is
    the one its layout gives it when the move starts; the layout takes it back at the end."""
    if not enabled():
        try:
            w.setGraphicsEffect(None)
        except RuntimeError:
            pass
        return
    try:
        fx = QGraphicsOpacityEffect(w)
        fx.setOpacity(0.0)
        w.setGraphicsEffect(fx)
    except RuntimeError:
        return

    def go():
        try:
            home = w.pos()
        except RuntimeError:
            return

        def step(t):
            fx.setOpacity(min(1.0, t * 1.25))
            w.move(home.x() + int(round(dx * (1 - t))), home.y() + int(round(dy * (1 - t))))

        def end():
            try:
                if w.graphicsEffect() is fx:
                    w.setGraphicsEffect(None)
                w.move(home)
                pw = w.parentWidget()
                if pw is not None and pw.layout() is not None:
                    pw.layout().invalidate()
            except RuntimeError:
                pass
        run(w, ms, step, end, QEasingCurve.Type.OutCubic)
    QTimer.singleShot(int(delay), go)


def install_dialog_fade():
    """Modal dialogs appear in a short fade (their window's opacity 0 → 1 in
    150 ms). Done once, by wrapping QDialog.exec: no event filter on the
    application, so nothing is added to the events of the map."""
    from PySide6.QtWidgets import QDialog
    if getattr(QDialog, "_ssa_fade", False):
        return
    plain = QDialog.exec

    fades = QGuiApplication.platformName() in ("windows", "xcb", "cocoa")   # where a window can be translucent

    def exec_(self, *a, **k):
        if fades and enabled():
            try:
                self.setWindowOpacity(0.0)

                def show_fully():
                    try:
                        self.setWindowOpacity(1.0)
                    except RuntimeError:
                        pass

                def start():
                    try:
                        run(self, 150, self.setWindowOpacity, show_fully)
                    except RuntimeError:
                        pass
                QTimer.singleShot(0, start)
                QTimer.singleShot(450, show_fully)          # whatever happens, it ends fully shown
            except Exception:
                pass
        return plain(self, *a, **k)
    try:
        QDialog.exec = exec_
        QDialog._ssa_fade = True
    except Exception:
        pass


# ── the window's background ──────────────────────────────────────────────────
class Backdrop(QWidget):
    """The central widget. SSA's own theme (theme.is_live): three soft lights in
    the two colours of the workspace; a page change glides them to a new random
    arrangement while their colours change. Other themes: their stylesheet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self._blobs = self._arrange()
        self._cols = self._colors()
        self._from = None
        self._t = 1.0
        self._anim = None
        self._img = None                         # the lights, rendered small (they are soft) and stretched
        self._full = self._full_key = None       # ... and stretched once to the window's size, at rest
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, theme.is_live())
        theme.signals.accent.connect(self._shift)
        theme.signals.changed.connect(self._shift)
        self._amb = QTimer(self)                 # now and then the lights drift to a new place
        self._amb.setSingleShot(True)
        self._amb.timeout.connect(self._breathe)
        self._amb.start(random.randint(14000, 26000))

    def _breathe(self):
        self._amb.start(random.randint(14000, 26000))
        win = self.window()
        if (theme.is_live() and enabled() and self.isVisible() and self._t >= 1.0
                and win is not None and win.isActiveWindow()
                and QGuiApplication.mouseButtons() == Qt.MouseButton.NoButton   # never during a drag
                and not self._big_map()):
            self._shift(ms=2800)

    def _big_map(self):
        """(15/11) a map fills the page: the lights stay still (each of their frames
        repainted the whole map under them, for 3 s)."""
        half = self.width() * self.height() * 0.4
        for c in self.findChildren(QWidget):
            if type(c).__name__ == "MapView" and c.isVisible() and c.width() * c.height() > half:
                return True
        return False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._img = None
        self._full = self._full_key = None

    @staticmethod
    def _colors():
        c1, c2 = theme.ws_pair(theme.WORKSPACE)
        return QColor(c1), QColor(c2)

    @staticmethod
    def _arrange():
        r = random.random
        # (x, y, radius) in fractions of the width / height, and which colour
        # (14/11 bis) five of them, behind the panels (left, right, bar below) as well: the
        # panels are glass in SSA's theme and take their colour
        return [(0.02 + 0.20 * r(), -0.06 + 0.10 * r(), 0.48 + 0.16 * r(), 0),
                (0.64 + 0.32 * r(), -0.04 + 0.12 * r(), 0.40 + 0.18 * r(), 1),
                (0.00 + 0.14 * r(), 0.40 + 0.40 * r(), 0.30 + 0.12 * r(), 1 if r() < 0.5 else 0),
                (0.88 + 0.12 * r(), 0.35 + 0.45 * r(), 0.30 + 0.12 * r(), 0 if r() < 0.5 else 1),
                (0.25 + 0.50 * r(), 0.96 + 0.08 * r(), 0.40 + 0.18 * r(), 1 if r() < 0.6 else 0)]

    def _now(self):
        """The lights as drawn at this instant [(x, y, radius, QColor)]"""
        out = []
        for i, (x, y, rad, ci) in enumerate(self._blobs):
            col = self._cols[ci]
            if self._from is not None and self._t < 1.0:
                fx, fy, fr, fc = self._from[i]
                t = self._t
                x, y, rad, col = fx + (x - fx) * t, fy + (y - fy) * t, fr + (rad - fr) * t, mix(fc, col, t)
            out.append((x, y, rad, col))
        return out

    def _shift(self, ms=720):
        new = self._colors()
        self._img = None
        # (15/11) SSA's theme paints every pixel: the window under it is not painted
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, theme.is_live())
        if not theme.is_live():
            self._cols = new
            self.update()
            return
        if not enabled() or not self.isVisible():
            self._cols, self._from, self._t = new, None, 1.0
            self.update()
            return
        self._from = self._now()
        self._blobs, self._cols, self._t = self._arrange(), new, 0.0
        if self._anim is not None:
            try:
                self._anim.stop()
            except RuntimeError:
                pass

        def step(t):
            self._t = t
            self._img = None
            self.update()
        self._anim = run(self, ms, step, curve=QEasingCurve.Type.InOutCubic)

    def paintEvent(self, e):
        p = QPainter(self)
        if not theme.is_live():                      # the stylesheet's background / backdrop picture
            opt = QStyleOption()
            opt.initFrom(self)
            self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)
            return
        if self._img is None:
            self._img = self._render()
        if self._t < 1.0:                            # the lights moving: stretched each frame
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF(self.rect()), self._img, QRectF(self._img.rect()))
            return
        # (15/11) at rest: stretched ONCE into a picture of the window's size, then only
        # the part to repaint is copied (the map repaints this region at each of its
        # frames; the stretch of the whole picture cost several ms)
        dpr = max(1.0, self.devicePixelRatioF())
        key = (id(self._img), self.width(), self.height(), dpr)
        if getattr(self, "_full_key", None) != key:
            pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            q.drawImage(QRectF(self.rect()), self._img, QRectF(self._img.rect()))
            q.end()
            self._full, self._full_key = pm, key
        r = e.rect()
        p.drawPixmap(QRectF(r), self._full, QRectF(r.x() * dpr, r.y() * dpr, r.width() * dpr, r.height() * dpr))

    def _render(self, k=4):
        """The base gradient and the lights at 1/k of the size: a repaint of the
        window (or of anything translucent above it) only stretches this picture."""
        w, h = max(8, self.width() // k), max(8, self.height() // k)
        img = QImage(w, h, QImage.Format.Format_RGB32)
        p = QPainter(img)
        g = QLinearGradient(0, 0, w * 0.35, h)
        g.setColorAt(0, QColor(theme.BASE_TOP))
        g.setColorAt(1, QColor(theme.BASE_BOTTOM))
        p.fillRect(img.rect(), g)
        for x, y, rad, col in self._now():
            rg = QRadialGradient(QPointF(x * w, y * h), rad * w)
            c = QColor(col)
            c.setAlphaF(0.34 if not theme.IS_LIGHT else 0.30)
            rg.setColorAt(0, c)
            c2 = QColor(col)
            c2.setAlphaF(0.12)
            rg.setColorAt(0.5, c2)
            c3 = QColor(col)
            c3.setAlphaF(0.0)
            rg.setColorAt(1, c3)
            p.fillRect(img.rect(), rg)
        p.end()
        return img


# ── pages that cross-fade ────────────────────────────────────────────────────
class FadeStack(QStackedWidget):
    """A page change = the picture of the old page slides `slide` pixels (towards
    the side it leaves by) and fades over the new page, which is already live."""

    def __init__(self, parent=None, slide=22, ms=230):
        super().__init__(parent)
        self.slide, self.ms = slide, ms
        self._ov = None
        self._an = None

    def setCurrentIndex(self, i):
        self._go(i)

    def setCurrentWidget(self, w):
        self._go(self.indexOf(w))

    def _drop(self):
        ov, self._ov = self._ov, None
        if ov is not None:
            try:
                ov.hide()
                ov.deleteLater()
            except RuntimeError:
                pass

    def _go(self, i):
        old = self.currentIndex()
        if i < 0 or i == old or old < 0 or not enabled() or not self.isVisible() or self.width() < 16:
            QStackedWidget.setCurrentIndex(self, i)
            return
        try:
            pm = self.grab()
        except Exception:
            pm = None
        if self._an is not None:
            try:
                self._an.stop()
            except RuntimeError:
                pass
        self._drop()
        QStackedWidget.setCurrentIndex(self, i)
        if pm is None or pm.isNull():
            return
        ov = QLabel(self)
        ov.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        ov.setPixmap(pm)
        ov.setGeometry(self.rect())
        fx = QGraphicsOpacityEffect(ov)
        ov.setGraphicsEffect(fx)
        ov.show()
        ov.raise_()
        self._ov = ov
        d = -1 if i > old else 1

        def step(t):
            try:
                fx.setOpacity(1.0 - t)
                ov.move(int(d * self.slide * t), 0)
            except RuntimeError:
                pass
        self._an = run(self, self.ms, step, self._drop)


# ── a light sweeping across a button / a progress bar ────────────────────────
class Shine(QWidget):
    """A child of `host` drawn over it: a soft diagonal light crossing it.
    hover=True: when the mouse enters; every=(a, b): at random, every a to b
    seconds, while the host is enabled and visible; loop(True): continuously."""

    def __init__(self, host, hover=True, every=None, radius=None, strength=0.36):
        super().__init__(host)
        self.host, self.radius, self.strength = host, radius, strength
        self.t = -1.0
        self._an = None
        self._loop = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._hover = hover
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.raise_()
        self.show()
        self._every = every
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        if every:
            self._timer.start(int(1000 * random.uniform(*every)))

    def eventFilter(self, o, e):
        t = e.type()
        if t == QEvent.Type.Resize:
            self.setGeometry(self.host.rect())
            self.raise_()
        elif t == QEvent.Type.Enter and self._hover:
            self.sweep()
        return False

    def _tick(self):
        self.sweep()
        if self._every:
            self._timer.start(int(1000 * random.uniform(*self._every)))

    def loop(self, on: bool):
        self._loop = bool(on)
        if on:
            self.sweep()

    def sweep(self, ms=None):
        if self._an is not None or not enabled():
            return
        if not self.host.isEnabled() or not self.host.isVisible() or self.host.width() < 12:
            return

        def step(t):
            self.t = t
            self.update()

        def done():
            self._an, self.t = None, -1.0
            self.update()
            if self._loop:
                QTimer.singleShot(260, self.sweep)
        ms = ms or (1300 if self._loop else max(520, min(900, int(self.host.width() * 3.2))))
        self._an = run(self, ms, step, done, QEasingCurve.Type.InOutSine)

    def paintEvent(self, e):
        if self.t < 0:
            return
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.radius if self.radius is not None else min(theme.RADIUS + 3, h / 2)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.setClipPath(path)
        band = max(26.0, w * 0.30)
        x = -band + (w + 2 * band) * self.t
        g = QLinearGradient(x - band, 0, x + band, h * 0.9)        # a slanted band
        c0 = QColor(255, 255, 255, 0)
        c1 = QColor(255, 255, 255, int(255 * self.strength))
        g.setColorAt(0.0, c0)
        g.setColorAt(0.5, c1)
        g.setColorAt(1.0, c0)
        p.fillRect(self.rect(), g)


class Spot(QWidget):
    """(15/11) A soft light that follows the cursor over `host` (a button, a tile):
    it appears when the mouse enters and fades when it leaves. Drawn over the host,
    only while hovered (the cursor is read 30 times a second meanwhile)."""

    def __init__(self, host, color=None, strength=0.20, radius=None):
        super().__init__(host)
        self.host, self.color, self.strength, self.radius = host, color, strength, radius
        self._a = 0.0
        self._an = None
        self._pos = QPointF(-999, -999)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self.raise_()
        self.show()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._follow)

    def eventFilter(self, o, e):
        t = e.type()
        if t == QEvent.Type.Resize:
            self.setGeometry(self.host.rect())
            self.raise_()
        elif t == QEvent.Type.Enter and enabled() and self.host.isEnabled():
            self._follow()
            self._timer.start()
            self._fade(1.0)
        elif t in (QEvent.Type.Leave, QEvent.Type.Hide):
            self._timer.stop()
            self._fade(0.0)
        return False

    def _follow(self):
        from PySide6.QtGui import QCursor
        p = QPointF(self.mapFromGlobal(QCursor.pos()))
        if p != self._pos:
            self._pos = p
            self.update()

    def _fade(self, to):
        if self._an is not None:
            try:
                self._an.stop()
            except RuntimeError:
                pass
            self._an = None
        a0 = self._a

        def step(t):
            self._a = a0 + (to - a0) * t
            self.update()

        def done():
            self._an = None
        self._an = run(self, 180, step, done)

    def paintEvent(self, e):
        if self._a <= 0.01:
            return
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.radius if self.radius is not None else min(theme.RADIUS + 3, h / 2)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.setClipPath(path)
        rad = max(46.0, min(w, 260) * 0.55)
        g = QRadialGradient(self._pos, rad)
        c = QColor(self.color or "#ffffff")
        c.setAlphaF(self.strength * self._a)
        g.setColorAt(0, c)
        c2 = QColor(c)
        c2.setAlphaF(0.0)
        g.setColorAt(1, c2)
        p.fillRect(self.rect(), g)


def roll_text(label, final: str, ms=460):
    """The text of a label settles from random digits, left to right (a seed that
    changes). Without animations: set at once."""
    if not enabled() or not label.isVisible() or not final:
        label.setText(final)
        return
    old = getattr(label, "_roll", None)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:
            pass
    n = len(final)

    def step(t):
        k = int((n + 3) * t) - 1
        label.setText("".join(ch if (i <= k or not ch.isdigit()) else random.choice("0123456789")
                              for i, ch in enumerate(final)))

    def done():
        label._roll = None
        label.setText(final)
    label._roll = run(label, ms, step, done, QEasingCurve.Type.OutQuad)
