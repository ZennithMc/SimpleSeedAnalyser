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
import sys
import threading

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QKeySequence, QLinearGradient, QPainter, QPen,
                           QShortcut)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QToolButton, QVBoxLayout, QWidget)

from . import icons, motion, theme
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


class TopTabs(QFrame):
    """(13/11) The workspaces, as tabs in the top bar (the left rail is gone: its
    84 px go to the pages). Same interface as the old rail: add(), group."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TopTabs")
        self.setFixedHeight(42)
        self.lay = QHBoxLayout(self); self.lay.setContentsMargins(4, 4, 4, 4); self.lay.setSpacing(2)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        # (14/11) the current tab's pill is painted here: it slides to the new tab
        # and takes its workspace's colours on the way
        self._pill = None                     # QRectF
        self._pc = (QColor(theme.ACCENT), QColor(theme.ACCENT))
        self._pan = None
        self.group.buttonToggled.connect(lambda b, on: on and self.move_pill(b))
        # (15/11) under the mouse: a faint pill of the hovered tab's colours glides from tab to tab
        self._ghost = None                    # QRectF
        self._gc = (QColor(theme.ACCENT), QColor(theme.ACCENT))
        self._ga = 0.0
        self._gan = None

    def eventFilter(self, o, e):
        t = e.type()
        if t == QEvent.Type.Enter and o in self.group.buttons():
            self._ghost_to(o)
        elif t == QEvent.Type.Leave and o in self.group.buttons():
            self._ghost_to(None)
        return False

    def _ghost_to(self, b):
        if self._gan is not None:
            try:
                self._gan.stop()
            except RuntimeError:
                pass
            self._gan = None
        if not motion.enabled():
            self._ga = 0.0
            self.update()
            return
        a0 = self._ga
        if b is None:
            def out(t):
                self._ga = a0 * (1.0 - t)
                self.update()
            self._gan = motion.run(self, 200, out)
            return
        r1 = QRectF(b.geometry())
        c1, c2 = (QColor(c) for c in theme.ws_pair(b.property("page")))
        r0 = QRectF(self._ghost) if (self._ghost is not None and a0 > 0.05) else QRectF(r1)
        g0 = self._gc

        def step(t):
            self._ghost = QRectF(r0.x() + (r1.x() - r0.x()) * t, r0.y() + (r1.y() - r0.y()) * t,
                                 r0.width() + (r1.width() - r0.width()) * t,
                                 r0.height() + (r1.height() - r0.height()) * t)
            self._gc = (motion.mix(g0[0], c1, t), motion.mix(g0[1], c2, t))
            self._ga = a0 + (1.0 - a0) * t
            self.update()
        self._gan = motion.run(self, 190, step)

    def move_pill(self, b, animate=True):
        c1, c2 = (QColor(c) for c in theme.ws_pair(b.property("page")))
        r1 = QRectF(b.geometry())
        if self._pan is not None:
            try:
                self._pan.stop()
            except RuntimeError:
                pass
            self._pan = None
        if self._pill is None or not animate or not motion.enabled() or not self.isVisible():
            self._pill, self._pc = r1, (c1, c2)
            self.update()
            return
        r0, (a0, b0) = QRectF(self._pill), self._pc

        def step(t):
            self._pill = QRectF(r0.x() + (r1.x() - r0.x()) * t, r0.y() + (r1.y() - r0.y()) * t,
                                r0.width() + (r1.width() - r0.width()) * t,
                                r0.height() + (r1.height() - r0.height()) * t)
            self._pc = (motion.mix(a0, c1, t), motion.mix(b0, c2, t))
            self.update()
        self._pan = motion.run(self, 280, step)

    def snap(self):
        b = self.group.checkedButton()
        if b is not None:
            self.move_pill(b, animate=False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self.snap)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self.snap)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._pill is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._ghost is not None and self._ga > 0.02:
            gr = self._ghost.adjusted(0.5, 0.5, -0.5, -0.5)
            gg = QLinearGradient(gr.topLeft(), gr.bottomRight())
            g1, g2 = QColor(self._gc[0]), QColor(self._gc[1])
            g1.setAlphaF(0.13 * self._ga)
            g2.setAlphaF(0.07 * self._ga)
            gg.setColorAt(0, g1)
            gg.setColorAt(1, g2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(gg)
            p.drawRoundedRect(gr, 10, 10)
        r = self._pill.adjusted(0.5, 0.5, -0.5, -0.5)
        c1, c2 = QColor(self._pc[0]), QColor(self._pc[1])
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        c1.setAlphaF(0.24)
        c2.setAlphaF(0.14)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        edge = QColor(self._pc[0])
        edge.setAlphaF(0.50)
        p.setBrush(g)
        p.setPen(QPen(edge, 1))
        p.drawRoundedRect(r, 10, 10)

    def add(self, key, label, icon_name, tip):
        b = QToolButton(); b.setObjectName("NavButton")
        b.setText(label); b.setToolTip(tip if tip and tip != label else "")   # (15/11) not the name again
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setCheckable(True); b.setFixedHeight(34)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setProperty("page", key); b.setProperty("tabIcon", icon_name)
        self.group.addButton(b)
        self.lay.addWidget(b)
        b.installEventFilter(self)            # (15/11) the hover pill
        self.restyle(b)
        return b

    def restyle(self, only=None):
        """Icons in the workspace's colour when the tab is the current one."""
        for b in ([only] if only is not None else self.group.buttons()):
            icons.bind(b, str(b.property("tabIcon")), 18, "text2", theme.ws_color(b.property("page")))
            b.setIconSize(QSize(18, 18))


