"""
qtui/draw_page.py — the Draw workspace: paint a terrain over the real map,
find the seeds that look like it.

  ┌ Draw ───────────────┐┌ Map + drawing (transparent) ─────┐┌ Results ─────────┐
  │ tools · paint ·      ││ edit: the drawing at its place   ││ test a seed       │
  │ weights · canvas ·   ││ result: green / red cells at the ││ thumbnails, exact │
  │ summary              ││ position the search found        ││ scores, offsets   │
  └──────────────────────┘└──────────────────────────────────┘└───────────────────┘
  ┌ run bar: min score · keep · start · threads · stop after · Start/Pause/Stop ──┐

Engine: draw_engine.py / cubiomes_src/draw_engine.c (v2). One exact score
everywhere (search, "test a seed", the green / red overlay): the weighted
share of painted cells whose world cell has the painted class, at the best
position within the slide radius.
"""
from __future__ import annotations

import hashlib
import os
import queue
import threading
import time

import numpy as np
from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, QRectF, QSize, Qt, QTime,
                            QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QIcon, QImage, QKeySequence, QPainter,
                           QPixmap, QShortcut)
from .widgets import user_path, remember_path
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QCompleter,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListView, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QStyle,
                               QStyledItemDelegate, QTimeEdit, QToolButton, QVBoxLayout, QWidget)

import cubiomes_wrap as cw
import draw_engine as de

from . import icons, theme
from .draw_canvas import DrawCanvas
from .draw_library import (DrawLibraryDialog, autosave_path, new_entry_path, read_json,
                           write_json)
from .draw_model import CELL_SIZES, MAX_SIDE, WEIGHTS, DrawModel, now_iso
from .sessions_page import SessionsPage, fmt_int, fmt_rate, fmt_time
from .widgets import EasySlider, Segmented, body, section_title
from .world import random_seed

TOOLS = [  # key, icon, label, shortcut, tip
    ("brush", "brush", "Brush", "B", "Paint with the selected class.  Right button: erase."),
    ("eraser", "eraser", "Eraser", "E", "Erase (cells become 'not painted' = ignored)."),
    ("line", "line", "Line", "L", "Straight line with the brush size."),
    ("rect", "rect", "Rect", "R", "Rectangle (filled or outline, see below)."),
    ("circle", "circle", "Circle", "C", "Circle: click its centre, drag to set the radius (filled or outline)."),
    ("fill", "fill", "Fill", "G", "Fill the connected area of the same class."),
    ("weight", "weight", "Weight", "W", "Paint an importance: a ×4 cell counts 4 times in "
                                        "the score.  Right button: back to ×1."),
]


def _swatch(rgb, size=14, border=None) -> QIcon:
    pm = QPixmap(size * 2, size * 2); pm.setDevicePixelRatio(2); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(border or "#00000060")); p.setBrush(QColor(*rgb))
    p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 3, 3); p.end()
    return QIcon(pm)


def _sig_hash(model: DrawModel) -> str:
    h = hashlib.sha1()
    for part in model.signature():
        h.update(part if isinstance(part, bytes) else repr(part).encode())
    return h.hexdigest()


_AVAIL = {}


def biome_available(mc: int, b: int) -> bool:
    """Does biome b generate in the overworld of version mc (cubiomes
    isOverworld)?"""
    key = (int(mc), int(b))
    v = _AVAIL.get(key)
    if v is None:
        try:
            import ctypes
            f = cw.get_lib()._lib.isOverworld
            f.argtypes = [ctypes.c_int, ctypes.c_int]
            f.restype = ctypes.c_int
            v = bool(f(int(mc), int(b)))
        except Exception:
            v = True
        _AVAIL[key] = v
    return v


# ═════════════════════════════════════════════════════════════════════════════
class ResultsModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []                    # (seed, score, ox, oz)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        r = self.rows[idx.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return str(r[0])
        if role == Qt.ItemDataRole.UserRole:
            return r
        return None

    def set_rows(self, rows):
        if rows == self.rows:
            return False
        self.beginResetModel(); self.rows = list(rows); self.endResetModel()
        return True


class _ThumbSignals(QObject):
    ready = Signal(object)                # seed


class ThumbMaker:
    """World preview of each result (the observed classes at the found
    position), computed in a background thread and cached."""

    def __init__(self):
        self.sig = _ThumbSignals()
        self.cache = {}
        self._q = queue.Queue()
        self._engine = None
        self._key = None
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="ssa-draw-thumbs").start()

    def set_spec(self, spec, key):
        with self._lock:
            if key == self._key:
                return
            self._key = key
            self.cache = {}
            try:
                self._engine = de.DrawEngine(spec)
            except Exception:
                self._engine = None
            while not self._q.empty():
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break

    def get(self, row):
        seed = row[0]
        im = self.cache.get((seed, row[2], row[3]))
        if im is None and (seed, row[2], row[3]) not in self.cache:
            self.cache[(seed, row[2], row[3])] = None           # queued
            self._q.put((self._key, row))
        return im

    def _loop(self):
        while True:
            key, row = self._q.get()
            with self._lock:
                eng, cur = self._engine, self._key
            if eng is None or key != cur:
                continue
            seed, _sc, ox, oz = row
            try:
                _s, obs, mm = eng.eval_at(seed, ox, oz)
            except Exception:
                continue
            H, W = obs.shape
            rgba = np.zeros((H, W, 4), np.uint8)
            painted = eng.spec.grid != de.ANY
            for c in np.unique(obs):
                c = int(c)
                col = (70, 70, 80) if c == de.ANY else de.code_color(c)
                rgba[obs == c] = (*col, 255)
            # dim the unpainted cells, outline misses in red
            rgba[~painted, :3] = (rgba[~painted, :3] * 0.45).astype(np.uint8)
            miss = painted & (mm == 0)
            rgba[miss] = (231, 76, 60, 255)
            img = QImage(rgba.tobytes(), W, H, 4 * W, QImage.Format.Format_RGBA8888).copy()
            with self._lock:
                if key == self._key:
                    self.cache[(seed, ox, oz)] = img
            self.sig.ready.emit(seed)


