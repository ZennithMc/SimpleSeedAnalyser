"""
qtui/settings_dialog.py — the Settings window (17/10 bis, redrawn 19/11).

A sidebar of categories (General, Searches, Map, Performance, Appearance,
Minecraft check, Files, About) whose current one is marked by a pill that slides
to it in the page's colour, and, on the right, the page: its header (icon, title,
one line) and its settings in groups — rounded cards, one row per setting (its
name, a sentence on what it does, its control). A search box filters every
setting at once. Nothing is written before Save; the settings that only apply
at the next launch carry a « restart » chip.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from . import brand, icons, motion, prefs, theme
from .widgets import LabeledSlider, body


class Switch(QAbstractButton):
    """An on / off switch (a checkable button drawn as a pill); the knob slides (19/11)."""
    def __init__(self, on=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True); self.setChecked(bool(on))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 24)
        self._t = 1.0 if on else 0.0
        self._an = None
        self.toggled.connect(self._slide)

    def sizeHint(self):
        return QSize(42, 24)

    def _slide(self, on):
        if self._an is not None:
            try:
                self._an.stop()
            except RuntimeError:
                pass
        t0, t1 = self._t, (1.0 if on else 0.0)

        def step(t):
            self._t = t0 + (t1 - t0) * t
            self.update()
        if not motion.enabled() or not self.isVisible():
            step(1.0)
            return
        self._an = motion.run(self, 170, step)

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        t = self._t
        p.setPen(Qt.PenStyle.NoPen)
        if not self.isEnabled():
            p.setOpacity(0.45)
        p.setBrush(motion.mix(theme.BG4, theme.ACCENT, t))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if t > 0.01:                                  # a light on the track, on
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            hi = QColor("#ffffff"); hi.setAlphaF(0.16 * t)
            lo = QColor("#ffffff"); lo.setAlphaF(0.0)
            g.setColorAt(0, hi); g.setColorAt(1, lo)
            p.setBrush(g)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * t
        sh = QColor(0, 0, 0, 60)
        p.setBrush(sh)
        p.drawEllipse(QRectF(x, r.top() + 4, d, d))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class SettingRow(QFrame):
    """One setting: title, description, control on the right."""
    def __init__(self, title, desc, control, restart=False, parent=None):
        super().__init__(parent)
        self.setObjectName("SetRow")
        self.search_text = f"{title} {desc}".lower()
        h = QHBoxLayout(self); h.setContentsMargins(14, 11, 14, 11); h.setSpacing(16)
        left = QVBoxLayout(); left.setSpacing(3)
        tr = QHBoxLayout(); tr.setSpacing(8)
        t = QLabel(title); t.setObjectName("SetTitle")
        tr.addWidget(t)
        if restart:
            chip = QLabel("restart"); chip.setObjectName("Chip"); chip.setProperty("state", "warn")
            chip.setToolTip("Applies at the next launch of SSA.")
            tr.addWidget(chip)
        tr.addStretch(1)
        left.addLayout(tr)
        if desc:
            d = body(desc, muted=True, small=True)
            left.addWidget(d)
        h.addLayout(left, 1)
        if control is not None:
            h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)


class SetGroup(QFrame):
    """(19/11) a rounded card holding a few rows, a thin line between two visible rows."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SetGroup")
        self.lay = QVBoxLayout(self); self.lay.setContentsMargins(4, 4, 4, 4); self.lay.setSpacing(0)
        self.rows = []
        self.label = None                       # its section title, hidden with it

    def add(self, row):
        self.lay.addWidget(row); self.rows.append(row)

    def refresh(self):
        vis = any(not r.isHidden() for r in self.rows)
        self.setVisible(vis)
        if self.label is not None:
            self.label.setVisible(vis)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        vis = [r for r in self.rows if not r.isHidden()]
        if len(vis) < 2:
            return
        p = QPainter(self)
        c = QColor(theme.LINE2); c.setAlphaF(min(1.0, c.alphaF() * 0.75))
        p.setPen(QPen(c, 1))
        for r in vis[1:]:
            y = r.geometry().top()
            p.drawLine(18, y, self.width() - 18, y)


