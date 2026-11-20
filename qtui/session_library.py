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
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
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


# ── (19/11) a picture of its own for an entry: a screenshot or a seed's sigil ──
# Kept beside the sessions (never inside them: the .session files stay as they are):
# library/pictures.json  {entry file name: {"kind": "image" | "sigil", "seed": …}}
# library/pictures/<entry file name>.png  (a square, 256 px)
_PIC_CACHE: dict = {}
_PIX: dict = {}


def _pic_dir() -> str:
    d = os.path.join(lib_dir(), "pictures")
    os.makedirs(d, exist_ok=True)
    return d


def _pics() -> dict:
    f = os.path.join(lib_dir(), "pictures.json")
    try:
        key = os.path.getmtime(f)
    except OSError:
        return {}
    if _PIC_CACHE.get("key") != key:
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            _PIC_CACHE.update(key=key, data=d if isinstance(d, dict) else {})
        except Exception:
            _PIC_CACHE.update(key=key, data={})
    return dict(_PIC_CACHE.get("data") or {})


def _pics_save(d: dict) -> None:
    f = os.path.join(lib_dir(), "pictures.json")
    tmp = f + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, f)
    _PIC_CACHE.clear()


def picture_of(path):
    """The entry's own picture ({kind, seed} / {kind, file}) or None (its conditions' icons)."""
    return _pics().get(os.path.basename(path or ""))


def set_picture(path, spec) -> None:
    """spec None: back to the conditions' icons (the picture file goes to _trash)."""
    d = _pics()
    base = os.path.basename(path)
    old = d.pop(base, None)
    if old and old.get("kind") == "image" and (spec is None or spec.get("kind") != "image"):
        _trash_picture(base)
    if spec is not None:
        d[base] = spec
    _pics_save(d)


