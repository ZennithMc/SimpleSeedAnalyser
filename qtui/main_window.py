"""
qtui/main_window.py — SSA main window (Qt).

Layout
  ┌ top bar ── seed · version · world type ─────────────────── GPU status ┐
  │ nav │                        page                                      │
  │ rail│  (Map · Sessions · Strongholds · Analyze · Drawing)              │
  └ status bar ───────────────────────────────────────────────────────────┘

The seed / version live in the top bar, always visible, shared by every page.
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

from . import icons, theme
from .layers_panel import LayersPanel
from .map_view import MapView
from .map_drawer import MapDrawer
from .map_link import MapLink
from .sessions_page import SessionsPage
from .stronghold_page import StrongholdPage
from .world import VERSION_NAMES, VERSION_TIPS, WorldState, parse_seed, random_seed

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def field_label(text):
    lab = QLabel(text.upper()); lab.setObjectName("FieldLabel")
    return lab


class _GpuProbe(QObject):
    done = Signal(bool, str, str)            # usable, short text, details


class TopBar(QFrame):
    def __init__(self, world: WorldState, parent=None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.world = world
        self.setFixedHeight(58)
        lay = QHBoxLayout(self); lay.setContentsMargins(16, 6, 16, 6); lay.setSpacing(10)

        name = QLabel("SimpleSeedAnalyser"); name.setObjectName("AppName")
        lay.addWidget(name)
        lay.addSpacing(18)

        # seed
        col = QVBoxLayout(); col.setSpacing(2)
        col.addWidget(field_label("Seed"))
        srow = QHBoxLayout(); srow.setSpacing(4)
        self.seed = QLineEdit(str(world.seed)); self.seed.setObjectName("SeedEdit")
        self.seed.setMinimumWidth(250)
        self.seed.setToolTip("Numeric seed, or any text (hashed exactly like Minecraft does).\n"
                             "Press Enter to apply.")
        self.seed.returnPressed.connect(self._apply_seed)
        self.seed.editingFinished.connect(self._apply_seed)
        srow.addWidget(self.seed)
        for ic, tip, fn in (("dice", "Random seed  (Ctrl+R)", self.random),
                            ("copy", "Copy seed", self._copy)):
            b = QToolButton(); icons.bind(b, ic, 18); b.setToolTip(tip)
            b.setFixedSize(30, 30); b.clicked.connect(fn); srow.addWidget(b)
        col.addLayout(srow)
        lay.addLayout(col)

        # version
        col = QVBoxLayout(); col.setSpacing(2)
        col.addWidget(field_label("Version"))
        # (31/10) a button: the major versions, each opening its exact versions
        self.version = QToolButton(); self.version.setObjectName("VersionButton")
        self.version.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.version.setMinimumWidth(130)
        self.version.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_version_menu()
        self._show_version()
        col.addWidget(self.version)
        lay.addLayout(col)

        # world type
        col = QVBoxLayout(); col.setSpacing(2)
        col.addWidget(field_label("World type"))
        self.large = QCheckBox("Large biomes")
        self.large.setChecked(world.large_biomes)
        self.large.toggled.connect(lambda on: self.world.set(large_biomes=on))
        col.addWidget(self.large)
        lay.addLayout(col)

        lay.addStretch(1)
        self.gpu = QLabel("GPU: checking…"); self.gpu.setObjectName("Chip")
        self.gpu.setProperty("state", "off")
        self.gpu.setFixedHeight(22)
        lay.addWidget(self.gpu, 0, Qt.AlignmentFlag.AlignVCenter)
        world.changed.connect(self._sync)

    def _apply_seed(self):
        v = parse_seed(self.seed.text())
        if v is None:
            self.seed.setText(str(self.world.seed))
            return
        if self.seed.text().strip() != str(v):
            self.seed.setToolTip(f"Text seed “{self.seed.text().strip()}” → {v}")
        self.world.set(seed=v)
        self.seed.setText(str(v))

    def random(self):
        self.world.set(seed=random_seed())

    def _build_version_menu(self):
        from PySide6.QtWidgets import QMenu
        from .world import GAME_VERSIONS, major_of
        m = QMenu(self.version)
        majors = {}
        for name in VERSION_NAMES:
            for ex in GAME_VERSIONS.get(name, []):
                majors.setdefault(major_of(ex), []).append((ex, name))
        for mj, items in majors.items():
            sub = m.addMenu(f"Java {mj}" if mj != "26" else "Java 26")
            for ex, name in items:
                a = sub.addAction(f"Java {ex}")
                a.setCheckable(True)
                tip = VERSION_TIPS.get(name)
                if tip:
                    a.setToolTip(f"Same world as {tip}")
                a.triggered.connect(lambda _c=False, e=ex, n=name: self._pick_version(e, n))
        m.setToolTipsVisible(True)
        m.aboutToShow.connect(self._check_version_menu)
        self._vmenu = m
        self.version.setMenu(m)

    def _check_version_menu(self):
        from .world import current_exact
        cur = current_exact(self.world.version_name)
        for sub in self._vmenu.actions():
            sm = sub.menu()
            hit = False
            for a in (sm.actions() if sm else ()):
                on = a.text() == f"Java {cur}"
                a.setChecked(on)
                hit = hit or on
            if sm is not None:
                f = sm.menuAction().font(); f.setBold(hit); sm.menuAction().setFont(f)

    def _pick_version(self, exact, name):
        from .world import set_current_exact
        set_current_exact(exact)
        try:
            from PySide6.QtCore import QSettings
            QSettings("SSA", "SimpleSeedAnalyser").setValue("ui/exact_version", exact)
        except Exception:
            pass
        if self.world.version_name != name:
            self.world.set(version_name=name)
        self._show_version()
        ws = getattr(self.window(), "_world_status", None)      # (13/11) the status bar too
        if ws is not None:
            ws()

    def _show_version(self):
        from .world import current_exact, set_current_exact, _EXACT
        if _EXACT[0] is None:
            try:
                from PySide6.QtCore import QSettings
                v = QSettings("SSA", "SimpleSeedAnalyser").value("ui/exact_version", "")
                if v:
                    set_current_exact(str(v))
            except Exception:
                pass
        ex = current_exact(self.world.version_name)
        self.version.setText(f"Java {ex}")
        tip = VERSION_TIPS.get(self.world.version_name)
        self.version.setToolTip(f"Same world generation: {tip}" if tip else "")

    def _copy(self):
        QGuiApplication.clipboard().setText(str(self.world.seed))

    def _sync(self):
        if parse_seed(self.seed.text()) != self.world.seed:
            self.seed.setText(str(self.world.seed))
        self._show_version()
        if self.large.isChecked() != self.world.large_biomes:
            self.large.setChecked(self.world.large_biomes)

    def set_gpu(self, ok, text, details):
        self.gpu.setText(text)
        self.gpu.setProperty("state", "ok" if ok else "off")
        self.gpu.setToolTip(details)
        self.gpu.style().unpolish(self.gpu); self.gpu.style().polish(self.gpu)


class NavRail(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(76)
        self.lay = QVBoxLayout(self); self.lay.setContentsMargins(8, 10, 8, 10); self.lay.setSpacing(4)
        self.group = QButtonGroup(self); self.group.setExclusive(True)

    def add(self, key, label, icon_name, tip):
        b = QToolButton(); b.setObjectName("NavButton")
        icons.bind(b, icon_name, 22, "text3", "text")
        b.setIconSize(QSize(22, 22))
        b.setText(label); b.setToolTip(tip)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        b.setCheckable(True); b.setFixedSize(60, 54)
        b.setProperty("page", key)
        self.group.addButton(b)
        self.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        return b


class Placeholder(QWidget):
    """Workspace not yet rebuilt in Qt: a short honest message."""
    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        outer = QVBoxLayout(self); outer.addStretch(1)
        card = QFrame(); card.setObjectName("Card"); card.setFixedWidth(520)
        lay = QVBoxLayout(card); lay.setContentsMargins(28, 26, 28, 26); lay.setSpacing(10)
        t = QLabel(title); t.setObjectName("PageTitle"); lay.addWidget(t)
        b = QLabel(text); b.setObjectName("Body"); b.setWordWrap(True); lay.addWidget(b)
        h = QHBoxLayout(); h.addStretch(1); h.addWidget(card); h.addStretch(1)
        outer.addLayout(h); outer.addStretch(2)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SimpleSeedAnalyser")
        self.resize(1480, 900)
        self.world = WorldState()

        root = QWidget(); root.setObjectName("Root")
        v = QVBoxLayout(root); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        self.top = TopBar(self.world); v.addWidget(self.top)
        mid = QHBoxLayout(); mid.setContentsMargins(0, 0, 0, 0); mid.setSpacing(0)
        v.addLayout(mid, 1)
        self.nav = NavRail(); mid.addWidget(self.nav)
        self.pages = QStackedWidget(); mid.addWidget(self.pages, 1)

        # Sessions page (own map, same world)
        # the wheel scrolls panels, never changes a slider / combo / spin box
        from .widgets import WheelGuard
        self._wheel_guard = WheelGuard(self)
        # (17/10) on the widgets themselves, found by a light periodic scan of
        # the open windows (forms and dialogs are built later), not on the
        # whole application (see WheelGuard)
        from PySide6.QtCore import QTimer as _QT

        def _scan():
            for top in QApplication.topLevelWidgets():
                if top.isVisible():
                    self._wheel_guard.guard(top)
        self._wheel_scan = _QT(self, interval=700, timeout=_scan)
        self._wheel_scan.start()
        _QT.singleShot(0, _scan)
        # every map of the window shares engine, view and layers (map_link.py)
        self.link = MapLink(self.world, self)
        self.sessions = SessionsPage(self.world, link=self.link)
        self.strongholds = StrongholdPage(self.world, link=self.link)
        from .draw_page import DrawPage
        self.drawing = DrawPage(self.world, link=self.link)
        from .loot_page import LootPage                    # (06/11) replaces "Stronghold"
        self.loot = LootPage(self.world)
        self.loot.status.connect(lambda t: self._flash(t))
        self.loot.openOnMap.connect(self._crack_open_map)
        from .seedcrack_page import SeedcrackPage          # (05/11) replaces "Analyze"
        self.seedcrack = SeedcrackPage(self.world)
        self.seedcrack.openOnMap.connect(self._crack_open_map)
        self.seedcrack.sendSisters.connect(self._crack_sisters)

        # Map page
        self.map = MapView(self.world, link=self.link)
        self.layers = LayersPanel(self.map, self.world)
        mp = QWidget(); mp.setObjectName("Page")
        h = QHBoxLayout(mp)                  # floating surfaces on the gradient base
        h.setContentsMargins(0, 2, 10, 4); h.setSpacing(10)
        h.addWidget(self.map, 1)
        # Layers = a drawer over the right edge of each map (one shared panel)
        self.drawers = {"map": MapDrawer(self.map, opened=True),
                        "sessions": MapDrawer(self.sessions.map),
                        "strongholds": MapDrawer(self.strongholds.map),
                        "drawing": MapDrawer(self.drawing.map)}
        self.drawers["map"].set_content(self.layers)

        pages = [
            ("map", "Map", "map", "Explore the seed: biomes, structures, landmarks", mp),
            ("sessions", "Sessions", "sessions", "Custom search sessions", self.sessions),
            # (06/11) the Stronghold page is gone from the rail (all of it is in the
            # Sessions' Stronghold condition); its object stays for the shared state
            ("loot", "Loot", "chest", "Find seeds by the loot of their chests · every loot table",
             self.loot),
            ("seedcrack", "Crack", "forest", "Seedcracking: rebuild the trees you saw in 3D "
                                             "and crack the structure seed (Java 1.16 – 1.17)",
             self.seedcrack),
            ("drawing", "Draw", "brush", "Draw a terrain over the map and find the seeds "
                                         "that look like it", self.drawing),
        ]
        self._page_index = {}
        for key, label, ic, tip, w in pages:
            self._page_index[key] = self.pages.addWidget(w)
            b = self.nav.add(key, label, ic, tip)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
        self.nav.lay.addStretch(1)
        _tools = [("Tools", "tools", "Paste seed + coordinates · measure · seed-list converter · "
                                     "legend · fullscreen", None),
                  ("Keys", "keyboard", "Keyboard shortcuts  (F1)", self.show_shortcuts),
                  ("Theme", "palette", "Appearance: themes and colours", self.open_theme),
                  ("Settings", "gear", "Settings: what SSA does by itself instead of asking",
                   self.open_settings)]
        for text, ic, tip, fn in _tools:
            cl = QToolButton(); cl.setObjectName("NavButton"); cl.setText(text)
            icons.bind(cl, ic, 22, "text3", "text"); cl.setIconSize(QSize(22, 22))
            cl.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon); cl.setFixedSize(60, 54)
            cl.setToolTip(tip)
            if fn is None:                       # the Tools menu
                self.tools_btn = cl
                cl.clicked.connect(self._tools_menu)
            else:
                cl.clicked.connect(fn)
            self.nav.lay.addWidget(cl, 0, Qt.AlignmentFlag.AlignHCenter)
        self.nav.group.buttons()[0].setChecked(True)

        # status bar
        sb = QFrame(); sb.setObjectName("StatusBar"); sb.setFixedHeight(26)
        sl = QHBoxLayout(sb); sl.setContentsMargins(14, 0, 14, 0)
        self.status_left = QLabel(); self.status_right = QLabel()
        sl.addWidget(self.status_left); sl.addStretch(1); sl.addWidget(self.status_right)
        v.addWidget(sb)
        self.setCentralWidget(root)

        self.map.status.connect(self.status_right.setText)
        self.sessions.map.status.connect(self.status_right.setText)
        self.sessions.status.connect(self._flash)
        self.sessions.openMap.connect(lambda: self.show_page("map"))
        self.strongholds.map.status.connect(self.status_right.setText)
        self.strongholds.status.connect(self._flash)
        self.drawing.map.status.connect(self.status_right.setText)
        self.drawing.status.connect(self._flash)
        self.world.changed.connect(self._world_status)
        self._world_status()
        # version / large biomes locked while a search runs (a change applied
        # to the map but not to the running engine → results of another world)
        for r in (self.sessions.runner, self.strongholds.runner):
            r.stateChanged.connect(lambda _s: self._lock_world())
        _notice = os.environ.pop("SSA_CRASH_NOTICE", "")
        if _notice:                           # no console in the .exe: show it here
            from PySide6.QtCore import QTimer as _QT
            _QT.singleShot(1200, lambda: self._flash("⚠ " + _notice))
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.top.random)
        QShortcut(QKeySequence("Ctrl+G"), self, activated=self.map.goto_dialog)
        # noise layer from the keyboard, on every page with a map (27/09)
        from .layers_panel import NOISE_KEYS
        for name, key in NOISE_KEYS.items():
            QShortcut(QKeySequence(key), self, activated=lambda n=name: self._noise_key(n))
        QShortcut(QKeySequence("Alt+G"), self, activated=self._arrows_key)
        # octave of the climate noise shown: Alt+1…9 = only that octave (list
        # order 0A, 0B, 1A…), Alt+Shift+1…9 = up to it, Alt+0 = all (28/09)
        for k in range(10):
            QShortcut(QKeySequence(f"Alt+{k}"), self, activated=lambda k=k: self._octave_key(k, False))
            if k:
                QShortcut(QKeySequence(f"Alt+Shift+{k}"), self,
                          activated=lambda k=k: self._octave_key(k, True))
        QShortcut(QKeySequence("Alt+O"), self, activated=self._octave_mode_key)   # 08/10
        QShortcut(QKeySequence("Alt+Shift+G"), self, activated=self._arrow_mode_key)
        QShortcut(QKeySequence("Alt+S"), self, activated=self._seed_view_key)
        # (18/10) dimension of the maps: Ctrl+1 Overworld, Ctrl+2 Nether, Ctrl+3 End
        for _k, _d in (("Ctrl+1", 0), ("Ctrl+2", -1), ("Ctrl+3", 1)):
            QShortcut(QKeySequence(_k), self, activated=lambda d=_d: self._dim_key(d))
        QShortcut(QKeySequence("F1"), self, activated=self.show_shortcuts)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+Shift+V"), self, activated=self.paste_seed_coords)
        from PySide6.QtCore import QSettings
        try:
            self.map.set_legend(QSettings("SimpleSeedAnalyser", "SSA").value("map/legend", True, type=bool))
        except Exception:
            pass
        self.layers.legend.setChecked(self.map.legend_on())
        self.layers.legend.toggled.connect(self.set_legend)
        QShortcut(QKeySequence("Ctrl+/"), self, activated=self.show_shortcuts)
        self._probe_gpu()
        theme.signals.changed.connect(lambda: icons.refresh(self))
        # last session back as it was (autosave.session), then a light periodic
        # autosave (only when something changed) in case of a crash
        from PySide6.QtCore import QTimer
        from . import ui_state
        from . import prefs as _prefs
        # window, world, map, layers, Stronghold page (03/10) — unless turned off
        # in Settings (17/10)
        _ui = ui_state.restore(self) if _prefs.get("restore_state") else None
        for _pg in (self.sessions, self.strongholds, self.drawing):
            _t = getattr(_pg, "threads", None)
            if _t is not None:
                _t.setValue(_prefs.threads_at_launch(_t.value()))
        # a search that ends by itself flashes the taskbar (Settings)
        for _pg in (self.sessions, self.strongholds):
            _r = getattr(_pg, "runner", None)
            if _r is not None:
                _r.stateChanged.connect(lambda st, r=_r: self._search_ended(r, st))
        QTimer.singleShot(0, self.sessions.restore_autosave)
        QTimer.singleShot(0, self.drawing.restore_autosave)
        # the Draw page centres the shared map on its drawing: the saved view wins
        QTimer.singleShot(0, lambda: ui_state.restore_map(self, _ui))
        # saved soon after every change too (not only at close: SSA closed from its
        # console, killed or crashed, never runs closeEvent)
        self._ui_timer = QTimer(self, singleShot=True, interval=2000,
                                timeout=lambda: ui_state.save(self, heavy=False))
        for sig in (self.world.changed, self.layers.changed):
            sig.connect(self._ui_timer.start)
        self.link.viewMoved.connect(self._ui_timer.start)
        self._ui_sh_sig = None
        self._autosave_timer = QTimer(self, interval=90_000, timeout=self._periodic_autosave)
        self._autosave_timer.start()
        self._autosave_sig = None

    # ── keyboard: noise layers / arrows / help ─────────────────────────────
    def _page_has_map(self):
        w = self.pages.currentWidget()
        return w is not None and any(m.isVisible() for m in (self.map, self.sessions.map,
                                                             self.strongholds.map, self.drawing.map))

    def _dim_key(self, d):
        mp = self._current_map()
        if mp is None:
            return
        if not mp.DIMS_ALLOWED:
            self._flash("This page shows the Overworld only.")
            return
        mp.set_dim(d)
        self._flash({0: "Overworld", -1: "Nether  (coordinates ÷ 8)", 1: "End"}[d]
                    + f"   ·   Ctrl+{ {0: 1, -1: 2, 1: 3}[d] }")

    def _noise_key(self, name):
        if not self._page_has_map():
            return
        from .layers_panel import NOISE_CHOICES, NOISE_KEYS
        cb = self.layers.noise
        item = cb.model().item(NOISE_CHOICES.index(name))
        if item is not None and not item.isEnabled():
            self._flash("Noise layers exist from Java 1.18 (noise-based generation).")
            return
        if self.link.engine.seed_view:      # Alt+B (or a noise) leaves the structure-seed view
            self.link.engine.set_seed_view(False)
            self.link.repaint_all()
        cb.setCurrentText(name)
        self._flash(("Noise: " + name if name != "Off" else "Biomes (no noise)")
                    + f"   ·   {NOISE_KEYS[name]}")

    def _octave_key(self, k, upto):
        if not self._page_has_map():
            return
        from .map_engine import CLIMATE
        lp = self.layers
        if self.map.ov.get("noise") not in CLIMATE:
            self._flash("Octaves: choose a climate noise first (Alt+T / H / C / E / W).")
            return
        if k == 0:
            lp.oct_mode.set_value("All")
            lp._apply_octave()
            self._flash(f"{self.map.ov['noise']}: all octaves   ·   Alt+1…9 one octave, "
                        "Alt+Shift+1…9 up to it")
            return
        if k > lp.oct_list.count():
            self._flash(f"{self.map.ov['noise']} has {lp.oct_list.count()} octave streams "
                        f"(Alt+1 … Alt+{min(9, lp.oct_list.count())}).")
            return
        lp.oct_mode.set_value("Up to" if upto else "Only")
        lp.oct_list.setCurrentRow(k - 1)
        lp._apply_octave()
        name = lp.oct_list.item(k - 1).text().split()[0]
        self._flash(f"{self.map.ov['noise']}: {'up to' if upto else 'only'} octave {name}"
                    "   ·   Alt+0: all octaves")

    def _octave_mode_key(self):
        """Alt+O: the same octave, Up to ↔ Only (All → Only)."""
        if not self._page_has_map():
            return
        from .map_engine import CLIMATE
        lp = self.layers
        if self.map.ov.get("noise") not in CLIMATE:
            self._flash("Octaves: choose a climate noise first (Alt+T / H / C / E / W).")
            return
        cur = lp.oct_mode.value()
        new = "Up to" if cur == "Only" else "Only"
        if lp.oct_list.currentRow() < 0 and lp.oct_list.count():
            lp.oct_list.setCurrentRow(0)
        lp.oct_mode.set_value(new)
        lp._apply_octave()
        it = lp.oct_list.currentItem()
        name = it.text().split()[0] if it is not None else ""
        self._flash(f"{self.map.ov['noise']}: {new.lower()} octave {name}   ·   Alt+O to switch")

    def _seed_view_key(self):
        """Structure-seed view (Alt+S, like cubiomes-viewer): every potential
        structure position of the enabled types, biomes ignored."""
        if not self._page_has_map():
            return
        eng = self.link.engine
        eng.set_seed_view(not eng.seed_view)
        self.link.repaint_all()
        self._flash("Structure seed view: potential positions, biomes ignored   ·   Alt+S"
                    if eng.seed_view else "Normal map (biomes)   ·   Alt+S")

    def _arrows_key(self):
        if not self._page_has_map():
            return
        from .map_engine import CLIMATE
        if self.map.ov.get("noise") not in CLIMATE:
            self._flash("Octave arrows: choose a climate noise first "
                        "(Alt+T / H / C / E / W).")
            return
        a = self.layers.arrows
        a.setChecked(not a.isChecked())
        self._flash(f"Octave arrows {'on' if a.isChecked() else 'off'}   ·   Alt+G")

    def _arrow_mode_key(self):
        if not self._page_has_map():
            return
        seg = self.layers.arrow_mode
        new = "Lattice" if seg.value() == "Gradient" else "Gradient"
        seg.set_value(new)
        self.layers._on_arrow_mode(new)
        if not self.layers.arrows.isChecked():
            self._arrows_key()
        self._flash(f"Octave arrows: {new}   ·   Alt+Shift+G")

    def _periodic_autosave(self):
        try:
            self.drawing.autosave_if_changed()
        except Exception:
            pass
        try:                                 # SSA as it is (+ the Stronghold results if changed)
            from . import ui_state
            r = self.strongholds.results
            sig = (len(r.all_rows), r.mode, str(getattr(self.strongholds, "_paused", None)),
                   self.strongholds.runner.state)
            ui_state.save(self, heavy=sig != self._ui_sh_sig)
            self._ui_sh_sig = sig
        except Exception:
            pass
        s = self.sessions
        if s.runner.state in ("pausing", "stopping"):
            return
        sig = (id(s.model.conds), len(s.model.conds), s.model.dirty, s.model.path,
               len(s.results.rows), s.runner.state, s.runner.resume_point, s._sig(s.model.export()))
        if sig != self._autosave_sig:
            self._autosave_sig = sig
            s.autosave()

    # ── Tools menu ──────────────────────────────────────────────────────────
    def _current_map(self):
        """The map of the page on screen (None on a page without a map)."""
        key = next((k for k, i in self._page_index.items() if i == self.pages.currentIndex()), None)
        d = self.drawers.get(key)
        return d.map if d is not None else None

    def _tools_menu(self):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        a_paste = m.addAction(icons.icon("paste", 16), "Paste seed + coordinates…\tCtrl+Shift+V")
        mp = self._current_map()
        can_measure = mp is not None and mp.MEASURE
        a_meas = m.addAction(icons.icon("ruler", 16), ("Stop measuring" if can_measure and mp.measuring
                                                        else "Measure distances on the map") + "\tM")
        a_meas.setEnabled(can_measure)
        a_clear = None
        if mp is not None and mp.measures:
            a_clear = m.addAction(f"Clear measurements ({len(mp.measures)})")
        m.addSeparator()
        a_conv = m.addAction(icons.icon("save", 16), "Convert a seed list to .seeds…")
        a_clim = m.addAction("Biome climate requirements…")      # (19/10)
        a_clim.setEnabled(self.world.mc >= 22)
        a_loot = m.addAction(icons.icon("legend", 16), "Loot tables…")      # (06/11)
        m.addSeparator()
        a_leg = m.addAction(icons.icon("legend", 16), "Structure legend on the map")
        a_leg.setCheckable(True); a_leg.setChecked(self.map.legend_on())
        a_full = m.addAction(icons.icon("fullscreen", 16),
                             ("Leave fullscreen" if self.isFullScreen() else "Fullscreen") + "\tF11")
        b = self.tools_btn
        act = m.exec(b.mapToGlobal(b.rect().topRight()))
        if act is a_paste:
            self.paste_seed_coords()
        elif act is a_meas and can_measure:
            mp.toggle_measure()
        elif act is not None and act is a_clear:
            mp.clear_measures()
        elif act is a_conv:
            from .tools_dialogs import ConvertSeedsDialog
            dlg = ConvertSeedsDialog(self)
            dlg.exec(); dlg.deleteLater()
        elif act is a_clim:
            mp2 = mp if mp is not None else self.map
            mp2.show_biome_climate()
        elif act is a_loot:
            import loot_engine as le
            from .loot_table_dialog import LootTableDialog
            sub = None
            try:
                from .world import current_exact
                ex = current_exact()
                if ex in (le.MC_SUBVERSIONS.get(int(self.world.mc)) or []):
                    sub = ex
            except Exception:                                # noqa: BLE001
                pass
            LootTableDialog(le.loot_version(int(self.world.mc), sub), None, self).show()
        elif act is a_leg:
            self.layers.legend.setChecked(a_leg.isChecked())
        elif act is a_full:
            self.toggle_fullscreen()

    def set_legend(self, on):
        self.map.set_legend(on)
        try:
            from PySide6.QtCore import QSettings
            QSettings("SimpleSeedAnalyser", "SSA").setValue("map/legend", bool(on))
        except Exception:
            pass

    def toggle_fullscreen(self):
        if self.isFullScreen():
            if getattr(self, "_was_max", False):
                self.showMaximized()
            else:
                self.showNormal()
            self._flash("Fullscreen off")
        else:
            self._was_max = self.isMaximized()
            self.showFullScreen()
            self._flash("Fullscreen   ·   F11 to leave")

    def paste_seed_coords(self):
        from .tools_dialogs import PasteSeedDialog
        dlg = PasteSeedDialog(self, current_seed=self.world.seed)
        ok = dlg.exec()
        r = dlg.result_value(); pin = dlg.pin.isChecked()
        dlg.deleteLater()
        if not ok or (r["seed"] is None and r["x"] is None):
            return
        if r["seed"] is not None and r["seed"] != self.world.seed:
            self.world.set(seed=r["seed"])
        if r["x"] is None:
            self._flash(f"Seed {r['seed']} loaded")
            return
        mp = self._current_map()
        if mp is None:                           # page without a map → the Map page
            self.show_page("map"); mp = self.map
        mp.center_on(r["x"] + 0.5, r["z"] + 0.5, bpp=min(mp.bpp, 2.0))
        label = f"{r['x']}, {r['z']}" if r["y"] is None else f"{r['x']}, {r['y']}, {r['z']}"
        for v in self.link.views:
            if pin:
                v.set_pin(r["x"], r["z"], label)
            else:
                v.clear_pin()
        self._flash(f"Went to X {r['x']}  Z {r['z']}" + (f"   ·   {r['note']}" if r["note"] else ""))

    def show_shortcuts(self):
        from .widgets import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def _lock_world(self):
        busy = any(r.state in ("running", "pausing", "stopping")
                   for r in (self.sessions.runner, self.strongholds.runner))
        for w in (self.top.version, self.top.large):
            w.setEnabled(not busy)
            w.setToolTip("Locked while a search runs (pause or stop it to change the world)"
                         if busy else "")

    def _flash(self, text):
        self.status_left.setText(text)

    def closeEvent(self, e):
        try:
            self._wheel_scan.stop()
        except Exception:
            pass
        from . import ui_state
        ui_state.save(self)                  # everything comes back at the next launch (03/10)
        if self.strongholds.runner.state in ("running", "pausing", "stopping"):
            self.strongholds.runner.stop()
        s = self.sessions
        # the session is kept as it is (conditions, results, a running search
        # paused at an exact point) and comes back at the next launch — like
        # cubiomes-viewer; no "unsaved changes" question any more
        s.autosave(closing=True)
        try:
            self.drawing.shutdown()          # drawing + its search (paused) come back next time
        except Exception:
            pass
        if s.runner.state in ("running", "pausing", "stopping"):
            s.runner.stop()
        from .map_engine import shutdown_eye_pool
        shutdown_eye_pool()                  # helper processes of the map
        # queued map jobs: the thread pools' destructors wait for EVERY queued
        # job — the process stayed alive ~40 s after the window closed (zoomed-
        # out noise map). Drop the queue; running jobs end on their own.
        seen = set()
        for m in (self.map, self.sessions.map, self.strongholds.map, self.drawing.map):
            eng = getattr(m, "engine", None)
            if eng is None or id(eng) in seen:
                continue
            seen.add(id(eng))
            try:
                eng.wanted = set(); eng._frame_wanted = set()
                eng.pool.clear(); eng.exact_pool.clear()
            except Exception:
                pass
        super().closeEvent(e)

    def open_settings(self):
        from .settings_dialog import SettingsDialog
        SettingsDialog(self).exec()
        for m in (self.map, self.sessions.map, self.strongholds.map, self.drawing.map):
            m.update()                          # map settings apply at once

    def _search_ended(self, runner, st):
        prev = getattr(runner, "_ntf_prev", None)
        runner._ntf_prev = st
        if prev == "running" and (st == "done" or (st == "paused" and runner.auto_paused)):
            from . import prefs
            prefs.notify_done(self)

    def open_theme(self):
        from .theme_dialog import ThemeDialog
        ThemeDialog(self).exec()

    def show_page(self, key):
        d = self.drawers.get(key)
        if d is not None:                      # the one Layers panel follows the page
            d.set_content(self.layers)
            self.layers.map = d.map
        self.pages.setCurrentIndex(self._page_index[key])
        for b in self.nav.group.buttons():
            if b.property("page") == key:
                b.setChecked(True)
        # the page's shortcuts (Ctrl+N/S/O…) only work with the focus inside it;
        # it stayed in the top bar's seed field after a page switch
        page = self.pages.currentWidget()
        fw = QApplication.focusWidget()
        if page is not None and (fw is None or not page.isAncestorOf(fw)):
            if page.focusPolicy() == Qt.FocusPolicy.NoFocus:
                page.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            page.setFocus(Qt.FocusReason.OtherFocusReason)

    def _crack_open_map(self, seed, version_name, x, z):
        """Seedcracking → a world seed on the map, centred on the trees."""
        self.world.set(seed=int(seed), version_name=version_name or None, large_biomes=False)
        self.show_page("map")
        try:
            self.map.center_on(int(x), int(z))
        except Exception:
            pass

    def _crack_sisters(self, ss, version_name):
        """Seedcracking → Sessions in « Sister seeds » mode on the structure seed."""
        if version_name:
            self.world.set(version_name=version_name)
        sp = self.sessions
        try:
            sp.mode.set_value("Sister seeds", emit=True)
            if isinstance(ss, (list, tuple)):        # (06/11) every result: a seed list
                sp.set_seed_list(ss, "Seedcracking")
            else:
                if sp.seed_list is not None:
                    sp._seed_list()                  # back to a start seed
                sp.start_seed.setText(str(int(ss)))
            sp._sync_start_ui()
            sp._sync_fam_opts()
        except Exception:
            pass
        self.show_page("sessions")

    def _world_status(self):
        w = self.world
        from .world import current_exact
        ex = current_exact(w.version_name)
        self.status_left.setText(f"Seed {w.seed}   ·   {('Java ' + ex) if ex else w.version_name}"
                                 + ("   ·   Large biomes" if w.large_biomes else ""))

    def _probe_gpu(self):
        self._probe = _GpuProbe()
        self._probe.done.connect(self.top.set_gpu)

        def run():
            try:
                import native_libs as nl
                lib, msg = nl.load_cuda("structure_cuda")
                if lib is not None:
                    name = nl.device_info(lib).split(" (")[0].replace("NVIDIA GeForce ", "")
                    self._probe.done.emit(True, f"GPU · {name}", msg)
                else:
                    self._probe.done.emit(False, "CPU only", msg)
            except Exception as e:
                self._probe.done.emit(False, "CPU only", str(e))
        threading.Thread(target=run, daemon=True).start()
