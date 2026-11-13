"""
qtui/seedcrack_page.py — (05/11) the Seedcracking workspace: rebuild in 3D the trees
seen in a world, then crack them into the structure seed (tree_crack.py).

  ┌ Scene ──────────────┐┌ 3D view ──────────────────────────┐┌ Tree | Results ─────┐
  │ version             ││ the population chunks, the trees, ││ type · position     │
  │ chunks (biome, bits)││ the 12 random corner leaves        ││ height · 3 leaf     │
  │ trees               ││ (leaf / no leaf / unknown)         ││ layers · bits       │
  └─────────────────────┘└────────────────────────────────────┘└─────────────────────┘
  ┌ run bar: information · device · threads · estimate · Crack / Stop · progress ───┐

Java 1.14 – 1.17.1 trees: Forest / Birch Forest (oak, birch, fancy oak), Taiga (1.16+).
(06/11) Every version up to 26.x: structures (struct_crack.py) and the End pillars.
"""
from __future__ import annotations

import json
import os
import re
import threading

from PySide6.QtCore import QObject, QPointF, QRectF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QKeySequence, QPainter, QPen,
                           QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPlainTextEdit, QDialog, QDialogButtonBox,
                               QProgressBar, QPushButton, QSpinBox, QSplitter,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

import tree_crack as tc

from . import icons, theme
from .tree_view3d import TreeView3D
from .widgets import Segmented, body, section_title, user_path, remember_path

TYPE_LABEL = {"oak": "Oak", "birch": "Birch", "fancy": "Fancy oak", "spruce": "Spruce", "pine": "Pine",
              "unknown": "Unknown"}
TYPE_COL = {"oak": "#6aa84f", "birch": "#d9d4c7", "fancy": "#3f8f3a", "spruce": "#3d5e3a",
            "pine": "#5b7f45", "unknown": "#8a8f98"}
ATTR_TIPS = {
    ("spruce", "bare"): "Logs of the trunk under the lowest leaves (from the ground).",
    ("spruce", "above"): "Leaf layers above the top log (the tip of the tree).",
    ("spruce", "widest"): "Radius of the widest leaf layer (blocks from the trunk to the edge).",
    ("spruce", "top"): "Radius of the highest leaf layer: 0 = a single leaf, 1 = a small cross.",
    ("pine", "layers"): "Leaf layers of the crown (the leaves are all near the top).",
    ("pine", "widest"): "Radius of the widest leaf layer (blocks from the trunk to the edge).",
}
STATE_NEXT = {"1": "0", "0": "?", "?": "1"}


def bits_color(b: float) -> str:
    if b >= tc.BITS_NEEDED:
        return theme.OK or "#3fb27f"
    if b >= tc.BITS_MINIMUM:
        return theme.WARN or "#e3b341"
    return theme.DANGER or "#e5534b"


# ── small widgets ──────────────────────────────────────────────────────────────
class BitsMeter(QWidget):
    """A bar: the bits of information against what the crack needs (48 / 56)."""

    def __init__(self, compact=False, parent=None):
        super().__init__(parent)
        self.bits = 0.0
        self.compact = compact
        self.setFixedHeight(8 if compact else 26)
        self.setMinimumWidth(80)

    def set_bits(self, b):
        self.bits = float(b)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        top = 0 if self.compact else 14
        h = 6 if self.compact else 8
        full = 80.0
        r = QRectF(0.5, top + 0.5, W - 1, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BG3 or "#33363d"))
        p.drawRoundedRect(r, h / 2, h / 2)
        fr = min(1.0, self.bits / full)
        if fr > 0:
            p.setBrush(QColor(bits_color(self.bits)))
            p.drawRoundedRect(QRectF(r.x(), r.y(), max(h, r.width() * fr), h), h / 2, h / 2)
        for mark in (tc.BITS_MINIMUM, tc.BITS_NEEDED):
            x = r.x() + r.width() * mark / full
            p.setPen(QPen(QColor(theme.TEXT2 or "#aaa"), 1.2))
            p.drawLine(QPointF(x, top - (0 if self.compact else 2)), QPointF(x, top + h + 1))
        if not self.compact:
            p.setFont(theme.ui_font(7.5))
            p.setPen(QColor(theme.TEXT3 or "#888"))
            for mark in (tc.BITS_MINIMUM, tc.BITS_NEEDED):
                x = r.x() + r.width() * mark / full
                p.drawText(QRectF(x - 14, 0, 28, 12), Qt.AlignmentFlag.AlignCenter, f"{mark:.0f}")
        p.end()


