"""
qtui/stronghold_dialogs.py — stronghold information on the map.

  eye_color(n)              colour of an eye count (same scale as the classic UI)
  StrongholdDetailsDialog   one stronghold: portal position, frames filled, Y…
  StrongholdListDialog      all 128 strongholds of the seed: eyes, top Y,
                            corruptible portal (1.13–1.17), distance; sortable;
                            double-click / "Go" centres the map on it.
"""
from __future__ import annotations

import math
import threading

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

from . import icons, struct_icons, theme
from .widgets import body, section_title

EYE_COLORS = ("#5a5f76", "#7c4257", "#a13a4d", "#d83a3a", "#ee5e2b", "#ff8a1f", "#ffb020",
              "#ffd166", "#e9e74d", "#a8d843", "#5fc955", "#22b573", "#00e87a")


def eye_color(n: int) -> str:
    return EYE_COLORS[max(0, min(12, int(n)))]


class _Frames(QWidget):
    """The 12 end-portal frames (filled = holds an eye of ender), in the order
    the game places them."""
    def __init__(self, frames, parent=None):
        super().__init__(parent)
        self.frames = list(frames or [False] * 12)
        self.setFixedSize(12 * 17, 22)

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(12):
            full = i < len(self.frames) and self.frames[i]
            p.setPen(QPen(QColor(theme.LINE2), 1))
            p.setBrush(QColor("#2f6b4a") if full else QColor(theme.BG3))
            p.drawRoundedRect(i * 17 + 1, 2, 15, 18, 3, 3)
            if full:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#7ef0b4"))
                p.drawEllipse(i * 17 + 5, 7, 7, 7)
        p.end()


class StrongholdDetailsDialog(QDialog):
    def __init__(self, seed, mc, sx, sz, parent=None):
        super().__init__(parent)
        import stronghold_eyes as se
        self.setWindowTitle(f"Stronghold ({sx}, {sz})")
        d = se.get_stronghold_diagnostics(seed, sx, sz, mc)
        v = QVBoxLayout(self); v.setContentsMargins(22, 18, 22, 16); v.setSpacing(10)
        top = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(struct_icons.pixmap("stronghold", 34)); top.addWidget(ic)
        col = QVBoxLayout(); col.setSpacing(0)
        t = QLabel("Stronghold · end portal"); t.setObjectName("PageTitle"); col.addWidget(t)
        col.addWidget(body(f"Seed {seed}", muted=True, wrap=False))
        top.addLayout(col, 1)
        eyes = int(d.get("eyes", 0))
        chip = QLabel(f"  {eyes} / 12 eyes  "); chip.setObjectName("Chip")
        chip.setStyleSheet(f"background:{eye_color(eyes)}; color:#10131a;")
        top.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)
        h = QHBoxLayout(); h.setSpacing(18)
        g = QGridLayout(); g.setHorizontalSpacing(14); g.setVerticalSpacing(5)
        px, py, pz = d.get("portal_pos", (sx, 0, sz))
        rows = [("Stronghold position", f"X {sx}   Z {sz}"),
                ("Portal centre", f"X {px}   Y {d.get('frame_y', py)}   Z {pz}"),
                ("Portal room chunk", str(d.get("portal_room_chunk") or "—")),
                ("Start chunk", str(d.get("chunk_start"))),
                ("Direction", str(d.get("direction", "?"))),
                ("Pieces generated", f"{d.get('n_pieces', '?')}  (attempt {d.get('attempt', '?')})")]
        if d.get("highest_y") is not None:
            rows.append(("Top of the stronghold", f"Y {d['highest_y']}"))
            rows.append(("Bottom", f"Y {d.get('lowest_y')}"))
        if d.get("ambiguous"):
            rows.append(("Note", f"portal across chunks — eyes {d.get('eyes_range')}"))
        for i, (a, b) in enumerate(rows):
            g.addWidget(body(a, muted=True, wrap=False), i, 0)
            lab = QLabel(b); lab.setFont(theme.mono_font(9.5))
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.addWidget(lab, i, 1)
        h.addLayout(g, 1)
        v.addLayout(h)
        v.addWidget(section_title("Portal frames"))
        v.addWidget(_Frames(d.get("frames")))
        row = QHBoxLayout(); row.addStretch(1)
        tp = QPushButton("Copy /tp to the portal"); tp.setProperty("kind", "ghost")
        from PySide6.QtGui import QGuiApplication
        tp.clicked.connect(lambda: QGuiApplication.clipboard().setText(
            f"/tp @s {px} {int(d.get('frame_y', py)) + 1} {pz}"))
        ok = QPushButton("Close"); ok.setProperty("kind", "primary"); ok.clicked.connect(self.accept)
        row.addWidget(tp); row.addWidget(ok)
        v.addLayout(row)


