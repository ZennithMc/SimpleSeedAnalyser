"""
qtui/draw_library.py — the drawings library (inside SSA, like the sessions
library): %APPDATA%/SimpleSeedAnalyser/library/drawings/*.ssadraw.

An entry is one JSON file: the drawing (draw_model.DrawModel.to_dict) plus,
when a search was run, its state (results, resume point, settings). Deleted
entries go to drawings/_trash (nothing is ever erased for good here).
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import uuid

import numpy as np
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from .widgets import user_path, remember_path
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

import draw_engine as de

from . import icons, theme
from .draw_model import FORMAT, _unpack, from_legacy_preset, now_iso

EXT = ".ssadraw"


def draw_dir() -> str:
    from .session_library import lib_dir
    d = os.path.join(lib_dir(), "drawings")
    os.makedirs(d, exist_ok=True)
    return d


def is_library_entry(path) -> bool:
    try:
        return bool(path) and os.path.normcase(os.path.dirname(os.path.abspath(path))) == \
            os.path.normcase(os.path.abspath(draw_dir()))
    except Exception:
        return False


def autosave_path() -> str:
    from .session_library import lib_dir
    return os.path.join(os.path.dirname(lib_dir()), "draw_autosave" + EXT)


def new_entry_path() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(draw_dir(), f"{stamp}-{uuid.uuid4().hex[:6]}{EXT}")


def write_json(path, d):
    """Atomic write (a crash never leaves half a file)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, separators=(",", ":"))
    os.replace(tmp, path)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or d.get("format") != FORMAT:
        raise ValueError("not an SSA drawing file")
    return d


def list_entries():
    d = draw_dir()
    out = []
    for f in os.listdir(d):
        if not f.endswith(EXT):
            continue
        p = os.path.join(d, f)
        try:
            m = read_json(p)
        except Exception:
            continue
        m["path"] = p
        out.append(m)
    out.sort(key=lambda m: m.get("modified", ""), reverse=True)
    return out


def trash(path):
    t = os.path.join(draw_dir(), "_trash")
    os.makedirs(t, exist_ok=True)
    dst = os.path.join(t, os.path.basename(path))
    if os.path.exists(dst):
        dst = os.path.join(t, uuid.uuid4().hex[:6] + "-" + os.path.basename(path))
    shutil.move(path, dst)


