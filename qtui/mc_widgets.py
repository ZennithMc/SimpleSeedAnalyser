"""
qtui/mc_widgets.py — (06/11) Minecraft's own look for the loot: the chest window of
the game (generic_54), the items as drawn in an inventory, their counts and
durability bars, the game's tooltip — all read from the player's Minecraft
(mc_assets). Without the game, a clean drawn stand-in keeps everything readable.

    ChestView(slots, title, version)     the container window, hover = tooltip
    ItemSlot(item, count, version)       one slot (totals, loot tables)
    show_tooltip(widget, pos, lines)     the game's tooltip
"""
from __future__ import annotations

import colorsys
import re

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

import mc_assets as ma

from . import theme

GRAY = 0xAAAAAA            # §7
BLUE = 0x5555FF            # §9 (beneficial effects)
RED = 0xFF5555             # §c (curses, harmful effects)
TITLE = 0x404040           # container titles
_HARMFUL = {"poison", "slowness", "strong_slowness", "strong_poison", "weakness", "harming",
            "strong_harming", "instant_damage"}
_CURSES = {"binding_curse", "vanishing_curse"}


# ── data about one stack (loot_engine.Stack or loot_stats.ItemStat) ───────────
def _item(st):
    return str(getattr(st, "item", st)).replace("minecraft:", "")


def _ench(st):
    e = getattr(st, "ench", None)
    if e:
        return list(e)
    return []


def glinted(st) -> bool:
    it = _item(st)
    return bool(_ench(st)) or bool(getattr(st, "enchanted", False)) or it in (
        "enchanted_book", "enchanted_golden_apple", "experience_bottle")


def assets(version):
    try:
        return ma.assets_for(version)
    except Exception:                                   # noqa: BLE001
        return None


def icon(st, size, version):
    """the item's QImage (the version's jar, else the newest one), or None"""
    it = _item(st)
    pot = getattr(st, "potion", None)
    for a in (assets(version), ma.newest()):
        if a is None:
            continue
        img = a.icon(it, size, glint=glinted(st), potion=pot, version=version)
        if img is not None:
            return img
    return None


def display_name(st, version) -> str:
    it = _item(st)
    a = assets(version) or ma.newest()
    key = getattr(st, "name_key", None)
    pot = (getattr(st, "potion", None) or "").replace("strong_", "").replace("long_", "")
    if a is not None:
        if key and a.text(key):
            return a.text(key)
        if pot and it in ("potion", "splash_potion", "lingering_potion", "tipped_arrow"):
            n = a.text(f"item.minecraft.{it}.effect.{pot}")
            if n:
                return n
        nb = a.text(f"item.minecraft.{it}") or a.text(f"block.minecraft.{it}")
        if nb:
            return nb
        if it == "grass":
            return a.text("block.minecraft.short_grass") or "Grass"
        if it.endswith("_map"):
            return a.text("item.minecraft.filled_map") or "Map"
    return it.replace("_", " ").title()


def item_detail(st, version) -> str:
    """the gray line under a few names: the trim of a smithing template…"""
    it = _item(st)
    a = assets(version) or ma.newest()
    if a is None:
        return ""
    if it.endswith("_armor_trim_smithing_template"):
        k = it[:-len("_armor_trim_smithing_template")]
        return a.text(f"trim_pattern.minecraft.{k}") or k.title() + " Armor Trim"
    if it == "netherite_upgrade_smithing_template":
        return a.text("upgrade.minecraft.netherite_upgrade") or "Netherite Upgrade"
    if it.startswith("music_disc_"):
        return a.text(f"item.minecraft.{it}.desc") or a.text(f"jukebox_song.minecraft.{it[11:]}")
    if it.endswith("_banner_pattern"):
        return a.text(f"item.minecraft.{it}.desc")
    if it == "disc_fragment_5":
        return a.text("item.minecraft.disc_fragment_5.desc")
    return ""