class _Loader(QObject):
    done = Signal(object, object)       # (object: a dict through Qt would get str keys)
    broken = Signal(int, object)          # (stronghold #, result | error text) — 03/10


class _Num(QTableWidgetItem):
    """Item sorted by a number (UserRole) instead of its text."""
    def __lt__(self, other):
        a, b = self.data(Qt.ItemDataRole.UserRole), other.data(Qt.ItemDataRole.UserRole)
        try:
            return float(a) < float(b)
        except (TypeError, ValueError):
            return super().__lt__(other)


class StrongholdMap(QWidget):
    """(15/11) The 128 strongholds of the seed at their real places around (0, 0):
    a dot each, coloured and sized by its eyes; the rings they belong to behind.
    Hover: its numbers. Click: its row in the list. Double-click: centre the map on it."""
    picked = Signal(int)                         # the stronghold's number (1 … 128)
    opened = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []                           # (number, x, z, eyes, max y)
        self.cur = None
        self._hot = None
        self._span = 1.0
        self.setMouseTracking(True)
        self.setMinimumSize(300, 300)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_rows(self, rows):
        self.rows = [r for r in rows if len(r) == 5]
        self._span = max([math.hypot(r[1], r[2]) for r in self.rows] + [1.0]) * 1.06
        self.update()

    def set_current(self, number):
        if number != self.cur:
            self.cur = number
            self.update()

    def _pos(self, x, z):
        w, h = self.width(), self.height()
        k = (min(w, h) / 2.0 - 14) / self._span
        return QPointF(w / 2.0 + x * k, h / 2.0 + z * k)

    def _at(self, pos):
        best, bd = None, 13.0 ** 2
        for r in self.rows:
            q = self._pos(r[1], r[2])
            d = (q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2
            if d < bd:
                best, bd = r, d
        return best

    def mouseMoveEvent(self, e):
        r = self._at(e.position())
        n = r[0] if r else None
        if n != self._hot:
            self._hot = n
            self.update()
            if r:
                from PySide6.QtWidgets import QToolTip
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"#{r[0]}   X {r[1]}  Z {r[2]}\n{r[3]} / 12 eyes"
                                  + (f"   ·   top Y {r[4]}" if r[4] is not None else ""), self)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hot = None
        self.update()

    def mousePressEvent(self, e):
        r = self._at(e.position())
        if r and e.button() == Qt.MouseButton.LeftButton:
            self.picked.emit(r[0])

    def mouseDoubleClickEvent(self, e):
        r = self._at(e.position())
        if r:
            self.opened.emit(r[0])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        frame = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(QPen(QColor(theme.LINE2), 1))
        p.setBrush(QColor(theme.field_bg()))
        p.drawRoundedRect(frame, 14, 14)
        c = QPointF(w / 2.0, h / 2.0)
        k = (min(w, h) / 2.0 - 14) / self._span
        a = QColor(theme.ACCENT)
        # the rings of the game: 1280 … 2816 blocks, then every 3072
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(8):
            mid = (1280 + 2816) / 2.0 + 3072 * i
            if mid * k > min(w, h) / 2.0:
                break
            band = QColor(a)
            band.setAlphaF(0.07)
            p.setPen(QPen(band, max(1.0, 1536 * k)))
            p.drawEllipse(c, mid * k, mid * k)
        axis = QColor(theme.TEXT)
        axis.setAlphaF(0.10)
        p.setPen(QPen(axis, 1))
        p.drawLine(QPointF(c.x() - 6, c.y()), QPointF(c.x() + 6, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 6), QPointF(c.x(), c.y() + 6))
        if not self.rows:
            p.setPen(QColor(theme.TEXT3))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Computing…")
            return
        p.setPen(Qt.PenStyle.NoPen)
        for r in sorted(self.rows, key=lambda q: q[3]):      # the richest on top
            q = self._pos(r[1], r[2])
            eyes = int(r[3])
            col = QColor(eye_color(eyes))
            rad = 2.2 + eyes * 0.42
            if eyes >= 4:                                    # a halo for the good ones
                halo = QColor(col)
                halo.setAlphaF(0.22)
                p.setBrush(halo)
                p.drawEllipse(q, rad + 3.5, rad + 3.5)
            col.setAlphaF(0.55 if eyes == 0 else 1.0)
            p.setBrush(col)
            p.drawEllipse(q, rad, rad)
        for n, colr, wd in ((self._hot, QColor(theme.TEXT2), 1.3), (self.cur, QColor("#ffffff"), 1.8)):
            r = next((q for q in self.rows if q[0] == n), None) if n else None
            if r:
                q = self._pos(r[1], r[2])
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(colr, wd))
                p.drawEllipse(q, 9.5, 9.5)
        p.setFont(theme.num_font(7.6))
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(10, h - 22, w - 20, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"± {int(self._span):,} blocks".replace(",", " "))


