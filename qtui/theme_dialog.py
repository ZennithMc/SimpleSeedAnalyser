"""
qtui/theme_dialog.py — Appearance: a gallery of themes (plain and textured:
marble, aurora, nebula, Minecraft stones…) and the colour editor of the two
custom themes.

Clicking a card previews the theme live on the whole application; Apply keeps
it, Cancel goes back. The textured backdrops are computed in the background
the first time the dialog opens (then cached), so switching is instant.
Midnight and Daylight stay editable: every colour of the design system can be
changed and is saved in themes.json.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QApplication, QColorDialog, QDialog, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import theme
from .widgets import body, section_title

CARD_W, CARD_H = 196, 150
THUMB_H = 104


class _Bus(QObject):
    thumb = Signal(str)


class ThemeCard(QAbstractButton):
    """Miniature of the theme: its backdrop, a panel with text, the accent."""

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self.setCheckable(True)
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumb = None
        self.setToolTip(f"{name} — {theme.EXTRAS.get(name, {}).get('desc', '')}")

    def sizeHint(self):
        return QSize(CARD_W, CARD_H)

    def set_thumb(self, path):
        pm = QPixmap(path)
        if not pm.isNull():
            self.thumb = pm.scaled(CARD_W * 2, THUMB_H * 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            self.update()

    def paintEvent(self, _e):
        t = theme.theme_tokens(self.name)
        ex = theme.EXTRAS.get(self.name, {})
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        clip = QPainterPath(); clip.addRoundedRect(r, 10, 10)
        p.setClipPath(clip)
        # backdrop (thumbnail) or the theme's gradient
        top = QRectF(r.left(), r.top(), r.width(), THUMB_H)
        if self.thumb is not None:
            p.drawPixmap(top, self.thumb, QRectF(self.thumb.rect()))
        else:
            g = QLinearGradient(top.topLeft(), top.bottomRight())
            g.setColorAt(0, QColor(t["base_top"])); g.setColorAt(1, QColor(t["base_bottom"]))
            p.fillRect(top, g)
        # a mini panel with two text lines and the accent
        panel = QRectF(top.left() + 14, top.top() + 16, top.width() - 28, top.height() - 32)
        pc = QColor(t["bg1"]); pc.setAlphaF(ex.get("panel", 1.0))
        p.setPen(QPen(QColor(t["line2"]), 1)); p.setBrush(pc)
        p.drawRoundedRect(panel, 6, 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["text"])); p.drawRoundedRect(QRectF(panel.left() + 10, panel.top() + 12, 70, 6), 3, 3)
        p.setBrush(QColor(t["text3"])); p.drawRoundedRect(QRectF(panel.left() + 10, panel.top() + 24, 46, 5), 2.5, 2.5)
        acc = QRectF(panel.left() + 10, panel.bottom() - 22, 58, 14)
        g = QLinearGradient(acc.topLeft(), acc.bottomRight())
        g.setColorAt(0, QColor(t["accent"])); g.setColorAt(1, QColor(ex.get("accent2") or t["accent"]))
        p.setBrush(g); p.drawRoundedRect(acc, 7, 7)
        for i, k in enumerate(("ok", "warn", "danger")):
            p.setBrush(QColor(t[k]))
            p.drawEllipse(QRectF(panel.right() - 16 - i * 12, panel.bottom() - 19, 8, 8))
        # caption
        cap = QRectF(r.left(), top.bottom(), r.width(), r.height() - THUMB_H)
        p.fillRect(cap, QColor(theme.BG2))
        p.setPen(QColor(theme.TEXT))
        p.setFont(theme.ui_font(9.5, QFont.Weight.DemiBold))
        p.drawText(cap.adjusted(10, 5, -8, -20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.name)
        p.setPen(QColor(theme.TEXT3)); p.setFont(theme.ui_font(8))
        d = p.fontMetrics().elidedText(ex.get("desc", ""), Qt.TextElideMode.ElideRight, int(cap.width() - 18))
        p.drawText(cap.adjusted(10, 22, -8, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, d)
        p.setClipping(False)
        # frame: accent when selected
        if self.isChecked():
            p.setPen(QPen(QColor(theme.ACCENT), 2.4))
        elif self.underMouse():
            p.setPen(QPen(QColor(theme.HOVER_LINE), 1.4))
        else:
            p.setPen(QPen(QColor(theme.LINE2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 10, 10)
        if self.isChecked():
            b = QRectF(r.right() - 26, r.top() + 8, 18, 18)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.ACCENT)); p.drawEllipse(b)
            p.setPen(QPen(QColor("white"), 2))
            path = QPainterPath()
            path.moveTo(b.left() + 5, b.top() + 9.5); path.lineTo(b.left() + 8, b.top() + 12.5)
            path.lineTo(b.left() + 13.5, b.top() + 6)
            p.drawPath(path)

    def enterEvent(self, e):
        self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self.update(); super().leaveEvent(e)


class ThemeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Appearance")
        self.resize(900, 760)
        self.app = QApplication.instance()
        self.original = (theme.THEME, dict(theme.TOKENS))
        self.edits: dict = {}                    # name -> unsaved tokens
        self.current = theme.THEME

        v = QVBoxLayout(self); v.setContentsMargins(22, 18, 22, 16); v.setSpacing(10)
        t = QLabel("Appearance"); t.setObjectName("PageTitle"); v.addWidget(t)
        v.addWidget(body("Click a theme to preview it on the whole application — Apply keeps it.",
                         muted=True))
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget(); iv = QVBoxLayout(inner); iv.setContentsMargins(0, 4, 8, 4); iv.setSpacing(10)

        self.cards = {}
        groups = (("Plain & metal", ["Slate", "Midnight", "Daylight", "Graphite", "Copper"]),
                  ("Marble", ["Obsidian Gold", "Carrara", "Emerald", "Lapis", "Rose Quartz",
                              "Arctic"]),
                  ("Minecraft", ["Deepslate", "Nether", "The End", "Mesa", "Amethyst",
                                 "Prismarine", "Jungle"]),
                  ("Sky & nature", ["Aurora", "Nebula", "Sunset Dune", "Abyss", "Sakura", "Ember"]))
        listed = {n for _g, ns in groups for n in ns}
        extra = [n for n in theme.BUILTIN if n not in listed]
        if extra:
            groups += (("More", extra),)
        for title, names in groups:
            iv.addWidget(section_title(title))
            g = QGridLayout(); g.setHorizontalSpacing(12); g.setVerticalSpacing(12)
            for i, n in enumerate(n for n in names if n in theme.BUILTIN):
                c = ThemeCard(n)
                c.clicked.connect(lambda _=False, n=n: self._select(n))
                self.cards[n] = c
                g.addWidget(c, i // 4, i % 4)
            g.setColumnStretch(4, 1)
            iv.addLayout(g)

        # colour editor (custom themes only)
        self.edit_box = QWidget(); ev = QVBoxLayout(self.edit_box); ev.setContentsMargins(0, 8, 0, 0)
        ev.addWidget(section_title("Colours of this custom theme"))
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(12); self.grid.setVerticalSpacing(6)
        self.swatches = {}
        for r, (key, label) in enumerate(theme.TOKEN_LABELS):
            col, row = (r % 2) * 3, r // 2
            self.grid.addWidget(body(label, wrap=False), row, col)
            hexlab = QLabel(); hexlab.setObjectName("Muted"); hexlab.setFont(theme.mono_font(9))
            sw = QPushButton(); sw.setObjectName("Swatch")
            sw.setCursor(Qt.CursorShape.PointingHandCursor)
            sw.clicked.connect(lambda _=False, k=key: self._pick(k))
            self.grid.addWidget(hexlab, row, col + 1, Qt.AlignmentFlag.AlignRight)
            self.grid.addWidget(sw, row, col + 2)
            self.swatches[key] = (sw, hexlab)
        ev.addLayout(self.grid)
        self.reset = QPushButton("Reset colours"); self.reset.setProperty("kind", "ghost")
        self.reset.clicked.connect(self._reset)
        rr = QHBoxLayout(); rr.addWidget(self.reset); rr.addStretch(1); ev.addLayout(rr)
        iv.addWidget(self.edit_box)
        iv.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)

        row = QHBoxLayout()
        self.info = body("", muted=True); row.addWidget(self.info, 1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply"); ok.setProperty("kind", "primary"); ok.clicked.connect(self.accept)
        row.addWidget(cancel); row.addWidget(ok)
        v.addLayout(row)
        self._select(theme.THEME, apply=False)

        # thumbnails / backdrop cache, computed in the background
        self._bus = _Bus(self)
        self._bus.thumb.connect(self._thumb_ready)
        self._stop = False
        threading.Thread(target=self._warm, daemon=True).start()

    def _warm(self):
        from .backdrops import backdrop_path
        order = [self.current] + [n for n in self.cards if n != self.current]
        for n in order:
            if self._stop:
                return
            spec = theme.EXTRAS.get(n, {}).get("backdrop")
            if spec:
                p = backdrop_path(spec)
                if p and not self._stop:
                    self._bus.thumb.emit(n + "\n" + p)

    def _thumb_ready(self, msg):
        n, p = msg.split("\n", 1)
        c = self.cards.get(n)
        if c is not None:
            c.set_thumb(p)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _tokens(self, name):
        return self.edits.get(name) or theme.theme_tokens(name)

    def _select(self, name, apply=True):
        self.current = name
        for n, c in self.cards.items():
            c.setChecked(n == name)
        editable = name in theme.EDITABLE
        ex = theme.EXTRAS.get(name, {})
        self.info.setText(f"{name} — {ex.get('desc', '')}"
                          + ("   ·   custom: its colours can be edited below" if editable else ""))
        self.edit_box.setVisible(editable)
        toks = self._tokens(name)
        for key, (sw, hexlab) in self.swatches.items():
            sw.setStyleSheet(f"QPushButton#Swatch {{ background: {toks[key]}; border: 1px solid {theme.LINE2}; }}")
            hexlab.setText(toks[key])
        if apply:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                theme.apply(self.app, name, toks)
            finally:
                QApplication.restoreOverrideCursor()
            for c in self.cards.values():
                c.update()

    def _pick(self, key):
        toks = dict(self._tokens(self.current))
        col = QColorDialog.getColor(QColor(toks[key]), self, f"{key}")
        if not col.isValid():
            return
        toks[key] = col.name()
        self.edits[self.current] = toks
        self._select(self.current)

    def _reset(self):
        if self.current in theme.EDITABLE:
            self.edits[self.current] = dict(theme.BUILTIN[self.current])
            self._select(self.current)

    # ── close ───────────────────────────────────────────────────────────────
    def done(self, r):
        self._stop = True
        super().done(r)

    def accept(self):
        name = self.current
        if name in theme.EDITABLE and name in self.edits:
            theme.save_user_theme(name, self.edits[name])
        theme.remember_theme(name)
        theme.apply(self.app, name)
        super().accept()

    def reject(self):
        theme.apply(self.app, *self.original)
        super().reject()