class LeafLayer(QWidget):
    """One leaf layer seen from above (north up): the trunk, the fixed leaves and the
    4 random corners — click a corner: leaf → no leaf → unknown."""
    changed = Signal(int, int, str)            # layer, corner, new state

    def __init__(self, layer: int, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.r = 1 if layer == 2 else 2
        self.states = "????"
        self.leaf_col = QColor(TYPE_COL["oak"])
        self.enabled_ = True
        self.setFixedSize(92, 92)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._hover = -1
        self.risky = set()

    def set_states(self, s: str, typ: str, enabled: bool, risky=()):
        self.risky = set(risky)
        self.states = s
        self.enabled_ = enabled
        self.leaf_col = QColor({"birch": "#86b153"}.get(typ, "#5f9e3c"))
        self.update()

    def _cell(self):
        n = 2 * self.r + 1
        return (self.width() - 8) / 5.0, n

    def _corner_rect(self, ci):
        cs, n = self._cell()
        off = (5 - n) / 2 * cs + 4
        dx = -self.r if ci in (0, 1) else self.r
        dz = -self.r if ci in (0, 2) else self.r
        return QRectF(off + (dx + self.r) * cs, off + (dz + self.r) * cs, cs, cs)

    def mouseMoveEvent(self, e):
        h = -1
        for ci in range(4):
            if self._corner_rect(ci).contains(e.position()):
                h = ci
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        if not self.enabled_:
            return
        for ci in range(4):
            if self._corner_rect(ci).contains(e.position()):
                cur = self.states[ci]
                new = "?" if e.button() == Qt.MouseButton.RightButton else STATE_NEXT.get(cur, "1")
                self.changed.emit(self.layer, ci, new)
                return

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cs, n = self._cell()
        off = (5 - n) / 2 * cs + 4
        dim = 1.0 if self.enabled_ else 0.35
        for i in range(n):
            for j in range(n):
                dx, dz = i - self.r, j - self.r
                rc = QRectF(off + i * cs + 1, off + j * cs + 1, cs - 2, cs - 2)
                corner = abs(dx) == self.r and abs(dz) == self.r
                if dx == 0 and dz == 0:
                    c = QColor("#7a5a35")
                    p.setPen(Qt.PenStyle.NoPen); p.setBrush(c); p.drawRoundedRect(rc, 2, 2)
                    continue
                if not corner:
                    c = QColor(self.leaf_col); c.setAlphaF(0.55 * dim)
                    p.setPen(Qt.PenStyle.NoPen); p.setBrush(c); p.drawRoundedRect(rc, 2, 2)
                    continue
                ci = (0 if dz < 0 else 1) if dx < 0 else (2 if dz < 0 else 3)
                st = self.states[ci]
                hov = ci == self._hover and self.enabled_
                if st == "1":
                    c = QColor(self.leaf_col).lighter(118)
                    if ci in self.risky:
                        p.setPen(QPen(QColor("#ffb82e"), 2.0))
                    else:
                        p.setPen(QPen(QColor("#d8ffb0") if hov else c.darker(130), 1.4))
                    p.setBrush(c); p.drawRoundedRect(rc, 2, 2)
                elif st == "0":
                    p.setPen(QPen(QColor(255, 255, 255, 230 if hov else 170), 1.4))
                    p.setBrush(QColor(255, 255, 255, 18)); p.drawRoundedRect(rc.adjusted(1, 1, -1, -1), 2, 2)
                else:
                    p.setPen(QPen(QColor("#ff8a80") if hov else QColor("#d63a34"), 1.4))
                    p.setBrush(QColor(214, 58, 52, 70))
                    p.drawRoundedRect(rc, 2, 2)
                    p.setPen(QColor(255, 255, 255))
                    p.setFont(theme.ui_font(8.0, QFont.Weight.Bold))
                    p.drawText(rc, Qt.AlignmentFlag.AlignCenter, "?")
        p.end()


class ChunkCard(QFrame):
    clicked = Signal(tuple)
    biomeChanged = Signal(tuple, str)

    def __init__(self, ch, parent=None):
        super().__init__(parent)
        self.ch = ch
        self.setObjectName("CrackChunk")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(self); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(5)
        top = QHBoxLayout(); top.setSpacing(6)
        self.title = QLabel(f"Chunk {ch[0]}, {ch[1]}")
        f = self.title.font(); f.setBold(True); self.title.setFont(f)
        top.addWidget(self.title)
        self.badge = QLabel("main"); self.badge.setObjectName("Chip"); self.badge.setProperty("state", "ok")
        self.badge.setToolTip("The chunk with the most information: the crack starts from it, "
                              "the other chunks then confirm the seeds.")
        top.addWidget(self.badge)
        top.addStretch(1)
        self.bits = QLabel()
        top.addWidget(self.bits)
        v.addLayout(top)
        self.meter = BitsMeter(compact=True)
        v.addWidget(self.meter)
        row = QHBoxLayout(); row.setSpacing(6)
        self.info = QLabel(); self.info.setObjectName("Muted")
        row.addWidget(self.info, 1)
        self.biome = Segmented(["Forest", "Birch", "Taiga"], "Forest")
        self.biome.setFixedWidth(190)
        self.biome.buttons["Birch"].setToolTip("Birch Forest (and its hills): only birches")
        self.biome.buttons["Taiga"].setToolTip("Taiga, Taiga Hills, Snowy Taiga: spruces and pines")
        self.biome.changed.connect(lambda b: self.biomeChanged.emit(
            self.ch, {"Birch": "birch_forest", "Taiga": "taiga"}.get(b, "forest")))
        row.addWidget(self.biome)
        v.addLayout(row)

    def update_from(self, scene, main):
        trees = scene.chunks().get(self.ch, [])
        bio = scene.biome(self.ch)
        b = tc.chunk_bits(trees, bio)
        self.bits.setText(f"<b style='color:{bits_color(b)}'>{b:.0f}</b> bits")
        self.meter.set_bits(b)
        self.info.setText(f"{len(trees)} tree{'s' * (len(trees) != 1)}")
        self.badge.setVisible(main)
        self.biome.set_value({"birch_forest": "Birch", "taiga": "Taiga"}.get(bio, "Forest"))
        from .widgets import explain_disabled as _xd
        _xd(self.biome.buttons["Taiga"], scene.version in tc.TAIGA_VERSIONS or bio == "taiga",
            "taiga trees need Java 1.16 or newer for now.")
        self.setProperty("main", main)
        self.setStyleSheet(
            f"#CrackChunk {{ background: {theme.BG2}; border: 1px solid "
            f"{theme.ACCENT_LINE or theme.ACCENT if main else theme.LINE}; border-radius: 9px; }}")

    def mousePressEvent(self, e):
        self.clicked.emit(self.ch)


class _Worker(QObject):
    progress = Signal(float, str)
    seed = Signal(object)
    done = Signal(object)
    failed = Signal(str)


# ── the page ───────────────────────────────────────────────────────────────────
class SeedcrackPage(QWidget):
    status = Signal(str)
    openOnMap = Signal(object, str, int, int)          # seed, version name, x, z
    sendSisters = Signal(object, str)                  # structure seed, version name

    def __init__(self, world=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.world = world
        self.scene = tc.Scene()
        self.sel = -1
        self._undo, self._redo = [], []
        self._stop = threading.Event()
        self._running = None              # "crack" | "world" | None
        self._result = None
        self._seeds = []
        self._gpu_state = None            # (ok, msg)
        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self._autosave)

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 2, 10, 4); outer.setSpacing(8)
        split = QSplitter(Qt.Orientation.Horizontal); split.setHandleWidth(10)
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)
        split.addWidget(self._build_left())
        split.addWidget(self._build_view())
        split.addWidget(self._build_right())
        split.setStretchFactor(1, 1)
        split.setSizes([330, 860, 340])
        outer.addWidget(self._build_runbar())

        for seq, fn in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo),
                        ("Ctrl+Shift+Z", self.redo), ("A", lambda: self._tool_add(True)),
                        ("Ctrl+S", self._save), ("Ctrl+O", self._open),
                        ("I", lambda: self.iso_btn.toggle())):
            sc = QShortcut(QKeySequence(seq), self); sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self._restore()
        self._refresh_all(reframe=True)

    # ── left: scene ──────────────────────────────────────────────────────────
    def _build_left(self):
        left = QFrame(); left.setObjectName("SidePanel"); left.setMinimumWidth(300)
        v = QVBoxLayout(left); v.setContentsMargins(16, 14, 14, 12); v.setSpacing(8)
        top = QHBoxLayout()
        ic = QLabel(); ic.setPixmap(icons.pixmap("forest", 24, theme.ACCENT)); top.addWidget(ic)
        tl = QVBoxLayout(); tl.setSpacing(0)
        t = QLabel("Seedcracking"); t.setObjectName("PanelTitle"); tl.addWidget(t)
        st = QLabel("Trees · structures → structure seed"); st.setObjectName("Muted"); tl.addWidget(st)
        top.addLayout(tl); top.addStretch(1)
        self.file_btn = QToolButton(); icons.bind(self.file_btn, "more", 18)
        self.file_btn.setToolTip("New · Open · Save · TreeCracker import / export")
        self.file_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(self.file_btn)
        for text, fn, key in (("New scene", self._new, None), ("Open…", self._open, "Ctrl+O"),
                              ("Save as…", self._save, "Ctrl+S"), (None, None, None),
                              ("Import TreeCracker input…", self._import_tc, None),
                              ("Copy as TreeCracker input", self._export_tc, None),
                              ("Copy the scene (to share)", self._copy_scene, None), (None, None, None),
                              ("Test with a known seed…", self._test_seed, None)):
            if text is None:
                m.addSeparator(); continue
            a = m.addAction(text); a.triggered.connect(fn)
            if key:
                a.setShortcut(QKeySequence(key))
        self.file_btn.setMenu(m)
        top.addWidget(self.file_btn)
        v.addLayout(top)

        v.addSpacing(4)
        v.addWidget(section_title("Game version"))
        self.version = QComboBox()
        for k, (lab, *_r) in tc.VERSIONS.items():
            self.version.addItem(lab, k)
        self.version.setToolTip("The version the world was GENERATED in (where these trees grew).")
        self.version.currentIndexChanged.connect(self._version_changed)
        v.addWidget(self.version)

        v.addSpacing(4)
        self.chunks_title = section_title("Population chunks")
        hdr = QHBoxLayout(); hdr.addWidget(self.chunks_title); hdr.addStretch(1)
        v.addLayout(hdr)
        self.chunk_box = QVBoxLayout(); self.chunk_box.setSpacing(6)
        cw = QWidget(); cw.setLayout(self.chunk_box)
        v.addWidget(cw)
        self.chunk_hint = body("", muted=True, small=True)
        v.addWidget(self.chunk_hint)

        # (06/11) the End's pillars: ~16 more bits, any version
        pc = QFrame(); pc.setObjectName("CrackPillars")
        pc.setStyleSheet(f"#CrackPillars {{ background: {theme.BG2}; border: 1px solid {theme.LINE};"
                         f" border-radius: 9px; }}")
        ph = QHBoxLayout(pc); ph.setContentsMargins(10, 7, 8, 7); ph.setSpacing(8)
        pv = QVBoxLayout(); pv.setSpacing(1)
        pt = QLabel("End pillars"); f = pt.font(); f.setBold(True); pt.setFont(f)
        pv.addWidget(pt)
        self.pillar_info = QLabel(); self.pillar_info.setObjectName("Muted")
        pv.addWidget(self.pillar_info)
        ph.addLayout(pv, 1)
        self.pillar_btn = QPushButton("  Edit…"); icons.bind(self.pillar_btn, "target", 16)
        self.pillar_btn.setToolTip(
            "The 10 obsidian pillars of the End (their height order and cages) depend on the "
            "structure seed: about 16 more bits, to sort out the false seeds the trees leave.")
        self.pillar_btn.clicked.connect(self._edit_pillars)
        ph.addWidget(self.pillar_btn)
        v.addWidget(pc)

        # (06/11) structures: ~9 bits each, every version (the only way from 1.18 on)
        sc_ = QFrame(); sc_.setObjectName("CrackStructs")
        sc_.setStyleSheet(f"#CrackStructs {{ background: {theme.BG2}; border: 1px solid {theme.LINE};"
                          f" border-radius: 9px; }}")
        sh = QHBoxLayout(sc_); sh.setContentsMargins(10, 7, 8, 7); sh.setSpacing(8)
        sv = QVBoxLayout(); sv.setSpacing(1)
        st_ = QLabel("Structures"); f = st_.font(); f.setBold(True); st_.setFont(f)
        sv.addWidget(st_)
        self.struct_info = QLabel(); self.struct_info.setObjectName("Muted")
        sv.addWidget(self.struct_info)
        sh.addLayout(sv, 1)
        self.struct_btn = QPushButton("  Edit…"); icons.bind(self.struct_btn, "pin", 16)
        self.struct_btn.setToolTip(
            "Villages, temples, shipwrecks, monuments… seen in the world: the chunk each one "
            "starts in depends on the structure seed — ~9 bits each, any version.")
        self.struct_btn.clicked.connect(self._edit_structures)
        sh.addWidget(self.struct_btn)
        v.addWidget(sc_)

        v.addSpacing(4)
        hdr = QHBoxLayout(); hdr.addWidget(section_title("Trees")); hdr.addStretch(1)
        self.add_btn = QPushButton("  Add tree"); icons.bind(self.add_btn, "plus", 16)
        self.add_btn.setCheckable(True)
        self.add_btn.setToolTip("Then click the ground of the 3D view where the trunk is  (A)\n"
                                "Double-click the ground also places a tree.")
        self.add_btn.toggled.connect(self._tool_add)
        hdr.addWidget(self.add_btn)
        v.addLayout(hdr)
        self.tree_list = QListWidget(); self.tree_list.setObjectName("CrackTrees")
        self.tree_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree_list.currentRowChanged.connect(self._list_select)
        self.tree_list.setIconSize(QSize(12, 12))
        v.addWidget(self.tree_list, 1)
        self.empty_hint = body("No tree yet. Click “Add tree”, then the ground where a trunk "
                               "stands — or import a TreeCracker input.", muted=True, small=True)
        v.addWidget(self.empty_hint)
        cr = QLabel("Method of Andrew (Gaider10)'s TreeCracker")
        cr.setObjectName("Muted"); cr.setStyleSheet("font-size: 7.5pt;")
        cr.setToolTip("SSA's tree cracker follows the method of TreeCracker and PopulationCrr by "
                      "Andrew (Gaider10), with knowledge from NelS' tree-cracker, Epic10l2's "
                      "documentation, Neil's tree salts, Cortex and TatertotGr8. Coordinates: "
                      "19MisterX98's TextureRotations. See THIRD_PARTY_LICENSES.txt.")
        v.addWidget(cr)
        return left

    # ── centre: 3D ───────────────────────────────────────────────────────────
    def _build_view(self):
        box = QFrame(); box.setObjectName("CrackView")
        box.setStyleSheet(f"#CrackView {{ border: 1px solid {theme.LINE}; border-radius: 10px; }}")
        v = QVBoxLayout(box); v.setContentsMargins(1, 1, 1, 1); v.setSpacing(0)
        self.view = TreeView3D()
        self.view.treeSelected.connect(self._select)
        self.view.cornerClicked.connect(self._corner_clicked)
        self.view.groundClicked.connect(self._ground_clicked)
        self.view.moveSelected.connect(self._move_selected)
        self.view.deleteSelected.connect(self._delete_selected)
        self.view.hoverText.connect(lambda t: self.hover_lbl.setText(t or self._help_text))
        v.addWidget(self.view, 1)
        bar = QFrame(); bar.setObjectName("CrackViewBar")
        bar.setStyleSheet(f"#CrackViewBar {{ background: {theme.PANEL or theme.BG1}; border-top: 1px solid {theme.LINE};"
                          f" border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }}")
        h = QHBoxLayout(bar); h.setContentsMargins(10, 4, 8, 4); h.setSpacing(4)
        self.hover_lbl = QLabel(); self.hover_lbl.setObjectName("Muted")
        self.hover_lbl.setFont(theme.ui_font(8.5))
        from PySide6.QtWidgets import QSizePolicy
        self.hover_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        h.addWidget(self.hover_lbl, 1)
        self._help_text = ("Drag: turn · Right-drag: move · Wheel: zoom · Click a corner: "
                           "leaf → no leaf → unknown · Double-click the ground: add a tree")
        self.hover_lbl.setText(self._help_text)
        self.iso_btn = QToolButton(); icons.bind(self.iso_btn, "target", 16)
        self.iso_btn.setCheckable(True)
        self.iso_btn.setToolTip("Isolate the selected tree: the others fade out and can't be "
                                "clicked — handy to reach corners hidden behind other trees  (I)")
        self.iso_btn.toggled.connect(self._isolate)
        h.addWidget(self.iso_btn)
        for ic, tip, fn in (("home", "Frame the trees", lambda: self.view.reframe()),
                            ("grid", "View from above", lambda: self.view.view_top()),
                            ("layers", "3D view", lambda: self.view.view_iso())):
            b = QToolButton(); icons.bind(b, ic, 16); b.setToolTip(tip); b.clicked.connect(fn)
            h.addWidget(b)
        v.addWidget(bar)
        return box

    # ── right: inspector / results ───────────────────────────────────────────
    def _build_right(self):
        right = QFrame(); right.setObjectName("SidePanel"); right.setMinimumWidth(320)
        v = QVBoxLayout(right); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(8)
        self.tabs = Segmented(["Tree", "Results"], "Tree")
        self.tabs.changed.connect(lambda t: self.stack.setCurrentIndex(0 if t == "Tree" else 1))
        v.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_inspector())
        self.stack.addWidget(self._build_results())
        v.addWidget(self.stack, 1)
        return right

    def _build_inspector(self):
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(0, 4, 0, 0); v.setSpacing(8)
        self.insp_empty = body("Select a tree in the 3D view or the list — or place one with "
                               "“Add tree”.\n\nFor each tree enter what you SAW: its type, the logs "
                               "of its trunk and the 12 corner leaves (the corners of the 3 leaf "
                               "layers under the top). A corner you can't see stays unknown.",
                               muted=True)
        v.addWidget(self.insp_empty)
        self.insp = QWidget()
        iv = QVBoxLayout(self.insp); iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(8)
        head = QHBoxLayout()
        self.insp_title = QLabel(); self.insp_title.setObjectName("PanelTitle")
        head.addWidget(self.insp_title); head.addStretch(1)
        self.insp_bits = QLabel(); head.addWidget(self.insp_bits)
        db = QToolButton(); icons.bind(db, "trash", 16); db.setToolTip("Delete this tree  (Del)")
        db.clicked.connect(self._delete_selected); head.addWidget(db)
        iv.addLayout(head)

        iv.addWidget(section_title("Type"))
        self.type_stack = QStackedWidget()
        self.type_seg = Segmented([TYPE_LABEL[t] for t in ("oak", "birch", "fancy", "unknown")], "Oak")
        self.type_seg.changed.connect(self._set_type)
        self.type_seg_taiga = Segmented([TYPE_LABEL[t] for t in ("spruce", "pine", "unknown")], "Spruce")
        self.type_seg_taiga.changed.connect(self._set_type)
        self.type_stack.addWidget(self.type_seg)
        self.type_stack.addWidget(self.type_seg_taiga)
        self.type_stack.setFixedHeight(self.type_seg.sizeHint().height())
        iv.addWidget(self.type_stack)

        iv.addWidget(section_title("Trunk position"))
        pr = QHBoxLayout(); pr.setSpacing(6)
        from .widgets import CoordEdit
        self.px = CoordEdit(placeholder="X"); self.pz = CoordEdit(placeholder="Z")
        for lab, sp in (("X", self.px), ("Z", self.pz)):
            sp.valueChanged.connect(self._set_pos)
            sp.pasted.connect(self._paste_pos)
            l = QLabel(lab); l.setObjectName("FieldLabel"); pr.addWidget(l); pr.addWidget(sp, 1)
        iv.addLayout(pr)
        self.pos_info = QLabel(); self.pos_info.setObjectName("Muted"); iv.addWidget(self.pos_info)

        iv.addWidget(section_title("Trunk height (logs)"))
        self.h_stack = QStackedWidget()
        self.h_seg = {}
        for typ, (lo, hi) in (("oak", (4, 6)), ("birch", (5, 7)), ("spruce", (5, 8)), ("pine", (6, 10))):
            s = Segmented(["?"] + [str(i) for i in range(lo, hi + 1)], "?")
            s.changed.connect(self._set_height)
            self.h_seg[typ] = s
            self.h_stack.addWidget(s)
        self.h_fancy = QSpinBox(); self.h_fancy.setRange(3, 12); self.h_fancy.setSpecialValueText("?")
        self.h_fancy.setToolTip("The straight column of logs of the trunk, from the ground up "
                                "(? when unsure: a fancy oak gives little information).")
        self.h_fancy.valueChanged.connect(lambda v_: self._set_height("?" if v_ <= 3 else str(v_)))
        self.h_stack.addWidget(self.h_fancy)
        self.h_none = body("Unknown type: no height.", muted=True, small=True)
        self.h_stack.addWidget(self.h_none)
        self.h_old = body("Not used in 1.14 / 1.15 (the fancy oak draws it from its own random).",
                          muted=True, small=True)
        self.h_stack.addWidget(self.h_old)
        iv.addWidget(self.h_stack)

        # (06/11) spruce / pine: the shape of the crown
        self.conifer = QWidget()
        cv = QVBoxLayout(self.conifer); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(6)
        cv.addWidget(section_title("Crown"))
        self.attr_rows = {}
        for typ, attrs in (("spruce", tc.SPRUCE_ATTRS), ("pine", tc.PINE_ATTRS)):
            for key, label, (lo, hi) in attrs:
                row = QWidget()
                rh = QHBoxLayout(row); rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(8)
                lab = QLabel(label); lab.setMinimumWidth(130)
                lab.setToolTip(ATTR_TIPS.get((typ, key), ""))
                rh.addWidget(lab)
                seg = Segmented(["?"] + [str(i) for i in range(lo, hi + 1)], "?")
                seg.setToolTip(ATTR_TIPS.get((typ, key), ""))
                seg.changed.connect(lambda val, k=key: self._set_attr(k, val))
                rh.addWidget(seg, 1)
                cv.addWidget(row)
                self.attr_rows[(typ, key)] = (row, seg)
        iv.addWidget(self.conifer)

        self.leaves_box = QWidget()
        lv_ = QVBoxLayout(self.leaves_box); lv_.setContentsMargins(0, 0, 0, 0); lv_.setSpacing(8)
        iv.addWidget(self.leaves_box)
        self.leaf_title = section_title("Corner leaves")
        lv_.addWidget(self.leaf_title)
        lg = QHBoxLayout(); lg.setSpacing(6)
        self.layers = []
        for li, name in ((0, "Lowest"), (1, "Middle"), (2, "Top")):
            col = QVBoxLayout(); col.setSpacing(2)
            lw = LeafLayer(li); lw.changed.connect(self._leaf_changed)
            lw.setToolTip({0: "2 below the top log (radius 2)", 1: "1 below the top log (radius 2)",
                           2: "the top log's level (radius 1)"}[li]
                          + "\nClick a corner: leaf → no leaf → unknown · right-click: unknown")
            self.layers.append(lw)
            col.addWidget(lw, 0, Qt.AlignmentFlag.AlignHCenter)
            l = QLabel(name); l.setObjectName("Muted"); l.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(l)
            lg.addLayout(col)
        lv_.addLayout(lg)
        iv.addStretch(1)
        v.addWidget(self.insp)
        v.addStretch(1)
        return w

    def _build_results(self):
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(0, 4, 0, 0); v.setSpacing(8)
        self.res_summary = body("No crack yet.", muted=True)
        v.addWidget(self.res_summary)
        self.res_list = _DeselectList(); self.res_list.setObjectName("CrackSeeds")
        self.res_list.setFont(theme.mono_font(10))
        self.res_list.currentRowChanged.connect(self._seed_selected)
        v.addWidget(self.res_list, 2)
        acts = QGridLayout(); acts.setSpacing(6)
        self.b_copy = QPushButton("  Copy"); icons.bind(self.b_copy, "copy", 16)
        self.b_copy.clicked.connect(self._copy_seeds)
        self.b_copy.setToolTip("Copy the selected structure seed (all of them when none is selected)")
        self.b_compare = QPushButton("  Compare trees"); icons.bind(self.b_compare, "tree", 16)
        self.b_compare.setCheckable(True)
        self.b_compare.setToolTip("Show in the 3D view, translucent, the trees this seed grows in "
                                  "the chunk below — go there in game and compare.")
        self.b_compare.toggled.connect(self._compare)
        self.b_world = QPushButton("  World seeds…"); self.b_world.setProperty("kind", "primary")
        icons.bind(self.b_world, "search", 16, "text", "text")
        self.b_world.setToolTip("The 65 536 world seeds of this structure seed whose biome is the "
                                "one entered at every chunk (checked with the game's biome "
                                "generation). Seeds a blank seed field (random) can give come first.")
        self.b_world.clicked.connect(self._world_seeds)
        self.b_sisters = QPushButton("  Sister seeds"); icons.bind(self.b_sisters, "sessions", 16)
        self.b_sisters.setToolTip("Open Sessions in “Sister seeds” mode on this structure seed, to "
                                  "search its world seeds with your own conditions.")
        self.b_sisters.clicked.connect(self._to_sisters)
        acts.addWidget(self.b_copy, 0, 0); acts.addWidget(self.b_compare, 0, 1)
        acts.addWidget(self.b_world, 1, 0, 1, 2); acts.addWidget(self.b_sisters, 2, 0, 1, 2)
        v.addLayout(acts)
        cr = QHBoxLayout(); cr.setSpacing(6)
        l = QLabel("Compare chunk"); l.setObjectName("FieldLabel"); cr.addWidget(l)
        self.cmp_x = QSpinBox(); self.cmp_z = QSpinBox()
        for sp in (self.cmp_x, self.cmp_z):
            sp.setRange(-2_000_000, 2_000_000)
            sp.valueChanged.connect(lambda *_: self._compare(self.b_compare.isChecked()))
            cr.addWidget(sp, 1)
        v.addLayout(cr)
        self.cmp_info = body("", muted=True, small=True)
        v.addWidget(self.cmp_info)
        self.ws_title = section_title("World seeds")
        v.addWidget(self.ws_title)
        from PySide6.QtWidgets import QLineEdit
        self.ws_find = QLineEdit(); self.ws_find.setPlaceholderText("Find a seed in the list…")
        self.ws_find.setClearButtonEnabled(True)
        self.ws_find.textChanged.connect(self._ws_filter)
        v.addWidget(self.ws_find)
        self.ws_list = QListWidget(); self.ws_list.setFont(theme.mono_font(9.5))
        self.ws_list.itemDoubleClicked.connect(lambda it: self._open_world(it.data(Qt.ItemDataRole.UserRole)))
        v.addWidget(self.ws_list, 3)
        wr = QHBoxLayout(); wr.setSpacing(6)
        self.b_ws_copy = QPushButton("  Copy all"); icons.bind(self.b_ws_copy, "copy", 16)
        self.b_ws_copy.clicked.connect(self._copy_world)
        self.b_ws_open = QPushButton("  Open on map"); icons.bind(self.b_ws_open, "map", 16)
        self.b_ws_open.clicked.connect(lambda: self._open_world(
            self.ws_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.ws_list.currentItem() else None))
        wr.addWidget(self.b_ws_copy); wr.addWidget(self.b_ws_open)
        v.addLayout(wr)
        self._sync_result_buttons()
        return w

    # ── run bar ──────────────────────────────────────────────────────────────
    def _build_runbar(self):
        bar = QFrame(); bar.setObjectName("RunBar")
        h = QHBoxLayout(bar); h.setContentsMargins(14, 8, 10, 8); h.setSpacing(12)
        col = QVBoxLayout(); col.setSpacing(1)
        self.info_title = QLabel(); f = self.info_title.font(); f.setBold(True); self.info_title.setFont(f)
        class _TipLabel(QLabel):                 # (13/11) clipped when narrow: full text on hover
            def setText(self, t):
                super().setText(t)
                self.setToolTip(t)
        self.info_sub = _TipLabel(); self.info_sub.setObjectName("Muted")
        from PySide6.QtWidgets import QSizePolicy
        for lab in (self.info_title, self.info_sub):
            lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lab.setMinimumWidth(120)
        col.addWidget(self.info_title); col.addWidget(self.info_sub)
        h.addLayout(col, 1)
        self.meter = BitsMeter()
        self.meter.setFixedWidth(220)
        self.meter.setToolTip("Information of the main chunk, in bits. A structure seed is 48 bits: "
                              "below 48 the crack can't single it out; from 56 on, false seeds "
                              "become rare. A position ≈ 8 bits, a height ≈ 1.6, a seen corner 1.")
        h.addWidget(self.meter)
        from PySide6.QtWidgets import QCheckBox
        self.shared = QCheckBox("Shared leaves → unknown")
        self.shared.setChecked(str(QSettings("SSA", "SimpleSeedAnalyser").value(
            "seedcrack/shared_unknown", "true")).lower() == "true")
        self.shared.setToolTip(
            "A corner seen WITH a leaf, within reach of another tree's leaves (amber in the 3D "
            "view), may hold the other tree's leaf: counted as unknown. Safer — one wrong corner "
            "loses the seed. Enter the neighbouring trees too (even in other chunks) so SSA knows "
            "which corners they can reach.")
        self.shared.toggled.connect(lambda on: (QSettings("SSA", "SimpleSeedAnalyser").setValue(
            "seedcrack/shared_unknown", "true" if on else "false"), self._refresh_all(scene_changed=False)))
        h.addWidget(self.shared)
        dl = QLabel("Device"); dl.setObjectName("FieldLabel"); h.addWidget(dl)
        self.device = Segmented(["GPU", "CPU"], "GPU")
        self.device.setFixedWidth(120)
        self.device.changed.connect(lambda *_: self._refresh_run())
        h.addWidget(self.device)
        tl = QLabel("Threads"); tl.setObjectName("FieldLabel"); h.addWidget(tl)
        self.threads = QSpinBox(); self.threads.setRange(1, max(1, os.cpu_count() or 1))
        self.threads.setValue(max(1, os.cpu_count() or 1))
        self.threads.valueChanged.connect(lambda *_: self._refresh_run())
        h.addWidget(self.threads)
        self.est = QLabel(); self.est.setObjectName("Muted"); self.est.setMinimumWidth(110)
        h.addWidget(self.est)
        self.prog = QProgressBar(); self.prog.setRange(0, 1000); self.prog.setFixedWidth(160)
        self.prog.setTextVisible(False); self.prog.hide()
        h.addWidget(self.prog)
        self.run_btn = QPushButton("  Crack"); self.run_btn.setProperty("kind", "primary")
        icons.bind(self.run_btn, "play", 16, "text", "text")
        self.run_btn.setMinimumWidth(120)
        self.run_btn.clicked.connect(self._run_clicked)
        h.addWidget(self.run_btn)
        return bar

    # ── scene edits ──────────────────────────────────────────────────────────
    def _snap(self):
        if getattr(self, "_test_ghosts", False):          # the test's blue trees go at the next edit
            self._test_ghosts = False
            if not self.b_compare.isChecked():
                self.view.set_ghosts([], None)
        self._undo.append(json.dumps(self.scene.to_json()))
        del self._undo[:-200]
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return
        self._redo.append(json.dumps(self.scene.to_json()))
        self.scene = tc.Scene.from_json(json.loads(self._undo.pop()))
        self.sel = min(self.sel, len(self.scene.trees) - 1)
        self._refresh_all()

    def redo(self):
        if not self._redo:
            return
        self._undo.append(json.dumps(self.scene.to_json()))
        self.scene = tc.Scene.from_json(json.loads(self._redo.pop()))
        self.sel = min(self.sel, len(self.scene.trees) - 1)
        self._refresh_all()

    def _tree(self):
        return self.scene.trees[self.sel] if 0 <= self.sel < len(self.scene.trees) else None

    def _eff(self):
        """the scene the crack will use (shared leaves → unknown when ticked)"""
        if getattr(self, "shared", None) is not None and self.shared.isChecked():
            return tc.safe_scene(self.scene)
        return self.scene

    def _tool_add(self, on):
        if self.add_btn.isChecked() != bool(on):
            self.add_btn.setChecked(bool(on))
            return
        self.view.set_tool("add" if on else "select")
        self.view.update()

    def _ground_clicked(self, x, z):
        if any(t.x == x and t.z == z for t in self.scene.trees):
            return
        self._snap()
        prev = self._tree()
        bio = self.scene.biome((x >> 4, z >> 4))
        typ = prev.type if prev is not None and prev.type != "unknown" else "oak"
        if typ not in tc.BIOME_TYPES[bio]:
            typ = tc.BIOME_TYPES[bio][0]
        self.scene.trees.append(tc.Tree(x, z, typ, None, "?" * 12))
        self.sel = len(self.scene.trees) - 1
        self._refresh_all()

    def _select(self, i):
        self.sel = i
        if i >= 0:
            self.tabs.set_value("Tree"); self.stack.setCurrentIndex(0)
        self._refresh_all(scene_changed=False)

    def _list_select(self, row):
        if row != self.sel and not getattr(self, "_filling", False):
            self._select(row)
            t = self._tree()
            if t is not None:
                self.view.follow(t.x, t.z)

    def _corner_clicked(self, ti, li, ci):
        if ti != self.sel:
            self.sel = ti
        t = self._tree()
        if t is None:
            return
        self._snap()
        t.set_leaf(li, ci, STATE_NEXT.get(t.leaf(li, ci), "1"))
        self._refresh_all()

    def _leaf_changed(self, li, ci, new):
        t = self._tree()
        if t is None:
            return
        self._snap()
        t.set_leaf(li, ci, new)
        self._refresh_all()

    def _set_all_leaves(self, v):
        t = self._tree()
        if t is None:
            return
        self._snap()
        t.leaves = v * 12
        self._refresh_all()

    def _set_type(self, label):
        t = self._tree()
        if t is None:
            return
        typ = {v: k for k, v in TYPE_LABEL.items()}[label]
        if typ == t.type:
            return
        self._snap()
        t.type = typ
        if typ in tc.HEIGHTS and typ != "fancy" and t.height is not None:
            lo, hi = tc.HEIGHTS[typ]
            if not lo <= t.height <= hi:
                t.height = None
        elif typ == "unknown":
            t.height = None
        keys = {k for k, _l, _r in (tc.SPRUCE_ATTRS if typ == "spruce" else tc.PINE_ATTRS if typ == "pine" else ())}
        t.attrs = {k: v for k, v in (t.attrs or {}).items() if k in keys}
        self._refresh_all()

    def _set_attr(self, key, val):
        t = self._tree()
        if t is None or getattr(self, "_filling", False):
            return
        v = None if val in ("?", "") else int(val)
        if (t.attrs or {}).get(key) == v:
            return
        self._snap()
        a = dict(t.attrs or {})
        if v is None:
            a.pop(key, None)
        else:
            a[key] = v
        t.attrs = a
        self._refresh_all()

    def _set_height(self, v):
        t = self._tree()
        if t is None or getattr(self, "_filling", False):
            return
        h = None if v in ("?", "") else int(v)
        if h == t.height:
            return
        self._snap()
        t.height = h
        self._refresh_all()

    def _paste_pos(self, xz):
        self.px.setValue(xz[0]); self.pz.setValue(xz[1])
        self._set_pos()

    def _set_pos(self, *_):
        t = self._tree()
        if t is None or getattr(self, "_filling", False):
            return
        x, z = self.px.value(), self.pz.value()
        if (x, z) == (t.x, t.z):
            return
        if any(o is not t and o.x == x and o.z == z for o in self.scene.trees):
            self._fill_inspector()
            return
        self._snap()
        t.x, t.z = x, z
        self._refresh_all()
        self.view.follow(t.x, t.z)          # (06/11) the view goes to the tree

    def _move_selected(self, dx, dz):
        t = self._tree()
        if t is None:
            return
        if any(o is not t and o.x == t.x + dx and o.z == t.z + dz for o in self.scene.trees):
            return
        self._snap()
        t.x += dx; t.z += dz
        self._refresh_all()
        self.view.follow(t.x, t.z)

    def _delete_selected(self):
        if self._tree() is None:
            return
        self._snap()
        del self.scene.trees[self.sel]
        self.sel = min(self.sel, len(self.scene.trees) - 1)
        self._refresh_all()

    def _version_changed(self, *_):
        k = self.version.currentData()
        if k and k != self.scene.version:
            self._snap()
            self.scene.version = k
            self._refresh_all()

    def _biome_changed(self, ch, bio):
        if self.scene.biome(ch) == bio:
            return
        self._snap()
        self.scene.biomes[tuple(ch)] = bio
        allowed = tc.BIOME_TYPES[bio]
        for t in self.scene.trees:
            if t.chunk == tuple(ch) and t.type != "unknown" and t.type not in allowed:
                t.type = allowed[0]
                t.attrs = {}
                if t.height is not None and not (tc.HEIGHTS[t.type][0] <= t.height <= tc.HEIGHTS[t.type][1]):
                    t.height = None
        self._refresh_all()

    # ── refresh ──────────────────────────────────────────────────────────────
    def _refresh_all(self, reframe=False, scene_changed=True):
        self._filling = True
        try:
            i = self.version.findData(self.scene.version)
            if i >= 0 and i != self.version.currentIndex():
                self.version.setCurrentIndex(i)
            self._fill_chunks()
            self._fill_list()
            self._fill_inspector()
        finally:
            self._filling = False
        self.view.set_scene(self.scene, self.sel, reframe=reframe)
        self._refresh_run()
        if scene_changed:
            self._save_timer.start()

    def _fill_chunks(self):
        while self.chunk_box.count():
            it = self.chunk_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        eff = self._eff()
        chunks = eff.chunks()
        best = tc.best_chunk(eff)
        for ch in sorted(chunks, key=lambda c: -tc.chunk_bits(chunks[c], eff.biome(c))):
            card = ChunkCard(ch)
            card.update_from(eff, best is not None and tuple(best[0]) == tuple(ch))
            card.clicked.connect(self._focus_chunk)
            card.biomeChanged.connect(self._biome_changed)
            self.chunk_box.addWidget(card)
        n = len(chunks)
        self.chunk_hint.setText(
            "" if not n else
            "Trees of one chunk are cracked together. A second chunk with a few trees "
            "removes the false seeds." if n == 1 else
            f"{n} chunks: the crack starts from the main one, the others confirm the seeds.")
        self.chunk_hint.setVisible(bool(n))
        self.chunks_title.setVisible(bool(n))

    def _fill_list(self):
        self.tree_list.blockSignals(True)
        self.tree_list.clear()
        eff = self._eff()
        for i, t in enumerate(self.scene.trees):
            bio = self.scene.biome(t.chunk)
            et = eff.trees[i]
            known = sum(1 for c in t.leaves if c in "01") if t.has_leaves() else None
            h = "?" if t.height is None else str(t.height)
            parts = [f"{TYPE_LABEL[t.type]}  {t.x}, {t.z}"]
            if t.type != "unknown":
                parts.append(f"h{h}")
            if known is not None:
                parts.append(f"{known}/12")
            parts.append(f"{tc.tree_bits(et, bio):.0f} b")
            it = QListWidgetItem(" · ".join(parts))
            it.setIcon(_dot(TYPE_COL[t.type]))
            it.setToolTip(f"Chunk {t.chunk[0]}, {t.chunk[1]} · trunk height {h} · "
                          + (f"{known} of the 12 corner leaves known · " if known is not None else "")
                          + f"{tc.tree_bits(et, bio):.1f} bits of information")
            self.tree_list.addItem(it)
        self.tree_list.setCurrentRow(self.sel)
        self.tree_list.blockSignals(False)
        self.empty_hint.setVisible(not self.scene.trees)

    def _fill_inspector(self):
        t = self._tree()
        self.insp.setVisible(t is not None)
        self.insp_empty.setVisible(t is None)
        if t is None:
            return
        self._filling = True
        try:
            bio = self.scene.biome(t.chunk)
            self.insp_title.setText(f"Tree {self.sel + 1} · {TYPE_LABEL[t.type]}")
            et = self._eff().trees[self.sel]
            b = tc.tree_bits(et, bio)
            self.insp_bits.setText(f"<b style='color:{theme.TEXT}'>{b:.1f}</b> bits")
            seg = self.type_seg_taiga if bio == "taiga" else self.type_seg
            self.type_stack.setCurrentWidget(seg)
            seg.set_value(TYPE_LABEL[t.type])
            for k, btn in self.type_seg.buttons.items():
                typ = {v: kk for kk, v in TYPE_LABEL.items()}[k]
                btn.setEnabled(not (bio == "birch_forest" and typ in ("oak", "fancy")))
            self.px.setValue(t.x); self.pz.setValue(t.z)
            self.pos_info.setText(f"chunk {t.x >> 4}, {t.z >> 4}  ·  in the chunk {t.x & 15}, {t.z & 15}")
            if t.type in ("oak", "birch", "spruce", "pine"):
                s = self.h_seg[t.type]
                s.set_value("?" if t.height is None else str(t.height))
                self.h_stack.setCurrentWidget(s)
            elif t.type == "fancy" and self.scene.version in ("1.14.4", "1.15.2"):
                self.h_stack.setCurrentWidget(self.h_old)
            elif t.type == "fancy":
                self.h_fancy.setValue(3 if t.height is None else int(t.height))
                self.h_stack.setCurrentWidget(self.h_fancy)
            else:
                self.h_stack.setCurrentWidget(self.h_none)
            self.conifer.setVisible(t.type in ("spruce", "pine"))
            for (typ, key), (row, sg) in self.attr_rows.items():
                row.setVisible(typ == t.type)
                if typ == t.type:
                    v_ = (t.attrs or {}).get(key)
                    sg.set_value("?" if v_ is None else str(v_))
            has = t.has_leaves()
            self.leaves_box.setVisible(has)
            risky = tc.risky_corners(self.scene, t)
            for lw in self.layers:
                lw.set_states(t.leaves[lw.layer * 4:lw.layer * 4 + 4], t.type, has,
                              [ci for li, ci in risky if li == lw.layer])
                lw.setVisible(True)
            self.leaf_title.setText(("CORNER LEAVES" if has else
                                     "CORNER LEAVES — oak and birch only"))
        finally:
            self._filling = False

    def _pillar_text(self):
        p = self.scene.pillars or {}
        if not tc.pillars_used(self.scene):
            return "Not used"
        n = sum(1 for x in (p.get("sizes") or []) if x is not None)
        c = sum(1 for x in (p.get("cages") or []) if x is not None)
        ps = tc.pillar_set(self.scene)
        if not ps:
            return f"<span style='color:{theme.DANGER}'>No layout fits</span>"
        b = tc.pillar_bits(self.scene)
        return (f"{n} height{'s' * (n != 1)}, {c} cage{'s' * (c != 1)}"
                f"{' · turned' if p.get('rot') else ''}  ·  <b style='color:{bits_color(b + 40)}'>{b:.1f} bits</b>")

    def _struct_text(self):
        obs = self.scene.structures
        if not obs:
            return "Not used"
        import struct_crack as stc
        mc = tc.VERSIONS[self.scene.version][3]
        if any(p[0] == "error" for p in stc.problems(obs, mc)):
            return f"<span style='color:{theme.DANGER}'>{len(obs)} — a problem, see Edit</span>"
        b = stc.effective_bits(obs, mc)
        return (f"{len(obs)} structure{'s' * (len(obs) != 1)}  ·  "
                f"<b style='color:{bits_color(b + 8)}'>{b:.0f} bits</b>")

    def _edit_structures(self):
        from .structures_dialog import StructuresDialog
        mc = tc.VERSIONS[self.scene.version][3]
        d = StructuresDialog(self.scene.structures, mc, tc.VERSIONS[self.scene.version][0], self)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        self._snap()
        self.scene.structures = d.value()
        self._refresh_all(scene_changed=True)
        if self._result is not None and self._running is None and self._result.all_seeds:
            tc.finish(self._result, self._finish_scene())
            self._show_result(self._result)

    def _finish_scene(self):
        return tc.safe_scene(self.scene) if self.shared.isChecked() else self.scene

    def _edit_pillars(self):
        from .end_pillars_dialog import EndPillarsDialog
        d = EndPillarsDialog(self.scene.pillars, self)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        self._snap()
        val = d.value()
        self.scene.pillars = val if (any(x is not None for x in val["sizes"]) or
                                     any(x is not None for x in val["cages"])) else {}
        self._refresh_all(scene_changed=True)
        if self._result is not None and self._running is None and self._result.all_seeds:
            tc.finish(self._result, self._finish_scene())
            self._show_result(self._result)

    def _dev(self):
        return "cpu" if self.device.value() == "CPU" else "auto"

    def _refresh_run(self):
        if hasattr(self, "pillar_info"):
            self.pillar_info.setText(self._pillar_text())
        if hasattr(self, "struct_info"):
            self.struct_info.setText(self._struct_text())
        trees_ver = self.scene.version in tc.TREE_VERSIONS
        if hasattr(self, "add_btn"):
            from .widgets import explain_disabled as _xd
            _xd(self.add_btn, trees_ver or bool(self.scene.trees),
                "From Java 1.18 on, trees can't crack the seed (their random uses the whole "
                "64-bit world seed): use structures and the End pillars.")
            self.empty_hint.setText(
                "No tree yet. Click “Add tree”, then the ground where a trunk stands — or import "
                "a TreeCracker input." if trees_ver else
                "Java 1.18+: trees don't help — enter Structures (and the End pillars) above.")
        best = tc.best_chunk(self._eff()) if trees_ver else None
        probs = tc.validate(self.scene)
        errs = [p for p in probs if p[0] == "error"]
        if self.scene.structures:
            mode = None if errs else tc.crack_mode(
                self.scene, "cpu" if self.device.value() == "CPU" else "auto", self.threads.value())
            if mode == "structures" or (mode is None and (best is None or best[1] < tc.BITS_MINIMUM - 8)):
                self._refresh_run_structures(errs)
                return
        if best is None:
            self.info_title.setText("Rebuild the trees you saw" if trees_ver else "Enter the structures you saw")
            self.info_sub.setText("Place trees in 3D, then set their type, height and leaves"
                                  if trees_ver else "Java 1.18+: structures and End pillars")
            self.info_sub.setToolTip("Place trees in the 3D view, then enter their type, height and "
                                     "corner leaves — or enter structures." if trees_ver else
                                     "Java 1.18+: villages, temples, shipwrecks… (Structures → Edit), "
                                     "plus the End pillars.")
            self.meter.set_bits(0)
            ok = False
        else:
            ch, b = best
            self.meter.set_bits(b)
            pb = tc.pillar_bits(self.scene)
            col = bits_color(b)
            if errs:
                self.info_title.setText(f"<span style='color:{theme.DANGER}'>{errs[0][1]}</span>")
                self.info_sub.setText("Fix this before cracking.")
            elif pb > 0:
                tot = b + pb
                self.info_title.setText(f"Main chunk {ch[0]}, {ch[1]}  ·  "
                                        f"<span style='color:{col}'>{b:.0f} bits</span>"
                                        f"  + {pb:.0f} End pillars = "
                                        f"<span style='color:{bits_color(tot)}'>{tot:.0f}</span>")
                if b < tc.BITS_MINIMUM - 8:
                    self.info_sub.setText(f"The trees alone need at least {tc.BITS_MINIMUM - 8:.0f} bits "
                                          f"for the search to run: add trees, heights or corner leaves.")
                elif tot < tc.BITS_NEEDED:
                    self.info_sub.setText("Enough to try: the pillars sort out most false seeds; "
                                          "a second chunk removes the rest.")
                else:
                    self.info_sub.setText("Enough information: the End pillars sort out the false "
                                          "seeds the trees leave.")
            else:
                self.info_title.setText(f"Main chunk {ch[0]}, {ch[1]}  ·  "
                                        f"<span style='color:{col}'>{b:.0f} bits</span>")
                if b < tc.BITS_MINIMUM:
                    self.info_sub.setText(f"Needs at least {tc.BITS_MINIMUM:.0f} bits (~{tc.BITS_NEEDED:.0f} to "
                                          f"be safe): add trees, heights or corner leaves.")
                elif b < tc.BITS_NEEDED:
                    self.info_sub.setText("Enough to try, with some false seeds: a second chunk "
                                          "(a few trees) removes them.")
                elif len(self.scene.chunks()) < 2:
                    self.info_sub.setText("Enough information. One chunk can still give a few seeds: "
                                          "a few trees of a second chunk single out the right one.")
                else:
                    self.info_sub.setText("Enough information, and other chunks to confirm the seeds.")
            ok = not errs and b >= tc.BITS_MINIMUM - 8
        if ok and self.scene.structures and tc.structures_ready(self.scene, self.threads.value(), self._dev()):
            import struct_crack as stc
            sb = stc.effective_bits(self.scene.structures, tc.VERSIONS[self.scene.version][3])
            self.info_sub.setText(self.info_sub.text() + f"  ·  structures +{sb:.0f} bits")
        self._refresh_tail(ok, best is not None)

    def _refresh_run_structures(self, errs):
        import struct_crack as stc
        mc = tc.VERSIONS[self.scene.version][3]
        obs = self.scene.structures
        b = stc.effective_bits(obs, mc)
        pb = tc.pillar_bits(self.scene)
        self.meter.set_bits(b + pb)
        if errs:
            self.info_title.setText(f"<span style='color:{theme.DANGER}'>{errs[0][1]}</span>")
            self.info_sub.setText("Fix this before cracking.")
            ok = False
        else:
            tot = b + pb
            self.info_title.setText(f"Structures  ·  <span style='color:{bits_color(b)}'>{b:.0f} bits</span>"
                                    + (f"  + {pb:.0f} End pillars = <span style='color:{bits_color(tot)}'>"
                                       f"{tot:.0f}</span>" if pb else ""))
            ready = tc.structures_ready(self.scene, self.threads.value(), self._dev())
            if not ready and b >= tc.STRUCT_MIN_BITS:
                self.info_sub.setText("The search would take too long: add temples, villages (up to "
                                      "1.17), shipwrecks or outposts — they fix the low bits first.")
            elif not ready:
                self.info_sub.setText(f"Needs at least {tc.STRUCT_MIN_BITS:.0f} bits: add structures "
                                      "(~9 bits each).")
            elif tot < tc.BITS_MINIMUM:
                self.info_sub.setText(f"Enough to search: about {2 ** (48 - tot):,.0f} seeds will fit — "
                                      "add structures or End pillars to sort them out.")
            else:
                self.info_sub.setText("Enough information: the structures single out the structure seed.")
            ok = ready
        self._refresh_tail(ok, True)

    def _refresh_tail(self, ok, has_info):
        running = self._running is not None
        gpu_ok = self._gpu_state[0] if self._gpu_state else None
        self.device.buttons["GPU"].setEnabled(gpu_ok is not False)
        if gpu_ok is False and self.device.value() == "GPU":
            self.device.set_value("CPU")
        self.device.buttons["GPU"].setToolTip(
            (self._gpu_state[1] if self._gpu_state else "Checking the GPU…"))
        cpu = self.device.value() == "CPU"
        self.threads.setEnabled(cpu and not running)
        if has_info:
            self.est.setText("≈ " + tc.estimate(self.scene, "cpu" if cpu else "auto",
                                               self.threads.value()))
        else:
            self.est.setText("")
        if running:
            self.run_btn.setText("  Stop"); icons.bind(self.run_btn, "stop", 16, "text", "text")
            self.run_btn.setProperty("kind", "danger")
            self.run_btn.setEnabled(True)
        else:
            self.run_btn.setText("  Crack"); icons.bind(self.run_btn, "play", 16, "text", "text")
            self.run_btn.setProperty("kind", "primary")
            self.run_btn.setEnabled(ok)
        self.run_btn.style().unpolish(self.run_btn); self.run_btn.style().polish(self.run_btn)
        self.version.setEnabled(not running)
        if hasattr(self, "b_sisters"):
            self._sync_result_buttons()

    def _isolate(self, on):
        self.view.isolate = bool(on)
        self.view.set_selected(self.sel)

    def _focus_chunk(self, ch):
        trees = [t for t in self.scene.trees if t.chunk == tuple(ch)]
        if trees:
            self.sel = self.scene.trees.index(trees[0])
        self.view.focus_chunk = tuple(ch)
        self._refresh_all(scene_changed=False)
        from PySide6.QtGui import QVector3D
        self.view.target = QVector3D(ch[0] * 16 + 8, 67, ch[1] * 16 + 8)
        self.view.dist = 34
        self.view.update()

    # ── run ──────────────────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        if self._gpu_state is None:
            self._gpu_state = (None, "Checking the GPU…")

            def probe():
                try:
                    g, msg = tc.gpu()
                    st = (g is not None, msg)
                    if g is not None:            # (06/11) the structure kernel too
                        import struct_crack as stc
                        sg, smsg = stc.gpu()
                        if sg is None:
                            st = (True, msg + "\nStructures: " + smsg)
                except Exception as ex:
                    st = (False, str(ex))
                self._gpu_probe.done.emit(st)
            self._gpu_probe = _Worker()
            self._gpu_probe.done.connect(self._gpu_done)
            threading.Thread(target=probe, daemon=True, name="ssa-tree-gpu").start()

    def _gpu_done(self, st):
        self._gpu_state = st
        self._refresh_run()

    def _run_clicked(self):
        if self._running:
            self._stop.set()
            self.run_btn.setEnabled(False)
            return
        mode = tc.crack_mode(self.scene, "cpu" if self.device.value() == "CPU" else "auto",
                             self.threads.value())
        if mode is None:
            return
        best = tc.best_chunk(self._eff())
        extra = tc.pillar_bits(self.scene)
        if mode == "trees" and best[1] + extra < tc.BITS_MINIMUM:
            r = QMessageBox.question(self, "Not enough information",
                                     f"The main chunk gives {best[1]:.0f} bits, under the "
                                     f"{tc.BITS_MINIMUM:.0f} a structure seed needs: the crack would "
                                     "return many false seeds. Crack anyway?")
            if r != QMessageBox.StandardButton.Yes:
                return
        self._start_crack()

    def _start_crack(self):
        self._stop.clear()
        self._running = "crack"
        self._seeds = []
        self.res_list.clear(); self.ws_list.clear()
        self.b_compare.setChecked(False)
        self.res_summary.setText("Cracking…")
        self.tabs.set_value("Results"); self.stack.setCurrentIndex(1)
        self.prog.setValue(0); self.prog.show()
        self._refresh_run()
        w = self._worker = _Worker()
        w.progress.connect(self._crack_progress)
        w.seed.connect(self._crack_seed)
        w.done.connect(self._crack_done)
        w.failed.connect(self._crack_failed)
        scene = tc.Scene.from_json(self.scene.to_json())
        dev = "cpu" if self.device.value() == "CPU" else "auto"
        thr = self.threads.value()
        shared = self.shared.isChecked()

        def work():
            try:
                res = tc.crack(scene, device=dev, threads=thr, stop=self._stop, shared_unknown=shared,
                               on_progress=lambda f, t: w.progress.emit(f, t),
                               on_seed=lambda s: w.seed.emit(s))
                w.done.emit(res)
            except Exception as ex:
                w.failed.emit(str(ex))
        self._thread = threading.Thread(target=work, daemon=True, name="ssa-tree-crack")
        self._thread.start()

    def _crack_progress(self, f, text):
        self.prog.setValue(int(f * 1000))
        self.est.setText(text.split(" · ", 1)[-1] if " · " in text else text)
        self.res_summary.setText(text)

    def _crack_seed(self, s):
        it = QListWidgetItem(f"{s}")
        it.setData(Qt.ItemDataRole.UserRole, int(s))
        it.setForeground(QColor(theme.TEXT3))
        it.setToolTip("Found in the main chunk — the other chunks are checked at the end.")
        self.res_list.addItem(it)

    def _crack_done(self, res):
        self._running = None
        self.prog.hide()
        self._result = res
        self._show_result(res)
        self._refresh_run()
        self._sync_result_buttons()
        self.status.emit(f"Seedcracking: {len(res.structure_seeds)} structure seed(s)")

    def _show_result(self, res):
        self.res_list.clear()
        ss = res.structure_seeds
        self._seeds = ss
        costs = getattr(res, "costs", {}) or {}
        rnds = getattr(res, "randoms", {}) or {}
        low = min(costs.values()) if costs else None
        n_low = sum(1 for c in costs.values() if c == low)
        for s in ss:
            c = costs.get(s)
            tag = ""
            if s in rnds:
                tag = "◆ random seed"
            elif c is not None and len(ss) > 1 and c == low and n_low == 1 and not rnds:
                tag = "✓ best fit"
            it = QListWidgetItem(f"{s:<17} {tag}")
            it.setData(Qt.ItemDataRole.UserRole, int(s))
            if s in rnds or (c is not None and c == low and n_low == 1 and len(ss) > 1 and not rnds):
                it.setForeground(QColor(theme.OK))
            it.setToolTip("Structure seed (the 48 low bits of the world seed). “World seeds…” "
                          "finds its world seeds."
                          + (f"\n◆ A blank seed field (random seed) gives {', '.join(map(str, rnds[s]))}, "
                             "with the entered biomes: most likely the world's seed if it wasn't typed in."
                             if s in rnds else "")
                          + ("\n✓ Best fit: the only seed whose trees all match what you entered "
                             "(no tree it grows in the open is missing)." if tag == "✓ best fit" else ""))
            self.res_list.addItem(it)
        parts = [f"<b>{len(ss)}</b> structure seed{'s' * (len(ss) != 1)}"]
        if getattr(res, "mode", "trees") == "structures":
            parts.append(f"from the structures ({res.candidates} fit)")
            if res.checked:
                parts.append(f"{len(res.checked)} tree chunk{'s' * (len(res.checked) != 1)} checked")
        elif res.checked:
            parts.append(f"{len(res.checked)} other chunk{'s' * (len(res.checked) != 1)} checked")
        if getattr(res, "struct_removed", 0):
            parts.append(f"{res.struct_removed} removed by the structures")
        if getattr(res, "pillar_removed", 0):
            parts.append(f"{res.pillar_removed} removed by the End pillars")
        parts.append(f"{tc._fmt_t(res.seconds)} on {res.device}")
        txt = "  ·  ".join(parts)
        if res.stopped:
            txt = "Stopped — partial results.  " + txt
        if not ss and not res.stopped:
            txt += ("<br><span style='color:%s'>No seed: an entered detail is probably wrong "
                    "(%s, the version%s).</span>"
                    % (theme.WARN,
                       "a structure's coordinates" if getattr(res, "mode", "") == "structures" else
                       "a corner leaf, a height, the biome",
                       ", a pillar" if getattr(res, "pillar_removed", 0) else ""))
        elif len(ss) > 1:
            txt += ("<br>Several seeds: add structures or the End pillars, or run World seeds… on "
                    "them (biomes)." if getattr(res, "mode", "") == "structures" else
                    "<br>Several seeds: add trees of another chunk, or compare their trees in game.")
            if rnds:
                txt += (" <b>◆</b> = a world made with a blank seed field gives this one: "
                        "the likely seed (first in the list).")
        self.res_summary.setText(txt)
        if ss:
            self.res_list.setCurrentRow(0)

    def _crack_failed(self, msg):
        self._running = None
        self.prog.hide()
        self.res_summary.setText(f"<span style='color:{theme.DANGER}'>Failed: {msg}</span>")
        self._refresh_run()

    # ── results ──────────────────────────────────────────────────────────────
    def _cur_seed(self):
        it = self.res_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _sync_result_buttons(self):
        has = self._cur_seed() is not None and self._running is None
        for b in (self.b_compare, self.b_world):
            b.setEnabled(has)
        n = self.res_list.count()
        self.b_sisters.setEnabled(self._running is None and (has or n > 0))
        self.b_sisters.setText("  Sister seeds" if has or n <= 1 else f"  Sister seeds · all {n}")
        self.b_sisters.setToolTip(
            "Open Sessions in “Sister seeds” mode on this structure seed, to search its world "
            "seeds with your own conditions." if has else
            "No seed selected: open Sessions in “Sister seeds” mode with every result as the "
            "seed list (each family searched once). Click a seed to send only that one.")
        self.b_copy.setEnabled(self.res_list.count() > 0)
        self.b_ws_copy.setEnabled(self.ws_list.count() > 0)
        from .widgets import explain_disabled as _xd
        old_v = not self._version_name()           # (06/11) 1.14 / 1.15: not in SSA's lists
        _xd(self.b_ws_open, not old_v and self.ws_list.count() > 0,
            "SSA's map starts at Java 1.16." if old_v else "")
        for sp in (self.cmp_x, self.cmp_z):
            sp.setEnabled(has)
        if old_v:
            _xd(self.b_sisters, False, "SSA's sessions and map start at Java 1.16.")
        if self.scene.version not in tc.TREE_VERSIONS:
            _xd(self.b_compare, False, "Tree previews: Java 1.14 – 1.17 only.")

    def _seed_selected(self, *_):
        self._sync_result_buttons()
        if not self.b_compare.isChecked():
            best = tc.best_chunk(self.scene)
            others = [c for c in self.scene.chunks() if best and tuple(c) != tuple(best[0])]
            ch = others[0] if others else (best[0] if best else (0, 0))
            self.cmp_x.blockSignals(True); self.cmp_z.blockSignals(True)
            self.cmp_x.setValue(ch[0]); self.cmp_z.setValue(ch[1])
            self.cmp_x.blockSignals(False); self.cmp_z.blockSignals(False)
        self._compare(self.b_compare.isChecked())

    def _compare(self, on):
        s = self._cur_seed()
        if not on or s is None:
            self.view.set_ghosts([], None)
            self.cmp_info.setText("")
            return
        ch = (self.cmp_x.value(), self.cmp_z.value())
        try:
            trees = tc.predicted_trees(s, self.scene, ch)
        except Exception as e:
            self.cmp_info.setText(str(e))
            return
        self.view.set_ghosts(trees, ch)
        mine = {(t.x, t.z): t for t in self.scene.trees if t.chunk == ch}
        hit = sum(1 for t in trees if (t.x, t.z) in mine)
        lines = [f"{TYPE_LABEL[t.type]} {t.x} {t.z} h{t.height}" for t in trees]
        self.cmp_info.setText(
            f"{len(trees)} trees the seed would grow in chunk {ch[0]}, {ch[1]} (the ones blocked by "
            f"others don't grow){f' · {hit} where you placed one' if mine else ''}.\n"
            + " · ".join(lines))
        self.view.focus_chunk = ch
        from PySide6.QtGui import QVector3D
        self.view.target = QVector3D(ch[0] * 16 + 8, 67, ch[1] * 16 + 8)
        self.view.update()

    def _copy_seeds(self):
        s = self._cur_seed()
        txt = str(s) if s is not None else "\n".join(str(x) for x in self._seeds)
        QGuiApplication.clipboard().setText(txt)
        self.status.emit("Copied")

    def _version_name(self):
        mc = tc.VERSIONS[self.scene.version][3]
        import cubiomes_wrap as cw
        for k, v in cw.MC_VERSIONS.items():
            if v == mc:
                return k
        return ""

    def _to_sisters(self):
        s = self._cur_seed()
        if s is not None:
            self.sendSisters.emit(int(s), self._version_name())
        elif self._seeds:
            self.sendSisters.emit([int(x) for x in self._seeds], self._version_name())

    def _world_seeds(self):
        s = self._cur_seed()
        if s is None or self._running:
            return
        self._stop.clear()
        self._running = "world"
        self.ws_list.clear()
        self.prog.setValue(0); self.prog.show()
        self._refresh_run()
        self._sync_result_buttons()
        w = self._wworker = _Worker()
        w.progress.connect(lambda f, t: (self.prog.setValue(int(f * 1000)), self.est.setText(t)))
        w.done.connect(self._world_done)
        w.failed.connect(self._crack_failed)
        scene = tc.Scene.from_json(self.scene.to_json())

        def work():
            try:
                out = tc.world_seeds(s, scene, stop=self._stop,
                                     on_progress=lambda f: w.progress.emit(f, f"world seeds {f * 100:.0f} %"))
                w.done.emit((s, out))
            except Exception as ex:
                w.failed.emit(str(ex))
        threading.Thread(target=work, daemon=True, name="ssa-tree-world").start()

    def _world_done(self, payload):
        s, out = payload
        self._running = None
        self.prog.hide()
        self.ws_list.clear()
        for seed, rnd in out:
            it = QListWidgetItem(f"{seed}" + ("   ◆ random" if rnd else ""))
            it.setData(Qt.ItemDataRole.UserRole, int(seed))
            if rnd:
                it.setForeground(QColor(theme.OK))
                it.setToolTip("A blank seed field can give this seed: most likely the world's seed "
                              "if it was not typed in.")
            self.ws_list.addItem(it)
        self._ws_filter(self.ws_find.text())
        if not out:
            it = QListWidgetItem("No world seed has the entered biome(s).")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.ws_list.addItem(it)
        self.est.setText(f"{len(out)} world seed{'s' * (len(out) != 1)}")
        self.ws_title.setText(f"WORLD SEEDS · {len(out)}" + (f" · {sum(1 for _s, r in out if r)} random" if out else ""))
        self._refresh_run()
        self._sync_result_buttons()

    def _ws_filter(self, text):
        t = text.strip()
        for i in range(self.ws_list.count()):
            it = self.ws_list.item(i)
            it.setHidden(bool(t) and t not in it.text())

    def _copy_world(self):
        seeds = [self.ws_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.ws_list.count())]
        QGuiApplication.clipboard().setText("\n".join(str(s) for s in seeds if s is not None))

    def _open_world(self, seed):
        if seed is None and self.ws_list.count():
            seed = self.ws_list.item(0).data(Qt.ItemDataRole.UserRole)
        if seed is None:
            return
        best = tc.best_chunk(self.scene)
        if best is None and self.scene.structures:
            o = self.scene.structures[0]
            self.openOnMap.emit(int(seed), self._version_name(), o.x, o.z)
            return
        ch = best[0] if best else (0, 0)
        self.openOnMap.emit(int(seed), self._version_name(), ch[0] * 16 + 8, ch[1] * 16 + 8)

    # ── files ────────────────────────────────────────────────────────────────
    def _new(self):
        if self.scene.trees:
            r = QMessageBox.question(self, "New scene", "Clear every tree of the current scene?")
            if r != QMessageBox.StandardButton.Yes:
                return
        self._snap()
        self.scene = tc.Scene(self.scene.version)
        self.sel = -1
        self._refresh_all(reframe=True)

    def _open(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open trees", user_path(),
                                           "SSA trees (*.ssatrees *.json);;All files (*.*)")
        if not p:
            return
        remember_path(p)
        try:
            sc = tc.Scene.load(p)
        except Exception as e:
            QMessageBox.warning(self, "Open", f"Cannot read this file: {e}")
            return
        self._snap()
        self.scene = sc
        self.sel = 0 if sc.trees else -1
        self._refresh_all(reframe=True)

    def _save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save trees", user_path("trees.ssatrees"),
                                           "SSA trees (*.ssatrees)")
        if not p:
            return
        remember_path(p)
        try:
            self.scene.save(p)
            self.status.emit(f"Saved {os.path.basename(p)}")
        except Exception as e:
            QMessageBox.warning(self, "Save", str(e))

    def _import_tc(self):
        d = QDialog(self); d.setWindowTitle("Import TreeCracker input")
        v = QVBoxLayout(d)
        v.addWidget(body("Paste the input of Andrew's TreeCracker (TreeChunkBuilder … .tree_oak(x, z, "
                         "IntRange(h), \"0?1?\"\"…\"\"…\") …) — several chunks may be pasted — or a "
                         "scene copied with “Copy the scene”.", muted=True))
        ed = QPlainTextEdit(); ed.setFont(theme.mono_font(9)); ed.setMinimumSize(560, 260)
        v.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        v.addWidget(bb)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            txt = ed.toPlainText().strip()
            if txt.startswith("{"):                    # a scene copied with "Copy the scene"
                sc, warn = tc.Scene.from_json(json.loads(txt)), []
            else:
                sc, warn = parse_treecracker(txt)
        except (ValueError, KeyError, TypeError) as e:
            QMessageBox.warning(self, "Import", str(e))
            return
        self._snap()
        self.scene = sc
        self.sel = 0 if sc.trees else -1
        self._refresh_all(reframe=True)
        if warn:
            QMessageBox.information(self, "Import", "\n".join(warn))

    def _copy_scene(self):
        QGuiApplication.clipboard().setText(json.dumps(self.scene.to_json(), separators=(",", ":")))
        self.status.emit("Scene copied: paste it anywhere (or in Import)")

    def _test_seed(self):
        """(06/11) Practice on a test world: what the entered trees get wrong for a
        known seed — the position, type, height and corners that don't match."""
        trees = bool(self.scene.trees) and self.scene.version in tc.TREE_VERSIONS
        if not trees and not self.scene.structures:
            return
        from PySide6.QtWidgets import QInputDialog
        txt, ok = QInputDialog.getText(self, "Test with a known seed",
                                       "World seed of the test world (the trees and structures "
                                       "must have been entered for this version):")
        if not ok or not txt.strip():
            return
        try:
            from .world import parse_seed
            seed = parse_seed(txt.strip())
        except Exception:
            seed = None
        if seed is None:
            QMessageBox.warning(self, "Test", "Not a seed.")
            return
        s_html = self._test_structures(seed)
        if not trees:
            m = QMessageBox(self)
            m.setWindowTitle("Test with a known seed")
            m.setTextFormat(Qt.TextFormat.RichText)
            m.setText(s_html)
            m.exec()
            return
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            d = tc.diagnose(self.scene, seed)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Test", str(e))
            return
        QApplication.restoreOverrideCursor()
        real = [t for trees in d["chunks"].values() for t in trees]
        self.view.set_ghosts(real, None)
        self._test_ghosts = True
        bad = [(i, p) for i, p in d["trees"] if p]
        if not bad:
            html = (f"<b style='color:{theme.OK}'>Every entered tree matches seed {seed}.</b><br><br>"
                    "If the crack still finds nothing, check the version and the biome of each chunk.")
        else:
            rows = []
            for i, p in bad:
                t = self.scene.trees[i]
                rows.append(f"<b>Tree {i + 1} · {TYPE_LABEL[t.type]} {t.x}, {t.z}</b><ul>"
                            + "".join(f"<li>{x}</li>" for x in p) + "</ul>")
            html = (f"<b style='color:{theme.WARN}'>{len(bad)} tree{'s' * (len(bad) != 1)} don't match "
                    f"seed {seed}:</b><br>" + "".join(rows)
                    + "<br>The trees this seed really grows are shown in blue in the 3D view.")
            self.sel = bad[0][0]
            self._refresh_all(scene_changed=False)
            self.view.set_ghosts(real, None)
        m = QMessageBox(self)
        m.setWindowTitle("Test with a known seed")
        m.setTextFormat(Qt.TextFormat.RichText)
        m.setText(html + ("<br><br>" + s_html if s_html else ""))
        m.exec()

    def _test_structures(self, seed) -> str:
        """(06/11) the entered structures that seed doesn't place where entered"""
        obs = self.scene.structures
        if not obs:
            return ""
        import ctypes
        import cubiomes_wrap as cw
        import struct_crack as stc
        mc = tc.VERSIONS[self.scene.version][3]
        pos = stc.positions(seed & tc.M48, obs, mc)
        bad = []
        g = stc._cubiomes()
        p = cw.Pos()
        for o, ps in zip(obs, pos):
            if ps is not None:
                continue
            cf = stc.config(o.key, mc)
            lab = stc.STRUCTS[o.key][0]
            if cf is None:
                bad.append(f"{lab}: not in this version")
                continue
            reg = cf[1]
            rx, rz = (o.x >> 4) // reg, (o.z >> 4) // reg
            st = cw.ALL_STRUCTURE_TYPES[stc.STRUCTS[o.key][1]]
            if g._lib.getStructurePos(st, mc, ctypes.c_uint64(seed & tc.M48), rx, rz, ctypes.byref(p)):
                bad.append(f"{lab} at {o.x}, {o.z}: this seed tries it in chunk {p.x >> 4}, {p.z >> 4} "
                           f"(blocks {p.x}, {p.z}) — read the coordinates again, or widen the margin")
            else:
                bad.append(f"{lab} at {o.x}, {o.z}: no {lab.lower()} can spawn in this region with this seed")
        if not bad:
            return (f"<b style='color:{theme.OK}'>Every entered structure matches seed {seed}.</b>")
        return (f"<b style='color:{theme.WARN}'>{len(bad)} structure{'s' * (len(bad) != 1)} don't match "
                f"seed {seed}:</b><ul>" + "".join(f"<li>{b}</li>" for b in bad) + "</ul>")

    def _export_tc(self):
        QGuiApplication.clipboard().setText(export_treecracker(self.scene))
        self.status.emit("TreeCracker input copied")

    def _autosave(self):
        try:
            QSettings("SSA", "SimpleSeedAnalyser").setValue("seedcrack/scene", json.dumps(self.scene.to_json()))
        except Exception:
            pass

    def _restore(self):
        try:
            raw = QSettings("SSA", "SimpleSeedAnalyser").value("seedcrack/scene", "")
            if raw:
                self.scene = tc.Scene.from_json(json.loads(raw))
                self.sel = 0 if self.scene.trees else -1
        except Exception:
            self.scene = tc.Scene()


