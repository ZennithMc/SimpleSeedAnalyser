"""
qtui/palette_editor.py — noise palette editor (the classic "Noise Palette
Editor", rebuilt for the Qt interface).

  ┌ Palettes ────────┐┌ Temperature | Humidity | Continentalness | … | Height ─────────┐
  │ ▰ Aurora   built-in││ Cold → hot                                  −1.00 … +1.00    │
  │ ▰ My ocean  ✎     ││ ┌────────────── gradient (drag the stops) ───────────────┐   │
  │ …                 ││ └──────────────────────────────────────────────────────────┘   │
  │ New  Duplicate … ││  ▼   ▼        ▼     ▼                                        │
  │ Import  Export   ││ Frozen │ Cold │ Temperate │ Warm │ Hot     (1.18+ regimes)     │
  └──────────────────┘│ stops: value · colour · hex · delete                           │
                      └───────────────────────────────────────────────────────────────┘

* one gradient per noise channel, like the classic editor; the map shows the
  edited palette LIVE (overlay switched to the edited noise);
* click the bar to add a stop (colour of the gradient there), drag to move
  (Shift = snap to a regime boundary), double-click = colour, right-click or
  Delete = remove; values are shown in real noise units / blocks;
* built-in palettes are read-only: the first edit makes an editable copy;
* saved automatically (noise_palettes.json in the user config folder), with
  Undo / Redo; import / export of the classic .noisepak files.
"""
from __future__ import annotations

import copy
import os

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QIcon, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QShortcut)
from .widgets import user_path, remember_path
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

import noise_palettes as npal

from . import icons, theme
from .widgets import Segmented, body, section_title

NOISES = npal.EDITOR_NOISES
DESC = {
    "Temperature": "Cold (left) → hot (right)",
    "Humidity": "Arid (left) → saturated (right)",
    "Continentalness": "Deep ocean (left) → far inland / peaks (right)",
    "Erosion": "Rugged, mountains (left) → flat, swamps (right)",
    "Weirdness": "−1 (left) → 0 → +1 (right) · peaks & valleys, variants",
    "Height": "Terrain height in blocks: sea level is Y 63",
}
MAX_STOPS = 16
CV = 10000.0                           # climate values shown like cubiomes-viewer (×10 000)


def value_range(noise):
    """Usual (core) range; the axis extends to npal.EXTREMES (see npal.axis)."""
    return npal.DISPLAY_RANGES.get(noise, (-1.0, 1.0))


def extreme_range(noise):
    return npal.EXTREMES.get(noise, value_range(noise))


def fmt_value(noise, v):
    if noise == "Height":
        return f"Y {v:.0f}"
    return f"{v * CV:+,.0f}".replace(",", " ")


def value_of(noise, pos):
    lo, hi = value_range(noise)
    return lo + pos * (hi - lo)


def pos_of_value(noise, v):
    lo, hi = value_range(noise)
    return (v - lo) / (hi - lo)


def regimes(noise):
    """[(pos 0..1, label)] of the 1.18+ regime boundaries inside the range."""
    lo, hi = value_range(noise)
    out = []
    for thr, lab in npal.NOISE_REGIME_LABELS.get(noise, []):
        p = (thr - lo) / (hi - lo)
        out.append((p, lab))
    return out


