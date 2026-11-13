"""
qtui/session_library.py — the sessions kept INSIDE SSA (like cubiomes-viewer's
"Filter presets", but complete).

Every entry is a normal .session file (cubiomes-viewer compatible + SSA extras)
in %APPDATA%/SimpleSeedAnalyser/library/, named by an id: renaming an entry
never moves a file. Its name, description and dates live in the #SSAState line.
An entry opened in the editor is saved back to the library by Ctrl+S.
Deleted entries go to library/_trash (never destroyed).
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import uuid

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from .widgets import user_path
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QStyle, QStyledItemDelegate, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import icons, theme


def lib_dir() -> str:
    import ssa_paths
    d = os.path.join(ssa_paths.data_dir(), "library")
    os.makedirs(d, exist_ok=True)
    return d


def is_library_path(path) -> bool:
    if not path:
        return False
    try:
        return os.path.normcase(os.path.dirname(os.path.abspath(path))) == \
            os.path.normcase(os.path.abspath(lib_dir()))
    except Exception:
        return False


def new_entry_path() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(lib_dir(), f"{stamp}-{uuid.uuid4().hex[:6]}.session")


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


_META_CACHE: dict = {}


def read_meta(path) -> dict:
    """Header of a library entry, cached by (mtime, size): counting the results
    reads the whole file (entries with 1 M results: ~15 s to open the library
    every time)."""
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    hit = _META_CACHE.get(path)
    if key is not None and hit is not None and hit[0] == key:
        return dict(hit[1])
    m = _read_meta(path)
    if key is not None:
        _META_CACHE[path] = (key, dict(m))
    return m


def _read_meta(path) -> dict:
    """Fast header read (no condition decoding): name, description, version,
    counts, dates."""
    m = dict(path=path, name=os.path.splitext(os.path.basename(path))[0], description="",
             mc="?", n_cond=0, n_res=0, created="", modified="", paused=False, types=[])
    types = []
    try:
        m["modified"] = datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(
            timespec="seconds")
        from cubiomes_session import ssa_line
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ssa_line(ln)
                if ln.startswith("#Cond:"):
                    m["n_cond"] += 1
                    try:
                        from cubiomes_session import F_TO_SSA
                        types.append(F_TO_SSA.get(int.from_bytes(bytes.fromhex(ln[6:].strip()[:8]),
                                                                 "little")))
                    except Exception:
                        types.append(None)
                elif ln.startswith("#SSAx:") or ln.startswith("#SSA:"):
                    try:
                        t = json.loads(ln.split(":", 1)[1]).get("type")
                    except Exception:
                        t = None
                    if ln.startswith("#SSA:"):
                        m["n_cond"] += 1
                        types.append(t)
                    elif types:
                        types[-1] = t or types[-1]
                elif ln.startswith("#MC:"):
                    m["mc"] = ln[4:].strip()
                    try:
                        from cubiomes_session import mc_from_session
                        m["mc"] = mc_from_session(m["mc"])[0] or m["mc"]
                    except Exception:
                        pass
                elif ln.startswith("#SSAState:"):
                    try:
                        st = json.loads(ln[len("#SSAState:"):])
                        m["name"] = st.get("name") or m["name"]
                        m["description"] = st.get("description") or ""
                        m["created"] = st.get("created") or ""
                        m["paused"] = bool((st.get("search") or {}).get("resume_point") is not None)
                    except Exception:
                        pass
                elif not ln.startswith("#") and ln.strip().lstrip("-").isdigit():
                    m["n_res"] += 1
    except OSError:
        pass
    seen = []
    for t in types:
        if t and t not in seen:
            seen.append(t)
    m["types"] = seen
    return m


def _plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}".replace(",", " ")


def list_entries():
    d = lib_dir()
    out = [read_meta(os.path.join(d, f)) for f in os.listdir(d) if f.endswith(".session")]
    out.sort(key=lambda m: m["modified"], reverse=True)
    return out


def update_meta(path, **kw):
    """Change name / description of an entry without touching the rest."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    from cubiomes_session import ssa_line
    st, idx = {}, None
    for i, ln in enumerate(lines):
        if ssa_line(ln).startswith("#SSAState:"):
            idx = i
            try:
                st = json.loads(ssa_line(ln)[len("#SSAState:"):])
            except Exception:
                st = {}
            break
    st.update(kw)
    new = "#Desc: #SSAState: " + json.dumps(st, ensure_ascii=False, separators=(",", ":"))
    if idx is None:
        at = next((i for i, ln in enumerate(lines) if ln.startswith("#Cond")
                   or ssa_line(ln).startswith("#SSA:")),
                  len(lines))
        lines.insert(at, new)
    else:
        lines[idx] = new
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