def ench_name(eid, lvl, version) -> str:
    """the game's name: the level only when the enchantment has more than one"""
    a = assets(version) or ma.newest()
    nm = a.text(f"enchantment.minecraft.{eid}") if a is not None else ""
    nm = nm or eid.replace("_", " ").title()
    mx = 0
    try:
        import loot_enchant as lx
        E = lx.env(version or "26.3")
        for e in E.order:
            if e.id == eid:
                mx = e.max_level
                break
    except Exception:                                   # noqa: BLE001
        mx = 0
    if lvl and (lvl != 1 or mx != 1):
        nm += " " + _roman(lvl)
    return nm


def _roman(n):
    return {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX",
            10: "X"}.get(int(n), str(n))


def tooltip_lines(st, version, extra=None) -> list:
    """[(text, rgb)] like the game's tooltip: the name in its rarity colour, then the
    enchantments (curses red), the effect of a potion, the other facts in gray"""
    it = _item(st)
    ench = _ench(st)
    rar = ma.rarity(it, bool(ench) and it != "enchanted_book")
    lines = [(display_name(st, version), ma.RARITY_COL[rar])]
    det = item_detail(st, version)
    if det:
        lines.append((det, GRAY))
    for eid, lvl in ench:
        lines.append((ench_name(eid, lvl, version), RED if eid in _CURSES else GRAY))
    pot = getattr(st, "potion", None)
    if pot:
        base = pot.replace("strong_", "").replace("long_", "")
        a = assets(version) or ma.newest()
        eff = ma._POTION_EFFECT.get(base, base)
        nm = (a.text(f"effect.minecraft.{eff}") if a else "") or eff.replace("_", " ").title()
        if pot.startswith("strong_"):
            nm += " II"
        lines.append((nm, RED if pot in _HARMFUL else BLUE))
    for inf in getattr(st, "info", []) or []:
        s = str(inf)
        if ench and any(s.startswith(e[0].replace("_", " ").capitalize()) for e in ench):
            continue
        if pot and s == pot:
            continue
        if s.startswith("map →") or "variant not computed" in s:
            continue
        if s.startswith("durability "):
            m = re.match(r"durability (\d+)/(\d+)", s)
            s = f"Durability: {m.group(1)} / {m.group(2)}" if m else s.capitalize()
        lines.append((s[:1].upper() + s[1:], GRAY))
    for t in extra or []:
        lines.append(t if isinstance(t, tuple) else (str(t), GRAY))
    return lines