class _DeselectList(QListWidget):
    """(06/11) a click on the empty part of the list deselects the seed"""

    def mousePressEvent(self, e):
        if self.itemAt(e.position().toPoint()) is None:
            self.clearSelection()
            self.setCurrentRow(-1)
            return
        super().mousePressEvent(e)


# ── TreeCracker text format ────────────────────────────────────────────────────
_VER_MAP = {"v1_14_4": "1.14.4", "v1_14": "1.14.4", "v1_15_2": "1.15.2", "v1_15": "1.15.2",
            "v1_16_1": "1.16.1", "v1_16_4": "1.16.5", "v1_16_5": "1.16.5", "v1_16_2": "1.16.5",
            "v1_16_3": "1.16.5", "v1_17_1": "1.17.1", "v1_17": "1.17.1"}


def parse_treecracker(text: str):
    """Andrew's TreeCracker input → (Scene, warnings). Spruce / pine follow its
    1.16 meaning: tree_spruce(x, z, trunk, logs under the leaves, widest radius, top
    radius, leaves above the trunk - 1), tree_pine(x, z, trunk, leaf layers, widest)."""
    text = re.sub(r"//[^\n]*", "", text)
    warns = []
    ver = None
    biomes = {}
    trees = []
    cur_bio = "forest"

    def ranges(args):
        out = []
        for m in re.finditer(r"IntRange\s*\(\s*(-?\d+)?\s*(?:,\s*(-?\d+)\s*)?\)", args):
            if m.group(1) is None:
                out.append(None)
                continue
            a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else a
            out.append(a if a == b and a >= 0 else None)
        return out
    for m in re.finditer(r"TreeChunkBuilder\s*\(\s*Version::(\w+)\s*,\s*Biome::(\w+)\s*\)|"
                         r"\.tree_(oak|birch|fancy_oak|unknown|spruce|pine)\s*\(([^;]*?)\)\s*(?=\.|\n|$)",
                         text, re.S):
        if m.group(1):
            v = _VER_MAP.get(m.group(1))
            if v is None:
                warns.append(f"Version {m.group(1)} is not supported yet: imported as 1.16.5.")
                v = "1.16.5"
            ver = ver or v
            b = m.group(2).lower()
            cur_bio = "birch_forest" if "birch" in b else "taiga" if "taiga" in b else "forest"
            if b not in ("forest", "birchforest", "taiga"):
                warns.append(f"Biome {m.group(2)} is not supported yet (Forest, BirchForest, Taiga).")
            continue
        kind, args = m.group(3), m.group(4)
        nums = re.match(r"\s*(-?\d+)\s*,\s*(-?\d+)", args)
        if not nums:
            continue
        x, z = int(nums.group(1)), int(nums.group(2))
        rg = ranges(args) + [None] * 5
        h = rg[0]
        leaves = "".join(re.findall(r'"([01?]*)"', args))
        leaves = (leaves + "?" * 12)[:12]
        typ = {"oak": "oak", "birch": "birch", "fancy_oak": "fancy", "unknown": "unknown",
               "spruce": "spruce", "pine": "pine"}[kind]
        attrs = {}
        if typ == "fancy":
            h = None                     # TreeCracker counts it differently: left unknown
        elif typ == "spruce":
            attrs = {"bare": rg[1], "widest": rg[2], "top": rg[3],
                     "above": None if rg[4] is None else rg[4] + 1}
        elif typ == "pine":
            attrs = {"layers": rg[1], "widest": rg[2]}
        attrs = {k: v for k, v in attrs.items() if v is not None}
        trees.append(tc.Tree(x, z, typ, h, leaves, 64, attrs))
        biomes[(x >> 4, z >> 4)] = cur_bio
    if not trees:
        raise ValueError("No tree found in the text.")
    sc = tc.Scene(ver or "1.16.5", trees, {k: v for k, v in biomes.items() if v != "forest"})
    return sc, warns


