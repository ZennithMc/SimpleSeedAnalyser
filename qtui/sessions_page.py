"""
qtui/sessions_page.py — the custom-session workspace.

  ┌ Conditions (tree) / editor ┐┌ Map (zones, hits) ───────────┐┌ Results ─────┐
  │ + Add  ✎  ⧉  ✕  ↑ ↓        ││ the selected condition's zone ││ # · seed      │
  │ ▸ Village  r ≤ 1500        ││ drawn live while editing;      ││ (unlimited,   │
  │   └ Outpost  r ≤ 400       ││ a result shows every condition ││  virtual)     │
  │ ▸ OR                        ││ zone + where it was satisfied  ││               │
  └─────────────────────────────┘└───────────────────────────────┘└───────────────┘
  ┌ run bar: mode · start seed · count/∞ · threads · stop after · Test · ▶ ⏸ ■ · progress · engine ┐

The tree replaces cubiomes-viewer's "[NN]" references: children sit under their
parent and are moved by drag & drop; the engine links are recomputed on export.
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRect, QRectF, Qt, QTime, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from .widgets import user_path, remember_path
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QTimeEdit, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableView,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
                               QApplication, QStyle, QStyledItemDelegate)

from . import icons, theme
from .condition_form import ConditionForm, TypePicker
from .map_view import MapView
from .search_runner import SearchRunner
from .session_model import (CONTAINERS, SessionModel, area_text, display_name, new_condition,
                            type_color, type_icon, icon_type)
from .widgets import Segmented, body
from .world import random_seed

def cs_active(conds):
    import custom_search as cs
    return cs.active_conditions(conds)


MODES = {"Incremental": "inc", "48-bit family": "fam48"}
MODE_TIPS = ("Incremental: every seed from the start seed, in order.\n"
             "48-bit family (as in cubiomes-viewer): for sessions with structures. The\n"
             "structure positions only depend on the lower 48 bits of the seed: each\n"
             "48-bit family is pre-tested once (GPU if ticked), then its 65 536 seeds are\n"
             "fully checked. Start / Count are in families (1 family = 65 536 seeds).\n"
             "Without any structure condition, the engine runs Incremental.")


def _dot(color, size=10):
    pm = QPixmap(size * 2, size * 2); pm.setDevicePixelRatio(2); pm.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QPainter
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color)); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(1, 1, size - 2, size - 2); p.end()
    return QIcon(pm)


def fmt_int(n):
    return f"{int(n):,}".replace(",", " ")


def fmt_rate(r):
    if r >= 1e9:
        return f"{r / 1e9:.2f} G/s"
    if r >= 1e6:
        return f"{r / 1e6:.2f} M/s"
    if r >= 1e3:
        return f"{r / 1e3:.1f} k/s"
    return f"{r:.0f} /s"


def fmt_time(s):
    if s is None:
        return "—"
    s = int(s)
    if s >= 86400 * 365:
        return "> 1 year"
    d, s = divmod(s, 86400); h, s = divmod(s, 3600); m, s = divmod(s, 60)
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


# ═════════════════════════════════════════════════════════════════════════════
def _heat(t):
    """0 (cheap) → 1 (the slowest condition): slate blue → teal → amber → red."""
    stops = ((0.0, (74, 98, 140)), (0.25, (46, 160, 150)), (0.55, (222, 176, 60)),
             (0.8, (238, 120, 50)), (1.0, (226, 64, 64)))
    t = max(0.0, min(1.0, t))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if t <= b:
            u = (t - a) / (b - a)
            return QColor(*(int(ca[k] + (cb[k] - ca[k]) * u) for k in range(3)))
    return QColor(*stops[-1][1])


def _short(n):
    """136 000 → '136k', 2 400 000 → '2.4M' (3 significant digits)."""
    n = int(n)
    for div, suf in ((10**12, "T"), (10**9, "G"), (10**6, "M"), (10**3, "k")):
        if n >= div:
            v = n / div
            return (f"{v:.0f}" if v >= 100 else f"{v:.1f}" if v >= 10 else f"{v:.2f}").rstrip("0").rstrip(".") + suf
    return str(n)


class _ProfileDelegate(QStyledItemDelegate):
    """Profiler cell: a bar = share of the search time ('x %' on it), then
    the number of seeds that passed the condition's branch and its rate."""
    def paint(self, p, opt, idx):
        v = idx.data(Qt.ItemDataRole.UserRole)
        if not v:
            return
        share, rate, reached, passed, rel = v
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(opt.rect).adjusted(2, 4, -4, -4)
        bar = QRectF(r.left(), r.top(), 46, r.height())
        p.setPen(Qt.PenStyle.NoPen)
        track = QColor(theme.BG4); p.setBrush(track); p.drawRoundedRect(bar, 3, 3)
        # fill = share of the time RELATIVE to the slowest condition (the
        # slowest fills the bar), colour on a heat ramp: cool → hot
        if share > 0:
            w = bar.width() * max(0.0, min(1.0, rel))
            p.setBrush(_heat(rel))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(1.5, w), bar.height()), 3, 3)
        p.setFont(theme.mono_font(7.5))
        pc = share * 100
        lab = (f"{pc:.0f}%" if pc >= 10 else f"{pc:.1f}%" if pc >= 1 else
               f"{pc:.2f}%" if pc >= 0.01 else ("<.01%" if pc > 0 else "—"))
        # dark outline so the label reads on every fill colour
        p.setPen(QColor(0, 0, 0, 170))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(bar.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, lab)
        p.setPen(QColor("#ffffff"))
        p.drawText(bar, Qt.AlignmentFlag.AlignCenter, lab)
        x = bar.right() + 5
        p.setFont(theme.mono_font(8, ))
        p.setPen(QColor(theme.OK if passed else theme.TEXT3))
        cnt = _short(passed)
        cw = p.fontMetrics().horizontalAdvance(cnt)
        p.drawText(QRectF(x, r.top(), cw + 2, r.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cnt)
        if rate is not None:
            pc = rate * 100
            txt = ("100%" if pc >= 99.995 else f"{pc:.1f}%" if pc >= 10 else
                   f"{pc:.2f}%" if pc >= 0.01 else ("<.01%" if pc > 0 else "0%"))
            p.setFont(theme.mono_font(7))
            p.setPen(QColor(theme.TEXT3))
            p.drawText(QRectF(x + cw + 5, r.top(), r.right() - x - cw - 5, r.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
        p.restore()


class ConditionTree(QTreeWidget):
    structureEdited = Signal(list)          # [(uid, parent_uid), …] in display order
    toggled = Signal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CondTree")
        # column 0 = "#n" gutter (number of the ACTIVE condition, the one used on
        # the map and in "explain"), 1 = the tree itself, 2 = area
        self.setColumnCount(4)                 # 3 = profiler (time share, pass rate)
        self.setTreePosition(1)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setIndentation(18)
        self.setUniformRowHeights(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.header().resizeSection(3, 142)
        self.setHeaderLabels(["#", "Condition", "Area", "Time · passed · rate"])
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.header().setMinimumSectionSize(8)
        self.setItemDelegateForColumn(3, _ProfileDelegate(self))
        self.setColumnHidden(3, True)
        self._by_num = {}
        self._profile = None
        self._filling = False
        self.itemChanged.connect(self._item_changed)

    def fill(self, model: SessionModel, keep_uid=None, expand_all=False):
        self._filling = True
        # every condition is expanded unless the user collapsed it (a set of
        # COLLAPSED uids survives refills, undo / redo and removals)
        collapsed = getattr(self, "_collapsed", set())
        for it in self._all():
            u = it.data(0, Qt.ItemDataRole.UserRole)
            if it.childCount():
                (collapsed.discard if it.isExpanded() else collapsed.add)(u)
        if expand_all:
            collapsed.clear()
        self._collapsed = collapsed
        self.clear()
        items = {}
        num = 0
        self._by_num = {}
        mono = theme.mono_font(8.5)
        for c in model.conds:
            uid = c["_uid"]
            parent = items.get(c.get("_parent", 0))
            it = QTreeWidgetItem(parent if parent is not None else self)
            it.setData(0, Qt.ItemDataRole.UserRole, uid)
            on = model.ancestors_enabled(uid)
            if on:                              # active conditions only: #1, #2… (renumbered
                num += 1                        # when one is disabled), same as the map
                self._by_num[num] = it
                it.setText(0, f"#{num}")
                it.setForeground(0, QColor(type_color(icon_type(c))))
                it.setData(0, Qt.ItemDataRole.UserRole + 1, type_color(icon_type(c)))
                it.setToolTip(0, f"Active condition #{num} — the number shown on the map")
            it.setFont(0, mono)
            it.setTextAlignment(0, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            it.setText(1, display_name(c))
            it.setIcon(1, type_icon(icon_type(c), 16) or _dot(type_color(icon_type(c))))
            it.setText(2, area_text(c))
            it.setToolTip(2, area_text(c))
            it.setForeground(2, QColor(theme.TEXT3))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled
                        | Qt.ItemFlag.ItemIsDropEnabled)
            it.setCheckState(1, Qt.CheckState.Checked if c.get("enabled", True) else Qt.CheckState.Unchecked)
            it.setData(1, Qt.ItemDataRole.UserRole + 3, theme.TEXT if on else theme.TEXT3)
            if not on:
                for col in (1, 2):
                    it.setForeground(col, QColor(theme.TEXT3))
                f = it.font(1); f.setStrikeOut(True); it.setFont(1, f)
            tip = (f"#{num}  " if on else "(inactive)  ") + f"{display_name(c)}\n{area_text(c)}"
            if c.get("type") in CONTAINERS:
                tip += "\nDrop conditions onto it to make them depend on it."
            it.setToolTip(1, tip)
            items[uid] = it
        for uid, it in items.items():
            it.setExpanded(uid not in collapsed)
        # deep trees (many relative conditions): narrower indentation and a
        # capped area column, so the names stay readable (27/09: a 12-level
        # session pushed the names out of the panel)
        depth = max((model.depth(c["_uid"]) for c in model.conds), default=0)
        self.setIndentation(max(10, min(18, 18 - 2 * max(0, depth - 4))))
        self.resizeColumnToContents(2)
        if self.columnWidth(2) > 120:
            self.setColumnWidth(2, 120)
        self.set_profile(self._profile)
        if getattr(self, "_check", None):
            self.set_check(self._check)
        if keep_uid in items:
            self.setCurrentItem(items[keep_uid])
        self._filling = False

    CHECK = {"ok": ("✓", "#2fbf71"), "culprit": ("✗", "#e5484d"), "fail": ("✗", "#d9a03a"),
             "alt": ("✗", "#8a8f99"), "skip": ("·", None)}

    def set_check(self, status):
        """✓ / ✗ per active condition for the checked seed (None = clear)."""
        self._check = status
        was, self._filling = self._filling, True        # itemChanged → not a user toggle
        try:
            self._set_check_items(status)
        finally:
            self._filling = was

    def _set_check_items(self, status):
        for n, it in self._by_num.items():
            v = status.get(n) if status else None
            mark, col = self.CHECK.get(v, ("", None))
            it.setText(0, (f"{mark} " if mark else "") + f"#{n}")
            base = it.data(1, Qt.ItemDataRole.UserRole + 3)
            f = it.font(1); f.setBold(v == "culprit"); it.setFont(1, f)
            it.setForeground(1, QColor("#ff6b6b") if v == "culprit" else QColor(base or theme.TEXT))
            if v == "culprit":
                it.setBackground(1, QColor(229, 72, 77, 46))
                it.setToolTip(0, f"#{n}: the seed FAILS here (tested, not satisfied)")
            elif v == "fail":
                it.setBackground(1, QColor(0, 0, 0, 0))
                it.setToolTip(0, f"#{n}: its own test passed, its branch fails further down")
            elif v == "alt":
                it.setBackground(1, QColor(0, 0, 0, 0))
                it.setToolTip(0, f"#{n}: not satisfied, but another alternative was (the seed matches)")
            elif v == "ok":
                it.setBackground(1, QColor(47, 191, 113, 26))
                it.setToolTip(0, f"#{n}: satisfied for this seed")
            else:
                it.setBackground(1, QColor(0, 0, 0, 0))
                it.setToolTip(0, f"#{n}: not reached for this seed" if v == "skip" else
                              f"Active condition #{n} — the number shown on the map")
            if col:
                it.setForeground(0, QColor(col))
            else:
                c = it.data(0, Qt.ItemDataRole.UserRole + 1)
                if c:
                    it.setForeground(0, QColor(c))

    def set_profile(self, prof):
        """prof = (rows, total) with rows[i] = [reached, passed, ticks] for the
        active condition #i+1, or None."""
        self._profile = prof
        was, self._filling = self._filling, True
        try:
            self._set_profile_items(prof)
        finally:
            self._filling = was
        self.viewport().update()

    def _set_profile_items(self, prof):
        rows = prof[0] if prof else None
        own = sum(r[2] for r in rows) if rows else 0
        top = max((r[2] for r in rows), default=0) if rows else 0
        for n, it in self._by_num.items():
            r = rows[n - 1] if rows and n - 1 < len(rows) else None
            if r is None:
                it.setData(3, Qt.ItemDataRole.UserRole, None)
                it.setToolTip(3, "")
                continue
            reached, passed, ticks = r
            share = ticks / own if own else 0.0
            rate = passed / reached if reached else None
            it.setData(3, Qt.ItemDataRole.UserRole, (share, rate, reached, passed,
                                                     ticks / top if top else 0.0))
            it.setToolTip(3, (
                f"#{n} {it.text(1)}\n"
                f"Reached by {fmt_int(reached)} seeds · its branch passed for {fmt_int(passed)}"
                + (f" ({rate * 100:.2f} %)" if rate is not None else "") + "\n"
                + (f"{share * 100:.2f} % of the search time (its own test; the conditions under it "
                   f"are counted on their own rows)" if ticks else
                   "Time: — (reached too rarely to be measured: the time is sampled on 1 seed in 32)")))
        self.viewport().update()

    def _all(self):
        out = []

        def walk(it):
            for i in range(it.childCount()):
                out.append(it.child(i)); walk(it.child(i))
        for i in range(self.topLevelItemCount()):
            out.append(self.topLevelItem(i)); walk(self.topLevelItem(i))
        return out

    def selected_uids(self):
        return [it.data(0, Qt.ItemDataRole.UserRole) for it in self.selectedItems()]

    def current_uid(self):
        it = self.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else 0

    def _item_changed(self, it, col):
        if self._filling or col != 1:
            return
        self.toggled.emit(it.data(0, Qt.ItemDataRole.UserRole),
                          it.checkState(1) == Qt.CheckState.Checked)

    def dropEvent(self, e):
        super().dropEvent(e)
        order = []
        for it in self._all():
            p = it.parent()
            order.append((it.data(0, Qt.ItemDataRole.UserRole),
                          p.data(0, Qt.ItemDataRole.UserRole) if p else 0))
        self.structureEdited.emit(order)


# ═════════════════════════════════════════════════════════════════════════════
class ResultsModel(QAbstractTableModel):
    HEAD = ("#", "Seed  ·  location", "Location")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []                                # (seed, details)
        self.loc = {}                                 # seed -> (x, z) | "…" (being located)
        self._mono = theme.mono_font(9.5)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def set_location(self, seed, loc):
        self.loc[seed] = loc
        r = self._row_of().get(seed)
        if r is not None:
            self.dataChanged.emit(self.index(r, 1), self.index(r, 2))

    def _row_of(self):
        # kept up to date by add() (before: rebuilt for EVERY row each time the
        # count changed → ~1 s per click with 1 M results, worse while searching)
        ro = getattr(self, "_rows_idx", None)
        if ro is None:
            ro = self._rows_idx = {}
            for i, (s, _d) in enumerate(self.rows):
                ro.setdefault(s, i)
        return ro

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        seed = self.rows[idx.row()][0]
        if role == Qt.ItemDataRole.DisplayRole:
            if idx.column() == 2:
                loc = self.loc.get(seed)
                if loc is None:
                    return ""
                return loc if isinstance(loc, str) else f"{loc[0]}, {loc[1]}"
            return str(idx.row() + 1) if idx.column() == 0 else str(seed)
        if role == Qt.ItemDataRole.FontRole and idx.column() >= 1:
            return self._mono
        if role == Qt.ItemDataRole.ForegroundRole and idx.column() == 2:
            return QColor(theme.ACCENT)
        if role == Qt.ItemDataRole.ForegroundRole and idx.column() == 0:
            return QColor(theme.TEXT3)
        if role == Qt.ItemDataRole.TextAlignmentRole and idx.column() == 0:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole and idx.column() == 2:
            loc = self.loc.get(seed)
            return (f"X {loc[0]}   Z {loc[1]}\n(right-click: copy /tp, centre the map)"
                    if isinstance(loc, tuple) else "Click the result to locate it")
        if role == Qt.ItemDataRole.UserRole:              # the two-line cell (delegate)
            return self.loc.get(seed)
        if role == Qt.ItemDataRole.ToolTipRole:
            u = seed & 0xFFFFFFFFFFFFFFFF
            loc = self.loc.get(seed)
            where = (f"X {loc[0]}   Z {loc[1]}  (right-click: copy /tp, centre the map)\n"
                     if isinstance(loc, tuple) else "")
            return f"{seed}\n{where}lower 48 bits: {u & 0xFFFFFFFFFFFF:#014x}\nupper 16 bits: {u >> 48:#06x}"
        return None

    def headerData(self, section, orient, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orient == Qt.Orientation.Horizontal:
            return self.HEAD[section]
        return None

    def add(self, batch):
        if not batch:
            return
        n = len(self.rows)
        self.beginInsertRows(QModelIndex(), n, n + len(batch) - 1)
        self.rows.extend(batch)
        ro = getattr(self, "_rows_idx", None)
        if ro is not None:
            for i, (s, _d) in enumerate(batch):
                ro.setdefault(s, n + i)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel(); self.rows = []; self.loc = {}; self._rows_idx = None
        self.endResetModel()


class _SeedCellDelegate(QStyledItemDelegate):
    """Seed on the first line, where the conditions are satisfied under it
    (the old third column was cut off in a narrow panel)."""
    def paint(self, p, opt, idx):
        self.initStyleOption(opt, idx)
        opt.text = ""
        st = opt.widget.style() if opt.widget else QApplication.style()
        st.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, p, opt.widget)
        seed = idx.data(Qt.ItemDataRole.DisplayRole)
        loc = idx.data(Qt.ItemDataRole.UserRole)
        r = QRect(opt.rect).adjusted(6, 3, -4, -3)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        p.save()
        p.setFont(theme.mono_font(9.5))
        p.setPen(QColor(theme.TEXT))
        fm = p.fontMetrics()
        top = QRect(r.left(), r.top(), r.width(), r.height() // 2 + 1)
        p.drawText(top, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   fm.elidedText(seed, Qt.TextElideMode.ElideMiddle, r.width()))
        bot = QRect(r.left(), r.top() + r.height() // 2, r.width(), r.height() // 2)
        p.setFont(theme.mono_font(8.5))
        if isinstance(loc, tuple):
            p.setPen(QColor(theme.TEXT if sel else theme.ACCENT))
            txt = f"X {loc[0]}   Z {loc[1]}"
        else:
            p.setPen(QColor(theme.TEXT3))
            txt = loc if isinstance(loc, str) else ""
        p.drawText(bot, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), txt)
        p.restore()


class _Explain(QObject):
    done = Signal(object, object)   # 64-bit seeds: never Signal(int) (32-bit)                        # seed, result


# ═════════════════════════════════════════════════════════════════════════════
class SessionsPage(QWidget):
    status = Signal(str)
    openMap = Signal()                               # "open this seed on the Map page"

    def __init__(self, world, parent=None, link=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.world = world
        self.model = SessionModel(self)
        self.runner = SearchRunner(self)
        self.seed_list = None
        self._editing_uid = None                     # None = no form; 0 = new condition
        self._new_parent = 0
        self._explain = _Explain(self)
        self._explain.done.connect(self._on_explained)
        self._explained = None

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 2, 10, 4); outer.setSpacing(8)
        split = QSplitter(Qt.Orientation.Horizontal); split.setHandleWidth(10)
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)

        # ── left: conditions / editor ──────────────────────────────────────
        left = QFrame(); left.setObjectName("SidePanel"); left.setMinimumWidth(330)
        self.left_stack = QStackedWidget(left)
        ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0); ll.addWidget(self.left_stack)
        self.left_stack.addWidget(self._build_conditions())
        self.picker = TypePicker(); self.picker.chosen.connect(self._type_chosen)
        self.picker.cancelled.connect(self._close_form)
        self.left_stack.addWidget(self.picker)
        self.form = ConditionForm()
        self.form.saved.connect(self._form_saved); self.form.cancelled.connect(self._close_form)
        self.form.preview.connect(self._form_preview)
        self.form.pickRequested.connect(self._pick)
        self.form.changeTypeRequested.connect(lambda: self.left_stack.setCurrentWidget(self.picker))
        self.left_stack.addWidget(self.form)
        split.addWidget(left)

        # ── centre: map ────────────────────────────────────────────────────
        self.map = MapView(world, link=link)
        self.map.picked.connect(self._picked)
        self.map.pickCancelled.connect(lambda: self.status.emit(""))
        split.addWidget(self.map)
        self._build_hits_button()

        # ── right: results ─────────────────────────────────────────────────
        split.addWidget(self._build_results())
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setStretchFactor(2, 0)
        split.setSizes([380, 800, 260])
        self._split = split
        # the left panel grows to its content (condition editor, type picker):
        # nothing is cut, no horizontal scroll bar (27/09)
        from PySide6.QtCore import QTimer as _QT
        self.left_stack.currentChanged.connect(lambda _i: _QT.singleShot(0, self._fit_left))

        outer.addWidget(self._build_runbar())

        # wiring
        self.model.changed.connect(self._model_changed)
        self.runner.resultsReady.connect(self._results_in)
        self.runner.progress.connect(self._progress)
        self.world.changed.connect(self._world_seed_changed)
        self.runner.engine.connect(self._engine)
        self.runner.stateChanged.connect(self._run_state)
        self.runner.message.connect(self._run_message)
        self.runner.failed.connect(self._run_failed)
        # the end-of-search summary (seeds searched, time…) stays until the
        # session or a search parameter changes, or a new search starts
        self._run_sig = None
        self.model.changed.connect(self._model_maybe_clear)
        self.mode.changed.connect(self._clear_summary)
        self.start_seed.textEdited.connect(self._clear_summary)
        self.threads.valueChanged.connect(self._clear_summary)
        self.rule_kind.currentIndexChanged.connect(self._clear_summary)
        self.rule_seeds.textEdited.connect(self._clear_summary)
        self.rule_results.valueChanged.connect(self._clear_summary)
        self.rule_time.timeChanged.connect(self._clear_summary)
        self.list_btn.clicked.connect(self._clear_summary)
        for key, fn in (("Ctrl+C", self._copy), ("Ctrl+V", self._paste), ("Delete", self._remove),
                        ("Ctrl+D", self._duplicate), ("Ctrl+N", self._add_root),
                        ("Ctrl+S", self._save), ("Ctrl+O", self._load),
                        ("Ctrl+L", self.open_library), ("Ctrl+Z", self._undo),
                        ("Ctrl+Y", self._redo), ("Ctrl+Shift+Z", self._redo),
                        ("Ctrl+T", self._check_world_seed)):
            sc = QShortcut(QKeySequence(key), self.tree if key in ("Ctrl+C", "Ctrl+V", "Delete", "Ctrl+D")
                           else self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self._model_changed()
        self._run_state("idle")

    def showEvent(self, e):
        super().showEvent(e)
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(0, self._fit_left)

    def _fit_left(self):
        w = self.left_stack.currentWidget()
        if w is None or not self.isVisible():
            return
        need = w.minimumSizeHint().width()
        from PySide6.QtWidgets import QScrollArea as _QSA
        sc = getattr(w, "scroll", None)       # the form's scroll area — but on the other
        if not isinstance(sc, _QSA):          # pages `scroll` is QWidget.scroll (a method)
            sc = w if isinstance(w, _QSA) else None
        if sc is not None and sc.widget() is not None:
            inner = sc.widget()
            need = max(need, inner.minimumSizeHint().width()
                       + sc.verticalScrollBar().sizeHint().width() + 4)
        need += 2
        sizes = self._split.sizes()
        if sizes and sizes[0] < need:
            extra = need - sizes[0]
            sizes[0] = need
            sizes[1] = max(320, sizes[1] - extra)
            self._split.setSizes(sizes)

    # ── builders ────────────────────────────────────────────────────────────
    def _tb(self, icon, tip, fn, text=None):
        b = QToolButton(); icons.bind(b, icon, 17); b.setToolTip(tip); b.clicked.connect(fn)
        b.setObjectName("PanelTool")
        if text:
            b.setText(" " + text); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        return b

    def _build_conditions(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(14, 12, 12, 10); v.setSpacing(8)
        top = QHBoxLayout(); top.setSpacing(4)
        col = QVBoxLayout(); col.setSpacing(0)
        self.sess_title = QLabel("Untitled session"); self.sess_title.setObjectName("PanelTitle")
        self.sess_sub = QLabel(""); self.sess_sub.setObjectName("Muted")
        col.addWidget(self.sess_title); col.addWidget(self.sess_sub)
        self.sess_title.setToolTip("Double-click to rename the session")
        self.sess_title.mouseDoubleClickEvent = lambda _e: self._rename_session()
        top.addLayout(col, 1)
        self.b_undo = self._tb("undo", "Undo  (Ctrl+Z)", self._undo)
        self.b_redo = self._tb("redo", "Redo  (Ctrl+Y)", self._redo)
        top.addWidget(self.b_undo); top.addWidget(self.b_redo); top.addSpacing(6)
        top.addWidget(self._tb("library", "Session library — your saved sessions  (Ctrl+L)",
                               self.open_library))
        top.addWidget(self._tb("folder", "Open a .session file  (Ctrl+O)", self._load))
        top.addWidget(self._tb("save", "Save  (Ctrl+S)\nA new session goes to the library; "
                                       "a library session or a file is updated in place.", self._save))
        more = QToolButton(); icons.bind(more, "chevron-down", 17); more.setObjectName("PanelTool")
        more.setToolTip("More"); more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(more)
        m.addAction("Session library…\tCtrl+L", self.open_library)
        m.addAction("Save to library as new…", self._save_library_new)
        m.addAction("Rename the session…", self._rename_session)
        m.addSeparator()
        m.addAction("Save as file…", self._save_as)
        m.addAction("New empty session", self._new_session)
        rec = m.addMenu("Recent sessions")
        rec.aboutToShow.connect(lambda: self._fill_recent(rec))
        m.addSeparator()
        m.addAction("Copy conditions\tCtrl+C", self._copy)
        m.addAction("Paste conditions\tCtrl+V", self._paste)
        more.setMenu(m)
        top.addWidget(more)
        v.addLayout(top)

        bar = QHBoxLayout(); bar.setSpacing(2)
        add = QPushButton("  Add"); icons.bind(add, "plus", 16, "#ffffff", "#ffffff")
        add.setMinimumWidth(88)
        add.setProperty("kind", "primary"); add.setToolTip("Add a condition  (Ctrl+N)")
        add_menu = QMenu(add)
        add_menu.addAction("At the world origin", self._add_root)
        self.act_add_child = add_menu.addAction("Depending on the selected condition", self._add_child)
        add.setMenu(add_menu)
        bar.addWidget(add)
        bar.addSpacing(6)
        self.b_edit = self._tb("edit", "Edit  (double-click)", self._edit_current)
        self.b_dup = self._tb("copy", "Duplicate with its dependent conditions  (Ctrl+D)", self._duplicate)
        self.b_up = self._tb("chevron-up", "Move up", lambda: self._move(-1))
        self.b_down = self._tb("chevron-down", "Move down", lambda: self._move(1))
        self.b_del = self._tb("trash", "Delete (with its dependent conditions)  (Del)", self._remove)
        for b in (self.b_edit, self.b_dup, self.b_up, self.b_down, self.b_del):
            bar.addWidget(b)
        bar.addStretch(1)
        self.b_stats = self._tb("analyze", "Stats: share of the search time and pass rate of "
                                           "every condition (shown after a search)", self._toggle_stats)
        self.b_stats.setCheckable(True)
        from PySide6.QtCore import QSettings
        self.b_stats.setChecked(str(QSettings("SSA", "SimpleSeedAnalyser").value(
            "sessions/stats", "true")).lower() == "true")
        bar.addWidget(self.b_stats)
        # engine of this session: Auto / analysed plan / as set / CPU only
        # default: the session as set (its own GPU ticks) — no analysis at Start
        # unless the user chooses Auto in the Engine window
        self.engine_mode, self.engine_plan, self._engine_plan_sig = "as_set", None, None
        self.b_engine = QToolButton(); icons.bind(self.b_engine, "gpu", 17)
        self.b_engine.setObjectName("PanelTool")
        self.b_engine.setToolTip("Engine: Auto\nCPU or GPU for this session: analyse, see why, choose.")
        self.b_engine.clicked.connect(self._open_engine)
        bar.addWidget(self.b_engine)
        v.addLayout(bar)

        self.tree = ConditionTree()
        self.tree.itemDoubleClicked.connect(lambda *_: self._edit_current())
        self.tree.currentItemChanged.connect(lambda *_: self._selection_changed())
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.structureEdited.connect(self._tree_restructured)
        self.tree.toggled.connect(lambda uid, on: self.model.set_enabled(uid, on))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        v.addWidget(self.tree, 1)
        self.empty_hint = body("No condition yet.\nAdd one, open a .session, or paste conditions "
                               "copied from cubiomes-viewer (Ctrl+V).", muted=True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.empty_hint)
        self.tree_hint = body("Drag a condition onto another one to make it depend on it "
                              "(its area is then centred on that condition's position).",
                              muted=True, small=True)
        v.addWidget(self.tree_hint)
        return w

    def _build_results(self):
        right = QFrame(); right.setObjectName("SidePanel"); right.setMinimumWidth(230)
        v = QVBoxLayout(right); v.setContentsMargins(14, 12, 12, 10); v.setSpacing(8)
        top = QHBoxLayout()
        t = QLabel("Results"); t.setObjectName("PanelTitle"); top.addWidget(t)
        top.addStretch(1)
        self.res_count = QLabel("0"); self.res_count.setObjectName("Chip"); self.res_count.setProperty("state", "off")
        top.addWidget(self.res_count)
        ck = QToolButton(); icons.bind(ck, "target", 17); ck.setObjectName("PanelTool")
        ck.setToolTip("Check the seed of the top bar against the conditions  (Ctrl+T)\n"
                      "Each condition gets ✓ (satisfied) or ✗ (where the seed fails).")
        ck.clicked.connect(self._check_world_seed)
        top.addWidget(ck)
        ex = QToolButton(); icons.bind(ex, "save", 17); ex.setObjectName("PanelTool")
        ex.setToolTip("Export the results (TXT / CSV)"); ex.clicked.connect(self._export)
        top.addWidget(ex)
        cl = QToolButton(); icons.bind(cl, "trash", 17); cl.setObjectName("PanelTool")
        cl.setToolTip("Clear the results"); cl.clicked.connect(self._clear_results)
        top.addWidget(cl)
        v.addLayout(top)
        self.results = ResultsModel(self)
        self.table = QTableView(); self.table.setObjectName("Results")
        self.table.setModel(self.results)
        _cp = QShortcut(QKeySequence.StandardKey.Copy, self.table)   # the seeds, not the "#" cell
        _cp.setContext(Qt.ShortcutContext.WidgetShortcut)
        _cp.activated.connect(lambda: QGuiApplication.clipboard().setText("\n".join(
            str(self.results.rows[r][0]) for r in
            sorted({i.row() for i in self.table.selectionModel().selectedRows()}))))
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setItemDelegateForColumn(1, _SeedCellDelegate(self.table))
        self.table.setColumnHidden(2, True)          # shown under the seed (two-line cell)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.selectionModel().currentRowChanged.connect(self._result_selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._results_menu)
        v.addWidget(self.table, 1)
        self.res_hint = body("Click a result: the seed is loaded and the map shows where "
                             "each condition is satisfied.", muted=True, small=True)
        v.addWidget(self.res_hint)
        return right

    def _build_runbar(self):
        bar = QFrame(); bar.setObjectName("RunBar")
        v = QVBoxLayout(bar); v.setContentsMargins(14, 10, 14, 10); v.setSpacing(8)
        row = QHBoxLayout(); row.setSpacing(10)

        def labeled(text, w, tip=None):
            c = QVBoxLayout(); c.setSpacing(2)
            lab = QLabel(text.upper()); lab.setObjectName("FieldLabel"); c.addWidget(lab); c.addWidget(w)
            if tip:
                w.setToolTip(tip); lab.setToolTip(tip)
            row.addLayout(c)

        self.mode = Segmented(list(MODES), "Incremental")
        for b in self.mode.buttons.values():
            b.setToolTip(MODE_TIPS)
        labeled("Mode", self.mode)
        self.start_seed = QLineEdit("0"); self.start_seed.setFixedWidth(190)
        self.start_seed.setFont(theme.mono_font(9.5))
        sw = QWidget(); sl = QHBoxLayout(sw); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(2)
        sl.addWidget(self.start_seed)
        dice = QToolButton(); icons.bind(dice, "dice", 16); dice.setToolTip("Random start")
        dice.clicked.connect(lambda: self.start_seed.setText(str(random_seed() >> 1)))
        sl.addWidget(dice)
        labeled("Start", sw, "First seed (Incremental) or first 48-bit value (other modes).")
        self.threads = QSpinBox(); self.threads.setRange(1, os.cpu_count() or 4)
        self.threads.setValue(max(1, (os.cpu_count() or 4) // 2))
        labeled("Threads", self.threads, "CPU threads. Can be changed while paused.")
        # stop rule: never / N seeds searched / N results / a duration
        self.rule_kind = QComboBox()
        for text, key in (("Never", "never"), ("Seeds searched", "seeds"),
                          ("Results found", "results"), ("Time", "time")):
            self.rule_kind.addItem(text, key)
        self.rule_seeds = QLineEdit("1G"); self.rule_seeds.setFixedWidth(110)
        self.rule_seeds.setFont(theme.mono_font(9.5))
        self.rule_seeds.setToolTip("Number of seeds to search: 500000, 250M, 1G, 2^40, 1e12…\n"
                                   "In 48-bit family mode it is rounded up to whole families "
                                   "(65 536 seeds).")
        self.rule_results = QSpinBox(); self.rule_results.setRange(1, 100_000_000)
        self.rule_results.setValue(100); self.rule_results.setFixedWidth(110)
        self.rule_results.setToolTip("Pause when this many results are found (Resume to continue).")
        self.rule_time = QTimeEdit(); self.rule_time.setDisplayFormat("H 'h' mm 'min'")
        self.rule_time.setTime(QTime(1, 0)); self.rule_time.setFixedWidth(110)
        self.rule_time.setToolTip("Pause after this running time (pauses don't count). "
                                  "Resume continues exactly where it stopped.")
        self.rule_stack = QStackedWidget(); self.rule_stack.setFixedWidth(112)
        blank = QLabel(""); self.rule_stack.addWidget(blank)
        for wd in (self.rule_seeds, self.rule_results, self.rule_time):
            self.rule_stack.addWidget(wd)
        self.rule_kind.currentIndexChanged.connect(self.rule_stack.setCurrentIndex)
        rw = QWidget(); rl = QHBoxLayout(rw); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)
        rl.addWidget(self.rule_kind); rl.addWidget(self.rule_stack)
        labeled("Stop after", rw, "When the search stops by itself. Seeds: the search ends there.\n"
                                  "Results / Time: the search PAUSES — Resume continues from the "
                                  "same point (raise the limit first).\nCan be changed while paused.")
        self.list_btn = QPushButton("Seed list…"); self.list_btn.setProperty("kind", "ghost")
        self.list_btn.setToolTip("Test only the seeds of a file (.txt / .seeds) instead of a range.")
        self.list_btn.clicked.connect(self._seed_list)
        labeled("Source", self.list_btn)
        row.addStretch(1)
        self.b_start = QPushButton("  Start"); self.b_start.setProperty("kind", "primary")
        icons.bind(self.b_start, "play", 16, "#ffffff", "#ffffff")
        self.b_start.clicked.connect(self._start_or_resume)
        self.b_pause = QPushButton("  Pause"); icons.bind(self.b_pause, "pause", 16)
        self.b_pause.clicked.connect(self.runner.pause)
        self.b_stop = QPushButton("  Stop"); icons.bind(self.b_stop, "stop", 16)
        self.b_stop.setProperty("kind", "danger"); self.b_stop.clicked.connect(self.runner.stop)
        for b in (self.b_start, self.b_pause, self.b_stop):
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignBottom)
        v.addLayout(row)

        prow = QHBoxLayout(); prow.setSpacing(12)
        self.prog = QProgressBar(); self.prog.setRange(0, 1000); self.prog.setTextVisible(False)
        self.prog.setFixedHeight(6)
        prow.addWidget(self.prog, 1)
        self.stat = QLabel("Idle"); self.stat.setObjectName("Muted"); self.stat.setFont(theme.mono_font(9))
        prow.addWidget(self.stat)
        self.engine_chip = QLabel("—"); self.engine_chip.setObjectName("Chip")
        self.engine_chip.setProperty("state", "off")
        prow.addWidget(self.engine_chip)
        v.addLayout(prow)
        return bar

    # ── conditions ──────────────────────────────────────────────────────────
    def _mc(self):
        return int(self.world.mc)

    def _model_changed(self):
        self.tree._check = None                      # ✓/✗ were for the old conditions
        if hasattr(self, "b_undo"):
            self._sync_undo()
        if hasattr(self, "b_stats"):
            self._show_profile_later()
        cur = self.tree.current_uid()
        self.tree.fill(self.model, keep_uid=cur)
        n = len(self.model.conds)
        self.empty_hint.setVisible(n == 0)
        self.tree.setVisible(n > 0)
        self.tree_hint.setVisible(n > 0)
        self.sess_title.setText(self.model.name + (" •" if self.model.dirty else ""))
        active = len([c for c in self.model.conds if self.model.ancestors_enabled(c["_uid"])])
        self.sess_sub.setText(f"{n} condition{'s' if n != 1 else ''}" +
                              (f" · {n - active} disabled" if active != n else ""))
        self._selection_changed()

    # ── profiler ───────────────────────────────────────────────────────────
    def _show_profile_later(self):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._show_profile)

    def _toggle_stats(self):
        from PySide6.QtCore import QSettings
        QSettings("SSA", "SimpleSeedAnalyser").setValue(
            "sessions/stats", "true" if self.b_stats.isChecked() else "false")
        self._show_profile()

    def _show_profile(self):
        prof = self.runner.profile()
        valid = prof is not None and getattr(self, "_prof_sig", None) == self._sig(self.model.export())
        on = bool(valid and self.b_stats.isChecked())
        self.tree.set_profile(prof if valid else None)
        self.tree.setColumnHidden(3, not on)
        self.tree.setColumnHidden(2, on)             # the area comes back with Stats off
        self.tree.setHeaderHidden(not on)            # "Time · passed · rate" titles the stats
        self.b_stats.setEnabled(bool(valid))
        base = ("Stats: share of the search time and pass rate of every condition "
                "(shown after a search)")
        if valid:
            why = ""
        elif prof is not None:
            why = "The conditions changed since the last search: run it again to get stats."
        else:
            modes = [m for m in getattr(self.runner, "_engine_modes", []) if isinstance(m, int)]
            if modes and modes[-1] == 3:
                why = ("No stats for this search: it ran on the GPU pre-filter, and the "
                       "per-condition timing comes from the C engine (CPU searches).")
            else:
                why = "No stats yet: they appear after a search (C engine)."
        self.b_stats.setToolTip(base + ("\n\n" + why if why else ""))

    def _undo(self):
        if self.left_stack.currentWidget() is self.form:
            return                                 # the form has its own fields
        what = self.model.undo()
        if what:
            self.status.emit(f"Undone: {what}   ·   Ctrl+Y to redo")

    def _redo(self):
        if self.left_stack.currentWidget() is self.form:
            return
        what = self.model.redo()
        if what:
            self.status.emit(f"Redone: {what}")

    def _sync_undo(self):
        m = self.model
        self.b_undo.setEnabled(m.can_undo()); self.b_redo.setEnabled(m.can_redo())
        self.b_undo.setToolTip("Undo" + (f": {m.undo_text()}" if m.can_undo() else "") + "  (Ctrl+Z)")
        self.b_redo.setToolTip("Redo" + (f": {m.redo_text()}" if m.can_redo() else "") + "  (Ctrl+Y)")

    def _selection_changed(self):
        uid = self.tree.current_uid()
        has = bool(uid)
        for b in (self.b_edit, self.b_dup, self.b_up, self.b_down, self.b_del):
            b.setEnabled(has)
        self.act_add_child.setEnabled(has)
        if self._editing_uid is None:
            self._show_zones()

    def _tree_restructured(self, order):
        pos = {u: i for i, (u, _p) in enumerate(order)}
        for c in self.model.conds:
            u = c["_uid"]
            if u in pos:
                c["_parent"] = order[pos[u]][1]
        self.model.conds.sort(key=lambda c: pos.get(c["_uid"], 1 << 30))
        self.model._touch("Drag & drop")

    def _tree_menu(self, pt):
        uid = self.tree.current_uid()
        m = QMenu(self)
        if uid:
            m.addAction("Edit", self._edit_current)
            m.addAction("Add a dependent condition", self._add_child)
            m.addAction("Duplicate", self._duplicate)
            c = self.model.get(uid)
            m.addAction("Disable" if c.get("enabled", True) else "Enable",
                        lambda: self.model.set_enabled(uid, not c.get("enabled", True)))
            if c.get("_parent", 0):
                m.addAction("Move to the world origin (no parent)", lambda: self.model.reparent(uid, 0))
            m.addSeparator()
            m.addAction("Copy", self._copy)
        m.addAction("Paste", self._paste)
        if uid:
            m.addSeparator()
            m.addAction("Delete", self._remove)
        m.exec(self.tree.viewport().mapToGlobal(pt))

    def _add_root(self):
        self._new_parent = 0
        self._editing_uid = 0
        self.left_stack.setCurrentWidget(self.picker)

    def _add_child(self):
        uid = self.tree.current_uid()
        self._new_parent = uid or 0
        self._editing_uid = 0
        self.left_stack.setCurrentWidget(self.picker)

    def _type_chosen(self, t):
        if self._editing_uid:                          # change type of an existing condition
            old = self.model.export(only={self._editing_uid})[0]
            c = new_condition(t)
            for k in ("label", "enabled", "area_mode", "radial", "rmax", "square_side",
                      "x1", "z1", "x2", "z2"):
                if k in old and c.get("area_mode") is not None and t not in ("logic_or", "logic_not"):
                    c[k] = old[k]
            parent = self.model.get(self._editing_uid).get("_parent", 0)
            self.form.edit(c, self._parent_label(parent), self._mc(), is_new=False)
        else:
            self.form.edit(new_condition(t), self._parent_label(self._new_parent), self._mc(),
                           is_new=True)
        self.left_stack.setCurrentWidget(self.form)
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(0, self._fit_left)

    def _parent_label(self, parent_uid):
        if not parent_uid:
            return None
        p = self.model.get(parent_uid)
        return display_name(p) if p else None

    def _edit_current(self):
        uid = self.tree.current_uid()
        if not uid:
            return
        c = self.model.get(uid)
        self._editing_uid = uid
        self.form.edit(self.model.export(only={uid})[0], self._parent_label(c.get("_parent", 0)),
                       self._mc())
        self.left_stack.setCurrentWidget(self.form)
        from PySide6.QtCore import QTimer as _QT
        _QT.singleShot(0, self._fit_left)

    def _form_saved(self, cond):
        if self._editing_uid:
            self.model.update(self._editing_uid, cond)
            uid = self._editing_uid
        else:
            uid = self.model.add(cond, parent=self._new_parent)
        self._close_form()
        self.tree.fill(self.model, keep_uid=uid)
        self._selection_changed()

    def _close_form(self):
        self._editing_uid = None
        self.map.cancel_pick()
        self.left_stack.setCurrentIndex(0)
        self._show_zones()

    def _duplicate(self):
        uid = self.tree.current_uid()
        if uid:
            new = self.model.duplicate(uid)
            self.tree.fill(self.model, keep_uid=new)

    def _move(self, d):
        uid = self.tree.current_uid()
        if uid:
            self.model.move(uid, d)
            self.tree.fill(self.model, keep_uid=uid)

    def _remove(self):
        uids = self.tree.selected_uids() or ([self.tree.current_uid()] if self.tree.current_uid() else [])
        if not uids:
            return
        n_sub = sum(len(self.model.subtree(u)) for u in uids)
        if n_sub:
            r = QMessageBox.question(self, "Delete", f"Delete {len(uids)} condition(s) and the "
                                     f"{n_sub} condition(s) that depend on them?")
            if r != QMessageBox.StandardButton.Yes:
                return
        self.model.remove(uids)

    def _copy(self):
        uids = self.tree.selected_uids()
        if not uids:
            return
        QGuiApplication.clipboard().setText(self.model.copy_text(uids))
        self.status.emit(f"{len(uids)} condition(s) copied — paste them in SSA or cubiomes-viewer.")

    def _paste(self):
        txt = QGuiApplication.clipboard().text()
        n, unsupported = self.model.paste_text(txt)
        if n:
            self.status.emit(f"{n} condition(s) pasted.")
        if unsupported:
            QMessageBox.information(self, "Paste", "Some conditions were skipped:\n\n• " +
                                    "\n• ".join(unsupported[:10]))
        elif not n:
            self.status.emit("Nothing to paste (copy conditions in SSA or cubiomes-viewer first).")

    # ── zones on the map ────────────────────────────────────────────────────
    def _zone_of(self, c, anchor, label=None, active=False, color=None):
        t = c.get("type", "")
        if t in ("scale_to_nether", "scale_to_overworld", "logic_or", "logic_not", "crunchiness"):
            return None
        ax, az = anchor
        if t in ("height", "precise_y"):
            if c.get("relative"):
                ax, az = ax + int(c.get("at_x", 0)), az + int(c.get("at_z", 0))
            else:
                ax, az = int(c.get("at_x", 0)), int(c.get("at_z", 0))
        rmax = int(c.get("rmax", 0) or 0)
        col = color or type_color(t)
        lab = label if label is not None else display_name(c)
        if rmax > 0:
            return dict(shape="circle", cx=ax, cz=az, r=rmax - 1, color=col, label=lab, active=active)
        if abs(int(c.get("x2", 0)) - int(c.get("x1", 0))) >= 20_000_000:
            return None
        return dict(shape="rect", x1=ax + int(c.get("x1", 0)), z1=az + int(c.get("z1", 0)),
                    x2=ax + int(c.get("x2", 0)), z2=az + int(c.get("z2", 0)),
                    color=col, label=lab, active=active)

    # ── what a clicked result shows on the map: Off / Simple / Detailed ──────
    HIT_MODES = ("Off", "Simple", "Detailed")

    def _build_hits_button(self):
        """A labelled switch in the map's top-right corner (27/09: the old icon
        button was not found). Off = nothing; Simple = one small '#n' badge per
        satisfied condition (the map stays readable); Detailed = zones, hit
        markers with name + coordinates (labels never overlap) and a legend of
        every active condition. H cycles the modes."""
        from PySide6.QtCore import QEvent, QObject, QSettings
        from PySide6.QtGui import QKeySequence, QShortcut
        from .widgets import Segmented
        st = QSettings("SSA", "SimpleSeedAnalyser")
        mode = str(st.value("map/hits_mode", "") or "")
        if mode not in self.HIT_MODES:                   # settings of the old button
            on = str(st.value("map/hits_on", "true")).lower() == "true"
            mode = "Detailed" if on else "Off"
        self._hit_mode = mode
        bar = QFrame(self.map); bar.setObjectName("HitsBar")
        lay = QHBoxLayout(bar); lay.setContentsMargins(8, 4, 4, 4); lay.setSpacing(8)
        lab = QLabel("Hits"); lab.setObjectName("FieldLabel")
        seg = Segmented(list(self.HIT_MODES), mode)
        seg.changed.connect(self._set_hit_mode)
        lay.addWidget(lab); lay.addWidget(seg)
        bar.setToolTip("What a clicked result draws on the map  (H to cycle)\n"
                       "• Off: nothing\n"
                       "• Simple: a small #n badge where each condition is satisfied\n"
                       "• Detailed: zones, markers with name and coordinates, and a legend")
        bar.adjustSize()
        self._hits_bar, self._hits_seg = bar, seg

        page = self

        class _Place(QObject):
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Type.Resize:
                    page._place_hits_bar()
                return False
        self._hits_place = _Place(self)
        self.map.installEventFilter(self._hits_place)
        self._place_hits_bar()
        sc = QShortcut(QKeySequence("H"), self.map)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._cycle_hit_mode)

    def _place_hits_bar(self):
        b = self._hits_bar
        b.adjustSize()
        b.move(self.map.width() - b.width() - 12, 12)
        b.raise_()
        self.map.legend_top = b.y() + b.height() + 8

    def _set_hit_mode(self, mode):
        from PySide6.QtCore import QSettings
        self._hit_mode = mode
        self._hits_seg.set_value(mode)
        QSettings("SSA", "SimpleSeedAnalyser").setValue("map/hits_mode", mode)
        self._show_zones()

    def _cycle_hit_mode(self):
        m = self.HIT_MODES
        self._set_hit_mode(m[(m.index(self._hit_mode) + 1) % len(m)])

    def _show_zones(self):
        """Selected condition's zone (relative ones drawn at the origin, dashed),
        or — when a result is explained — every active condition at its real
        place, numbered #1… like the condition tree."""
        zones, markers, legend = [], [], []
        ex = self._explained
        style = "detailed"
        if ex and ex[0] == self.world.seed:
            _seed, ok, active, anchors, pos = ex
            mode = getattr(self, "_hit_mode", "Detailed")
            style = mode.lower()
            if mode != "Off":
                for i, c in enumerate(active):
                    t = c.get("type", "")
                    n = i + 1
                    col = type_color(t)
                    name = display_name(c)
                    p = pos[i]
                    legend.append(dict(num=n, name=name, color=col,
                                       where=(f"{p[0]}, {p[1]}" if p is not None else
                                              ("✓" if ok and t in CONTAINERS else "—"))))
                    if p is None and t not in CONTAINERS:
                        continue
                    if mode == "Detailed" and anchors[i] is not None:
                        z = self._zone_of(c, anchors[i], label=f"#{n}")
                        if z:
                            z["num"] = n                 # the legend can hide it
                            zones.append(z)
                    if p is not None and t not in CONTAINERS:
                        markers.append(dict(x=p[0], z=p[1], num=n, color=col, label=name,
                                            detail=f"X {p[0]}  Z {p[1]}"))
            if mode != "Detailed":
                legend = []
            # conditions shown / hidden from the legend stay while the session's
            # conditions stay the same (compare results with the same view)
            key = tuple(display_name(c) for c in active)
            if key != getattr(self, "_legend_key", None):
                self._legend_key = key
                self.map.legend_reset()
        else:
            uid = self.tree.current_uid()
            c = self.model.get(uid) if uid else None
            if c:
                exp = self.model.export(only={uid})[0]
                rel = bool(c.get("_parent", 0))
                exp["relative"] = 1 if rel else 0
                z = self._zone_of(exp, (0, 0), active=True,
                                  label=display_name(c) + ("  (relative to its parent)" if rel else ""))
                if z:
                    z["dashed"] = rel
                    zones.append(z)
        self.map.hit_style = style
        self.map.legend = legend
        self.map.set_zones(zones)
        self.map.set_markers(markers)

    def _form_preview(self, cond):
        parent = self.model.get(self._editing_uid).get("_parent", 0) if self._editing_uid else self._new_parent
        c = dict(cond); c["relative"] = 1 if parent else 0
        z = self._zone_of(c, (0, 0), active=True,
                          label=display_name(cond) + ("  (relative to its parent)" if parent else ""))
        if z:
            z["dashed"] = bool(parent)
        self.map.set_zones([z] if z else [])
        self.map.set_markers([])

    def _pick(self, kind):
        self.form.pick_origin = (0, 0)
        origin = (0, 0) if kind == "circle" else None
        self.map.start_pick(kind, origin=origin)
        self.status.emit("Draw on the map (Esc to cancel). Areas are relative to the parent "
                         "(drawn around 0, 0).")

    def _picked(self, r):
        if self.left_stack.currentWidget() is self.form:
            self.form.apply_pick(r)

    # ── session files ───────────────────────────────────────────────────────
    def _confirm_discard(self):
        if not self.model.dirty or not self.model.conds:
            return True
        r = QMessageBox.question(self, "Unsaved session",
                                 "The current session has unsaved changes. Discard them?")
        return r == QMessageBox.StandardButton.Yes

    def _end_paused_search(self):
        """Another session is opened: a paused search of the previous one ends
        (its summary stays until the new session is shown)."""
        if self.runner.state == "paused":
            self.runner.stop()

    def _busy_refuse(self, what):
        """A running search keeps its conditions: say so instead of silently
        clearing them under it (results of the old conditions kept coming)."""
        if self.runner.state in ("running", "pausing", "stopping"):
            QMessageBox.information(self, what, "A search is running: pause or stop it first.")
            return True
        return False

    def _new_session(self):
        if self._busy_refuse("New session"):
            return
        if self._confirm_discard():
            self._end_paused_search()
            self.model.load([], name="Untitled session")
            self._lib_meta = {}

    def _load(self):
        if self._busy_refuse("Open a session"):
            return
        if not self._confirm_discard():
            return
        from PySide6.QtCore import QSettings
        start_dir = str(QSettings("SSA", "SimpleSeedAnalyser").value("sessions/dir", "") or "")
        path, _ = QFileDialog.getOpenFileName(self, "Open a session", start_dir,
                                              "Sessions (*.session);;All files (*.*)")
        if path:
            self.open_path(path)

    def open_path(self, path, quiet=False, autosave=False):
        """Open a .session (SSA or cubiomes-viewer) COMPLETELY: conditions,
        version, large biomes, start seed, threads, mode, stop rule, results and
        — when it was saved paused (or a cubiomes-viewer session with progress)
        — the search itself, ready to Resume where it stopped."""
        from cubiomes_session import parse_session_file
        try:
            r = parse_session_file(path)
        except Exception as e:
            if not quiet:
                QMessageBox.critical(self, "Open session", f"Could not read the file:\n{e}")
            return False
        st = r.get("ssa_state") or {}
        self._end_paused_search()
        if self.runner.state in ("running", "pausing", "stopping"):
            if not quiet:
                QMessageBox.information(self, "Open session",
                                        "A search is running: pause or stop it first.")
            return False
        # autosave: the session keeps the name / file / unsaved flag it had;
        # a library entry is named by its saved name (its file name is an id)
        from .session_library import is_library_path
        name = st.get("name") if (autosave or is_library_path(path)) else None
        real_path = st.get("path") if autosave else path
        self._lib_meta = dict(description=st.get("description", ""), created=st.get("created", ""))
        self.model.load(r["conditions"], name=name or os.path.splitext(os.path.basename(path))[0],
                        path=real_path)
        if autosave:
            self.model.dirty = bool(st.get("dirty"))
            self._model_changed()
        self.world.set(version_name=r["mc_str"] if r.get("mc_str") else None,
                       large_biomes=bool(r.get("large_biomes")))
        self.start_seed.setText(str(r.get("seed_min", 0) or 0))
        if r.get("threads"):
            self.threads.setValue(max(1, min(self.threads.maximum(), int(r["threads"]))))
        inv = {v: k for k, v in MODES.items()}
        if st.get("mode") in inv:
            self.mode.set_value(inv[st["mode"]])
        rule = st.get("rule")
        if rule:
            self._set_rule_ui(rule)
        # results
        self.results.clear()
        self._explained = None
        seeds = list(dict.fromkeys(int(x) for x in r.get("results", [])))
        self.results.add([(sd, "") for sd in seeds])
        self._results_count()
        self._show_zones()
        # the search: SSA state, or a cubiomes-viewer #Progress
        search = st.get("search") or {}
        conds = self.model.export()
        resumed = False
        self._run_world = (self.world.version_name, bool(self.world.large_biomes))
        if search.get("resume_point") is not None:
            resumed = self.runner.restore_paused(conds, self._mc(), self.threads.value(), search, seeds)
        elif not st and r.get("progress") not in (None, r.get("seed_min", 0)):
            resumed = self.runner.restore_paused(
                conds, self._mc(), self.threads.value(),
                dict(mode="inc", eff_mode="inc", base_start=int(r.get("seed_min", 0) or 0),
                     resume_point=int(r["progress"]),
                     done=(int(r["progress"]) - int(r.get("seed_min", 0) or 0)) % (1 << 64),
                     elapsed=0.0, rule=["never", 0]), seeds)
            if resumed:
                self.mode.set_value(inv["inc"])
        prof = st.get("profile")
        if prof and isinstance(prof.get("rows"), list):
            self.runner.profile_base = ([list(map(int, r)) for r in prof["rows"]], int(prof.get("total", 0)))
            self.runner._prof_live = None
            self._prof_sig = self._sig(conds)
        else:
            self.runner.profile_base = None
            self._prof_sig = None
        self._show_profile()
        if resumed:
            self._run_sig = self._sig(conds)
        elif self.runner.state == "paused":
            self.runner.stop()
        if not autosave and not is_library_path(path):
            self._remember_recent(path)
        msgs = []
        if r.get("unsupported"):
            msgs.append("Skipped:\n• " + "\n• ".join(r["unsupported"][:10]))
        if msgs and not quiet:
            QMessageBox.information(self, "Open session", "\n\n".join(msgs))
        what = [f"{len(r['conditions'])} condition(s)"]
        if seeds:
            what.append(f"{fmt_int(len(seeds))} result(s)")
        if resumed:
            what.append(f"paused search — Resume continues from {self.runner.resume_point}")
        self.status.emit(("Restored the last session" if autosave else f"Opened {os.path.basename(path)}")
                         + " — " + ", ".join(what) + ".")
        return True

    def _set_rule_ui(self, rule):
        kind, val = rule[0], rule[1] if len(rule) > 1 else 0
        i = self.rule_kind.findData(kind)
        if i < 0:
            return
        self.rule_kind.setCurrentIndex(i)
        try:
            if kind == "seeds":
                self.rule_seeds.setText(str(int(val)))
            elif kind == "results":
                self.rule_results.setValue(int(val))
            elif kind == "time":
                secs = int(val)
                self.rule_time.setTime(QTime(min(23, secs // 3600), (secs // 60) % 60))
        except (TypeError, ValueError):
            pass

    # recent files (chevron menu)
    def _remember_recent(self, path):
        from PySide6.QtCore import QSettings
        st = QSettings("SSA", "SimpleSeedAnalyser")
        path = os.path.abspath(path)
        rec = [p for p in (st.value("sessions/recent", []) or []) if isinstance(p, str)]
        rec = [path] + [p for p in rec if os.path.normcase(p) != os.path.normcase(path)]
        st.setValue("sessions/recent", rec[:10])
        st.setValue("sessions/dir", os.path.dirname(path))

    def _fill_recent(self, menu):
        from PySide6.QtCore import QSettings
        menu.clear()
        rec = [p for p in (QSettings("SSA", "SimpleSeedAnalyser").value("sessions/recent", []) or [])
               if isinstance(p, str)]
        for p in rec:
            a = menu.addAction(os.path.basename(p), lambda p=p: self._open_recent(p))
            a.setToolTip(p)
            a.setEnabled(os.path.exists(p))
        if not rec:
            menu.addAction("(none yet)").setEnabled(False)
        else:
            menu.addSeparator()
            menu.addAction("Clear the list", lambda: QSettings("SSA", "SimpleSeedAnalyser")
                           .setValue("sessions/recent", []))

    def _open_recent(self, path):
        if self._confirm_discard():
            self.open_path(path)

    def _save(self):
        if self.model.path:
            self._write(self.model.path)
        else:
            self._save_library_new()

    # ── the library (sessions kept inside SSA) ─────────────────────────────
    def open_library(self):
        from .session_library import LibraryDialog
        dlg = LibraryDialog(self, self.window())
        dlg.exec()
        dlg.deleteLater()

    def _save_library_new(self):
        if not self.model.conds:
            QMessageBox.information(self, "Save", "Add at least one condition first.")
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save to the library", "Session name:",
                                        text=self.model.name)
        if ok and name.strip():
            self.save_to_library(name.strip())

    def save_to_library(self, name, path=None, description=None, created=None):
        """Write the current session as a library entry (new one when path is
        None). The editor is then linked to it: Ctrl+S updates it."""
        from .session_library import new_entry_path, now_iso
        path = path or new_entry_path()
        meta = getattr(self, "_lib_meta", None) or {}
        st = self._session_state()
        st.update(name=name,
                  description=meta.get("description", "") if description is None else description,
                  created=created or now_iso())
        try:
            self._write_file(path, st)
        except Exception as e:
            QMessageBox.critical(self, "Save", f"Could not save:\n{e}")
            return None
        self._lib_meta = dict(description=st["description"], created=st["created"])
        self.model.path = path
        self.model.name = name
        self.model.dirty = False
        self._model_changed()
        self.status.emit(f"Saved “{name}” in the session library.")
        return path

    def open_library_entry(self, path):
        if not self._confirm_discard():
            return False
        return self.open_path(path)

    def library_renamed(self, path, name):
        if self.model.path and os.path.normcase(os.path.abspath(self.model.path)) == \
                os.path.normcase(os.path.abspath(path)):
            self.model.name = name
            self._model_changed()

    def library_deleted(self, path):
        if self.model.path and os.path.normcase(os.path.abspath(self.model.path)) == \
                os.path.normcase(os.path.abspath(path)):
            self.model.path = None
            self.model.dirty = True
            self._model_changed()

    def _rename_session(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Rename", "Session name:", text=self.model.name)
        if not ok or not name.strip() or name.strip() == self.model.name:
            return
        self.model.name = name.strip()
        from .session_library import is_library_path, update_meta
        if is_library_path(self.model.path):
            try:
                update_meta(self.model.path, name=self.model.name)   # the library follows
            except OSError:
                self.model.dirty = True
        else:
            self.model.dirty = True
        self._model_changed()

    def _save_as(self):
        from PySide6.QtCore import QSettings
        d = str(QSettings("SSA", "SimpleSeedAnalyser").value("sessions/dir", "") or "")
        path, _ = QFileDialog.getSaveFileName(self, "Save the session",
                                              os.path.join(d, self.model.name + ".session"),
                                              "Sessions (*.session)")
        if path:
            self._write(path)

    def _session_state(self):
        """SSA extras saved with the session (#SSAState)."""
        st = dict(mode=MODES[self.mode.value()], threads=self.threads.value())
        try:
            st["rule"] = list(self._read_rule())
        except ValueError:
            pass
        rs = self.runner.export_state()
        if rs and rs.get("resume_point") is not None and self.runner.state == "paused":
            st["search"] = rs
        meta = getattr(self, "_lib_meta", None) or {}
        st.update(name=self.model.name, description=meta.get("description", ""),
                  created=meta.get("created", ""))
        prof = self.runner.profile()
        if prof is not None and getattr(self, "_prof_sig", None) == self._sig(self.model.export()):
            st["profile"] = dict(rows=prof[0], total=prof[1], seeds=int(self.runner._done_before),
                                 n=len(prof[0]))
        return st

    def _write_file(self, path, state):
        from cubiomes_session_writer import write_session_file
        try:
            start = int(self.start_seed.text() or 0)
        except ValueError:
            start = 0
        prog = self.runner.resume_point if self.runner.state == "paused" else None
        tmp = path + ".tmp"
        write_session_file(tmp, self.model.export(), mc_str=self.world.version_name,
                           seed_min=start, threads=self.threads.value(),
                           results=[s for s, _d in self.results.rows], progress=prog,
                           large=self.world.large_biomes, state=state)
        os.replace(tmp, path)                 # never a half-written session file

    def _write(self, path):
        try:
            self._write_file(path, self._session_state())
        except Exception as e:
            QMessageBox.critical(self, "Save session", f"Could not save:\n{e}")
            return
        from .session_library import is_library_path
        self.model.path = path
        if not is_library_path(path):
            self.model.name = os.path.splitext(os.path.basename(path))[0]
            self._remember_recent(path)
        self.model.dirty = False
        self._model_changed()
        n = len(self.results.rows)
        extra = (f", {fmt_int(n)} result(s)" if n else "") + \
                (", paused search (Resume continues it after reopening)"
                 if self.runner.state == "paused" else "")
        self.status.emit(f"Saved {os.path.basename(path)}{extra}.")

    # ── autosave: the session comes back as it was at the next launch ───────
    @staticmethod
    def autosave_path():
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "SimpleSeedAnalyser")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "autosave.session")

    def autosave(self, closing=False):
        """Write the current session (conditions, results, paused search) to
        %APPDATA%/SimpleSeedAnalyser/autosave.session. At close, a running
        search is PAUSED first so that it can be resumed after the restart."""
        try:
            if closing and self.runner.state == "running" and self.runner.can_pause:
                self.runner.pause()
                from PySide6.QtWidgets import QApplication
                import time as _t
                t_end = _t.time() + 5
                while self.runner.state == "pausing" and _t.time() < t_end:
                    QApplication.processEvents(); _t.sleep(0.02)
            if not self.model.conds and not self.results.rows:
                p = self.autosave_path()
                if os.path.exists(p):
                    os.replace(p, p + ".old")
                return
            st = self._session_state()
            st.update(path=self.model.path, name=self.model.name, dirty=bool(self.model.dirty))
            self._write_file(self.autosave_path(), st)
        except Exception:
            import traceback
            traceback.print_exc()

    def restore_autosave(self):
        p = self.autosave_path()
        if os.path.exists(p):
            self.open_path(p, quiet=True, autosave=True)

    # ── run ─────────────────────────────────────────────────────────────────
    @staticmethod
    def parse_count(text):
        """'500000', '250M', '1.5G', '2^40', '1e12', '4 000 000' → int."""
        t = text.strip().replace(" ", "").replace("_", "").lower()
        if not t:
            raise ValueError("empty")
        if "^" in t:
            a, b = t.split("^", 1)
            return int(float(a) ** int(b))
        mult = {"k": 10**3, "m": 10**6, "g": 10**9, "b": 10**9, "t": 10**12}
        if t[-1] in mult:
            return int(float(t[:-1]) * mult[t[-1]])
        return int(float(t)) if ("e" in t or "." in t) else int(t)

    def _read_rule(self):
        kind = self.rule_kind.currentData()
        if kind == "seeds":
            try:
                n = self.parse_count(self.rule_seeds.text())
            except (ValueError, OverflowError):
                raise ValueError("Stop after: write a number of seeds, e.g. 500000, 250M, 1G, 2^40.")
            if n <= 0:
                raise ValueError("Stop after: the number of seeds must be > 0.")
            return ("seeds", n)
        if kind == "results":
            return ("results", self.rule_results.value())
        if kind == "time":
            t = self.rule_time.time()
            secs = t.hour() * 3600 + t.minute() * 60
            if secs <= 0:
                raise ValueError("Stop after: choose a duration.")
            return ("time", secs)
        return ("never", 0)

    def _read_start(self):
        try:
            start = int(self.start_seed.text().strip() or 0)
        except ValueError:
            raise ValueError("The start seed must be an integer.")
        if not (-(1 << 63) <= start < (1 << 63)):
            raise ValueError("The start seed must fit in 64 bits.")
        return start

    def _start_or_resume(self):
        if self.runner.state == "paused":
            self._resume()
            return
        conds = self.model.export()
        active = [c for c in conds if c.get("enabled", True)]
        if not active:
            QMessageBox.information(self, "Start", "Add at least one enabled condition.")
            return
        try:
            start = self._read_start()
            rule = self._read_rule()
        except ValueError as e:
            QMessageBox.warning(self, "Start", str(e))
            return
        self.results.clear()
        self._explained = None
        self._results_count()
        self._run_sig = self._sig(conds)
        self._prof_sig = self._run_sig
        self._run_world = (self.world.version_name, bool(self.world.large_biomes))
        self.runner.start(conds, self._mc(), start, self.threads.value(),
                          MODES[self.mode.value()], seed_list=self.seed_list, rule=rule,
                          **self._engine_args(conds))

    @staticmethod
    def _sig(conds):
        import json
        return json.dumps(cs_active(conds), sort_keys=True, default=str)

    def _search_space_changed(self):
        """Did the start seed / mode / seed list change since the pause?"""
        p = self.runner.params or {}
        try:
            start = self._read_start()
        except ValueError:
            return True
        rw = getattr(self, "_run_world", None)       # version / large biomes of the search
        world_changed = rw is not None and rw != (self.world.version_name,
                                                  bool(self.world.large_biomes))
        return (MODES[self.mode.value()] != p.get("mode") or start != self.runner.base_start
                or self.seed_list is not p.get("seed_list") or world_changed)

    def _resume(self):
        try:
            rule = self._read_rule()
        except ValueError as e:
            QMessageBox.warning(self, "Resume", str(e))
            return
        if self._search_space_changed():
            p = self.runner.params
            old_mode = {v: k for k, v in MODES.items()}.get(p.get("mode"), "?")
            box = QMessageBox(self)
            box.setWindowTitle("Search changed")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText("The start seed, the mode, the seed list, the version or Large biomes "
                        "changed during the pause.")
            box.setInformativeText(
                f"The paused search ({old_mode}, started at {self.runner.base_start}) can continue "
                f"from {self.runner.resume_point}, or a new search can start with the new settings.")
            b_new = box.addButton("Start a new search", QMessageBox.ButtonRole.AcceptRole)
            b_old = box.addButton("Resume the paused search", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(b_new)
            box.exec()
            if box.clickedButton() is b_new:
                self.runner.stop()
                self._start_or_resume()
                return
            if box.clickedButton() is not b_old:
                return
            # back to the paused search's settings (version / large biomes too:
            # resuming a 1.21 search while the map shows 1.16 mixed two worlds)
            self.mode.set_value(old_mode)
            self.start_seed.setText(str(self.runner.base_start))
            rw = getattr(self, "_run_world", None)
            if rw is not None:
                self.world.set(version_name=rw[0], large_biomes=rw[1])
        conds = self.model.export()
        old = self.runner.params["conditions"]
        if not [c for c in conds if c.get("enabled", True)]:
            QMessageBox.information(self, "Resume", "Add at least one enabled condition.")
            return
        if self._sig(conds) != self._sig(old):
            box = QMessageBox(self)
            box.setWindowTitle("Conditions changed")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText("The conditions were changed during the pause.")
            box.setInformativeText(
                "The seeds already searched were tested with the OLD conditions. Resuming "
                "continues from the same point with the new ones.\n\n"
                "• Re-check results: keeps only the results found so far that also match the new "
                "conditions (fast).\n"
                "• If the new conditions are LOOSER, seeds already searched may hold new "
                "matches: restart from the start seed to cover them.")
            b_check = box.addButton("Resume + re-check results", QMessageBox.ButtonRole.AcceptRole)
            b_keep = box.addButton("Resume, keep results", QMessageBox.ButtonRole.AcceptRole)
            b_restart = box.addButton("Restart from the start seed", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(b_check)
            box.exec()
            clicked = box.clickedButton()
            if clicked is b_restart:
                self.runner.stop()
                self._start_or_resume()
                return
            if clicked not in (b_check, b_keep):
                return
            if clicked is b_check:
                self._recheck_results(conds)
        if self._sig(conds) != getattr(self, "_prof_sig", None):
            self.runner.profile_base = None           # other conditions: new profile
            self._prof_sig = self._sig(conds)
        err = self.runner.resume(conditions=conds, threads=self.threads.value(), rule=rule,
                                 **self._engine_args(conds))
        if err:
            QMessageBox.information(self, "Resume", err)

    def _recheck_results(self, conds):
        """Keep only the results that match the new conditions (C engine)."""
        seeds = [s for s, _d in self.results.rows]
        if not seeds:
            return
        import custom_search as cs
        keep = set()
        ev = threading.Event()
        try:
            cs.evaluate_session(conds, 0, 0, self._mc(), self.threads.value(), ev,
                                lambda s, d="": keep.add(int(s)), lambda a, b: None,
                                seed_list=seeds)
        except Exception as e:
            QMessageBox.warning(self, "Re-check", f"Could not re-check the results:\n{e}")
            return
        rows = [(s, d) for s, d in self.results.rows if s in keep]
        removed = len(self.results.rows) - len(rows)
        self.results.clear(); self.results.add(rows)
        with self.runner._lock:
            self.runner._seen = {s for s, _d in rows}
            self.runner.found = len(rows)
        self._results_count()
        self.status.emit(f"Re-check: {removed} result(s) no longer match and were removed.")

    def _run_state(self, st):
        running = st in ("running", "pausing", "stopping")
        if st in ("paused", "done", "error", "running"):
            self._show_profile()
        self.b_start.setEnabled(not running)
        self.b_start.setText("  Resume" if st == "paused" else "  Start")
        self.b_pause.setEnabled(st == "running" and self.runner.can_pause)
        self.b_stop.setEnabled(st in ("running", "pausing", "paused"))
        # mode / start / source define WHICH seeds are searched. They stay
        # editable during a pause: Resume then asks whether to start a NEW
        # search with them or to continue the paused one (27/09).
        for w in (self.mode, self.start_seed, self.list_btn):
            w.setEnabled(not running)
        for w in (self.threads, self.rule_kind, self.rule_seeds, self.rule_results, self.rule_time):
            w.setEnabled(not running)
        self.b_engine.setEnabled(not running)
        self.engine_chip.setProperty("state", "ok" if running else "off")
        self.engine_chip.style().unpolish(self.engine_chip); self.engine_chip.style().polish(self.engine_chip)
        if st == "idle":
            self.stat.setText("Idle")
        if not running and self.prog.maximum() == 0:     # unlimited: stop the busy bar
            self.prog.setRange(0, 1000); self.prog.setValue(0)

    # ── engine (CPU / GPU) ──────────────────────────────────────────────────
    def _engine_ctx(self):
        conds = self.model.export()
        if not [c for c in conds if c.get("enabled", True)]:
            return "Add at least one enabled condition first."
        try:
            start = self._read_start()
        except ValueError as e:
            return str(e)
        return (conds, self._mc(), start, self.threads.value(), MODES[self.mode.value()],
                self.seed_list is None)          # a seed list has no search mode to compare

    def _open_engine(self):
        from .engine_dialog import EngineDialog
        plan = self.engine_plan if self._engine_plan_valid() else None
        if plan is None and self.runner.plan is not None and self.runner.params and \
                self._sig(self.runner.params.get("conditions") or []) == self._sig(self.model.export()):
            plan = self.runner.plan                    # the one GPU: Auto used for this search
        dlg = EngineDialog(self._engine_ctx, self.engine_mode if self.engine_mode != "plan" or plan
                           else "as_set", plan, self)
        dlg.modeChosen.connect(self._engine_mode_chosen)
        dlg.chosen.connect(self._engine_chosen)
        dlg.startRequested.connect(lambda: (dlg.close(), self._start_or_resume()))
        dlg.exec()
        dlg.deleteLater()                      # (each opening left a dialog alive)

    def _engine_mode_chosen(self, m):
        """The analysis recommends another search mode (Incremental / 48-bit)."""
        label = next((k for k, v in MODES.items() if v == m), None)
        if label is None or label == self.mode.value():
            return
        if self.runner.state == "running":
            self.status.emit(f"Search mode not changed while searching: stop, then choose "
                             f"{label}.")
            return
        self.mode.set_value(label)
        self.status.emit(f"Search mode: {label} (recommended by the engine analysis).")

    def _engine_chosen(self, mode, plan):
        self.engine_mode = mode
        if mode == "plan" and plan is not None:
            self.engine_plan = plan
            self._engine_plan_sig = self._sig(self.model.export())
        self._update_engine_button()

    def _engine_plan_valid(self):
        p = self.engine_plan
        return (p is not None and self._engine_plan_sig == self._sig(self.model.export())
                and getattr(p, "mode", "") in ("", MODES[self.mode.value()]))

    def _update_engine_button(self):
        if self.engine_mode == "plan" and self.engine_plan is not None:
            txt = self.engine_plan.label.replace("Auto → ", "")
        else:
            txt = {"auto": "Auto", "as_set": "As set", "cpu": "CPU only"}.get(self.engine_mode, "Auto")
        # the button stays "Engine" (narrow panel); the current choice is in its tooltip
        self.b_engine.setToolTip(f"Engine: {txt}\nCPU or GPU for this session: analyse, "
                                 f"see why, choose.")

    def _engine_args(self, conds):
        """gpu / plan arguments of the runner for this start."""
        if self.engine_mode == "plan":
            if self._engine_plan_valid():
                return dict(gpu="plan", plan=self.engine_plan)
            # the conditions changed since the analysis: analyse again at Start
            self.status.emit("The conditions or the search mode changed since the engine "
                             "analysis: the session runs as set (Engine → Analyse now to "
                             "measure again).")
            self.engine_mode = "as_set"; self._update_engine_button()
        return dict(gpu=self.engine_mode, plan=None)

    def _clear_summary(self, *_):
        """Back to 'Idle' once a finished search no longer matches the screen
        (a paused search keeps its summary: Resume continues it)."""
        if self.runner.state in ("done", "error"):
            self.stat.setText("Idle")
            self.prog.setRange(0, 1000); self.prog.setValue(0)

    def _model_maybe_clear(self):
        if self.runner.state in ("done", "error") and self._run_sig is not None:
            try:
                same = self._sig(self.model.export()) == self._run_sig
            except Exception:
                same = False
            if not same:
                self._clear_summary()

    def _run_message(self, text):
        self.stat.setText(text)
        self.status.emit(text)

    def _run_failed(self, err):
        self.stat.setText("Error")
        QMessageBox.critical(self, "Search failed", f"The search stopped with an error:\n\n{err}")

    def _engine(self, label, tip):
        self.engine_chip.setText(label)
        self.engine_chip.setToolTip(tip)
        if self.engine_mode == "auto" and self.runner.plan is not None:
            self.b_engine.setToolTip(f"Engine: {self.runner.plan.label}\n{self.runner.plan.detail}"
                                     "\nClick for the details.")
        if self.runner.state == "running":
            self.b_pause.setEnabled(self.runner.can_pause)
            self.b_pause.setToolTip("" if self.runner.can_pause else
                                    "This engine cannot be paused and resumed exactly.")

    def _progress(self, d):
        import time as _t
        if _t.time() - getattr(self, "_prof_t", 0) > 0.7:
            self._prof_t = _t.time()
            self._show_profile()
        frac = d.get("frac")
        if frac is not None:
            self.prog.setRange(0, 1000); self.prog.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        else:
            self.prog.setRange(0, 0)                    # unlimited → busy bar
        unit = d.get("unit", "seeds")
        parts = [f"{fmt_int(d['done'])} {unit}", fmt_rate(d["sps"]), f"{fmt_int(d['found'])} found",
                 f"elapsed {fmt_time(d['elapsed'])}"]
        if frac is not None:
            parts.insert(1, f"{frac * 100:.2f} %")
            parts.append(f"ETA {fmt_time(d['eta'])}")
        self.stat.setText("  ·  ".join(parts))
        self._results_count()

    def _results_in(self, batch):
        at_end = self.table.verticalScrollBar().value() >= self.table.verticalScrollBar().maximum() - 2
        self.results.add(batch)
        self._results_count()
        if at_end and self.results.rows and len(self.results.rows) < 2000:
            self.table.scrollToBottom()

    def _results_count(self):
        n = len(self.results.rows)
        self.res_count.setText(fmt_int(n))

    def _seed_list(self):
        if self.seed_list is not None:
            self.seed_list = None
            self.list_btn.setText("Seed list…")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Seed list", user_path(),
                                              "Seed lists (*.txt *.seeds *.csv);;All files (*.*)")
        if not path:
            return
        remember_path(path)
        from .tools_dialogs import maybe_convert
        path = maybe_convert(self, path)
        try:
            from seedlist_io import SeedListSource
            src = SeedListSource(path)
            n = len(src)
        except Exception as e:
            QMessageBox.critical(self, "Seed list", f"Could not read the list:\n{e}")
            return
        self.seed_list = src
        self.list_btn.setText(f"{os.path.basename(path)} · {fmt_int(n)}  ✕")
        self.list_btn.setToolTip("Click to go back to a seed range.")

    # ── results ─────────────────────────────────────────────────────────────
    def _result_selected(self, cur, _prev):
        if not cur.isValid():
            return
        seed = self.results.rows[cur.row()][0]
        self.world.set(seed=seed)
        self._explain_seed(seed)

    def _world_seed_changed(self):
        ex = self._explained
        if getattr(self.tree, "_check", None) and (ex is None or ex[0] != self.world.seed):
            self.tree.set_check(None)

    def _check_world_seed(self):
        """Test the seed of the top bar (any seed, not only a result)."""
        if not [c for c in self.model.conds if c.get("enabled", True)]:
            QMessageBox.information(self, "Check a seed", "Add at least one enabled condition.")
            return
        self.table.clearSelection()
        self._explain_seed(int(self.world.seed), check=True)

    def _explain_seed(self, seed, check=False):
        conds = self.model.export()
        mc = self._mc()
        self._explained = None
        self.tree.set_check(None)
        self._show_zones()
        key = (seed, mc, self._sig(conds))
        cache = self.__dict__.setdefault("_explain_cache", {})
        if key in cache:                                  # already located
            self._on_explained(seed, cache[key])
            return
        if self.results.loc.get(seed) is None:
            self.results.set_location(seed, "locating…")
        self.status.emit(f"Checking seed {seed}…" if check else "Locating the conditions of this seed…")

        # the Python evaluator runs in the map's helper PROCESS (a session with
        # far spirals can take seconds: in a thread it made the UI stutter)
        def work():
            try:
                import custom_search as cs
                from .map_engine import _eye_pool
                res = _eye_pool().submit(cs.check_seed, conds, seed, mc).result()
                cache[key] = res
                self._explain.done.emit(seed, res)
            except Exception as e:
                self._explain.done.emit(seed, ("err", f"{type(e).__name__}: {e}"))
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def focus_of(active, pos):
        """Where the seed 'is': the satisfied condition deepest in the tree
        (the final match of the chain, e.g. the spot a far spiral stopped at);
        ties → the last one. None if nothing was located."""
        def depth(i):
            d, seen = 0, set()
            r = int(active[i].get("relative", 0) or 0)
            while r and r not in seen and 1 <= r <= len(active):
                seen.add(r); d += 1
                r = int(active[r - 1].get("relative", 0) or 0)
            return d
        best = None
        for i, p in enumerate(pos):
            if p is not None and (best is None or depth(i) >= depth(best)):
                best = i
        return None if best is None else tuple(pos[best])

    def _on_explained(self, seed, res):
        if seed != self.world.seed:
            return
        if res[0] == "err":
            self.status.emit(f"Could not locate the conditions: {res[1]}")
            return
        ok, active, anchors, pos = res[:4]
        reached, passed = (res[4], res[5]) if len(res) >= 6 else (None, None)
        self._explained = (seed, ok, active, anchors, pos)
        self._show_zones()
        st = self._check_status(active, anchors, pos, reached, passed, ok)
        self.tree.set_check(st)
        culprits = [n for n, v in st.items() if v == "culprit"]
        focus = self.focus_of(active, pos)
        if not ok:
            if culprits:
                c = active[culprits[0] - 1]
                more = f" (and {len(culprits) - 1} other branch{'es' if len(culprits) > 2 else ''})" \
                    if len(culprits) > 1 else ""
                self.status.emit(f"Seed {seed} FAILS at #{culprits[0]} {display_name(c)}{more} — "
                                 f"red ✗ in the conditions")
            else:
                self.status.emit(f"Seed {seed} does not match the current conditions.")
        if focus is None:
            self.results.set_location(seed, "—")
            return
        fx, fz = int(focus[0]), int(focus[1])
        self.results.set_location(seed, (fx, fz))
        # frame the conditions around that spot only (parents millions of blocks
        # away — the spiral's origin — would otherwise zoom the map out to nothing)
        near = [p for p in pos if p is not None and abs(p[0] - fx) <= 4096 and abs(p[1] - fz) <= 4096]
        xs = [p[0] for p in near]; zs = [p[1] for p in near]
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        self.map.center_on(cx, cz)            # the current zoom is kept (27/09)
        far = max(abs(fx), abs(fz))
        if ok:
            self.status.emit(f"Seed {seed} matches ✓   X {fx}  Z {fz}"
                             + (f"  ({far:,} blocks from 0, 0)".replace(",", " ") if far > 100_000 else ""))

    @staticmethod
    def _check_status(active, anchors, pos, reached=None, passed=None, ok=False):
        """#n -> 'ok' (satisfied), 'culprit' (tested and failed, nothing
        tested under it: this is where the seed fails), 'fail' (its branch
        fails further down), 'skip' (not reached)."""
        n = len(active)
        kids = {i: [] for i in range(n)}
        for i, c in enumerate(active):
            r = int(c.get("relative", 0) or 0)
            if 1 <= r <= n and r - 1 != i:
                kids[r - 1].append(i)
        out = {}
        if reached is not None and len(reached) == n:
            for i in range(n):
                if passed[i] or pos[i] is not None:
                    out[i + 1] = "ok"
                elif not reached[i]:
                    out[i + 1] = "skip"
                elif ok:
                    out[i + 1] = "alt"       # an alternative that failed, the seed matches anyway
                else:
                    out[i + 1] = "fail" if any(reached[k] for k in kids[i]) else "culprit"
            return out
        for i in range(n):
            if pos[i] is not None:
                out[i + 1] = "ok"
            elif anchors[i] is None:
                out[i + 1] = "skip"
            else:
                reached_below = any(anchors[k] is not None for k in kids[i])
                out[i + 1] = "fail" if reached_below else "culprit"
        return out

    def _results_menu(self, pt):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            return
        seeds = [self.results.rows[r][0] for r in rows]
        m = QMenu(self)
        a_copy = m.addAction(f"Copy seed{'s' if len(seeds) > 1 else ''}")
        loc = self.results.loc.get(seeds[0]) if len(seeds) == 1 else None
        has_loc = isinstance(loc, tuple)
        a_tp = m.addAction(f"Copy /tp {loc[0]} ~ {loc[1]}") if has_loc else None
        a_go = m.addAction("Centre the map on the location") if has_loc else None
        a_map = m.addAction("Open on the Map page") if len(seeds) == 1 else None
        m.addSeparator()
        a_exp = m.addAction("Export all results…")
        act = m.exec(self.table.viewport().mapToGlobal(pt))
        if act is a_copy:
            QGuiApplication.clipboard().setText("\n".join(str(s) for s in seeds))
        elif act is not None and act is a_tp:
            QGuiApplication.clipboard().setText(f"/tp @s {loc[0]} ~ {loc[1]}")
        elif act is not None and act is a_go:
            self.map.center_on(loc[0], loc[1])
        elif act is not None and act is a_map:
            self.world.set(seed=seeds[0])
            self.openMap.emit()
        elif act is a_exp:
            self._export()

    def _export(self):
        if not self.results.rows:
            return
        path, flt = QFileDialog.getSaveFileName(self, "Export results", user_path(self.model.name + "_results.txt"),
                                                "Text, one seed per line (*.txt);;CSV (*.csv)")
        if not path:
            return
        remember_path(path)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                if path.lower().endswith(".csv"):
                    f.write("index,seed,x,z\n")
                    for i, (s, _d) in enumerate(self.results.rows, 1):
                        loc = self.results.loc.get(s)
                        xz = f"{loc[0]},{loc[1]}" if isinstance(loc, tuple) else ","
                        f.write(f"{i},{s},{xz}\n")
                else:
                    for s, _d in self.results.rows:
                        f.write(f"{s}\n")
        except OSError as e:                      # read-only folder, file in use…
            QMessageBox.warning(self, "Export results", f"Could not write the file:\n{e}")
            return
        self.status.emit(f"{fmt_int(len(self.results.rows))} seeds exported to {os.path.basename(path)}.")

    def _clear_results(self):
        if self.runner.state in ("running", "pausing", "stopping"):
            return
        self.results.clear()
        self._explained = None
        self._results_count()
        self._show_zones()


