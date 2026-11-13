"""
qtui/biome_palette_editor.py — biome colour palettes (22/10), the same idea as
cubiomes-viewer's « Biome colors » dialog:

  * palettes: the built-in ones (read-only) and yours — new, duplicate,
    rename, delete; import / export cubiomes-viewer .colormap files;
  * one row per biome of the current version (the others below, « Not in
    this version »): its colour (click: the colour picker), back to the
    default colour, dimmed (to make the other biomes stand out);
  * « All to default » / « All dimmed », like cubiomes-viewer;
  * the map shows the edited colours LIVE; a built-in palette, once edited,
    becomes « My … » (a copy);
  * Close keeps the palette shown, Cancel goes back to the one before.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

import colormaps as cmaps
import cubiomes_wrap as cw

from . import icons, theme
from .color_picker import pick_color
from .widgets import Segmented, section_title, user_path, remember_path

DIM = 6                                 # cubiomes-viewer's DIM_DIVIDER


def _default_colors():
    return dict(cmaps.CLASSIC_COLORS)


def _swatch_icon(colors, w=40, h=14):
    """A small strip of a palette's colours (list icon)."""
    pm = QPixmap(w, h); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    keys = [1, 4, 29, 6, 2, 37, 16, 0, 24, 12, 185, 177]
    for i, k in enumerate(keys):
        c = colors.get(k, (60, 60, 60))
        p.fillRect(int(i * w / len(keys)), 0, int(w / len(keys)) + 1, h, QColor(*c))
    p.end()
    return QIcon(pm)


class _Row(QWidget):
    """One biome: colour button, name, hex, default, dimmed."""

    def __init__(self, ed, bid, name):
        super().__init__()
        self.ed, self.bid, self.name = ed, bid, name
        h = QHBoxLayout(self); h.setContentsMargins(4, 1, 4, 1); h.setSpacing(8)
        self.sw = QPushButton(); self.sw.setFixedSize(30, 20)
        self.sw.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sw.setToolTip("Change the colour")
        self.sw.clicked.connect(lambda: ed.edit_color(bid))
        h.addWidget(self.sw)
        self.lab = QLabel(name); self.lab.setMinimumWidth(110)
        h.addWidget(self.lab, 1)
        self.hex = QLabel(""); self.hex.setFont(theme.mono_font(8.5)); self.hex.setObjectName("Muted")
        self.hex.setFixedWidth(62)
        h.addWidget(self.hex)
        for ic, tip, fn in (("undo", "Back to the default colour", lambda: ed.set_color(bid, ed.defaults[bid])),
                            ("minus", "Dimmed (the default colour ÷ 6)", lambda: ed.set_color(bid, ed.dimmed(bid)))):
            b = QToolButton(); icons.bind(b, ic, 14); b.setAutoRaise(True); b.setToolTip(tip)
            b.setFixedSize(22, 22)
            b.clicked.connect(fn)
            h.addWidget(b)

    def refresh(self, rgb):
        c = QColor(*rgb)
        self.sw.setStyleSheet(f"QPushButton {{ background: {c.name()}; border: 1px solid {theme.LINE2};"
                              f" border-radius: 5px; padding: 0; min-height: 0; }}"
                              f"QPushButton:hover {{ border: 2px solid {theme.TEXT}; }}")
        self.hex.setText(c.name())