def export_treecracker(scene) -> str:
    rev = {"1.14.4": "v1_14_4", "1.15.2": "v1_15_2", "1.16.1": "v1_16_1", "1.16.5": "v1_16_4",
           "1.17.1": "v1_16_4"}
    out = []

    def ir(v):
        return f"IntRange({-1 if v is None else v})"
    for ch, trees in scene.chunks().items():
        bio = {"birch_forest": "BirchForest", "taiga": "Taiga"}.get(scene.biome(ch), "Forest")
        out.append(f"// chunk {ch[0]}, {ch[1]}" + ("  (1.17: salt 80002)" if scene.version == "1.17.1" else ""))
        out.append(f"return TreeChunkBuilder(Version::{rev[scene.version]}, Biome::{bio})")
        for t in trees:
            hr = ir(t.height)
            a = t.attrs or {}
            if t.type in ("oak", "birch"):
                lv = '"' + t.leaves[0:4] + '""' + t.leaves[4:8] + '""' + t.leaves[8:12] + '"'
                out.append(f"    .tree_{t.type}({t.x}, {t.z}, {hr}, {lv})")
            elif t.type == "fancy":
                out.append(f"    .tree_fancy_oak({t.x}, {t.z}, IntRange(-1))")
            elif t.type == "spruce":
                ab = a.get("above")
                out.append(f"    .tree_spruce({t.x}, {t.z}, {hr}, {ir(a.get('bare'))}, {ir(a.get('widest'))}, "
                           f"{ir(a.get('top'))}, {ir(None if ab is None else ab - 1)})")
            elif t.type == "pine":
                out.append(f"    .tree_pine({t.x}, {t.z}, {hr}, {ir(a.get('layers'))}, {ir(a.get('widest'))})")
            else:
                out.append(f"    .tree_unknown({t.x}, {t.z})")
        out.append("    .build();")
        out.append("")
    return "\n".join(out)


_DOT_CACHE = {}


def _dot(col):
    from PySide6.QtGui import QIcon, QPixmap
    ic = _DOT_CACHE.get(col)
    if ic is None:
        pm = QPixmap(24, 24); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1.5)); p.setBrush(QColor(col)); p.drawRoundedRect(3, 3, 18, 18, 4, 4); p.end()
        pm.setDevicePixelRatio(2.0)
        ic = _DOT_CACHE[col] = QIcon(pm)
    return ic