# ── the game's tooltip ─────────────────────────────────────────────────────────
class _Tip(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.lines, self.font_ = [], None
        self.k = 2

    def set(self, lines, a):
        self.lines = lines
        self.font_ = a.font() if a is not None and a.font().ok else None
        k = self.k
        if self.font_:
            w = max(self.font_.width(t, k) for t, _c in lines) + k
            lh = 10 * k
            h = (8 + (2 if len(lines) > 1 else 0) + 10 * (len(lines) - 1)) * k
        else:
            f = QFont(theme.ui_font(10)); f.setBold(False)
            from PySide6.QtGui import QFontMetrics
            fm = QFontMetrics(f)
            w = max(fm.horizontalAdvance(t) for t, _c in lines)
            lh = fm.height() + 2
            h = lh * len(lines) + 2 * k
        self.lh = lh
        self.resize(int(w + 8 * k), int(h + 8 * k))
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        k = self.k
        W, H = self.width(), self.height()
        bg = QColor(16, 0, 16, 240)
        p.fillRect(QRect(k, 0, W - 2 * k, H), bg)
        p.fillRect(QRect(0, k, W, H - 2 * k), bg)
        # the border: a purple gradient, top to bottom
        for y in range(k, H - k):
            t = (y - k) / max(1, H - 3 * k)
            c = QColor(int(0x50 * (1 - t) + 0x28 * t), 0, int(0xFF * (1 - t) + 0x7F * t), int(0x50 * (1 - t) + 0x50 * t))
            p.fillRect(QRect(k, y, k, 1), c)
            p.fillRect(QRect(W - 2 * k, y, k, 1), c)
        p.fillRect(QRect(2 * k, k, W - 4 * k, k), QColor(0x50, 0, 0xFF, 0x50))
        p.fillRect(QRect(2 * k, H - 2 * k, W - 4 * k, k), QColor(0x28, 0, 0x7F, 0x50))
        x, y = 4 * k, 4 * k
        for i, (t, c) in enumerate(self.lines):
            if self.font_:
                self.font_.draw(p, x, y, t, c, k)
                y += (12 if i == 0 and len(self.lines) > 1 else 10) * k
            else:
                p.setPen(QColor(c))
                f = QFont(theme.ui_font(10))
                p.setFont(f)
                p.drawText(QPointF(x, y + self.lh - 4), t)
                y += self.lh
        p.end()


_TIP = None


def show_tooltip(widget, gpos: QPoint, lines, version=None):
    global _TIP
    if _TIP is None:
        _TIP = _Tip()
    a = assets(version) or ma.newest()
    _TIP.set(lines, a)
    scr = QApplication.screenAt(gpos) or QApplication.primaryScreen()
    r = scr.availableGeometry()
    x, y = gpos.x() + 14, gpos.y() - 14 - _TIP.height() // 2
    if x + _TIP.width() > r.right():
        x = gpos.x() - 14 - _TIP.width()
    y = max(r.top(), min(y, r.bottom() - _TIP.height()))
    _TIP.move(x, y)
    _TIP.show()
    _TIP.raise_()


def hide_tooltip():
    if _TIP is not None:
        _TIP.hide()


# ── drawing one stack in a 16 × 16 slot ───────────────────────────────────────
def draw_stack(p: QPainter, x, y, k, st, version, count=None, font=None):
    """the item at (x, y) (top-left of the 16-px slot interior), k px per game px"""
    img = icon(st, int(16 * k), version)
    if img is not None:
        p.drawImage(QRectF(x, y, 16 * k, 16 * k), img)
    else:                                               # no game: a short label
        p.save()
        p.setPen(QColor("#2b2b2b"))
        from PySide6.QtGui import QFontMetricsF
        nm = _item(st).replace("_", " ")
        words = nm.split()
        pt = max(5.0, 3.2 * k)
        while pt > 4.0:                             # the longest word fits the slot
            f = QFont(theme.ui_font(pt)); f.setBold(True)
            if max(QFontMetricsF(f).horizontalAdvance(w) for w in words) <= 17 * k:
                break
            pt -= 0.5
        p.setFont(f)
        p.drawText(QRectF(x - k / 2, y, 17 * k, 16 * k), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                   nm)
        p.restore()
    dmg = getattr(st, "damage", None)
    if dmg and dmg[1] and dmg[0] < dmg[1]:
        left, mx = dmg
        w = round(13.0 * left / mx)
        hue = max(0.0, left / mx) / 3.0
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        p.fillRect(QRectF(x + 2 * k, y + 13 * k, 13 * k, 2 * k), QColor(0, 0, 0))
        p.fillRect(QRectF(x + 2 * k, y + 13 * k, w * k, k), QColor(int(r * 255), int(g * 255), int(b * 255)))
    n = getattr(st, "count", 1) if count is None else count
    if n is not None and (isinstance(n, str) or n > 1):
        txt = str(n)
        if font is not None and font.ok:
            tw = font.width(txt, k)
            font.draw(p, x + 17 * k - tw, y + 9 * k, txt, 0xFFFFFF, k)
        else:
            p.save()
            f = QFont(theme.ui_font(max(7.0, 4.2 * k)))
            f.setBold(True)
            p.setFont(f)
            r = QRectF(x, y + 6 * k, 17 * k, 10 * k)
            p.setPen(QColor(63, 63, 63))
            p.drawText(r.translated(k, k), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, txt)
            p.setPen(QColor(255, 255, 255))
            p.drawText(r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, txt)
            p.restore()


def _bevel_slot(p, x, y, k):
    """a drawn slot (no game): dark top-left, light bottom-right"""
    p.fillRect(QRectF(x - k, y - k, 18 * k, 18 * k), QColor(0x37, 0x37, 0x37))
    p.fillRect(QRectF(x, y, 17 * k, 17 * k), QColor(0xFF, 0xFF, 0xFF))
    p.fillRect(QRectF(x, y, 16 * k, 16 * k), QColor(0x8B, 0x8B, 0x8B))


# ── the container window ───────────────────────────────────────────────────────
class ChestView(QWidget):
    """The game's chest window (3 or 6 rows of 9) filled with the slots; scale
    chosen to fit (≤ 3 game px per px). hover: the slot lights up, the tooltip."""
    hovered = Signal(int)

    def __init__(self, slots, title="Chest", version=None, parent=None, max_scale=3):
        super().__init__(parent)
        self.slots = list(slots or [])
        self.rows = max(1, (len(self.slots) + 8) // 9)
        self.title = title
        self.version = version
        self.max_scale = max_scale
        self.hover = -1
        self.focus_items = set()            # slots to outline (a searched item)
        self.setMouseTracking(True)
        self.a = assets(version) or ma.newest()
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # geometry (game px): 176 wide, 17 + 18·rows + 7 high
    def _h(self):
        return 17 + 18 * self.rows + 7

    def _k(self):
        return max(1, min(self.max_scale, self.width() // 176))

    def sizeHint(self):
        return QSize(176 * self.max_scale, self._h() * self.max_scale)

    def minimumSizeHint(self):
        return QSize(176 * 2, self._h() * 2)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._h() * max(1, min(self.max_scale, w // 176))

    def resizeEvent(self, e):
        self.setFixedHeight(self.heightForWidth(self.width()))
        super().resizeEvent(e)

    def _origin(self):
        k = self._k()
        return (self.width() - 176 * k) // 2, 0, k

    def _slot_at(self, pos):
        ox, oy, k = self._origin()
        x, y = (pos.x() - ox) / k - 8, (pos.y() - oy) / k - 18
        if x < 0 or y < 0:
            return -1
        c, r = int(x // 18), int(y // 18)
        if c >= 9 or r >= self.rows or x - c * 18 >= 17 or y - r * 18 >= 17:
            return -1
        i = r * 9 + c
        return i if i < len(self.slots) else -1

    def mouseMoveEvent(self, e):
        i = self._slot_at(e.position().toPoint())
        if i != self.hover:
            self.hover = i
            self.update()
            self.hovered.emit(i)
        st = self.slots[i] if 0 <= i < len(self.slots) else None
        if st is not None:
            show_tooltip(self, e.globalPosition().toPoint(), tooltip_lines(st, self.version), self.version)
        else:
            hide_tooltip()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()
        hide_tooltip()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        ox, oy, k = self._origin()
        a = self.a
        gui = a.gui("container/generic_54") if a is not None else None
        top = 17 + 18 * self.rows
        if gui is not None:
            s = gui.width() / 256.0
            p.drawImage(QRectF(ox, oy, 176 * k, top * k), gui, QRectF(0, 0, 176 * s, top * s))
            p.drawImage(QRectF(ox, oy + top * k, 176 * k, 7 * k), gui, QRectF(0, 215 * s, 176 * s, 7 * s))
        else:                                       # drawn: the game's colours
            W, H = 176 * k, (top + 7) * k
            p.fillRect(QRectF(ox + k, oy + k, W - 2 * k, H - 2 * k), QColor(0xC6, 0xC6, 0xC6))
            p.fillRect(QRectF(ox + k, oy, W - 3 * k, k), QColor(0, 0, 0))
            p.fillRect(QRectF(ox, oy + k, k, H - 3 * k), QColor(0, 0, 0))
            p.fillRect(QRectF(ox + 2 * k, oy + H - k, W - 3 * k, k), QColor(0, 0, 0))
            p.fillRect(QRectF(ox + W - k, oy + 2 * k, k, H - 3 * k), QColor(0, 0, 0))
            p.fillRect(QRectF(ox + k, oy + k, W - 3 * k, 2 * k), QColor(255, 255, 255))
            p.fillRect(QRectF(ox + k, oy + k, 2 * k, H - 3 * k), QColor(255, 255, 255))
            p.fillRect(QRectF(ox + 3 * k, oy + H - 3 * k, W - 4 * k, 2 * k), QColor(0x55, 0x55, 0x55))
            p.fillRect(QRectF(ox + W - 3 * k, oy + 3 * k, 2 * k, H - 4 * k), QColor(0x55, 0x55, 0x55))
            for i in range(9 * self.rows):
                _bevel_slot(p, ox + (8 + (i % 9) * 18) * k, oy + (18 + (i // 9) * 18) * k, k)
        font = a.font() if a is not None else None
        if font is not None and font.ok:
            font.draw(p, ox + 8 * k, oy + 6 * k, self.title, TITLE, k, shadow=False)
        else:
            p.setPen(QColor(TITLE))
            f = QFont(theme.ui_font(max(7.0, 3.6 * k)))
            p.setFont(f)
            p.drawText(QPointF(ox + 8 * k, oy + 13 * k), self.title)
        for i, st in enumerate(self.slots):
            x, y = ox + (8 + (i % 9) * 18) * k, oy + (18 + (i // 9) * 18) * k
            if i in self.focus_items:
                p.fillRect(QRectF(x, y, 16 * k, 16 * k), QColor(255, 214, 64, 120))
            if st is not None:
                draw_stack(p, x, y, k, st, self.version, font=font)
            if i == self.hover:
                p.fillRect(QRectF(x, y, 16 * k, 16 * k), QColor(255, 255, 255, 128))
        p.end()


# ── one slot (totals, loot table rows) ────────────────────────────────────────
class ItemSlot(QWidget):
    """An item in a game slot, its count at the bottom right; hover = tooltip."""

    def __init__(self, st, version=None, k=2, count=None, extra=None, framed=True, parent=None):
        super().__init__(parent)
        self.st, self.version, self.k, self.count, self.extra = st, version, k, count, extra
        self.framed = framed
        self.setFixedSize(18 * k, 18 * k)
        self.setMouseTracking(True)
        self.a = assets(version) or ma.newest()

    def paintEvent(self, e):
        p = QPainter(self)
        k = self.k
        if self.framed:
            gui = self.a.gui("container/generic_54") if self.a is not None else None
            if gui is not None:
                s = gui.width() / 256.0
                p.drawImage(QRectF(0, 0, 18 * k, 18 * k), gui, QRectF(7 * s, 17 * s, 18 * s, 18 * s))
            else:
                _bevel_slot(p, k, k, k)
        font = self.a.font() if self.a is not None else None
        draw_stack(p, k, k, k, self.st, self.version, count=self.count, font=font)
        p.end()

    def enterEvent(self, e):
        show_tooltip(self, self.mapToGlobal(QPoint(self.width(), self.height() // 2)),
                     tooltip_lines(self.st, self.version, self.extra), self.version)

    def leaveEvent(self, e):
        hide_tooltip()


def container_kind(card) -> str:
    """chest / trapped_chest / barrel / minecart / copper_chest / dispenser"""
    b = str(card.get("block") or "")
    t = str(card.get("table") or "")
    n = str(card.get("name") or "").lower()
    if "dispenser" in t:
        return "dispenser"
    if "barrel" in t or "barrel" in n or b == "barrel":
        return "barrel"
    if "minecart" in n or "abandoned_mineshaft" in t:
        return "minecart"
    if "secret_chest" in t or "copper" in n:
        return "copper_chest"
    if "trapped" in n or b == "trapped_chest":
        return "trapped_chest"
    return "chest"


def container_title(card, version) -> str:
    kind = container_kind(card)
    a = assets(version) or ma.newest()
    key = {"barrel": "container.barrel", "minecart": "entity.minecraft.chest_minecart",
           "dispenser": "container.dispenser", "trapped_chest": "container.chest",
           "copper_chest": "container.chest"}.get(kind, "container.chest")
    if len(card.get("slots") or []) > 27 and kind in ("chest", "trapped_chest"):
        key = "container.chestDouble"
    t = a.text(key) if a is not None else ""
    return t or {"barrel": "Barrel", "minecart": "Minecart with Chest"}.get(kind, "Chest")


def container_icon(kind, size, version):
    """a small picture of the container"""
    item = {"barrel": "barrel", "minecart": "chest_minecart", "dispenser": "dispenser",
            "copper_chest": "copper_chest", "trapped_chest": "trapped_chest"}.get(kind, "chest")
    for a in (assets(version), ma.newest()):
        if a is None:
            continue
        img = a.icon(item, size)
        if img is None and item in ("chest", "trapped_chest", "copper_chest"):
            img = a.chest_icon(size, {"trapped_chest": "trapped", "copper_chest": "copper"}.get(item, "normal"))
        if img is not None:
            return img
    return None
