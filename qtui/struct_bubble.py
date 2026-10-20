"""
qtui/struct_bubble.py — the bubble of the structure clicked on the map (18/10):
a header (icon, name, dimension + position), the facts in an airy two-column
list, then the precise /tp commands as cards: a click on a card copies its
command. The details come from struct_info.details (helper process); until
then « Loading details… » is shown.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QToolButton,
                               QVBoxLayout)

from . import struct_icons, theme


class _TpCard(QFrame):
    """One /tp: its purpose above, the command below; click = copy."""

    def __init__(self, label, cmd, parent=None):
        super().__init__(parent)
        self.cmd, self.label = cmd, label
        self.setObjectName("TpCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to copy")
        v = QVBoxLayout(self); v.setContentsMargins(10, 6, 10, 7); v.setSpacing(2)
        self.t = QLabel(label); self.t.setObjectName("TpLabel")
        self.c = QLabel(cmd); self.c.setObjectName("TpCmd"); self.c.setFont(theme.mono_font(9))
        self.c.setWordWrap(True)
        v.addWidget(self.t); v.addWidget(self.c)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            QGuiApplication.clipboard().setText(self.cmd)
            self.t.setText("✓ Copied to the clipboard")
            self.setProperty("copied", True); self.style().unpolish(self); self.style().polish(self)
            QTimer.singleShot(1300, self._reset)
        e.accept()

    def _reset(self):
        try:
            self.t.setText(self.label)
            self.setProperty("copied", False); self.style().unpolish(self); self.style().polish(self)
        except RuntimeError:                       # deleted meanwhile
            pass


class StructBubble(QFrame):
    closed = Signal()
    WIDTH = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StructBubble")
        acc = theme.ACCENT or "#7c9cff"
        self.setStyleSheet(
            f"#StructBubble {{ background: {theme.BG1 or '#1b1f27'}; border: 1px solid {theme.LINE2};"
            f" border-radius: 12px; }}"
            f"#StructBubble QLabel {{ background: transparent; }}"
            f"#BubTitle {{ font-size: 11pt; font-weight: 600; }}"
            f"#BubSub {{ color: {theme.TEXT3}; font-size: 8.5pt; }}"
            f"#BubKey {{ color: {theme.TEXT3}; font-size: 8.5pt; }}"
            f"#BubVal {{ font-size: 9pt; }}"
            f"#BubSection {{ color: {theme.TEXT3}; font-size: 7.5pt; font-weight: 600;"
            f" letter-spacing: 1px; }}"
            f"#BubLine {{ background: {theme.LINE2}; }}"
            f"#TpCard {{ background: {theme.BG2 or '#232833'}; border: 1px solid {theme.LINE2};"
            f" border-radius: 8px; }}"
            f"#TpCard:hover {{ border-color: {acc}; }}"
            f"#TpCard[copied=\"true\"] {{ border-color: #3fd6a0; }}"
            f"#TpLabel {{ color: {theme.TEXT2}; font-size: 8.5pt; font-weight: 600; }}"
            f"#TpCmd {{ color: {theme.TEXT}; }}"
            f"#StructBubble QToolButton {{ border: none; font-size: 10pt; color: {theme.TEXT3}; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 12, 14); v.setSpacing(10)
        top = QHBoxLayout(); top.setSpacing(10)
        self.ic = QLabel(); top.addWidget(self.ic, 0, Qt.AlignmentFlag.AlignTop)
        tc = QVBoxLayout(); tc.setSpacing(1)
        self.title = QLabel(); self.title.setObjectName("BubTitle")
        self.sub = QLabel(); self.sub.setObjectName("BubSub")
        self.sub.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tc.addWidget(self.title); tc.addWidget(self.sub)
        top.addLayout(tc, 1)
        x = QToolButton(); x.setText("✕"); x.setToolTip("Close  (Esc)")
        x.setCursor(Qt.CursorShape.PointingHandCursor)
        x.clicked.connect(self._close)
        top.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)
        self.body = QVBoxLayout(); self.body.setSpacing(10)
        v.addLayout(self.body)
        self.hide()

    def _close(self):
        self.hide()
        self.closed.emit()

    @staticmethod
    def _clear(lay):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            elif it.layout() is not None:
                StructBubble._clear(it.layout())

    def _section(self, text):
        lab = QLabel(text.upper()); lab.setObjectName("BubSection")
        self.body.addWidget(lab)

    def show_for(self, key, rows, tps, loading=False, subtitle=""):
        # hidden while rebuilt: children added to a VISIBLE widget stay hidden
        # (and out of the size hint) until shown one by one
        self.hide()
        self.setMinimumSize(0, 0); self.setMaximumSize(16777215, 16777215)
        self.ic.setPixmap(struct_icons.pixmap(key, 30))
        self.title.setText(struct_icons.label(key))
        self.sub.setText(subtitle)
        self._clear(self.body)
        if rows or loading:
            self._section("Details")
            g = QGridLayout(); g.setHorizontalSpacing(16); g.setVerticalSpacing(5)
            g.setColumnStretch(1, 1)
            for i, (a, b) in enumerate(rows):
                la = QLabel(a); la.setObjectName("BubKey")
                lb = QLabel(b); lb.setObjectName("BubVal"); lb.setWordWrap(True)
                lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                g.addWidget(la, i, 0, Qt.AlignmentFlag.AlignTop); g.addWidget(lb, i, 1)
            if loading:
                w = QLabel("Loading details…"); w.setObjectName("BubKey")
                g.addWidget(w, len(rows), 0, 1, 2)
            self.body.addLayout(g)
        if tps:
            self._section("Teleport  ·  click to copy")
            for lab, cmd in tps:
                self.body.addWidget(_TpCard(lab, cmd))
        self.setFixedWidth(self.WIDTH)
        self.show(); self.raise_()
        self.layout().invalidate(); self.layout().activate()
        self.setFixedHeight(self.layout().totalHeightForWidth(self.WIDTH)
                            if self.layout().hasHeightForWidth() else self.layout().sizeHint().height())
