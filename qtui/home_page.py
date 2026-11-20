"""
qtui/home_page.py — (13/11) Home: the current seed over its own map, the
workspaces, the recent sessions of the library and what the version brought.

Nothing here searches or changes a session: the buttons only lead to the other
pages (window.show_page) or open a library entry the way the library does.
"""
from __future__ import annotations

import math
import os
import random
import threading
import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from . import brand, icons, motion, theme

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TILES = (("map", "Map", "Biomes, structures, climate", "hm_map"),        # (20/11) richer icons here
         ("sessions", "Sessions", "Search seeds by conditions", "hm_sessions"),
         ("loot", "Loot", "Find seeds by chest loot", "hm_loot"),
         ("seedcrack", "Crack", "Rebuild a seed from a video", "hm_crack"),
         ("drawing", "Draw", "Sketch a terrain, find it", "hm_draw"))
PREVIEW = (1120, 336, 4)             # cells x cells at 1:4 (4 480 blocks wide), around the spawn
TILE = 128                           # (19/11) the world is made in tiles of 128 columns, ahead of the flight
FLY_PX = 7.0                         # the flight: screen pixels per second, from right to left


class _Bus(QObject):
    ready = Signal(object, object, object)   # generation, "spawn" | tile index, data


# (19/11) the structures shown on the hero's map (the common ones, by surface)
HERO_STRUCTS = ("village", "pillager_outpost", "mansion", "monument", "desert_pyramid", "jungle_temple",
                "swamp_hut", "igloo", "ancient_city")           # (19/11) no trial chambers: everywhere


