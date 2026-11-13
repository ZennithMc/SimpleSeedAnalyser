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
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
                               QVBoxLayout)

from . import icons, struct_icons, theme


def _short_tp(cmd: str) -> str:
    """the coordinates of a /tp (the card shows them; a click copies the whole command)"""
    import re
    m = re.search(r"tp @s (\S+) (\S+) (\S+)\s*$", cmd)
    if m:
        return "  ".join(m.groups())
    m = re.search(r"tp @s (\S+) (\S+)\s*$", cmd)
    return "  ".join(m.groups()) if m else cmd


class _TpCard(QFrame):
    """One /tp: its purpose, its coordinates; click = copy the command."""

    def __init__(self, label, cmd, parent=None):
        super().__init__(parent)
        self.cmd, self.label = cmd, label
        self.setObjectName("TpCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(cmd + "\n\nClick to copy")
        h = QHBoxLayout(self); h.setContentsMargins(12, 7, 10, 7); h.setSpacing(10)
        self.t = QLabel(label); self.t.setObjectName("TpLabel")
        self.c = QLabel(_short_tp(cmd)); self.c.setObjectName("TpCmd"); self.c.setFont(theme.mono_font(9.5))
        self.ic = QLabel(); self.ic.setPixmap(icons.pixmap("copy", 14, theme.TEXT3))
        h.addWidget(self.t, 1); h.addWidget(self.c); h.addWidget(self.ic)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            QGuiApplication.clipboard().setText(self.cmd)
            self.t.setText("Copied")
            self.ic.setPixmap(icons.pixmap("check", 14, "#3fd6a0"))
            self.setProperty("copied", True); self.style().unpolish(self); self.style().polish(self)
            QTimer.singleShot(1300, self._reset)
        e.accept()

    def _reset(self):
        try:
            self.t.setText(self.label)
            self.ic.setPixmap(icons.pixmap("copy", 14, theme.TEXT3))
            self.setProperty("copied", False); self.style().unpolish(self); self.style().polish(self)
        except RuntimeError:                       # deleted meanwhile
            pass


class _Fact(QFrame):
    """a small tile: the label above, the value below"""

    def __init__(self, key, val):
        super().__init__()
        self.setObjectName("BubFact")
        v = QVBoxLayout(self); v.setContentsMargins(10, 6, 10, 7); v.setSpacing(1)
        a = QLabel(key.upper()); a.setObjectName("BubKey")
        b = QLabel(val); b.setObjectName("BubVal"); b.setWordWrap(True)
        b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(a); v.addWidget(b)


# (31/10) the map's icon keys → the loot's key (None: no chest — an igloo without
# a basement has none)
LOOT_ALIAS = {"igloo_basement": "igloo", "dungeon_ok": "dungeon"}
NO_LOOT = {"igloo"}


def loot_key(key):
    if key in NO_LOOT:
        return None
    k = LOOT_ALIAS.get(key, key)
    try:
        import structure_loot
        return k if k in structure_loot.SUPPORTED else None
    except Exception:
        return None


class StructBubble(QFrame):
    closed = Signal()
    lootRequested = Signal()            # (25/10) « Loot of the chests »
    lootTableRequested = Signal()       # (06/11) its loot tables
    floorRequested = Signal(int)        # (31/10) a mansion: roof (0) / floor 1-3 (X-ray)
    WIDTH = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StructBubble")
        acc = theme.ACCENT or "#7c9cff"
        self.setStyleSheet(
            f"#StructBubble {{ background: {theme.BG1 or '#1b1f27'}; border: 1px solid {theme.LINE2};"
            f" border-radius: 16px; }}"
            f"#StructBubble QLabel {{ background: transparent; }}"
            f"#BubIcon {{ background: {theme.BG2}; border: 1px solid {theme.LINE2}; border-radius: 12px; }}"
            f"#BubTitle {{ font-size: 13pt; font-weight: 650; }}"
            f"#BubSub {{ color: {theme.TEXT3}; font-size: 8.5pt; }}"
            f"#BubFact {{ background: {theme.BG2 or '#232833'}; border-radius: 10px; }}"
            f"#BubKey {{ color: {theme.TEXT3}; font-size: 7pt; font-weight: 600; letter-spacing: 1px; }}"
            f"#BubVal {{ font-size: 9.5pt; font-weight: 500; }}"
            f"#BubSection {{ color: {theme.TEXT3}; font-size: 7.5pt; font-weight: 600;"
            f" letter-spacing: 1px; }}"
            f"#BubLine {{ background: {theme.LINE2}; }}"
            f"#TpCard {{ background: {theme.BG2 or '#232833'}; border: 1px solid transparent;"
            f" border-radius: 10px; }}"
            f"#TpCard:hover {{ border-color: {acc}; }}"
            f"#TpCard[copied=\"true\"] {{ border-color: #3fd6a0; }}"
            f"#TpLabel {{ color: {theme.TEXT2}; font-size: 9pt; font-weight: 600; }}"
            f"#TpCmd {{ color: {theme.TEXT}; }}"
            f"#BubLoot {{ background: {acc}; color: white; border: none; border-radius: 10px;"
            f" padding: 8px 12px; font-weight: 600; font-size: 9.5pt; }}"
            f"#BubLoot:hover {{ background: {theme.ACCENT_H or acc}; }}"
            f"#BubTable {{ background: {theme.BG2}; color: {theme.TEXT}; border: 1px solid {theme.LINE2};"
            f" border-radius: 10px; padding: 8px 12px; font-size: 9.5pt; }}"
            f"#BubTable:hover {{ border-color: {acc}; }}"
            f"#BubFloor {{ background: {theme.BG2}; border: 1px solid {theme.LINE2}; border-radius: 9px;"
            f" padding: 5px 10px; font-size: 9pt; color: {theme.TEXT2}; }}"
            f"#BubFloor:checked {{ border-color: {acc}; color: {acc}; font-weight: 600; }}"
            f"#StructBubble QToolButton#BubClose {{ border: none; font-size: 10pt; color: {theme.TEXT3}; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(self); v.setContentsMargins(16, 14, 12, 16); v.setSpacing(12)
        top = QHBoxLayout(); top.setSpacing(12)
        self.ic = QLabel(); self.ic.setObjectName("BubIcon"); self.ic.setFixedSize(46, 46)
        self.ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.ic, 0, Qt.AlignmentFlag.AlignTop)
        tc = QVBoxLayout(); tc.setSpacing(1)
        self.title = QLabel(); self.title.setObjectName("BubTitle")
        self.sub = QLabel(); self.sub.setObjectName("BubSub")
        self.sub.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tc.addWidget(self.title); tc.addWidget(self.sub)
        top.addLayout(tc, 1)
        x = QToolButton(); x.setText("✕"); x.setToolTip("Close  (Esc)"); x.setObjectName("BubClose")
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

    def show_for(self, key, rows, tps, loading=False, subtitle="", floor=None, floor_labels=None):
        # hidden while rebuilt: children added to a VISIBLE widget stay hidden
        # (and out of the size hint) until shown one by one
        self.hide()
        self.setMinimumSize(0, 0); self.setMaximumSize(16777215, 16777215)
        self.ic.setPixmap(struct_icons.pixmap(key, 30))
        self.title.setText(struct_icons.label(key))
        self.sub.setText(subtitle)
        self._clear(self.body)
        if rows or loading:
            g = QGridLayout(); g.setHorizontalSpacing(6); g.setVerticalSpacing(6)
            g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
            r = c = 0
            isw = lambda a, b: len(str(b)) > 22 or len(str(a)) > 16
            rows = [x for x in rows if not isw(*x)] + [x for x in rows if isw(*x)]   # pairs first
            for a, b in rows:
                wide = len(str(b)) > 22 or len(str(a)) > 16
                if wide and c == 1:
                    r, c = r + 1, 0
                g.addWidget(_Fact(a, b), r, c, 1, 2 if wide else 1)
                if wide:
                    r, c = r + 1, 0
                else:
                    c += 1
                    if c == 2:
                        r, c = r + 1, 0
            if loading:
                w = QLabel("Loading…"); w.setObjectName("BubSub")
                g.addWidget(w, r + (1 if c else 0), 0, 1, 2)
            self.body.addLayout(g)
        if floor is not None:                      # (31/10) a mansion: what the map shows
            row = QHBoxLayout(); row.setSpacing(6)
            for f, lab, tip in (floor_labels or
                                ((0, "Roof", "The mansion as seen from the sky"),
                                 (1, "Floor 1", "X-ray: the ground floor without its ceiling"),
                                 (2, "Floor 2", "X-ray: the first floor without its ceiling"),
                                 (3, "Floor 3", "X-ray: the tower's top floor"))):
                b = QToolButton(); b.setText(lab); b.setToolTip(tip)
                b.setObjectName("BubFloor"); b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setCheckable(True); b.setChecked(f == floor)
                b.clicked.connect(lambda _c=False, f=f: self.floorRequested.emit(f))
                row.addWidget(b)
            row.addStretch(1)
            self.body.addLayout(row)
        has_loot = loot_key(key) is not None       # (25/10) the chests' loot
        if has_loot:
            row = QHBoxLayout(); row.setSpacing(8)
            lb = QPushButton("  Open the chests"); lb.setObjectName("BubLoot")
            lb.setCursor(Qt.CursorShape.PointingHandCursor)
            try:
                from .mc_widgets import container_icon
                img = container_icon("minecart" if key == "mineshaft" else "chest", 40, None)
                if img is not None:
                    from PySide6.QtGui import QIcon, QPixmap
                    pm = QPixmap.fromImage(img); pm.setDevicePixelRatio(2.0)
                    lb.setIcon(QIcon(pm))
                    from PySide6.QtCore import QSize
                    lb.setIconSize(QSize(20, 20))
            except Exception:                      # noqa: BLE001
                pass
            lb.clicked.connect(self.lootRequested.emit)
            row.addWidget(lb, 1)
            tb = QPushButton("  Loot table"); tb.setObjectName("BubTable")
            icons.bind(tb, "legend", 16)
            tb.setCursor(Qt.CursorShape.PointingHandCursor)
            tb.setToolTip("Every item its chests can hold, with its chance")
            tb.clicked.connect(self.lootTableRequested.emit)
            row.addWidget(tb)
            self.body.addLayout(row)
        if tps:
            self._section("Teleport")
            for lab, cmd in tps:
                self.body.addWidget(_TpCard(lab, cmd))
        self.setFixedWidth(self.WIDTH)
        self.show(); self.raise_()
        self.layout().invalidate(); self.layout().activate()
        self.setFixedHeight(self.layout().totalHeightForWidth(self.WIDTH)
                            if self.layout().hasHeightForWidth() else self.layout().sizeHint().height())
