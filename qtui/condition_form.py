"""
qtui/condition_form.py — side-panel editor of one session condition.

  TypePicker      searchable grid of the condition types, by category
  BiomePicker     biome chooser (multi or single) with colour swatches + search
  ConditionForm   General · Location · Details, built from condition_types
                  (the same catalogue as the classic editor). Emits `preview`
                  on every change so the map shows the zone live, and
                  `pickRequested` to draw the area on the map.

Parameters the form does not know (e.g. imported from cubiomes-viewer) are
kept untouched when the condition is saved (before: they were dropped).
"""
from __future__ import annotations

import copy

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from condition_types import (CATEGORY_COLORS, CONDITION_TYPES, _build_octave_options)

from . import icons
from .session_model import (IMPORTED_TYPES, NO_AREA, NO_INSTANCES, TYPE_INFO, new_condition,
                            type_color, type_icon, type_name)
from .widgets import DecimalSpin, Segmented, body, hline, section_title

CATEGORIES = ["Structures", "Biomes", "World", "Iterators", "Helpers"]
CAT_HINT = {"Structures": "Positions of generated structures",
            "Biomes": "Biomes, climate noises, terrain height",
            "World": "Spawn, slime chunks",
            "Iterators": "Try many positions (children must pass at one of them)",
            "Helpers": "Logic gates and dimension scaling"}
STRUCTURE_KEYS = {t["key"] for t in CONDITION_TYPES if t["category"] == "Structures"} | \
    {"stronghold", "fortress", "bastion", "end_city", "mineshaft", "desert_well"}


def _swatch(color, size=12):
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


def _spin(lo=-30_000_000, hi=30_000_000, val=0, step=16, suffix=""):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(val))
    s.setSingleStep(step)
    s.setSuffix(suffix)
    s.setKeyboardTracking(True)       # live: the map follows while typing (debounced)
    return s


def _field(label, w, tip=None):
    row = QHBoxLayout(); row.setSpacing(8)
    lab = body(label, wrap=False)
    lab.setMinimumWidth(118)
    row.addWidget(lab)
    row.addWidget(w, 1)
    if tip:
        lab.setToolTip(tip); w.setToolTip(tip)
    return row


# ═════════════════════════════════════════════════════════════════════════════
class TypePicker(QWidget):
    chosen = Signal(str)
    cancelled = Signal()

    def __init__(self, mc_ok=None, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(8)
        top = QHBoxLayout()
        t = QLabel("Add a condition"); t.setObjectName("PanelTitle"); top.addWidget(t)
        top.addStretch(1)
        x = QToolButton(); icons.bind(x, "close", 16); x.setToolTip("Cancel")
        x.clicked.connect(self.cancelled.emit); top.addWidget(x)
        v.addLayout(top)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search a condition…")
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget(); self.lay = QVBoxLayout(inner)
        self.lay.setContentsMargins(0, 4, 4, 4); self.lay.setSpacing(4)
        self.buttons = []
        for cat in CATEGORIES:
            types = [t for t in CONDITION_TYPES if t["category"] == cat]
            if not types:
                continue
            h = section_title(cat); h.setToolTip(CAT_HINT.get(cat, ""))
            self.lay.addWidget(h)
            grid = QGridLayout(); grid.setHorizontalSpacing(6); grid.setVerticalSpacing(6)
            for i, t in enumerate(types):
                b = QPushButton("  " + t["name"]); b.setObjectName("TypeButton")
                b.setIcon(type_icon(t["key"], 18) or _swatch(type_color(t["key"]), 10))
                b.setIconSize(QSize(18, 18))
                b.setToolTip(t.get("desc") or t["name"])
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=t["key"]: self.chosen.emit(k))
                grid.addWidget(b, i, 0)
                self.buttons.append((b, t["name"].lower() + " " + t["key"], h))
            self.lay.addLayout(grid)
            self.lay.addSpacing(6)
        self.lay.addStretch(1)
        sc.setWidget(inner)
        v.addWidget(sc, 1)

    def _filter(self, text):
        q = text.strip().lower()
        for b, key, _h in self.buttons:
            b.setVisible(not q or q in key)

    def showEvent(self, e):
        super().showEvent(e)
        self.search.clear(); self.search.setFocus()


