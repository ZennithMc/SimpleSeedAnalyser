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

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QCheckBox, QComboBox, QDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QToolButton, QVBoxLayout, QWidget)

from condition_types import (CATEGORY_COLORS, CONDITION_TYPES, _build_octave_options)

from . import icons, theme
from .area_pad import AreaPad
from .session_model import (IMPORTED_TYPES, NO_AREA, NO_INSTANCES, TYPE_INFO, new_condition,
                            type_color, type_icon, type_name)
from .widgets import DecimalSpin, Segmented, body, hline, section_title

# octave pairs of each climate noise (1.18+, same in every version and with
# Large biomes; = ssa_crunch_n_octaves): T, H, C, E, W
CRUNCH_OCTAVES = {0: 2, 1: 2, 2: 6, 3: 4, 5: 3}

CATEGORIES = ["Structures", "Nether", "End", "Biomes", "World", "Iterators", "Helpers"]
CAT_HINT = {"Structures": "Positions of generated structures",
            "Nether": "Nether structures and biomes (Nether coordinates)",
            "End": "End structures and biomes (End coordinates)",
            "Biomes": "Biomes, climate noises, terrain height",
            "World": "Spawn, slime chunks",
            "Iterators": "Try many positions (children must pass at one of them)",
            "Helpers": "Logic gates and dimension scaling"}
STRUCTURE_KEYS = {t["key"] for t in CONDITION_TYPES if t["category"] == "Structures"} | \
    {"stronghold", "fortress", "bastion", "end_city", "mineshaft", "desert_well"}


def _spin(lo=-30_000_000, hi=30_000_000, val=0, step=16, suffix=""):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(val))
    s.setSingleStep(step)
    s.setSuffix(suffix)
    s.setKeyboardTracking(True)       # live: the map follows while typing (debounced)
    return s


class _Scrub(QObject):
    """(14/11) The label of a number is a handle: dragged sideways it changes
    the value (4 pixels a step, Shift: 10 steps), as in design tools."""

    def __init__(self, label, spin):
        super().__init__(label)
        self.label, self.spin = label, spin
        self._x0 = None
        self._v0 = 0
        label.setCursor(Qt.CursorShape.SizeHorCursor)
        label.installEventFilter(self)

    def eventFilter(self, o, e):
        t = e.type()
        try:
            if t == QEvent.Type.MouseButtonPress and e.button() == Qt.MouseButton.LeftButton \
                    and self.spin.isEnabled():
                self._x0, self._v0 = e.globalPosition().x(), self.spin.value()
                return True
            if t == QEvent.Type.MouseMove and self._x0 is not None:
                k = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                n = int((e.globalPosition().x() - self._x0) / 4.0)
                self.spin.setValue(self._v0 + n * k * self.spin.singleStep())
                return True
            if t == QEvent.Type.MouseButtonRelease and self._x0 is not None:
                self._x0 = None
                return True
        except RuntimeError:
            self._x0 = None
        return False


def _field(label, w, tip=None):
    row = QHBoxLayout(); row.setSpacing(8)
    lab = body(label, wrap=False)
    lab.setMinimumWidth(118)
    row.addWidget(lab)
    row.addWidget(w, 1)
    if tip:                                  # (19/11) on the field only, not twice
        w.setToolTip(tip)
    if isinstance(w, QAbstractSpinBox) and label:
        _Scrub(lab, w)                       # (its ↔ cursor says the label can be dragged)
    return row


class _OctaveCell(QCheckBox):
    """(14/11) An octave of a climate noise as a cell: its wave (the higher the
    octave, the finer the detail) and its name; lit when it counts. Still a
    QCheckBox for the form."""

    def __init__(self, slot):
        super().__init__(slot)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(52, 42)

    def minimumSizeHint(self):
        return QSize(40, 42)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def enterEvent(self, e):
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.update()

    def paintEvent(self, e):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on, live = self.isChecked(), self.isEnabled()
        if not live:
            p.setOpacity(0.32)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        a = theme.ACCENT
        hov = self.underMouse() and live
        p.setBrush(QColor(theme._mix(theme.field_bg(), a, 0.26 if on else 0.05 if hov else 0.0)))
        p.setPen(QPen(QColor(theme._mix(theme.LINE2 if hov else theme.LINE, a, 0.75 if on else 0.0)), 1))
        p.drawRoundedRect(r, 8, 8)
        try:
            k = int(self.text()[0])
        except (ValueError, IndexError):
            k = 0
        cycles = 0.75 * (1.55 ** k)              # octave 0: one slow wave … octave 5: fine ripples
        phase = 0.0 if self.text().endswith("A") else math.pi / 2
        x0, x1, yc, amp = r.left() + 7, r.right() - 7, r.top() + 13, 5.0
        pts = [QPointF(x0 + (x1 - x0) * i / 40.0,
                       yc - amp * math.sin(phase + 2 * math.pi * cycles * i / 40.0)) for i in range(41)]
        pen = QPen(QColor(a if on else theme.TEXT3), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPolyline(pts)
        f = QFont(self.font())
        f.setPointSizeF(8.0)
        f.setWeight(QFont.Weight.Bold if on else QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT if on else theme.TEXT3))
        p.drawText(QRectF(r.left(), r.bottom() - 17, r.width(), 15), Qt.AlignmentFlag.AlignCenter, self.text())


class _AreaMode(QObject):
    """Which of the three ways gives the area: "Radius" / "Square" / "Box" (the
    names the form has always used; Segmented's value() / set_value() / changed)."""
    changed = Signal(str)

    def __init__(self, modes, current, parent=None):
        super().__init__(parent)
        self.modes = list(modes)
        self._v = current if current in self.modes else self.modes[0]

    def value(self) -> str:
        return self._v

    def set_value(self, v: str):               # silent, as Segmented's
        if v in self.modes:
            self._v = v

    def choose(self, v: str):
        if v in self.modes and v != self._v:
            self._v = v
            self.changed.emit(v)


class _RadioDot(QWidget):
    def __init__(self):
        super().__init__()
        self.on = False
        self.setFixedSize(16, 16)

    def set_on(self, on):
        self.on = bool(on)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = QColor(theme.ACCENT)
        p.setPen(QPen(a if self.on else QColor(theme.TEXT3), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(2, 2, 12, 12))
        if self.on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(a)
            p.drawEllipse(QRectF(5, 5, 6, 6))


class _ModeRow(QFrame):
    """One of the ways to give the area, as a line (Cubiomes Viewer's three lines,
    in its order). The line of the current mode is lit and its fields live; a
    click anywhere on another line chooses that one."""

    def __init__(self, mode, label, area):
        super().__init__()
        self.setObjectName("AreaRow")
        self.mode, self.area = mode, area
        self.v = QVBoxLayout(self); self.v.setContentsMargins(9, 5, 7, 5); self.v.setSpacing(5)
        self.top = QHBoxLayout(); self.top.setSpacing(8)
        self.radio = _RadioDot(); self.radio.setVisible(len(area.modes) > 1)
        self.lab = body(label, wrap=False)
        self.top.addWidget(self.radio); self.top.addWidget(self.lab); self.top.addStretch(1)
        self.v.addLayout(self.top)
        self.fields = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def add(self, w):
        self.top.addWidget(w); self.fields.append(w)

    def set_active(self, on):
        self.setProperty("active", bool(on))
        self.radio.set_on(on)
        for w in self.fields:
            w.setEnabled(bool(on))
        self.setCursor(Qt.CursorShape.ArrowCursor if on else Qt.CursorShape.PointingHandCursor)
        self.style().unpolish(self); self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.area.choose(self.mode)
        super().mousePressEvent(e)


def _type_tile(t, size=38, isz=22):
    """The type's icon on a tile of its colour (a dot when it has no icon)"""
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr)); pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    c = QColor(type_color(t)); bg = QColor(c); bg.setAlphaF(0.17)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(bg)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    ic = type_icon(t, isz)
    o = (size - isz) / 2
    if ic is not None:
        src = ic.pixmap(isz * 2, isz * 2)
        p.drawPixmap(QRectF(o, o, isz, isz), src, QRectF(src.rect()))
    else:
        p.setBrush(c); p.drawEllipse(QRectF(size / 2 - 6, size / 2 - 6, 12, 12))
    p.end()
    return pm


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
FAMILY_COLORS = {"Structures": CATEGORY_COLORS["Structures"], "Nether": CATEGORY_COLORS["Nether"],
                 "End": CATEGORY_COLORS["End"], "Biomes": CATEGORY_COLORS["Biomes"], "Climate": "#5fb3ff",
                 "Algorithm helpers": "#b8c0d4", "Presets": "#f5b83d", "Others": CATEGORY_COLORS["World"]}


