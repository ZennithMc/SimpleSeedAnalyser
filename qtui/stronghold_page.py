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
from PySide6.QtGui import QColor, QGuiApplication
from .widgets import user_path, remember_path
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


# ═════════════════════════════════════════════════════════════════════════════
class ShResults(QAbstractTableModel):
    COLS = {"Eyes": ("#", "Seed", "Eyes", "X", "Z"),
            "Top Y": ("#", "Seed", "Top Y", "Eyes", "X", "Z"),
            "Broken portal": ("#", "Seed", "Frames", "Portal (x y z)", "Checked")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "Eyes"
        self.rows = []                      # (seed, dict)
        self._mono = theme.mono_font(9.5)

    def set_mode(self, mode):
        self.beginResetModel(); self.mode = mode; self.rows = []; self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.COLS[self.mode])

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and o == Qt.Orientation.Horizontal:
            return self.COLS[self.mode][s]
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
        if role == Qt.ItemDataRole.FontRole and name != "#":
            return self._mono
        if role == Qt.ItemDataRole.ForegroundRole:
            if name == "#":
                return QColor(theme.TEXT3)
            if name == "Eyes" and v is not None:
                return QColor(eye_color(int(v)))
            if name == "Checked" and v:
                return QColor(theme.OK) if str(v).startswith("✓") else QColor(theme.DANGER)
        if role == Qt.ItemDataRole.TextAlignmentRole and name == "#":
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def add(self, batch):
        if batch:
            n = len(self.rows)
            self.beginInsertRows(QModelIndex(), n, n + len(batch) - 1)
            self.rows.extend(batch)
            self.endInsertRows()

    def clear(self):
        self.beginResetModel(); self.rows = []; self.endResetModel()

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
                    self.min_frames.valueChanged, self.start_seed.textEdited, self.threads.valueChanged,
                    self.rule_kind.currentIndexChanged, self.rule_seeds.textEdited,
                    self.rule_results.valueChanged, self.rule_time.timeChanged, self.list_btn.clicked):
            sig.connect(self._clear_summary)
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
        row = QHBoxLayout()
        row.addWidget(body("Eyes in the portal", wrap=False)); row.addStretch(1)
        self.min_eyes = QSpinBox(); self.min_eyes.setRange(1, 12); self.min_eyes.setValue(12)
        self.min_eyes.setSuffix(" / 12"); self.min_eyes.valueChanged.connect(self._refresh_support)
        self.eyes_cmp = QComboBox(); self.eyes_cmp.addItems(["at least", "exactly"])
        row.addWidget(self.eyes_cmp); row.addWidget(self.min_eyes)
        l.addLayout(row)
        self.eyes_prob = body("", muted=True, small=True); l.addWidget(self.eyes_prob)
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
        row = QHBoxLayout()
        row.addWidget(body("Top of the stronghold  ≥", wrap=False)); row.addStretch(1)
        self.min_y = QSpinBox(); self.min_y.setRange(-64, 320); self.min_y.setValue(60)
        self.min_y.setPrefix("Y ")
        row.addWidget(self.min_y); l.addLayout(row)
        l.addWidget(body("Highest block of the stronghold after Minecraft moves it below sea "
                         "level — a high stronghold can be seen / reached from the surface.",
                         muted=True, small=True))
        self.stack.addWidget(w)

        # Broken portal
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 6, 0, 0); l.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(body("Frames under the ruined portal  ≥", wrap=False)); row.addStretch(1)
        self.min_frames = QSpinBox(); self.min_frames.setRange(1, 12); self.min_frames.setValue(1)
        row.addWidget(self.min_frames); l.addLayout(row)
        l.addWidget(body("A ruined portal generated over the end portal can break its frames. The "
                         "search finds candidates quickly; about 1 in 15 is really broken. "
                         "Verify with the real Minecraft checker to be sure (slow, a few seconds "
                         "per seed).", muted=True, small=True))
        vr = QHBoxLayout()
        self.b_verify = QPushButton("Verify selected results"); self.b_verify.setProperty("kind", "ghost")
        self.b_verify.clicked.connect(self._verify_selected)
        vr.addWidget(self.b_verify); vr.addStretch(1); l.addLayout(vr)
        self.verify_lbl = body("", muted=True, small=True); l.addWidget(self.verify_lbl)
        self.stack.addWidget(w)

        self.all128 = QCheckBox("All 128 strongholds (not only the first)")
        self.all128.setToolTip("Check every stronghold of the seed (8 rings). Much slower; a seed can "
                               "appear several times, once per matching stronghold.")
        self.all128.toggled.connect(self._refresh_support)
        v.addWidget(self.all128)
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
                              "modes scan seeds one by one.")
        labeled("Mode", self.smode)
        self.start_seed = QLineEdit("0"); self.start_seed.setFixedWidth(190)
        self.start_seed.setFont(theme.mono_font(9.5))
        sw = QWidget(); sl = QHBoxLayout(sw); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(2)
        sl.addWidget(self.start_seed)
        dice = QToolButton(); icons.bind(dice, "dice", 16); dice.setToolTip("Random start")
        dice.clicked.connect(lambda: self.start_seed.setText(str(random_seed() >> 1)))
        sl.addWidget(dice)
        labeled("Start", sw)
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
        self.b_start = QPushButton("  Start"); self.b_start.setProperty("kind", "primary")
        icons.bind(self.b_start, "play", 16, "#ffffff", "#ffffff"); self.b_start.clicked.connect(self._start)
        self.b_stop = QPushButton("  Stop"); icons.bind(self.b_stop, "stop", 16)
        self.b_stop.setProperty("kind", "danger"); self.b_stop.clicked.connect(self.runner.stop)
        row.addWidget(self.b_start, 0, Qt.AlignmentFlag.AlignBottom)
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
        n = self.min_eyes.value()
        p = p_eyes_at_least(n) if self.eyes_cmp.currentIndex() == 0 else \
            math.comb(12, n) * 0.1 ** n * 0.9 ** (12 - n)
        self.eyes_prob.setText(f"First stronghold: about 1 seed in {fmt_int(round(1 / p))}"
                               + (" · with all 128 strongholds, many more matches"
                                  if self.all128.isChecked() else ""))
        gpu = self.use_gpu.isChecked() and self.use_gpu.isEnabled()
        all128_ok = not (self.mode == "Broken portal" and mc >= 22)
        self.all128.setEnabled(all128_ok)
        self.fast_pre.setVisible(self.min_eyes.value() < 12)
        runs, notes = support(self.mode, mc, self.all128.isChecked() and all128_ok, gpu,
                              self.min_eyes.value(), self.fast_pre.isChecked())
        head = (f"<b style='color:{theme.OK}'>✓ {self.world.version_name}</b>" if runs else
                f"<b style='color:{theme.DANGER}'>✗ {self.world.version_name}</b>")
        self.support_lbl.setText(head + ("<br>" + "<br>".join("• " + x for x in notes) if notes else
                                         " — fully supported (CPU" + (" + GPU" if gpu_ok and
                                                                     self.mode == "Eyes" else "") + ")"))
        self.b_start.setEnabled(runs and self.runner.state not in ("running", "stopping"))
        # the scan order is fixed by the engine: the GPU layout pipeline scans
        # 48-bit families, every other path scans seeds one by one
        fam = gpu and not (self.all128.isChecked() and all128_ok) and self.seed_list is None \
            and gpu_family(mc) and self.mode == "Eyes"
        self.smode.set_value("48-bit family" if fam else "Incremental")
        self.smode.setEnabled(False)

    # ── run ─────────────────────────────────────────────────────────────────
    def _start(self):
        try:
            try:
                start = int(self.start_seed.text().strip() or 0)
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
        all128 = self.all128.isChecked() and self.all128.isEnabled()
        seed_list = self.seed_list
        gpu = self.use_gpu.isChecked() and self.use_gpu.isEnabled() and mode == "Eyes"
        fam = gpu and not all128 and seed_list is None and gpu_family(mc)
        count = rule[1] if rule[0] == "seeds" else 0
        min_eyes = self.min_eyes.value()
        exact = self.eyes_cmp.currentIndex() == 1
        ignore = self.ignore_val.value() if self.ignore_on.isChecked() else None
        min_y = self.min_y.value()
        min_frames = self.min_frames.value()
        # 1.18+: the range prefilter is complete (grid, validated exactly);
        # 1.13-1.17: the LCG prefilter is lossy -> only when asked
        fast = self.fast_pre.isChecked() and min_eyes < 12

        def job(run_start, run_count, threads, stop, on_match, on_progress, on_mode):
            if mode == "Broken portal":
                import ruined_portal as rp

                def cb(seed, ex, ey, ez, rx, ry, rz, under, *_):
                    if int(under) >= min_frames:
                        on_match(int(seed), dict(x=int(ex), y=int(ey), z=int(ez), frames=int(under),
                                                 ruined=(int(rx), int(ry), int(rz))))
                rp.search_corrupted_portals(run_start, run_count, cb, stop,
                                            progress_callback=on_progress, num_workers=threads,
                                            mc_version=mc, scan_128=all128, seed_list=seed_list)
                return
            import stronghold_eyes as se

            def cb(seed, eyes, bx, bz, maxy=None, *_):
                on_match(int(seed), dict(eyes=int(eyes), x=int(bx), z=int(bz), maxy=maxy))
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
                ignore_eyes=ignore if mode == "Eyes" else None)

        label = {"Eyes": "Eyes", "Top Y": "Top Y", "Broken portal": "Broken portal"}[mode]
        label += " · " + ("GPU + CPU check" if gpu else f"CPU · {self.threads.value()} processes")
        if fam:
            label += " · 48-bit family"
        if all128:
            label += " · 128"
        elif mode == "Eyes" and not gpu and min_eyes < 12:
            label += " · fast prefilter" if fast else " · complete"
        self.results.set_mode(mode); self._count()
        self.runner.start([], mc, start, self.threads.value(), "inc", seed_list, rule,
                          _count=count, job=job, engine_label=label)

    def _run_state(self, st):
        running = st in ("running", "pausing", "stopping")
        self.b_stop.setEnabled(running)
        for w in (self.mode_seg, self.stack, self.all128, self.start_seed, self.threads,
                  self.rule_kind, self.rule_stack, self.list_btn):
            w.setEnabled(not running)
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

    def _seed_list(self):
        if self.seed_list is not None:
            self.seed_list = None; self.list_btn.setText("Seed list…"); self._refresh_support(); return
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

    def _verify_done(self, text):
        self.verify_lbl.setText(text); self.b_verify.setEnabled(True)
