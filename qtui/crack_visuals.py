"""
qtui/crack_visuals.py — (15/11) the Crack page's pictures. Only the look: each one
shows or drives the page's own controls.

    SourceCard(title, glyph, info, button)   End pillars / Structures: a card with its
                                             picture, the page's info label (its text is set
                                             by the page as before) and its Edit button; a
                                             click anywhere on the card presses Edit. Lit in
                                             the workspace's colour once used.
    EmptyTrees(view, hint, on_add, on_import) over the empty list of trees: an oak drawn
                                             with its 12 corner leaves, the page's hint, and
                                             the two ways to start
    LeafGuide()                              the inspector's empty state: the 3 leaf layers
                                             and their corners — what to look at on a tree
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import motion, theme

LEAF, LEAF_D, LOG, LOG_D = "#5fa83a", "#3f7a26", "#8a6a3e", "#5e4626"


def _c(hex_, a=1.0):
    c = QColor(hex_)
    c.setAlphaF(a)
    return c


# ── End pillars / Structures ─────────────────────────────────────────────────
class _Glyph(QWidget):
    def __init__(self, kind):
        super().__init__()
        self.kind, self.on = kind, False
        self.setFixedSize(40, 40)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = theme.ACCENT
        r = QRectF(0.5, 0.5, 39, 39)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c(a, 0.24 if self.on else 0.10))
        p.drawRoundedRect(r, 11, 11)
        if self.kind == "pillars":                # obsidian pillars of different heights, a crystal
            for i, (x, h) in enumerate(((9, 15), (15.5, 22), (22, 12), (28.5, 18))):
                g = QLinearGradient(x, 33 - h, x + 5, 33)
                g.setColorAt(0, QColor("#4b3a6e")); g.setColorAt(1, QColor("#1c1430"))
                p.setBrush(g)
                p.drawRect(QRectF(x, 33 - h, 5, h))
            p.setBrush(QColor("#f0b8ff" if self.on else "#b9a6d6"))
            p.drawEllipse(QPointF(18, 8.5), 2.6, 2.6)
            p.setBrush(_c("#f0b8ff", 0.25))
            p.drawEllipse(QPointF(18, 8.5), 5, 5)
        else:                                     # a pin over a small village roof
            from . import struct_icons
            ic = struct_icons.pixmap("village", 22)
            p.drawPixmap(QRectF(9, 11, 22, 22), ic, QRectF(ic.rect()))
            pin = QPainterPath(QPointF(29, 18))
            pin.lineTo(25.5, 11)
            pin.arcTo(QRectF(25, 3.5, 8, 8), 200, -220)
            pin.closeSubpath()
            p.setBrush(QColor(a if self.on else theme.TEXT3))
            p.drawPath(pin)


class SourceCard(QFrame):
    def __init__(self, title, glyph, info: QLabel, button: QPushButton, parent=None):
        super().__init__(parent)
        self.setObjectName("CrackSource")
        self.info, self.button = info, button
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self); h.setContentsMargins(10, 8, 8, 8); h.setSpacing(10)
        self.glyph = _Glyph(glyph)
        h.addWidget(self.glyph)
        col = QVBoxLayout(); col.setSpacing(1)
        t = QLabel(title); f = t.font(); f.setWeight(QFont.Weight.DemiBold); t.setFont(f)
        col.addWidget(t)
        info.setTextFormat(Qt.TextFormat.RichText)
        col.addWidget(info)
        h.addLayout(col, 1)
        button.setText("")                        # the card says it: only the pencil stays
        from . import icons
        icons.bind(button, "edit", 16)
        button.setProperty("kind", "ghost")
        button.setFixedSize(32, 32)
        h.addWidget(button)
        self._used = None
        self._sync = QTimer(self, singleShot=True, interval=0, timeout=self.refresh)
        info.installEventFilter(self)
        self.refresh()

    def eventFilter(self, o, e):
        if e.type() in (QEvent.Type.Paint,) and o is self.info:
            if self._used != self._is_used():
                self._sync.start()
        return False

    def _is_used(self):
        return not self.info.text().strip().startswith("Not used")

    def refresh(self):
        used = self._is_used()
        self._used = used
        self.glyph.on = used
        self.glyph.update()
        self.setProperty("used", "true" if used else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()) \
                and self.button.isEnabled():
            self.button.click()
        super().mouseReleaseEvent(e)


# ── the empty list of trees ──────────────────────────────────────────────────
def _oak(p, cx, base, s, corners=None, t=0.0):
    """a side view of an oak: trunk, 3 leaf layers of 5 and the top; the corner leaves marked.
    corners: colours of the 6 visible corners (left / right of the 3 layers) or None"""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(LOG))
    p.drawRect(QRectF(cx - s / 2, base - 6 * s, s, 6 * s))
    p.setBrush(QColor(LOG_D))
    p.drawRect(QRectF(cx - s / 2, base - 6 * s, s * 0.3, 6 * s))
    rows = [(base - 4 * s, 2), (base - 5 * s, 2), (base - 6 * s, 1), (base - 7 * s, 1)]
    shades = ("#5fa83a", "#4f9330", "#68b343", "#467f2b")
    k = 0
    for ri, (y, r) in enumerate(rows):
        for i in range(-r, r + 1):
            corner = r == 2 and abs(i) == 2
            x = cx + i * s - s / 2
            col = QColor(shades[(i * 3 + ri * 5 + 7) % 4])
            if corner:
                col = QColor(corners[k] if corners else LEAF)
                k += 1
            p.setBrush(col)
            p.drawRect(QRectF(x, y - s, s, s))
            if corner:
                glow = 0.55 + 0.45 * math.sin(t * 2.2 + k)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_c("#ffffff", 0.35 + 0.4 * glow), 1.4))
                p.drawRect(QRectF(x + 0.7, y - s + 0.7, s - 1.4, s - 1.4))
                p.setPen(Qt.PenStyle.NoPen)


class EmptyTrees(QWidget):
    def __init__(self, host, hint: QLabel, on_add, on_import):
        super().__init__(host)
        self.host, self.hint = host, hint
        self._t = 0.0
        v = QVBoxLayout(self); v.setContentsMargins(14, 0, 14, 16); v.setSpacing(8)
        v.addStretch(1)
        self.text = QLabel(); self.text.setObjectName("Muted"); self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self.text)
        row = QHBoxLayout(); row.setSpacing(6)
        self.b_add = QPushButton("  Add tree"); self.b_add.setProperty("kind", "primary")
        from . import icons
        icons.bind(self.b_add, "plus", 15, "on_accent", "on_accent")
        self.b_add.clicked.connect(on_add)
        self.b_imp = QPushButton("Import…"); self.b_imp.setProperty("kind", "ghost")
        self.b_imp.setToolTip("Import a TreeCracker input")
        self.b_imp.clicked.connect(on_import)
        row.addStretch(1); row.addWidget(self.b_add); row.addWidget(self.b_imp); row.addStretch(1)
        v.addLayout(row)
        host.installEventFilter(self)
        self.setGeometry(host.rect())
        self._tick = QTimer(self); self._tick.setInterval(60); self._tick.timeout.connect(self._step)

    def eventFilter(self, o, e):
        if e.type() == QEvent.Type.Resize:
            self.setGeometry(self.host.rect())
        return False

    def set_shown(self, on):
        self.text.setText(self.hint.text())
        self.setVisible(bool(on))
        if on:
            self.raise_()

    def showEvent(self, e):
        super().showEvent(e)
        if motion.enabled():
            self._tick.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._tick.stop()

    def _step(self):
        win = self.window()
        if win is not None and win.isActiveWindow():
            self._t += 0.06
            self.update(QRectF(0, 0, self.width(), self.height() * 0.6).toRect())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w = self.width()
        s = 14.0
        room = self.height() - self.text.sizeHint().height() - 70     # above the text and the buttons
        base = max(9 * s, room / 2.0 + 4.5 * s)
        cx = w / 2.0
        glow = QColor(theme.ACCENT)
        from PySide6.QtGui import QRadialGradient
        rg = QRadialGradient(QPointF(cx, base - 4 * s), 9 * s)
        rg.setColorAt(0, _c(glow.name(), 0.16)); rg.setColorAt(1, _c(glow.name(), 0.0))
        p.fillRect(self.rect(), rg)
        p.setPen(Qt.PenStyle.NoPen)                     # a strip of grass blocks
        for i in range(-3, 4):
            x = cx + i * s - s / 2
            p.setBrush(QColor("#6aa84f" if i % 2 else "#5d9a44"))
            p.drawRect(QRectF(x, base, s, s * 0.35))
            p.setBrush(QColor("#7a5a3a" if i % 2 else "#6e5034"))
            p.drawRect(QRectF(x, base + s * 0.35, s, s * 0.65))
        _oak(p, cx, base, s, ["#7fd45a", "#7fd45a", theme.DANGER, "#7fd45a", "#7fd45a", "#7fd45a"], self._t)


# ── the inspector's empty state ──────────────────────────────────────────────
class LeafGuide(QWidget):
    """the 3 leaf layers under the top (5 × 5, seen from above), their 4 corners each: leaf,
    no leaf or unknown — what the tree's form asks for"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(176)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("For each tree, enter what you SAW: its type, the logs of its trunk and the\n"
                        "12 corner leaves (the corners of the 3 leaf layers under the top).\n"
                        "A corner you can't see stays unknown.")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        s = 12.0
        states = [["leaf", "none", "leaf", "unknown"], ["leaf", "leaf", "none", "leaf"],
                  ["unknown", "leaf", "leaf", "none"]]
        col = {"leaf": QColor("#7fd45a"), "none": None, "unknown": QColor(theme.DANGER)}
        lw = 5 * s
        gap = 16.0
        x0 = (w - 3 * lw - 2 * gap) / 2.0
        for li in range(3):
            ox, oy = x0 + li * (lw + gap), 22.0
            for i in range(5):
                for j in range(5):
                    corner = i in (0, 4) and j in (0, 4)
                    r = QRectF(ox + i * s, oy + j * s, s - 1, s - 1)
                    if corner:
                        st = states[li][(0 if i == 0 else 1) + (0 if j == 0 else 2)]
                        c = col[st]
                        p.setPen(QPen(QColor(theme.TEXT2), 1.2) if c is None else Qt.PenStyle.NoPen)
                        p.setBrush(Qt.BrushStyle.NoBrush if c is None else c)
                        p.drawRoundedRect(r, 1.5, 1.5)
                    else:
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor(LEAF_D if (i + j) % 2 else LEAF) if not (i == 2 and j == 2)
                                   else QColor(LOG))
                        p.drawRect(r)
            p.setPen(QColor(theme.TEXT3))
            p.setFont(theme.ui_font(7.6))
            p.drawText(QRectF(ox - 6, oy + lw + 6, lw + 12, 14), Qt.AlignmentFlag.AlignCenter,
                       f"layer {li + 1}")
        # legend
        p.setFont(theme.ui_font(8.0))
        items = [("leaf", "leaf"), ("none", "no leaf"), ("unknown", "unknown")]
        fm = p.fontMetrics()
        tw = sum(fm.horizontalAdvance(t) + 26 for _k, t in items)
        x = (w - tw) / 2.0
        y = 22.0 + lw + 34
        for k, t in items:
            c = col[k]
            p.setPen(QPen(QColor(theme.TEXT2), 1.2) if c is None else Qt.PenStyle.NoPen)
            p.setBrush(Qt.BrushStyle.NoBrush if c is None else c)
            p.drawRoundedRect(QRectF(x, y - 9, 10, 10), 2, 2)
            p.setPen(QColor(theme.TEXT2))
            p.drawText(QPointF(x + 15, y), t)
            x += fm.horizontalAdvance(t) + 26
