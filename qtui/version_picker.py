"""
qtui/version_picker.py — (14/11) the Minecraft version chosen on one panel:
every exact version at once, a line per major version, and the versions that
generate the same world held together in one block.

    pk = VersionPicker(rows, current, tips, parent)
        rows     [(major, [(exact, world name)])]   e.g. ("1.21", [("1.21.2", "Java 1.21.3"), …])
        current  the exact version in use ("1.21.8")
        tips     {world name: "1.21.2 → 1.21.4"}
    pk.picked(exact, world name)
    pk.popup_under(button)

It only replaces the nested menus: the choice goes through the same function.
The former menu stays on a right click of the version button.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import motion, theme

PAD, HEAD, ROW, LABEL = 16, 34, 36, 50
CW, CH, GAP, GROUP = 56, 26, 3, 9


class VersionPicker(QWidget):
    picked = Signal(str, str)
    closed_at = 0.0                              # (class) when a panel last closed: a click on its
                                                 # button that closed it does not open it again

    def __init__(self, rows, current, tips=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.current, self.tips = current, tips or {}
        self.labels, self.groups, self.chips = [], [], []
        self.hot = None
        y, right = PAD + HEAD, 0.0
        for major, items in rows:
            self.labels.append((QRectF(PAD, y, LABEL, ROW), major))
            x, prev, g0 = PAD + LABEL, None, None
            for exact, name in items:
                if name != prev:
                    if g0 is not None:
                        self.groups.append((QRectF(g0 - 3, y + 2, x - GAP - g0 + 6, ROW - 4), prev))
                        x += GROUP
                    g0, prev = x, name
                self.chips.append((QRectF(x, y + (ROW - CH) / 2, CW, CH), exact, name))
                x += CW + GAP
            if g0 is not None:
                self.groups.append((QRectF(g0 - 3, y + 2, x - GAP - g0 + 6, ROW - 4), prev))
            right = max(right, x - GAP + 3)
            y += ROW
        self.foot = QRectF(PAD, y + 6, right - PAD, 18)
        self.setFixedSize(int(right + PAD), int(y + 6 + 18 + PAD - 4))

    # ── showing ──────────────────────────────────────────────────────────────
    def popup_under(self, button):
        p = button.mapToGlobal(QPoint(0, button.height() + 6))
        scr = button.screen().availableGeometry() if button.screen() else None
        x = p.x() + button.width() - self.width()            # right edges aligned
        if scr is not None:
            x = max(scr.left() + 8, min(x, scr.right() - self.width() - 8))
        self.move(x, p.y())
        fade = motion.enabled() and QGuiApplication.platformName() in ("windows", "xcb", "cocoa")
        if fade:
            self.setWindowOpacity(0.0)
        self.show()
        if fade:
            motion.run(self, 130, self.setWindowOpacity, lambda: self.setWindowOpacity(1.0))

    def closeEvent(self, e):
        VersionPicker.closed_at = time.monotonic()
        super().closeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)

    # ── the hand ─────────────────────────────────────────────────────────────
    def _at(self, pos):
        for i, (r, _ex, _n) in enumerate(self.chips):
            if r.adjusted(-1.5, -3, 1.5, 3).contains(pos):
                return i
        return None

    def mouseMoveEvent(self, e):
        h = self._at(e.position())
        if h != self.hot:
            self.hot = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h is not None else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self.hot is not None:
            self.hot = None
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        h = self._at(e.position())
        if h is not None:
            _r, exact, name = self.chips[h]
            self.picked.emit(exact, name)
            self.close()

    # ── the picture ──────────────────────────────────────────────────────────
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        panel = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(theme.BG1))
        p.setPen(QPen(QColor(theme.LINE2), 1))
        p.drawRoundedRect(panel, 14, 14)
        f = QFont(theme._pick(theme.DISPLAY_FAMILIES))
        f.setPointSizeF(11.5)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(PAD, PAD - 4, 300, HEAD - 6), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "Minecraft version")
        hot_name = self.chips[self.hot][2] if self.hot is not None else None
        cur_name = next((n for _r, ex, n in self.chips if ex == self.current), None)
        acc = QColor(theme.ACCENT)
        for r, name in self.groups:              # the versions of one world, together
            fill = QColor(theme.TEXT)
            fill.setAlphaF(0.085 if name == hot_name else 0.045)
            line = QColor(theme.LINE2 if name == hot_name else theme.LINE)
            if name == cur_name:
                line = QColor(acc)
                line.setAlphaF(0.55)
            p.setBrush(fill)
            p.setPen(QPen(line, 1))
            p.drawRoundedRect(r, 10, 10)
        p.setFont(theme.num_font(10.5, QFont.Weight.Bold))
        for r, major in self.labels:
            on = any(ex == self.current and rr.top() >= r.top() and rr.bottom() <= r.bottom()
                     for rr, ex, _n in self.chips)
            p.setPen(QColor(theme.TEXT if on else theme.TEXT3))
            p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, major)
        p.setFont(theme.num_font(8.6, QFont.Weight.DemiBold))
        for i, (r, exact, _name) in enumerate(self.chips):
            if exact == self.current:
                g = QLinearGradient(r.topLeft(), r.bottomRight())
                g.setColorAt(0, acc)
                g.setColorAt(1, QColor(theme.ACCENT2 or theme.ACCENT))
                p.setBrush(g)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(r, 8, 8)
                p.setPen(QColor(theme.ON_ACCENT))
            elif i == self.hot:
                hv = QColor(theme.TEXT)
                hv.setAlphaF(0.13)
                p.setBrush(hv)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(r, 8, 8)
                p.setPen(QColor(theme.TEXT))
            else:
                p.setPen(QColor(theme.TEXT2))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, exact)
        f2 = theme.ui_font(8.0)
        p.setFont(f2)
        p.setPen(QColor(theme.TEXT3))
        if hot_name is not None and self.tips.get(hot_name):
            txt = f"{self.tips[hot_name]} — the same world"
        else:
            txt = "Versions in the same block generate the same world."
        p.drawText(self.foot, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
        dot = QPointF(self.foot.right() - 4, self.foot.center().y())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(acc)
        p.drawEllipse(dot, 3, 3)
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(self.foot.left(), self.foot.top(), self.foot.width() - 12, self.foot.height()),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"in use: {self.current}")