def _glow_sprite(color, radius, core, dpr):
    """A soft light (radial gradient) drawn once: the fireflies are pictures, not gradients
    built again 60 times a second."""
    s = int(2 * radius + 2)
    pm = QPixmap(int(s * dpr), int(s * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QColor(color)
    g = QRadialGradient(s / 2, s / 2, radius)
    c0 = QColor(c); c0.setAlphaF(0.95)
    c1 = QColor(c); c1.setAlphaF(0.38)
    c2 = QColor(c); c2.setAlphaF(0.0)
    g.setColorAt(0.0, c0)
    g.setColorAt(0.32, c1)
    g.setColorAt(1.0, c2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(QRectF(0, 0, s, s))
    if core > 0:
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawEllipse(QRectF(s / 2 - core / 2, s / 2 - core / 2, core, core))
    p.end()
    return pm


class Hero(QFrame):
    """The seed, large, over the biomes around the spawn of its own world (19/11: alive).

    Layers, from the back: the map (it breathes — a slow drift and zoom — and leans a little
    towards the mouse), the veil that keeps the text readable, the spawn (a pulse) and the
    structures around it (icons; a click opens the map there), three depths of fireflies
    (pictures drawn once, moved at the screen's rate), a soft light under the cursor. A new
    seed cross-fades its map in. Nothing moves while Home is hidden, the window inactive or
    the animations off."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._key = None
        self._flight = None                  # (19/11) the world under the hero, flown over
        self._gen = 0
        self._busy = False
        self._strip = self._strip_key = None
        self._old_snap = None
        self.setMinimumHeight(236)               # (19/11) Home does not scroll: in a short window
        self.setMaximumHeight(330)               # the hero gives some height first
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 34, 40, 30)
        v.setSpacing(12)
        lab = QLabel("CURRENT SEED")
        lab.setObjectName("FieldLabel")
        v.addWidget(lab)
        row = QHBoxLayout()
        row.setSpacing(18)
        self.sg = QLabel()
        self.sg.setFixedSize(64, 64)
        self.sg.setToolTip("This seed's sigil: the same seed looks the same everywhere in SSA.")
        row.addWidget(self.sg)
        self.seed = QLabel()
        self.seed.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.seed)
        row.addStretch(1)
        v.addLayout(row)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.c_ver, self.c_world, self.c_spawn = QLabel(), QLabel(), QLabel()
        for c in (self.c_ver, self.c_world, self.c_spawn):
            c.setObjectName("Chip")
            c.setProperty("state", "off")
            chips.addWidget(c)
        self.c_spawn.hide()
        chips.addStretch(1)
        v.addLayout(chips)
        v.addStretch(1)
        btns = QHBoxLayout()
        btns.setSpacing(10)
        b1 = QPushButton("  Open the map")
        b1.setProperty("kind", "primary")
        icons.bind(b1, "map", 16, "on_accent", "on_accent")
        b1.clicked.connect(lambda: win.show_page("map"))
        b2 = QPushButton("  New session")
        b2.setProperty("kind", "ghost")
        icons.bind(b2, "plus", 16)
        b2.clicked.connect(lambda: win.show_page("sessions"))
        b3 = QPushButton("  Random seed")
        b3.setProperty("kind", "ghost")
        icons.bind(b3, "dice", 16)
        b3.setToolTip("Ctrl+R")
        b3.clicked.connect(win.top.random)
        for b in (b1, b2, b3):
            b.setMinimumHeight(38)
            btns.addWidget(b)
        self._shine = motion.Shine(b1, every=(7.0, 13.0))
        self._spots = [motion.Spot(b) for b in (b1, b2, b3)]          # (15/11) a light under the cursor
        btns.addStretch(1)
        v.addLayout(btns)
        # (19/11) fireflies in three depths: far (small, slow, faint), middle, near (big, quick)
        r = random.random
        self._motes = []
        for depth, n in ((0, 22), (1, 14), (2, 7)):
            for _ in range(n):
                self._motes.append(dict(x=r(), y=r(), ox=0.0, oy=0.0, d=depth, ph=r() * 6.28, c=int(r() < 0.5),
                                        vx=(r() - 0.35) * (0.006 + 0.006 * depth),
                                        vy=-(0.003 + r() * 0.008) * (1 + 0.6 * depth),
                                        f=0.6 + r() * 1.2))
        self._sprites = {}
        self._fade = 1.0                              # a new seed: the old map fades out
        self._veil = self._veil_key = None
        self._t0 = time.monotonic()
        self._clock = self._t0
        self._mouse = None                            # the cursor (widget coords) or None
        self._lean = [0.0, 0.0]                       # the map's lean towards the mouse (smoothed)
        self._hits = []                               # (QRectF, label, x, z) of the markers
        self._hot = None
        self._appear = 0.0                            # the markers fade in with a new map
        self._tick = QTimer(self)
        self._tick.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick.timeout.connect(self._advance)
        self._bus = _Bus(self)
        self._bus.ready.connect(self._preview_in)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._preview)
        win.world.changed.connect(self.sync)
        theme.signals.changed.connect(self.sync)
        self.sync()

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(super().sizeHint().width(), 330)      # full height when there is room

    # ── state ────────────────────────────────────────────────────────────────
    def sync(self):
        from .world import current_exact
        w = self.win.world
        self.sg.setPixmap(brand.sigil(w.seed, 64))
        self.seed.setFont(theme.num_font(31, QFont.Weight.Bold, display=True))
        if getattr(self, "_last_seed", None) not in (None, w.seed):
            motion.roll_text(self.seed, str(w.seed))     # a new seed: its digits settle
        else:
            self.seed.setText(str(w.seed))
        self._last_seed = w.seed
        self.c_ver.setText("Java " + (current_exact(w.version_name) or w.version_name.replace("Java ", "")))
        self.c_world.setText("Large biomes" if w.large_biomes else "Default world")
        self._sprites = {}
        self._veil = None
        if self.isVisible():
            self._timer.start()

    def _frame_ms(self):
        try:
            hz = float(self.screen().refreshRate())
        except Exception:
            hz = 60.0
        return int(max(7, min(16, 1000.0 / max(30.0, hz))))      # 60 to 144 images / s

    def showEvent(self, e):
        super().showEvent(e)
        if self._key != self._want():
            self._timer.start()
        if motion.enabled():
            self._clock = time.monotonic()
            self._tick.start(self._frame_ms())

    def hideEvent(self, e):
        super().hideEvent(e)
        self._tick.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._veil = None
        if self._flight is not None and self._flight.get("ready"):
            self._need_tiles()

    # ── the mouse ────────────────────────────────────────────────────────────
    def mouseMoveEvent(self, e):
        self._mouse = e.position()
        hot = next((h for h in self._hits if h[0].contains(self._mouse)), None)
        if (hot[2:] if hot else None) != (self._hot[2:] if self._hot else None):
            self._hot = hot
            self.setCursor(Qt.CursorShape.PointingHandCursor if hot else Qt.CursorShape.ArrowCursor)
        if not self._tick.isActive():
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._mouse = None
        self._hot = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._hot is not None:
            _r, _lab, x, z = self._hot
            self.win.show_page("map")
            try:
                self.win.map.center_on(x, z, 2.0)
            except Exception:                      # noqa: BLE001
                pass
            return
        super().mouseReleaseEvent(e)

    # ── time ─────────────────────────────────────────────────────────────────
    def _advance(self):
        now = time.monotonic()
        dt, self._clock = min(0.05, now - self._clock), now
        win = self.window()
        if win is None or not win.isActiveWindow() or not motion.enabled():
            return                               # nothing moves behind another window
        w, h = max(1.0, self.width()), max(1.0, self.height())
        m = self._mouse
        cx, cy = (m.x() / w, m.y() / h) if m is not None else (None, None)
        back = math.exp(-dt * 2.6)               # (19/11) a pushed firefly comes back (~0.4 s)
        for f in self._motes:
            sp = 1.0 + 0.5 * f["d"]
            fly = -FLY_PX * (0.6, 0.9, 1.3)[f["d"]] / w if self._flight is not None else 0.0   # with the flight
            f["x"] = (f["x"] + (f["vx"] + fly) * dt
                      + 0.0010 * math.sin(now * 0.7 * f["f"] + f["ph"]) * dt * 10 * sp) % 1.0
            f["y"] = (f["y"] + f["vy"] * dt) % 1.0
            f["ox"] *= back
            f["oy"] *= back
            if cx is not None:                   # (15/11) they move away from the cursor
                dx, dy = f["x"] + f["ox"] - cx, (f["y"] + f["oy"] - cy) * h / w
                d2 = dx * dx + dy * dy
                if d2 < 0.012:
                    k = (0.012 - d2) / 0.012 * 0.35 * dt / max(0.02, d2 ** 0.5)
                    f["ox"] += dx * k
                    f["oy"] += dy * k * w / h
        # (19/11) no lean towards the mouse any more: the flight keeps its course, whatever the cursor
        fl = self._flight
        if fl is not None and fl.get("ready"):   # (19/11) the flight over the world, one way
            fl["cam"] += FLY_PX * dt / max(0.01, self._scale())
            self._need_tiles()
        if self._fade < 1.0:
            self._fade = min(1.0, self._fade + dt / 0.7)
            if self._fade >= 1.0:
                self._old_snap = None
        if self._appear < 1.0 and fl is not None and fl.get("ready"):
            self._appear = min(1.0, self._appear + dt / 0.9)
        self.update()

    # ── the world below (tiles made by a worker thread) ──────────────────────
    def _want(self):
        try:
            pal = self.win.link.engine.palette
        except Exception:
            pal = None
        return (self.win.world.key, pal)

    def _scale(self):
        """Screen pixels per map cell: the strip's height covers the hero, with a margin."""
        return max(self.width() / PREVIEW[0], self.height() / PREVIEW[1]) * 1.06

    def _preview(self):
        """A new world: its spawn, then the tiles of its first view (in the background)."""
        want = self._want()
        if want == self._key:
            return
        if self._flight is not None and self._flight.get("ready") and motion.enabled():
            self._old_snap = self._snapshot()           # the old map fades out over the new one
            self._fade = 0.0
        self._key = want
        self._gen += 1
        self._flight = dict(key=want, gen=self._gen, tiles={}, structs={}, asked=set(), ready=False,
                            x0=0, z0=0, cam=0.0, spawn=None)
        self._strip = self._strip_key = None
        self._appear = 0.0 if (motion.enabled() and self._tick.isActive()) else 1.0
        self.c_spawn.hide()
        fl, bus, gen = self._flight, self._bus, self._gen
        vis = int(self.width() / max(0.01, self._scale())) + 2

        def job():
            sp = None
            try:
                from .map_engine import _thread_lib
                lib = _thread_lib(want[0], 0)
                s = lib.get_spawn()                      # (19/11) the map's spawn (1.18+: the game's own)
                sp = (int(s[0]), int(s[1]))
            except Exception:
                pass
            sc = PREVIEW[2]
            cx, cz = ((sp[0] // sc), (sp[1] // sc)) if sp else (0, 0)
            x0 = cx - int(vis * 0.70)                    # the spawn at 70 % of the width
            z0 = cz - PREVIEW[1] // 2
            try:
                bus.ready.emit(gen, "spawn", (sp, x0, z0))
            except RuntimeError:
                return
            for i in range(0, vis // TILE + 2):
                self._make_tile(want, gen, x0, z0, i)
        fl["asked"].update(range(0, vis // TILE + 2))
        threading.Thread(target=job, daemon=True, name="ssa-home-preview").start()

    def _make_tile(self, want, gen, x0, z0, i):
        """One tile (TILE columns of cells, the whole height) and its structures — worker side."""
        img, found = None, []
        try:
            import numpy as np
            import cubiomes_wrap as cw
            from .map_engine import _thread_lib, biome_lut, _pixmap_from_rgb
            w, h, sc = PREVIEW
            lib = _thread_lib(want[0], 0)
            tx = x0 + i * TILE
            ids = np.frombuffer(lib.gen_biomes_region(tx, z0, TILE, h, sc), dtype=np.int32).reshape(h, TILE)
            lut = biome_lut(want[1]) if want[1] else biome_lut("True Colors (ShowMe7)")
            img = _pixmap_from_rgb(lut[np.clip(ids, 0, len(lut) - 1)])
            for key in HERO_STRUCTS:
                t = cw.ALL_STRUCTURE_TYPES.get(key)
                if t is None:
                    continue
                try:
                    pts = lib.find_structures_in_box(t, tx * sc, z0 * sc, (tx + TILE) * sc - 1, (z0 + h) * sc,
                                                     max_count=6)
                except Exception:
                    continue
                found += [(key, int(px), int(pz)) for px, pz in pts
                          if tx * sc <= px < (tx + TILE) * sc]
        except Exception:
            img = None
        try:
            self._bus.ready.emit(gen, i, (img, found))
        except RuntimeError:
            pass

    def _preview_in(self, gen, what, data):
        fl = self._flight
        if fl is None or gen != fl["gen"]:
            return
        if what == "spawn":
            data, fl["x0"], fl["z0"] = data
            sc = PREVIEW[2]
            vis = self.width() / max(0.01, self._scale())
            fl["spawn"] = data
            cx = (data[0] // sc) if data else 0
            fl["cam"] = float(cx) - 0.70 * vis
            if data:
                self.c_spawn.setText(f"Spawn  {data[0]}, {data[1]}")
                self.c_spawn.show()
            return
        img, found = data
        if isinstance(img, QImage):
            fl["tiles"][what] = img
            fl["structs"][what] = found
        else:
            fl["tiles"][what] = None
        if not fl["ready"]:
            i0, i1 = self._visible_tiles()
            if all(i in fl["tiles"] for i in range(i0, i1 + 1)):
                fl["ready"] = True
        self.update()

    def _visible_tiles(self):
        fl = self._flight
        k = self._scale()
        i0 = int(math.floor((fl["cam"] - fl["x0"]) / TILE))
        i1 = int(math.floor((fl["cam"] + self.width() / k - fl["x0"]) / TILE))
        return max(0, i0), max(0, i1)

    def _need_tiles(self):
        """Tiles ahead are asked before they show; the ones far behind are let go."""
        fl = self._flight
        i0, i1 = self._visible_tiles()
        for i in [j for j in fl["tiles"] if j < i0 - 1]:
            fl["tiles"].pop(i, None)
            fl["structs"].pop(i, None)
        ahead = [i for i in range(i0, i1 + 3) if i not in fl["asked"]]
        if not ahead or self._busy:
            return
        fl["asked"].update(ahead)
        self._busy = True
        want, gen, x0, z0 = fl["key"], fl["gen"], fl["x0"], fl["z0"]

        def job():
            try:
                for i in ahead:
                    self._make_tile(want, gen, x0, z0, i)
            finally:
                self._busy = False
        threading.Thread(target=job, daemon=True, name="ssa-home-tiles").start()

    def _strip_pixmap(self):
        """The tiles in view, side by side, one pixel per cell (rebuilt when the set changes)."""
        fl = self._flight
        if fl is None or not fl["ready"]:
            return None, 0
        i0, i1 = self._visible_tiles()
        i1 += 1
        key = (fl["gen"], i0, i1, tuple(i in fl["tiles"] and fl["tiles"][i] is not None for i in range(i0, i1 + 1)))
        if key != self._strip_key or self._strip is None:
            n = i1 - i0 + 1
            pm = QPixmap(n * TILE, PREVIEW[1])
            pm.fill(QColor(theme.BG1))
            p = QPainter(pm)
            for j in range(n):
                img = fl["tiles"].get(i0 + j)
                if img is not None:
                    p.drawImage(j * TILE, 0, img)
            p.end()
            self._strip, self._strip_key = pm, key
        return self._strip, i0

    def _snapshot(self):
        """The map as it is now (no veil, no marks): it fades out when the seed changes."""
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(theme.BG1))
        p = QPainter(pm)
        self._paint_map(p)
        p.end()
        return pm

    # ── pictures kept between frames ─────────────────────────────────────────
    def _veil_layer(self):
        """The veil over the text side, the outline: one picture, laid over each frame."""
        key = (self.width(), self.height(), theme.THEME, theme.BG1, theme.LINE2, self.devicePixelRatioF())
        if key == self._veil_key and self._veil is not None:
            return self._veil
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 22, 22)
        p.setClipPath(path)
        g = QLinearGradient(r.left(), 0, r.right(), 0)       # the text side stays readable
        for t, a in ((0.0, 0.97), (0.30, 0.90), (0.58, 0.42), (0.80, 0.10), (1.0, 0.02)):
            c = QColor(theme.BG1)
            c.setAlphaF(a)
            g.setColorAt(t, c)
        p.fillRect(r, g)
        gb = QLinearGradient(0, r.top(), 0, r.bottom())      # and a little depth, top and bottom
        for t, a in ((0.0, 0.22), (0.18, 0.0), (0.80, 0.0), (1.0, 0.30)):
            c = QColor(theme.BG1)
            c.setAlphaF(a)
            gb.setColorAt(t, c)
        p.fillRect(r, gb)
        ge = QLinearGradient(r.right() - 90, 0, r.right(), 0)   # where the world comes in from
        c0 = QColor(theme.BG1); c0.setAlphaF(0.0)
        c1 = QColor(theme.BG1); c1.setAlphaF(0.35)
        ge.setColorAt(0, c0)
        ge.setColorAt(1, c1)
        p.fillRect(QRectF(r.right() - 90, r.top(), 90, r.height()), ge)
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.LINE2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()
        self._veil, self._veil_key = pm, key
        return pm

    def _sprite(self, kind, depth):
        key = (kind, depth)
        pm = self._sprites.get(key)
        if pm is None:
            pair = theme.ws_pair("home")
            col = pair[kind] if kind in (0, 1) else "#ffffff"
            rad, core = ((4.5, 1.3), (7.0, 1.9), (11.0, 2.6))[depth]
            pm = self._sprites[key] = _glow_sprite(col, rad, core, self.devicePixelRatioF())
        return pm

    def _cursor_light(self):
        pm = self._sprites.get("cursor")
        if pm is None:
            c = QColor(theme.ws_pair("home")[0])
            pm = self._sprites["cursor"] = _glow_sprite(c.lighter(140).name(), 120, 0, self.devicePixelRatioF())
        return pm

    # ── the picture ──────────────────────────────────────────────────────────
    def _to_screen(self, bx, bz):
        """A block of the world → the hero (the flight's camera, the lean)."""
        fl = self._flight
        k = self._scale()
        sc = PREVIEW[2]
        top = self.height() / 2 - PREVIEW[1] * k / 2
        return ((bx / sc - fl["cam"]) * k + self._lean[0], top + (bz / sc - fl["z0"]) * k + self._lean[1])

    def _paint_map(self, p):
        strip, i0 = self._strip_pixmap()
        if strip is None:
            return
        fl = self._flight
        k = self._scale()
        top = self.height() / 2 - PREVIEW[1] * k / 2 + self._lean[1]
        x = (fl["x0"] + i0 * TILE - fl["cam"]) * k + self._lean[0]
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(QRectF(x, top, strip.width() * k, strip.height() * k), strip, QRectF(strip.rect()))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = time.monotonic()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 22, 22)
        p.setClipPath(clip)
        p.fillPath(clip, QColor(theme.BG1))
        if self._old_snap is not None and self._fade < 1.0:
            p.drawPixmap(0, 0, self._old_snap)
            p.setOpacity(self._fade)
        self._paint_map(p)
        p.setOpacity(1.0)
        p.drawPixmap(0, 0, self._veil_layer())
        p.setClipPath(clip)
        self._paint_marks(p, now)
        if self._mouse is not None and motion.enabled():            # a soft light under the cursor
            cl = self._cursor_light()
            s = cl.width() / cl.devicePixelRatio()
            p.setOpacity(0.10 + 0.08 * min(1.0, self._mouse.x() / max(1.0, self.width()) * 1.6))
            p.drawPixmap(QRectF(self._mouse.x() - s / 2, self._mouse.y() - s / 2, s, s), cl, QRectF(cl.rect()))
            p.setOpacity(1.0)
        if self._tick.isActive():
            self._paint_motes(p, now)

    def _paint_marks(self, p, now):
        """The spawn (a pulse) and the structures, on the map side; they travel with the map."""
        self._hits = []
        fl = self._flight
        if fl is None or not fl["ready"]:
            return
        from . import struct_icons
        w, hh = self.width(), self.height()
        a = self._appear
        isz = 20
        n = 0
        for i in sorted(fl["structs"]):
            for key, bx, bz in fl["structs"][i]:
                sx, sy = self._to_screen(bx, bz)
                if sx < w * 0.40 or sx > w + 20 or sy < 14 or sy > hh - 14:
                    continue
                edge = min(1.0, max(0.0, (sx - w * 0.40) / (w * 0.08)))   # they fade under the text
                ai = max(0.0, min(1.0, a * 2.2 - n * 0.05)) * edge
                n += 1
                if ai <= 0.01:
                    continue
                hot = self._hot is not None and self._hot[2:] == (bx, bz)
                s = isz * (1.18 if hot else 1.0) * (0.7 + 0.3 * min(1.0, a * 2.2))
                p.setOpacity(ai)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(10, 14, 22, 150 if not hot else 200))
                p.drawEllipse(QRectF(sx - s * 0.72, sy - s * 0.72, s * 1.44, s * 1.44))
                ic = struct_icons.pixmap(key, int(isz))
                p.drawPixmap(QRectF(sx - s / 2, sy - s / 2, s, s), ic, QRectF(ic.rect()))
                p.setOpacity(1.0)
                self._hits.append((QRectF(sx - 14, sy - 14, 28, 28), struct_icons.label(key), bx, bz))
                if hot:
                    self._label(p, sx, sy - s, f"{struct_icons.label(key)}  ·  {bx}, {bz}")
        sp = fl.get("spawn")
        if sp is not None:
            sx, sy = self._to_screen(*sp)
            if w * 0.40 < sx < w and 0 < sy < hh:
                col = QColor(theme.ws_pair("home")[0])
                t = ((now - self._t0) % 2.4) / 2.4 if motion.enabled() else 0.4
                for ph in (0.0, 0.5):                               # two rings, one after the other
                    tt = (t + ph) % 1.0
                    ring = QColor(col)
                    ring.setAlphaF(0.75 * (1 - tt) * a)
                    p.setPen(QPen(ring, 2.0))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    rr = 7 + 26 * tt
                    p.drawEllipse(QRectF(sx - rr, sy - rr, 2 * rr, 2 * rr))
                p.setPen(QPen(QColor(255, 255, 255, int(235 * a)), 2.2))
                p.setBrush(col)
                p.drawEllipse(QRectF(sx - 6.5, sy - 6.5, 13, 13))
                self._hits.append((QRectF(sx - 14, sy - 14, 28, 28), "Spawn", sp[0], sp[1]))
                if self._hot is not None and self._hot[1] == "Spawn":
                    self._label(p, sx, sy - 12, f"Spawn  ·  {sp[0]}, {sp[1]}")
        if self._mouse is not None:              # the marks move: what is under a still mouse changes
            hot = next((h for h in self._hits if h[0].contains(self._mouse)), None)
            if (hot[2:] if hot else None) != (self._hot[2:] if self._hot else None):
                self._hot = hot
                self.setCursor(Qt.CursorShape.PointingHandCursor if hot else Qt.CursorShape.ArrowCursor)

    def _label(self, p, x, y, text):
        f = QFont(theme.ui_font(8.4))
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text) + 18
        r = QRectF(min(max(x - tw / 2, 8), self.width() - tw - 8), y - 30, tw, 22)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(QColor(12, 16, 26, 225))
        p.drawRoundedRect(r, 11, 11)
        p.setPen(QColor("#ffffff"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_motes(self, p, now):
        w, h = self.width(), self.height()
        for f in self._motes:
            tw = 0.5 + 0.5 * math.sin(now * (1.1 + 0.4 * f["f"]) + f["ph"])
            a = (0.30 + 0.70 * tw) * (0.6, 0.85, 1.0)[f["d"]]
            a *= min(1.0, 0.2 + (f["x"] + f["ox"]) * 1.4)       # fainter over the text side
            lean = (0.3, 0.6, 1.0)[f["d"]]                       # the near ones lean more (depth)
            x = (f["x"] + f["ox"]) * w + self._lean[0] * lean
            y = (f["y"] + f["oy"]) * h + self._lean[1] * lean
            spr = self._sprite(f["c"], f["d"])
            s = spr.width() / spr.devicePixelRatio()
            p.setOpacity(min(1.0, a))
            p.drawPixmap(QRectF(x - s / 2, y - s / 2, s, s), spr, QRectF(spr.rect()))
        p.setOpacity(1.0)


class Tile(QFrame):
    def __init__(self, win, key, title, sub, icon):
        super().__init__()
        self.win, self.key, self.icon = win, key, icon
        self.setObjectName("WsTile")
        self.setProperty("ws", key)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(92)
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 16, 14)
        h.setSpacing(14)
        self.ic = QLabel()
        self.ic.setFixedSize(48, 48)
        h.addWidget(self.ic)
        v = QVBoxLayout()
        v.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("TileTitle")
        s = QLabel(sub)
        s.setObjectName("Muted")
        for lab in (t, s):
            lab.setMinimumWidth(40)
        # (15/11) no tooltip: the tile shows its title and what it is for
        v.addStretch(1)
        v.addWidget(t)
        v.addWidget(s)
        v.addStretch(1)
        h.addLayout(v, 1)
        self._spot = motion.Spot(self, theme.ws_color(key), strength=0.17, radius=14)   # (15/11)
        theme.signals.changed.connect(self.sync)
        self.sync()

    def sync(self):
        self.ic.setPixmap(brand.ws_tile(self.icon, theme.ws_color(self.key), 48, 28))

    # (14/11) under the mouse: a glow of the workspace's colour rises
    def _glow(self, on):
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        if not motion.enabled():
            return
        fx = self.graphicsEffect()
        if not isinstance(fx, QGraphicsDropShadowEffect):      # (19/11) none, or the intro's fade
            fx = QGraphicsDropShadowEffect(self)
            fx.setOffset(0, 5)
            fx.setBlurRadius(0)
            self.setGraphicsEffect(fx)
        c = QColor(theme.ws_color(self.key))
        b0 = fx.blurRadius()
        b1 = 34.0 if on else 0.0
        fx.setEnabled(True)

        def step(t):
            b = b0 + (b1 - b0) * t
            fx.setBlurRadius(b)
            c.setAlphaF(0.50 * b / 34.0)
            fx.setColor(c)

        def done():
            if not on:
                fx.setEnabled(False)
        old = getattr(self, "_glow_an", None)
        if old is not None:
            try:
                old.stop()
            except RuntimeError:
                pass
        self._glow_an = motion.run(self, 170, step, done)

    def enterEvent(self, e):
        super().enterEvent(e)
        self._glow(True)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._glow(False)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.win.show_page(self.key)


# ── (19/11) what each version brought, from Updates.txt ─────────────────────
def parse_updates(text: str):
    """[{ver, date, headline, entries: [{title, text, bug, more: [lines]}]}], newest first.
    Updates.txt: a « vX.Y.Z — date » line opens a version, « # … — headline » gives its
    headline, « - **Title**: text » an entry (« Bug fixed » ones list the fixes, comma
    separated); indented « - … » lines under an entry are its details."""
    import re
    out, cur, last, skip = [], None, None, False

    def clean(t):                                          # the Markdown marks go
        return re.sub(r"[`*]", "", t).strip()
    for raw in text.splitlines():
        ln = raw.strip()
        if ln.startswith("v") and ln[1:2].isdigit():
            ver, _s, date = ln.partition("—")
            cur = dict(ver=ver.strip(), date=date.strip(), headline="", entries=[], note="")
            out.append(cur)
            last, skip = None, False
            continue
        if ln.startswith("## "):                           # « Download », « Install »: not news
            skip = any(w in ln.lower() for w in ("download", "install", "credit", "thank"))
            continue
        if cur is None or skip or not ln or set(ln) <= set("-="):
            continue
        if ln.startswith("# "):
            head = ln[2:]
            cur["headline"] = head.split("—", 1)[1].strip() if "—" in head else head
            continue
        if raw[:1] in (" ", "\t") and ln.startswith("- ") and last is not None:
            last["more"].append(clean(ln[2:]))
            continue
        if ln.startswith("-") and "**" in ln:
            body = ln.lstrip("-").strip()
            if body.startswith("**") and "**" in body[2:]:
                k = body.index("**", 2)
                title, rest = body[2:k].strip().rstrip(":"), body[k + 2:].lstrip(":").strip()
                rest = clean(rest)
                last = dict(title=title, text=rest[:1].upper() + rest[1:], bug=title.lower().startswith("bug"),
                            more=[])
                cur["entries"].append(last)
                continue
        if not cur["entries"] and not cur["headline"]:
            cur["headline"] = clean(ln)                    # a version without a « # » line
        elif cur["entries"] and not cur["note"] and not ln.startswith("-"):
            cur["note"] = clean(ln)                        # a sentence after the list
    return out


def read_updates():
    for d in (APP_DIR, os.path.dirname(APP_DIR)):          # sources / next to the .exe
        try:
            with open(os.path.join(d, "Updates.txt"), encoding="utf-8") as fh:
                return parse_updates(fh.read())
        except OSError:
            continue
    return []


def _split_top(text: str):
    """« a, b (c, d), e » → [a, b (c, d), e]: a list of fixes, commas inside brackets kept."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth <= 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return [x[:1].upper() + x[1:] for x in parts if x]


class _NewsRow(QFrame):
    """One novelty: its icon, title and the start of its text; a click unfolds the
    whole text (and the list of fixes) — one row open at a time."""
    toggled = Signal(object)

    def __init__(self, entry, color):
        super().__init__()
        self.entry, self.col = entry, QColor(color)
        self.open, self._h, self._an = False, 0.0, None
        self._hov, self._han = 0.0, None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 0, 12, 0)
        v.setSpacing(0)
        head = QWidget()
        head.setFixedHeight(44)
        head.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        h = QHBoxLayout(head)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(11)
        ic = QLabel()
        ic.setFixedSize(32, 32)
        from . import news_icons                  # (19/11) a real picture of the change
        ic.setPixmap(news_icons.tile(entry["title"], 32)[0])
        h.addWidget(ic)
        t = QLabel(entry["title"])
        f = t.font()
        f.setWeight(QFont.Weight.DemiBold)
        t.setFont(f)
        h.addWidget(t)
        h.addStretch(1)                          # (19/11) the text only shows once unfolded
        self.chev = QLabel()
        self.chev.setFixedSize(16, 16)
        h.addWidget(self.chev)
        v.addWidget(head)
        self.fold = QWidget()                     # the unfolding part (its height is animated)
        self.fold.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        fl = QVBoxLayout(self.fold)
        fl.setContentsMargins(43, 0, 6, 12)
        self.detail = QLabel(self._detail_html())
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setObjectName("NewsDetail")
        fl.addWidget(self.detail)
        self.fold.setMaximumHeight(0)
        v.addWidget(self.fold)
        self._chev()

    def _detail_html(self):
        import html
        e = self.entry
        dot = f"<td style='color:{self.col.name()}; padding-right:8px'>•</td>"
        items = _split_top(e["text"]) if e["bug"] else []
        if len(items) > 1:
            body = ("<table cellspacing='0' cellpadding='1'>" + "".join(
                f"<tr>{dot}<td>{html.escape(x)}</td></tr>" for x in items) + "</table>")
        else:
            body = f"<div>{html.escape(e['text'])}</div>"
        if e["more"]:
            body += ("<table cellspacing='0' cellpadding='1' style='margin-top:4px'>" + "".join(
                f"<tr>{dot}<td>{html.escape(x)}</td></tr>" for x in e["more"]) + "</table>")
        return f"<div style='line-height:135%'>{body}</div>"

    def _chev(self):
        """The chevron, turned with the unfolding (down → up), coloured when open / hovered."""
        from PySide6.QtGui import QTransform
        hot = self.open or self._hov > 0.5
        pm = icons.pixmap("chevron", 16, self.col.name() if hot else theme.TEXT3)
        if self._h > 0.001:
            pm = pm.transformed(QTransform().rotate(180 * self._h), Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(2.0)
        self.chev.setPixmap(pm)

    def set_open(self, on, animate=True):
        if on == self.open:
            return
        self.open = on
        if self._an is not None:
            try:
                self._an.stop()
            except RuntimeError:
                pass
        w = self.fold.width() - 49 if self.fold.width() > 100 else max(60, self.width() - 69)
        full = self.detail.heightForWidth(w) + 12
        h0, h1 = self._h, (1.0 if on else 0.0)

        def step(t):
            self._h = h0 + (h1 - h0) * t
            self.fold.setMaximumHeight(int(round(full * self._h)))
            self._chev()
            self.update()

        def done():
            if self.open:                       # open: the text may still re-wrap with the width
                self.fold.setMaximumHeight(16777215)
        if not animate or not motion.enabled():
            step(1.0)
            done()
            return
        self._an = motion.run(self, 280 if on else 200, step, done)

    def enterEvent(self, e):
        super().enterEvent(e)
        self._hover(1.0)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hover(0.0)

    def _hover(self, to):
        if self._han is not None:
            try:
                self._han.stop()
            except RuntimeError:
                pass
        a = self._hov

        def step(t):
            self._hov = a + (to - a) * t
            self._chev()
            self.update()
        if not motion.enabled():
            step(1.0)
            return
        self._han = motion.run(self, 150, step)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.toggled.emit(self)

    def paintEvent(self, e):
        k = max(self._hov * 0.6, self._h)
        if k <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(r.topLeft(), r.topRight())
        c1, c2 = QColor(self.col), QColor(self.col)
        c1.setAlphaF(0.12 * k)
        c2.setAlphaF(0.02 * k)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 11, 11)
        if self._h > 0.01:                       # an open row: a thin bar of its colour on the left
            bar = QColor(self.col)
            bar.setAlphaF(0.85 * self._h)
            p.setBrush(bar)
            p.drawRoundedRect(QRectF(r.left() + 1, r.top() + 12, 3, max(0.0, r.height() - 24)), 1.5, 1.5)


def _inner_scroll(content):
    """(19/11) a list inside a card that scrolls by itself (Home itself never scrolls)."""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
    sa.viewport().setAutoFillBackground(False)
    sa.setWidget(content)
    return sa


class NewsCard(QFrame):
    """(19/11) « What's new »: every version of Updates.txt (pills), its headline, and
    its novelties one per row — a click on a row shows its whole text."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("Surface")
        self.setMinimumHeight(170)
        self.versions = read_updates()
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 14)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(4)
        t = QLabel("What's new")
        t.setObjectName("PanelTitle")
        top.addWidget(t)
        top.addStretch(1)
        self.pills = []
        for i, ver in enumerate(self.versions[:4]):
            b = QPushButton(ver["ver"])
            b.setObjectName("VerPill")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if ver["date"]:
                b.setToolTip(ver["date"])
            b.clicked.connect(lambda _c=False, k=i: self.show_version(k))
            top.addWidget(b)
            self.pills.append(b)
        v.addLayout(top)
        self.head = QLabel()
        self.head.setObjectName("Muted")
        self.head.setWordWrap(True)
        v.addWidget(self.head)
        v.addSpacing(4)
        self.host = QWidget()
        hv = QVBoxLayout(self.host)
        hv.setContentsMargins(0, 0, 4, 0)
        hv.setSpacing(0)
        self.rows_lay = QVBoxLayout()
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(1)
        hv.addLayout(self.rows_lay)
        self.note = QLabel()
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        self.note.setContentsMargins(8, 6, 8, 0)
        hv.addWidget(self.note)
        hv.addStretch(1)
        self.scroll = _inner_scroll(self.host)
        v.addWidget(self.scroll, 1)
        self.rows = []
        self.cur = -1
        if self.versions:
            self.show_version(0, animate=False)
        else:
            self.head.setText("Updates.txt was not found next to SSA.")

    @staticmethod
    def _colors():
        cols = [theme.ws_color(k) for k in theme.WS_KEYS[1:]]
        if len(set(cols)) == 1:                # a theme with one accent: the usual hues
            cols = [theme.OK, theme.ACCENT, theme.WARN]
        return cols

    def show_version(self, k, animate=True):
        for i, b in enumerate(self.pills):
            b.setChecked(i == k)
        if k == self.cur:
            return
        self.cur = k
        ver = self.versions[k]
        self.head.setText(ver["headline"] + (f"   ·   {ver['date']}" if ver["date"] else ""))
        self.note.setText(ver.get("note", ""))
        self.note.setVisible(bool(ver.get("note")))
        for r in self.rows:
            self.rows_lay.removeWidget(r)
            r.deleteLater()
        self.rows = []
        from . import news_icons
        cols = self._colors()
        n = 0
        for e in ver["entries"]:
            col = news_icons.pick(e["title"])[1] if e["title"] else cols[n % len(cols)]
            n += not e["bug"]
            r = _NewsRow(e, col)
            r.toggled.connect(self._toggle)
            self.rows_lay.addWidget(r)
            self.rows.append(r)
        if animate and motion.enabled():
            for i, r in enumerate(self.rows):          # the rows come in one after the other
                fx = QGraphicsOpacityEffect(r)
                fx.setOpacity(0.0)
                r.setGraphicsEffect(fx)
                QTimer.singleShot(22 * i, lambda w=r: motion.fade_in(w, 220))

    def _toggle(self, row):
        for r in self.rows:
            r.set_open(r is row and not r.open)
        if row.open:                                    # an unfolded row near the bottom: into view
            QTimer.singleShot(320, lambda: self._into_view(row))

    def _into_view(self, row):
        try:
            self.scroll.ensureWidgetVisible(row, 0, 6)
        except RuntimeError:
            pass


class _RecentRow(QFrame):
    def __init__(self, win, meta):
        super().__init__()
        from . import session_library as sl
        from .session_model import type_color
        self.win, self.path = win, meta.get("path")
        self.setObjectName("RecentRow")
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(9, 0, 14, 0)
        h.setSpacing(11)
        sign = QLabel()                        # (15/11) the session's picture, as in the library
        sign.setFixedSize(36, 36)
        sign.setPixmap(sl.entry_pixmap(meta, 36))      # (19/11) its own picture when it has one
        h.addWidget(sign)
        n = QLabel(meta.get("name", ""))
        f = n.font()
        f.setWeight(QFont.Weight.DemiBold)
        n.setFont(f)
        h.addWidget(n)
        types = [t for t in (meta.get("types") or []) if t][:8]
        if types:                              # one dot per condition, in its type's colour
            pm = QPixmap(2 * (len(types) * 11 + 2), 2 * 12)
            pm.setDevicePixelRatio(2.0)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            for i, t in enumerate(types):
                p.setBrush(QColor(type_color(t)))
                p.drawEllipse(QRectF(i * 11 + 1, 2, 8, 8))
            p.end()
            dots = QLabel()
            dots.setPixmap(pm)
            h.addWidget(dots)
        h.addStretch(1)
        info = QLabel(f"{sl._plural(meta.get('n_cond', 0), 'condition')}   ·   {meta.get('mc', '?')}"
                      f"   ·   {sl._when(meta.get('modified', ''))}")
        info.setObjectName("Muted")
        h.addWidget(info)
        self._spot = motion.Spot(self, theme.ws_color("sessions"), strength=0.14, radius=10)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.rect().contains(e.position().toPoint()):
            return
        sp = self.win.sessions
        if self.path and sp.open_library_entry(self.path):
            self.win.show_page("sessions")


class RecentCard(QFrame):
    """(19/11) the last sessions of the library — or, with none yet, where they will come from."""

    def __init__(self, win):
        super().__init__()
        from . import session_library as sl
        self.win = win
        self.setObjectName("Surface")
        self.setMinimumHeight(170)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 16)
        v.setSpacing(6)
        top = QHBoxLayout()
        t = QLabel("Recent sessions")
        t.setObjectName("PanelTitle")
        top.addWidget(t)
        top.addStretch(1)
        try:
            ents = sl.list_entries()[:6]
        except Exception:
            ents = []
        if ents:
            lib = QPushButton("  Library")
            lib.setObjectName("CardLink")
            lib.setCursor(Qt.CursorShape.PointingHandCursor)
            icons.bind(lib, "library", 15)
            lib.setToolTip("Ctrl+L")
            lib.clicked.connect(self._library)
            top.addWidget(lib)
        v.addLayout(top)
        v.addSpacing(6)
        if ents:
            host = QWidget()
            hv = QVBoxLayout(host)
            hv.setContentsMargins(0, 0, 4, 0)
            hv.setSpacing(6)
            for m in ents:
                hv.addWidget(_RecentRow(win, m))
            hv.addStretch(1)
            v.addWidget(_inner_scroll(host), 1)
        if not ents:
            v.addStretch(1)
            box = QVBoxLayout()
            box.setSpacing(8)
            ic = QLabel()
            ic.setFixedSize(58, 58)
            ic.setPixmap(brand.icon_tile("sessions", theme.ws_color("sessions"), 58, 28))
            box.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
            box.addSpacing(4)
            a = QLabel("No saved session yet")
            a.setObjectName("TileTitle")
            box.addWidget(a, 0, Qt.AlignmentFlag.AlignHCenter)
            b = QLabel("Build a search in Sessions and save it (Ctrl+S): it shows up here.")
            b.setObjectName("Muted")
            box.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
            box.addSpacing(8)
            go = QPushButton("  New session")
            go.setProperty("kind", "ghost")
            icons.bind(go, "plus", 16)
            go.setMinimumHeight(36)
            go.setCursor(Qt.CursorShape.PointingHandCursor)
            go.clicked.connect(lambda: win.show_page("sessions"))
            self._spot = motion.Spot(go)
            box.addWidget(go, 0, Qt.AlignmentFlag.AlignHCenter)
            v.addLayout(box)
            v.addStretch(1)

    def _library(self):
        self.win.show_page("sessions")
        QTimer.singleShot(0, self.win.sessions.open_library)


class _MiniSeed(QWidget):
    """(19/11) a kept seed on Home: its cover, its sigil, its name; a click opens it in the library."""
    W, H = 168, 124

    def __init__(self, win, s):
        super().__init__()
        self.win, self.s = win, s
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hov, self._an, self._pm = 0.0, None, None
        from .seed_library import _cover_image, renderer, world_key
        img = _cover_image(s)
        if img is not None:
            self._set(img)
        else:
            renderer().done.connect(self._in)
            renderer().request(("home", s["id"]), 0, world_key(s), 0, 0, 16, 120, 70, structs=False,
                               around_spawn=True)
        self.setToolTip(s.get("name") and s["seed"] or "")

    def _in(self, owner, tok, img, info):
        if owner == ("home", self.s["id"]) and isinstance(img, QImage):
            try:
                from .seed_store import thumb_path
                img.save(thumb_path(self.s["id"]))
                self._set(img)
            except RuntimeError:
                pass

    def _set(self, img):
        from .seed_library import _rounded
        self._pm = _rounded(img, self.W, self.H - 30, 12, top_only=True)
        self.update()

    def enterEvent(self, e):
        super().enterEvent(e)
        self._hover(1.0)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hover(0.0)

    def _hover(self, to):
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
        self._an = motion.run(self, 160, step)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.win.open_seed_library(self.s["id"])

    def paintEvent(self, e):
        from .seed_library import GOLD, label
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = self._hov
        r = QRectF(0.5, 0.5 + 2 - 2 * k, self.W - 1, self.H - 3)
        bg = QColor(theme.BG2)
        bg.setAlphaF(0.9)
        p.setPen(QPen(motion.mix(theme.LINE, GOLD, 0.7 * k), 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 12, 12)
        if self._pm is not None:
            p.drawPixmap(QRectF(r.left(), r.top(), r.width(), r.height() - 30), self._pm, QRectF(self._pm.rect()))
        sg = brand.sigil(int(self.s["seed"]), 22)
        p.drawPixmap(QRectF(r.left() + 8, r.bottom() - 26, 22, 22), sg, QRectF(sg.rect()))
        f = theme.ui_font(8.8, QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(r.left() + 36, r.bottom() - 27, r.width() - 44, 24), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(label(self.s), Qt.TextElideMode.ElideRight, int(r.width() - 44)))
        if self.s.get("fav"):
            st = icons.pixmap("star_fill", 14, GOLD)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(10, 14, 22, 150))
            p.drawEllipse(QRectF(r.right() - 26, r.top() + 6, 20, 20))
            p.drawPixmap(QRectF(r.right() - 23, r.top() + 9, 14, 14), st, QRectF(st.rect()))


class SeedsCard(QFrame):
    """(19/11) Home's view of the Seed Library: the favourites, then the last kept seeds."""

    def __init__(self, win):
        super().__init__()
        from .seed_store import store
        self.win = win
        self.setObjectName("Surface")
        self.setMinimumHeight(170)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 16)
        v.setSpacing(6)
        top = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap("star_fill", 18, "#f5b942"))
        top.addWidget(ic)
        t = QLabel("Seed Library")
        t.setObjectName("PanelTitle")
        top.addWidget(t)
        top.addStretch(1)
        seeds = sorted(store().seeds, key=lambda s: (not s.get("fav"), s.get("modified", "")), reverse=False)
        seeds = [s for s in seeds if s.get("fav")] + sorted([s for s in seeds if not s.get("fav")],
                                                            key=lambda s: s.get("modified", ""), reverse=True)
        if seeds:
            op = QPushButton("  Open")
            op.setObjectName("CardLink")
            op.setCursor(Qt.CursorShape.PointingHandCursor)
            icons.bind(op, "seed_library", 15)
            op.clicked.connect(lambda: win.open_seed_library())
            top.addWidget(op)
        v.addLayout(top)
        v.addSpacing(6)
        if seeds:
            from .widgets import FlowLayout
            host = QWidget()
            fl = FlowLayout(host, 10, 10)
            for s in seeds[:12]:
                fl.addWidget(_MiniSeed(win, s))
            from .seed_library import AddTile               # (20/11) a seed typed in, from here too
            t = AddTile(_MiniSeed.W, _MiniSeed.H, "Add a seed")
            t.setToolTip("Type a seed in (or paste one)")
            t.clicked.connect(lambda: win.open_seed_library(add=True))
            fl.addWidget(t)
            v.addWidget(_inner_scroll(host), 1)
        else:
            v.addStretch(1)
            box = QVBoxLayout()
            box.setSpacing(8)
            tile = QLabel()
            tile.setFixedSize(58, 58)
            tile.setPixmap(brand.icon_tile("star_fill", "#f5b942", 58, 28))
            box.addWidget(tile, 0, Qt.AlignmentFlag.AlignHCenter)
            box.addSpacing(4)
            a = QLabel("No seed kept yet")
            a.setObjectName("TileTitle")
            box.addWidget(a, 0, Qt.AlignmentFlag.AlignHCenter)
            b = QLabel("The ★ next to the seed in the top bar keeps it here, with its own board.")
            b.setObjectName("Muted")
            b.setWordWrap(True)
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
            box.addSpacing(8)
            go = QPushButton("  Keep the current seed")
            go.setProperty("kind", "ghost")
            icons.bind(go, "star", 16)
            go.setMinimumHeight(36)
            go.setCursor(Qt.CursorShape.PointingHandCursor)
            go.clicked.connect(win.seed_star)
            self._spot = motion.Spot(go)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addStretch(1)
            row.addWidget(go)
            ad = QPushButton("  Add a seed")
            ad.setProperty("kind", "ghost")
            icons.bind(ad, "plus", 16)
            ad.setMinimumHeight(36)
            ad.setCursor(Qt.CursorShape.PointingHandCursor)
            ad.clicked.connect(lambda: win.open_seed_library(add=True))
            row.addWidget(ad)
            row.addStretch(1)
            box.addLayout(row)
            v.addLayout(box)
            v.addStretch(1)



class HomePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("Page")
        # (19/11) no scroll: the page fits the window — the two cards below take what is
        # left and scroll inside themselves when their list is longer
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 8, 26, 18)
        v.setSpacing(16)
        self.hero = Hero(win)
        v.addWidget(self.hero)
        row = QHBoxLayout()
        row.setSpacing(14)
        self._intro = []
        for key, title, sub, ic in TILES:
            t = Tile(win, key, title, sub, ic)
            row.addWidget(t, 1)
            self._intro.append(t)
        v.addLayout(row)
        self._bot = QHBoxLayout()
        self._bot.setSpacing(14)
        self._rec = self._recent()
        self._bot.addWidget(self._rec, 3)
        self._seeds = SeedsCard(win)               # (19/11) the Seed Library
        self._bot.addWidget(self._seeds, 3)
        from .seed_store import store
        self._seeds_t = QTimer(self)
        self._seeds_t.setSingleShot(True)
        self._seeds_t.setInterval(350)
        self._seeds_t.timeout.connect(self._seeds_again)
        store().changed.connect(lambda what: what in ("seeds", "shelves") and self._seeds_t.start())
        from .seed_library import renderer as _lib_renderer          # (20/11) a cover made again
        _lib_renderer().cover_saved.connect(lambda _sid: self._seeds_t.start())
        self._nw = self._news()
        self._bot.addWidget(self._nw, 3)
        self._news_theme = theme.THEME
        v.addLayout(self._bot, 1)
        from .home_lights import HomeLights            # (20/11) light under the mouse
        self._lights = HomeLights(self, self._light_blocks)

    def _light_blocks(self):
        """[(widget, colour, corner radius)]: what the light of the mouse knows on the page."""
        out = [(self.hero, theme.ws_color("home"), 22)]
        out += [(t, theme.ws_color(t.key), 16) for t in self._intro]
        out.append((self._rec, theme.ws_color("sessions"), 14))
        out.append((self._seeds, "#e8b84a", 14))
        col = theme.ws_pair("home")[1]
        for r in getattr(self._nw, "rows", []):
            if getattr(r, "open", False):
                col = r.col.name()
        out.append((self._nw, col, 14))
        return out

    def _launch_blocks(self):
        return self._intro + [self._rec, self._seeds, self._nw]

    def _launch_hold(self):
        """(20/11) SSA opens: the blocks under the hero wait, invisible, for the veil to open."""
        for w in self._launch_blocks():
            fx = QGraphicsOpacityEffect(w)
            fx.setOpacity(0.0)
            w.setGraphicsEffect(fx)

    def launch_reveal(self):
        """(20/11) the veil opens: the seed settles, the tiles then the cards rise in."""
        try:
            motion.roll_text(self.hero.seed, str(self.win.world.seed), 620)
        except Exception:                      # noqa: BLE001
            pass
        # (20/11 bis) photos of the blocks glide in (launch.RiseLayer): no widget moves or
        # repaints through an effect while the veil opens — the real block takes over at the end
        items = [(w, 24, 120 + 55 * i, 560, 16) for i, w in enumerate(self._intro)]
        items += [(w, 30, 300 + 80 * i, 620, 14) for i, w in enumerate((self._rec, self._seeds, self._nw))]
        try:
            from .launch import RiseLayer
            self._rise_layer = RiseLayer(self.win, items)
            veil = getattr(self.win, "_veil", None)
            if veil is not None:
                veil.raise_()                  # the veil stays above the rising photos
        except Exception as ex:                # noqa: BLE001 — never invisible blocks
            print(f"[launch] rise: {ex}", flush=True)
            for w, *_r in items:
                try:
                    w.setGraphicsEffect(None)
                except RuntimeError:
                    pass

    def _seeds_again(self):
        """(19/11) a seed kept / removed: the card again (only while Home shows)."""
        if not self.isVisible():
            return
        rl = getattr(self, "_rise_layer", None)
        try:
            if rl is not None and rl.isVisible():      # (20/11 bis) not while the blocks rise in
                self._seeds_t.start()
                return
        except RuntimeError:
            pass
        old = self._seeds
        new = SeedsCard(self.win)
        self._bot.replaceWidget(old, new)
        old.deleteLater()
        self._seeds = new
        self._lights._fit()
        motion.fade_in(new, 220)

    def _recent(self):
        return RecentCard(self.win)

    def _news(self):
        return NewsCard(self.win)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._lights._fit)  # (20/11) the light's layers around the new cards
        old = self._rec                        # the library may have changed
        new = self._recent()
        self._bot.replaceWidget(old, new)
        old.deleteLater()
        self._rec = new
        old = self._seeds                      # (19/11) and the Seed Library
        new = SeedsCard(self.win)
        self._bot.replaceWidget(old, new)
        old.deleteLater()
        self._seeds = new
        if theme.THEME != self._news_theme:    # (19/11) otherwise the news keep their open row
            old = self._nw
            new = self._news()
            self._bot.replaceWidget(old, new)
            old.deleteLater()
            self._nw = new
            self._news_theme = theme.THEME
        if not getattr(self, "_entered", False) and motion.enabled():
            self._entered = True               # (19/11) the first time: the blocks come in one by one
            if getattr(self.win, "_launching", False):
                self._launch_hold()            # (20/11) ... once SSA's opening veil opens (launch.py)
                return
            for i, w in enumerate(self._intro + [self._rec, self._seeds, self._nw]):
                fx = QGraphicsOpacityEffect(w)
                fx.setOpacity(0.0)
                w.setGraphicsEffect(fx)
                QTimer.singleShot(60 + 55 * i, lambda w=w: motion.fade_in(w, 320))