class BiomePaletteEditor(QDialog):
    def __init__(self, map_engine, mc, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Biome colours")
        self.resize(980, 680)
        self.engine = map_engine
        self.mc = int(mc)
        self.start_palette = map_engine.palette
        self.defaults = _default_colors()
        self.colors = {}                    # colours of the palette being edited
        self.name = None
        self.dirty = False
        self._seq = 0
        self.chosen = None                  # the palette kept at Close

        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        # ── left: palettes ──────────────────────────────────────────────────
        side = QFrame(); side.setObjectName("SidePanel"); side.setFixedWidth(250)
        sv = QVBoxLayout(side); sv.setContentsMargins(14, 16, 12, 12); sv.setSpacing(8)
        t = QLabel("Biome colours"); t.setObjectName("PageTitle"); sv.addWidget(t)
        sv.addWidget(section_title("Palettes"))
        self.list = QListWidget(); self.list.setIconSize(QSize(40, 14))
        self.list.setStyleSheet("QListWidget { background: transparent; border: 0; }"
                                "QListWidget::item { padding: 6px 4px; border-radius: 6px; }")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(lambda *_: self._list_changed())
        sv.addWidget(self.list, 1)
        g = QGridLayout(); g.setHorizontalSpacing(6); g.setVerticalSpacing(6)
        for i, (txt, fn, tip) in enumerate((
                ("New", self._new, "A new palette from the default colours"),
                ("Duplicate", self._duplicate, "A copy of the selected palette"),
                ("Rename", self._rename, "Rename your palette"),
                ("Delete", self._delete, "Delete your palette (kept in biome_palettes/_trash)"),
                ("Import…", self._import, "Read a cubiomes-viewer .colormap file"),
                ("Export…", self._export, "Save the palette as a .colormap file"))):
            b = QPushButton(txt); b.setProperty("kind", "ghost"); b.setToolTip(tip); b.clicked.connect(fn)
            g.addWidget(b, i // 2, i % 2)
            setattr(self, "b_" + txt.strip("…").lower(), b)
        sv.addLayout(g)
        root.addWidget(side)

        # ── right: the colours ─────────────────────────────────────────────
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(20, 16, 20, 14); rv.setSpacing(10)
        hr = QHBoxLayout(); hr.setSpacing(10)
        self.title = QLabel(""); self.title.setStyleSheet("font-size: 13pt; font-weight: 600;")
        hr.addWidget(self.title)
        self.note = QLabel(""); self.note.setObjectName("Muted"); hr.addWidget(self.note)
        hr.addStretch(1)
        rv.addLayout(hr)
        tb = QHBoxLayout(); tb.setSpacing(8)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search a biome…")
        self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self._filter)
        tb.addWidget(self.search, 1)
        self.scope = Segmented(["This version", "All biomes"], "This version")
        self.scope.changed.connect(lambda _v: self._filter())
        tb.addWidget(self.scope)
        for txt, fn, tip in (("All to default", self._all_default, "Every biome back to its default colour"),
                             ("All dimmed", self._all_dimmed,
                              "Every biome dimmed: then give a colour to the ones you want to see")):
            b = QPushButton(txt); b.setProperty("kind", "ghost"); b.setToolTip(tip); b.clicked.connect(fn)
            tb.addWidget(b)
        rv.addLayout(tb)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget(); self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(0, 0, 8, 0); self.grid.setHorizontalSpacing(24); self.grid.setVerticalSpacing(1)
        sc.setWidget(self.inner)
        rv.addWidget(sc, 1)
        bt = QHBoxLayout(); bt.setSpacing(8)
        self.state = QLabel(""); self.state.setObjectName("Muted"); bt.addWidget(self.state, 1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject); bt.addWidget(cancel)
        ok = QPushButton("Use this palette"); ok.setProperty("kind", "primary"); ok.clicked.connect(self._close_ok)
        bt.addWidget(ok)
        rv.addLayout(bt)
        root.addWidget(right, 1)

        self._build_rows()
        self._fill_list(current or map_engine.palette)

    # ── biome rows ──────────────────────────────────────────────────────────
    def _build_rows(self):
        from biome_catalog import dim_biomes_for, biome_name_for
        here, seen = [], set()
        for d in (0, -1, 1):
            for bid, name in dim_biomes_for(self.mc, d):
                if bid is None:
                    here.append((None, name))
                elif bid not in seen:
                    seen.add(bid); here.append((bid, name))
        other = [b for b in sorted(set(cw.BIOME_COLORS) | set(self.defaults)) if b not in seen and 0 <= b < 256]
        self.rows = {}
        self._items = []                    # (kind, widget, bid or None)
        r = 0
        col_rows = []
        for bid, name in here:
            col_rows.append((bid, name, True))
        if other:
            col_rows.append((None, "Not in this version", False))
            for b in other:
                col_rows.append((b, biome_name_for(35, b), False))
        # two columns, headers keep their group
        half = (len(col_rows) + 1) // 2
        for i, (bid, name, cur) in enumerate(col_rows):
            c, rr = (0, i) if i < half else (1, i - half)
            if bid is None:
                w = section_title(name)
                w.setContentsMargins(4, 8, 0, 2)
            else:
                w = _Row(self, bid, name)
                self.rows[bid] = w
            self.grid.addWidget(w, rr, c)
            self._items.append((bid, w, cur, name))
        self.grid.setRowStretch(half + 1, 1)

    def _filter(self, *_a):
        t = self.search.text().strip().lower()
        allb = self.scope.value() == "All biomes"
        for bid, w, cur, name in self._items:
            if bid is None:
                w.setVisible(not t and (cur or allb))
            else:
                w.setVisible((cur or allb) and (not t or t in name.lower()))

    # ── palettes list ───────────────────────────────────────────────────────
    def _fill_list(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for name in cmaps.get_available_colormaps():
            it = QListWidgetItem(_swatch_icon(cmaps.load_colormap(name)), name)
            it.setData(Qt.ItemDataRole.UserRole, name)
            if cmaps.is_builtin(name):
                it.setToolTip("Built-in palette: edit it to make your own copy")
                it.setForeground(QColor(theme.TEXT2))
            self.list.addItem(it)
        self.list.blockSignals(False)
        names = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        self.list.setCurrentRow(names.index(select) if select in names else 0)

    def _cur_name(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _list_changed(self):
        self._save_if_dirty()
        name = self._cur_name()
        if name is None:
            return
        self.name = name
        self.colors = dict(cmaps.load_colormap(name))
        self.dirty = False
        self._show()
        self._preview()

    def _show(self):
        built = cmaps.is_builtin(self.name)
        self.title.setText(self.name)
        self.note.setText("built-in · an edit makes your own copy" if built else "your palette")
        self.b_rename.setEnabled(not built); self.b_delete.setEnabled(not built)
        for bid, w in self.rows.items():
            w.refresh(self.colors.get(bid, self.defaults.get(bid, cw.DEFAULT_COLOR)))
        self._state()
        self._filter()

    def _state(self):
        self.state.setText("Unsaved changes" if self.dirty else "")

    # ── editing ─────────────────────────────────────────────────────────────
    def dimmed(self, bid):
        r, g, b = self.defaults.get(bid, cw.DEFAULT_COLOR)
        return (r // DIM, g // DIM, b // DIM)

    def _ensure_editable(self):
        """A built-in palette: the first edit makes « My <name> »."""
        if not cmaps.is_builtin(self.name):
            return
        base = "My " + self.name.split(" (")[0]
        name, i = base, 2
        while name in cmaps.get_available_colormaps():
            name = f"{base} {i}"; i += 1
        cmaps.save_user_palette(name, self.colors)
        self.name = name
        self._fill_list(name)

    def set_color(self, bid, rgb, save=True):
        self._ensure_editable()
        self.colors[bid] = tuple(int(x) for x in rgb)
        if bid in self.rows:
            self.rows[bid].refresh(self.colors[bid])
        self.dirty = True
        self._state()
        self._preview()

    def edit_color(self, bid):
        cur = QColor(*self.colors.get(bid, self.defaults.get(bid, cw.DEFAULT_COLOR)))
        name = self.rows[bid].name if bid in self.rows else str(bid)

        def live(c):
            self.colors[bid] = (c.red(), c.green(), c.blue())
            if bid in self.rows:
                self.rows[bid].refresh(self.colors[bid])
            self._preview()
        c = pick_color(self, cur, f"{name} — colour", live=live)
        if c is not None:
            self.set_color(bid, (c.red(), c.green(), c.blue()))

    def _all_default(self):
        self._ensure_editable()
        self.colors = dict(self.defaults)
        self.dirty = True
        self._show(); self._preview()

    def _all_dimmed(self):
        self._ensure_editable()
        self.colors = {b: self.dimmed(b) for b in self.defaults}
        self.dirty = True
        self._show(); self._preview()

    def _preview(self):
        """The map shows the colours being edited (a temporary palette)."""
        old = f"__edit__{self._seq}"
        self._seq += 1
        name = f"__edit__{self._seq}"
        cmaps.TEMP.pop(old, None)
        cmaps.TEMP[name] = dict(self.colors)
        self.engine.set_palette(name, force=True)
        self._repaint_maps()

    def _repaint_maps(self):
        p = self.parent()
        try:
            for m in (p.map, p.sessions.map, p.strongholds.map, p.drawing.map):
                m.update()
        except Exception:
            try:
                p.update()
            except Exception:
                pass

    def _save_if_dirty(self):
        if self.dirty and self.name and not cmaps.is_builtin(self.name):
            cmaps.save_user_palette(self.name, self.colors)
            self.dirty = False
            from .map_engine import forget_palette
            forget_palette(self.name)
            it = self.list.currentItem()
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == self.name:
                it.setIcon(_swatch_icon(self.colors))

    # ── palette actions ─────────────────────────────────────────────────────
    def _ask(self, title, default):
        name, ok = QInputDialog.getText(self, title, "Name:", text=default)
        name = " ".join((name or "").split())
        if not ok or not name:
            return None
        if name in cmaps.get_available_colormaps():
            QMessageBox.warning(self, title, f"A palette called « {name} » already exists.")
            return None
        return name

    def _new(self):
        self._save_if_dirty()
        name = self._ask("New palette", "My palette")
        if name:
            cmaps.save_user_palette(name, self.defaults)
            self._fill_list(name)

    def _duplicate(self):
        self._save_if_dirty()
        name = self._ask("Duplicate", f"{self.name} (copy)")
        if name:
            cmaps.save_user_palette(name, self.colors)
            self._fill_list(name)

    def _rename(self):
        if cmaps.is_builtin(self.name):
            return
        self._save_if_dirty()
        name = self._ask("Rename", self.name)
        if name:
            path = cmaps.user_palettes().get(self.name)
            cmaps.save_user_palette(name, self.colors, path=path)
            from .map_engine import forget_palette
            forget_palette(self.name)
            self._fill_list(name)

    def _delete(self):
        if cmaps.is_builtin(self.name):
            return
        if QMessageBox.question(self, "Delete", f"Delete the palette « {self.name} »?") \
                != QMessageBox.StandardButton.Yes:
            return
        cmaps.delete_user_palette(self.name)
        self.dirty = False
        self._fill_list(cmaps.DEFAULT_PALETTE)

    def _import(self):
        path, _f = QFileDialog.getOpenFileName(self, "Import a biome palette", user_path(),
                                               "Colour maps (*.colormap *.txt);;All files (*)")
        if not path:
            return
        remember_path(path)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            QMessageBox.warning(self, "Import", str(e)); return
        got = cmaps.parse_colormap_text(text)
        if not got:
            QMessageBox.warning(self, "Import", "No biome colour found in this file.")
            return
        first = text.lstrip().splitlines()[0] if text.strip() else ""
        base = first[1:].strip() if first.startswith(";") else os.path.splitext(os.path.basename(path))[0]
        name, i = base or "Imported", 2
        while name in cmaps.get_available_colormaps():
            name = f"{base} {i}"; i += 1
        cols = dict(self.defaults); cols.update(got)
        self._save_if_dirty()
        cmaps.save_user_palette(name, cols)
        self._fill_list(name)

    def _export(self):
        safe = "".join(ch for ch in self.name if ch not in '<>:"/\\|?*').strip() or "palette"
        path, _f = QFileDialog.getSaveFileName(self, "Export the palette", user_path(safe + ".colormap"),
                                               "Colour maps (*.colormap)")
        if not path:
            return
        remember_path(path)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(cmaps.export_text(self.colors, self.name))

    # ── close ───────────────────────────────────────────────────────────────
    def _cleanup_temp(self):
        for k in [k for k in cmaps.TEMP if k.startswith("__edit__")]:
            cmaps.TEMP.pop(k, None)

    def _close_ok(self):
        self._save_if_dirty()
        self.chosen = self.name
        self._cleanup_temp()
        self.accept()

    def reject(self):
        if self.dirty and self.name and not cmaps.is_builtin(self.name):
            if QMessageBox.question(self, "Biome colours", "Keep the changes of this palette?") \
                    == QMessageBox.StandardButton.Yes:
                self._save_if_dirty()
        self._cleanup_temp()
        self.chosen = None
        self.engine.set_palette(self.start_palette, force=True)
        self._repaint_maps()
        super().reject()