def set_picture_image(path, img) -> bool:
    """A picture (QImage) for the entry: its centre, square, 256 px."""
    from PySide6.QtGui import QImage
    if img is None or img.isNull():
        return False
    w, h = img.width(), img.height()
    k = min(w, h)
    sq = img.copy((w - k) // 2, (h - k) // 2, k, k)
    sq = sq.scaled(256, 256, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    base = os.path.basename(path)
    if not sq.convertToFormat(QImage.Format.Format_ARGB32).save(os.path.join(_pic_dir(), base + ".png"), "PNG"):
        return False
    set_picture(path, dict(kind="image", file=base + ".png"))
    return True


def _trash_picture(base) -> None:
    src = os.path.join(_pic_dir(), base + ".png")
    if os.path.isfile(src):
        trash = os.path.join(lib_dir(), "_trash")
        os.makedirs(trash, exist_ok=True)
        dst = os.path.join(trash, base + ".png")
        if os.path.exists(dst):
            dst = os.path.join(trash, f"{base}-{uuid.uuid4().hex[:4]}.png")
        shutil.move(src, dst)


def copy_picture(src_path, dst_path) -> None:
    spec = picture_of(src_path)
    if not spec:
        return
    spec = dict(spec)
    if spec.get("kind") == "image":
        a = os.path.join(_pic_dir(), os.path.basename(src_path) + ".png")
        if not os.path.isfile(a):
            return
        shutil.copyfile(a, os.path.join(_pic_dir(), os.path.basename(dst_path) + ".png"))
        spec["file"] = os.path.basename(dst_path) + ".png"
    d = _pics()
    d[os.path.basename(dst_path)] = spec
    _pics_save(d)


def drop_picture(path) -> None:
    """The entry left the library (deleted): its picture follows it to _trash."""
    d = _pics()
    base = os.path.basename(path)
    if d.pop(base, None) is not None:
        _trash_picture(base)
        _pics_save(d)


def gallery_dir() -> str:
    """(19/11) SSA's own pictures for sessions (tools/pictures/make_session_pictures.py)."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "session_pictures")


def gallery() -> list:
    """[{key, title, category, tags}] of the gallery (empty when it is not there)."""
    try:
        with open(os.path.join(gallery_dir(), "index.json"), encoding="utf-8") as f:
            return [g for g in json.load(f) if os.path.isfile(os.path.join(gallery_dir(), g["key"] + ".png"))]
    except Exception:
        return []


def rounded_pixmap(path, size: int, dpr: float = 2.0):
    """A picture file as a rounded square tile (cached)."""
    from PySide6.QtGui import QImage, QPainterPath, QPixmap
    try:
        key = (path, os.path.getmtime(path), size, dpr)
    except OSError:
        return None
    pm = _PIX.get(key)
    if pm is not None:
        return pm
    img = QImage(path)
    if img.isNull():
        return None
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, size * dpr < img.width())
    path_ = QPainterPath()
    path_.addRoundedRect(QRectF(0, 0, size, size), size * 0.26, size * 0.26)
    p.setClipPath(path_)
    p.drawImage(QRectF(0, 0, size, size), img)
    p.setClipping(False)
    edge = QColor("#ffffff"); edge.setAlphaF(0.14)
    p.setPen(QPen(edge, 1)); p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * 0.26, size * 0.26)
    p.end()
    if len(_PIX) > 600:
        _PIX.clear()
    _PIX[key] = pm
    return pm


def entry_pixmap(meta, size: int, dpr: float = 2.0):
    """The picture of a library entry: its own (screenshot / sigil) or its conditions' icons."""
    from . import brand
    spec = picture_of(meta.get("path")) if meta.get("path") else None
    if spec and spec.get("kind") == "sigil":
        try:
            return brand.sigil(int(spec.get("seed")), size, dpr)
        except (TypeError, ValueError):
            pass
    if spec and spec.get("kind") in ("image", "preset"):
        if spec.get("kind") == "preset":                    # (19/11) one of the gallery's pictures
            f = os.path.join(gallery_dir(), str(spec.get("name") or "") + ".png")
        else:
            f = os.path.join(_pic_dir(), str(spec.get("file") or ""))
        pm = rounded_pixmap(f, size, dpr)
        if pm is not None:
            return pm
    return brand.signature(meta.get("types"), size, dpr)


class _CardDelegate(QStyledItemDelegate):
    """(15/11) One session = one card: its picture (the icons of its conditions),
    its name and date, then small pills — version, conditions, results, paused, open."""
    H = 76

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
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        r = QRectF(opt.rect).adjusted(1, 3, -3, -3)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hov = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        a = QColor(theme.ACCENT)
        if sel:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, _soft(theme.ACCENT, 0.24))
            g.setColorAt(1, _soft(theme.ACCENT2 or theme.ACCENT, 0.10))
            bg, line = g, _soft(theme.ACCENT, 0.85)
        else:
            bg = _soft(theme.TEXT, 0.075 if hov else 0.045)
            line = QColor(theme.LINE2 if hov else theme.LINE)
        p.setPen(QPen(line, 1.0)); p.setBrush(bg)
        p.drawRoundedRect(r, 12, 12)
        ts = 48
        pm = entry_pixmap(m, ts)
        p.drawPixmap(QRectF(r.left() + 11, r.center().y() - ts / 2, ts, ts), pm, QRectF(pm.rect()))
        x0 = r.left() + 11 + ts + 12
        # line 1: name ……… date
        p.setFont(theme.ui_font(8.2))
        when = _when(m["modified"])
        dw = p.fontMetrics().horizontalAdvance(when)
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(r.right() - dw - 12, r.top() + 11, dw, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, when)
        p.setFont(theme.ui_font(10.2, QFont.Weight.DemiBold)); p.setPen(QColor(theme.TEXT))
        nw = r.right() - dw - 22 - x0
        p.drawText(QRectF(x0, r.top() + 10, nw, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(m["name"], Qt.TextElideMode.ElideRight, int(nw)))
        # line 2: pills
        pills = [(m["mc"].replace("Java ", ""), theme.TEXT2, 0.09),
                 (_plural(m["n_cond"], "condition"), theme.TEXT2, 0.09)]
        if m["n_res"]:
            pills.append((_plural(m["n_res"], "result"), theme.OK, 0.16))
        if m.get("paused"):
            pills.append(("paused", theme.WARN, 0.16))
        if self.dlg.is_open_entry(m["path"]):
            pills.append(("open", a.name(), 0.20))
        f = theme.num_font(7.8, QFont.Weight.DemiBold)
        p.setFont(f)
        fm = p.fontMetrics()
        x, y = x0, r.top() + 39
        for txt, col, al in pills:
            w = fm.horizontalAdvance(txt) + 14
            if x + w > r.right() - 8:
                break
            pr = QRectF(x, y, w, 19)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(_soft(col, al))
            p.drawRoundedRect(pr, 9.5, 9.5)
            p.setPen(QColor(col))
            p.drawText(pr, Qt.AlignmentFlag.AlignCenter, txt)
            x += w + 5
        p.restore()


def _soft(color, a):
    c = QColor(color); c.setAlphaF(a)
    return c


class _GalleryDelegate(QStyledItemDelegate):
    """(19/11) one picture of the gallery: its tile (lifted a little under the mouse,
    a ring of the accent when chosen) and its name."""
    T, W, H = 92, 118, 134

    def sizeHint(self, opt, idx):
        return QSize(self.W, self.H)

    def paint(self, p, opt, idx):
        g = idx.data(Qt.ItemDataRole.UserRole)
        if not g:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(opt.rect)
        sel = bool(opt.state & QStyle.StateFlag.State_Selected)
        hov = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        t = self.T + (4 if hov or sel else 0)
        x = r.center().x() - t / 2
        y = r.top() + 8 - (2 if hov else 0)
        if sel or hov:
            ring = QColor(theme.ACCENT); ring.setAlphaF(0.95 if sel else 0.45)
            glow = QColor(theme.ACCENT); glow.setAlphaF(0.16 if sel else 0.08)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(glow)
            p.drawRoundedRect(QRectF(x - 6, y - 6, t + 12, t + 12), t * 0.3, t * 0.3)
            p.setPen(QPen(ring, 2.0 if sel else 1.2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(x - 3, y - 3, t + 6, t + 6), t * 0.29, t * 0.29)
        pm = rounded_pixmap(os.path.join(gallery_dir(), g["key"] + ".png"), int(t))
        if pm is not None:
            p.drawPixmap(QRectF(x, y, t, t), pm, QRectF(pm.rect()))
        p.setFont(theme.ui_font(8.4, QFont.Weight.DemiBold if sel else QFont.Weight.Normal))
        p.setPen(QColor(theme.TEXT if sel or hov else theme.TEXT2))
        tr = QRectF(r.left() + 4, y + t + 8, r.width() - 8, 18)
        p.drawText(tr, Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(g["title"], Qt.TextElideMode.ElideRight, int(tr.width())))
        p.restore()


class PictureGallery(QDialog):
    """(19/11) SSA's pictures for a session: by kind (terrain, structures, the Nether…),
    a search over names and keywords (« mushroom », « ring », « sinkhole »…)."""
    CATS = ("All", "Terrain", "Biomes", "Landscapes", "Structures", "Underground", "Nether", "End", "Abstract")

    def __init__(self, parent=None, current=None):
        super().__init__(parent)
        self.setWindowTitle("Choose a picture")
        self.resize(860, 640)
        self.chosen = None
        self.items = gallery()
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(10)
        top = QHBoxLayout(); top.setSpacing(10)
        t = QLabel("Choose a picture"); t.setObjectName("PageTitle"); top.addWidget(t)
        n = QLabel(_plural(len(self.items), "picture")); n.setObjectName("Muted"); top.addWidget(n)
        top.addStretch(1)
        self.search = QLineEdit(); self.search.setPlaceholderText("mushroom, ring, sinkhole, nether…")
        self.search.setClearButtonEnabled(True); self.search.setMinimumWidth(260)
        self.search.addAction(icons.icon("search", 16, theme.TEXT3), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search)
        v.addLayout(top)
        chips = QHBoxLayout(); chips.setSpacing(4)
        from PySide6.QtWidgets import QButtonGroup
        self.cat_group = QButtonGroup(self); self.cat_group.setExclusive(True)
        have = {g["category"] for g in self.items}
        for c in self.CATS:
            if c != "All" and c not in have:
                continue
            b = QPushButton(c); b.setObjectName("VerPill"); b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(c == "All")
            b.clicked.connect(lambda _c=False: self._filter())
            self.cat_group.addButton(b)
            chips.addWidget(b)
        chips.addStretch(1)
        v.addLayout(chips)
        self.list = QListWidget(); self.list.setObjectName("LibList")
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(4)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.verticalScrollBar().setSingleStep(24)
        self.list.setItemDelegate(_GalleryDelegate(self.list))
        self.list.setStyleSheet("QListWidget#LibList { background: transparent; border: 0; outline: 0; }"
                                "QListWidget#LibList::item { background: transparent; border: 0; }")
        for g in self.items:
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, g)
            it.setSizeHint(QSize(_GalleryDelegate.W, _GalleryDelegate.H))
            self.list.addItem(it)
            if g["key"] == current:
                self.list.setCurrentItem(it)
        self.list.itemDoubleClicked.connect(lambda *_: self._use())
        self.list.currentItemChanged.connect(lambda *_: self._sync())
        v.addWidget(self.list, 1)
        bar = QHBoxLayout(); bar.setSpacing(8)
        self.name = QLabel(""); self.name.setObjectName("Muted"); bar.addWidget(self.name, 1)
        c = QPushButton("Cancel"); c.clicked.connect(self.reject); bar.addWidget(c)
        self.ok = QPushButton("Use this picture"); self.ok.setProperty("kind", "primary")
        self.ok.clicked.connect(self._use); bar.addWidget(self.ok)
        v.addLayout(bar)
        self._sync()
        if self.list.currentItem() is not None:
            self.list.scrollToItem(self.list.currentItem())

    def _filter(self, *_):
        q = self.search.text().strip().lower()
        b = self.cat_group.checkedButton()
        cat = b.text() if b is not None else "All"
        for i in range(self.list.count()):
            it = self.list.item(i)
            g = it.data(Qt.ItemDataRole.UserRole)
            ok = (cat == "All" or g["category"] == cat) and \
                (not q or all(w in (g["title"] + " " + g["tags"] + " " + g["category"]).lower() for w in q.split()))
            it.setHidden(not ok)
        from . import motion
        motion.fade_in(self.list.viewport(), 180)

    def _sync(self):
        it = self.list.currentItem()
        g = it.data(Qt.ItemDataRole.UserRole) if it is not None and not it.isHidden() else None
        self.ok.setEnabled(g is not None)
        self.name.setText(f"{g['title']}  ·  {g['category']}" if g else "Pick a picture — double-click uses it.")

    def _use(self):
        it = self.list.currentItem()
        if it is None or it.isHidden():
            return
        self.chosen = it.data(Qt.ItemDataRole.UserRole)["key"]
        self.accept()


class _PicLabel(QLabel):
    """(19/11) the entry's picture in the library: under the mouse a veil and a pencil — a click
    chooses another one (a screenshot, a pasted picture, a seed's sigil)."""
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self._hov = 0.0
        self._an = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _hover(self, to):
        from . import motion
        if self._an is not None:
            try:
                self._an.stop()
            except RuntimeError:
                pass
        a = self._hov

        def step(t):
            self._hov = a + (to - a) * t
            self.update()
        if not motion.enabled():
            step(1.0)
            return
        self._an = motion.run(self, 140, step)

    def enterEvent(self, e):
        super().enterEvent(e)
        self._hover(1.0)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hover(0.0)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()) \
                and self.isEnabled():
            self.clicked.emit()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._hov <= 0.01 or not self.isEnabled():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        veil = QColor(0, 0, 0); veil.setAlphaF(0.45 * self._hov)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(veil)
        p.drawRoundedRect(r, r.width() * 0.26, r.width() * 0.26)
        p.setOpacity(self._hov)
        ic = icons.pixmap("edit", 22, "#ffffff")
        p.drawPixmap(QRectF(r.center().x() - 11, r.center().y() - 11, 22, 22), ic, QRectF(ic.rect()))


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
            QLineEdit#LibName {{ font-size: 15pt; font-weight: 700; padding: 6px 10px;
                                 background: transparent; border: 1px solid transparent; }}
            QLineEdit#LibName:hover {{ border-color: {theme.LINE2}; }}
            QLineEdit#LibName:focus {{ border-color: {theme.ACCENT}; background: {theme.BG2}; }}
            QPlainTextEdit#LibDesc {{ background: {theme.field_bg()}; border: 1px solid {theme.LINE2};
                                      border-radius: 10px; padding: 6px; }}
            QPlainTextEdit#LibDesc:focus {{ border-color: {theme.ACCENT}; }}
            QFrame#LibCard {{ background: {theme.PANEL or theme.BG1}; border: 1px solid {theme.LINE};
                              border-radius: 14px; }}
            QLabel#LibCount {{ color: {theme.TEXT3}; }}
            QLabel#LibMeta {{ color: {theme.TEXT3}; }}
            QTreeWidget#LibTree {{ background: transparent; border: 0; }}
        """)
        from .seed_library import SkyBackdrop          # (20/11) a new sky at each opening
        self._sky = SkyBackdrop(self)
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
        self.list.setSpacing(1)
        self.list.currentItemChanged.connect(lambda *_: self._show())
        self.list.itemDoubleClicked.connect(lambda *_: self._open())
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(
            lambda pos: self.list.itemAt(pos) is not None and self._picture_menu(self.list.viewport().mapToGlobal(pos)))
        lv.addWidget(self.list, 1)
        row = QHBoxLayout(); row.setSpacing(6)
        self.b_new = QPushButton("  Save current session")
        self.b_new.setProperty("kind", "primary")
        icons.bind(self.b_new, "plus", 16, "on_accent", "on_accent")
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
        # (15/11) the session's picture beside its name
        hd = QHBoxLayout(); hd.setSpacing(12)
        self.sign = _PicLabel(); self.sign.setFixedSize(64, 64)          # (19/11) a click: its picture
        self.sign.clicked.connect(lambda: self._picture_menu(self.sign.mapToGlobal(self.sign.rect().bottomLeft())))
        hd.addWidget(self.sign)
        hc = QVBoxLayout(); hc.setSpacing(0)
        self.name = QLineEdit(); self.name.setObjectName("LibName")
        self.name.setPlaceholderText("Name")
        self.name.editingFinished.connect(self._rename)
        hc.addWidget(self.name)
        self.info = QLabel(""); self.info.setObjectName("LibMeta")
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setContentsMargins(11, 0, 0, 2)
        hc.addWidget(self.info)
        hd.addLayout(hc, 1)
        rv.addLayout(hd)
        rv.addSpacing(4)
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
        icons.bind(self.b_open, "folder", 16, "on_accent", "on_accent")
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
            self.name.setText(""); self.desc.setPlainText(""); self.sign.clear()
            self.info.setText("No saved session yet — “Save current session” adds the one being edited.")
            self._loading = False
            return
        self.name.setText(m["name"])
        self.sign.setPixmap(entry_pixmap(m, 64))
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

    # ── (19/11) the picture of an entry ──────────────────────────────────────
    def _picture_menu(self, at):
        from PySide6.QtWidgets import QApplication, QMenu
        m = self._cur()
        if not m:
            return
        spec = picture_of(m["path"])
        menu = QMenu(self)
        a_gal = menu.addAction(icons.icon("palette", 16), "Choose from the gallery…")
        a_gal.setEnabled(bool(gallery()))
        a_img = menu.addAction(icons.icon("folder", 16), "Screenshot or picture…")
        clip = QApplication.clipboard().image()
        a_paste = menu.addAction(icons.icon("paste", 16), "Paste a picture")
        a_paste.setEnabled(not clip.isNull())
        menu.addSeparator()
        seed = int(getattr(getattr(self.page, "world", None), "seed", 0) or 0)
        from . import brand
        a_cur = menu.addAction(brand.sigil(seed, 16), f"Sigil of the current seed  ({seed})")
        a_any = menu.addAction(icons.icon("dice", 16), "Sigil of a seed…")
        menu.addSeparator()
        a_def = menu.addAction(icons.icon("sessions", 16), "Its conditions' icons")
        a_def.setCheckable(True); a_def.setChecked(spec is None)
        got = menu.exec(at)
        if got is None:
            return
        try:
            if got is a_gal:
                cur = spec.get("name") if spec and spec.get("kind") == "preset" else None
                d = PictureGallery(self, cur)
                if d.exec() != QDialog.DialogCode.Accepted or not d.chosen:
                    return
                set_picture(m["path"], dict(kind="preset", name=d.chosen))
            elif got is a_img:
                path, _ = QFileDialog.getOpenFileName(self, "A picture for this session", user_path(),
                                                      "Pictures (*.png *.jpg *.jpeg *.bmp *.webp *.gif)")
                if not path:
                    return
                from PySide6.QtGui import QImage
                if not set_picture_image(m["path"], QImage(path)):
                    QMessageBox.warning(self, "Picture", "This file could not be read as a picture.")
                    return
            elif got is a_paste:
                set_picture_image(m["path"], clip)
            elif got is a_cur:
                set_picture(m["path"], dict(kind="sigil", seed=str(seed)))
            elif got is a_any:
                txt, ok = QInputDialog.getText(self, "Sigil of a seed", "Seed (a number or any text):",
                                               text=str(seed))
                if not ok or not txt.strip():
                    return
                from .world import parse_seed
                v = parse_seed(txt.strip())
                if v is None:
                    return
                set_picture(m["path"], dict(kind="sigil", seed=str(v)))
            elif got is a_def:
                set_picture(m["path"], None)
        except Exception as e:                          # noqa: BLE001
            QMessageBox.warning(self, "Picture", f"Could not change the picture:\n{e}")
            return
        self.list.viewport().update()
        self._show()
        from . import motion
        motion.fade_in(self.sign, 260)

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
        try:
            copy_picture(m["path"], p)
        except Exception:                               # noqa: BLE001 — the copy keeps its icons
            pass
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
        try:
            drop_picture(m["path"])
        except Exception:                               # noqa: BLE001
            pass
        self.page.library_deleted(m["path"])
        self.reload()