# ═════════════════════════════════════════════════════════════════════════════
class BiomePicker(QDialog):
    def __init__(self, selected, single=False, mc=28, parent=None):
        super().__init__(parent)
        from biome_catalog import BIOME_MIN_MC, OVERWORLD_BIOMES_ORDERED, biome_swatch_hex
        self.setWindowTitle("Choose a biome" if single else "Choose biomes")
        self.resize(360, 560)
        self.single = single
        v = QVBoxLayout(self); v.setContentsMargins(14, 14, 14, 12); v.setSpacing(8)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)
        self.list = QListWidget()
        sel = set(selected if isinstance(selected, (list, tuple, set)) else [selected])
        group = ""
        for bid, name in OVERWORLD_BIOMES_ORDERED:
            if bid is None:
                group = name
                it = QListWidgetItem(name.upper())
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                it.setData(Qt.ItemDataRole.UserRole, None)
                f = it.font(); f.setBold(True); f.setPointSizeF(f.pointSizeF() * 0.85); it.setFont(f)
                self.list.addItem(it)
                continue
            it = QListWidgetItem(_swatch(biome_swatch_hex(bid), 14), name)
            it.setData(Qt.ItemDataRole.UserRole, bid)
            it.setData(Qt.ItemDataRole.UserRole + 1, (name + " " + group).lower())
            ok = mc >= BIOME_MIN_MC.get(bid, 0)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if not single:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            if not ok:
                flags = Qt.ItemFlag.NoItemFlags
                it.setToolTip(f"Needs a newer version (from MC id {BIOME_MIN_MC[bid]})")
            it.setFlags(flags)
            if not single:
                it.setCheckState(Qt.CheckState.Checked if bid in sel else Qt.CheckState.Unchecked)
            self.list.addItem(it)
            if single and bid in sel:
                self.list.setCurrentItem(it)
        if single:
            self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        if not single:
            for text, on in (("All", True), ("None", False)):
                b = QPushButton(text); b.setProperty("kind", "ghost")
                b.clicked.connect(lambda _=False, o=on: self._all(o))
                row.addWidget(b)
        row.addStretch(1)
        c = QPushButton("Cancel"); c.clicked.connect(self.reject); row.addWidget(c)
        ok = QPushButton("OK"); ok.setProperty("kind", "primary"); ok.clicked.connect(self.accept)
        row.addWidget(ok)
        v.addLayout(row)

    def _filter(self, text):
        q = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            key = it.data(Qt.ItemDataRole.UserRole + 1)
            it.setHidden(bool(q) and (key is None or q not in key))

    def _all(self, on):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) is not None and not it.isHidden() \
                    and it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def value(self):
        if self.single:
            it = self.list.currentItem()
            return it.data(Qt.ItemDataRole.UserRole) if it else None
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) is not None and \
                    it.checkState() == Qt.CheckState.Checked:
                out.append(int(it.data(Qt.ItemDataRole.UserRole)))
        return out


