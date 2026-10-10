"""
qtui/stronghold_page.py — the Stronghold workspace (eyes, top Y, broken portal).

  ┌ Search ─────────────┐┌ Map (strongholds + eye counts) ┐┌ Results ───────────┐
  │ Eyes | Top Y | Broken ││                                ││ seed · eyes · x z … │
  │ options of the mode   ││                                ││                     │
  │ support for the       ││                                ││                     │
  │ current version       ││                                ││                     │
  └───────────────────────┘└────────────────────────────────┘└─────────────────────┘
  ┌ run bar: mode · start · threads · stop after · seed list · Start / Stop ────────┐

Engines: stronghold_eyes.search_end_portal_seeds (eyes / top Y, CPU or GPU) and
ruined_portal.search_corrupted_portals (broken portal). The page only offers the
combinations the backend really supports for the version of the top bar, and
says why an option is unavailable (see `support`).
"""
from __future__ import annotations

import math
import os
import threading

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QTime, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from .widgets import AdaptiveButton, user_path, remember_path
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QSpinBox, QSplitter, QStackedWidget,
                               QTableView, QTimeEdit, QToolButton, QVBoxLayout, QWidget)

from . import icons, struct_icons, theme
from .map_view import MapView
from .search_runner import SearchRunner
from .sessions_page import SessionsPage, fmt_int, fmt_rate, fmt_time
from .stronghold_dialogs import eye_color
from .widgets import Segmented, body, section_title
from .world import random_seed

MODES = ("Eyes", "Top Y", "Broken portal")
VERIFIED_EYE_VERSIONS = {20, 21, 26, 28}          # checked in game (1.16, 1.17, 1.21.x)


def p_eyes_at_least(n: int) -> float:
    """P(first stronghold has ≥ n eyes): each of the 12 frames has an eye with
    probability 0.1 (binomial)."""
    return sum(math.comb(12, k) * 0.1 ** k * 0.9 ** (12 - k) for k in range(n, 13))


def gpu_ready():
    try:
        import gaider_cuda as g
        return bool(g.is_available())
    except Exception:
        return False


# measured 26/09 against the exact count (see SSA_justesse_moteur.md, E20-E24)
FAST_NOTE_118 = ("Fast prefilter: checks the portal chunk as if the portal room were alone in "
                 "it — true for about 1 stronghold in 3, so it finds about 1/3 of the matching "
                 "seeds, but ~30× faster than the complete search (≈ 10× more results per "
                 "minute). Untick it to check every seed.")
FAST_NOTE_PRE118 = ("Fast prefilter: about 7× faster, but it skips roughly half of the "
                    "matching seeds (measured on 1.16). Untick it to check every seed.")


_TOPY_GPU = None
_ROOM_OK = None


def _room_native_ok():
    """gaider_layouts knows the portal-room Top Y (checked once)."""
    global _ROOM_OK
    if _ROOM_OK is None:
        import stronghold_eyes as se
        _ROOM_OK = se.native_room_ok()
    return _ROOM_OK


def topy_gpu_ready():
    """(usable, why) for the Top Y GPU library (loaded and self-tested once)."""
    global _TOPY_GPU
    if _TOPY_GPU is None:
        try:
            import topy_gpu
            lib, msg = topy_gpu.load()
            _TOPY_GPU = (lib is not None, msg)
        except Exception as e:
            _TOPY_GPU = (False, str(e))
    return _TOPY_GPU


def gpu_family(mc: int) -> bool:
    """Does the GPU eye search scan 48-bit families (layout pipeline) for this
    version? 1.18+ always; 1.13-1.17 when the pre-1.18 layouts are built."""
    if mc >= 22:
        return True
    try:
        import gaider_layouts as gl
        return bool(gl.has_pre118())
    except Exception:
        return False


def support(mode: str, mc: int, all128: bool, gpu: bool, min_eyes: int = 12, fast: bool = False):
    """(runs, [notes]) for this combination — derived from the backends."""
    notes = []
    pre118 = mc <= 21
    if mode == "Eyes":
        if min_eyes >= 12 and not gpu and not all128:
            notes.append("12/12 on the CPU uses a quick one-chunk check: very fast, but it "
                         "finds only a small part of the 12-eye seeds.")
        if min_eyes >= 12 and gpu and not all128 and not pre118:
            notes.append("GPU 12/12 (Gaider layouts): tests the strongholds that stay in their "
                         "ring chunk with the portal room first in its chunk — about 1 in 12 of "
                         "the 12-eye seeds, but at billions of seeds per second.")
        elif fast and not gpu and not all128:
            notes.append(FAST_NOTE_PRE118 if pre118 else FAST_NOTE_118)
        elif not gpu and not all128:
            notes.append("Complete search: every seed is checked exactly"
                         + (" (~20 ms per seed and process in 1.18+)." if not pre118 else "."))
        if mc not in VERIFIED_EYE_VERSIONS:
            notes.append("Eye counts for this version have not been checked in game yet.")
        if mc == 24:
            notes.append("“Java 1.19” = 1.19.3 / 1.19.4. For a 1.19.0 – 1.19.2 world choose "
                         "“Java 1.19.2”: the eyes are drawn with another random sequence.")
        elif mc == 23:
            notes.append("1.19.0 – 1.19.2 (for 1.19.3 / 1.19.4 choose “Java 1.19”).")
        if gpu and all128 and not pre118:
            notes.append("GPU: the all-128 scan runs on the CPU in 1.18+.")
        elif gpu and all128:
            notes.append("GPU computes the 128 positions; eyes are checked on the CPU.")
        elif gpu and gpu_family(mc):
            notes.append("GPU scans 48-bit families: Start is a 48-bit value and each "
                         "family is its 65 536 seeds.")
        return True, notes
    if mode == "Top Y":
        if gpu:
            notes.append("Top Y is computed on the CPU only.")
        return True, notes
    # broken portal
    if mc == 21:
        # 1.17 protects the frames against almost all of a ruined portal and against
        # the lava border of lakes, and its lakes are placed with another random
        # sequence: the 1.16 checker would give wrong results — not offered (03/10)
        return False, ["Broken portals are not searched in 1.17: the frames are protected "
                       "against almost all of a ruined portal and the 1.16 check would give "
                       "wrong results. Use 1.16 or 1.18+."]
    try:
        import ruined_portal as rp
        has = rp.has_corrupted() and (pre118 or rp.has_118())
    except Exception:
        has = False
    if not has:
        return False, ["The broken-portal library is not available for this version "
                       "(rebuild the native libraries)."]
    if not pre118:
        notes.append("1.18+: first stronghold only, portal Y is not filtered.")
        if all128:
            notes.append("'All 128 strongholds' is not supported in 1.18+ — first stronghold only.")
    if gpu:
        notes.append("Broken portals are searched on the CPU only.")
    return True, notes


# what breaks the end portal (03/10): one colour per cause, used by the cause
# chips and by the results (dot before the seed, colour of the "Checked" text)
CAUSES = (("ruined", "ruined portal", "Ruined portal", "#b46cf0"),
          ("lava", "lava lake", "Lava lake", "#ff7a1f"),
          ("water", "water lake", "Water lake", "#3aa2ff"))
_CAUSE_HEX = {c[1]: c[3] for c in CAUSES}
# 1.16 spawn categories (09/10, Sessions results)
_CAUSE_HEX.update({"lava": "#ff6a1f", "cave": "#b98cff", "structure": "#e3b341",
                   "stuck": "#c0504d", "chance": "#c9a06a", "feat_ok": "#3fb27f", "feat_no": "#c0504d", "water": "#4aa3ff", "surface": "#8a8f98", "normal": "#8a8f98"})
_DOTS = {}


def cause_color(cause) -> str:
    """The cause colour, made readable on the current theme."""
    col = _CAUSE_HEX.get(str(cause or ""), None)
    if col is None:
        return theme.TEXT2
    try:
        return theme.readable(col, theme.TEXT, [theme.BG1, theme.BG2], 3.0)
    except Exception:
        return col


def cause_dot(cause) -> QIcon:
    col = cause_color(cause)
    ic = _DOTS.get(col)
    if ic is None:
        pm = QPixmap(12, 12); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(col)); p.drawEllipse(2, 2, 8, 8); p.end()
        ic = _DOTS[col] = QIcon(pm)
    return ic


class CauseChip(QPushButton):
    """A coloured on/off chip for one cause (same API as a QCheckBox)."""

    def __init__(self, cause, text, parent=None):
        super().__init__(text, parent)
        self.cause = cause
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda *_: self.restyle())
        self.restyle()

    def restyle(self):
        col = cause_color(self.cause)
        on = self.isChecked() and self.isEnabled()
        self.setIcon(cause_dot(self.cause if on else ""))
        bg = theme._rgba(col, 0.22) if on else "transparent"
        bd = col if on else theme.LINE
        fg = theme.TEXT if on else theme.TEXT3
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: 1px solid {bd}; border-radius: 11px;"
            f" color: {fg}; padding: 3px 10px 3px 6px; font-weight: {600 if on else 400}; }}"
            f"QPushButton:hover {{ border-color: {col}; }}"
            f"QPushButton:disabled {{ color: {theme.TEXT3}; border-style: dashed; }}")

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.EnabledChange:
            self.restyle()