class TopBar(QFrame):
    """(13/11) mark · workspace tabs · — · seed capsule · version · world type ·
    tools · GPU. Narrow windows: the tabs lose their text, then the tools fold
    into one menu (_fit)."""
    def __init__(self, world: WorldState, parent=None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.world = world
        self.setFixedHeight(64)
        lay = QHBoxLayout(self); lay.setContentsMargins(18, 10, 16, 8); lay.setSpacing(10)

        self.mark = QLabel(); self.mark.setFixedSize(30, 30)
        self._mark_shine = motion.Shine(self.mark, hover=True, every=(9.0, 19.0), radius=9)   # (15/11)
        lay.addWidget(self.mark)
        self.name = QLabel("SSA"); self.name.setObjectName("AppName")
        lay.addWidget(self.name)
        lay.addSpacing(8)
        self.tabs = TopTabs()
        lay.addWidget(self.tabs)
        lay.addStretch(1)

        # seed: its sigil, the field, random, copy — one capsule
        cap = QFrame(); cap.setObjectName("SeedCapsule"); cap.setFixedHeight(40)
        ch = QHBoxLayout(cap); ch.setContentsMargins(8, 3, 5, 3); ch.setSpacing(3)
        self.sigil = QLabel(); self.sigil.setFixedSize(26, 26)
        self.sigil.setToolTip("This seed's sigil: the same seed looks the same everywhere in SSA.")
        ch.addWidget(self.sigil); ch.addSpacing(4)
        self.seed = QLineEdit(str(world.seed)); self.seed.setObjectName("SeedEdit")
        self.seed.setMinimumWidth(214)
        self.seed.setToolTip("Seed: a number, or any text (hashed exactly like Minecraft does).\n"
                             "Press Enter to apply.")
        self.seed.returnPressed.connect(self._apply_seed)
        self.seed.editingFinished.connect(self._apply_seed)
        ch.addWidget(self.seed)
        for ic, tip, fn in (("dice", "Random seed  (Ctrl+R)", self.random),
                            ("copy", "Copy seed", self._copy)):
            b = QToolButton(); icons.bind(b, ic, 18); b.setToolTip(tip)
            b.setFixedSize(30, 30); b.clicked.connect(fn); ch.addWidget(b)
        # (19/11) the Seed Library: ★ keeps this seed (then opens it there); right click: the library
        self.star = QToolButton(); self.star.setFixedSize(30, 30)
        self.star.setIconSize(QSize(18, 18))
        self.star.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star.clicked.connect(lambda: self.window().seed_star())
        self.star.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.star.customContextMenuRequested.connect(lambda *_: self.window().open_seed_library())
        ch.addWidget(self.star)
        lay.addWidget(cap)

        # version: a button, the major versions, each opening its exact versions (31/10)
        class _VersionButton(QToolButton):      # (14/11) a click: the panel of all the versions;
            openPicker = Signal()               # a right click: the former menus
            def mousePressEvent(self, e):
                if e.button() == Qt.MouseButton.LeftButton:
                    self.openPicker.emit(); e.accept()
                elif e.button() == Qt.MouseButton.RightButton:
                    self.showMenu()
                else:
                    super().mousePressEvent(e)
        self.version = _VersionButton(); self.version.setObjectName("VersionButton")
        self.version.openPicker.connect(self._open_version_picker)
        self.version.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.version.setMinimumWidth(118)
        self.version.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_version_menu()
        self._show_version()
        lay.addWidget(self.version)

        self.large = QCheckBox("Large biomes")
        self.large.setChecked(world.large_biomes)
        self.large.toggled.connect(lambda on: self.world.set(large_biomes=on))
        lay.addWidget(self.large)
        lay.addSpacing(4)

        self.tools = []                       # Tools, Keys, Theme, Settings (add_tool)
        self._tools_at = lay.count()
        self.more = QToolButton(); self.more.setObjectName("TopTool")
        icons.bind(self.more, "more", 20); self.more.setIconSize(QSize(20, 20))
        self.more.setFixedSize(36, 36); self.more.setToolTip("Tools · Keys · Theme · Settings")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.hide()
        lay.addWidget(self.more)
        self.gpu = QLabel("GPU: checking…"); self.gpu.setObjectName("Chip")
        self.gpu.setProperty("state", "off")
        lay.addWidget(self.gpu, 0, Qt.AlignmentFlag.AlignVCenter)
        self._mode = -1
        self._need = None
        world.changed.connect(self._sync)
        self.restyle()

    def add_tool(self, text, icon_name, tip, fn):
        """An icon button right of the world settings (and a line of the « more »
        menu of a narrow window)."""
        b = QToolButton(); b.setObjectName("TopTool")
        icons.bind(b, icon_name, 20, "text2", "text"); b.setIconSize(QSize(20, 20))
        b.setFixedSize(36, 36); b.setToolTip(f"{text} — {tip}" if tip else text)   # (19/11) an icon: its name
        b.clicked.connect(fn)
        self.layout().insertWidget(self._tools_at + len(self.tools), b)
        self.tools.append((b, text, icon_name, fn))
        from PySide6.QtWidgets import QMenu
        m = QMenu(self.more)
        for _b, t, ic, f in self.tools:
            m.addAction(icons.icon(ic, 16), t, f)
        self.more.setMenu(m)
        self._need = None
        return b

    def restyle(self):
        """Theme-dependent pictures (the mark, the sigil, the tabs' icons)."""
        from . import brand
        self.mark.setPixmap(brand.mark(30, *theme.ws_pair(theme.WORKSPACE)))
        self.sigil.setPixmap(brand.sigil(self.world.seed, 26))
        self.seed.setFont(theme.num_font(11.5, QFont.Weight.DemiBold))
        self.tabs.restyle()
        QTimer.singleShot(0, self.tabs.snap)
        self._need = None
        self._fit()

    # the widths the three layouts need are measured once (per theme / tool set)
    def _apply_mode(self, mode):
        for b in self.tabs.group.buttons():
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if mode == 0
                                 else Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.name.setVisible(mode == 0)
        for b, *_r in self.tools:
            b.setVisible(mode < 2)
        self.more.setVisible(mode == 2)

    def _fit(self):
        if self._need is None:
            need = []
            for m in (0, 1, 2):
                self._apply_mode(m)
                self.tabs.layout().invalidate()     # (the tabs' own layout is asked again now,
                self.tabs.updateGeometry()          #  not at the next layout request)
                self.layout().invalidate()
                need.append(self.layout().minimumSize().width())
            self._need, self._mode = need, -1
            # the bar must not hold the window at its widest layout
            self.setMinimumWidth(need[2])
        w = self.width()
        mode = 0 if w >= self._need[0] else (1 if w >= self._need[1] else 2)
        if mode != self._mode:
            self._mode = mode
            self._apply_mode(mode)
            QTimer.singleShot(0, self.tabs.snap)     # the tabs moved: the pill follows

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

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

    def _open_version_picker(self):
        import time
        from .version_picker import VersionPicker
        from .world import GAME_VERSIONS, current_exact, major_of
        if time.monotonic() - VersionPicker.closed_at < 0.25:      # the click that just closed it
            return
        majors = {}
        for name in VERSION_NAMES:
            for ex in GAME_VERSIONS.get(name, []):
                majors.setdefault(major_of(ex), []).append((ex, name))
        pk = VersionPicker(list(majors.items()), current_exact(self.world.version_name), VERSION_TIPS, self)
        pk.picked.connect(self._pick_version)
        pk.popup_under(self.version)

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
        self.version.setToolTip((f"Same world generation: {tip}\n" if tip else "") +
                                "Click: all the versions · right click: the list")

    def _copy(self):
        QGuiApplication.clipboard().setText(str(self.world.seed))

    def sync_star(self, pop=False):
        """(19/11) ★ filled (gold) when this seed — version, world type — is in the Seed Library."""
        from .seed_store import store
        w = self.world
        kept = store().find(w.seed, w.version_name, w.large_biomes) is not None
        gold = "#f5b942"
        self.star.setIcon(icons.icon("star_fill" if kept else "star", 18, gold if kept else theme.TEXT2,
                                     gold if kept else theme.TEXT))
        self.star.setToolTip("In the Seed Library — click to open it there" if kept
                             else "Keep this seed in the Seed Library  (right click: open the library)")
        if pop and motion.enabled():
            import math

            def step(t):
                k = 1 + 0.45 * math.sin(math.pi * t)
                self.star.setIconSize(QSize(int(18 * k), int(18 * k)))
            motion.run(self.star, 300, step, lambda: self.star.setIconSize(QSize(18, 18)))

    def _sync(self):
        from . import brand
        if getattr(self, "_sigil_seed", None) != self.world.seed:
            self._sigil_seed = self.world.seed
            self.sigil.setPixmap(brand.sigil(self.world.seed, 26))
            motion.fade_in(self.sigil, 320)             # (14/11) a new seed: its sigil appears
        if parse_seed(self.seed.text()) != self.world.seed:
            self.seed.setText(str(self.world.seed))
        self._show_version()
        if self.large.isChecked() != self.world.large_biomes:
            self.large.setChecked(self.world.large_biomes)
        if hasattr(self, "star"):
            self.sync_star()

    def set_gpu(self, ok, text, details):
        self.gpu.setText(text)
        self.gpu.setProperty("state", "ok" if ok else "off")
        self.gpu.setToolTip(details)
        self.gpu.style().unpolish(self.gpu); self.gpu.style().polish(self.gpu)


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

        motion.install_dialog_fade()             # (14/11) dialogs fade in
        root = motion.Backdrop()                 # (14/11) objectName "Root"; SSA's theme: living lights
        v = QVBoxLayout(root); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        self.top = TopBar(self.world); v.addWidget(self.top)
        # (13/11) the workspaces are tabs of the top bar: no rail, the pages get its width
        mid = QHBoxLayout(); mid.setContentsMargins(10, 0, 0, 0); mid.setSpacing(0)
        v.addLayout(mid, 1)
        self.nav = self.top.tabs
        self.pages = motion.FadeStack(); mid.addWidget(self.pages, 1)      # (14/11) pages cross-fade

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
        from . import memory                      # (15/11) its caches sized from this PC's RAM
        memory.apply()
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

        from .home_page import HomePage                    # (13/11)
        self.home = HomePage(self)
        pages = [
            ("home", "Home", "home", "", self.home),
            ("map", "Map", "map", "", mp),                      # (19/11) no tip: the tab says it
            ("sessions", "Sessions", "sessions", "", self.sessions),
            # (06/11) the Stronghold page is gone from the rail (all of it is in the
            # Sessions' Stronghold condition); its object stays for the shared state
            ("loot", "Loot", "chest", "", self.loot),
            ("seedcrack", "Crack", "forest", "Seedcracking — Java 1.16 – 1.17", self.seedcrack),
            ("drawing", "Draw", "brush", "", self.drawing),
        ]
        self._page_index = {}
        for key, label, ic, tip, w in pages:
            self._page_index[key] = self.pages.addWidget(w)
            w.setProperty("ws", key)             # the stylesheet's rules of its workspace colour
            b = self.nav.add(key, label, ic, tip)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
        _tools = [("Tools", "tools", "Paste seed + coordinates · measure · seed-list converter · "
                                     "legend · fullscreen", None),
                  ("Keys", "keyboard", "Keyboard shortcuts  (F1)", self.show_shortcuts),
                  ("Theme", "palette", "Themes and colours", self.open_theme),
                  ("Settings", "gear", "", self.open_settings)]
        for text, ic, tip, fn in _tools:
            cl = self.top.add_tool(text, ic, tip, fn or self._tools_menu)
            if fn is None:                       # the Tools menu
                self.tools_btn = cl
        self.nav.group.buttons()[0].setChecked(True)
        theme.set_workspace("home")

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
        from .seed_store import store as _seed_store              # (19/11) the ★ follows the library
        _seed_store().changed.connect(lambda *_: self.top.sync_star())
        theme.signals.changed.connect(lambda *_: self.top.sync_star())
        self.top.sync_star()
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
        theme.signals.changed.connect(self._theme_changed)
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
        for sig in (self.world.changed, self.layers.changed, self.loot.changed):
            sig.connect(self._ui_timer.start)
        self.link.viewMoved.connect(self._ui_timer.start)
        self._ui_sh_sig = None
        self._autosave_timer = QTimer(self, interval=90_000, timeout=self._periodic_autosave)
        self._autosave_timer.start()
        self._autosave_sig = None
        # (20/11) SSA opens with a short animation (launch.py) — not when the animations are off
        self._launching = (motion.enabled() and bool(_prefs.get("launch_anim"))
                           and not os.environ.get("SSA_NO_LAUNCH"))

    def showEvent(self, e):
        super().showEvent(e)
        if getattr(self, "_launching", False) and not getattr(self, "_launch_started", False):
            self._launch_started = True
            try:
                from .launch import LaunchVeil
                self._veil = LaunchVeil(self, on_open=self.home.launch_reveal, on_end=self._launch_end)
            except Exception as ex:                      # noqa: BLE001 — cosmetic only
                print(f"[launch] {ex}", flush=True)
                self._launch_end()
                self.home.launch_reveal()

    def _launch_end(self):
        self._launching = False
        self._veil = None

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
        b = self.tools_btn if self.tools_btn.isVisible() else self.top.more
        act = m.exec(b.mapToGlobal(b.rect().bottomLeft()))
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

    def changeEvent(self, e):
        super().changeEvent(e)
        from PySide6.QtCore import QEvent, QTimer
        if e.type() == QEvent.Type.WindowStateChange and self.isFullScreen():
            QTimer.singleShot(0, self._fullscreen_border)

    def _fullscreen_border(self):
        """(13/11) Windows: the Crack tab's 3D view (QOpenGLWidget) makes the whole
        window an OpenGL one, and a fullscreen OpenGL window is not composited by the
        DWM: menus, combo lists, tooltips and dialogs then open BEHIND it (the Add
        button's menu never showed). Qt's documented workaround is a 1-pixel border
        in fullscreen (QWindowsWindowFunctions::setHasBorderInFullScreen, private in
        Qt 6), set here through the Win32 API; Qt restores its own style when the
        window leaves fullscreen."""
        if sys.platform != "win32" or not self.isFullScreen():
            return
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            get, put = u.GetWindowLongPtrW, u.SetWindowLongPtrW
            get.restype, get.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int]
            put.restype, put.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            GWL_STYLE, WS_BORDER = -16, 0x00800000
            hwnd = wintypes.HWND(int(self.winId()))
            st = get(hwnd, GWL_STYLE)
            if not st & WS_BORDER:
                put(hwnd, GWL_STYLE, st | WS_BORDER)
                # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
                u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x1 | 0x2 | 0x4 | 0x10 | 0x20)
        except Exception as ex:                          # noqa: BLE001 — cosmetic only
            print(f"[fullscreen] border: {ex}", flush=True)

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
        lib = getattr(self, "_seed_library", None)        # (19/11 bis) its own window: closed with SSA
        if lib is not None:
            try:
                lib.close()
            except RuntimeError:
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

    # ── (19/11) the Seed Library ──────────────────────────────────────────────
    def open_seed_library(self, seed_id=None, add=False):
        from .seed_library import open_library
        return open_library(self, seed_id, add=add)

    def seed_star(self):
        """★ of the top bar: keeps the current seed; already kept: opens it in the library."""
        from .seed_store import store
        w = self.world
        s = store().find(w.seed, w.version_name, w.large_biomes)
        if s is not None:
            self.open_seed_library(s["id"])
            return
        store().add_seed(w.seed, w.version_name, w.large_biomes)
        self.top.sync_star(pop=True)
        self._flash("Kept in the Seed Library — ★ again opens it there.")

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
        self._enter_workspace(key)
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

    def _enter_workspace(self, key):
        """(13/11) The theme's colour of this workspace (theme.set_workspace). A page
        that styles itself (its own _style) does it again under its colour, once
        per theme."""
        theme.set_workspace(key)
        from . import brand
        self.top.mark.setPixmap(brand.mark(30, *theme.ws_pair(key)))   # the mark wears the workspace's colours
        page = self.pages.widget(self._page_index[key])
        fn = getattr(page, "_style", None)
        sig = (theme.THEME, theme.ACCENT, id(theme.TOKENS))
        if callable(fn) and getattr(page, "_ws_sig", None) != sig:
            try:
                fn()
            except Exception:
                pass
            page._ws_sig = sig
        page.update()

    def _theme_changed(self):
        icons.refresh(self)
        self.top.restyle()
        key = next((k for k, i in self._page_index.items() if i == self.pages.currentIndex()), None)
        if key:
            self._enter_workspace(key)

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