# ═════════════════════════════════════════════════════════════════════════════
class ConditionForm(QWidget):
    saved = Signal(dict)
    cancelled = Signal()
    preview = Signal(dict)
    pickRequested = Signal(str)            # "circle" / "rect" / "point"
    changeTypeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cond = None
        self.parent_label = None
        self.mc = 28
        self._building = False
        # live preview while typing, debounced (no redraw per key stroke)
        self._preview_timer = QTimer(self, singleShot=True, interval=60,
                                     timeout=self._emit_preview)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        # header
        hdr = QFrame(); hdr.setObjectName("FormHeader")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(14, 12, 10, 10); hl.setSpacing(8)
        self.dot = QLabel(); self.dot.setFixedSize(12, 12)
        hl.addWidget(self.dot)
        col = QVBoxLayout(); col.setSpacing(0)
        self.title = QLabel(); self.title.setObjectName("PanelTitle")
        self.subtitle = QLabel(); self.subtitle.setObjectName("Muted")
        col.addWidget(self.title); col.addWidget(self.subtitle)
        hl.addLayout(col, 1)
        self.change_btn = QPushButton("Change type"); self.change_btn.setProperty("kind", "ghost")
        self.change_btn.clicked.connect(self.changeTypeRequested.emit)
        hl.addWidget(self.change_btn)
        v.addWidget(hdr)
        v.addWidget(hline())
        # body (scroll)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        v.addWidget(self.scroll, 1)
        # footer
        v.addWidget(hline())
        foot = QHBoxLayout(); foot.setContentsMargins(14, 10, 14, 12)
        self.err = QLabel(); self.err.setObjectName("Error"); self.err.setWordWrap(True)
        foot.addWidget(self.err, 1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.cancelled.emit)
        self.ok = QPushButton("Save"); self.ok.setProperty("kind", "primary")
        self.ok.clicked.connect(self._save)
        foot.addWidget(cancel); foot.addWidget(self.ok)
        v.addLayout(foot)

    # ── building ────────────────────────────────────────────────────────────
    def edit(self, cond, parent_label=None, mc=28, is_new=False):
        """Show the form for `cond` (engine-format dict)."""
        self.cond = copy.deepcopy(cond)
        self.parent_label = parent_label
        self.mc = mc
        self.is_new = is_new
        self._build()
        self._emit_preview()

    def _build(self):
        self._building = True
        c = self.cond
        t = c.get("type", "")
        info = TYPE_INFO.get(t, {})
        ic = type_icon(t, 22)
        if ic is not None:
            self.dot.setFixedSize(22, 22); self.dot.setStyleSheet("")
            self.dot.setPixmap(ic.pixmap(22, 22))
        else:
            self.dot.setFixedSize(12, 12); self.dot.clear()
            self.dot.setStyleSheet(f"background:{type_color(t)}; border-radius:6px;")
        self.title.setText(("New · " if self.is_new else "") + type_name(t))
        self.subtitle.setText(info.get("category", "Imported") +
                              (f" · relative to {self.parent_label}" if self.parent_label
                               else " · relative to the world origin"))
        self.change_btn.setVisible(t in TYPE_INFO)
        self.err.clear()
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 10, 14, 14); lay.setSpacing(8)
        self.w = {}
        self.pw = {}

        # General
        lay.addWidget(section_title("General"))
        self.w["label"] = QLineEdit(c.get("label", "")); self.w["label"].setPlaceholderText(type_name(t))
        self.w["label"].textChanged.connect(self._changed)
        lay.addLayout(_field("Label", self.w["label"]))
        self.w["enabled"] = QCheckBox("Enabled"); self.w["enabled"].setChecked(c.get("enabled", True))
        self.w["enabled"].toggled.connect(self._changed)
        lay.addWidget(self.w["enabled"])
        if info.get("desc"):
            lay.addWidget(body(info["desc"], muted=True, small=True))
        if t in IMPORTED_TYPES:
            lay.addWidget(body("Imported from a cubiomes-viewer session. Its parameters are "
                               "kept as they are; the area and label can be edited here.",
                               muted=True, small=True))

        # Location
        if t not in NO_AREA:
            lay.addSpacing(4); lay.addWidget(section_title("Location"))
            self._build_location(lay, c, info)

        # Details
        specs = info.get("params", {})
        if specs:
            lay.addSpacing(4); lay.addWidget(section_title("Details"))
            self._build_params(lay, c, specs)
        lay.addStretch(1)
        self.scroll.setWidget(w)
        self._building = False
        self._sync_enable()

    def _build_location(self, lay, c, info):
        t = c["type"]
        if info.get("no_area"):
            lay.addWidget(body("Searched over the whole world.", muted=True, small=True))
            return
        radial_only = info.get("radial_only")
        modes = ["Radius"] if radial_only else (["Square", "Box"] if info.get("no_radial")
                                                else ["Radius", "Square", "Box"])
        am = int(c.get("area_mode", 0) or 0)
        cur = {0: "Radius", 1: "Square", 2: "Box"}.get(am, "Radius")
        if cur not in modes:
            cur = modes[0]
        self.w["area"] = Segmented(modes, cur)
        self.w["area"].changed.connect(self._area_mode_changed)
        if len(modes) > 1:
            lay.addWidget(self.w["area"])
        r = int(c.get("radial", 0) or 0)
        if not r and int(c.get("rmax", 0) or 0) > 0:
            r = int(c["rmax"]) - 1
        self.w["radius"] = _spin(0, 30_000_000, r, 16, " blocks")
        self.w["square"] = _spin(0, 60_000_000, int(c.get("square_side", 0) or
                                                    (c.get("x2", 0) - c.get("x1", 0))), 16, " blocks")
        box = QWidget(); g = QGridLayout(box); g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(6); g.setVerticalSpacing(4)
        for i, k in enumerate(("x1", "z1", "x2", "z2")):
            self.w[k] = _spin(val=int(c.get(k, 0)))
            g.addWidget(body(k.upper(), muted=True, wrap=False), i // 2, (i % 2) * 2)
            g.addWidget(self.w[k], i // 2, (i % 2) * 2 + 1)
        self.w["box"] = box
        self._rows = {}
        for key, label, wid in (("radius", "Radius", self.w["radius"]),
                                ("square", "Square side", self.w["square"])):
            holder = QWidget(); hl = QHBoxLayout(holder); hl.setContentsMargins(0, 0, 0, 0)
            hl.addLayout(_field(label, wid))
            self._rows[key] = holder; lay.addWidget(holder)
            wid.valueChanged.connect(self._changed)
        self._rows["box"] = box; lay.addWidget(box)
        for k in ("x1", "z1", "x2", "z2"):
            self.w[k].valueChanged.connect(self._changed)
        if info.get("requires_at"):
            at = QWidget(); al = QHBoxLayout(at); al.setContentsMargins(0, 0, 0, 0); al.setSpacing(6)
            self.w["at_x"] = _spin(val=int(c.get("at_x", 0))); self.w["at_z"] = _spin(val=int(c.get("at_z", 0)))
            lab = body("Centre X / Z", wrap=False); lab.setMinimumWidth(118)
            al.addWidget(lab); al.addWidget(self.w["at_x"], 1); al.addWidget(self.w["at_z"], 1)
            pb = QToolButton(); icons.bind(pb, "target", 16); pb.setToolTip("Pick the centre on the map")
            pb.clicked.connect(lambda: self.pickRequested.emit("point"))
            al.addWidget(pb)
            lay.addWidget(at)
            for k in ("at_x", "at_z"):
                self.w[k].valueChanged.connect(self._changed)
            if t == "precise_y" and self.parent_label:
                lay.addWidget(body("With a parent, the centre is an OFFSET from the parent's "
                                   "position.", muted=True, small=True))
        draw = QPushButton("  Draw the area on the map"); icons.bind(draw, "brush", 16)
        draw.setProperty("kind", "ghost")
        draw.clicked.connect(lambda: self.pickRequested.emit(
            "circle" if self.w["area"].value() == "Radius" else "rect"))
        lay.addWidget(draw)
        if t not in NO_INSTANCES:
            self.w["instances"] = _spin(0, 1024, int(c.get("instances", 1)), 1)
            lay.addLayout(_field("Instances", self.w["instances"],
                                 "Minimum number of instances inside the area.\n"
                                 "0 = exclusion: NO instance may be inside the area."))
            self.w["instances"].valueChanged.connect(self._changed)
        if t in STRUCTURE_KEYS and self.parent_label:
            self.w["ignore_ref"] = QCheckBox("Ignore the reference structure itself")
            self.w["ignore_ref"].setToolTip("An instance exactly at the parent's position is not "
                                            "counted (useful for 'another village near this one').")
            self.w["ignore_ref"].setChecked(bool(c.get("ignore_ref")))
            self.w["ignore_ref"].toggled.connect(self._changed)
            lay.addWidget(self.w["ignore_ref"])
        if t == "precise_y":
            p = c.get("params", {})
            self.w["coverage"] = DecimalSpin(2); self.w["coverage"].setRange(0, 100)
            self.w["coverage"].setSingleStep(5); self.w["coverage"].setSuffix(" %")
            self.w["coverage"].setValue(float(p.get("coverage", 0) or 0) * 100)
            self.w["confidence"] = DecimalSpin(2); self.w["confidence"].setRange(1, 99.99)
            self.w["confidence"].setSuffix(" %")
            self.w["confidence"].setValue(float(p.get("confidence", 0.95) or 0.95) * 100)
            self.w["sampling"] = QComboBox(); self.w["sampling"].addItems(["1/1", "1/4", "1/16"])
            self.w["sampling"].setCurrentText({1: "1/1", 2: "1/4", 4: "1/16"}.get(int(p.get("sampling", 2) or 2), "1/4"))
            lay.addLayout(_field("Coverage", self.w["coverage"], "0 % = any sampled point is enough"))
            lay.addLayout(_field("Confidence", self.w["confidence"]))
            lay.addLayout(_field("Sampling", self.w["sampling"]))
            for k in ("coverage", "confidence"):
                self.w[k].valueChanged.connect(self._changed)
            self.w["sampling"].currentTextChanged.connect(self._changed)
        self._area_mode_changed(self.w["area"].value(), emit=False)

    def _area_mode_changed(self, mode, emit=True):
        for k, row in self._rows.items():
            row.setVisible({"Radius": "radius", "Square": "square", "Box": "box"}[mode] == k)
        if emit:
            self._changed()

    # ── params ──────────────────────────────────────────────────────────────
    def _build_params(self, lay, c, specs):
        p = c.get("params", {})
        self.pw = {}
        for key, spec in specs.items():
            typ = spec.get("type")
            label = (spec.get("label") or key).strip()
            for emo in ("🔎", "📏", "💥"):
                label = label.replace(emo, "").strip()
            val = p.get(key, spec.get("default"))
            if typ == "bool":
                wd = QCheckBox(label); wd.setChecked(bool(val)); wd.toggled.connect(self._changed)
                lay.addWidget(wd)
            elif typ in ("int", "percent"):
                lo, hi = spec.get("min", -2**31 + 1), spec.get("max", 2**31 - 1)
                if typ == "percent":
                    # decimals: 99.5 % coverage, 0.01 % (cherry) imported from
                    # cubiomes-viewer — an integer box turned those into 99 / 0
                    wd = DecimalSpin(4); wd.setRange(lo, hi)
                    wd.setValue(float(val or 0))
                    wd.setSuffix(" %")
                elif hi > 2**31 - 1:
                    wd = DecimalSpin(0); wd.setRange(lo, hi)
                    wd.setValue(int(val or 0))
                else:
                    wd = QSpinBox(); wd.setRange(lo, hi)
                    wd.setValue(int(val or 0)); wd.setKeyboardTracking(True)
                wd.valueChanged.connect(self._changed)
                if label:
                    lay.addLayout(_field(label, wd))
                else:
                    lay.addWidget(wd)
            elif typ == "choice":
                wd = QComboBox()
                for o in spec.get("options", []):
                    lab, v = (o if isinstance(o, tuple) else (str(o), o))
                    wd.addItem(str(lab), v)
                i = wd.findData(val)
                wd.setCurrentIndex(i if i >= 0 else 0)
                wd.currentIndexChanged.connect(lambda _=0, k=key: self._choice_changed(k))
                lay.addLayout(_field(label, wd))
            elif typ == "range":
                lo, hi = (list(val) + [None, None])[:2] if val else (None, None)
                box = QWidget(); bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0)
                a = QLineEdit("" if lo is None else str(lo)); a.setPlaceholderText("−∞")
                b = QLineEdit("" if hi is None else str(hi)); b.setPlaceholderText("+∞")
                for e in (a, b):
                    e.textChanged.connect(self._changed)
                bl.addWidget(a); bl.addWidget(body("to", muted=True, wrap=False)); bl.addWidget(b)
                wd = (a, b)
                lay.addLayout(_field(label, box, "Empty = no bound. Values are ×10000 like "
                                                 "cubiomes-viewer (e.g. -4500)."))
            elif typ == "octave_choice":
                wd = QComboBox()
                wd.currentIndexChanged.connect(self._changed)
                lay.addLayout(_field(label, wd))
                self.pw[key] = (typ, wd, spec)
                self._fill_octaves(key, p.get(spec.get("depends_on", "para")), val)
                continue
            elif typ in ("biome_list", "biome_single"):
                wd = QPushButton(); wd.setProperty("kind", "ghost")
                wd.setProperty("value", val)
                wd.clicked.connect(lambda _=False, k=key: self._pick_biomes(k))
                lay.addLayout(_field(label, wd))
                self.pw[key] = (typ, wd, spec)
                self._biome_text(key)
                continue
            elif typ == "dual_height_slider":
                lo = int(p.get("y_min", spec.get("min", -64)))
                hi = int(p.get("y_max", spec.get("max", 320)))
                box = QWidget(); bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0)
                a = _spin(-64, 320, lo, 1); b = _spin(-64, 320, hi, 1)
                for e in (a, b):
                    e.valueChanged.connect(self._changed)
                bl.addWidget(a); bl.addWidget(body("to", muted=True, wrap=False)); bl.addWidget(b)
                wd = (a, b)
                lay.addLayout(_field(label, box))
            elif typ == "slider_with_entry":
                wd = DecimalSpin(3)
                wd.setRange(spec.get("min", 0.0), spec.get("max", 1.0))
                wd.setSingleStep(spec.get("step", 0.001)); wd.setValue(float(val or 0))
                wd.valueChanged.connect(self._changed)
                lay.addLayout(_field(label, wd))
            elif typ == "exclusive_pair":
                opts = spec.get("options", [])
                wd = Segmented([o[1] for o in opts],
                               next((o[1] for o in opts if o[0] == val), opts[0][1]))
                wd.setProperty("opts", opts)
                wd.changed.connect(lambda _v: self._changed())
                lay.addWidget(body(label, wrap=False)); lay.addWidget(wd)
            elif typ == "octave_grid":
                box = QWidget(); g = QGridLayout(box); g.setContentsMargins(0, 0, 0, 0)
                g.setHorizontalSpacing(10); g.setVerticalSpacing(2)
                wd = {}
                cur = val or {}
                for i in range(6):
                    for j, ab in enumerate("AB"):
                        slot = f"{i}{ab}"
                        cb = QCheckBox(slot); cb.setChecked(bool(cur.get(slot)))
                        cb.toggled.connect(self._changed)
                        g.addWidget(cb, j, i); wd[slot] = cb
                lay.addWidget(body(label, wrap=False)); lay.addWidget(box)
            elif typ == "weight_matrix":
                box = QWidget(); g = QGridLayout(box); g.setContentsMargins(0, 0, 0, 0)
                wd = {}
                cur = val or {}
                for i in range(6):
                    for j, ab in enumerate("AB"):
                        slot = f"{i}{ab}"
                        sp = DecimalSpin(2); sp.setRange(0, 100)
                        sp.setValue(float(cur.get(slot, 1.0))); sp.valueChanged.connect(self._changed)
                        g.addWidget(body(slot, muted=True, wrap=False), j * 2, i)
                        g.addWidget(sp, j * 2 + 1, i); wd[slot] = sp
                wd["__box"] = box
                lay.addWidget(box)
            else:
                continue                                    # unknown widget type: value kept
            self.pw[key] = (typ, wd, spec)
        self._sync_enable()

    def _fill_octaves(self, key, para, current):
        typ, wd, spec = self.pw[key]
        wd.blockSignals(True)
        wd.clear()
        opts = _build_octave_options(para or "Temperature")
        for lab, v in opts:
            wd.addItem(lab, v)
        i = wd.findData(current)
        wd.setCurrentIndex(i if i >= 0 else 0)
        wd.blockSignals(False)

    def _choice_changed(self, key):
        # octave list depends on the climate parameter
        for k, (typ, wd, spec) in self.pw.items():
            if typ == "octave_choice" and spec.get("depends_on", "para") == key:
                self._fill_octaves(k, self.pw[key][1].currentData(), wd.currentData())
        self._changed()

    def _biome_text(self, key):
        from biome_catalog import biome_display_name
        typ, wd, spec = self.pw[key]
        val = wd.property("value")
        if typ == "biome_single":
            wd.setText(biome_display_name(int(val)) if val is not None else "Choose…")
        else:
            ids = list(val or [])
            if not ids:
                wd.setText("Choose biomes…")
            elif len(ids) <= 2:
                wd.setText(", ".join(biome_display_name(int(b)) for b in ids))
            else:
                wd.setText(f"{len(ids)} biomes")

    def _pick_biomes(self, key):
        typ, wd, spec = self.pw[key]
        dlg = BiomePicker(wd.property("value") or [], single=(typ == "biome_single"),
                          mc=self.mc, parent=self)
        if dlg.exec():
            v = dlg.value()
            if typ == "biome_single" and v is None:
                return
            wd.setProperty("value", v)
            self._biome_text(key)
            self._changed()

    def _sync_enable(self):
        pw = getattr(self, "pw", {})
        # crunchiness: custom weights only for "Custom"
        if "custom_weights" in pw and "weighting" in pw:
            custom = pw["weighting"][1].currentData() == "Custom"
            pw["custom_weights"][1]["__box"].setVisible(custom)
        # biome_sample: exclusive pair enables its own field
        for k, (typ, wd, spec) in pw.items():
            if typ == "exclusive_pair":
                chosen = wd.value()
                for code, lab, field in wd.property("opts"):
                    if field in pw:
                        pw[field][1].setEnabled(lab == chosen)
        # stronghold sub-options follow their switch
        if "min_eyes" in pw:
            eyes_on = pw["min_eyes"][1].value() > 0
            for k in ("exact_eyes", "eye_gpu"):
                if k in pw:
                    pw[k][1].setEnabled(eyes_on)
            if "maxy_enabled" in pw and "min_max_y" in pw:
                pw["min_max_y"][1].setEnabled(pw["maxy_enabled"][1].isChecked())
            if "corrupt_enabled" in pw and "min_frames" in pw:
                pw["min_frames"][1].setEnabled(pw["corrupt_enabled"][1].isChecked())

    # ── value ───────────────────────────────────────────────────────────────
    def value(self):
        """The edited condition (engine format), or raises ValueError(message)."""
        c = copy.deepcopy(self.cond)
        t = c["type"]
        info = TYPE_INFO.get(t, {})
        c["label"] = self.w["label"].text().strip()
        c["enabled"] = self.w["enabled"].isChecked()
        if "area" in self.w:
            mode = self.w["area"].value()
            if mode == "Radius":
                r = self.w["radius"].value()
                c.update(area_mode=0, radial=r, square_side=0, rmax=(r + 1) if r > 0 else 0,
                         x1=-r, z1=-r, x2=r, z2=r)
            elif mode == "Square":
                s = self.w["square"].value(); h = s // 2
                c.update(area_mode=1, radial=0, square_side=s, rmax=0, x1=-h, z1=-h, x2=h, z2=h)
            else:
                x1, z1, x2, z2 = (self.w[k].value() for k in ("x1", "z1", "x2", "z2"))
                if x2 < x1 or z2 < z1:
                    raise ValueError("Box: X2 / Z2 must be ≥ X1 / Z1.")
                c.update(area_mode=2, radial=0, square_side=0, rmax=0, x1=x1, z1=z1, x2=x2, z2=z2)
            if info.get("requires_at"):
                ax, az = self.w["at_x"].value(), self.w["at_z"].value()
                r = c.get("radial", 0)
                c.update(at_x=ax, at_z=az, x1=ax - r, z1=az - r, x2=ax + r, z2=az + r)
        if "instances" in self.w:
            c["instances"] = self.w["instances"].value()
        if "ignore_ref" in self.w:
            c["ignore_ref"] = self.w["ignore_ref"].isChecked()
        params = dict(c.get("params", {}))                 # unknown keys are kept
        for key, (typ, wd, spec) in getattr(self, "pw", {}).items():
            if typ == "bool":
                params[key] = wd.isChecked()
            elif typ == "percent":
                params[key] = float(wd.value())
            elif typ == "int":
                params[key] = int(wd.value())
            elif typ == "choice":
                params[key] = wd.currentData()
            elif typ == "range":
                vals = []
                for e in wd:
                    s = e.text().strip().replace(",", ".").replace(" ", "")
                    if s == "":
                        vals.append(None)
                        continue
                    try:
                        float(s)
                    except ValueError:
                        raise ValueError(f"{spec.get('label', key)}: '{s}' is not a number.")
                    vals.append(s)
                params[key] = tuple(vals)
            elif typ == "octave_choice":
                params[key] = int(wd.currentData() if wd.currentData() is not None else -1)
            elif typ in ("biome_list", "biome_single"):
                v = wd.property("value")
                params[key] = (list(v or []) if typ == "biome_list" else int(v or 0))
            elif typ == "dual_height_slider":
                lo, hi = wd[0].value(), wd[1].value()
                if lo > hi:
                    lo, hi = hi, lo
                params["y_min"], params["y_max"] = lo, hi
            elif typ == "slider_with_entry":
                params[key] = float(wd.value())
            elif typ == "exclusive_pair":
                chosen = wd.value()
                params[key] = next(o[0] for o in wd.property("opts") if o[1] == chosen)
            elif typ == "octave_grid":
                params[key] = {slot: cb.isChecked() for slot, cb in wd.items()}
            elif typ == "weight_matrix":
                params[key] = {slot: sp.value() for slot, sp in wd.items() if slot != "__box"}
        if t == "precise_y" and "coverage" in self.w:
            params["coverage"] = self.w["coverage"].value() / 100.0
            params["confidence"] = self.w["confidence"].value() / 100.0
            params["sampling"] = {"1/1": 1, "1/4": 2, "1/16": 4}[self.w["sampling"].currentText()]
        c["params"] = params
        # semantic checks (same rules as the classic editor, plus a few)
        if t in ("biome_filter", "biome_sample") and not params.get("biomes_incl"):
            raise ValueError("Choose at least one biome.")
        if t == "biome_center" and params.get("biome_id") in (None,):
            raise ValueError("Choose a biome.")
        if t == "crunchiness" and not any((params.get("octaves") or {}).values()):
            raise ValueError("Tick at least one octave.")
        return c

    # ── signals ─────────────────────────────────────────────────────────────
    def _changed(self, *_):
        if self._building:
            return
        self._sync_enable()
        self._preview_timer.start()

    def _emit_preview(self):
        try:
            c = self.value()
            self.err.clear()
        except ValueError as e:
            self.err.setText(str(e))
            return
        self.preview.emit(c)

    def _save(self):
        try:
            c = self.value()
        except ValueError as e:
            self.err.setText(str(e))
            return
        self.saved.emit(c)

    def apply_pick(self, r):
        """Result of a map pick (see MapView.picked) → area fields."""
        if r["kind"] == "point" and "at_x" in self.w:
            self.w["at_x"].setValue(r["x"]); self.w["at_z"].setValue(r["z"])
        elif r["kind"] == "circle" and "radius" in self.w:
            self.w["area"].set_value("Radius"); self._area_mode_changed("Radius", emit=False)
            self.w["radius"].setValue(r["r"])
            if "at_x" in self.w and not self.parent_label:
                self.w["at_x"].setValue(r["x"]); self.w["at_z"].setValue(r["z"])
        elif r["kind"] == "rect" and "x1" in self.w:
            ox, oz = getattr(self, "pick_origin", (0, 0)) or (0, 0)
            self.w["area"].set_value("Box"); self._area_mode_changed("Box", emit=False)
            for k, v in (("x1", r["x1"] - ox), ("z1", r["z1"] - oz), ("x2", r["x2"] - ox), ("z2", r["z2"] - oz)):
                self.w[k].setValue(v)
        self._changed()


def fresh(t: str) -> dict:
    return new_condition(t)


__all__ = ["TypePicker", "BiomePicker", "ConditionForm", "fresh", "CATEGORY_COLORS"]