def chunk_order_hint(trig, broken, what="ruined portal"):
    """How to get the frames broken in game when it depends on the chunk order:
    the frames' chunk(s) must be generated BEFORE the chunk of the cause (03/10).
    Returns the how-to text ('' if nothing to say)."""
    tx, tz = int(trig[0]), int(trig[1])
    chunks = sorted({(int(b[0]) >> 4, int(b[2]) >> 4) for b in broken} - {(tx, tz)})
    if not chunks:
        return ""
    cx = round(sum(c[0] for c in chunks) / len(chunks))
    cz = round(sum(c[1] for c in chunks) / len(chunks))
    sx = (cx > tx) - (cx < tx)
    sz = (cz > tz) - (cz < tz)
    side = [n for n, on in (("north", sz < 0), ("south", sz > 0), ("east", sx > 0),
                            ("west", sx < 0)) if on]
    # render distance 2: stand 2 chunks beyond the frames, the cause's chunk is
    # then 3 chunks away — generated (decorated) only when you come closer
    px, pz = (cx + 2 * sx) * 16 + 8, (cz + 2 * sz) * 16 + 8
    return (f"The {what} of chunk {tx} {tz} breaks the frames of chunk(s) "
            f"{', '.join(f'{a} {b}' for a, b in chunks)} only if those are generated BEFORE it.\n"
            f"New world, render distance 2, spectator: /tp {px} 120 {pz} (the "
            f"{'-'.join(side)} side), wait a few seconds, then fly slowly to the portal. "
            f"Arriving from the other side, or a /tp straight into chunk {tx} {tz}, "
            f"leaves the frames intact.")


def lake_order_hint(trig, broken, ey):
    """Lakes: not confirmed in the game (03/10) — same hint, 'unconfirmed' label."""
    return "? lake (unconfirmed)", chunk_order_hint(trig, broken, "lake")


from broken_search import run_broken_sources as _run_broken_sources   # noqa: E402 (03/10)


class ScopeSeg(Segmented):
    """First / First ring / All 128 (03/10). Keeps the old checkbox API of
    `all128` (isChecked = more than the first, toggled) for the rest of the page."""
    toggled = Signal(bool)
    SCAN = {"First": False, "First ring": 3, "All 128": True}

    def __init__(self, current="First", parent=None):
        super().__init__(list(self.SCAN), current, parent)
        tips = {"First": "The first stronghold only (the fastest).",
                "First ring": "The 3 strongholds of the first ring (the 3 nearest to 0, 0): "
                              "about 3× slower.",
                "All 128": "Every stronghold of the seed (8 rings): the slowest. A seed can "
                           "appear several times, once per matching stronghold."}
        for k, b in self.buttons.items():
            b.setToolTip(tips[k])
        self.changed.connect(lambda v: self.toggled.emit(v != "First"))

    def isChecked(self):
        return self.value() != "First"

    def setChecked(self, on):                 # old saved state (a checkbox)
        v = "All 128" if on else "First"
        if v != self.value():
            self.set_value(v)
            self.changed.emit(v)

    def scan(self):
        return self.SCAN.get(self.value(), False)

    def count(self):
        return {"First": 1, "First ring": 3}.get(self.value(), 128)


# ═════════════════════════════════════════════════════════════════════════════
class ShResults(QAbstractTableModel):
    COLS = {"Eyes": ("#", "Seed", "Eyes", "X", "Z"),
            "Top Y": ("#", "Seed", "Top Y", "Eyes", "X", "Z"),
            "Broken portal": ("#", "Seed", "Frames", "Portal (x y z)", "Checked")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "Eyes"
        self.rows = []                      # (seed, dict) shown
        self.all_rows = []                  # every result (sisters included)
        self.hide_sisters = True
        self._keys = set()
        self._mono = theme.mono_font(9.5)

    def set_mode(self, mode):
        self.beginResetModel(); self.mode = mode; self.rows = []; self.all_rows = []
        self._keys = set(); self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS[self.mode])

    topy_label = "Top Y"                      # "Room Y" for a portal-room search

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and o == Qt.Orientation.Horizontal:
            name = self.COLS[self.mode][s]
            return self.topy_label if name == "Top Y" else name
        return None

    def _val(self, r, name):
        seed, d = self.rows[r]
        return {"#": r + 1, "Seed": seed, "Eyes": d.get("eyes"), "X": d.get("x"), "Z": d.get("z"),
                "Top Y": d.get("maxy"), "Frames": d.get("frames"),
                "Portal (x y z)": (f"{d['x']} {d['y']} {d['z']}" if "y" in d else None),
                "Checked": d.get("checked")}.get(name)

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        name = self.COLS[self.mode][idx.column()]
        v = self._val(idx.row(), name)
        if role == Qt.ItemDataRole.DisplayRole:
            if v is None:
                return "—"
            if name == "Eyes":
                return f"{v}/12"
            if name == "Frames":
                return f"{v}/12"
            return str(v)
        if role == Qt.ItemDataRole.DecorationRole and name == "Seed" and self.mode == "Broken portal":
            cause = self.rows[idx.row()][1].get("cause")
            return cause_dot(cause) if cause else None
        if role == Qt.ItemDataRole.ToolTipRole and self.mode == "Broken portal":
            d = self.rows[idx.row()][1]
            cause = d.get("cause")
            tip = f"Broken by a {cause}" if cause else ""
            if d.get("hint"):
                tip += ("\n\n" if tip else "") + d["hint"]
            return tip or None
        if role == Qt.ItemDataRole.FontRole and name != "#":
            return self._mono
        if role == Qt.ItemDataRole.ForegroundRole:
            if name == "#":
                return QColor(theme.TEXT3)
            if name == "Eyes" and v is not None:
                return QColor(eye_color(int(v)))
            if name == "Checked" and v:
                if str(v).startswith("✓"):
                    cause = self.rows[idx.row()][1].get("cause")
                    return QColor(cause_color(cause) if cause else theme.OK)
                if str(v).startswith("?"):
                    return QColor(theme.TEXT3)
                return QColor(theme.DANGER)
        if role == Qt.ItemDataRole.TextAlignmentRole and name == "#":
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    @staticmethod
    def sister_key(seed, d):
        """Sister seeds (03/10): same 48 low bits AND same stronghold (start / portal
        chunk) = the very same stronghold, only the biomes / terrain differ."""
        x, z = d.get("x"), d.get("z")
        if x is None or z is None:
            return None
        return (int(seed) & ((1 << 48) - 1), int(x) >> 4, int(z) >> 4)

    def _visible(self, batch):
        if not self.hide_sisters:
            return list(batch)
        out = []
        for seed, d in batch:
            k = self.sister_key(seed, d)
            if k is None or k not in self._keys:
                if k is not None:
                    self._keys.add(k)
                out.append((seed, d))
        return out

    def add(self, batch):
        if batch:
            self.all_rows.extend(batch)
            vis = self._visible(batch)
            if not vis:
                return
            n = len(self.rows)
            self.beginInsertRows(QModelIndex(), n, n + len(vis) - 1)
            self.rows.extend(vis)
            self.endInsertRows()

    def set_hide_sisters(self, on):
        self.hide_sisters = bool(on)
        self.beginResetModel()
        self._keys = set()
        self.rows = self._visible(self.all_rows)
        self.endResetModel()

    def clear(self):
        self.beginResetModel(); self.rows = []; self.all_rows = []; self._keys = set()
        self.endResetModel()

    def set_checked(self, seed, text):
        for r, (s, d) in enumerate(self.rows):
            if s == seed:
                d["checked"] = text
                c = self.COLS[self.mode].index("Checked") if "Checked" in self.COLS[self.mode] else 0
                self.dataChanged.emit(self.index(r, c), self.index(r, c))


class _Verify(QObject):
    progress = Signal(str)
    one = Signal(object, str)          # seed is 64-bit
    done = Signal(str)


