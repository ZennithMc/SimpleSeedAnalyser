"""
qtui/map_view.py — the interactive biome map.

  * drag to pan (with inertia), wheel / pinch to zoom around the cursor with a
    smooth animation, double-click to zoom in, arrows / +/- / Home keys
  * progressive rendering: a coarser cached tile is stretched over any tile that
    is still generating (no holes); tiles are requested centre-first, a ring
    around the view is prefetched, and stale requests are skipped
  * noise overlay (climate noises, octaves, approximate height, exact Y) with
    its own palette and opacity, plus octave gradient arrows (∇ field) or the
    raw Perlin lattice vectors of one octave
  * structures, spawn, strongholds and origin as crisp pixel-art icons
  * live read-out (block, biome, noise value) — right-click menu — Ctrl+G
"""
from __future__ import annotations

import colorsys
import math
import time

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QInputDialog, QLabel, QMenu, QToolButton, QVBoxLayout, QWidget

import cubiomes_wrap as cw
import noise_palettes as npal
from . import struct_icons

from . import icons, theme
from .map_engine import CLIMATE, SCALES, TILE, MapEngine, octave_arrays

MIN_BPP, MAX_BPP = 1 / 32, 1024.0      # blocks per screen pixel
STRUCT_MAX_BPP = 48.0                  # structures hidden when zoomed out further
BOX_MAX_BPP = 4.0                      # real footprints of the structures from this zoom
PIECES_MAX_BPP = 2.0                   # stronghold pieces from this zoom (03/10)
ARROW_UNIFORM = {"White": "#f2f2f2", "Black": "#111111", "Cyan": "#33e0ff", "Yellow": "#ffe14d"}


def _fmt_blocks(d):
    return (f"{d:,.0f} blocks" if d >= 1.5 else f"{d:.1f} block").replace(",", " ")


def _pil_to_qimage(img) -> QImage:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    return QImage(data, img.width, img.height, img.width * 4,
                  QImage.Format.Format_RGBA8888).copy()


def _default_noise_palette():
    """Startup noise palette: one of the extended palettes (Eclipse / Nocturne)."""
    import random
    pool = [n for n in getattr(npal, "STOP_BUILTINS", {}) if n in npal.NOISE_PALETTE_NAMES]
    return random.choice(pool) if pool else "Aurora"