class StrongholdListDialog(QDialog):
    goto = Signal(int, int)

    def __init__(self, seed, mc, strongholds=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("All strongholds")
        self.resize(1120, 660)                # (15/11) the map of the strongholds beside the list
        self.seed, self.mc = seed, mc
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(8)
        top = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(struct_icons.pixmap("stronghold", 30)); top.addWidget(ic)
        t = QLabel("All strongholds"); t.setObjectName("PageTitle"); top.addWidget(t)
        top.addStretch(1)
        self.summary = QLabel("Computing…"); self.summary.setObjectName("Muted"); top.addWidget(self.summary)
        v.addLayout(top)
        hint = body("Click a column to sort · double-click a stronghold (row or dot) to centre the map on it  ⓘ",
                    muted=True, small=True)
        hint.setToolTip("Each dot is a stronghold at its real place around (0, 0): the more eyes in its\n"
                        "portal, the bigger and brighter. A click on a dot selects its row, and a row its dot.")
        v.addWidget(hint)
        # « Broken »: is the end portal broken by a ruined portal? Checked by the real
        # Minecraft 1.16 generation (mc_checker) for the strongholds a ruined portal
        # can reach; the others are not broken (03/10, replaces « Distance »)
        cols = ["#", "Ring", "X", "Z", "Eyes", "", "Top Y", "Broken"]
        self.corr = mc in (19, 20)
        cols.append("")
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(31)          # (15/11) the « Go » buttons fit
        hh = self.table.horizontalHeader()
        for i in range(len(cols)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        if not self.corr:                     # broken portals: checked in 1.16 only
            self.table.setColumnHidden(7, True)
            hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._go(r))
        self.table.setMinimumWidth(560)
        self.ring = StrongholdMap()
        self.ring.picked.connect(self._pick_number)
        self.ring.opened.connect(lambda n: (self._pick_number(n), self._go(self._row_of(n))))
        self.table.itemSelectionChanged.connect(self._row_selected)
        mid = QHBoxLayout(); mid.setSpacing(12)
        mid.addWidget(self.ring, 5); mid.addWidget(self.table, 6)
        v.addLayout(mid, 1)
        row = QHBoxLayout(); row.addStretch(1)
        ok = QPushButton("Close"); ok.clicked.connect(self.accept); row.addWidget(ok)
        v.addLayout(row)
        self._loader = _Loader(self)
        self._loader.done.connect(self._fill)
        self._loader.broken.connect(self._set_broken)
        threading.Thread(target=self._load, args=(strongholds,), daemon=True).start()

    def _load(self, strongholds):
        import stronghold_eyes as se
        rows, corr = [], {}
        try:
            if not strongholds:
                from cubiomes_wrap import CubiomesLib
                lib = CubiomesLib(); lib.setup_and_seed(mc=self.mc, seed=self.seed)
                strongholds = lib.find_strongholds(max_count=128)
            for i, (x, z) in enumerate(strongholds):
                eyes, maxy = se.get_stronghold_eyes_and_maxy(self.seed, x, z, self.mc)
                rows.append((i + 1, x, z, int(eyes), maxy))
            if self.corr:
                try:
                    import ruined_portal as rp
                    corr = rp.corrupt_check_all_strongholds(self.seed, strongholds, self.mc)
                except Exception:
                    corr = {}
        except Exception as e:
            rows = [("error", str(e))]
        self._loader.done.emit(rows, corr)

    def _row_of(self, number):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == number:
                return r
        return -1

    def _pick_number(self, number):
        r = self._row_of(number)
        if r >= 0:
            self.table.selectRow(r)
            self.table.scrollToItem(self.table.item(r, 0), QAbstractItemView.ScrollHint.PositionAtCenter)

    def _row_selected(self):
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        self.ring.set_current(it.data(Qt.ItemDataRole.UserRole) if it is not None else None)

    @staticmethod
    def _ring(i):
        # 3, 6, 10, 15, 21, 28, 36, 9 strongholds per ring (1.9+)
        n = 0
        for r, k in enumerate((3, 6, 10, 15, 21, 28, 36, 9), start=1):
            n += k
            if i <= n:
                return r
        return 8

    def _fill(self, rows, corr):
        if rows and rows[0][0] == "error":
            self.summary.setText("Error: " + rows[0][1]); return
        self.ring.set_rows(rows)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        mono = theme.mono_font(9.5)
        total = 0
        for r, (idx, x, z, eyes, maxy) in enumerate(rows):
            total += eyes
            dist = int(math.hypot(x, z))
            vals = [(str(idx), idx), (str(self._ring(idx)), self._ring(idx)), (str(x), x), (str(z), z),
                    (f"{eyes}/12", eyes), ("█" * eyes + "░" * (12 - eyes), eyes),
                    (str(maxy) if maxy is not None else "—", maxy if maxy is not None else -9999),
                    (f"{dist:,}".replace(",", " "), dist)]
            vals.pop()                                     # (no Distance column any more)
            if not self.corr:
                vals.append(("1.16 only", -2))
            elif corr.get(idx - 1) and int(corr[idx - 1].get("corruptible", 0)) > 0:
                vals.append(("checking…", -1))              # checked by Minecraft below
            else:
                vals.append(("✗", 0))
            for cidx, (text, key) in enumerate(vals):
                it = _Num(text); it.setData(Qt.ItemDataRole.UserRole, key)
                it.setFont(mono)
                if cidx in (4, 5):
                    it.setForeground(QColor(eye_color(eyes)))
                if cidx == 7:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if not self.corr:
                        it.setForeground(QColor(theme.TEXT3))
                        it.setToolTip("Broken portals are checked in Java 1.16 / 1.16.1 only "
                                      "(the checker is the real Minecraft 1.16 generation).")
                    elif key == 0:
                        it.setForeground(QColor(theme.DANGER))
                        it.setToolTip("Not broken: no ruined portal reaches this end portal.")
                    else:
                        it.setForeground(QColor(theme.TEXT3))
                        it.setToolTip("A ruined portal can reach the portal: checking it with "
                                      "the real Minecraft generation…")
                self.table.setItem(r, cidx, it)
            go = QToolButton(); icons.bind(go, "target", 15)        # (15/11) a small icon, not a big « Go »
            go.setToolTip("Centre the map on it")
            go.setAutoRaise(True); go.setFixedSize(26, 24)
            go.setCursor(Qt.CursorShape.PointingHandCursor)
            go.clicked.connect(lambda _=False, xx=x, zz=z: self.goto.emit(xx, zz))
            holder = QWidget(); hl = QHBoxLayout(holder); hl.setContentsMargins(0, 0, 6, 0)
            hl.addStretch(1); hl.addWidget(go)
            self.table.setCellWidget(r, len(vals), holder)
            self.table.item(r, 0).setData(Qt.ItemDataRole.UserRole + 1, (x, z))
        self.table.setSortingEnabled(True)
        # the last sort chosen (column + order) is kept between seeds (03/10)
        from PySide6.QtCore import QSettings
        qs = QSettings("SSA", "SimpleSeedAnalyser")
        try:
            col = int(qs.value("stronghold/list_sort_col", 4))
            desc = str(qs.value("stronghold/list_sort_desc", "true")).lower() in ("true", "1")
        except (TypeError, ValueError):
            col, desc = 4, True
        if not (0 <= col < self.table.columnCount() - 1) or self.table.isColumnHidden(col):
            col = 4
        self.table.sortItems(col, Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder)

        def _remember(c, order):
            qs.setValue("stronghold/list_sort_col", int(c))
            qs.setValue("stronghold/list_sort_desc",
                        "true" if order == Qt.SortOrder.DescendingOrder else "false")
        self.table.horizontalHeader().sortIndicatorChanged.connect(_remember)
        n = len(rows)
        self.summary.setText(f"{n} strongholds · {total} eyes · avg {total / max(1, n):.1f}")
        if self.corr:
            self._check_broken([(idx, x, z) for (idx, x, z, _e, _m) in rows
                                if corr.get(idx - 1) and int(corr[idx - 1].get("corruptible", 0)) > 0])

    def _check_broken(self, todo):
        """Sends the reachable strongholds to the Minecraft checker (background)."""
        if not todo:
            return
        try:
            import mc_checker
            ok, why = mc_checker.status()
        except Exception as e:
            ok, why = False, str(e)
        if not ok:
            for idx, _x, _z in todo:
                self._loader.broken.emit(idx, why)
            return
        seed, mc, sig = self.seed, self.mc, self._loader.broken

        def run():
            import ruined_portal as rp
            try:
                pool = mc_checker.pool()
            except Exception as e:
                for idx, _x, _z in todo:
                    sig.emit(idx, str(e))
                return
            for idx, x, z in todo:
                try:
                    rq = rp.corrupt_request_for(int(seed), mc, sx=x, sz=z)
                except Exception:
                    rq = None
                if rq is None:
                    sig.emit(idx, dict(forced=0, natural=0))
                    continue
                frames, _ex, _ez, rox, roz = rq

                def cb(res, err, idx=idx):
                    sig.emit(idx, res if res is not None else (err or "checker error"))
                pool.submit(int(seed), frames, rox, roz, cb, natural="auto",
                            sh=getattr(rq, "sh", None))
        threading.Thread(target=run, daemon=True).start()

    def _set_broken(self, idx, res):
        """One checker answer → the « Broken » cell of stronghold #idx."""
        for r in range(self.table.rowCount()):
            it0 = self.table.item(r, 0)
            if it0 is None or it0.data(Qt.ItemDataRole.UserRole) != idx:
                continue
            it = self.table.item(r, 7)
            if it is None:
                return
            sorting = self.table.isSortingEnabled()
            self.table.setSortingEnabled(False)
            if isinstance(res, dict):
                n = int(res.get("forced", 0))
                if n > 0:
                    nat = int(res.get("natural", -1))
                    it.setText(f"✓ {n}" + ("" if nat > 0 else " /tp"))
                    it.setForeground(QColor(theme.OK))
                    it.setToolTip(f"Broken by a ruined portal: {n} frame(s) missing in Minecraft"
                                  + ("" if nat > 0 else " — when the portal's chunks are "
                                     "generated before the ruined portal's chunk (arrive "
                                     "from the portal's side, see the Stronghold page)."))
                    it.setData(Qt.ItemDataRole.UserRole, n)
                else:
                    it.setText("✗"); it.setForeground(QColor(theme.DANGER))
                    it.setToolTip("Checked with Minecraft: not broken.")
                    it.setData(Qt.ItemDataRole.UserRole, 0)
            else:
                it.setText("?"); it.setForeground(QColor(theme.WARN))
                it.setToolTip("Not checked: " + str(res))
                it.setData(Qt.ItemDataRole.UserRole, -1)
            self.table.setSortingEnabled(sorting)
            return

    def _go(self, r):
        it = self.table.item(r, 0)
        if it is not None:
            x, z = it.data(Qt.ItemDataRole.UserRole + 1)
            self.goto.emit(int(x), int(z))
