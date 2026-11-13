"""
qtui/loot_page.py — (06/11, redesigned 07/11) the Loot workspace: find seeds whose
structure chests hold what you want (loot_finder), with the exact odds of the
query (loot_exact), and browse every loot table (LootTablesView).

  ┌ structures: 15 tiles in the game's own block textures ────────────────────────────┐
  ┌ the query ──────────────┐┌ results ─────────┐┌ the chests of the selected result ──┐
  │ structure · its chests   ││ seed · where ·   ││ the game's chest windows (/tp, map)  │
  │ what you want (slots)    ││ found            ││                                      │
  │ Find · Most · Layout     ││                  ││                                      │
  │ the chest grid (Layout)  ││                  ││                                      │
  │ within · how rare (exact)││                  ││                                      │
  └──────────────────────────┘└──────────────────┘└──────────────────────────────────────┘
  ┌ run bar — the Sessions' one: mode · start (Manual / Saved) · processes · stop after ·
  │ seed list · Start / Pause / Stop · progress                                          │
"""
from __future__ import annotations

import math
import os
import threading
import time

from PySide6.QtCore import (QEasingCurve, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTime,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QTransform)
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QComboBox, QDialog, QFileDialog,
                               QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
                               QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTimeEdit,
                               QToolButton, QVBoxLayout, QWidget)

import loot_engine as le
import loot_finder as lf
import loot_stats as ls
import mc_assets as ma

from . import icons, struct_icons, theme
from .loot_table_dialog import STRUCT_SCOPE, LootTablesView, pct, table_label
from .mc_widgets import ItemSlot, assets, container_icon, display_name, draw_stack, hide_tooltip
from .widgets import AdaptiveButton, CoordEdit, Segmented, remember_path, user_path

STRUCTS = ["desert_pyramid", "jungle_temple", "buried_treasure", "shipwreck", "ruined_portal",
           "igloo", "ocean_ruin", "pillager_outpost", "village", "mansion", "ancient_city",
           "abandoned_camp", "bastion", "fortress", "end_city"]
_ICON_KEY = {"buried_treasure": "treasure"}
_SCOPE = dict(STRUCT_SCOPE, buried_treasure=("buried_treasure",))
_SHORT = {"mansion": "Mansion", "abandoned_camp": "Camp", "bastion": "Bastion",
          "fortress": "Fortress", "pillager_outpost": "Outpost", "buried_treasure": "Treasure",
          "desert_pyramid": "Pyramid", "jungle_temple": "Jungle temple", "ruined_portal": "Ruined portal",
          "ancient_city": "Ancient city", "ocean_ruin": "Ocean ruin", "end_city": "End city"}
# each structure's tile: its building block, in the game's texture (the newest jar found)
_TEX = {
    "desert_pyramid": ("block/cut_sandstone", "#c9ae74"), "jungle_temple": ("block/mossy_cobblestone", "#5d7a45"),
    "buried_treasure": ("block/sand", "#d8c792"), "shipwreck": ("block/spruce_planks", "#6b4d2c"),
    "ruined_portal": ("block/crying_obsidian", "#3a1f5c"), "igloo": ("block/snow", "#e3ecf3"),
    "ocean_ruin": ("block/prismarine_bricks", "#3e8f86"), "pillager_outpost": ("block/dark_oak_log", "#45362a"),
    "village": ("block/oak_planks", "#a3804e"), "mansion": ("block/dark_oak_planks", "#4b3220"),
    "ancient_city": ("block/deepslate_tiles", "#33373d"), "abandoned_camp": ("block/coarse_dirt", "#6f5639"),
    "bastion": ("block/polished_blackstone_bricks", "#2c2a31"), "fortress": ("block/nether_bricks", "#4b1d22"),
    "end_city": ("block/purpur_block", "#a679a6"),
}
# how rare: the tiers of the odds card (1 in N)
TIERS = [(3, "Common", "#3fd6a0"), (30, "Uncommon", "#8fd14f"), (300, "Rare", "#4da3ff"),
         (3000, "Epic", "#b48cff"), (100000, "Legendary", "#f0b54a"), (float("inf"), "Mythic", "#ff6bb5")]
IMPOSSIBLE = "#ff6b6b"


def _tier(p):
    if p is None or p <= 0:
        return ("Impossible", IMPOSSIBLE)
    n = 1.0 / p
    for lim, name, col in TIERS:
        if n <= lim:
            return (name, col)
    return TIERS[-1][1:]


def _rgba(col, a):
    c = QColor(col)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _anim(owner, ms, cb, start=0.0, end=1.0, curve=QEasingCurve.Type.OutCubic):
    a = QVariantAnimation(owner)
    a.setDuration(ms)
    a.setStartValue(float(start)); a.setEndValue(float(end))
    a.setEasingCurve(curve)
    a.valueChanged.connect(lambda v: cb(float(v)))
    return a


def fade_in(w, ms=260):
    """a widget appears: opacity 0 → 1"""
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(0.0)
    w.setGraphicsEffect(eff)
    a = _anim(w, ms, eff.setOpacity)
    a.finished.connect(lambda: w.setGraphicsEffect(None))
    a.start()
    w._fade = a


_DECO = None


def _deco():
    """the assets used for the decoration (the newest jar: same look in every version)"""
    global _DECO
    if _DECO is None:
        try:
            _DECO = ma.newest() or False
        except Exception:                                # noqa: BLE001
            _DECO = False
    return _DECO or None


def _texture(ref):
    a = _deco()
    try:
        return a.texture(ref) if a is not None else None
    except Exception:                                    # noqa: BLE001
        return None


def _pm(img, size):
    if img is None:
        return QPixmap()
    p = QPixmap.fromImage(img)
    p.setDevicePixelRatio(img.width() / size)
    return p


def _version(mc):
    sub = None
    try:
        from .world import current_exact
        ex = current_exact()
        if ex in (le.MC_SUBVERSIONS.get(int(mc)) or []):
            sub = ex
    except Exception:                                    # noqa: BLE001
        pass
    return le.loot_version(int(mc), sub), sub


_ODDS_POOL = None
ODDS_TIMEOUT = 30.0                                     # seconds


def _odds_pool():
    """(07/11) the odds' own process (spawned once, warmed up at once)"""
    global _ODDS_POOL
    if _ODDS_POOL is None:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        _ODDS_POOL = ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"))
        _ODDS_POOL.submit(lf.odds_warm)
        import atexit
        atexit.register(_odds_kill)
    return _ODDS_POOL


def _odds_kill():
    """(12/11) stop the odds process now (a computation one no longer wants)"""
    global _ODDS_POOL
    pool, _ODDS_POOL = _ODDS_POOL, None
    if pool is None:
        return
    for pr in list((getattr(pool, "_processes", None) or {}).values()):
        try:
            pr.terminate()
        except Exception:                               # noqa: BLE001
            pass
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:                                   # noqa: BLE001
        pass


class _R:
    def __init__(self, p, max_count):
        self.p, self.max_count = p, max_count
        self.impossible = p <= 0


