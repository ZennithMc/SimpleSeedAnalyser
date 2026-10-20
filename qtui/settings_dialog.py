"""
qtui/settings_dialog.py — the Settings window (17/10 bis).

A sidebar of categories (General, Searches, Map, Performance, Appearance,
Minecraft check, Files, About) and, on the right, one card per setting: its
name, a sentence on what it does and its control. A search box filters every
setting at once. Nothing is written before Save; the settings that only apply
at the next launch carry a « restart » chip.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from . import icons, prefs, theme
from .widgets import LabeledSlider, body


class Switch(QAbstractButton):
    """An on / off switch (a checkable button drawn as a pill)."""
    def __init__(self, on=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True); self.setChecked(bool(on))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 24)

    def sizeHint(self):
        return QSize(42, 24)

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT if on else theme.BG4))
        if not self.isEnabled():
            p.setOpacity(0.45)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.right() - d - 3 if on else r.left() + 3
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class SettingRow(QFrame):
    """One setting: title, description, control on the right."""
    def __init__(self, title, desc, control, restart=False, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.search_text = f"{title} {desc}".lower()
        h = QHBoxLayout(self); h.setContentsMargins(14, 11, 14, 11); h.setSpacing(14)
        left = QVBoxLayout(); left.setSpacing(3)
        tr = QHBoxLayout(); tr.setSpacing(8)
        t = QLabel(title); t.setStyleSheet("font-weight: 600;")
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


class SettingsDialog(QDialog):
    PAGES = [("general", "General", "home"), ("searches", "Searches", "sessions"),
             ("map", "Map", "map"), ("performance", "Performance", "gpu"),
             ("appearance", "Appearance", "palette"), ("checker", "Minecraft check", "check"),
             ("files", "Files & data", "folder"), ("about", "About", "legend")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(900, 620)
        self.win = parent
        self.pending: dict = {}             # key -> value, written by Save
        self.rows: dict = {}                # page -> [SettingRow]
        self.extra_save = []                # callables run by Save (non-prefs settings)

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        body_row = QHBoxLayout(); body_row.setContentsMargins(0, 0, 0, 0); body_row.setSpacing(0)
        outer.addLayout(body_row, 1)

        # ── sidebar ────────────────────────────────────────────────────────
        side = QFrame(); side.setObjectName("SidePanel"); side.setFixedWidth(220)
        sv = QVBoxLayout(side); sv.setContentsMargins(14, 16, 10, 12); sv.setSpacing(10)
        tt = QLabel("Settings"); tt.setObjectName("PageTitle"); sv.addWidget(tt)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search a setting…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        sv.addWidget(self.search)
        self.nav = QListWidget(); self.nav.setObjectName("SettingsNav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setStyleSheet("QListWidget#SettingsNav { background: transparent; border: 0; }"
                               "QListWidget#SettingsNav::item { padding: 7px 8px; border-radius: 6px; }")
        sv.addWidget(self.nav, 1)
        body_row.addWidget(side)

        self.stack = QStackedWidget()
        body_row.addWidget(self.stack, 1)
        self.page_index = {}
        for key, label, ic in self.PAGES:
            it = QListWidgetItem(icons.icon(ic, 18), label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(it)
            sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
            inner = QWidget(); lay = QVBoxLayout(inner)
            lay.setContentsMargins(22, 18, 22, 18); lay.setSpacing(8)
            head = QLabel(label); head.setObjectName("PanelTitle"); lay.addWidget(head)
            sc.setWidget(inner)
            self.page_index[key] = self.stack.addWidget(sc)
            self.rows[key] = []
            getattr(self, f"_page_{key}")(lay)
            lay.addStretch(1)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        # ── bottom bar ─────────────────────────────────────────────────────
        bar = QFrame(); bar.setObjectName("RunBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(14, 10, 14, 10); bl.setSpacing(8)
        rs = QPushButton("Reset all settings"); rs.setProperty("kind", "ghost")
        rs.setToolTip("Back to the defaults of every setting of this window.")
        rs.clicked.connect(self._reset_all)
        bl.addWidget(rs)
        self.note = body("", muted=True, small=True); bl.addWidget(self.note, 1)
        c = QPushButton("Cancel"); c.clicked.connect(self.reject); bl.addWidget(c)
        ok = QPushButton("Save"); ok.setProperty("kind", "primary"); ok.clicked.connect(self._save)
        bl.addWidget(ok)
        outer.addWidget(bar)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _add(self, lay, page, title, desc, control, restart=False):
        row = SettingRow(title, desc, control, restart)
        lay.addWidget(row)
        self.rows[page].append(row)
        return row

    def _section(self, lay, text):
        from .widgets import section_title
        lay.addSpacing(6); lay.addWidget(section_title(text))

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
        self._add(lay, "general", "Restore the last state at launch",
                  "The window, the seed and version, the map view, the layers and the Stronghold "
                  "page come back as SSA was closed. The Sessions and Draw pages always keep "
                  "their autosave.", self._switch("restore_state"))
        self._add(lay, "general", "Threads at launch", prefs.CHOICES["default_threads"][2].replace("\n", " "),
                  self._choice("default_threads"))
        self._add(lay, "general", "When a search ends", prefs.CHOICES["notify_done"][2].replace("\n", " "),
                  self._choice("notify_done"))

    def _page_searches(self, lay):
        self._section(lay, "Questions SSA can answer by itself")
        for key in ("resume_conditions_changed", "resume_space_changed", "topy_family"):
            self._add(lay, "searches", prefs.LABELS[key], prefs.CHOICES[key][2].replace("\n", " "),
                      self._choice(key))
        self._section(lay, "Safety")
        self._add(lay, "searches", "Pause when there are many results",
                  prefs.CHOICES["safety_first"][2].replace("\n", " "), self._choice("safety_first"))

    def _page_map(self, lay):
        self._add(lay, "map", "Inertia", "The map keeps sliding a little after a drag.",
                  self._switch("inertia"))
        sl = LabeledSlider("", 0.5, 2.0, float(prefs.get("zoom_speed")), "{:.1f}×", 0.1)
        sl.setMinimumWidth(220)
        sl.changed.connect(lambda v: self._set("zoom_speed", round(float(v), 2)))
        self._add(lay, "map", "Zoom speed", "How much one step of the mouse wheel zooms.", sl)
        self._add(lay, "map", "Show structures up to",
                  "Structure icons appear when zoomed in to this many blocks per pixel or closer. "
                  "Further out means more structures to compute (slower when zoomed out).",
                  self._combo_values("struct_max_bpp",
                                     [(16.0, "16 blocks / pixel"), (32.0, "32 blocks / pixel"),
                                      (48.0, "48 blocks / pixel (default)"), (64.0, "64 blocks / pixel"),
                                      (96.0, "96 blocks / pixel"), (128.0, "128 blocks / pixel")],
                                     lambda v: f"{v:g} blocks / pixel"))

    def _page_performance(self, lay):
        self._add(lay, "performance", "Use the NVIDIA GPU",
                  "GPU pre-filters for structures, climate, strongholds and 12-eye searches "
                  "(RTX 20 series or newer, driver 580+). Off: everything runs on the CPU.",
                  self._switch("use_gpu"), restart=True)
        gpu = getattr(self.win, "gpu", None) if self.win is not None else None
        st = (gpu.text() if gpu is not None else "") or "unknown"
        tip = (gpu.toolTip() if gpu is not None else "") or ""
        lab = body(st, small=True); lab.setToolTip(tip)
        self._add(lay, "performance", "GPU status", "What SSA found at launch (hover for details).", lab)
        self._add(lay, "performance", "Self-test",
                  "Checks the engines with known answers and the GPU, then shows a report "
                  "(also saved as selftest.txt).", self._button("Run the self-test", self._selftest))

    def _page_appearance(self, lay):
        self._add(lay, "appearance", "Theme and colours",
                  "35+ themes, your own pictures, the two editable themes.",
                  self._button("Open the themes…", self._open_theme))
        fr = QComboBox(); fr.setMinimumWidth(160)
        for v, text in (("veil", "Veil"), ("glass", "Glass"), ("clear", "Clear"), ("bands", "Bands")):
            fr.addItem(text, v)
        fr.setCurrentIndex(max(0, fr.findData(theme.frame_style())))

        def _frame_save(c=fr):
            from PySide6.QtCore import QSettings
            if c.currentData() != theme.frame_style():
                QSettings("SSA", "SimpleSeedAnalyser").setValue("theme/frame", c.currentData())
                return True
            return False
        fr.currentIndexChanged.connect(lambda _i: self._set("__frame", fr.currentData()))
        self.extra_save.append(_frame_save)
        self._add(lay, "appearance", "Textured themes: frame",
                  "Veil: an even veil over the backdrop. Glass: lighter. Clear: the backdrop as it "
                  "is. Bands: translucent bars.", fr)
        self._add(lay, "appearance", "Interface scale",
                  "Size of the whole interface (text, buttons, panels).",
                  self._combo_values("ui_scale", [(90, "90 %"), (100, "100 % (default)"), (110, "110 %"),
                                                  (125, "125 %"), (150, "150 %")],
                                     lambda v: f"{v} %"), restart=True)

    def _page_checker(self, lay):
        lay.addWidget(body("Some results are confirmed with the real Minecraft world generation "
                           "(broken portals, 1.16 spawns): SSA runs the seed-checker project with "
                           "a Java 17 JDK.", muted=True, small=True))
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
        self._add(lay, "files", "Your data",
                  f"Sessions, library, drawings, palettes, themes: {dd}",
                  self._button("Open the folder", lambda: self._open_folder(dd)))
        self._add(lay, "files", "Logs",
                  f"console.log, crash.log, selftest.txt — send them with a bug report: {ld}",
                  self._button("Open the folder", lambda: self._open_folder(ld)))

        def _clear():
            from .backdrops import clear_cache
            n = clear_cache()
            QMessageBox.information(self, "Backgrounds", f"{n} cached background picture"
                                    f"{'s' if n != 1 else ''} removed: they are drawn again when needed.")
        self._add(lay, "files", "Theme backgrounds cache",
                  "The textured backgrounds are drawn once and kept in the temporary folder.",
                  self._button("Clear the cache", _clear))

    def _page_about(self, lay):
        import ssa_paths
        lay.addWidget(body(f"<b>SimpleSeedAnalyser {ssa_paths.APP_VERSION}</b> — free seedfinding "
                           "tool for Minecraft Java, built on Cubiomes.", wrap=True))
        lay.addWidget(body("Credits: Cubitect (Cubiomes, Cubiomes Viewer), Gaider10 (12-eye), "
                           "jellejurre (seed-checker), xpple (Cubiomes fork). Licence: GPL-3.0 — "
                           "see LICENSE and THIRD_PARTY_LICENSES.txt.", muted=True, small=True))
        self._add(lay, "about", "Project page", "Releases, source code and the README.",
                  self._button("Open GitHub", lambda: QDesktopServices.openUrl(
                      QUrl("https://github.com/ZennithMc/SimpleSeedAnalyser"))))
        self._add(lay, "about", "Feedback", "Bugs and ideas: on Discord, @zennith7402.", None)

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
        for i, (key, _label, _ic) in enumerate(self.PAGES):
            n = 0
            for r in self.rows[key]:
                vis = not q or q in r.search_text
                r.setVisible(vis); n += vis
            it = self.nav.item(i)
            it.setForeground(QColor(theme.TEXT if (n or not q) else theme.TEXT3))
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

    def _save(self):
        restart = False
        for k, v in self.pending.items():
            if k.startswith("__"):
                continue
            if k in prefs.RESTART and v != prefs.get(k):
                restart = True
            prefs.set(k, v)
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