def gradient_icon(stops, w=46, h=14) -> QIcon:
    import numpy as np
    lut = npal.lut_from_stops(stops)
    row = np.ascontiguousarray(np.repeat(lut[np.linspace(0, 255, w).astype(int)][None], h, axis=0))
    img = QImage(row.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
    pm = QPixmap(w, h); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath(); path.addRoundedRect(QRectF(0, 0, w, h), 3, 3)
    p.setClipPath(path); p.drawImage(0, 0, img); p.end()
    return QIcon(pm)


# ═════════════════════════════════════════════════════════════════════════════
class GradientBar(QWidget):
    """The gradient with draggable stop handles and the regime strip."""
    changed = Signal(bool)             # final (True at drag end / add / delete)
    selected = Signal(int)
    editColor = Signal(int)

    BAR_Y, BAR_H, HANDLE = 8, 46, 11

    def __init__(self, parent=None):
        super().__init__(parent)
        self.noise = "Temperature"
        self.stops = []
        self.sel = -1
        self._drag = None
        self.setMinimumHeight(118)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # geometry
    def _x0(self):
        return 14

    def _w(self):
        return max(10, self.width() - 28)

    # the bar is the extended colour axis: 76 % usual range, 12 % tails of
    # extremes on each side (square-root compressed) — see npal.axis
    def x_of(self, pos):
        return self._x0() + npal.u_from_pos(pos, self.noise) * self._w()

    def pos_of(self, x):
        u = min(1.0, max(0.0, (x - self._x0()) / self._w()))
        return npal.pos_from_u(u, self.noise)

    def set_stops(self, noise, stops, sel=-1):
        self.noise, self.stops = noise, stops
        self.sel = sel if 0 <= sel < len(stops) else -1
        self.update()

    def _handle_at(self, x, y):
        hy = self.BAR_Y + self.BAR_H
        best, bd = -1, 1e9
        for i, s in enumerate(self.stops):
            d = abs(self.x_of(s["pos"]) - x)
            if d < 9 and self.BAR_Y - 4 <= y <= hy + self.HANDLE + 8 and d < bd:
                best, bd = i, d
        return best

    def color_at(self, pos):
        lut = npal.lut_from_stops(self.stops, self.noise)
        r, g, b = lut[int(round(npal.u_from_pos(pos, self.noise) * 255))]
        return int(r), int(g), int(b)

    # painting
    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x0, w = self._x0(), self._w()
        bar = QRectF(x0, self.BAR_Y, w, self.BAR_H)
        import numpy as np
        lut = npal.lut_from_stops(self.stops, self.noise)
        row = np.ascontiguousarray(lut[None, :, :])
        img = QImage(row.data, 256, 1, 256 * 3, QImage.Format.Format_RGB888).copy()
        path = QPainterPath(); path.addRoundedRect(bar, 7, 7)
        p.save(); p.setClipPath(path)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(bar, img)
        # extremes: a light veil + label on the two tails
        t = npal.TAIL * w
        p.setPen(Qt.PenStyle.NoPen)
        for r in (QRectF(x0, self.BAR_Y, t, 11), QRectF(x0 + w - t, self.BAR_Y, t, 11)):
            p.fillRect(r, QColor(0, 0, 0, 110))
        p.setPen(QColor(255, 255, 255, 190)); p.setFont(theme.ui_font(6.5))
        p.drawText(QRectF(x0, self.BAR_Y, t, 11), Qt.AlignmentFlag.AlignCenter, "extremes")
        p.drawText(QRectF(x0 + w - t, self.BAR_Y, t, 11), Qt.AlignmentFlag.AlignCenter, "extremes")
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 170), 1.5))
        for q in (0.0, 1.0):                           # usual-range boundaries
            xq = self.x_of(q)
            p.drawLine(QPointF(xq, self.BAR_Y - 3), QPointF(xq, self.BAR_Y + self.BAR_H + 3))
        p.setPen(QPen(QColor(theme.LINE2), 1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
        # regime boundaries
        regs = regimes(self.noise)
        p.setFont(theme.ui_font(7.5))
        strip_y = self.BAR_Y + self.BAR_H + self.HANDLE + 12
        _, _, pmin, pmax = npal.axis(self.noise)
        bounds = [(max(pmin, min(pmax, q)), lab) for q, lab in regs]
        for i, (q, lab) in enumerate(bounds):
            nxt = bounds[i + 1][0] if i + 1 < len(bounds) else pmax
            if nxt - q <= 0:
                continue
            xa, xb = self.x_of(q), self.x_of(nxt)
            if pmin < q < pmax:
                p.setPen(QPen(QColor(255, 255, 255, 110), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(xa, self.BAR_Y + 2), QPointF(xa, self.BAR_Y + self.BAR_H - 2))
                p.setPen(QPen(QColor(theme.LINE2), 1))
                p.drawLine(QPointF(xa, strip_y - 2), QPointF(xa, strip_y + 16))
            p.setPen(QColor(theme.TEXT3))
            r = QRectF(xa + 2, strip_y, xb - xa - 4, 16)
            txt = p.fontMetrics().elidedText(lab, Qt.TextElideMode.ElideRight, int(max(0, r.width())))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        # value ticks
        p.setFont(theme.mono_font(7.5))
        ty = strip_y + 20
        for q, strong in ((pmin, False), (0.0, True), (0.5, False), (1.0, True), (pmax, False)):
            t = fmt_value(self.noise, value_of(self.noise, q))
            fw = p.fontMetrics().horizontalAdvance(t)
            x = min(max(self.x_of(q) - fw / 2, 0), self.width() - fw)
            p.setPen(QColor(theme.TEXT2 if strong else theme.TEXT3))
            p.drawText(QPointF(x, ty + 10), t)
        # handles
        hy = self.BAR_Y + self.BAR_H + 2
        for i, s in enumerate(self.stops):
            x = self.x_of(s["pos"])
            selected = i == self.sel
            p.setPen(QPen(QColor(255, 255, 255, 200 if selected else 120), 1))
            p.drawLine(QPointF(x, self.BAR_Y), QPointF(x, self.BAR_Y + self.BAR_H))
            tri = QPolygonF([QPointF(x, hy), QPointF(x - 7, hy + self.HANDLE),
                             QPointF(x + 7, hy + self.HANDLE)])
            p.setBrush(QColor(*s["rgb"]))
            p.setPen(QPen(QColor(theme.ACCENT) if selected else QColor(theme.TEXT), 2 if selected else 1.2))
            p.drawPolygon(tri)
        p.end()

    # interaction
    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        i = self._handle_at(x, y)
        if e.button() == Qt.MouseButton.RightButton:
            if i >= 0:
                self._delete(i)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if i < 0:
            if not (self.BAR_Y <= y <= self.BAR_Y + self.BAR_H) or len(self.stops) >= MAX_STOPS:
                return
            pos = round(self.pos_of(x), 4)
            self.stops.append({"pos": pos, "rgb": self.color_at(pos)})
            i = len(self.stops) - 1
            self.changed.emit(True)
        self.sel = i
        self.selected.emit(i)
        self._drag = i
        self.update()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag is not None:
            pos = self.pos_of(x)
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:        # snap to a regime boundary
                cands = [q for q, _ in regimes(self.noise)] + [0.0, 1.0]
                q = min(cands, key=lambda c: abs(c - pos))
                if abs(self.x_of(q) - x) < 14:
                    pos = q
            self.stops[self._drag]["pos"] = round(pos, 4)
            self.changed.emit(False)
            self.update()
        pos = self.pos_of(x)
        v = value_of(self.noise, pos)
        reg = npal.regime_label_at(self.noise, v)
        if pos < 0 or pos > 1:
            reg = (reg + " · " if reg else "") + "beyond the usual range"
        self.setToolTip(f"{fmt_value(self.noise, v)}" + (f"  ·  {reg}" if reg else "")
                        + "\nClick: add a stop · drag: move (Shift = snap to a regime)"
                          "\nDouble-click: colour · right-click / Delete: remove")

    def mouseReleaseEvent(self, _e):
        if self._drag is not None:
            self._drag = None
            self.changed.emit(True)

    def mouseDoubleClickEvent(self, e):
        i = self._handle_at(e.position().x(), e.position().y())
        if i >= 0:
            self.editColor.emit(i)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.sel >= 0:
            self._delete(self.sel)
        elif e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.sel >= 0:
            step = (0.01 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.002)
            d = -step if e.key() == Qt.Key.Key_Left else step
            s = self.stops[self.sel]
            s["pos"] = round(min(1.0, max(0.0, s["pos"] + d)), 4)
            self.changed.emit(True); self.update()
        else:
            super().keyPressEvent(e)

    def _delete(self, i):
        if len(self.stops) <= 2:
            return
        self.stops.pop(i)
        self.sel = -1
        self.selected.emit(-1)
        self.changed.emit(True)
        self.update()


# ═════════════════════════════════════════════════════════════════════════════
class _StopRow(QWidget):
    def __init__(self, editor, i, stop):
        super().__init__()
        self.ed, self.i = editor, i
        h = QHBoxLayout(self); h.setContentsMargins(0, 1, 0, 1); h.setSpacing(6)
        # value in the units shown everywhere: blocks for height, cubiomes-viewer
        # ×10 000 integers for the climate noises; from extreme to extreme
        self.k = 1.0 if editor.noise == "Height" else CV
        elo, ehi = extreme_range(editor.noise)
        self.val = QDoubleSpinBox(); self.val.setDecimals(0)
        self.val.setRange(elo * self.k, ehi * self.k)
        self.val.setSingleStep(1 if editor.noise == "Height" else 100)
        self.val.setGroupSeparatorShown(editor.noise != "Height")
        self.val.setValue(value_of(editor.noise, stop["pos"]) * self.k); self.val.setFixedWidth(104)
        self.val.setKeyboardTracking(True)
        self.val.valueChanged.connect(self._val)
        self.reg = QLabel(); self.reg.setObjectName("Muted"); self.reg.setMinimumWidth(92)
        self.sw = QToolButton(); self.sw.setFixedSize(QSize(34, 22))
        self.sw.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sw.setToolTip("Choose the colour")
        self.sw.clicked.connect(lambda: editor.pick_color(self.i))
        self.hex = QLineEdit(); self.hex.setFixedWidth(78); self.hex.setFont(theme.mono_font(9))
        self.hex.editingFinished.connect(self._hex)
        dl = QToolButton(); icons.bind(dl, "trash", 15); dl.setToolTip("Remove this stop")
        dl.clicked.connect(lambda: editor.bar._delete(self.i))
        dl.setEnabled(len(editor.stops) > 2)
        for wd in (self.val, self.reg, self.sw, self.hex):
            h.addWidget(wd)
        h.addStretch(1); h.addWidget(dl)
        self.refresh(stop)

    def refresh(self, stop):
        r, g, b = stop["rgb"]
        self.sw.setStyleSheet(f"QToolButton {{ background: rgb({r},{g},{b}); border: 1px solid "
                              f"{theme.LINE2}; border-radius: 5px; }}")
        if not self.hex.hasFocus():
            self.hex.setText(f"#{r:02x}{g:02x}{b:02x}")
        v = value_of(self.ed.noise, stop["pos"])
        if not self.val.hasFocus():
            self.val.blockSignals(True); self.val.setValue(v * self.k); self.val.blockSignals(False)
        out = not (0.0 <= stop["pos"] <= 1.0)
        self.reg.setText(("extreme · " if out else "") + npal.regime_label_at(self.ed.noise, v))

    def _val(self, v):
        self.ed.stops[self.i]["pos"] = round(pos_of_value(self.ed.noise, v / self.k), 4)
        self.ed.edited(final=True, rows=False)

    def _hex(self):
        c = QColor(self.hex.text().strip())
        if c.isValid():
            self.ed.stops[self.i]["rgb"] = (c.red(), c.green(), c.blue())
            self.ed.edited(final=True)
        else:
            r, g, b = self.ed.stops[self.i]["rgb"]
            self.hex.setText(f"#{r:02x}{g:02x}{b:02x}")


# ═════════════════════════════════════════════════════════════════════════════
class PaletteEditor(QDialog):
    """Non-modal. `applied(palette, map_noise)` asks the map to show the edited
    palette on that noise; `palettesChanged()` = list / names changed."""
    applied = Signal(str, str)
    palettesChanged = Signal()
    edited_live = Signal(str)          # palette whose colours changed (recolour tiles)

    def __init__(self, palette=None, map_noise=None, modern=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Noise palettes")
        self.resize(980, 600)
        self.modern = modern
        self.noise = npal.editor_noise(map_noise) if map_noise in npal.EDITOR_NOISES + [
            "Approx height", "Exact Y"] else "Temperature"
        self.map_noise = map_noise if map_noise and map_noise != "Off" else None
        self.palette = palette if palette in npal.NOISE_PALETTE_NAMES else "Semantic"
        self.data = {}                  # working copy {noise: stops} of self.palette
        self._undo, self._redo = [], []
        self._last_final = None
        self._save_timer = QTimer(self, singleShot=True, interval=500, timeout=self._save_disk)
        self._live_timer = QTimer(self, singleShot=True, interval=30, timeout=self._push_live)

        root = QHBoxLayout(self); root.setContentsMargins(14, 14, 14, 12); root.setSpacing(12)
        root.addWidget(self._build_list())
        right = QVBoxLayout(); right.setSpacing(8)
        root.addLayout(right, 1)
        self.tabs = Segmented(NOISES, self.noise)
        self.tabs.changed.connect(self._set_noise)
        right.addWidget(self.tabs)
        head = QHBoxLayout()
        self.title = QLabel(); self.title.setObjectName("PageTitle")
        self.desc = body("", muted=True, wrap=False)
        head.addWidget(self.title); head.addSpacing(10); head.addWidget(self.desc, 1)
        self.range_lbl = QLabel(); self.range_lbl.setObjectName("Muted"); self.range_lbl.setFont(theme.mono_font(9))
        head.addWidget(self.range_lbl)
        right.addLayout(head)
        self.ro_banner = QLabel(); self.ro_banner.setObjectName("Muted"); self.ro_banner.setWordWrap(True)
        right.addWidget(self.ro_banner)
        self.bar = GradientBar()
        self.bar.changed.connect(lambda final: self.edited(final))
        self.bar.selected.connect(self._select_row)
        self.bar.editColor.connect(self.pick_color)
        right.addWidget(self.bar)

        tools = QHBoxLayout(); tools.setSpacing(6)
        for text, tip, fn in (("Reverse", "Mirror the gradient", self._reverse),
                              ("Even spacing", "Spread the stops evenly", self._even),
                              ("Stops at regimes", "One stop at each regime boundary, "
                               "coloured by the current gradient", self._at_regimes),
                              ("Extreme stops", "Add a stop at each far end (record values), "
                               "in a contrasting colour", self._extremes),
                              ("Copy to all noises", "Use this gradient for every noise channel",
                               self._to_all)):
            b = QPushButton(text); b.setProperty("kind", "ghost"); b.setToolTip(tip)
            b.clicked.connect(fn); tools.addWidget(b)
        tools.addStretch(1)
        tools.addWidget(body("Start from", muted=True, wrap=False))
        self.base = QComboBox(); self.base.setToolTip("Replace this channel's gradient by the one of "
                                                      "another palette")
        self.base.activated.connect(self._from_base)
        tools.addWidget(self.base)
        right.addLayout(tools)

        right.addWidget(section_title("Stops"))
        self.rows_host = QWidget(); self.rows_lay = QVBoxLayout(self.rows_host)
        self.rows_lay.setContentsMargins(0, 0, 6, 0); self.rows_lay.setSpacing(2)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(self.rows_host)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        right.addWidget(sc, 1)

        bottom = QHBoxLayout()
        self.b_undo = QPushButton("Undo"); self.b_undo.clicked.connect(self.undo)
        self.b_redo = QPushButton("Redo"); self.b_redo.clicked.connect(self.redo)
        for b in (self.b_undo, self.b_redo):
            b.setProperty("kind", "ghost"); bottom.addWidget(b)
        self.saved_lbl = QLabel(""); self.saved_lbl.setObjectName("Muted")
        bottom.addWidget(self.saved_lbl); bottom.addStretch(1)
        self.note = body("", muted=True, small=True, wrap=False); bottom.addWidget(self.note)
        close = QPushButton("Close"); close.setProperty("kind", "primary"); close.clicked.connect(self.close)
        bottom.addWidget(close)
        right.addLayout(bottom)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self.redo)

        self._fill_list()
        self._load_palette(self.palette)

    # ── palette list ────────────────────────────────────────────────────────
    def _build_list(self):
        box = QFrame(); box.setObjectName("SidePanel"); box.setFixedWidth(250)
        v = QVBoxLayout(box); v.setContentsMargins(12, 12, 12, 10); v.setSpacing(6)
        t = QLabel("Palettes"); t.setObjectName("PanelTitle"); v.addWidget(t)
        self.lst = QListWidget(); self.lst.setIconSize(QSize(46, 14))
        self.lst.currentItemChanged.connect(self._list_changed)
        v.addWidget(self.lst, 1)
        g1 = QHBoxLayout(); g2 = QHBoxLayout()
        for lay, items in ((g1, (("New", self._new), ("Duplicate", self._duplicate),
                                 ("Rename", self._rename))),
                           (g2, (("Delete", self._delete), ("Import…", self._import),
                                 ("Export…", self._export)))):
            for text, fn in items:
                b = QPushButton(text); b.setProperty("kind", "ghost"); b.clicked.connect(fn)
                lay.addWidget(b)
                setattr(self, "b_" + text.strip("…").lower(), b)
            v.addLayout(lay)
        v.addWidget(body("Built-in palettes are read-only: editing one creates your own copy. "
                         "Your palettes are saved automatically.", muted=True, small=True))
        return box

    def _fill_list(self):
        self.lst.blockSignals(True)
        self.lst.clear()
        from .palette_combo import name_color
        for title, names in npal.palette_groups():
            if title:
                hd = QListWidgetItem(title.upper())
                hd.setFlags(Qt.ItemFlag.NoItemFlags)
                f = hd.font(); f.setPointSizeF(7.2); f.setWeight(QFont.Weight.DemiBold); hd.setFont(f)
                hd.setForeground(QColor(theme.TEXT3))
                self.lst.addItem(hd)
            for name in names:
                self._add_list_item(name, name_color(name))
        self.lst.blockSignals(False)
        from .palette_combo import fill_palette_combo
        fill_palette_combo(self.base, placeholder="palette…")

    def _add_list_item(self, name, col):
        it = QListWidgetItem(name)
        it.setData(Qt.ItemDataRole.UserRole, name)
        it.setIcon(gradient_icon(npal.stops_for(name, self.noise)))
        it.setForeground(col)
        f = it.font(); f.setWeight(QFont.Weight.DemiBold); it.setFont(f)
        if name in npal.BUILTIN_PALETTES:
            it.setToolTip("Built-in (read-only)")
        self.lst.addItem(it)
        if name == self.palette:
            self.lst.setCurrentItem(it)

    def _refresh_icons(self):
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            name = it.data(Qt.ItemDataRole.UserRole)
            if not name:                                   # group title
                continue
            stops = self.data.get(self.noise) if name == self.palette else npal.stops_for(name, self.noise)
            it.setIcon(gradient_icon(stops))

    def _list_changed(self, cur, _prev):
        if cur is not None:
            self._load_palette(cur.data(Qt.ItemDataRole.UserRole))

    def _load_palette(self, name):
        self.palette = name
        self.data = {k: npal.stops_for(name, k) for k in NOISES}
        self._undo, self._redo = [], []
        self._last_final = copy.deepcopy(self.data)
        self._set_noise(self.noise, apply=True)
        builtin = name in npal.BUILTIN_PALETTES
        for b in (self.b_rename, self.b_delete):
            b.setEnabled(not builtin)
        self._update_undo()

    # ── channel ────────────────────────────────────────────────────────────
    @property
    def stops(self):
        return self.data[self.noise]

    def _set_noise(self, noise, apply=True):
        self.noise = noise
        self.tabs.set_value(noise)
        self.title.setText(f"{self.palette} · {noise}")
        self.desc.setText(DESC.get(noise, ""))
        lo, hi = value_range(noise)
        elo, ehi = extreme_range(noise)
        self.range_lbl.setText(f"usual {fmt_value(noise, lo)} … {fmt_value(noise, hi)}   ·   "
                               f"extremes to {fmt_value(noise, elo)} / {fmt_value(noise, ehi)}")
        builtin = self.palette in npal.BUILTIN_PALETTES
        self.ro_banner.setText("Built-in palette — your first change creates an editable copy."
                               if builtin else "")
        self.ro_banner.setVisible(builtin)
        self.bar.set_stops(noise, self.stops)
        self._rebuild_rows()
        self._refresh_icons()
        if not self.modern:
            self.note.setText("Noise overlays need Java 1.18+ (change the version to preview "
                              "on the map).")
        if apply:
            self._show_on_map()

    def _map_noise_for(self, noise):
        if noise == "Height":
            return self.map_noise if self.map_noise in ("Approx height", "Exact Y") else "Approx height"
        return noise

    def _show_on_map(self):
        if self.modern:
            self.map_noise = self._map_noise_for(self.noise)
            self.applied.emit(self.palette, self.map_noise)

    # ── stop rows ──────────────────────────────────────────────────────────
    def _rebuild_rows(self):
        while self.rows_lay.count():
            it = self.rows_lay.takeAt(0)
            if it.widget():
                wd = it.widget(); wd.hide(); wd.deleteLater()
        order = sorted(range(len(self.stops)), key=lambda i: self.stops[i]["pos"])
        self.rows = {}
        for i in order:
            r = _StopRow(self, i, self.stops[i])
            self.rows[i] = r
            self.rows_lay.addWidget(r)
        self.rows_lay.addStretch(1)
        add = QPushButton("  Add a stop"); icons.bind(add, "plus", 15); add.setProperty("kind", "ghost")
        add.setEnabled(len(self.stops) < MAX_STOPS)
        add.clicked.connect(self._add_in_gap)
        self.rows_lay.addWidget(add)

    def _select_row(self, i):
        for k, r in getattr(self, "rows", {}).items():
            r.setStyleSheet(f"background: {theme.BG3}; border-radius: 6px;" if k == i else "")

    def _add_in_gap(self):
        st = sorted(self.stops, key=lambda s: s["pos"])
        gaps = [(st[k + 1]["pos"] - st[k]["pos"], k) for k in range(len(st) - 1)]
        g, k = max(gaps) if gaps else (1.0, 0)
        pos = round((st[k]["pos"] + st[k + 1]["pos"]) / 2, 4) if gaps else 0.5
        self.stops.append({"pos": pos, "rgb": self.bar.color_at(pos)})
        self.edited(final=True)

    # ── editing ────────────────────────────────────────────────────────────
    def _ensure_editable(self):
        """First change on a built-in palette → work on an editable copy."""
        if self.palette not in npal.BUILTIN_PALETTES:
            return
        name = self._unique(f"{self.palette} (custom)")
        npal.register_custom(name, self.data)
        self.palette = name
        self._fill_list()
        self.ro_banner.setVisible(False)
        self.title.setText(f"{self.palette} · {self.noise}")
        for b in (self.b_rename, self.b_delete):
            b.setEnabled(True)
        self.palettesChanged.emit()
        self._show_on_map()                        # the map now shows the copy

    def edited(self, final=True, rows=True):
        self._ensure_editable()
        if final:
            if self._last_final is not None and self._last_final != self.data:
                self._undo.append(self._last_final)
                self._redo.clear()
            self._last_final = copy.deepcopy(self.data)
            self._update_undo()
        self.bar.update()
        if rows and final:
            sel = self.bar.sel
            self._rebuild_rows()
            self._select_row(sel)
        else:
            for i, r in getattr(self, "rows", {}).items():
                if i < len(self.stops):
                    r.refresh(self.stops[i])
        self._live_timer.start()
        if final:
            self._save_timer.start()
            self.saved_lbl.setText("")

    def _push_live(self):
        npal.register_custom(self.palette, self.data)
        self.edited_live.emit(self.palette)
        it = self.lst.currentItem()
        if it is not None:
            it.setIcon(gradient_icon(self.stops))

    def _save_disk(self):
        try:
            npal.register_custom(self.palette, self.data)
            npal.save_custom()
            self.saved_lbl.setText("✓ saved")
        except Exception as e:
            self.saved_lbl.setText(f"not saved: {e}")

    def pick_color(self, i):
        if not (0 <= i < len(self.stops)):
            return
        c = QColorDialog.getColor(QColor(*self.stops[i]["rgb"]), self, "Stop colour")
        if c.isValid():
            self.stops[i]["rgb"] = (c.red(), c.green(), c.blue())
            self.edited(final=True)

    def _reverse(self):
        """Mirror on the colour axis (the bar), extremes included."""
        for s in self.stops:
            s["pos"] = round(float(npal.pos_from_u(1.0 - npal.u_from_pos(s["pos"], self.noise),
                                                   self.noise)), 4)
        self.edited(final=True)

    def _even(self):
        """Evenly spaced along the bar between the first and the last stop."""
        st = sorted(self.stops, key=lambda s: s["pos"])
        n = len(st)
        if n < 3:
            return
        u0, u1 = (npal.u_from_pos(st[0]["pos"], self.noise), npal.u_from_pos(st[-1]["pos"], self.noise))
        for k, s in enumerate(st):
            s["pos"] = round(float(npal.pos_from_u(u0 + (u1 - u0) * k / (n - 1), self.noise)), 4)
        self.edited(final=True)

    def _extremes(self):
        """A stop at each far end (records), in the complementary hue of the
        usual-range end colour, so extreme values stand out on the map."""
        _, _, pmin, pmax = npal.axis(self.noise)
        have = {round(s["pos"], 4) for s in self.stops}
        for q, ref in ((pmin, 0.0), (pmax, 1.0)):
            if round(q, 4) in have or len(self.stops) >= MAX_STOPS:
                continue
            c = QColor(*self.bar.color_at(ref))
            h, sat, v, _a = c.getHsvF()
            c2 = QColor.fromHsvF(((h if h >= 0 else 0.0) + 0.5) % 1.0, max(0.75, sat), max(0.85, v))
            self.stops.append({"pos": round(q, 4), "rgb": (c2.red(), c2.green(), c2.blue())})
        self.edited(final=True)

    def _at_regimes(self):
        qs = sorted({0.0, 1.0} | {round(q, 4) for q, _ in regimes(self.noise) if 0.0 < q < 1.0})
        if len(qs) > MAX_STOPS:
            qs = qs[:MAX_STOPS]
        new = [{"pos": q, "rgb": self.bar.color_at(q)} for q in qs]
        self.data[self.noise] = new
        self.bar.set_stops(self.noise, new)
        self.edited(final=True)

    def _to_all(self):
        for k in NOISES:
            if k != self.noise:
                self.data[k] = copy.deepcopy(self.stops)
        self.edited(final=True)
        self.saved_lbl.setText("copied to every noise")

    def _from_base(self, idx):
        name = self.base.itemData(idx)
        if not name:
            return
        self.data[self.noise] = npal.stops_for(name, self.noise)
        self.bar.set_stops(self.noise, self.stops)
        self.base.setCurrentIndex(-1)
        self.edited(final=True)

    # ── undo / redo ────────────────────────────────────────────────────────
    def _update_undo(self):
        self.b_undo.setEnabled(bool(self._undo)); self.b_redo.setEnabled(bool(self._redo))

    def _restore(self, snap):
        self.data = copy.deepcopy(snap)
        self._last_final = copy.deepcopy(snap)
        self.bar.set_stops(self.noise, self.stops)
        self._rebuild_rows(); self._refresh_icons(); self._update_undo()
        self._live_timer.start(); self._save_timer.start()

    def undo(self):
        if self._undo:
            self._redo.append(copy.deepcopy(self.data))
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(copy.deepcopy(self.data))
            self._restore(self._redo.pop())

    # ── palette management ────────────────────────────────────────────────
    def _unique(self, base):
        name, k = base, 2
        while name in npal.NOISE_PALETTE_NAMES:
            name = f"{base} {k}"; k += 1
        return name

    def _ask_name(self, title, default, allow=None):
        """allow: a name accepted even if it exists (Rename: the current name)."""
        while True:
            name, ok = QInputDialog.getText(self, title, "Palette name:", text=default)
            if not ok:
                return None
            name = name.strip()
            if not name or name.startswith("__"):
                QMessageBox.warning(self, title, "Choose a name.")
            elif name in npal.NOISE_PALETTE_NAMES and name != allow:
                QMessageBox.warning(self, title, f"“{name}” already exists.")
            else:
                return name

    def _commit_new(self, name, data):
        self._save_timer.stop()
        if self.palette not in npal.BUILTIN_PALETTES:
            self._save_disk()
        npal.register_custom(name, data)
        npal.save_custom()
        self.palette = name
        self._fill_list()
        self._load_palette(name)
        self.palettesChanged.emit()

    def _new(self):
        name = self._ask_name("New palette", self._unique("My palette"))
        if name:
            fb = [{"pos": s[0], "rgb": tuple(s[1:4])} for s in npal.NOISE_FALLBACK_STOPS]
            self._commit_new(name, {k: copy.deepcopy(fb) for k in NOISES})

    def _duplicate(self):
        name = self._ask_name("Duplicate", self._unique(f"{self.palette} copy"))
        if name:
            self._commit_new(name, copy.deepcopy(self.data))

    def _rename(self):
        if self.palette in npal.BUILTIN_PALETTES:
            return
        old = self.palette
        name = self._ask_name("Rename", old, allow=old)
        if name == old:
            return                               # same name: nothing to do (was "already exists")
        if name:
            data = copy.deepcopy(self.data)
            npal.remove_custom(old)
            npal.register_custom(name, data)
            npal.save_custom()
            self.palette = name
            self._fill_list(); self._load_palette(name)
            self.palettesChanged.emit()

    def _delete(self):
        if self.palette in npal.BUILTIN_PALETTES:
            return
        if QMessageBox.question(self, "Delete palette",
                                f"Delete the palette “{self.palette}”?") != QMessageBox.StandardButton.Yes:
            return
        self._save_timer.stop()
        npal.remove_custom(self.palette)
        npal.save_custom()
        self.palette = "Semantic"
        self._fill_list(); self._load_palette("Semantic")
        self.palettesChanged.emit()

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import a palette", user_path(),
                                              "Noise palette (*.noisepak *.json);;All files (*.*)")
        if not path:
            return
        try:
            data = npal.read_noisepak(path)
        except Exception as e:
            QMessageBox.critical(self, "Import", f"Could not read the file:\n{e}")
            return
        if not data:
            QMessageBox.warning(self, "Import", "No noise gradient found in this file.")
            return
        full = {k: data.get(k) or npal.stops_for("Semantic", k) for k in NOISES}
        name = self._unique(os.path.splitext(os.path.basename(path))[0] or "Imported")
        self._commit_new(name, full)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export the palette", user_path(f"{self.palette}.noisepak"),
                                              "Noise palette (*.noisepak)")
        if not path:
            return
        try:
            npal.write_noisepak(path, self.data)
            self.saved_lbl.setText(f"exported to {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "Export", str(e))

    def closeEvent(self, e):
        if self._save_timer.isActive():
            self._save_timer.stop()
            if self.palette not in npal.BUILTIN_PALETTES:
                self._save_disk()
        super().closeEvent(e)