class ResultDelegate(QStyledItemDelegate):
    H = 52

    def __init__(self, page):
        super().__init__(page)
        self.page = page

    def sizeHint(self, opt, idx):
        return QSize(200, self.H)

    def paint(self, p, opt, idx):
        seed, sc, ox, oz = idx.data(Qt.ItemDataRole.UserRole)
        r = QRectF(opt.rect).adjusted(2, 2, -2, -2)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hov = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT_SOFT) if sel else QColor(theme.BG3) if hov else QColor(theme.BG2))
        p.drawRoundedRect(r, 7, 7)
        # thumbnail
        t = QRectF(r.left() + 6, r.top() + 5, r.height() - 10, r.height() - 10)
        im = self.page.thumbs.get((seed, sc, ox, oz))
        if im is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            p.drawImage(t, im)
        else:
            p.setBrush(QColor(theme.BG1)); p.drawRoundedRect(t, 4, 4)
        p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QColor(theme.LINE2)); p.drawRect(t)
        x = t.right() + 10
        # rank + seed
        p.setFont(theme.ui_font(7.5, QFont.Weight.Bold)); p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(x, r.top() + 5, 40, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"#{idx.row() + 1}")
        p.setFont(theme.mono_font(9.5)); p.setPen(QColor(theme.TEXT))
        seed_s = p.fontMetrics().elidedText(str(seed), Qt.TextElideMode.ElideMiddle, int(r.right() - x - 36 - 8))
        p.drawText(QRectF(x + 30, r.top() + 5, r.right() - x - 38, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, seed_s)
        # score bar
        by = r.top() + 27
        bw = r.right() - x - 64
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.BG1))
        p.drawRoundedRect(QRectF(x, by, bw, 7), 3.5, 3.5)
        col = _score_color(sc)
        p.setBrush(col); p.drawRoundedRect(QRectF(x, by, max(3.5, bw * sc), 7), 3.5, 3.5)
        p.setFont(theme.ui_font(8.5, QFont.Weight.Bold)); p.setPen(col)
        p.drawText(QRectF(r.right() - 60, by - 5, 54, 17), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{sc * 100:.1f} %")
        p.setFont(theme.ui_font(7.5)); p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(x, by + 8, bw, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"offset {ox:+d}, {oz:+d}")
        p.restore()


def _score_color(sc):
    a, b, c = QColor("#d9534f"), QColor("#d9b43a"), QColor("#2fbf71")
    t = max(0.0, min(1.0, (sc - 0.5) / 0.5))
    lo, hi, k = (a, b, t * 2) if t < 0.5 else (b, c, (t - 0.5) * 2)
    return QColor(int(lo.red() + (hi.red() - lo.red()) * k), int(lo.green() + (hi.green() - lo.green()) * k),
                  int(lo.blue() + (hi.blue() - lo.blue()) * k))


# ═════════════════════════════════════════════════════════════════════════════
class DrawPage(QWidget):
    status = Signal(str)
    parse_count = staticmethod(SessionsPage.parse_count)
    _read_rule = SessionsPage._read_rule

    def __init__(self, world, parent=None, link=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.world = world
        self.model = DrawModel(self)
        self.thumbs = ThumbMaker()
        self.state = "idle"
        self.search = None                # DrawSearch while running
        self.engine = None                # its DrawEngine
        self.sinfo = None                 # dict: search parameters / progress bookkeeping
        self.rows = []                    # results (seed, score, ox, oz), best first
        self._recent = []
        self._spd = None
        self._autosave_sig = None

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 2, 10, 4); outer.setSpacing(8)
        split = QSplitter(Qt.Orientation.Horizontal); split.setHandleWidth(10)
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)
        self.map = DrawCanvas(world, self.model, link=link)
        split.addWidget(self._build_left())
        split.addWidget(self.map)
        split.addWidget(self._build_results())
        self._build_float()
        split.setStretchFactor(1, 1)
        split.setSizes([372, 790, 320])
        outer.addWidget(self._build_runbar())

        self.thumbs.sig.ready.connect(self._thumb_ready)      # queued: made in a thread
        self.map.cellHover.connect(self._hover)
        self.map.toolUsed.connect(self._leave_result)
        self.map.codePicked.connect(self._picked)
        self.map.brushChanged.connect(self._brush_from_map)
        self.model.changed.connect(self._model_changed)
        world.changed.connect(self._world_changed)
        self._poll = QTimer(self, interval=250, timeout=self._tick)
        self._shortcuts()
        self._set_tool("brush")
        self._set_code(de.LAND)
        self._brush_from_map(self.map.radius)
        self._sync_canvas_controls()
        self._refresh_biomes()
        self._refresh_summary()
        self._run_state("idle")

    def _thumb_ready(self, _seed):
        self.list.viewport().update()

    # ═══ left panel ═════════════════════════════════════════════════════════
    def _build_left(self):
        left = QFrame(); left.setObjectName("SidePanel"); left.setMinimumWidth(368)
        lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(0)
        head = QWidget(); hv = QVBoxLayout(head); hv.setContentsMargins(16, 14, 14, 8); hv.setSpacing(6)
        top = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.pixmap("brush", 22, theme.ACCENT)); top.addWidget(ic)
        t = QLabel("Draw"); t.setObjectName("PanelTitle"); top.addWidget(t); top.addStretch(1)
        for name, tip, fn in (("plus", "New drawing", self._new),
                              ("library", "Drawings library (open / save / import)", self.open_library),
                              ("save", "Save to the library  (Ctrl+S)", self.save),
                              ("undo", "Undo  (Ctrl+Z)", self.model.undo),
                              ("redo", "Redo  (Ctrl+Y)", self.model.redo)):
            b = QToolButton(); icons.bind(b, name, 18); b.setToolTip(tip); b.clicked.connect(fn)
            b.setAutoRaise(True)
            top.addWidget(b)
            if name == "undo":
                self.b_undo = b
            elif name == "redo":
                self.b_redo = b
        hv.addLayout(top)
        self.name_edit = QLineEdit(); self.name_edit.setPlaceholderText("Drawing name")
        self.name_edit.editingFinished.connect(self._name_edited)
        hv.addWidget(self.name_edit)
        lv.addWidget(head)

        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget(); inner.setObjectName("Transparent")
        inner.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        v = QVBoxLayout(inner); v.setContentsMargins(14, 4, 12, 14); v.setSpacing(8)
        scroll.setWidget(inner)
        lv.addWidget(scroll, 1)

        # tools
        v.addWidget(section_title("Tools"))
        tg = QGridLayout(); tg.setSpacing(4)
        self.tool_group = QButtonGroup(self); self.tool_group.setExclusive(True)
        self.tool_btns = {}
        for k, (key, ic, label, sc, tip) in enumerate(TOOLS):
            b = QToolButton(); b.setCheckable(True); b.setObjectName("DrawTool")
            icons.bind(b, ic, 20, "text2", "text"); b.setIconSize(QSize(20, 20))
            b.setText(label); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setFixedSize(68, 50)
            b.setToolTip(f"<b>{label}</b>  ({sc})<br>{tip}")
            b.clicked.connect(lambda _=False, t=key: self._set_tool(t))
            self.tool_group.addButton(b)
            self.tool_btns[key] = b
            tg.addWidget(b, k // 4, k % 4)
        v.addLayout(tg)
        row = QHBoxLayout()
        row.addWidget(body("Size", wrap=False))
        self.size_slider = EasySlider(); self.size_slider.setRange(0, 60); self.size_slider.setValue(3)
        self.size_slider.valueChanged.connect(lambda val: self.map.set_radius(val / 2.0))
        row.addWidget(self.size_slider, 1)
        self.size_lbl = QLabel(); self.size_lbl.setObjectName("Muted"); self.size_lbl.setFixedWidth(64)
        row.addWidget(self.size_lbl)
        v.addLayout(row)
        self.filled = QCheckBox("Filled shapes"); self.filled.setChecked(True)
        self.filled.toggled.connect(lambda on: setattr(self.map, "filled", on))
        v.addWidget(self.filled)

        # paint
        v.addSpacing(4); v.addWidget(section_title("Paint"))
        cur = QHBoxLayout()
        self.cur_swatch = QLabel(); self.cur_swatch.setFixedSize(30, 30)
        cur.addWidget(self.cur_swatch)
        cc = QVBoxLayout(); cc.setSpacing(0)
        self.cur_name = QLabel(); self.cur_name.setObjectName("Body")
        self.cur_name.setFont(theme.ui_font(10, QFont.Weight.DemiBold))
        self.cur_layer = QLabel(); self.cur_layer.setObjectName("Muted")
        cc.addWidget(self.cur_name); cc.addWidget(self.cur_layer)
        cur.addLayout(cc, 1)
        v.addLayout(cur)
        self.code_group = QButtonGroup(self); self.code_group.setExclusive(False)
        self.code_btns = {}

        def chip_grid(entries, cols=3):
            g = QGridLayout(); g.setSpacing(4)
            for k, (code, label, rgb, tip) in enumerate(entries):
                b = QToolButton(); b.setObjectName("PaintChip"); b.setCheckable(True)
                b.setIcon(_swatch(rgb)); b.setText(label.replace("&", "&&"))
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                b.setToolTip(tip)
                b.clicked.connect(lambda _=False, c=code: self._set_code(c))
                self.code_btns[code] = b
                g.addWidget(b, k // cols, k % cols)
            return g
        v.addWidget(body("Terrain  ·  continentalness", muted=True, small=True))
        v.addLayout(chip_grid([(c, lab, rgb, tip) for c, _k, lab, rgb, layer, tip in de.CLASSES
                               if layer == "Continentalness"]))
        v.addWidget(body("Climate  ·  temperature (Java 1.18+)", muted=True, small=True))
        v.addLayout(chip_grid([(c, lab, rgb, tip) for c, _k, lab, rgb, layer, tip in de.CLASSES
                               if layer == "Temperature"], cols=2))
        v.addWidget(body("Biome groups  ·  any biome of the group", muted=True, small=True))
        v.addLayout(chip_grid([(de.group_code(k), lab, rgb,
                                f"{lab}: " + ", ".join(cw.BIOME_NAMES.get(b, str(b)) for b in members))
                               for k, (_key, lab, rgb, members) in enumerate(de.GROUPS)], cols=2))
        v.addWidget(body("One biome", muted=True, small=True))
        self.biome_combo = QComboBox(); self.biome_combo.setEditable(True)
        self.biome_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.biome_combo.lineEdit().setPlaceholderText("Search a biome…")
        comp = self.biome_combo.completer()
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.biome_combo.activated.connect(self._biome_chosen)
        v.addWidget(self.biome_combo)
        self.recent_row = QHBoxLayout(); self.recent_row.setSpacing(4)
        rw = QWidget(); rw.setLayout(self.recent_row)
        self.recent_row.setContentsMargins(0, 0, 0, 0)
        v.addWidget(rw)

        # weights
        v.addSpacing(4); v.addWidget(section_title("Importance (weights)"))
        self.weight_seg = Segmented([f"×{w:g}" if w >= 1 else ("×¼" if w == 0.25 else "×½")
                                     for w in WEIGHTS], "×2")
        self.weight_seg.changed.connect(self._weight_chosen)
        v.addWidget(self.weight_seg)
        wr = QHBoxLayout()
        self.show_w = QCheckBox("Show weights"); self.show_w.toggled.connect(self._show_weights)
        wr.addWidget(self.show_w)
        self.balance = QCheckBox("Balance classes")
        self.balance.setToolTip("Every painted class weighs the same in the score, whatever its "
                                "painted area (a small island counts as much as the ocean around it).")
        self.balance.toggled.connect(self.model.set_balance)
        wr.addWidget(self.balance); wr.addStretch(1)
        rb = QToolButton(); rb.setText("Reset"); rb.setToolTip("All weights back to ×1")
        rb.clicked.connect(self.model.reset_weights); wr.addWidget(rb)
        v.addLayout(wr)

        # canvas / placement
        v.addSpacing(4); v.addWidget(section_title("Canvas & placement"))
        g = QGridLayout(); g.setHorizontalSpacing(8); g.setVerticalSpacing(6)
        g.addWidget(body("Grid", wrap=False), 0, 0)
        gw = QHBoxLayout(); gw.setSpacing(4)
        self.sp_w = QSpinBox(); self.sp_w.setRange(1, MAX_SIDE); self.sp_w.setSuffix(" w")
        self.sp_h = QSpinBox(); self.sp_h.setRange(1, MAX_SIDE); self.sp_h.setSuffix(" h")
        self.sp_w.setKeyboardTracking(False); self.sp_h.setKeyboardTracking(False)
        self.lock = QToolButton(); self.lock.setCheckable(True); self.lock.setChecked(True)
        self.lock.setText("⇔"); self.lock.setToolTip("Square grid")
        self.sp_w.valueChanged.connect(lambda _v: self._grid_edited("w"))
        self.sp_h.valueChanged.connect(lambda _v: self._grid_edited("h"))
        gw.addWidget(self.sp_w); gw.addWidget(self.lock); gw.addWidget(self.sp_h)
        g.addLayout(gw, 0, 1)
        self.keep_area = QCheckBox("Keep the area (resample)")
        self.keep_area.setChecked(True)
        self.keep_area.setToolTip("On: a finer / coarser grid over the same world area (the "
                                  "painting is resampled, the cell size adapts).\nOff: cells keep "
                                  "their size, the canvas grows / shrinks around the centre.")
        g.addWidget(self.keep_area, 1, 1)
        g.addWidget(body("Cell", wrap=False), 2, 0)
        self.cell_combo = QComboBox(); self.cell_combo.setEditable(True)
        for c in CELL_SIZES:
            self.cell_combo.addItem(f"{c} blocks", c)
        self.cell_combo.activated.connect(self._cell_chosen)
        self.cell_combo.lineEdit().editingFinished.connect(self._cell_typed)
        g.addWidget(self.cell_combo, 2, 1)
        g.addWidget(body("Size", wrap=False), 3, 0)
        ar = QHBoxLayout(); ar.setSpacing(6)
        self.sp_area = QSpinBox(); self.sp_area.setRange(1, 2_000_000); self.sp_area.setSuffix(" blocks")
        self.sp_area.setKeyboardTracking(False); self.sp_area.setGroupSeparatorShown(True)
        self.sp_area.setToolTip("Width of the square in the world (its longest side). The grid "
                                "keeps its cells: the cell size follows.")
        self.sp_area.valueChanged.connect(self._area_edited)
        ar.addWidget(self.sp_area, 1)
        self.area_lbl = QLabel(); self.area_lbl.setObjectName("Muted")
        ar.addWidget(self.area_lbl)
        g.addLayout(ar, 3, 1)
        g.addWidget(body("Slide", wrap=False), 6, 0)
        sr = QHBoxLayout()
        self.sp_slide = QSpinBox(); self.sp_slide.setRange(0, 256); self.sp_slide.setSuffix(" cells")
        self.sp_slide.setKeyboardTracking(False)
        self.sp_slide.setToolTip("How far (in cells, every direction) the drawing may move to fit a "
                                 "seed. 0 = exactly at its place. Bigger = more matches, slower.")
        self.sp_slide.valueChanged.connect(lambda val: self.model.set_geometry(slide=val, label="Slide"))
        sr.addWidget(self.sp_slide)
        self.slide_lbl = QLabel(); self.slide_lbl.setObjectName("Muted"); sr.addWidget(self.slide_lbl, 1)
        g.addLayout(sr, 6, 1)
        g.setColumnStretch(1, 1)
        v.addLayout(g)
        v.addWidget(body("On the map: drag a corner of the square to resize it, its border to "
                         "move it. Outside the square, dragging moves the map.", muted=True, small=True))
        grow = QHBoxLayout()
        self.grid_lines = QCheckBox("Grid lines"); self.grid_lines.setChecked(True)
        self.grid_lines.toggled.connect(lambda on: (setattr(self.map, "show_grid", on), self.map.update()))
        grow.addWidget(self.grid_lines); grow.addStretch(1)
        v.addLayout(grow)
        trow = QHBoxLayout(); trow.setSpacing(4)
        for ic, tip, kind in (("flip", "Flip horizontally", "flip_h"), ("rotate", "Rotate right 90°", "rot_cw")):
            b = QToolButton(); icons.bind(b, ic, 17); b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=kind: self.model.transform(k)); trow.addWidget(b)
        b = QToolButton(); icons.bind(b, "flipv", 17); b.setToolTip("Flip vertically")
        b.clicked.connect(lambda: self.model.transform("flip_v")); trow.addWidget(b)
        seedb = QToolButton(); icons.bind(seedb, "seedcopy", 17); seedb.setText(" From seed")
        seedb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        seedb.setToolTip("Replace the drawing by the current seed's own map at the drawing's place")
        seedb.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        sm = QMenu(seedb)
        sm.addAction("Land / ocean / mushroom", lambda: self._from_seed("cont"))
        sm.addAction("Hot / cold  (1.18+)", lambda: self._from_seed("temp"))
        sm.addAction("Biomes", lambda: self._from_seed("bio"))
        sm.addAction("Biome groups", lambda: self._from_seed("grp"))
        seedb.setMenu(sm)
        trow.addWidget(seedb)
        trow.addStretch(1)
        clr = QToolButton(); icons.bind(clr, "trash", 17); clr.setToolTip("Clear the drawing (undoable)")
        clr.clicked.connect(self.model.clear); trow.addWidget(clr)
        v.addLayout(trow)

        # summary
        v.addSpacing(4); v.addWidget(section_title("Summary"))
        self.summary = body("", small=True); self.summary.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.summary)
        self.hover_lbl = QLabel(" "); self.hover_lbl.setObjectName("Muted")
        self.hover_lbl.setFont(theme.mono_font(8.5))
        v.addWidget(self.hover_lbl)
        v.addStretch(1)
        self._style_left(left)
        theme.signals.changed.connect(lambda: self._style_left(left))
        return left

    def _style_left(self, w):
        w.setStyleSheet(f"""
            QToolButton#DrawTool {{ background: {theme.BG2}; border: 1px solid {theme.LINE};
                border-radius: 7px; color: {theme.TEXT2}; font-size: 7.5pt; padding-top: 3px; }}
            QToolButton#DrawTool:hover {{ border-color: {theme.LINE2}; color: {theme.TEXT}; }}
            QToolButton#DrawTool:checked {{ background: {theme.ACCENT_SOFT}; border-color: {theme.ACCENT};
                color: {theme.TEXT}; }}
            QToolButton#PaintChip {{ background: {theme.BG2}; border: 1px solid {theme.LINE};
                border-radius: 6px; color: {theme.TEXT}; padding: 3px 4px; text-align: left; }}
            QToolButton#PaintChip:hover {{ border-color: {theme.LINE2}; }}
            QToolButton#PaintChip:checked {{ background: {theme.ACCENT_SOFT}; border: 1.5px solid {theme.ACCENT}; }}
        """)

    # ═══ floating bar on the map: drawing ↔ map slider, show the drawing ═══
    def _build_float(self):
        bar = QFrame(self.map); bar.setObjectName("DrawFloat")
        h = QHBoxLayout(bar); h.setContentsMargins(12, 5, 8, 5); h.setSpacing(8)
        l1 = QLabel("Drawing"); l1.setObjectName("FloatText"); h.addWidget(l1)
        self.op_slider = EasySlider(); self.op_slider.setRange(0, 100); self.op_slider.setValue(50)
        self.op_slider.setFixedWidth(170)
        self.op_slider.setToolTip("Inside the square: 0 % = the drawing alone on dark grey  ·  100 % = the "
                                  "map alone (drawing hidden). Outside the square the map is always shown.")
        self.op_slider.valueChanged.connect(self._opacity)
        h.addWidget(self.op_slider)
        l2 = QLabel("Map"); l2.setObjectName("FloatText"); h.addWidget(l2)
        self.op_lbl = QLabel("50 %"); self.op_lbl.setObjectName("FloatPct"); self.op_lbl.setFixedWidth(36)
        h.addWidget(self.op_lbl)
        sep = QFrame(); sep.setFixedSize(1, 18); sep.setObjectName("FloatSep"); h.addWidget(sep)
        fit = QToolButton(); icons.bind(fit, "crosshair", 17, "#dfe3ea", "#ffffff"); fit.setAutoRaise(True)
        fit.setToolTip("Show the whole drawing  (F)"); fit.clicked.connect(lambda: self.fit())
        h.addWidget(fit)
        bar.setStyleSheet("""
            QFrame#DrawFloat { background: rgba(16, 18, 24, 205); border: 1px solid rgba(255,255,255,40);
                               border-radius: 10px; }
            QLabel#FloatText { color: #dfe3ea; font-size: 8.5pt; font-weight: 600; background: transparent; }
            QLabel#FloatPct { color: #aab2c0; font-size: 8.5pt; background: transparent; }
            QFrame#FloatSep { background: rgba(255,255,255,50); }
        """)
        bar.adjustSize()
        self._float = bar

        class _Place(QObject):
            def eventFilter(s2, obj, ev):
                if ev.type() in (ev.Type.Resize, ev.Type.Show):
                    self._place_float()
                return False
        self._float_filter = _Place(self)
        self.map.installEventFilter(self._float_filter)
        self._place_float()

    def _place_float(self):
        b = self._float
        b.adjustSize()
        b.move(max(60, (self.map.width() - b.width()) // 2), 12)
        b.raise_()

    # ═══ results panel ══════════════════════════════════════════════════════
    def _build_results(self):
        right = QFrame(); right.setObjectName("SidePanel"); right.setMinimumWidth(290)
        v = QVBoxLayout(right); v.setContentsMargins(14, 12, 12, 10); v.setSpacing(8)
        v.addWidget(section_title("Test a seed"))
        tr = QHBoxLayout(); tr.setSpacing(4)
        self.test_seed = QLineEdit(); self.test_seed.setPlaceholderText("Seed (empty = current)")
        self.test_seed.setFont(theme.mono_font(9.5))
        self.test_seed.returnPressed.connect(self._test)
        tr.addWidget(self.test_seed, 1)
        b = QPushButton("Test"); b.setProperty("kind", "primary"); b.clicked.connect(self._test)
        b.setToolTip("Score of the seed with the drawing, best position within the slide  (Ctrl+T)")
        tr.addWidget(b)
        v.addLayout(tr)
        self.test_lbl = body("", muted=True, small=True); self.test_lbl.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.test_lbl)
        v.addSpacing(4)
        top = QHBoxLayout()
        t = QLabel("Results"); t.setObjectName("PanelTitle"); top.addWidget(t); top.addStretch(1)
        self.res_count = QLabel("0"); self.res_count.setObjectName("Chip"); self.res_count.setProperty("state", "off")
        top.addWidget(self.res_count)
        ex = QToolButton(); icons.bind(ex, "save", 17); ex.setToolTip("Export (TXT / CSV)")
        ex.clicked.connect(self._export); top.addWidget(ex)
        cl = QToolButton(); icons.bind(cl, "trash", 17); cl.setToolTip("Clear the results (ends the search)")
        cl.clicked.connect(self._clear_results); top.addWidget(cl)
        v.addLayout(top)
        self.results = ResultsModel(self)
        self.list = QListView(); self.list.setModel(self.results)
        self.list.setItemDelegate(ResultDelegate(self))
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setStyleSheet("QListView { background: transparent; border: none; }")
        self.list.selectionModel().currentChanged.connect(self._selected)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        v.addWidget(self.list, 1)
        self.stale_lbl = body("", small=True); self.stale_lbl.setStyleSheet(f"color: {theme.WARN}; font-size: 8pt;")
        self.stale_lbl.hide(); v.addWidget(self.stale_lbl)
        v.addWidget(body("Click a result: the seed is loaded and the drawing shown where it "
                         "fits — green cells match, red ones do not. Paint again to edit.",
                         muted=True, small=True))
        return right

    # ═══ run bar ════════════════════════════════════════════════════════════
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

        self.min_score = QSpinBox(); self.min_score.setRange(1, 100); self.min_score.setValue(75)
        self.min_score.setSuffix(" %")
        labeled("Min score", self.min_score, "Keep the seeds whose score is at least this. Higher = "
                                             "faster (more seeds rejected early).")
        self.top_k = QSpinBox(); self.top_k.setRange(1, 100_000); self.top_k.setValue(500)
        labeled("Keep best", self.top_k, "Size of the results list (the best ones are kept). When "
                                         "it is full, only better seeds get in — the search speeds up.")
        self.start_seed = QLineEdit("0"); self.start_seed.setFixedWidth(180)
        self.start_seed.setFont(theme.mono_font(9.5))
        sw = QWidget(); sl = QHBoxLayout(sw); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(2)
        sl.addWidget(self.start_seed)
        dice = QToolButton(); icons.bind(dice, "dice", 16); dice.setToolTip("Random start")
        dice.clicked.connect(lambda: self.start_seed.setText(str(random_seed())))
        sl.addWidget(dice)
        labeled("Start", sw)
        self.threads = QSpinBox(); self.threads.setRange(1, max(1, os.cpu_count() or 4))
        self.threads.setValue(max(1, (os.cpu_count() or 4) - 1))
        labeled("Threads", self.threads)
        self.rule_kind = QComboBox()
        for text, key in (("Never", "never"), ("Seeds searched", "seeds"),
                          ("Results found", "results"), ("Time", "time")):
            self.rule_kind.addItem(text, key)
        self.rule_seeds = QLineEdit("10M")
        self.rule_results = QSpinBox(); self.rule_results.setRange(1, 100_000); self.rule_results.setValue(50)
        self.rule_time = QTimeEdit(); self.rule_time.setDisplayFormat("H 'h' mm 'min'"); self.rule_time.setTime(QTime(0, 30))
        self.rule_stack = QStackedWidget(); self.rule_stack.setFixedWidth(112)
        self.rule_stack.addWidget(QLabel(""))
        for wd in (self.rule_seeds, self.rule_results, self.rule_time):
            wd.setFixedWidth(110); self.rule_stack.addWidget(wd)
        self.rule_kind.currentIndexChanged.connect(self.rule_stack.setCurrentIndex)
        rw = QWidget(); rl = QHBoxLayout(rw); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)
        rl.addWidget(self.rule_kind); rl.addWidget(self.rule_stack)
        labeled("Stop after", rw, "A seeds limit ends the search; a results or time limit pauses it "
                                  "(Resume continues exactly where it stopped).")
        row.addStretch(1)
        self.b_start = QPushButton("  Start"); self.b_start.setProperty("kind", "primary")
        icons.bind(self.b_start, "play", 16, "#ffffff", "#ffffff"); self.b_start.clicked.connect(self._start_clicked)
        self.b_pause = QPushButton("  Pause"); icons.bind(self.b_pause, "pause", 16)
        self.b_pause.clicked.connect(self.pause)
        self.b_stop = QPushButton("  Stop"); icons.bind(self.b_stop, "stop", 16)
        self.b_stop.setProperty("kind", "danger"); self.b_stop.clicked.connect(self.stop)
        for b in (self.b_start, self.b_pause, self.b_stop):
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignBottom)
        v.addLayout(row)
        prow = QHBoxLayout(); prow.setSpacing(12)
        self.prog = QProgressBar(); self.prog.setRange(0, 1000); self.prog.setTextVisible(False)
        self.prog.setFixedHeight(6); prow.addWidget(self.prog, 1)
        self.stat = QLabel("Idle"); self.stat.setObjectName("Muted"); self.stat.setFont(theme.mono_font(9))
        prow.addWidget(self.stat)
        self.engine_chip = QLabel("Draw engine"); self.engine_chip.setObjectName("Chip")
        self.engine_chip.setProperty("state", "off")
        prow.addWidget(self.engine_chip)
        v.addLayout(prow)
        return bar

    # ═══ shortcuts ══════════════════════════════════════════════════════════
    def _shortcuts(self):
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for key, _ic, _lab, sc, _tip in TOOLS:
            QShortcut(QKeySequence(sc), self, activated=lambda k=key: self._set_tool(k), context=ctx)
        for seq, fn in (("Ctrl+Z", self.model.undo), ("Ctrl+Y", self.model.redo),
                        ("Ctrl+Shift+Z", self.model.redo), ("Ctrl+S", self.save),
                        ("Ctrl+T", self._test), ("F", self.fit),
                        ("[", lambda: self.map.set_radius(self.map.radius - 0.5)),
                        ("]", lambda: self.map.set_radius(self.map.radius + 0.5)),
                        ("X", self._swap_last_code)):
            QShortcut(QKeySequence(seq), self, activated=fn, context=ctx)

    # ═══ tools / paint ══════════════════════════════════════════════════════
    def _set_tool(self, key):
        self.map.set_tool(key)
        b = self.tool_btns.get(key)
        if b is not None:
            b.setChecked(True)
        self.filled.setEnabled(key in ("rect", "circle"))
        self.map.setFocus()

    def _brush_from_map(self, r):
        v = int(round(r * 2))
        if self.size_slider.value() != v:
            self.size_slider.blockSignals(True); self.size_slider.setValue(v); self.size_slider.blockSignals(False)
        d = 2 * r + 1
        self.size_lbl.setText(f"Ø {d:g} cell{'s' if d != 1 else ''}")

    def _set_code(self, code):
        code = int(code)
        prev = self.map.code
        self.map.code = code
        for c, b in self.code_btns.items():
            b.setChecked(c == code)
        rgb = de.code_color(code)
        pm = QPixmap(60, 60); pm.setDevicePixelRatio(2); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(theme.LINE2)); p.setBrush(QColor(*rgb)); p.drawRoundedRect(QRectF(1, 1, 28, 28), 6, 6); p.end()
        self.cur_swatch.setPixmap(pm)
        self.cur_name.setText(de.code_name(code))
        self.cur_layer.setText("Continentalness" if code in (de.LAND, de.OCEAN, de.MUSHROOM) else
                               "Temperature" if code in (de.HOT, de.COLD) else
                               "Biome group" if de.is_group_code(code) else "Biome")
        if de.is_biome_code(code):
            i = self.biome_combo.findData(code)
            if i >= 0:
                self.biome_combo.blockSignals(True); self.biome_combo.setCurrentIndex(i)
                self.biome_combo.blockSignals(False)
            self._push_recent(code)
        if prev != code:
            self._last_code = prev
        if self.map.tool in ("eraser", "picker", "pan", "weight"):
            self._set_tool("brush")

    def _swap_last_code(self):
        last = getattr(self, "_last_code", None)
        if last is not None:
            self._set_code(last)

    def _push_recent(self, code):
        if code in self._recent:
            self._recent.remove(code)
        self._recent.insert(0, code)
        del self._recent[8:]
        while self.recent_row.count():
            it = self.recent_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for c in self._recent:
            b = QToolButton(); b.setIcon(_swatch(de.code_color(c), 16)); b.setAutoRaise(True)
            b.setToolTip(de.code_name(c)); b.clicked.connect(lambda _=False, cc=c: self._set_code(cc))
            self.recent_row.addWidget(b)
        self.recent_row.addStretch(1)

    def _picked(self, code):
        self._set_code(code)
        self.status.emit(f"Picked: {de.code_name(code)}")

    def _biome_chosen(self, idx):
        code = self.biome_combo.itemData(idx)
        if code is not None:
            self._set_code(int(code))

    def _refresh_biomes(self):
        mc = int(self.world.mc)
        if getattr(self, "_biomes_mc", None) == mc:
            return
        self._biomes_mc = mc
        cur = self.map.code if de.is_biome_code(self.map.code) else None
        self.biome_combo.blockSignals(True)
        self.biome_combo.clear()
        items = []
        for b, name in cw.BIOME_NAMES.items():
            if b < 0 or b >= 256:
                continue
            if not biome_available(mc, b):
                continue
            items.append((name, b))
        for name, b in sorted(items):
            self.biome_combo.addItem(_swatch(cw.BIOME_COLORS.get(b, cw.DEFAULT_COLOR)), name, de.biome_code(b))
        self.biome_combo.setCurrentIndex(-1)
        if cur is not None:
            i = self.biome_combo.findData(cur)
            if i >= 0:
                self.biome_combo.setCurrentIndex(i)
        self.biome_combo.blockSignals(False)

    def _weight_chosen(self, text):
        labels = [f"×{w:g}" if w >= 1 else ("×¼" if w == 0.25 else "×½") for w in WEIGHTS]
        self.map.weight = WEIGHTS[labels.index(text)]
        self._set_tool("weight")

    def _show_weights(self, on):
        self.map.show_weights = on
        self.map._img_key = None
        self.map.update()

    def _opacity(self, v):
        self.op_lbl.setText(f"{v} %")
        self.map.opacity = v / 100.0
        self.map._img_key = None
        self.map.update()

    def _hover(self, i, j):
        m = self.model
        if i < 0:
            self.hover_lbl.setText(" ")
            return
        c = int(m.grid[j, i])
        x0, z0 = m.corner(*self.map.offset())
        txt = f"cell {i},{j}  ·  x {x0 + i * m.cell}..{x0 + (i + 1) * m.cell - 1}  z {z0 + j * m.cell}"
        txt += f"  ·  {de.code_name(c) if c else 'not painted'}"
        if c and abs(float(m.weights[j, i]) - 1.0) > 1e-6:
            txt += f" ×{float(m.weights[j, i]):g}"
        self.hover_lbl.setText(txt)

    # ═══ canvas / placement ═════════════════════════════════════════════════
    def _sync_canvas_controls(self):
        m = self.model
        for sp, val in ((self.sp_w, m.W), (self.sp_h, m.H), (self.sp_slide, m.slide)):
            if sp.value() != val:
                sp.blockSignals(True); sp.setValue(val); sp.blockSignals(False)
        self.lock.setChecked(m.W == m.H)
        i = self.cell_combo.findData(m.cell)
        self.cell_combo.blockSignals(True)
        if i >= 0:
            self.cell_combo.setCurrentIndex(i)
        else:
            self.cell_combo.setEditText(f"{m.cell} blocks")
        self.cell_combo.blockSignals(False)
        side = max(m.W, m.H) * m.cell
        if self.sp_area.value() != side:
            self.sp_area.blockSignals(True); self.sp_area.setSingleStep(max(m.W, m.H))
            self.sp_area.setValue(side); self.sp_area.blockSignals(False)
        self.area_lbl.setText("" if m.W == m.H else f"{fmt_int(m.W * m.cell)} × {fmt_int(m.H * m.cell)}")
        self.slide_lbl.setText(f"± {fmt_int(m.slide * m.cell)} blocks")
        if self.name_edit.text() != m.name:
            self.name_edit.setText(m.name)
        if self.balance.isChecked() != m.balance:
            self.balance.blockSignals(True); self.balance.setChecked(m.balance); self.balance.blockSignals(False)
        self.b_undo.setEnabled(m.can_undo()); self.b_redo.setEnabled(m.can_redo())
        self.b_undo.setToolTip(f"Undo {m.undo_text()}  (Ctrl+Z)" if m.can_undo() else "Undo  (Ctrl+Z)")
        self.b_redo.setToolTip(f"Redo {m.redo_text()}  (Ctrl+Y)" if m.can_redo() else "Redo  (Ctrl+Y)")

    def _grid_edited(self, which):
        m = self.model
        W, H = self.sp_w.value(), self.sp_h.value()
        if self.lock.isChecked():
            if which == "w":
                H = W
            else:
                W = H
        if (W, H) == (m.W, m.H):
            self._sync_canvas_controls(); return
        if self.keep_area.isChecked():
            # same world area: the cell size follows (rounded)
            area = max(m.W * m.cell, m.H * m.cell)
            new_cell = max(1, int(round(area / max(W, H))))
            old = (m.W, m.H)
            m.begin("Resize grid")
            m.set_size(W, H, keep="scale")
            m.set_geometry(cell=new_cell, slide=int(round(m.slide * max(W, H) / max(old))))
            m.commit("Resize grid")
        else:
            m.set_size(W, H, keep="crop")

    def _area_edited(self, val):
        m = self.model
        cell = max(1, int(round(val / max(m.W, m.H))))
        m.set_geometry(cell=cell, label="Area size")
        self._sync_canvas_controls()

    def _cell_chosen(self, idx):
        c = self.cell_combo.itemData(idx)
        if c:
            self.model.set_geometry(cell=int(c), label="Cell size")

    def _cell_typed(self):
        t = self.cell_combo.currentText().lower().replace("blocks", "").strip()
        try:
            c = int(float(t))
        except ValueError:
            self._sync_canvas_controls(); return
        if 1 <= c <= 4096:
            self.model.set_geometry(cell=c, label="Cell size")
        self._sync_canvas_controls()


    def centre_on_drawing(self, ox=0, oz=0):
        """Centre the map on the drawing at offset (ox, oz), zoom unchanged."""
        m = self.model
        x0, z0 = m.corner(ox, oz)
        self.map.center_on(x0 + m.W * m.cell / 2, z0 + m.H * m.cell / 2)

    def fit(self, ox=None, oz=None):
        m = self.model
        if ox is None:
            ox, oz = self.map.offset()
        x0, z0 = m.corner(ox, oz)
        cx, cz = x0 + m.W * m.cell / 2, z0 + m.H * m.cell / 2
        pad = (m.slide * m.cell * 2) if self.map.mode == "edit" else 0
        w, h = max(1, self.map.width() - 60), max(1, self.map.height() - 60)
        bpp = max((m.W * m.cell + pad) / w, (m.H * m.cell + pad) / h)
        self.map.center_on(cx, cz, max(0.25, bpp))

    def _name_edited(self):
        t = self.name_edit.text().strip()
        if t and t != self.model.name:
            self.model.name = t
            self.model.dirty = True
            if self.model.path and os.path.exists(self.model.path):
                try:
                    d = read_json(self.model.path); d["name"] = t; d["modified"] = now_iso()
                    write_json(self.model.path, d)
                except Exception:
                    pass

    def _from_seed(self, what):
        m = self.model
        mc, large = int(self.world.mc), bool(self.world.large_biomes)
        if what == "temp" and mc < cw.MC_VERSIONS["Java 1.18"]:
            QMessageBox.information(self, "From seed", "Temperature classes exist from Java 1.18.")
            return
        base = {"cont": de.LAND, "temp": de.HOT}.get(what, de.biome_code(1))
        spec = de.DrawSpec(np.full((m.H, m.W), base, np.int32), None, cell=m.cell,
                           center=m.center, slide=0, mc=mc, large=large)
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            _s, obs, _mm = de.DrawEngine(spec).eval_at(self.world.seed, 0, 0)
        except Exception as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "From seed", str(e)); return
        QGuiApplication.restoreOverrideCursor()
        g = obs.astype(np.int32)
        if what == "grp":
            masks = de.group_masks()
            out = np.zeros_like(g)
            for c in np.unique(g):
                b = int(c) - de.BIO
                mk = int(masks[b]) if 0 <= b < 256 else 0
                if mk:
                    k = (mk & -mk).bit_length() - 1
                    out[g == c] = de.group_code(k)
            g = out
        self._leave_result()
        m.begin()
        m.grid[:] = g
        m.weights[:] = 1.0
        m.commit("From seed")
        m.changed.emit("paint")
        self.status.emit(f"Drawing replaced by seed {self.world.seed}'s map "
                         f"({ {'cont': 'land / ocean', 'temp': 'hot / cold', 'bio': 'biomes', 'grp': 'groups'}[what]}).")

    # ═══ model / world changes ══════════════════════════════════════════════
    def _model_changed(self, kind):
        if kind in ("geometry", "load", "history", "meta"):
            self._sync_canvas_controls()
        if kind == "load":
            self.map.set_result(None)
        self._refresh_summary()
        self._refresh_resume()

    def _world_changed(self):
        self._refresh_biomes()
        self._refresh_summary()
        self._refresh_resume()
        if self.map.mode == "result" and self.map.result and self.map.result.get("seed") != self.world.seed:
            self.map.set_result(None)

    def _refresh_summary(self):
        m = self.model
        mc = int(self.world.mc)
        modern = mc >= cw.MC_VERSIONS["Java 1.18"]
        n = m.painted()
        tot = m.W * m.H
        lines = []
        if not n:
            lines.append(f"<span style='color:{theme.WARN}'>Nothing painted yet — choose a class "
                         f"and paint over the map.</span>")
        else:
            lines.append(f"<b>{fmt_int(n)}</b> painted cells of {fmt_int(tot)} "
                         f"({n * 100 / tot:.0f} %)")
            used = m.used_codes()
            names = ", ".join(f"{de.code_name(c)}" for c, _k in used[:6]) + ("…" if len(used) > 6 else "")
            lines.append(names)
            cont = any(c in (de.LAND, de.OCEAN, de.MUSHROOM) for c, _ in used)
            temp = any(c in (de.HOT, de.COLD) for c, _ in used)
            bio = any(c >= de.BIO for c, _ in used)
            if temp and not modern:
                lines.append(f"<span style='color:{theme.WARN}'>Hot / Cold need Java 1.18+: those "
                             f"cells are ignored for {self.world.version_name}.</span>")
            missing = [de.code_name(c) for c, _ in used if de.is_biome_code(c)
                       and not biome_available(mc, c - de.BIO)]
            if missing:
                lines.append(f"<span style='color:{theme.WARN}'>Not generated in "
                             f"{self.world.version_name}: {', '.join(missing)} — never matches.</span>")
            if modern and (cont or temp):
                how = "fast climate pre-filter" + (", biomes only for the seeds that pass" if bio else "")
            else:
                how = "full biome generation for every seed (slower)"
            lines.append(f"Search: {how}. Positions tested per seed: "
                         f"{fmt_int((2 * m.slide + 1) ** 2)}.")
        self.summary.setText("<br>".join(lines))

    # ═══ test a seed / show a result ════════════════════════════════════════
    def _current_spec(self):
        return self.model.spec(int(self.world.mc), bool(self.world.large_biomes))

    def _test(self):
        t = self.test_seed.text().strip()
        if t:
            from .world import parse_seed
            seed = parse_seed(t)
            if seed is None:
                return
            self.world.set(seed=seed)
        seed = self.world.seed
        if not self.model.painted():
            self.test_lbl.setText("Paint something first."); return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            eng = de.DrawEngine(self._current_spec())
            t0 = time.perf_counter()
            sc, ox, oz = eng.score_seed(seed)
            _s, _obs, mm = eng.eval_at(seed, ox, oz)
            dt = time.perf_counter() - t0
        except Exception as e:
            QGuiApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Test", str(e)); return
        QGuiApplication.restoreOverrideCursor()
        self._show_result(seed, sc, ox, oz, mm, fit=True)
        painted = self.model.grid != de.ANY
        nm = int(((mm == 1) & painted).sum())
        col = _score_color(sc).name()
        self.test_lbl.setText(f"Seed <b>{seed}</b>: <b style='color:{col}'>{sc * 100:.2f} %</b> at "
                              f"offset {ox:+d}, {oz:+d} cells ({ox * self.model.cell:+d}, "
                              f"{oz * self.model.cell:+d} blocks) · {nm}/{int(painted.sum())} cells "
                              f"match · {dt * 1000:.0f} ms")

    def _show_result(self, seed, sc, ox, oz, mm, fit=False):
        self.map.set_result(dict(seed=seed, score=sc, ox=ox, oz=oz, mm=mm))
        if fit:                               # centred, the current zoom is kept
            self.centre_on_drawing(ox, oz)

    def _leave_result(self):
        if self.map.mode == "result":
            self.map.set_result(None)

    def _selected(self, cur, _prev):
        if not cur.isValid():
            return
        seed, sc, ox, oz = cur.data(Qt.ItemDataRole.UserRole)
        self.world.set(seed=seed)
        stale = self.sinfo is not None and self.sinfo.get("sig") != _sig_hash(self.model)
        try:
            eng = de.DrawEngine(self._current_spec())
            if stale:                         # the drawing changed since the search
                sc, ox, oz = eng.score_seed(seed)
            _s, _obs, mm = eng.eval_at(seed, ox, oz)
        except Exception as e:
            self.status.emit(str(e)); return
        self._show_result(seed, sc, ox, oz, mm, fit=True)
        self.test_lbl.setText(f"Result: seed <b>{seed}</b> · <b style='color:{_score_color(sc).name()}'>"
                              f"{sc * 100:.2f} %</b> at offset {ox:+d}, {oz:+d}"
                              + (" (re-scored: the drawing changed)" if stale else ""))

    def _menu(self, pt):
        rows = sorted({i.row() for i in self.list.selectionModel().selectedIndexes()})
        if not rows:
            return
        seeds = [self.results.rows[r][0] for r in rows]
        m = QMenu(self)
        a1 = m.addAction(f"Copy seed{'s' if len(seeds) > 1 else ''}")
        a2 = m.addAction("Copy with scores")
        act = m.exec(self.list.viewport().mapToGlobal(pt))
        if act is a1:
            QGuiApplication.clipboard().setText("\n".join(str(s) for s in seeds))
        elif act is a2:
            QGuiApplication.clipboard().setText("\n".join(
                f"{r[0]}\t{r[1] * 100:.2f}%\t{r[2]:+d},{r[3]:+d}" for r in (self.results.rows[i] for i in rows)))

    def _export(self):
        if not self.rows:
            return
        safe = "".join(ch for ch in self.model.name if ch not in '\\/:*?"<>|').strip() or "drawing"
        path, _ = QFileDialog.getSaveFileName(self, "Export results", user_path(safe + ".txt"),
                                              "Text, one seed per line (*.txt);;CSV (*.csv)")
        if not path:
            return
        remember_path(path)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                if path.lower().endswith(".csv"):
                    f.write("seed,score,offset_x_cells,offset_z_cells,centre_x,centre_z\n")
                    m = self.model
                    for s, sc, ox, oz in self.rows:
                        f.write(f"{s},{sc:.6f},{ox},{oz},{m.center[0] + ox * m.cell},{m.center[1] + oz * m.cell}\n")
                else:
                    for s, *_ in self.rows:
                        f.write(f"{s}\n")
        except OSError as e:                      # read-only folder, file in use…
            QMessageBox.warning(self, "Export results", f"Could not write the file:\n{e}")
            return
        self.status.emit(f"Exported {len(self.rows)} results to {os.path.basename(path)}.")

    def _clear_results(self):
        if self.state in ("running", "pausing"):
            return
        if self.rows and QMessageBox.question(self, "Clear", "Clear the results? The search "
                                              "cannot be resumed afterwards.") != QMessageBox.StandardButton.Yes:
            return
        self.rows = []
        self.results.set_rows([])
        self.sinfo = None
        self.engine = None
        self._count()
        self._run_state("idle")

    def _count(self):
        self.res_count.setText(fmt_int(len(self.rows)))

    # ═══ search ═════════════════════════════════════════════════════════════
    def _params(self):
        return dict(min_score=self.min_score.value() / 100.0, top_k=self.top_k.value(),
                    threads=self.threads.value())

    def _resumable(self):
        s = self.sinfo
        return (s is not None and self.state in ("paused",) and s.get("sig") == _sig_hash(self.model)
                and s.get("mc") == int(self.world.mc) and s.get("large") == bool(self.world.large_biomes))

    def _refresh_resume(self):
        if self.state in ("running", "pausing"):
            return
        self._run_state(self.state)

    def _start_clicked(self):
        if self._resumable():
            self._launch(resume=True)
        else:
            self._launch(resume=False)

    def _launch(self, resume):
        if not self.model.painted():
            QMessageBox.information(self, "Search", "Paint something first."); return
        try:
            rule = self._read_rule()
        except ValueError as e:
            QMessageBox.warning(self, "Search", str(e)); return
        p = self._params()
        spec = self._current_spec()
        if not resume:
            try:
                start = int(self.start_seed.text().strip() or 0)
            except ValueError:
                QMessageBox.warning(self, "Start", "The start seed must be an integer."); return
            if self.rows and QMessageBox.question(
                    self, "New search", "Start a new search? The current results will be replaced.") \
                    != QMessageBox.StandardButton.Yes:
                return
            self.rows = []
            self.results.set_rows([])
            self.sinfo = dict(sig=_sig_hash(self.model), mc=spec.mc, large=spec.large, start=start,
                              done=0, elapsed=0.0, tested=0, passed=0)
        s = self.sinfo
        try:
            eng = de.DrawEngine(spec, min_score=p["min_score"], top_k=p["top_k"])
        except Exception as e:
            QMessageBox.warning(self, "Search", str(e)); return
        if resume and self.rows:
            eng.put_results(self.rows)
        s.update(min_score=p["min_score"], top_k=p["top_k"], threads=p["threads"], rule=list(rule))
        count = 0
        if rule[0] == "seeds":
            count = max(0, int(rule[1]) - int(s["done"]))
            if count == 0:
                self._run_state("done"); return
        self.engine = eng
        self.thumbs.set_spec(spec, s["sig"] + f"|{spec.mc}|{spec.large}")
        self.search = de.DrawSearch(eng, s["start"] + s["done"], count, p["threads"])
        self._run_t0 = time.perf_counter()
        self._spd = None
        self._last_rate_pt = (time.perf_counter(), 0)
        self.search.start()
        self._poll.start()
        self._run_state("running")
        self.engine_chip.setText(f"Draw engine · {p['threads']} threads")

    def pause(self):
        if self.state == "running" and self.search is not None:
            self.search.pause()
            self._run_state("pausing")

    def stop(self):
        if self.state in ("running", "pausing") and self.search is not None:
            self._stop_req = True
            self.search.stop()
            self._run_state("pausing")
        elif self.state == "paused":
            self._run_state("stopped")

    def _tick(self):
        s, srch, eng = self.sinfo, self.search, self.engine
        if srch is None or s is None:
            self._poll.stop(); return
        done = s["done"] + srch.done
        elapsed = s["elapsed"] + (time.perf_counter() - self._run_t0)
        tested, passed = eng.counters()
        now = time.perf_counter()
        t_last, n_last = self._last_rate_pt
        if now - t_last >= 0.9:
            rate = (tested - n_last) / (now - t_last)
            self._spd = rate if self._spd is None else 0.6 * rate + 0.4 * self._spd
            self._last_rate_pt = (now, tested)
        rows = eng.results()
        if rows != self.rows:
            cur = self.list.currentIndex()
            keep = self.results.rows[cur.row()][0] if cur.isValid() else None
            self.rows = rows
            self.results.set_rows(rows)
            if keep is not None:
                for i, r in enumerate(rows):
                    if r[0] == keep:
                        self.list.selectionModel().blockSignals(True)
                        self.list.setCurrentIndex(self.results.index(i))
                        self.list.selectionModel().blockSignals(False)
                        break
            self._count()
        rule = s.get("rule") or ["never", 0]
        frac = None
        if rule[0] == "seeds":
            frac = done / max(1, rule[1])
        elif rule[0] == "time":
            frac = elapsed / max(1, rule[1])
        elif rule[0] == "results":
            frac = len(rows) / max(1, rule[1])
        if frac is not None:
            self.prog.setRange(0, 1000); self.prog.setValue(int(min(1.0, frac) * 1000))
        elif self.state == "running":
            self.prog.setRange(0, 0)
        pr = f"{passed * 100 / tested:.1f} %" if tested else "—"
        self.stat.setText("  ·  ".join([f"{fmt_int(done)} seeds", fmt_rate(self._spd or 0.0),
                                        f"pre-filter pass {pr}", f"{fmt_int(len(rows))} kept",
                                        f"{fmt_time(elapsed)}"]))
        self.stat.setToolTip(f"Resume point: {s['start'] + done}\nTested (incl. re-tested): {fmt_int(tested)}")
        # limits
        if self.state == "running":
            if (rule[0] == "results" and len(rows) >= rule[1]) or (rule[0] == "time" and elapsed >= rule[1]):
                self.pause()
        if not srch.running():
            self._finish()

    def _finish(self):
        srch, s = self.search, self.sinfo
        self._poll.stop()
        s["done"] += srch.done
        s["elapsed"] += srch.t_run
        t, pz = self.engine.counters()
        s["tested"] = s.get("tested", 0) + t
        s["passed"] = s.get("passed", 0) + pz
        self.rows = self.engine.results()
        self.results.set_rows(self.rows)
        self._count()
        self.search = None
        stop_req = getattr(self, "_stop_req", False)
        self._stop_req = False
        if srch.error:
            QMessageBox.critical(self, "Search failed", srch.error)
            self._run_state("stopped")
        elif srch.finished:
            self._run_state("done")
        elif stop_req:
            self._run_state("stopped")
        else:
            self._run_state("paused")
        self.prog.setRange(0, 1000)
        self._tick_summary()

    def _tick_summary(self):
        s = self.sinfo
        if s is None:
            return
        st = {"paused": "Paused", "done": "Done", "stopped": "Stopped"}.get(self.state, "")
        self.stat.setText(f"{st}  ·  {fmt_int(s['done'])} seeds  ·  {fmt_int(len(self.rows))} kept  ·  "
                          f"{fmt_time(s['elapsed'])}" + (f"  ·  next {s['start'] + s['done']}"
                                                         if self.state == "paused" else ""))

    def _run_state(self, st):
        self.state = st
        running = st in ("running", "pausing")
        can_resume = self._resumable()
        self.b_start.setEnabled(not running)
        self.b_start.setText("  Resume" if can_resume else "  Start")
        icons.bind(self.b_start, "play", 16, "#ffffff", "#ffffff")
        self.b_pause.setEnabled(st == "running")
        self.b_stop.setEnabled(running or st == "paused")
        for w in (self.start_seed, self.threads, self.rule_kind, self.rule_stack):
            w.setEnabled(not running)
        self.min_score.setEnabled(not running); self.top_k.setEnabled(not running)
        self.engine_chip.setProperty("state", "ok" if running else "off")
        self.engine_chip.style().unpolish(self.engine_chip); self.engine_chip.style().polish(self.engine_chip)
        if st == "paused" and not can_resume and self.sinfo is not None:
            why = ("the drawing changed" if self.sinfo.get("sig") != _sig_hash(self.model)
                   else "the version / world type changed")
            self.stale_lbl.setText(f"Search paused but {why}: Start begins a new search "
                                   f"(undo the change to resume).")
            self.stale_lbl.show()
        elif self.sinfo is not None and self.rows and self.sinfo.get("sig") != _sig_hash(self.model) \
                and not running:
            self.stale_lbl.setText("The drawing changed since this search: clicking a result "
                                   "re-scores it with the current drawing.")
            self.stale_lbl.show()
        else:
            self.stale_lbl.hide()
        if st in ("idle",):
            self.stat.setText("Idle"); self.prog.setRange(0, 1000); self.prog.setValue(0)
        elif st in ("paused", "done", "stopped"):
            self._tick_summary()

    # ═══ saving: library / autosave ═════════════════════════════════════════
    def search_state(self):
        s = self.sinfo
        if s is None:
            return None
        st = self.state if self.state in ("paused", "done", "stopped") else "paused"
        return dict(state=st, sig=s["sig"], mc=s["mc"], large=s["large"], start=s["start"],
                    done=s["done"], elapsed=round(s["elapsed"], 2), tested=s.get("tested", 0),
                    passed=s.get("passed", 0), min_score=s.get("min_score"), top_k=s.get("top_k"),
                    threads=s.get("threads"), rule=s.get("rule"),
                    results=[[int(a), float(b), int(c), int(d)] for a, b, c, d in self.rows])

    def to_dict(self):
        d = self.model.to_dict()
        ss = self.search_state()
        if ss is not None:
            d["search"] = ss
        d["ui"] = dict(min_score=self.min_score.value(), top_k=self.top_k.value(),
                       start=self.start_seed.text(), threads=self.threads.value(),
                       map_pct=self.op_slider.value(), recent=self._recent[:8])
        return d

    def load_dict(self, d, path=None):
        self._halt_for_io()
        self.model.load_dict(d, path)
        self.map.set_result(None)
        ui = d.get("ui") or {}
        if ui.get("min_score"):
            self.min_score.setValue(int(ui["min_score"]))
        if ui.get("top_k"):
            self.top_k.setValue(int(ui["top_k"]))
        if ui.get("start") is not None:
            self.start_seed.setText(str(ui["start"]))
        if ui.get("map_pct") is not None:
            self.op_slider.setValue(int(ui["map_pct"]))
        for c in reversed(ui.get("recent") or []):
            self._push_recent(int(c))
        s = d.get("search")
        self.rows = []
        self.sinfo = None
        if s:
            self.rows = [(int(a), float(b), int(c), int(e)) for a, b, c, e in s.get("results") or []]
            self.sinfo = dict(sig=s.get("sig"), mc=s.get("mc"), large=bool(s.get("large")),
                              start=int(s.get("start", 0)), done=int(s.get("done", 0)),
                              elapsed=float(s.get("elapsed", 0)), tested=int(s.get("tested", 0)),
                              passed=int(s.get("passed", 0)), min_score=s.get("min_score"),
                              top_k=s.get("top_k"), threads=s.get("threads"), rule=s.get("rule"))
            if s.get("min_score"):
                self.min_score.setValue(int(round(float(s["min_score"]) * 100)))
            if s.get("top_k"):
                self.top_k.setValue(int(s["top_k"]))
            self.thumbs.set_spec(self._current_spec(), f"{s.get('sig')}|{s.get('mc')}|{bool(s.get('large'))}")
        self.results.set_rows(self.rows)
        self._count()
        st = (s or {}).get("state") or "idle"
        self._run_state(st if s else "idle")
        self._sync_canvas_controls()
        QTimer.singleShot(0, self.fit)

    def _halt_for_io(self):
        """A running search is paused (and waited for) before the drawing is
        replaced or saved, so its state is exact."""
        if self.search is not None:
            self.search.pause()
            self.search.join(5.0)
            if self.search is not None and not self.search.running():
                self._finish()

    def save(self):
        m = self.model
        from .draw_library import is_library_entry
        if m.path and is_library_entry(m.path):
            self.save_to_library(path=m.path, name=m.name)
        else:
            name, ok = QInputDialog.getText(self, "Save to the library", "Name:", text=m.name)
            if ok and name.strip():
                self.save_to_library(new=True, name=name.strip())

    def save_to_library(self, new=False, path=None, name=None):
        was_running = self.state == "running"
        self._halt_for_io()
        m = self.model
        if name:
            m.name = name
        target = path if (path and not new) else new_entry_path()
        d = self.to_dict()
        if os.path.exists(target):
            try:
                d["created"] = read_json(target).get("created", d["created"])
            except Exception:
                pass
        try:
            write_json(target, d)
        except Exception as e:
            QMessageBox.critical(self, "Save", f"Could not save:\n{e}"); return None
        m.path = target
        m.dirty = False
        self._sync_canvas_controls()
        self.status.emit(f"Saved “{m.name}” to the library.")
        if was_running and self._resumable():
            self._launch(resume=True)
        return target

    def open_drawing(self, path):
        if not self._confirm_replace():
            return False
        try:
            d = read_json(path)
        except Exception as e:
            QMessageBox.critical(self, "Open", f"Could not open:\n{e}"); return False
        self.load_dict(d, path)
        self.status.emit(f"Opened “{self.model.name}”.")
        return True

    def _confirm_replace(self):
        m = self.model
        if not m.dirty or not m.painted():
            return True
        r = QMessageBox.question(self, "Drawing", f"Save “{m.name}” to the library first?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                 | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save:
            self.save()
        return True

    def _new(self):
        if self.state in ("running", "pausing"):
            QMessageBox.information(self, "New drawing", "Pause or stop the search first."); return
        if not self._confirm_replace():
            return
        self.rows = []; self.results.set_rows([]); self.sinfo = None; self._count()
        self.model.new(48, 48, 64)
        self.model.center = (int(self.map.cx), int(self.map.cz))
        self._run_state("idle")
        self._sync_canvas_controls()

    def open_library(self):
        DrawLibraryDialog(self, self).exec()

    def library_renamed(self, path, name):
        if self.model.path and os.path.normcase(self.model.path) == os.path.normcase(path):
            self.model.name = name
            self._sync_canvas_controls()

    def library_described(self, path, text):
        if self.model.path and os.path.normcase(self.model.path) == os.path.normcase(path):
            self.model.description = text

    def library_deleted(self, path):
        if self.model.path and os.path.normcase(self.model.path) == os.path.normcase(path):
            self.model.path = None
            self.model.dirty = True

    def autosave(self, closing=False):
        """The drawing (and its search, paused) comes back at the next launch."""
        if closing:
            self._halt_for_io()
        try:
            d = self.to_dict()
            d["path"] = self.model.path
            d["dirty"] = self.model.dirty
            if self.state in ("running", "pausing") and "search" in d:
                d["search"]["state"] = "paused"
                d["search"]["done"] = self.sinfo["done"] + (self.search.done if self.search else 0)
            write_json(autosave_path(), d)
        except Exception:
            pass

    def autosave_if_changed(self):
        if self.state in ("pausing",):
            return
        sig = (_sig_hash(self.model), self.model.name, len(self.rows),
               self.sinfo["done"] if self.sinfo else None, self.state)
        if sig != self._autosave_sig:
            self._autosave_sig = sig
            if self.state == "running":
                # a running search: its exact resume point is only known at a
                # batch boundary; save what is certain (done so far)
                pass
            self.autosave()

    def restore_autosave(self):
        from .draw_library import import_legacy_presets_once
        try:
            import_legacy_presets_once(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        except Exception:
            pass
        p = autosave_path()
        if not os.path.exists(p):
            self.model.new(48, 48, 64)
            return
        try:
            d = read_json(p)
            path = d.get("path")
            self.load_dict(d, path if path and os.path.exists(path) else None)
            self.model.dirty = bool(d.get("dirty", False))
        except Exception:
            self.model.new(48, 48, 64)

    def shutdown(self):
        self.autosave(closing=True)
        if self.search is not None:
            self.search.stop()
            self.search.join(2.0)