class SettingsNav(QFrame):
    """(19/11) the categories: a pill in the page's colour slides to the current one,
    a faint one follows the mouse (as the top bar's tabs)."""
    currentRowChanged = Signal(int)

    def __init__(self, pages, parent=None):
        super().__init__(parent)
        self.setObjectName("SetNav")
        self.v = QVBoxLayout(self); self.v.setContentsMargins(0, 0, 0, 0); self.v.setSpacing(2)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        self.btns, self.cols = [], []
        for i, (_key, label, ic, ws) in enumerate(pages):
            b = QPushButton("  " + label.replace("&", "&&")); b.setObjectName("SetNavButton")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(36)
            b.setIconSize(QSize(18, 18))
            col = theme.ws_color(ws)
            icons.bind(b, ic, 18, "text2", col)
            b.installEventFilter(self)
            self.group.addButton(b, i)
            self.v.addWidget(b)
            self.btns.append(b); self.cols.append(QColor(col))
        self.v.addStretch(1)
        self._pill, self._pc, self._pan = None, QColor(theme.ACCENT), None
        self._ghost, self._gc, self._ga, self._gan = None, QColor(theme.ACCENT), 0.0, None
        self.group.idToggled.connect(lambda i, on: on and self._chosen(i))

    # the QListWidget API the window (and its tests) used
    def setCurrentRow(self, i):
        if 0 <= i < len(self.btns):
            self.btns[i].setChecked(True)

    def currentRow(self):
        return self.group.checkedId()

    def set_dim(self, i, dim):
        b = self.btns[i]
        if bool(b.property("dim")) != dim:
            b.setProperty("dim", dim); b.style().unpolish(b); b.style().polish(b)

    def _chosen(self, i):
        self._move_pill(i)
        self.currentRowChanged.emit(i)

    def _move_pill(self, i, animate=True):
        b = self.btns[i]
        r1, c1 = QRectF(b.geometry()), self.cols[i]
        if self._pan is not None:
            try:
                self._pan.stop()
            except RuntimeError:
                pass
        if self._pill is None or not animate or not motion.enabled() or not self.isVisible():
            self._pill, self._pc = r1, c1
            self.update()
            return
        r0, c0 = QRectF(self._pill), QColor(self._pc)

        def step(t):
            self._pill = QRectF(r0.x() + (r1.x() - r0.x()) * t, r0.y() + (r1.y() - r0.y()) * t,
                                r0.width() + (r1.width() - r0.width()) * t,
                                r0.height() + (r1.height() - r0.height()) * t)
            self._pc = motion.mix(c0, c1, t)
            self.update()
        self._pan = motion.run(self, 260, step)

    def eventFilter(self, o, e):
        from PySide6.QtCore import QEvent
        if o in self.btns:
            if e.type() == QEvent.Type.Enter:
                self._ghost_to(self.btns.index(o))
            elif e.type() == QEvent.Type.Leave:
                self._ghost_to(None)
        return False

    def _ghost_to(self, i):
        if self._gan is not None:
            try:
                self._gan.stop()
            except RuntimeError:
                pass
        a0 = self._ga
        if not motion.enabled():
            self._ga = 0.0
            self.update()
            return
        if i is None:
            def out(t):
                self._ga = a0 * (1.0 - t); self.update()
            self._gan = motion.run(self, 180, out)
            return
        r1, c1 = QRectF(self.btns[i].geometry()), self.cols[i]
        r0 = QRectF(self._ghost) if (self._ghost is not None and a0 > 0.05) else QRectF(r1)
        g0 = QColor(self._gc)

        def step(t):
            self._ghost = QRectF(r0.x() + (r1.x() - r0.x()) * t, r0.y() + (r1.y() - r0.y()) * t,
                                 r1.width(), r1.height())
            self._gc = motion.mix(g0, c1, t)
            self._ga = a0 + (1.0 - a0) * t
            self.update()
        self._gan = motion.run(self, 170, step)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        i = self.group.checkedId()
        if i >= 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._move_pill(i, animate=False))

    def showEvent(self, e):
        super().showEvent(e)
        i = self.group.checkedId()
        if i >= 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._move_pill(i, animate=False))

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._ghost is not None and self._ga > 0.02:
            c = QColor(self._gc); c.setAlphaF(0.09 * self._ga)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(c)
            p.drawRoundedRect(self._ghost.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        if self._pill is None:
            return
        r = self._pill.adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(r.topLeft(), r.topRight())
        c1, c2 = QColor(self._pc), QColor(self._pc)
        c1.setAlphaF(0.22); c2.setAlphaF(0.06)
        g.setColorAt(0, c1); g.setColorAt(1, c2)
        edge = QColor(self._pc); edge.setAlphaF(0.40)
        p.setBrush(g); p.setPen(QPen(edge, 1))
        p.drawRoundedRect(r, 10, 10)
        bar = QColor(self._pc)                     # and a short bar of its colour on the left
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(bar)
        p.drawRoundedRect(QRectF(r.left() + 3, r.center().y() - 7, 3, 14), 1.5, 1.5)


class SettingsDialog(QDialog):
    # key, label, icon, the workspace whose colour the page takes, its line under the title
    PAGES = [("general", "General", "gear", "home", "How SSA starts, and what it does when a search ends."),
             ("searches", "Searches", "sessions", "sessions", "What SSA decides by itself instead of asking you."),
             ("map", "Map", "map", "map", "Moving around, the structures and their icons."),
             ("performance", "Performance", "bolt", "loot", "Graphics card, memory and a self-test of the engines."),
             ("appearance", "Appearance", "palette", "drawing", "Theme, size of the interface, animations and help bubbles."),
             ("checker", "Minecraft check", "check", "seedcrack",
              "Some results are confirmed with the real Minecraft world generation (broken portals, 1.16 spawns)."),
             ("files", "Files & data", "folder", "map", "Where SSA keeps your work, its logs and its caches."),
             ("about", "About", "info", "home", "")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(980, 660)
        self.win = parent
        self.pending: dict = {}             # key -> value, written by Save
        self.rows: dict = {}                # page -> [SettingRow]
        self.groups: dict = {}              # page -> [SetGroup]
        self.extra_save = []                # callables run by Save (non-prefs settings)
        self._group = None

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        body_row = QHBoxLayout(); body_row.setContentsMargins(10, 10, 10, 0); body_row.setSpacing(10)
        outer.addLayout(body_row, 1)

        # ── sidebar ────────────────────────────────────────────────────────
        side = QFrame(); side.setObjectName("SidePanel"); side.setFixedWidth(226)
        sv = QVBoxLayout(side); sv.setContentsMargins(12, 16, 12, 12); sv.setSpacing(12)
        top = QHBoxLayout(); top.setSpacing(10)
        mk = QLabel(); mk.setFixedSize(26, 26); mk.setPixmap(icons.pixmap("gear", 22, theme.ACCENT))
        top.addWidget(mk)
        tt = QLabel("Settings"); tt.setObjectName("PageTitle"); top.addWidget(tt); top.addStretch(1)
        sv.addLayout(top)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search a setting…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", 16, theme.TEXT3), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._filter)
        sv.addWidget(self.search)
        self.nav = SettingsNav([(k, lab, ic, ws) for k, lab, ic, ws, _d in self.PAGES])
        sv.addWidget(self.nav, 1)
        import ssa_paths
        ver = QLabel(f"SimpleSeedAnalyser {ssa_paths.APP_VERSION}"); ver.setObjectName("Muted")
        ver.setStyleSheet("font-size: 8pt;")
        sv.addWidget(ver)
        body_row.addWidget(side)

        self.stack = motion.FadeStack(slide=16, ms=220)
        body_row.addWidget(self.stack, 1)
        self.page_index = {}
        for key, label, ic, ws, sub in self.PAGES:
            sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            inner = QWidget(); inner.setProperty("ws", ws); lay = QVBoxLayout(inner)
            lay.setContentsMargins(18, 14, 18, 18); lay.setSpacing(8)
            if key != "about":
                lay.addWidget(self._header(label, ic, ws, sub))
                lay.addSpacing(4)
            sc.setWidget(inner)
            self.page_index[key] = self.stack.addWidget(sc)
            self.rows[key] = []
            self.groups[key] = []
            self._group, self._page = None, key
            getattr(self, f"_page_{key}")(lay)
            lay.addStretch(1)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        # ── bottom bar ─────────────────────────────────────────────────────
        bar = QFrame(); bar.setObjectName("SetBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(14, 12, 14, 12); bl.setSpacing(8)
        rs = QPushButton("Reset all settings"); rs.setProperty("kind", "ghost")
        rs.clicked.connect(self._reset_all)
        bl.addWidget(rs)
        self.note = body("", muted=True, small=True); bl.addWidget(self.note, 1)
        c = QPushButton("Cancel"); c.clicked.connect(self.reject); bl.addWidget(c)
        ok = QPushButton("Save"); ok.setProperty("kind", "primary"); ok.clicked.connect(self._save)
        ok.setMinimumWidth(96)
        self._ok_shine = motion.Shine(ok)
        bl.addWidget(ok)
        outer.addWidget(bar)

    def _header(self, label, ic, ws, sub):
        w = QWidget()
        h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(14)
        tile = QLabel(); tile.setFixedSize(46, 46)
        tile.setPixmap(brand.icon_tile(ic, theme.ws_color(ws), 46, 24))
        h.addWidget(tile)
        v = QVBoxLayout(); v.setSpacing(2)
        t = QLabel(label); t.setObjectName("SetPageTitle"); v.addWidget(t)
        if sub:
            s = QLabel(sub); s.setObjectName("Muted"); s.setWordWrap(True); v.addWidget(s)
        h.addLayout(v, 1)
        return w

    # ── helpers ───────────────────────────────────────────────────────────────
    def _add(self, lay, page, title, desc, control, restart=False):
        row = SettingRow(title, desc, control, restart)
        if self._group is None:                     # rows before any section: a group of their own
            self._group = SetGroup(); lay.addWidget(self._group); self.groups[page].append(self._group)
        self._group.add(row)
        self.rows[page].append(row)
        return row

    def _section(self, lay, text):
        from .widgets import section_title
        lab = section_title(text); lab.setObjectName("SetSection")
        lay.addSpacing(8); lay.addWidget(lab)
        self._group = SetGroup(); self._group.label = lab
        lay.addWidget(self._group); self.groups[self._page].append(self._group)

    def _choice(self, key):
        cb = QComboBox(); cb.setMinimumWidth(250)
        for val, text in prefs.CHOICES[key][1]:
            cb.addItem(text, val)
        cb.setCurrentIndex(max(0, cb.findData(prefs.get(key))))
        cb.currentIndexChanged.connect(lambda _i, k=key, c=cb: self._set(k, c.currentData()))
        return cb

    def _switch(self, key):
        sw = Switch(prefs.get(key))
        sw.toggled.connect(lambda on, k=key: self._set(k, bool(on)))
        return sw

    def _combo_values(self, key, items, fmt=str):
        cb = QComboBox(); cb.setMinimumWidth(160)
        cur = prefs.get(key)
        for v, text in items:
            cb.addItem(text, v)
        i = cb.findData(cur)
        if i < 0:
            cb.addItem(fmt(cur), cur); i = cb.count() - 1
        cb.setCurrentIndex(i)
        cb.currentIndexChanged.connect(lambda _i, k=key, c=cb: self._set(k, c.currentData()))
        return cb

    def _set(self, key, value):
        self.pending[key] = value
        n = len(self.pending)
        self.note.setText(f"{n} change{'s' if n != 1 else ''} — Save to keep "
                          f"{'them' if n != 1 else 'it'}.")

    def _path_box(self, current, placeholder, on_pick, folder=True):
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        ed = QLineEdit(current); ed.setPlaceholderText(placeholder); ed.setMinimumWidth(300)
        b = QPushButton("Browse…"); b.setProperty("kind", "ghost")

        def _pick():
            d = QFileDialog.getExistingDirectory(self, placeholder, ed.text() or os.path.expanduser("~"))
            if d:
                ed.setText(d)
        b.clicked.connect(_pick)
        ed.textChanged.connect(on_pick)
        h.addWidget(ed, 1); h.addWidget(b)
        return w

    @staticmethod
    def _open_folder(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _button(self, text, fn, kind="ghost"):
        b = QPushButton(text); b.setProperty("kind", kind); b.clicked.connect(fn)
        return b

    # ── pages ─────────────────────────────────────────────────────────────────
    def _page_general(self, lay):
        self._section(lay, "At launch")
        self._add(lay, "general", "Restore the last state at launch",
                  "The window, the seed and version, the map view, the layers and the Stronghold "
                  "page come back as SSA was closed. The Sessions and Draw pages always keep "
                  "their autosave.", self._switch("restore_state"))
        self._add(lay, "general", "Threads at launch", prefs.CHOICES["default_threads"][2].replace("\n", " "),
                  self._choice("default_threads"))
        self._section(lay, "Notifications")
        self._add(lay, "general", "When a search ends", prefs.CHOICES["notify_done"][2].replace("\n", " "),
                  self._choice("notify_done"))

    def _page_searches(self, lay):
        self._section(lay, "Conditions")
        self._add(lay, "searches", "Name the conditions from their settings",
                  "A condition without a name of its own is called by what it looks for "
                  "(« Continentalness ≤ -11 500 · r 2 000 », « Village ×2 · abandoned »). "
                  "Give it a name and that one stays.", self._switch("auto_names"))
        self._section(lay, "Questions SSA can answer by itself")
        for key in ("resume_conditions_changed", "resume_space_changed", "topy_family"):
            self._add(lay, "searches", prefs.LABELS[key], prefs.CHOICES[key][2].replace("\n", " "),
                      self._choice(key))
        self._section(lay, "Safety")
        self._add(lay, "searches", "Pause when there are many results",
                  prefs.CHOICES["safety_first"][2].replace("\n", " "), self._choice("safety_first"))

    def _page_map(self, lay):
        # (15/11) in groups: moving around, the structures, their icons
        self._section(lay, "Moving around")
        self._add(lay, "map", "Inertia", "The map keeps sliding a little after a drag.",
                  self._switch("inertia"))
        sl = LabeledSlider("", 0.5, 2.0, float(prefs.get("zoom_speed")), "{:.1f}×", 0.1)
        sl.setMinimumWidth(220)
        sl.changed.connect(lambda v: self._set("zoom_speed", round(float(v), 2)))
        self._add(lay, "map", "Zoom speed", "How much one step of the mouse wheel zooms.", sl)
        self._section(lay, "Structures")
        self._add(lay, "map", "Show structures up to",
                  "Structure icons appear when zoomed in to this many blocks per pixel or closer. "
                  "Further out means more structures to compute (slower when zoomed out).",
                  self._combo_values("struct_max_bpp",
                                     [(16.0, "16 blocks / pixel"), (32.0, "32 blocks / pixel"),
                                      (48.0, "48 blocks / pixel (default)"), (64.0, "64 blocks / pixel"),
                                      (96.0, "96 blocks / pixel"), (128.0, "128 blocks / pixel")],
                                     lambda v: f"{v:g} blocks / pixel"))
        self._add(lay, "map", "Each structure's zoom",
                  "Choose, structure by structure, from which zoom its icons disappear "
                  "(the others follow the setting above).",
                  self._button("Per structure…", self._struct_limits))
        self._add(lay, "map", "New look of the structures",
                  "Structure plans get a rim of their structure's colour and clearer colours (the dark "
                  "ones: ancient city, fortress, ruined portal…). Off: the plans and boxes as before.",
                  self._switch("plans_enhanced"))
        self._add(lay, "map", "Structure boxes",
                  "The outline of each structure's size when zoomed in close.",
                  self._switch("struct_boxes"))
        self._add(lay, "map", "Hide structures on noise maps",
                  "No structure icons while a noise or height layer is shown; they come back "
                  "as soon as you return to the biomes.",
                  self._switch("hide_structs_on_noise"))
        self._section(lay, "Icons")
        mp = getattr(self.win, "map", None) if self.win is not None else None
        px = int(getattr(mp, "icon_px", 22) or 22)
        isl = LabeledSlider("", 12, 40, px, "{:.0f} px")
        isl.setMinimumWidth(220)
        isl.changed.connect(lambda v: self._set("__icon_px", int(v)))
        self._add(lay, "map", "Icon size",
                  "All the structure icons on the map. Each structure can be bigger or smaller: "
                  "the small arrow of its chip in the Layers panel.", isl)
        n = len((getattr(mp, "_style", {}) or {}).get("icon_scale") or {}) if mp is not None else 0
        rb = self._button("All the same size", lambda: self._set("__icon_reset", True)) if n else None
        self._add(lay, "map", "Sizes of each structure",
                  (f"{n} structure{'s' if n != 1 else ''} with an icon of its own size."
                   if n else "Every structure has the same icon size (the arrow of a chip in the "
                   "Layers panel changes one)."), rb)

    def _struct_limits(self):
        """(31/10) a window: one zoom per structure (« As above » by default)."""
        from PySide6.QtWidgets import QDialogButtonBox, QGridLayout
        import cubiomes_wrap as cw
        from . import struct_icons
        raw = self.pending.get("struct_max_bpp_by", prefs.get("struct_max_bpp_by"))
        cur = {}
        for part in str(raw).split(";"):
            k, _s, v = part.partition("=")
            try:
                if k.strip():
                    cur[k.strip()] = float(v)
            except ValueError:
                pass
        d = QDialog(self); d.setWindowTitle("Each structure's zoom")
        v = QVBoxLayout(d)
        v.addWidget(body("Icons of a structure disappear when zoomed out further than its value "
                         "(blocks per pixel).", small=True))
        sc = QScrollArea(); sc.setWidgetResizable(True); inner = QWidget(); g = QGridLayout(inner)
        g.setHorizontalSpacing(14); g.setVerticalSpacing(6)
        keys = list(cw.ALL_STRUCTURE_TYPES) + ["dungeon", "lava_lake"]
        vals = [None, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 48.0, 64.0, 96.0, 128.0]
        combos = {}
        for i, k in enumerate(keys):
            lab = QLabel(); lab.setPixmap(struct_icons.pixmap(k, 18))
            g.addWidget(lab, i, 0)
            g.addWidget(QLabel(struct_icons.label(k)), i, 1)
            cb = QComboBox(); cb.setMinimumWidth(170)
            for val in vals:
                cb.addItem("As above" if val is None else f"{val:g} blocks / pixel", val)
            j = cb.findData(cur.get(k))
            if j < 0 and cur.get(k) is not None:
                cb.addItem(f"{cur[k]:g} blocks / pixel", cur[k]); j = cb.count() - 1
            cb.setCurrentIndex(max(0, j))
            g.addWidget(cb, i, 2)
            combos[k] = cb
        sc.setWidget(inner); sc.setMinimumSize(460, 420)
        v.addWidget(sc, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept); bb.rejected.connect(d.reject)
        v.addWidget(bb)
        if d.exec() == QDialog.DialogCode.Accepted:
            out = ";".join(f"{k}={cb.currentData():g}" for k, cb in combos.items()
                           if cb.currentData() is not None)
            self._set("struct_max_bpp_by", out)

    def _page_performance(self, lay):
        self._section(lay, "Graphics card")
        self._add(lay, "performance", "Use the NVIDIA GPU",
                  "GPU pre-filters for structures, climate, strongholds and 12-eye searches "
                  "(RTX 20 series or newer, driver 580+). Off: everything runs on the CPU.",
                  self._switch("use_gpu"), restart=True)
        gpu = None                                # (15/11) the chip lives in the top bar (« unknown » before)
        if self.win is not None:
            gpu = getattr(self.win, "gpu", None) or getattr(getattr(self.win, "top", None), "gpu", None)
        st = (gpu.text() if gpu is not None else "") or "unknown"
        tip = (gpu.toolTip() if gpu is not None else "") or ""
        lab = body(st, small=True); lab.setToolTip(tip)
        self._add(lay, "performance", "GPU status", "What SSA found at launch (hover for details).", lab)
        self._add(lay, "performance", "Self-test",
                  "Checks the engines with known answers and the GPU, then shows a report "
                  "(also saved as selftest.txt).", self._button("Run the self-test", self._selftest))
        self._section(lay, "Memory")
        from . import memory                      # (15/11) the map's caches, from the RAM of this PC
        ram = memory.fmt(memory.total_ram() / (1 << 20))
        cb = self._combo_values("map_memory", memory.choices(), lambda v: memory.fmt(int(v)))
        cb.setToolTip(f"This PC: {ram} of RAM. Auto keeps 6 % of it for the map "
                      f"(at least 384 MB, at most 4 GB).")
        self._add(lay, "performance", "Memory for the map",
                  "What the map keeps (biomes, noises, structure pictures) so that coming back to "
                  "a place or a zoom is instant. More: faster on a big PC; less: lighter.", cb)
        now = 0
        if self.win is not None and getattr(self.win, "link", None) is not None:
            now = memory.usage(self.win.link.engine, getattr(self.win.link, "views", ()))
        use = body(f"{memory.fmt(now / (1 << 20))} of {memory.fmt(memory.budget_mb())}", small=True)
        self._add(lay, "performance", "Map memory in use", "Right now, estimated.", use)

    def _page_appearance(self, lay):
        self._section(lay, "Look")
        self._add(lay, "appearance", "Theme and colours",
                  "35+ themes, your own pictures, the two editable themes.",
                  self._button("Open the themes…", self._open_theme))
        fr = QComboBox(); fr.setMinimumWidth(160)
        for v, text in (("veil", "Veil"), ("glass", "Glass"), ("clear", "Clear"), ("bands", "Bands")):
            fr.addItem(text, v)
        fr.setCurrentIndex(max(0, fr.findData(theme.frame_style())))

        self._frame0 = theme.frame_style()

        def _frame_save(c=fr):
            from PySide6.QtCore import QSettings
            theme.FRAME_OVERRIDE = None
            if c.currentData() != self._frame0:
                QSettings("SSA", "SimpleSeedAnalyser").setValue("theme/frame", c.currentData())
                return True
            return False

        def _frame_live(_i, c=fr):
            # (22/10) previewed at once, like in the Themes window; Cancel undoes it
            self._set("__frame", c.currentData())
            theme.FRAME_OVERRIDE = c.currentData()
            self._reapply_theme()
        fr.currentIndexChanged.connect(_frame_live)
        self.extra_save.append(_frame_save)
        self._add(lay, "appearance", "Textured themes: frame",
                  "Veil: an even veil over the backdrop. Glass: lighter. Clear: the backdrop as it "
                  "is. Bands: translucent bars.", fr)
        self._add(lay, "appearance", "Interface scale",
                  "Size of the whole interface (text, buttons, panels).",
                  self._combo_values("ui_scale", [(90, "90 %"), (100, "100 % (default)"), (110, "110 %"),
                                                  (125, "125 %"), (150, "150 %")],
                                     lambda v: f"{v} %"), restart=True)
        self._section(lay, "Motion and help")
        self._add(lay, "appearance", "Animations",
                  "Page and panel transitions, the sliding tab, lights on the buttons, "
                  "the living background of SSA's own theme.",
                  self._switch("motion"))
        self._add(lay, "appearance", "Opening animation",
                  "SSA's mark builds itself, then Home opens (about 1.5 s; a click skips it). "
                  "Needs the animations.",
                  self._switch("launch_anim"))
        self._add(lay, "appearance", "Help bubbles",
                  "The small explanations that appear when the mouse rests on a button or a field. "
                  "Off: none at all, once you know SSA by heart.",
                  self._switch("tips"))

    def _page_checker(self, lay):
        self._section(lay, "Java and the seed-checker")
        try:
            import checker_client as cc
            jh_auto = cc.find_java_home() if not prefs.get("java_home") else None
            ck = cc.checker_dir()
        except Exception:
            cc, jh_auto, ck = None, None, ""
        self._add(lay, "checker", "Java 17 (JDK) folder",
                  "Empty: found automatically" + (f" ({jh_auto})." if jh_auto else " (none found yet)."),
                  self._path_box(prefs.get("java_home"), "JDK 17 folder (empty = automatic)",
                                 lambda t: self._set("java_home", t.strip())))
        self._ck_new = None

        def _ck(t):
            self._ck_new = t.strip()
            self._set("__checker", self._ck_new)

        def _ck_save():
            if self._ck_new and cc is not None and os.path.isdir(self._ck_new):
                cc.set_checker_dir(self._ck_new)
                return True
            return False
        self.extra_save.append(_ck_save)
        self._add(lay, "checker", "seed-checker project folder",
                  "The 1.16.1 seed-checker project used for the checks.",
                  self._path_box(ck or "", "seed-checker folder", _ck))

    def _page_files(self, lay):
        import ssa_paths
        dd, ld = ssa_paths.data_dir(), ssa_paths.log_dir()
        self._section(lay, "Folders")
        self._add(lay, "files", "Your data",
                  f"Sessions, library, drawings, palettes, themes: {dd}",
                  self._button("Open the folder", lambda: self._open_folder(dd)))
        self._add(lay, "files", "Logs",
                  f"console.log, crash.log, selftest.txt — send them with a bug report: {ld}",
                  self._button("Open the folder", lambda: self._open_folder(ld)))

        # (06/11) the game's textures for the loot (read from the player's Minecraft)
        self._section(lay, "Minecraft")
        from .mc_folder import choose_minecraft_folder, status as mc_status
        lab = {}

        def _pick():
            if choose_minecraft_folder(self) and lab.get("w") is not None:
                lab["w"].setText(mc_status())
        box = QWidget(); hb = QHBoxLayout(box); hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(8)
        lw = QLabel(mc_status()); lw.setObjectName("Muted"); lab["w"] = lw
        hb.addWidget(lw); hb.addWidget(self._button("Choose…", _pick))
        self._add(lay, "files", "Minecraft (item textures)",
                  "The loot shows the game's own items, read from your Minecraft "
                  "(official launcher, Modrinth, Prism, CurseForge — found by itself).", box)

        def _clear():
            from .backdrops import clear_cache
            n = clear_cache()
            QMessageBox.information(self, "Backgrounds", f"{n} cached background picture"
                                    f"{'s' if n != 1 else ''} removed: they are drawn again when needed.")
        self._section(lay, "Cache")
        self._add(lay, "files", "Theme backgrounds cache",
                  "The textured backgrounds are drawn once and kept in the temporary folder.",
                  self._button("Clear the cache", _clear))

    def _page_about(self, lay):
        import ssa_paths
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        hero = QFrame(); hero.setObjectName("AboutHero")
        hv = QHBoxLayout(hero); hv.setContentsMargins(22, 20, 22, 20); hv.setSpacing(18)
        mk = QLabel(); mk.setFixedSize(64, 64); mk.setPixmap(brand.mark(64))
        self._mark_shine = motion.Shine(mk, hover=True, every=(6.0, 12.0), radius=16)
        hv.addWidget(mk, 0, Qt.AlignmentFlag.AlignTop)
        tv = QVBoxLayout(); tv.setSpacing(4)
        nm = QHBoxLayout(); nm.setSpacing(10)
        t = QLabel("SimpleSeedAnalyser"); t.setObjectName("AboutName"); nm.addWidget(t)
        chip = QLabel(f"v{ssa_paths.APP_VERSION}"); chip.setObjectName("Chip"); chip.setProperty("state", "ok")
        nm.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter); nm.addStretch(1)
        tv.addLayout(nm)
        tv.addWidget(body("Free seedfinding tool for Minecraft Java (1.16 → 26.3), built on Cubiomes.",
                          muted=True))
        btns = QHBoxLayout(); btns.setSpacing(8)
        gh = QPushButton("  GitHub"); gh.setProperty("kind", "primary"); icons.bind(gh, "globe", 16, "on_accent", "on_accent")
        gh.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/ZennithMc/SimpleSeedAnalyser")))
        btns.addWidget(gh)
        dc = QPushButton("  Copy my Discord"); dc.setProperty("kind", "ghost"); icons.bind(dc, "copy", 16)

        def _copy():
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText("zennith7402")
            dc.setText("  Copied: @zennith7402")
        dc.clicked.connect(_copy)
        btns.addWidget(dc); btns.addStretch(1)
        tv.addSpacing(6); tv.addLayout(btns)
        hv.addLayout(tv, 1)
        lay.addWidget(hero)
        self._section(lay, "Credits")
        for who, what in (("Cubitect", "Cubiomes and Cubiomes Viewer: the world generation SSA is built on"),
                          ("Gaider10 (Andrew)", "the 12-eye method and the tree / population crackers"),
                          ("jellejurre", "the seed-checker project used by the Minecraft check"),
                          ("xpple", "the Cubiomes fork with the newest versions")):
            self._add(lay, "about", who, what[:1].upper() + what[1:] + ".", None)
        self._section(lay, "Licence")
        lic = os.path.join(here, "LICENSE")
        third = os.path.join(here, "THIRD_PARTY_LICENSES.txt")
        box = QWidget(); hb = QHBoxLayout(box); hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(6)
        for name, path in (("LICENSE", lic), ("Third parties", third)):
            if os.path.isfile(path):
                hb.addWidget(self._button(name, lambda _c=False, f=path: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(f))))
        self._add(lay, "about", "GPL-3.0", "SSA is free software: use it, change it and share it under the "
                  "same licence.", box if hb.count() else None)
        self._add(lay, "about", "Bugs and ideas", "On Discord, @zennith7402 — with console.log and crash.log "
                  "(Files & data → Logs) for a bug.", None)

    # ── actions ───────────────────────────────────────────────────────────────
    def _selftest(self):
        import subprocess
        try:
            if getattr(sys, "frozen", False):
                args = [sys.executable, "--selftest"]
            else:
                here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                args = [sys.executable, os.path.join(here, "ssa_qt.py"), "--selftest"]
            subprocess.Popen(args)
        except Exception as e:
            QMessageBox.warning(self, "Self-test", f"Could not start the self-test:\n{e}")

    def _open_theme(self):
        from .theme_dialog import ThemeDialog
        ThemeDialog(self.win or self).exec()

    def _filter(self, text):
        q = text.strip().lower()
        first = None
        for i, (key, *_rest) in enumerate(self.PAGES):
            n = 0
            for r in self.rows[key]:
                vis = not q or q in r.search_text
                r.setVisible(vis); n += vis
            for g in self.groups[key]:
                g.refresh()
            self.nav.set_dim(i, bool(q) and not n)
            if q and n and first is None:
                first = i
        if first is not None and q:
            self.nav.setCurrentRow(first)

    def _reset_all(self):
        if QMessageBox.question(self, "Reset", "Back to the defaults of every setting of this "
                                "window?") != QMessageBox.StandardButton.Yes:
            return
        prefs.reset()
        self.pending.clear()
        self.accept()

    def _reapply_theme(self):
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            theme.apply(QApplication.instance(), theme.THEME)
        finally:
            QApplication.restoreOverrideCursor()

    def reject(self):
        if theme.FRAME_OVERRIDE is not None:          # (22/10) undo the live preview
            theme.FRAME_OVERRIDE = None
            self._reapply_theme()
        super().reject()

    def _save(self):
        restart = False
        for k, v in self.pending.items():
            if k.startswith("__"):
                continue
            if k in prefs.RESTART and v != prefs.get(k):
                restart = True
            prefs.set(k, v)
        if "auto_names" in self.pending and self.win is not None:       # (15/11) the names again
            for pg in (getattr(self.win, "sessions", None), getattr(self.win, "strongholds", None)):
                tree, model = getattr(pg, "tree", None), getattr(pg, "model", None)
                if tree is not None and model is not None and hasattr(tree, "fill"):
                    try:
                        tree.fill(model, keep_uid=tree.current_uid())
                    except Exception:                                   # noqa: BLE001
                        pass
        if ("__icon_px" in self.pending or "__icon_reset" in self.pending) and self.win is not None:
            mp, lp = getattr(self.win, "map", None), getattr(self.win, "layers", None)
            if mp is not None:
                if self.pending.get("__icon_reset"):
                    mp._style["icon_scale"] = {}
                    if lp is not None:
                        for cb in getattr(lp, "boxes", {}).values():
                            if hasattr(cb, "size_tag"):
                                cb.size_tag = ""; cb.update()
                if "__icon_px" in self.pending:
                    mp.icon_px = int(self.pending["__icon_px"])
                    sz = getattr(lp, "size", None) if lp is not None else None
                    if sz is not None and hasattr(sz, "slider"):        # the Layers panel's slider
                        sz.slider.blockSignals(True)
                        sz.slider.setValue(int(round(mp.icon_px / sz.scale)))
                        sz.slider.blockSignals(False)
                        sz._on(sz.slider.value(), emit=False)
                from .layers_panel import _save_icon_style
                _save_icon_style(mp._style)
                if mp.link is not None:
                    mp.link.repaint_all()
                else:
                    mp.update()
        if "map_memory" in self.pending:
            from . import memory
            memory.apply()
        changed_theme = False
        for fn in self.extra_save:
            try:
                if fn() and fn.__name__ == "_frame_save":
                    changed_theme = True
            except Exception:
                pass
        if changed_theme:
            from PySide6.QtWidgets import QApplication
            theme.apply(QApplication.instance(), theme.THEME)
        if restart:
            QMessageBox.information(self, "Settings", "Some changes apply at the next launch of SSA.")
        self.accept()
