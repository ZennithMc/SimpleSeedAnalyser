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

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from . import struct_icons, theme
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


class StrongholdListDialog(QDialog):
    goto = Signal(int, int)

    def __init__(self, seed, mc, strongholds=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("All strongholds")
        self.resize(700, 640)
        self.seed, self.mc = seed, mc
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(8)
        top = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(struct_icons.pixmap("stronghold", 30)); top.addWidget(ic)
        t = QLabel("All strongholds"); t.setObjectName("PageTitle"); top.addWidget(t)
        top.addStretch(1)
        self.summary = QLabel("Computing…"); self.summary.setObjectName("Muted"); top.addWidget(self.summary)
        v.addLayout(top)
        v.addWidget(body("Click a column to sort. Double-click a row (or Go) to centre the map on it.",
                         muted=True, small=True))
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
        self.table.verticalHeader().setDefaultSectionSize(26)
        hh = self.table.horizontalHeader()
        for i in range(len(cols)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        if not self.corr:                     # broken portals: checked in 1.16 only
            self.table.setColumnHidden(7, True)
            hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._go(r))
        v.addWidget(self.table, 1)
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
            go = QPushButton("Go"); go.setProperty("kind", "ghost")
            go.clicked.connect(lambda _=False, xx=x, zz=z: self.goto.emit(xx, zz))
            self.table.setCellWidget(r, len(vals), go)
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