def _one_in(p):
    """1 / p, short: 2.4 · 4 812 · 4.81 M · 3.2 B · 1.2 × 10^15"""
    if p <= 0:
        return "∞"
    n = 1.0 / p
    if n < 10:
        return f"{n:.2f}".rstrip("0").rstrip(".")
    if n < 1e6:
        return f"{n:,.0f}".replace(",", " ")
    if n < 1e9:
        return f"{n / 1e6:.3g} M"
    if n < 1e12:
        return f"{n / 1e9:.3g} B"
    e = int(math.floor(math.log10(n)))
    sup = str(e).translate(str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻"))
    return f"{n / 10 ** e:.2g} × 10{sup}"


class _Want:
    """one wanted item (a plain dict for the workers)"""

    def __init__(self, item, mn=1, mx=None, ench=None, ench_lvl=1, potion=None):
        self.d = dict(item=item, min=mn, max=mx, ench=ench, ench_lvl=ench_lvl, potion=potion)



class _Stub:
    """a stack-like object for the icon / tooltip of a wanted item"""

    def __init__(self, d):
        self.item = d["item"]
        self.count = 1
        self.potion = d.get("potion")
        self.ench = [(d["ench"], d.get("ench_lvl") or 1)] if d.get("ench") else []
        self.enchanted = bool(d.get("ench")) or self.item == "enchanted_book"
        self.info = []
        self.name_key = None
        self.damage = None


def _enchantable(item):
    return any(item.endswith(s) for s in ("_sword", "_axe", "_pickaxe", "_shovel", "_hoe", "_helmet",
                                          "_chestplate", "_leggings", "_boots", "_spear")) or \
        item in ("bow", "crossbow", "fishing_rod", "trident")


_ENCH = {}


def _enchants(version):
    if version not in _ENCH:
        out = []
        try:
            import loot_enchant as lx
            a = assets(version) or ma.newest()
            for e in lx.env(version).order:
                nm = (a.text(f"enchantment.minecraft.{e.id}") if a else "") or e.id.replace("_", " ").title()
                out.append((e.id, nm))
        except Exception:                                # noqa: BLE001
            pass
        _ENCH[version] = sorted(out, key=lambda t: t[1])
    return _ENCH[version]


# ── the item picker ────────────────────────────────────────────────────────────
class _Picker(QDialog):
    """every item the chosen structure's chests can hold, with its chance"""

    def __init__(self, parent, version, tables):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("LpPicker")
        self.picked = None
        v = QVBoxLayout(self); v.setContentsMargins(12, 12, 12, 12); v.setSpacing(8)
        self.find = QLineEdit(); self.find.setPlaceholderText("Find an item…")
        self.find.textChanged.connect(self._fill)
        v.addWidget(self.find)
        self.area = QScrollArea(); self.area.setWidgetResizable(True); self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setFixedSize(470, 380)
        v.addWidget(self.area)
        best = {}
        for tb in tables:
            st = ls.table_stats(version, tb)
            for s in (st.items if st else []):
                k = (s.item, s.potion)
                if k not in best or s.chance > best[k].chance:
                    best[k] = s
        self.items = sorted(best.values(), key=lambda s: (-s.chance, s.item))
        self.version = version
        self._fill()
        self.find.setFocus()

    def _fill(self, *_):
        q = self.find.text().strip().lower()
        w = QWidget(); g = QGridLayout(w); g.setSpacing(6); g.setContentsMargins(0, 0, 0, 0)
        n = 0
        for s in self.items:
            nm = display_name(s, self.version)
            if q and q not in nm.lower() and q not in s.item:
                continue
            b = QToolButton(); b.setObjectName("LpPick")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            from .mc_widgets import icon as _icon
            b.setIcon(_pm(_icon(s, 64, self.version), 32)); b.setIconSize(QSize(32, 32))
            b.setText(f"{nm}\n{pct(s.chance)}")
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setFixedSize(226, 50)
            b.setToolTip(f"{nm} — {pct(s.chance)} of the chests")
            b.clicked.connect(lambda _=False, s=s: self._take(s))
            g.addWidget(b, n // 2, n % 2)
            n += 1
        g.setRowStretch(n // 2 + 1, 1)
        self.area.setWidget(w)

    def _take(self, s):
        self.picked = s
        self.accept()


# ── the structure tiles ────────────────────────────────────────────────────────
class _StructTile(QAbstractButton):
    """a structure: its building block's texture, its icon, its name; hover lifts it,
    the chosen one glows"""

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(82, 86)
        self.setMaximumHeight(86)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(struct_icons.label(_ICON_KEY.get(key, key)))
        ref, col = _TEX.get(key, ("", "#555b66"))
        img = _texture(ref) if ref else None
        self._tex = None if img is None else QPixmap.fromImage(
            img.scaled(48, 48, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation))
        self._gray = None if img is None else QPixmap.fromImage(
            img.convertToFormat(QImage.Format.Format_Grayscale8).scaled(
                48, 48, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation))
        self._col = col
        self._icon = struct_icons.pixmap(_ICON_KEY.get(key, key), 40)
        self._h = 0.0
        self._s = 0.0
        self._anims = {}
        self.toggled.connect(lambda on: self._go("_s", 1.0 if on else 0.0, 220))

    def _go(self, attr, to, ms):
        a = self._anims.get(attr)
        if a is not None:
            a.stop()

        def set_(v, attr=attr):
            setattr(self, attr, v)
            self.update()
        a = _anim(self, ms, set_, getattr(self, attr), to)
        self._anims[attr] = a
        a.start()

    def enterEvent(self, e):
        if self.isEnabled():
            self._go("_h", 1.0, 150)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._go("_h", 0.0, 220)
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h, s, on = self._h, self._s, self.isEnabled()
        lift = 3.0 * h
        r = QRectF(self.rect()).adjusted(4, 5 - lift, -4, -3 - lift)
        path = QPainterPath(); path.addRoundedRect(r, 11, 11)
        acc = QColor(theme.ACCENT or "#7c9cff")
        if s > 0:                                        # the chosen one: a soft glow
            for i in range(4, 0, -1):
                g = QPainterPath(); g.addRoundedRect(r.adjusted(-i, -i, i, i), 11 + i, 11 + i)
                p.setPen(QPen(_rgba(acc, 0.10 * s * (5 - i) / 4), 2))
                p.drawPath(g)
        if h > 0 and on:                                 # lifted: its shadow
            sh = QPainterPath(); sh.addRoundedRect(r.translated(0, 3 + lift), 11, 11)
            p.fillPath(sh, _rgba("#000000", 0.25 * h))
        p.save()
        p.setClipPath(path)
        pm = self._tex if on else self._gray
        if pm is not None:
            br = QBrush(pm)
            br.setTransform(QTransform.fromTranslate(r.x(), r.y()))
            p.fillRect(r, br)
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor(self._col).lighter(125)); g.setColorAt(1, QColor(self._col).darker(150))
            p.fillRect(r, g)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0.0, _rgba("#000000", 0.08 + 0.18 * (1 - h)))
        g.setColorAt(0.5, _rgba("#000000", 0.30 + 0.12 * (1 - h)))
        g.setColorAt(1.0, _rgba("#000000", 0.84))
        p.fillRect(r, g)
        if s > 0:
            g2 = QLinearGradient(r.topLeft(), r.bottomLeft())
            g2.setColorAt(0, _rgba(acc, 0.0)); g2.setColorAt(1, _rgba(acc, 0.35 * s))
            p.fillRect(r, g2)
        if not on:
            p.fillRect(r, _rgba("#0c0e12", 0.55))
        p.fillRect(QRectF(r.x(), r.y(), r.width(), 1.5), _rgba("#ffffff", 0.16 + 0.12 * h))
        p.restore()
        if s > 0.01:
            p.setPen(QPen(_mix(QColor(0, 0, 0, 140), acc, s), 2.0))
        elif h > 0.01 and on:
            p.setPen(QPen(_rgba("#ffffff", 0.18 + 0.35 * h), 1.2))
        else:
            p.setPen(QPen(_rgba("#000000", 0.55), 1.0))
        p.drawPath(path)
        sz = 34.0 * (1.0 + 0.10 * h + 0.06 * s)
        p.setOpacity(1.0 if on else 0.45)
        p.drawPixmap(QRectF(r.center().x() - sz / 2, r.y() + 9 + (34 - sz) / 2, sz, sz), self._icon,
                     QRectF(self._icon.rect()))
        p.setOpacity(1.0)
        f = QFont(theme.ui_font(8.4)); f.setBold(True)
        p.setFont(f)
        tr = QRectF(r.x() + 3, r.bottom() - 26, r.width() - 6, 22)
        txt = QFontMetricsF(f).elidedText(_SHORT.get(self.key) or self.toolTip(),
                                          Qt.TextElideMode.ElideRight, tr.width())
        p.setPen(_rgba("#000000", 0.85))
        p.drawText(tr.translated(1, 1), Qt.AlignmentFlag.AlignCenter, txt)
        p.setPen(QColor("#ffffff") if on else QColor(theme.TEXT3 or "#888"))
        p.drawText(tr, Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


# ── a wanted item ──────────────────────────────────────────────────────────────
class _Badge(QWidget):
    """the item's letter (as on the Layout grid)"""

    def __init__(self, letter):
        super().__init__()
        self.letter = letter
        self.setFixedSize(22, 22)
        self.setToolTip("Its letter on the Layout grid")

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        acc = QColor(theme.ACCENT or "#7c9cff")
        g = QLinearGradient(0, 0, 0, 22)
        g.setColorAt(0, acc.lighter(125)); g.setColorAt(1, acc.darker(120))
        p.setBrush(g); p.setPen(QPen(_rgba("#000000", 0.4), 1))
        p.drawEllipse(QRectF(1, 1, 20, 20))
        f = QFont(theme.ui_font(8.5)); f.setBold(True); p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(QRectF(0, 0, 22, 22), Qt.AlignmentFlag.AlignCenter, self.letter)
        p.end()


class _Elide(QLabel):
    """one line, an ellipsis when it does not fit (the full text in the tooltip)"""

    def __init__(self, text):
        super().__init__(text)
        self.full = text
        self.setToolTip(text)
        self.setMinimumWidth(30)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().windowText().color())
        t = self.fontMetrics().elidedText(self.full, Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), t)
        p.end()


class _WantCard(QFrame):
    changed = Signal()
    removed = Signal(object)

    def __init__(self, want: _Want, version, letter):
        super().__init__()
        self.want, self.version = want, version
        self.setObjectName("LpWant")
        self.setFixedHeight(74)
        h = QHBoxLayout(self); h.setContentsMargins(8, 6, 4, 6); h.setSpacing(8)
        bcol = QVBoxLayout(); bcol.setContentsMargins(0, 0, 0, 0)
        bcol.addWidget(_Badge(letter)); bcol.addStretch(1)
        h.addLayout(bcol)
        self.slot = ItemSlot(_Stub(want.d), version, k=3)
        h.addWidget(self.slot, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout(); col.setSpacing(3); col.setContentsMargins(0, 2, 0, 0)
        nm = _Elide(display_name(_Stub(want.d), version)); nm.setObjectName("LpWantName")
        col.addWidget(nm)
        row = QHBoxLayout(); row.setSpacing(4)
        lab = QLabel("≥"); lab.setObjectName("LpMuted"); row.addWidget(lab)
        self.mn_lab = lab
        self.mn = QSpinBox(); self.mn.setRange(0, 999); self.mn.setValue(int(want.d["min"] or 0))
        self.mn.setFixedWidth(58)
        self.mn.setToolTip("At least this many (in one chest, or in the whole structure)")
        self.mn.valueChanged.connect(self._set)
        row.addWidget(self.mn)
        self.ench = None
        if want.d["item"] == "enchanted_book" or _enchantable(want.d["item"]):
            self.ench = QComboBox()
            self.ench.addItem("any enchantment", None)
            for eid, nmx in _enchants(version):
                self.ench.addItem(nmx, eid)
            if want.d.get("ench"):
                i = self.ench.findData(want.d["ench"])
                self.ench.setCurrentIndex(max(0, i))
            self.ench.setMinimumWidth(60)
            self.ench.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self.ench.currentIndexChanged.connect(self._set)
            row.addWidget(self.ench, 1)
        else:
            row.addStretch(1)
        col.addLayout(row)
        h.addLayout(col, 1)
        x = QToolButton(); x.setObjectName("LpX"); x.setToolTip("Remove")
        icons.bind(x, "close", 14)
        x.clicked.connect(lambda: self.removed.emit(self))
        h.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)

    def set_layout(self, on):
        """Layout: the counts are set on the grid's cells"""
        self.mn_lab.setVisible(not on)
        self.mn.setVisible(not on)

    def _set(self, *_):
        self.want.d["min"] = self.mn.value()
        if self.ench is not None:
            self.want.d["ench"] = self.ench.currentData()
        self.changed.emit()


class _AddCard(QPushButton):
    def __init__(self):
        super().__init__("  Add an item")
        self.setObjectName("LpAdd")
        self.setFixedHeight(74)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.bind(self, "plus", 18)