class _PickTile(QAbstractButton):
    """(14/11) A tile of the « Add a condition » panel: a family (big: its name
    and how many conditions) or one condition, on its colour. Under the mouse
    the tile takes its colour."""

    def __init__(self, icon, color, name, sub="", big=False, parent=None):
        super().__init__(parent)
        self.icon_, self.color, self.name, self.sub, self.big = icon, QColor(color), name, sub, big
        self._hv = 0.0
        self._an = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(72 if big else 50)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(120)

    def _glide(self, to):
        from . import motion
        if self._an is not None:
            try:
                self._an.stop()
            except RuntimeError:
                pass
            self._an = None
        if not motion.enabled():
            self._hv = to
            self.update()
            return
        h0 = self._hv

        def step(t):
            self._hv = h0 + (to - h0) * t
            self.update()
        self._an = motion.run(self, 140, step)

    def enterEvent(self, e):
        super().enterEvent(e)
        self._glide(1.0)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._glide(0.0)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        h = max(self._hv, 1.0 if self.hasFocus() else 0.0)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        c = self.color.name()
        p.setBrush(QColor(theme._mix(theme.BG2, c, 0.04 + 0.11 * h)))
        p.setPen(QPen(QColor(theme._mix(theme.LINE2, c, 0.10 + 0.62 * h)), 1))
        p.drawRoundedRect(r, 12, 12)
        ts = 42 if self.big else 30
        tx, ty = 13 if self.big else 10, (self.height() - ts) / 2
        bg = QColor(self.color)
        bg.setAlphaF(0.17 + 0.10 * h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(tx, ty, ts, ts), ts * 0.28, ts * 0.28)
        isz = 24 if self.big else 18
        if self.icon_ is not None:
            src = self.icon_.pixmap(isz * 2, isz * 2)
            p.drawPixmap(QRectF(tx + (ts - isz) / 2, ty + (ts - isz) / 2, isz, isz), src, QRectF(src.rect()))
        else:
            p.setBrush(self.color)
            p.drawEllipse(QRectF(tx + ts / 2 - 5, ty + ts / 2 - 5, 10, 10))
        x = tx + ts + (12 if self.big else 9)
        f = QFont(self.font())
        f.setWeight(QFont.Weight.DemiBold)
        f.setPointSizeF(10.0 if self.big else 9.25)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        if self.big:
            while p.fontMetrics().horizontalAdvance(self.name) > r.right() - x - 6 and f.pointSizeF() > 8.6:
                f.setPointSizeF(f.pointSizeF() - 0.25)       # a long family name is set a little smaller
                p.setFont(f)
            fm = p.fontMetrics()
            p.drawText(QRectF(x, ty + 1, r.right() - x - 6, fm.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(self.name, Qt.TextElideMode.ElideRight, int(r.right() - x - 6)))
            f2 = QFont(self.font())
            f2.setPointSizeF(8.25)
            p.setFont(f2)
            p.setPen(QColor(theme._mix(theme.TEXT3, c, 0.25 + 0.45 * h)))
            p.drawText(QRectF(x, ty + ts - p.fontMetrics().height() - 1, r.right() - x - 26,
                              p.fontMetrics().height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.sub)
            arrow = QColor(c)                   # the arrow comes with the mouse
            arrow.setAlphaF(h)
            pen = QPen(arrow, 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            ax, ay = r.right() - 15 + 3.0 * h, ty + ts - 8
            p.drawPolyline([QPointF(ax - 3, ay - 4.5), QPointF(ax + 1.5, ay), QPointF(ax - 3, ay + 4.5)])
        else:
            p.drawText(QRectF(x, 3, r.right() - x - 6, self.height() - 6),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) |
                       int(Qt.TextFlag.TextWordWrap), self.name)


class TypePicker(QWidget):
    """Add a condition (03/10): first the family, then its conditions; typing in
    the search box lists every matching one."""
    chosen = Signal(str)
    cancelled = Signal()

    # (family, condition keys or None = every other non-structure one, icon key)
    GROUPS = [("Structures", "structures", "village"),
              ("Nether", ["fortress", "bastion", "ruined_portal_nether", "nether_biomes"],
               "fortress"),
              ("End", ["end_city", "end_gateway", "end_biomes", "elytra_spawn"], "end_city"),
              ("Biomes", ["biome_filter", "biome_sample", "biome_center"], "biome_filter"),
              ("Climate", ["climate_minmax", "climate_center", "noise_sample", "crunchiness",
                           "height", "precise_y"], "climate_center"),
              ("Algorithm helpers", ["spiral", "logic_or", "logic_not", "scale_to_nether",
                                     "scale_to_overworld"], "spiral"),
              ("Presets", ["spawn116_preset", "mansion_spawn116_preset", "dungeon_spawn116_preset",
                          "lavalake_spawn116_preset", "elytra_spawn_preset"], "spawn"),
              ("Others", None, "slime")]

    # (15/11) the structures in a logical order: the common land ones first (the
    # village was among the last), then the sea, underground, the rare ones and the
    # 1.16 features (any structure not listed: after, in its usual order)
    STRUCT_ORDER = ["village", "first_stronghold", "desert_pyramid", "jungle_temple", "igloo", "hut",
                    "outpost", "mansion", "abandoned_camp", "monument", "shipwreck", "ocean_ruin",
                    "treasure", "ruined_portal", "mineshaft", "ancient_city", "trial_chambers",
                    "trail_ruins", "desert_well", "dungeon", "lava_lake"]

    @classmethod
    def _members(cls, keys):
        live = [t for t in CONDITION_TYPES if not t.get("disabled")]
        if keys == "structures":
            ts = [t for t in live if t["category"] == "Structures"]
            pos = {k: i for i, k in enumerate(cls.STRUCT_ORDER)}
            return sorted(ts, key=lambda t: (pos.get(t["key"], len(pos)), ts.index(t)))
        if keys is not None:
            by = {t["key"]: t for t in live}
            return [by[k] for k in keys if k in by]
        named = {k for _n, ks, _i in cls.GROUPS if isinstance(ks, list) for k in ks}
        return [t for t in live if t["category"] not in ("Structures", "Nether", "End")
                and t["key"] not in named]

    def __init__(self, mc_ok=None, parent=None):
        super().__init__(parent)
        from .motion import FadeStack
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
        self.stack = FadeStack(slide=16, ms=190)        # (14/11) families ↔ conditions glide
        v.addWidget(self.stack, 1)
        home = QWidget(); hl = QGridLayout(home); hl.setContentsMargins(0, 4, 0, 0); hl.setSpacing(8)
        self.pages = {}
        i = 0
        for name, keys, ikey in self.GROUPS:
            types = self._members(keys)
            if not types:
                continue
            card = _PickTile(type_icon(ikey, 24), FAMILY_COLORS.get(name, "#9aa3b8"), name,
                             f"{len(types)} conditions", big=True)
            card.clicked.connect(lambda _=False, g=name: self._open(g))
            hl.addWidget(card, i // 2, i % 2)
            i += 1
            self.pages[name] = self._type_page(types)
        hl.setRowStretch(i // 2 + 1, 1)
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
        sc.groups = []                             # (title or None, grid, [(tile, search key)])
        groups = []
        if flat:                                   # search: grouped by family
            have = {t["key"] for t in types}
            for name, keys, _i in self.GROUPS:
                groups.append((name, [t for t in self._members(keys) if t["key"] in have]))
        else:
            groups.append((None, types))
        for gname, ts in groups:
            if not ts:
                continue
            h = None
            if gname:
                h = section_title(gname); lay.addWidget(h)
            grid = QGridLayout(); grid.setContentsMargins(0, 0, 0, 0); grid.setSpacing(6)
            grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
            tiles = []
            for i, t in enumerate(ts):
                b = _PickTile(type_icon(t["key"], 18), type_color(t["key"]), t["name"])
                if t.get("desc"):                  # (15/11) no tooltip repeating the name
                    b.setToolTip(t["desc"])
                b.clicked.connect(lambda _=False, k=t["key"]: self.chosen.emit(k))
                grid.addWidget(b, i // 2, i % 2)
                tiles.append((b, t["name"].lower() + " " + t["key"] + " " + (gname or "").lower()))
            lay.addLayout(grid)
            sc.groups.append((h, grid, tiles))
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
        for h, grid, tiles in self.results.groups:
            shown = [b for b, key in tiles if q in key]
            for b, _key in tiles:                  # the matching tiles, packed two by two
                grid.removeWidget(b)
                b.setVisible(b in shown)
            for i, b in enumerate(shown):
                grid.addWidget(b, i // 2, i % 2)
            if h is not None:
                h.setVisible(bool(shown))
        self.stack.setCurrentWidget(self.results)
        self.title.setText("Search")
        self.back.setVisible(True)

    def showEvent(self, e):
        super().showEvent(e)
        self.search.clear(); self.search.setFocus()
        self._home()


# ═════════════════════════════════════════════════════════════════════════════
def _round_swatch(color, size=12):
    from PySide6.QtGui import QPainter
    pm = QPixmap(size, size); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QColor(0, 0, 0, 90)); p.setBrush(QColor(color))
    p.drawEllipse(1, 1, size - 2, size - 2); p.end()
    return QIcon(pm)


class BiomePicker(QDialog):
    """Biome chooser (08/10 redesign): one card per family (Ocean, Jungle…)
    with its own All / None and a selected count, biomes as coloured chips that
    toggle on click, a search that keeps the families, a running total.
    single=True: one biome, a click selects it, a double-click confirms."""

    def __init__(self, selected, single=False, mc=28, dim=0, parent=None):
        super().__init__(parent)
        from biome_catalog import biome_display_name, biome_swatch_hex, dim_biomes_for
        from .widgets import FlowLayout
        self.setWindowTitle("Choose a biome" if single else "Choose biomes")
        self.resize(760, 640)
        self.single = single
        sel = set(selected if isinstance(selected, (list, tuple, set)) else [selected])
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(10)
        top = QHBoxLayout(); top.setSpacing(10)
        t = QLabel("Choose a biome" if single else "Choose biomes"); t.setObjectName("PanelTitle")
        top.addWidget(t)
        self.count = QLabel(); self.count.setObjectName("Chip"); self.count.setProperty("state", "off")
        top.addWidget(self.count)
        if not single:          # (08/10) every biome at once, beside the total
            for text, on, tip in (("All", True, "Select every biome (the ones shown by the search)"),
                                  ("None", False, "Clear the selection (the ones shown by the search)")):
                b = QPushButton(text); b.setProperty("kind", "ghost"); b.setToolTip(tip)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, o=on: self._set(
                    [c[0] for c in self.chips if not c[0].isHidden()], o))
                top.addWidget(b)
        top.addStretch(1)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search a biome or a family…")
        self.search.setClearButtonEnabled(True); self.search.setMinimumWidth(260)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search)
        v.addLayout(top)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget(); grid = QGridLayout(inner); grid.setContentsMargins(0, 0, 6, 0)
        grid.setHorizontalSpacing(10); grid.setVerticalSpacing(10)
        self.chips = []                     # (button, bid, search key, card)
        self.cards = []                     # (card, title label, chips)
        self.mc = mc
        groups, cur = [], None
        listed = dim_biomes_for(mc, dim)           # (17/10) the biomes of THIS version
        if dim:                                    # (18/10) Nether / End: small dialog
            self.resize(560, 300)
        have = {b for b, _n in listed if b is not None}
        gone = [b for b in sel if isinstance(b, int) and b not in have]
        if gone:                                   # chosen before, absent from this version
            listed = list(listed) + [(None, "Not in this version")] + \
                [(b, biome_display_name(b)) for b in gone]
        for bid, name in listed:
            if bid is None:
                cur = (name, []); groups.append(cur)
                continue
            if cur is None:
                cur = ("Biomes", []); groups.append(cur)
            cur[1].append((bid, name))
        from PySide6.QtWidgets import QButtonGroup
        self._excl = QButtonGroup(self) if single else None
        if self._excl is not None:
            self._excl.setExclusive(True)
        for gi, (gname, items) in enumerate(groups):
            card = QFrame(); card.setObjectName("Card")
            cv = QVBoxLayout(card); cv.setContentsMargins(12, 10, 12, 10); cv.setSpacing(8)
            head = QHBoxLayout(); head.setSpacing(6)
            title = QLabel(gname.upper()); title.setObjectName("SectionTitle")
            head.addWidget(title); head.addStretch(1)
            cnt = QLabel(); cnt.setObjectName("Muted")
            head.addWidget(cnt)
            mine = []
            if not single:
                for text, on in (("All", True), ("None", False)):
                    b = QPushButton(text); b.setProperty("kind", "ghost")
                    b.setStyleSheet("padding: 1px 8px; min-height: 14px; font-size: 8pt;")
                    b.setCursor(Qt.CursorShape.PointingHandCursor)
                    b.clicked.connect(lambda _=False, m=mine, o=on: self._set(m, o))
                    head.addWidget(b)
            cv.addLayout(head)
            flow = FlowLayout(h_spacing=6, v_spacing=6)
            for bid, name in items:
                b = QPushButton(_round_swatch(biome_swatch_hex(bid), 12), " " + name)
                b.setObjectName("BiomeChip"); b.setCheckable(True)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                ok = bid in have
                if not ok:
                    b.setEnabled(False)
                    b.setToolTip(f"{name}\nUnavailable: not generated in this Minecraft version.")
                b.setChecked(bid in sel and ok)
                if single:
                    self._excl.addButton(b)
                    b.installEventFilter(self)
                b.toggled.connect(lambda _on: self._refresh())
                flow.addWidget(b)
                mine.append(b)
                self.chips.append((b, bid, (name + " " + gname).lower(), card))
            cv.addLayout(flow)
            self.cards.append((card, cnt, mine))
            grid.addWidget(card, gi // 2, gi % 2)
        grid.setRowStretch(grid.rowCount(), 1)
        sc.setWidget(inner)
        v.addWidget(sc, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        c = QPushButton("Cancel"); c.clicked.connect(self.reject); row.addWidget(c)
        ok = QPushButton("OK"); ok.setProperty("kind", "primary"); ok.clicked.connect(self.accept)
        row.addWidget(ok)
        v.addLayout(row)
        self._refresh()

    def eventFilter(self, obj, ev):                  # single: double-click = OK
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Type.MouseButtonDblClick and self.single:
            obj.setChecked(True)
            self.accept()
            return True
        return False

    def _set(self, buttons, on):
        for b in buttons:
            if b.isEnabled() and not b.isHidden():
                b.blockSignals(True); b.setChecked(on); b.blockSignals(False)
        self._refresh()

    def _refresh(self):
        n = 0
        for card, cnt, mine in self.cards:
            k = sum(1 for b in mine if b.isChecked())
            n += k
            avail = sum(1 for b in mine if b.isEnabled())
            cnt.setText(f"{k} / {avail}" if not self.single else "")
        if self.single:
            cur = self.value()
            from biome_catalog import biome_display_name
            self.count.setText(biome_display_name(int(cur), self.mc) if cur is not None else "none")
        else:
            self.count.setText(f"{n} selected")

    def _filter(self, text):
        q = text.strip().lower()
        for card, _cnt, mine in self.cards:
            vis = 0
            for b in mine:
                key = next(c[2] for c in self.chips if c[0] is b)
                show = not q or q in key
                b.setVisible(show)
                vis += show
            card.setVisible(vis > 0)

    def value(self):
        if self.single:
            b = next((c for c in self.chips if c[0].isChecked()), None)
            return b[1] if b else None
        return [int(bid) for b, bid, _k, _c in self.chips if b.isChecked()]


# ═════════════════════════════════════════════════════════════════════════════
STEP_MAX = 1 << 30          # spiral step (08/10: noise tiling, 131 072 and more)


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
        self.parent_choices = None         # [(uid, label)] for "Related to" (08/10)
        self.parent_uid = 0
        self.parent_lookup = None          # uid -> condition dict (10/10 ter: height link)
        self.mc = 28
        self._building = False
        # live preview while typing, debounced (no redraw per key stroke)
        self._preview_timer = QTimer(self, singleShot=True, interval=60,
                                     timeout=self._emit_preview)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        # header
        hdr = QFrame(); hdr.setObjectName("FormHeader")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(14, 12, 10, 10); hl.setSpacing(8)
        self.dot = QLabel(); self.dot.setFixedSize(38, 38)
        hl.addWidget(self.dot)
        col = QVBoxLayout(); col.setSpacing(0)
        self.title = QLabel(); self.title.setObjectName("PanelTitle")
        self.cat = QLabel(); self.cat.setObjectName("FormCat")               # the category, then the type
        self.subtitle = QLabel(); self.subtitle.setObjectName("Muted")       # a note (several conditions edited)
        self.subtitle.setVisible(False)
        col.addWidget(self.cat); col.addWidget(self.title); col.addWidget(self.subtitle)
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
        self.ok = QPushButton("OK"); self.ok.setProperty("kind", "primary")
        self.ok.setMinimumWidth(84)
        self.ok.clicked.connect(self._save)
        foot.addWidget(self.ok); foot.addWidget(cancel)
        v.addLayout(foot)

    # ── building ────────────────────────────────────────────────────────────
    def edit(self, cond, parent_label=None, mc=28, is_new=False, parent_choices=None,
             parent_uid=0):
        """Show the form for `cond` (engine-format dict). parent_choices: the
        conditions it may be related to, [(uid, label)] — a "Related to" field
        then lets the parent be changed (08/10)."""
        if is_new:
            self.plan_gpu = False
        self.cond = copy.deepcopy(cond)
        self.parent_label = parent_label
        self.parent_choices = parent_choices
        self.parent_uid = int(parent_uid or 0)
        self.mc = mc
        self.is_new = is_new
        if is_new and self._same_type_parent():
            self.cond["ignore_ref"] = True
        self._build()
        self._emit_preview()

    def _build(self):
        self._building = True
        self._range_bars = []
        self._bands = []
        c = self.cond
        t = c.get("type", "")
        info = TYPE_INFO.get(t, {})
        self.pad = None
        self.dot.setPixmap(_type_tile(t))
        self.title.setText(type_name(t))
        cat = info.get("category", "Imported")
        self.cat.setText(cat.upper())
        self.cat.setStyleSheet(f"color: {CATEGORY_COLORS.get(cat, theme.TEXT3)}; font-size: 7.5pt; "
                               "font-weight: 700; letter-spacing: 1.2px;")
        self.subtitle.clear(); self.subtitle.setVisible(False)
        self.change_btn.setVisible(t in TYPE_INFO)
        self.err.clear()
        w = QWidget(); lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 10, 14, 14); lay.setSpacing(8)
        self.w = {}
        self.pw = {}
        has_area = t not in NO_AREA and not info.get("no_area")

        # (14/11) Cubiomes Viewer's order: the type and its notes, General, Location,
        # the type's own block, OK / Cancel
        if info.get("desc"):
            lay.addWidget(body(info["desc"], muted=True, small=True))
        dim_hint = self._dim_hint(c)
        if dim_hint:                           # (18/10) Nether / End coordinates
            warn = body(dim_hint, small=True)
            warn.setStyleSheet(f"color: {theme.WARN};")
            lay.addWidget(warn)
        if info.get("disabled"):               # e.g. abandoned camp (temporarily hidden)
            warn = body(info["disabled"] + " This one is kept as it was loaded.", small=True)
            warn.setStyleSheet(f"color: {theme.WARN};")
            lay.addWidget(warn)
        if t in IMPORTED_TYPES:
            lay.addWidget(body("Imported from a cubiomes-viewer session. Its parameters are "
                               "kept as they are; the area and label can be edited here.",
                               muted=True, small=True))

        # General
        lay.addSpacing(2); lay.addWidget(section_title("General"))
        self.w["label"] = QLineEdit(c.get("label", "")); self.w["label"].setPlaceholderText(type_name(t))
        self.w["label"].textChanged.connect(self._changed)
        lay.addLayout(_field("Label", self.w["label"]))
        self.w["enabled"] = QCheckBox("Condition enabled"); self.w["enabled"].setChecked(c.get("enabled", True))
        self.w["enabled"].toggled.connect(self._changed)
        lay.addWidget(self.w["enabled"])
        if not has_area:                       # no Location block: what it is placed around goes here
            self._build_parent(lay, "Relative to")

        # Location
        if has_area:                           # (whole world: nothing to set)
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
        self.vg = None
        if t == "village":                     # (05/11) its own panel
            self.pw = {}
            lay.addSpacing(4)
            self._build_village(lay, c)
            specs = {}
        self.rp = None
        if t == "ruined_portal":               # (07/11) its own panel
            self.pw = {}
            lay.addSpacing(4)
            self._build_ruined_portal(lay, c)
            specs = {}
        if specs:
            lay.addSpacing(4); lay.addWidget(section_title("Details"))
            self._build_params(lay, c, specs)
            if t == "climate_center":            # cells → blocks, live
                self._ca_hint = body("", muted=True, small=True)
                lay.addWidget(self._ca_hint)
        lay.addStretch(1)
        self.scroll.setWidget(w)
        # "Outside range" ↔ the range bar shows the kept part outside (08/10)
        inv = self.pw.get("invert")
        if inv is not None and self._range_bars and inv[0] == "bool":
            def _inv(on):
                for rs in self._range_bars:
                    rs.set_inverted(on)
            inv[1].toggled.connect(_inv)
            _inv(inv[1].isChecked())
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
        self.w["area"] = _AreaMode(modes, cur, self)
        self.w["area"].changed.connect(self._area_mode_changed)

        # (14/11) the area drawn to scale around its reference, shaped by hand
        ic = type_icon(t, 15)
        self.pad = AreaPad(type_color(t), ic.pixmap(30, 30) if ic is not None else None)
        self.pad.radiusChanged.connect(lambda v: self.w["radius"].setValue(v))
        self.pad.squareChanged.connect(lambda v: self.w["square"].setValue(v))
        self.pad.boxChanged.connect(self._pad_box)
        lay.addWidget(self.pad)

        # Instances within area  (+ Ignore reference)
        ignore = t in STRUCTURE_KEYS and bool(self.parent_label)
        if t not in NO_INSTANCES or ignore:
            row = QHBoxLayout(); row.setSpacing(8)
            if t not in NO_INSTANCES:
                tip = ("Minimum number of instances inside the area.\n"
                       "0 = exclusion: NO instance may be inside the area.")
                self.w["instances"] = _spin(0, 1024, int(c.get("instances", 1)), 1)
                self.w["instances"].setFixedWidth(74)
                lab = body("Instances within area", wrap=False)
                lab.setToolTip(tip); self.w["instances"].setToolTip(tip)
                row.addWidget(lab); row.addWidget(self.w["instances"])
                self.w["instances"].valueChanged.connect(self._changed)
            row.addStretch(1)
            if ignore:
                self.w["ignore_ref"] = QCheckBox("Ignore reference")
                self.w["ignore_ref"].setToolTip(
                    "The parent's own instance is not counted: ticked, it asks for ANOTHER one\n"
                    "('another village near this village'). Unticked, a condition linked to one\n"
                    "of its own type is met by the parent itself (Cubiomes Viewer's default).\n"
                    "Ticked by default when the parent is of the same type.")
                self.w["ignore_ref"].setChecked(bool(c.get("ignore_ref")))
                self.w["ignore_ref"].toggled.connect(self._changed)
                row.addWidget(self.w["ignore_ref"])
            lay.addLayout(row)

        # Within radial distance · Within centred square of side · Custom
        r = int(c.get("radial", 0) or 0)
        if not r and int(c.get("rmax", 0) or 0) > 0:
            r = int(c["rmax"]) - 1
        self.w["radius"] = _spin(0, 30_000_000, r, 16, " blocks")
        self.w["square"] = _spin(0, 60_000_000, int(c.get("square_side", 0) or
                                                    (c.get("x2", 0) - c.get("x1", 0))), 16, " blocks")
        for k in ("x1", "z1", "x2", "z2"):
            self.w[k] = _spin(val=int(c.get(k, 0)))
        self._rows = {}
        rows = QVBoxLayout(); rows.setSpacing(3)
        for key, mode, label in (("radius", "Radius", "Within radial distance"),
                                 ("square", "Square", "Within centred square of side")):
            self.w[key].setFixedWidth(128)
            self.w[key].valueChanged.connect(self._changed)
            if mode in modes:
                mr = _ModeRow(mode, label, self.w["area"])
                mr.add(self.w[key])
                self._rows[key] = mr; rows.addWidget(mr)
        if "Box" in modes:
            mr = _ModeRow("Box", "Custom", self.w["area"])
            vis = QPushButton("From visible"); vis.setProperty("kind", "ghost")
            vis.setToolTip("Use the part of the map that is on screen as the box.")
            vis.clicked.connect(lambda: self.pickRequested.emit("visible"))
            mr.top.addWidget(vis)                # stays live: it also chooses « Custom »
            g = QGridLayout(); g.setContentsMargins(24, 0, 0, 2)
            g.setHorizontalSpacing(6); g.setVerticalSpacing(4)
            for i, k in enumerate(("x1", "z1", "x2", "z2")):
                kl = body(k.upper(), muted=True, wrap=False)
                g.addWidget(kl, i // 2, (i % 2) * 2)
                g.addWidget(self.w[k], i // 2, (i % 2) * 2 + 1)
                mr.fields += [kl, self.w[k]]
            g.setColumnStretch(1, 1); g.setColumnStretch(3, 1)
            mr.v.addLayout(g)
            mr.setToolTip("Two opposite corners of the box, in any order.")
            self._rows["box"] = mr; rows.addWidget(mr)
            self.w["box"] = mr
        for k in ("x1", "z1", "x2", "z2"):
            self.w[k].valueChanged.connect(self._changed)
        lay.addLayout(rows)
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
        self._build_dy(lay, c)
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
        self._build_parent(lay, "Location is relative to")
        self._area_mode_changed(self.w["area"].value(), emit=False)

    def _build_parent(self, lay, label):
        """What the condition is placed around: another condition or the world origin"""
        if self.parent_choices is None:
            return
        pc = QComboBox()
        pc.addItem("World origin (0, 0)", 0)
        for u, lab in self.parent_choices:
            pc.addItem(lab, int(u))
        i = pc.findData(self.parent_uid)
        pc.setCurrentIndex(i if i >= 0 else 0)
        pc.setToolTip("The condition this one is placed around (its area is centred on\n"
                      "where that condition is satisfied), or the world origin.")
        pc.currentIndexChanged.connect(self._parent_changed)
        self.w["parent"] = pc
        if len(label) > 14:                    # the long label sits above its list
            lay.addWidget(body(label, wrap=False))
            lay.addWidget(pc)
        else:
            lay.addLayout(_field(label, pc))

    def _pad_box(self, x1, z1, x2, z2):
        was, self._building = self._building, True
        try:
            for k, v in (("x1", x1), ("z1", z1), ("x2", x2), ("z2", z2)):
                self.w[k].setValue(v)
        finally:
            self._building = was
        self._changed()

    def _sync_pad(self):
        pad = getattr(self, "pad", None)
        if pad is None or "area" not in self.w:
            return
        ref = self.parent_label or "0, 0"
        if "at_x" in self.w and not self.parent_label:
            ref = f"{self.w['at_x'].value()}, {self.w['at_z'].value()}"
        try:
            pad.set_state(self.w["area"].value(), self.w["radius"].value(), self.w["square"].value(),
                          tuple(self.w[k].value() for k in ("x1", "z1", "x2", "z2")),
                          self.w["instances"].value() if "instances" in self.w else None, ref)
        except RuntimeError:
            pass

    def _same_type_parent(self):
        """(10/10) Linked to a condition of its OWN type ("village near a
        village"): as in Cubiomes Viewer the parent instance itself would count,
        so one village was enough — 'Ignore the reference structure' is then
        ticked by default, which asks for ANOTHER one."""
        par = self._parent_cond()
        t = self.cond.get("type")
        return bool(par is not None and t in STRUCTURE_KEYS and par.get("type") == t)

    def _dim_hint(self, c):
        """A Nether / End condition linked to a parent whose positions are of
        another dimension (or the reverse): say how cubiomes-viewer handles it."""
        from condition_types import DIM_NAMES, cond_dim
        from .session_model import coord_dim
        if not self.parent_uid or self.parent_lookup is None:
            return ""
        try:
            pd = coord_dim(self.parent_lookup, self.parent_uid)
        except Exception:
            return ""
        me = cond_dim(c)
        if pd is None or pd == me or c.get("type") in ("scale_to_nether", "scale_to_overworld",
                                                       "logic_or", "logic_not", "spiral"):
            return ""
        if {pd, me} == {0, -1}:
            fix = ("put a « Scale to Nether » (÷ 8) between them" if me == -1 else
                   "put a « Scale to Overworld » (× 8) between them")
            return (f"Its parent gives {DIM_NAMES[pd]} coordinates and this condition uses "
                    f"{DIM_NAMES[me]} ones: the same numbers are used as they are (like "
                    f"cubiomes-viewer). To link the two places through a portal, {fix}.")
        return (f"Its parent gives {DIM_NAMES[pd]} coordinates and this condition uses "
                f"{DIM_NAMES[me]} ones: the same numbers are used as they are (like "
                f"cubiomes-viewer).")

    def _parent_cond(self):
        if not self.parent_uid or self.parent_lookup is None:
            return None
        try:
            return self.parent_lookup(self.parent_uid)
        except Exception:
            return None

    def _build_dy(self, lay, c):
        """(10/10 ter) Height link: a 1.16 dungeon / lava lake placed around a
        parent that has a Y (dungeon spawner, top lava layer, stronghold end
        portal) may also be limited in height: |its Y − the parent's Y| ≤ N."""
        from custom_search import Y_CHILD_TYPES, y_tracking
        par = self._parent_cond()
        if c["type"] not in Y_CHILD_TYPES or par is None or not y_tracking(par):
            return
        what = {"dungeon": "its spawner", "lava_lake": "its top lava layer"}.get(par.get("type"),
                                                                                  "its end portal")
        row = QWidget(); hl = QHBoxLayout(row); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(8)
        cb = QCheckBox("Height to the parent ≤")
        cb.setChecked(bool(c.get("dy_on")))
        sp = _spin(0, 255, int(c.get("dy_max", 8) or 8), 1, " blocks")
        sp.setEnabled(cb.isChecked())
        mine = "dungeons" if c["type"] == "dungeon" else "lava lakes"
        tip = (f"The parent has a Y ({what}). Ticked: only the {mine} whose Y\n"
               "is at most this many blocks above or below it count — on top of the\n"
               "distance above (the horizontal one).")
        cb.setToolTip(tip); sp.setToolTip(tip)
        cb.toggled.connect(sp.setEnabled)
        cb.toggled.connect(self._changed)
        sp.valueChanged.connect(self._changed)
        hl.addWidget(cb); hl.addWidget(sp, 1)
        lay.addWidget(row)
        self.w["dy_on"], self.w["dy_max"] = cb, sp

    def _area_mode_changed(self, mode, emit=True):
        for k, row in self._rows.items():      # the three lines stay, the current one is lit
            row.set_active({"Radius": "radius", "Square": "square", "Box": "box"}[mode] == k)
        self._sync_pad()
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
        # (14/11 bis) both chosen on a picture: the rings of the 128 strongholds (which ones) and
        # the stronghold itself (where the position is taken). The two controls above stay the
        # truth, hidden; the scene only drives them.
        from .param_visuals import ShScene, SwitchCard
        scene = sh["scene"] = ShScene(sh["scope"].value(), sh["anchor"].value())
        for seg in (sh["anchor"], sh["scope"]):
            seg.setParent(scene); seg.hide()

        def _scene_pick(seg, v, scene=scene, sh=sh):
            seg.set_value(v)
            scene.set_state(sh["scope"].value(), sh["anchor"].value())
            seg.changed.emit(v)
        scene.scopeChosen.connect(lambda v: _scene_pick(sh["scope"], v))
        scene.anchorChosen.connect(lambda v: _scene_pick(sh["anchor"], v))
        lay.addWidget(scene)
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
        sh["eyes_on"] = SwitchCard("Eyes in the portal", "eye")
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
        # (06/11) 1.18+: the fast prefilter (portal room alone in its chunk) misses seeds
        sh["full"] = QCheckBox("Complete search (slower)")
        sh["full"].setToolTip("1.18+, 6 to 11 eyes: checks every seed exactly (CPU).\n"
                              "Unticked: ~30× faster, but finds about 1 matching seed in 3.")
        sh["full"].setChecked(bool(p.get("eye_full")))
        sh["full"].toggled.connect(self._changed)
        bl.addWidget(sh["portal"]); bl.addWidget(sh["cmp"]); bl.addWidget(sh["gpu"])
        bl.addWidget(sh["full"])
        sh["eyes_box"] = box
        lay.addWidget(box)
        # top Y: the same gauge as the Stronghold page
        from .stronghold_visuals import HeightGauge
        sh["maxy_on"] = SwitchCard("Top Y", "height")
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
        sh["corr"] = SwitchCard("Broken portal", "broken")
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
        sh["full"].setVisible(mc >= 22)
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

    # ── village (05/11) ─────────────────────────────────────────────────────
    def _build_village(self, lay, c):
        """The village's type (cards), abandoned or not, and its houses and forges
        (histograms of 100 000 villages with two handles, and their rarity)."""
        from .village_visuals import CountRange, VillageTypePicker, histogram, rarity_text, share
        import village_filter as vf
        p = c.get("params", {}) or {}
        vg = self.vg = {}
        from .village_visuals import stats_info
        hdr = QHBoxLayout(); hdr.setSpacing(6)
        hdr.addWidget(section_title("Village")); hdr.addStretch(1)
        st = QLabel("ⓘ  Stats"); st.setObjectName("Muted")
        st.setToolTip(stats_info())
        st.setCursor(Qt.CursorShape.WhatsThisCursor)
        hdr.addWidget(st)
        lay.addLayout(hdr)
        vg["types"] = VillageTypePicker(p.get("types") or [])
        lay.addWidget(vg["types"])
        vg["variant"] = Segmented(["Any", "Not abandoned", "Abandoned"],
                                  {"abandoned": "Abandoned", "not_abandoned": "Not abandoned"}.get(
                                      p.get("variant", "any"), "Any"))
        vg["variant"].buttons["Abandoned"].setToolTip("A zombie village (2 % of the villages): "
                                                      "zombie villagers, broken houses.")
        # (14/11 bis) … as three pictures; the control above stays the truth, hidden
        from .param_visuals import SwitchCard, VariantPicker
        vp = vg["variant_pick"] = VariantPicker(vg["variant"].value())
        vg["variant"].setParent(vp); vg["variant"].hide()

        def _variant(v, vp=vp, vg=vg):
            vg["variant"].set_value(v)
            vp.set_value(v)
            vg["variant"].changed.emit(v)
        vp.chosen.connect(_variant)
        lay.addWidget(vp)

        def _range_block(key, title, n, unit, tip):
            lo, hi = vf.bounds(p, key)
            head = QHBoxLayout(); head.setSpacing(8)
            cb = SwitchCard(title, "house" if key == "houses" else "forge")
            cb.setChecked(lo is not None or hi is not None)
            cb.setToolTip(tip)
            head.addWidget(cb, 1)
            rar = QLabel(""); rar.setObjectName("Muted")
            head.addWidget(rar)
            lay.addSpacing(6); lay.addLayout(head)
            box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 2, 0, 0); bl.setSpacing(6)
            cr = CountRange(n, lo or 0, hi, unit)
            cr.setToolTip(tip)
            bl.addWidget(cr)
            row = QHBoxLayout(); row.setSpacing(8)
            # (05/11) plain fields: a spin box with « Min » inside appended the typed
            # digits instead of replacing them
            from PySide6.QtGui import QIntValidator
            mn = QLineEdit("" if not lo else str(lo)); mn.setPlaceholderText("0")
            mx = QLineEdit("" if hi is None else str(hi)); mx.setPlaceholderText("∞")
            for w_ in (mn, mx):
                w_.setValidator(QIntValidator(0, 999, w_))
                w_.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(body("Min", muted=True, wrap=False))
            row.addWidget(mn, 1)
            row.addSpacing(6); row.addWidget(body("Max", muted=True, wrap=False))
            row.addWidget(mx, 1)
            bl.addLayout(row)
            lay.addWidget(box)
            vg[key] = dict(cb=cb, cr=cr, mn=mn, mx=mx, box=box, rar=rar, n=n)

            def _from_bar(a, b, mn=mn, mx=mx):
                for w_, v in ((mn, "" if not a else str(a)), (mx, "" if b is None else str(b))):
                    w_.blockSignals(True); w_.setText(v); w_.blockSignals(False)
                self._sync_village(); self._changed()

            def _from_fields(_v=None, cr=cr, mn=mn, mx=mx):
                a = int(mn.text()) if mn.text().strip() else 0
                b = int(mx.text()) if mx.text().strip() else None
                cr.set_values(a, None if b is None else max(a, b))
                self._sync_village(); self._changed()
            cr.changed.connect(_from_bar)
            mn.textChanged.connect(_from_fields); mx.textChanged.connect(_from_fields)
            cb.toggled.connect(lambda _v: (self._sync_village(), self._changed()))
        _range_block("houses", "Houses", 40, "houses",
                     "Farms, animal pens and meeting points are not houses.")
        _range_block("smiths", "Forges with a chest", 6, "forges",
                     "The smiths' houses with a loot chest (weaponsmith, toolsmith or armorer, "
                     "depending on the village type).")
        # the summary card
        card = QFrame(); card.setObjectName("Card")
        cl = QVBoxLayout(card); cl.setContentsMargins(12, 10, 12, 10); cl.setSpacing(3)
        vg["sum_title"] = QLabel(""); vg["sum_title"].setObjectName("PanelTitle")
        vg["sum_text"] = body("", muted=True, small=True)
        cl.addWidget(vg["sum_title"]); cl.addWidget(vg["sum_text"])
        lay.addSpacing(6); lay.addWidget(card)
        vg["card"] = card
        vg["gpu"] = QCheckBox("GPU CUDA (48-bit only)")
        vg["gpu"].setChecked(bool(p.get("use_gpu")))
        vg["gpu"].setToolTip("Village positions on the GPU; houses and forges on the CPU.")
        vg["gpu"].toggled.connect(self._changed)
        lay.addWidget(vg["gpu"])
        vg["types"].changed.connect(lambda _v: (self._sync_village(), self._changed()))
        vg["variant"].changed.connect(lambda _v: self._changed())
        self._vv = (histogram, rarity_text, share)
        self._sync_village()

    def _village_bounds(self, key):
        d = self.vg[key]
        if not d["cb"].isChecked():
            return None, None
        lo = int(d["mn"].text()) if d["mn"].text().strip() else 0
        hi = int(d["mx"].text()) if d["mx"].text().strip() else None
        if hi is not None and hi < lo:
            hi = lo
        return (lo or None), hi

    def _sync_village(self):
        vg = getattr(self, "vg", None)
        if not vg:
            return
        histogram, rarity_text, share = self._vv
        types = vg["types"].value()
        for key, which in (("houses", "houses"), ("smiths", "forges")):
            d = vg[key]
            on = d["cb"].isChecked()
            d["box"].setVisible(on)
            d["cr"].set_hist(histogram(types, which, d["n"] + 1))
            lo, hi = self._village_bounds(key)
            pr = share(types, *(lo, hi) if key == "houses" else (None, None),
                       *(lo, hi) if key == "smiths" else (None, None))
            if on and pr is not None:
                pct, one, col = rarity_text(pr, types)
                d["rar"].setText(one if pr else pct)
                d["rar"].setStyleSheet(f"color: {col.name()};")
            else:
                d["rar"].setText("")
        hl, hh = self._village_bounds("houses")
        fl, fh = self._village_bounds("smiths")
        filt = any(v is not None for v in (hl, hh, fl, fh))
        if filt:
            pr = share(types, hl, hh, fl, fh)
            pct, one, col = rarity_text(pr, types)
            vg["sum_title"].setText(one if pr else pct)
            vg["sum_title"].setStyleSheet(f"color: {col.name()};")
            vg["sum_text"].setText((pct if pr else "") + (" · " if pr else "") +
                                   "checked on the real terrain")
        else:
            vg["sum_title"].setText("Any village" if not types else
                                    " / ".join(t.capitalize() for t in types) + " villages")
            vg["sum_title"].setStyleSheet("")
            vg["sum_text"].setText("")
        vg["sum_text"].setVisible(bool(vg["sum_text"].text()))

    def _village_params(self):
        vg = self.vg
        def rng(key):
            lo, hi = self._village_bounds(key)
            return None if lo is None and hi is None else (
                None if lo is None else str(lo), None if hi is None else str(hi))
        return dict(variant={"Abandoned": "abandoned", "Not abandoned": "not_abandoned"}.get(
                        vg["variant"].value(), "any"),
                    types=vg["types"].value(), houses=rng("houses"), smiths=rng("smiths"),
                    use_gpu=vg["gpu"].isChecked())

    # ── ruined portal (07/11) ──────────────────────────────────────────────
    def _build_ruined_portal(self, lay, c):
        """Where it stands, its type, the crying obsidian of its frame, and whether it
        can be completed and lit with its own chest."""
        from .ruined_portal_visuals import PlacementPicker, PortalFrame
        from .village_visuals import CountRange
        import ruined_portal_filter as rpf
        p = c.get("params", {}) or {}
        rp = self.rp = {}
        hdr = QHBoxLayout(); hdr.setSpacing(6)
        hdr.addWidget(section_title("Ruined portal")); hdr.addStretch(1)
        st = QLabel("ⓘ  Stats"); st.setObjectName("Muted")
        st.setCursor(Qt.CursorShape.WhatsThisCursor)
        hdr.addWidget(st)
        rp["stats"] = st
        lay.addLayout(hdr)
        rp["place"] = PlacementPicker(rpf.placements_of(p))
        lay.addWidget(rp["place"])
        # the frame
        lay.addSpacing(6)
        rp["frame"] = PortalFrame()
        rp["frame"].setToolTip(
            "The portal's frame: the cheapest nether portal (4 × 5 or bigger, corners not\n"
            "needed) made with the ruin's obsidian where it stands. Crying obsidian does not\n"
            "make a portal: it must be broken and replaced.")
        lay.addWidget(rp["frame"])
        from .param_visuals import SwitchCard
        rp["comp"] = SwitchCard("Completable", "portal")
        rp["comp"].setChecked(bool(p.get("completable")))
        rp["comp"].setToolTip(
            "Its chest holds enough obsidian to fill the frame's holes and replace its crying\n"
            "obsidian, and something to light it: a flint and steel, a fire charge, or flint +\n"
            "iron nuggets (9 nuggets → an ingot → a flint and steel). The chest must still be\n"
            "there (not in lava, not under the netherrack).")
        lay.addWidget(rp["comp"])
        # crying obsidian: a histogram with two handles, as the village's houses
        lo, hi = rpf.bounds(p, "crying")
        head = QHBoxLayout(); head.setSpacing(8)
        rp["cry_on"] = SwitchCard("Crying obsidian in the frame", "tear")
        rp["cry_on"].setChecked(lo is not None or hi is not None)
        rp["cry_on"].setToolTip("How many crying obsidian blocks of the frame you would have to\n"
                                "break (a diamond pickaxe) and replace.")
        head.addWidget(rp["cry_on"]); head.addStretch(1)
        rp["cry_rar"] = QLabel(""); rp["cry_rar"].setObjectName("Muted")
        head.addWidget(rp["cry_rar"])
        lay.addSpacing(2); lay.addLayout(head)
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 2, 0, 0); bl.setSpacing(6)
        rp["cry"] = CountRange(6, lo or 0, hi, "crying")
        bl.addWidget(rp["cry"])
        row = QHBoxLayout(); row.setSpacing(8)
        from PySide6.QtGui import QIntValidator
        mn = QLineEdit("" if not lo else str(lo)); mn.setPlaceholderText("0")
        mx = QLineEdit("" if hi is None else str(hi)); mx.setPlaceholderText("∞")
        for w_ in (mn, mx):
            w_.setValidator(QIntValidator(0, 99, w_))
            w_.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(body("Min", muted=True, wrap=False)); row.addWidget(mn, 1)
        row.addSpacing(6); row.addWidget(body("Max", muted=True, wrap=False)); row.addWidget(mx, 1)
        bl.addLayout(row)
        rp["cry_box"], rp["cry_mn"], rp["cry_mx"] = box, mn, mx
        lay.addWidget(box)

        def _from_bar(a, b):
            for w_, v in ((mn, "" if not a else str(a)), (mx, "" if b is None else str(b))):
                w_.blockSignals(True); w_.setText(v); w_.blockSignals(False)
            self._sync_ruined(); self._changed()

        def _from_fields(_v=None):
            a = int(mn.text()) if mn.text().strip() else 0
            b = int(mx.text()) if mx.text().strip() else None
            rp["cry"].set_values(a, None if b is None else max(a, b))
            self._sync_ruined(); self._changed()
        rp["cry"].changed.connect(_from_bar)
        mn.textChanged.connect(_from_fields); mx.textChanged.connect(_from_fields)
        # the summary
        card = QFrame(); card.setObjectName("Card")
        cl = QVBoxLayout(card); cl.setContentsMargins(12, 10, 12, 10); cl.setSpacing(3)
        rp["sum_title"] = QLabel(""); rp["sum_title"].setObjectName("PanelTitle")
        rp["sum_text"] = body("", muted=True, small=True)
        cl.addWidget(rp["sum_title"]); cl.addWidget(rp["sum_text"])
        lay.addSpacing(6); lay.addWidget(card)
        rp["gpu"] = QCheckBox("GPU CUDA (48-bit only)")
        rp["gpu"].setChecked(bool(p.get("use_gpu")))
        rp["gpu"].setToolTip("Portal positions on the GPU (without the filters above: they need\n"
                             "the terrain, so the whole seed).")
        rp["gpu"].toggled.connect(self._changed)
        lay.addWidget(rp["gpu"])
        rp["place"].changed.connect(lambda _v: (self._sync_ruined(), self._changed()))
        for k in ("comp", "cry_on"):
            rp[k].toggled.connect(lambda _v: (self._sync_ruined(), self._changed()))
        self._sync_ruined()

    def _ruined_bounds(self):
        rp = self.rp
        if not rp["cry_on"].isChecked():
            return None, None
        lo = int(rp["cry_mn"].text()) if rp["cry_mn"].text().strip() else 0
        hi = int(rp["cry_mx"].text()) if rp["cry_mx"].text().strip() else None
        if hi is not None and hi < lo:
            hi = lo
        return (lo or None), hi

    def _sync_ruined(self):
        rp = getattr(self, "rp", None)
        if not rp:
            return
        from .ruined_portal_visuals import (crying_hist, placement_shares, rarity_text, share,
                                            stats_info)
        mc = int(self.mc)
        pl, ks = rp["place"].value(), []
        comp = rp["comp"].isChecked()
        lo, hi = self._ruined_bounds()
        on = rp["cry_on"].isChecked()
        rp["cry_box"].setVisible(on)
        rp["place"].set_shares(placement_shares(mc))
        rp["stats"].setToolTip(stats_info(mc))
        rp["cry"].set_hist(crying_hist(mc, pl, ks, comp))
        rp["frame"].set_state(lo or 0, hi, comp)
        if on:
            pr = share(mc, pl, ks, lo, hi, False)
            pct, one, col = rarity_text(pr, mc)
            rp["cry_rar"].setText(one if pr else pct)
            rp["cry_rar"].setStyleSheet(f"color: {col.name()};")
        else:
            rp["cry_rar"].setText("")
        filt = bool(pl or ks or comp or lo is not None or hi is not None)
        if mc < 19:
            rp["sum_title"].setText("No ruined portals before 1.16")
            rp["sum_title"].setStyleSheet("")
            rp["sum_text"].setText("")
        elif filt:
            pr = share(mc, pl, ks, lo, hi, comp)
            pct, one, col = rarity_text(pr, mc)
            rp["sum_title"].setText(one if pr else pct)
            rp["sum_title"].setStyleSheet(f"color: {col.name()};")
            rp["sum_text"].setText((pct + " · " if pr else "") + "checked on the real terrain")
        else:
            rp["sum_title"].setText("Any ruined portal")
            rp["sum_title"].setStyleSheet("")
            rp["sum_text"].setText("")
        rp["sum_text"].setVisible(bool(rp["sum_text"].text()))
        rp["gpu"].setVisible(not filt)

    def _ruined_params(self):
        rp = self.rp
        lo, hi = self._ruined_bounds()
        cry = None if lo is None and hi is None else (
            None if lo is None else str(lo), None if hi is None else str(hi))
        return dict(placements=rp["place"].value(), kinds=[],
                    completable=rp["comp"].isChecked(), crying=cry,
                    use_gpu=rp["gpu"].isChecked())

    def set_mc(self, mc):
        """The version of the top bar changed: grey / ungrey what depends on it."""
        self.mc = int(mc)
        self._sync_stronghold()
        self._sync_ruined()

    def _stronghold_params(self):
        sh = self.sh
        eyes = sh["eyes_on"].isChecked()
        out = dict(min_eyes=int(sh["portal"].value()) if eyes else 0,
                   exact_eyes=eyes and sh["cmp"].value() == "Exactly",
                   eye_gpu=sh["gpu"].isChecked(),
                   eye_full=sh["full"].isChecked(),
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
        late = []                              # (14/11) the biome palettes close the block, as in Cubiomes Viewer
        for key, spec in specs.items():
            typ = spec.get("type")
            label = (spec.get("label") or {"blocks_count": "Minimum block count",      # (14/11) not the raw key
                                           "coverage_pct": "Required coverage"}.get(key) or key).strip()
            for emo in ("🔎", "📏", "💥"):
                label = label.replace(emo, "").strip()
            val = p.get(key, spec.get("default"))
            if typ == "bool":
                wd = QCheckBox(label); wd.setChecked(bool(val)); wd.toggled.connect(self._changed)
                if spec.get("tip"):
                    wd.setToolTip(spec["tip"])
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
                lay.addLayout(_field(label, wd, spec.get("tip")))
            elif typ == "multi_choice":
                # (18/10) several named options at once (bastion types): none = any
                from .widgets import FlowLayout
                box = QWidget(); fl = FlowLayout(box, h_spacing=10, v_spacing=4)
                chosen = set(val or [])
                wd = {}
                names = spec.get("labels", {})
                for o in spec.get("options", []):
                    cb = QCheckBox(names.get(o, str(o)))
                    cb.setChecked(o in chosen)
                    cb.toggled.connect(self._changed)
                    fl.addWidget(cb)
                    wd[o] = cb
                lay.addLayout(_field(label, box, spec.get("tip")))
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
                if spec.get("int"):                       # (05/11) counts (village houses)
                    from PySide6.QtGui import QIntValidator
                    for e in (a, b):
                        e.setValidator(QIntValidator(0, 1000, e))
                par = self.pw.get("para")
                if not spec.get("int") and par is not None and par[0] == "choice":
                    # (14/11) the range on the parameter's own scale, cut in the game's bands;
                    # the two fields stay the truth
                    from .range_visuals import ClimateBand
                    band = ClimateBand(str(par[1].currentData() or par[1].currentText()))

                    def _num(e):
                        s_ = e.text().strip().replace(",", ".").replace(" ", "")
                        try:
                            return int(round(float(s_))) if s_ else None
                        except ValueError:
                            return None

                    def _from_band(lo_, hi_, a=a, b=b):
                        for e, v in ((a, lo_), (b, hi_)):
                            e.blockSignals(True); e.setText("" if v is None else str(int(v))); e.blockSignals(False)
                        self._changed()

                    def _to_band(_t=None, a=a, b=b, band=band):
                        band.set_values(_num(a), _num(b))
                    band.rangeChanged.connect(_from_band)
                    for e in (a, b):
                        e.textChanged.connect(_to_band)
                    _to_band()
                    band.setToolTip("")
                    self._bands.append(band)
                    self._range_bars = getattr(self, "_range_bars", []) + [band]
                    lay.addWidget(body(label, wrap=False))
                    lay.addWidget(band)
                    box.setToolTip(spec.get("tip") or "Empty = no bound. Values are ×10000 like "
                                                      "cubiomes-viewer (e.g. -4500).")
                    lay.addWidget(box)
                else:
                    lay.addLayout(_field(label, box, spec.get("tip") or
                                         "Empty = no bound. Values are ×10000 like "
                                         "cubiomes-viewer (e.g. -4500)."))
            elif typ == "octave_choice":
                wd = QComboBox()
                wd.currentIndexChanged.connect(self._changed)
                lay.addLayout(_field(label, wd))
                self.pw[key] = (typ, wd, spec)
                self._fill_octaves(key, p.get(spec.get("depends_on", "para")), val)
                continue
            elif typ in ("biome_list", "biome_single"):
                # (14/11) a palette in the form itself: a tile per biome in its map colour.
                # Included and excluded biomes (Nether / End) share one palette.
                from .biome_palette import BiomePalette
                es = specs.get("biomes_excl") if key == "biomes_incl" else None
                paired = bool(es) and es.get("type") == "biome_list"
                if key == "biomes_excl" and specs.get("biomes_incl", {}).get("type") == "biome_list":
                    continue                   # shown with the included ones
                wd = BiomePalette(self.mc, int(spec.get("dim", 0) or 0), val,
                                  excl=(p.get("biomes_excl", es.get("default")) or []) if paired else None,
                                  single=(typ == "biome_single"), label=label)
                wd.changed.connect(self._changed)
                wd.searchRequested.connect(lambda k=key: self._pick_biomes(k))
                late.append(wd)
                self.pw[key] = (typ, wd, spec)
                if paired:
                    self.pw["biomes_excl"] = ("biome_list", wd.excl, es)
                continue
            elif typ == "dual_height_slider":
                # clamped: an imported cubiomes-viewer « Approx height » has no
                # bound = ±1e30, which made the spin box raise → the editor
                # could not open (01/10)
                # (10/10) the spec's own bounds (0 … 255 for the 1.16 features)
                ymn, ymx = int(spec.get("min", -64)), int(spec.get("max", 320))

                def _y(v, d, ymn=ymn, ymx=ymx):
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        return d
                    return int(max(float(ymn), min(float(ymx), v))) if v == v else d
                lo = _y(p.get("y_min"), ymn)
                hi = _y(p.get("y_max"), ymx)
                # (08/10) a two-handle bar over the two fields, like
                # cubiomes-viewer; "Outside range" paints the kept part outside
                # (14/11) … on a slice of the world (sky, sea level, stone, deepslate)
                from .range_visuals import YColumn
                box = QWidget(); bv = QVBoxLayout(box); bv.setContentsMargins(0, 0, 0, 0)
                bv.setSpacing(6)
                rs = YColumn(ymn, ymx, lo, hi)
                bl = QHBoxLayout(); bl.setContentsMargins(0, 0, 0, 0)
                a = _spin(ymn, ymx, lo, 1); b = _spin(ymn, ymx, hi, 1)

                def _from_bar(l, h, a=a, b=b):
                    for e, v in ((a, l), (b, h)):
                        e.blockSignals(True); e.setValue(v); e.blockSignals(False)
                    self._changed()

                def _from_fields(_v=None, a=a, b=b, rs=rs):
                    rs.set_values(a.value(), b.value())
                    self._changed()
                rs.rangeChanged.connect(_from_bar)
                for e in (a, b):
                    e.valueChanged.connect(_from_fields)
                bl.addWidget(a); bl.addWidget(body("to", muted=True, wrap=False)); bl.addWidget(b)
                bv.addWidget(rs); bv.addLayout(bl)
                wd = (a, b)
                self._range_bars = getattr(self, "_range_bars", []) + [rs]
                box.setToolTip(f"Drag the two lines (or the band between them), or type the bounds ({ymn} … {ymx}).")
                lay.addWidget(body(label, wrap=False))
                lay.addWidget(box)
            elif typ == "spiral_step":
                # any step in blocks (sessions use 437, 1 500 …): the old fixed list
                # fell back to 1 for those and saving wrote step 1 (01/10)
                try:
                    blocks = int(val if val is not None else 16)
                    blocks = 512 if blocks < 1 else min(STEP_MAX, blocks)   # 0 = 512 in cubiomes-viewer
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
                        sp.setRange(1, STEP_MAX // 16); sp.setValue(max(1, round(b / 16)))
                    else:
                        sp.setRange(1, STEP_MAX); sp.setValue(b)
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
                                                 "like cubiomes-viewer (which stores at most "
                                                 "65 535).\nUp to 2^30 blocks: e.g. 131 072 = the "
                                                 "period of the A octaves of weirdness "
                                                 "(noise tiling)."))
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
                g.setHorizontalSpacing(4); g.setVerticalSpacing(4)
                wd = {}
                cur = val or {}
                for i in range(6):
                    for j, ab in enumerate("AB"):
                        slot = f"{i}{ab}"
                        cb = _OctaveCell(slot); cb.setChecked(bool(cur.get(slot)))
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
        for wd in late:
            lay.addSpacing(2); lay.addWidget(wd)
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
        if key == "para":                      # the range is shown on that parameter's scale
            for band in getattr(self, "_bands", []):
                band.set_param(str(self.pw[key][1].currentData() or self.pw[key][1].currentText()))
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
            wd.setText(biome_display_name(int(val), self.mc) if val is not None else "Choose…")
        else:
            ids = list(val or [])
            if not ids:
                wd.setText(spec.get("empty_text") or "Choose biomes…")
            elif len(ids) <= 2:
                wd.setText(", ".join(biome_display_name(int(b), self.mc) for b in ids))
            else:
                wd.setText(f"{len(ids)} biomes")

    def _pick_biomes(self, key):
        typ, wd, spec = self.pw[key]
        dlg = BiomePicker(wd.property("value") or [], single=(typ == "biome_single"),
                          mc=self.mc, dim=int(spec.get("dim", 0) or 0), parent=self)
        if dlg.exec():
            v = dlg.value()
            if typ == "biome_single" and v is None:
                return
            if hasattr(wd, "load"):            # the palette shows the chooser's answer
                wd.load(v)
            else:
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
            for k in ("exact_eyes", "eye_gpu", "eye_full"):
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
                # (05/11) any two opposite corners: put in order (no error any more)
                x1, x2 = min(x1, x2), max(x1, x2)
                z1, z2 = min(z1, z2), max(z1, z2)
                c.update(area_mode=2, radial=0, square_side=0, rmax=0, x1=x1, z1=z1, x2=x2, z2=z2)
            if info.get("requires_at"):
                ax, az = self.w["at_x"].value(), self.w["at_z"].value()
                r = c.get("radial", 0)
                c.update(at_x=ax, at_z=az, x1=ax - r, z1=az - r, x2=ax + r, z2=az + r)
        if "instances" in self.w:
            c["instances"] = self.w["instances"].value()
        if "ignore_ref" in self.w:
            c["ignore_ref"] = self.w["ignore_ref"].isChecked()
        if "dy_on" in self.w:                               # (10/10 ter) height link
            c["dy_on"] = self.w["dy_on"].isChecked()
            c["dy_max"] = int(self.w["dy_max"].value())
        elif c.get("dy_on") and self.parent_lookup is not None:   # the parent has no Y any more
            c["dy_on"] = False
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
            elif typ == "multi_choice":
                params[key] = [o for o, cb in wd.items() if cb.isChecked()]
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
        if t == "village" and getattr(self, "vg", None):
            params.update(self._village_params())
            params.pop("smith_kind", None)
        if t == "ruined_portal" and getattr(self, "rp", None):
            params.update(self._ruined_params())
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
        if t in ("nether_biomes", "end_biomes"):
            inc, exc = set(params.get("biomes_incl") or []), set(params.get("biomes_excl") or [])
            if not inc and not exc:
                raise ValueError("Choose at least one biome (to find or to exclude).")
            if inc & exc:
                raise ValueError("A biome cannot be both wanted and excluded.")
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

    def _parent_changed(self, _i):
        """Another parent: keep what was typed, rebuild (texts and options that
        depend on having a parent, e.g. Exact Y's offset)."""
        pc = self.w.get("parent")
        if pc is None or self._building:
            return
        try:
            self.cond = self.value()
        except ValueError:
            pass
        self.parent_uid = int(pc.currentData() or 0)
        self.parent_label = pc.currentText() if self.parent_uid else None
        if self._same_type_parent():
            self.cond["ignore_ref"] = True
        QTimer.singleShot(0, self._rebuild_keep)

    def _rebuild_keep(self):
        self._build()
        self._emit_preview()

    # ── signals ─────────────────────────────────────────────────────────────
    def _changed(self, *_):
        if self._building:
            return
        self._sync_pad()
        self._sync_enable()
        self._preview_timer.start()

    def _emit_preview(self):
        try:
            c = self.value()
            self.err.clear()
        except ValueError as e:
            self.err.setText(str(e))
            return
        self._name_hint(c)
        self.preview.emit(c)

    def _name_hint(self, c):
        """(15/11) the empty Label field shows the name the condition will have"""
        try:
            from .session_model import _auto_on, auto_name
            self.w["label"].setPlaceholderText(auto_name(dict(c, label="")) if _auto_on()
                                               else type_name(c.get("type", "")))
        except Exception:                                   # noqa: BLE001
            pass

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