def _when(iso):
    try:
        t = datetime.datetime.fromisoformat(iso)
    except Exception:
        return ""
    d = datetime.datetime.now() - t
    if d.days == 0:
        return "today " + t.strftime("%H:%M")
    if d.days == 1:
        return "yesterday " + t.strftime("%H:%M")
    return t.strftime("%d/%m/%Y")


class _CardDelegate(QStyledItemDelegate):
    """(22/10) One session = one quiet row: the name and the date on top, a
    single muted line under it (version · conditions · results · paused). No
    coloured chips, no icons: the details are on the right."""
    H = 62

    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg

    def sizeHint(self, opt, idx):
        return QSize(opt.rect.width(), self.H)

    def paint(self, p, opt, idx):
        m = idx.data(Qt.ItemDataRole.UserRole)
        if not m:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(opt.rect).adjusted(1, 2, -3, -2)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hov = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        # (22/10) each session in its own quiet card (a thin border), so the
        # entries stand apart; selected = accent tint + accent border
        if sel:
            bg, line = _soft(theme.ACCENT, 0.14), QColor(theme.ACCENT)
        else:
            bg = QColor(theme.BG3 if hov else theme.BG2)
            line = QColor(theme.LINE2 if hov else theme.LINE)
        p.setPen(QPen(line, 1.0)); p.setBrush(bg)
        p.drawRoundedRect(r, 7, 7)
        if sel:                                          # accent bar on the left
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(QRectF(r.left(), r.top() + 12, 3, r.height() - 24), 1.5, 1.5)
        x0 = r.left() + 14
        # line 1: name ……… date
        p.setFont(theme.ui_font(8.5))
        when = _when(m["modified"])
        dw = p.fontMetrics().horizontalAdvance(when)
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(r.right() - dw - 12, r.top() + 9, dw, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, when)
        p.setFont(theme.ui_font(10, QFont.Weight.DemiBold)); p.setPen(QColor(theme.TEXT))
        name = p.fontMetrics().elidedText(m["name"], Qt.TextElideMode.ElideRight,
                                          int(r.width() - dw - 44))
        p.drawText(QRectF(x0, r.top() + 8, r.width() - dw - 34, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        # line 2: one muted line
        p.setFont(theme.ui_font(8.5))
        parts = [m["mc"].replace("Java ", "")]
        parts.append(_plural(m["n_cond"], "condition"))
        if m["n_res"]:
            parts.append(_plural(m["n_res"], "result"))
        txt = "  ·  ".join(parts)
        x = x0
        p.setPen(QColor(theme.TEXT3))
        fm = p.fontMetrics()
        p.drawText(QRectF(x, r.top() + 30, r.width() - 40, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
        x += fm.horizontalAdvance(txt)
        for flag, col in ((self.dlg.is_open_entry(m["path"]) and "open", theme.ACCENT),):
            if flag:
                x += 10
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(col))
                p.drawEllipse(QRectF(x, r.top() + 37, 5, 5))
                p.setPen(QColor(col))
                p.drawText(QRectF(x + 9, r.top() + 30, 80, 18),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, flag)
                x += 9 + fm.horizontalAdvance(flag)
        p.restore()


def _soft(color, a):
    c = QColor(color); c.setAlphaF(a)
    return c


class LibraryDialog(QDialog):
    """Saved sessions: search, open, rename, describe, update with the current
    session, duplicate, delete, import / export files."""
    opened = Signal(str)

    def __init__(self, page, parent=None):
        super().__init__(parent or page)
        from .widgets import section_title
        self.page = page
        self.setWindowTitle("Session library")
        self.resize(1080, 680)
        self.setStyleSheet(f"""
            QListWidget#LibList {{ background: transparent; border: 0; outline: 0; }}
            QListWidget#LibList::item {{ background: transparent; border: 0; padding: 0; margin: 0; }}
            QLineEdit#LibName {{ font-size: 14pt; font-weight: 600; padding: 6px 10px;
                                 background: transparent; border: 1px solid transparent; }}
            QLineEdit#LibName:hover {{ border-color: {theme.LINE2}; }}
            QLineEdit#LibName:focus {{ border-color: {theme.ACCENT}; background: {theme.BG2}; }}
            QPlainTextEdit#LibDesc {{ background: {theme.BG2}; border: 1px solid {theme.LINE2};
                                      border-radius: 6px; padding: 6px; }}
            QPlainTextEdit#LibDesc:focus {{ border-color: {theme.ACCENT}; }}
            QFrame#LibCard {{ background: {theme.BG1}; border: 1px solid {theme.LINE};
                              border-radius: 10px; }}
            QLabel#LibCount {{ color: {theme.TEXT3}; }}
            QLabel#LibMeta {{ color: {theme.TEXT3}; }}
            QTreeWidget#LibTree {{ background: transparent; border: 0; }}
        """)
        root = QVBoxLayout(self); root.setContentsMargins(16, 16, 16, 12); root.setSpacing(10)

        split = QSplitter(Qt.Orientation.Horizontal); split.setHandleWidth(14)
        # ── left: search + cards
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(8)
        head = QHBoxLayout(); head.setSpacing(6)
        head.addWidget(section_title("Saved sessions"))
        self.count = QLabel(""); self.count.setObjectName("LibCount")
        head.addStretch(1); head.addWidget(self.count)
        from PySide6.QtWidgets import QToolButton
        fold = QToolButton(); icons.bind(fold, "folder", 15); fold.setAutoRaise(True)
        fold.setToolTip(f"Open the library folder\n{lib_dir()}\n(deleted sessions go to _trash)")
        fold.clicked.connect(self._open_folder)
        head.addWidget(fold)
        lv.addLayout(head)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search by name or description…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lv.addWidget(self.search)
        self.list = QListWidget(); self.list.setObjectName("LibList")
        self.list.setItemDelegate(_CardDelegate(self))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSpacing(2)
        self.list.currentItemChanged.connect(lambda *_: self._show())
        self.list.itemDoubleClicked.connect(lambda *_: self._open())
        lv.addWidget(self.list, 1)
        row = QHBoxLayout(); row.setSpacing(6)
        self.b_new = QPushButton("  Save current session")
        self.b_new.setProperty("kind", "primary")
        icons.bind(self.b_new, "plus", 16, "#ffffff", "#ffffff")
        self.b_new.setToolTip("Add the session being edited to the library (new entry)")
        self.b_new.clicked.connect(self._save_current_new)
        row.addWidget(self.b_new, 1)
        b_imp = QPushButton("Import…"); b_imp.setProperty("kind", "ghost"); b_imp.clicked.connect(self._import)
        b_imp.setToolTip("Copy .session files (SSA or cubiomes-viewer) into the library")
        row.addWidget(b_imp)
        lv.addLayout(row)
        split.addWidget(left)

        # ── right: the selected session
        right = QFrame(); right.setObjectName("LibCard")
        rv = QVBoxLayout(right); rv.setContentsMargins(16, 12, 16, 14); rv.setSpacing(8)
        self.name = QLineEdit(); self.name.setObjectName("LibName")
        self.name.setPlaceholderText("Name")
        self.name.setToolTip("Click to rename")
        self.name.editingFinished.connect(self._rename)
        rv.addWidget(self.name)
        # (22/10) one quiet line instead of the coloured chips
        self.info = QLabel(""); self.info.setObjectName("LibMeta")
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setContentsMargins(11, 0, 0, 6)
        rv.addWidget(self.info)
        rv.addWidget(section_title("Description"))
        self.desc = QPlainTextEdit(); self.desc.setObjectName("LibDesc")
        self.desc.setPlaceholderText("Add a description…")
        self.desc.setToolTip("Saved automatically")
        self.desc.setFixedHeight(64)
        self.desc.textChanged.connect(self._desc_changed)
        rv.addWidget(self.desc)
        rv.addSpacing(4)
        rv.addWidget(section_title("Conditions"))
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setColumnCount(3)
        self.tree.setTreePosition(1); self.tree.setRootIsDecorated(True)
        self.tree.setObjectName("LibTree")
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        from PySide6.QtWidgets import QHeaderView
        h = self.tree.header(); h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        rv.addWidget(self.tree, 1)
        acts = QHBoxLayout(); acts.setSpacing(6)
        self.b_open = QPushButton("  Open in the editor"); self.b_open.setProperty("kind", "primary")
        icons.bind(self.b_open, "folder", 16, "#ffffff", "#ffffff")
        self.b_open.setToolTip("Load it in the editor (Ctrl+S then saves the changes back here)."
                               "\nDouble-click a session does the same.")
        self.b_open.clicked.connect(self._open)
        self.b_update = QPushButton("Replace with current"); self.b_update.setProperty("kind", "ghost")
        self.b_update.setToolTip("Overwrite this entry with the session being edited "
                                 "(its name and description are kept)")
        self.b_update.clicked.connect(self._replace)
        self.b_dup = QPushButton("Duplicate"); self.b_dup.clicked.connect(self._duplicate)
        self.b_exp = QPushButton("Export…"); self.b_exp.clicked.connect(self._export)
        for b in (self.b_dup, self.b_exp):
            b.setProperty("kind", "ghost")
        self.b_exp.setToolTip("Save a copy as a .session file (cubiomes-viewer compatible)")
        self.b_del = QPushButton("Delete"); self.b_del.setProperty("kind", "ghost")
        self.b_del.setStyleSheet(f"QPushButton {{ color: {theme.DANGER}; }}")
        self.b_del.clicked.connect(self._delete)
        acts.addWidget(self.b_open)
        acts.addSpacing(8)
        for b in (self.b_update, self.b_dup, self.b_exp):
            acts.addWidget(b)
        acts.addStretch(1); acts.addWidget(self.b_del)
        rv.addLayout(acts)
        split.addWidget(right)
        split.setSizes([400, 660])
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)
        self._loading = False
        self.reload()

    def is_open_entry(self, path):
        mp = self.page.model.path
        return bool(mp) and os.path.normcase(os.path.abspath(mp)) == os.path.normcase(os.path.abspath(path))

    # ── list ────────────────────────────────────────────────────────────────
    def reload(self, select=None):
        cur = select or (self._cur() or {}).get("path")
        self.list.clear()
        entries = list_entries()
        for m in entries:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, m)
            it.setToolTip(m["description"] or m["name"])
            self.list.addItem(it)
            if cur and os.path.normcase(m["path"]) == os.path.normcase(cur):
                self.list.setCurrentItem(it)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.count.setText(_plural(len(entries), "session"))
        self._filter(self.search.text())
        self._show()

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            m = it.data(Qt.ItemDataRole.UserRole)
            it.setHidden(bool(t) and t not in (m["name"] + " " + m["description"]).lower())

    def _cur(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

    def _show(self):
        m = self._cur()
        on = m is not None
        for w in (self.name, self.desc, self.b_open, self.b_update, self.b_dup, self.b_exp, self.b_del):
            w.setEnabled(on)
        self._loading = True
        self.tree.clear()
        if not on:
            self.name.setText(""); self.desc.setPlainText("")
            self.info.setText("No saved session yet — “Save current session” adds the one being edited.")
            self._loading = False
            return
        self.name.setText(m["name"])
        self.desc.setPlainText(m["description"])
        dot = lambda col, txt: (f"&nbsp;&nbsp;<span style='color:{col}'>●</span>&nbsp;"
                                f"<span style='color:{col}'>{txt}</span>")
        parts = [m["mc"], _plural(m["n_cond"], "condition"), _plural(m["n_res"], "result"),
                 f"modified {_when(m['modified'])}"]
        meta = "&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts)
        if m["paused"]:
            meta += "&nbsp;&nbsp;·&nbsp;&nbsp;search paused"
        if self.is_open_entry(m["path"]):
            meta += dot(theme.ACCENT, "open in the editor")
        self.info.setText(meta)
        tip = f"Created {_when(m['created'])}" if m["created"] else ""
        self.info.setToolTip(tip)
        self._loading = False
        self._fill_tree(m["path"])

    def _fill_tree(self, path):
        from cubiomes_session import parse_session_file
        from .session_model import SessionModel, area_text, display_name, type_color, type_icon, icon_type
        try:
            r = parse_session_file(path)
        except Exception as e:
            QTreeWidgetItem(self.tree, ["", f"Could not read: {e}", ""])
            return
        sm = SessionModel(); sm.load(r["conditions"])
        items, num = {}, 0
        mono = theme.mono_font(8.5)
        for c in sm.conds:
            parent = items.get(c.get("_parent", 0))
            it = QTreeWidgetItem(parent if parent is not None else self.tree)
            on = sm.ancestors_enabled(c["_uid"])
            if on:
                num += 1
                it.setText(0, f"#{num}")
                it.setForeground(0, QColor(type_color(icon_type(c))))
            it.setFont(0, mono)
            it.setTextAlignment(0, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            it.setText(1, display_name(c))
            ic = type_icon(icon_type(c), 16)
            if ic is not None:
                it.setIcon(1, ic)
            it.setText(2, area_text(c))
            it.setForeground(2, QColor(theme.TEXT3))
            if not on:
                it.setForeground(1, QColor(theme.TEXT3))
            items[c["_uid"]] = it
        self.tree.expandAll()

    # ── actions ─────────────────────────────────────────────────────────────
    def _rename(self):
        m = self._cur()
        if not m or self._loading:
            return
        new = self.name.text().strip()
        if not new or new == m["name"]:
            self.name.setText(m["name"])
            return
        update_meta(m["path"], name=new)
        self.page.library_renamed(m["path"], new)
        self.reload(select=m["path"])

    def _desc_changed(self):
        m = self._cur()
        if not m or self._loading:
            return
        update_meta(m["path"], description=self.desc.toPlainText())
        m["description"] = self.desc.toPlainText()
        self.list.currentItem().setToolTip(m["description"] or m["name"])
        self.list.viewport().update()

    def _open(self):
        m = self._cur()
        if m and self.page.open_library_entry(m["path"]):
            self.accept()

    def _save_current_new(self):
        if not self.page.model.conds:
            QMessageBox.information(self, "Session library", "The current session has no condition.")
            return
        name, ok = QInputDialog.getText(self, "Save to the library", "Name:",
                                        text=self.page.model.name)
        if not ok or not name.strip():
            return
        path = self.page.save_to_library(name.strip())
        if path:
            self.reload(select=path)

    def _replace(self):
        m = self._cur()
        if not m:
            return
        if QMessageBox.question(self, "Replace",
                                f"Replace “{m['name']}” with the session being edited?\n"
                                "(its name and description are kept)") != QMessageBox.StandardButton.Yes:
            return
        if self.page.save_to_library(m["name"], path=m["path"], description=m["description"],
                                     created=m["created"]):
            self.reload(select=m["path"])

    def _duplicate(self):
        m = self._cur()
        if not m:
            return
        p = new_entry_path()
        shutil.copyfile(m["path"], p)
        update_meta(p, name=m["name"] + " (copy)", created=now_iso())
        self.reload(select=p)

    def _export(self):
        m = self._cur()
        if not m:
            return
        safe = "".join(ch for ch in m["name"] if ch not in '<>:"/\\|?*').strip() or "session"
        path, _ = QFileDialog.getSaveFileName(self, "Export the session", user_path(safe + ".session"),
                                              "Sessions (*.session)")
        if path:
            shutil.copyfile(m["path"], path)

    def _import(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import sessions", user_path(),
                                                "Sessions (*.session);;All files (*.*)")
        last = None
        for src in paths:
            p = new_entry_path()
            shutil.copyfile(src, p)
            meta = read_meta(p)
            kw = dict(created=now_iso())
            if meta["name"] == os.path.splitext(os.path.basename(p))[0]:
                kw["name"] = os.path.splitext(os.path.basename(src))[0]
            update_meta(p, **kw)
            last = p
        if last:
            self.reload(select=last)

    def _open_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(lib_dir()))

    def _delete(self):
        m = self._cur()
        if not m:
            return
        if QMessageBox.question(self, "Delete", f"Delete “{m['name']}” from the library?\n"
                                "(it is moved to library/_trash)") != QMessageBox.StandardButton.Yes:
            return
        trash = os.path.join(lib_dir(), "_trash")
        os.makedirs(trash, exist_ok=True)
        shutil.move(m["path"], os.path.join(trash, os.path.basename(m["path"])))
        self.page.library_deleted(m["path"])
        self.reload()