class MapView(QWidget):
    hover = Signal(int, int, str)            # block x, block z, biome name
    status = Signal(str)
    overlayChanged = Signal()
    picked = Signal(dict)                    # area drawn in pick mode (see start_pick)
    pickCancelled = Signal()
    measureChanged = Signal(bool)            # measuring tool on / off

    MEASURE = True                           # the ruler tool exists on this map
    STRUCT_LEGEND = True                     # the structure legend may be drawn

    def __init__(self, world, parent=None, link=None):
        super().__init__(parent)
        self.world = world
        self.link = link
        self.engine = link.engine if link is not None else MapEngine(world)
        self.cx = 0.0
        self.cz = 0.0
        self.bpp = 8.0
        self.right_inset = 0                     # width covered by the Layers drawer
        self._style = {"icon_px": 22}            # shared with the linked maps
        self.visible = {"__spawn": True, "__strongholds": True, "__origin": True,
                        "village": True, "zombie_village": True, "pillager_outpost": True, "desert_pyramid": True,
                        "jungle_temple": True, "swamp_hut": True, "igloo": True, "mansion": True}
        # noise overlay state
        self.ov = dict(noise=None, octave=None, caves=True, palette=_default_noise_palette(), opacity=1.0,
                       arrows=False, arrow_mode="Gradient", arrow_color="Direction",
                       arrow_density=1.0, arrow_length=1.0, arrow_y=0.0)
        self._drag = None
        self._vel = (0.0, 0.0)
        self._last_move = None
        self._target_bpp = None
        self._zoom_anchor = None
        self._icon_cache: dict = {}
        self._ui_libs: dict = {}
        self._arrows = None                      # (key, [(wx, wz, dx, dz, t, vy), …], note)
        self._arrow_key = None
        # session overlays: condition zones and result markers (world coords)
        self.zones = []       # dict(shape="circle"/"rect", cx, cz, r | x1, z1, x2, z2, color, label, active)
        self.markers = []     # dict(x, z, label, color[, num, detail])
        self.hit_style = "detailed"   # how numbered markers are drawn: simple / detailed
        self.legend = []      # detailed: dict(num, name, color, where) per active condition
        self.legend_top = 12
        # detailed legend = a switchboard: click a row to show / hide that
        # condition on the map, keep the button down and slide over the rows to
        # switch many at once (99-condition sessions), right-click = only this
        # one, click the title = all / none, wheel = scroll a long list.
        self.legend_hidden = set()            # condition numbers not drawn
        self.legend_scroll = 0                # first row shown
        self._legend_rows = []                # [(QRectF, num)] of the last paint
        self._legend_box = None               # QRectF of the last paint
        self._legend_head = None              # QRectF of the title (all / none)
        self._legend_drag = None              # None or (show?: bool, touched: set)
        self._legend_drag_last = None
        self._pick = None     # None or dict(kind="circle"/"rect"/"point", start=(wx, wz), cur=(wx, wz))
        # measuring tool (ruler): polylines in world coords, kept until cleared
        self.measuring = False
        self.measures = []                    # finished polylines: [[(x, z), …], …]
        self._mcur = []                       # polyline being drawn
        self._mhover = None                   # cursor (world) for the live segment
        self._mpress = None                   # press position (click vs drag)
        self._mdbl = False
        self.pin = None                       # (x, z, label) from "Paste seed + coordinates"
        self._struct_counts = {}              # structures drawn in the last paint (legend)
        if link is not None:
            link.add(self)    # shared layers / view (see map_link.py)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        self._repaint = QTimer(self, singleShot=True, interval=12, timeout=self.update)
        for sig in (self.engine.tileReady, self.engine.structuresReady, self.engine.landmarksReady):
            sig.connect(self._schedule)
        world.changed.connect(self._on_world)

        self._anim = QTimer(self, interval=15, timeout=self._animate)   # zoom easing + inertia
        self._clock = QElapsedTimer(); self._clock.start()
        self._arrow_timer = QTimer(self, singleShot=True, interval=70, timeout=self._compute_arrows)
        self._hover_timer = QTimer(self, singleShot=True, interval=30, timeout=self._do_readout)
        self._hover_pos = None
        self._build_overlay()
        theme.signals.changed.connect(self._on_theme)

    @property
    def icon_px(self):
        return self._style["icon_px"]

    @icon_px.setter
    def icon_px(self, v):
        self._style["icon_px"] = int(v)
        if self.link is not None:
            self.link.repaint_all()

    # ── floating controls ───────────────────────────────────────────────────
    def _tool(self, name, tip, fn):
        b = QToolButton(self)
        b.setObjectName("MapTool")
        icons.bind(b, name, 18)
        b.setToolTip(tip)
        b.setFixedSize(32, 32)
        b.clicked.connect(fn)
        return b

    def _build_overlay(self):
        box = QWidget(self)
        lay = QVBoxLayout(box); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        lay.addWidget(self._tool("plus", "Zoom in  (+)", lambda: self.zoom_smooth(1 / 2)))
        lay.addWidget(self._tool("minus", "Zoom out  (−)", lambda: self.zoom_smooth(2)))
        lay.addSpacing(4)
        lay.addWidget(self._tool("home", "Centre on 0, 0  (Home)", lambda: self.center_on(0, 0)))
        lay.addWidget(self._tool("target", "Centre on spawn", self.center_on_spawn))
        self._ruler = None
        if self.MEASURE:
            lay.addSpacing(4)
            self._ruler = self._tool("ruler", "Measure distances  (M)\n"
                                     "Click to add points · double-click / Enter / right-click to finish\n"
                                     "Shift: straight line (8 directions) · Backspace: undo a point\n"
                                     "Esc: leave the tool", self.toggle_measure)
            self._ruler.setCheckable(True)
            lay.addWidget(self._ruler)
        box.move(12, 12); box.adjustSize()
        self._controls = box
        self._readout = QLabel(self)
        self._readout.setObjectName("MapReadout")
        self._readout.setFont(theme.mono_font(9))
        self._readout.hide()

    def _on_theme(self):
        icons.refresh(self)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_readout()

    def showEvent(self, e):
        super().showEvent(e)
        # a linked map may have moved / changed layers while this one was hidden
        if self.ov["arrows"] and self.ov["noise"] in CLIMATE and self._arrows is None:
            self._arrow_timer.start()

    def _place_readout(self):
        self._readout.adjustSize()
        self._readout.move(12, self.height() - self._readout.height() - 12)

    # ── coordinates ─────────────────────────────────────────────────────────
    def to_screen(self, x, z):
        return ((x - self.cx) / self.bpp + self.width() / 2,
                (z - self.cz) / self.bpp + self.height() / 2)

    def to_world(self, sx, sy):
        return (self.cx + (sx - self.width() / 2) * self.bpp,
                self.cz + (sy - self.height() / 2) * self.bpp)

    def center_on(self, x, z, bpp=None):
        self._stop_motion()
        self.cx, self.cz = float(x), float(z)
        if bpp is not None:
            self.bpp = max(MIN_BPP, min(MAX_BPP, bpp))
        self._view_changed()

    def center_on_spawn(self):
        sp = self.engine.landmarks.get("spawn")
        if sp:
            self.center_on(*sp)

    def _zoom_to(self, nb, anchor):
        wx, wz = self.to_world(anchor.x(), anchor.y())
        nb = max(MIN_BPP, min(MAX_BPP, nb))
        self.cx = wx - (anchor.x() - self.width() / 2) * nb
        self.cz = wz - (anchor.y() - self.height() / 2) * nb
        self.bpp = nb

    def zoom_by(self, factor, anchor=None):
        self._zoom_to(self.bpp * factor, anchor or QPointF(self.width() / 2, self.height() / 2))
        self._view_changed()

    def zoom_smooth(self, factor, anchor=None):
        """Animated zoom: the target accumulates, the view eases towards it."""
        base = self._target_bpp if self._target_bpp is not None else self.bpp
        self._target_bpp = max(MIN_BPP, min(MAX_BPP, base * factor))
        self._zoom_anchor = anchor or QPointF(self.width() / 2, self.height() / 2)
        if not self._anim.isActive():
            self._clock.restart(); self._anim.start()

    def _animate(self):
        dt = min(0.05, self._clock.restart() / 1000.0)
        moving = False
        if self._target_bpp is not None:
            k = 1.0 - math.exp(-dt * 18.0)                # critically damped easing
            lb, lt = math.log(self.bpp), math.log(self._target_bpp)
            if abs(lt - lb) < 0.004:
                self._zoom_to(self._target_bpp, self._zoom_anchor)
                self._target_bpp = None
            else:
                self._zoom_to(math.exp(lb + (lt - lb) * k), self._zoom_anchor)
                moving = True
        vx, vz = self._vel
        if self._drag is None and (abs(vx) > 2 or abs(vz) > 2):   # inertia (px/s)
            self.cx -= vx * dt * self.bpp
            self.cz -= vz * dt * self.bpp
            decay = math.exp(-dt * 5.0)
            self._vel = (vx * decay, vz * decay)
            moving = True
        else:
            self._vel = (0.0, 0.0)
        self._view_changed()
        if not moving:
            self._anim.stop()

    def _stop_motion(self):
        self._target_bpp = None
        self._vel = (0.0, 0.0)
        self._anim.stop()

    def _view_changed(self):
        self.update()
        if self.ov["arrows"] and self.ov["noise"] in CLIMATE:
            self._arrow_timer.start()
        if self.link is not None:
            self.link.moved(self)

    def _schedule(self):
        if not self._repaint.isActive():
            self._repaint.start()

    def _on_world(self):
        self._ui_libs.clear()
        self._arrows = None
        self._view_changed()

    def set_overlay(self, **kw):
        self.ov.update(kw)
        self._arrows = None
        self._view_changed()
        self.overlayChanged.emit()
        if self.link is not None:
            self.link.overlay_changed(self)

    # ── painting ────────────────────────────────────────────────────────────
    def _tile_scale(self):
        mn = self.engine.min_scale()
        s = mn
        for k in SCALES:
            if mn <= k <= self.bpp * 2:
                s = k
        return s

    def paintEvent(self, _e):
        p = QPainter(self)
        clip = QPainterPath(); clip.addRoundedRect(QRectF(self.rect()), 10, 10)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setClipPath(clip)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), QColor(theme.BG0))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.engine.begin_frame()
        # with a noise layer the NOISE is computed first (it used to wait for
        # the biome tiles under it — beta tester, 28/09), and under an opaque
        # one the biomes are not computed at all (only cached ones are drawn)
        seed_view = self.engine.seed_view
        noise_on = bool(self.ov["noise"]) and not seed_view
        opaque = noise_on and self.ov["opacity"] >= 0.999
        if seed_view:                     # structure-seed view: no biomes (Alt+S)
            p.fillRect(self.rect(), QColor("#161c26" if not theme.IS_LIGHT else "#dfe3ea"))
        else:
            self._paint_layer(p, noise=False, fetch=not opaque, boost=-4 if noise_on else 0)
        if noise_on:
            p.setOpacity(self.ov["opacity"])
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            self._paint_layer(p, noise=True, boost=2)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            p.setOpacity(1.0)
        self.engine.end_frame()
        self._paint_grid(p)
        self._paint_origin(p)
        self._paint_arrows(p)
        self._paint_structures(p)
        self._paint_zones(p)
        self._paint_pin(p)
        self._paint_measures(p)
        self._paint_struct_legend(p)
        self._paint_scale_bar(p)
        if getattr(self, "_pieces_shown", False):
            self._paint_height_legend(p)
        if seed_view:
            self._paint_seed_view_tag(p)
        p.setClipping(False)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(theme.LINE2), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        p.end()
        n = self.engine.pending_count()
        self.status.emit(f"Generating… {n} job{'s' if n > 1 else ''} left" if n else "")

    def _visible_tiles(self, s, ring=0):
        span = TILE * s
        x0, z0 = self.to_world(0, 0)
        x1, z1 = self.to_world(self.width(), self.height())
        tx0, tx1 = math.floor(x0 / span) - ring, math.floor(x1 / span) + ring
        tz0, tz1 = math.floor(z0 / span) - ring, math.floor(z1 / span) + ring
        ctx, ctz = self.cx / span, self.cz / span
        tiles = [(tx, tz) for tz in range(tz0, tz1 + 1) for tx in range(tx0, tx1 + 1)]
        tiles.sort(key=lambda t: (t[0] + 0.5 - ctx) ** 2 + (t[1] + 0.5 - ctz) ** 2)
        return tiles, span

    def _get(self, noise, s, tx, tz, prio, fetch=True):
        if noise:
            spec = (self.ov["noise"], self.ov["octave"], self.ov["caves"])
            return self.engine.noise_tile(spec, self.ov["palette"], s, tx, tz, prio, fetch)
        return self.engine.tile(s, tx, tz, prio, fetch)

    def _paint_layer(self, p: QPainter, noise: bool, fetch=True, boost=0):
        """fetch=False: draw what is cached, request nothing (biomes under an
        opaque noise layer). boost: priority offset (noise before biomes)."""
        s = self._tile_scale()
        animating = self._anim.isActive()
        tiles, span = self._visible_tiles(s)
        for i, (tx, tz) in enumerate(tiles):
            sx, sy = self.to_screen(tx * span, tz * span)
            size = span / self.bpp
            target = QRectF(sx, sy, size + 0.6, size + 0.6)       # +0.6: no seams
            pm = self._get(noise, s, tx, tz, (10 if i < 4 else 6) + boost, fetch)
            if pm is not None:
                p.drawPixmap(target, pm, QRectF(pm.rect()))
            else:
                self._paint_fallback(p, noise, s, tx, tz, target)
        if not animating and fetch:                                # prefetch ring
            ring, _ = self._visible_tiles(s, ring=1)
            seen = set(tiles)
            for (tx, tz) in ring:
                if (tx, tz) not in seen:
                    self._get(noise, s, tx, tz, max(0, boost))

    def _paint_fallback(self, p, noise, s, tx, tz, target):
        """Stretch a coarser cached tile (or finer ones) over a tile still generating."""
        if not noise:                     # quick coarse version of this very tile
            pm = self.engine.preview(s, tx, tz)
            if pm is not None:
                p.drawPixmap(target, pm, QRectF(pm.rect()))
                return
        for s2 in SCALES:
            if s2 <= s:
                continue
            ratio = s2 // s
            ptx, ptz = tx // ratio, tz // ratio
            pm = self._get(noise, s2, ptx, ptz, 0, fetch=False)
            if pm is None:
                continue
            sub = pm.width() / ratio
            src = QRectF((tx - ptx * ratio) * sub, (tz - ptz * ratio) * sub, sub, sub)
            p.drawPixmap(target, pm, src)
            return
        # finer tiles (after a zoom out) — draw the four children if cached
        idx = SCALES.index(s)
        if idx > 0:
            s1 = SCALES[idx - 1]
            r = s // s1
            for dz in range(r):
                for dx in range(r):
                    pm = self._get(noise, s1, tx * r + dx, tz * r + dz, 0, fetch=False)
                    if pm is not None:
                        w = target.width() / r
                        p.drawPixmap(QRectF(target.x() + dx * w, target.y() + dz * w, w + 0.6, w + 0.6),
                                     pm, QRectF(pm.rect()))

    # ── coordinate grid (like cubiomes-viewer), 01/10 ─────────────────────────
    GRID_STEPS = (16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768,
                  65536, 131072, 262144, 524288, 1048576)

    def grid_step(self):
        """Spacing in blocks actually drawn, or None (grid off).
        ov['grid_step'] 0 = Auto (lines ≥ ~64 px apart); a fixed step that would
        be too dense when zoomed out is doubled until lines are ≥ 8 px apart."""
        if not self.visible.get("__grid"):
            return None
        want = int(self.ov.get("grid_step", 0) or 0)
        if want <= 0:
            return next((g for g in self.GRID_STEPS if g / self.bpp >= 64), self.GRID_STEPS[-1])
        g = want
        while g / self.bpp < 8 and g < (1 << 26):
            g *= 2
        return g

    def _paint_grid(self, p: QPainter):
        g = self.grid_step()
        if g is None:
            return
        x0, z0 = self.to_world(0, 0)
        x1, z1 = self.to_world(self.width(), self.height())
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        minor = QPen(QColor(255, 255, 255, 46), 1)
        major = QPen(QColor(255, 255, 255, 92), 1)        # every 8 lines (region at chunk step)
        axis = QPen(QColor(255, 255, 255, 150), 1)
        dark = QPen(QColor(0, 0, 0, 60), 1)               # under each line: readable on snow
        big = g * 8
        lab = g / self.bpp >= 56
        xs, zs = [], []
        k = math.floor(x0 / g)
        while k * g <= x1:
            wx = k * g
            sx = round(self.to_screen(wx, 0)[0])
            pen = axis if wx == 0 else (major if wx % big == 0 else minor)
            p.setPen(dark); p.drawLine(sx + 1, 0, sx + 1, self.height())
            p.setPen(pen); p.drawLine(sx, 0, sx, self.height())
            xs.append((sx, wx)); k += 1
        k = math.floor(z0 / g)
        while k * g <= z1:
            wz = k * g
            sy = round(self.to_screen(0, wz)[1])
            pen = axis if wz == 0 else (major if wz % big == 0 else minor)
            p.setPen(dark); p.drawLine(0, sy + 1, self.width(), sy + 1)
            p.setPen(pen); p.drawLine(0, sy, self.width(), sy)
            zs.append((sy, wz)); k += 1
        if lab:                                            # coordinates along the edges
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setFont(theme.mono_font(7))
            fm = p.fontMetrics()
            for sx, wx in xs:
                t = f"{wx:,}".replace(",", " ")
                self._halo_text(p, sx + 3, fm.ascent() + 3, t)
            for sy, wz in zs:
                t = f"{wz:,}".replace(",", " ")
                self._halo_text(p, 3, sy - 3, t)
        p.restore()

    @staticmethod
    def _halo_text(p, x, y, t):
        p.setPen(QColor(0, 0, 0, 170))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(QPointF(x + dx, y + dy), t)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(QPointF(x, y), t)

    def _paint_origin(self, p: QPainter):
        if self.visible.get("__origin"):
            ox, oy = self.to_screen(0, 0)
            if -20 < ox < self.width() + 20 and -20 < oy < self.height() + 20:
                # an × (the cursor is a +): dark halo, then white
                p.save()
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                for pen in (QPen(QColor(0, 0, 0, 150), 3.2), QPen(QColor(255, 255, 255, 215), 1.5)):
                    p.setPen(pen)
                    p.drawLine(QPointF(ox - 6, oy - 6), QPointF(ox + 6, oy + 6))
                    p.drawLine(QPointF(ox - 6, oy + 6), QPointF(ox + 6, oy - 6))
                p.restore()

    def _icon(self, key):
        k = (key, self.icon_px)
        pm = self._icon_cache.get(k)
        if pm is None:
            pm = struct_icons.pixmap(key, self.icon_px, max(2.0, self.devicePixelRatioF()))
            self._icon_cache[k] = pm
        return pm

    def _draw_icon(self, p, key, x, z):
        pm = self._icon(key)
        if pm is None:
            return
        sx, sy = self.to_screen(x, z)
        h = self.icon_px / 2
        if key == "spawn":                       # a pin points at the spot with its tip
            sy -= h * 0.85
        if -h <= sx <= self.width() + h and -h <= sy <= self.height() + h:
            p.drawPixmap(int(sx - h), int(sy - h), pm)

    def _draw_footprint(self, p: QPainter, key, x, z):
        """Real size of a structure: solid = exact, dashed = approximate."""
        from .struct_boxes import footprint
        fp = footprint(key, x, z, self.world.seed)
        if fp is None:
            return
        bx0, bz0, bx1, bz1, exact = fp
        sx0, sy0 = self.to_screen(bx0, bz0)
        sx1, sy1 = self.to_screen(bx1 + 1, bz1 + 1)
        if sx1 < 0 or sy1 < 0 or sx0 > self.width() or sy0 > self.height():
            return
        col = QColor(255, 110, 60, 230 if exact else 170)
        pen = QPen(col, 1.6 if exact else 1.2)
        if not exact:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(pen)
        fill = QColor(col); fill.setAlpha(28 if exact else 14)
        p.setBrush(fill)
        p.drawRect(QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0))
        p.restore()

    def _draw_pieces(self, p: QPainter, pieces):
        """Every piece of a stronghold (like cubiomes-viewer), coloured by its
        height; the portal room stands out (green, thick outline)."""
        from .struct_boxes import grad_rgb
        import stronghold_eyes as se
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        ylo = min(q[4] for q in pieces); yhi = max(q[4] for q in pieces)
        span = max(1, yhi - ylo)

        def height_rgb(y):
            return grad_rgb((y - ylo) / span)
        # legend: the stronghold nearest to the centre
        d = min(abs(q[0] - self.cx) + abs(q[2] - self.cz) for q in pieces)
        if d < getattr(self, "_legend_d", 1e18):
            self._legend_d = d
            self._legend_range = (ylo, yhi)
            pr = next((q for q in pieces if q[6] == se.PORTAL_ROOM), None)
            self._legend_portal = (pr[4], pr[1] + 3) if pr else None
        top_piece = max(pieces, key=lambda q: q[4])
        portal = None
        for pc in sorted(pieces, key=lambda q: q[4]):          # higher pieces on top
            x0, y0, z0, x1, y1, z1, t = pc
            if t == se.PORTAL_ROOM:
                portal = pc
                continue
            sx0, sy0 = self.to_screen(x0, z0)
            sx1, sy1 = self.to_screen(x1 + 1, z1 + 1)
            r, g, b = height_rgb(y1)
            p.setPen(QPen(QColor(r, g, b, 235), 1))
            p.setBrush(QColor(r, g, b, 60))
            p.drawRect(QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0))
        if portal is not None:
            x0, y0, z0, x1, y1, z1, t = portal
            sx0, sy0 = self.to_screen(x0, z0)
            sx1, sy1 = self.to_screen(x1 + 1, z1 + 1)
            rect = QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0)
            p.setPen(QPen(QColor(255, 255, 255, 240), 3))
            p.setBrush(QColor(63, 214, 160, 150))
            p.drawRect(rect)
            r, g, b = height_rgb(y1)
            p.setPen(QPen(QColor(r, g, b), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect.adjusted(3, 3, -3, -3))
        # labels: the portal room top (under the room) and the highest point of the
        # stronghold (on its highest piece) — merged when they are the same piece
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tx0, _, tz0, tx1, ty1, tz1, _t = top_piece
        if portal is not None:
            x0, y0, z0, x1, y1, z1, t = portal
            same = top_piece is portal or y1 == ty1 and (tx0, tz0) == (x0, z0)
            txt = f"Portal Y {y1}" + (" = max" if same else "")
            self._chips.append((self._srect(x0, z0, x1, z1), txt, QColor(63, 214, 160)))
        if portal is None or not (top_piece is portal):
            r, g, b = height_rgb(ty1)
            self._chips.append((self._srect(tx0, tz0, tx1, tz1), f"max Y {ty1}", QColor(r, g, b)))
        p.restore()

    def _srect(self, x0, z0, x1, z1):
        sx0, sy0 = self.to_screen(x0, z0)
        sx1, sy1 = self.to_screen(x1 + 1, z1 + 1)
        return QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0)

    def _chip(self, p: QPainter, room: QRectF, text, col):
        """A small rounded label inside the room (just above it while the room is
        still too small on screen to hold it)."""
        f = QFont(theme.ui_font(7)); f.setBold(True); p.setFont(f)
        w = p.fontMetrics().horizontalAdvance(text) + 10
        cx, cy = room.center().x(), room.center().y()
        if w > room.width() + 6 or room.height() < 18:
            cy = room.top() - 10
        r = QRectF(cx - w / 2, cy - 8, w, 16)
        if r.right() < 0 or r.left() > self.width() or r.bottom() < 0 or r.top() > self.height():
            return
        bg = QColor(16, 20, 28, 225)
        p.setPen(QPen(col, 1.4)); p.setBrush(bg)
        p.drawRoundedRect(r, 8, 8)
        p.setPen(col.lighter(125))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_height_legend(self, p: QPainter):
        """Height colours of the stronghold pieces (bottom left): from the lowest
        to the highest piece top of the stronghold nearest the centre."""
        from .struct_boxes import grad_rgb
        from PySide6.QtGui import QLinearGradient
        lo, hi = getattr(self, "_legend_range", (0, 1))
        portal = getattr(self, "_legend_portal", None)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = 230.0, 10.0
        x, y = 14.0, self.height() - 80.0
        bg = QColor(theme.BG1 or "#1b1f27"); bg.setAlpha(225)
        p.setPen(QPen(QColor(theme.LINE2), 1)); p.setBrush(bg)
        p.drawRoundedRect(QRectF(x - 8, y - 20, w + 16, 82), 8, 8)
        g = QLinearGradient(x, 0, x + w, 0)
        for k in range(11):
            g.setColorAt(k / 10, QColor(*grad_rgb(k / 10)))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(g)
        p.drawRoundedRect(QRectF(x, y, w, h), 3, 3)
        f = QFont(theme.ui_font(7.5)); p.setFont(f)
        p.setPen(QColor(theme.TEXT2 or theme.TEXT))
        p.drawText(QPointF(x, y - 6), "Stronghold pieces: top Y, lowest → highest")
        p.drawText(QRectF(x, y + h + 1, 60, 12), Qt.AlignmentFlag.AlignLeft, f"Y {lo}")
        p.drawText(QRectF(x + w - 60, y + h + 1, 60, 12), Qt.AlignmentFlag.AlignRight, f"Y {hi}")
        # the two heights that matter (stronghold nearest the centre)
        fb = QFont(theme.ui_font(8.5)); fb.setBold(True); p.setFont(fb)
        p.setPen(QColor(*grad_rgb(1.0)))
        p.drawText(QPointF(x, y + h + 30), f"▲ Stronghold max Y {hi}")
        if portal is not None:
            p.setPen(QColor(63, 214, 160))
            p.drawText(QPointF(x, y + h + 46),
                       f"▣ Portal room top Y {portal[0]}   ·   frames Y {portal[1]}")
        p.restore()

    def _paint_seed_view_tag(self, p: QPainter):
        """'Structure seed' chip (top left) while biomes are ignored."""
        from PySide6.QtGui import QFont
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f = QFont(theme.ui_font(9)); f.setBold(True); p.setFont(f)
        txt = "Structure seed · potential positions, biomes ignored   Alt+S"
        w = p.fontMetrics().horizontalAdvance(txt) + 22
        r = QRectF((self.width() - w) / 2, 10, w, 26)      # top centre (buttons on the left)
        bg = QColor(theme.BG1 or "#1b1f27"); bg.setAlpha(235)
        p.setPen(QPen(QColor(theme.ACCENT), 1.2)); p.setBrush(bg)
        p.drawRoundedRect(r, 13, 13)
        p.setPen(QColor(theme.TEXT))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        p.restore()

    def _paint_structures(self, p: QPainter):
        x0, z0 = self.to_world(0, 0)
        x1, z1 = self.to_world(self.width(), self.height())
        counts = self._struct_counts = {}
        self._chips = []                      # stronghold labels, drawn over the icons
        self._pieces_shown = False
        self._legend_d = 1e18
        if self.bpp <= STRUCT_MAX_BPP:
            B = self.engine.STRUCT_BLOCK
            fetch = not self._anim.isActive()
            # abandoned villages follow the Village switch (no switch of their
            # own, 01/10); they keep their own icon
            show_z = bool(self.visible.get("village"))
            for key, on in self.visible.items():
                if not on or key.startswith("__") or key not in cw.STRUCTURE_TYPES:
                    continue
                if not self.world.structure_available(key):
                    continue
                for bz in range(math.floor(z0 / B), math.floor(z1 / B) + 1):
                    for bx in range(math.floor(x0 / B), math.floor(x1 / B) + 1):
                        pts = self.engine.structures(key, bx, bz, fetch) or ()
                        zset = self.engine.abandoned(bx, bz) if key == "village" and pts else ()
                        bset = self.engine.with_basement(bx, bz) if key == "igloo" and pts else ()
                        for (x, z) in pts:
                            k = key
                            if zset and (x, z) in zset:
                                k = "zombie_village"
                                if not show_z:
                                    continue
                            elif key == "village" and not self.visible.get("village"):
                                continue
                            elif bset and (x, z) in bset:
                                k = "igloo_basement"
                            if x0 <= x <= x1 and z0 <= z <= z1:
                                counts[k] = counts.get(k, 0) + 1
                            if self.bpp <= BOX_MAX_BPP:
                                self._draw_footprint(p, key, x, z)
                            self._draw_icon(p, k, x, z)
        lm = self.engine.landmarks
        if self.engine.seed_view:         # spawn / strongholds depend on biomes
            return
        if self.visible.get("__strongholds"):
            info = lm.get("sh_info", {})
            show_eyes = self.bpp <= 64
            pieces_on = self.bpp <= PIECES_MAX_BPP
            for (x, z) in lm.get("strongholds", ()):
                if x0 <= x <= x1 and z0 <= z <= z1:
                    counts["stronghold"] = counts.get("stronghold", 0) + 1
                if pieces_on and x0 - 200 <= x <= x1 + 200 and z0 - 200 <= z <= z1 + 200:
                    pcs = self.engine.stronghold_pieces(x, z, not self._anim.isActive())
                    if pcs:
                        self._draw_pieces(p, pcs)
                        self._pieces_shown = True
                self._draw_icon(p, "stronghold", x, z)
                ei = info.get((x, z))
                if show_eyes and ei is not None:
                    self._draw_eye_badge(p, x, z, int(ei[0]))
        if self._chips:
            p.save()
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            for (rect, txt, col) in self._chips:
                self._chip(p, rect, txt, col)
            p.restore()
        if self.visible.get("__spawn") and lm.get("spawn"):
            self._draw_icon(p, "spawn", *lm["spawn"])

    # ── session overlays ────────────────────────────────────────────────────
    def set_zones(self, zones):
        self.zones = list(zones or [])
        self.update()

    def set_markers(self, markers):
        self.markers = list(markers or [])
        self.update()

    def start_pick(self, kind="circle", origin=None):
        """Let the user draw an area on the map. kind: 'circle' (drag from the
        centre), 'rect' (drag a corner to the other) or 'point' (click).
        `origin`: for a circle, force the centre (e.g. the parent's position)
        — the drag then only sets the radius. Emits picked(dict) / pickCancelled."""
        self._pick = dict(kind=kind, origin=origin, start=None, cur=None)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()
        self.update()

    def cancel_pick(self):
        if self._pick is not None:
            self._pick = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
            self.pickCancelled.emit()

    def _pick_result(self):
        pk = self._pick
        (sx, sz), (cx, cz) = pk["start"], pk["cur"]
        if pk["kind"] == "point":
            return dict(kind="point", x=int(round(sx)), z=int(round(sz)))
        if pk["kind"] == "circle":
            ox, oz = pk["origin"] if pk["origin"] is not None else (sx, sz)
            r = int(round(math.hypot(cx - ox, cz - oz)))
            return dict(kind="circle", x=int(round(ox)), z=int(round(oz)), r=max(1, r))
        x1, x2 = sorted((int(round(sx)), int(round(cx))))
        z1, z2 = sorted((int(round(sz)), int(round(cz))))
        return dict(kind="rect", x1=x1, z1=z1, x2=x2, z2=z2)

    def _paint_zones(self, p: QPainter):
        if not (self.zones or self.markers or self._pick):
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f = p.font(); f.setPointSizeF(8.5); f.setBold(True); p.setFont(f)
        hid = self.legend_hidden if self.legend else ()
        items = [zn for zn in self.zones if zn.get("num") not in hid] if hid else list(self.zones)
        if self._pick and self._pick.get("start") is not None and self._pick.get("cur") is not None:
            r = self._pick_result()
            col = theme.ACCENT
            if r["kind"] == "circle":
                items.append(dict(shape="circle", cx=r["x"], cz=r["z"], r=r["r"], color=col,
                                  label=f"r = {r['r']}", active=True))
            elif r["kind"] == "rect":
                items.append(dict(shape="rect", x1=r["x1"], z1=r["z1"], x2=r["x2"], z2=r["z2"],
                                  color=col, active=True,
                                  label=f"{r['x2'] - r['x1']} × {r['z2'] - r['z1']}"))
        for zn in items:
            c = QColor(zn.get("color", theme.ACCENT))
            active = zn.get("active", False)
            fill = QColor(c); fill.setAlpha(46 if active else 22)
            pen = QPen(c, 2.0 if active else 1.3)
            if zn.get("dashed"):
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen); p.setBrush(fill)
            if zn["shape"] == "circle":
                x, y = self.to_screen(zn["cx"], zn["cz"])
                rr = max(2.0, zn["r"] / self.bpp)
                p.drawEllipse(QPointF(x, y), rr, rr)
                tl = QPointF(x - rr * 0.707, y - rr * 0.707)
            else:
                x1, y1 = self.to_screen(zn["x1"], zn["z1"])
                x2, y2 = self.to_screen(zn["x2"] + 1, zn["z2"] + 1)
                rect = QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized()
                if rect.width() < 2 and rect.height() < 2:
                    rect = QRectF(rect.center().x() - 3, rect.center().y() - 3, 6, 6)
                p.drawRect(rect)
                tl = rect.topLeft()
            lab = zn.get("label")
            if lab:
                fm = p.fontMetrics()
                w = fm.horizontalAdvance(lab) + 10
                box = QRectF(tl.x(), tl.y() - 18, w, 16)
                bg = QColor(theme.BG1); bg.setAlpha(225)
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(bg)
                p.drawRoundedRect(box, 4, 4)
                p.setPen(c)
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, lab)
        if self.hit_style == "simple":
            self._paint_hit_badges(p)
        else:
            self._paint_hit_markers(p)
        if self.legend:
            self._paint_legend(p)
        if self._pick and self._pick.get("start") is None:
            hint = {"circle": "Drag to draw the radius" + ("" if self._pick["origin"] is None
                                                          else " (centred on the parent)"),
                    "rect": "Drag from one corner to the other",
                    "point": "Click the position"}[self._pick["kind"]] + "  ·  Esc to cancel"
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(hint) + 20
            box = QRectF((self.width() - w) / 2, 12, w, 26)
            bg = QColor(theme.BG1); bg.setAlpha(235)
            p.setPen(QPen(QColor(theme.ACCENT), 1)); p.setBrush(bg)
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QColor(theme.TEXT))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, hint)
        p.restore()

    # ── result overlay: numbered hits ───────────────────────────────────────
    def _num_pill(self, p, cx, cy, num, color, h=16):
        """'#n' pill centred on (cx, cy), in the condition's colour."""
        txt = f"#{num}"
        w = max(h, p.fontMetrics().horizontalAdvance(txt) + 9)
        r = QRectF(cx - w / 2, cy - h / 2, w, h)
        c = QColor(color)
        p.setPen(QPen(QColor(0, 0, 0, 190), 1.2)); p.setBrush(c)
        p.drawRoundedRect(r, h / 2, h / 2)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        p.setPen(QColor("#10131a") if lum > 150 else QColor("#ffffff"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        return r

    def _hit_groups(self):
        """Markers on screen, grouped when they (nearly) coincide."""
        groups = []
        hid = self.legend_hidden if self.legend else ()
        for m in self.markers:
            if hid and m.get("num") in hid:
                continue
            x, y = self.to_screen(m["x"], m["z"])
            if not (-30 <= x <= self.width() + 30 and -30 <= y <= self.height() + 30):
                continue
            for g in groups:
                if abs(g["x"] - x) <= 10 and abs(g["y"] - y) <= 10:
                    g["items"].append(m)
                    break
            else:
                groups.append(dict(x=x, y=y, items=[m]))
        return groups

    def _paint_hit_badges(self, p):
        """Simple mode: a dot at the exact spot + small '#n' pills above it."""
        f = p.font(); f.setPointSizeF(7.2); f.setBold(True); p.setFont(f)
        for g in self._hit_groups():
            x, y = g["x"], g["y"]
            p.setPen(QPen(QColor(0, 0, 0, 200), 1)); p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(x, y), 2.6, 2.6)
            items = sorted(g["items"], key=lambda m: m.get("num", 0))
            ws = [max(16, p.fontMetrics().horizontalAdvance(f"#{m.get('num', '?')}") + 9) for m in items]
            tot = sum(ws) + 2 * (len(ws) - 1)
            cx = x - tot / 2
            for m, w in zip(items, ws):
                self._num_pill(p, cx + w / 2, y - 13, m.get("num", "?"), m.get("color", theme.ACCENT))
                cx += w + 2

    def _paint_hit_markers(self, p):
        """Detailed mode: crosshair at the exact spot, a '#n' pill, and a label
        '#n name / X … Z …' placed where it overlaps nothing (leader line)."""
        groups = self._hit_groups()
        f = p.font(); f.setPointSizeF(7.2); f.setBold(True); p.setFont(f)
        for g in groups:                        # crosshairs first (under the labels)
            x, y = g["x"], g["y"]
            c = QColor(g["items"][0].get("color", theme.ACCENT))
            p.setPen(QPen(QColor(0, 0, 0, 160), 3)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(x, y), 6, 6)
            p.setPen(QPen(c, 1.8)); p.drawEllipse(QPointF(x, y), 6, 6)
            for a, b in (((-10, 0), (-4, 0)), ((4, 0), (10, 0)), ((0, -10), (0, -4)), ((0, 4), (0, 10))):
                p.drawLine(QPointF(x + a[0], y + a[1]), QPointF(x + b[0], y + b[1]))
        if not any(m.get("label") or m.get("num") for g in groups for m in g["items"]):
            return
        fb = QFont(self.font()); fb.setPointSizeF(8); fb.setBold(True)
        fd = theme.mono_font(7.5)
        placed = [QRectF(g["x"] - 11, g["y"] - 11, 22, 22) for g in groups]
        for g in sorted(groups, key=lambda g: (g["y"], g["x"])):
            x, y = g["x"], g["y"]
            items = sorted(g["items"], key=lambda m: m.get("num", 0))
            lines = []
            for m in items:
                lines.append((m, m.get("label") or "", m.get("detail") or ""))
            p.setFont(fb); fmb = p.fontMetrics()
            p.setFont(fd); fmd = p.fontMetrics()
            pill_w = 26
            w = 12 + max(pill_w + 4 + max(fmb.horizontalAdvance(t) for _m, t, _d in lines),
                         fmd.horizontalAdvance(lines[0][2]) + pill_w + 4)
            row_h = 16
            h = 8 + row_h * len(lines) + (13 if lines[0][2] else 0)
            box = None
            for k in range(40):                 # right / left of the spot, then further down
                dy = (k // 4) * (h * 0.6 + 4)
                for dx_sign, up in ((1, 1), (1, 0), (-1, 1), (-1, 0)):
                    bx = x + 16 if dx_sign > 0 else x - 16 - w
                    by = (y - h - 6 if up else y + 6) + (dy if not up else -dy)
                    cand = QRectF(bx, by, w, h)
                    if all(not cand.intersects(r) for r in placed) and \
                            cand.left() > -w / 2 and cand.right() < self.width() + w / 2:
                        box = cand
                        break
                if box is not None:
                    break
            if box is None:
                box = QRectF(x + 16, y + 6, w, h)
            placed.append(box.adjusted(-3, -3, 3, 3))
            # leader line from the spot to the box
            anchor = QPointF(box.left() if box.left() > x else box.right(),
                             min(max(y, box.top() + 8), box.bottom() - 8))
            p.setPen(QPen(QColor(0, 0, 0, 140), 3)); p.drawLine(QPointF(x, y), anchor)
            p.setPen(QPen(QColor(theme.TEXT2), 1.2)); p.drawLine(QPointF(x, y), anchor)
            bg = QColor(theme.BG1); bg.setAlpha(232)
            p.setPen(QPen(QColor(theme.LINE2), 1)); p.setBrush(bg)
            p.drawRoundedRect(box, 5, 5)
            yy = box.top() + 4
            for m, t, _d in lines:
                p.setFont(fb)
                self._num_pill(p, box.left() + 6 + pill_w / 2, yy + row_h / 2, m.get("num", "?"),
                               m.get("color", theme.ACCENT), h=14)
                p.setFont(fb); p.setPen(QColor(theme.TEXT))
                p.drawText(QRectF(box.left() + 10 + pill_w, yy, w, row_h),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, t)
                yy += row_h
            if lines[0][2]:
                p.setFont(fd); p.setPen(QColor(theme.TEXT2))
                p.drawText(QRectF(box.left() + 10 + pill_w, yy - 1, w, 13),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, lines[0][2])

    def _paint_legend(self, p):
        """Detailed mode: every active condition, its number and where it was
        satisfied (✓ container passed, — not located). Clickable: see
        legend_hidden (hidden rows are dimmed, their zone / marker not drawn)."""
        rows = self.legend
        hid = self.legend_hidden
        fb = QFont(self.font()); fb.setPointSizeF(8.5); fb.setBold(False)
        fp = QFont(self.font()); fp.setPointSizeF(7.2); fp.setBold(True)
        fd = theme.mono_font(7.5)
        p.setFont(fb); fmb = p.fontMetrics()
        p.setFont(fd); fmd = p.fontMetrics()
        row_h = 18
        top = self.legend_top
        avail = max(row_h * 3, self.height() - top - 60)
        nfit = max(1, int((avail - 48) // row_h))
        scroll = max(0, min(self.legend_scroll, max(0, len(rows) - nfit)))
        self.legend_scroll = scroll
        shown = rows[scroll:scroll + nfit]
        above, below = scroll, max(0, len(rows) - scroll - len(shown))
        name_w = min(190, max(fmb.horizontalAdvance(r["name"]) for r in rows))
        where_w = max(fmd.horizontalAdvance(r["where"]) for r in rows)
        title = f"CONDITIONS · {len(rows) - len(hid & {r['num'] for r in rows})}/{len(rows)} shown"
        p.setFont(fp)
        w = max(10 + 30 + 6 + name_w + 12 + where_w + 10, p.fontMetrics().horizontalAdvance(title) + 60)
        extra = (1 if above else 0) + (1 if below else 0)
        h = 26 + row_h * (len(shown) + extra) + 4
        box = QRectF(self.width() - w - 12, top, w, h)
        self._legend_box = box
        self._legend_rows = []
        bg = QColor(theme.BG1); bg.setAlpha(235)
        p.setPen(QPen(QColor(theme.LINE2), 1)); p.setBrush(bg)
        p.drawRoundedRect(box, 8, 8)
        self._legend_head = QRectF(box.left(), box.top(), w, 22)
        p.setFont(fp); p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(box.left() + 10, box.top() + 5, w - 20, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        p.setPen(QColor(theme.ACCENT))
        p.drawText(QRectF(box.left() + 10, box.top() + 5, w - 20, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "all" if hid else "none")
        y = box.top() + 24
        if above:
            p.setFont(fb); p.setPen(QColor(theme.TEXT3))
            p.drawText(QRectF(box.left() + 46, y, w - 56, row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"▲ {above} more (wheel)")
            y += row_h
        for r in shown:
            off = r["num"] in hid
            rr = QRectF(box.left() + 4, y, w - 8, row_h)
            self._legend_rows.append((rr, r["num"]))
            p.setFont(fp)
            p.setOpacity(0.35 if off else 1.0)
            self._num_pill(p, box.left() + 10 + 15, y + row_h / 2, r["num"],
                           theme.TEXT3 if off else r["color"], h=14)
            p.setFont(fb)
            p.setPen(QColor(theme.TEXT if (r["where"] != "—" and not off) else theme.TEXT3))
            name = fmb.elidedText(r["name"], Qt.TextElideMode.ElideRight, int(name_w))
            p.drawText(QRectF(box.left() + 46, y, name_w, row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            p.setFont(fd); p.setPen(QColor(theme.TEXT2 if (r["where"] != "—" and not off) else theme.TEXT3))
            p.drawText(QRectF(box.right() - 10 - where_w, y, where_w, row_h),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, r["where"])
            p.setOpacity(1.0)
            y += row_h
        if below:
            p.setFont(fb); p.setPen(QColor(theme.TEXT3))
            p.drawText(QRectF(box.left() + 46, y, w - 56, row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"▼ {below} more (wheel)")

    # ── legend interaction ──────────────────────────────────────────────────
    def _legend_hit(self, pos):
        """'row' + num, 'head', 'box' (inside the legend) or None."""
        if not self.legend or self._legend_box is None or not self._legend_box.contains(pos):
            return None, None
        for rr, num in self._legend_rows:
            if rr.contains(pos):
                return "row", num
        if self._legend_head is not None and self._legend_head.contains(pos):
            return "head", None
        return "box", None

    def _legend_set(self, num, show):
        if show:
            self.legend_hidden.discard(num)
        else:
            self.legend_hidden.add(num)

    def _legend_press(self, e):
        """True if the press was for the legend (then the map does not pan)."""
        kind, num = self._legend_hit(e.position())
        if kind is None:
            return False
        nums = [r["num"] for r in self.legend]
        if e.button() == Qt.MouseButton.RightButton:
            if kind == "row":                   # only this one (again: all)
                others = set(nums) - {num}
                if self.legend_hidden == others:
                    self.legend_hidden = set()
                else:
                    self.legend_hidden = others
        elif e.button() == Qt.MouseButton.LeftButton:
            if kind == "head":
                self.legend_hidden = set() if self.legend_hidden else set(nums)
            elif kind == "row":
                show = num in self.legend_hidden
                self._legend_set(num, show)
                self._legend_drag = (show, {num})
                self._legend_drag_last = num
        self.update()
        return True

    def legend_reset(self):
        self.legend_hidden = set()
        self.legend_scroll = 0
        self.update()

    def _draw_eye_badge(self, p: QPainter, x, z, eyes):
        """'n/12' pill under a stronghold, coloured like the classic UI."""
        from .stronghold_dialogs import eye_color
        sx, sy = self.to_screen(x, z)
        if not (-40 <= sx <= self.width() + 40 and -40 <= sy <= self.height() + 40):
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f = p.font(); f.setPointSizeF(7.5); f.setBold(True); p.setFont(f)
        txt = f"{eyes}/12"
        w = p.fontMetrics().horizontalAdvance(txt) + 10
        r = QRectF(sx - w / 2, sy + self.icon_px / 2 + 1, w, 14)
        p.setPen(QPen(QColor(0, 0, 0, 170), 1)); p.setBrush(QColor(eye_color(eyes)))
        p.drawRoundedRect(r, 7, 7)
        p.setPen(QColor("#10131a") if eyes >= 5 else QColor("#ffffff"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        p.restore()

    def stronghold_near(self, sx, sy, radius_px=14):
        best, bd = None, radius_px * radius_px
        for (x, z) in self.engine.landmarks.get("strongholds", ()):
            ox, oy = self.to_screen(x, z)
            d = (ox - sx) ** 2 + (oy - sy) ** 2
            if d <= bd:
                best, bd = (x, z), d
        return best

    def show_stronghold(self, x, z):
        from .stronghold_dialogs import StrongholdDetailsDialog
        StrongholdDetailsDialog(self.world.seed, self.world.mc, x, z, self).exec()

    def show_all_strongholds(self):
        from .stronghold_dialogs import StrongholdListDialog
        dlg = StrongholdListDialog(self.world.seed, self.world.mc,
                                   self.engine.landmarks.get("strongholds"), self)
        dlg.goto.connect(lambda x, z: self.center_on(x, z, min(self.bpp, 8)))
        dlg.show()

    def _paint_scale_bar(self, p: QPainter):
        raw = 120 * self.bpp
        mag = 10 ** math.floor(math.log10(max(raw, 1e-9)))
        blocks = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw * 0.6)
        w = blocks / self.bpp
        x = self.width() - self.right_inset - w - 18
        y = self.height() - 20
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        bg = QColor(theme.BG1); bg.setAlpha(225)
        p.setBrush(bg)
        p.setFont(theme.ui_font(8))
        label = f"{blocks:,.0f} blocks".replace(",", " ") if blocks >= 1 else f"{blocks:g} block"
        g = self.grid_step()
        if g is not None:                                  # the grid spacing in use
            label += "  ·  grid " + {16: "chunk", 512: "region"}.get(g, f"{g:,}".replace(",", " "))
        tw = p.fontMetrics().horizontalAdvance(label) + 16
        bw = max(w + 20, tw)                               # the box fits the text too
        bx = min(x - 10, self.width() - self.right_inset - 8 - bw)
        p.drawRoundedRect(QRectF(bx, y - 22, bw, 32), 6, 6)
        x = bx + (bw - w) / 2
        p.setPen(QPen(QColor(theme.TEXT), 2))
        p.drawLine(QPointF(x, y), QPointF(x + w, y))
        p.drawLine(QPointF(x, y - 4), QPointF(x, y + 2))
        p.drawLine(QPointF(x + w, y - 4), QPointF(x + w, y + 2))
        p.drawText(QRectF(bx, y - 20, bw, 14), Qt.AlignmentFlag.AlignCenter, label)

    # ── structure legend (what the icons on screen are) ─────────────────────
    def legend_on(self):
        return self.STRUCT_LEGEND and bool(self._style.get("legend", True))

    def set_legend(self, on):
        self._style["legend"] = bool(on)
        if self.link is not None:
            self.link.repaint_all()
        else:
            self.update()

    def _legend_rows_struct(self):
        rows = []
        lm = self.engine.landmarks
        if self.visible.get("__spawn") and lm.get("spawn"):
            rows.append(("spawn", "Spawn", None))
        if self.visible.get("__strongholds") and lm.get("strongholds"):
            rows.append(("stronghold", "Stronghold", self._struct_counts.get("stronghold", 0)))
        zoomed_out = self.bpp > STRUCT_MAX_BPP
        for key, on in self.visible.items():
            if not on or key.startswith("__") or key not in cw.STRUCTURE_TYPES:
                continue
            if not self.world.structure_available(key):
                continue
            rows.append((key, struct_icons.label(key),
                         None if zoomed_out else self._struct_counts.get(key, 0)))
            if key == "village":
                rows.append(("zombie_village", struct_icons.label("zombie_village"),
                             None if zoomed_out else self._struct_counts.get("zombie_village", 0)))
            if key == "igloo":
                rows.append(("igloo_basement", struct_icons.label("igloo_basement"),
                             None if zoomed_out else self._struct_counts.get("igloo_basement", 0)))
        return rows, zoomed_out

    def _paint_struct_legend(self, p: QPainter):
        if not self.legend_on() or self._pick is not None:
            return
        rows, zoomed_out = self._legend_rows_struct()
        if not rows:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fb = theme.ui_font(8.3); fh = theme.ui_font(7.2, QFont.Weight.Bold); fn = theme.mono_font(7.8)
        p.setFont(fb); fm = p.fontMetrics()
        row_h, ic = 19, 16
        name_w = max(fm.horizontalAdvance(r[1]) for r in rows)
        p.setFont(fn); cnt_w = max([p.fontMetrics().horizontalAdvance(str(r[2])) for r in rows
                                    if r[2] is not None] + [0])
        w = 10 + ic + 7 + name_w + (12 + cnt_w if cnt_w else 0) + 10
        foot = "zoom in for structures" if zoomed_out else ""
        p.setFont(fh); w = max(w, p.fontMetrics().horizontalAdvance("ON SCREEN") + 20)
        if foot:
            p.setFont(fb); w = max(w, fm.horizontalAdvance(foot) + 20)
        top = self._controls.geometry().bottom() + 12
        avail = self.height() - top - 70
        nfit = max(0, int((avail - 24 - (row_h if foot else 0)) // row_h))
        if nfit <= 0:
            p.restore(); return
        shown = rows[:nfit]
        h = 22 + row_h * len(shown) + (row_h if foot else 0) + 6
        box = QRectF(12, top, w, h)
        bg = QColor(theme.BG1); bg.setAlpha(228)
        p.setPen(QPen(QColor(theme.LINE2), 1)); p.setBrush(bg)
        p.drawRoundedRect(box, 8, 8)
        p.setFont(fh); p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(box.left() + 10, box.top() + 5, w - 20, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "ON SCREEN")
        y = box.top() + 22
        for key, label, n in shown:
            pm = struct_icons.pixmap(key, ic, max(2.0, self.devicePixelRatioF()))
            dim = (n == 0) or (n is None and key not in ("spawn",) and zoomed_out)
            p.setOpacity(0.45 if dim else 1.0)
            if pm is not None:
                p.drawPixmap(int(box.left() + 10), int(y + (row_h - ic) / 2), pm)
            p.setFont(fb); p.setPen(QColor(theme.TEXT if not dim else theme.TEXT3))
            p.drawText(QRectF(box.left() + 10 + ic + 7, y, name_w + 2, row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            if n is not None and cnt_w:
                p.setFont(fn); p.setPen(QColor(theme.TEXT2))
                p.drawText(QRectF(box.right() - 10 - cnt_w, y, cnt_w, row_h),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(n))
            p.setOpacity(1.0)
            y += row_h
        if foot:
            p.setFont(fb); p.setPen(QColor(theme.TEXT3))
            p.drawText(QRectF(box.left() + 10, y, w - 20, row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, foot)
        p.restore()

    # ── pin (Paste seed + coordinates) ──────────────────────────────────────
    def set_pin(self, x, z, label=""):
        self.pin = (float(x), float(z), label, self.world.seed)
        self.update()

    def clear_pin(self):
        if self.pin is not None:
            self.pin = None
            self.update()

    def _paint_pin(self, p: QPainter):
        if self.pin is None:
            return
        x, z, label, seed = self.pin
        if seed != self.world.seed:              # a pin belongs to the seed it came with
            return
        sx, sy = self.to_screen(x + 0.5, z + 0.5)
        if not (-40 < sx < self.width() + 40 and -60 < sy < self.height() + 40):
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath(); path.moveTo(sx, sy)
        path.cubicTo(sx - 6, sy - 9, sx - 9, sy - 13, sx - 9, sy - 18)
        path.cubicTo(sx - 9, sy - 23, sx - 5, sy - 27, sx, sy - 27)
        path.cubicTo(sx + 5, sy - 27, sx + 9, sy - 23, sx + 9, sy - 18)
        path.cubicTo(sx + 9, sy - 13, sx + 6, sy - 9, sx, sy)
        p.setPen(QPen(QColor(0, 0, 0, 170), 3)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
        p.setPen(QPen(QColor(255, 255, 255), 1.4)); p.setBrush(QColor(theme.ACCENT)); p.drawPath(path)
        p.setBrush(QColor(255, 255, 255)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(sx, sy - 18), 3, 3)
        text = label or f"{int(x)}, {int(z)}"
        self._label_pill(p, QPointF(sx, sy - 36), text, QColor(theme.TEXT), QColor(theme.ACCENT))
        p.restore()

    def _label_pill(self, p, c, text, fg, line, font=None):
        p.setFont(font or theme.mono_font(8))
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 14, fm.height() + 6
        r = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
        # stay inside the map (left of the Layers drawer)
        right = self.width() - self.right_inset - 6
        r.moveLeft(max(6.0, min(r.left(), right - w)))
        r.moveTop(max(6.0, min(r.top(), self.height() - 6 - h)))
        bg = QColor(theme.BG1); bg.setAlpha(235)
        p.setPen(QPen(line, 1)); p.setBrush(bg)
        p.drawRoundedRect(r, h / 2, h / 2)
        p.setPen(fg)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        return r

    # ── measuring tool ──────────────────────────────────────────────────────
    MEASURE_COLOR = "#ffd24a"

    def toggle_measure(self, on=None):
        on = (not self.measuring) if on is None or not isinstance(on, bool) else on
        if not self.MEASURE:
            return
        if not on and self._mcur:
            self._measure_finish()
        self.measuring = on
        self._mhover = None
        if self._ruler is not None and self._ruler.isChecked() != on:
            self._ruler.setChecked(on)
        if on:
            self.setFocus()
        self.update()
        self.measureChanged.emit(on)

    def clear_measures(self):
        self.measures = []
        self._mcur = []
        self.update()

    def _measure_point(self, pos, shift):
        x, z = self.to_world(pos.x(), pos.y())
        x, z = math.floor(x) + 0.5, math.floor(z) + 0.5      # block centres
        if shift and self._mcur:
            ax, az = self._mcur[-1]
            dx, dz = x - ax, z - az
            ang = round(math.atan2(dz, dx) / (math.pi / 4)) * (math.pi / 4)
            d = math.hypot(dx, dz)
            if abs(math.cos(ang)) > 0.5 and abs(math.sin(ang)) > 0.5:
                k = round(d / math.sqrt(2))                  # diagonal: whole blocks on both axes
                x, z = ax + k * math.copysign(1, math.cos(ang)), az + k * math.copysign(1, math.sin(ang))
            else:
                x, z = ax + round(d * math.cos(ang)), az + round(d * math.sin(ang))
        return (x, z)

    def _measure_finish(self):
        if len(self._mcur) >= 2:
            self.measures.append(self._mcur)
        self._mcur = []
        self.update()

    @staticmethod
    def _plen(pts):
        return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))

    def _paint_measures(self, p: QPainter):
        live = self._mcur + ([self._mhover] if (self.measuring and self._mcur and self._mhover) else [])
        if not (self.measures or live or self.measuring):
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = QColor(self.MEASURE_COLOR)
        halo = QPen(QColor(0, 0, 0, 160), 4.2); halo.setCapStyle(Qt.PenCapStyle.RoundCap)
        line = QPen(col, 2); line.setStyle(Qt.PenStyle.DashLine); line.setDashPattern([5, 3])
        f = theme.mono_font(8)
        for pts, is_live in [(m, False) for m in self.measures] + ([(live, True)] if len(live) >= 1 else []):
            sp = [QPointF(*self.to_screen(x, z)) for x, z in pts]
            for pen in (halo, line):
                p.setPen(pen)
                for a, b in zip(sp, sp[1:]):
                    p.drawLine(a, b)
            p.setPen(QPen(QColor(255, 255, 255), 1.2)); p.setBrush(col)
            for q in sp[:len(sp) - (1 if is_live and len(sp) > 1 else 0)]:
                p.drawEllipse(q, 3.6, 3.6)
            # per-segment length (only when there is room for it)
            if len(pts) > 2 or not is_live:
                for (a, b), (qa, qb) in zip(zip(pts, pts[1:]), zip(sp, sp[1:])):
                    if math.hypot(qb.x() - qa.x(), qb.y() - qa.y()) < 70 or len(pts) == 2:
                        continue
                    d = math.hypot(b[0] - a[0], b[1] - a[1])
                    self._label_pill(p, (qa + qb) / 2, _fmt_blocks(d), QColor(theme.TEXT2),
                                     QColor(theme.LINE2), theme.mono_font(7.5))
            if len(pts) >= 2:
                tot = self._plen(pts)
                (ax, az), (bx, bz) = pts[0], pts[-1]
                if len(pts) == 2:
                    txt = (f"{_fmt_blocks(tot)}   ΔX {bx - ax:+,.0f}  ΔZ {bz - az:+,.0f}"
                           f"   ·   Nether {_fmt_blocks(tot / 8)}").replace(",", " ")
                else:
                    txt = f"Σ {_fmt_blocks(tot)}   ·   {len(pts) - 1} segments   ·   Nether {_fmt_blocks(tot / 8)}"
                end = sp[-1]
                dy = -18 if end.y() > 40 else 20
                self._label_pill(p, QPointF(end.x(), end.y() + dy), txt, col, col, f)
        if self.measuring:
            hint = ("Measure — click: add a point · double-click / Enter / right-click: finish · "
                    "Shift: straight · Backspace: undo · Esc: leave")
            if not self._mcur:
                hint = "Measure — click on the map to start · Esc or M: leave the tool"
            self._label_pill(p, QPointF((self.width() - self.right_inset) / 2, 22), hint, QColor(theme.TEXT), col, theme.ui_font(8.3))
        p.restore()

    # ── octave arrows ──────────────────────────────────────────────────────
    def _ui_noise(self, noise):
        key = (self.world.key, noise)
        nm = self._ui_libs.get(key)
        if nm is None:
            nm = cw.NoiseMapLib(self.world.seed, nptype=cw.NOISE_PARAMS[noise],
                                large=self.world.large_biomes)
            self._ui_libs[key] = nm
        return nm

    def octave_info(self, noise):
        try:
            return self._ui_noise(noise).octave_info()
        except Exception:
            return None

    def _compute_arrows(self):
        """World-anchored arrow list, recomputed only when the view settles;
        while moving, the cached arrows are simply re-projected (fluid)."""
        noise = self.ov["noise"]
        if not (self.ov["arrows"] and noise in CLIMATE):
            self._arrows = None
            return
        try:
            nm = self._ui_noise(noise)
        except Exception:
            return
        w_world, h_world = self.width() * self.bpp, self.height() * self.bpp
        wx0, wz0 = self.cx - w_world / 2, self.cz - h_world / 2
        dens, note, out = self.ov["arrow_density"], "", []
        if self.ov["arrow_mode"] == "Lattice":
            oc = self.ov["octave"]
            pair, stream = (0, 0) if oc is None else (oc[0], 1 if oc[1] else 0)
            cap = int(max(600, min(9000, round(3600 * dens))))
            res = nm.gen_octave_perlin_vectors(wx0, wz0, wx0 + w_world, wz0 + h_world,
                                               self.ov["arrow_y"], 1, pair, stream, cap=cap)
            if res is None:
                note = "Lattice vectors need a newer libcubiomes (rebuild)."
            else:
                wx, wz, gx, gy, gz = res
                if len(wx) == 0:
                    note = "This octave's lattice is coarser than the view — zoom out."
                for k in range(len(wx)):
                    h = math.hypot(gx[k], gz[k])
                    if h > 1e-9:
                        out.append((wx[k], wz[k], gx[k] / h, gz[k] / h, abs(gy[k]), gy[k], 0.45))
        else:
            import numpy as np
            spacing_px = 46.0 / max(0.25, dens)
            gsc = max(1, int(round(spacing_px * self.bpp)))
            gx0, gz0 = int(math.floor(wx0 / gsc)) - 1, int(math.floor(wz0 / gsc)) - 1
            W = min(110, max(3, int(math.ceil(w_world / gsc)) + 3))
            H = min(90, max(3, int(math.ceil(h_world / gsc)) + 3))
            arr = np.asarray(octave_arrays(nm, gx0, gz0, W, H, gsc, self.ov["octave"]), np.float64)
            gzz, gxx = np.gradient(arr)
            mag = np.hypot(gxx, gzz)
            mmax = float(mag.max()) if mag.size else 0.0
            if mmax > 1e-12:
                for j in range(1, H - 1):
                    for i in range(1, W - 1):
                        mv = mag[j, i]
                        if mv < mmax * 0.045:
                            continue
                        t = min(1.0, mv / mmax)
                        out.append(((gx0 + i) * gsc, (gz0 + j) * gsc, gxx[j, i] / mv, gzz[j, i] / mv,
                                    t, 0.0, 0.32 + 0.68 * t))
        self._arrows = (self.bpp, out, note)
        self.update()

    def _arrow_color(self, dx, dz, t, vy):
        mode = self.ov["arrow_color"]
        if mode in ARROW_UNIFORM:
            return QColor(ARROW_UNIFORM[mode])
        if mode == "Magnitude":
            r, g, b = colorsys.hsv_to_rgb((210.0 - 210.0 * max(0.0, min(1.0, t))) / 360.0, 0.82, 0.97)
        else:
            hue = (math.atan2(dz, dx) / math.pi * 180.0 + 180.0) % 360.0
            v = 0.97 if vy == 0 else (0.75 + 0.25 * max(-1.0, min(1.0, vy)))
            r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 0.74, v)
        return QColor(int(r * 255), int(g * 255), int(b * 255))

    def _paint_arrows(self, p: QPainter):
        if not (self.ov["arrows"] and self.ov["noise"] in CLIMATE):
            return
        if self._arrows is None:
            self._arrow_timer.start()
            return
        _bpp0, arrows, note = self._arrows
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        base = (22.0 if self.ov["arrow_mode"] == "Lattice" else 46.0 / max(0.25, self.ov["arrow_density"]) * 1.15)
        L0 = base * self.ov["arrow_length"]
        W, H = self.width(), self.height()
        for (wx, wz, dx, dz, t, vy, lf) in arrows:
            sx, sy = self.to_screen(wx, wz)
            if sx < -24 or sx > W + 24 or sy < -24 or sy > H + 24:
                continue
            ln = L0 * lf if self.ov["arrow_mode"] != "Lattice" else L0
            ex, ey = sx + dx * ln, sy + dz * ln
            col = self._arrow_color(dx, dz, t, vy)
            pen = QPen(col, 1.8); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(sx, sy), QPointF(ex, ey))
            a = math.atan2(dz, dx)
            for s in (-0.45, 0.45):
                p.drawLine(QPointF(ex, ey), QPointF(ex - 8 * math.cos(a + s), ey - 8 * math.sin(a + s)))
            if self.ov["arrow_mode"] == "Lattice":
                p.setBrush(col); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(sx, sy), 2, 2)
        if note:
            p.setPen(QColor(theme.WARN)); p.setFont(theme.ui_font(9, theme.QFont.Weight.DemiBold))
            p.drawText(QPointF(58, 24), note)

    # ── interaction ─────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if self._pick is not None:
            if e.button() == Qt.MouseButton.LeftButton:
                w = self.to_world(e.position().x(), e.position().y())
                self._pick["start"] = w; self._pick["cur"] = w
                if self._pick["kind"] == "point":
                    r = self._pick_result(); self._pick = None
                    self.setCursor(Qt.CursorShape.CrossCursor); self.update()
                    self.picked.emit(r)
            elif e.button() == Qt.MouseButton.RightButton:
                self.cancel_pick()
            return
        if self._legend_press(e):
            return
        if self.measuring and e.button() == Qt.MouseButton.LeftButton:
            self._mpress = e.position()              # a click adds a point, a drag pans
        if e.button() == Qt.MouseButton.LeftButton:
            self._stop_motion()
            self._drag = (e.position(), self.cx, self.cz)
            self._last_move = (e.position(), time.perf_counter())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._legend_drag is not None:       # sliding over the rows, button down
            kind, num = self._legend_hit(pos)
            show, touched = self._legend_drag
            if kind == "row":
                # every row between the last one and this one (a fast slide
                # skips rows between two mouse events)
                idx = [n for _r, n in self._legend_rows]
                last = self._legend_drag_last if self._legend_drag_last in idx else num
                a, b = sorted((idx.index(last), idx.index(num)))
                for n in idx[a:b + 1]:
                    if n not in touched:
                        touched.add(n)
                        self._legend_set(n, show)
                self._legend_drag_last = num
                self.update()
            return
        if self._drag is None and self.legend:
            kind, _n = self._legend_hit(pos)
            want = Qt.CursorShape.PointingHandCursor if kind in ("row", "head") else None
            if want is not None:
                if self.cursor().shape() != want:
                    self.setCursor(want)
                self._readout.hide()
                return
            if self.cursor().shape() == Qt.CursorShape.PointingHandCursor:
                self.setCursor(Qt.CursorShape.CrossCursor)
        if self._pick is not None and self._pick.get("start") is not None:
            self._pick["cur"] = self.to_world(pos.x(), pos.y())
            self.update()
        if self.measuring:
            self._mhover = self._measure_point(pos, bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            if self._mcur and self._drag is None:
                self.update()
        if self._drag is not None:
            start, cx, cz = self._drag
            self.cx = cx - (pos.x() - start.x()) * self.bpp
            self.cz = cz - (pos.y() - start.y()) * self.bpp
            now = time.perf_counter()
            lp, lt = self._last_move
            dt = max(1e-3, now - lt)
            vx, vz = (pos.x() - lp.x()) / dt, (pos.y() - lp.y()) / dt
            ovx, ovz = self._vel
            self._vel = (0.6 * vx + 0.4 * ovx, 0.6 * vz + 0.4 * ovz)
            self._last_move = (pos, now)
            self._view_changed()
        self._hover_pos = pos
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def mouseReleaseEvent(self, e):
        if self._legend_drag is not None:
            self._legend_drag = None
            return
        if self._pick is not None and self._pick.get("start") is not None \
                and e.button() == Qt.MouseButton.LeftButton:
            self._pick["cur"] = self.to_world(e.position().x(), e.position().y())
            r = self._pick_result()
            self._pick = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
            self.picked.emit(r)
            return
        if self.measuring and e.button() == Qt.MouseButton.LeftButton and self._mpress is not None:
            d = e.position() - self._mpress
            self._mpress = None
            if abs(d.x()) + abs(d.y()) < 5:
                pt = self._measure_point(e.position(),
                                         bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                if not self._mcur or pt != self._mcur[-1]:
                    self._mcur.append(pt)
                self._drag = None
                self._vel = (0.0, 0.0)
                self.setCursor(Qt.CursorShape.CrossCursor)
                self.update()
                return
        if e.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            if time.perf_counter() - self._last_move[1] > 0.08:   # stopped before release
                self._vel = (0.0, 0.0)
            if abs(self._vel[0]) > 40 or abs(self._vel[1]) > 40:
                self._clock.restart(); self._anim.start()
            else:
                self._vel = (0.0, 0.0)

    def mouseDoubleClickEvent(self, e):
        if self._legend_press(e):               # a quick 2nd click on the legend
            return                              # switches again (no zoom)
        if self.measuring:                      # the 1st click placed the end point
            if e.button() == Qt.MouseButton.LeftButton:
                self._measure_finish()
            return
        if self.visible.get("__strongholds"):
            sh = self.stronghold_near(e.position().x(), e.position().y(), 10)
            if sh is not None:
                self.show_stronghold(*sh)
                return
        self.zoom_smooth(1 / 2, e.position())

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.pixelDelta().y()
        if d and self._legend_hit(e.position())[0] is not None:
            step = max(1, abs(d) // 40)
            self.legend_scroll = max(0, self.legend_scroll - step if d > 0 else self.legend_scroll + step)
            self.update()
            return
        if d:
            self.zoom_smooth(0.8 ** (d / 120.0), e.position())

    def leaveEvent(self, _e):
        self._readout.hide()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape and self._pick is not None:
            self.cancel_pick()
            return
        if self.measuring:
            if k == Qt.Key.Key_Escape:
                if self._mcur:
                    self._mcur = []; self.update()
                else:
                    self.toggle_measure(False)
                return
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._measure_finish(); return
            if k == Qt.Key.Key_Backspace:
                if self._mcur:
                    self._mcur.pop()
                elif self.measures:
                    self._mcur = self.measures.pop()[:-1]     # reopen the last line
                self.update(); return
        if k == Qt.Key.Key_M and self.MEASURE and not (e.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            self.toggle_measure(); return
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_smooth(1 / 2)
        elif k == Qt.Key.Key_Minus:
            self.zoom_smooth(2)
        elif k == Qt.Key.Key_Home:
            self.center_on(0, 0)
        elif k == Qt.Key.Key_G and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.goto_dialog()
        elif (k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
              and getattr(self, "arrow_handler", None) is not None):
            # a page can take the arrows (Sessions: ↑ / ↓ = previous / next
            # result, ← / → nothing — moving the map was useless there, 01/10)
            self.arrow_handler(k)
        else:
            step = 90 * self.bpp
            moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                     Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}
            if k in moves:
                dx, dz = moves[k]
                self.cx += dx; self.cz += dz
                self._view_changed()
            else:
                super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        if self._pick is not None:
            return
        if self.measuring and self._mcur:            # right-click = finish the line
            self._measure_finish()
            return
        x, z = (int(math.floor(v)) for v in self.to_world(e.pos().x(), e.pos().y()))
        m = QMenu(self)
        sh = self.stronghold_near(e.pos().x(), e.pos().y()) if self.visible.get("__strongholds") else None
        a_sh = a_all = None
        if sh is not None:
            a_sh = m.addAction(struct_icons.icon("stronghold", 16), f"Stronghold details  ({sh[0]}, {sh[1]})…")
            a_all = m.addAction("All strongholds of this seed…")
            m.addSeparator()
        a1 = m.addAction(f"Copy coordinates   {x}, {z}")
        a2 = m.addAction("Copy /tp command")
        m.addSeparator()
        a3 = m.addAction("Centre map here")
        a4 = m.addAction("Go to coordinates…\tCtrl+G")
        a5 = m.addAction("Paste seed + coordinates…\tCtrl+Shift+V")
        a_me = a_mc = a_pin = None
        if self.pin is not None and self.pin[3] != self.world.seed:
            self.pin = None
        if self.MEASURE or self.measures or self.pin is not None:
            m.addSeparator()
        if self.MEASURE:
            a_me = m.addAction(icons.icon("ruler", 16), ("Stop measuring" if self.measuring
                                                          else "Measure distances") + "\tM")
        if self.measures:
            a_mc = m.addAction(f"Clear measurements ({len(self.measures)})")
        if self.pin is not None:
            a_pin = m.addAction(icons.icon("pin", 16), "Remove the pin")
        act = m.exec(e.globalPos())
        if act is not None and act is a5:
            w = self.window()
            if hasattr(w, "paste_seed_coords"):
                w.paste_seed_coords()
            return
        if act is not None and act is a_me:
            self.toggle_measure(); return
        if act is not None and act is a_mc:
            self.clear_measures(); return
        if act is not None and act is a_pin:
            self.clear_pin(); return
        cb = QGuiApplication.clipboard()
        if act is not None and act is a_sh:
            self.show_stronghold(*sh)
            return
        if act is not None and act is a_all:
            self.show_all_strongholds()
            return
        if act is a1:
            cb.setText(f"{x} {z}")
        elif act is a2:
            cb.setText(f"/tp @s {x} ~ {z}")
        elif act is a3:
            self.center_on(x, z)
        elif act is a4:
            self.goto_dialog()

    def goto_dialog(self):
        txt, ok = QInputDialog.getText(self, "Go to coordinates", "X, Z  (blocks):",
                                       text=f"{int(self.cx)}, {int(self.cz)}")
        if not ok:
            return
        parts = [t for t in txt.replace(",", " ").split() if t]
        try:
            x, z = int(float(parts[0])), int(float(parts[-1]))
        except (ValueError, IndexError):
            return
        self.center_on(x, z)

    # ── read-out ────────────────────────────────────────────────────────────
    def _ui_lib(self):
        key = (self.world.key, "lib")
        lib = self._ui_libs.get(key)
        if lib is None:
            lib = cw.make_worker_lib()
            lib.setup_and_seed(self.world.mc, self.world.seed, large_biomes=self.world.large_biomes)
            self._ui_libs[key] = lib
        return lib

    def _biome_at(self, x, z):
        scale = 1 if self.world.mc >= cw.MC_VERSIONS["Java 1.18"] else 4
        try:
            bid = self._ui_lib().get_biome_at(x, z, scale=scale)
        except Exception:
            return "?"
        return cw.BIOME_NAMES.get(bid, f"biome {bid}")

    def _noise_at(self, x, z):
        noise = self.ov["noise"]
        try:
            if noise in CLIMATE:
                v = float(octave_arrays(self._ui_noise(noise), x, z, 1, 1, 1, self.ov["octave"])[0, 0])
                reg = "" if self.ov["octave"] else npal.regime_label_at(noise, v)
                return f"{noise} {v:+.3f}" + (f" · {reg}" if reg else "")
            if noise == "Approx height":
                key = (self.world.key, "height")
                hm = self._ui_libs.get(key)
                if hm is None:
                    hm = cw.HeightMapLib(self._ui_lib()); hm.set_seed(self.world.seed)
                    self._ui_libs[key] = hm
                h, _ = hm.gen_height_grid(x >> 2, z >> 2, 1, 1)
                return f"≈ Y {float(h[0, 0]):.0f}"
            if noise == "Exact Y":
                key = (self.world.key, "terrain")
                eng = self._ui_libs.get(key)
                if eng is None:
                    import terrain
                    eng = terrain.TerrainEngine(self.world.seed, self.world.mc)
                    self._ui_libs[key] = eng
                return f"Y {eng.exact_y(x, z, caves=self.ov['caves'])}"
        except Exception:
            return ""
        return ""

    def _do_readout(self):
        if self._hover_pos is None or not self.rect().contains(self._hover_pos.toPoint()):
            return
        x, z = (int(math.floor(v)) for v in self.to_world(self._hover_pos.x(), self._hover_pos.y()))
        name = self._biome_at(x, z)
        extra = self._noise_at(x, z) if self.ov["noise"] else ""
        self._readout.setText(f"X {x}    Z {z}    ·    {name}" + (f"    ·    {extra}" if extra else ""))
        self._readout.show()
        self._place_readout()
        self.hover.emit(x, z, name)

    def _update_readout(self, pos):          # kept for tests / external callers
        self._hover_pos = pos
        self._do_readout()
