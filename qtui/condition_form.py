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

from . import icons, theme
from .session_model import (IMPORTED_TYPES, NO_AREA, NO_INSTANCES, TYPE_INFO, new_condition,
                            type_color, type_icon, type_name)
from .widgets import DecimalSpin, Segmented, body, hline, section_title

# octave pairs of each climate noise (1.18+, same in every version and with
# Large biomes; = ssa_crunch_n_octaves): T, H, C, E, W
CRUNCH_OCTAVES = {0: 2, 1: 2, 2: 6, 3: 4, 5: 3}

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


class _SegChoice(Segmented):
    """A « choice » parameter as joined buttons; currentData() like a QComboBox."""

    def __init__(self, options, labels, value):
        self._vals = [o[1] if isinstance(o, tuple) else o for o in options]
        self._labs = [o[0] if isinstance(o, tuple) else labels.get(o, str(o)) for o in options]
        cur = self._labs[self._vals.index(value)] if value in self._vals else self._labs[0]
        super().__init__(self._labs, cur)

    def currentData(self):
        lab = self.value()
        return self._vals[self._labs.index(lab)] if lab in self._labs else self._vals[0]


# ═════════════════════════════════════════════════════════════════════════════
class _Card(QFrame):
    """A clickable family card: icon, name, number of conditions."""
    clicked = Signal()

    def __init__(self, icon, name, n, parent=None):
        super().__init__(parent)
        self.setObjectName("FamilyCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(46)
        h = QHBoxLayout(self); h.setContentsMargins(12, 0, 12, 0); h.setSpacing(12)
        ic = QLabel(); ic.setPixmap(icon.pixmap(24, 24)); h.addWidget(ic)
        t = QLabel(name); f = t.font(); f.setBold(True); f.setPointSizeF(f.pointSizeF() * 1.05)
        t.setFont(f); h.addWidget(t, 1)
        c = QLabel(str(n)); c.setObjectName("Muted"); h.addWidget(c)
        ch = QLabel("›"); ch.setObjectName("Muted"); h.addWidget(ch)
        self._style(False)

    def _style(self, hover):
        self.setStyleSheet(
            f"QFrame#FamilyCard {{ background: {theme.BG2}; border-radius: 8px;"
            f" border: 1px solid {theme.ACCENT if hover else theme.LINE2}; }}"
            f"QFrame#FamilyCard QLabel {{ background: transparent; border: none; color: {theme.TEXT}; }}"
            f"QFrame#FamilyCard QLabel#Muted {{ color: {theme.TEXT3}; }}")

    def enterEvent(self, e):
        self._style(True); super().enterEvent(e)

    def leaveEvent(self, e):
        self._style(False); super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class TypePicker(QWidget):
    """Add a condition (03/10): first the family, then its conditions; typing in
    the search box lists every matching one."""
    chosen = Signal(str)
    cancelled = Signal()

    # (family, condition keys or None = every other non-structure one, icon key)
    GROUPS = [("Structures", "structures", "village"),
              ("Biomes", ["biome_filter", "biome_sample", "biome_center"], "biome_filter"),
              ("Climate", ["climate_minmax", "climate_center", "noise_sample", "crunchiness",
                           "height", "precise_y"], "climate_center"),
              ("Algorithm helpers", ["spiral", "logic_or", "logic_not", "scale_to_nether",
                                     "scale_to_overworld"], "spiral"),
              ("Others", None, "slime")]

    @classmethod
    def _members(cls, keys):
        live = [t for t in CONDITION_TYPES if not t.get("disabled")]
        if keys == "structures":
            return [t for t in live if t["category"] == "Structures"]
        if keys is not None:
            by = {t["key"]: t for t in live}
            return [by[k] for k in keys if k in by]
        named = {k for _n, ks, _i in cls.GROUPS if isinstance(ks, list) for k in ks}
        return [t for t in live if t["category"] != "Structures" and t["key"] not in named]

    def __init__(self, mc_ok=None, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QStackedWidget
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(8)
        top = QHBoxLayout()
        self.back = QToolButton(); icons.bind(self.back, "chevron-left", 16)
        self.back.setToolTip("Back to the families"); self.back.clicked.connect(self._home)
        top.addWidget(self.back)
        self.title = QLabel("Add a condition"); self.title.setObjectName("PanelTitle")
        top.addWidget(self.title)
        top.addStretch(1)
        x = QToolButton(); icons.bind(x, "close", 16); x.setToolTip("Cancel")
        x.clicked.connect(self.cancelled.emit); top.addWidget(x)
        v.addLayout(top)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search a condition…")
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        home = QWidget(); hl = QVBoxLayout(home); hl.setContentsMargins(0, 4, 0, 0); hl.setSpacing(8)
        self.pages = {}
        for name, keys, ikey in self.GROUPS:
            types = self._members(keys)
            if not types:
                continue
            icn = type_icon(ikey, 24) or _swatch(type_color(types[0]["key"]), 18)
            card = _Card(icn, name, len(types))
            card.clicked.connect(lambda g=name: self._open(g))
            hl.addWidget(card)
            self.pages[name] = self._type_page(types)
        hl.addStretch(1)
        self.stack.addWidget(home)
        self.home = home
        for pg in self.pages.values():
            self.stack.addWidget(pg)
        self.results = self._type_page([t for t in CONDITION_TYPES if not t.get("disabled")],
                                       flat=True)
        self.stack.addWidget(self.results)
        self._home()

    def _type_page(self, types, flat=False):
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget(); lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 4, 4, 4); lay.setSpacing(4)
        sc.buttons = []
        groups = []
        if flat:                                   # search: grouped by family
            for name, keys, _i in self.GROUPS:
                ks = {t["key"] for t in self._members(keys)}
                groups.append((name, [t for t in types if t["key"] in ks]))
        else:
            groups.append((None, types))
        for gname, ts in groups:
            if not ts:
                continue
            h = None
            if gname:
                h = section_title(gname); lay.addWidget(h)
            for t in ts:
                b = QPushButton("  " + t["name"]); b.setObjectName("TypeButton")
                b.setIcon(type_icon(t["key"], 18) or _swatch(type_color(t["key"]), 10))
                b.setIconSize(QSize(18, 18))
                b.setToolTip(t.get("desc") or t["name"])
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=t["key"]: self.chosen.emit(k))
                lay.addWidget(b)
                sc.buttons.append((b, t["name"].lower() + " " + t["key"] + " "
                                   + (gname or "").lower(), h))
            lay.addSpacing(6)
        lay.addStretch(1)
        sc.setWidget(inner)
        return sc

    def _home(self):
        self.stack.setCurrentWidget(self.home)
        self.title.setText("Add a condition")
        self.back.setVisible(False)

    def _open(self, group):
        self.stack.setCurrentWidget(self.pages[group])
        self.title.setText(group)
        self.back.setVisible(True)

    def _filter(self, text):
        q = text.strip().lower()
        if not q:
            self._home()
            return
        heads = {}
        for b, key, h in self.results.buttons:
            on = q in key
            b.setVisible(on)
            if h is not None:
                heads[h] = heads.get(h, False) or on
        for h, on in heads.items():
            h.setVisible(on)
        self.stack.setCurrentWidget(self.results)
        self.title.setText("Search")
        self.back.setVisible(True)

    def showEvent(self, e):
        super().showEvent(e)
        self.search.clear(); self.search.setFocus()
        self._home()


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
        if is_new:
            self.plan_gpu = False
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
        self.title.setText(type_name(t))
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
        if info.get("disabled"):               # e.g. abandoned camp (temporarily hidden)
            warn = body(info["disabled"] + " This one is kept as it was loaded.", small=True)
            warn.setStyleSheet(f"color: {theme.WARN};")
            lay.addWidget(warn)
        if t in IMPORTED_TYPES:
            lay.addWidget(body("Imported from a cubiomes-viewer session. Its parameters are "
                               "kept as they are; the area and label can be edited here.",
                               muted=True, small=True))

        # Location
        if t not in NO_AREA and not info.get("no_area"):   # (whole world: nothing to set)
            lay.addSpacing(4); lay.addWidget(section_title("Location"))
            self._build_location(lay, c, info)

        # Details
        specs = info.get("params", {})
        self._ca_hint = None
        self.sh = None
        if t == "first_stronghold":            # its own panel (03/10)
            self.pw = {}
            lay.addSpacing(4)
            self._build_stronghold(lay, c)
            specs = {}
        if specs:
            lay.addSpacing(4); lay.addWidget(section_title("Details"))
            self._build_params(lay, c, specs)
            if t == "climate_center":            # cells → blocks, live
                self._ca_hint = body("", muted=True, small=True)
                lay.addWidget(self._ca_hint)
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

    # ── first stronghold (03/10) ────────────────────────────────────────────
    def _build_stronghold(self, lay, c):
        """Eyes (the portal), Top Y (stronghold / portal room), ruined portal over
        the end portal (1.13–1.17 only), first or any of the 128."""
        from .stronghold_visuals import PortalPicker
        p = c.get("params", {}) or {}
        sh = self.sh = {}
        # what the condition's position is (03/10): linked conditions, the result's /tp
        sh["anchor"] = Segmented(["Stronghold origin", "End portal"],
                                 "End portal" if p.get("sh_anchor") == "portal"
                                 else "Stronghold origin")
        sh["anchor"].buttons["Stronghold origin"].setToolTip(
            "The ring point of the stronghold, like cubiomes-viewer (≤ 112 blocks from the real\n"
            "stronghold). Linked conditions are placed from it. The filters below always look at\n"
            "the real stronghold.")
        sh["anchor"].buttons["End portal"].setToolTip(
            "The centre of the end portal (the /tp of the result). Linked conditions are placed\n"
            "from it, e.g. a village within 200 blocks of the portal. Exact; linked conditions\n"
            "are a little slower to search.")
        sh["anchor"].changed.connect(lambda _v: self._changed())
        lay.addLayout(_field("Position", sh["anchor"]))
        # which strongholds
        _sc = str(p.get("sh_scope", ""))
        scope = ("All 128" if (_sc == "All 128" or (not _sc and p.get("scan_128")))
                 else "First ring" if _sc == "Ring 3" else "First")
        sh["scope"] = Segmented(["First", "First ring", "All 128"], scope)
        sh["scope"].buttons["First"].setToolTip("The filters must pass on the first stronghold "
                                                "(the fastest).")
        sh["scope"].buttons["First ring"].setToolTip(
            "The filters must pass on one of the 3 strongholds of the first ring (the 3 nearest "
            "to 0, 0): about 3× slower.")
        sh["scope"].buttons["All 128"].setToolTip(
            "The filters must pass on at least one of the 128 strongholds (the slowest).")
        lay.addLayout(_field("Strongholds", sh["scope"]))
        sh["sisters"] = QCheckBox("Hide sister seeds")
        sh["sisters"].setChecked(bool(p.get("hide_sisters", True)))
        sh["sisters"].setToolTip(
            "Sister seeds share the 48 low bits AND land on the same stronghold: the very same\n"
            "stronghold (eyes, height, portal), only the biomes / terrain differ. Only the first\n"
            "one is kept in the results (as on the Stronghold page), when this is the\\n"
            "session's only condition.")
        sh["sisters"].toggled.connect(self._changed)
        lay.addWidget(sh["sisters"])
        # eyes
        try:
            me = int(p.get("min_eyes", 0) or 0)
        except (TypeError, ValueError):
            me = 0
        sh["eyes_on"] = QCheckBox("Eyes in the portal")
        sh["eyes_on"].setChecked(me > 0 or bool(p.get("eye_enabled")))
        lay.addWidget(sh["eyes_on"])
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(18, 0, 0, 0); bl.setSpacing(4)
        sh["portal"] = PortalPicker(me if me > 0 else 12)
        sh["portal"].setFixedHeight(128)
        sh["cmp"] = Segmented(["At least", "Exactly"], "Exactly" if p.get("exact_eyes") else "At least")
        sh["portal"].set_exact(bool(p.get("exact_eyes")))
        sh["cmp"].changed.connect(lambda v: (sh["portal"].set_exact(v == "Exactly"), self._changed()))
        sh["portal"].changed.connect(lambda _v: self._changed())
        sh["gpu"] = QCheckBox("Use the GPU (CUDA)")
        sh["gpu"].setToolTip("The eye scan on the graphics card, as on the Stronghold page "
                             "(eyes alone, first stronghold).")
        sh["gpu"].setChecked(bool(p.get("eye_gpu", True)))
        sh["gpu"].toggled.connect(self._changed)
        bl.addWidget(sh["portal"]); bl.addWidget(sh["cmp"]); bl.addWidget(sh["gpu"])
        sh["eyes_box"] = box
        lay.addWidget(box)
        # top Y: the same gauge as the Stronghold page
        from .stronghold_visuals import HeightGauge
        sh["maxy_on"] = QCheckBox("Top Y")
        sh["maxy_on"].setChecked(bool(p.get("maxy_enabled")))
        lay.addWidget(sh["maxy_on"])
        yb = QWidget(); yl = QVBoxLayout(yb); yl.setContentsMargins(18, 0, 0, 0); yl.setSpacing(4)
        row = QHBoxLayout(); row.setSpacing(8)
        sh["target"] = Segmented(["Stronghold", "Portal room"],
                                 "Portal room" if p.get("maxy_target") == "room" else "Stronghold")
        sh["miny"] = QSpinBox(); sh["miny"].setRange(-64, 320); sh["miny"].setPrefix("≥ Y ")
        sh["miny"].setValue(int(p.get("min_max_y", 60) or 60))
        row.addWidget(sh["target"], 1); row.addWidget(sh["miny"])
        yl.addLayout(row)
        sh["gauge"] = HeightGauge(sh["miny"].value())
        sh["gauge"].set_mc(self.mc)
        sh["gauge"].set_room(sh["target"].value() == "Portal room")
        sh["gauge"].changed.connect(sh["miny"].setValue)
        sh["miny"].valueChanged.connect(sh["gauge"].set_value)
        sh["miny"].valueChanged.connect(self._changed)
        sh["target"].changed.connect(lambda v: (sh["gauge"].set_room(v == "Portal room"),
                                                self._changed()))
        yl.addWidget(sh["gauge"])
        sh["topy_gpu"] = QCheckBox("Use the GPU too (CPU + GPU)")
        sh["topy_gpu"].setChecked(bool(p.get("topy_gpu", True)))
        sh["topy_gpu"].setToolTip("1.16, 48-bit family engine: the graphics card scans blocks of "
                                  "families\nwhile the CPU does the others and checks every hit "
                                  "exactly (as on the Stronghold page).")
        sh["topy_gpu"].toggled.connect(self._changed)
        yl.addWidget(sh["topy_gpu"])
        sh["topy_note"] = body("", muted=True, small=True)
        yl.addWidget(sh["topy_note"])
        sh["miny"].valueChanged.connect(lambda _v: self._sync_stronghold())
        sh["target"].changed.connect(lambda _v: self._sync_stronghold())
        sh["maxy_box"] = yb
        lay.addWidget(yb)
        # broken end portal: the same portal + causes as the Stronghold page
        from .stronghold_page import CAUSES, CauseChip
        sh["corr"] = QCheckBox("Broken portal")
        sh["corr"].setChecked(bool(p.get("corrupt_enabled")))
        lay.addWidget(sh["corr"])
        cb_ = QWidget(); cl = QVBoxLayout(cb_); cl.setContentsMargins(18, 0, 0, 0); cl.setSpacing(4)
        sh["frames"] = PortalPicker(max(1, int(p.get("min_frames", 1) or 1)), style="broken")
        sh["frames"].setFixedHeight(128)
        sh["frames"].changed.connect(lambda _v: self._changed())
        cl.addWidget(sh["frames"])
        chips = QHBoxLayout(); chips.setSpacing(6)
        src = p.get("broken_sources") or ["ruined"]
        sh["chips"] = {}
        for c0 in CAUSES:
            ch = CauseChip(c0[1], {"ruined": "Ruined", "lava": "Lava", "water": "Water"}[c0[0]])
            ch.setChecked(c0[0] in src)
            ch.toggled.connect(lambda _v: (self._sync_stronghold(), self._changed()))
            sh["chips"][c0[0]] = ch
            chips.addWidget(ch)
        chips.addStretch(1); cl.addLayout(chips)
        sh["chips"]["ruined"].setToolTip(
            "A ruined portal whose footprint covers end portal frames (1.13 – 1.17).")
        lake = ("1.16 only, EXPERIMENTAL: no lake-broken portal confirmed in the game yet.")
        sh["chips"]["lava"].setToolTip(lake); sh["chips"]["water"].setToolTip(lake)
        sh["confirm"] = QCheckBox("Confirm each result with Minecraft 1.16.1")
        sh["confirm"].setChecked(bool(p.get("confirm_mc", True)))
        sh["confirm"].setToolTip(
            "Every result is sent to the real Minecraft generation (the checker of the\n"
            "Stronghold page): the result list shows checking… then ✓ n (n frames broken;\n"
            "'/tp' = only when the chunks load in the /tp order) or ✗ (Minecraft keeps\n"
            "the frames). The search itself is not slowed; the checks follow it.")
        sh["confirm"].toggled.connect(lambda _v: (self._sync_stronghold(), self._changed()))
        cl.addWidget(sh["confirm"])
        sh["corr_note"] = body("", muted=True, small=True)
        cl.addWidget(sh["corr_note"])
        sh["corr_box"] = cb_
        lay.addWidget(cb_)
        for k in ("eyes_on", "maxy_on", "corr"):
            sh[k].toggled.connect(lambda _v: (self._sync_stronghold(), self._changed()))
        sh["scope"].changed.connect(lambda _v: self._changed())
        self._sync_stronghold()

    def _sync_stronghold(self):
        sh = self.sh
        if not sh:
            return
        mc = int(self.mc)
        sh["eyes_box"].setVisible(sh["eyes_on"].isChecked())
        sh["maxy_box"].setVisible(sh["maxy_on"].isChecked())
        sh["gauge"].set_mc(mc)
        import topy_family as _tf
        room = sh["target"].value() == "Portal room"
        fam_ok = (mc in _tf.VERSIONS and sh["miny"].value()
                  >= (_tf.MIN_FAST_Y_ROOM if room else _tf.MIN_FAST_Y))
        sh["topy_gpu"].setVisible(fam_ok)
        sh["topy_note"].setText("Fastest with the session in <b>48-bit family</b> mode."
                                if fam_ok else "")
        sh["topy_note"].setToolTip(
            "1.16, Y ≥ %d (stronghold) / %d (portal room): the Stronghold page's 48-bit family "
            "engine (exact, CPU + GPU)\nruns when the session is in 48-bit family mode (Start / "
            "Count in families)." % (_tf.MIN_FAST_Y, _tf.MIN_FAST_Y_ROOM))
        sh["topy_note"].setVisible(bool(sh["topy_note"].text()))
        ruined_ok, lakes_ok = mc <= 21, mc in (19, 20)
        sh["corr"].setEnabled(ruined_ok or lakes_ok)
        sh["corr"].setToolTip("" if (ruined_ok or lakes_ok) else
                              "Java 1.13 – 1.17 only: from 1.18 the end portal frames are "
                              "protected (the Stronghold page explains it).")
        sh["corr_box"].setVisible(sh["corr"].isChecked() and sh["corr"].isEnabled())
        sh["chips"]["ruined"].setEnabled(ruined_ok)
        for k in ("lava", "water"):
            sh["chips"][k].setEnabled(lakes_ok)
        on = [k for k, ch in sh["chips"].items() if ch.isChecked() and ch.isEnabled()]
        can_confirm = mc in (19, 20)
        sh["confirm"].setVisible(can_confirm)
        sh["corr_note"].setText(
            "Each result is confirmed by Minecraft after the search finds it (column ✓ / ✗)."
            if can_confirm and sh["confirm"].isChecked() else
            "Predicted from the generation (not confirmed by Minecraft)." if not can_confirm else
            "Predicted from the generation: right-click a result → Confirm with Minecraft.")
        from .stronghold_page import CAUSES
        sh["frames"].set_causes([c[3] for c in CAUSES
                                 if c[0] in on])

    def set_mc(self, mc):
        """The version of the top bar changed: grey / ungrey what depends on it."""
        self.mc = int(mc)
        self._sync_stronghold()

    def _stronghold_params(self):
        sh = self.sh
        eyes = sh["eyes_on"].isChecked()
        out = dict(min_eyes=int(sh["portal"].value()) if eyes else 0,
                   exact_eyes=eyes and sh["cmp"].value() == "Exactly",
                   eye_gpu=sh["gpu"].isChecked(),
                   topy_gpu=sh["topy_gpu"].isChecked(),
                   maxy_enabled=sh["maxy_on"].isChecked(),
                   min_max_y=int(sh["miny"].value()),
                   maxy_target="room" if sh["target"].value() == "Portal room" else "stronghold",
                   corrupt_enabled=sh["corr"].isChecked() and sh["corr"].isEnabled(),
                   min_frames=int(sh["frames"].value()),
                   broken_sources=[k for k, ch in sh["chips"].items()
                                   if ch.isChecked() and ch.isEnabled()] or ["ruined"],
                   confirm_mc=sh["confirm"].isChecked(),
                   sh_scope={"All 128": "All 128", "First ring": "Ring 3"}.get(
                       sh["scope"].value(), "First"),
                   hide_sisters=sh["sisters"].isChecked(),
                   sh_anchor="portal" if sh["anchor"].value() == "End portal" else "origin")
        out.pop("eye_enabled", None)
        return out

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
                if key == "use_gpu" and getattr(self, "plan_gpu", False) and not val:
                    lay.addWidget(body("Engine plan applied: this condition runs as a GPU "
                                       "pre-filter (Engine → Apply). This box is the "
                                       "session's own setting.", muted=True, small=True))
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
                    if key == "blocks_count":
                        wd.setSuffix(" blocks")
                else:
                    wd = QSpinBox(); wd.setRange(lo, hi)
                    wd.setValue(int(val or 0)); wd.setKeyboardTracking(True)
                wd.valueChanged.connect(self._changed)
                if label:
                    lay.addLayout(_field(label, wd))
                else:
                    lay.addWidget(wd)
            elif typ == "choice" and spec.get("segmented"):
                # a few named choices (igloo basement, abandoned village…): one click
                wd = _SegChoice(spec.get("options", []), spec.get("labels", {}), val)
                wd.changed.connect(lambda _v, k=key: self._choice_changed(k))
                lay.addLayout(_field(label, wd))
            elif typ == "choice":
                wd = QComboBox()
                names = spec.get("labels", {})
                for o in spec.get("options", []):
                    lab, v = (o if isinstance(o, tuple) else (names.get(o, str(o)), o))
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
                # clamped: an imported cubiomes-viewer « Approx height » has no
                # bound = ±1e30, which made the spin box raise → the editor
                # could not open (01/10)
                def _y(v, d):
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        return d
                    return int(max(-64.0, min(320.0, v))) if v == v else d
                lo = _y(p.get("y_min"), spec.get("min", -64))
                hi = _y(p.get("y_max"), spec.get("max", 320))
                box = QWidget(); bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0)
                a = _spin(-64, 320, lo, 1); b = _spin(-64, 320, hi, 1)
                for e in (a, b):
                    e.valueChanged.connect(self._changed)
                bl.addWidget(a); bl.addWidget(body("to", muted=True, wrap=False)); bl.addWidget(b)
                wd = (a, b)
                lay.addLayout(_field(label, box))
            elif typ == "spiral_step":
                # any step in blocks (sessions use 437, 1 500 …): the old fixed list
                # fell back to 1 for those and saving wrote step 1 (01/10)
                try:
                    blocks = int(val if val is not None else 16)
                    blocks = 512 if blocks < 1 else min(65535, blocks)   # 0 = 512 in cubiomes-viewer
                except (TypeError, ValueError):
                    blocks = 16
                unit = p.get("step_unit", "blocks")
                if unit == "chunks" and blocks % 16:
                    unit = "blocks"
                box = QWidget(); bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0)
                sp = QSpinBox(); sp.setKeyboardTracking(True)
                seg = Segmented(["blocks", "chunks"], unit)
                def _apply_unit(u, sp=sp, keep=None):
                    b = keep if keep is not None else sp.property("blocks")
                    sp.blockSignals(True)
                    if u == "chunks":
                        sp.setRange(1, 4095); sp.setValue(max(1, round(b / 16)))
                    else:
                        sp.setRange(1, 65535); sp.setValue(b)
                    sp.blockSignals(False)
                sp.setProperty("blocks", blocks)
                _apply_unit(unit, keep=blocks)
                def _sp_changed(v, sp=sp, seg=seg):
                    sp.setProperty("blocks", v * 16 if seg.value() == "chunks" else v)
                    self._changed()
                def _seg_changed(u, sp=sp, f=_apply_unit):
                    f(u)
                    sp.setProperty("blocks", sp.value() * 16 if u == "chunks" else sp.value())
                    self._changed()
                sp.valueChanged.connect(_sp_changed)
                seg.changed.connect(_seg_changed)
                bl.addWidget(sp, 1); bl.addWidget(seg)
                wd = (sp, seg)
                lay.addLayout(_field(label, box, "Distance between two tested positions. "
                                                 "1 chunk = 16 blocks; saved in blocks, "
                                                 "like cubiomes-viewer."))
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
                        lab = body(slot, muted=True, wrap=False)
                        g.addWidget(lab, j * 2, i)
                        g.addWidget(sp, j * 2 + 1, i); wd[slot] = sp; wd["__lab" + slot] = lab
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
        hint = getattr(self, "_ca_hint", None)
        if hint is not None and "scale" in pw and "min_size" in pw:
            sc = int(pw["scale"][1].currentData() or 16)
            n = int(pw["min_size"][1].value())
            side = int(round((n ** 0.5) * sc))
            mx = int(pw["max_size"][1].value()) if "max_size" in pw else 0
            txt = (f"{n} cells of {sc}×{sc} blocks ≈ a {side:,} × {side:,} block square "
                   f"({n * sc * sc:,} blocks²).").replace(",", " ")
            if mx:
                txt += (f" At most {mx} cells ({mx * sc * sc:,} blocks²); a region cut by "
                        f"the area border then fails (its size is unknown).").replace(",", " ")
            txt += " Area 0 = explored within ±128 cells."
            hint.setText(txt)
        # crunchiness: custom weights only for "Custom"
        if "custom_weights" in pw and "weighting" in pw:
            custom = pw["weighting"][1].currentData() == "Custom"
            pw["custom_weights"][1]["__box"].setVisible(custom)
        # crunchiness: only the octaves the chosen climate noise really has
        # (the grid always showed 0A … 5B, e.g. 5B for Temperature, which has 2)
        if "octaves" in pw and "climate" in pw:
            n = CRUNCH_OCTAVES.get(pw["climate"][1].currentData(), 6)
            grid = pw["octaves"][1]
            wm = pw["custom_weights"][1] if "custom_weights" in pw else {}
            for slot, cb in grid.items():
                on = int(slot[0]) < n
                cb.setVisible(on)
                if not on and cb.isChecked():
                    cb.blockSignals(True); cb.setChecked(False); cb.blockSignals(False)
                if slot in wm:
                    wm[slot].setVisible(on); wm["__lab" + slot].setVisible(on)
        # biome_sample: exclusive pair enables its own field
        for k, (typ, wd, spec) in pw.items():
            if typ == "exclusive_pair":
                chosen = wd.value()
                for code, lab, field in wd.property("opts"):
                    if field in pw:
                        # only the chosen field is shown, like the area shape
                        # (both visible read as « % AND blocks », 01/10)
                        pw[field][1].setVisible(lab == chosen)
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
            elif typ == "spiral_step":
                params[key] = int(wd[0].property("blocks") or 1)
                params["step_unit"] = wd[1].value()
            elif typ == "slider_with_entry":
                params[key] = float(wd.value())
            elif typ == "exclusive_pair":
                chosen = wd.value()
                params[key] = next(o[0] for o in wd.property("opts") if o[1] == chosen)
            elif typ == "octave_grid":
                params[key] = {slot: cb.isChecked() for slot, cb in wd.items()}
            elif typ == "weight_matrix":
                params[key] = {slot: sp.value() for slot, sp in wd.items() if not slot.startswith("__")
                               and sp.isVisibleTo(wd["__box"])}
        if t == "first_stronghold" and self.sh is not None:
            params.update(self._stronghold_params())
            for old in ("eye_enabled", "scan_128"):      # pre-rework keys (now explicit)
                params.pop(old, None)
        if t == "precise_y" and "coverage" in self.w:
            params["coverage"] = self.w["coverage"].value() / 100.0
            params["confidence"] = self.w["confidence"].value() / 100.0
            params["sampling"] = {"1/1": 1, "1/4": 2, "1/16": 4}[self.w["sampling"].currentText()]
        c["params"] = params
        # semantic checks (same rules as the classic editor, plus a few)
        if t in ("biome_filter", "biome_sample") and not params.get("biomes_incl"):
            raise ValueError("Choose at least one biome.")
        if t == "climate_center":
            r = params.get("range") or (None, None)
            if all(v is None or str(v).strip() == "" for v in r):
                raise ValueError("Set at least one bound of the value range: without one, "
                                 "every cell is in the region.")
            if int(params.get("max_size", 0) or 0) and \
                    int(params["max_size"]) < int(params.get("min_size", 1) or 1):
                raise ValueError("The maximum region size is below the minimum.")
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
