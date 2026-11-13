"""
qtui/loot_table_dialog.py — (06/11) every chest loot table of every version, every
item it can give with its chance (loot_stats: exact, from the table): the chance a
chest has at least one, how many per stack, the mean per chest.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QScrollArea, QVBoxLayout, QWidget)

import loot_engine as le
import loot_stats as ls
import mc_assets as ma

from . import theme
from .mc_widgets import ItemSlot, container_icon, display_name, hide_tooltip, item_detail

# table id → (group, name, container kind)
_GROUPS = [
    ("village/", "Village"), ("trial_chambers/", "Trial Chambers"), ("bastion_", "Bastion Remnant"),
    ("stronghold_", "Stronghold"), ("underwater_ruin_", "Ocean Ruin"), ("shipwreck_", "Shipwreck"),
    ("abandoned_camp_", "Abandoned Camp"), ("ancient_city", "Ancient City"),
    ("jungle_temple", "Jungle Temple"),
]
_SINGLE = {"abandoned_mineshaft": "Mineshaft", "buried_treasure": "Buried Treasure",
           "desert_pyramid": "Desert Pyramid", "end_city_treasure": "End City",
           "igloo_chest": "Igloo", "nether_bridge": "Nether Fortress", "pillager_outpost": "Pillager Outpost",
           "ruined_portal": "Ruined Portal", "simple_dungeon": "Dungeon", "spawn_bonus_chest": "Bonus Chest",
           "woodland_mansion": "Woodland Mansion"}
_NICE = {"village_weaponsmith": "Weaponsmith", "village_toolsmith": "Toolsmith", "village_armorer": "Armorer",
         "village_cartographer": "Cartographer", "village_mason": "Mason", "village_shepherd": "Shepherd",
         "village_butcher": "Butcher", "village_fletcher": "Fletcher", "village_fisher": "Fisherman",
         "village_tannery": "Tannery", "village_temple": "Temple", "village_desert_house": "Desert house",
         "village_plains_house": "Plains house", "village_savanna_house": "Savanna house",
         "village_snowy_house": "Snowy house", "village_taiga_house": "Taiga house",
         "underwater_ruin_big": "Big ruin", "underwater_ruin_small": "Small ruin",
         "shipwreck_map": "Map chest", "shipwreck_supply": "Supply chest", "shipwreck_treasure": "Treasure chest",
         "stronghold_library": "Library", "stronghold_corridor": "Corridor", "stronghold_crossing": "Storeroom",
         "bastion_treasure": "Treasure", "bastion_bridge": "Bridge", "bastion_hoglin_stable": "Hoglin stable",
         "bastion_other": "Other chests", "ancient_city": "Chest", "ancient_city_ice_box": "Ice box",
         "jungle_temple": "Chest", "jungle_temple_dispenser": "Dispenser",
         "abandoned_camp_barrel": "Barrel", "abandoned_camp_common_chest": "Chest",
         "abandoned_camp_secret_chest": "Secret chest"}
# where each structure's loot (structure_loot keys) points first
STRUCT_TABLE = {"desert_pyramid": "desert_pyramid", "jungle_temple": "jungle_temple", "igloo": "igloo_chest",
                "shipwreck": "shipwreck_treasure", "ocean_ruin": "underwater_ruin_big",
                "village": "village/village_weaponsmith", "pillager_outpost": "pillager_outpost",
                "mineshaft": "abandoned_mineshaft", "ruined_portal": "ruined_portal",
                "end_city": "end_city_treasure", "end_city_ship": "end_city_treasure",
                "mansion": "woodland_mansion", "fortress": "nether_bridge", "bastion": "bastion_treasure",
                "ancient_city": "ancient_city", "abandoned_camp": "abandoned_camp_common_chest",
                "dungeon": "simple_dungeon", "stronghold": "stronghold_corridor",
                "first_stronghold": "stronghold_corridor", "treasure": "buried_treasure",
                "buried_treasure": "buried_treasure", "trial_chambers": "trial_chambers/reward"}


# the tables a structure's containers can use (its own group)
STRUCT_SCOPE = {"desert_pyramid": ("desert_pyramid",), "jungle_temple": ("jungle_temple",),
                "igloo": ("igloo_chest",), "shipwreck": ("shipwreck_",), "ocean_ruin": ("underwater_ruin_",),
                "village": ("village/",), "pillager_outpost": ("pillager_outpost",),
                "mineshaft": ("abandoned_mineshaft",), "ruined_portal": ("ruined_portal",),
                "ruined_portal_nether": ("ruined_portal",), "end_city": ("end_city_treasure",),
                "end_city_ship": ("end_city_treasure",), "mansion": ("woodland_mansion",),
                "fortress": ("nether_bridge",), "bastion": ("bastion_",), "ancient_city": ("ancient_city",),
                "abandoned_camp": ("abandoned_camp_",), "dungeon": ("simple_dungeon",),
                "stronghold": ("stronghold_",), "first_stronghold": ("stronghold_",),
                "treasure": ("buried_treasure",), "buried_treasure": ("buried_treasure",),
                "trial_chambers": ("trial_chambers/",)}


def _norm_table(t: str) -> str:
    t = str(t).replace("minecraft:", "")
    return t[len("chests/"):] if t.startswith("chests/") else t


def tables_of(cards) -> dict:
    """{table id: number of containers} of computed loot cards"""
    out = {}
    for c in cards or []:
        if c.get("slots") is None:
            continue
        for t in str(c.get("table") or "").split(" + "):
            if t and "dispenser" not in t:
                k = _norm_table(t)
                out[k] = out.get(k, 0) + 1
    return out


def table_label(tid: str):
    """(group, name) of a table id"""
    if tid in _SINGLE:
        return _SINGLE[tid], ""
    base = tid.split("/")[-1]
    for pre, g in _GROUPS:
        if tid.startswith(pre) or tid == pre.rstrip("_/"):
            if g == "Trial Chambers":
                if base.startswith("reward"):
                    rest = base[len("reward"):].strip("_").split("_")
                    om = rest and rest[0] == "ominous"
                    rest = [r for r in rest if r and r != "ominous"]
                    nm = ("Ominous vault" if om else "Vault") + (f" · {rest[0]}" if rest else "")
                    return g, nm
                return g, base.replace("_", " ").capitalize()
            return g, _NICE.get(base, base.replace("_", " ").capitalize())
    return base.replace("_", " ").title(), ""


def table_kind(tid: str) -> str:
    if "barrel" in tid:
        return "barrel"
    if "dispenser" in tid:
        return "dispenser"
    if tid == "abandoned_mineshaft":
        return "minecart"
    if "secret_chest" in tid:
        return "copper_chest"
    if tid.startswith("trial_chambers/reward"):
        return "vault"
    return "chest"


def _icon_pix(kind, size, version):
    if kind == "vault":
        for a in (ma.assets_for(version), ma.newest()):
            if a is not None:
                img = a.icon("vault", size * 2)
                if img is not None:
                    return _pm(img, size)
        kind = "chest"
    return _pm(container_icon(kind, size * 2, version), size)


def _pm(img, size):
    if img is None:
        return QPixmap()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(img.width() / size)
    return pm


def pct(p: float) -> str:
    if p >= 0.9995:
        return "100 %"
    if p >= 0.0995:
        return f"{p * 100:.0f} %"
    if p >= 0.00995:
        return f"{p * 100:.1f} %"
    if p >= 0.000995:
        return f"{p * 100:.2f} %"
    return f"{p * 100:.3f} %"


# chance tiers: the colour of the bar and the value (common → rare)
def tier(p: float) -> str:
    if p >= 0.5:
        return "#3fd6a0"
    if p >= 0.15:
        return theme.ACCENT or "#7c9cff"
    if p >= 0.05:
        return "#b48cff"
    return "#f0b54a"


class _Row(QWidget):
    """one item: its slot, its name (and detail), the chance as a bar and a big
    value, the stack size under it. Hover the slot: the game's tooltip."""
    H = 58

    def __init__(self, s, version, mark=False, hl=False):
        super().__init__()
        self.s, self.version, self.mark, self.hl = s, version, mark, hl
        self.setFixedHeight(self.H)
        self.setMouseTracking(True)
        self.hover = False
        extra = [(f"Pool {pi + 1}: {pct(sh)} of the rolls", 0xAAAAAA) for pi, sh in s.share]
        extra.append((f"Mean per chest: {s.mean:.2f}", 0xAAAAAA))
        self.slot = ItemSlot(s, version, k=2, count=None, extra=extra, parent=self)
        self.slot.move(10, (self.H - self.slot.height()) // 2)
        self.name = display_name(s, version)
        sub = []
        det = item_detail(s, version)
        if det:
            sub.append(det)
        if s.enchanted and s.item != "enchanted_book":
            sub.append("Enchanted" + (f" · {s.label}" if s.label else ""))
        elif s.label:
            sub.append(s.label)
        self.sub = "  ·  ".join(sub)
        self.stack = (f"×{s.cmin}–{s.cmax}" if s.cmax > s.cmin else (f"×{s.cmin}" if s.cmin else ""))
        tip = [f"{pct(s.chance)} of the chests have at least one",
               f"Mean per chest: {s.mean:.2f}"]
        if self.stack:
            tip.append(f"Per stack: {self.stack[1:]}")
        tip += [f"Pool {pi + 1}: {pct(sh)} of its rolls" for pi, sh in s.share]
        self.setToolTip("\n".join(tip))

    def enterEvent(self, e):
        self.hover = True; self.update()

    def leaveEvent(self, e):
        self.hover = False; self.update()

    def paintEvent(self, e):
        from PySide6.QtGui import QFontMetrics
        q = QPainter(self)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if self.hover or self.hl:
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(theme.ACCENT_SOFT or theme.BG3) if self.hl else QColor(theme.BG2))
            q.drawRoundedRect(QRectF(2, 2, W - 4, H - 4), 10, 10)
        col = QColor(tier(self.s.chance))
        right = 108                                     # the value's column
        x0 = 10 + self.slot.width() + 14
        tw = max(40, W - x0 - right - 14)
        # name (+ detail) — elided to the room left
        f1 = theme.ui_font(10.5); f1.setWeight(f1.Weight.DemiBold)
        q.setFont(f1)
        fm = QFontMetrics(f1)
        q.setPen(QColor(theme.TEXT))
        y1 = 8
        q.drawText(QRectF(x0, y1, tw, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.name, Qt.TextElideMode.ElideRight, int(tw)))
        nw = min(tw, fm.horizontalAdvance(self.name))
        if self.mark:
            q.setPen(Qt.PenStyle.NoPen); q.setBrush(QColor("#ffc928"))
            q.drawEllipse(QRectF(x0 + nw + 8, y1 + 7, 7, 7))
        if self.sub:
            f2 = theme.ui_font(8.5); q.setFont(f2)
            fm2 = QFontMetrics(f2)
            q.setPen(QColor(theme.TEXT3))
            sx = x0 + nw + (22 if self.mark else 10)
            if sx < x0 + tw - 30:
                q.drawText(QRectF(sx, y1, x0 + tw - sx, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           fm2.elidedText(self.sub, Qt.TextElideMode.ElideRight, int(x0 + tw - sx)))
        # the bar under the name
        by = y1 + 26
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(QColor(theme.BG3 or "#2b313d"))
        q.drawRoundedRect(QRectF(x0, by, tw, 7), 3.5, 3.5)
        if self.s.chance > 0:
            g = QLinearGradient(x0, 0, x0 + tw, 0)
            g.setColorAt(0, col.darker(135)); g.setColorAt(1, col)
            q.setBrush(g)
            q.drawRoundedRect(QRectF(x0, by, max(7.0, tw * min(1.0, self.s.chance)), 7), 3.5, 3.5)
        # the value, big; the stack size under it
        f3 = theme.ui_font(13); f3.setWeight(f3.Weight.Bold)
        q.setFont(f3); q.setPen(col)
        q.drawText(QRectF(W - right - 4, 4, right - 8, 30), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   pct(self.s.chance))
        if self.stack:
            f4 = theme.mono_font(8.5); q.setFont(f4); q.setPen(QColor(theme.TEXT3))
            q.drawText(QRectF(W - right - 4, 32, right - 8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       self.stack)
        q.end()


class _Pill(QLabel):
    def __init__(self, text, tip=""):
        super().__init__(text)
        self.setObjectName("LtPill")
        self.setToolTip(tip)


class LootTablesView(QWidget):
    """the browser itself (the dialog below, and the Loot page)"""

    def __init__(self, version, table=None, parent=None, structure=None, focus_slots=None,
                 tables=None, compute=None):
        """structure: show only its tables (a toggle shows them all); tables: the exact
        {table: count} of its containers; compute: (seed, mc, x, z, sub) to find them"""
        super().__init__(parent)
        self.structure = structure
        self.counts = dict(tables) if tables else None
        self.scope_on = bool(structure)
        self.focus_items = {getattr(x, "item", None) for x in (focus_slots or []) if x is not None}
        acc = theme.ACCENT or "#7c9cff"
        self.setStyleSheet(
            f"#LtSide {{ background: {theme.BG2}; border: 1px solid {theme.LINE2}; border-radius: 14px; }}"
            f"#LtList {{ background: transparent; border: none; outline: none; font-size: 10pt; }}"
            f"#LtList::item {{ padding: 7px 8px; border-radius: 8px; color: {theme.TEXT}; }}"
            f"#LtList::item:selected {{ background: {acc}; color: white; }}"
            f"#LtList::item:hover:!selected {{ background: {theme.BG3}; }}"
            f"#LtTile {{ background: {theme.BG2}; border: 1px solid {theme.LINE2}; border-radius: 14px; }}"
            f"#LtHead {{ font-size: 17pt; font-weight: 700; }}"
            f"#LtGroup {{ color: {theme.TEXT3}; font-size: 9pt; font-weight: 600; letter-spacing: 1px; }}"
            f"#LtPill {{ background: {theme.BG2}; border: 1px solid {theme.LINE2}; border-radius: 11px;"
            f" padding: 3px 10px; color: {theme.TEXT2}; font-size: 8.5pt; }}"
            f"#LtHint {{ color: {theme.TEXT3}; font-size: 8.5pt; }}"
            f"#LtPanel {{ background: {theme.BG0 or theme.BG1}; border: 1px solid {theme.LINE2}; border-radius: 14px; }}")
        v = QHBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(16)
        side = QFrame(); side.setObjectName("LtSide"); self.side = side
        side.setFixedWidth(250)
        sv = QVBoxLayout(side); sv.setContentsMargins(10, 12, 10, 10); sv.setSpacing(8)
        self.ver = QComboBox()
        vers = list(le.data_versions())[::-1]
        for x in vers:
            self.ver.addItem(f"Java {x}", x)
        self.ver.setCurrentIndex(max(0, vers.index(version) if version in vers else 0))
        self.ver.currentIndexChanged.connect(lambda *_: self._fill_list(self._cur))
        sv.addWidget(self.ver)
        self.find = QLineEdit(); self.find.setPlaceholderText("Find a chest or an item…")
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(lambda *_: self._fill_list(self._cur))
        sv.addWidget(self.find)
        self.scope_bar = None
        if structure:                          # this structure's tables / all of them
            try:
                from . import struct_icons
                slabel = struct_icons.label(structure)
            except Exception:                  # noqa: BLE001
                slabel = structure.replace("_", " ").title()
            from .widgets import Segmented
            self.scope_bar = Segmented([slabel, "All"], slabel)
            self.scope_bar.setToolTip("This structure's chests, or every loot table")
            self.scope_bar.changed.connect(lambda v: self._set_scope(v != "All"))
            sv.addWidget(self.scope_bar)
        self.list = QListWidget(); self.list.setObjectName("LtList")
        self.list.setIconSize(QSize(24, 24))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.currentItemChanged.connect(self._picked)
        sv.addWidget(self.list, 1)
        v.addWidget(side)
        main = QVBoxLayout(); main.setSpacing(12)
        hd = QHBoxLayout(); hd.setSpacing(14); hd.setContentsMargins(4, 2, 4, 0)
        self.h_icon = QLabel(); self.h_icon.setObjectName("LtTile"); self.h_icon.setFixedSize(60, 60)
        self.h_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hd.addWidget(self.h_icon)
        tc = QVBoxLayout(); tc.setSpacing(2)
        self.h_group = QLabel(); self.h_group.setObjectName("LtGroup")
        self.h_title = QLabel(); self.h_title.setObjectName("LtHead")
        tc.addWidget(self.h_group); tc.addWidget(self.h_title)
        self.pills = QHBoxLayout(); self.pills.setSpacing(6)
        tc.addLayout(self.pills)
        hd.addLayout(tc, 1)
        main.addLayout(hd)
        panel = QFrame(); panel.setObjectName("LtPanel")
        pv = QVBoxLayout(panel); pv.setContentsMargins(6, 8, 6, 6); pv.setSpacing(0)
        hint = QLabel("Chance that a chest holds at least one"); hint.setObjectName("LtHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        hint.setContentsMargins(0, 0, 14, 4)
        pv.addWidget(hint)
        self.area = QScrollArea(); self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setStyleSheet("QScrollArea { background: transparent; } "
                                "QScrollArea > QWidget > QWidget { background: transparent; }")
        pv.addWidget(self.area, 1)
        main.addWidget(panel, 1)
        v.addLayout(main, 1)
        if table is None and structure:
            table = STRUCT_TABLE.get(structure)
        self._cur = table
        self._fill_list(table)
        if compute is not None and self.counts is None and structure:
            self._compute(structure, *compute)

    def _compute(self, key, seed, mc, x, z, sub):
        """the structure's containers (helper process) → its exact tables"""
        import structure_loot as sl
        try:
            from .map_engine import loot_pool
            fut = loot_pool().submit(sl.chests, key, seed, mc, x, z, sub)
        except Exception:                                # noqa: BLE001
            fut = None
        if fut is None:
            return
        from PySide6.QtCore import QTimer

        def poll():
            try:
                if not fut.done():
                    QTimer.singleShot(80, poll)
                    return
                res = fut.result()
            except RuntimeError:                         # the window is gone
                return
            except Exception:                            # noqa: BLE001
                return
            try:
                self.counts = tables_of(res) or None
                if self.scope_on:
                    self._fill_list(self._cur)
            except RuntimeError:
                pass
        QTimer.singleShot(50, poll)

    def _set_scope(self, on):
        self.scope_on = bool(on)
        self._fill_list(self._cur)

    def _in_scope(self, tid):
        if not self.scope_on or not self.structure:
            return True
        if self.counts:
            return tid in self.counts
        return any(tid.startswith(p) for p in STRUCT_SCOPE.get(self.structure, ("\0",)))

    def version(self):
        return self.ver.currentData()

    def _fill_list(self, select=None):
        ver = self.version()
        q = self.find.text().strip().lower()
        self.list.blockSignals(True)
        self.list.clear()
        rows = []
        for tid in ls.tables(ver):
            g, n = table_label(tid)
            rows.append((g, n, tid))
        cnt = {}
        for g, n, tid in rows:
            cnt[g] = cnt.get(g, 0) + 1
        # the structures with one table first (under one header), then each group
        rows = [(("", g, ""), g, n, tid) if cnt[g] == 1 else ((g,), g, n, tid) for g, n, tid in rows]
        rows.sort(key=lambda r: (r[0][0] != "", r[0], r[2]))
        rows = [(g if cnt[g] > 1 else "Structures", (n if cnt[g] > 1 else g), tid) for _k, g, n, tid in rows
                if self._in_scope(tid)]
        pick = None
        first = None
        last_g = None
        for g, n, tid in rows:
            label = n if g == "Structures" else f"{g} · {n}"
            if q and q not in label.lower():
                st = ls.table_stats(ver, tid)
                if not st or not any(q in display_name(s, ver).lower() or q in s.item for s in st.items):
                    continue
            if g != last_g:                                  # a header per group
                hd = QListWidgetItem(g.upper())
                hd.setFlags(Qt.ItemFlag.NoItemFlags)
                f = theme.ui_font(7.5); f.setBold(True); f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 1.2)
                hd.setFont(f)
                hd.setForeground(QColor(theme.TEXT3))
                hd.setSizeHint(QSize(10, 34))
                hd.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
                self.list.addItem(hd)
            last_g = g
            k = (self.counts or {}).get(tid, 0) if self.scope_on else 0
            it = QListWidgetItem(n + (f"   ×{k}" if k > 1 else ""))
            if k:
                it.setToolTip(f"{k} container{'s' * (k != 1)} of this structure")
            it.setData(Qt.ItemDataRole.UserRole, tid)
            pm = _icon_pix(table_kind(tid), 22, ver)
            ic = QIcon()
            for mode in (QIcon.Mode.Normal, QIcon.Mode.Selected, QIcon.Mode.Active):
                ic.addPixmap(pm, mode)
            it.setIcon(ic)
            self.list.addItem(it)
            first = first or it
            if tid == select:
                pick = it
        self.list.blockSignals(False)
        it = pick or first
        if it is not None:
            self.list.setCurrentItem(it)
            self._picked(it)
        else:
            self._show(None)

    def _picked(self, it, _prev=None):
        if it is None:
            return
        self._cur = it.data(Qt.ItemDataRole.UserRole)
        self._show(self._cur)

    def _show(self, tid):
        hide_tooltip()
        ver = self.version()
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(2)
        if tid is None:
            self.h_title.setText("—"); self.h_group.setText(""); self.h_pools.setText("")
            self.area.setWidget(w)
            return
        g, n = table_label(tid)
        self.h_group.setText((g if n else "Loot table").upper())
        self.h_title.setText(n or g)
        self.h_icon.setPixmap(_icon_pix(table_kind(tid), 40, ver))
        while self.pills.count():
            it = self.pills.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        st = ls.table_stats(ver, tid)
        if st is None:
            self.area.setWidget(w)
            return
        for i, p in enumerate(st.pools):
            r = f"{p.rolls[0]}–{p.rolls[1]}" if p.rolls[1] > p.rolls[0] else str(p.rolls[0])
            pl = _Pill(f"Pool {i + 1}  ·  {r} roll{'s' * (p.rolls[1] != 1)}",
                       f"Pool {i + 1} picks {r} item{'s' * (p.rolls[1] != 1)} at random"
                       + (f" ({p.empty_share * 100:.0f} % of the picks give nothing)" if p.empty_share else ""))
            self.pills.addWidget(pl)
        self.pills.addStretch(1)
        q = self.find.text().strip().lower()
        for s in st.items:
            hl = bool(q) and (q in display_name(s, ver).lower() or q in s.item)
            lay.addWidget(_Row(s, ver, mark=s.item in self.focus_items, hl=hl))
        lay.addStretch(1)
        self.area.setWidget(w)

    def resizeEvent(self, e):
        self.side.setFixedWidth(250 if self.width() >= 900 else 200)
        super().resizeEvent(e)

    def hideEvent(self, e):
        hide_tooltip()
        super().hideEvent(e)


class LootTableDialog(QDialog):
    def __init__(self, version, table=None, parent=None, structure=None, focus_slots=None,
                 tables=None, compute=None):
        super().__init__(parent)
        self.setWindowTitle("Loot tables")
        from PySide6.QtGui import QGuiApplication
        scr = (parent.screen() if parent is not None else None) or QGuiApplication.primaryScreen()
        av = scr.availableGeometry()
        self.resize(min(1120, int(av.width() * 0.85)), min(880, int(av.height() * 0.88)))
        self.setMinimumSize(560, 420)
        self.setStyleSheet(f"QDialog {{ background: {theme.BG1 or '#1b1f27'}; }}")
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 16, 16, 16)
        self.view = LootTablesView(version, table, self, structure, focus_slots, tables, compute)
        lay.addWidget(self.view)

    def hideEvent(self, e):
        hide_tooltip()
        super().hideEvent(e)
