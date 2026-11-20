"""
qtui/bubbles.py — (19/11) SSA's help bubbles (the tooltips of every widget).

Qt draws a tooltip in its own small window (QTipLabel, a QLabel named
« qtooltip_label »), square and opaque: a stylesheet radius left dark corners.
Here the application style is Fusion behind a thin QProxyStyle whose only job is
to see each tooltip window being polished (once, when Qt creates it): the window
becomes translucent (real round corners, a soft shadow drawn by the stylesheet's
margin + a drop-shadow effect) and it fades in, rising by a few pixels.

Nothing else goes through Python: no event filter on the application (the map's
events stay untouched), only one filter on each tooltip window while it lives.
The « Help bubbles » setting (prefs « tips ») hides them all.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QProxyStyle, QStyleFactory, QToolTip, QWidget

from . import motion

MARGIN = 9                       # px of transparent margin around the bubble (its shadow)


def _on() -> bool:
    try:
        from . import prefs
        return bool(prefs.get("tips"))
    except Exception:
        return True


class _TipWatch(QObject):
    """On a tooltip window: fade it in when it shows (or hide it when bubbles are off)."""

    def eventFilter(self, o, e):
        t = e.type()
        if t == QEvent.Type.Show:
            if not _on():
                o.setWindowOpacity(0.0)
                QTimer.singleShot(0, QToolTip.hideText)
                return False
            self._appear(o)
        return False

    @staticmethod
    def _appear(w):
        if not motion.enabled() or QGuiApplication.platformName() not in ("windows", "xcb", "cocoa"):
            return
        try:
            lift = 5
            base = w.pos()
            exp = [base + QPoint(0, lift)]
            w.setWindowOpacity(0.0)
            w.move(exp[0])

            def step(t):
                w.setWindowOpacity(t)
                if w.pos() == exp[0]:            # (not when Qt moved it meanwhile: a new text)
                    exp[0] = base + QPoint(0, int(round(lift * (1.0 - t))))
                    w.move(exp[0])

            def done():
                w.setWindowOpacity(1.0)
            motion.run(w, 150, step, done)
        except RuntimeError:
            pass


_WATCH = None


class BubbleStyle(QProxyStyle):
    """Fusion, plus the bubbles' windows made translucent when Qt polishes them."""

    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))

    def polish(self, arg):
        if isinstance(arg, QWidget) and arg.metaObject().className() == "QTipLabel":   # (named later)
            try:
                _dress(arg)
            except Exception:                    # noqa: BLE001 — a plain tooltip then
                pass
        return super().polish(arg)


def _dress(w):
    global _WATCH
    if w.property("ssaBubble"):
        return
    w.setProperty("ssaBubble", True)
    if QGuiApplication.platformName() in ("windows", "xcb", "cocoa", "offscreen"):
        w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        w.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)     # the square system shadow
        fx = QGraphicsDropShadowEffect(w)
        fx.setBlurRadius(16)
        fx.setOffset(0, 3)
        fx.setColor(QColor(0, 0, 0, 120))
        w.setGraphicsEffect(fx)
    if _WATCH is None:
        _WATCH = _TipWatch()
    w.installEventFilter(_WATCH)


def install(app) -> None:
    """Once, before the stylesheet (theme.apply): the style that dresses the bubbles."""
    if getattr(app, "_ssa_bubbles", None) is not None:
        return
    try:
        from PySide6.QtWidgets import QApplication
        QApplication.setEffectEnabled(Qt.UIEffect.UI_FadeTooltip, False)    # our own fade
        QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, False)
        st = BubbleStyle()
        app.setStyle(st)
        app._ssa_bubbles = st                    # kept alive with the application
    except Exception:                            # noqa: BLE001
        app.setStyle("Fusion")
        app._ssa_bubbles = False