# ── the Layout grid ────────────────────────────────────────────────────────────
class _LayoutGrid(QWidget):
    """the chest's 27 slots (the game's window, ×3): click = any → wanted item A, B…
    → something → empty; wheel on an item = exactly that many; right click = any"""
    changed = Signal()
    K = 3

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.cells = [None] * 27
        k = self.K
        self.setFixedSize(176 * k, 78 * k)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = None
        self._pop = {}                                   # cell → animation value
        self.setToolTip("Click a slot: any → a wanted item (A, B…) → something → empty.\n"
                        "Wheel on an item: how many exactly (none: any count).\n"
                        "Right-click: back to any.")

    def _origin(self):
        return 8, 18                                     # the first slot's interior (game px)

    def _cell_at(self, pos):
        k = self.K
        ox, oy = self._origin()
        x, y = pos.x() / k - ox, pos.y() / k - oy
        c, r = int(x // 18), int(y // 18)
        return r * 9 + c if (0 <= c < 9 and 0 <= r < 3 and x >= 0 and y >= 0) else None

    @staticmethod
    def _base(c):
        return c[0] if isinstance(c, (list, tuple)) else c

    def drop_want(self, i):
        out = []
        for c in self.cells:
            b = self._base(c)
            if isinstance(b, int):
                if b == i:
                    c = None
                elif b > i:
                    c = [b - 1, c[1]] if isinstance(c, (list, tuple)) else b - 1
            out.append(c)
        self.cells = out
        self.update()

    def clear(self):
        self.cells = [None] * 27
        self.update()
        self.changed.emit()

    def _poke(self, i):
        a = _anim(self, 260, lambda v, i=i: (self._pop.__setitem__(i, v), self.update()), 0.0, 1.0,
                  QEasingCurve.Type.OutBack)
        a.finished.connect(lambda i=i: (self._pop.pop(i, None), self.update()))
        a.start()

    def mouseMoveEvent(self, e):
        i = self._cell_at(e.position())
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        i = self._cell_at(e.position())
        if i is None:
            return
        if e.button() == Qt.MouseButton.RightButton:
            self.cells[i] = None
        else:
            seq = [None] + list(range(len(self.page.wants))) + ["filled", "empty"]
            cur = self._base(self.cells[i])
            self.cells[i] = seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else None
        self._poke(i)
        self.update()
        self.changed.emit()

    def wheelEvent(self, e):
        i = self._cell_at(e.position())
        if i is None or not isinstance(self._base(self.cells[i]), int):
            e.ignore()
            return
        c = self.cells[i]
        b = self._base(c)
        n = c[1] if isinstance(c, (list, tuple)) else 0
        w = self.page.wants[b].d["item"] if b < len(self.page.wants) else ""
        mx = le.max_stack(w) if w else 64
        n = max(0, min(mx, n + (1 if e.angleDelta().y() > 0 else -1)))
        self.cells[i] = [b, n] if n else b
        self._poke(i)
        self.update()
        self.changed.emit()
        e.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        k = self.K
        a = assets(self.page.version) or ma.newest()
        gui = a.gui("container/generic_54") if a is not None else None
        if gui is not None:                              # the window: top (title) + 3 rows + bottom
            s = gui.width() / 256.0
            p.drawImage(QRectF(0, 0, 176 * k, 71 * k), gui, QRectF(0, 0, 176 * s, 71 * s))
            p.drawImage(QRectF(0, 71 * k, 176 * k, 7 * k), gui, QRectF(0, 215 * s, 176 * s, 7 * s))
        else:                                            # no game: drawn slots
            from .mc_widgets import _bevel_slot
            p.fillRect(self.rect(), QColor(0xC6, 0xC6, 0xC6))
            for j in range(9 * (getattr(self, "rows", 3))):
                _bevel_slot(p, (8 + (j % 9) * 18) * k, (18 + (j // 9) * 18) * k, k)
        font = a.font() if a is not None else None
        if font is not None and font.ok:
            font.draw(p, 8 * k, 6 * k, "Chest", 0x404040, k, shadow=False)
        ox, oy = self._origin()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, c in enumerate(self.cells):
            x, y = (ox + (i % 9) * 18) * k, (oy + (i // 9) * 18) * k
            pop = self._pop.get(i)
            if pop is not None:
                p.save()
                sc = 0.85 + 0.15 * pop
                p.translate(x + 8 * k, y + 8 * k); p.scale(sc, sc); p.translate(-x - 8 * k, -y - 8 * k)
            if c == "empty":
                p.fillRect(QRectF(x, y, 16 * k, 16 * k), QColor(205, 60, 60, 120))
                p.setPen(QPen(QColor(110, 20, 20), 3))
                p.drawLine(QPointF(x + 4 * k, y + 4 * k), QPointF(x + 12 * k, y + 12 * k))
                p.drawLine(QPointF(x + 12 * k, y + 4 * k), QPointF(x + 4 * k, y + 12 * k))
            elif c == "filled":
                p.fillRect(QRectF(x, y, 16 * k, 16 * k), QColor(60, 150, 255, 95))
                p.setPen(QColor(20, 60, 120))
                f = QFont(theme.ui_font(15)); f.setBold(True); p.setFont(f)
                p.drawText(QRectF(x, y, 16 * k, 16 * k), Qt.AlignmentFlag.AlignCenter, "?")
            elif isinstance(self._base(c), int) and self._base(c) < len(self.page.wants):
                n = c[1] if isinstance(c, (list, tuple)) else 0
                b = self._base(c)
                draw_stack(p, x, y, k, _Stub(self.page.wants[b].d), self.page.version,
                           count=(str(n) if n else 0), font=font)
                acc = QColor(theme.ACCENT or "#7c9cff")
                p.setPen(QPen(_rgba("#000000", 0.5), 1)); p.setBrush(acc)
                p.drawEllipse(QRectF(x + 10.5 * k, y - 2 * k, 7 * k, 7 * k))
                p.setPen(QColor("white"))
                f = QFont(theme.ui_font(8)); f.setBold(True); p.setFont(f)
                p.drawText(QRectF(x + 10.5 * k, y - 2 * k, 7 * k, 7 * k), Qt.AlignmentFlag.AlignCenter,
                           chr(65 + b))
            if pop is not None:
                p.restore()
            if i == self._hover:                         # the game's slot highlight
                p.fillRect(QRectF(x, y, 16 * k, 16 * k), QColor(255, 255, 255, 80))
        p.end()


# ── what these chests can hold (click: add it) ─────────────────────────────────
class _LootPalette(QWidget):
    """every item of the chosen chests in the game's chest window, most common
    first; a dot of its tier's colour; hover: the game tooltip and its chance;
    click: wanted. The items already wanted carry their letter."""
    picked = Signal(object)

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.items = []
        self.k = 3.0
        self.rows = 1
        self.title = "Loot"
        self._hover = None
        self._pop = {}
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_items(self, stats, title):
        self.items = stats
        self.title = title
        self.rows = max(1, min(6, math.ceil(len(stats) / 9)))
        self.fit(self.k)

    def fit(self, k):
        self.k = max(1.6, min(3.0, k))
        self.setFixedSize(int(176 * self.k), int((24 + 18 * self.rows) * self.k))
        self.update()

    def _cell_at(self, pos):
        k = self.k
        x, y = pos.x() / k - 8, pos.y() / k - 18
        c, r = int(x // 18), int(y // 18)
        i = r * 9 + c
        return i if (0 <= c < 9 and 0 <= r < self.rows and x >= 0 and y >= 0 and i < len(self.items)) else None

    def _stub(self, s):
        st = _Stub(dict(item=s.item, potion=s.potion))
        st.enchanted = bool(s.enchanted) or s.item == "enchanted_book"
        return st

    def mouseMoveEvent(self, e):
        i = self._cell_at(e.position())
        if i != self._hover:
            self._hover = i
            self.update()
            if i is None:
                hide_tooltip()
            else:
                s = self.items[i]
                k = self.k
                gp = self.mapToGlobal(QPoint(int((8 + (i % 9) * 18 + 17) * k), int((18 + (i // 9) * 18 + 8) * k)))
                from .mc_widgets import show_tooltip as _show, tooltip_lines as _lines
                _show(self, gp, _lines(self._stub(s), self.page.version,
                                       [(f"{pct(s.chance)} of the chests · click to add", 0x55FFFF)]),
                      self.page.version)

    def leaveEvent(self, e):
        self._hover = None
        hide_tooltip()
        self.update()

    def mousePressEvent(self, e):
        i = self._cell_at(e.position())
        if i is None or e.button() != Qt.MouseButton.LeftButton:
            return
        a = _anim(self, 280, lambda v, i=i: (self._pop.__setitem__(i, v), self.update()), 0.0, 1.0,
                  QEasingCurve.Type.OutBack)
        a.finished.connect(lambda i=i: (self._pop.pop(i, None), self.update()))
        a.start()
        self.picked.emit(self.items[i])

    def paintEvent(self, e):
        p = QPainter(self)
        k = self.k
        a = assets(self.page.version) or ma.newest()
        gui = a.gui("container/generic_54") if a is not None else None
        top = 17 + 18 * self.rows
        if gui is not None:
            s = gui.width() / 256.0
            p.drawImage(QRectF(0, 0, 176 * k, top * k), gui, QRectF(0, 0, 176 * s, top * s))
            p.drawImage(QRectF(0, top * k, 176 * k, 7 * k), gui, QRectF(0, 215 * s, 176 * s, 7 * s))
        else:                                            # no game: drawn slots
            from .mc_widgets import _bevel_slot
            p.fillRect(self.rect(), QColor(0xC6, 0xC6, 0xC6))
            for j in range(9 * (getattr(self, "rows", 3))):
                _bevel_slot(p, (8 + (j % 9) * 18) * k, (18 + (j // 9) * 18) * k, k)
        font = a.font() if a is not None else None
        if font is not None and font.ok:
            font.draw(p, 8 * k, 6 * k, self.title, 0x404040, k, shadow=False)
        wanted = {}
        for j, w in enumerate(self.page.wants):
            wanted.setdefault((w.d["item"], w.d.get("potion")), j)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, st in enumerate(self.items[:9 * self.rows]):
            x, y = (8 + (i % 9) * 18) * k, (18 + (i // 9) * 18) * k
            pop = self._pop.get(i)
            if pop is not None:
                p.save()
                sc = 0.8 + 0.2 * pop
                p.translate(x + 8 * k, y + 8 * k); p.scale(sc, sc); p.translate(-x - 8 * k, -y - 8 * k)
            draw_stack(p, x, y, k, self._stub(st), self.page.version, count=0, font=font)
            if pop is not None:
                p.restore()
            col = QColor(_tier(st.chance)[1])
            p.setPen(QPen(_rgba("#000000", 0.6), 1)); p.setBrush(col)
            p.drawEllipse(QRectF(x + 12.2 * k, y + 12.2 * k, 3.6 * k, 3.6 * k))
            j = wanted.get((st.item, st.potion))
            if j is not None:
                acc = QColor(theme.ACCENT or "#7c9cff")
                p.setBrush(acc)
                p.drawEllipse(QRectF(x - 1.5 * k, y - 1.5 * k, 6.5 * k, 6.5 * k))
                p.setPen(QColor("white"))
                f = QFont(theme.ui_font(max(6.5, 2.4 * k))); f.setBold(True); p.setFont(f)
                p.drawText(QRectF(x - 1.5 * k, y - 1.5 * k, 6.5 * k, 6.5 * k), Qt.AlignmentFlag.AlignCenter,
                           chr(65 + j))
            if i == self._hover:
                p.fillRect(QRectF(x, y, 16 * k, 16 * k), QColor(255, 255, 255, 80))
        p.end()


# ── how rare (exact) ───────────────────────────────────────────────────────────
class _Rarity(QWidget):
    """the odds of the query: « 1 in N », its tier, a log meter (1 → 10^9+); the
    number and the marker glide to a new value; a spinner while computing"""
    LOGMAX = 9.0

    def __init__(self):
        super().__init__()
        self.setFixedHeight(110)
        self.title, self.big, self.sub, self.note = "ONE CHEST IN", "—", "Add what you want", ""
        self.p = None
        self.state = "none"                              # none | busy | value | impossible | max
        self._log = 0.0                                  # shown position (log10 N)
        self._col = QColor(theme.TEXT3 or "#888")
        self._spin = 0.0
        self._a = None
        self._t = QTimer(self, interval=30, timeout=self._tick)

    def _tick(self):
        self._spin = (self._spin + 9) % 360
        self.update()

    def busy(self):
        self.state = "busy" if self.state in ("none",) else self.state
        self._busy = True
        self._t.start()
        self.update()

    def set_none(self, sub):
        self._stop_busy()
        self.state, self.title, self.big, self.sub, self.p, self.note = "none", "ONE CHEST IN", "—", sub, None, ""
        self._glide(0.0, QColor(theme.TEXT3 or "#888"))

    def set_value(self, p, sub, title="ONE CHEST IN", big=None, note=""):
        self._stop_busy()
        self.state = "impossible" if (p is not None and p <= 0) else "value"
        self.p, self.title, self.sub, self.note = p, title, sub, note
        self._big = big
        if self.state == "impossible":
            self.big = "Impossible"
            self._glide(self.LOGMAX + 0.6, QColor(IMPOSSIBLE))
        else:
            target = min(self.LOGMAX + 0.3, max(0.0, -math.log10(max(p, 1e-300))))
            self._glide(target, QColor(_tier(p)[1]))

    def _stop_busy(self):
        self._busy = False
        self._t.stop()

    def _glide(self, target, col):
        if self._a is not None:
            self._a.stop()
        l0, c0 = self._log, QColor(self._col)

        def step(t):
            self._log = l0 + (target - l0) * t
            self._col = _mix(c0, col, t)
            self.update()
        self._a = _anim(self, 520, step)
        self._a.start()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath(); path.addRoundedRect(r, 14, 14)
        base = QColor(theme.BG2 or "#1d2129")
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, _rgba(self._col, 0.30)); g.setColorAt(0.55, _rgba(base, 0.88)); g.setColorAt(1, _rgba(base, 0.88))
        p.fillPath(path, g)
        p.setPen(QPen(_rgba(self._col, 0.55), 1.2)); p.drawPath(path)
        x0 = r.x() + 18
        f = QFont(theme.ui_font(7.6)); f.setBold(True); f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        p.setFont(f); p.setPen(QColor(theme.TEXT2 or "#aab"))
        p.drawText(QRectF(x0, r.y() + 10, 260, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.title)
        # the big value (glides with the marker)
        if self.state == "value" and self.p and self._big is None:
            shown = 10 ** self._log
            big = _one_in(1.0 / shown) if shown >= 1 else _one_in(self.p)
            if self._a is not None and self._a.state() != QVariantAnimation.State.Running:
                big = _one_in(self.p)
        elif self.state == "value" and self._big is not None:
            big = self._big
        else:
            big = self.big
        f = QFont(theme.ui_font(21)); f.setBold(True); p.setFont(f)
        p.setPen(self._col if self.state != "none" else QColor(theme.TEXT3 or "#888"))
        p.drawText(QRectF(x0, r.y() + 24, 330, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, big)
        # the tier and the exact percentage, at the right
        if self.state in ("value", "impossible"):
            tname = "Impossible" if self.state == "impossible" else _tier(self.p)[0]
            f = QFont(theme.ui_font(10)); f.setBold(True); p.setFont(f)
            fm = QFontMetricsF(f)
            tw = fm.horizontalAdvance(tname) + 22
            pill = QRectF(r.right() - 16 - tw, r.y() + 12, tw, 24)
            pp = QPainterPath(); pp.addRoundedRect(pill, 12, 12)
            p.fillPath(pp, _rgba(self._col, 0.20))
            p.setPen(QPen(_rgba(self._col, 0.65), 1)); p.drawPath(pp)
            p.setPen(self._col); p.drawText(pill, Qt.AlignmentFlag.AlignCenter, tname)
        f = QFont(theme.ui_font(8.6)); p.setFont(f); p.setPen(QColor(theme.TEXT2 or "#aab"))
        p.drawText(QRectF(r.right() - 260, r.y() + 40, 244, 34),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                   self.sub)
        # the meter: one band per tier, on a log scale
        mx0, mx1 = x0, r.right() - 18
        my = r.bottom() - 26
        edges = [0.0] + [math.log10(t[0]) if t[0] != float("inf") else self.LOGMAX for t in TIERS]
        for i, (_lim, _nm, col) in enumerate(TIERS):
            a = mx0 + (mx1 - mx0) * min(1.0, edges[i] / self.LOGMAX)
            b = mx0 + (mx1 - mx0) * min(1.0, edges[i + 1] / self.LOGMAX)
            seg = QPainterPath(); seg.addRoundedRect(QRectF(a + 1, my, max(1.0, b - a - 2), 6), 3, 3)
            p.fillPath(seg, _rgba(col, 0.55 if self.state == "value" else 0.22))
        if self.state in ("value", "impossible"):
            mx = mx0 + (mx1 - mx0) * min(1.0, self._log / self.LOGMAX)
            tri = QPainterPath()
            tri.moveTo(mx, my - 2); tri.lineTo(mx - 6, my - 11); tri.lineTo(mx + 6, my - 11); tri.closeSubpath()
            p.fillPath(tri, self._col)
            p.setPen(QPen(_rgba("#ffffff", 0.9), 2)); p.drawLine(QPointF(mx, my - 1), QPointF(mx, my + 7))
        f = QFont(theme.ui_font(7)); p.setFont(f); p.setPen(QColor(theme.TEXT3 or "#778"))
        for v, txt in ((0, "1"), (3, "1 000"), (6, "1 M"), (9, "1 B")):
            xx = mx0 + (mx1 - mx0) * v / self.LOGMAX
            p.drawText(QRectF(xx - 30, my + 8, 60, 12), Qt.AlignmentFlag.AlignCenter, txt)
        if getattr(self, "_busy", False):                # computing: a spinner by the title
            p.setPen(QPen(QColor(theme.ACCENT or "#7c9cff"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(x0 + QFontMetricsF(QFont(theme.ui_font(7.6))).horizontalAdvance(self.title) + 18,
                             r.y() + 11, 13, 13), int(-self._spin * 16), 100 * 16)
        p.end()


# ── results ────────────────────────────────────────────────────────────────────
class _ResultDelegate(QStyledItemDelegate):
    """new rows flash (accent fading out); « found » as a pill"""
    FLASH = 900.0

    def __init__(self, table):
        super().__init__(table)
        self.table = table
        self.born = {}                                   # row key → time added

    def paint(self, p, opt, idx):
        h = idx.data(Qt.ItemDataRole.UserRole + 1)
        if h is not None:
            age = (time.time() - h) * 1000.0
            if age < self.FLASH:
                t = 1.0 - age / self.FLASH
                p.fillRect(opt.rect, _rgba(theme.ACCENT or "#7c9cff", 0.32 * t))
        if idx.column() == 2:
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            from PySide6.QtWidgets import QStyle
            sel = bool(opt.state & QStyle.StateFlag.State_Selected)
            if sel:
                p.fillRect(opt.rect, QColor(theme.ACCENT or "#4d7cff"))
            txt = str(idx.data() or "")
            col = QColor("#ffffff") if sel else QColor(idx.data(Qt.ItemDataRole.UserRole + 2) or theme.ACCENT or "#7c9cff")
            f = QFont(theme.ui_font(8.5)); f.setBold(True); p.setFont(f)
            w = QFontMetricsF(f).horizontalAdvance(txt) + 18
            rr = QRectF(opt.rect.center().x() - w / 2, opt.rect.center().y() - 10, w, 20)
            pp = QPainterPath(); pp.addRoundedRect(rr, 10, 10)
            p.fillPath(pp, _rgba(col, 0.18))
            p.setPen(QPen(_rgba(col, 0.6), 1)); p.drawPath(pp)
            p.setPen(col); p.drawText(rr, Qt.AlignmentFlag.AlignCenter, txt)
            p.restore()
            return
        super().paint(p, opt, idx)


class _Bus(QObject):
    hit = Signal(object)
    progress = Signal(object)
    done = Signal(object)


_MODE_DESC = [
    ("Incremental", "Every seed in order, from the start seed."),
    ("Random", "Random seeds (64-bit), never the same order twice: a lucky hunt."),
]


class _Floating(QWidget):
    """a picture that floats gently (the empty states)"""

    def __init__(self, img, size=96):
        super().__init__()
        self.img, self.size_ = img, size
        self.setFixedSize(size + 8, size + 22)
        self._t0 = time.time()
        self._tm = QTimer(self, interval=33, timeout=self.update)

    def showEvent(self, e):
        self._tm.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._tm.stop()
        super().hideEvent(e)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = time.time() - self._t0
        dy = 5.0 * math.sin(t * 1.9)
        cx = self.width() / 2
        sw = self.size_ * (0.62 - 0.05 * math.sin(t * 1.9))
        p.setBrush(_rgba("#000000", 0.28)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - sw / 2, self.height() - 12, sw, 7))
        p.setOpacity(0.8)
        p.drawImage(QRectF(cx - self.size_ / 2, 4 + 5 + dy, self.size_, self.size_), self.img)
        p.end()


def _empty_state(icon_img, text):
    w = QWidget()
    v = QVBoxLayout(w); v.setContentsMargins(20, 20, 20, 20); v.setSpacing(10)
    v.addStretch(1)
    if icon_img is not None:
        v.addWidget(_Floating(icon_img), 0, Qt.AlignmentFlag.AlignHCenter)
    t = QLabel(text); t.setObjectName("LpEmpty"); t.setAlignment(Qt.AlignmentFlag.AlignCenter)
    t.setWordWrap(True)
    v.addWidget(t)
    v.addStretch(2)
    return w


# ── the page ───────────────────────────────────────────────────────────────────
class LootPage(QWidget):
    status = Signal(str)
    openOnMap = Signal(object, str, int, int)          # seed, version name, x, z

    def __init__(self, world, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.world = world
        self.wants = []
        self.search = None
        self.paused = None                              # (query, source, rule, state) after a Pause
        self.seed_list = None
        self.hits = []
        self._seen = set()
        self.query_running = {}
        self.key = "desert_pyramid"
        self.chest_btns = {}
        self.bus = _Bus(self)
        self.bus.hit.connect(self._hit)
        self.bus.progress.connect(self._progress)
        self.bus.done.connect(self._done)
        self._style()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 2, 10, 4); outer.setSpacing(8)
        top = QHBoxLayout(); top.setContentsMargins(2, 0, 0, 0)
        self.view_seg = Segmented(["Finder", "Loot tables"], "Finder")
        icons.bind(self.view_seg.buttons["Finder"], "search", 15)
        icons.bind(self.view_seg.buttons["Loot tables"], "legend", 15)
        self.view_seg.setFixedWidth(280)
        self.view_seg.changed.connect(lambda v: self.stack.setCurrentIndex(0 if v == "Finder" else 1))
        top.addWidget(self.view_seg); top.addStretch(1)
        outer.addLayout(top)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        fw = QWidget(); fl = QVBoxLayout(fw); fl.setContentsMargins(0, 0, 0, 0); fl.setSpacing(10)
        fl.addWidget(self._build_strip())
        main = QHBoxLayout(); main.setSpacing(10)
        main.addWidget(self._build_query())
        from PySide6.QtWidgets import QSplitter
        split = QSplitter(Qt.Orientation.Horizontal); split.setHandleWidth(10)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_results())
        split.addWidget(self._build_preview())
        split.setStretchFactor(0, 2); split.setStretchFactor(1, 3)
        split.setSizes([460, 700])
        main.addWidget(split, 1)
        fl.addLayout(main, 1)
        fl.addWidget(self._build_runbar())
        self.stack.addWidget(fw)
        self.tables_holder = QFrame(); self.tables_holder.setObjectName("LpCard")
        tl = QVBoxLayout(self.tables_holder); tl.setContentsMargins(14, 14, 14, 14)
        self.tables_view = None
        self.stack.addWidget(self.tables_holder)
        self.stack.currentChanged.connect(self._ensure_tables)
        world.changed.connect(self._world_changed)
        self._flash = QTimer(self, interval=40, timeout=self._flash_tick)
        self._pick_struct("desert_pyramid")

    def _style(self):
        acc = theme.ACCENT or "#7c9cff"
        card = theme.PANEL or theme.BG1
        self.setStyleSheet(
            f"#LpCard {{ background: {card}; border: 1px solid {theme.LINE}; border-radius: 14px; }}"
            f"#LpWant {{ background: {theme._rgba(theme.BG2, 0.85) if hasattr(theme, '_rgba') else theme.BG2};"
            f" border: 1px solid {theme.LINE2}; border-radius: 12px; }}"
            f"#LpWant:hover {{ border-color: {acc}; }}"
            f"#LpWantName {{ font-weight: 650; font-size: 9.5pt; }}"
            f"#LpMuted {{ color: {theme.TEXT3}; font-size: 8.5pt; }}"
            f"#LpEmpty {{ color: {theme.TEXT2}; font-size: 10pt; }}"
            f"#LpX {{ border: none; background: transparent; padding: 2px; }}"
            f"#LpX:hover {{ background: {theme.BG3}; border-radius: 6px; }}"
            f"#LpAdd {{ background: transparent; border: 1.5px dashed {theme.LINE2}; border-radius: 12px;"
            f" color: {theme.TEXT2}; font-weight: 600; }}"
            f"#LpAdd:hover {{ border-color: {acc}; color: {theme.TEXT}; background: {theme.ACCENT_SOFT or theme.BG2}; }}"
            f"#LpHead {{ font-size: 15pt; font-weight: 700; }}"
            f"#LpTitle {{ font-size: 12pt; font-weight: 700; }}"
            f"#LpSub {{ color: {theme.TEXT2}; font-size: 9pt; }}"
            f"#LpCount {{ background: {theme.ACCENT_SOFT or theme.BG3}; color: {acc}; border-radius: 10px;"
            f" padding: 1px 9px; font-weight: 700; font-size: 9pt; }}"
            f"#LpChip {{ background: {theme.BG2}; border: 1px solid {theme.LINE2}; border-radius: 13px;"
            f" padding: 3px 10px 3px 6px; color: {theme.TEXT2}; font-weight: 600; }}"
            f"#LpChip:hover {{ border-color: {acc}; color: {theme.TEXT}; }}"
            f"#LpChip:checked {{ border-color: {acc}; color: {theme.TEXT}; background: {theme.ACCENT_SOFT or theme.BG3}; }}"
            f"#LpPick {{ background: {theme.BG2}; border: 1px solid {theme.LINE2}; border-radius: 10px;"
            f" text-align: left; padding: 4px 8px; font-size: 8.5pt; }}"
            f"#LpPick:hover {{ border-color: {acc}; background: {theme.ACCENT_SOFT or theme.BG3}; }}"
            f"#LpPicker {{ background: {theme.BG1}; border: 1px solid {theme.LINE2}; border-radius: 12px; }}"
            f"QTableWidget#LpTable {{ background: transparent; border: none; gridline-color: transparent; }}"
            f"QTableWidget#LpTable::item {{ padding: 0 8px; border-bottom: 1px solid {theme.LINE}; }}"
            f"QTableWidget#LpTable::item:selected {{ background: {theme.ACCENT_SOFT or theme.BG3}; color: {theme.TEXT}; }}")

    # ── the structures ───────────────────────────────────────────────────────
    def _build_strip(self):
        w = QWidget()
        h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        self.tiles = {}
        for k in STRUCTS:
            t = _StructTile(k)
            t.clicked.connect(lambda _=False, k=k: self._pick_struct(k))
            h.addWidget(t)
            self.tiles[k] = t
        return w

    # ── the query ────────────────────────────────────────────────────────────
    def _build_query(self):
        panel = QFrame(); panel.setObjectName("LpCard"); panel.setFixedWidth(600)
        outer = QVBoxLayout(panel); outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(18, 14, 18, 14); v.setSpacing(10)
        # header: the structure
        hd = QHBoxLayout(); hd.setSpacing(12)
        self.q_icon = QLabel(); self.q_icon.setFixedSize(48, 48)
        hd.addWidget(self.q_icon)
        col = QVBoxLayout(); col.setSpacing(0)
        self.q_name = QLabel(""); self.q_name.setObjectName("LpHead")
        self.q_sub = QLabel(""); self.q_sub.setObjectName("LpSub")
        col.addWidget(self.q_name); col.addWidget(self.q_sub)
        hd.addLayout(col, 1)
        self.q_tables = QPushButton("  Loot table"); self.q_tables.setProperty("kind", "ghost")
        icons.bind(self.q_tables, "legend", 15)
        self.q_tables.setToolTip("Every item these chests can hold, with its chance")
        self.q_tables.clicked.connect(lambda: self.open_table(self._sel_tables()[0] if self._sel_tables() else None))
        hd.addWidget(self.q_tables, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(hd)
        self.chest_row = QHBoxLayout(); self.chest_row.setSpacing(6)
        self.chest_w = QWidget(); self.chest_w.setLayout(self.chest_row)
        v.addWidget(self.chest_w)
        v.addWidget(self._label("WHAT YOU WANT"))
        self.want_grid = QGridLayout(); self.want_grid.setSpacing(8)
        self.want_grid.setColumnStretch(0, 1); self.want_grid.setColumnStretch(1, 1)
        ww = QWidget(); ww.setLayout(self.want_grid)
        v.addWidget(ww)
        self.add_card = _AddCard()
        self.add_card.clicked.connect(self._add_item)
        mr = QHBoxLayout(); mr.setSpacing(10)
        self.mode = Segmented(["Find", "Most", "Layout"], "Find")
        self.mode.buttons["Find"].setToolTip("Every seed that has what you want")
        self.mode.buttons["Most"].setToolTip("The seeds with the most of the first item (best first)")
        self.mode.buttons["Layout"].setToolTip("Where each item lies in the chest")
        self.mode.changed.connect(self._mode_changed)
        mr.addWidget(self.mode, 3)
        self.scope = Segmented(["One chest", "Whole structure"], "One chest")
        self.scope.buttons["One chest"].setToolTip("Everything you want in the same chest")
        self.scope.buttons["Whole structure"].setToolTip("Summed over all the structure's chests")
        self.scope.changed.connect(lambda *_: self._query_changed())
        mr.addWidget(self.scope, 2)
        v.addLayout(mr)
        self.grid_box = QWidget()
        gb = QVBoxLayout(self.grid_box); gb.setContentsMargins(0, 2, 0, 0); gb.setSpacing(4)
        self.grid = _LayoutGrid(self)
        self.grid.changed.connect(self._query_changed)
        gb.addWidget(self.grid, 0, Qt.AlignmentFlag.AlignHCenter)
        gh = QHBoxLayout()
        hint = QLabel("Click: any → A, B… → filled → empty  ·  wheel: exact count  ·  right click: any")
        hint.setObjectName("LpMuted")
        gh.addWidget(hint, 1)
        clr = QToolButton(); clr.setText("Clear"); clr.setObjectName("LpX")
        clr.clicked.connect(self.grid.clear)
        gh.addWidget(clr)
        gb.addLayout(gh)
        v.addWidget(self.grid_box)
        self.grid_box.hide()
        ar = QHBoxLayout(); ar.setSpacing(6)
        ar.addWidget(self._label("WITHIN"))
        self.radius = QSpinBox(); self.radius.setRange(16, 30000); self.radius.setSingleStep(100)
        self.radius.setValue(1000); self.radius.setSuffix(" blocks"); self.radius.setFixedWidth(120)
        self.radius.valueChanged.connect(self._query_changed)
        ar.addWidget(self.radius)
        of = QLabel("of"); of.setObjectName("LpMuted"); ar.addWidget(of)
        self.cx = CoordEdit(0, placeholder="X"); self.cz = CoordEdit(0, placeholder="Z")
        for c in (self.cx, self.cz):
            c.setFixedWidth(84)
            c.valueChanged.connect(lambda *_: self._query_changed())
        self.cx.pasted.connect(lambda xz: (self.cx.setValue(xz[0]), self.cz.setValue(xz[1]), self._query_changed()))
        self.cz.pasted.connect(lambda xz: (self.cx.setValue(xz[0]), self.cz.setValue(xz[1]), self._query_changed()))
        lx = QLabel("X"); lx.setObjectName("LpMuted"); ar.addWidget(lx); ar.addWidget(self.cx)
        lz = QLabel("Z"); lz.setObjectName("LpMuted"); ar.addWidget(lz); ar.addWidget(self.cz)
        ar.addStretch(1)
        v.addLayout(ar)
        self.pal_box = QWidget()
        pb = QVBoxLayout(self.pal_box); pb.setContentsMargins(0, 0, 0, 0); pb.setSpacing(4)
        pb.addWidget(self._label("IN THESE CHESTS  ·  CLICK TO ADD"))
        self.palette = _LootPalette(self)
        self.palette.picked.connect(self._palette_pick)
        pb.addWidget(self.palette, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self.pal_box)
        v.addStretch(1)
        self.rarity = _Rarity()
        v.addWidget(self.rarity)
        v.setSpacing(9)
        sc.setWidget(w)
        outer.addWidget(sc)
        self.q_scroll, self.q_body = sc, w
        sc.viewport().installEventFilter(self)
        self._odds_timer = QTimer(self, singleShot=True, interval=300, timeout=self._odds)
        self._odds_fut = self._odds_next = self._odds_job = None
        self._odds_t0 = 0.0
        self._odds_poll = QTimer(self, interval=100, timeout=self._odds_check)
        return panel

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Type.Resize and obj is self.q_scroll.viewport():
            QTimer.singleShot(0, self._fit_palette)
        return super().eventFilter(obj, ev)

    def _fit_palette(self):
        """the palette as big as the free room allows (no scrolling)"""
        if not hasattr(self, "palette") or not self.pal_box.isVisible():
            return
        free = self.q_scroll.viewport().height() - (self.q_body.sizeHint().height() - self.palette.height())
        rows = self.palette.rows
        k = (free - 4) / (24 + 18 * rows)
        k = min(k, (self.q_scroll.viewport().width() - 40) / 176)
        if abs(k - self.palette.k) > 0.02 or self.palette.k > max(1.6, min(3.0, k)):
            self.palette.fit(k)

    def _palette_pick(self, s):
        for w in self.wants:
            if w.d["item"] == s.item and w.d.get("potion") == s.potion:
                return
        self.wants.append(_Want(s.item, 1, None, None, 1, s.potion))
        self._rebuild_wants(new=len(self.wants) - 1)

    def _fill_palette(self):
        best = {}
        tabs = self._sel_tables()
        for tb in tabs:
            st = ls.table_stats(self.version, tb)
            for it in (st.items if st else []):
                key = (it.item, it.potion)
                if key not in best or it.chance > best[key].chance:
                    best[key] = it
        items = sorted(best.values(), key=lambda s: (-s.chance, s.item))
        if len(tabs) == 1:
            g, n = table_label(tabs[0])
            title = n or g
        else:
            title = struct_icons.label(_ICON_KEY.get(self.key, self.key))
        self.palette.set_items(items, title)
        QTimer.singleShot(0, self._fit_palette)

    @staticmethod
    def _label(text):
        lab = QLabel(text); lab.setObjectName("FieldLabel")
        return lab

    @property
    def version(self):
        return _version(self.world.mc)[0]

    def _tables(self):
        pre = _SCOPE.get(self.key, ())
        return [t for t in ls.tables(self.version) if any(t.startswith(p) for p in pre)
                and "dispenser" not in t]

    def _pick_struct(self, key):
        self.key = key
        for k, t in self.tiles.items():
            t.setChecked(k == key)
        self._fill_header()
        self._query_changed()

    def _fill_header(self):
        key = self.key
        tabs = self._tables()
        self.q_icon.setPixmap(struct_icons.pixmap(_ICON_KEY.get(key, key), 48))
        self.q_name.setText(struct_icons.label(_ICON_KEY.get(key, key)))
        nt = len(tabs)
        self.q_sub.setText((f"{nt} kind{'s' * (nt != 1)} of chest · " if nt else "No chest in this version · ")
                           + f"Java {self.version}")
        while self.chest_row.count():
            it = self.chest_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.chest_btns = {}
        for tb in tabs if nt > 1 else []:
            g, n = table_label(tb)
            b = QToolButton(); b.setText(" " + (n or g)); b.setObjectName("LpChip"); b.setCheckable(True)
            img = container_icon("barrel" if "barrel" in tb else "chest", 36, self.version)
            if img is not None:
                pm = QPixmap.fromImage(img); pm.setDevicePixelRatio(img.width() / 18)
                b.setIcon(pm); b.setIconSize(QSize(18, 18))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setChecked(True); b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip("Search in this chest (click to leave it out)")
            b.toggled.connect(lambda *_: self._query_changed())
            self.chest_row.addWidget(b)
            self.chest_btns[tb] = b
        self.chest_row.addStretch(1)
        self.chest_w.setVisible(nt > 1)
        self._update_tiles()

    def _update_tiles(self):
        ver = self.version
        have = ls.tables(ver)
        for k, t in self.tiles.items():
            pre = _SCOPE.get(k, ())
            ok = any(any(tb.startswith(p) for p in pre) for tb in have)
            t.setEnabled(ok)
            t.setToolTip(struct_icons.label(_ICON_KEY.get(k, k)) + ("" if ok else "\nNot in this version"))

    def _add_item(self):
        d = _Picker(self, self.version, self._sel_tables())
        d.move(self.add_card.mapToGlobal(QPoint(0, self.add_card.height() + 4)))
        if d.exec() and d.picked is not None:
            s = d.picked
            self.wants.append(_Want(s.item, 1, None, None, 1, s.potion))
            self._rebuild_wants(new=len(self.wants) - 1)

    def _rebuild_wants(self, new=None):
        while self.want_grid.count():
            it = self.want_grid.takeAt(0)
            wd = it.widget()
            if wd is not None and wd is not self.add_card:
                wd.deleteLater()
        lay = self.mode.value() == "Layout"
        pos = 0                                         # cells of a 2-column grid
        gap = None                                      # a half row left empty (for « Add »)
        for i, w in enumerate(self.wants):
            r = _WantCard(w, self.version, chr(65 + i))
            r.changed.connect(self._query_changed)
            r.removed.connect(self._remove_want)
            r.set_layout(lay)
            if r.ench is not None:                      # its enchantment needs the room: a full row
                if pos % 2 and gap is None:
                    gap = pos
                pos += pos % 2
                self.want_grid.addWidget(r, pos // 2, 0, 1, 2)
                pos += 2
            else:
                self.want_grid.addWidget(r, pos // 2, pos % 2)
                pos += 1
            if i == new:
                fade_in(r)
        if gap is None:
            gap = pos
        self.want_grid.addWidget(self.add_card, gap // 2, gap % 2)
        self.grid.update()
        self._query_changed()
        QTimer.singleShot(0, self._fit_palette)

    def _remove_want(self, card):
        i = self.want_grid.indexOf(card)
        if 0 <= i < len(self.wants):
            del self.wants[i]
            self.grid.drop_want(i)
        self._rebuild_wants()

    def _mode_changed(self, m):
        lay = m == "Layout"
        self.grid_box.setVisible(lay)
        if lay:
            fade_in(self.grid_box, 220)
        for i in range(self.want_grid.count()):
            w = self.want_grid.itemAt(i).widget()
            if isinstance(w, _WantCard):
                w.set_layout(lay)
        self.scope.setVisible(not lay)
        self.pal_box.setVisible(not lay)
        if not lay:
            fade_in(self.pal_box, 220)
            QTimer.singleShot(0, self._fit_palette)
        self._query_changed()

    def _sel_tables(self):
        tabs = self._tables()
        if getattr(self, "chest_btns", None):
            tabs = [t for t in tabs if self.chest_btns.get(t) is None or self.chest_btns[t].isChecked()]
        return tabs

    def query(self):
        mc = int(self.world.mc)
        ver, sub = _version(mc)
        tabs = self._sel_tables()
        return dict(key=self.key, mc=mc, sub=sub, radius=self.radius.value(),
                    cx=self.cx.value(), cz=self.cz.value(),
                    items=[dict(w.d) for w in self.wants],
                    scope="structure" if self.scope.value().startswith("Whole") else "chest",
                    mode=self.mode.value().lower(), layout=list(self.grid.cells),
                    tables=None if len(tabs) == len(self._tables()) else tabs)

    def _query_changed(self, *_):
        self._impossible = False
        if hasattr(self, "palette"):
            tabs = tuple(self._sel_tables())
            if tabs != getattr(self, "_pal_tabs", None) or self.version != getattr(self, "_pal_ver", None):
                self._pal_tabs, self._pal_ver = tabs, self.version
                self._fill_palette()
            self.palette.update()
        if self.paused is not None and self.search is None:
            self.paused = None                          # another query: Start begins again
        self._odds_timer.start()
        self._refresh_run()

    # ── how rare: exact (loot_exact), in its own process ─────────────────────
    def _odds(self):
        q = self.query()
        if not q["items"] and not (q["mode"] == "layout" and any(c is not None for c in q["layout"])):
            self.rarity.set_none("Add what you want")
            return
        job = (q, self._sel_tables(), self.version)
        self.rarity.busy()
        if self._odds_fut is not None and not self._odds_fut.done():
            # (12/11) a long computation is abandoned (its process killed): the new
            # query never waits behind an old one; a short one ends first
            if time.monotonic() - self._odds_t0 > 0.3:
                _odds_kill()
                self._odds_fut = None
            else:
                self._odds_next = job                   # the latest only, after this one
                return
        self._odds_submit(job)

    def _odds_submit(self, job):
        pool = _odds_pool()
        self._odds_job = job
        self._odds_next = None
        self._odds_t0 = time.monotonic()
        try:
            self._odds_fut = pool.submit(lf.odds_job, *job)
        except RuntimeError:                            # a broken pool: a fresh one
            _odds_kill()
            try:
                self._odds_fut = _odds_pool().submit(lf.odds_job, *job)
            except RuntimeError:
                self._odds_fut = None
                return
        self._odds_poll.start()

    def _odds_check(self):
        f = self._odds_fut
        if f is None:
            self._odds_poll.stop()
            return
        if not f.done():
            if time.monotonic() - self._odds_t0 > ODDS_TIMEOUT:
                _odds_kill()                            # (12/11) never a frozen card
                self._odds_fut = None
                self._odds_poll.stop()
                self.rarity.set_none("Too complex to compute")
                self.rarity.setToolTip(f"The exact odds took more than {ODDS_TIMEOUT:.0f} s: "
                                       "simplify the request (fewer cells or items).")
            return
        self._odds_poll.stop()
        if self._odds_next is not None:
            self._odds_submit(self._odds_next)
            return
        try:
            res, approx = f.result()
        except Exception as e:                          # noqa: BLE001
            res, approx = repr(e), None
        self._odds_shown(self._odds_job[0], res, approx)

    def _odds_shown(self, q, res, approx):
        if isinstance(res, str):
            self.rarity.set_none("Could not compute")
            self.rarity.setToolTip(res)
            return
        ok = [(t, _R(*r), b) for t, r, b, _x in res if r is not None]
        tip = "\n".join(f"{table_label(t)[1] or table_label(t)[0]}: "
                        + ("impossible" if r.impossible else f"1 in {_one_in(r.p)}  ({r.p * 100:.6g} %)")
                        + (f" — in {b.replace('_', ' ').title()}" if b else "")
                        for t, r, b in ok)
        self.rarity.setToolTip(("Exact: every draw of the game's fill counted.\n" + tip) if ok else "")
        best = max(ok, key=lambda x: x[1].p, default=None)
        p = best[1].p if best else 0.0
        if approx is not None and approx > p:
            p, best = approx, None
        where = ""
        if best is not None and len(res) > 1:
            g, n = table_label(best[0])
            where = n or g
        if best is not None and best[2]:
            where = (where + " · " if where else "") + "in " + best[2].replace("_", " ").title()
        if q["mode"] == "most" and best is not None and best[1].max_count is not None:
            mx = best[1].max_count
            if not mx and approx is None:
                self._impossible = True
            mn = q["items"][0].get("min") or 1
            self.rarity.set_value(p if mx else 0.0,
                                  (f"≥ {mn}: 1 chest in {_one_in(p)}" if p > 0 else "")
                                  + (f"\n{where}" if where else ""),
                                  "THE MOST IN ONE CHEST", big=(f"up to ×{mx}" if mx else None))
        elif p <= 0 and approx is None:
            self._impossible = True
            self.rarity.set_value(0.0, "never in these chests" if len(res) > 1 else "never in this chest")
        else:
            sub = (f"≈ {pct(p)}" if best is None else f"{p * 100:.4g} %") + (f"\n{where}" if where else "")
            self.rarity.set_value(p, sub)
        self._refresh_run()

    # ── results ──────────────────────────────────────────────────────────────
    def _build_results(self):
        panel = QFrame(); panel.setObjectName("LpCard"); panel.setMinimumWidth(330)
        v = QVBoxLayout(panel); v.setContentsMargins(16, 14, 16, 12); v.setSpacing(8)
        hd = QHBoxLayout(); hd.setSpacing(8)
        t = QLabel("Results"); t.setObjectName("LpTitle"); hd.addWidget(t)
        self.res_count = QLabel("0"); self.res_count.setObjectName("LpCount"); hd.addWidget(self.res_count)
        hd.addStretch(1)
        cp = QToolButton(); icons.bind(cp, "copy", 16); cp.setToolTip("Copy the seeds")
        cp.clicked.connect(self._copy_seeds)
        hd.addWidget(cp)
        clr = QToolButton(); icons.bind(clr, "trash", 16); clr.setToolTip("Clear the results")
        clr.clicked.connect(self._clear_results)
        hd.addWidget(clr)
        v.addLayout(hd)
        self.res_stack = QStackedWidget()
        a = _deco()
        self.res_stack.addWidget(_empty_state(a.chest_icon(144) if a is not None else None,
                                              "Pick a structure, add what you want, then Start."))
        self.table = QTableWidget(0, 3); self.table.setObjectName("LpTable")
        self.table.setHorizontalHeaderLabels(["Seed", "Where", "Found"])
        self.table.horizontalHeaderItem(0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed); self.table.setColumnWidth(2, 104)
        self.table.setShowGrid(False)
        self.table.setFont(theme.mono_font(9.5))
        self.table.setItemDelegate(_ResultDelegate(self.table))
        self.table.itemSelectionChanged.connect(self._selected)
        self.res_stack.addWidget(self.table)
        v.addWidget(self.res_stack, 1)
        return panel

    def _copy_seeds(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText("\n".join(str(h[0]) for h in self.hits))
        self.status.emit(f"{len(self.hits)} seeds copied")

    def _clear_results(self):
        if self.search is not None:
            return
        self.hits = []
        self._seen = set()
        self.table.setRowCount(0)
        self.res_count.setText("0")
        self.res_stack.setCurrentIndex(0)

    def _hit(self, h):
        seed, x, z, score, hits = h
        if (seed, x, z) in self._seen:                  # a resumed block found again
            return
        self._seen.add((seed, x, z))
        if self.query_running.get("mode") == "most":
            if len(self.hits) >= 500 and score <= self.hits[-1][3]:
                return
            self.hits.append(h)
            self.hits.sort(key=lambda t: -t[3])
            del self.hits[500:]
            if not getattr(self, "_refill", False):
                self._refill = True
                QTimer.singleShot(150, self._refill_now)
        else:
            self.hits.append(h)
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._set_row(r, h, flash=True)
        self.res_count.setText(f"{len(self.hits):,}".replace(",", " "))
        self.res_stack.setCurrentIndex(1)

    def _refill_now(self):
        self._refill = False
        sel = self._selected_seed()
        self.table.setRowCount(0)
        for h in self.hits:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._set_row(r, h)
        if sel is not None:
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)[0] == sel:
                    self.table.blockSignals(True); self.table.selectRow(r); self.table.blockSignals(False)
                    break

    def _selected_seed(self):
        it = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        return it.data(Qt.ItemDataRole.UserRole)[0] if it is not None else None

    def _set_row(self, r, h, flash=False):
        seed, x, z, score, hits = h
        now = time.time() if flash else None
        a = QTableWidgetItem(str(seed)); a.setData(Qt.ItemDataRole.UserRole, h)
        b = QTableWidgetItem(f"{x}  {z}"); b.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        b.setForeground(QColor(theme.TEXT2 or "#aab"))
        most = self.query_running.get("mode") == "most"
        c = QTableWidgetItem(f"×{score}" if most else f"{len(hits)} chest{'s' * (len(hits) != 1)}")
        c.setData(Qt.ItemDataRole.UserRole + 2, "#f0b54a" if most else (theme.OK or "#3fd6a0"))
        for col, it in enumerate((a, b, c)):
            if now is not None:
                it.setData(Qt.ItemDataRole.UserRole + 1, now)
            self.table.setItem(r, col, it)
        if flash and not self._flash.isActive():
            self._flash.start()

    def _flash_tick(self):
        self.table.viewport().update()
        last = self.table.item(self.table.rowCount() - 1, 0) if self.table.rowCount() else None
        t = last.data(Qt.ItemDataRole.UserRole + 1) if last is not None else None
        if t is None or time.time() - t > 1.0:
            self._flash.stop()

    # ── the chests of the selected result ────────────────────────────────────
    def _build_preview(self):
        panel = QFrame(); panel.setObjectName("LpCard"); panel.setMinimumWidth(560)
        v = QVBoxLayout(panel); v.setContentsMargins(16, 14, 16, 12); v.setSpacing(8)
        hd = QHBoxLayout(); hd.setSpacing(10)
        self.pv_icon = QLabel(); self.pv_icon.setFixedSize(28, 28); hd.addWidget(self.pv_icon)
        self.pv_icon.hide()
        col = QVBoxLayout(); col.setSpacing(0)
        self.pv_title = QLabel("Chests"); self.pv_title.setObjectName("LpTitle")
        self.pv_sub = QLabel(""); self.pv_sub.setObjectName("LpSub")
        col.addWidget(self.pv_title); col.addWidget(self.pv_sub)
        hd.addLayout(col, 1)
        self.pv_copy = QToolButton(); icons.bind(self.pv_copy, "copy", 16); self.pv_copy.setToolTip("Copy the seed")
        self.pv_copy.clicked.connect(self._copy_one)
        self.pv_copy.setEnabled(False)
        hd.addWidget(self.pv_copy)
        self.pv_map = QPushButton("  Open on the map"); self.pv_map.setProperty("kind", "primary")
        icons.bind(self.pv_map, "map", 16, "#ffffff", "#ffffff")
        self.pv_map.clicked.connect(self._open_map)
        self.pv_map.setEnabled(False)
        hd.addWidget(self.pv_map)
        v.addLayout(hd)
        self.pv_area = QScrollArea(); self.pv_area.setWidgetResizable(True)
        self.pv_area.setFrameShape(QFrame.Shape.NoFrame)
        self.pv_area.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        a = _deco()
        self.pv_area.setWidget(_empty_state(a.chest_icon(144, "trapped") if a is not None else None,
                                            "Pick a result: its chests, exactly as in the game."))
        v.addWidget(self.pv_area, 1)
        return panel

    def _copy_one(self):
        h = getattr(self, "_sel", None)
        if h is not None:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(str(h[0]))
            self.status.emit("Seed copied")

    def _selected(self):
        it = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        if it is None:
            return
        h = it.data(Qt.ItemDataRole.UserRole)
        self._sel = h
        self.pv_map.setEnabled(True); self.pv_copy.setEnabled(True)
        seed, x, z, score, hits = h
        q = self.query_running or self.query()
        key = q["key"]
        self.pv_icon.setPixmap(struct_icons.pixmap(_ICON_KEY.get(key, key), 28)); self.pv_icon.show()
        self.pv_title.setText(f"{struct_icons.label(_ICON_KEY.get(key, key))}  ·  {x}  {z}")
        self.pv_sub.setText(f"Seed {seed}")
        import structure_loot as sl
        lk = lf.LOOT_KEY.get(key, key)
        w = QWidget(); lay = QVBoxLayout(w); m = QLabel("Computing the chests…"); m.setObjectName("LpMuted")
        lay.addWidget(m); lay.addStretch(1)
        self.pv_area.setWidget(w)
        tok = object(); self._pv_tok = tok
        try:
            from .map_engine import loot_pool
            fut = loot_pool().submit(sl.chests, lk, seed, q["mc"], x, z, q.get("sub"))
        except Exception:                                # noqa: BLE001
            fut = None

        def show(cards):
            if tok is not self._pv_tok:
                return
            from .loot_dialog import ChestCard
            ww = QWidget(); ll = QVBoxLayout(ww); ll.setSpacing(12); ll.setContentsMargins(0, 0, 0, 0)
            order = list(hits) + [i for i in range(len(cards or [])) if i not in hits]
            for i in order:
                if i >= len(cards) or cards[i].get("slots") is None:
                    continue
                c = ChestCard(cards[i], _version(q["mc"])[0], lk, self)
                if i in hits:
                    c.setStyleSheet("#LootCard { border: 2px solid #ffc928; }")
                ll.addWidget(c)
            ll.addStretch(1)
            self.pv_area.setWidget(ww)
            fade_in(ww, 240)
        if fut is None:
            show(sl.chests(lk, seed, q["mc"], x, z, q.get("sub")))
            return

        def poll():
            if tok is not self._pv_tok:
                return
            if not fut.done():
                QTimer.singleShot(50, poll)
                return
            try:
                show(fut.result())
            except Exception:                            # noqa: BLE001
                pass
        QTimer.singleShot(30, poll)

    def open_table(self, table, focus_slots=None):          # ChestCard's « Loot table »
        from .loot_table_dialog import LootTableDialog
        LootTableDialog(self.version, table, self, structure=lf.LOOT_KEY.get(self.key, self.key),
                        focus_slots=focus_slots).show()

    def _open_map(self):
        h = getattr(self, "_sel", None)
        if h is None:
            return
        self.openOnMap.emit(int(h[0]), self.world.version_name, int(h[1]), int(h[2]))

    # ── run bar (the Sessions' one) ──────────────────────────────────────────
    def _build_runbar(self):
        from .mode_picker import ModePicker
        from .start_source import StartSource
        from .world import random_seed
        bar = QFrame(); bar.setObjectName("RunBar")
        v = QVBoxLayout(bar); v.setContentsMargins(14, 10, 14, 10); v.setSpacing(8)
        row = QHBoxLayout(); row.setSpacing(10)

        def labeled(text, w, tip=None):
            c = QVBoxLayout(); c.setSpacing(2)
            lab = QLabel(text.upper()); lab.setObjectName("FieldLabel"); c.addWidget(lab); c.addWidget(w)
            if tip:
                w.setToolTip(tip); lab.setToolTip(tip)
            row.addLayout(c)
        self.smode = ModePicker(_MODE_DESC, "Incremental")
        self.smode.changed.connect(lambda *_: self._sync_start_ui())
        labeled("Mode", self.smode, "\n".join(f"{a}: {b}" for a, b in _MODE_DESC))
        self.start_seed = QLineEdit("0"); self.start_seed.setFixedWidth(190)
        self.start_seed.setFont(theme.mono_font(9.5))
        sw = QWidget(); sl = QHBoxLayout(sw); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(2)
        sl.addWidget(self.start_seed)
        dice = QToolButton(); icons.bind(dice, "dice", 16); dice.setToolTip("Random start")
        sl.addWidget(dice)
        self.start_src = StartSource("loot", self.start_seed, ("Manual", "Saved"), (
            "Manual start: the seed typed here.",
            "Saved start: the same start as the Sessions, Stronghold and Draw pages, kept\n"
            "between launches. When a search started from it stops, it moves on to the last\n"
            "seed searched — the next search (on any page) continues from there."), self)
        dice.clicked.connect(lambda: (self.start_seed.setText(str(random_seed() >> 1)),
                                      self.start_src.edited(self.start_seed.text())))
        self.start_src.bind_dice(dice)
        row.addLayout(self.start_src.layout(sw, "The first seed searched (Incremental)."))
        self.threads = QSpinBox(); self.threads.setRange(1, max(1, min(61, os.cpu_count() or 4)))
        self.threads.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.threads.setValue(max(1, (os.cpu_count() or 4) - 1))
        labeled("Processes", self.threads, "Worker processes (every one is used).")
        self.rule_kind = QComboBox()
        for text, key in (("Never", "never"), ("Seeds searched", "seeds"),
                          ("Results found", "results"), ("Time", "time")):
            self.rule_kind.addItem(text, key)
        self.rule_seeds = QLineEdit("10M"); self.rule_seeds.setFixedWidth(110)
        self.rule_seeds.setFont(theme.mono_font(9.5))
        self.rule_seeds.setToolTip("Number of seeds to search: 500000, 250M, 1G, 2^40, 1e12…")
        self.rule_results = QSpinBox(); self.rule_results.setRange(1, 10_000_000); self.rule_results.setValue(100)
        self.rule_results.setToolTip("Pause when this many results are found (Resume to continue).")
        self.rule_time = QTimeEdit(); self.rule_time.setDisplayFormat("H 'h' mm 'min'"); self.rule_time.setTime(QTime(1, 0))
        self.rule_time.setToolTip("Pause after this running time (pauses don't count).")
        self.rule_stack = QStackedWidget(); self.rule_stack.setFixedWidth(112)
        self.rule_stack.addWidget(QLabel(""))
        for wd in (self.rule_seeds, self.rule_results, self.rule_time):
            wd.setFixedWidth(110); self.rule_stack.addWidget(wd)
        self.rule_kind.currentIndexChanged.connect(self.rule_stack.setCurrentIndex)
        rw = QWidget(); rl = QHBoxLayout(rw); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)
        rl.addWidget(self.rule_kind); rl.addWidget(self.rule_stack)
        labeled("Stop after", rw, "When the search stops by itself. Seeds: it ends there.\n"
                                  "Results / Time: it PAUSES — Resume continues from the same point.")
        self.list_btn = QPushButton("Seed list…"); self.list_btn.setProperty("kind", "ghost")
        self.list_btn.setToolTip("Test only the seeds of a file (.txt / .seeds) instead of a range.")
        self.list_btn.clicked.connect(self._seed_list)
        labeled("Source", self.list_btn)
        row.addStretch(1)
        self.b_start = AdaptiveButton("  Start"); self.b_start.setProperty("kind", "primary")
        icons.bind(self.b_start, "play", 16, "#ffffff", "#ffffff")
        self.b_start.clicked.connect(self._start_or_resume)
        self.b_pause = AdaptiveButton("  Pause"); icons.bind(self.b_pause, "pause", 16)
        self.b_pause.clicked.connect(self._pause)
        self.b_stop = AdaptiveButton("  Stop"); icons.bind(self.b_stop, "stop", 16)
        self.b_stop.setProperty("kind", "danger"); self.b_stop.clicked.connect(self._stop)
        for b in (self.b_start, self.b_pause, self.b_stop):
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignBottom)
        v.addLayout(row)
        prow = QHBoxLayout(); prow.setSpacing(12)
        self.prog = QProgressBar(); self.prog.setRange(0, 1000); self.prog.setTextVisible(False)
        self.prog.setFixedHeight(6); prow.addWidget(self.prog, 1)
        self.stat = QLabel("Idle"); self.stat.setObjectName("Muted"); self.stat.setFont(theme.mono_font(9))
        prow.addWidget(self.stat)
        self.engine_chip = QLabel("CPU · exact chests"); self.engine_chip.setObjectName("Chip")
        self.engine_chip.setProperty("state", "off")
        self.engine_chip.setToolTip("Every structure near the centre is generated with its chests,\n"
                                    "exactly as in the game, then checked.")
        prow.addWidget(self.engine_chip)
        v.addLayout(prow)
        self._sync_start_ui()
        return bar

    def _sync_start_ui(self):
        if not hasattr(self, "b_start"):
            return
        idle = self.search is None
        ranged = self.smode.value() == "Incremental" and self.seed_list is None
        self.start_seed.setEnabled(idle and ranged)
        self.start_src.set_enabled(idle and ranged)
        self.smode.setEnabled(idle and self.seed_list is None)
        for w in (self.rule_kind, self.rule_stack, self.list_btn):
            w.setEnabled(idle)
        self.threads.setEnabled(idle)
        self._refresh_run()

    def _seed_list(self):
        if self.seed_list is not None:
            self.seed_list = None
            self.list_btn.setText("Seed list…")
            self._sync_start_ui()
            return
        path, _ = QFileDialog.getOpenFileName(self, "Seed list", user_path(),
                                              "Seed lists (*.txt *.seeds *.csv);;All files (*.*)")
        if not path:
            return
        remember_path(path)
        try:
            from .tools_dialogs import maybe_convert
            path = maybe_convert(self, path)
            from seedlist_io import SeedListSource
            src = SeedListSource(path); n = len(src)
        except Exception as e:                           # noqa: BLE001
            QMessageBox.critical(self, "Seed list", f"Could not read the list:\n{e}")
            return
        self.seed_list = src
        self.list_btn.setText(f"{os.path.basename(path)} · {n:,}  ✕".replace(",", " "))
        self._sync_start_ui()

    def _read_rule(self):
        kind = self.rule_kind.currentData()
        if kind == "seeds":
            from .sessions_page import SessionsPage
            n = SessionsPage.parse_count(self.rule_seeds.text())
            if n <= 0:
                raise ValueError
            return ("seeds", n)
        if kind == "results":
            return ("results", self.rule_results.value())
        if kind == "time":
            t = self.rule_time.time()
            return ("time", max(60, t.hour() * 3600 + t.minute() * 60))
        return ("never", None)

    def _refresh_run(self):
        if not hasattr(self, "b_start"):
            return
        q = self.query()
        ok = bool(q["items"]) or (q["mode"] == "layout" and any(c is not None for c in q["layout"]))
        running = self.search is not None
        never = getattr(self, "_impossible", False)
        self.b_start.setEnabled(not running and ((ok and not never) or self.paused is not None))
        self.b_start.setText("  Resume" if (self.paused is not None and not running) else "  Start")
        self.b_pause.setEnabled(running)
        self.b_stop.setEnabled(running or self.paused is not None)
        if not running and self.paused is None:
            if not ok:
                self.stat.setText("Add what you want")
            elif never:
                self.stat.setText("Impossible in these chests: nothing to search")
            elif self.stat.text() in ("Add what you want", "Impossible in these chests: nothing to search"):
                self.stat.setText("Idle")

    def _start_or_resume(self):
        if self.search is not None:
            return
        if self.paused is not None:
            q, source, rule, state = self.paused
            self.paused = None
            self._launch(q, source, rule, state)
            return
        try:
            rule = self._read_rule()
        except (ValueError, OverflowError):
            QMessageBox.warning(self, "Stop after", "Write a number of seeds, e.g. 500000, 250M, 1G, 2^40.")
            return
        from .world import parse_seed
        if self.seed_list is not None:
            source = ("list", self.seed_list)
        elif self.smode.value() == "Random":
            source = ("random", None)
        else:
            source = ("range", parse_seed(self.start_seed.text()) or 0)
        q = self.query()
        self.query_running = q
        self._clear_results()
        self.start_src.search_started()
        self._launch(q, source, rule, None)

    def _launch(self, q, source, rule, state):
        bus = self.bus
        s = lf.Search(q, self.threads.value(), lambda *h: bus.hit.emit(h),
                      lambda *a: bus.progress.emit(a), source, rule, state)
        self.search = s
        self._run = (q, source, rule)

        def run():
            st = None
            try:
                st = s.run()
            finally:
                bus.done.emit(st)
        threading.Thread(target=run, daemon=True, name="ssa-loot-search").start()
        try:
            import loot_native
            vm = loot_native.available()
        except Exception:                                # noqa: BLE001
            vm = False
        self.engine_chip.setText(f"{'C' if vm else 'Python'} · {self.threads.value()} processes")
        self.engine_chip.setToolTip("The chests are filled and checked in C (lootvm), the same draws as "
                                    "the game." if vm else "lootvm is not built: the chests are filled in "
                                    "Python (slower). py tools/build_native.py --lootvm")
        self.engine_chip.setProperty("state", "ok")
        self.engine_chip.style().unpolish(self.engine_chip); self.engine_chip.style().polish(self.engine_chip)
        self.stat.setText("Starting…")
        self._sync_start_ui()

    def _pause(self):
        if self.search is not None:
            self.search.pause.set()
            self.b_pause.setEnabled(False)
            self.stat.setText("Pausing…")

    def _stop(self):
        if self.search is not None:
            self.search.stop.set()
            self.b_stop.setEnabled(False)
            return
        if self.paused is not None:                     # paused: Stop forgets the resume point
            st = self.paused[3]
            self.paused = None
            self._ended(st)
            self._refresh_run()

    def _progress(self, a):
        done, total, rate, found, el = a
        from .sessions_page import fmt_int, fmt_rate, fmt_time
        if total:
            self.prog.setRange(0, 1000); self.prog.setValue(int(1000 * done / max(1, total)))
        else:
            self.prog.setRange(0, 0)                     # busy
        self.stat.setText(f"{fmt_int(done)} seeds · {fmt_time(el)} · {fmt_rate(rate)} · "
                          f"{fmt_int(found)} found")

    def _done(self, st):
        from .sessions_page import fmt_int, fmt_time
        self.search = None
        self.prog.setRange(0, 1000)
        self.engine_chip.setText("CPU · exact chests"); self.engine_chip.setProperty("state", "off")
        self.engine_chip.style().unpolish(self.engine_chip); self.engine_chip.style().polish(self.engine_chip)
        end = (st or {}).get("end", "stopped")
        if st is not None and end in ("paused", "rule"):
            q, source, rule = self._run
            self.paused = (q, source, rule, st)
            why = "Paused" if end == "paused" else "Paused (stop rule — raise it to go on)"
            self.stat.setText(f"{why} · {fmt_int(st['done'])} seeds · {fmt_time(st['elapsed'])} · "
                              f"{fmt_int(st['found'])} found")
        else:
            self._ended(st)
            if st is not None:
                self.prog.setValue(1000 if end == "done" else self.prog.value())
                self.stat.setText(f"{'Done' if end == 'done' else 'Stopped'} · {fmt_int(st['done'])} seeds · "
                                  f"{fmt_time(st['elapsed'])} · {fmt_int(st['found'])} found")
        self._sync_start_ui()
        self.status.emit(f"Loot: {len(self.hits)} seed(s)")

    def _ended(self, st):
        """the search is over (not paused): a Saved start moves on"""
        if st is not None and self._run[1][0] == "range":
            self.start_src.search_ended(st.get("cursor"))

    # ── loot tables view ─────────────────────────────────────────────────────
    def _ensure_tables(self, i):
        if i != 1:
            return
        if self.tables_view is None or self.tables_view.version() != self.version:
            if self.tables_view is not None:
                self.tables_view.setParent(None)
                self.tables_view.deleteLater()
            self.tables_view = LootTablesView(self.version, None, self)
            self.tables_holder.layout().addWidget(self.tables_view)
            fade_in(self.tables_view, 220)
        tabs = self._sel_tables()                       # open on the structure being searched
        if tabs and getattr(self, "_tv_for", None) != (self.key, self.version):
            self._tv_for = (self.key, self.version)
            try:
                self.tables_view._fill_list(select=tabs[0])
            except Exception:                            # noqa: BLE001
                pass

    def _world_changed(self):
        self._fill_header()
        self._rebuild_wants()
        if self.stack.currentIndex() == 1:
            self._ensure_tables(1)

    def hideEvent(self, e):
        hide_tooltip()
        super().hideEvent(e)