def thumb(d, size=72) -> QPixmap:
    """Preview of a drawing dict (its classes' colours, unpainted = dark)."""
    try:
        W, H = int(d["W"]), int(d["H"])
        g = _unpack(d["grid"], "<i4", (H, W))
    except Exception:
        g = np.zeros((1, 1), np.int32); W = H = 1
    rgba = np.zeros((H, W, 4), np.uint8)
    bg = QColor(theme.BG3)
    rgba[...] = (bg.red(), bg.green(), bg.blue(), 255)
    for c in np.unique(g):
        if int(c) != de.ANY:
            rgba[g == c] = (*de.code_color(int(c)), 255)
    img = QImage(rgba.tobytes(), W, H, 4 * W, QImage.Format.Format_RGBA8888).copy()
    k = size / max(W, H)
    pm = QPixmap(size, size); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    tw, th = max(1, int(W * k)), max(1, int(H * k))
    p.drawImage(QRect((size - tw) // 2, (size - th) // 2, tw, th), img)
    p.end()
    return pm


def describe(d) -> str:
    W, H, cell = int(d.get("W", 0)), int(d.get("H", 0)), int(d.get("cell", 0))
    s = d.get("search") or {}
    parts = [f"{W}×{H} cells", f"{W * cell:,}×{H * cell:,} blocks".replace(",", " ")]
    if d.get("slide"):
        parts.append(f"slide ±{d['slide']}")
    n = len(s.get("results") or [])
    if n:
        parts.append(f"{n} result{'s' if n > 1 else ''}")
    if s.get("state") == "paused":
        parts.append("search paused")
    return "  ·  ".join(parts)


def _when(iso):
    try:
        t = datetime.datetime.fromisoformat(iso)
    except Exception:
        return ""
    d = datetime.datetime.now() - t
    if d.days == 0:
        return t.strftime("today %H:%M")
    if d.days == 1:
        return t.strftime("yesterday %H:%M")
    return t.strftime("%d/%m/%Y %H:%M")


class DrawLibraryDialog(QDialog):
    """Drawings saved in SSA: open, save the current one, rename, describe,
    duplicate, export / import (and the classic UI's presets), delete."""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page
        self.setWindowTitle("Drawings library")
        self.resize(820, 560)
        self.setStyleSheet(f"""
            QListWidget#DrawLib {{ background: {theme.BG1}; border: 1px solid {theme.LINE}; border-radius: 8px; }}
            QListWidget#DrawLib::item {{ border-radius: 6px; padding: 4px; color: {theme.TEXT}; }}
            QListWidget#DrawLib::item:selected {{ background: {theme.ACCENT_SOFT}; }}
            QPlainTextEdit#LibDesc {{ background: {theme.BG2}; border: 1px solid {theme.LINE2};
                                     border-radius: 6px; padding: 6px; color: {theme.TEXT}; }}
        """)
        v = QVBoxLayout(self); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(10)
        top = QHBoxLayout()
        t = QLabel("Drawings"); t.setObjectName("PanelTitle"); top.addWidget(t)
        self.count = QLabel(); self.count.setObjectName("Muted"); top.addWidget(self.count)
        top.addStretch(1)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search…"); self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._filter); top.addWidget(self.search)
        v.addLayout(top)
        mid = QHBoxLayout(); mid.setSpacing(12); v.addLayout(mid, 1)
        self.list = QListWidget(); self.list.setObjectName("DrawLib")
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(96, 96)); self.list.setGridSize(QSize(124, 136))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust); self.list.setMovement(QListWidget.Movement.Static)
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(self._show)
        self.list.itemDoubleClicked.connect(lambda _it: self._open())
        mid.addWidget(self.list, 3)
        side = QFrame(); side.setObjectName("SidePanel"); side.setMinimumWidth(270)
        sv = QVBoxLayout(side); sv.setContentsMargins(14, 12, 14, 12); sv.setSpacing(8)
        self.preview = QLabel(); self.preview.setFixedSize(160, 160)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sv.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self.name = QLineEdit(); self.name.setPlaceholderText("Name")
        self.name.editingFinished.connect(self._rename); sv.addWidget(self.name)
        self.info = QLabel(); self.info.setObjectName("Muted"); self.info.setWordWrap(True); sv.addWidget(self.info)
        self.desc = QPlainTextEdit(); self.desc.setObjectName("LibDesc")
        self.desc.setPlaceholderText("Notes (saved automatically)…")
        self.desc.textChanged.connect(self._desc_changed); sv.addWidget(self.desc, 1)
        row = QHBoxLayout()
        self.b_open = QPushButton("Open"); self.b_open.setProperty("kind", "primary")
        self.b_open.clicked.connect(self._open); row.addWidget(self.b_open)
        self.b_dup = QPushButton("Duplicate"); self.b_dup.clicked.connect(self._duplicate); row.addWidget(self.b_dup)
        sv.addLayout(row)
        row = QHBoxLayout()
        self.b_exp = QPushButton("Export…"); self.b_exp.clicked.connect(self._export); row.addWidget(self.b_exp)
        self.b_del = QPushButton("Delete"); self.b_del.setProperty("kind", "danger")
        self.b_del.clicked.connect(self._delete); row.addWidget(self.b_del)
        sv.addLayout(row)
        mid.addWidget(side, 2)
        bot = QHBoxLayout()
        b = QPushButton("  Save current drawing"); icons.bind(b, "save", 16)
        b.clicked.connect(self._save_current); bot.addWidget(b)
        self.b_replace = QPushButton("Replace selected with current")
        self.b_replace.clicked.connect(self._replace); bot.addWidget(self.b_replace)
        imp = QPushButton("Import…"); imp.setToolTip("SSA drawings (.ssadraw) or the classic "
                                                    "interface's presets (drawing_presets.json)")
        imp.clicked.connect(self._import); bot.addWidget(imp)
        bot.addStretch(1)
        close = QPushButton("Close"); close.clicked.connect(self.accept); bot.addWidget(close)
        v.addLayout(bot)
        self._loading = False
        self.reload()

    # ── list ────────────────────────────────────────────────────────────────
    def reload(self, select=None):
        self._loading = True
        self.list.clear()
        entries = list_entries()
        for m in entries:
            it = QListWidgetItem(QIcon(thumb(m, 96)), m.get("name") or "?")
            it.setData(Qt.ItemDataRole.UserRole, m)
            it.setToolTip(describe(m))
            self.list.addItem(it)
            if select and os.path.normcase(m["path"]) == os.path.normcase(select):
                self.list.setCurrentItem(it)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.count.setText(f"{len(entries)} drawing{'s' if len(entries) != 1 else ''}")
        self._loading = False
        self._show()
        self._filter(self.search.text())

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            m = it.data(Qt.ItemDataRole.UserRole)
            it.setHidden(bool(t) and t not in (m.get("name", "") + " " + m.get("description", "")).lower())

    def _cur(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

    def _show(self, *_):
        m = self._cur()
        for w in (self.b_open, self.b_dup, self.b_exp, self.b_del, self.b_replace, self.name, self.desc):
            w.setEnabled(m is not None)
        self._loading = True
        if m is None:
            self.name.setText(""); self.desc.setPlainText(""); self.preview.clear()
            self.info.setText("No drawing saved yet — “Save current drawing” adds the one being edited.")
        else:
            self.name.setText(m.get("name", ""))
            self.desc.setPlainText(m.get("description", ""))
            self.preview.setPixmap(thumb(m, 160))
            self.info.setText(describe(m) + f"\nModified {_when(m.get('modified', ''))}")
        self._loading = False

    def _update(self, m, **kw):
        try:
            d = read_json(m["path"])
            d.update(kw)
            d["modified"] = now_iso()
            write_json(m["path"], d)
            m.update(kw)
        except Exception as e:
            QMessageBox.warning(self, "Library", f"Could not update the entry:\n{e}")

    def _rename(self):
        m = self._cur()
        if m is None or self._loading:
            return
        new = self.name.text().strip()
        if not new or new == m.get("name"):
            self.name.setText(m.get("name", ""))
            return
        self._update(m, name=new)
        self.list.currentItem().setText(new)
        self.page.library_renamed(m["path"], new)

    def _desc_changed(self):
        m = self._cur()
        if m is None or self._loading:
            return
        self._update(m, description=self.desc.toPlainText())
        self.page.library_described(m["path"], self.desc.toPlainText())

    def _open(self):
        m = self._cur()
        if m is not None and self.page.open_drawing(m["path"]):
            self.accept()

    def _save_current(self):
        name, ok = QInputDialog.getText(self, "Save to the library", "Name:", text=self.page.model.name)
        if not ok or not name.strip():
            return
        path = self.page.save_to_library(new=True, name=name.strip())
        if path:
            self.reload(select=path)

    def _replace(self):
        m = self._cur()
        if m is None:
            return
        if QMessageBox.question(self, "Replace", f"Replace “{m.get('name')}” with the current drawing "
                                "(and its search)?") != QMessageBox.StandardButton.Yes:
            return
        path = self.page.save_to_library(path=m["path"], name=m.get("name"))
        if path:
            self.reload(select=path)

    def _duplicate(self):
        m = self._cur()
        if m is None:
            return
        d = read_json(m["path"])
        d["name"] = (d.get("name") or "Drawing") + " (copy)"
        d["created"] = d["modified"] = now_iso()
        p = new_entry_path()
        write_json(p, d)
        self.reload(select=p)

    def _export(self):
        m = self._cur()
        if m is None:
            return
        safe = "".join(ch for ch in (m.get("name") or "drawing") if ch not in '\\/:*?"<>|').strip() or "drawing"
        path, _ = QFileDialog.getSaveFileName(self, "Export the drawing", user_path(safe + EXT), f"SSA drawings (*{EXT})")
        if path:
            shutil.copyfile(m["path"], path)

    def _delete(self):
        m = self._cur()
        if m is None:
            return
        if QMessageBox.question(self, "Delete", f"Move “{m.get('name')}” to the library's trash?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            trash(m["path"])
        except Exception as e:
            QMessageBox.warning(self, "Delete", str(e))
            return
        self.page.library_deleted(m["path"])
        self.reload()

    def _import(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import drawings", user_path(),
            f"Drawings (*{EXT} drawing_presets.json *.json);;All files (*.*)")
        last, n, errors = None, 0, []
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and d.get("format") == FORMAT:
                    items = [d]
                elif isinstance(d, list):              # classic UI presets
                    items = [from_legacy_preset(p) for p in d if isinstance(p, dict) and "grid" in p]
                elif isinstance(d, dict) and "grid" in d:
                    items = [from_legacy_preset(d)]
                else:
                    raise ValueError("unknown format")
                for it in items:
                    it["created"] = it.get("created") or now_iso()
                    it["modified"] = now_iso()
                    last = new_entry_path()
                    write_json(last, it)
                    n += 1
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        if errors:
            QMessageBox.warning(self, "Import", "\n".join(errors))
        if n:
            self.reload(select=last)


def import_legacy_presets_once(app_dir) -> int:
    """First run of the new Draw page: the classic UI's presets are copied
    into the library (once; the original file is left untouched)."""
    flag = os.path.join(draw_dir(), ".legacy_imported")
    if os.path.exists(flag):
        return 0
    n = 0
    src = os.path.join(app_dir, "drawing_presets.json")
    try:
        if os.path.exists(src):
            with open(src, "r", encoding="utf-8") as f:
                presets = json.load(f)
            for p in presets if isinstance(presets, list) else []:
                try:
                    d = from_legacy_preset(p)
                    d["created"] = d["modified"] = now_iso()
                    write_json(new_entry_path(), d)
                    n += 1
                except Exception:
                    pass
    finally:
        try:
            with open(flag, "w") as f:
                f.write(now_iso())
        except Exception:
            pass
    return n