# ═════════════════════════════════════════════════════════════════════════════
class StrongholdPage(QWidget):
    status = Signal(str)
    parse_count = staticmethod(SessionsPage.parse_count)
    _read_rule = SessionsPage._read_rule

    def __init__(self, world, parent=None, link=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.world = world
        self.runner = SearchRunner(self)
        self.seed_list = None
        self._verify = _Verify(self)
        self._verify.progress.connect(lambda t: self.verify_lbl.setText(t))
        self._verify.one.connect(lambda s, t: self.results.set_checked(s, t))
        self._verify.done.connect(self._verify_done)

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 2, 10, 4); outer.setSpacing(8)
        split = QSplitter(Qt.Orientation.Horizontal); split.setHandleWidth(10)
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)
        split.addWidget(self._build_left())
        self.map = MapView(world, link=link)
        split.addWidget(self.map)
        split.addWidget(self._build_results())
        split.setStretchFactor(1, 1)
        split.setSizes([360, 820, 330])
        outer.addWidget(self._build_runbar())

        self.runner.resultsReady.connect(self._results_in)
        self.runner.progress.connect(self._progress)
        self.runner.engine.connect(lambda lab, tip: (self.engine_chip.setText(lab),
                                                     self.engine_chip.setToolTip(tip)))
        self.runner.stateChanged.connect(self._run_state)
        self.runner.message.connect(self._msg)
        self.runner.failed.connect(lambda e: QMessageBox.critical(self, "Search failed", e))
        world.changed.connect(self._refresh_support)
        # the end-of-search summary stays until a search parameter changes
        for sig in (self.mode_seg.changed, self.min_eyes.valueChanged, self.eyes_cmp.currentIndexChanged,
                    self.ignore_on.toggled, self.ignore_val.valueChanged, self.fast_pre.toggled,
                    self.use_gpu.toggled, self.all128.toggled, self.min_y.valueChanged,
                    self.min_frames.valueChanged, self.src_ruined.toggled, self.src_lava.toggled,
                    self.src_water.toggled, self.start_seed.textEdited, self.threads.valueChanged,
                    self.rule_kind.currentIndexChanged, self.rule_seeds.textEdited,
                    self.rule_results.valueChanged, self.rule_time.timeChanged, self.list_btn.clicked):
            sig.connect(self._clear_summary)
        self.min_y.valueChanged.connect(self._refresh_support)   # Top Y 1.16: family engine ≥ 56
        self._set_mode("Eyes")
        self._run_state("idle")

    # ── left panel ──────────────────────────────────────────────────────────
    def _build_left(self):
        left = QFrame(); left.setObjectName("SidePanel"); left.setMinimumWidth(330)
        v = QVBoxLayout(left); v.setContentsMargins(16, 14, 14, 12); v.setSpacing(8)
        top = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(struct_icons.pixmap("stronghold", 26)); top.addWidget(ic)
        t = QLabel("Stronghold search"); t.setObjectName("PanelTitle"); top.addWidget(t); top.addStretch(1)
        v.addLayout(top)
        self.mode_seg = Segmented(list(MODES), "Eyes")
        self.mode_seg.changed.connect(self._set_mode)
        v.addWidget(self.mode_seg)
        self.stack = QStackedWidget()
        v.addWidget(self.stack)

        # Eyes
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 6, 0, 0); l.setSpacing(6)
        # the portal itself chooses the eye count (03/10); the spin box / combo stay
        # as the page's values (not shown)
        from .stronghold_visuals import PortalPicker
        self.min_eyes = QSpinBox(w); self.min_eyes.setRange(1, 12); self.min_eyes.setValue(12)
        self.min_eyes.hide(); self.min_eyes.valueChanged.connect(self._refresh_support)
        self.eyes_cmp = QComboBox(w); self.eyes_cmp.addItems(["at least", "exactly"])
        self.eyes_cmp.hide()
        self.portal = PortalPicker(12)
        self.portal.changed.connect(self.min_eyes.setValue)
        self.min_eyes.valueChanged.connect(self.portal.set_value)
        l.addWidget(self.portal)
        row = QHBoxLayout(); row.setSpacing(8)
        self.eyes_cmp_seg = Segmented(["At least", "Exactly"], "At least")
        self.eyes_cmp_seg.setFixedWidth(150)
        self.eyes_cmp_seg.changed.connect(
            lambda v: self.eyes_cmp.setCurrentIndex(1 if v == "Exactly" else 0))
        self.eyes_cmp.currentIndexChanged.connect(lambda i: (
            self.portal.set_exact(i == 1),
            self.eyes_cmp_seg.set_value("Exactly" if i == 1 else "At least")))
        row.addWidget(self.eyes_cmp_seg); row.addStretch(1)
        self.eyes_prob = QLabel(""); self.eyes_prob.setObjectName("Chip")
        self.eyes_prob.setToolTip("Seeds whose first stronghold has this many eyes "
                                  "(each frame has an eye with probability 1/10).")
        row.addWidget(self.eyes_prob)
        l.addLayout(row)
        ig = QHBoxLayout()
        self.ignore_on = QCheckBox("Ignore strongholds with")
        self.ignore_val = QSpinBox(); self.ignore_val.setRange(0, 12); self.ignore_val.setValue(12)
        self.ignore_val.setSuffix(" eyes"); self.ignore_val.setEnabled(False)
        self.ignore_on.toggled.connect(self.ignore_val.setEnabled)
        self.ignore_on.setToolTip("Useful with 'all 128 strongholds': hide the known 12-eye one to "
                                  "see the others.")
        ig.addWidget(self.ignore_on); ig.addWidget(self.ignore_val); ig.addStretch(1)
        l.addLayout(ig)
        self.fast_pre = QCheckBox("Fast prefilter (finds only part of the matches)")
        self.fast_pre.setChecked(True)
        self.fast_pre.setToolTip("On: much faster, more results per minute, but some matching "
                                 "seeds of the range are skipped.\nOff (complete): every seed "
                                 "of the range is checked exactly.")
        self.fast_pre.toggled.connect(self._refresh_support)
        l.addWidget(self.fast_pre)
        self.use_gpu = QCheckBox("Use the GPU (CUDA)")
        self.use_gpu.toggled.connect(self._refresh_support)
        l.addWidget(self.use_gpu)
        self.stack.addWidget(w)

        # Top Y
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 6, 0, 0); l.setSpacing(6)
        from .stronghold_visuals import HeightGauge
        # what must be high: the whole stronghold, or its portal room (03/10)
        from PySide6.QtCore import QSettings as _QS4
        t0 = str(_QS4("SSA", "SimpleSeedAnalyser").value("stronghold/topy_target", "Stronghold"))
        self.topy_target = Segmented(["Stronghold", "Portal room"],
                                     t0 if t0 in ("Stronghold", "Portal room") else "Stronghold")
        self.topy_target.buttons["Stronghold"].setToolTip(
            "The highest block of the whole stronghold (any room or corridor).")
        self.topy_target.buttons["Portal room"].setToolTip(
            "The top of the end portal room: the portal itself is close to the surface.\n"
            "Same exact generation (the whole stronghold decides how far Minecraft moves\n"
            "it down), only the measured height changes.")
        l.addWidget(self.topy_target)
        row = QHBoxLayout()
        lab = QLabel("TOP OF THE STRONGHOLD  ≥"); lab.setObjectName("FieldLabel")
        self._topy_lab = lab
        row.addWidget(lab); row.addStretch(1)
        self.min_y = QSpinBox(); self.min_y.setRange(-64, 320); self.min_y.setValue(60)
        self.min_y.setPrefix("Y ")
        row.addWidget(self.min_y); l.addLayout(row)
        self.gauge = HeightGauge(60)
        self.gauge.changed.connect(self.min_y.setValue)
        self.min_y.valueChanged.connect(self.gauge.set_value)
        lab.setToolTip(self.gauge.toolTip()); self.min_y.setToolTip(self.gauge.toolTip())
        l.addWidget(self.gauge)

        def _show_target(v):
            room = v == "Portal room"
            self._topy_lab.setText("TOP OF THE PORTAL ROOM  ≥" if room else "TOP OF THE STRONGHOLD  ≥")
            self.gauge.set_room(room)

        def _target(v):
            _QS4("SSA", "SimpleSeedAnalyser").setValue("stronghold/topy_target", v)
            _show_target(v)
            self._clear_summary()
            self._refresh_support()
        self.topy_target.changed.connect(_target)
        _show_target(self.topy_target.value())
        # 1.16 « 48-bit family »: the GPU scans families next to the CPU workers (03/10)
        from PySide6.QtCore import QSettings as _QS3
        self.topy_gpu = QCheckBox("Use the GPU too (CPU + GPU)")
        self.topy_gpu.setChecked(str(_QS3("SSA", "SimpleSeedAnalyser").value(
            "stronghold/topy_gpu", "true")).lower() in ("true", "1"))
        self._topy_gpu_tip = (
            "48-bit family mode (1.16): the graphics card computes the strongholds of\n"
            "whole families next to the CPU processes — about twice the families per\n"
            "second on an RTX 5080 + 24 threads. Every result is still checked exactly\n"
            "on the CPU. Best for high thresholds (Y ≥ 68): below, the CPU check of\n"
            "the many tall strongholds is the limit.")
        self.topy_gpu.toggled.connect(lambda v: (_QS3("SSA", "SimpleSeedAnalyser").setValue(
            "stronghold/topy_gpu", bool(v)), self._refresh_support()))
        l.addWidget(self.topy_gpu)
        self.stack.addWidget(w)

        # Broken portal
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 6, 0, 0); l.setSpacing(6)
        # the portal with its broken frames chooses the minimum (03/10)
        from .stronghold_visuals import PortalPicker
        self.min_frames = QSpinBox(w); self.min_frames.setRange(1, 12); self.min_frames.setValue(1)
        self.min_frames.hide()
        self.broken_pick = PortalPicker(1, style="broken")
        self.broken_pick.changed.connect(self.min_frames.setValue)
        self.min_frames.valueChanged.connect(self.broken_pick.set_value)
        tip = ("Frames of the end portal that are really missing in game.\n"
               "Ruined portals and (in 1.16) lakes can break them; the search finds\n"
               "candidates quickly and the real Minecraft generation decides.\n"
               "Click a frame to choose the minimum.")
        self.broken_pick.setToolTip(tip)
        l.addWidget(self.broken_pick)
        # what breaks the frames (03/10): coloured chips, the same colours as the results
        crow = QHBoxLayout(); crow.setSpacing(6)
        self.src_ruined, self.src_lava, self.src_water = (
            CauseChip(c[1], {"ruined": "Ruined", "lava": "Lava", "water": "Water"}[c[0]]) for c in CAUSES)
        for cb in (self.src_ruined, self.src_lava, self.src_water):
            # lakes: off by default, not confirmed in the game (03/10, see the tooltip)
            cb.setChecked(cb is self.src_ruined)
            cb.toggled.connect(self._refresh_support)
            crow.addWidget(cb)
        crow.addStretch(1); l.addLayout(crow)
        self.src_ruined.setToolTip("Ruined portal over the end portal: its netherrack, lava and air\n"
                                   "pockets replace frames (1.13–1.16; from 1.17 only its hanging\n"
                                   "netherrack columns can).")
        lake_tip = ("EXPERIMENTAL — no lake-broken portal confirmed in the game yet: the\n"
                    "seeds tested in 1.16.1 / 1.16.4 had intact frames (the checker's chunk\n"
                    "order is not the game's: when the portal room is generated first, its\n"
                    "air and lava make Minecraft cancel the lake).\n\n"
                    "1.16 only. A lake placed by a neighbouring chunk spills into the portal\n"
                    "room after it was generated: its top layer becomes air and a lava\n"
                    "lake's border becomes stone. ~45× more frequent than ruined portals in\n"
                    "1.16 (mostly lava), usually 1–6 frames, sometimes 12/12.\n"
                    "From 1.17 the frames are protected against the lava border, from 1.18\n"
                    "against lakes entirely.")
        self.src_lava.setToolTip(lake_tip); self.src_water.setToolTip(lake_tip)
        # exact confirmation by the real Minecraft generation, inside the search (02/10)
        self.auto_check = QCheckBox("Confirm with Minecraft")
        self.auto_check.setChecked(True)
        self._auto_tip = (
            "Each candidate is generated by the real Minecraft world generation (the\n"
            "seed-checker) and only the really broken portals are listed, with the real\n"
            "number of broken frames. A few seconds per candidate, several workers.\n"
            "✓ broken = a ruined portal breaks the frames in both chunk orders tested;\n"
            "✓ via /tp = broken when the portal's chunks load first (/tp straight onto it);\n"
            "? lake (unconfirmed) = the checker breaks them, the game did not so far\n"
            "(lakes are experimental, off by default).")
        self.auto_check.setToolTip(self._auto_tip)
        self.auto_check.toggled.connect(self._refresh_support)
        vr = QHBoxLayout(); vr.setSpacing(8)
        vr.addWidget(self.auto_check); vr.addStretch(1)
        self.b_verify = QPushButton("Verify"); self.b_verify.setProperty("kind", "ghost")
        self.b_verify.setToolTip("Check the selected results (or all of them) with Minecraft.")
        self.b_verify.clicked.connect(self._verify_selected)
        vr.addWidget(self.b_verify); l.addLayout(vr)
        # warnings only (the normal case is in the tooltip)
        self.auto_lbl = body("", muted=True, small=True); l.addWidget(self.auto_lbl)
        self.verify_lbl = body("", muted=True, small=True); l.addWidget(self.verify_lbl)
        self.stack.addWidget(w)

        # which strongholds (03/10): the first (fastest), the first ring (the 3
        # nearest) or all 128 (8 rings, the slowest); `all128` keeps its name
        self.all128 = ScopeSeg()
        self.all128.toggled.connect(self._refresh_support)
        srow = QHBoxLayout(); srow.setSpacing(8)
        _sl = QLabel("Strongholds"); _sl.setObjectName("FieldLabel")
        srow.addWidget(_sl); srow.addWidget(self.all128, 1)
        v.addLayout(srow)
        v.addSpacing(6)
        v.addWidget(section_title("Support for this version"))
        self.support_lbl = body("", small=True)
        v.addWidget(self.support_lbl)
        v.addStretch(1)
        return left

    def _build_results(self):
        right = QFrame(); right.setObjectName("SidePanel"); right.setMinimumWidth(280)
        v = QVBoxLayout(right); v.setContentsMargins(14, 12, 12, 10); v.setSpacing(8)
        top = QHBoxLayout()
        t = QLabel("Results"); t.setObjectName("PanelTitle"); top.addWidget(t); top.addStretch(1)
        self.res_count = QLabel("0"); self.res_count.setObjectName("Chip"); self.res_count.setProperty("state", "off")
        top.addWidget(self.res_count)
        ex = QToolButton(); icons.bind(ex, "save", 17); ex.setToolTip("Export (TXT / CSV)")
        ex.clicked.connect(self._export); top.addWidget(ex)
        cl = QToolButton(); icons.bind(cl, "trash", 17); cl.setToolTip("Clear")
        cl.clicked.connect(lambda: (self.results.clear(), self._count()) if self.runner.state not in
                           ("running", "stopping") else None)
        top.addWidget(cl)
        v.addLayout(top)
        self.results = ShResults(self)
        # sister seeds (03/10): same 48 low bits + same stronghold -> one result is enough
        from PySide6.QtCore import QSettings as _QS3
        self.hide_sisters = QCheckBox("Hide sister seeds")
        self.hide_sisters.setChecked(_QS3("SSA", "SimpleSeedAnalyser").value(
            "stronghold/hide_sisters", "true") in (True, "true"))
        self.hide_sisters.setToolTip(
            "Sister seeds share their 48 low bits and land on the same stronghold: the same\n"
            "stronghold, only the biomes / terrain differ. On: one result per stronghold\n"
            "(the others are kept, untick to show them).\n\n"
            "Top Y « 48-bit family »: set when the search starts. Ticked, the search\n"
            "looks for ONE seed per stronghold (fastest); unticked, it lists every seed\n"
            "of each tall stronghold (hundreds per stronghold, slower check).")
        self.results.hide_sisters = self.hide_sisters.isChecked()

        def _hs(v):
            _QS3("SSA", "SimpleSeedAnalyser").setValue("stronghold/hide_sisters",
                                                       "true" if v else "false")
            self.results.set_hide_sisters(v)
            self._count()
        self.hide_sisters.toggled.connect(_hs)
        v.addWidget(self.hide_sisters)
        self.table = QTableView(); self.table.setObjectName("Results"); self.table.setModel(self.results)
        from PySide6.QtGui import QKeySequence as _QKS, QShortcut as _QSc
        _cp = _QSc(_QKS.StandardKey.Copy, self.table)       # the seeds, not the "#" cell
        _cp.setContext(Qt.ShortcutContext.WidgetShortcut)
        _cp.activated.connect(lambda: QGuiApplication.clipboard().setText(
            "\n".join(str(s) for s in dict.fromkeys(self._selected_seeds()))))
        self.table.verticalHeader().hide(); self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(34)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().currentRowChanged.connect(self._selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        v.addWidget(self.table, 1)
        v.addWidget(body("Click a result: the seed is loaded and the map centred on the "
                         "stronghold. Double-click it on the map for its details.",
                         muted=True, small=True))
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

        self.smode = Segmented(["Incremental", "48-bit family"], "Incremental")
        self.smode.setToolTip("Set by the engine: the GPU eye search scans 48-bit families "
                              "(65 536 seeds each, Start = 48-bit value); the CPU and the other "
                              "modes scan seeds one by one.\n\nTop Y 1.16 (Y ≥ 56): your choice.\n"
                              "• 48-bit family (default): tests every stronghold the 65 536 seeds "
                              "of a 48-bit family\n  can have, and lists ONE seed per stronghold "
                              "— about 2.5× more strongholds per\n  second than Incremental for "
                              "a record hunt (Y ≥ 68). Start = 48-bit value.\n"
                              "• Incremental: seed by seed.")
        from PySide6.QtCore import QSettings as _QS2
        self._topy_pref = str(_QS2("SSA", "SimpleSeedAnalyser").value("stronghold/topy_mode2",
                                                                     "48-bit family"))
        self.smode.changed.connect(self._smode_chosen)
        labeled("Mode", self.smode)
        self.start_seed = QLineEdit("0"); self.start_seed.setFixedWidth(190)
        self.start_seed.setFont(theme.mono_font(9.5))
        sw = QWidget(); sl = QHBoxLayout(sw); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(2)
        sl.addWidget(self.start_seed)
        dice = QToolButton(); icons.bind(dice, "dice", 16); dice.setToolTip("Random start")
        sl.addWidget(dice)
        # « Manual / Saved » start, the Saved one shared with Sessions and Draw (03/10)
        from .start_source import StartSource
        self.start_src = StartSource("stronghold", self.start_seed, ("Manual", "Saved"), (
            "Manual start: the seed typed here.",
            "Saved start: the same start as the Sessions and Draw pages, kept between\n"
            "launches. When a search started from it stops, it moves on to the last\n"
            "seed searched — the next search (on any page) continues from there."), self)
        dice.clicked.connect(lambda: (self.start_seed.setText(str(random_seed() >> 1)),
                                      self.start_src.edited(self.start_seed.text())))
        self.start_src.bind_dice(dice)
        row.addLayout(self.start_src.layout(sw, "First seed (Incremental) or first 48-bit value "
                                                "(48-bit family)."))
        self.threads = QSpinBox(); self.threads.setRange(1, max(1, min(61, os.cpu_count() or 4)))
        self.threads.setValue(max(1, (os.cpu_count() or 4) - 1))
        labeled("Processes", self.threads, "Worker processes (every one is used).")
        self.rule_kind = QComboBox()
        for text, key in (("Never", "never"), ("Seeds searched", "seeds"),
                          ("Results found", "results"), ("Time", "time")):
            self.rule_kind.addItem(text, key)
        self.rule_seeds = QLineEdit("100M"); self.rule_seeds.setFixedWidth(110)
        self.rule_results = QSpinBox(); self.rule_results.setRange(1, 10_000_000); self.rule_results.setValue(100)
        self.rule_time = QTimeEdit(); self.rule_time.setDisplayFormat("H 'h' mm 'min'"); self.rule_time.setTime(QTime(1, 0))
        self.rule_stack = QStackedWidget(); self.rule_stack.setFixedWidth(112)
        self.rule_stack.addWidget(QLabel(""))
        for wd in (self.rule_seeds, self.rule_results, self.rule_time):
            wd.setFixedWidth(110); self.rule_stack.addWidget(wd)
        self.rule_kind.currentIndexChanged.connect(self.rule_stack.setCurrentIndex)
        rw = QWidget(); rl = QHBoxLayout(rw); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)
        rl.addWidget(self.rule_kind); rl.addWidget(self.rule_stack)
        labeled("Stop after", rw, "Stronghold searches cannot be paused yet: a results / time limit "
                                  "stops the search.")
        self.list_btn = QPushButton("Seed list…"); self.list_btn.setProperty("kind", "ghost")
        self.list_btn.clicked.connect(self._seed_list)
        labeled("Source", self.list_btn)
        row.addStretch(1)
        self.b_start = AdaptiveButton("  Start"); self.b_start.setProperty("kind", "primary")
        icons.bind(self.b_start, "play", 16, "#ffffff", "#ffffff"); self.b_start.clicked.connect(self._start)
        self.b_stop = AdaptiveButton("  Stop"); icons.bind(self.b_stop, "stop", 16)
        self.b_stop.setProperty("kind", "danger"); self.b_stop.clicked.connect(self._stop)
        # Pause / Resume (03/10): Pause stops where the search is and keeps the
        # results; Resume starts again from there (same settings, results kept)
        self.b_pause = AdaptiveButton("  Pause"); icons.bind(self.b_pause, "pause", 16)
        self.b_pause.clicked.connect(self._pause_resume)
        self.b_pause.setEnabled(False)
        self._paused = None; self._pausing = False
        row.addWidget(self.b_start, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.b_pause, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.b_stop, 0, Qt.AlignmentFlag.AlignBottom)
        v.addLayout(row)
        prow = QHBoxLayout(); prow.setSpacing(12)
        self.prog = QProgressBar(); self.prog.setRange(0, 1000); self.prog.setTextVisible(False)
        self.prog.setFixedHeight(6); prow.addWidget(self.prog, 1)
        self.stat = QLabel("Idle"); self.stat.setObjectName("Muted"); self.stat.setFont(theme.mono_font(9))
        prow.addWidget(self.stat)
        self.engine_chip = QLabel("—"); self.engine_chip.setObjectName("Chip"); self.engine_chip.setProperty("state", "off")
        prow.addWidget(self.engine_chip)
        v.addLayout(prow)
        return bar

    # ── mode / support ──────────────────────────────────────────────────────
    def _set_mode(self, mode):
        self.mode = mode
        self.stack.setCurrentIndex(MODES.index(mode))
        if self.runner.state not in ("running", "stopping"):
            self.results.set_mode(mode); self._count()
        self._refresh_support()

    def _refresh_support(self, *_):
        mc = int(self.world.mc)
        gpu_ok = gpu_ready()
        self.use_gpu.setEnabled(gpu_ok and self.mode == "Eyes")
        if not gpu_ok:
            self.use_gpu.setToolTip("No usable CUDA GPU (see the GPU chip in the top bar).")
        if self.mode == "Top Y":
            fam_ok = self._topy_family(mc, self.all128.isChecked(), self.seed_list,
                                       self.min_y.value())
            tg_ok, tg_why = topy_gpu_ready()
            self.topy_gpu.setVisible(fam_ok)
            self.topy_gpu.setEnabled(fam_ok and tg_ok)
            self.topy_gpu.setToolTip(self._topy_gpu_tip + ("" if tg_ok else "\n\nUnavailable: " + tg_why))
        n = self.min_eyes.value()
        p = p_eyes_at_least(n) if self.eyes_cmp.currentIndex() == 0 else \
            math.comb(12, n) * 0.1 ** n * 0.9 ** (12 - n)
        from .stronghold_visuals import one_in
        self.eyes_prob.setText(one_in(p) + " seeds" + (f"  · ×{self.all128.count()}"
                                                       if self.all128.isChecked() else ""))
        self.gauge.set_mc(mc)
        gpu = self.use_gpu.isChecked() and self.use_gpu.isEnabled()
        all128_ok = not (self.mode == "Broken portal" and mc >= 22)
        self.all128.setEnabled(all128_ok)
        if self.mode == "Broken portal":
            import lake_portals as lp
            lakes_ok = mc in lp.LAKE_VERSIONS
            for cb in (self.src_lava, self.src_water):
                cb.setEnabled(lakes_ok)
                if not lakes_ok:
                    cb.setToolTip("Lakes break end portals in 1.16 only (frames are protected "
                                  "against them from 1.17 / 1.18).")
            for cb in (self.src_ruined, self.src_lava, self.src_water):
                cb.restyle()                      # theme colours may have changed
            _src = self._broken_sources(mc)
            self.broken_pick.set_causes([c[3] for c in CAUSES if c[0] in _src])
            ok, why = self._auto_status(mc)
            self.auto_check.setEnabled(ok)
            self.auto_check.setToolTip(self._auto_tip + "\n\n" + why)
            # the panel shows only what needs attention; the rest is in the tooltips
            if not ok:
                warn = why
            elif not self.auto_check.isChecked():
                warn = "Unconfirmed candidates — verify them afterwards."
            elif mc == 21:
                warn = ("1.17: confirmed with the 1.16 rules — a ruined-portal result may be "
                        "intact in 1.17 (hover for details).")
            else:
                warn = ""
            self.auto_lbl.setText(warn)
            self.auto_lbl.setVisible(bool(warn))
        self.fast_pre.setVisible(self.min_eyes.value() < 12)
        runs, notes = support(self.mode, mc, self.all128.isChecked() and all128_ok, gpu,
                              self.min_eyes.value(), self.fast_pre.isChecked())
        head = (f"<b style='color:{theme.OK}'>✓ {self.world.version_name}</b>" if runs else
                f"<b style='color:{theme.DANGER}'>✗ {self.world.version_name}</b>")
        self.support_lbl.setText(head + ("<br>" + "<br>".join("• " + x for x in notes) if notes else
                                         " — fully supported (CPU" + (" + GPU" if gpu_ok and
                                                                     self.mode == "Eyes" else "") + ")"))
        if (self.mode == "Top Y" and self.topy_target.value() == "Portal room"
                and not _room_native_ok()):
            runs = False
            self.support_lbl.setText(self.support_lbl.text() + "<br>• Portal room: rebuild the "
                                     "stronghold library first (py tools/build_native.py --layouts).")
        if self.mode == "Broken portal" and not self._broken_sources(mc):
            runs = False
            self.support_lbl.setText(self.support_lbl.text() + "<br>• Choose at least one cause "
                                     "(ruined portals / lakes).")
        self.b_start.setEnabled(runs and self.runner.state not in ("running", "stopping"))
        # the scan order is fixed by the engine: the GPU layout pipeline scans
        # 48-bit families, every other path scans seeds one by one
        fam = gpu and not (self.all128.isChecked() and all128_ok) and self.seed_list is None \
            and gpu_family(mc) and self.mode == "Eyes"
        choice = self._topy_eligible(mc, self.all128.isChecked() and all128_ok,
                                     self.seed_list, self.min_y.value())
        fam = fam or self._topy_family(mc, self.all128.isChecked() and all128_ok,
                                       self.seed_list, self.min_y.value())
        self.smode.set_value("48-bit family" if fam else "Incremental")
        self.smode.setEnabled(choice and self.runner.state not in ("running", "stopping"))

    def _topy_eligible(self, mc, all128, seed_list, min_y):
        """Top Y 1.16 where the 48-bit family engine can run (the user chooses)."""
        import topy_family as tf
        room = self.topy_target.value() == "Portal room"
        return (self.mode == "Top Y" and mc in tf.VERSIONS and not all128
                and seed_list is None
                and min_y >= (tf.MIN_FAST_Y_ROOM if room else tf.MIN_FAST_Y))

    def _topy_family(self, mc, all128, seed_list, min_y):
        """Top Y 1.16 by 48-bit families (topy_family, 03/10): exact, and much
        faster for a high threshold (tall strongholds are rare)."""
        return (self._topy_eligible(mc, all128, seed_list, min_y)
                and self._topy_pref == "48-bit family")

    def _smode_chosen(self, v):
        from PySide6.QtCore import QSettings
        self._topy_pref = v
        QSettings("SSA", "SimpleSeedAnalyser").setValue("stronghold/topy_mode2", v)
        self._refresh_support()

    def _broken_sources(self, mc):
        """The causes searched: subset of ("ruined", "lava", "water")."""
        import lake_portals as lp
        out = []
        if self.src_ruined.isChecked():
            out.append("ruined")
        if mc in lp.LAKE_VERSIONS:
            if self.src_lava.isChecked():
                out.append("lava")
            if self.src_water.isChecked():
                out.append("water")
        return tuple(out)

    def _auto_status(self, mc):
        """(usable, text) of the exact Minecraft confirmation for this version."""
        if mc >= 22:
            return False, ("1.18+: confirm the results afterwards (right-click → Verify with "
                           "the Minecraft checker).")
        if not hasattr(self, "_mcc_status"):
            try:
                import mc_checker
                self._mcc_status = mc_checker.status()
            except Exception as e:                      # noqa: BLE001
                self._mcc_status = (False, str(e))
        ok, why = self._mcc_status
        if not ok:
            return False, "Exact confirmation unavailable: " + why + "."
        note = "" if mc == 19 else " (checked with the 1.16.1 generation)"
        if mc == 21:
            note += (". Careful in 1.17: the frames are protected against most of a ruined "
                     "portal (only its hanging netherrack columns can break them), so a result "
                     "confirmed with the 1.16 rules may be intact in 1.17")
        return True, "Each candidate is confirmed by Minecraft 1.16.1" + note + "."

    # ── run ─────────────────────────────────────────────────────────────────
    def _pause_resume(self):
        if self.runner.state == "running":
            self._pausing = True
            self.runner.stop()
        elif self._paused is not None:
            self._start(resume=True)

    def _stop(self):
        if self.runner.state in ("running", "pausing"):
            self.runner.stop()
        self._paused = None                    # Stop = the end, nothing to resume
        self._pause_ui()

    def _pause_ui(self):
        running = self.runner.state in ("running", "pausing", "stopping")
        if self._paused is not None and not running:
            self.b_pause.setText("  Resume"); icons.bind(self.b_pause, "play", 16)
            self.b_pause.setEnabled(True)
            self.b_pause.setToolTip(f"Continue from seed {self._paused['start']} "
                                    "(same settings, the results are kept).")
            self.b_stop.setEnabled(True)
        else:
            self.b_pause.setText("  Pause"); icons.bind(self.b_pause, "pause", 16)
            self.b_pause.setEnabled(running and self.runner.state == "running"
                                    and self.seed_list is None)
            self.b_pause.setToolTip("Stop here and keep the results: Resume continues "
                                    "from where it stopped." if self.seed_list is None else
                                    "A seed-list search cannot be paused.")

    def _start(self, resume=False):
        if not resume:
            self._paused = None
        try:
            try:
                start = int(self.start_seed.text().strip() or 0)
                if resume and self._paused is not None:
                    start = int(self._paused["start"])
            except ValueError:
                raise ValueError("The start seed must be an integer.")
            if not (-(1 << 63) <= start < (1 << 63)):     # (above 2^63: OverflowError)
                raise ValueError("The start seed must fit in 64 bits.")
            rule = self._read_rule()
        except ValueError as e:
            QMessageBox.warning(self, "Start", str(e) or "The start seed must be an integer.")
            return
        mc = int(self.world.mc)
        mode = self.mode
        all128 = self.all128.scan() if self.all128.isEnabled() else False   # False / 3 / True
        seed_list = self.seed_list
        gpu = self.use_gpu.isChecked() and self.use_gpu.isEnabled() and mode == "Eyes"
        fam = gpu and not all128 and seed_list is None and gpu_family(mc)
        topy_fam = self._topy_family(mc, all128, seed_list, self.min_y.value())
        topy_gpu_on = (topy_fam and self.topy_gpu.isChecked() and self.topy_gpu.isEnabled())
        # family mode: one seed per stronghold only when the sisters are hidden anyway
        topy_one = self.hide_sisters.isChecked()
        count = rule[1] if rule[0] == "seeds" else 0
        if resume and self._paused is not None and count:
            count = max(1, count - int(self._paused.get("done", 0)))
            rule = ("seeds", count)
        min_eyes = self.min_eyes.value()
        exact = self.eyes_cmp.currentIndex() == 1
        ignore = self.ignore_val.value() if self.ignore_on.isChecked() else None
        min_y = self.min_y.value()
        topy_room = mode == "Top Y" and self.topy_target.value() == "Portal room"
        if topy_room:                          # the threshold is for the portal room top
            import stronghold_eyes as _se
            min_y = _se.RoomY(min_y)
        min_frames = self.min_frames.value()
        sources = self._broken_sources(mc)
        # 1.18+: the range prefilter is complete (grid, validated exactly);
        # 1.13-1.17: the LCG prefilter is lossy -> only when asked
        fast = self.fast_pre.isChecked() and min_eyes < 12

        auto = (mode == "Broken portal" and self.auto_check.isChecked()
                and self.auto_check.isEnabled())
        vsig = self._verify

        def job(run_start, run_count, threads, stop, on_match, on_progress, on_mode):
            # exact resume point (03/10): blocks finish in any order, so "start +
            # seeds done" could skip a slow block still running at the pause
            from broken_search import ResumeTracker
            trk = ResumeTracker(run_start, ["ruined", "lakes", "e"]) if seed_list is None else None
            self._exact_next = None
            try:
                _job(run_start, run_count, threads, stop, on_match, on_progress, on_mode, trk)
            finally:
                if trk is not None and trk.used and not getattr(self, "_exact_fam", False):
                    self._exact_next = trk.point()

        def _job(run_start, run_count, threads, stop, on_match, on_progress, on_mode, trk):
            self._exact_fam = False
            if mode == "Broken portal" and auto:
                self._broken_auto(run_start, run_count, threads, stop, on_match, on_progress,
                                  mc, all128, seed_list, min_frames, vsig, sources, tracker=trk)
                return
            if mode == "Broken portal":
                def cb(seed, ex, ey, ez, rx, ry, rz, under, *_):
                    if int(under) >= min_frames:
                        on_match(int(seed), dict(x=int(ex), y=int(ey), z=int(ez), frames=int(under),
                                                 ruined=(int(rx), int(ry), int(rz)),
                                                 cause="ruined portal"))

                def lcb(seed, ex, ey, ez, lx, ly, lz, npred, kind, sx, sz, frames, trig):
                    if int(npred) >= min_frames:
                        on_match(int(seed), dict(x=int(ex), y=int(ey), z=int(ez), frames=int(npred),
                                                 cause=f"{kind} lake",
                                                 checked="? unconfirmed",
                                                 req=(frames, trig[0], trig[1], (sx >> 4, sz >> 4))))
                _run_broken_sources(sources, run_start, run_count, threads, stop, on_progress, mc,
                                    all128, seed_list, cb, lcb, tracker=trk)
                return
            import stronghold_eyes as se

            def cb(seed, eyes, bx, bz, maxy=None, *_):
                on_match(int(seed), dict(eyes=int(eyes), x=int(bx), z=int(bz), maxy=maxy))
            if topy_fam:                          # 1.16 Top Y by 48-bit families
                import topy_family as tf

                def fprog(done, total):
                    on_progress(min(done, total) if total > 0 else done, total)
                self._exact_fam = True

                def _fam_resume(f):
                    self._exact_next = int(f)
                tf.search_topy_families(run_start & ((1 << 48) - 1), run_count, min_y, cb, stop,
                                        fprog, threads, mc, one_per_family=topy_one,
                                        use_gpu=topy_gpu_on, status_callback=on_mode,
                                        resume_callback=_fam_resume)
                return
            s0, n0 = run_start, run_count
            if fam:                               # GPU families: start = 48-bit, count in seeds
                s0 = run_start & ((1 << 48) - 1)

            def prog(done, total):                # the GPU reports whole families (can pass 100 %)
                on_progress(min(done, total) if total > 0 else done, total)
            se.search_end_portal_seeds(
                s0, n0, 0 if mode == "Top Y" else min_eyes, cb, stop, progress_callback=prog,
                num_workers=threads, mc_version=mc, fast_mode=fast, use_gpu=gpu,
                min_max_y=min_y if mode == "Top Y" else None, seed_list=seed_list,
                scan_128=all128, exact_eyes=exact and mode == "Eyes",
                ignore_eyes=ignore if mode == "Eyes" else None,
                chunk_callback=(trk.chunk_cb("e") if (trk is not None and not fam) else None))

        label = {"Eyes": "Eyes", "Top Y": "Top Y" + (" · portal room" if topy_room else ""),
                 "Broken portal": "Broken portal" + (" · Minecraft-confirmed" if auto else "")}[mode]
        label += " · " + ("GPU + CPU check" if gpu else
                          f"CPU + GPU · {self.threads.value()} processes" if topy_gpu_on else
                          f"CPU · {self.threads.value()} processes")
        if fam or topy_fam:
            label += " · 48-bit family"
        if all128:
            label += " · 128" if all128 is True else " · first ring"
        elif mode == "Eyes" and not gpu and min_eyes < 12:
            label += " · fast prefilter" if fast else " · complete"
        if not resume:                         # Resume keeps the results of before
            self.results.set_mode(mode); self._count()
        self._resume_done = (int(self._paused.get("done", 0))
                             if resume and self._paused is not None else 0)
        self._paused = None
        self.results.topy_label = "Room Y" if topy_room else "Top Y"
        self.start_src.search_started()
        if seed_list is not None:              # a seed list ignores the start (03/10)
            self.start_src.from_saved = False
        self._run_fam = bool(fam or topy_fam)
        self.runner.start([], mc, start, self.threads.value(), "inc", seed_list, rule,
                          _count=count, job=job, engine_label=label)

    @staticmethod
    def _broken_auto(run_start, run_count, threads, stop, on_match, on_progress, mc, all128,
                     seed_list, min_frames, vsig, sources=("ruined",), tracker=None):
        """Broken portal search where every candidate is confirmed by the real
        Minecraft generation (mc_checker) before it becomes a result (02/10).
        Candidates stream to warm Minecraft workers while the search goes on; the
        search ends once the last candidate is checked (Stop: the ones not started
        yet are dropped)."""
        import threading as _th
        import time as _t
        import mc_checker
        import ruined_portal as rp
        vsig.progress.emit("Starting Minecraft 1.16.1… (the first time: about a minute to "
                           "prepare it)")
        pool = mc_checker.pool()
        pool.clear()
        st = dict(pending=0, sent=0, ok=0, rejected=0, failed=0, by={})
        lock = _th.Lock()
        idle = _th.Event(); idle.set()

        def note():
            by = "  ".join(f"<b style='color:{cause_color(c)}'>● {n}</b>"
                           for c, n in st["by"].items() if n)
            msg = (f"Minecraft: {by or '<b>0</b>'} broken · "
                   f"{st['rejected']} not broken"
                   + (f" · {st['pending']} waiting" if st["pending"] else "")
                   + (f" · <span style='color:{theme.DANGER}'>{st['failed']} failed</span>"
                      if st["failed"] else "")
                   + f" · {pool.n} worker{'s' if pool.n > 1 else ''}")
            vsig.progress.emit(msg)

        def make_cb(seed, ex, ey, ez, rx, ry, rz, cause="ruined portal", req=None):
            def done(res, err):
                with lock:
                    st["pending"] -= 1
                    if res is None:
                        st["failed"] += 1
                    elif res["forced"] >= max(1, min_frames):
                        st["ok"] += 1
                        st["by"][cause] = st["by"].get(cause, 0) + 1
                    else:
                        st["rejected"] += 1
                    if st["pending"] == 0:
                        idle.set()
                if res is not None and res["forced"] >= max(1, min_frames):
                    nat = res.get("natural", -1)
                    lab = "✓ broken" if nat > 0 else "✓ via /tp"     # the cause = the colour
                    hint = ""
                    if "lake" in cause and req is not None:   # always order-dependent
                        lab, hint = lake_order_hint((req[1], req[2]), res.get("broken", []), ey)
                    elif nat == 0 and req is not None:        # ruined portal, /tp order only
                        hint = chunk_order_hint((req[1], req[2]), res.get("broken", []))
                    d = dict(x=int(ex), y=int(ey), z=int(ez), frames=int(res["forced"]),
                             checked=lab, cause=cause, broken=res.get("broken", []), req=req,
                             hint=hint)
                    if rx is not None:
                        d["ruined"] = (int(rx), int(ry), int(rz))
                    on_match(int(seed), d)
                note()
            return done

        def cb(seed, ex, ey, ez, rx, ry, rz, under, ti=None, sx=None, sz=None, *_):
            try:
                rq = rp.corrupt_request_for(int(seed), mc, sx=sx, sz=sz)
            except Exception:
                rq = None
            if rq is None:
                return
            frames, _epx, _epz, rox, roz = rq
            sh = getattr(rq, "sh", None)
            send(seed, frames, rox, roz, sh,
                 make_cb(seed, ex, ey, ez, rx, ry, rz, req=(frames, rox, roz, sh)))

        def send(seed, frames, rox, roz, sh, done):
            with lock:
                st["pending"] += 1; st["sent"] += 1
                idle.clear()
            if tracker is not None:              # resumes at or before an unanswered check
                tracker.hold(seed)

                def done(res, err, _d=done, _s=int(seed)):
                    tracker.release(_s)
                    _d(res, err)
            pool.submit(int(seed), frames, rox, roz, done, natural="auto", sh=sh)
            note()

        def lcb(seed, ex, ey, ez, lx, ly, lz, npred, kind, sx, sz, frames, trig):
            # the checker decorates the frame chunks first and the lake's chunk last
            sh = (int(sx) >> 4, int(sz) >> 4)
            send(seed, frames, trig[0], trig[1], sh,
                 make_cb(seed, ex, ey, ez, None, None, None, cause=f"{kind} lake",
                         req=(frames, trig[0], trig[1], sh)))

        def throttle():
            """Lakes give many candidates: keep the search just ahead of Minecraft."""
            while not stop.is_set():
                with lock:
                    if st["pending"] < 6 * pool.n + 4:
                        return
                _t.sleep(0.2)

        note()
        _run_broken_sources(sources, run_start, run_count, threads, stop, on_progress, mc,
                            all128, seed_list, cb, lcb, throttle, tracker=tracker)
        if stop.is_set():                       # Stop: drop the checks not started yet
            dropped = 0
            try:
                while True:
                    pool.q.get_nowait(); dropped += 1
            except Exception:
                pass
            with lock:
                st["pending"] -= dropped
                if st["pending"] <= 0:
                    st["pending"] = 0; idle.set()
        while not idle.wait(0.3):               # the last candidates are being checked
            if stop.is_set():
                break
        note()

    def _next_start(self):
        """Where the last search stopped, in the unit of the Start field."""
        r = self.runner
        if r.params is None or r.params.get("seed_list") is not None:
            return None
        ex = getattr(self, "_exact_next", None)
        if ex is not None:                      # exact (blocks reported, 03/10)
            return (int(ex) & ((1 << 48) - 1)) if getattr(self, "_run_fam", False) else int(ex)
        base, done = int(r.base_start), int(r._done_before)
        if getattr(self, "_run_fam", False):     # 48-bit families of 65 536 seeds
            return ((base & ((1 << 48) - 1)) + done // 65536) & ((1 << 48) - 1)
        v = (base + done) % (1 << 64)
        return v - (1 << 64) if v >= 1 << 63 else v

    def _run_state(self, st):
        running = st in ("running", "pausing", "stopping")
        if st in ("done", "error") and getattr(self, "_was_running", False):
            try:
                self.start_src.search_ended(self._next_start())
            except Exception:
                pass
            if self._pausing and st == "done":
                nxt = self._next_start()
                if nxt is not None:
                    done = int(self.runner._done_before) if self.runner.params else 0
                    if getattr(self, "_exact_next", None) is not None:   # what is really done
                        base = int(self.runner.base_start)
                        if getattr(self, "_run_fam", False):
                            done = ((nxt - (base & ((1 << 48) - 1))) % (1 << 48)) * 65536
                        else:
                            done = (nxt - base) % (1 << 64)
                    prev = (self._resume_done if getattr(self, "_resume_done", None) else 0)
                    self._paused = dict(start=nxt, done=prev + done)
            self._pausing = False
        self._was_running = running
        self.b_stop.setEnabled(running)
        for w in (self.mode_seg, self.stack, self.all128, self.threads,
                  self.rule_kind, self.rule_stack, self.list_btn, self.auto_check):
            w.setEnabled(not running)
        self._sync_start_ui(running)
        self._pause_ui()
        if not running:
            self._refresh_support()
            if self.prog.maximum() == 0:                  # unlimited: stop the busy bar
                self.prog.setRange(0, 1000); self.prog.setValue(0)
        else:
            self.b_start.setEnabled(False)
        self.engine_chip.setProperty("state", "ok" if running else "off")
        self.engine_chip.style().unpolish(self.engine_chip); self.engine_chip.style().polish(self.engine_chip)

    def _clear_summary(self, *_):
        if self.runner.state in ("done", "error"):
            self.stat.setText("Idle")
            self.prog.setRange(0, 1000); self.prog.setValue(0)

    def _msg(self, text):
        self.stat.setText(text); self.status.emit(text)

    def _progress(self, d):
        frac = d.get("frac")
        if frac is not None:
            self.prog.setRange(0, 1000); self.prog.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        else:
            self.prog.setRange(0, 0)
        parts = [f"{fmt_int(d['done'])} seeds", fmt_rate(d["sps"]), f"{fmt_int(d['found'])} found",
                 f"elapsed {fmt_time(d['elapsed'])}"]
        if frac is not None:
            parts.insert(1, f"{min(frac, 1) * 100:.1f} %")
        self.stat.setText("  ·  ".join(parts))
        self._count()

    def _results_in(self, batch):
        first = not self.results.rows
        self.results.add(batch); self._count()
        if first or len(self.results.rows) < 200:
            self.table.resizeColumnsToContents()

    def _count(self):
        self.res_count.setText(fmt_int(len(self.results.rows)))
        hidden = len(self.results.all_rows) - len(self.results.rows)
        self.res_count.setToolTip(f"{fmt_int(hidden)} sister seed(s) hidden" if hidden > 0 else "")

    def _sync_start_ui(self, running=None):
        """The start (field, dice, Manual / Saved) is greyed while a seed list is
        the source: the list decides the seeds, the start is ignored (03/10)."""
        if running is None:
            running = self.runner.state in ("running", "pausing", "stopping")
        on = not running and self.seed_list is None
        self.start_seed.setEnabled(on)
        self.start_src.set_enabled(on)
        if self.start_src.dice is not None:
            self.start_src.dice.setEnabled(on)
        self.start_seed.setToolTip("Ignored: the seed list is the source (✕ on the list to go "
                                   "back to a range)." if self.seed_list is not None else
                                   "First seed (Incremental) or first 48-bit value (48-bit family).")

    def _seed_list(self):
        if self.seed_list is not None:
            self.seed_list = None; self.list_btn.setText("Seed list…"); self._refresh_support()
            self._sync_start_ui(); return
        path, _ = QFileDialog.getOpenFileName(self, "Seed list", user_path(), "Seed lists (*.txt *.seeds *.csv);;All files (*.*)")
        if not path:
            return
        remember_path(path)
        from .tools_dialogs import maybe_convert
        path = maybe_convert(self, path)
        try:
            from seedlist_io import SeedListSource
            src = SeedListSource(path); n = len(src)
        except Exception as e:
            QMessageBox.critical(self, "Seed list", f"Could not read the list:\n{e}"); return
        self.seed_list = src
        self.list_btn.setText(f"{os.path.basename(path)} · {fmt_int(n)}  ✕")
        self._refresh_support()
        self._sync_start_ui()

    # ── results ─────────────────────────────────────────────────────────────
    def _selected(self, cur, _prev):
        if not cur.isValid():
            return
        seed, d = self.results.rows[cur.row()]
        self.world.set(seed=seed)
        if d.get("x") is not None:
            self.map.center_on(d["x"], d["z"])          # current zoom kept

    def _selected_seeds(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.results.rows[r][0] for r in rows]

    def _menu(self, pt):
        seeds = self._selected_seeds()
        if not seeds:
            return
        m = QMenu(self)
        a1 = m.addAction(f"Copy seed{'s' if len(seeds) > 1 else ''}")
        a2 = m.addAction("Verify with the Minecraft checker") if self.mode == "Broken portal" else None
        act = m.exec(self.table.viewport().mapToGlobal(pt))
        if act is a1:
            QGuiApplication.clipboard().setText("\n".join(str(s) for s in dict.fromkeys(seeds)))
        elif act is not None and act is a2:
            self._verify_selected()

    def _export(self):
        if not self.results.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export results", user_path("strongholds.txt"),
                                              "Text, one seed per line (*.txt);;CSV (*.csv)")
        if not path:
            return
        cols = ShResults.COLS[self.results.mode]
        remember_path(path)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                if path.lower().endswith(".csv"):
                    f.write(",".join(c for c in cols if c != "#") + "\n")
                    for r in range(len(self.results.rows)):
                        f.write(",".join(str(self.results._val(r, c)) for c in cols if c != "#") + "\n")
                else:
                    for s in dict.fromkeys(s for s, _ in self.results.rows):
                        f.write(f"{s}\n")
        except OSError as e:                      # read-only folder, file in use…
            QMessageBox.warning(self, "Export results", f"Could not write the file:\n{e}")
            return
        self.status.emit(f"Exported to {os.path.basename(path)}.")

    # ── broken-portal verification (real Minecraft checker) ─────────────────
    def _verify_selected(self):
        seeds = list(dict.fromkeys(self._selected_seeds())) or \
            list(dict.fromkeys(s for s, _ in self.results.rows))
        if not seeds:
            self.verify_lbl.setText("No result to verify."); return
        mc = int(self.world.mc)
        if mc < 22 and self._auto_status(mc)[0]:        # the fast exact workers (02/10)
            self._verify_selected_fast(seeds, mc)
            return
        import checker_client as cc
        cc.set_active_mc(mc)
        if not cc.is_available():
            self.verify_lbl.setText(f"Checker unavailable (JDK 17 / project in {cc.checker_dir()})")
            return
        ver = "1.18.1" if mc >= 22 else "1.16.1"
        note = "" if mc in (20, 22) else f" — note: checked with the {ver} generator"
        self.b_verify.setEnabled(False)
        self.verify_lbl.setText(f"Verifying {len(seeds)} seed(s) with Minecraft {ver}{note}…"
                                + ("  (starting the checker, ~25 s)" if not cc.is_running() else ""))
        sig = self._verify

        def work():
            import time as _t
            import ruined_portal as rp
            reqs = []
            for s in seeds:
                try:
                    rq = rp.corrupt_request_118(s, mc) if mc >= 22 else rp.corrupt_request_auto(s, mc)
                except Exception:
                    rq = None
                if rq is None:
                    sig.one.emit(s, "?")
                    continue
                frames, _epx, _epz, rox, roz = rq
                reqs.append((s, frames, rox, roz))
            t0 = _t.time(); done = 0
            for i in range(0, len(reqs), 5):
                part = reqs[i:i + 5]
                try:
                    out = cc.verify(part)
                except Exception:
                    try:
                        cc.stop_server(); cc.start_server(); out = cc.verify(part)
                    except Exception as e:
                        sig.done.emit(f"Checker error: {e}"); return
                for s, _f, _x, _z in part:
                    r = out.get(s)
                    if r is None:
                        sig.one.emit(s, "?")
                    elif int(r.get("forced", 0)) > 0:
                        sig.one.emit(s, f"✓ {r.get('forced')} broken")
                    else:
                        sig.one.emit(s, "✗ not broken")
                done += len(part)
                per = (_t.time() - t0) / max(1, done)
                sig.progress.emit(f"Verifying {done}/{len(reqs)}… about {fmt_time(per * (len(reqs) - done))} left")
            sig.done.emit(f"Verified {len(reqs)} seed(s) with Minecraft {ver}.")
        threading.Thread(target=work, daemon=True).start()

    def _verify_selected_fast(self, seeds, mc):
        """Manual verification (1.13–1.17) with the warm Minecraft workers. A result
        that carries its own request (lake results) is checked with it."""
        stored, causes = {}, {}
        for s, d in self.results.rows:
            if d.get("req") and s not in stored:
                stored[s] = d["req"]
            if d.get("cause") and s not in causes:
                causes[s] = d["cause"]
        self.b_verify.setEnabled(False)
        self.verify_lbl.setText(f"Verifying {len(seeds)} seed(s) with Minecraft 1.16.1…")
        sig = self._verify

        def work():
            import threading as _th
            import mc_checker
            import ruined_portal as rp
            try:
                pool = mc_checker.pool()
            except Exception as e:                      # noqa: BLE001
                sig.done.emit(f"Checker error: {e}"); return
            left = [0]; lock = _th.Lock(); fin = _th.Event()
            for s in seeds:
                rq = stored.get(s)                       # the result's own request (lakes)
                if rq is None:
                    try:
                        rq = rp.corrupt_request_auto(s, mc)
                    except Exception:
                        rq = None
                    if rq is None:
                        sig.one.emit(s, "?")
                        continue
                    rq = (rq[0], rq[3], rq[4], getattr(rq, "sh", None))
                frames, rox, roz, sh = rq
                cause = causes.get(s, "")

                def cb(res, err, s=s):
                    if res is None:
                        sig.one.emit(s, "?")
                    elif res["forced"] > 0:
                        sig.one.emit(s, f"✓ {res['forced']} broken"
                                     + ("" if res.get("natural", -1) > 0 else " via /tp"))
                    else:
                        sig.one.emit(s, "✗ not broken")
                    with lock:
                        left[0] -= 1
                        if left[0] == 0:
                            fin.set()
                with lock:
                    left[0] += 1
                pool.submit(s, frames, rox, roz, cb, natural="auto", sh=sh)
            with lock:
                if left[0] == 0:
                    fin.set()
            fin.wait()
            sig.done.emit(f"Verified {len(seeds)} seed(s) with Minecraft 1.16.1.")
        threading.Thread(target=work, daemon=True).start()

    def _verify_done(self, text):
        self.verify_lbl.setText(text); self.b_verify.setEnabled(True)
